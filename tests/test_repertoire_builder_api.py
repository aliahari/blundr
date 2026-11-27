"""The builder's endpoints: next gap, pick, coverage goal (repertoire builder, task 3)."""
import asyncio
import time

import chess
import pytest

from app.main import app
from app.routes import repertoire as repertoire_routes
from app.services import explorer_service
from app.services.engine_lines import EngineMove
from app.services.explorer_service import ExplorerMove
from app.services.masters_service import MasterMove
from tests.conftest import client
from tests.helpers import make_user

START = chess.Board().epd()
ENGINE = [EngineMove("e2e4", "e4", 30), EngineMove("d2d4", "d4", 25)]


def key_after(*sans):
    b = chess.Board()
    for s in sans:
        b.push_san(s)
    return b.epd()


def mv(uci, games):
    return ExplorerMove(uci=uci, san=uci, games=games, white=games // 2, draws=0,
                        black=games - games // 2)


class FakeExplorer:
    def __init__(self, data, delay=0.0):
        self.data, self.delay, self.calls = data, delay, []

    async def __call__(self, db, http, *, user_id, token, key, band):
        if token is None:
            return self.data.get(key) if key in self.data and self.delay == 0 else None
        self.calls.append(key)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.data.get(key)


@pytest.fixture
def explorer(monkeypatch):
    fake = FakeExplorer({})
    monkeypatch.setattr(explorer_service, "explorer_moves", fake)
    return fake


@pytest.fixture(autouse=True)
def engine(monkeypatch):
    async def fake(db, key, **kwargs):
        return list(ENGINE) if key == START else []
    monkeypatch.setattr(repertoire_routes, "get_engine_lines", fake)


@pytest.fixture(autouse=True)
def no_prefetch(monkeypatch):
    if hasattr(repertoire_routes, "schedule_prefetch"):
        monkeypatch.setattr(repertoire_routes, "schedule_prefetch", lambda *a, **k: None)


class _Book:
    available = True

    def __init__(self, moves):
        self._moves = moves

    def moves(self, keys):
        return {k: self._moves[k] for k in keys if k in self._moves}


def _next(h, color="white", **params):
    return client.get(f"/api/repertoire/{color}/next", headers=h, params=params)


def _pick(h, color, key, uci):
    r = client.put(f"/api/repertoire/{color}/position", headers=h,
                   json={"position_key": key, "move_uci": uci})
    assert r.status_code == 200, r.text
    return r.json()


def test_next_white_empty_is_start(explorer):
    h = make_user("bld_start", lichess_token="lio_a")
    body = _next(h).json()
    assert body["gap"]["key"] == START
    assert body["gap"]["likelihood"] == 1.0
    assert body["prepared"] == 0 and body["left"] == 1 and body["goal"] == 20
    assert body["source"] == "lichess" and body["reconnect"] is False


def test_pick_returns_next_gap(explorer):
    explorer.data[key_after("e4")] = [mv("e7e5", 60), mv("c7c5", 40)]
    h = make_user("bld_pick", lichess_token="lio_b")
    body = _pick(h, "white", START, "e2e4")
    nxt = body["next"]
    assert nxt["gap"]["key"] == key_after("e4", "e5")
    assert nxt["gap"]["last_reply_san"] == "e5"
    assert nxt["gap"]["last_share"] == pytest.approx(0.6)
    assert nxt["gap"]["line"] == ["e2e4", "e7e5"]
    assert nxt["gap"]["line_san"] == ["e4", "e5"]
    assert nxt["prepared"] == 1 and nxt["left"] == 2


def test_pick_off_gap_still_returns_first_gap(explorer):
    explorer.data[key_after("e4")] = [mv("e7e5", 60), mv("c7c5", 40)]
    h = make_user("bld_off", lichess_token="lio_c")
    _pick(h, "white", START, "e2e4")
    body = _pick(h, "white", key_after("e4", "c5"), "g1f3")
    assert body["next"]["gap"]["key"] == key_after("e4", "e5")


def test_goal_validation_and_effect(explorer):
    explorer.data[key_after("e4")] = [mv("e7e5", 60), mv("c7c5", 37), mv("d7d5", 3)]
    h = make_user("bld_goal", lichess_token="lio_d")
    _pick(h, "white", START, "e2e4")
    at20 = _next(h).json()
    assert client.put("/api/repertoire/white/goal", headers=h, json={"goal": 30}).status_code == 400
    r = client.put("/api/repertoire/white/goal", headers=h, json={"goal": 50})
    assert r.status_code == 200
    assert r.json()["goal"] == 50
    assert r.json()["left"] > at20["left"]  # 1...d5 (3%) is in at 1 in 50
    assert _next(h, "black").json()["goal"] == 20  # per colour


def test_falls_back_to_masters_without_token(explorer, monkeypatch):
    monkeypatch.setattr(app.state, "masters_book", _Book({key_after("e4"): [
        MasterMove("e7e5", 60, 20, 20, 20), MasterMove("c7c5", 40, 15, 10, 15)]}))
    h = make_user("bld_masters")
    _pick(h, "white", START, "e2e4")
    body = _next(h).json()
    assert body["source"] == "masters" and body["reconnect"] is True
    assert body["gap"]["key"] == key_after("e4", "e5")
    assert explorer.calls == []


def test_budget_limits_explorer_wait(monkeypatch):
    fake = FakeExplorer({key_after("e4"): [mv("e7e5", 60), mv("c7c5", 40)]}, delay=2.0)
    monkeypatch.setattr(explorer_service, "explorer_moves", fake)
    h = make_user("bld_budget", lichess_token="lio_e")
    for key, uci in ((START, "e2e4"), (key_after("e4", "e5"), "g1f3"), (key_after("e4", "c5"), "g1f3")):
        client.put("/api/repertoire/white/position", headers=h,
                   json={"position_key": key, "move_uci": uci})
    fake.calls.clear()
    t = time.monotonic()
    r = _next(h)
    assert r.status_code == 200
    assert time.monotonic() - t < 4.5
    assert len(fake.calls) <= 2


def test_level_share_on_candidates(explorer):
    explorer.data[START] = [mv("e2e4", 60), mv("d2d4", 40)]
    h = make_user("bld_level", lichess_token="lio_f")
    cands = {c["uci"]: c for c in _next(h).json()["gap"]["candidates"]}
    assert cands["e2e4"]["level_share"] == pytest.approx(0.6)
    assert cands["e2e4"]["level_score"] == pytest.approx(0.5)
    assert cands["e2e4"]["engine_rank"] == 1


def test_next_with_line_param(explorer):
    explorer.data[key_after("e4")] = [mv("e7e5", 60), mv("c7c5", 40)]
    h = make_user("bld_line", lichess_token="lio_g")
    _pick(h, "white", START, "e2e4")
    body = _next(h, line="e2e4,c7c5").json()
    assert body["gap"]["key"] == key_after("e4", "c5")
    assert _next(h, line="e2e4").status_code == 400  # the opponent's turn
    assert _next(h, line="d2d4,d7d5").status_code == 400  # not reached by the repertoire


def test_pick_does_not_wait_for_recheck(explorer, monkeypatch):
    async def slow(*args, **kwargs):
        await asyncio.sleep(5)
    monkeypatch.setattr(repertoire_routes, "recheck_games", slow)
    h = make_user("bld_fast", lichess_token="lio_h")
    t = time.monotonic()
    _pick(h, "white", START, "e2e4")
    assert time.monotonic() - t < 2


def test_accept_and_start_gone():
    h = make_user("bld_gone")
    assert client.post("/api/repertoire/white/start", headers=h).status_code in (404, 405)
    assert client.post("/api/repertoire/white/accept", headers=h,
                       json={"position_keys": [START]}).status_code in (404, 405)
