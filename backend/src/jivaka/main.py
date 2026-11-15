from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from jivaka.api.auth_routes import router as auth_router
from jivaka.api.chat_routes import router as chat_router
from jivaka.api.routes import router
from jivaka.auth.deps import optional_user
from jivaka.auth.store import User

app = FastAPI(title="Jivaka API")
app.include_router(router)
app.include_router(chat_router)
app.include_router(auth_router)

_WEB_DIR = Path(__file__).parent / "web"
_INDEX_HTML = (_WEB_DIR / "index.html").read_text(encoding="utf-8")
_CHAT_HTML = (_WEB_DIR / "chat.html").read_text(encoding="utf-8")
_LOGIN_HTML = (_WEB_DIR / "login.html").read_text(encoding="utf-8")
_ADMIN_HTML = (_WEB_DIR / "admin.html").read_text(encoding="utf-8")
_AUTH_JS = (_WEB_DIR / "auth.js").read_text(encoding="utf-8")


def _to_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def index(user: Optional[User] = Depends(optional_user)) -> Response:
    if user is None:
        return _to_login()
    if not user.can_ingest:
        # Students only chat; the ingest page lists uploads they can't make.
        return RedirectResponse("/chat", status_code=303)
    return HTMLResponse(_INDEX_HTML)


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(user: Optional[User] = Depends(optional_user)) -> Response:
    if user is None:
        return _to_login()
    return HTMLResponse(_CHAT_HTML)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(user: Optional[User] = Depends(optional_user)) -> Response:
    if user is None:
        return _to_login()
    if not user.is_admin:
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(_ADMIN_HTML)


@app.get("/login", response_class=HTMLResponse)
async def login_page(user: Optional[User] = Depends(optional_user)) -> Response:
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(_LOGIN_HTML)


@app.get("/auth.js")
async def auth_js() -> Response:
    return Response(_AUTH_JS, media_type="text/javascript")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
