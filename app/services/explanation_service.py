"""
Opening explanations written by Claude (specs/features/02-openings, D4;
R4.1, R4.2, R4.6).

For every accepted (position, move) that has no explanation yet, Blundr
asks Claude why the move makes sense, grounded in its own data: the moves so
far, the opening name, whether the move is theory, a Stockfish evaluation
and main line, and the replies the player's opponents have chosen. Answers
are stored once per (position, move) and shared by every player who has
that position.

All generation goes through the Message Batches API at half price, since
nobody waits on an explanation. One cycle (run_explanation_cycle) collects
ended batches, retries refused or unusable answers once as a direct call
with the server-side fallback (which the Batches API doesn't accept), and,
when no batch is open, submits the next batch. The background worker runs
a cycle every few minutes, and right away when positions are accepted.

Every call to the API goes through a small adapter (AnthropicClaude), so
tests use a fake and never reach the API.
"""
import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import anthropic
import chess
import chess.engine
from sqlalchemy import and_, exists, select

from ..config import settings
from ..db import async_session
from ..models.db_models import (
    AnalyzedGame,
    ExplanationBatch,
    OpeningExplanation,
    OpeningName,
    RepertoireMove,
    TheoryMove,
)
from .analysis_service import MATE_SCORE, _analysis_limit, _open_engine
from .repertoire_service import TreeGame, build_tree, line_to, reachable

logger = logging.getLogger(__name__)

# Bump when the prompt or schema changes, so old explanations can be found
# and regenerated.
PROMPT_VERSION = 1

SYSTEM_PROMPT = """\
You explain chess opening moves to a club player (rated about 1200-1800) who is building an \
opening repertoire from their own games. You are given one position from their repertoire and \
the move they play there, with data about it: the moves so far, the opening name, whether the \
move is established theory, a Stockfish evaluation and main line, and the replies their \
opponents have chosen.

Explain the ideas, not a list of moves:
- plan: what each side is trying to do in this kind of position.
- why_this_move: why this move fits that plan, and what it prepares or prevents.
- pawn_structure: the pawn structure and what it means for the plans, only if it matters here; \
otherwise an empty string.

Write plain language, together under about 150 words. Base what you say on the data given. \
Don't mention named games or players. If the position is unusual or the data doesn't support a \
clear point, say less rather than guess."""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plan": {"type": "string"},
        "why_this_move": {"type": "string"},
        "pawn_structure": {"type": "string"},
    },
    "required": ["plan", "why_this_move", "pawn_structure"],
    "additionalProperties": False,
}

FALLBACK_BETA = "server-side-fallback-2026-07-01"
PV_PLIES = 6         # engine main line shown to Claude
REPLIES_SHOWN = 5    # opponents' most common replies shown to Claude


# --- the request --------------------------------------------------------------

@dataclass
class ExplanationInput:
    """Everything Claude is told about one (position, move)."""
    position_key: str
    move_uci: str
    move_san: str
    color: str                               # the player's side
    line_san: list[str]                      # moves from the start to the position
    fen: str
    opening: tuple[str, str] | None          # (eco, name)
    theory_line: str | None                  # named line the move is on; None = off-book
    eval_before_cp: int | None = None        # player's side
    eval_after_cp: int | None = None         # player's side, after the move
    best_san: str | None = None
    pv_san: list[str] | None = None
    opponent_replies: list[tuple[str, int]] | None = None  # (san, games)


def custom_id_for(position_key: str, move_uci: str) -> str:
    """A batch custom_id for a (position, move): stable, and within the API's
    [a-zA-Z0-9_-]{1,64} rule (position keys contain '/' and spaces)."""
    return "x" + hashlib.sha1(f"{position_key}|{move_uci}".encode()).hexdigest()[:40]


def _numbered(sans: list[str]) -> str:
    parts = []
    for i, san in enumerate(sans):
        parts.append(f"{i // 2 + 1}. {san}" if i % 2 == 0 else san)
    return " ".join(parts) if parts else "(the starting position)"


