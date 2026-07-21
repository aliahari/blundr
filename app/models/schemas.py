"""
Pydantic schemas for API requests and responses.
"""
from pydantic import BaseModel, EmailStr, Field
from datetime import date
from typing import Optional, List, Dict, Any
from enum import Enum


class GameType(str, Enum):
    """Supported game types for filtering."""
    ALL = "all"
    RAPID = "rapid"
    BLITZ = "blitz"
    CLASSICAL = "classical"
    BULLET = "bullet"
    CORRESPONDENCE = "correspondence"
    STANDARD = "standard"  # Combination of rapid, classical, blitz


class GameRequest(BaseModel):
    """Request model for fetching user games."""
    username: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Lichess username",
        example="DrNykterstein"
    )
    start_date: date = Field(
        ...,
        description="Start date in YYYY-MM-DD format",
        example="2024-01-01"
    )
    end_date: date = Field(
        ...,
        description="End date in YYYY-MM-DD format",
        example="2024-01-31"
    )
    max_games: Optional[int] = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of games to return",
        example=100
    )
    game_type: Optional[GameType] = Field(
        default=GameType.ALL,
        description="Filter by game type",
        example="rapid"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "username": "DrNykterstein",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "max_games": 50,
                "game_type": "rapid"
            }
        }


class PlayerInfo(BaseModel):
    """Player information in a game."""
    id: str = Field(
        ...,
        description="Canonical Lichess user ID (lowercase, stable identifier)",
        example="drnykterstein"
    )
    name: str = Field(..., description="Player username", example="DrNykterstein")
    rating: Optional[int] = Field(
        default=None,
        description="Player rating at the time of the game",
        example=2500
    )
    color: str = Field(..., description="Player color (white or black)", example="white")
    result: Optional[str] = Field(
        default=None,
        description="Game result for this player",
        example="win"
    )


class GameResponse(BaseModel):
    """Response model for a single game."""
    id: str = Field(..., description="Lichess game ID", example="ABC12345")
    game_date: date = Field(..., description="Date the game was played", example="2024-01-15")
    created_at: int = Field(..., description="Unix timestamp in milliseconds", example=1705305600000)
    white: PlayerInfo = Field(..., description="White player information")
    black: PlayerInfo = Field(..., description="Black player information")
    result: str = Field(..., description="Game result", example="1-0")
    pgn: str = Field(..., description="PGN notation of the game")
    time_control: str = Field(..., description="Time control", example="15+10")
    end_status: str = Field(..., description="How the game ended", example="checkmate")
    moves: List[str] = Field(..., description="List of moves in SAN format")
    initial_fen: str = Field(
        default="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        description="Initial FEN position"
    )


class GameListResponse(BaseModel):
    """Response model for a list of games."""
    games: List[GameResponse] = Field(..., description="List of games")
    total: int = Field(..., description="Total number of games returned", example=25)
    username: str = Field(..., description="Username the games belong to", example="DrNykterstein")
    date_range: Dict[str, date] = Field(
        ...,
        description="Date range of the query",
        example={"start": "2024-01-01", "end": "2024-01-31"}
    )


class ErrorResponse(BaseModel):
    """Standard error response model."""
    error: str = Field(..., description="Error type", example="validation_error")
    detail: str = Field(..., description="Error details", example="start_date must be before end_date")
    status_code: int = Field(..., description="HTTP status code", example=400)


# --- Auth ---

class RegisterRequest(BaseModel):
    """Request body for account registration."""
    username: str = Field(..., min_length=3, max_length=50, description="Login username")
    email: EmailStr = Field(..., description="Used for password reset")
    password: str = Field(..., min_length=8, max_length=128, description="Password")
    lichess_username: str = Field(
        ..., min_length=1, max_length=50, description="Lichess account to analyze"
    )


class LoginRequest(BaseModel):
    """Request body for login."""
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """JWT access token."""
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    """Public view of a user account."""
    id: int
    username: str
    email: Optional[str] = None
    lichess_username: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None


