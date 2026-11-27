"""
Authentication: Blundr's own session tokens (JWT). Signing in itself goes
through Lichess (routes/auth.py, services/lichess_auth.py).
"""
from datetime import UTC, datetime, timedelta

import jwt

from ..config import settings


def create_access_token(user_id: int) -> str:
    """Issue a signed JWT for the given user id."""
    now = datetime.now(UTC)
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
