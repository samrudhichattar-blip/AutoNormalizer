import csv, io, zipfile
import pytest
from fastapi.testclient import TestClient
from backend.api.demo_app import app
from backend.api.plus_routes import llm
from backend.ai.llm_client import OfflineClient
from backend.engine.er_diagram import build_er, literal, quote, to_ddl
from backend.engine.fd_engine import FD

app.dependency_overrides[llm] = lambda: OfflineClient()
c = TestClient(app)
ER = build_er([{"sid", "sname"}, {"cid", "cname"}, {"sid", "cid", "grade"}],
              [FD("sid", "sname"), FD("cid", "cname"), FD(["sid", "cid"], "grade")], names={0: "Students", 1: "Courses", 2: "Enrollments"}, types={"sid": "int"})
CSV = "sid,sname,cid,cname,grade\n1,Rahul,C1,DBMS,A\n1,Rahul,C2,Java,B\n2,Priya,C1,DBMS,A\n"
FDS = [{"lhs": ["sid"], "rhs": ["sname"]}, {"lhs": ["cid"], "rhs": ["cname"]}, {"lhs": ["sid", "cid"], "rhs": ["grade"]}]
BODY = {"csv_text": CSV, "fds": FDS, "relations": [["sid", "sname"], ["cid", "cname"], ["cid", "sid", "grade"]], "names": ["Students", "Courses", "Enrollments"]}


def test_default_dialect_is_postgresql_and_parents_come_first():
    ddl = to_ddl(ER)
    assert 'CREATE TABLE "Students"' in ddl and "TEXT" in ddl
    assert ddl.index('CREATE TABLE "Students"') < ddl.index('CREATE TABLE "Enrollments"')

def test_mysql_uses_varchar_keys_and_backticks_oracle_uses_varchar2():
    my, ora = to_ddl(ER, "mysql"), to_ddl(ER, "oracle")
    assert "`sname` VARCHAR(255)" in my and "TEXT" not in my and "`" in my
    assert "VARCHAR2(255)" in ora and "NUMBER(10)" in ora and '"' not in ora          # plain identifiers stay unquoted in Oracle

def test_quoting_rules():
    assert quote("Grade", "oracle") == "Grade" and quote("Date", "oracle") == '"Date"' and quote("my col", "oracle") == '"my col"'
    assert quote("a`b", "mysql") == "`a``b`" and quote('a"b') == '"a""b"'

def test_literals_are_escaped_and_typed():
    assert literal("O'Neil", "text") == "'O''Neil'" and literal("a\\b", "text", "mysql") == "'a\\\\b'"
    assert literal("", "int") == "NULL" and literal("", "text") == "''" and literal("7", "int") == "7"
    assert literal("2026-01-02", "date", "oracle") == "DATE '2026-01-02'" and literal("2026-01-02", "date") == "'2026-01-02'"

@pytest.mark.parametrize("dialect", ["postgresql", "mysql", "oracle"])
def test_export_zip_contents_and_row_counts(dialect):
    r = c.post("/plus/export", json={**BODY, "dialect": dialect})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(z.namelist()) == {"tables/Students.csv", "tables/Courses.csv", "tables/Enrollments.csv", "schema.sql", "README.txt"}
    assert len(list(csv.reader(io.StringIO(z.read("tables/Students.csv").decode())))) == 3        # header + Rahul + Priya
    sql = z.read("schema.sql").decode()
    assert sql.count("INSERT INTO") == 2 + 2 + 3 and "FOREIGN KEY" in sql
    assert sql.index("INSERT INTO") < sql.index("FOREIGN KEY")                                    # keys are added after the data
    assert ("COMMIT;" in sql) == (dialect == "oracle")
    assert "LOSSLESS" in z.read("README.txt").decode()

def test_export_refuses_when_data_contradicts_a_primary_key():
    bad = CSV + "1,rahul,C3,OS,A\n"                                                             # same sid, different name
    r = c.post("/plus/export", json={**BODY, "csv_text": bad})
    assert r.status_code == 409
    d = r.json()["detail"]; assert d["conflicts"][0]["table"] == "Students" and len(d["conflicts"][0]["examples"][0]["variants"]) == 2

def test_export_validates_input():
    assert c.post("/plus/export", json={**BODY, "names": ["a b", "Courses", "E"]}).status_code == 400            # unsafe name
    assert c.post("/plus/export", json={**BODY, "names": ["A", "A", "B"]}).status_code == 400                    # duplicates
    assert c.post("/plus/export", json={**BODY, "relations": [["sid", "ghost"], ["cid"], ["grade"]]}).status_code == 400
    assert c.post("/plus/export", json={**BODY, "dialect": "db2"}).status_code == 400

def test_analyze_returns_relations_and_honours_dialect():
    r = c.post("/plus/analyze", json={"csv_text": CSV, "fds": FDS, "dialect": "mysql"}).json()
    assert len(r["relations"]) == len(r["table_names"]["names"]) == 3 and "`" in r["ddl"]
    assert c.post("/plus/analyze", json={"csv_text": CSV, "fds": FDS, "dialect": "nope"}).status_code == 400
