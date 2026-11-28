"""
Spaced-repetition review routes: due queue, answering cards, queue stats.
"""
import json
import logging
from datetime import UTC, datetime

import chess
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_db
from ..models.db_models import (
    CARD_SOURCE_COLUMNS,
    AnalyzedGame,
    Blunder,
    OpeningName,
    RepertoireMove,
    ReviewCard,
    ReviewLog,
    TheoryMove,
    User,
)
from ..models.schemas import (
    LineMove,
    LinePractice,
    DeviationCardPayload,
    OpeningCardPayload,
    OpeningLabel,
    ReviewAnswerRequest,
    ReviewCardResponse,
    ReviewStatsResponse,
)
from ..services.explanation_service import preferred_explanation
from ..services.repertoire_service import line_to, reachable
from ..services.srs_service import apply_grade
from .analysis import blunder_to_response
from .deps import get_current_user
from .openings import explanation_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reviews", tags=["reviews"])


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# Load each card's source with it, whatever its kind.
CARD_LOADS = (
    selectinload(ReviewCard.blunder).selectinload(Blunder.game),
    selectinload(ReviewCard.repertoire_move),
    selectinload(ReviewCard.repertoire_deviation),
)


async def _opening_label(db: AsyncSession, keys: list[str]):
    """The name of the last named position among `keys` (in line order)."""
    names = dict((await db.execute(
        select(OpeningName.position_key, OpeningName.name).where(OpeningName.position_key.in_(keys))
    )).all())
    ecos = dict((await db.execute(
        select(OpeningName.position_key, OpeningName.eco).where(OpeningName.position_key.in_(keys))
    )).all())
    named = [k for k in keys if k in names]
    if not named:
        return None
    return OpeningLabel(eco=ecos[named[-1]], name=names[named[-1]])


def _replay(ucis: list[str]) -> tuple[chess.Board, list[str], list[str]]:
    """Board after the moves, their SAN, and the position keys passed (start included)."""
    board = chess.Board()
    sans, keys = [], [board.epd()]
    for uci in ucis:
        move = chess.Move.from_uci(uci)
        sans.append(board.san(move))
        board.push(move)
        keys.append(board.epd())
    return board, sans, keys


async def _opening_payload(db, card, contexts) -> OpeningCardPayload | None:
    row = card.repertoire_move
    if row is None:
        return None
    ctx_key = (row.user_id, row.color)
    if ctx_key not in contexts:
        moves = dict((await db.execute(
            select(RepertoireMove.position_key, RepertoireMove.move_uci).where(
                RepertoireMove.user_id == row.user_id, RepertoireMove.color == row.color)
        )).all())
        contexts[ctx_key] = (moves, reachable(row.color, moves))
    moves, parents = contexts[ctx_key]
    line = line_to(row.position_key, moves, parents)
    if line is None:
        return None
    board, sans, keys = _replay(line)
    fen = board.fen()
    board.push(chess.Move.from_uci(row.move_uci))
    keys.append(board.epd())
    theory = await db.scalar(select(TheoryMove.line_name).where(
        TheoryMove.position_key == row.position_key, TheoryMove.move_uci == row.move_uci))
    explanation = await preferred_explanation(db, row.position_key, row.move_uci)
    return OpeningCardPayload(
        color=row.color, line=sans, fen=fen, answer_uci=row.move_uci, answer_san=row.move_san,
        opening=await _opening_label(db, keys), theory_line=theory,
        explanation=explanation_response(explanation) if explanation else None,
    )


