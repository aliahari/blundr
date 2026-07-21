"""
Authentication routes: register, login, current user, password reset.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
        now = datetime.now(timezone.utc).replace(tzinfo=None)
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
    now = datetime.now(timezone.utc).replace(tzinfo=None)
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
