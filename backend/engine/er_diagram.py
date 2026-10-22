"""
ER diagram from the FINAL tables: entities with PK / FK / unique flags, relationships with cardinality, Mermaid text, and DDL with foreign keys.

Foreign key rule: relation A references relation B through key K when K is a candidate key of B and K is contained in A.
Cardinality: K equals A's own key -> one-to-one, otherwise many(A)-to-one(B). If K is part of A's primary key the relationship is 'identifying'.
"""
import re

from backend.engine.fd_engine import candidate_keys, project_fds


def _ident(name):
    return re.sub(r"\W+", "_", str(name)).strip("_") or "X"


def build_er(relations, fds, names=None, types=None):
    """relations: list of attribute sets. names: {index: table name}. types: {attribute: 'int'|'float'|'date'|'text'}."""
    rels = [frozenset(r) for r in relations]
    names = {i: (names or {}).get(i, f"R{i + 1}") for i in range(len(rels))}
    types = types or {}
    keys = [sorted(candidate_keys(r, project_fds(r, fds)), key=lambda k: (len(k), sorted(k))) for r in rels]
    pk = [k[0] if k else r for k, r in zip(keys, rels)]

    fks = []
    for i, a in enumerate(rels):
        for j, b in enumerate(rels):
            if i == j or a <= b: continue
            for k in keys[j]:
                if not k <= a: continue
                if k == pk[i] and k == pk[j] and i < j: continue            # same key on both sides: keep only one direction
                fks.append({"from": names[i], "to": names[j], "columns": sorted(k), "cardinality": "one-to-one" if k == pk[i] else "many-to-one",
                            "identifying": k <= pk[i], "_i": i})
    fk_cols = {i: set() for i in range(len(rels))}
    for f in fks: fk_cols[f["_i"]] |= set(f["columns"])

    entities = []
    for i, r in enumerate(rels):
        uniq = {next(iter(k)) for k in keys[i] if len(k) == 1 and k != pk[i]}
        entities.append({"name": names[i], "primary_key": sorted(pk[i]), "candidate_keys": [sorted(k) for k in keys[i]],
                         "attributes": [{"name": a, "type": types.get(a, "text"), "pk": a in pk[i], "fk": a in fk_cols[i], "unique": a in uniq}
                                        for a in sorted(r, key=lambda a: (a not in pk[i], a))]})
    for f in fks: f.pop("_i")
    return {"entities": entities, "relationships": fks}


def to_mermaid(er):
    lines = ["erDiagram"]
    for f in er["relationships"]:
        right = "||" if f["cardinality"] == "one-to-one" else "o{"
        lines.append(f"    {_ident(f['to'])} ||--{right} {_ident(f['from'])} : \"{', '.join(f['columns'])}\"")
    for e in er["entities"]:
        lines.append(f"    {_ident(e['name'])} {{")
        for a in e["attributes"]:
            flags = ",".join(x for x, on in (("PK", a["pk"]), ("FK", a["fk"]), ("UK", a["unique"] and not a["pk"])) if on)
            lines.append(f"        {a['type'] if a['type'] != 'text' else 'string'} {_ident(a['name'])}{' ' + flags if flags else ''}")
        lines.append("    }")
    return "\n".join(lines)


# ------------------------------------------------------------------ SQL (PostgreSQL, MySQL, Oracle)
DIALECTS = {
    "postgresql": {"int": "INTEGER", "float": "NUMERIC", "date": "DATE", "text": "TEXT", "max_name": 63},
    "mysql": {"int": "INT", "float": "DOUBLE", "date": "DATE", "text": "VARCHAR(255)", "max_name": 64},      # TEXT cannot be a MySQL key
    "oracle": {"int": "NUMBER(10)", "float": "NUMBER", "date": "DATE", "text": "VARCHAR2(255)", "max_name": 30},
}
_ORA_RESERVED = {"DATE", "LEVEL", "COMMENT", "NUMBER", "SIZE", "ORDER", "GROUP", "USER", "SELECT", "TABLE", "FROM", "WHERE", "UPDATE", "INDEX",
                 "MODE", "ROW", "ROWS", "SESSION", "START", "UID", "VIEW", "COLUMN", "INSERT", "DELETE", "DROP", "ALTER", "CHECK", "DEFAULT", "NULL", "ACCESS"}


def check_dialect(d):
    if d not in DIALECTS: raise ValueError(f"dialect must be one of {sorted(DIALECTS)}")
    return d


def quote(name, dialect="postgresql"):
    name = str(name)
    if dialect == "mysql": return "`" + name.replace("`", "``") + "`"
    if dialect == "oracle" and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,29}", name) and name.upper() not in _ORA_RESERVED:
        return name                                                              # unquoted = case-insensitive, the Oracle way
    return '"' + name.replace('"', '""') + '"'


def literal(value, typ, dialect="postgresql"):
    if value is None or (value == "" and typ != "text"): return "NULL"
    if typ in ("int", "float"): return str(value)                                # infer_types guarantees these are numeric
    if typ == "date": return f"DATE '{value}'" if dialect == "oracle" else f"'{value}'"
    v = str(value).replace("'", "''")
    return "'" + (v.replace("\\", "\\\\") if dialect == "mysql" else v) + "'"


def _fk_name(dialect, child, parent, n):
    base = f"fk_{n}_{_ident(child)}_{_ident(parent)}"
    return base[:DIALECTS[dialect]["max_name"]]


def table_order(er):
    """Parents (referenced tables) first; falls back to the given order if there is a cycle."""
    names = [e["name"] for e in er["entities"]]
    deps = {n: {f["to"] for f in er["relationships"] if f["from"] == n} - {n} for n in names}
    out = []
    while len(out) < len(names):
        ready = [n for n in names if n not in out and deps[n] <= set(out)]
        if not ready: return names
        out += ready
    return out


def to_ddl(er, dialect="postgresql"):
    d = DIALECTS[check_dialect(dialect)]; q = lambda s: quote(s, dialect)
    ents = {e["name"]: e for e in er["entities"]}
    out = []
    for name in table_order(er):
        e = ents[name]
        cols = [f"  {q(a['name'])} {d.get(a['type'], d['text'])}{' NOT NULL' if a['pk'] else ''}" for a in e["attributes"]]
        cols.append(f"  PRIMARY KEY ({', '.join(q(c) for c in e['primary_key'])})")
        out.append(f"CREATE TABLE {q(name)} (\n" + ",\n".join(cols) + "\n);")
    out += [fk_statement(f, n, dialect) for n, f in enumerate(er["relationships"], 1)]
    return "\n\n".join(out)


def fk_statement(f, n, dialect):
    q = lambda s: quote(s, dialect); cols = ", ".join(q(c) for c in f["columns"])
    return (f"ALTER TABLE {q(f['from'])} ADD CONSTRAINT {q(_fk_name(dialect, f['from'], f['to'], n))} "
            f"FOREIGN KEY ({cols}) REFERENCES {q(f['to'])} ({cols});")
