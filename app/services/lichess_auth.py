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
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

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
