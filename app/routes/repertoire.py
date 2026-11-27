"""
Repertoire routes (specs/features/02-openings, design D3; R2, R3, R5.1, R5.3).

The suggestions come from the user's own games (repertoire_service); what
the user accepts is stored in repertoire_moves, one row per position where
it's their turn, each with an `opening` review card. After every change the
repertoire is re-walked from the start (repertoire_service.reachable): a
position the repertoire no longer leads to is removed with its card, and
every surviving position gets the parent of the first line that reaches it.
"""
import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Literal

import chess
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..db import async_session, get_db
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
    CandidateRow,
    DeviationResponse,
    ExploreResponse,
    GapOut,
    GoalRequest,
    GraphEdgeOut,
    GraphNodeOut,
    NextResponse,
    OpeningLabel,
    PanelRepertoireMove,
    PositionPanelResponse,
    PositionRequest,
    RepertoireChangeResponse,
    RepertoireEntry,
    RepertoireGraphResponse,
    ReplyChoice,
    ReplyRow,
    SetPositionRequest,
)
from ..services import builder_prefetch, builder_service
from ..services.deviation_service import recheck_games
from ..services.engine_lines import get_engine_lines
from ..services.explorer_service import ExplorerMove
from ..services.lichess_auth import user_lichess_token
from ..services.masters_service import MasterMove, MastersBook, is_theory
from ..services.repertoire_builder import completion
from ..services.repertoire_graph import build_graph
from ..services.repertoire_service import (
    START_KEY,
    WORSE_WINPROB,
    Node,
    TreeGame,
    build_tree,
    reachable,
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


@router.put("/{color}/position", response_model=RepertoireChangeResponse)
async def set_position(
    color: Color,
    body: SetPositionRequest,
    request: Request,
    with_next: bool = Query(True, description="Answer with the builder's next gap"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepertoireChangeResponse:
    """
    Set the repertoire move in a position: replace a move, or add one by hand
    (including past what the user's games cover). The position must be one
    the repertoire reaches. Replacing a move removes the positions it no
    longer leads to.
    """
    builder_prefetch.cancel_prefetch(request.app, user.id)
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
    response = await _change_response(db, user, color, removed)
    # Check the user's games against the changed repertoire (D6) in the
    # background: the next step shouldn't wait for it
    _recheck_in_background(request, user.id, color)
    _kick_explanations(request)
    if with_next:
        response.next = await _next_response(db, request, user, color)
        schedule_prefetch(request.app, user.id, color, _goal(user, color))
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
    await db.flush()
    await recheck_games(db, user.id, color)
    return await _change_response(db, user, color, removed)


# --- the repertoire graph (specs/features/02-openings/repertoire-graph.md) ---

def _masters_book(request: Request) -> MastersBook | None:
    book = getattr(request.app.state, "masters_book", None)
    return book if book is not None and book.available else None


async def _master_moves(book: MastersBook | None, keys) -> dict[str, list[MasterMove]]:
    if book is None:
        return {}
    return await asyncio.to_thread(book.moves, list(keys))


def _theory_source(book: MastersBook | None) -> str:
    return "masters" if book is not None else "dataset"


def _games_reaching(tree: Mapping[str, Node], key: str) -> int:
    if key == START_KEY:
        root = tree.get(START_KEY)
        return sum(e.count for e in root.moves.values()) if root else 0
    return sum(e.count for n in tree.values() for e in n.moves.values() if e.child_key == key)


def _master_score(m: MasterMove, white_to_move: bool) -> float:
    wins = m.white if white_to_move else m.black
    return (wins + 0.5 * m.draws) / m.games if m.games else 0.0


def _parse_line(line: str) -> tuple[chess.Board, list[str], list[str]]:
    """The board after a comma-separated uci line, the ucis, and their SANs; 400 if illegal."""
    board = chess.Board()
    ucis = [u.strip() for u in line.split(",")] if line.strip() else []
    sans = []
    for uci in ucis:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Not a move: {uci!r}") from None
        if move not in board.legal_moves:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Illegal move: {uci}")
        sans.append(board.san(move))
        board.push(move)
    return board, ucis, sans


def _pick_blocked_at(ucis: list[str], color: str, repertoire: Mapping[str, str]) -> list[str] | None:
    """The line to the first user position on `ucis` whose repertoire move is
    missing or different, or None when the repertoire reaches the end of it."""
    board = chess.Board()
    for i, uci in enumerate(ucis):
        if (board.turn == chess.WHITE) == (color == "white"):
            if repertoire.get(board.epd()) != uci:
                return ucis[:i]
        board.push(chess.Move.from_uci(uci))
    return None


async def _user_deviation_keys(db: AsyncSession, user_id: int, color: str,
                               repertoire: Mapping[str, str]) -> set[str]:
    """Positions where the user played against a repertoire move that's still current."""
    rows = (await db.execute(
        select(RepertoireDeviation.position_key, RepertoireDeviation.expected_uci)
        .join(AnalyzedGame, AnalyzedGame.id == RepertoireDeviation.game_id)
        .where(RepertoireDeviation.user_id == user_id, RepertoireDeviation.side == "user",
               AnalyzedGame.user_color == color)
    )).all()
    return {key for key, expected in rows if repertoire.get(key) == expected}


async def _candidates(
    db: AsyncSession,
    board: chess.Board,
    key: str,
    *,
    played: list,
    masters_here: list[MasterMove] | None,
    theory: dict,
    available: bool,
    chosen: str | None,
    level: list[ExplorerMove] | None = None,
    engine_wait: float | None = None,
    engine: list | None | object = ...,
    names: dict | None = None,
    include_level: bool = False,
) -> tuple[list[CandidateRow], bool]:
    """The choices at the user's turn: the engine's top moves, master theory,
    the user's own moves and their repertoire move, with each one's numbers.
    The bool says the engine was busy."""
    white_to_move = board.turn == chess.WHITE
    if engine is ...:  # not run by the caller already
        engine = await (get_engine_lines(db, key) if engine_wait is None
                        else get_engine_lines(db, key, wait=engine_wait))
    ranks = {m.uci: (i + 1, m.eval_cp) for i, m in enumerate(engine or [])}
    master_by_uci = {m.uci: m for m in masters_here or ()}
    total_master = sum(m.games for m in masters_here or ())
    level_by_uci = {m.uci: m for m in level or ()}
    total_level = sum(m.games for m in level or ())
    mine = {e.uci: e for e in played}
    total_mine = sum(e.count for e in played)

    def theory_of(uci: str) -> bool:
        return is_theory(key, uci, masters_here, theory, available)

    ucis_here = list(ranks)
    ucis_here += [m.uci for m in masters_here or () if theory_of(m.uci)]
    ucis_here += list(mine)
    if include_level and total_level:
        # What players at the user's level play, down to 1% of games
        ucis_here += [m.uci for m in level or () if m.games / total_level >= 0.01]
    if chosen:
        ucis_here.append(chosen)
    candidates = []
    for uci in dict.fromkeys(ucis_here):
        m, e, lv = master_by_uci.get(uci), mine.get(uci), level_by_uci.get(uci)
        rank, cp = ranks.get(uci, (None, None))
        level_wins = (lv.white if white_to_move else lv.black) if lv else 0
        level_losses = (lv.black if white_to_move else lv.white) if lv else 0
        child_name = None
        if names is not None:
            board.push(chess.Move.from_uci(uci))
            child_name = names.get(board.epd())
            board.pop()
        candidates.append(CandidateRow(
            uci=uci, san=board.san(chess.Move.from_uci(uci)), engine_rank=rank, eval_cp=cp,
            master_games=m.games if m else 0,
            master_share=round((m.games if m else 0) / total_master, 3) if total_master else None,
            master_score=round(_master_score(m, white_to_move), 3) if m else None,
            games=e.count if e else 0,
            share=round((e.count if e else 0) / total_mine, 3) if total_mine else None,
            score=round(e.score, 3) if e else None,
            theory=theory_of(uci),
            level_share=round((lv.games if lv else 0) / total_level, 3) if total_level else None,
            level_score=round((level_wins + 0.5 * lv.draws) / lv.games, 3)
            if lv and lv.games else None,
            level_win=round(level_wins / lv.games, 3) if lv and lv.games else None,
            level_draw=round(lv.draws / lv.games, 3) if lv and lv.games else None,
            level_loss=round(level_losses / lv.games, 3) if lv and lv.games else None,
            opening=OpeningLabel(eco=child_name[0], name=child_name[1]) if child_name else None,
        ))
    candidates.sort(key=lambda c: (c.engine_rank is None, c.engine_rank or 0,
                                   -c.master_games, -c.games, c.san))
    return candidates, engine is None


# --- the step-by-step builder (specs/features/02-openings/repertoire-builder.md) ---

GOALS = (20, 50, 100, 200)
NEXT_BUDGET_S = 3.0  # the most a step waits on the Lichess explorer
ENGINE_WAIT_S = 1.0  # the most a step waits for the engine to be free


def schedule_prefetch(app, user_id: int, color: str, goal: int,
                      focus: list[str] | None = None) -> None:
    """Prepare the next steps in the background (off in tests): around the
    position on screen (`focus`, uci from the start) and the next gaps."""
    if settings.BUILDER_PREFETCH:
        builder_prefetch.schedule_prefetch(app, user_id, color, goal, focus=focus)


def _goal(user: User, color: str) -> int:
    return (user.goal_white if color == "white" else user.goal_black) or GOALS[0]


def _recheck_in_background(request: Request, user_id: int, color: str) -> None:
    async def run() -> None:
        try:
            async with async_session() as db:
                await recheck_games(db, user_id, color)
                await db.commit()
        except Exception:
            logger.exception(f"Repertoire recheck failed for user {user_id} ({color})")

    tasks = getattr(request.app.state, "recheck_tasks", None)
    if tasks is None:
        tasks = request.app.state.recheck_tasks = set()
    task = asyncio.create_task(run())
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def _games_by_key(tree: Mapping[str, Node]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in tree.values():
        for e in node.moves.values():
            counts[e.child_key] = counts.get(e.child_key, 0) + e.count
    root = tree.get(START_KEY)
    counts[START_KEY] = sum(e.count for e in root.moves.values()) if root else 0
    return counts


async def _next_response(
    db: AsyncSession, request: Request, user: User, color: str, line: str | None = None,
) -> NextResponse:
    """The builder's next step: the first gap (or the position `line` leads
    to), its choices, and progress at the user's goal."""
    theory, names = await _load_book(db)
    tree = build_tree(await _load_games(db, user.id, color), color, theory=theory)
    repertoire = {r.position_key: r.move_uci for r in await _accepted_rows(db, user.id, color)}
    book = _masters_book(request)
    goal = _goal(user, color)
    http = request.app.state.oauth_http
    loop = asyncio.get_running_loop()
    deadline = loop.time() + NEXT_BUDGET_S
    result, source = await builder_service.next_gaps(
        db, http=http, masters=book, user=user, color=color, goal=goal,
        repertoire=repertoire, my_games=_games_by_key(tree), budget_s=NEXT_BUDGET_S,
    )

    gap = result.gaps[0] if result.gaps else None
    if line is not None:
        board, ucis, _ = _parse_line(line)
        key = board.epd()
        user_turn = (board.turn == chess.WHITE) == (color == "white")
        if not user_turn or _pick_blocked_at(ucis, color, repertoire) is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "That isn't a position your repertoire reaches at your turn")
        gap = next((g for g in result.gaps if g.key == key), None) or \
            builder_service.Gap(key, ucis, None, ucis[-1] if ucis else None, None,
                                _games_by_key(tree).get(key, 0))

    gap_out = None
    if gap is not None:
        board = chess.Board()
        sans = []
        opening = names.get(START_KEY)
        for uci in gap.line:
            move = chess.Move.from_uci(uci)
            sans.append(board.san(move))
            board.push(move)
            opening = names.get(board.epd(), opening)
        masters_here = (await _master_moves(book, [gap.key])).get(gap.key)
        node = tree.get(gap.key)
        played = sorted(node.moves.values(), key=lambda e: (-e.count, e.san)) if node else []
        # The explorer (network) and the engine (CPU) at the same time; the
        # explorer lookup gets its own session, since one can't be shared
        async def level_lookup():
            async with async_session() as level_db:
                return await builder_service.level_moves(
                    level_db, http, user, gap.key, budget_s=max(0.0, deadline - loop.time()))

        level, engine = await asyncio.gather(
            level_lookup(), get_engine_lines(db, gap.key, wait=ENGINE_WAIT_S))
        candidates, engine_busy = await _candidates(
            db, board, gap.key, played=played, masters_here=masters_here, theory=theory,
            available=book is not None, chosen=repertoire.get(gap.key), level=level,
            engine=engine,
        )
        last_san = sans[-1] if gap.last_reply and sans else None
        gap_out = GapOut(
            line=gap.line, line_san=sans, fen=board.fen(), key=gap.key,
            likelihood=round(gap.likelihood, 4) if gap.likelihood is not None else None,
            last_reply_san=last_san,
            last_share=round(gap.last_share, 3) if gap.last_share is not None else None,
            my_games=gap.my_games,
            opening=OpeningLabel(eco=opening[0], name=opening[1]) if opening else None,
            candidates=candidates, engine_busy=engine_busy,
        )
    return NextResponse(
        gap=gap_out, prepared=result.prepared, left=len(result.gaps), goal=goal,
        source=source, reconnect=user_lichess_token(user) is None,
    )


@router.get("/{color}/next", response_model=NextResponse)
async def get_next(
    color: Color,
    request: Request,
    line: str | None = Query(None, description="Open this position instead: uci, comma-separated"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NextResponse:
    """The most likely position the user hasn't prepared, with its choices."""
    builder_prefetch.cancel_prefetch(request.app, user.id)
    response = await _next_response(db, request, user, color, line)
    schedule_prefetch(request.app, user.id, color, response.goal)
    return response


async def _reply_data(db, http, user, book, key, known):
    """(uci, games) for the opponent's moves in `key`: what the walk used,
    else the explorer (cache or a quick fetch), else master games."""
    if key in known:
        return known[key]
    level = await builder_service.level_moves(db, http, user, key, budget_s=1.0)
    if level:
        return [(m.uci, m.games) for m in level]
    masters = (await _master_moves(book, [key])).get(key) or []
    return [(m.uci, m.games) for m in masters]


async def _line_likelihood(db, http, user, book, color, ucis, known) -> float | None:
    """How often the user reaches the end of `ucis`: the product of the
    opponent's moves' shares along it. None where a share isn't known."""
    user_turn = chess.WHITE if color == "white" else chess.BLACK
    board = chess.Board()
    likelihood = 1.0
    for uci in ucis:
        if board.turn != user_turn:
            data = dict(await _reply_data(db, http, user, book, board.epd(), known))
            total = sum(data.values())
            if not total or not data.get(uci):
                return None
            likelihood *= data[uci] / total
        board.push(chess.Move.from_uci(uci))
    return likelihood


@router.get("/{color}/explore", response_model=ExploreResponse)
async def get_explore(
    color: Color,
    request: Request,
    line: str = Query("", description="Moves from the start, uci, comma-separated"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExploreResponse:
    """One position in the navigator: the opponent's likely replies, each named
    with how often it comes up and how much below it is prepared, or the
    user's choices with masters, engine and results at their level."""
    builder_prefetch.cancel_prefetch(request.app, user.id)
    board, ucis, sans = _parse_line(line)
    key = board.epd()
    user_to_move = (board.turn == chess.WHITE) == (color == "white")
    theory, names = await _load_book(db)
    tree = build_tree(await _load_games(db, user.id, color), color, theory=theory)
    repertoire = {r.position_key: r.move_uci for r in await _accepted_rows(db, user.id, color)}
    book = _masters_book(request)
    goal = _goal(user, color)
    http = request.app.state.oauth_http
    loop = asyncio.get_running_loop()
    deadline = loop.time() + NEXT_BUDGET_S
    result, source = await builder_service.next_gaps(
        db, http=http, masters=book, user=user, color=color, goal=goal,
        repertoire=repertoire, my_games=_games_by_key(tree), budget_s=NEXT_BUDGET_S,
    )
    likelihood = result.likelihood.get(key)
    if likelihood is None:
        likelihood = await _line_likelihood(db, http, user, book, color, ucis, result.replies)

    opening = names.get(START_KEY)
    walk_board = chess.Board()
    for uci in ucis:
        walk_board.push(chess.Move.from_uci(uci))
        opening = names.get(walk_board.epd(), opening)

    candidates: list[CandidateRow] = []
    replies: list[ReplyChoice] = []
    engine_busy = False
    if user_to_move:
        async def level_lookup():
            async with async_session() as level_db:
                return await builder_service.level_moves(
                    level_db, http, user, key, budget_s=max(0.0, deadline - loop.time()))

        level, engine = await asyncio.gather(
            level_lookup(), get_engine_lines(db, key, wait=ENGINE_WAIT_S))
        masters_here = (await _master_moves(book, [key])).get(key)
        node = tree.get(key)
        played = sorted(node.moves.values(), key=lambda e: (-e.count, e.san)) if node else []
        candidates, engine_busy = await _candidates(
            db, board, key, played=played, masters_here=masters_here, theory=theory,
            available=book is not None, chosen=repertoire.get(key), level=level,
            engine=engine, names=names, include_level=True,
        )
        candidates.sort(key=lambda c: (-(c.master_share or 0), -(c.level_share or 0),
                                       c.engine_rank or 99, c.san))
    else:
        data = await _reply_data(db, http, user, book, key, result.replies)
        total = sum(games for _, games in data)
        floor = 1.0 / goal
        for uci, games in sorted(data, key=lambda p: -p[1]):
            if not total or not games:
                continue
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                continue
            san = board.san(move)
            board.push(move)
            child = board.epd()
            board.pop()
            share = games / total
            child_likelihood = likelihood * share if likelihood is not None else None
            name = names.get(child)
            replies.append(ReplyChoice(
                uci=uci, san=san,
                opening=OpeningLabel(eco=name[0], name=name[1]) if name else None,
                share=round(share, 3),
                likelihood=round(child_likelihood, 4) if child_likelihood is not None else None,
                completion=_round(completion(result, ucis + [uci])),
                in_scope=child_likelihood is not None and child_likelihood + 1e-12 >= floor,
                answered=child in repertoire,
            ))

    schedule_prefetch(request.app, user.id, color, goal, focus=ucis)
    return ExploreResponse(
        line=ucis, line_san=sans, fen=board.fen(), key=key, user_to_move=user_to_move,
        opening=OpeningLabel(eco=opening[0], name=opening[1]) if opening else None,
        likelihood=_round(likelihood, 4), completion=_round(completion(result, [])),
        goal=goal, prepared=result.prepared, left=len(result.gaps), source=source,
        reconnect=user_lichess_token(user) is None,
        repertoire_move=repertoire.get(key) if user_to_move else None,
        pick_blocked_at=_pick_blocked_at(ucis, color, repertoire),
        replies=replies, candidates=candidates, engine_busy=engine_busy,
    )


def _round(x: float | None, digits: int = 3) -> float | None:
    return round(x, digits) if x is not None else None


@router.put("/{color}/goal", response_model=NextResponse)
async def set_goal(
    color: Color,
    body: GoalRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NextResponse:
    """Set the coverage goal (1 game in N) for this colour."""
    if body.goal not in GOALS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"The goal must be one of {list(GOALS)}")
    builder_prefetch.cancel_prefetch(request.app, user.id)
    user = await db.merge(user)
    if color == "white":
        user.goal_white = body.goal
    else:
        user.goal_black = body.goal
    await db.commit()
    response = await _next_response(db, request, user, color)
    schedule_prefetch(request.app, user.id, color, body.goal)
    return response


@router.get("/{color}/graph", response_model=RepertoireGraphResponse)
async def get_graph(
    color: Color,
    request: Request,
    unfold: list[str] = Query(default_factory=list),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepertoireGraphResponse:
    """The user's lines for one colour, merged with their repertoire and theory, folded."""
    theory, names = await _load_book(db)
    tree = build_tree(await _load_games(db, user.id, color), color, theory=theory)
    repertoire = {r.position_key: r.move_uci for r in await _accepted_rows(db, user.id, color)}
    book = _masters_book(request)
    masters = await _master_moves(book, set(tree) | set(repertoire))
    deviations = await _user_deviation_keys(db, user.id, color, repertoire)
    graph = build_graph(tree, color, repertoire, masters, theory, names, deviations,
                        book is not None, frozenset(unfold))

    # The builder's gaps at the user's goal, from cached data only: the
    # overview never waits on the explorer
    walked, _ = await builder_service.next_gaps(
        db, http=request.app.state.oauth_http, masters=book, user=user, color=color,
        goal=_goal(user, color), repertoire=repertoire, my_games=_games_by_key(tree), budget_s=0,
    )

    plies = (await db.scalars(
        select(AnalyzedGame.repertoire_plies).where(
            AnalyzedGame.user_id == user.id, AnalyzedGame.user_color == color,
            AnalyzedGame.repertoire_plies.is_not(None),
        )
    )).all()
    return RepertoireGraphResponse(
        nodes=[
            GraphNodeOut(
                key=n.key, user_to_move=n.user_to_move, games=n.games, ply=n.ply,
                parent=n.parent,
                opening=OpeningLabel(eco=n.opening[0], name=n.opening[1]) if n.opening else None,
                mark=n.mark, folded=n.folded,
            )
            for n in graph.nodes.values()
        ],
        edges=[GraphEdgeOut(**e.__dict__) for e in graph.edges],
        todo=graph.todo,
        theory_source=_theory_source(book),
        avg_moves_in_repertoire=round(sum(plies) / len(plies) / 2, 1) if plies else None,
        empty_repertoire=not repertoire,
        gaps=[g.key for g in walked.gaps],
        thin=sorted(walked.thin),
    )


@router.get("/{color}/position", response_model=PositionPanelResponse)
async def get_position_panel(
    color: Color,
    request: Request,
    line: str = Query("", description="Moves from the start, uci, comma-separated"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PositionPanelResponse:
    """Everything the panel shows for one position (the graph's selected node)."""
    board, ucis, sans = _parse_line(line)
    key = board.epd()
    user_to_move = (board.turn == chess.WHITE) == (color == "white")
    theory, names = await _load_book(db)
    tree = build_tree(await _load_games(db, user.id, color), color, theory=theory)
    repertoire = {r.position_key: r.move_uci for r in await _accepted_rows(db, user.id, color)}
    book = _masters_book(request)
    masters_here = (await _master_moves(book, [key])).get(key)
    master_by_uci = {m.uci: m for m in masters_here or ()}
    total_master = sum(m.games for m in masters_here or ())
    available = book is not None
    node = tree.get(key)
    played = sorted(node.moves.values(), key=lambda e: (-e.count, e.san)) if node else []

    def theory_of(uci: str) -> bool:
        return is_theory(key, uci, masters_here, theory, available)

    def san(uci: str) -> str:
        return board.san(chess.Move.from_uci(uci))

    # The opening name, inherited along the line
    opening = names.get(START_KEY)
    walk = chess.Board()
    for uci in ucis:
        walk.push(chess.Move.from_uci(uci))
        opening = names.get(walk.epd(), opening)

    repertoire_move = None
    candidates: list[CandidateRow] = []
    replies: list[ReplyRow] = []
    answered_pct = None
    engine_busy = False
    note = None
    if user_to_move:
        chosen = repertoire.get(key)
        if chosen:
            # Explanations are read in Learn, not here (repertoire-builder.md)
            repertoire_move = PanelRepertoireMove(uci=chosen, san=san(chosen))
        candidates, engine_busy = await _candidates(
            db, board, key, played=played, masters_here=masters_here, theory=theory,
            available=available, chosen=chosen,
        )
        if played:
            usual = max(played, key=lambda e: (e.count, e.score, e.uci))
            if not theory_of(usual.uci):
                note = f"You usually play {usual.san} here, which is off-book."
            elif usual.avg_drop is not None and usual.avg_drop >= WORSE_WINPROB:
                others = [(n, u) for u, n in usual.best_ucis.items() if u != usual.uci]
                if others:
                    note = (f"You usually play {usual.san} here; "
                            f"{san(max(others)[1])} is clearly better.")
    else:
        total = sum(e.count for e in played)
        answered = 0
        for e in played:
            answer = repertoire.get(e.child_key)
            if answer:
                answered += e.count
            m = master_by_uci.get(e.uci)
            replies.append(ReplyRow(
                uci=e.uci, san=e.san, games=e.count,
                share=round(e.count / total, 3) if total else 0.0,
                score=round(e.score, 3),
                master_share=round(m.games / total_master, 3) if m and total_master else
                (0.0 if total_master else None),
                theory=theory_of(e.uci),
                answer_san=_san(e.child_key, answer) if answer else None,
                in_games=True,
            ))
        met = {e.uci for e in played}
        for m in masters_here or ():
            if m.uci in met or not theory_of(m.uci):
                continue
            board.push(chess.Move.from_uci(m.uci))
            child = board.epd()
            board.pop()
            answer = repertoire.get(child)
            replies.append(ReplyRow(
                uci=m.uci, san=san(m.uci), games=0, share=0.0, score=None,
                master_share=round(m.games / total_master, 3) if total_master else None,
                theory=True, answer_san=_san(child, answer) if answer else None,
                in_games=False,
            ))
        answered_pct = round(100 * answered / total, 1) if total else None

    return PositionPanelResponse(
        key=key, fen=board.fen(), line_san=sans, user_to_move=user_to_move,
        opening=OpeningLabel(eco=opening[0], name=opening[1]) if opening else None,
        games=_games_reaching(tree, key),
        repertoire_move=repertoire_move, candidates=candidates, replies=replies,
        answered_pct=answered_pct,
        pick_blocked_at=_pick_blocked_at(ucis, color, repertoire),
        theory_source=_theory_source(book), engine_busy=engine_busy, usual_move_note=note,
    )
