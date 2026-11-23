"""
Spaced-repetition review routes: due queue, answering cards, queue stats.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_db
from ..models.db_models import CARD_SOURCE_COLUMNS, Blunder, ReviewCard, ReviewLog, User
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
    """Map a ReviewCard (with its source loaded) to the API shape."""
    return ReviewCardResponse(
        card_id=card.id,
        kind=card.kind,
        card_type=card.card_type,
        step=card.step,
        due_at=card.due_at.isoformat(),
        repetitions=card.repetitions,
        lapses=card.lapses,
        blunder=(
            blunder_to_response(card.blunder)
            if card.kind == "blunder" and card.blunder is not None
            else None
        ),
    )


# A training item is identified by (kind, source id). These helpers group
# cards by that key, so an item with several cards (avoid + punish, or one
# card per move of a line) is one unit for the daily new-item limit.
ItemKey = tuple[str, int]


def _source_id_expr():
    """
    SQL expression for a card's source id, whichever kind's column holds it.
    Built in one place so each new kind only adds its column to
    CARD_SOURCE_COLUMNS. (SQLite's COALESCE needs two or more arguments,
    hence the single-column case.)
    """
    cols = [getattr(ReviewCard, name) for name in CARD_SOURCE_COLUMNS.values()]
    return cols[0] if len(cols) == 1 else func.coalesce(*cols)


async def _items_started_today(db: AsyncSession, user_id: int) -> int:
    """
    Count distinct training items whose FIRST review (across any of their
    cards) happened today. A blunder's same-day avoid+punish pair counts
    once — that's what makes an item spend a single daily-intake slot.
    """
    first_review_per_card = (
        select(ReviewLog.card_id, func.min(ReviewLog.reviewed_at).label("first_at"))
        .where(ReviewLog.user_id == user_id)
        .group_by(ReviewLog.card_id)
        .subquery()
    )
    source_id = _source_id_expr()
    first_review_per_item = (
        select(
            ReviewCard.kind,
            source_id.label("source_id"),
            func.min(first_review_per_card.c.first_at).label("first_at"),
        )
        .select_from(first_review_per_card)
        .join(ReviewCard, ReviewCard.id == first_review_per_card.c.card_id)
        .group_by(ReviewCard.kind, source_id)
        .subquery()
    )
    today = _utcnow_naive().date().isoformat()
    count = await db.scalar(
        select(func.count()).select_from(first_review_per_item).where(
            func.date(first_review_per_item.c.first_at) == today
        )
    )
    return count or 0


async def _fresh_item_keys(
    db: AsyncSession, user_id: int, now: datetime, quota: int
) -> list[ItemKey]:
    """
    Training items eligible for the never-reviewed ("fresh") bucket right now:
    - "leftover": already has >=1 reviewed card but a still-unreviewed
      sibling (e.g. the punish card of a pair whose avoid card was already
      graded) — free, already paid for, doesn't touch the quota.
    - "brand new": zero reviewed cards at all — consumes the quota, oldest
      first (by the item's lowest card id, i.e. creation order).

    This is the one place a later phase can give each kind its own share of
    the quota; with only blunders there's nothing to balance yet.
    """
    source_id = _source_id_expr()
    started = {
        (kind, sid) for kind, sid in (await db.execute(
            select(ReviewCard.kind, source_id)
            .where(ReviewCard.user_id == user_id, ReviewCard.last_reviewed_at.is_not(None))
            .distinct()
        )).all()
    }

    candidates = [
        (kind, sid) for kind, sid in (await db.execute(
            select(ReviewCard.kind, source_id)
            .where(
                ReviewCard.user_id == user_id,
                ReviewCard.due_at <= now,
                ReviewCard.last_reviewed_at.is_(None),
            )
            .group_by(ReviewCard.kind, source_id)
            .order_by(func.min(ReviewCard.id))
        )).all()
    ]

    leftover = [key for key in candidates if key in started]
    brand_new = [key for key in candidates if key not in started][:quota]
    return leftover + brand_new


def _fresh_cards_stmt(user_id: int, now: datetime, item_keys: list[ItemKey]):
    """
    Never-reviewed, due cards belonging to the given items. An item's cards
    come out together, in card_type then step order — for blunders that is
    avoid before punish.
    """
    source_id = _source_id_expr()
    return (
        select(ReviewCard)
        .where(
            ReviewCard.user_id == user_id,
            tuple_(ReviewCard.kind, source_id).in_(item_keys),
            ReviewCard.due_at <= now,
            ReviewCard.last_reviewed_at.is_(None),
        )
        .order_by(ReviewCard.kind, source_id, ReviewCard.card_type, ReviewCard.step)
    )


@router.get("/due", response_model=list[ReviewCardResponse])
async def due_cards(
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ReviewCardResponse]:
    """
    Cards to work on now: previously-seen due cards (always served, most
    overdue first), then never-reviewed training items up to the user's
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
        started_today = await _items_started_today(db, user.id)
        quota = max(0, user.max_new_per_day - started_today)
        item_keys = await _fresh_item_keys(db, user.id, now, quota)
        if item_keys:
            fresh = (await db.scalars(
                _fresh_cards_stmt(user.id, now, item_keys)
                .options(selectinload(ReviewCard.blunder).selectinload(Blunder.game))
                .limit(remaining_slots)
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
    started_today = await _items_started_today(db, user.id)
    new_remaining = max(0, user.max_new_per_day - started_today)
    item_keys = await _fresh_item_keys(db, user.id, now, new_remaining)
    new_due = 0
    if item_keys:
        new_due = await db.scalar(
            select(func.count()).select_from(_fresh_cards_stmt(user.id, now, item_keys).subquery())
        ) or 0

    total_cards = await db.scalar(
        select(func.count(ReviewCard.id)).where(ReviewCard.user_id == user.id)
    )
    total_blunders = await db.scalar(
        select(func.count(Blunder.id)).where(Blunder.user_id == user.id)
    )
    return ReviewStatsResponse(
        due_now=(seen_due or 0) + new_due,
        new_remaining_today=new_remaining,
        total_cards=total_cards or 0,
        total_blunders=total_blunders or 0,
    )
