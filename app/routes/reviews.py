"""
Spaced-repetition review routes: due queue, answering cards, queue stats.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_db
from ..models.db_models import Blunder, ReviewCard, ReviewLog, User
from ..models.schemas import (
    ReviewAnswerRequest,
    ReviewCardResponse,
    ReviewStatsResponse,
)
from ..services.srs_service import apply_grade
from .analysis import blunder_to_response
from .deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reviews", tags=["reviews"])


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def card_to_response(card: ReviewCard) -> ReviewCardResponse:
    """Map a ReviewCard (with blunder+game loaded) to the API shape."""
    return ReviewCardResponse(
        card_id=card.id,
        due_at=card.due_at.isoformat(),
        repetitions=card.repetitions,
        lapses=card.lapses,
        blunder=blunder_to_response(card.blunder),
    )


async def _new_cards_started_today(db: AsyncSession, user_id: int) -> int:
    """Count cards whose FIRST review happened today (today's new intake)."""
    first_review = (
        select(ReviewLog.card_id, func.min(ReviewLog.reviewed_at).label("first_at"))
        .where(ReviewLog.user_id == user_id)
        .group_by(ReviewLog.card_id)
        .subquery()
    )
    today = _utcnow_naive().date().isoformat()
    count = await db.scalar(
        select(func.count()).select_from(first_review).where(
            func.date(first_review.c.first_at) == today
        )
    )
    return count or 0


@router.get("/due", response_model=list[ReviewCardResponse])
async def due_cards(
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ReviewCardResponse]:
    """
    Cards to work on now: previously-seen due cards (always served, most
    overdue first), then never-reviewed blunders up to the user's
    max_new_per_day — counting new cards already started today against the
    quota.
    """
    now = _utcnow_naive()

    seen = (await db.scalars(
        select(ReviewCard)
        .where(
            ReviewCard.user_id == user.id,
            ReviewCard.due_at <= now,
            ReviewCard.last_reviewed_at.is_not(None),
        )
        .options(selectinload(ReviewCard.blunder).selectinload(Blunder.game))
        .order_by(ReviewCard.due_at)
        .limit(limit)
    )).all()

    cards = list(seen)
    remaining_slots = limit - len(cards)
    if remaining_slots > 0:
        started_today = await _new_cards_started_today(db, user.id)
        new_quota = max(0, user.max_new_per_day - started_today)
        if new_quota > 0:
            fresh = (await db.scalars(
                select(ReviewCard)
                .where(
                    ReviewCard.user_id == user.id,
                    ReviewCard.due_at <= now,
                    ReviewCard.last_reviewed_at.is_(None),
                )
                .options(selectinload(ReviewCard.blunder).selectinload(Blunder.game))
                .order_by(ReviewCard.id)  # oldest blunders enter first
                .limit(min(new_quota, remaining_slots))
            )).all()
            cards.extend(fresh)

    return [card_to_response(c) for c in cards]


@router.post("/{card_id}", response_model=ReviewCardResponse)
async def answer_card(
    card_id: int,
    answer: ReviewAnswerRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewCardResponse:
    """Grade a card (again/good/easy) and reschedule it."""
    card = await db.scalar(
        select(ReviewCard)
        .where(ReviewCard.id == card_id, ReviewCard.user_id == user.id)
        .options(selectinload(ReviewCard.blunder).selectinload(Blunder.game))
    )
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review card {card_id} not found",
        )

    apply_grade(card, answer.grade)
    db.add(ReviewLog(user_id=user.id, card_id=card.id, grade=answer.grade))
    await db.commit()

    return card_to_response(card)


@router.get("/stats", response_model=ReviewStatsResponse)
async def review_stats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReviewStatsResponse:
    """Summary counts for the review queue, respecting the daily new-card cap."""
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
    new_remaining = max(0, user.max_new_per_day - started_today)

    total_cards = await db.scalar(
        select(func.count(ReviewCard.id)).where(ReviewCard.user_id == user.id)
    )
    total_blunders = await db.scalar(
        select(func.count(Blunder.id)).where(Blunder.user_id == user.id)
    )
    return ReviewStatsResponse(
        due_now=(seen_due or 0) + min(new_due or 0, new_remaining),
        new_remaining_today=new_remaining,
        total_cards=total_cards or 0,
        total_blunders=total_blunders or 0,
    )
