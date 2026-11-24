"""
Repertoire routes (specs/features/02-openings, design D3; R2, R3, R5.1, R5.3).

The suggestions come from the user's own games (repertoire_service); what
the user accepts is stored in repertoire_moves, one row per position where
it's their turn, each with an `opening` review card. After every change the
repertoire is re-walked from the start (repertoire_service.reachable): a
position the repertoire no longer leads to is removed with its card, and
every surviving position gets the parent of the first line that reaches it.
"""
import json
import logging
from typing import Literal

import chess
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_db
from ..models.db_models import (
    AnalyzedGame,
    OpeningName,
    RepertoireDeviation,
    RepertoireMove,
    ReviewLog,
    TheoryMove,
    User,
    new_review_card,
)
from ..models.schemas import (
    AcceptRequest,
    DeviationResponse,
    MoveOption,
    OpeningLabel,
    PositionRequest,
    RepertoireChangeResponse,
    RepertoireEntry,
    RepertoireResponse,
    RepertoireSuggestion,
    SetPositionRequest,
    TheoryAlternative,
)
from ..services.deviation_service import check_games
from ..services.repertoire_service import (
    TreeGame,
    build_tree,
    reachable,
    suggest,
)
from .deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/repertoire", tags=["repertoire"])

Color = Literal["white", "black"]


# --- loading ----------------------------------------------------------------

async def _load_book(db: AsyncSession) -> tuple[dict, dict]:
    theory = {
        (key, uci): line
        for key, uci, line in (await db.execute(
            select(TheoryMove.position_key, TheoryMove.move_uci, TheoryMove.line_name)
        )).all()
    }
    names = {
        key: (eco, name)
        for key, eco, name in (await db.execute(
            select(OpeningName.position_key, OpeningName.eco, OpeningName.name)
        )).all()
    }
    return theory, names


async def _load_games(db: AsyncSession, user_id: int, color: str) -> list[TreeGame]:
    rows = (await db.execute(
        select(AnalyzedGame.result, AnalyzedGame.positions, AnalyzedGame.initial_eval_cp)
        .where(
            AnalyzedGame.user_id == user_id,
            AnalyzedGame.user_color == color,
            AnalyzedGame.positions.is_not(None),
        )
    )).all()
    return [
        TreeGame(user_color=color, result=result, positions=json.loads(positions),
                 initial_eval_cp=initial)
        for result, positions, initial in rows
    ]


async def _accepted_rows(db: AsyncSession, user_id: int, color: str) -> list[RepertoireMove]:
    return list((await db.scalars(
        select(RepertoireMove)
        .where(RepertoireMove.user_id == user_id, RepertoireMove.color == color)
        .options(selectinload(RepertoireMove.cards))
        .order_by(RepertoireMove.id)
    )).all())


# --- positions --------------------------------------------------------------

def _board(key: str, color: str) -> chess.Board:
    """The board for a user position, or a 400 if the key isn't one."""
    board = chess.Board()
    try:
        board.set_epd(key)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not a position: {key!r}") from None
    if not board.is_valid():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not a legal position: {key!r}")
    if (board.turn == chess.WHITE) != (color == "white"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "It isn't your move in that position")
    return board


def _san(key: str, uci: str) -> str:
    board = chess.Board()
    board.set_epd(key)
    return board.san(chess.Move.from_uci(uci))


def _opening(key: str, names: dict, inherited: tuple[str, str] | None = None):
    label = names.get(key, inherited)
    return OpeningLabel(eco=label[0], name=label[1]) if label else None


def _entry(row: RepertoireMove, names: dict) -> RepertoireEntry:
    return RepertoireEntry(
        position_key=row.position_key,
        move_uci=row.move_uci,
        move_san=row.move_san,
        parent_key=row.parent_key,
        source=row.source,
        opening=_opening(row.position_key, names),
        card_id=row.cards[0].id if row.cards else None,
    )


# --- changing the repertoire ------------------------------------------------

async def _delete_cards(db: AsyncSession, row: RepertoireMove) -> None:
    """Remove a position's cards and their review history."""
    card_ids = [card.id for card in row.cards]
    if card_ids:
        await db.execute(delete(ReviewLog).where(ReviewLog.card_id.in_(card_ids)))
    for card in list(row.cards):
        await db.delete(card)
    row.cards.clear()


async def _add_card(db: AsyncSession, row: RepertoireMove) -> None:
    await db.flush()  # the row needs its id first
    card = new_review_card(
        user_id=row.user_id, kind="opening", card_type="play", repertoire_move_id=row.id
    )
    db.add(card)
    await db.flush()  # one at a time, so card ids follow line order
    row.cards.append(card)


