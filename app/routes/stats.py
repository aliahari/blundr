"""
Dashboard statistics routes.
"""
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models.db_models import AnalyzedGame, Blunder, ReviewCard, ReviewLog, User
from ..models.schemas import StatsOverviewResponse, TimelinePoint
from ..services.srs_service import MASTERED_REPETITIONS, mastered_percentage_by_day
from .deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stats", tags=["stats"])


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/overview", response_model=StatsOverviewResponse)
async def stats_overview(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StatsOverviewResponse:
    """Headline numbers for the dashboard."""
    games_analyzed = await db.scalar(
        select(func.count(AnalyzedGame.id)).where(AnalyzedGame.user_id == user.id)
    )
    total_blunders = await db.scalar(
        select(func.count(Blunder.id)).where(Blunder.user_id == user.id)
    )
    mastered = await db.scalar(
        select(func.count(ReviewCard.id)).where(
            ReviewCard.user_id == user.id,
            ReviewCard.repetitions >= MASTERED_REPETITIONS,
        )
    )
    # Same capped "actionable now" semantics as /reviews/stats so the
    # dashboard tile and the Learn badge always agree
    from .reviews import _new_cards_started_today
    now = _utcnow_naive()
    seen_due = await db.scalar(
        select(func.count(ReviewCard.id)).where(
            ReviewCard.user_id == user.id,
            ReviewCard.due_at <= now,
            ReviewCard.last_reviewed_at.is_not(None),
        )
    )
    new_due = await db.scalar(
        select(func.count(ReviewCard.id)).where(
            ReviewCard.user_id == user.id,
            ReviewCard.due_at <= now,
            ReviewCard.last_reviewed_at.is_(None),
        )
    )
    started_today = await _new_cards_started_today(db, user.id)
    due_now = (seen_due or 0) + min(
        new_due or 0, max(0, user.max_new_per_day - started_today)
    )
    reviews_done = await db.scalar(
        select(func.count(ReviewLog.id)).where(ReviewLog.user_id == user.id)
    )
    return StatsOverviewResponse(
        games_analyzed=games_analyzed or 0,
        total_blunders=total_blunders or 0,
        blunders_mastered=mastered or 0,
        due_now=due_now or 0,
        reviews_done=reviews_done or 0,
    )


@router.get("/timeline", response_model=list[TimelinePoint])
async def stats_timeline(
    days: int = Query(30, ge=7, le=90, description="How many days back to include"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TimelinePoint]:
    """
    Per-day analyzed-game and blunder counts (by the date the game was
    played), zero-filled so charts get a continuous series.
    """
    start = date.today() - timedelta(days=days - 1)
    start_dt = datetime(start.year, start.month, start.day)

    game_day = func.date(AnalyzedGame.played_at)
    game_rows = (await db.execute(
        select(game_day, func.count(AnalyzedGame.id))
        .where(AnalyzedGame.user_id == user.id, AnalyzedGame.played_at >= start_dt)
        .group_by(game_day)
    )).all()

    blunder_rows = (await db.execute(
        select(game_day, func.count(Blunder.id))
        .join(AnalyzedGame, Blunder.game_id == AnalyzedGame.id)
        .where(Blunder.user_id == user.id, AnalyzedGame.played_at >= start_dt)
        .group_by(game_day)
    )).all()

    games_by_day = {str(d): n for d, n in game_rows}
    blunders_by_day = {str(d): n for d, n in blunder_rows}

    # Reviews per day (within the window)
    log_day = func.date(ReviewLog.reviewed_at)
    review_rows = (await db.execute(
        select(log_day, func.count(ReviewLog.id))
        .where(ReviewLog.user_id == user.id, ReviewLog.reviewed_at >= start_dt)
        .group_by(log_day)
    )).all()
    reviews_by_day = {str(d): n for d, n in review_rows}

    # Mastered % per day, replayed from the FULL review history (state on
    # day 1 of the window depends on everything before it)
    card_rows = (await db.execute(
        select(ReviewCard.id, Blunder.created_at)
        .join(Blunder, ReviewCard.blunder_id == Blunder.id)
        .where(ReviewCard.user_id == user.id)
    )).all()
    card_created = {cid: created.date() for cid, created in card_rows}

    all_logs = (await db.execute(
        select(ReviewLog.card_id, ReviewLog.grade, ReviewLog.reviewed_at)
        .where(ReviewLog.user_id == user.id)
    )).all()

    day_list = [start + timedelta(days=i) for i in range(days)]
    mastered_pcts = mastered_percentage_by_day(
        card_created, [tuple(row) for row in all_logs], day_list
    )

    points = []
    for i, day in enumerate(day_list):
        d = day.isoformat()
        points.append(TimelinePoint(
            date=d,
            games=games_by_day.get(d, 0),
            blunders=blunders_by_day.get(d, 0),
            reviews=reviews_by_day.get(d, 0),
            mastered_pct=mastered_pcts[i],
        ))
    return points
