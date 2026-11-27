"""
Phase 2, task 10: opening and punish cards in the review queue (design D5,
D6; R5.1-R5.3, R6.5).

Cards come in through the real endpoints (accepting positions creates
opening cards); explanations and deviations are seeded in the test DB.
"""
import json
import sqlite3

import chess

from tests.conftest import client
from tests.helpers import set_line
from tests.test_blundr_features import _TEST_DB_PATH
from tests.test_repertoire_api import RUY, _key, _user_with_games

START = chess.Board().epd()


def _due(h, limit=50):
    return client.get(f"/api/reviews/due?limit={limit}", headers=h).json()


def _uid(h):
    return client.get("/api/auth/me", headers=h).json()["id"]


def _sql(query, params=()):
    con = sqlite3.connect(str(_TEST_DB_PATH))
    try:
        cur = con.execute(query, params)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()  # even on error, or the shared test DB stays locked


def _accept_all(h, color="white", line=None):
    set_line(h, color, line or RUY)


class TestOpeningCards:

    def test_queue_serves_opening_cards_in_line_order_with_payloads(self):
        h = _user_with_games("rp_open", [(RUY, "white", "1-0")] * 3)
        client.put("/api/settings", headers=h, json={"max_new_per_day": 10})
        _accept_all(h)
        _sql(
            # OR REPLACE: other test modules seed explanations in the shared test DB too.
            "INSERT OR REPLACE INTO opening_explanations (position_key, move_uci, source, body, model,"
            " prompt_version, report_count, created_at, updated_at)"
            " VALUES (?, 'g1f3', 'claude', ?, 'claude-opus-5-5', 1, 0, '2026-11-24', '2026-11-24')",
            (_key("e4", "e5"), json.dumps({"plan": "Develop and attack e5.",
                                          "why_this_move": "Nf3 hits e5.", "pawn_structure": None})),
        )

        cards = [c for c in _due(h) if c["kind"] == "opening"]

        assert [c["opening"]["answer_san"] for c in cards] == ["e4", "Nf3", "Bb5"]  # line order
        first, second, third = (c["opening"] for c in cards)
        assert first["line"] == [] and first["fen"].startswith(START.split(" ")[0])
        assert second["line"] == ["e4", "e5"]
        assert second["answer_uci"] == "g1f3"
        assert second["explanation"]["plan"] == "Develop and attack e5."
        assert second["explanation"]["source"] == "claude"
        assert third["line"] == ["e4", "e5", "Nf3", "Nc6"]
        assert third["opening"]["name"]                      # named from the opening book
        assert third["explanation"] is None                   # not generated yet
        assert all(c["blunder"] is None and c["deviation"] is None for c in cards)

    def test_grading_an_opening_card_returns_its_payload(self):
        h = _user_with_games("rp_grade", [(RUY, "white", "1-0")] * 3)
        _accept_all(h)
        card = next(c for c in _due(h) if c["kind"] == "opening")

        resp = client.post(f"/api/reviews/{card['card_id']}", headers=h, json={"grade": "good"})

        assert resp.status_code == 200
        assert resp.json()["opening"]["answer_san"] == card["opening"]["answer_san"]
        assert resp.json()["repetitions"] == 1

    def test_transposed_position_shows_the_line_it_was_accepted_from(self):
        # Black repertoire reached by 1.c4 e6 2.d4 Nf6 3.Nc3 (and 1.d4 Nf6 2.c4 e6 3.Nc3).
        line_c4 = ["c4", "e6", "d4", "Nf6", "Nc3", "Bb4"]
        h = _user_with_games("rp_trans", [(line_c4, "black", "0-1")] * 3)
        _accept_all(h, color="black", line=line_c4)

        cards = {c["opening"]["answer_san"]: c["opening"] for c in _due(h) if c["kind"] == "opening"}

        assert cards["Bb4"]["line"] == ["c4", "e6", "d4", "Nf6", "Nc3"]
        assert cards["Bb4"]["color"] == "black"
        board = chess.Board()
        for san in cards["Bb4"]["line"]:
            board.push_san(san)
        assert cards["Bb4"]["fen"] == board.fen()


class TestMixedQueue:

    def test_blunder_and_opening_cards_together(self):
        h = _user_with_games("rp_mixed", [(RUY, "white", "1-0")] * 3)
        uid = _uid(h)
        client.put("/api/settings", headers=h, json={"max_new_per_day": 10})
        game_id = sqlite3.connect(str(_TEST_DB_PATH)).execute(
            "SELECT id FROM analyzed_games WHERE user_id = ? LIMIT 1", (uid,)).fetchone()[0]
        blunder = _sql(
            "INSERT INTO blunders (user_id, game_id, ply, fen_before, move_played_uci, move_played_san,"
            " best_move_uci, best_move_san, eval_before_cp, eval_after_cp, win_prob_drop, created_at)"
            " VALUES (?, ?, 5, ?, 'a2a3', 'a3', 'f1b5', 'Bb5', 30, -300, 30.0, '2026-11-24')",
            (uid, game_id, chess.Board().fen()),
        )
        _sql("INSERT INTO review_cards (user_id, kind, blunder_id, card_type, step, ease, interval_days,"
             " repetitions, lapses, due_at) VALUES (?, 'blunder', ?, 'avoid', 0, 2.5, 0, 0, 0, '2026-01-01')",
             (uid, blunder))
        _accept_all(h)

        kinds = [c["kind"] for c in _due(h)]
        assert kinds.count("blunder") == 1 and kinds.count("opening") == 3


class TestPunishCards:

    def test_deviation_card_payload(self):
        # The opponent left the user's repertoire with 2...Qh4?? (after 1.e4 e5 2.Nf3);
        # the engine's punishment is 3.Nxh4.
        sans = ["e4", "e5", "Nf3", "Qh4", "Nxh4"]
        h = _user_with_games("rp_punish", [(sans, "white", "1-0")])
        uid = _uid(h)
        game_id = sqlite3.connect(str(_TEST_DB_PATH)).execute(
            "SELECT id FROM analyzed_games WHERE user_id = ?", (uid,)).fetchone()[0]
        after = _key("e4", "e5", "Nf3", "Qh4")
        deviation = _sql(
            "INSERT INTO repertoire_deviations (user_id, game_id, ply, side, position_key, expected_uci,"
            " played_uci, punish_uci, played_was_theory, created_at)"
            " VALUES (?, ?, 4, 'opponent', ?, NULL, 'd8h4', 'f3h4', 0, '2026-11-24')",
            (uid, game_id, _key("e4", "e5", "Nf3")),
        )
        _sql("INSERT INTO review_cards (user_id, kind, repertoire_deviation_id, card_type, step, ease,"
             " interval_days, repetitions, lapses, due_at)"
             " VALUES (?, 'deviation', ?, 'punish', 0, 2.5, 0, 0, 0, '2026-01-01')", (uid, deviation))

        card = next(c for c in _due(h) if c["kind"] == "deviation")
        p = card["deviation"]

        assert p["line"] == ["e4", "e5", "Nf3", "Qh4"]
        assert p["opponent_move_san"] == "Qh4"
        assert p["answer_uci"] == "f3h4" and p["answer_san"] == "Nxh4"
        assert p["color"] == "white"
        assert chess.Board(p["fen"]).epd() == after
        assert p["lichess_game_id"].startswith("rp_punis")
        assert card["opening"] is None and card["blunder"] is None
