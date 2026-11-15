"""FastAPI dependencies for who is signed in and what they may do."""

from typing import Optional

from fastapi import Depends, HTTPException, Request

from jivaka.auth.store import User, get_store
from jivaka.config import get_settings

SESSION_COOKIE = "jivaka_session"

# Who every request runs as when AUTH_ENABLED=false (local development).
DEV_USER = User(email="dev@localhost", name="Developer (sign-in off)", role="admin")


def optional_user(request: Request) -> Optional[User]:
    if not get_settings().auth_enabled:
        return DEV_USER
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return get_store().user_for_session(token)


def current_user(user: Optional[User] = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return user


def ingest_user(user: User = Depends(current_user)) -> User:
    """Admins and doctors: may upload, cancel and delete documents."""
    if not user.can_ingest:
        raise HTTPException(status_code=403, detail="Only doctors and admins can ingest or remove documents.")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can do this.")
    return user
