"""
Drop-in router for the new features. In YOUR existing FastAPI app:

    from backend.api.plus_routes import router as plus_router
    app.include_router(plus_router)

All endpoints are stateless (schema + optional data in, JSON out), so they work with or without your PostgreSQL persistence layer.
"""
import csv
import io
from functools import lru_cache
from typing import List, Optional

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from backend.ai.ai_features import IDLIKE, explain, name_tables, suggest_fds
from backend.ai.llm_client import OfflineClient, get_client
from backend.engine.data_tools import discover_keys, infer_types, verify_reconstruction
from backend.engine.er_diagram import DIALECTS, build_er, to_ddl, to_mermaid
from backend.engine.export import build_zip
from backend.engine.fd_engine import FD, candidate_keys, project_fds
from backend.engine.mvd_engine import MVD, decompose_4nf, discover_mvds, violations_4nf
from backend.engine.whatif import analyze, what_if

router = APIRouter(prefix="/plus", tags=["plus"])
MAX_ROWS, MAX_COLS = 20_000, 30


@lru_cache
def llm():
    return get_client()


class FDIn(BaseModel):
    lhs: List[str]
    rhs: List[str]


class MVDIn(BaseModel):
    lhs: List[str]
    rhs: List[str]


class ProfileIn(BaseModel):
    csv_text: str
    use_ai: bool = True


class AnalyzeIn(BaseModel):
    attributes: Optional[List[str]] = None
    fds: List[FDIn]
    csv_text: Optional[str] = None
    target: str = "3NF"                      # "3NF" or "BCNF"
    use_ai: bool = True
    dialect: str = "postgresql"              # postgresql | mysql | oracle


class FourNFIn(BaseModel):
    attributes: Optional[List[str]] = None
    fds: List[FDIn] = []
    mvds: List[MVDIn]
    csv_text: Optional[str] = None
    use_ai: bool = True
    dialect: str = "postgresql"


class WhatIfIn(BaseModel):
    attributes: List[str]
    fds: List[FDIn]
    changes: List[dict]


def parse_csv(text):
    try:
        reader = csv.DictReader(io.StringIO(text.strip()))
        cols = [c.strip() for c in (reader.fieldnames or [])]
        rows = [{c.strip(): (v or "").strip() for c, v in r.items() if c is not None} for r, _ in zip(reader, range(MAX_ROWS + 1))]
    except csv.Error as e:
        raise HTTPException(400, f"could not read CSV: {e}")
    if len(cols) < 2 or not rows: raise HTTPException(400, "CSV needs a header row, at least 2 columns and 1 data row")
    if len(cols) != len(set(cols)): raise HTTPException(400, "duplicate column names")
    if len(cols) > MAX_COLS: raise HTTPException(400, f"at most {MAX_COLS} columns")
    if len(rows) > MAX_ROWS: raise HTTPException(400, f"at most {MAX_ROWS} rows")
    return cols, rows


def _fds(items, attrs):
    out = []
    for f in items:
        if not f.lhs or not f.rhs or not set(f.lhs) | set(f.rhs) <= set(attrs) or set(f.lhs) & set(f.rhs):
            raise HTTPException(400, f"invalid FD: {f.lhs} -> {f.rhs}")
        out.append(FD(f.lhs, f.rhs))
    return out


def _resolve(attributes, csv_text):
    rows = None
    if csv_text:
        cols, rows = parse_csv(csv_text)
        attributes = attributes or cols
    if not attributes: raise HTTPException(400, "give attributes or csv_text")
    if rows and not set(attributes) <= set(rows[0]): raise HTTPException(400, "attributes must be columns of the CSV")
    return list(dict.fromkeys(attributes)), rows


def _dialect(d):
    if d not in DIALECTS: raise HTTPException(400, f"dialect must be one of {sorted(DIALECTS)}")
    return d


def _package(attrs, fds, relations, rows, client, use_ai, dialect="postgresql"):  # client is already Offline when use_ai is False
    """Names + ER + DDL + reconstruction proof for a chosen decomposition."""
    relations = [frozenset(r) for r in sorted(relations, key=sorted)]
    types = infer_types(rows) if rows else {}
    draft = build_er(relations, fds)
    pks = [e["primary_key"] for e in draft["entities"]]
    named = name_tables(client, [sorted(r) for r in relations], pks)
    er = build_er(relations, fds, names=dict(enumerate(named["names"])), types=types)
    return {"table_names": named, "relations": [sorted(r) for r in relations], "er": er, "mermaid": to_mermaid(er), "ddl": to_ddl(er, dialect),
            "reconstruction": verify_reconstruction(rows, relations) if rows else None}


