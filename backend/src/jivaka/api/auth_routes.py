"""Sign-in pages and the admin's user list."""

import logging
import secrets
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator

from jivaka.auth import google
from jivaka.auth.deps import SESSION_COOKIE, admin_user, current_user
from jivaka.auth.store import User, get_store
from jivaka.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()

STATE_COOKIE = "jivaka_oauth_state"


def _login_redirect(error: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?error={error}", status_code=303)
    response.delete_cookie(STATE_COOKIE)
    return response


@router.get("/auth/google")
async def start_google_sign_in() -> RedirectResponse:
    settings = get_settings()
    if not settings.google_client_id:
        return _login_redirect("not_configured")
    state = secrets.token_urlsafe(24)
    response = RedirectResponse(google.authorize_url(settings, state), status_code=303)
    response.set_cookie(
        STATE_COOKIE, state, max_age=600, httponly=True, secure=settings.cookie_secure, samesite="lax"
    )
    return response


@router.get("/auth/callback")
async def google_callback(
    request: Request, code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    settings = get_settings()
    expected_state = request.cookies.get(STATE_COOKIE)
    if error:
        return _login_redirect("cancelled")
    if not code or not expected_state or not secrets.compare_digest(state, expected_state):
        return _login_redirect("expired")
    try:
        identity = await google.fetch_identity(settings, code)
    except google.GoogleSignInError as exc:
        logger.warning("Google sign-in failed: %s", exc)
        return _login_redirect("google")

    store = get_store()
    user = store.get_user(identity.email)
    if user is None and identity.email in settings.admin_email_set():
        user = store.add_user(identity.email, "admin", name=identity.name, invited_by="ADMIN_EMAILS")
    if user is None:
        logger.info("Sign-in refused for %s: not invited", identity.email)
        return _login_redirect("not_invited")
    if not user.active:
        return _login_redirect("disabled")

    store.record_login(user.email, identity.name)
    token = store.create_session(user.email, settings.session_days)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(STATE_COOKIE)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return response


@router.post("/auth/logout")
async def logout(request: Request) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        get_store().delete_session(token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return {
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "can_ingest": user.can_ingest,
        "can_see_patient_records": user.can_see_patient_records,
        "auth_enabled": get_settings().auth_enabled,
    }


# Admin: invite list


class NewUser(BaseModel):
    email: str = Field(..., max_length=254)
    role: Literal["admin", "doctor", "student"]
    name: str = Field("", max_length=200)

    @field_validator("email")
    @classmethod
    def looks_like_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or " " in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("enter a full email address")
        return value


class UserChange(BaseModel):
    role: Optional[Literal["admin", "doctor", "student"]] = None
    active: Optional[bool] = None
    name: Optional[str] = Field(None, max_length=200)


@router.get("/admin/users")
async def list_users(_: User = Depends(admin_user)) -> list[dict]:
    return [u.to_dict() for u in get_store().list_users()]


@router.post("/admin/users")
async def invite_user(body: NewUser, admin: User = Depends(admin_user)) -> dict:
    store = get_store()
    if store.get_user(body.email) is not None:
        raise HTTPException(status_code=409, detail=f"{body.email} is already on the list.")
    return store.add_user(body.email, body.role, name=body.name, invited_by=admin.email).to_dict()


@router.patch("/admin/users/{email}")
async def change_user(email: str, body: UserChange, admin: User = Depends(admin_user)) -> dict:
    store = get_store()
    if store.get_user(email) is None:
        raise HTTPException(status_code=404, detail="No such user.")
    if email.lower() == admin.email and (body.role not in (None, "admin") or body.active is False):
        raise HTTPException(status_code=400, detail="You can't remove your own admin access.")
    return store.update_user(email, role=body.role, active=body.active, name=body.name).to_dict()  # type: ignore[union-attr]


@router.delete("/admin/users/{email}")
async def remove_user(email: str, admin: User = Depends(admin_user)) -> dict:
    if email.lower() == admin.email:
        raise HTTPException(status_code=400, detail="You can't remove yourself.")
    if not get_store().delete_user(email):
        raise HTTPException(status_code=404, detail="No such user.")
    return {"removed": email.lower()}
