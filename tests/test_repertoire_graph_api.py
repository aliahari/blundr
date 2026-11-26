"""The repertoire graph and position endpoints, and "Start from my games" (repertoire graph, task 5)."""
from pathlib import Path

import pytest

from app.main import app
from app.routes import repertoire as repertoire_routes
from app.services.engine_lines import EngineMove
from app.services.masters_service import MastersBook, build_masters_db
from tests.conftest import client
from tests.test_repertoire_api import RUY, START, _key, _user_with_games

FIXTURE = Path(__file__).parent / "fixtures" / "masters_sample.pgn"

ENGINE = [EngineMove("e2e4", "e4", 30), EngineMove("d2d4", "d4", 25),
          EngineMove("g1f3", "Nf3", 20), EngineMove("c2c4", "c4", 15)]


@pytest.fixture
def masters(tmp_path, monkeypatch):
    path = tmp_path / "masters.sqlite"
    build_masters_db(FIXTURE, path, min_position_games=1, processes=1)
    monkeypatch.setattr(app.state, "masters_book", MastersBook(path))


@pytest.fixture
def engine(monkeypatch):
    calls = []

    async def fake(db, key, **kwargs):
        calls.append(key)
        return list(ENGINE) if key == START else []

    monkeypatch.setattr(repertoire_routes, "get_engine_lines", fake)
    return calls


@pytest.fixture
def busy_engine(monkeypatch):
    async def fake(db, key, **kwargs):
        return None

    monkeypatch.setattr(repertoire_routes, "get_engine_lines", fake)


def _set(h, color, key, uci):
    r = client.put(f"/api/repertoire/{color}/position", headers=h,
                   json={"position_key": key, "move_uci": uci})
    assert r.status_code == 200, r.text


def _position(h, color, line="", **params):
    return client.get(f"/api/repertoire/{color}/position", headers=h,
                      params={"line": line, **params})


def test_graph_shape_and_theory_source(monkeypatch, tmp_path):
    h = _user_with_games("graph_shape", [(RUY, "white", "1-0")] * 3)
    body = client.get("/api/repertoire/white/graph", headers=h).json()
    keys = {n["key"] for n in body["nodes"]}
    assert {START, _key("e4"), _key(*RUY)} <= keys
    assert body["theory_source"] == "dataset"
    assert body["empty_repertoire"] is True
    assert body["avg_moves_in_repertoire"] is None
    root = next(n for n in body["nodes"] if n["key"] == START)
    assert root["games"] == 3 and root["parent"] is None and root["user_to_move"] is True
    e4 = next(e for e in body["edges"] if e["parent"] == START)
    assert (e4["uci"], e4["games"], e4["w"], e4["theory"]) == ("e2e4", 3, 3, True)
    assert "line" not in root  # rebuilt from parents on the client

    path = tmp_path / "m.sqlite"
    build_masters_db(FIXTURE, path, min_position_games=1, processes=1)
    monkeypatch.setattr(app.state, "masters_book", MastersBook(path))
    body = client.get("/api/repertoire/white/graph", headers=h).json()
    assert body["theory_source"] == "masters"
    e4 = next(e for e in body["edges"] if e["parent"] == START)
    assert e4["master_games"] == 6


def test_graph_unfold_and_marks():
    h = _user_with_games("graph_unfold", [(RUY, "white", "1-0")] * 3
                         + [(["e4", "b5", "d4"], "white", "1-0")] * 2
                         + [(["e4", "a5", "d4"], "white", "1-0")])
    _set(h, "white", START, "e2e4")
    body = client.get("/api/repertoire/white/graph", headers=h).json()
    after_e4 = next(n for n in body["nodes"] if n["key"] == _key("e4"))
    assert after_e4["folded"] == 1  # 1...a5, played once
    marked = {n["key"]: n["mark"] for n in body["nodes"] if n["mark"]}
    assert marked[_key("e4", "b5")] == "add_answer"
    assert body["todo"][0] in marked
    assert body["empty_repertoire"] is False

    body = client.get("/api/repertoire/white/graph", headers=h,
                      params={"unfold": [_key("e4")]}).json()
    assert _key("e4", "a5") in {n["key"] for n in body["nodes"]}


def test_position_at_users_turn_lists_candidates(masters, engine):
    h = _user_with_games("pos_user", [(RUY, "white", "1-0")] * 3 + [(["a3", "e5"], "white", "0-1")])
    body = _position(h, "white").json()
    assert body["key"] == START and body["user_to_move"] is True and body["games"] == 4
    assert body["theory_source"] == "masters" and body["engine_busy"] is False
    rows = {c["uci"]: c for c in body["candidates"]}
    assert len(rows) == len(body["candidates"])  # each move once
    assert [c["uci"] for c in body["candidates"][:4]] == ["e2e4", "d2d4", "g1f3", "c2c4"]
    e4 = rows["e2e4"]
    assert (e4["engine_rank"], e4["eval_cp"], e4["master_games"], e4["games"]) == (1, 30, 6, 3)
    assert e4["master_score"] == pytest.approx((3 + 0.5) / 6, abs=1e-3)
    assert e4["score"] == 1.0 and e4["theory"] is True
    assert rows["c2c4"]["master_games"] == 1
    a3 = rows["a2a3"]  # the user's own move, off the engine's list
    assert (a3["engine_rank"], a3["games"], a3["score"]) == (None, 1, 0.0)
    assert body["candidates"][-1]["uci"] == "a2a3"
    assert body["replies"] == [] and body["answered_pct"] is None
    assert body["pick_blocked_at"] is None
    assert body["repertoire_move"] is None


