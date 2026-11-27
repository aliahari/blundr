"""
Test helpers: create users straight in the test database and sign them in
with a minted JWT, instead of going through a sign-in route.
"""
import sqlite3
from datetime import datetime

from app.services.auth_service import create_access_token
from app.services.lichess_auth import encrypt_token
from tests.conftest import _TEST_DB, client


def make_user(
    username: str, *, lichess_username: str | None = None, is_owner: bool = False,
    lichess_token: str | None = None, token_expires_at: datetime | None = None,
) -> dict[str, str]:
    """Insert a user and return auth headers for them. A lichess_token is
    stored encrypted, by default with a far-future expiry."""
    con = sqlite3.connect(str(_TEST_DB))
    try:
        cur = con.execute(
            "INSERT INTO users (username, lichess_username, sync_game_types,"
            " sync_days_back, max_new_per_day, created_at, is_owner)"
            " VALUES (?, ?, '[\"rapid\", \"blitz\"]', 7, 10, ?, ?)",
            (username, lichess_username or f"{username}_li", datetime.utcnow().isoformat(" "), int(is_owner)),
        )
        user_id = cur.lastrowid
        if lichess_token is not None:
            expires = token_expires_at or datetime(2099, 1, 1)
            con.execute(
                "UPDATE users SET lichess_token_enc = ?, lichess_token_expires_at = ? WHERE id = ?",
                (encrypt_token(lichess_token), expires.isoformat(" "), user_id),
            )
        con.commit()
    finally:
        con.close()
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def user_id(headers: dict[str, str]) -> int:
    return client.get("/api/auth/me", headers=headers).json()["id"]
