"""
Internal game data models.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict
from enum import Enum


class GameResult(str, Enum):
    """Possible game results."""
    WHITE_WIN = "1-0"
    BLACK_WIN = "0-1"
    DRAW = "1/2-1/2"
    ONGOING = "*"


class GameStatus(str, Enum):
    """How a game can end."""
    CHECKMATE = "checkmate"
    RESIGN = "resign"
    TIMEOUT = "timeout"
    DRAW = "draw"
    ABANDONED = "abandoned"
    UNKNOWN = "unknown"


@dataclass
class Player:
    """Represents a player in a chess game."""
    id: str
    name: str
    rating: Optional[int] = None
    provisional: bool = False
    color: Optional[str] = None  # "white" or "black"
    result: Optional[str] = None  # "win", "loss", "draw"


@dataclass
class Clock:
    """Time clock information."""
    initial: int  # Initial time in seconds
    increment: int  # Increment in seconds
    total_time: Optional[int] = None  # Total time available


@dataclass
class LichessGame:
    """
    Internal representation of a Lichess game.
    
    This model represents a complete chess game from Lichess with all
    relevant information for blunder analysis.
    """
    # Required fields (no defaults) must come first
    id: str
    created_at: datetime
    white: Player
    black: Player
    
    # Optional fields (with defaults) come after
    last_move_at: Optional[datetime] = None
    
    # Game details
    initial_fen: str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    pgn: str = ""
    moves: List[str] = field(default_factory=list)
    
    # Time control
    time_control: str = ""
    clock: Optional[Clock] = None
    
    # Results
    result: str = "*"
    end_status: str = "unknown"
    winner: Optional[str] = None  # "white", "black", or None for draw
    
    # Metadata
    rated: bool = True
    tournament: Optional[str] = None
    match: Optional[str] = None
    
    # Analysis fields (to be populated later)
    analysis: Optional[Dict] = None
    
    def get_player_by_color(self, color: str) -> Optional[Player]:
        """Get the player for a given color."""
        if color.lower() == "white":
            return self.white
        elif color.lower() == "black":
            return self.black
        return None
    
    def get_user_game_result(self, username: str) -> Optional[str]:
        """
        Get the result for a specific user in this game.

        Matches on the player's canonical Lichess id (always lowercase)
        rather than display name, since name comparisons that only
        lowercase one side are fragile against the many ways a username
        can differ from its own id (e.g. anonymized/legacy display names).
        """
        username_lower = username.lower()
        if self.white.id.lower() == username_lower:
            return self.white.result
        elif self.black.id.lower() == username_lower:
            return self.black.result
        return None
    
    def is_user_winner(self, username: str) -> bool:
        """Check if the user won this game."""
        result = self.get_user_game_result(username)
        return result == "win"
    
    def is_user_drawer(self, username: str) -> bool:
        """Check if the user drew this game."""
        result = self.get_user_game_result(username)
        return result == "draw"
    
    def is_user_loser(self, username: str) -> bool:
        """Check if the user lost this game."""
        result = self.get_user_game_result(username)
        return result == "loss"
