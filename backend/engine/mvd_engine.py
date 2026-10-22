"""
Multivalued dependencies (MVD) and Fourth Normal Form (4NF).

  X ->> Y   means: for each X value, the set of Y values is independent of the rest of the row (Z = R - X - Y).
            Equivalent test on data: within each X group, the (Y,Z) pairs must be the FULL cross product of Y values and Z values.
  4NF       R is in 4NF if for every non-trivial MVD X ->> Y, X is a superkey.  (Every FD also counts as an MVD.)
"""
from collections import defaultdict
from itertools import combinations

from backend.engine.fd_engine import FD, closure, candidate_keys, project_fds, is_superkey, fmt


def MVD(lhs, rhs):
    return (frozenset(lhs), frozenset(rhs))


def mvd_check(rows, attrs, X, Y, sample=3):
    X, Y = sorted(X), sorted(set(Y) - set(X))
    Z = sorted(set(attrs) - set(X) - set(Y))
    if not Y or not Z:
        return {"trivial": True, "holds": True, "informative_groups": 0, "violating_groups": 0, "examples": []}
    groups = defaultdict(lambda: {"pairs": set(), "ys": set(), "zs": set()})
    for r in rows:
        y, z = tuple(r[a] for a in Y), tuple(r[a] for a in Z)
        g = groups[tuple(r[a] for a in X)]
        g["pairs"].add((y, z)); g["ys"].add(y); g["zs"].add(z)
    bad, informative = [], 0
    for k, g in groups.items():
        if len(g["ys"]) > 1 and len(g["zs"]) > 1: informative += 1
        if len(g["pairs"]) != len(g["ys"]) * len(g["zs"]):
            missing = [(y, z) for y in g["ys"] for z in g["zs"] if (y, z) not in g["pairs"]]
            bad.append({"lhs": dict(zip(X, k)), "missing_combinations": [{"Y": dict(zip(Y, y)), "Z": dict(zip(Z, z))} for y, z in missing[:sample]],
                        "missing_count": len(missing)})
    return {"trivial": False, "holds": not bad, "informative_groups": informative, "violating_groups": len(bad), "examples": bad[:sample]}


def discover_mvds(rows, attrs, fds=(), max_lhs=1, max_rhs=1, min_informative=2):
    """MVDs that hold in the data and are NOT just FDs. 'informative' = groups where both sides really vary,
    which rules out MVDs that hold only because the data is too small to contradict them."""
    attrs, seen, out = sorted(attrs), set(), []
    for k in range(1, max_lhs + 1):
        for X in combinations(attrs, k):
            Xs, rest = frozenset(X), [a for a in attrs if a not in X]
            if len(rest) < 2: continue
            cl = closure(Xs, list(fds))
            for m in range(1, max_rhs + 1):
                for Y in combinations(rest, m):
                    Ys, Zs = frozenset(Y), frozenset(rest) - frozenset(Y)
                    if not Zs: continue
                    canon = (Xs, min(tuple(sorted(Ys)), tuple(sorted(Zs))))     # X ->> Y and X ->> Z are the same statement
                    if canon in seen: continue
                    seen.add(canon)
                    if Ys <= cl or Zs <= cl: continue                            # implied by a declared FD
                    res = mvd_check(rows, attrs, Xs, Ys)
                    if res["holds"] and res["informative_groups"] >= min_informative:
                        out.append({"lhs": sorted(Xs), "rhs": sorted(Ys), "complement": sorted(Zs), "informative_groups": res["informative_groups"]})
    return out


def _with_complements(attrs, mvds):
    out = set(mvds)
    for x, y in mvds:
        z = frozenset(attrs) - x - y
        if z: out.add((x, z))
    return out


def violations_4nf(attrs, fds, mvds):
    """List of ('FD'|'MVD', X, Y) that break 4NF in this relation."""
    attrs = frozenset(attrs); p = project_fds(attrs, fds); out = []
    for l, r in p:
        if not r <= l and not is_superkey(l, attrs, p): out.append(("FD", l, r - l))
    for x, y in _with_complements(attrs, mvds):
        y = y & attrs
        if x <= attrs and y and not y <= x and (x | y) != attrs and not is_superkey(x, attrs, p):
            out.append(("MVD", x, y - x))
    return sorted(set(out), key=lambda v: (v[0], sorted(v[1]), sorted(v[2])))


def is_4nf(attrs, fds, mvds):
    return not violations_4nf(attrs, fds, mvds)


def decompose_4nf(attrs, fds, mvds, steps=None):
    """Split on a violating dependency X ->> Y into (X u Y) and (R - Y). An MVD Z ->> W still holds on any child containing Z as Z ->> (W n child)."""
    mvds = _with_complements(frozenset(attrs), mvds)
    todo, result = [frozenset(attrs)], []
    while todo:
        r = todo.pop()
        v = violations_4nf(r, fds, mvds)
        if not v:
            result.append(r); continue
        kind, x, y = v[0]
        r1, r2 = frozenset(x | y), frozenset(r - y)
        if steps is not None:
            steps.append(f"R({fmt(r)}) violates 4NF via {kind} {fmt(x)} {'->' if kind == 'FD' else '->>'} {fmt(y)} ({fmt(x)} is not a superkey). "
                         f"Split into R({fmt(r1)}) and R({fmt(r2)})")
        todo += [r1, r2]          # MVDs are projected automatically: violations_4nf() intersects Y with the child's attributes
    return sorted(set(result), key=lambda s: sorted(s))
