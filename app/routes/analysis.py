"""
Blunder-analysis routes: start a background job, poll its progress, list
detected blunders.
"""
import asyncio
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
)
from ..services.analysis_service import (
    AnalysisJob,
    already_analyzed_ids,
    best_reply_to_move,
    run_analysis_job,
)
from ..services.game_service import GameService
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
        win_prob_drop=b.win_prob_drop,
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

    # Fetch games synchronously (fast) so we can report a real total;
    # engine analysis (slow) happens in the background task.
    games = await game_service.fetch_user_games(GameRequest(
        username=request.lichess_username,
        start_date=request.start_date,
        end_date=request.end_date,
        max_games=request.max_games,
        game_type=request.game_type,
    ))

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


@router.get("/status", response_model=AnalysisStatusResponse)
async def analysis_status(
    http_request: Request,
    user: User = Depends(get_current_user),
) -> AnalysisStatusResponse:
    """Progress of this user's most recent analysis job."""
    job: AnalysisJob | None = http_request.app.state.analysis_jobs.get(user.id)
    if job is None:
        return AnalysisStatusResponse(state="idle")
    return AnalysisStatusResponse(
        state=job.state,
        games_total=job.games_total,
        games_done=job.games_done,
        blunders_found=job.blunders_found,
        error=job.error,
    )


@router.post("/sync", response_model=AnalysisStatusResponse, status_code=status.HTTP_202_ACCEPTED)
async def sync_games(
    http_request: Request,
    force: bool = Query(False, description="Sync even if the last sync was recent"),
    user: User = Depends(get_current_user),
    game_service: GameService = Depends(get_game_service),
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
    if existing is not None and existing.state == "running":
        # Sync is idempotent from the client's perspective: an in-flight job
        # IS the sync, so report it instead of erroring
        return AnalysisStatusResponse(
            state=existing.state,
            games_total=existing.games_total,
            games_done=existing.games_done,
            blunders_found=existing.blunders_found,
        )

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

    try:
        games = await game_service.lichess_client.get_user_games(
            username=user.lichess_username,
            since=since,
            until=until,
            max_games=100,
            game_types=game_types,
        )
    except NoGamesFoundError:
        # Nothing played in the window — that's a successful, empty sync
        job = AnalysisJob(state="done")
        jobs[user.id] = job
        await _mark_synced(user.id)
        return AnalysisStatusResponse(state="done", last_synced_at=now.isoformat())

    done_ids = await already_analyzed_ids(user.id, [g.id for g in games])
    to_analyze = [g for g in games if g.id not in done_ids]

    job = AnalysisJob(games_total=len(to_analyze))
    if not to_analyze:
        job.state = "done"
        await _mark_synced(user.id)
    else:
        async def _run_and_mark(user_id: int, username: str) -> None:
            await run_analysis_job(job, user_id, username, to_analyze)
            if job.state == "done":
                await _mark_synced(user_id)

        job.task = asyncio.create_task(_run_and_mark(user.id, user.lichess_username))
    jobs[user.id] = job

    logger.info(
        f"Sync started for user {user.id}: {len(to_analyze)} new games "
        f"({len(done_ids)} already analyzed) over last {user.sync_days_back}d {game_types}"
    )
    return AnalysisStatusResponse(
        state=job.state,
        games_total=job.games_total,
        games_done=job.games_done,
        blunders_found=job.blunders_found,
    )


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
