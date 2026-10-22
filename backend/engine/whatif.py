"""
Schema what-if: apply hypothetical changes to a COPY of the schema and compare before / after.
The original attributes and FDs are never modified (inputs are treated as read-only).
"""
from backend.engine.fd_engine import (FD, bcnf_decompose, candidate_keys, closure, fmt, highest_normal_form, is_dependency_preserving,
                                      is_lossless, minimal_cover, project_fds, synthesize_3nf)


def fd_str(fd): return f"{fmt(fd[0])} -> {fmt(fd[1])}"
def rel_str(r): return "R(" + ", ".join(sorted(r)) + ")"
def _rels(rs): return sorted(rel_str(r) for r in rs)


def analyze(attrs, fds):
    attrs, fds = frozenset(attrs), [FD(l, r) for l, r in fds]
    keys = candidate_keys(attrs, fds)
    prime = set().union(*keys) if keys else set()
    cover = minimal_cover(fds)
    violations = []
    for l, r in cover:
        if closure(l, fds) >= attrs: continue                                   # determinant is a superkey: fine
        a = next(iter(r))
        if a in prime: kind, breaks = "determinant is not a superkey, but the dependent attribute is part of a key", "BCNF"
        elif any(l < k for k in keys): kind, breaks = "partial dependency (depends on only part of a candidate key)", "2NF"
        else: kind, breaks = "transitive dependency (non-key attribute determines another non-key attribute)", "3NF"
        violations.append({"fd": fd_str((l, r)), "lhs": sorted(l), "rhs": sorted(r), "type": kind, "breaks": breaks})
    s3, sb = [], []
    d3, db = synthesize_3nf(attrs, fds, s3), bcnf_decompose(attrs, fds, sb)
    props = lambda d: {"lossless": is_lossless(attrs, d, fds), "dependency_preserving": is_dependency_preserving(d, fds)[0],
                       "lost_dependencies": [fd_str(x) for x in is_dependency_preserving(d, fds)[1]]}
    return {"attributes": sorted(attrs), "candidate_keys": sorted(sorted(k) for k in keys), "prime_attributes": sorted(prime),
            "minimal_cover": sorted(fd_str(f) for f in cover), "normal_form": highest_normal_form(attrs, fds), "violations": violations,
            "decomposition_3nf": {"relations": [sorted(r) for r in sorted(d3, key=sorted)], "steps": s3, **props(d3)},
            "decomposition_bcnf": {"relations": [sorted(r) for r in sorted(db, key=sorted)], "steps": sb, **props(db)}}


def apply_changes(attrs, fds, changes):
    """changes: [{'op': 'add_fd'|'remove_fd'|'add_attribute'|'remove_attribute', ...}]. Returns a NEW (attrs, fds)."""
    attrs, fds = set(attrs), [FD(l, r) for l, r in fds]
    for c in changes:
        op = c["op"]
        if op == "add_attribute": attrs.add(c["name"])
        elif op == "remove_attribute":
            a = c["name"]; attrs.discard(a)
            fds = [FD(l, r - {a}) for l, r in fds if a not in l and (r - {a})]      # FDs using it on the left disappear, on the right shrink
        elif op == "add_fd":
            fd = FD(c["lhs"], c["rhs"])
            if not (fd[0] | fd[1]) <= attrs: raise ValueError(f"FD uses unknown attribute(s): {sorted((fd[0] | fd[1]) - attrs)}")
            if fd not in fds: fds.append(fd)
        elif op == "remove_fd":
            fd = FD(c["lhs"], c["rhs"])
            if fd not in fds: raise ValueError(f"FD not found: {fd_str(fd)}")
            fds.remove(fd)
        else: raise ValueError(f"unknown change: {op}")
    return attrs, fds


def what_if(attrs, fds, changes):
    before = analyze(attrs, fds)
    a2, f2 = apply_changes(attrs, fds, changes)
    after = analyze(a2, f2)
    ks = lambda x: {tuple(k) for k in x["candidate_keys"]}
    diff = {"normal_form": {"before": before["normal_form"], "after": after["normal_form"], "changed": before["normal_form"] != after["normal_form"]},
            "keys_added": sorted(list(k) for k in ks(after) - ks(before)), "keys_removed": sorted(list(k) for k in ks(before) - ks(after)),
            "violations_before": len(before["violations"]), "violations_after": len(after["violations"]), "decompositions": {}}
    for name in ("decomposition_3nf", "decomposition_bcnf"):
        b, a = before[name], after[name]
        sb, sa = {tuple(r) for r in b["relations"]}, {tuple(r) for r in a["relations"]}
        diff["decompositions"][name] = {"tables_added": sorted(list(r) for r in sa - sb), "tables_removed": sorted(list(r) for r in sb - sa),
                                        "dependency_preserving": {"before": b["dependency_preserving"], "after": a["dependency_preserving"]},
                                        "lossless": {"before": b["lossless"], "after": a["lossless"]}}
    lines = []
    if diff["normal_form"]["changed"]: lines.append(f"Normal form changes from {before['normal_form']} to {after['normal_form']}.")
    else: lines.append(f"Normal form stays {before['normal_form']}.")
    if diff["keys_added"] or diff["keys_removed"]:
        lines.append("Candidate keys change: " + ", ".join([f"+{''.join(k)}" for k in diff["keys_added"]] + [f"-{''.join(k)}" for k in diff["keys_removed"]]) + ".")
    for name, label in (("decomposition_3nf", "3NF"), ("decomposition_bcnf", "BCNF")):
        d = diff["decompositions"][name]
        if d["tables_added"] or d["tables_removed"]: lines.append(f"{label} decomposition changes: {len(d['tables_added'])} table(s) added, {len(d['tables_removed'])} removed.")
        if d["dependency_preserving"]["before"] != d["dependency_preserving"]["after"]:
            lines.append(f"{label} dependency preservation: {d['dependency_preserving']['before']} -> {d['dependency_preserving']['after']}.")
    diff["summary"] = lines
    return {"before": before, "after": after, "diff": diff}