class ForgotPasswordRequest(BaseModel):
    """Request body to start a password reset."""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Request body to complete a password reset."""
    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class MessageResponse(BaseModel):
    """Generic acknowledgement — used where the response must not leak state."""
    message: str


# --- Settings ---

SYNC_GAME_TYPES = {"rapid", "blitz", "bullet", "classical", "correspondence"}


class SettingsResponse(BaseModel):
    """Profile + sync + learning preferences."""
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    lichess_username: Optional[str] = None
    sync_game_types: List[str]
    sync_days_back: int
    max_new_per_day: int


class SettingsUpdateRequest(BaseModel):
    """Partial update of preferences (only provided fields change)."""
    email: Optional[EmailStr] = None
    display_name: Optional[str] = Field(default=None, max_length=100)
    avatar_url: Optional[str] = Field(default=None, max_length=500)
    lichess_username: Optional[str] = Field(default=None, max_length=50)
    sync_game_types: Optional[List[str]] = None
    sync_days_back: Optional[int] = Field(default=None, ge=1, le=365)
    max_new_per_day: Optional[int] = Field(default=None, ge=0, le=100)


# --- Stats ---

class StatsOverviewResponse(BaseModel):
    """Dashboard headline numbers."""
    games_analyzed: int
    total_blunders: int
    blunders_mastered: int  # cards with >= 3 consecutive successful reviews
    due_now: int
    reviews_done: int


class TimelinePoint(BaseModel):
    """One day of activity for the dashboard charts."""
    date: str  # YYYY-MM-DD
    games: int
    blunders: int
    reviews: int  # review answers submitted that day
    mastered_pct: Optional[float] = None  # day-end mastered %, None before any cards exist


# --- Analysis ---

class AnalysisStartRequest(BaseModel):
    """Request body to start a blunder-analysis job."""
    lichess_username: str = Field(..., min_length=1, max_length=50)
    start_date: date
    end_date: date
    max_games: int = Field(default=20, ge=1, le=100)
    game_type: Optional[GameType] = Field(default=GameType.ALL)


class AnalysisStatusResponse(BaseModel):
    """Progress snapshot of the user's analysis job."""
    state: str = Field(..., description="idle | running | done | error | skipped")
    games_total: int = 0
    games_done: int = 0
    blunders_found: int = 0
    error: Optional[str] = None
    last_synced_at: Optional[str] = None  # ISO; set on "skipped" responses


class BlunderResponse(BaseModel):
    """A detected blunder with everything the review UI needs."""
    id: int
    game_lichess_id: str
    played_at: str
    user_color: str
    opponent: str
    ply: int
    fen_before: str
    move_played_san: str
    move_played_uci: str
    best_move_san: str
    best_move_uci: str
    refutation_san: Optional[str] = None  # opponent's punish of the played move
    refutation_uci: Optional[str] = None
    eval_before_cp: int
    eval_after_cp: int
    win_prob_drop: float


class BestReplyRequest(BaseModel):
    """Ask the engine how the opponent punishes a candidate move."""
    fen: str = Field(..., max_length=120, description="Position before the move")
    move_uci: str = Field(..., min_length=4, max_length=5, description="Candidate move (UCI)")


class BestReplyResponse(BaseModel):
    """Engine's best reply to a candidate move."""
    move_san: str
    reply_uci: Optional[str] = None  # None if the candidate move ends the game
    reply_san: Optional[str] = None
    eval_after_cp: int  # from the candidate mover's perspective
    game_over: bool = False


# --- Reviews ---

class ReviewCardResponse(BaseModel):
    """A due review card with its blunder payload."""
    card_id: int
    due_at: str
    repetitions: int
    lapses: int
    blunder: BlunderResponse


class ReviewAnswerRequest(BaseModel):
    """User's grade for a reviewed card."""
    grade: str = Field(..., pattern="^(again|good|easy)$")


class ReviewStatsResponse(BaseModel):
    """Review queue summary."""
    due_now: int  # actionable now: due seen cards + today's remaining new intake
    new_remaining_today: int  # new-blunder slots left today
    total_cards: int
    total_blunders: int