async def _prune(db: AsyncSession, rows: list[RepertoireMove], color: str) -> int:
    """Remove positions the repertoire no longer reaches; refresh parent keys."""
    parents = reachable(color, {r.position_key: r.move_uci for r in rows})
    removed = 0
    for row in rows:
        if row.position_key in parents:
            row.parent_key = parents[row.position_key]
        else:
            await _delete_cards(db, row)
            await db.delete(row)
            removed += 1
    return removed


async def _change_response(
    db: AsyncSession, user: User, color: str, removed: int
) -> RepertoireChangeResponse:
    await db.commit()
    _, names = await _load_book(db)
    rows = await _accepted_rows(db, user.id, color)
    order = _line_order(rows, color)
    return RepertoireChangeResponse(
        accepted=[_entry(r, names) for r in order], removed=removed
    )


def _kick_explanations(request: Request) -> None:
    """New positions may need explanations: wake the background worker."""
    kick = getattr(request.app.state, "explanation_kick", None)
    if kick is not None:
        kick.set()


def _line_order(rows: list[RepertoireMove], color: str) -> list[RepertoireMove]:
    """Rows in line order (parents first), the order reachable() walks them."""
    by_key = {r.position_key: r for r in rows}
    parents = reachable(color, {r.position_key: r.move_uci for r in rows})
    return [by_key[key] for key in parents]


# --- routes -----------------------------------------------------------------
# /deviations is declared before /{color}, so "deviations" isn't taken for a colour.

@router.get("/deviations", response_model=list[DeviationResponse])
async def list_deviations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DeviationResponse]:
    """Where recent games left the user's repertoire, newest first (R6.4)."""
    rows = (await db.execute(
        select(RepertoireDeviation, AnalyzedGame.lichess_game_id, AnalyzedGame.played_at)
        .join(AnalyzedGame, AnalyzedGame.id == RepertoireDeviation.game_id)
        .where(RepertoireDeviation.user_id == user.id)
        .order_by(AnalyzedGame.played_at.desc())
        .limit(50)
    )).all()
    return [
        DeviationResponse(
            game_id=d.game_id, lichess_game_id=lichess_id, played_at=played_at.isoformat(),
            ply=d.ply, side=d.side, position_key=d.position_key, expected_uci=d.expected_uci,
            played_uci=d.played_uci, punish_uci=d.punish_uci,
            played_was_theory=d.played_was_theory,
        )
        for d, lichess_id, played_at in rows
    ]


