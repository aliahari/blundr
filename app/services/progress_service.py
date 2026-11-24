"""
Progress measures computed from stored game data.

- The blunder rate: blunders per 100 of the user's own moves, per week,
  optionally limited to one game phase (specs/features/01-foundations, D4).
  Only games with stored move data (AnalyzedGame.positions) count.
- Repertoire depth: how far the user's games stay in their opening
  repertoire, per week (specs/features/02-openings, D7; R7.2). Only games
  checked against the repertoire count.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import chess

from .game_phase import Phase, game_phase


@dataclass
class GameMoves:
    """What the rate needs from one analyzed game."""
    played_at: datetime
    user_color: str  # "white" | "black"
    positions: list[dict]  # AnalyzedGame.positions, decoded
    blunder_plies: set[int] = field(default_factory=set)


def week_start(day: date) -> date:
    """The Monday of `day`'s ISO week."""
    return day - timedelta(days=day.weekday())


def _count_game(game: GameMoves, phase: Phase | None) -> tuple[int, int]:
    """(user moves, user blunders) in one game, limited to `phase` if given."""
    user_is_white = game.user_color == "white"
    board = chess.Board()
    moves = blunders = 0
    for ply, entry in enumerate(game.positions, start=1):
        if (board.turn == chess.WHITE) == user_is_white:
            # A move belongs to the phase of the position it was played from.
            if phase is None or game_phase(board, ply - 1) == phase:
                moves += 1
                if ply in game.blunder_plies:
                    blunders += 1
        try:
            board.push_uci(entry["uci"])
        except ValueError:
            break  # stored data shouldn't be corrupt; stop rather than guess
    return moves, blunders


def blunder_rate_by_week(
    games: list[GameMoves],
    *,
    first_week: date,
    weeks: int,
    phase: Phase | None,
) -> list[dict]:
    """
    One point per week, oldest first, starting at the Monday `first_week`:
    {week, games, user_moves, blunders, rate}. `games` is how many games the
    week is based on, so a week with two games isn't read as a trend. Weeks
    without moves get rate None (a gap in the chart, not a zero).
    """
    totals = {
        (first_week + timedelta(weeks=i)): {"games": 0, "user_moves": 0, "blunders": 0}
        for i in range(weeks)
    }
    for game in games:
        bucket = totals.get(week_start(game.played_at.date()))
        if bucket is None:
            continue
        moves, blunders = _count_game(game, phase)
        bucket["games"] += 1
        bucket["user_moves"] += moves
        bucket["blunders"] += blunders

    return [
        {
            "week": monday.isoformat(),
            **t,
            "rate": round(100 * t["blunders"] / t["user_moves"], 2) if t["user_moves"] else None,
        }
        for monday, t in totals.items()
    ]


@dataclass
class CheckedGame:
    """What repertoire depth needs from one game checked against the repertoire."""
    played_at: datetime
    plies: int    # plies it followed the repertoire (AnalyzedGame.repertoire_plies)
    left: bool    # whether it left the repertoire (repertoire_exit_ply is set)


def repertoire_depth_by_week(
    games: list[CheckedGame], *, first_week: date, weeks: int
) -> list[dict]:
    """
    One point per week, oldest first, starting at the Monday `first_week`:
    {week, games, avg_moves, left_pct}. `avg_moves` is the average number of
    moves (two plies each) the week's games followed the repertoire, a game
    that never left it counting up to where the repertoire ran out;
    `left_pct` is the share of games that left it. Weeks without games get
    None for both (a gap in the chart, not a zero).
    """
    totals = {
        (first_week + timedelta(weeks=i)): {"games": 0, "plies": 0, "left": 0}
        for i in range(weeks)
    }
    for game in games:
        bucket = totals.get(week_start(game.played_at.date()))
        if bucket is None:
            continue
        bucket["games"] += 1
        bucket["plies"] += game.plies
        bucket["left"] += game.left

    return [
        {
            "week": monday.isoformat(),
            "games": t["games"],
            "avg_moves": round(t["plies"] / t["games"] / 2, 1) if t["games"] else None,
            "left_pct": round(100 * t["left"] / t["games"], 1) if t["games"] else None,
        }
        for monday, t in totals.items()
    ]
