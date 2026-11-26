"""
Stockfish's top moves for one position, for the repertoire panel
(specs/features/02-openings/repertoire-graph.md, "Engine moves at the user's turn").

Rationed: at most one run at a time across the app, so a busy panel can't
starve game analysis; a caller that can't get the engine within `wait`
seconds gets None ("engine busy"). Results are cached per position for good.
"""
from __future__ import annotations

import asyncio
import json
import logging
import weakref
from dataclasses import dataclass

import chess
import chess.engine
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.db_models import EngineLine
from . import analysis_service
from .analysis_service import MATE_SCORE

logger = logging.getLogger(__name__)

MULTI_PV = 4
ENGINE_DEPTH = 20
ENGINE_TIME = 1.5
ENGINE_WAIT_SECONDS = 5.0

# One slot per event loop (there's one in production). An asyncio.Semaphore
# binds to the loop that first waits on it, so a single module-level one would
# break under test runners that give each test its own loop.
_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _engine_slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slot = _slots.get(loop)
    if slot is None:
        slot = _slots[loop] = asyncio.Semaphore(1)
    return slot


@dataclass(frozen=True)
class EngineMove:
    uci: str
    san: str
    eval_cp: int  # from the side to move; mate is ±MATE_SCORE


async def _cached(db: AsyncSession, key: str) -> list[EngineMove] | None:
    row = await db.get(EngineLine, key)
    if row is None:
        return None
    return [EngineMove(uci, san, cp) for uci, san, cp in json.loads(row.body)]


def _moves_from(board: chess.Board, infos) -> list[EngineMove]:
    moves = []
    for info in infos[:MULTI_PV]:
        pv = info.get("pv")
        if not pv:
            continue
        score = info["score"].pov(board.turn)
        if score.is_mate():
            cp = MATE_SCORE if score.mate() > 0 else -MATE_SCORE
        else:
            cp = score.score()
        moves.append(EngineMove(pv[0].uci(), board.san(pv[0]), cp))
    return moves


async def get_engine_lines(
    db: AsyncSession,
    key: str,
    *,
    open_engine=None,
    wait: float = ENGINE_WAIT_SECONDS,
) -> list[EngineMove] | None:
    """
    Up to four engine moves at `key` (a board.epd()), best first; [] when the
    game is over there; None when the engine is busy or failed. New results
    are committed to the shared cache.
    """
    cached = await _cached(db, key)
    if cached is not None:
        return cached
    board = chess.Board(key + " 0 1")
    if board.is_game_over():
        return []
    open_engine = open_engine or analysis_service._open_engine

    slot = _engine_slot()
    try:
        await asyncio.wait_for(slot.acquire(), wait)
    except TimeoutError:
        return None
    try:
        cached = await _cached(db, key)  # filled while we waited?
        if cached is not None:
            return cached
        engine = await open_engine()
        try:
            infos = await engine.analyse(
                board, chess.engine.Limit(depth=ENGINE_DEPTH, time=ENGINE_TIME), multipv=MULTI_PV
            )
        finally:
            try:
                await engine.quit()
            except Exception:  # noqa: BLE001 — already dead
                pass
    except (chess.engine.EngineError, chess.engine.EngineTerminatedError, OSError) as exc:
        logger.warning("Engine lines failed for %s: %s", key, exc)
        return None
    finally:
        slot.release()

    if isinstance(infos, dict):
        infos = [infos]
    moves = _moves_from(board, infos)
    depth = min((info.get("depth", 0) for info in infos), default=0)
    await db.merge(EngineLine(
        position_key=key, depth=depth,
        body=json.dumps([[m.uci, m.san, m.eval_cp] for m in moves]),
    ))
    await db.commit()
    return moves
