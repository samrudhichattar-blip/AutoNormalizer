"""
AutoNormalizer - deterministic normalization engine (no AI in here).
FDs are tuples (lhs: frozenset, rhs: frozenset). Every function can return a
`steps` list so the UI can show "why" for each calculation.
"""
from itertools import combinations

def FD(lhs, rhs):
    return (frozenset(lhs), frozenset(rhs))

def fmt(s):
    return "".join(sorted(s)) if all(len(a) == 1 for a in s) else "{" + ", ".join(sorted(s)) + "}"

# ---------- 1. Attribute closure ----------
def closure(attrs, fds, steps=None):
    res = set(attrs)
    changed = True
    while changed:
        changed = False
        for lhs, rhs in fds:
            if lhs <= res and not rhs <= res:
                res |= rhs
                changed = True
                if steps is not None:
                    steps.append(f"{fmt(lhs)} -> {fmt(rhs)} applies, closure becomes {fmt(res)}")
    return res

# ---------- 2. Candidate keys ----------
def candidate_keys(attrs, fds):
    attrs = set(attrs)
    on_rhs = set().union(*[r for _, r in fds]) if fds else set()
    on_lhs = set().union(*[l for l, _ in fds]) if fds else set()
    must = attrs - on_rhs                      # never derived -> in every key
    optional = sorted((attrs & on_lhs & on_rhs) - must)   # may or may not be needed
    if closure(must, fds) >= attrs:
        return [frozenset(must)]
    keys = []
    for k in range(1, len(optional) + 1):
        for combo in combinations(optional, k):
            cand = frozenset(must | set(combo))
            if any(key <= cand for key in keys):
                continue                       # not minimal
            if closure(cand, fds) >= attrs:
                keys.append(cand)
    return keys

# ---------- 3. Minimal (canonical) cover ----------
def minimal_cover(fds, steps=None):
    # a) single attribute on RHS
    g = [FD(l, [a]) for l, r in fds for a in r]
    if steps is not None: steps.append("Step 1: split RHS -> " + "; ".join(f"{fmt(l)}->{fmt(r)}" for l, r in g))
    # b) remove extraneous LHS attributes
    for i, (l, r) in enumerate(g):
        l = set(l)
        for a in sorted(l):
            if len(l) > 1 and r <= closure(l - {a}, g):
                l.discard(a)
                if steps is not None: steps.append(f"Step 2: {a} is extraneous in {fmt(g[i][0])}->{fmt(r)}, removed")
        g[i] = (frozenset(l), r)
    g = list(dict.fromkeys(g))                    # identical FDs can appear after step b; keep one copy
    # c) remove redundant FDs
    i = 0
    while i < len(g):
        rest = g[:i] + g[i + 1:]
        if g[i][1] <= closure(g[i][0], rest):
            if steps is not None: steps.append(f"Step 3: {fmt(g[i][0])}->{fmt(g[i][1])} is redundant, removed")
            g = rest
        else:
            i += 1
    return list(dict.fromkeys(g))

# ---------- 4. Normal form checks ----------
def is_superkey(x, attrs, fds):
    return closure(x, fds) >= set(attrs)

def project_fds(attrs, fds):
    """FDs that hold on a sub-relation (exponential, fine for <= ~12 attributes)."""
    attrs = sorted(attrs); out = []
    for k in range(1, len(attrs)):
        for combo in combinations(attrs, k):
            x = frozenset(combo)
            rhs = (closure(x, fds) & set(attrs)) - x
            if rhs: out.append((x, frozenset(rhs)))
    return minimal_cover(out) if out else []

def bcnf_violations(attrs, fds):
    return [(l, r) for l, r in project_fds(attrs, fds)
            if not r <= l and not is_superkey(l, attrs, project_fds(attrs, fds))]

def is_bcnf(attrs, fds):
    p = project_fds(attrs, fds)
    return all(r <= l or is_superkey(l, attrs, p) for l, r in p)

