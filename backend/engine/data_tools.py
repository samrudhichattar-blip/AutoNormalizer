"""
Tools that look at REAL DATA (rows = list of dicts). They never decide business rules; they only produce evidence.

  fd_check            does X -> Y hold in the data? how badly does it fail? show counterexamples
  discover_fds        find FDs that hold in the data (TANE-lite, levelwise, minimal, small LHS)
  verify_reconstruction  join the decomposed tables back together and compare with the original rows
  infer_types         guess int / float / date / text per column
"""
import re
from collections import Counter, defaultdict
from itertools import combinations


def _key(row, cols):
    return tuple(row[c] for c in cols)


def fd_check(rows, lhs, rhs, max_examples=3):
    lhs, rhs = sorted(lhs), sorted(rhs)
    groups = defaultdict(Counter)
    for r in rows:
        groups[_key(r, lhs)][_key(r, rhs)] += 1
    n = len(rows)
    kept = sum(max(c.values()) for c in groups.values())            # rows we keep if each group agrees on its most common RHS
    bad = [(k, c) for k, c in groups.items() if len(c) > 1]
    return {"holds": not bad, "error_rate": round((n - kept) / n, 4) if n else 0.0,
            "violating_groups": len(bad), "total_groups": len(groups),
            "repeated_groups": sum(1 for c in groups.values() if sum(c.values()) > 1),
            "lhs_is_unique": len(groups) == n,
            "examples": [{"lhs": dict(zip(lhs, k)), "rhs_values": [dict(zip(rhs, v)) for v in c], "rows": sum(c.values())}
                         for k, c in bad[:max_examples]]}


def discover_fds(rows, attrs, max_lhs=2, max_error=0.0):
    """Minimal FDs X -> b that hold (within max_error) in the data. Key-like and constant columns are skipped on purpose:
    a unique column 'determines' everything and a constant column is determined by everything, neither is a useful finding."""
    attrs, n = sorted(attrs), len(rows)
    if n == 0: return []
    distinct = {a: len({r[a] for r in rows}) for a in attrs}
    unique = {a for a in attrs if distinct[a] == n}
    constant = {a for a in attrs if distinct[a] == 1}
    found = []                                                      # (frozenset lhs, rhs, info)
    for k in range(1, max_lhs + 1):
        for lhs in combinations([a for a in attrs if a not in unique], k):
            groups = defaultdict(list)
            for r in rows: groups[_key(r, lhs)].append(r)
            if all(len(g) == 1 for g in groups.values()): continue   # lhs is key-like in this data
            for b in attrs:
                if b in lhs or b in constant: continue
                if any(set(f[0]) < set(lhs) and f[1] == b for f in found): continue      # not minimal
                kept = sum(max(Counter(r[b] for r in g).values()) for g in groups.values())
                err = (n - kept) / n
                if err <= max_error:
                    rep = [g for g in groups.values() if len(g) > 1]
                    found.append((frozenset(lhs), b, {"lhs": list(lhs), "rhs": [b], "error_rate": round(err, 4),
                                                       "repeated_groups": len(rep), "support_rows": sum(len(g) for g in rep)}))
    return [f[2] for f in found]


def _distinct_tuples(rows, attrs):
    attrs = sorted(attrs)
    return {tuple(r[a] for a in attrs) for r in rows}, attrs


def verify_reconstruction(rows, relations, max_rows=300_000, sample=3):
    """Project the data onto every relation, natural-join everything back, and compare with the original distinct rows."""
    all_attrs = sorted(set().union(*[set(r) for r in relations]))
    original, _ = _distinct_tuples(rows, all_attrs)
    tables = []
    for rel in relations:
        tuples, cols = _distinct_tuples(rows, rel)
        tables.append((cols, [dict(zip(cols, t)) for t in tuples]))
    cols, acc = tables[0]; acc = list(acc); have = set(cols); rest = tables[1:]
    while rest:
        idx = next((i for i, (c, _) in enumerate(rest) if have & set(c)), 0)       # join something connected first
        c, trows = rest.pop(idx)
        common = sorted(have & set(c))
        index = defaultdict(list)
        for t in trows: index[tuple(t[a] for a in common)].append(t)
        new = []
        for a in acc:
            for t in index.get(tuple(a[x] for x in common), []):
                new.append({**a, **t})
                if len(new) > max_rows: return {"lossless": None, "error": f"join exceeds {max_rows} rows (very lossy or too large)"}
        acc, have = new, have | set(c)
    rebuilt = {tuple(a[x] for x in all_attrs) for a in acc}
    spurious, missing = rebuilt - original, original - rebuilt
    show = lambda ts: [dict(zip(all_attrs, t)) for t in list(ts)[:sample]]
    return {"lossless": not spurious and not missing, "original_distinct_rows": len(original), "reconstructed_rows": len(rebuilt),
            "spurious_rows": len(spurious), "missing_rows": len(missing), "spurious_examples": show(spurious), "missing_examples": show(missing)}


def infer_types(rows):
    out = {}
    for a in (rows[0].keys() if rows else []):
        vals = [str(r[a]).strip() for r in rows if str(r[a]).strip() != ""]
        if vals and all(re.fullmatch(r"-?\d+", v) for v in vals): out[a] = "int"
        elif vals and all(re.fullmatch(r"-?\d+(\.\d+)?", v) for v in vals): out[a] = "float"
        elif vals and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in vals): out[a] = "date"
        else: out[a] = "text"
    return out


def discover_keys(rows, attrs, max_size=3):
    """Minimal column sets whose values are unique across all rows ('keys of THIS data'). The user decides which one is the real key."""
    attrs, n = sorted(attrs), len(rows)
    if n == 0: return []
    if len(attrs) > 15: max_size = min(max_size, 2)
    keys = []
    for k in range(1, max_size + 1):
        for combo in combinations(attrs, k):
            if any(set(key) <= set(combo) for key in keys): continue
            if len({tuple(r[a] for a in combo) for r in rows}) == n: keys.append(combo)
    return [list(k) for k in keys]
