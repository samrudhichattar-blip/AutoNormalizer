"""
AI features. Principle: AI PROPOSES and EXPLAINS, the engine and the data VERIFY.

  suggest_fds   AI (or a naming heuristic offline) proposes FDs; every one is checked against the real rows.
  name_tables   AI proposes table names; validated (unique, safe identifiers) or replaced by a heuristic.
  explain       AI explains the engine's findings in plain language; it may only talk about violations the engine found.
Each result says where it came from: source = 'llm' or 'template' / 'heuristic'.
"""
import json
import re

from backend.ai.llm_client import LLMError, parse_json
from backend.engine.data_tools import discover_fds, fd_check

IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
IDLIKE = re.compile(r"(id|no|code|key)$", re.I)

# ------------------------------------------------------------------ suggest FDs
SYS_FDS = ("You are a database design assistant. Given column names and sample rows, propose functional dependencies that are "
           "SEMANTICALLY plausible (real business rules, not coincidences in the sample). Use ONLY the given column names. "
           "Prefer left sides with 1 or 2 columns. Return ONLY JSON: a list of objects "
           '{"lhs": [..], "rhs": [..], "reason": "one short sentence"}. At most 12 items.')


def _heuristic_fds(columns):
    """Offline guess from naming patterns (StudentID -> StudentName, cid -> cname)."""
    out = []
    for k in columns:
        m = re.match(r"^(.*?)[_ ]?(id|no|code)$", k, re.I)
        if not m or not m.group(1): continue
        base = m.group(1).lower()
        for c in columns:
            if c != k and c.lower().startswith(base) and not re.search(r"(id|no|code)$", c, re.I):
                out.append({"lhs": [k], "rhs": [c], "reason": f"naming pattern: '{c}' looks like a property of '{k}'"})
    return out


def suggest_fds(client, columns, rows, max_ai=12):
    cols = set(columns)
    try:
        sample = [{c: r[c] for c in columns} for r in rows[:8]]
        raw = parse_json(client.complete(SYS_FDS, json.dumps({"columns": columns, "sample_rows": sample})))
        if not isinstance(raw, list): raise ValueError("expected a list")
        proposals, source = raw[:max_ai], "llm"
    except (LLMError, ValueError):
        proposals, source = _heuristic_fds(columns), "name-heuristic"

    cands, seen = [], {}
    def add(lhs, rhs, reason, src):
        key = (tuple(sorted(lhs)), rhs)
        if key in seen:
            if src not in seen[key]["source"]: seen[key]["source"] += "+" + src
            return
        ev = fd_check(rows, lhs, [rhs]) if rows else {"holds": False, "error_rate": 1, "violating_groups": 0, "repeated_groups": 0, "lhs_is_unique": False, "examples": []}
        meaningful = not ev["lhs_is_unique"]
        status = ("trivial" if not meaningful else "supported" if ev["holds"] else "approximate" if ev["error_rate"] <= 0.05 else "contradicted")
        c = {"lhs": sorted(lhs), "rhs": [rhs], "source": src, "reason": reason, "status": status, "error_rate": ev["error_rate"],
             "repeated_groups": ev["repeated_groups"], "violating_groups": ev["violating_groups"], "examples": ev["examples"], "meaningful": meaningful,
             # Pre-tick only what is safe: backed by AI / naming AND data, or a single identifier-like column that repeats in the data.
             # Everything else found in data is shown but left for the user to decide (coincidences look just like rules).
             "recommended": status == "supported" and (("ai" in src or "heuristic" in src) and ev["repeated_groups"] >= 1
                                                       or (len(lhs) == 1 and bool(IDLIKE.search(lhs[0])) and ev["repeated_groups"] >= 2))}
        seen[key] = c; cands.append(c)

    rejected = 0
    for p in proposals:                                                           # AI proposals: validate before they even reach the data check
        try: lhs, rhs = list(p["lhs"]), list(p["rhs"])
        except (KeyError, TypeError): rejected += 1; continue
        if not lhs or not rhs or not (set(lhs) | set(rhs)) <= cols or set(lhs) & set(rhs): rejected += 1; continue
        for b in rhs: add(lhs, b, str(p.get("reason", ""))[:200], "ai" if source == "llm" else source)
    for f in discover_fds(rows, columns, max_lhs=2) if rows else []:              # evidence from the data itself
        add(f["lhs"], f["rhs"][0], "holds in every row of the uploaded data", "data")

    for i, c in enumerate(cands): c["id"] = i
    order = {"supported": 0, "approximate": 1, "contradicted": 2, "trivial": 3}
    cands.sort(key=lambda c: (order[c["status"]], len(c["lhs"]), c["lhs"], c["rhs"]))
    ai = [c for c in cands if "ai" in c["source"] or "heuristic" in c["source"]]
    return {"ai_source": source, "candidates": cands,
            "summary": {"proposed_by_ai": len(ai), "rejected_invalid": rejected, "contradicted_by_data": sum(c["status"] == "contradicted" for c in ai),
                        "supported_by_data": sum(c["status"] == "supported" for c in cands), "discovered_from_data_only": sum(c["source"] == "data" for c in cands)}}


# ------------------------------------------------------------------ table names
SYS_NAMES = ("You name database tables. For each relation (attributes + primary key) give a short, clear, singular-concept table name "
             'in PascalCase (letters, digits, underscore only). Return ONLY JSON: {"names": [..]} in the same order as the input.')


