"""Sign in with Lichess: the OAuth routes, with lichess.org faked (no network)."""
import json
import sqlite3
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.config import settings
from app.main import app
from app.routes.auth import get_oauth_http
from app.services.lichess_auth import decrypt_token
from tests.conftest import _TEST_DB, client

LICHESS_TOKEN = "lio_fake_token_123"


class FakeLichess:
    """Answers /api/token and /api/account like lichess.org does."""

    def __init__(self, user_id="alice", username="Alice", token_status=200):
        self.user_id, self.username, self.token_status = user_id, username, token_status
        self.token_requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/token":
            self.token_requests.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={
                "token_type": "Bearer", "access_token": LICHESS_TOKEN, "expires_in": 31536000})
        if request.url.path == "/api/account":
            assert request.headers["Authorization"] == f"Bearer {LICHESS_TOKEN}"
            return httpx.Response(200, json={
                "id": self.user_id, "username": self.username,
                "perfs": {"blitz": {"rating": 1650, "games": 10}, "rapid": {"rating": 1720}}})
        return httpx.Response(404)


@pytest.fixture
def lichess():
    fake = FakeLichess()
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: fake.handler(r)))
    app.dependency_overrides[get_oauth_http] = lambda: http
    client.cookies.clear()
    yield fake
    app.dependency_overrides.pop(get_oauth_http, None)
    client.cookies.clear()


def _start() -> str:
    resp = client.get("/api/auth/lichess/start", follow_redirects=False)
    assert resp.status_code == 302
    return parse_qs(urlparse(resp.headers["location"]).query)["state"][0]


def _callback(**params) -> httpx.Response:
    return client.get("/api/auth/lichess/callback", params=params, follow_redirects=False)


def _jwt(resp) -> str:
    loc = resp.headers["location"]
    assert loc.startswith("/#auth="), loc
    return loc.removeprefix("/#auth=")


def _me(jwt: str) -> dict:
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {jwt}"}).json()


def _user_count() -> int:
    con = sqlite3.connect(str(_TEST_DB))
    try:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        con.close()


def _row(user_id: int) -> dict:
    con = sqlite3.connect(str(_TEST_DB))
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
    finally:
        con.close()


def test_start_redirects_to_lichess_and_sets_cookie(lichess):
    resp = client.get("/api/auth/lichess/start", follow_redirects=False)
    assert resp.status_code == 302
    assert urlparse(resp.headers["location"]).netloc == "lichess.org"
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith("lichess_oauth=")
    assert "httponly" in cookie.lower()
    assert "path=/api/auth/lichess" in cookie.lower()


def test_start_503_when_not_configured(lichess, monkeypatch):
    monkeypatch.setattr(settings, "LICHESS_TOKEN_KEY", "")
    resp = client.get("/api/auth/lichess/start", follow_redirects=False)
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Sign-in isn't configured"


def test_callback_creates_user(lichess):
    state = _start()
    resp = _callback(code="c0de", state=state)
    me = _me(_jwt(resp))
    assert me["lichess_username"] == "Alice"
    assert me["username"] == "Alice"
    row = _row(me["id"])
    assert LICHESS_TOKEN not in row["lichess_token_enc"]
    assert decrypt_token(row["lichess_token_enc"]) == LICHESS_TOKEN
    assert row["lichess_token_expires_at"] is not None
    assert json.loads(row["lichess_ratings"]) == {"blitz": 1650, "rapid": 1720}
    sent = lichess.token_requests[0]
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "c0de"
    assert sent["redirect_uri"] == "http://testserver/api/auth/lichess/callback"
    assert sent["client_id"] == "blundr.ch"
    assert len(sent["code_verifier"]) == 64


def test_callback_same_lichess_id_same_user(lichess):
    lichess.user_id, lichess.username = "bobby", "Bobby"
    first = _me(_jwt(_callback(code="a", state=_start())))
    lichess.username = "BOBBY"
    second = _me(_jwt(_callback(code="b", state=_start())))
    assert second["id"] == first["id"]
    assert second["lichess_username"] == "BOBBY"


def test_callback_bad_state(lichess):
    before = _user_count()
    _start()
    assert _callback(code="c", state="wrong").headers["location"] == "/#auth_error=state"
    client.cookies.clear()
    assert _callback(code="c", state="anything").headers["location"] == "/#auth_error=state"
    assert _user_count() == before


def test_callback_denied(lichess):
    state = _start()
    resp = _callback(error="access_denied", error_description="denied", state=state)
    assert resp.headers["location"] == "/#auth_error=denied"


def test_callback_lichess_failure(lichess):
    lichess.token_status = 400
    resp = _callback(code="c", state=_start())
    assert resp.headers["location"] == "/#auth_error=lichess"


def test_callback_clears_cookie(lichess):
    resp = _callback(code="c", state=_start())
    assert 'lichess_oauth=""' in resp.headers["set-cookie"] or "max-age=0" in resp.headers["set-cookie"].lower()


def test_owner_flag(lichess, monkeypatch):
    monkeypatch.setattr(settings, "OWNER_LICHESS_USERNAME", "carol")
    lichess.user_id, lichess.username = "carol", "Carol"
    assert _me(_jwt(_callback(code="c", state=_start())))["is_owner"] is True
    lichess.user_id, lichess.username = "dan", "Dan"
    assert _me(_jwt(_callback(code="d", state=_start())))["is_owner"] is False


def test_token_never_in_response(lichess):
    resp = client.get("/api/auth/lichess/start", follow_redirects=False)
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    cb = _callback(code="c", state=state)
    for r in (resp, cb):
        assert LICHESS_TOKEN not in r.text
        assert LICHESS_TOKEN not in str(r.headers)
    assert LICHESS_TOKEN not in json.dumps(_me(_jwt(cb)))
