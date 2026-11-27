"""
What players at the user's level reply, from the Lichess opening explorer
(specs/features/02-openings/repertoire-builder.md).

Each position is fetched once per rating band with the user's own Lichess
token and kept for 30 days in `explorer_cache`. Requests go out one at a time
per user; a 429 stops that user's requests for 60 s. Anything that goes wrong
returns None, never raises, and is never cached: callers fall back to master
games.
"""
import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import chess
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.db_models import ExplorerCache, User

logger = logging.getLogger(__name__)

EXPLORER_URL = "https://explorer.lichess.ovh/lichess"
EXPLORER_SPEEDS = ("ultraBullet", "bullet", "blitz", "rapid", "classical", "correspondence")
DEFAULT_SPEEDS = ("blitz", "rapid")
RATING_BUCKETS = (0, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500)
DEFAULT_RATING = 1500
CACHE_DAYS = 30
BACKOFF_SECONDS = 60
TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ExplorerMove:
    uci: str
    san: str
    games: int
    white: int
    draws: int
    black: int


_locks: dict[int, asyncio.Lock] = {}
_backoff_until: dict[int, datetime] = {}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _speeds(user: User) -> list[str]:
    try:
        chosen = json.loads(user.sync_game_types or "[]")
    except ValueError:
        chosen = []
    speeds = sorted({s for s in chosen if s in EXPLORER_SPEEDS})
    return speeds or list(DEFAULT_SPEEDS)


def band_for(user: User) -> str:
    """The cache key and request parameters: speeds, and the user's rating
    bucket plus the one above, e.g. "blitz,rapid:1600,1800"."""
    speeds = _speeds(user)
    try:
        ratings = json.loads(user.lichess_ratings or "{}")
    except ValueError:
        ratings = {}
    mine = [r for s, r in ratings.items() if s in speeds and isinstance(r, int)]
    rating = max(mine) if mine else DEFAULT_RATING
    i = max(i for i, b in enumerate(RATING_BUCKETS) if b <= rating)
    buckets = RATING_BUCKETS[i:i + 2]
    return f"{','.join(speeds)}:{','.join(str(b) for b in buckets)}"


def shares(moves: list[ExplorerMove]) -> list[tuple[str, int]]:
    """(uci, games) pairs, most games first."""
    return sorted(((m.uci, m.games) for m in moves), key=lambda p: -p[1])


def _parse(key: str, body: dict) -> list[ExplorerMove]:
    board = chess.Board(key + " 0 1")
    moves = []
    for m in body.get("moves", []):
        try:
            # Lichess writes castling king-takes-rook (e1h1); parse_uci
            # turns it into the standard e1g1
            move = board.parse_uci(m["uci"])
        except (KeyError, ValueError):
            continue
        games = int(m.get("white", 0)) + int(m.get("draws", 0)) + int(m.get("black", 0))
        moves.append(ExplorerMove(
            uci=move.uci(), san=board.san(move), games=games,
            white=int(m.get("white", 0)), draws=int(m.get("draws", 0)), black=int(m.get("black", 0)),
        ))
    return sorted(moves, key=lambda m: -m.games)


def _from_cache(row: ExplorerCache) -> list[ExplorerMove]:
    return [ExplorerMove(**m) for m in json.loads(row.body)]


async def explorer_moves(
    db: AsyncSession,
    http: httpx.AsyncClient,
    *,
    user_id: int,
    token: str | None,
    key: str,
    band: str,
) -> list[ExplorerMove] | None:
    """The moves players in `band` make in the position `key`, or None when
    they can't be had (no token, backing off, an error)."""
    row = await db.get(ExplorerCache, (key, band))
    if row is not None and row.fetched_at > _now() - timedelta(days=CACHE_DAYS):
        return _from_cache(row)
    if not token:
        return None
    lock = _locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        if _backoff_until.get(user_id, datetime.min) > _now():
            return None
        speeds, ratings = band.split(":", 1)
        try:
            resp = await http.get(
                EXPLORER_URL,
                params={"fen": f"{key} 0 1", "speeds": speeds, "ratings": ratings},
                headers={"Authorization": f"Bearer {token}"},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            logger.warning("Explorer request failed: %s", type(exc).__name__)
            return None
        if resp.status_code == 429:
            _backoff_until[user_id] = _now() + timedelta(seconds=BACKOFF_SECONDS)
            return None
        if resp.status_code != 200:
            logger.warning("Explorer answered %s", resp.status_code)
            return None
        try:
            moves = _parse(key, resp.json())
        except ValueError:
            return None
    body = json.dumps([asdict(m) for m in moves])
    if row is None:
        db.add(ExplorerCache(position_key=key, rating_band=band, body=body, fetched_at=_now()))
    else:
        row.body, row.fetched_at = body, _now()
    await db.commit()
    return moves
