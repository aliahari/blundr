"""
Blunder-analysis routes: start a background job, poll its progress, list
detected blunders.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_db
from ..models.db_models import Blunder, User
from ..models.schemas import (
    AnalysisStartRequest,
    AnalysisStatusResponse,
    BestReplyRequest,
    BestReplyResponse,
    BlunderResponse,
    GameRequest,
    PvPlyResponse,
)
from ..services.analysis_service import (
    AnalysisJob,
    already_analyzed_ids,
    best_reply_to_move,
    run_analysis_job,
    win_prob,
)
from ..services.backfill_service import backfill_moves
from ..services.game_service import GameService
from ..services.lichess_auth import (
    forget_lichess_token,
    refresh_lichess_ratings,
    user_lichess_token,
)
from ..utils.exceptions import LichessAuthError
from .deps import get_current_user
from .games import get_game_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["analysis"])


async def _mark_synced(user_id: int) -> None:
    """Record a successful sync completion on the user row."""
    from datetime import datetime, timezone
    from ..db import async_session

    async with async_session() as db:
        u = await db.get(User, user_id)
        if u is not None:
            u.last_synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()


def blunder_to_response(b: Blunder) -> BlunderResponse:
    """Map a Blunder ORM row (with its game loaded) to the API shape."""
    game = b.game
    opponent = game.black_name if game.user_color == "white" else game.white_name
    def as_plies(raw: str | None) -> list[PvPlyResponse]:
        return [
            PvPlyResponse(
                move_uci=p["move_uci"],
                move_san=p["move_san"],
                win_prob=round(win_prob(p["eval_cp"]), 1),
            )
            for p in (json.loads(raw) if raw else [])
        ]
    return BlunderResponse(
        id=b.id,
        game_lichess_id=game.lichess_game_id,
        played_at=game.played_at.isoformat(),
        user_color=game.user_color,
        opponent=opponent,
        ply=b.ply,
        fen_before=b.fen_before,
        move_played_san=b.move_played_san,
        move_played_uci=b.move_played_uci,
        best_move_san=b.best_move_san,
        best_move_uci=b.best_move_uci,
        refutation_san=b.refutation_san,
        refutation_uci=b.refutation_uci,
        eval_before_cp=b.eval_before_cp,
        eval_after_cp=b.eval_after_cp,
        win_prob_before=round(win_prob(b.eval_before_cp), 1),
        win_prob_after=round(win_prob(b.eval_after_cp), 1),
        win_prob_drop=b.win_prob_drop,
        refutation_line=as_plies(b.refutation_line),
        best_line=as_plies(b.best_line),
    )


@router.post("/start", response_model=AnalysisStatusResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_analysis(
    request: AnalysisStartRequest,
    http_request: Request,
    user: User = Depends(get_current_user),
    game_service: GameService = Depends(get_game_service),
) -> AnalysisStatusResponse:
    """
    Fetch the requested games from Lichess and analyze them in the background.

    Games already analyzed for this account are skipped. One job per user at
    a time.
    """
    jobs: dict = http_request.app.state.analysis_jobs
    existing = jobs.get(user.id)
    if existing is not None and existing.state == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An analysis job is already running for this account",
        )

    # Claim this user's job slot before the first await. Otherwise a second
    # request arriving while this one is still mid-fetch (e.g. a double
    # click) sees no "running" job yet — since jobs[user.id] wasn't set
    # until after fetch_user_games returned — and races a second concurrent
    # call to Lichess, which itself only allows one in-flight request per
    # caller and 429s the loser.
    jobs[user.id] = AnalysisJob(state="running")

    # Fetch games synchronously (fast) so we can report a real total;
    # engine analysis (slow) happens in the background task.
    try:
        games = await game_service.fetch_user_games(GameRequest(
            username=request.lichess_username,
            start_date=request.start_date,
            end_date=request.end_date,
            max_games=request.max_games,
            game_type=request.game_type,
        ))
    except Exception:
        jobs.pop(user.id, None)  # release the slot so a retry isn't stuck
        raise

    done_ids = await already_analyzed_ids(user.id, [g.id for g in games])
    to_analyze = [g for g in games if g.id not in done_ids]

    job = AnalysisJob(games_total=len(to_analyze))
    if not to_analyze:
        job.state = "done"
    else:
        job.task = asyncio.create_task(
            run_analysis_job(job, user.id, request.lichess_username, to_analyze)
        )
    jobs[user.id] = job

    logger.info(
        f"Analysis started for user {user.id}: {len(to_analyze)} new games "
        f"({len(done_ids)} already analyzed)"
    )
    return AnalysisStatusResponse(
        state=job.state,
        games_total=job.games_total,
        games_done=job.games_done,
        blunders_found=job.blunders_found,
    )


def _status(job: AnalysisJob) -> AnalysisStatusResponse:
    return AnalysisStatusResponse(
        state=job.state,
        games_total=job.games_total,
        games_done=job.games_done,
        blunders_found=job.blunders_found,
        error=job.error,
        backfill_total=job.backfill_total,
        backfill_done=job.backfill_done,
    )


def _sync_in_progress(job: AnalysisJob | None) -> bool:
    """
    A sync is in progress while its analysis runs, and also while the move
    backfill at the tail of the same task runs (job.state is already "done"
    then). Either way it may still be talking to Lichess, which allows one
    request at a time per caller.
    """
    if job is None:
        return False
    return job.state == "running" or (job.task is not None and not job.task.done())


class _with_token:
    """The Lichess client as the move backfill sees it, carrying the user's
    token on its bulk exports; on a rejected token it goes on without one."""

    def __init__(self, lichess, token: str | None):
        self._lichess, self._token = lichess, token
        self.EXPORT_BATCH_SIZE = lichess.EXPORT_BATCH_SIZE

    async def export_games_by_ids(self, game_ids: list[str]):
        if self._token:
            try:
                return await self._lichess.export_games_by_ids(game_ids, token=self._token)
            except LichessAuthError:
                self._token = None
        return await self._lichess.export_games_by_ids(game_ids)


def _start_sync_task(
    job: AnalysisJob,
    user_id: int,
    username: str,
    to_analyze: list,
    lichess,
) -> None:
    """
    The sync's background work: analyze the new games (if any), then give
    games analyzed before Phase 1 their moves (backfill_service). The
    backfill never fails the sync; it's retried on the next one.
    """
    async def _run() -> None:
        if to_analyze:
            await run_analysis_job(job, user_id, username, to_analyze)
            if job.state == "done":
                await _mark_synced(user_id)
        if job.state != "error":
            try:
                await backfill_moves(job, user_id, lichess)
            except Exception:
                logger.exception(f"Move backfill failed for user {user_id}; will retry next sync")

    job.task = asyncio.create_task(_run())


@router.get("/status", response_model=AnalysisStatusResponse)
async def analysis_status(
    http_request: Request,
    user: User = Depends(get_current_user),
) -> AnalysisStatusResponse:
    """Progress of this user's most recent analysis job."""
    job: AnalysisJob | None = http_request.app.state.analysis_jobs.get(user.id)
    if job is None:
        return AnalysisStatusResponse(state="idle")
    return _status(job)


