"""
Phase 2, task 13: how far games stay in the repertoire, per week (design D7; R7).

The plies followed come from follow_repertoire(), the weekly figures from a
pure function; one endpoint test checks the wiring through a first
acceptance.
"""
import sqlite3
from datetime import date, datetime, timedelta

import chess

from app.services.progress_service import CheckedGame, repertoire_depth_by_week
from app.services.repertoire_service import follow_repertoire
from tests.conftest import client
from tests.test_blundr_features import _TEST_DB_PATH
from tests.test_deviations import BLACK, WHITE, _positions
from tests.test_repertoire_api import RUY, _user_with_games

START = chess.Board().epd()
MON_A = date(2026, 11, 2)  # a Monday
MON_B = MON_A + timedelta(days=7)


def _checked(day, plies, left):
    return CheckedGame(played_at=datetime(day.year, day.month, day.day, 18), plies=plies, left=left)


class TestPliesFollowed:

    def test_left_by_the_user(self):
        dev, plies = follow_repertoire(_positions(["e4", "e5", "Bc4", "Nc6"]), "white", WHITE)
        assert (dev.ply, plies) == (3, 2)

    def test_left_by_the_opponent(self):
        dev, plies = follow_repertoire(_positions(["e4", "c5", "Nf3"]), "white", WHITE)
        assert (dev.ply, plies) == (2, 1)

    def test_never_left_counts_up_to_where_the_repertoire_ends(self):
        # The repertoire ends after 2.Nf3 (ply 3): the game followed it for 3 plies.
        assert follow_repertoire(_positions(["e4", "e5", "Nf3", "Nc6", "Bb5"]), "white", WHITE) == (None, 3)
        # Black: 1.e4 c5 2.Nf3 d6, then the repertoire has no more.
        assert follow_repertoire(_positions(["e4", "c5", "Nf3", "d6", "d4"]), "black", BLACK) == (None, 4)

    def test_a_game_that_ends_inside_the_repertoire(self):
        assert follow_repertoire(_positions(["e4", "e5"]), "white", WHITE) == (None, 2)
        assert follow_repertoire([], "white", WHITE) == (None, 0)


class TestDepthByWeek:

    def test_weekly_average_and_share_left(self):
        games = [
            _checked(MON_A, plies=10, left=True),
            _checked(MON_A + timedelta(days=6), plies=7, left=False),  # Sunday, same week
            _checked(MON_B + timedelta(days=1), plies=4, left=True),
        ]
        out = repertoire_depth_by_week(games, first_week=MON_A, weeks=3)

        assert out == [
            {"week": "2026-11-02", "games": 2, "avg_moves": 4.2, "left_pct": 50.0},
            {"week": "2026-11-09", "games": 1, "avg_moves": 2.0, "left_pct": 100.0},
            {"week": "2026-11-16", "games": 0, "avg_moves": None, "left_pct": None},
        ]

    def test_games_outside_the_window_are_ignored(self):
        games = [_checked(MON_A - timedelta(days=1), plies=10, left=False)]
        out = repertoire_depth_by_week(games, first_week=MON_A, weeks=1)
        assert out == [{"week": "2026-11-02", "games": 0, "avg_moves": None, "left_pct": None}]


class TestDepthEndpoint:

    def test_checked_games_are_counted_this_week(self):
        h = _user_with_games("depth_ep", [(RUY, "white", "1-0")] * 3 + [
            (["e4", "c5", "Nf3"], "white", "1-0"),  # the opponent leaves at ply 2
        ])
        uid = client.get("/api/auth/me", headers=h).json()["id"]
        today = datetime.utcnow().strftime("%Y-%m-%d 12:00:00")
        con = sqlite3.connect(str(_TEST_DB_PATH))
        con.execute("UPDATE analyzed_games SET played_at = ? WHERE user_id = ?", (today, uid))
        con.commit()
        con.close()

        before = client.get("/api/stats/repertoire-depth?weeks=2", headers=h).json()
        assert [p["games"] for p in before] == [0, 0]  # nothing checked yet

        first = client.get("/api/repertoire/white", headers=h).json()["suggestions"][0]["position_key"]
        client.post("/api/repertoire/white/accept", headers=h,
                    json={"position_keys": [first], "include_below": True})

        this_week = client.get("/api/stats/repertoire-depth?weeks=2", headers=h).json()[-1]
        # The repertoire ends with 3.Bb5: the Ruy games follow it 5 plies, the Sicilian 1.
        assert this_week["games"] == 4
        assert this_week["avg_moves"] == round((5 * 3 + 1) / 4 / 2, 1)
        assert this_week["left_pct"] == 25.0

    def test_auth_required(self):
        assert client.get("/api/stats/repertoire-depth").status_code == 401