def _eval(cp: int | None) -> str:
    if cp is None:
        return "unknown"
    if abs(cp) >= MATE_SCORE - 1000:
        return "a forced mate for " + ("the player" if cp > 0 else "the opponent")
    return f"{cp / 100:+.2f}"


def build_params(item: ExplanationInput, model: str) -> dict[str, Any]:
    """The Messages API parameters for one explanation (used in a batch)."""
    lines = [
        f"Opening: {item.opening[1]} ({item.opening[0]})" if item.opening else "Opening: unnamed",
        f"Moves so far: {_numbered(item.line_san)}",
        f"Position (FEN): {item.fen}",
        f"The player has the {item.color} pieces and plays: {item.move_san}",
        (f"This move is theory, on the named line: {item.theory_line}" if item.theory_line
         else "This move is not on any named opening line (off-book)."),
    ]
    if item.eval_before_cp is not None:
        lines.append(
            f"Stockfish, from the player's side: {_eval(item.eval_before_cp)} before the move, "
            f"{_eval(item.eval_after_cp)} after it."
        )
    if item.best_san:
        main = f"; its main line: {' '.join(item.pv_san)}" if item.pv_san else ""
        lines.append(f"Stockfish's best move here: {item.best_san}{main}")
    if item.opponent_replies:
        replies = ", ".join(
            f"{san} ({n} game{'s' if n != 1 else ''})" for san, n in item.opponent_replies
        )
        lines.append(f"Replies the player's opponents chose after {item.move_san}: {replies}")
    return {
        "model": model,
        "max_tokens": 8000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": "\n".join(lines)}],
        "output_config": {
            "effort": "medium",
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
    }


def parse_explanation(message: Any) -> dict[str, str | None] | None:
    """The explanation in a Message, or None if refused or unusable."""
    if getattr(message, "stop_reason", None) == "refusal":
        return None
    text = next((b.text for b in message.content if getattr(b, "type", None) == "text"), None)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    plan, why, pawns = data.get("plan"), data.get("why_this_move"), data.get("pawn_structure")
    if not (isinstance(plan, str) and plan.strip() and isinstance(why, str) and why.strip()):
        return None
    if not isinstance(pawns, str):
        return None
    return {"plan": plan.strip(), "why_this_move": why.strip(), "pawn_structure": pawns.strip() or None}


# --- the API adapter ----------------------------------------------------------

class ClaudeClient(Protocol):
    async def create_batch(self, requests: list[dict]) -> str: ...
    async def batch_status(self, batch_id: str) -> str: ...
    async def batch_results(self, batch_id: str) -> list[tuple[str, str, Any]]: ...
    async def create_with_fallback(self, params: dict) -> Any: ...


class AnthropicClaude:
    """The real Claude API, through the anthropic SDK."""

    def __init__(self, api_key: str):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def create_batch(self, requests: list[dict]) -> str:
        batch = await self.client.messages.batches.create(requests=requests)
        return batch.id

    async def batch_status(self, batch_id: str) -> str:
        return (await self.client.messages.batches.retrieve(batch_id)).processing_status

    async def batch_results(self, batch_id: str) -> list[tuple[str, str, Any]]:
        out = []
        async for entry in await self.client.messages.batches.results(batch_id):
            message = entry.result.message if entry.result.type == "succeeded" else None
            out.append((entry.custom_id, entry.result.type, message))
        return out

    async def create_with_fallback(self, params: dict) -> Any:
        return await self.client.beta.messages.create(
            **params, betas=[FALLBACK_BETA], fallbacks="default"
        )


# --- what needs explaining ----------------------------------------------------

async def _pending(db) -> list[tuple[str, str, str]]:
    """Accepted (position, move, san) with no explanation, owner text or marker yet."""
    has_any = exists().where(and_(
        OpeningExplanation.position_key == RepertoireMove.position_key,
        OpeningExplanation.move_uci == RepertoireMove.move_uci,
    ))
    rows = (await db.execute(
        select(RepertoireMove.position_key, RepertoireMove.move_uci, RepertoireMove.move_san)
        .where(~has_any)
        .distinct()
    )).all()
    return [tuple(r) for r in rows]


async def _gather_inputs(db, items, engine) -> dict[tuple[str, str], ExplanationInput]:
    """Build ExplanationInputs for (position, move) items, from the first
    user who has each in their repertoire."""
    theory = {(k, u): line for k, u, line in (await db.execute(
        select(TheoryMove.position_key, TheoryMove.move_uci, TheoryMove.line_name))).all()}
    names = {k: (eco, name) for k, eco, name in (await db.execute(
        select(OpeningName.position_key, OpeningName.eco, OpeningName.name))).all()}

    per_user: dict[tuple[int, str], tuple[dict, dict, dict]] = {}
    out: dict[tuple[str, str], ExplanationInput] = {}
    limit = _analysis_limit()

    for key, uci, san in items:
        owner = (await db.execute(
            select(RepertoireMove.user_id, RepertoireMove.color)
            .where(RepertoireMove.position_key == key, RepertoireMove.move_uci == uci)
            .order_by(RepertoireMove.id).limit(1)
        )).first()
        if owner is None:
            continue
        user_id, color = owner
        if (user_id, color) not in per_user:
            moves = dict((await db.execute(
                select(RepertoireMove.position_key, RepertoireMove.move_uci)
                .where(RepertoireMove.user_id == user_id, RepertoireMove.color == color)
            )).all())
            games = [
                TreeGame(user_color=color, result=result, positions=json.loads(positions))
                for result, positions in (await db.execute(
                    select(AnalyzedGame.result, AnalyzedGame.positions).where(
                        AnalyzedGame.user_id == user_id, AnalyzedGame.user_color == color,
                        AnalyzedGame.positions.is_not(None),
                    ))).all()
            ]
            per_user[(user_id, color)] = (moves, reachable(color, moves), build_tree(games, color))
        moves, parents, tree = per_user[(user_id, color)]

        line = line_to(key, moves, parents)
        if line is None:
            continue  # no longer reached; it'll be pruned
        board = chess.Board()
        line_san = []
        opening = names.get(board.epd())
        for move_uci in line:
            move = chess.Move.from_uci(move_uci)
            line_san.append(board.san(move))
            board.push(move)
            opening = names.get(board.epd(), opening)

        move = chess.Move.from_uci(uci)
        item = ExplanationInput(
            position_key=key, move_uci=uci, move_san=san, color=color, line_san=line_san,
            fen=board.fen(), opening=opening, theory_line=theory.get((key, uci)),
        )
        user_side = chess.WHITE if color == "white" else chess.BLACK
        if engine is not None:
            info = await engine.analyse(board, limit)
            item.eval_before_cp = info["score"].pov(user_side).score(mate_score=MATE_SCORE)
            pv = list(info.get("pv") or [])[:PV_PLIES]
            if pv:
                item.best_san = board.san(pv[0])
                pv_board = board.copy()
                item.pv_san = []
                for pv_move in pv:
                    item.pv_san.append(pv_board.san(pv_move))
                    pv_board.push(pv_move)
        board.push(move)
        item.opening = names.get(board.epd(), opening)
        if engine is not None and not board.is_game_over():
            info = await engine.analyse(board, limit)
            item.eval_after_cp = info["score"].pov(user_side).score(mate_score=MATE_SCORE)
        child = tree.get(board.epd())
        if child is not None and child.moves:
            replies = sorted(child.moves.values(), key=lambda e: (-e.count, e.san))[:REPLIES_SHOWN]
            item.opponent_replies = [(e.san, e.count) for e in replies]
        out[(key, uci)] = item
    return out


# --- one cycle ----------------------------------------------------------------

def _store(db, key: str, uci: str, source: str, body: dict, model: str | None) -> None:
    db.add(OpeningExplanation(
        position_key=key, move_uci=uci, source=source, body=json.dumps(body),
        model=model, prompt_version=PROMPT_VERSION if source == "claude" else None,
    ))


async def run_explanation_cycle(claude: ClaudeClient | None, engine=None) -> dict:
    """
    Collect ended batches, then submit a new one if none is open and
    something needs explaining. `engine` is a Stockfish engine for the
    grounding evaluations; when None, one is opened for the cycle if needed.
    """
    if claude is None:
        return {"skipped": "no API key"}
    summary = {"stored": 0, "retried": 0, "submitted": 0}
    own_engine = None

    async def get_engine():
        nonlocal own_engine
        if engine is not None:
            return engine
        if own_engine is None:
            own_engine = await _open_engine()
        return own_engine

    try:
        async with async_session() as db:
            open_batches = (await db.scalars(
                select(ExplanationBatch).where(ExplanationBatch.status != "ended")
            )).all()
            still_open = False
            for batch in open_batches:
                status = await claude.batch_status(batch.batch_id)
                if status != "ended":
                    batch.status = status
                    still_open = True
                    continue
                pending = {custom_id_for(k, u): (k, u, s) for k, u, s in await _pending(db)}
                retry: list[tuple[str, str, str]] = []
                for custom_id, result_type, message in await claude.batch_results(batch.batch_id):
                    item = pending.pop(custom_id, None)
                    if item is None or result_type != "succeeded":
                        continue  # explained meanwhile, or errored/expired: asked again later
                    parsed = parse_explanation(message)
                    if parsed is None:
                        retry.append(item)
                    else:
                        _store(db, item[0], item[1], "claude", parsed, batch.model)
                        summary["stored"] += 1
                if retry:
                    inputs = await _gather_inputs(db, retry, await get_engine())
                    for key, uci, _ in retry:
                        if (key, uci) not in inputs:
                            continue
                        summary["retried"] += 1
                        message = await claude.create_with_fallback(
                            build_params(inputs[(key, uci)], batch.model)
                        )
                        parsed = parse_explanation(message)
                        if parsed is not None:
                            _store(db, key, uci, "claude", parsed, batch.model)
                            summary["stored"] += 1
                        else:
                            # Marker: don't ask (and pay) again; the owner can still write one.
                            logger.warning(f"Explanation refused or unusable twice: {key} {uci}")
                            _store(db, key, uci, "refused", {}, batch.model)
                batch.status = "ended"
                batch.ended_at = datetime.now(UTC).replace(tzinfo=None)
            await db.commit()

            if still_open:
                return summary
            pending_items = await _pending(db)
            if not pending_items:
                return summary
            inputs = await _gather_inputs(db, pending_items, await get_engine())
            if not inputs:
                return summary
            model = settings.EXPLANATION_MODEL
            requests = [
                {"custom_id": custom_id_for(k, u), "params": build_params(item, model)}
                for (k, u), item in inputs.items()
            ]
            batch_id = await claude.create_batch(requests)
            db.add(ExplanationBatch(batch_id=batch_id, model=model, status="in_progress"))
            await db.commit()
            summary["submitted"] = len(requests)
            logger.info(f"Explanation batch {batch_id}: {len(requests)} positions ({model})")
            return summary
    finally:
        if own_engine is not None:
            await own_engine.quit()


async def explanation_worker(kick: asyncio.Event) -> None:
    """
    Run a cycle every EXPLANATION_POLL_SECONDS, and soon after `kick` is set
    (positions accepted). Does nothing without an API key.
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.info("No ANTHROPIC_API_KEY: opening explanations are off")
        return
    claude = AnthropicClaude(settings.ANTHROPIC_API_KEY)
    while True:
        try:
            summary = await run_explanation_cycle(claude)
            if any(summary.values()):
                logger.info(f"Explanation cycle: {summary}")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Explanation cycle failed; trying again next time")
        try:
            await asyncio.wait_for(kick.wait(), timeout=settings.EXPLANATION_POLL_SECONDS)
            await asyncio.sleep(30)  # let a run of acceptances land in one batch
        except TimeoutError:
            pass
        kick.clear()