async def _deviation_payload(db, card) -> DeviationCardPayload | None:
    dev = card.repertoire_deviation
    if dev is None or dev.punish_uci is None:
        return None
    game = await db.get(AnalyzedGame, dev.game_id)
    if game is None or game.positions is None:
        return None
    ucis = [p["uci"] for p in json.loads(game.positions)[:dev.ply]]
    if len(ucis) < dev.ply:
        return None
    board, sans, keys = _replay(ucis)
    try:
        answer = chess.Move.from_uci(dev.punish_uci)
        answer_san = board.san(answer)
    except (ValueError, AssertionError):
        return None
    return DeviationCardPayload(
        color=game.user_color, line=sans, fen=board.fen(), opponent_move_san=sans[-1],
        answer_uci=dev.punish_uci, answer_san=answer_san, opening=await _opening_label(db, keys),
        lichess_game_id=game.lichess_game_id, played_at=game.played_at.isoformat(),
    )


async def cards_to_responses(db: AsyncSession, cards) -> list[ReviewCardResponse]:
    """Cards (loaded with CARD_LOADS) in the API shape, with each kind's payload."""
    contexts: dict = {}
    out = []
    for card in cards:
        response = ReviewCardResponse(
            card_id=card.id, kind=card.kind, card_type=card.card_type, step=card.step,
            due_at=card.due_at.isoformat(), repetitions=card.repetitions, lapses=card.lapses,
        )
        if card.kind == "blunder" and card.blunder is not None:
            response.blunder = blunder_to_response(card.blunder)
        elif card.kind == "opening":
            response.opening = await _opening_payload(db, card, contexts)
        elif card.kind == "deviation":
            response.deviation = await _deviation_payload(db, card)
        out.append(response)
    return out


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


async def _items_started_today(
    db: AsyncSession, user_id: int, kinds: list[str] | None = None
) -> int:
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
        .where(*_kind_filter(kinds))
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


def _kind_filter(kinds: list[str] | None) -> list:
    """WHERE clauses limiting cards to some kinds (none: every kind)."""
    return [ReviewCard.kind.in_(kinds)] if kinds else []


async def _fresh_item_keys(
    db: AsyncSession, user_id: int, now: datetime, quota: int, kinds: list[str] | None = None
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
            .where(ReviewCard.user_id == user_id, ReviewCard.last_reviewed_at.is_not(None),
                   *_kind_filter(kinds))
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
                *_kind_filter(kinds),
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


async def _due_card_list(
    db: AsyncSession, user: User, limit: int, kind: list[str] | None, extra_new: int,
) -> list[ReviewCard]:
    """The due queue (see due_cards): seen cards first, then new items within
    today's allowance."""
    now = _utcnow_naive()

    seen = (await db.scalars(
        select(ReviewCard)
        .where(
            ReviewCard.user_id == user.id,
            ReviewCard.due_at <= now,
            ReviewCard.last_reviewed_at.is_not(None),
            *_kind_filter(kind),
        )
        .options(*CARD_LOADS)
        .order_by(ReviewCard.due_at)
        .limit(limit)
    )).all()

    cards = list(seen)
    remaining_slots = limit - len(cards)
    if remaining_slots > 0:
        started_today = await _items_started_today(db, user.id, kind)
        # extra_new raises today's allowance; items reviewed under it count
        # against it like any other, so it doesn't reopen on every refresh
        quota = max(0, user.max_new_per_day + extra_new - started_today)
        item_keys = await _fresh_item_keys(db, user.id, now, quota, kind)
        if item_keys:
            fresh = (await db.scalars(
                _fresh_cards_stmt(user.id, now, item_keys)
                .options(*CARD_LOADS)
                .limit(remaining_slots)
            )).all()
            cards.extend(fresh)

    return cards


@router.get("/due", response_model=list[ReviewCardResponse])
async def due_cards(
    limit: int = Query(20, ge=1, le=100),
    kind: list[str] | None = Query(None, description="Only these kinds of card (repeatable)"),
    extra_new: int = Query(0, ge=0, le=100, description="New items past today's limit ('review more')"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ReviewCardResponse]:
    """
    Cards to work on now: previously-seen due cards (always served, most
    overdue first), then never-reviewed training items up to the user's
    max_new_per_day — counting new cards already started today against the
    quota. With `kind`, only those kinds, and the daily limit applies to them
    alone (so blunders don't use up the openings' new cards). `extra_new`
    lets that many more new items in, for a player who wants to keep going.
    """
    cards = await _due_card_list(db, user, limit, kind, extra_new)
    return await cards_to_responses(db, cards)


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
        .options(*CARD_LOADS)
    )
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Review card {card_id} not found",
        )

    apply_grade(card, answer.grade)
    db.add(ReviewLog(user_id=user.id, card_id=card.id, grade=answer.grade))
    await db.commit()

    return (await cards_to_responses(db, [card]))[0]


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


