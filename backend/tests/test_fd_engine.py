from backend.engine.fd_engine import *

def S(x): return frozenset(x)

def test_closure_and_keys():
    fds = [FD("A", "B"), FD("B", "C")]
    assert closure("A", fds) == {"A", "B", "C"}
    assert candidate_keys("ABCD", fds) == [S("AD")]

def test_multiple_keys():
    fds = [FD("AB", "C"), FD("C", "B")]          # classic: 3NF but not BCNF
    assert set(candidate_keys("ABC", fds)) == {S("AB"), S("AC")}
    assert highest_normal_form("ABC", fds) == "3NF"

def test_minimal_cover():
    fds = [FD("A", "BC"), FD("B", "C"), FD("A", "B"), FD("AB", "C")]
    mc = minimal_cover(fds)
    assert set(mc) == {FD("A", "B"), FD("B", "C")}

def test_bcnf_not_dependency_preserving():
    fds = [FD("AB", "C"), FD("C", "B")]
    d = bcnf_decompose("ABC", fds)
    assert is_lossless("ABC", d, fds)
    ok, lost = is_dependency_preserving(d, fds)
    assert not ok and lost == [FD("AB", "C")]

def test_3nf_synthesis_preserves():
    fds = [FD("AB", "C"), FD("C", "B")]
    d = synthesize_3nf("ABC", fds)
    assert is_lossless("ABC", d, fds)
    assert is_dependency_preserving(d, fds)[0]

def test_student_course_example():
    attrs = ["sid", "sname", "cid", "cname", "grade"]
    fds = [FD(["sid"], ["sname"]), FD(["cid"], ["cname"]), FD(["sid", "cid"], ["grade"])]
    assert candidate_keys(attrs, fds) == [S(["sid", "cid"])]
    assert highest_normal_form(attrs, fds) == "1NF"          # partial dependencies
    d = synthesize_3nf(attrs, fds)
    assert set(d) == {S(["sid", "sname"]), S(["cid", "cname"]), S(["sid", "cid", "grade"])}
    assert is_lossless(attrs, d, fds) and is_dependency_preserving(d, fds)[0]
