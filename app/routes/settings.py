"""
User profile + sync-preference routes.
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models.db_models import User
from ..models.schemas import SYNC_GAME_TYPES, SettingsResponse, SettingsUpdateRequest
from .deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


def _to_response(user: User) -> SettingsResponse:
    return SettingsResponse(
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        lichess_username=user.lichess_username,
        sync_game_types=json.loads(user.sync_game_types),
        sync_days_back=user.sync_days_back,
        max_new_per_day=user.max_new_per_day,
    )


@router.get("", response_model=SettingsResponse)
async def get_settings(user: User = Depends(get_current_user)) -> SettingsResponse:
    """Current profile and sync preferences."""
    return _to_response(user)


@router.put("", response_model=SettingsResponse)
async def update_settings(
    request: SettingsUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    """Update profile/sync preferences; only provided fields change."""
    if request.sync_game_types is not None:
        invalid = set(request.sync_game_types) - SYNC_GAME_TYPES
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown game types: {sorted(invalid)}. "
                       f"Valid: {sorted(SYNC_GAME_TYPES)}",
            )
        if not request.sync_game_types:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="At least one game type is required",
            )

    if request.email is not None:
        taken = await db.scalar(
            select(User).where(User.email == request.email, User.id != user.id)
        )
        if taken is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with that email already exists",
            )

    # Re-attach the user to this request's session before mutating
    user = await db.merge(user)
    if request.email is not None:
        user.email = request.email
    if request.display_name is not None:
        user.display_name = request.display_name or None
    if request.avatar_url is not None:
        user.avatar_url = request.avatar_url or None
    if request.lichess_username is not None:
        user.lichess_username = request.lichess_username or None
    if request.sync_game_types is not None:
        user.sync_game_types = json.dumps(request.sync_game_types)
    if request.sync_days_back is not None:
        user.sync_days_back = request.sync_days_back
    if request.max_new_per_day is not None:
        user.max_new_per_day = request.max_new_per_day

    await db.commit()
    await db.refresh(user)
    logger.info(f"Settings updated for user {user.id}")
    return _to_response(user)
