from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from jivaka.api.routes import router

app = FastAPI(title="Jivaka Ingestion API")
app.include_router(router)

_INDEX_HTML = (Path(__file__).parent / "web" / "index.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _INDEX_HTML


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
