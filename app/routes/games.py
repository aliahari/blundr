"""
API routes for game-related endpoints.

This module provides endpoints for:
- Fetching user games from Lichess
- Getting game details
- Listing games with filters

Error handling: domain exceptions (UserNotFoundError, InvalidDateRangeError,
RateLimitExceededError, LichessAPIError, NoGamesFoundError) are translated to
HTTP responses by the global exception handlers registered in app/main.py, so
routes below only need to raise HTTPException for handler-local concerns
(e.g. a game not belonging to the requested user) and can otherwise let
exceptions propagate.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query, Path

from ..models.schemas import (
    GameRequest,
    GameResponse,
    GameListResponse,
    ErrorResponse,
    GameType
)
from ..services.game_service import GameService
from ..utils.date_utils import parse_date

logger = logging.getLogger(__name__)

# Create router with prefix and tags
router = APIRouter(prefix="/games", tags=["games"])


def get_game_service(request: Request) -> GameService:
    """Dependency that provides a GameService backed by the app-wide LichessClient."""
    return GameService(request.app.state.lichess_client)


@router.post(
    "/fetch",
    response_model=GameListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad Request"},
        404: {"model": ErrorResponse, "description": "Not Found"},
        429: {"model": ErrorResponse, "description": "Rate Limit Exceeded"},
        500: {"model": ErrorResponse, "description": "Internal Server Error"}
    },
    summary="Fetch user games from Lichess",
    description="Retrieve all chess games for a given user between specified dates"
)
async def fetch_user_games(
    request: GameRequest,
    game_service: GameService = Depends(get_game_service)
) -> GameListResponse:
    """
    Fetch games for a Lichess user between start_date and end_date.

    This endpoint retrieves all chess games played by a specific user on Lichess
    within the given date range. The games can be filtered by type (rapid, blitz, etc.)
    and limited by count.

    **Request Body:**
    - `username` (required): Lichess username
    - `start_date` (required): Start date in YYYY-MM-DD format
    - `end_date` (required): End date in YYYY-MM-DD format
    - `max_games` (optional): Maximum number of games to return (default: 100, max: 1000)
    - `game_type` (optional): Filter by game type (all, rapid, blitz, classical, bullet, correspondence, standard)

    **Response:**
    - List of games with metadata
    - Total count of games returned
    - Username and date range

    **Example:**
    ```
    curl -X POST "http://localhost:8000/api/games/fetch" \
         -H "Content-Type: application/json" \
         -d '{"username": "DrNykterstein", "start_date": "2024-01-01", "end_date": "2024-01-31"}'
    ```
    """
    games = await game_service.fetch_user_games(request)
    response = game_service.create_list_response(games, request)
    logger.info(f"Successfully fetched {len(games)} games for user '{request.username}'")
    return response


@router.get(
    "/{username}",
    response_model=GameListResponse,
    summary="Get user games (alternative endpoint)",
    description="Alternative GET endpoint for fetching user games"
)
async def get_user_games(
    username: str = Path(..., min_length=1, max_length=50, description="Lichess username"),
    start_date: str = Query(..., description="Start date in YYYY-MM-DD format"),
    end_date: str = Query(..., description="End date in YYYY-MM-DD format"),
    max_games: int = Query(100, ge=1, le=1000, description="Maximum number of games"),
    game_type: GameType = Query(GameType.ALL, description="Game type filter"),
    game_service: GameService = Depends(get_game_service)
) -> GameListResponse:
    """
    GET endpoint alternative for fetching user games.

    This is an alternative to the POST endpoint for clients that prefer GET requests.
    All parameters are passed as query parameters.
    """
    request = GameRequest(
        username=username,
        start_date=parse_date(start_date),
        end_date=parse_date(end_date),
        max_games=max_games,
        game_type=game_type
    )

    games = await game_service.fetch_user_games(request)
    return game_service.create_list_response(games, request)


@router.get(
    "/{username}/{game_id}",
    response_model=GameResponse,
    summary="Get a specific game by ID",
    description="Retrieve a specific game by its Lichess ID for a user"
)
async def get_game_by_id(
    username: str,
    game_id: str,
    game_service: GameService = Depends(get_game_service)
) -> GameResponse:
    """
    Get a specific game by its ID.

    This endpoint retrieves a single game by its Lichess game ID.
    The username parameter is used to verify the game belongs to the user.
    """
    game = await game_service.get_game_by_id(game_id)

    username_lower = username.lower()
    if game.white.id.lower() != username_lower and game.black.id.lower() != username_lower:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Game {game_id} not found for user {username}"
        )

    return game_service.convert_to_response(game)
