"""Google sign-in (OAuth 2.0 authorization code flow, OpenID Connect).

Jivaka only asks for the person's verified email address and name. Whether
they may get in is decided by the invite list, not by Google."""

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from jivaka.config import Settings

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleSignInError(Exception):
    pass


@dataclass
class GoogleIdentity:
    email: str
    name: str


def redirect_uri(settings: Settings) -> str:
    return settings.public_base_url.rstrip("/") + "/auth/callback"


def authorize_url(settings: Settings, state: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(settings),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def fetch_identity(settings: Settings, code: str) -> GoogleIdentity:
    """Swaps the code Google sent back for tokens, then reads the account's
    email from Google's userinfo endpoint. Both calls go straight to Google
    over TLS, so the answer needs no further signature check."""
    async with httpx.AsyncClient(timeout=15) as client:
        token_response = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri(settings),
                "grant_type": "authorization_code",
            },
        )
        if token_response.status_code != 200:
            raise GoogleSignInError(f"Google refused the sign-in code ({token_response.status_code}).")
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise GoogleSignInError("Google sent no access token.")
        info_response = await client.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        if info_response.status_code != 200:
            raise GoogleSignInError(f"Couldn't read the Google account ({info_response.status_code}).")
    info = info_response.json()
    email = (info.get("email") or "").strip().lower()
    if not email or not info.get("email_verified"):
        raise GoogleSignInError("This Google account has no verified email address.")
    return GoogleIdentity(email=email, name=info.get("name") or "")
