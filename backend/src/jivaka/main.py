from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from jivaka.api.chat_routes import router as chat_router
from jivaka.api.routes import router

app = FastAPI(title="Jivaka API")
app.include_router(router)
app.include_router(chat_router)

_WEB_DIR = Path(__file__).parent / "web"
_INDEX_HTML = (_WEB_DIR / "index.html").read_text(encoding="utf-8")
_CHAT_HTML = (_WEB_DIR / "chat.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _INDEX_HTML


@app.get("/chat", response_class=HTMLResponse)
async def chat_page() -> str:
    return _CHAT_HTML


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