@router.post("/profile")
def profile(body: ProfileIn, client=Depends(llm)):
    cols, rows = parse_csv(body.csv_text)
    sug = suggest_fds(client if body.use_ai else OfflineClient(), cols, rows)
    keys = sorted(discover_keys(rows, cols), key=lambda k: (len(k), -sum(bool(IDLIKE.search(c)) for c in k), sum(cols.index(c) for c in k)))[:8]
    return {"columns": cols, "row_count": len(rows), "types": infer_types(rows), "preview": rows[:5], "fd_suggestions": sug,
            "key_candidates": [{"columns": k, "recommended": i == 0} for i, k in enumerate(keys)]}


@router.post("/analyze")
def analyze_endpoint(body: AnalyzeIn, client=Depends(llm)):
    if body.target not in ("3NF", "BCNF"): raise HTTPException(400, "target must be 3NF or BCNF")
    _dialect(body.dialect)
    attrs, rows = _resolve(body.attributes, body.csv_text)
    fds = _fds(body.fds, attrs)
    a = analyze(attrs, fds)
    key = "decomposition_3nf" if body.target == "3NF" else "decomposition_bcnf"
    c = client if body.use_ai else OfflineClient()
    out = {"analysis": a, "target": body.target, **_package(attrs, fds, a[key]["relations"], rows, c, body.use_ai, body.dialect), "explanation": explain(c, a, body.target)}
    if rows:
        out["mvd_candidates"] = discover_mvds(rows, attrs, fds)
    return out


@router.post("/4nf")
def fourth_nf(body: FourNFIn, client=Depends(llm)):
    attrs, rows = _resolve(body.attributes, body.csv_text)
    fds = _fds(body.fds, attrs)
    mvds = []
    for m in body.mvds:
        if not m.lhs or not m.rhs or not set(m.lhs) | set(m.rhs) <= set(attrs): raise HTTPException(400, f"invalid MVD: {m.lhs} ->> {m.rhs}")
        mvds.append(MVD(m.lhs, m.rhs))
    steps = []
    relations = decompose_4nf(attrs, fds, mvds, steps)
    c = client if body.use_ai else OfflineClient()
    return {"violations_before": [{"kind": k, "lhs": sorted(x), "rhs": sorted(y)} for k, x, y in violations_4nf(attrs, fds, mvds)],
            "steps": steps, "relations": [sorted(r) for r in relations], **_package(attrs, fds, relations, rows, c, body.use_ai, _dialect(body.dialect))}


@router.post("/whatif")
def whatif_endpoint(body: WhatIfIn):
    attrs = list(dict.fromkeys(body.attributes))
    try:
        return what_if(attrs, _fds(body.fds, attrs), body.changes)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


class ERIn(BaseModel):
    relations: List[List[str]]
    fds: List[FDIn]
    names: Optional[List[str]] = None


@router.post("/er")
def er_endpoint(body: ERIn):
    attrs = sorted(set().union(*map(set, body.relations)))
    er = build_er([set(r) for r in body.relations], _fds(body.fds, attrs), names=dict(enumerate(body.names or [])))
    return {"er": er, "mermaid": to_mermaid(er), "ddl": to_ddl(er)}


IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")


class ExportIn(BaseModel):
    csv_text: str
    fds: List[FDIn] = []
    relations: List[List[str]]               # exactly what /analyze returned, so the export matches what the user saw
    names: List[str]
    dialect: str = "postgresql"


@router.post("/export")
def export_endpoint(body: ExportIn):
    """ZIP with one CSV per table and schema.sql (CREATE, INSERT, foreign keys). 409 if the data contradicts a primary key."""
    _dialect(body.dialect)
    cols, rows = parse_csv(body.csv_text)
    if len(body.names) != len(body.relations) or len(set(body.names)) != len(body.names) or not all(IDENT.match(n) for n in body.names):
        raise HTTPException(400, "names must be unique identifiers, one per relation")
    if not all(set(r) <= set(cols) and r for r in body.relations): raise HTTPException(400, "relations must use columns of the CSV")
    fds = _fds(body.fds, cols)
    rels = [frozenset(r) for r in body.relations]
    er = build_er(rels, fds, names=dict(enumerate(body.names)), types=infer_types(rows))
    data, conflicts = build_zip(er, rows, infer_types(rows), body.dialect, verify_reconstruction(rows, rels))
    if conflicts:
        raise HTTPException(409, {"message": "Your data contradicts a primary key, so the normalized tables cannot be loaded. Fix or remove the conflicting rows first.",
                                  "conflicts": conflicts})
    return Response(data, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="normalized_database.zip"'})
