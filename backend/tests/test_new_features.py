import copy
import pytest
from backend.engine.fd_engine import FD
from backend.engine.data_tools import discover_fds, fd_check, infer_types, verify_reconstruction
from backend.engine.mvd_engine import MVD, decompose_4nf, discover_mvds, is_4nf, mvd_check, violations_4nf
from backend.engine.er_diagram import build_er, to_ddl, to_mermaid
from backend.engine.whatif import analyze, apply_changes, what_if

ENROL = [dict(sid=s, sname=n, cid=c, cname=cn, grade=g) for s, n, c, cn, g in
         [("101", "Rahul", "C01", "DBMS", "A"), ("101", "Rahul", "C02", "Java", "B"), ("102", "Priya", "C01", "DBMS", "A"),
          ("103", "Amit", "C02", "Java", "C"), ("103", "Amit", "C01", "DBMS", "B"), ("102", "Priya", "C02", "Java", "A")]]
ATTRS = ["sid", "sname", "cid", "cname", "grade"]
FDS = [FD(["sid"], ["sname"]), FD(["cid"], ["cname"]), FD(["sid", "cid"], ["grade"])]


# ---------- data evidence ----------
def test_fd_check_holds_and_fails():
    assert fd_check(ENROL, ["sid"], ["sname"])["holds"]
    bad = ENROL + [dict(sid="101", sname="rahul", cid="C03", cname="OS", grade="A")]
    r = fd_check(bad, ["sid"], ["sname"])
    assert not r["holds"] and r["violating_groups"] == 1 and r["examples"][0]["lhs"] == {"sid": "101"}
    assert 0 < r["error_rate"] < 0.2

def test_discover_fds_finds_real_dependencies_and_skips_keys():
    found = {(tuple(f["lhs"]), f["rhs"][0]) for f in discover_fds(ENROL, ATTRS)}
    assert (("sid",), "sname") in found and (("cid",), "cname") in found
    assert not any(l == ("sid",) and r == "cid" for l, r in found)              # not a real dependency

def test_discover_fds_tolerates_noise_only_when_asked():
    noisy = ENROL * 5 + [dict(sid="101", sname="rahul", cid="C01", cname="DBMS", grade="A")]
    assert (("sid",), "sname") not in {(tuple(f["lhs"]), f["rhs"][0]) for f in discover_fds(noisy, ATTRS)}
    assert (("sid",), "sname") in {(tuple(f["lhs"]), f["rhs"][0]) for f in discover_fds(noisy, ATTRS, max_error=0.05)}

def test_reconstruction_lossless_and_lossy():
    good = verify_reconstruction(ENROL, [{"sid", "sname"}, {"cid", "cname"}, {"sid", "cid", "grade"}])
    assert good["lossless"] and good["spurious_rows"] == 0
    lossy = verify_reconstruction(ENROL, [{"sid", "sname", "cname"}, {"cid", "cname", "grade"}, {"sid", "cid"}][:2])
    assert lossy["lossless"] is False or lossy["lossless"] is None

def test_infer_types():
    assert infer_types([dict(a="1", b="x", c="2026-01-02", d="1.5")]) == {"a": "int", "b": "text", "c": "date", "d": "float"}


# ---------- MVD / 4NF ----------
CTB = [dict(course=c, teacher=t, book=b) for c, ts, bs in [("DBMS", ["Rao", "Iyer"], ["Korth", "Elmasri"]), ("OS", ["Shah", "Roy"], ["Galvin", "Tanenbaum"]),
                                                          ("AI", ["Nair"], ["Russell"])] for t in ts for b in bs]

def test_mvd_holds_and_breaks():
    assert mvd_check(CTB, ["course", "teacher", "book"], ["course"], ["teacher"])["holds"]
    broken = CTB[:-0 or None][:3] + CTB[4:]                                      # remove one combination
    assert not mvd_check(broken, ["course", "teacher", "book"], ["course"], ["teacher"])["holds"]

def test_discover_mvds_dedupes_complement_and_skips_fds():
    found = discover_mvds(CTB, ["course", "teacher", "book"])
    assert len(found) == 1 and found[0]["lhs"] == ["course"]
    assert discover_mvds(ENROL, ATTRS, FDS) == []

def test_4nf_decomposition_classic():
    attrs, mvds = {"course", "teacher", "book"}, [MVD(["course"], ["teacher"])]
    assert not is_4nf(attrs, [], mvds)
    steps = []
    d = decompose_4nf(attrs, [], mvds, steps)
    assert set(d) == {frozenset({"course", "teacher"}), frozenset({"course", "book"})} and steps
    assert all(is_4nf(r, [], mvds) for r in d)
    assert verify_reconstruction(CTB, [set(r) for r in d])["lossless"]

def test_bcnf_schema_can_still_violate_4nf_and_fd_violation_counts():
    assert [v[0] for v in violations_4nf({"A", "B", "C"}, [FD("A", "B")], [])] == ["FD"]
    assert is_4nf({"A", "B", "C"}, [FD("A", "BC")], [MVD("A", "B")])             # A is a key: MVD is harmless