@router.post("/sync", response_model=AnalysisStatusResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_games(
    http_request: Request,
    force: bool = Query(False, description="Sync even if the last sync was recent"),
    user: User = Depends(get_current_user),
    game_service: GameService = Depends(get_game_service),
    db: AsyncSession = Depends(get_db),
) -> AnalysisStatusResponse:
    """
    Sync from the user's saved preferences: fetch games for their Lichess
    account over the configured lookback window and analyze whatever is new.

    Non-forced syncs (the app's automatic on-load call) are skipped when the
    last successful sync is fresher than 24 hours; force=true (the Sync now
    button) always runs.
    """
    import json as _json
    from datetime import date as _date, datetime as _datetime, timedelta as _timedelta, timezone as _timezone

    if not user.lichess_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set your Lichess username in Settings before syncing",
        )

    jobs: dict = http_request.app.state.analysis_jobs
    existing = jobs.get(user.id)
    if _sync_in_progress(existing):
        # Sync is idempotent from the client's perspective: an in-flight job
        # IS the sync, so report it instead of erroring
        return _status(existing)

    now = _datetime.now(_timezone.utc).replace(tzinfo=None)
    if not force and user.last_synced_at is not None:
        if now - user.last_synced_at < _timedelta(hours=24):
            return AnalysisStatusResponse(
                state="skipped",
                last_synced_at=user.last_synced_at.isoformat(),
            )

    game_types = _json.loads(user.sync_game_types)
    until = _date.today()
    since = until - _timedelta(days=user.sync_days_back)

    from ..utils.exceptions import NoGamesFoundError

    # Claim this user's job slot before the first await. Otherwise a second
    # request arriving while this one is still mid-fetch (e.g. the frontend's
    # auto-sync-on-load racing a manual "Sync now" click) sees no "running"
    # job yet — jobs[user.id] wasn't set until after get_user_games returned
    # — and races a second concurrent call to Lichess, which only allows one
    # in-flight request per caller and 429s the loser.
    jobs[user.id] = AnalysisJob(state="running")

    # The user's own token: Lichess sends a user their own games faster.
    # A token Lichess rejects is forgotten (the app then asks to sign in
    # again) and the sync carries on without it, since games are public.
    token = user_lichess_token(user)
    lichess = game_service.lichess_client

    async def fetch(with_token: str | None):
        return await lichess.get_user_games(
            username=user.lichess_username,
            since=since,
            until=until,
            max_games=100,
            game_types=game_types,
            token=with_token,
        )

    if token:
        # The builder's rating band follows the user's current ratings
        await refresh_lichess_ratings(db, http_request.app.state.oauth_http, user, token)

    try:
        try:
            games = await fetch(token)
        except LichessAuthError:
            if token is None:
                raise
            logger.warning(f"Lichess rejected user {user.id}'s token; syncing without it")
            await forget_lichess_token(db, user)
            token = None
            games = await fetch(None)
    except NoGamesFoundError:
        # Nothing played in the window — that's a successful, empty sync.
        # Older games may still need their moves.
        job = AnalysisJob(state="done")
        jobs[user.id] = job
        await _mark_synced(user.id)
        _start_sync_task(job, user.id, user.lichess_username, [], _with_token(lichess, token))
        return AnalysisStatusResponse(state="done", last_synced_at=now.isoformat())
    except Exception:
        jobs.pop(user.id, None)  # release the slot so a retry isn't stuck
        raise

    done_ids = await already_analyzed_ids(user.id, [g.id for g in games])
    to_analyze = [g for g in games if g.id not in done_ids]

    job = AnalysisJob(games_total=len(to_analyze))
    if not to_analyze:
        job.state = "done"
        await _mark_synced(user.id)
    _start_sync_task(job, user.id, user.lichess_username, to_analyze, _with_token(lichess, token))
    jobs[user.id] = job

    logger.info(
        f"Sync started for user {user.id}: {len(to_analyze)} new games "
        f"({len(done_ids)} already analyzed) over last {user.sync_days_back}d {game_types}"
    )
    return _status(job)


@router.post("/best-reply", response_model=BestReplyResponse)
async def best_reply(
    request: BestReplyRequest,
    user: User = Depends(get_current_user),
) -> BestReplyResponse:
    """
    Engine's best reply to a candidate move — used by the review UI to show
    how the opponent would punish a wrong attempt.
    """
    try:
        result = await best_reply_to_move(request.fen, request.move_uci)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return BestReplyResponse(**result)


@router.get("/blunders", response_model=list[BlunderResponse])
async def list_blunders(
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[BlunderResponse]:
    """All detected blunders for this user, newest games first."""
    rows = await db.scalars(
        select(Blunder)
        .where(Blunder.user_id == user.id)
        .options(selectinload(Blunder.game))
        .order_by(Blunder.id.desc())
        .limit(limit)
    )
    return [blunder_to_response(b) for b in rows.all()]
