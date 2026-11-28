"""
The step-by-step builder's I/O around the pure walk
(specs/features/02-openings/repertoire-builder.md): opponents' reply shares
from the Lichess explorer, at the user's level, within a time budget, with
master games standing in wherever the explorer can't answer in time.
"""
import asyncio
from collections.abc import Mapping

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.db_models import User
from . import explorer_service
from .explorer_service import ExplorerMove, band_for, shares
from .lichess_auth import user_lichess_token
from .masters_service import MastersBook
from .repertoire_builder import Gap, Walk, walk

__all__ = ["Gap", "Walk", "level_moves", "next_gaps"]

MAX_ROUNDS = 40  # each round fills every position the last walk was missing


async def _cached_or_fetch(
    db: AsyncSession, http: httpx.AsyncClient, user: User, token: str | None,
    key: str, band: str, remaining: float,
) -> list[ExplorerMove] | None:
    """The explorer's moves for `key`: a fetch while there's time left,
    otherwise only what's cached."""
    if remaining <= 0:
        token = None  # explorer_moves then answers from the cache or not at all
    return await explorer_service.explorer_moves(
        db, http, user_id=user.id, token=token, key=key, band=band,
        timeout=remaining if token else None)


async def level_moves(
    db: AsyncSession, http: httpx.AsyncClient, user: User, key: str, *, budget_s: float = 3.0,
) -> list[ExplorerMove] | None:
    """What players at the user's level play in `key` (for the choices' shares)."""
    return await _cached_or_fetch(db, http, user, user_lichess_token(user), key,
                                  band_for(user), budget_s)


async def next_gaps(
    db: AsyncSession,
    *,
    http: httpx.AsyncClient,
    masters: MastersBook | None,
    user: User,
    color: str,
    depth: int,
    repertoire: Mapping[str, str],
    my_games: Mapping[str, int],
    budget_s: float = 3.0,
) -> tuple[Walk, str]:
    """Walk the repertoire, fetching the reply data the walk needs. Returns
    the walk and where its numbers came from ("lichess", or "masters" when any
    position fell back to master games)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_s
    token = user_lichess_token(user)
    band = band_for(user)
    replies: dict[str, list[tuple[str, int]]] = {}
    from_masters = False
    result = walk(color, repertoire, replies, depth, my_games=my_games)
    for _ in range(MAX_ROUNDS):
        if not result.missing:
            break
        fallback = []
        for key in sorted(result.missing):
            moves = await _cached_or_fetch(db, http, user, token, key, band,
                                           deadline - loop.time())
            if moves is None:
                fallback.append(key)
            else:
                replies[key] = shares(moves)
        if fallback:
            from_masters = True
            found = await asyncio.to_thread(masters.moves, fallback) if masters else {}
            for key in fallback:
                replies[key] = [(m.uci, m.games) for m in found.get(key, [])]
        result = walk(color, repertoire, replies, depth, my_games=my_games)
    result.replies = replies
    return result, "masters" if from_masters else "lichess"
