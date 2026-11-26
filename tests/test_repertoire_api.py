"""
Phase 2, task 6: the repertoire endpoints (R3.1-R3.3, R5.1, R5.3).

Runs through the shared TestClient. Games are seeded straight into the test
database with their stored moves; the opening book is loaded at app startup,
so theory marks and names come from the real dataset.
"""
import json
import sqlite3

import chess
import pytest

from tests.conftest import client
from tests.test_blundr_features import _TEST_DB_PATH, _register

START = chess.Board().epd()


def _key(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.epd()


def _positions(sans):
    board = chess.Board()
    out = []
    for san in sans:
        move = board.parse_san(san)
        out.append({"san": san, "uci": move.uci(), "eval_cp": None, "best_uci": None})
        board.push(move)
    return out


def _user_with_games(name, games):
    """games: list of (sans, colour, result). Returns auth headers."""
    _register(name)
    token = client.post(
        "/api/auth/login", json={"username": name, "password": "password123"}
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    uid = client.get("/api/auth/me", headers=h).json()["id"]
    con = sqlite3.connect(str(_TEST_DB_PATH))
    for i, (sans, color, result) in enumerate(games):
        con.execute(
            "INSERT INTO analyzed_games (user_id, lichess_game_id, lichess_username, user_color,"
            " white_name, black_name, result, played_at, analyzed_at, positions, user_moves)"
            " VALUES (?, ?, 'x', ?, 'W', 'B', ?, '2026-10-01', '2026-10-01', ?, 0)",
            (uid, f"{name[:8]}{i:04d}", color, result, json.dumps(_positions(sans))),
        )
    con.commit()
    con.close()
    return h


def _cards(h):
    """The user's opening cards, oldest first, as (card id, position key)."""
    uid = client.get("/api/auth/me", headers=h).json()["id"]
    con = sqlite3.connect(str(_TEST_DB_PATH))
    rows = con.execute(
        "SELECT rc.id, rm.position_key FROM review_cards rc"
        " JOIN repertoire_moves rm ON rm.id = rc.repertoire_move_id"
        " WHERE rc.user_id = ? AND rc.kind = 'opening' ORDER BY rc.id",
        (uid,),
    ).fetchall()
    con.close()
    return rows


RUY = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]


class TestValidation:

    def test_colour_is_validated_and_auth_required(self):
        h = _user_with_games("rep_val", [])
        assert client.get("/api/repertoire/green/graph", headers=h).status_code == 422
        assert client.get("/api/repertoire/white/graph").status_code == 401


class TestAccept:

    def test_accepting_a_position_brings_its_line_and_cards_in_order(self):
        h = _user_with_games("rep_acc", [(RUY, "white", "1-0")] * 3)

        resp = client.post("/api/repertoire/white/accept", headers=h,
                           json={"position_keys": [_key("e4", "e5", "Nf3", "Nc6")]})

        assert resp.status_code == 200
        keys = [a["position_key"] for a in resp.json()["accepted"]]
        assert keys == [START, _key("e4", "e5"), _key("e4", "e5", "Nf3", "Nc6")]
        # One opening card per position, created parents first (line order).
        assert [k for _, k in _cards(h)] == keys

        # Accepting again changes nothing.
        client.post("/api/repertoire/white/accept", headers=h,
                    json={"position_keys": [_key("e4", "e5", "Nf3", "Nc6")]})
        assert len(_cards(h)) == 3

    def test_include_below_takes_the_whole_subtree(self):
        h = _user_with_games("rep_sub", [(RUY, "white", "1-0")] * 3)
        resp = client.post("/api/repertoire/white/accept", headers=h,
                           json={"position_keys": [START], "include_below": True})
        assert len(resp.json()["accepted"]) == 3

    def test_unknown_position_is_rejected(self):
        h = _user_with_games("rep_unk", [(RUY, "white", "1-0")] * 3)
        resp = client.post("/api/repertoire/white/accept", headers=h,
                           json={"position_keys": [_key("d4")]})
        assert resp.status_code == 400


class TestSetAndRemove:

    def _accepted_ruy(self, name):
        h = _user_with_games(name, [(RUY, "white", "1-0")] * 3)
        client.post("/api/repertoire/white/accept", headers=h,
                    json={"position_keys": [START], "include_below": True})
        return h

    def test_replacing_a_move_removes_the_line_it_cut_off(self):
        h = self._accepted_ruy("rep_rep")

        resp = client.put("/api/repertoire/white/position", headers=h,
                          json={"position_key": START, "move_uci": "d2d4"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["removed"] == 2           # 2.Nf3 and 3.Bb5 can't be reached after 1.d4
        assert [(a["position_key"], a["move_uci"]) for a in body["accepted"]] == [(START, "d2d4")]
        assert [k for _, k in _cards(h)] == [START]  # the old 1.e4 card replaced by a new one

    def test_adding_a_move_by_hand(self):
        h = self._accepted_ruy("rep_add")
        deep = _key("e4", "e5", "Nf3", "Nc6", "Bb5", "a6")

        resp = client.put("/api/repertoire/white/position", headers=h,
                          json={"position_key": deep, "move_uci": "b5a4"})

        assert resp.status_code == 200
        added = [a for a in resp.json()["accepted"] if a["position_key"] == deep]
        assert added and added[0]["source"] == "manual"
        assert added[0]["parent_key"] == _key("e4", "e5", "Nf3", "Nc6")
        assert len(_cards(h)) == 4

    @pytest.mark.parametrize("body, why", [
        ({"position_key": _key("d4", "d5"), "move_uci": "c2c4"}, "not reachable"),
        ({"position_key": START, "move_uci": "e2e5"}, "illegal"),
        ({"position_key": _key("e4"), "move_uci": "e7e5"}, "not the user's turn"),
        ({"position_key": "not a position", "move_uci": "e2e4"}, "bad key"),
    ])
    def test_bad_moves_are_rejected(self, body, why):
        h = self._accepted_ruy(f"rep_bad{abs(hash(why)) % 1000}")
        assert client.put("/api/repertoire/white/position", headers=h, json=body).status_code == 400

    def test_removing_a_position_removes_everything_below_it(self):
        h = self._accepted_ruy("rep_del")

        resp = client.request("DELETE", "/api/repertoire/white/position", headers=h,
                              json={"position_key": _key("e4", "e5")})

        assert resp.status_code == 200
        assert resp.json()["removed"] == 2
        assert [k for _, k in _cards(h)] == [START]


class TestBlackAndDeviations:

    def test_black_repertoire(self):
        h = _user_with_games("rep_blk", [(["e4", "c5", "Nf3", "d6"], "black", "0-1")] * 3)
        body = client.post("/api/repertoire/black/start", headers=h).json()
        assert [a["move_san"] for a in body["accepted"]] == ["c5", "d6"]
        assert body["accepted"][0]["parent_key"] is None

    def test_deviations_list_starts_empty(self):
        h = _user_with_games("rep_dev", [])
        assert client.get("/api/repertoire/deviations", headers=h).json() == []


class TestFirstAcceptanceChecksGames:
    """Task 12 (R6.1): accepting a first repertoire checks the user's past games."""

    def test_past_deviation_is_listed(self):
        h = _user_with_games("rep_chk", [(RUY, "white", "1-0")] * 3 + [
            (["e4", "c5", "Nf3"], "white", "1-0"),        # the opponent left it at move 1
            (["e4", "e5", "Bc4", "Nc6"], "white", "0-1"),  # the user left it at move 2
        ])
        assert client.get("/api/repertoire/deviations", headers=h).json() == []
        client.post("/api/repertoire/white/start", headers=h)

        devs = client.get("/api/repertoire/deviations", headers=h).json()
        assert sorted((d["side"], d["ply"], d["played_uci"]) for d in devs) == [
            ("opponent", 2, "c7c5"), ("user", 3, "f1c4"),
        ]