@router.get("/{color}", response_model=RepertoireResponse)
async def get_repertoire(
    color: Color,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepertoireResponse:
    """The repertoire suggested from the user's games, and what they accepted."""
    theory, names = await _load_book(db)
    games = await _load_games(db, user.id, color)
    suggestions = suggest(build_tree(games, color, theory=theory), color,
                          theory=theory, names=names)
    rows = await _accepted_rows(db, user.id, color)
    accepted_moves = {r.position_key: r.move_uci for r in rows}

    out = []
    for s in suggestions:
        out.append(RepertoireSuggestion(
            position_key=s.position_key, ply=s.ply, move_uci=s.move_uci, move_san=s.move_san,
            games=s.games, score=round(s.score, 3), parent_key=s.parent_key,
            opening=OpeningLabel(eco=s.opening[0], name=s.opening[1]) if s.opening else None,
            theory_line=s.theory_line,
            theory_alternatives=[
                TheoryAlternative(uci=uci, san=_san(s.position_key, uci), line=line)
                for uci, line in s.theory_alternatives
            ],
            avg_drop=round(s.avg_drop, 1) if s.avg_drop is not None else None,
            better_uci=s.better_uci,
            better_san=_san(s.position_key, s.better_uci) if s.better_uci else None,
            accepted_move_uci=accepted_moves.get(s.position_key),
        ))
    return RepertoireResponse(
        color=color, games_used=len(games), suggestions=out,
        accepted=[_entry(r, names) for r in _line_order(rows, color)],
    )


@router.post("/{color}/accept", response_model=RepertoireChangeResponse)
async def accept_suggestions(
    color: Color,
    body: AcceptRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepertoireChangeResponse:
    """
    Accept suggested positions into the repertoire, with the line leading to
    each (and, with include_below, every suggestion further down it). Adds
    only: a position already in the repertoire keeps its move; change it
    with PUT /{color}/position.
    """
    theory, _ = await _load_book(db)
    games = await _load_games(db, user.id, color)
    suggestions = suggest(build_tree(games, color, theory=theory), color, theory=theory)
    by_key = {s.position_key: s for s in suggestions}

    unknown = [k for k in body.position_keys if k not in by_key]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Not a suggested position: {unknown[0]!r}")

    wanted: set[str] = set()
    for key in body.position_keys:
        node = key
        while node is not None and node not in wanted:  # the line leading to it
            wanted.add(node)
            node = by_key[node].parent_key
    if body.include_below:
        def below(key: str) -> bool:
            while key is not None:
                if key in body.position_keys:
                    return True
                key = by_key[key].parent_key
            return False
        wanted |= {s.position_key for s in suggestions if below(s.position_key)}

    rows = await _accepted_rows(db, user.id, color)
    existing = {r.position_key for r in rows}
    for s in suggestions:  # suggestion order is line order
        if s.position_key in wanted and s.position_key not in existing:
            row = RepertoireMove(
                user_id=user.id, color=color, position_key=s.position_key,
                move_uci=s.move_uci, move_san=s.move_san, parent_key=s.parent_key,
                source="games", cards=[],  # loaded (empty), so no async lazy load
            )
            db.add(row)
            await _add_card(db, row)
            rows.append(row)

    removed = await _prune(db, rows, color)
    await db.flush()
    # A first repertoire for this colour: check the user's games against it (D6).
    await check_games(db, user.id)
    response = await _change_response(db, user, color, removed)
    _kick_explanations(request)
    return response


@router.put("/{color}/position", response_model=RepertoireChangeResponse)
async def set_position(
    color: Color,
    body: SetPositionRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepertoireChangeResponse:
    """
    Set the repertoire move in a position: replace a move, or add one by hand
    (including past what the user's games cover). The position must be one
    the repertoire reaches. Replacing a move removes the positions it no
    longer leads to.
    """
    board = _board(body.position_key, color)
    try:
        move = chess.Move.from_uci(body.move_uci)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not a move: {body.move_uci!r}") from None
    if move not in board.legal_moves:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That move isn't legal there")

    rows = await _accepted_rows(db, user.id, color)
    planned = {r.position_key: r.move_uci for r in rows}
    planned[body.position_key] = move.uci()
    if body.position_key not in reachable(color, planned):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Your repertoire doesn't reach that position; add the moves leading to it first",
        )

    row = next((r for r in rows if r.position_key == body.position_key), None)
    if row is None:
        row = RepertoireMove(
            user_id=user.id, color=color, position_key=body.position_key,
            move_uci=move.uci(), move_san=board.san(move), source="manual",
            cards=[],  # loaded (empty), so no async lazy load
        )
        db.add(row)
        await _add_card(db, row)
        rows.append(row)
    elif row.move_uci != move.uci():
        # A different move to learn: the old card's history doesn't apply.
        await _delete_cards(db, row)
        row.move_uci, row.move_san, row.source = move.uci(), board.san(move), "manual"
        await _add_card(db, row)

    removed = await _prune(db, rows, color)
    await db.flush()
    # A first repertoire for this colour: check the user's games against it (D6).
    await check_games(db, user.id)
    response = await _change_response(db, user, color, removed)
    _kick_explanations(request)
    return response


@router.delete("/{color}/position", response_model=RepertoireChangeResponse)
async def remove_position(
    color: Color,
    body: PositionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepertoireChangeResponse:
    """Remove a position and every position below it that nothing else reaches."""
    rows = await _accepted_rows(db, user.id, color)
    row = next((r for r in rows if r.position_key == body.position_key), None)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That position isn't in your repertoire")
    await _delete_cards(db, row)
    await db.delete(row)
    rows.remove(row)
    removed = 1 + await _prune(db, rows, color)
    return await _change_response(db, user, color, removed)


@router.get("/{color}/options", response_model=list[MoveOption])
async def move_options(
    color: Color,
    position_key: str = Query(..., description="The position, as board.epd()"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MoveOption]:
    """
    Every legal move in one of the user's positions, for adding a move by
    hand: theory moves first, then the moves the user has played there, then
    the rest (R3.2).
    """
    board = _board(position_key, color)
    theory, _ = await _load_book(db)
    tree = build_tree(await _load_games(db, user.id, color), color)
    played = {uci: e.count for uci, e in tree[position_key].moves.items()} if position_key in tree else {}

    options = [
        MoveOption(uci=m.uci(), san=board.san(m), theory_line=theory.get((position_key, m.uci())),
                   games=played.get(m.uci(), 0))
        for m in board.legal_moves
    ]
    options.sort(key=lambda o: (o.theory_line is None, o.games == 0, -o.games, o.san))
    return options
