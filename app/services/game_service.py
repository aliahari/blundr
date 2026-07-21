"""
Game service for fetching and managing chess games.

This service provides the business logic for:
- Fetching games from Lichess
- Filtering and sorting games
- Preparing game data for responses
"""
import logging
from datetime import date
from typing import List, Dict, Any

from ..models.game import LichessGame
from ..models.schemas import GameRequest, GameResponse, GameListResponse, PlayerInfo, GameType
from ..services.lichess_client import LichessClient
from ..utils.exceptions import InvalidDateRangeError, NoGamesFoundError
from ..utils.date_utils import validate_date_range

logger = logging.getLogger(__name__)


class GameService:
    """
    Service for managing chess games.
    
    This service abstracts the Lichess API and provides a clean interface
    for fetching and working with chess games.
    """
    
    # Mapping of our game types to Lichess API types
    GAME_TYPE_MAPPING: Dict[GameType, List[str]] = {
        GameType.ALL: None,  # None means no filter
        GameType.RAPID: ["rapid"],
        GameType.BLITZ: ["blitz"],
        GameType.CLASSICAL: ["classical"],
        GameType.BULLET: ["bullet"],
        GameType.CORRESPONDENCE: ["correspondence"],
        GameType.STANDARD: ["rapid", "classical", "blitz"]
    }
    
    def __init__(self, lichess_client: LichessClient):
        """
        Initialize the game service.

        Args:
            lichess_client: The LichessClient to use (shared instance owned
                by the app, see app.state.lichess_client)
        """
        self.lichess_client = lichess_client
    
    async def fetch_user_games(
        self,
        request: GameRequest
    ) -> List[LichessGame]:
        """
        Fetch games for a user based on the request parameters.
        
        Args:
            request: GameRequest with username, date range, etc.
            
        Returns:
            List of LichessGame objects
            
        Raises:
            InvalidDateRangeError: If start_date > end_date
            NoGamesFoundError: If no games are found
            UserNotFoundError: If the user doesn't exist
            LichessAPIError: For other API errors
        """
        # Validate date range
        validate_date_range(request.start_date, request.end_date)
        
        # Get Lichess game types
        game_types = self.GAME_TYPE_MAPPING.get(request.game_type)
        
        logger.info(
            f"Fetching games for user '{request.username}' "
            f"from {request.start_date} to {request.end_date}"
        )
        
        # Fetch games from Lichess
        games = await self.lichess_client.get_user_games(
            username=request.username,
            since=request.start_date,
            until=request.end_date,
            max_games=request.max_games or 100,
            game_types=game_types
        )
        
        logger.info(f"Found {len(games)} games for user '{request.username}'")

        return games

    async def get_game_by_id(self, game_id: str) -> LichessGame:
        """
        Fetch a single game by its Lichess ID.

        Args:
            game_id: The Lichess game ID

        Returns:
            LichessGame object

        Raises:
            GameNotFoundError: If the game doesn't exist
            LichessAPIError: For other API errors
        """
        return await self.lichess_client.get_game_by_id(game_id)

    def convert_to_response(self, game: LichessGame) -> GameResponse:
        """
        Convert a LichessGame to a GameResponse.

        Args:
            game: The LichessGame to convert

        Returns:
            GameResponse object
        """
        white_info = PlayerInfo(
            id=game.white.id,
            name=game.white.name,
            rating=game.white.rating,
            color="white",
            result=game.white.result
        )

        black_info = PlayerInfo(
            id=game.black.id,
            name=game.black.name,
            rating=game.black.rating,
            color="black",
            result=game.black.result
        )
        
        return GameResponse(
            id=game.id,
            game_date=game.created_at.date(),
            created_at=date_to_timestamp(game.created_at.date()),
            white=white_info,
            black=black_info,
            result=game.result,
            pgn=game.pgn,
            time_control=game.time_control,
            end_status=game.end_status,
            moves=game.moves,
            initial_fen=game.initial_fen
        )
    
    def create_list_response(
        self,
        games: List[LichessGame],
        request: GameRequest
    ) -> GameListResponse:
        """
        Create a GameListResponse from a list of games.
        
        Args:
            games: List of LichessGame objects
            request: The original GameRequest
            
        Returns:
            GameListResponse object
        """
        game_responses = [self.convert_to_response(game) for game in games]
        
        return GameListResponse(
            games=game_responses,
            total=len(game_responses),
            username=request.username,
            date_range={
                "start": request.start_date,
                "end": request.end_date
            }
        )
    
    def filter_games_by_result(
        self,
        games: List[LichessGame],
        username: str,
        result: str
    ) -> List[LichessGame]:
        """
        Filter games by result for a specific user.
        
        Args:
            games: List of games to filter
            username: The username to check results for
            result: The result to filter by ("win", "loss", "draw")
            
        Returns:
            Filtered list of games
        """
        return [
            game for game in games
            if game.get_user_game_result(username) == result
        ]
    
    def sort_games_by_date(
        self,
        games: List[LichessGame],
        ascending: bool = False
    ) -> List[LichessGame]:
        """
        Sort games by date.
        
        Args:
            games: List of games to sort
            ascending: If True, sort oldest first; if False, newest first
            
        Returns:
            Sorted list of games
        """
        return sorted(
            games,
            key=lambda g: g.created_at,
            reverse=not ascending
        )


# Helper function for the service
from ..utils.date_utils import date_to_timestamp
