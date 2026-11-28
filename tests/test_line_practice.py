"""Practising whole lines (repertoire-builder.md, "Revisions": practising whole lines)."""
import sqlite3

import chess

from tests.conftest import client
from tests.helpers import make_user, set_line, user_id
from tests.test_blundr_features import _TEST_DB_PATH

ITALIAN = ["e4", "e5", "Nf3", "Nc6", "Bc4"]
SICILIAN = ["e4", "c5", "Nf3"]


def _key(*sans):
    b = chess.Board()
    for s in sans:
        b.push_san(s)
    return b.epd()


def _sql(query, params=()):
    con = sqlite3.connect(str(_TEST_DB_PATH))
    try:
        con.execute(query, params)
        con.commit()
    finally:
        con.close()


def _card_of(h, *sans):
    con = sqlite3.connect(str(_TEST_DB_PATH))
    try:
        return con.execute(
            "SELECT rc.id FROM review_cards rc JOIN repertoire_moves rm ON rm.id = rc.repertoire_move_id"
            " WHERE rc.user_id = ? AND rm.position_key = ?", (user_id(h), _key(*sans))).fetchone()[0]
    finally:
        con.close()


def _line(h, **params):
    r = client.get("/api/reviews/line", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _user(name, max_new=10):
    h = make_user(name)
    client.put("/api/settings", headers=h, json={"max_new_per_day": max_new})
    set_line(h, "white", ITALIAN)
    set_line(h, "white", SICILIAN)
    return h


def test_a_line_runs_from_the_start_to_the_end_of_a_branch():
    h = _user("lp_basic")
    body = _line(h)
    assert body["color"] == "white"
    assert [m["san"] for m in body["moves"]] == ["e4", "e5", "Nf3", "Nc6", "Bc4"]
    assert [m["by"] for m in body["moves"]] == ["user", "opponent", "user", "opponent", "user"]
    mine = [m for m in body["moves"] if m["by"] == "user"]
    assert all(m["graded"] and m["new"] and m["card_id"] for m in mine)
    assert all(m["card_id"] is None for m in body["moves"] if m["by"] == "opponent")
    assert body["moves"][0]["fen_before"] == chess.STARTING_FEN


def test_moves_not_due_are_played_for_context():
    h = _user("lp_context")
    future = "2099-01-01 00:00:00"
    for sans in ((), ("e4", "e5"), ("e4", "e5", "Nf3", "Nc6")):
        _sql("UPDATE review_cards SET last_reviewed_at = '2026-11-20', interval_days = 6,"
             " repetitions = 2, due_at = ? WHERE id = ?", (future, _card_of(h, *sans)))
    body = _line(h)
    # Only 2.Nf3 in the Sicilian is due: that line comes up, 1.e4 played for context
    assert [m["san"] for m in body["moves"]] == ["e4", "c5", "Nf3"]
    first, last = body["moves"][0], body["moves"][2]
    assert (first["graded"], first["new"]) == (False, False)
    assert (last["graded"], last["new"]) == (True, True)


def test_nothing_due_means_no_line():
    h = make_user("lp_empty")
    assert client.get("/api/reviews/line", headers=h).json() is None


def test_new_moves_past_the_daily_limit_wait_unless_asked():
    h = _user("lp_limit", max_new=1)
    body = _line(h)
    graded = [m["san"] for m in body["moves"] if m["graded"]]
    assert graded == ["e4"]  # one new move today; the others are played for context
    more = _line(h, extra_new=2)
    assert [m["san"] for m in more["moves"] if m["graded"]] == ["e4", "Nf3", "Bc4"]


def test_explanations_come_with_your_moves():
    h = _user("lp_expl")
    _sql("INSERT OR REPLACE INTO opening_explanations (position_key, move_uci, source, body,"
         " report_count, created_at, updated_at) VALUES (?, 'e2e4', 'owner', ?, 0,"
         " '2026-11-28', '2026-11-28')",
         (_key(), '{"plan": "Take the centre.", "why_this_move": "e4 grabs space.", "pawn_structure": null}'))
    first = _line(h)["moves"][0]
    assert first["explanation"]["plan"] == "Take the centre."


def test_black_lines_start_with_whites_first_move():
    h = make_user("lp_black")
    set_line(h, "black", ["e4", "c5", "Nf3", "d6"])
    body = _line(h)
    assert body["color"] == "black"
    assert [m["san"] for m in body["moves"]] == ["e4", "c5", "Nf3", "d6"]
    assert [m["by"] for m in body["moves"]] == ["opponent", "user", "opponent", "user"]
