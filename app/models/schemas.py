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
    is_owner: bool = False  # can write opening explanations and see reports


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
    blunders_mastered: int  # blunders whose card(s) all have >= 3 consecutive successful reviews
    due_now: int
    reviews_done: int


class TimelinePoint(BaseModel):
    """One day of activity for the dashboard charts."""
    date: str  # YYYY-MM-DD
    games: int
    blunders: int
    reviews: int  # review answers submitted that day
    mastered_pct: Optional[float] = None  # day-end mastered %, None before any cards exist


class BlunderRatePoint(BaseModel):
    """One week of the blunder rate: blunders per 100 of the user's moves."""
    week: str  # YYYY-MM-DD, the Monday the week starts on
    games: int  # games with move data that week: how much the rate is based on
    user_moves: int  # the user's moves counted (only those in the phase, if filtered)
    blunders: int
    rate: float | None = None  # None when there were no moves to count


class RepertoireDepthPoint(BaseModel):
    """One week of repertoire depth: how far the user's games stayed in their repertoire."""
    week: str  # YYYY-MM-DD, the Monday the week starts on
    games: int  # games checked against the repertoire that week
    avg_moves: float | None = None  # average moves played inside the repertoire
    left_pct: float | None = None  # share of games that left the repertoire, in %


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
    # Moves being fetched for games analyzed before Phase 1 (runs after analysis)
    backfill_total: int = 0
    backfill_done: int = 0


class PvPlyResponse(BaseModel):
    """One ply of a stored line — post-blunder, or post-best-move."""
    move_uci: str
    move_san: str
    win_prob: float  # blunderer's win%, 0..100, after this ply


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
    win_prob_before: float  # player's win% right before the blunder, 0..100
    win_prob_after: float  # player's win% right after the blunder, 0..100
    win_prob_drop: float
    refutation_line: List[PvPlyResponse] = []  # follows the move played
    best_line: List[PvPlyResponse] = []  # follows best_move; empty on pre-existing rows


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
    """A due review card with the payload for its kind of training item."""
    card_id: int
    kind: str  # "blunder" | "opening" | "deviation"
    card_type: str  # blunder: "avoid" | "punish"; opening: "play"; deviation: "punish"
    step: int  # which move of the item's line; 0 for every kind so far
    due_at: str
    repetitions: int
    lapses: int
    blunder: BlunderResponse | None = None  # set when kind == "blunder"
    opening: "OpeningCardPayload | None" = None  # set when kind == "opening"
    deviation: "DeviationCardPayload | None" = None  # set when kind == "deviation"


class ReviewAnswerRequest(BaseModel):
    """User's grade for a reviewed card."""
    grade: str = Field(..., pattern="^(again|good|easy)$")


class ReviewStatsResponse(BaseModel):
    """Review queue summary."""
    due_now: int  # actionable now: due seen cards + today's remaining new intake
    new_remaining_today: int  # new-blunder slots left today
    total_cards: int
    total_blunders: int


# --- Repertoire (Phase 2, specs/features/02-openings) ---

class OpeningLabel(BaseModel):
    eco: str
    name: str


class TheoryAlternative(BaseModel):
    uci: str
    san: str
    line: str  # the named line it's on


class RepertoireSuggestion(BaseModel):
    """A suggested move in one of the user's positions, from their games."""
    position_key: str
    ply: int
    move_uci: str
    move_san: str
    games: int
    score: float  # points per game from the user's side
    parent_key: str | None
    opening: OpeningLabel | None
    theory_line: str | None  # None: the move is off-book
    theory_alternatives: list[TheoryAlternative]
    avg_drop: float | None  # average win-% given away, when evals exist
    better_uci: str | None
    better_san: str | None
    accepted_move_uci: str | None  # what the user's repertoire plays here, if anything


class RepertoireEntry(BaseModel):
    """An accepted position of the user's repertoire."""
    position_key: str
    move_uci: str
    move_san: str
    parent_key: str | None
    source: str  # "games" | "manual"
    opening: OpeningLabel | None
    card_id: int | None


class RepertoireResponse(BaseModel):
    color: str
    games_used: int
    suggestions: list[RepertoireSuggestion]
    accepted: list[RepertoireEntry]


class RepertoireChangeResponse(BaseModel):
    accepted: list[RepertoireEntry]
    removed: int  # positions cut off by the change, removed with their cards


class AcceptRequest(BaseModel):
    position_keys: list[str] = Field(..., min_length=1)
    include_below: bool = False


class SetPositionRequest(BaseModel):
    position_key: str
    move_uci: str


class PositionRequest(BaseModel):
    position_key: str


class MoveOption(BaseModel):
    uci: str
    san: str
    theory_line: str | None
    games: int  # how often the user played it here


class DeviationResponse(BaseModel):
    game_id: int
    lichess_game_id: str
    played_at: str
    ply: int
    side: str  # "user" | "opponent"
    position_key: str
    expected_uci: str | None
    played_uci: str
    punish_uci: str | None
    played_was_theory: bool


# --- Opening explanations (Phase 2, task 9) ---

class ExplanationResponse(BaseModel):
    id: int
    position_key: str
    move_uci: str
    source: str  # "owner" | "claude"
    plan: str
    why_this_move: str
    pawn_structure: str | None
    model: str | None  # None for the owner's own text


class OwnerExplanationRequest(BaseModel):
    position_key: str
    move_uci: str
    plan: str = Field(..., min_length=1, max_length=2000)
    why_this_move: str = Field(..., min_length=1, max_length=2000)
    pawn_structure: str = Field("", max_length=2000)


class ReportRequest(BaseModel):
    note: str | None = Field(None, max_length=1000)


class ReportResponse(BaseModel):
    report_count: int


class ReportedExplanation(ExplanationResponse):
    report_count: int
    notes: list[str]


# --- Review payloads for Phase 2 cards (task 10) ---

class OpeningCardPayload(BaseModel):
    """An opening card: replay `line` from the start, then play `answer` from `fen`."""
    color: str
    line: list[str]          # SAN from the start position to the card's position
    fen: str                 # the position to play from
    answer_uci: str
    answer_san: str
    opening: OpeningLabel | None
    theory_line: str | None  # the named line the answer is on; None = off-book
    explanation: ExplanationResponse | None  # owner's text first, then Claude's


class DeviationCardPayload(BaseModel):
    """A punish card: the opponent left your repertoire with a mistake; find the punishment."""
    color: str
    line: list[str]          # SAN from the start to the opponent's mistake, inclusive
    fen: str                 # the position after it, your move
    opponent_move_san: str
    answer_uci: str
    answer_san: str
    opening: OpeningLabel | None
    lichess_game_id: str
    played_at: str


ReviewCardResponse.model_rebuild()
