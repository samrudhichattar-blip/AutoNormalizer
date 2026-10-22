"""Standalone demo:  uvicorn backend.api.demo_app:app --reload   then open http://127.0.0.1:8000"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from backend.api.plus_routes import router
from backend.api.product_routes import router as product_router

app = FastAPI(title="AutoNormalizer Plus (demo)")
app.include_router(router)
app.include_router(product_router)


@app.get("/")
def home():
    return FileResponse(Path(__file__).resolve().parents[2] / "frontend_demo" / "index.html")
