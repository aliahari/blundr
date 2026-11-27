"""
Sign in with Lichess: OAuth 2 with PKCE, and the user's Lichess token kept
encrypted at rest (specs/features/lichess-sign-in/design.md).

Lichess accepts clients it hasn't registered: the client id is any unique
string and there is no client secret. No scope is asked for: a token with
none is enough for the opening explorer and public game data.
"""
import base64
import hashlib
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from ..models.db_models import User

LICHESS_OAUTH_URL = "https://lichess.org/oauth"
REDIRECT_PATH = "/api/auth/lichess/callback"


def _fernet() -> Fernet | None:
    try:
        return Fernet(settings.LICHESS_TOKEN_KEY.encode())
    except (ValueError, TypeError):
        return None


def sign_in_configured() -> bool:
    """True when LICHESS_TOKEN_KEY is a valid Fernet key, so tokens can be stored."""
    return _fernet() is not None


def encrypt_token(token: str) -> str:
    fernet = _fernet()
    if fernet is None:
        raise RuntimeError("LICHESS_TOKEN_KEY isn't set to a valid key")
    return fernet.encrypt(token.encode()).decode()


def decrypt_token(enc: str) -> str | None:
    """The token, or None when it can't be decrypted (bad data, another key, no key)."""
    fernet = _fernet()
    if fernet is None:
        return None
    try:
        return fernet.decrypt(enc.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return None


def challenge_for(verifier: str) -> str:
    """The PKCE S256 challenge: base64url(sha256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_pkce() -> tuple[str, str]:
    """A fresh (verifier, challenge) pair."""
    verifier = secrets.token_urlsafe(48)  # 64 characters
    return verifier, challenge_for(verifier)


def redirect_uri() -> str:
    return f"{settings.PUBLIC_URL.rstrip('/')}{REDIRECT_PATH}"


def authorize_url(state: str, challenge: str) -> str:
    return f"{LICHESS_OAUTH_URL}?" + urlencode({
        "response_type": "code",
        "client_id": settings.LICHESS_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    })


def user_lichess_token(user: "User") -> str | None:
    """The user's Lichess token, or None when there's none, it has expired,
    or it can't be decrypted. Every Lichess call made for a user goes
    through here."""
    if not user.lichess_token_enc:
        return None
    expires = user.lichess_token_expires_at
    if expires is not None and expires <= datetime.now(UTC).replace(tzinfo=None):
        return None
    return decrypt_token(user.lichess_token_enc)


async def forget_lichess_token(db: "AsyncSession", user: "User") -> None:
    """Lichess rejected the token: clear it, so the app asks to sign in again."""
    user = await db.merge(user)
    user.lichess_token_enc = None
    user.lichess_token_expires_at = None
    await db.commit()
