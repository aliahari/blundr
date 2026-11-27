"""Each user's own Lichess token on their Lichess requests (no network)."""
import sqlite3
from datetime import datetime

import httpx
import pytest

from app.main import app
from app.models.db_models import User
from app.services.lichess_auth import user_lichess_token
from tests.conftest import _TEST_DB, client
from tests.helpers import make_user, user_id


class FakeLichess:
    """Records the Authorization header of each request; answers like Lichess."""

    def __init__(self, reject: set[str] = frozenset()):
        self.reject = reject
        self.auth_headers: list[str | None] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization")
        self.auth_headers.append(auth)
        if auth and auth.removeprefix("Bearer ") in self.reject:
            return httpx.Response(401, json={"error": "No such token"})
        return httpx.Response(200, text="")  # no games in the window


@pytest.fixture
def fake_lichess():
    lichess = app.state.lichess_client
    real = lichess.client
    fake = FakeLichess()
    lichess.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: fake.handler(r)),
        headers={"User-Agent": "test"},
    )
    yield fake
    lichess.client = real


def _token_columns(uid: int):
    con = sqlite3.connect(str(_TEST_DB))
    try:
        return con.execute(
            "SELECT lichess_token_enc, lichess_token_expires_at FROM users WHERE id = ?",
            (uid,)).fetchone()
    finally:
        con.close()


def _sync(h):
    resp = client.post("/api/analysis/sync", headers=h, params={"force": "true"})
    assert resp.status_code == 202, resp.text
    return resp.json()


def test_sync_sends_user_token(fake_lichess):
    h = make_user("tok_user1", lichess_token="lio_user1")
    _sync(h)
    assert fake_lichess.auth_headers[0] == "Bearer lio_user1"


def test_sync_401_forgets_token(fake_lichess):
    h = make_user("tok_user2", lichess_token="lio_revoked")
    other = make_user("tok_user3", lichess_token="lio_fine")
    fake_lichess.reject = {"lio_revoked"}
    body = _sync(h)
    # Games are public: the sync carries on without the token
    assert body["state"] == "done"
    assert fake_lichess.auth_headers[:2] == ["Bearer lio_revoked", None]
    assert _token_columns(user_id(h)) == (None, None)
    assert client.get("/api/auth/me", headers=h).json()["lichess_connected"] is False
    assert _token_columns(user_id(other))[0] is not None
    assert client.get("/api/auth/me", headers=other).json()["lichess_connected"] is True


def test_expired_token_not_used(fake_lichess):
    h = make_user("tok_user4", lichess_token="lio_old", token_expires_at=datetime(2020, 1, 1))
    _sync(h)
    assert fake_lichess.auth_headers[0] is None
    assert client.get("/api/auth/me", headers=h).json()["lichess_connected"] is False


def test_user_lichess_token_handles_missing_and_garbage():
    assert user_lichess_token(User(username="x")) is None
    assert user_lichess_token(User(username="x", lichess_token_enc="garbage",
                                   lichess_token_expires_at=datetime(2099, 1, 1))) is None
