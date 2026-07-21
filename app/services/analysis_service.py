"""
Engine analysis: replay games with python-chess, evaluate every position
with Stockfish, and flag the user's moves that sharply dropped their win
probability.

Runs as a background asyncio task per user; progress is reported through an
in-memory AnalysisJob that the status endpoint reads. Blunders and their
review cards are persisted as each game finishes, so a crash mid-job keeps
completed work.
"""
import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

import chess
import chess.engine
from sqlalchemy import select

from ..config import settings
from ..db import async_session
from ..models.db_models import AnalyzedGame, Blunder, ReviewCard
from ..models.game import LichessGame

logger = logging.getLogger(__name__)

STANDARD_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
MATE_SCORE = 10000

# One search thread per engine — parallelism comes from running several
# engine processes side by side (see ANALYSIS_CONCURRENCY)
ENGINE_OPTIONS = {"Threads": 1, "Hash": 64}


def _analysis_limit() -> chess.engine.Limit:
    """Depth-limited search with a wall-clock safety cap."""
    return chess.engine.Limit(
        depth=settings.ANALYSIS_DEPTH,
        time=settings.ANALYSIS_MAX_TIME_PER_POSITION,
    )


async def _open_engine() -> chess.engine.Protocol:
    """Start a configured Stockfish process."""
    transport, engine = await chess.engine.popen_uci(settings.STOCKFISH_PATH)
    await engine.configure(ENGINE_OPTIONS)
    return engine


def win_prob(cp: int) -> float:
    """
    Convert a centipawn eval to a win probability in [0, 100].

    Same sigmoid Lichess uses for its accuracy metrics — it saturates for
    large evals, so a drop from +9 to +5 (both ~100%) isn't a "blunder"
    while a drop from +1 to -1 is.
    """
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


@dataclass
class AnalysisJob:
    """In-memory progress snapshot for one user's analysis run."""
    state: str = "running"  # running | done | error
    games_total: int = 0
    games_done: int = 0
    blunders_found: int = 0
    error: Optional[str] = None
    task: Optional[asyncio.Task] = field(default=None, repr=False)


@dataclass
class DetectedBlunder:
    """One flagged move, ready to persist."""
    ply: int
    fen_before: str
    move_played_uci: str
    move_played_san: str
    best_move_uci: str
    best_move_san: str
    eval_before_cp: int
    eval_after_cp: int
    win_prob_drop: float
    refutation_uci: Optional[str] = None  # engine's punish of the played move
    refutation_san: Optional[str] = None


async def detect_blunders(
    engine: chess.engine.Protocol,
    game: LichessGame,
    user_color: chess.Color,
) -> List[DetectedBlunder]:
    """
    Evaluate every position of a game and return the user's blunders.

    One engine.analyse call per position gives both the eval and (via the
    principal variation) the best move for positions where the user is to
    move — no second pass needed.
    """
    limit = _analysis_limit()
    board = chess.Board()

    blunders: List[DetectedBlunder] = []

    # Eval + best move of the current position, carried into the next iteration
    info = await engine.analyse(board, limit)
    prev_cp = info["score"].pov(user_color).score(mate_score=MATE_SCORE)
    prev_best = (info.get("pv") or [None])[0]

    for ply, san in enumerate(game.moves, start=1):
        fen_before = board.fen()
        user_to_move = board.turn == user_color

        try:
            move = board.push_san(san)
        except ValueError:
            # Corrupt/variant move list — stop analyzing this game
            logger.warning(f"Game {game.id}: cannot replay move {ply} ({san!r}), stopping")
            break

        game_over = board.is_game_over()
        if game_over:
            # Terminal positions can't be sent to the engine; score them
            # directly. A stalemate from a winning position is a classic
            # blunder, so don't just skip these.
            if board.is_checkmate():
                # The side that just moved delivered mate
                mated_pov = -MATE_SCORE if board.turn == user_color else MATE_SCORE
                cp = mated_pov
            else:
                cp = 0  # stalemate / insufficient material / 75-move etc.
        else:
            info = await engine.analyse(board, limit)
            cp = info["score"].pov(user_color).score(mate_score=MATE_SCORE)

        if user_to_move and prev_best is not None and move != prev_best:
            drop = win_prob(prev_cp) - win_prob(cp)
            if drop >= settings.BLUNDER_WINPROB_THRESHOLD:
                # Recompute SAN of the best move against the pre-move board
                pre_board = chess.Board(fen_before)
                # The opponent's punish: engine's best reply to the played
                # move (pv head of the position we just evaluated). None on
                # terminal positions (no reply exists).
                refutation = None if game_over else (info.get("pv") or [None])[0]
                blunders.append(DetectedBlunder(
                    ply=ply,
                    fen_before=fen_before,
                    move_played_uci=move.uci(),
                    move_played_san=san,
                    best_move_uci=prev_best.uci(),
                    best_move_san=pre_board.san(prev_best),
                    eval_before_cp=prev_cp,
                    eval_after_cp=cp,
                    win_prob_drop=round(drop, 1),
                    refutation_uci=refutation.uci() if refutation else None,
                    refutation_san=board.san(refutation) if refutation else None,
                ))

        if game_over:
            break

        prev_cp = cp
        prev_best = (info.get("pv") or [None])[0]

    return blunders


