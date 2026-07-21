"""
Async client for the Lichess API.

This module provides an async interface to the Lichess API for fetching
user games and other data.
"""
import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable, List, Optional, Dict, Any
from dataclasses import asdict

import httpx

from ..config import settings
from ..models.game import LichessGame, Player, Clock
from ..utils.exceptions import (
    LichessAPIError,
    RateLimitExceededError,
    UserNotFoundError,
    GameNotFoundError,
    NoGamesFoundError
)
from ..utils.date_utils import date_to_timestamp

logger = logging.getLogger(__name__)


class LichessClient:
    """
    Async HTTP client for the Lichess API.
    
    This client handles:
    - Fetching user games
    - Rate limiting
    - Error handling
    - Response parsing
    """
    
    BASE_URL = "https://lichess.org/api"

    def __init__(self, client: httpx.AsyncClient, rate_limit_delay: float = None):
        """
        Initialize the Lichess client.

        Args:
            client: A shared httpx.AsyncClient. Its lifecycle (creation and
                closing) is owned by the caller (see app startup/shutdown in
                main.py) so connections are pooled across requests instead of
                opened and leaked per-request.
            rate_limit_delay: Delay between requests in seconds (default: from settings)
        """
        self.client = client
        self.rate_limit_delay = rate_limit_delay or settings.LICHESS_RATE_LIMIT_DELAY
        self._last_request_time = 0

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        not_found_error: Optional[Callable[[], Exception]] = None
    ) -> httpx.Response:
        """
        Make an HTTP request with rate limiting.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (without base URL)
            params: Query parameters
            headers: Request headers
            not_found_error: Factory for the exception to raise on a 404.
                _make_request is shared by user-games and single-game
                lookups, which mean different things by "not found", so the
                caller supplies the right exception instead of this method
                guessing from params.

        Returns:
            HTTP response

        Raises:
            RateLimitExceededError: If rate limited
            LichessAPIError: For other API errors
        """
        # Rate limiting
        elapsed = asyncio.get_event_loop().time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            await asyncio.sleep(self.rate_limit_delay - elapsed)
        
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            self._last_request_time = asyncio.get_event_loop().time()
            response = await self.client.request(
                method,
                url,
                params=params,
                headers=headers or {"Accept": "application/x-ndjson"}
            )
            
            # Handle rate limiting
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                raise RateLimitExceededError(retry_after=retry_after)
            
            # Handle other errors
            if response.status_code >= 400:
                error_msg = f"Lichess API error: {response.status_code} - {response.text}"
                if response.status_code == 404 and not_found_error is not None:
                    raise not_found_error()
                raise LichessAPIError(
                    message=error_msg,
                    status_code=response.status_code,
                    response_text=response.text
                )
            
            return response
            
        except httpx.TimeoutException as e:
            raise LichessAPIError(
                message=f"Request timeout: {str(e)}",
                status_code=None
            )
        except httpx.RequestError as e:
            raise LichessAPIError(
                message=f"Request error: {str(e)}",
                status_code=None
            )
    
    async def get_user_games(
        self,
        username: str,
        since: date,
        until: date,
        max_games: int = 100,
        game_types: Optional[List[str]] = None,
        rated: Optional[bool] = None
    ) -> List[LichessGame]:
        """
        Fetch games for a user between specified dates.
        
        Lichess API endpoint: /api/games/user/{username}
        
        Args:
            username: Lichess username
            since: Start date (inclusive)
            until: End date (inclusive)
            max_games: Maximum number of games to return
            game_types: List of game types to filter by
            rated: If True, only rated games; if False, only casual; if None, both
            
        Returns:
            List of LichessGame objects
            
        Raises:
            UserNotFoundError: If the user doesn't exist
            NoGamesFoundError: If no games are found in the date range
            LichessAPIError: For other API errors
        """
        # Build query parameters. `until` must be END-inclusive: the date
        # converts to UTC midnight at the START of that day, which would drop
        # every game played on the end date itself (e.g. today's games when
        # querying up to today) — so send midnight of the following day.
        params: Dict[str, Any] = {
            "since": date_to_timestamp(since),
            "until": date_to_timestamp(until + timedelta(days=1)),
            "max": min(max_games, 100),  # Lichess max per request is 100
            "pgnInJson": "true",  # Include PGN in JSON response
            "clocks": "true",  # Include clock information
        }
        
        if game_types:
            params["type"] = ",".join(game_types)
        
        if rated is not None:
            params["rated"] = "true" if rated else "false"
        
        games = []
        page = 1
        
        while len(games) < max_games:
            # Add pagination parameter
            params["page"] = page
            
            try:
                response = await self._make_request(
                    "GET",
                    f"/games/user/{username}",
                    params=params,
                    not_found_error=lambda: UserNotFoundError(username)
                )

                # Parse NDJSON response
                lines = response.text.strip().split('\n')

                if not lines or lines == ['']:
                    # No more games
                    break

                for line in lines:
                    if line.strip():
                        game_data = json.loads(line)
                        game = self._parse_game_data(game_data)

                        # Check if game is within our date range
                        game_date = game.created_at.date()
                        if since <= game_date <= until:
                            games.append(game)

                            if len(games) >= max_games:
                                break

                # A page shorter than what we asked for ("max") means Lichess
                # has no more games to give us. Comparing against a hardcoded
                # 100 instead was wrong whenever max_games < 100, since the
                # first (and only) page would always be "short" relative to
                # 100 and pagination would stop after page 1 even if more
                # games existed.
                if len(games) >= max_games or len(lines) < params["max"]:
                    break

                page += 1
                
            except RateLimitExceededError:
                # Wait and retry
                logger.warning("Rate limit exceeded, waiting before retry...")
                await asyncio.sleep(60)  # Wait 60 seconds
                continue
        
        if not games:
            raise NoGamesFoundError(username, since, until)
        
        return games
    
    async def user_exists(self, username: str) -> Optional[bool]:
        """
        Check whether a Lichess account exists.

        Returns:
            True/False when Lichess answered, None when it couldn't be
            verified (rate limit, network trouble) — callers should treat
            None as "don't block on this".
        """
        try:
            await self._make_request(
                "GET",
                f"/user/{username}",
                headers={"Accept": "application/json"},
                not_found_error=lambda: UserNotFoundError(username),
            )
            return True
        except UserNotFoundError:
            return False
        except LichessAPIError as e:
            logger.warning(f"Could not verify Lichess user {username!r}: {e}")
            return None

    async def get_game_by_id(self, game_id: str) -> LichessGame:
        """
        Fetch a single game by its ID.
        
        Lichess API endpoint: /api/game/{gameId}
        
        Args:
            game_id: The Lichess game ID
            
        Returns:
            LichessGame object
            
        Raises:
            GameNotFoundError: If the game doesn't exist
            LichessAPIError: For other API errors
        """
        params = {
            "pgnInJson": "true",
            "clocks": "true"
        }
        
        response = await self._make_request(
            "GET",
            f"/game/{game_id}",
            params=params,
            not_found_error=lambda: GameNotFoundError(game_id)
        )

        game_data = response.json()
        return self._parse_game_data(game_data)
    
    def _parse_player_identity(self, player_data: Dict) -> tuple[str, str]:
        """
        Extract (id, name) from a Lichess player object.

        Lichess is inconsistent across endpoints: the NDJSON games-export
        endpoint nests identity under player["user"]["id"/"name"], while the
        single-game endpoint puts a flat player["userId"] with no name.
        Anonymous/AI opponents have neither.

        Args:
            player_data: Raw white/black entry from game_data["players"]

        Returns:
            (id, name) tuple, both possibly empty strings
        """
        user = player_data.get("user") or {}
        player_id = user.get("id") or player_data.get("userId", "")
        name = user.get("name") or player_id

        if not name and player_data.get("aiLevel") is not None:
            name = f"Stockfish level {player_data['aiLevel']}"

        return player_id, name

    def _parse_game_data(self, game_data: Dict) -> LichessGame:
        """
        Parse Lichess API game data into our internal model.

        Args:
            game_data: Raw game data from Lichess API

        Returns:
            LichessGame object
        """
        # Extract player information
        players = game_data.get("players", {})
        white_data = players.get("white", {})
        black_data = players.get("black", {})
        white_id, white_name = self._parse_player_identity(white_data)
        black_id, black_name = self._parse_player_identity(black_data)

        white = Player(
            id=white_id,
            name=white_name,
            rating=white_data.get("rating"),
            provisional=white_data.get("provisional", False),
            color="white"
        )

        black = Player(
            id=black_id,
            name=black_name,
            rating=black_data.get("rating"),
            provisional=black_data.get("provisional", False),
            color="black"
        )
        
        # Determine results.
        # Lichess games don't carry a "1-0"-style result field; it must be
        # derived from "winner" (absent on draws/unfinished games) and
        # "status" (distinguishes an actual draw from an aborted/unstarted game).
        status = game_data.get("status", "unknown")
        winner = game_data.get("winner")
        if winner == "white":
            result = "1-0"
        elif winner == "black":
            result = "0-1"
        elif status not in ("created", "started", "aborted", "noStart"):
            result = "1/2-1/2"
        else:
            result = "*"
        
        # Set player results
        if result == "1-0":
            white.result = "win"
            black.result = "loss"
        elif result == "0-1":
            white.result = "loss"
            black.result = "win"
        elif result == "1/2-1/2":
            white.result = "draw"
            black.result = "draw"
        else:
            white.result = None
            black.result = None
        
        # Parse clock information
        clock_data = game_data.get("clock")
        clock = None
        if clock_data:
            clock = Clock(
                initial=clock_data.get("initial", 0),
                increment=clock_data.get("increment", 0)
            )
        
        # Parse timestamps as UTC, matching date_to_timestamp's UTC encoding
        # of the since/until query params above: parsing in local time here
        # would shift created_at.date() near local midnight depending on the
        # server's timezone, causing the since <= game_date <= until filter
        # to wrongly include/exclude games at the range boundaries.
        created_at = datetime.fromtimestamp(game_data.get("createdAt", 0) / 1000, tz=timezone.utc)
        last_move_at = None
        if game_data.get("lastMoveAt"):
            last_move_at = datetime.fromtimestamp(game_data.get("lastMoveAt") / 1000, tz=timezone.utc)
        
        # Parse moves
        moves = game_data.get("moves", "").split() if game_data.get("moves") else []
        
        # Parse PGN
        pgn = game_data.get("pgn", "")
        
        return LichessGame(
            id=game_data.get("id", ""),
            created_at=created_at,
            last_move_at=last_move_at,
            white=white,
            black=black,
            initial_fen=game_data.get("initialFen", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
            pgn=pgn,
            moves=moves,
            time_control=game_data.get("timeControl", {}).get("show", ""),
            clock=clock,
            result=result,
            end_status=status,
            winner=winner,
            rated=game_data.get("rated", True),
            tournament=game_data.get("tournament"),
            match=game_data.get("match")
        )
