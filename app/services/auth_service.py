"""
Authentication: password hashing (bcrypt), JWT issuing/verification, and
password-reset token generation.
"""
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from ..config import settings


def hash_password(password: str) -> str:
    """Hash a password with bcrypt (salt included in the hash)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed stored hash — treat as non-match rather than a 500
        return False


def create_access_token(user_id: int) -> str:
    """Issue a signed JWT for the given user id."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRES_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> int:
    """
    Verify a JWT and return the user id it identifies.

    Raises:
        jwt.InvalidTokenError (or a subclass) if the token is invalid/expired.
    """
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    return int(payload["sub"])


def generate_reset_token() -> str:
    """A URL-safe random token for password reset — opaque, not a JWT (see PasswordResetToken)."""
    return secrets.token_urlsafe(32)
