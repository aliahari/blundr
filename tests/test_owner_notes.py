"""
Phase 2, task 9: the owner's own explanations, and reports from players
(design D4 "Owner override and reports"; R4.3, R4.4).
"""
import json
import sqlite3

import chess

from tests.conftest import client
from tests.test_blundr_features import _TEST_DB_PATH, _register

START = chess.Board().epd()
NOTE = {"plan": "Grab the centre.", "why_this_move": "It frees the queen and bishop.", "pawn_structure": ""}


def _login(name, owner=False):
    _register(name)
    token = client.post("/api/auth/login", json={"username": name, "password": "password123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    if owner:
        uid = client.get("/api/auth/me", headers=h).json()["id"]
        con = sqlite3.connect(str(_TEST_DB_PATH))
        con.execute("UPDATE users SET is_owner = 1 WHERE id = ?", (uid,))
        con.commit()
        con.close()
    return h


def _claude_explanation(key, uci, source="claude", plan="Claude's plan."):
    con = sqlite3.connect(str(_TEST_DB_PATH))
    body = json.dumps({"plan": plan, "why_this_move": "Claude's why.", "pawn_structure": None})
    # OR REPLACE: other test modules seed explanations in the shared test DB too.
    cur = con.execute(
        "INSERT OR REPLACE INTO opening_explanations (position_key, move_uci, source, body, model, prompt_version,"
        " report_count, created_at, updated_at) VALUES (?, ?, ?, ?, 'claude-opus-5-5', 1, 0,"
        " '2026-11-24', '2026-11-24')",
        (key, uci, source, body if source != "refused" else "{}"),
    )
    con.commit()
    con.close()
    return cur.lastrowid


def _key(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.epd()


def _get(h, key, uci):
    return client.get("/api/openings/explanations", headers=h,
                      params={"position_key": key, "move_uci": uci})


class TestLookup:

    def test_claude_explanation_is_served(self):
        h = _login("on_look1")
        key = _key("e4", "e5")
        _claude_explanation(key, "g1f3")
        body = _get(h, key, "g1f3").json()
        assert body["source"] == "claude" and body["plan"] == "Claude's plan."

    def test_refused_marker_is_never_served(self):
        h = _login("on_look2")
        key = _key("e4", "c5")
        _claude_explanation(key, "g1f3", source="refused")
        assert _get(h, key, "g1f3").status_code == 404

    def test_missing_is_404(self):
        h = _login("on_look3")
        assert _get(h, _key("a3"), "e7e5").status_code == 404


class TestOwnerText:

    def test_owner_text_replaces_claudes_and_can_be_edited(self):
        owner = _login("on_owner1", owner=True)
        player = _login("on_player1")
        key = _key("d4", "d5")
        _claude_explanation(key, "c2c4")

        resp = client.put("/api/openings/explanations", headers=owner,
                          json={"position_key": key, "move_uci": "c2c4", **NOTE})
        assert resp.status_code == 200
        served = _get(player, key, "c2c4").json()
        assert served["source"] == "owner"
        assert served["plan"] == NOTE["plan"]
        assert served["pawn_structure"] is None

        client.put("/api/openings/explanations", headers=owner,
                   json={"position_key": key, "move_uci": "c2c4", **NOTE, "plan": "Edited."})
        assert _get(player, key, "c2c4").json()["plan"] == "Edited."

    def test_owner_can_write_where_claude_refused(self):
        owner = _login("on_owner2", owner=True)
        key = _key("d4", "Nf6")
        _claude_explanation(key, "c2c4", source="refused")
        client.put("/api/openings/explanations", headers=owner,
                   json={"position_key": key, "move_uci": "c2c4", **NOTE})
        assert _get(owner, key, "c2c4").json()["source"] == "owner"

    def test_only_the_owner_writes(self):
        player = _login("on_player2")
        resp = client.put("/api/openings/explanations", headers=player,
                          json={"position_key": START, "move_uci": "e2e4", **NOTE})
        assert resp.status_code == 403

    def test_bad_position_or_move_is_rejected(self):
        owner = _login("on_owner3", owner=True)
        for body in (
            {"position_key": "nonsense", "move_uci": "e2e4"},
            {"position_key": START, "move_uci": "e2e5"},
            {"position_key": START, "move_uci": "e2e4", "plan": ""},
        ):
            payload = {**NOTE, **body}
            assert client.put("/api/openings/explanations", headers=owner, json=payload).status_code in (400, 422)


class TestReports:

    def test_each_player_counts_once_and_the_owner_sees_the_list(self):
        owner = _login("on_owner4", owner=True)
        p1, p2 = _login("on_rep1"), _login("on_rep2")
        busy = _claude_explanation(_key("e4", "e6"), "d2d4", plan="Busy one.")
        quiet = _claude_explanation(_key("e4", "c6"), "d2d4", plan="Quiet one.")

        assert client.post(f"/api/openings/explanations/{busy}/report", headers=p1,
                           json={"note": "The plan is backwards."}).status_code == 200
        client.post(f"/api/openings/explanations/{busy}/report", headers=p1, json={})  # repeat
        client.post(f"/api/openings/explanations/{busy}/report", headers=p2, json={})
        client.post(f"/api/openings/explanations/{quiet}/report", headers=p2, json={})

        reported = client.get("/api/openings/explanations/reported", headers=owner).json()
        mine = [r for r in reported if r["id"] in (busy, quiet)]
        assert [r["id"] for r in mine] == [busy, quiet]          # most reported first
        assert mine[0]["report_count"] == 2
        assert "The plan is backwards." in mine[0]["notes"]
        assert mine[0]["plan"] == "Busy one."

    def test_report_list_is_owner_only_and_unknown_ids_404(self):
        player = _login("on_rep3")
        assert client.get("/api/openings/explanations/reported", headers=player).status_code == 403
        assert client.post("/api/openings/explanations/999999/report", headers=player, json={}).status_code == 404

    def test_auth_required(self):
        assert client.get("/api/openings/explanations",
                          params={"position_key": START, "move_uci": "e2e4"}).status_code == 401


def test_me_says_who_the_owner_is():
    owner = _login("on_me_owner", owner=True)
    player = _login("on_me_player")
    assert client.get("/api/auth/me", headers=owner).json()["is_owner"] is True
    assert client.get("/api/auth/me", headers=player).json()["is_owner"] is False
