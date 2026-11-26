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
    CandidateRow,
    DeviationResponse,
    GraphEdgeOut,
    GraphNodeOut,
    OpeningLabel,
    PanelRepertoireMove,
    PositionPanelResponse,
    PositionRequest,
    RepertoireChangeResponse,
    RepertoireEntry,
    RepertoireGraphResponse,
    ReplyRow,
    SetPositionRequest,
)
from ..services.deviation_service import recheck_games
from ..services.engine_lines import get_engine_lines
from ..services.explanation_service import preferred_explanation
from ..services.masters_service import MasterMove, MastersBook, is_theory
from ..services.repertoire_graph import build_graph
from ..services.repertoire_service import (
    START_KEY,
    WORSE_WINPROB,
    Node,
    TreeGame,
    build_tree,
    reachable,
    suggest,
)
from .deps import get_current_user
from .openings import explanation_response

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
    await recheck_games(db, user.id, color)
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
    await recheck_games(db, user.id, color)
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
    white_to_move = board.turn == chess.WHITE

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
            expl = await preferred_explanation(db, key, chosen)
            repertoire_move = PanelRepertoireMove(
                uci=chosen, san=san(chosen),
                explanation=explanation_response(expl) if expl else None,
            )
        engine = await get_engine_lines(db, key)
        engine_busy = engine is None
        ranks = {m.uci: (i + 1, m.eval_cp) for i, m in enumerate(engine or [])}
        mine = {e.uci: e for e in played}
        ucis_here = list(ranks)
        ucis_here += [m.uci for m in masters_here or () if theory_of(m.uci)]
        ucis_here += list(mine)
        if chosen:
            ucis_here.append(chosen)
        total_mine = sum(e.count for e in played)
        for uci in dict.fromkeys(ucis_here):
            m, e = master_by_uci.get(uci), mine.get(uci)
            rank, cp = ranks.get(uci, (None, None))
            candidates.append(CandidateRow(
                uci=uci, san=san(uci), engine_rank=rank, eval_cp=cp,
                master_games=m.games if m else 0,
                master_share=round((m.games if m else 0) / total_master, 3) if total_master else None,
                master_score=round(_master_score(m, white_to_move), 3) if m else None,
                games=e.count if e else 0,
                share=round((e.count if e else 0) / total_mine, 3) if total_mine else None,
                score=round(e.score, 3) if e else None,
                theory=theory_of(uci),
            ))
        candidates.sort(key=lambda c: (c.engine_rank is None, c.engine_rank or 0,
                                       -c.master_games, -c.games, c.san))
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


@router.post("/{color}/start", response_model=RepertoireChangeResponse)
async def start_from_my_games(
    color: Color,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepertoireChangeResponse:
    """
    "Start from my games": accept the repertoire suggested from the user's
    games, taking a theory move where their usual one is off-book (the one
    masters play most, when the masters data is there). Only for an empty
    colour; after that the user builds position by position.
    """
    if await _accepted_rows(db, user.id, color):
        raise HTTPException(status.HTTP_409_CONFLICT, "This colour already has a repertoire")
    theory, _ = await _load_book(db)
    tree = build_tree(await _load_games(db, user.id, color), color, theory=theory)
    suggestions = suggest(tree, color, theory=theory)
    book = _masters_book(request)
    available = book is not None
    masters = await _master_moves(book, [s.position_key for s in suggestions])

    rows: list[RepertoireMove] = []
    for s in suggestions:  # line order
        move_uci, move_san = s.move_uci, s.move_san
        here = masters.get(s.position_key)
        if not is_theory(s.position_key, s.move_uci, here, theory, available):
            # Every move that counts as theory here: the dataset's named lines,
            # then the master moves; the one masters play most wins (ties keep order)
            options = [uci for uci, _ in s.theory_alternatives]
            options += [m.uci for m in here or ()
                        if is_theory(s.position_key, m.uci, here, theory, available)]
            if options:
                counts = {m.uci: m.games for m in here or ()}
                move_uci = max(dict.fromkeys(options), key=lambda u: counts.get(u, 0))
                move_san = _san(s.position_key, move_uci)
        row = RepertoireMove(
            user_id=user.id, color=color, position_key=s.position_key,
            move_uci=move_uci, move_san=move_san, parent_key=s.parent_key,
            source="games", cards=[],
        )
        db.add(row)
        await _add_card(db, row)
        rows.append(row)

    removed = await _prune(db, rows, color)
    await db.flush()
    await recheck_games(db, user.id, color)
    response = await _change_response(db, user, color, removed)
    _kick_explanations(request)
    return response
