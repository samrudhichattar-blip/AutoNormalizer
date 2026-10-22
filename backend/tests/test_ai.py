import json
import httpx
from backend.ai.ai_features import explain, name_tables, suggest_fds
from backend.ai.llm_client import AnthropicClient, LLMError, OfflineClient, parse_json
from backend.engine.fd_engine import FD
from backend.engine.whatif import analyze

ROWS = [dict(sid=s, sname=n, cid=c, cname=cn, grade=g) for s, n, c, cn, g in
        [("101", "Rahul", "C01", "DBMS", "A"), ("101", "Rahul", "C02", "Java", "B"), ("102", "Priya", "C01", "DBMS", "A"),
         ("103", "Amit", "C02", "Java", "C"), ("103", "Amit", "C01", "DBMS", "B"), ("102", "Priya", "C02", "Java", "A")]]
COLS = ["sid", "sname", "cid", "cname", "grade"]
A = analyze(COLS, [FD(["sid"], ["sname"]), FD(["cid"], ["cname"]), FD(["sid", "cid"], ["grade"])])


class Fake:
    name, model = "fake", "fake-model"
    def __init__(self, reply): self.reply = reply
    def complete(self, system, user, max_tokens=0):
        if isinstance(self.reply, Exception): raise self.reply
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def test_parse_json_handles_fences_and_prose():
    assert parse_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}

def test_anthropic_client_request_and_parsing():
    seen = {}
    def handler(req):
        seen["h"], seen["b"] = dict(req.headers), json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}, {"type": "text", "text": " there"}]})
    c = AnthropicClient("sk-test", model="m1", transport=httpx.MockTransport(handler))
    assert c.complete("sys", "user") == "hi there"
    assert seen["h"]["x-api-key"] == "sk-test" and seen["b"]["model"] == "m1" and seen["b"]["system"] == "sys"
    bad = AnthropicClient("k", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="nope")))
    try: bad.complete("s", "u"); assert False
    except LLMError: pass

def test_suggest_fds_verifies_ai_against_data_and_drops_hallucinations():
    ai = [{"lhs": ["sid"], "rhs": ["sname"], "reason": "student id identifies the student"},
          {"lhs": ["cname"], "rhs": ["grade"], "reason": "wrong on purpose"},            # contradicted by the data
          {"lhs": ["ghost"], "rhs": ["sname"], "reason": "column does not exist"}]       # hallucinated column
    res = suggest_fds(Fake(ai), COLS, ROWS)
    by = {(tuple(c["lhs"]), c["rhs"][0]): c for c in res["candidates"]}
    assert by[(("sid",), "sname")]["status"] == "supported" and by[(("sid",), "sname")]["recommended"] and "ai" in by[(("sid",), "sname")]["source"]
    assert by[(("cname",), "grade")]["status"] == "contradicted" and by[(("cname",), "grade")]["examples"]
    assert not any("ghost" in c["lhs"] for c in res["candidates"])
    assert res["summary"]["rejected_invalid"] == 1 and res["summary"]["contradicted_by_data"] == 1
    assert (("cid",), "cname") in by and by[(("cid",), "cname")]["source"] == "data"      # found by data only

def test_suggest_fds_offline_fallback_uses_naming_heuristic_and_still_checks_data():
    res = suggest_fds(OfflineClient(), COLS, ROWS)
    assert res["ai_source"] == "name-heuristic"
    assert any(c["lhs"] == ["sid"] and c["rhs"] == ["sname"] and c["status"] == "supported" for c in res["candidates"])

def test_name_tables_validates_llm_output():
    rels, keys = [["sid", "sname"], ["cid", "cname"], ["cid", "grade", "sid"]], [["sid"], ["cid"], ["cid", "sid"]]
    ok = name_tables(Fake({"names": ["Students", "Courses", "Enrollments"]}), rels, keys)
    assert ok == {"source": "llm", "names": ["Students", "Courses", "Enrollments"]}
    for bad in ({"names": ["A", "A", "B"]}, {"names": ["Drop Table;", "x", "y"]}, {"names": ["only one"]}, "not json"):
        r = name_tables(Fake(bad), rels, keys)
        assert r["source"] == "heuristic" and len(set(r["names"])) == 3
    assert name_tables(OfflineClient(), [["StudentID", "Name"]], [["StudentID"]])["names"] == ["Students"]

def test_explain_only_keeps_violations_the_engine_found():
    real = A["violations"][0]["fd"]
    reply = {"summary": "Two partial dependencies.", "violations": [{"fd": real, "explanation": "custom", "example": "e"},
                                                                    {"fd": "zzz -> yyy", "explanation": "invented", "example": "x"}],
             "decomposition": ["Split students out."], "viva_questions": ["Q1?"]}
    res = explain(Fake(reply), A)
    assert res["source"] == "llm" and len(res["violations"]) == len(A["violations"])
    assert not any(v["fd"] == "zzz -> yyy" for v in res["violations"])
    assert next(v for v in res["violations"] if v["fd"] == real)["explanation"] == "custom"

def test_explain_falls_back_to_template_on_failure():
    for client in (OfflineClient(), Fake("garbage"), Fake(LLMError("down")), Fake({"no": "summary"})):
        res = explain(client, A)
        assert res["source"] == "template" and res["violations"] and "1NF" in res["summary"]


def test_template_decomposition_steps_are_plain_english():
    res = explain(OfflineClient(), A, "3NF")
    text = " ".join(res["decomposition"])
    assert "minimal cover" in text and "lossless" in text and "Step 2" not in text and len(res["decomposition"]) == 4
