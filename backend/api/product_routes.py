"""Auth, persistent projects, XLSX import, DB connectivity and guarded copilot endpoints."""
import os, re, json, hmac, base64, hashlib, secrets, sqlite3, time, io, csv
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["accounts-workspaces"])
DATA_DIR = Path(os.getenv("AUTONORMALIZER_DATA_DIR", str(Path.home()/".autonormalizer")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "autonormalizer.sqlite3"
SECRET_PATH = DATA_DIR / "secret.key"

def db():
    c=sqlite3.connect(DB_PATH, timeout=10); c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,salt TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,name TEXT NOT NULL,data_json TEXT NOT NULL,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.commit(); return c
def secret():
    if not SECRET_PATH.exists():
        SECRET_PATH.write_bytes(secrets.token_bytes(32))
        try: SECRET_PATH.chmod(0o600)
        except OSError: pass
    return SECRET_PATH.read_bytes()
def hashpw(password, salt=None):
    if not isinstance(password,str) or not 10 <= len(password) <= 256: raise HTTPException(400,"Password must be 10–256 characters.")
    salt=salt or secrets.token_bytes(16)
    digest=hashlib.scrypt(password.encode(),salt=salt,n=2**14,r=8,p=1,dklen=32)
    return base64.urlsafe_b64encode(digest).decode(),base64.urlsafe_b64encode(salt).decode()
def token(uid):
    p=base64.urlsafe_b64encode(json.dumps({"uid":uid,"exp":int(time.time())+43200},separators=(",",":")).encode()).decode().rstrip("=")
    s=base64.urlsafe_b64encode(hmac.new(secret(),p.encode(),hashlib.sha256).digest()).decode().rstrip("=")
    return p+"."+s
def uid_from(auth):
    if not auth or not auth.lower().startswith("bearer "): raise HTTPException(401,"Sign in to continue.")
    try:
        p,s=auth.split(" ",1)[1].split(".",1)
        good=base64.urlsafe_b64encode(hmac.new(secret(),p.encode(),hashlib.sha256).digest()).decode().rstrip("=")
        if not hmac.compare_digest(s,good): raise ValueError()
        payload=json.loads(base64.urlsafe_b64decode(p+"="*((4-len(p)%4)%4)))
        if payload["exp"] < time.time(): raise ValueError()
        return int(payload["uid"])
    except Exception: raise HTTPException(401,"Session expired or invalid; sign in again.")
class Auth(BaseModel):
    email:str
    password:str
class Workspace(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    data:dict=Field(default_factory=dict)
class Xlsx(BaseModel):
    content_base64:str
    sheet:Optional[str]=None
class DBConfig(BaseModel):
    engine:str
    host:str="localhost"
    port:Optional[int]=None
    database:str
    username:str
    password:str
    ssl:bool=True
class Copilot(BaseModel):
    question:str
    csv_text:Optional[str]=None
    context:dict=Field(default_factory=dict)

@router.post("/auth/signup")
def signup(x:Auth):
    email=x.email.strip().lower()
    if len(email)>254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email): raise HTTPException(400,"Enter a valid email address.")
    ph,salt=hashpw(x.password); c=db()
    try:
        cur=c.execute("INSERT INTO users(email,password_hash,salt) VALUES(?,?,?)",(email,ph,salt)); c.commit()
        return {"token":token(cur.lastrowid),"user":{"id":cur.lastrowid,"email":email}}
    except sqlite3.IntegrityError: raise HTTPException(409,"An account with this email already exists.")
    finally: c.close()
@router.post("/auth/login")
def login(x:Auth):
    email=x.email.strip().lower(); c=db(); row=c.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone(); c.close()
    if not row: raise HTTPException(401,"Email or password is incorrect.")
    try: got,_=hashpw(x.password,base64.urlsafe_b64decode(row["salt"]))
    except HTTPException: raise HTTPException(401,"Email or password is incorrect.")
    if not hmac.compare_digest(got,row["password_hash"]): raise HTTPException(401,"Email or password is incorrect.")
    return {"token":token(row["id"]),"user":{"id":row["id"],"email":row["email"]}}
@router.get("/auth/me")
def me(authorization:Optional[str]=Header(None)):
    uid=uid_from(authorization); c=db(); r=c.execute("SELECT id,email,created_at FROM users WHERE id=?",(uid,)).fetchone(); c.close()
    if not r: raise HTTPException(401,"Account not found.")
    return dict(r)
@router.get("/workspaces")
def list_workspaces(authorization:Optional[str]=Header(None)):
    uid=uid_from(authorization); c=db(); rs=c.execute("SELECT id,name,updated_at FROM workspaces WHERE user_id=? ORDER BY updated_at DESC",(uid,)).fetchall(); c.close()
    return {"workspaces":[dict(r) for r in rs]}
@router.post("/workspaces")
def save_workspace(x:Workspace,authorization:Optional[str]=Header(None)):
    uid=uid_from(authorization); payload=json.dumps(x.data)
    if len(payload)>2_000_000: raise HTTPException(413,"Workspace data exceeds 2 MB.")
    c=db(); cur=c.execute("INSERT INTO workspaces(user_id,name,data_json) VALUES(?,?,?)",(uid,x.name.strip(),payload)); c.commit(); wid=cur.lastrowid; c.close()
    return {"id":wid,"name":x.name.strip(),"saved":True}
@router.get("/workspaces/{wid}")
def get_workspace(wid:int,authorization:Optional[str]=Header(None)):
    uid=uid_from(authorization); c=db(); r=c.execute("SELECT id,name,data_json,updated_at FROM workspaces WHERE id=? AND user_id=?",(wid,uid)).fetchone(); c.close()
    if not r: raise HTTPException(404,"Workspace not found.")
    return {"id":r["id"],"name":r["name"],"data":json.loads(r["data_json"]),"updated_at":r["updated_at"]}
@router.put("/workspaces/{wid}")
def update_workspace(wid:int,x:Workspace,authorization:Optional[str]=Header(None)):
    uid=uid_from(authorization); payload=json.dumps(x.data)
    if len(payload)>2_000_000: raise HTTPException(413,"Workspace data exceeds 2 MB.")
    c=db(); cur=c.execute("UPDATE workspaces SET name=?,data_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",(x.name.strip(),payload,wid,uid)); c.commit(); c.close()
    if not cur.rowcount: raise HTTPException(404,"Workspace not found.")
    return {"id":wid,"name":x.name.strip(),"saved":True}
@router.delete("/workspaces/{wid}")
def delete_workspace(wid:int,authorization:Optional[str]=Header(None)):
    uid=uid_from(authorization); c=db(); cur=c.execute("DELETE FROM workspaces WHERE id=? AND user_id=?",(wid,uid)); c.commit(); c.close()
    if not cur.rowcount: raise HTTPException(404,"Workspace not found.")
    return {"deleted":True}
@router.post("/import/xlsx")
def import_xlsx(x:Xlsx,authorization:Optional[str]=Header(None)):
    uid_from(authorization)
    try: import openpyxl
    except ImportError: raise HTTPException(501,"Install openpyxl to import XLSX files.")
    try:
        raw=base64.b64decode(x.content_base64,validate=True)
        if len(raw)>15*1024*1024: raise HTTPException(413,"Workbook limit is 15 MB.")
        wb=openpyxl.load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
        ws=wb[x.sheet] if x.sheet else wb[wb.sheetnames[0]]
        it=ws.iter_rows(values_only=True); hdr=next(it,None)
        if not hdr: raise HTTPException(400,"Sheet is empty.")
        headers=[str(v).strip() if v is not None else "" for v in hdr]
        if any(not h for h in headers) or len(set(headers))!=len(headers): raise HTTPException(400,"First row must contain unique non-empty column names.")
        if len(headers)>50: raise HTTPException(400,"Maximum 50 columns supported.")
        rows=[]
        for i,row in enumerate(it):
            if i>=20000: raise HTTPException(400,"Maximum 20,000 rows supported.")
            rows.append([str(row[j]) if j<len(row) and row[j] is not None else "" for j in range(len(headers))])
        out=io.StringIO(); w=csv.writer(out,lineterminator="\n"); w.writerow(headers); w.writerows(rows)
        return {"sheet":ws.title,"sheets":wb.sheetnames,"columns":headers,"row_count":len(rows),"csv_text":out.getvalue(),"preview":[dict(zip(headers,r)) for r in rows[:8]]}
    except HTTPException: raise
    except Exception as e: raise HTTPException(400,"Could not read workbook: "+str(e)[:160])
def connect(cfg):
    if cfg.engine=="postgresql":
        import psycopg
        return psycopg.connect(host=cfg.host,port=cfg.port or 5432,dbname=cfg.database,user=cfg.username,password=cfg.password,connect_timeout=5,sslmode="require" if cfg.ssl else "prefer")
    if cfg.engine=="mysql":
        import pymysql
        return pymysql.connect(host=cfg.host,port=cfg.port or 3306,database=cfg.database,user=cfg.username,password=cfg.password,connect_timeout=5,read_timeout=5,write_timeout=5,ssl={"ssl":{}} if cfg.ssl else None)
    raise HTTPException(400,"Engine must be postgresql or mysql.")
@router.post("/db/test")
def db_test(x:DBConfig,authorization:Optional[str]=Header(None)):
    uid_from(authorization)
    try:
        c=connect(x); cur=c.cursor(); cur.execute("SELECT version()" if x.engine=="postgresql" else "SELECT VERSION()"); version=cur.fetchone()[0]; c.close()
        return {"connected":True,"engine":x.engine,"version":str(version)}
    except ImportError: raise HTTPException(501,"Install database drivers: psycopg[binary] and pymysql.")
    except HTTPException: raise
    except Exception as e: raise HTTPException(400,"Connection failed: "+str(e)[:180])
@router.post("/db/query")
def db_query(payload:dict,authorization:Optional[str]=Header(None)):
    uid_from(authorization); x=DBConfig(**payload.get("connection",{})); sql=str(payload.get("sql","")).strip()
    if not re.match(r"(?is)^select\b",sql) or ";" in sql.rstrip(";") or "--" in sql or "/*" in sql:
        raise HTTPException(400,"Only one plain SELECT statement is allowed.")
    try:
        c=connect(x)
        if x.engine=="postgresql": c.execute("SET default_transaction_read_only = on")
        else: c.cursor().execute("SET SESSION TRANSACTION READ ONLY")
        cur=c.cursor(); cur.execute(sql); columns=[d.name if hasattr(d,"name") else d[0] for d in (cur.description or [])]; rows=cur.fetchmany(501); c.rollback(); c.close()
        return {"columns":columns,"rows":[list(r) for r in rows[:500]],"truncated":len(rows)>500}
    except ImportError: raise HTTPException(501,"Install database drivers.")
    except HTTPException: raise
    except Exception as e: raise HTTPException(400,"Query failed: "+str(e)[:180])
@router.post("/copilot")
def copilot(x:Copilot,authorization:Optional[str]=Header(None)):
    uid_from(authorization); q=x.question.strip()
    if not q or len(q)>2000: raise HTTPException(400,"Question must be 1–2000 characters.")
    answer=[]; actions=[]
    if x.csv_text:
        try:
            reader=csv.DictReader(io.StringIO(x.csv_text)); cols=reader.fieldnames or []; rows=list(reader)
            nulls=sum(not (r.get(k) or "").strip() for r in rows for k in cols)
            dupes=len(rows)-len({tuple(r.get(k,"") for k in cols) for r in rows})
            answer.append(f"Dataset profile: {len(rows)} rows, {len(cols)} columns, {nulls} empty cells, {dupes} duplicate rows.")
            if nulls: actions.append({"type":"review_missing_values","label":"Review missing values","validated":True,"requires_confirmation":True})
            if dupes: actions.append({"type":"review_duplicates","label":"Review duplicate rows","validated":True,"requires_confirmation":True})
        except Exception: answer.append("CSV could not be parsed. Check its header and quoting.")
    ql=q.lower()
    if any(k in ql for k in ("normaliz","3nf","bcnf","dependency","candidate key")):
        answer.append("Validate discovered dependencies against business rules before normalizing. Run the deterministic engine, then inspect candidate keys, lossless reconstruction and dependency preservation.")
        actions.append({"type":"run_normalization","label":"Run normalization analysis","validated":True,"requires_confirmation":True})
    if any(k in ql for k in ("sql","database","postgres","mysql","query")):
        answer.append("Test a least-privilege connection first. Database query endpoint is restricted to SELECT; schema-changing SQL is not executed by this copilot.")
    if not answer: answer.append("I can review data quality, functional dependencies, normalization, ER/SQL output, and database safety. Ask about a dataset or a result for a grounded recommendation.")
    return {"source":"validated-rules","answer":" ".join(answer),"actions":actions,"note":"Suggestions only. No DDL or data-changing SQL is executed automatically."}
