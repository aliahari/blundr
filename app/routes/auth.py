"""
Authentication routes: sign in with Lichess (the only way in) and the
current user. See specs/features/lichess-sign-in/design.md.
"""
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..models.db_models import User
from ..models.schemas import UserResponse
from ..services.auth_service import create_access_token
from ..services.lichess_auth import (
    authorize_url,
    encrypt_token,
    new_pkce,
    redirect_uri,
    sign_in_configured,
    user_lichess_token,
)
from .deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        lichess_username=user.lichess_username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        is_owner=bool(user.is_owner),
        lichess_connected=user_lichess_token(user) is not None,
    )


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    """Return the authenticated user."""
    return _user_to_response(user)


# --- Sign in with Lichess (specs/features/lichess-sign-in/design.md) -------

OAUTH_COOKIE = "lichess_oauth"
OAUTH_COOKIE_PATH = "/api/auth/lichess"
OAUTH_COOKIE_MAX_AGE = 600
LICHESS_TOKEN_URL = "https://lichess.org/api/token"
LICHESS_ACCOUNT_URL = "https://lichess.org/api/account"


def get_oauth_http(request: Request) -> httpx.AsyncClient:
    """The HTTP client for lichess.org sign-in calls (overridden in tests)."""
    return request.app.state.oauth_http


def _auth_error(code: str) -> RedirectResponse:
    resp = RedirectResponse(f"/#auth_error={code}", status_code=status.HTTP_302_FOUND)
    resp.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
    return resp


def _ratings(perfs: dict) -> dict[str, int]:
    return {
        speed: perf["rating"]
        for speed, perf in (perfs or {}).items()
        if isinstance(perf, dict) and isinstance(perf.get("rating"), int)
    }


@router.get("/lichess/start")
async def lichess_start() -> RedirectResponse:
    """Send the browser to lichess.org to approve signing in."""
    if not sign_in_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in isn't configured")
    state = secrets.token_urlsafe(24)
    verifier, challenge = new_pkce()
    cookie = jwt.encode(
        {"state": state, "verifier": verifier,
         "exp": datetime.now(UTC) + timedelta(seconds=OAUTH_COOKIE_MAX_AGE)},
        settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM,
    )
    resp = RedirectResponse(authorize_url(state, challenge), status_code=status.HTTP_302_FOUND)
    resp.set_cookie(
        OAUTH_COOKIE, cookie, max_age=OAUTH_COOKIE_MAX_AGE, path=OAUTH_COOKIE_PATH,
        httponly=True, samesite="lax", secure=settings.PUBLIC_URL.startswith("https://"),
    )
    return resp


@router.get("/lichess/callback")
async def lichess_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_oauth_http),
) -> RedirectResponse:
    """Lichess sends the browser back here: exchange the code, sign the user in."""
    if error:
        return _auth_error("denied" if error == "access_denied" else "lichess")
    try:
        saved = jwt.decode(request.cookies.get(OAUTH_COOKIE, ""), settings.JWT_SECRET,
                           algorithms=[settings.JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        return _auth_error("state")
    if not state or not code or not secrets.compare_digest(state, str(saved.get("state", ""))):
        return _auth_error("state")

    try:
        token_resp = await http.post(LICHESS_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": saved["verifier"],
            "redirect_uri": redirect_uri(),
            "client_id": settings.LICHESS_OAUTH_CLIENT_ID,
        })
        token_resp.raise_for_status()
        grant = token_resp.json()
        access_token = grant["access_token"]
        account_resp = await http.get(
            LICHESS_ACCOUNT_URL, headers={"Authorization": f"Bearer {access_token}"})
        account_resp.raise_for_status()
        account = account_resp.json()
        lichess_id, username = str(account["id"]).lower(), str(account["username"])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        # Never log the response bodies: the token response holds the token
        logger.warning("Lichess sign-in failed: %s", type(exc).__name__)
        return _auth_error("lichess")

    user = await db.scalar(select(User).where(
        User.auth_provider == "lichess", User.provider_user_id == lichess_id))
    if user is None:
        user = User(auth_provider="lichess", provider_user_id=lichess_id,
                    username=username)
        db.add(user)
    expires_in = grant.get("expires_in")
    user.username = username
    user.lichess_username = username
    user.lichess_token_enc = encrypt_token(access_token)
    user.lichess_token_expires_at = (
        datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=int(expires_in))
        if isinstance(expires_in, (int, float)) else None
    )
    user.lichess_ratings = json.dumps(_ratings(account.get("perfs")))
    owner = settings.OWNER_LICHESS_USERNAME.strip().lower()
    user.is_owner = bool(owner) and username.lower() == owner
    await db.commit()
    await db.refresh(user)

    resp = RedirectResponse(f"/#auth={create_access_token(user.id)}",
                            status_code=status.HTTP_302_FOUND)
    resp.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
    return resp
