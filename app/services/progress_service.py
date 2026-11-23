"""
Progress measures computed from stored game data.

So far: the blunder rate, i.e. blunders per 100 of the user's own moves,
per week, optionally limited to one game phase
(specs/features/01-foundations, D4). Only games with stored move data
(AnalyzedGame.positions, kept since Phase 1) count; older games are skipped
by the caller.
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
