"""
Opening explanation routes (specs/features/02-openings, design D4; R4.3, R4.4).

Players read the explanation for a (position, move): the owner's own text if
there is one, else Claude's. The owner writes or corrects explanations; any
player can report one as wrong, once, and the owner sees the reported list.
"""
import json
import logging
from datetime import UTC, datetime

import chess
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models.db_models import ExplanationReport, OpeningExplanation, User
from ..models.schemas import (
    ExplanationResponse,
    OwnerExplanationRequest,
    ReportedExplanation,
    ReportRequest,
    ReportResponse,
)
from ..services.explanation_service import preferred_explanation
from .deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/openings", tags=["openings"])


def require_owner(user: User = Depends(get_current_user)) -> User:
    if not user.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can do that")
    return user


def _response(row: OpeningExplanation) -> ExplanationResponse:
    body = json.loads(row.body)
    return ExplanationResponse(
        id=row.id, position_key=row.position_key, move_uci=row.move_uci, source=row.source,
        plan=body.get("plan", ""), why_this_move=body.get("why_this_move", ""),
        pawn_structure=body.get("pawn_structure") or None, model=row.model,
    )


def _check_move(position_key: str, move_uci: str) -> None:
    board = chess.Board()
    try:
        board.set_epd(position_key)
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not a position and move") from None
    if not board.is_valid() or move not in board.legal_moves:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That move isn't legal in that position")


@router.get("/explanations", response_model=ExplanationResponse)
async def get_explanation(
    position_key: str = Query(..., description="The position, as board.epd()"),
    move_uci: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExplanationResponse:
    """The explanation players see for a move: the owner's text first, then Claude's."""
    row = await preferred_explanation(db, position_key, move_uci)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No explanation for that move yet")
    return _response(row)


@router.put("/explanations", response_model=ExplanationResponse)
async def write_explanation(
    body: OwnerExplanationRequest,
    owner: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> ExplanationResponse:
    """Write or correct the explanation for a move. It replaces Claude's for every player."""
    _check_move(body.position_key, body.move_uci)
    text = {
        "plan": body.plan.strip(),
        "why_this_move": body.why_this_move.strip(),
        "pawn_structure": body.pawn_structure.strip() or None,
    }
    row = await db.scalar(select(OpeningExplanation).where(
        OpeningExplanation.position_key == body.position_key,
        OpeningExplanation.move_uci == body.move_uci,
        OpeningExplanation.source == "owner",
    ))
    if row is None:
        row = OpeningExplanation(position_key=body.position_key, move_uci=body.move_uci,
                                 source="owner", body=json.dumps(text))
        db.add(row)
    else:
        row.body = json.dumps(text)
        row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()
    await db.refresh(row)
    logger.info(f"Owner {owner.id} wrote the explanation for {body.position_key} {body.move_uci}")
    return _response(row)


@router.post("/explanations/{explanation_id}/report", response_model=ReportResponse)
async def report_explanation(
    explanation_id: int,
    body: ReportRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    """Report an explanation as wrong. Each player counts once; a repeat keeps the first note."""
    row = await db.get(OpeningExplanation, explanation_id)
    if row is None or row.source == "refused":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such explanation")
    existing = await db.scalar(select(ExplanationReport).where(
        ExplanationReport.explanation_id == explanation_id, ExplanationReport.user_id == user.id,
    ))
    if existing is None:
        note = (body.note or "").strip() or None
        db.add(ExplanationReport(explanation_id=explanation_id, user_id=user.id, note=note))
        await db.flush()
    row.report_count = await db.scalar(
        select(func.count()).select_from(ExplanationReport)
        .where(ExplanationReport.explanation_id == explanation_id)
    )
    await db.commit()
    return ReportResponse(report_count=row.report_count)


@router.get("/explanations/reported", response_model=list[ReportedExplanation])
async def reported_explanations(
    owner: User = Depends(require_owner),
    db: AsyncSession = Depends(get_db),
) -> list[ReportedExplanation]:
    """Explanations players reported as wrong, most reported first, with their notes."""
    rows = (await db.scalars(
        select(OpeningExplanation)
        .where(OpeningExplanation.report_count > 0)
        .order_by(OpeningExplanation.report_count.desc(), OpeningExplanation.id)
        .limit(200)
    )).all()
    notes: dict[int, list[str]] = {row.id: [] for row in rows}
    if rows:
        for explanation_id, note in (await db.execute(
            select(ExplanationReport.explanation_id, ExplanationReport.note)
            .where(ExplanationReport.explanation_id.in_(notes), ExplanationReport.note.is_not(None))
            .order_by(ExplanationReport.id)
        )).all():
            notes[explanation_id].append(note)
    return [
        ReportedExplanation(**_response(row).model_dump(), report_count=row.report_count,
                            notes=notes[row.id])
        for row in rows
    ]
