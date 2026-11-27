"""
Phase 1, task 8: blunder rate per week (R4.1-R4.3).

The counting is tested as a pure function on hand-made games; one endpoint
test checks the wiring (auth, filters, skipping games without move data).
"""
import json
import pathlib
import sqlite3
from datetime import UTC, date, datetime, timedelta

import chess
import pytest

from app.services import progress_service
from app.services.progress_service import GameMoves, blunder_rate_by_week
from tests.conftest import client
from tests.helpers import make_user

_TEST_DB_PATH = pathlib.Path(__file__).parent / "test_blundr.db"

# 12 plies of a normal opening: white plays the odd plies, black the even.
SANS = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O", "Be7", "Re1", "b5"]


def _positions(sans=SANS):
    board = chess.Board()
    out = []
    for san in sans:
        move = board.parse_san(san)
        out.append({"san": san, "uci": move.uci(), "eval_cp": 0, "best_uci": None})
        board.push(move)
    return out


MON_A = date(2026, 11, 2)  # a Monday
MON_B = MON_A + timedelta(days=7)


def _game(day, color="white", blunders=(), sans=SANS):
    return GameMoves(
        played_at=datetime(day.year, day.month, day.day, 18, 0),
        user_color=color,
        positions=_positions(sans),
        blunder_plies=set(blunders),
    )


class TestBlunderRateByWeek:

    def test_counts_per_week_both_colors(self):
        games = [
            _game(MON_A, "white", blunders=[3]),              # 6 user moves, 1 blunder
            _game(MON_A + timedelta(days=6), "black", blunders=[4, 10]),  # Sunday, same week
            _game(MON_B + timedelta(days=2), "white"),        # next week, clean
        ]
        out = blunder_rate_by_week(games, first_week=MON_A, weeks=3, phase=None)

        assert [p["week"] for p in out] == ["2026-11-02", "2026-11-09", "2026-11-16"]
        assert out[0] == {"week": "2026-11-02", "games": 2, "user_moves": 12, "blunders": 3,
                          "rate": 25.0}
        assert out[1] == {"week": "2026-11-09", "games": 1, "user_moves": 6, "blunders": 0,
                          "rate": 0.0}
        # A week with no games: zeros and no rate, so the chart leaves a gap.
        assert out[2] == {"week": "2026-11-16", "games": 0, "user_moves": 0, "blunders": 0,
                          "rate": None}

    def test_games_outside_the_window_are_ignored(self):
        games = [_game(MON_A - timedelta(days=1)), _game(MON_A + timedelta(days=14))]
        out = blunder_rate_by_week(games, first_week=MON_A, weeks=2, phase=None)
        assert [p["games"] for p in out] == [0, 0]

    def test_phase_filter_counts_only_moves_from_that_phase(self, monkeypatch):
        # Fake phases (game_phase has its own tests), keyed by the ply count
        # of the position a move is played from: 0-3 "opening", 4-7
        # "middlegame", 8+ "endgame".
        def fake_phase(board, ply):
            return "opening" if ply < 4 else "middlegame" if ply < 8 else "endgame"
        monkeypatch.setattr(progress_service, "game_phase", fake_phase)

        games = [_game(MON_A, "white", blunders=[1, 5, 7, 11])]

        def week(phase):
            (point,) = blunder_rate_by_week(games, first_week=MON_A, weeks=1, phase=phase)
            return point["user_moves"], point["blunders"], point["games"]

        # White moves on plies 1,3 (opening), 5,7 (middlegame), 9,11 (endgame).
        assert week("opening") == (2, 1, 1)
        assert week("middlegame") == (2, 2, 1)
        assert week("endgame") == (2, 1, 1)
        assert week(None) == (6, 4, 1)

    def test_blunder_beyond_the_stored_moves_is_not_counted(self):
        games = [_game(MON_A, "white", blunders=[3, 41])]  # 41 isn't in positions
        (point,) = blunder_rate_by_week(games, first_week=MON_A, weeks=1, phase=None)
        assert point["blunders"] == 1


class TestBlunderRateEndpoint:

    def test_filters_and_skips_games_without_move_data(self):
        h = make_user("rateuser")
        uid = client.get("/api/auth/me", headers=h).json()["id"]

        # Played now (UTC), so it always lands in the current week, even on a Monday.
        played = datetime.now(UTC).replace(tzinfo=None)
        con = sqlite3.connect(str(_TEST_DB_PATH))
        cur = con.cursor()

        def add_game(gid, speed, positions, blunder_plies):
            cur.execute(
                "INSERT INTO analyzed_games (user_id, lichess_game_id, lichess_username,"
                " user_color, white_name, black_name, result, played_at, analyzed_at,"
                " positions, user_moves, speed, initial_eval_cp)"
                " VALUES (?, ?, 'someone', 'white', 'W', 'B', '1-0', ?, ?, ?, ?, ?, 0)",
                (uid, gid, played.isoformat(sep=" "), played.isoformat(sep=" "),
                 json.dumps(positions) if positions is not None else None,
                 6 if positions is not None else None, speed),
            )
            game_id = cur.lastrowid
            for ply in blunder_plies:
                cur.execute(
                    "INSERT INTO blunders (user_id, game_id, ply, fen_before,"
                    " move_played_uci, move_played_san, best_move_uci, best_move_san,"
                    " eval_before_cp, eval_after_cp, win_prob_drop, created_at)"
                    " VALUES (?, ?, ?, 'fen', 'e2e4', 'e4', 'd2d4', 'd4', 0, -300, 30.0, ?)",
                    (uid, game_id, ply, played.isoformat(sep=" ")),
                )

        add_game("rate_blitz", "blitz", _positions(), [3])
        add_game("rate_rapid", "rapid", _positions(), [])
        add_game("rate_old", "blitz", None, [1, 3, 5])  # analyzed before move data: skipped
        con.commit()
        con.close()

        out = client.get("/api/stats/blunder-rate?weeks=4", headers=h).json()
        assert len(out) == 4
        this_week = out[-1]
        assert (this_week["games"], this_week["user_moves"], this_week["blunders"]) == (2, 12, 1)

        blitz = client.get("/api/stats/blunder-rate?weeks=4&speed=blitz", headers=h).json()[-1]
        assert (blitz["games"], blitz["blunders"], blitz["rate"]) == (1, 1, pytest.approx(16.67))

        # A 12-ply game is all opening under the real game_phase.
        opening = client.get("/api/stats/blunder-rate?weeks=4&phase=opening", headers=h).json()[-1]
        endgame = client.get("/api/stats/blunder-rate?weeks=4&phase=endgame", headers=h).json()[-1]
        assert opening["user_moves"] == 12
        assert (endgame["user_moves"], endgame["rate"]) == (0, None)

        bad = client.get("/api/stats/blunder-rate?phase=lunch", headers=h)
        assert bad.status_code == 422
