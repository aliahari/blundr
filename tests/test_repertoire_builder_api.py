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

    async def __call__(self, db, http, *, user_id, token, key, band, timeout=None):
        if token is None:
            return self.data.get(key) if key in self.data and self.delay == 0 else None
        self.calls.append(key)
        if self.delay:
            if timeout is not None and timeout < self.delay:  # as the real one: give up in time
                await asyncio.sleep(max(0.0, timeout))
                return None
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


def test_old_prefetch_is_cancelled_before_the_step(explorer):
    from tests.helpers import user_id

    h = make_user("bld_cancel", lichess_token="lio_i")
    uid = user_id(h)
    state = {}

    async def plant():
        async def forever():
            await asyncio.sleep(60)
        state["task"] = asyncio.create_task(forever())
        app.state.builder_jobs[uid] = state["task"]
    client.portal.call(plant)
    seen = {}
    real = repertoire_routes._next_response

    async def spy(*args, **kwargs):
        seen["cancelling"] = bool(state["task"].cancelling()) or state["task"].cancelled()
        return await real(*args, **kwargs)
    repertoire_routes._next_response = spy
    try:
        assert _next(h).status_code == 200
    finally:
        repertoire_routes._next_response = real
    assert seen["cancelling"] is True


def _explore(h, color="white", line=""):
    return client.get(f"/api/repertoire/{color}/explore", headers=h, params={"line": line})


def test_explore_opponent_turn_lists_named_replies(explorer):
    explorer.data[key_after("e4")] = [mv("e7e5", 60), mv("c7c5", 40)]
    h = make_user("nav_opp", lichess_token="lio_n1")
    _pick(h, "white", START, "e2e4")
    body = _explore(h, line="e2e4").json()
    assert body["user_to_move"] is False
    assert body["line_san"] == ["e4"]
    replies = {r["san"]: r for r in body["replies"]}
    assert list(replies) == ["e5", "c5"]
    assert replies["e5"]["likelihood"] == pytest.approx(0.6)
    assert replies["c5"]["opening"]["name"].startswith("Sicilian Defense")
    assert replies["c5"]["completion"] == 0.0 and replies["c5"]["in_scope"] is True
    assert replies["c5"]["answered"] is False
    assert body["completion"] == pytest.approx(0.5)  # the start (1.0) prepared; 0.6 + 0.4 open
    assert body["candidates"] == []


def test_explore_user_turn_choices(explorer):
    explorer.data[START] = [ExplorerMove("e2e4", "e4", 60, 30, 6, 24), mv("d2d4", 40)]
    h = make_user("nav_user", lichess_token="lio_n2")
    body = _explore(h).json()
    assert body["user_to_move"] is True and body["likelihood"] == 1.0
    e4 = next(c for c in body["candidates"] if c["uci"] == "e2e4")
    assert (e4["level_win"], e4["level_draw"], e4["level_loss"]) == pytest.approx((0.5, 0.1, 0.4))
    assert e4["opening"] is not None  # the opening 1.e4 leads to
    assert body["replies"] == []
    assert body["repertoire_move"] is None


def test_explore_marks_your_move_and_follows_it(explorer):
    explorer.data[key_after("e4")] = [mv("e7e5", 100)]
    h = make_user("nav_mine", lichess_token="lio_n3")
    _pick(h, "white", START, "e2e4")
    assert _explore(h).json()["repertoire_move"] == "e2e4"
    deeper = _explore(h, line="e2e4,e7e5").json()
    assert deeper["user_to_move"] is True and deeper["pick_blocked_at"] is None


def test_pick_without_next_is_quick(explorer):
    h = make_user("nav_nonext", lichess_token="lio_n4")
    r = client.put("/api/repertoire/white/position", headers=h, params={"with_next": "false"},
                   json={"position_key": START, "move_uci": "e2e4"})
    assert r.status_code == 200 and r.json()["next"] is None


def test_explore_bad_line_is_400(explorer):
    h = make_user("nav_bad", lichess_token="lio_n5")
    assert _explore(h, line="e2e5").status_code == 400


def test_summary_complete_mastered_due(explorer):
    import sqlite3
    from tests.helpers import user_id
    from tests.test_blundr_features import _TEST_DB_PATH

    explorer.data[key_after("e4")] = [mv("e7e5", 60), mv("c7c5", 40)]
    h = make_user("sum_user", lichess_token="lio_s1")
    _pick(h, "white", START, "e2e4")
    _pick(h, "white", key_after("e4", "e5"), "g1f3")
    body = client.get("/api/repertoire/white/summary", headers=h).json()
    # Weighted: the start (1.0) and 1...e5 (0.6) prepared; 1...c5 (0.4) open
    assert body["completion"] == pytest.approx(1.6 / 2.0)
    assert body["goal"] == 20 and body["saved"] == 2
    assert body["cards"] == 2 and body["mastered"] == 0 and body["mastered_pct"] == 0.0
    assert body["due"] == 0 and body["new"] == 2  # never reviewed: new, not due

    # A card reviewed into a 6-day interval counts as mastered, and isn't due
    con = sqlite3.connect(str(_TEST_DB_PATH))
    con.execute(
        "UPDATE review_cards SET interval_days = 6, repetitions = 2,"
        " last_reviewed_at = '2026-11-20', due_at = '2099-01-01'"
        " WHERE id = (SELECT MIN(id) FROM review_cards WHERE user_id = ? AND kind = 'opening')",
        (user_id(h),))
    con.commit()
    con.close()
    body = client.get("/api/repertoire/white/summary", headers=h).json()
    assert body["mastered"] == 1 and body["mastered_pct"] == 0.5
    assert body["due"] == 0 and body["new"] == 1  # the mastered card is due in 2099
    black = client.get("/api/repertoire/black/summary", headers=h).json()
    assert black["saved"] == 0 and black["cards"] == 0 and black["mastered_pct"] is None
