"""
Authentication routes: register, login, current user, password reset.
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
from ..models.db_models import PasswordResetToken, User
from ..models.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserResponse,
)
from ..services.auth_service import (
    create_access_token,
    generate_reset_token,
    hash_password,
    verify_password,
)
from ..services.email_service import send_password_reset_email
from ..services.lichess_auth import (
    authorize_url,
    encrypt_token,
    new_pkce,
    redirect_uri,
    sign_in_configured,
)
from .deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Generic response for /forgot-password regardless of outcome — the account
# existing or not existing must not be observable from the response.
_FORGOT_PASSWORD_ACK = MessageResponse(
    message="If an account exists for that email, a reset link has been sent."
)


def _user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        lichess_username=user.lichess_username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        is_owner=bool(user.is_owner),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: RegisterRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Create an account and return a token (auto-login)."""
    existing_username = await db.scalar(select(User).where(User.username == request.username))
    if existing_username is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{request.username}' is already taken",
        )

    existing_email = await db.scalar(select(User).where(User.email == request.email))
    if existing_email is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        )

    # The whole app revolves around this account — catch typos at the door.
    # Best-effort: None (Lichess unreachable) doesn't block registration.
    if settings.VALIDATE_LICHESS_ACCOUNT:
        exists = await http_request.app.state.lichess_client.user_exists(request.lichess_username)
        if exists is False:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Lichess user '{request.lichess_username}' does not exist. "
                       "Check the spelling.",
            )

    user = User(
        username=request.username,
        email=request.email,
        password_hash=hash_password(request.password),
        lichess_username=request.lichess_username,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(f"Registered new user '{user.username}' (id={user.id})")
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """Verify credentials and return a token."""
    user = await db.scalar(select(User).where(User.username == request.username))
    # Same error for unknown user and wrong password — don't leak which usernames exist
    if user is None or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    """Return the authenticated user."""
    return _user_to_response(user)


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    request: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Start a password reset. Always returns the same message — whether the
    email exists is never observable from this endpoint's response.
    """
    user = await db.scalar(select(User).where(User.email == request.email))
    if user is not None:
        now = datetime.now(UTC).replace(tzinfo=None)
        reset = PasswordResetToken(
            user_id=user.id,
            token=generate_reset_token(),
            expires_at=now + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRES_MINUTES),
        )
        db.add(reset)
        await db.commit()

        reset_link = f"{settings.FRONTEND_URL}/?reset_token={reset.token}"
        try:
            await send_password_reset_email(user.email, reset_link)
        except Exception:
            # Delivery failures must not surface to the client (same
            # response either way) — email_service already logs internally.
            logger.exception(f"send_password_reset_email raised for user {user.id}")

    return _FORGOT_PASSWORD_ACK


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    request: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Complete a password reset given a valid, unexpired, unused token."""
    reset = await db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token == request.token)
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    if (
        reset is None
        or reset.used_at is not None
        or reset.expires_at < now
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired. Request a new one.",
        )

    user = await db.get(User, reset.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired. Request a new one.",
        )

    user.password_hash = hash_password(request.new_password)
    reset.used_at = now
    await db.commit()

    logger.info(f"Password reset completed for user {user.id}")
    return MessageResponse(message="Password updated. You can log in now.")


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
                    username=username, password_hash="")  # password_hash: removed in Task 4
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