def is_3nf(attrs, fds):
    p = project_fds(attrs, fds)
    prime = set().union(*candidate_keys(attrs, p)) if candidate_keys(attrs, p) else set()
    return all(r <= l or is_superkey(l, attrs, p) or (r - l) <= prime for l, r in p)

def is_2nf(attrs, fds):
    p = project_fds(attrs, fds); keys = candidate_keys(attrs, p)
    prime = set().union(*keys) if keys else set()
    for l, r in p:
        for key in keys:
            if l < key and (r - l) - prime:
                return False                   # partial dependency
    return True

def highest_normal_form(attrs, fds):
    if is_bcnf(attrs, fds): return "BCNF"
    if is_3nf(attrs, fds): return "3NF"
    if is_2nf(attrs, fds): return "2NF"
    return "1NF"

# ---------- 5. 3NF synthesis (Bernstein) ----------
def synthesize_3nf(attrs, fds, steps=None):
    attrs = set(attrs)
    mc = minimal_cover(fds, steps)
    groups = {}
    for l, r in mc:
        groups.setdefault(l, set()).update(r)
    merged = {}
    for l, r in groups.items(): merged.setdefault(frozenset(l | r), []).append(FD(l, r))      # A->B and B->A give the SAME relation: keep one
    rels = list(merged.items())
    if steps is not None: steps.append("Group by LHS -> " + ", ".join(f"R({fmt(a)})" for a, _ in rels))
    keys = candidate_keys(attrs, fds)
    if not any(k <= a for a, _ in rels for k in keys):
        rels.append((keys[0], []))
        if steps is not None: steps.append(f"No relation contains a candidate key, add R({fmt(keys[0])})")
    # drop relations contained in another
    rels = [(a, f) for a, f in rels if not any(a < b for b, _ in rels)]
    covered = set().union(*[a for a, _ in rels])
    if attrs - covered: rels.append((frozenset(attrs - covered), []))
    return [a for a, _ in rels]

# ---------- 6. BCNF decomposition ----------
def bcnf_decompose(attrs, fds, steps=None):
    result, todo = [], [frozenset(attrs)]
    while todo:
        r = todo.pop()
        p = project_fds(r, fds)
        bad = next(((l, rr) for l, rr in p if not rr <= l and not is_superkey(l, r, p)), None)
        if bad is None:
            result.append(r); continue
        l, _ = bad
        r1 = frozenset(closure(l, p) & r)      # X+ within R
        r2 = frozenset(l | (r - r1))
        if steps is not None:
            steps.append(f"R({fmt(r)}) violates BCNF via {fmt(l)} (not a superkey). Split into R({fmt(r1)}) and R({fmt(r2)})")
        todo += [r1, r2]
    return sorted(set(result), key=lambda s: sorted(s))

# ---------- 7. Decomposition property checks ----------
def is_lossless(attrs, decomposition, fds):
    """Chase / tableau test."""
    attrs = sorted(attrs)
    tab = [{a: (f"a_{a}" if a in rel else f"b_{i}_{a}") for a in attrs} for i, rel in enumerate(decomposition)]
    changed = True
    while changed:
        changed = False
        for l, r in fds:
            for t1, t2 in combinations(tab, 2):
                if all(t1[a] == t2[a] for a in l):
                    for a in r:
                        if t1[a] != t2[a]:
                            keep = t1[a] if t1[a].startswith("a_") else t2[a]
                            old = t2[a] if keep == t1[a] else t1[a]
                            for t in tab:
                                if t[a] == old: t[a] = keep
                            changed = True
    return any(all(t[a].startswith("a_") for a in attrs) for t in tab)

def is_dependency_preserving(decomposition, fds):
    lost = []
    for l, r in fds:
        z = set(l)
        changed = True
        while changed:
            changed = False
            for rel in decomposition:
                add = closure(z & rel, fds) & rel
                if not add <= z:
                    z |= add; changed = True
        if not r <= z: lost.append((l, r))
    return (len(lost) == 0, lost)
