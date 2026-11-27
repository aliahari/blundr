"""
Getting ahead of the user (specs/features/02-openings/repertoire-builder.md):
while they choose a move, fetch the explorer data and engine lines the next
few gaps need, so each step opens straight away.

One job per user: a newer one (after another pick, or a goal change)
cancels the old. Explorer requests still go one at a time per user
(explorer_service), and the engine only runs when it's free (wait=0), so game
analysis keeps priority.
"""
import asyncio
import logging

from sqlalchemy import select

from ..db import async_session
from ..models.db_models import RepertoireMove, User
from . import builder_service
from .engine_lines import get_engine_lines

logger = logging.getLogger(__name__)

PREFETCH_GAPS = 4
PREFETCH_BUDGET_S = 30.0
LEVEL_BUDGET_S = 10.0


async def _run(app, user_id: int, color: str, goal: int, session_factory) -> None:
    try:
        async with session_factory() as db:
            user = await db.get(User, user_id)
            if user is None:
                return
            rows = (await db.execute(select(RepertoireMove).where(
                RepertoireMove.user_id == user_id, RepertoireMove.color == color,
            ))).scalars().all()
            repertoire = {r.position_key: r.move_uci for r in rows}
            book = getattr(app.state, "masters_book", None)
            masters = book if book is not None and book.available else None
            http = app.state.oauth_http
            result, _ = await builder_service.next_gaps(
                db, http=http, masters=masters, user=user, color=color, goal=goal,
                repertoire=repertoire, my_games={}, budget_s=PREFETCH_BUDGET_S,
            )
            for gap in result.gaps[:PREFETCH_GAPS]:
                await builder_service.level_moves(db, http, user, gap.key, budget_s=LEVEL_BUDGET_S)
                await get_engine_lines(db, gap.key, wait=0)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(f"Builder prefetch failed for user {user_id} ({color})")


def schedule_prefetch(app, user_id: int, color: str, goal: int, *,
                      session_factory=async_session) -> asyncio.Task:
    """Start preparing the next gaps for this user, replacing any older job."""
    jobs: dict[int, asyncio.Task] = app.state.__dict__.setdefault("builder_jobs", {})
    old = jobs.get(user_id)
    if old is not None and not old.done():
        old.cancel()
    task = asyncio.create_task(_run(app, user_id, color, goal, session_factory))
    jobs[user_id] = task
    return task
