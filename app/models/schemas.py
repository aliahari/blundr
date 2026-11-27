"""
Pydantic schemas for API requests and responses.
"""
from pydantic import BaseModel, Field
from datetime import date
from typing import Optional, List, Dict, Any, Literal
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

class UserResponse(BaseModel):
    """Public view of a user account."""
    id: int
    username: str
    lichess_username: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    is_owner: bool = False  # can write opening explanations and see reports
    lichess_connected: bool = False  # a usable Lichess token is stored


# --- Settings ---

SYNC_GAME_TYPES = {"rapid", "blitz", "bullet", "classical", "correspondence"}


class SettingsResponse(BaseModel):
    """Profile + sync + learning preferences."""
    username: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    lichess_username: Optional[str] = None
    sync_game_types: List[str]
    sync_days_back: int
    max_new_per_day: int


class SettingsUpdateRequest(BaseModel):
    """Partial update of preferences (only provided fields change)."""
    display_name: Optional[str] = Field(default=None, max_length=100)
    avatar_url: Optional[str] = Field(default=None, max_length=500)
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


class RepertoireEntry(BaseModel):
    """An accepted position of the user's repertoire."""
    position_key: str
    move_uci: str
    move_san: str
    parent_key: str | None
    source: str  # "games" | "manual"
    opening: OpeningLabel | None
    card_id: int | None


class GapOut(BaseModel):
    """A position the builder asks the user to prepare."""
    line: list[str]  # uci from the start
    line_san: list[str]
    fen: str
    key: str
    likelihood: float | None  # share of games reaching it at the user's level; None off the walk
    last_reply_san: str | None
    last_share: float | None
    my_games: int
    opening: "OpeningLabel | None"
    candidates: list["CandidateRow"]
    engine_busy: bool


class NextResponse(BaseModel):
    """The builder's next step (specs/features/02-openings/repertoire-builder.md)."""
    gap: GapOut | None  # None: everything at this goal is prepared
    prepared: int
    left: int
    goal: int
    source: Literal["lichess", "masters"]
    reconnect: bool  # no usable Lichess token: sign in again for level data


class ReplyChoice(BaseModel):
    """An opponent reply in the navigator."""
    uci: str
    san: str
    opening: "OpeningLabel | None"  # the opening it leads to, when named
    share: float  # of the games here at the user's level (or masters)
    likelihood: float | None  # how often the user reaches the position after it
    completion: float | None  # how much below it is prepared, at the goal
    in_scope: bool  # reached in at least 1 game in `goal`
    answered: bool  # the user has a move in the position after it


class ExploreResponse(BaseModel):
    """One position in the builder's navigator (repertoire-builder.md, "The screen")."""
    line: list[str]
    line_san: list[str]
    fen: str
    key: str
    user_to_move: bool
    opening: "OpeningLabel | None"
    likelihood: float | None
    completion: float | None  # of the whole colour at the goal
    goal: int
    prepared: int
    left: int
    source: Literal["lichess", "masters"]
    reconnect: bool
    repertoire_move: str | None  # uci, at the user's turn
    pick_blocked_at: list[str] | None
    replies: list[ReplyChoice]  # at the opponent's turn
    candidates: list["CandidateRow"]  # at the user's turn
    engine_busy: bool


class GoalRequest(BaseModel):
    goal: int


class RepertoireChangeResponse(BaseModel):
    accepted: list[RepertoireEntry]
    removed: int  # positions cut off by the change, removed with their cards
    next: NextResponse | None = None  # the builder's next step, after a pick


class SetPositionRequest(BaseModel):
    position_key: str
    move_uci: str


class PositionRequest(BaseModel):
    position_key: str


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


# --- Repertoire graph (specs/features/02-openings/repertoire-graph.md) -------

class GraphNodeOut(BaseModel):
    key: str
    user_to_move: bool
    games: int
    ply: int
    parent: str | None  # spanning-tree parent; the line is rebuilt from these
    opening: OpeningLabel | None
    mark: str | None  # "you_left_theory" (gaps come separately, in `gaps`)
    folded: int


class GraphEdgeOut(BaseModel):
    parent: str
    child: str
    uci: str
    san: str
    games: int
    w: int
    d: int
    l: int  # noqa: E741
    master_games: int
    theory: bool
    repertoire: bool
    transposition: bool


class RepertoireGraphResponse(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    todo: list[str]
    theory_source: Literal["masters", "dataset"]
    avg_moves_in_repertoire: float | None
    empty_repertoire: bool
    gaps: list[str] = []  # positions the builder would ask for at the current goal
    thin: list[str] = []  # positions too rarely reached at the user's level to go on


class PanelRepertoireMove(BaseModel):
    uci: str
    san: str


class CandidateRow(BaseModel):
    uci: str
    san: str
    engine_rank: int | None
    eval_cp: int | None
    master_games: int
    master_share: float | None  # of the master games here; None without masters data
    master_score: float | None  # points per game for the side to move (win 1, draw 0.5)
    games: int
    share: float | None  # of the user's moves here; None when they never played here
    score: float | None  # points per game from the user's side
    theory: bool
    # What players at the user's level do here (Lichess explorer); None without data
    level_share: float | None = None
    level_score: float | None = None  # points per game for the side to move
    # Results at the user's level for the side to move: shares of win, draw, loss
    level_win: float | None = None
    level_draw: float | None = None
    level_loss: float | None = None
    opening: "OpeningLabel | None" = None  # the opening the move leads to, when named


class ReplyRow(BaseModel):
    uci: str
    san: str
    games: int
    share: float
    score: float | None
    master_share: float | None
    theory: bool
    answer_san: str | None
    in_games: bool


class PositionPanelResponse(BaseModel):
    key: str
    fen: str
    line_san: list[str]
    user_to_move: bool
    opening: OpeningLabel | None
    games: int
    repertoire_move: PanelRepertoireMove | None
    candidates: list[CandidateRow]
    replies: list[ReplyRow]
    answered_pct: float | None
    pick_blocked_at: list[str] | None
    theory_source: Literal["masters", "dataset"]
    engine_busy: bool
    usual_move_note: str | None


# GapOut refers to models defined further down
GapOut.model_rebuild()
ReplyChoice.model_rebuild()
ExploreResponse.model_rebuild()
CandidateRow.model_rebuild()
NextResponse.model_rebuild()
RepertoireChangeResponse.model_rebuild()
