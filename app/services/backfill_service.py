"""
Give games analyzed before Phase 1 their moves (specs/features/02-openings, D1).

Those games were analyzed before AnalyzedGame.positions existed, so their
moves were never stored. Re-running Stockfish on them would be wasteful:
their blunders are already known. Instead their move lists are fetched from
Lichess in bulk and replayed, giving `positions` entries with null
evaluations (`eval_cp` and `best_uci` are None). That's enough for the
opening repertoire (which needs moves) and for the blunder rate (which needs
moves and the already-stored blunders).

Runs at the tail of a sync's background task, after analysis, so Blundr
never has two Lichess requests in flight for the same user: Lichess allows
only one at a time per caller.
"""
import json
import logging
from typing import Protocol

import chess
from sqlalchemy import select

from ..db import async_session
from ..models.db_models import AnalyzedGame
from ..models.game import LichessGame
from .analysis_service import AnalysisJob

logger = logging.getLogger(__name__)


class _GameExporter(Protocol):
    EXPORT_BATCH_SIZE: int

    async def export_games_by_ids(self, game_ids: list[str]) -> list[LichessGame]: ...


def positions_from_moves(moves: list[str], user_color: str) -> tuple[list[dict], int]:
    """
    Replay SAN moves from the start position into `positions` entries with
    no evaluations, plus how many of the moves were the user's. Stops at the
    first move that can't be replayed, keeping the moves before it (the same
    rule the analysis loop uses).
    """
    board = chess.Board()
    user_is_white = user_color == "white"
    positions: list[dict] = []
    user_moves = 0
    for san in moves:
        users_turn = (board.turn == chess.WHITE) == user_is_white
        try:
            move = board.push_san(san)
        except ValueError:
            break
        positions.append({"san": san, "uci": move.uci(), "eval_cp": None, "best_uci": None})
        if users_turn:
            user_moves += 1
    return positions, user_moves


async def backfill_moves(job: AnalysisJob, user_id: int, lichess: _GameExporter) -> int:
    """
    Fetch and store moves for every one of the user's games that has none.
    Saves after each Lichess batch, so an interruption keeps what was done.
    Games Lichess no longer has stay NULL and are asked for again on the
    next run. Returns how many games got their moves.
    """
    async with async_session() as db:
        pending = (await db.execute(
            select(AnalyzedGame.id, AnalyzedGame.lichess_game_id)
            .where(AnalyzedGame.user_id == user_id, AnalyzedGame.positions.is_(None))
            .order_by(AnalyzedGame.id)
        )).all()

    job.backfill_total = len(pending)
    job.backfill_done = 0
    if not pending:
        return 0

    row_ids = {lichess_id: row_id for row_id, lichess_id in pending}
    lichess_ids = list(row_ids)
    filled = 0
    batch_size = lichess.EXPORT_BATCH_SIZE

    for start in range(0, len(lichess_ids), batch_size):
        batch = lichess_ids[start:start + batch_size]
        fetched = {g.id: g for g in await lichess.export_games_by_ids(batch)}

        async with async_session() as db:
            for lichess_id in batch:
                game = fetched.get(lichess_id)
                if game is None:
                    logger.warning(f"Backfill: Lichess has no game {lichess_id} (user {user_id})")
                    continue
                row = await db.get(AnalyzedGame, row_ids[lichess_id])
                if row is None or row.positions is not None:
                    continue  # deleted or filled meanwhile
                positions, user_moves = positions_from_moves(game.moves, row.user_color)
                row.positions = json.dumps(positions)
                row.user_moves = user_moves
                row.speed = row.speed or game.speed or None
                filled += 1
            await db.commit()

        job.backfill_done += len(batch)

    logger.info(f"Backfill for user {user_id}: {filled} of {len(lichess_ids)} games got their moves")
    return filled