# --- Practising whole lines (repertoire-builder.md, "Revisions") -------------

LINE_MAX_PLIES = 60


@router.get("/line", response_model=LinePractice | None)
async def next_line(
    extra_new: int = Query(0, ge=0, le=100, description="New moves past today's limit"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LinePractice | None:
    """
    The next line to practise: from the start to the end of the branch that
    holds the most urgent due opening card. The user plays every one of their
    moves; due ones are graded (one card each), the rest are played for
    context. None when no opening card is due.
    """
    due = await _due_card_list(db, user, 100, ["opening"], extra_new)
    due = [c for c in due if c.repertoire_move is not None]
    if not due:
        return None
    due_ids = {c.id for c in due}
    color = due[0].repertoire_move.color
    rows = (await db.scalars(
        select(RepertoireMove)
        .where(RepertoireMove.user_id == user.id, RepertoireMove.color == color)
        .options(selectinload(RepertoireMove.cards))
        .order_by(RepertoireMove.id)
    )).all()
    by_key = {r.position_key: r for r in rows}
    moves = {r.position_key: r.move_uci for r in rows}
    parents = reachable(color, moves)
    user_turn = chess.WHITE if color == "white" else chess.BLACK

    # Positions on the way to a due move, so a branch is chosen that has one
    toward_due: set[str] = set()
    for card in due:
        if card.repertoire_move.color != color:
            continue
        node = card.repertoire_move.position_key
        while node is not None and node not in toward_due:
            toward_due.add(node)
            node = parents.get(node)

    first = due[0].repertoire_move
    path = line_to(first.position_key, moves, parents)
    if path is None:
        return None
    board = chess.Board()
    for uci in path:
        board.push(chess.Move.from_uci(uci))
    # Down to the end of the branch: the user's move, then the opponent reply
    # that leads to a due move if any, else the first one built
    while len(board.move_stack) < LINE_MAX_PLIES:
        if board.turn == user_turn:
            chosen = moves.get(board.epd())
            if chosen is None:
                break
            board.push(chess.Move.from_uci(chosen))
            continue
        options = []
        for move in board.legal_moves:
            board.push(move)
            key = board.epd()
            board.pop()
            if key in moves and key in parents:
                options.append((key not in toward_due, by_key[key].id, move))
        if not options:
            break
        board.push(min(options)[2])

    out: list[LineMove] = []
    replay = chess.Board()
    names: list[str] = []
    for move in board.move_stack:
        fen_before = replay.fen()
        san = replay.san(move)
        key = replay.epd()
        if replay.turn == user_turn:
            row = by_key.get(key)
            card = row.cards[0] if row is not None and row.cards else None
            expl = await preferred_explanation(db, key, move.uci())
            out.append(LineMove(
                uci=move.uci(), san=san, by="user", fen_before=fen_before,
                card_id=card.id if card else None,
                graded=bool(card and card.id in due_ids),
                new=bool(card and card.last_reviewed_at is None and card.id in due_ids),
                explanation=explanation_response(expl) if expl else None,
            ))
        else:
            out.append(LineMove(uci=move.uci(), san=san, by="opponent", fen_before=fen_before,
                                card_id=None, graded=False, new=False, explanation=None))
        replay.push(move)
        names.append(replay.epd())
    label = await _opening_label(db, names)
    return LinePractice(color=color, title=label.name if label else None, moves=out)