async def run_analysis_job(
    job: AnalysisJob,
    user_id: int,
    lichess_username: str,
    games: List[LichessGame],
) -> None:
    """
    Analyze a batch of games for a user, persisting results per game.

    Games are spread over ANALYSIS_CONCURRENCY parallel engine workers,
    each with its own Stockfish process. Games already analyzed for this
    user were filtered out before the job started; games where the user
    didn't play are skipped.
    """
    lichess_id = lichess_username.lower()
    queue: asyncio.Queue = asyncio.Queue()
    for game in games:
        queue.put_nowait(game)

    async def analyze_one(engine: chess.engine.Protocol, game: LichessGame) -> None:
        if game.initial_fen != STANDARD_FEN:
            logger.info(f"Skipping non-standard game {game.id}")
            return

        if game.white.id.lower() == lichess_id:
            user_color = chess.WHITE
        elif game.black.id.lower() == lichess_id:
            user_color = chess.BLACK
        else:
            logger.info(f"Skipping game {game.id}: user not a player")
            return

        found = await detect_blunders(engine, game, user_color)

        async with async_session() as db:
            analyzed = AnalyzedGame(
                user_id=user_id,
                lichess_game_id=game.id,
                lichess_username=lichess_username,
                user_color="white" if user_color == chess.WHITE else "black",
                white_name=game.white.name,
                black_name=game.black.name,
                result=game.result,
                played_at=game.created_at.replace(tzinfo=None),
            )
            db.add(analyzed)
            await db.flush()

            for b in found:
                blunder = Blunder(
                    user_id=user_id,
                    game_id=analyzed.id,
                    ply=b.ply,
                    fen_before=b.fen_before,
                    move_played_uci=b.move_played_uci,
                    move_played_san=b.move_played_san,
                    best_move_uci=b.best_move_uci,
                    best_move_san=b.best_move_san,
                    refutation_uci=b.refutation_uci,
                    refutation_san=b.refutation_san,
                    eval_before_cp=b.eval_before_cp,
                    eval_after_cp=b.eval_after_cp,
                    win_prob_drop=b.win_prob_drop,
                )
                db.add(blunder)
                await db.flush()
                db.add(ReviewCard(user_id=user_id, blunder_id=blunder.id))

            await db.commit()

        job.blunders_found += len(found)

    async def worker() -> None:
        engine = await _open_engine()
        try:
            while True:
                try:
                    game = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await analyze_one(engine, game)
                except Exception:
                    logger.exception(f"Failed to analyze game {game.id}, continuing")
                finally:
                    job.games_done += 1
        finally:
            await engine.quit()

    try:
        n_workers = max(1, min(settings.ANALYSIS_CONCURRENCY, len(games)))
        await asyncio.gather(*(worker() for _ in range(n_workers)))
        job.state = "done"
        logger.info(
            f"Analysis done for user {user_id}: {job.games_done} games, "
            f"{job.blunders_found} blunders ({n_workers} workers)"
        )
    except Exception as e:
        logger.exception(f"Analysis job failed for user {user_id}")
        job.state = "error"
        job.error = str(e)


async def best_reply_to_move(fen: str, move_uci: str) -> dict:
    """
    Evaluate a move the user is considering: push it and ask the engine for
    the opponent's best reply. Used by the review UI to show why a wrong
    attempt fails.

    Returns a dict with the attempted move's SAN, the reply (None if the
    move ends the game), and the resulting eval from the mover's perspective.

    Raises:
        ValueError: On an invalid FEN or an illegal move.
    """
    try:
        board = chess.Board(fen)
    except ValueError:
        raise ValueError("Invalid FEN")

    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        raise ValueError(f"Invalid move: {move_uci!r}")
    if move not in board.legal_moves:
        raise ValueError(f"Illegal move for this position: {move_uci!r}")

    mover = board.turn
    move_san = board.san(move)
    board.push(move)

    if board.is_game_over():
        if board.is_checkmate():
            eval_cp = MATE_SCORE  # the mover delivered mate
        else:
            eval_cp = 0  # draw
        return {"move_san": move_san, "reply_uci": None, "reply_san": None,
                "eval_after_cp": eval_cp, "game_over": True}

    engine = await _open_engine()
    try:
        # A bit deeper than batch analysis — this is a single interactive
        # request and the reply is shown as "the" punish
        info = await engine.analyse(board, chess.engine.Limit(
            depth=settings.ANALYSIS_DEPTH + 4, time=1.0))
    finally:
        await engine.quit()

    reply = (info.get("pv") or [None])[0]
    eval_cp = info["score"].pov(mover).score(mate_score=MATE_SCORE)
    return {
        "move_san": move_san,
        "reply_uci": reply.uci() if reply else None,
        "reply_san": board.san(reply) if reply else None,
        "eval_after_cp": eval_cp,
        "game_over": False,
    }


async def already_analyzed_ids(user_id: int, game_ids: List[str]) -> set:
    """Return the subset of game_ids already analyzed for this user."""
    if not game_ids:
        return set()
    async with async_session() as db:
        rows = await db.scalars(
            select(AnalyzedGame.lichess_game_id).where(
                AnalyzedGame.user_id == user_id,
                AnalyzedGame.lichess_game_id.in_(game_ids),
            )
        )
        return set(rows.all())