def _heuristic_name(attrs, key, i):
    if len(key) == 1:
        k = key[0]; m = re.match(r"^(.*?)[_ ]?(id|no|code)$", k, re.I)
        base = m.group(1) if m and len(m.group(1)) >= 3 else k
        base = re.sub(r"\W+", "_", base).strip("_").capitalize()
        return base if base.endswith("s") else (base[:-1] + "ies" if base.endswith("y") else base + "s")
    bases = []
    for k in key:                                                                   # (StudentID, CourseID) -> Student_Course
        m = re.match(r"^(.*?)[_ ]?(id|no|code)$", k, re.I)
        bases.append(re.sub(r"\W+", "_", m.group(1) if m and len(m.group(1)) >= 3 else k).strip("_").capitalize())
    return "_".join(bases)[:40] or f"Table{i + 1}"


def name_tables(client, relations, keys):
    """relations: list of attribute lists; keys: list of primary keys (attribute lists)."""
    fallback = []
    for i, (r, k) in enumerate(zip(relations, keys)):
        n = _heuristic_name(r, k, i)
        while n in fallback: n += "_" + str(i + 1)
        fallback.append(n)
    try:
        raw = parse_json(client.complete(SYS_NAMES, json.dumps([{"attributes": r, "primary_key": k} for r, k in zip(relations, keys)])))
        names = raw["names"] if isinstance(raw, dict) else raw
        if len(names) != len(relations) or len(set(names)) != len(names) or not all(isinstance(n, str) and IDENT.match(n) for n in names):
            raise ValueError("invalid names")
        return {"source": "llm", "names": names}
    except (LLMError, ValueError, KeyError, TypeError):
        return {"source": "heuristic", "names": fallback}


# ------------------------------------------------------------------ explanations
SYS_EXPLAIN = ("You are a friendly DBMS tutor. Explain the ENGINE FINDINGS below to a student. Use ONLY the facts given: do not invent "
               "dependencies, keys or attributes. Return ONLY JSON: "
               '{"summary": "2-3 sentences", "violations": [{"fd": "<copy the fd string exactly>", "explanation": "why it is a problem and what to do", '
               '"example": "a tiny concrete example of the anomaly"}], "decomposition": ["one plain sentence per step"], "viva_questions": ["3 questions"]}')


def _template_explain(a, decomp_key):
    d = a[decomp_key]
    keys = " or ".join("{" + ", ".join(k) + "}" for k in a["candidate_keys"])
    viol = []
    for v in a["violations"]:
        l, r = ", ".join(v["lhs"]), ", ".join(v["rhs"])
        viol.append({"fd": v["fd"], "explanation": f"{v['type'].capitalize()}. '{r}' is determined by '{l}' alone, so the same fact is repeated on many rows; "
                     f"this breaks {v['breaks']}. Fix: store '{r}' in its own table keyed by '{l}'.",
                     "example": f"If '{l}' appears on several rows, changing '{r}' on one row but not the others leaves the data inconsistent (update anomaly)."})
    prop = ("lossless and dependency preserving" if d["lossless"] and d["dependency_preserving"]
            else "lossless, but it loses " + ", ".join(d["lost_dependencies"]) if d["lossless"] else "NOT lossless")
    if decomp_key == "decomposition_3nf":
        rel = lambda r: "R(" + ", ".join(r) + ")"
        key = a["candidate_keys"][0] if a["candidate_keys"] else []
        holder = next((r for r in d["relations"] if set(key) <= set(r)), None)
        steps = [f"Reduce the dependencies to a minimal cover (drop redundant rules and extra attributes): {'; '.join(a['minimal_cover']) or 'none'}.",
                 "Make one table for each left side of those dependencies: " + ", ".join(rel(r) for r in d["relations"]) + ".",
                 (f"Check that some table contains a candidate key {{{', '.join(key)}}}. {rel(holder)} does, so the join of the tables loses nothing." if holder
                  else f"No table contained the candidate key {{{', '.join(key)}}}, so a table for it was added to keep the join lossless."),
                 f"Result: {len(d['relations'])} tables, lossless join: {'yes' if d['lossless'] else 'NO'}, dependencies preserved: "
                 f"{'yes' if d['dependency_preserving'] else 'NO (lost: ' + ', '.join(d['lost_dependencies']) + ')'}."]
    else:
        steps = list(d["steps"])
    return {"summary": f"The relation is in {a['normal_form']}. Candidate key(s): {keys}. The suggested decomposition has {len(d['relations'])} tables and is {prop}.",
            "violations": viol, "decomposition": steps,
            "viva_questions": ["Why is a partial dependency a problem?", "What is the difference between lossless join and dependency preservation?",
                               "Why can BCNF decomposition lose a dependency while 3NF synthesis never does?"]}


def explain(client, analysis, target="3NF"):
    key = "decomposition_3nf" if target == "3NF" else "decomposition_bcnf"
    template = _template_explain(analysis, key)
    facts = {k: analysis[k] for k in ("attributes", "candidate_keys", "minimal_cover", "normal_form", "violations")}
    facts["decomposition"] = analysis[key]
    try:
        raw = parse_json(client.complete(SYS_EXPLAIN, json.dumps(facts), max_tokens=1800))
        if not isinstance(raw, dict) or not isinstance(raw.get("summary"), str): raise ValueError("bad shape")
        real = {v["fd"] for v in analysis["violations"]}
        by_fd = {v.get("fd"): v for v in raw.get("violations", []) if isinstance(v, dict) and v.get("fd") in real}       # drop anything the engine did not find
        violations = [by_fd.get(t["fd"], t) for t in template["violations"]]                                            # every real violation is covered
        steps = [s for s in raw.get("decomposition", []) if isinstance(s, str)] or template["decomposition"]
        qs = [q for q in raw.get("viva_questions", []) if isinstance(q, str)][:5] or template["viva_questions"]
        return {"source": "llm", "model": getattr(client, "model", None), "summary": raw["summary"], "violations": violations,
                "decomposition": steps, "viva_questions": qs}
    except (LLMError, ValueError, KeyError, TypeError):
        return {"source": "template", **template}
