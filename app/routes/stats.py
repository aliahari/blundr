"""
Dashboard statistics routes.
"""
import json
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models.db_models import AnalyzedGame, Blunder, ReviewCard, ReviewLog, User
from ..models.schemas import BlunderRatePoint, StatsOverviewResponse, TimelinePoint
from ..services.progress_service import GameMoves, blunder_rate_by_week, week_start
from ..services.srs_service import MASTERED_REPETITIONS, mastered_percentage_by_day
from .deps import get_current_user
from .reviews import _fresh_cards_stmt, _fresh_item_keys, _items_started_today, _source_id_expr

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
    # An item counts as mastered only once ALL of its cards (a blunder has
    # 1 or 2, depending on whether it has a punish card; a line has one per
    # move) hit the mastery threshold. The tile is still "blunders
    # mastered", so only blunder items are counted.
    source_id = _source_id_expr()
    mastered = await db.scalar(
        select(func.count()).select_from(
            select(ReviewCard.kind, source_id)
            .where(ReviewCard.user_id == user.id, ReviewCard.kind == "blunder")
            .group_by(ReviewCard.kind, source_id)
            .having(func.min(ReviewCard.repetitions) >= MASTERED_REPETITIONS)
            .subquery()
        )
    )
    # Same "actionable now" semantics as /reviews/stats — reuse the same
    # helpers so the dashboard tile and the Learn badge can't drift apart.
    now = _utcnow_naive()
    seen_due = await db.scalar(
        select(func.count(ReviewCard.id)).where(
            ReviewCard.user_id == user.id,
            ReviewCard.due_at <= now,
            ReviewCard.last_reviewed_at.is_not(None),
        )
    )
    started_today = await _items_started_today(db, user.id)
    new_remaining = max(0, user.max_new_per_day - started_today)
    item_keys = await _fresh_item_keys(db, user.id, now, new_remaining)
    new_due = 0
    if item_keys:
        new_due = await db.scalar(
            select(func.count()).select_from(_fresh_cards_stmt(user.id, now, item_keys).subquery())
        ) or 0
    due_now = (seen_due or 0) + new_due
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
        select(ReviewCard.id, ReviewCard.created_at)
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


@router.get("/blunder-rate", response_model=list[BlunderRatePoint])
async def blunder_rate(
    weeks: int = Query(12, ge=1, le=52, description="How many weeks back, including this one"),
    phase: str = Query("all", pattern="^(all|opening|middlegame|endgame)$"),
    speed: str | None = Query(None, description="Only games of this Lichess speed, e.g. blitz"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[BlunderRatePoint]:
    """
    Blunders per 100 of the user's own moves, per week (Monday-based, by the
    date the game was played), optionally limited to one game phase and one
    speed. Only games with stored move data count; games analyzed before it
    was kept are skipped.
    """
    first_week = week_start(_utcnow_naive().date()) - timedelta(weeks=weeks - 1)
    start_dt = datetime(first_week.year, first_week.month, first_week.day)

    query = select(AnalyzedGame).where(
        AnalyzedGame.user_id == user.id,
        AnalyzedGame.played_at >= start_dt,
        AnalyzedGame.positions.is_not(None),
    )
    if speed:
        query = query.where(AnalyzedGame.speed == speed)
    rows = (await db.scalars(query)).all()

    plies_by_game: dict[int, set[int]] = {row.id: set() for row in rows}
    if rows:
        blunder_rows = (await db.execute(
            select(Blunder.game_id, Blunder.ply).where(Blunder.game_id.in_(plies_by_game))
        )).all()
        for game_id, ply in blunder_rows:
            plies_by_game[game_id].add(ply)

    games = [
        GameMoves(
            played_at=row.played_at,
            user_color=row.user_color,
            positions=json.loads(row.positions),
            blunder_plies=plies_by_game[row.id],
        )
        for row in rows
    ]
    points = blunder_rate_by_week(
        games, first_week=first_week, weeks=weeks, phase=None if phase == "all" else phase
    )
    return [BlunderRatePoint(**p) for p in points]