# ---------- ER ----------
def test_er_keys_fks_and_mermaid_and_ddl():
    rels = [{"sid", "sname"}, {"cid", "cname"}, {"sid", "cid", "grade"}]
    er = build_er(rels, FDS, names={0: "Students", 1: "Courses", 2: "Enrollments"}, types={"sid": "int"})
    ent = {e["name"]: e for e in er["entities"]}
    assert ent["Students"]["primary_key"] == ["sid"] and ent["Enrollments"]["primary_key"] == ["cid", "sid"]
    assert {(f["from"], f["to"], tuple(f["columns"])) for f in er["relationships"]} == {("Enrollments", "Students", ("sid",)), ("Enrollments", "Courses", ("cid",))}
    assert all(f["cardinality"] == "many-to-one" and f["identifying"] for f in er["relationships"])
    m = to_mermaid(er); assert "Students ||--o{ Enrollments" in m and "int sid PK" in m
    ddl = to_ddl(er); assert 'CREATE TABLE "Students"' in ddl and 'FOREIGN KEY ("sid") REFERENCES "Students" ("sid")' in ddl

def test_er_one_to_one_not_duplicated():
    er = build_er([{"a", "b"}, {"a", "c"}], [FD("a", "b"), FD("a", "c")])
    assert len(er["relationships"]) == 1 and er["relationships"][0]["cardinality"] == "one-to-one"


# ---------- what-if ----------
def test_analyze_classifies_violations():
    a = analyze(ATTRS, FDS)
    assert a["normal_form"] == "1NF" and a["candidate_keys"] == [["cid", "sid"]]
    assert {v["breaks"] for v in a["violations"]} == {"2NF"}
    assert a["decomposition_3nf"]["lossless"] and a["decomposition_3nf"]["dependency_preserving"]

def test_what_if_does_not_modify_input_and_reports_diff():
    attrs, fds = list(ATTRS), copy.deepcopy(FDS)
    res = what_if(attrs, fds, [{"op": "remove_fd", "lhs": ["cid"], "rhs": ["cname"]}])
    assert attrs == ATTRS and fds == FDS                                         # original untouched
    assert res["after"]["violations"] != res["before"]["violations"]
    assert any("decomposition" in s for s in res["diff"]["summary"])

def test_what_if_adding_an_fd_can_improve_normal_form():
    base = [FD("A", "B"), FD("B", "C")]                                          # transitive dependency: 2NF
    res = what_if(["A", "B", "C"], base, [{"op": "add_fd", "lhs": ["C"], "rhs": ["A"]}])
    assert res["diff"]["normal_form"] == {"before": "2NF", "after": "BCNF", "changed": True}
    assert sorted(res["diff"]["keys_added"]) == [["B"], ["C"]]

def test_what_if_removing_an_fd_can_make_it_worse():
    base = [FD("A", "B"), FD("B", "C")]
    res = what_if(["A", "B", "C"], base, [{"op": "remove_fd", "lhs": ["B"], "rhs": ["C"]}])
    assert res["diff"]["normal_form"] == {"before": "2NF", "after": "1NF", "changed": True}     # C is no longer determined, so the key grows to AC

def test_apply_changes_validation():
    with pytest.raises(ValueError): apply_changes(["A"], [], [{"op": "add_fd", "lhs": ["A"], "rhs": ["Z"]}])
    with pytest.raises(ValueError): apply_changes(["A", "B"], [], [{"op": "remove_fd", "lhs": ["A"], "rhs": ["B"]}])
    a, f = apply_changes(["A", "B", "C"], [FD("A", "BC")], [{"op": "remove_attribute", "name": "C"}])
    assert a == {"A", "B"} and f == [FD("A", "B")]


# ---------- found while testing with realistic sample files ----------
def test_synthesis_does_not_duplicate_relations_for_mutual_fds():
    from backend.engine.fd_engine import synthesize_3nf
    d = synthesize_3nf({"A", "B", "C"}, [FD("A", "B"), FD("B", "A")])
    assert len(d) == len(set(d)) and frozenset({"A", "B"}) in d

def test_discover_keys_finds_minimal_unique_combinations():
    from backend.engine.data_tools import discover_keys
    keys = discover_keys(ENROL, ATTRS)
    assert ["sid", "cid"] in keys or ["cid", "sid"] in keys
    assert all(len(k) >= 2 for k in keys)                                         # no single unique column here
    assert not any(set(a) < set(b) for a in keys for b in keys)                   # minimal only


def test_minimal_cover_log_has_no_duplicate_lines():
    steps = []
    from backend.engine.fd_engine import minimal_cover
    minimal_cover([FD("C", "F"), FD("F", "N"), FD("C", "N"), FD(["C", "S"], "N")], steps)
    assert len(steps) == len(set(steps))