def test_position_at_opponents_turn_lists_replies_and_answers(masters, engine):
    h = _user_with_games("pos_opp", [(RUY, "white", "1-0")] * 3
                         + [(["e4", "c5", "Nf3"], "white", "1/2-1/2")] * 2)
    _set(h, "white", START, "e2e4")
    _set(h, "white", _key("e4", "e5"), "g1f3")
    body = _position(h, "white", "e2e4").json()
    assert body["user_to_move"] is False and body["line_san"] == ["e4"]
    assert body["candidates"] == [] and engine == []  # no engine at the opponent's turn
    replies = {r["uci"]: r for r in body["replies"]}
    e5, c5, e6 = replies["e7e5"], replies["c7c5"], replies["e7e6"]
    assert (e5["games"], e5["share"], e5["answer_san"], e5["in_games"]) == (3, 0.6, "Nf3", True)
    assert e5["master_share"] == pytest.approx(3 / 6, abs=1e-3)
    assert (c5["games"], c5["answer_san"], c5["score"]) == (2, None, 0.5)
    assert (e6["games"], e6["in_games"], e6["share"]) == (0, False, 0.0)  # masters only
    assert [r["uci"] for r in body["replies"]][:2] == ["e7e5", "c7c5"]
    assert body["answered_pct"] == 60.0


def test_repertoire_move_and_explanation_shown(engine):
    h = _user_with_games("pos_rep", [(RUY, "white", "1-0")] * 3)
    _set(h, "white", START, "e2e4")
    body = _position(h, "white").json()
    assert body["repertoire_move"]["uci"] == "e2e4"
    assert body["repertoire_move"]["san"] == "e4"
    assert body["repertoire_move"]["explanation"] is None


def test_unreachable_position_says_where_to_start(engine):
    h = _user_with_games("pos_unreach", [(RUY, "white", "1-0")] * 3)
    _set(h, "white", START, "e2e4")
    deeper = _position(h, "white", "e2e4,e7e5,g1f3,b8c6").json()
    assert deeper["pick_blocked_at"] == ["e2e4", "e7e5"]  # no move after 1...e5 yet
    assert _position(h, "white", "e2e4,e7e5").json()["pick_blocked_at"] is None
    assert _position(h, "white", "d2d4,d7d5").json()["pick_blocked_at"] == []  # 1.e4, not 1.d4


def test_engine_busy_flag(busy_engine):
    h = _user_with_games("pos_busy", [(RUY, "white", "1-0")] * 3)
    body = _position(h, "white").json()
    assert body["engine_busy"] is True
    assert [(c["uci"], c["engine_rank"]) for c in body["candidates"]] == [("e2e4", None)]


def test_bad_line_is_400(engine):
    h = _user_with_games("pos_bad", [])
    assert _position(h, "white", "e2e5").status_code == 400
    assert _position(h, "white", "zz").status_code == 400
    assert _position(h, "white", "e2e4,,e7e5").status_code == 400


def test_usual_move_note(engine):
    h = _user_with_games("pos_note", [(["e4", "e5", "h3"], "white", "1-0")] * 3)
    body = _position(h, "white", "e2e4,e7e5").json()
    assert body["usual_move_note"] == "You usually play h3 here, which is off-book."
    h = _user_with_games("pos_note2", [(RUY, "white", "1-0")] * 3)
    assert _position(h, "white").json()["usual_move_note"] is None
    assert _position(h, "white", "e2e4").json()["usual_move_note"] is None  # opponent's turn


def test_start_from_my_games_prefers_theory(masters):
    h = _user_with_games("start_theory", [(["e4", "e5", "h3", "Nc6"], "white", "1-0")] * 3)
    r = client.post("/api/repertoire/white/start", headers=h)
    assert r.status_code == 200, r.text
    moves = {a["position_key"]: a["move_uci"] for a in r.json()["accepted"]}
    assert moves[START] == "e2e4"
    assert moves[_key("e4", "e5")] == "g1f3"  # the theory move masters play most, not 2.h3
    assert client.post("/api/repertoire/white/start", headers=h).status_code == 409


def test_start_with_no_games_adds_nothing():
    h = _user_with_games("start_empty", [])
    r = client.post("/api/repertoire/black/start", headers=h)
    assert r.status_code == 200 and r.json()["accepted"] == []
    body = client.get("/api/repertoire/black/graph", headers=h).json()
    assert [n["key"] for n in body["nodes"]] == [START] and body["edges"] == []


class _Book:
    """A masters book with hand-written moves."""
    available = True

    def __init__(self, moves):
        self._moves = moves

    def moves(self, keys):
        return {k: self._moves[k] for k in keys if k in self._moves}


def test_start_prefers_the_master_move_over_an_unplayed_named_line(monkeypatch):
    from app.services.masters_service import MasterMove

    after_bc5 = _key("e4", "e5", "Nc3", "Bc5")
    # The dataset names only 3.Na4 and 3.Qg4 here; masters play 3.Nf3 and 3.Bc4
    monkeypatch.setattr(app.state, "masters_book", _Book({after_bc5: [
        MasterMove("g1f3", 12, 5, 4, 3), MasterMove("f1c4", 6, 2, 2, 2),
        MasterMove("g2g3", 6, 2, 2, 2), MasterMove("f2f4", 4, 1, 1, 2),
    ]}))
    h = _user_with_games("start_masters", [(["e4", "e5", "Nc3", "Bc5", "f4"], "white", "1-0")] * 3)
    r = client.post("/api/repertoire/white/start", headers=h)
    moves = {a["position_key"]: a["move_uci"] for a in r.json()["accepted"]}
    assert moves[after_bc5] == "g1f3"
