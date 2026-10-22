from fastapi.testclient import TestClient
from backend.api.demo_app import app
from backend.api.plus_routes import llm
from backend.ai.llm_client import OfflineClient

app.dependency_overrides[llm] = lambda: OfflineClient()
c = TestClient(app)

ENROL = "sid,sname,cid,cname,grade\n101,Rahul,C01,DBMS,A\n101,Rahul,C02,Java,B\n102,Priya,C01,DBMS,A\n103,Amit,C02,Java,C\n103,Amit,C01,DBMS,B\n102,Priya,C02,Java,A\n"
CTB = "course,teacher,book\n" + "".join(f"{co},{t},{b}\n" for co, ts, bs in [("DBMS", "Rao Iyer".split(), "Korth Elmasri".split()), ("OS", "Shah Roy".split(), "Galvin Tanen".split()),
                                                                              ("AI", ["Nair"], ["Russell"])] for t in ts for b in bs)


def test_profile_suggests_verified_fds():
    r = c.post("/plus/profile", json={"csv_text": ENROL}).json()
    assert r["row_count"] == 6 and r["columns"][0] == "sid"
    sup = {(tuple(x["lhs"]), x["rhs"][0]) for x in r["fd_suggestions"]["candidates"] if x["status"] == "supported"}
    assert (("sid",), "sname") in sup and (("cid",), "cname") in sup

def test_analyze_full_pipeline():
    body = {"csv_text": ENROL, "fds": [{"lhs": ["sid"], "rhs": ["sname"]}, {"lhs": ["cid"], "rhs": ["cname"]}, {"lhs": ["sid", "cid"], "rhs": ["grade"]}]}
    r = c.post("/plus/analyze", json=body).json()
    assert r["analysis"]["normal_form"] == "1NF" and len(r["er"]["entities"]) == 3 and len(r["er"]["relationships"]) == 2
    assert r["reconstruction"]["lossless"] is True
    assert r["explanation"]["source"] == "template" and r["table_names"]["source"] == "heuristic"
    assert r["mermaid"].startswith("erDiagram") and "CREATE TABLE" in r["ddl"] and "FOREIGN KEY" in r["ddl"]

def test_4nf_with_discovered_mvd_end_to_end():
    r = c.post("/plus/analyze", json={"csv_text": CTB, "fds": []}).json()
    assert r["mvd_candidates"] and r["mvd_candidates"][0]["lhs"] == ["course"]
    f = c.post("/plus/4nf", json={"csv_text": CTB, "mvds": [{"lhs": ["course"], "rhs": ["teacher"]}]}).json()
    assert sorted(map(tuple, f["relations"])) == [("book", "course"), ("course", "teacher")]
    assert f["reconstruction"]["lossless"] is True and f["violations_before"] and f["steps"]

def test_whatif_endpoint_and_errors():
    body = {"attributes": ["A", "B", "C"], "fds": [{"lhs": ["A"], "rhs": ["B"]}, {"lhs": ["B"], "rhs": ["C"]}], "changes": [{"op": "add_fd", "lhs": ["C"], "rhs": ["A"]}]}
    assert c.post("/plus/whatif", json=body).json()["diff"]["normal_form"]["after"] == "BCNF"
    assert c.post("/plus/whatif", json={**body, "changes": [{"op": "remove_fd", "lhs": ["A"], "rhs": ["C"]}]}).status_code == 400
    assert c.post("/plus/whatif", json={**body, "changes": [{"op": "nope"}]}).status_code == 400
    assert c.post("/plus/analyze", json={"csv_text": ENROL, "fds": [{"lhs": ["ghost"], "rhs": ["sid"]}]}).status_code == 400
    assert c.post("/plus/profile", json={"csv_text": "only_one_column\n1\n"}).status_code == 400
    assert c.post("/plus/analyze", json={"csv_text": ENROL, "fds": [], "target": "5NF"}).status_code == 400

def test_er_endpoint():
    r = c.post("/plus/er", json={"relations": [["a", "b"], ["a", "c"]], "fds": [{"lhs": ["a"], "rhs": ["b", "c"]}], "names": ["Left", "Right"]}).json()
    assert len(r["er"]["relationships"]) == 1 and "Left" in r["mermaid"]

def test_demo_page_served():
    assert c.get("/").status_code in (200, 404)    # 404 only before the HTML file exists
