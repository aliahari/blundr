"""
SQLAlchemy ORM models for persistent storage.

Users own analyzed games; analyzed games own blunders; each blunder has one
or two spaced-repetition review cards ("avoid" and "punish"). Review cards
are generic over the kind of training item they drill (see ReviewCard), so
later training kinds reuse the same queue. ReviewLog
keeps the raw review history so progress tracking can be built on top later
without a schema change.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base


def utcnow() -> datetime:
    """Timezone-aware UTC now (SQLite stores it naive but consistently UTC)."""
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    # Nullable at the DB level so the migration adding this column doesn't
    # need to invent placeholder values for existing rows — SQLite allows
    # multiple NULLs under a UNIQUE constraint. New registrations always
    # require one (enforced in the request schema, not here).
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    lichess_username: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Profile
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Sync preferences
    sync_game_types: Mapped[str] = mapped_column(
        String(200), default='["rapid", "blitz"]'  # JSON list of Lichess perf types
    )
    sync_days_back: Mapped[int] = mapped_column(Integer, default=7)
    # Learning preferences: how many never-reviewed blunders may enter the
    # queue per day (0 pauses new intake; seen cards are always served)
    max_new_per_day: Mapped[int] = mapped_column(Integer, default=10)
    # Last successful sync — non-forced syncs are skipped within 24h of this
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # The app's owner: can write opening explanations and see reports
    # (specs/features/02-openings, D4). Set by hand; there is no sign-up path.
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    games: Mapped[list["AnalyzedGame"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AnalyzedGame(Base):
    """A Lichess game that has been through engine analysis for a user."""

    __tablename__ = "analyzed_games"
    __table_args__ = (UniqueConstraint("user_id", "lichess_game_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    lichess_game_id: Mapped[str] = mapped_column(String(16), index=True)
    lichess_username: Mapped[str] = mapped_column(String(50))  # which account was analyzed
    user_color: Mapped[str] = mapped_column(String(5))  # "white" | "black"
    white_name: Mapped[str] = mapped_column(String(50))
    black_name: Mapped[str] = mapped_column(String(50))
    result: Mapped[str] = mapped_column(String(8))
    played_at: Mapped[datetime] = mapped_column(DateTime)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Move data kept from the analysis (specs/features/01-foundations, D3).
    # JSON list, one entry per ply: {san, uci, eval_cp, best_uci} — eval_cp
    # is the eval AFTER the move from user_color's POV, best_uci the
    # engine's pick in the position BEFORE it. FENs come from replaying.
    # Games analyzed before Phase 1 get their moves later from Lichess
    # (backfill_service): for those, eval_cp and best_uci are None in every
    # entry and initial_eval_cp stays NULL, so readers must handle None. A
    # game Lichess no longer has keeps positions NULL.
    positions: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_moves: Mapped[int | None] = mapped_column(Integer, nullable=True)  # moves the user made
    speed: Mapped[str | None] = mapped_column(String(16), nullable=True)  # Lichess speed: blitz, rapid...
    # Eval of the starting position, so the first move's drop can be
    # computed from `positions` alone.
    initial_eval_cp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Checking the game against the user's opening repertoire (Phase 2, R7):
    # the ply where it left the repertoire, NULL if it never did. NULL in
    # repertoire_checked_at means "not checked yet", which tells the two apart.
    repertoire_exit_ply: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repertoire_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="games")
    blunders: Mapped[list["Blunder"]] = relationship(back_populates="game", cascade="all, delete-orphan")


class Blunder(Base):
    """A single blunder: the user's move dropped the win probability sharply."""

    __tablename__ = "blunders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("analyzed_games.id"), index=True)
    ply: Mapped[int] = mapped_column(Integer)  # 1-based half-move index of the blunder
    fen_before: Mapped[str] = mapped_column(Text)  # position the user faced
    move_played_uci: Mapped[str] = mapped_column(String(8))
    move_played_san: Mapped[str] = mapped_column(String(12))
    best_move_uci: Mapped[str] = mapped_column(String(8))
    best_move_san: Mapped[str] = mapped_column(String(12))
    # Engine's punishing reply to the played blunder ("why it was a blunder");
    # null when the blunder ended the game (e.g. stalemate) or for rows
    # analyzed before this column existed.
    refutation_uci: Mapped[str | None] = mapped_column(String(8), nullable=True)
    refutation_san: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # JSON list of up to REFUTATION_LINE_PLIES {move_uci, move_san, eval_cp}
    # dicts, continuing the PV from refutation_uci onward. Null when
    # refutation_uci is null, or for rows analyzed before this column existed.
    refutation_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Same shape, the other branch: what best play looks like instead,
    # starting with the opponent's reply to best_move. This is what answers
    # "why was that the best move?" after a card is solved. Null when the
    # best move ended the game, or for rows analyzed before this column.
    best_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval_before_cp: Mapped[int] = mapped_column(Integer)  # from the user's perspective
    eval_after_cp: Mapped[int] = mapped_column(Integer)
    win_prob_drop: Mapped[float] = mapped_column(Float)  # 0..100 percentage points
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    game: Mapped[AnalyzedGame] = relationship(back_populates="blunders")
    # Two cards: "avoid" (play the blunderer, find the best move) and
    # "punish" (play the opponent, find the refutation) — punish is absent
    # when refutation_uci is null (the blunder ended the game).
    cards: Mapped[list["ReviewCard"]] = relationship(back_populates="blunder", cascade="all, delete-orphan")


class ReviewCard(Base):
    """
    SM-2 spaced-repetition state for one move of one training item.

    A card trains one item of a given `kind` and points to it through that
    kind's own nullable foreign-key column (only `blunder_id` so far; later
    kinds add their own column). Exactly one source column is set, matching
    `kind` — enforced by new_review_card(), not a CHECK constraint, since
    SQLite can't change a CHECK without rebuilding the table.

    `step` is which move of the item's line the card drills, so a line is
    scheduled move by move. Blunder cards are a single move: always step 0.
    """

    __tablename__ = "review_cards"
    __table_args__ = (
        UniqueConstraint("blunder_id", "card_type", "step"),
        # Later kinds' keys are named indexes, not inline constraints, so the
        # ADD COLUMN migration in db.create_tables() creates exactly what
        # create_all does on a fresh database.
        Index(
            "ux_review_cards_repertoire_move", "repertoire_move_id", "card_type", "step",
            unique=True,
        ),
        Index(
            "ux_review_cards_repertoire_deviation", "repertoire_deviation_id", "card_type", "step",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Server defaults on the new columns keep raw-SQL inserts that predate
    # them (and the migration's own copy) valid.
    kind: Mapped[str] = mapped_column(String(20), default="blunder", server_default="blunder")
    blunder_id: Mapped[int | None] = mapped_column(ForeignKey("blunders.id"), nullable=True)
    card_type: Mapped[str] = mapped_column(String(20))  # blunder: "avoid" | "punish"
    step: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[float] = mapped_column(Float, default=0.0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.current_timestamp()
    )
    # Phase 2 source columns, one per kind (see CARD_SOURCE_COLUMNS)
    repertoire_move_id: Mapped[int | None] = mapped_column(
        ForeignKey("repertoire_moves.id"), nullable=True
    )
    repertoire_deviation_id: Mapped[int | None] = mapped_column(
        ForeignKey("repertoire_deviations.id"), nullable=True
    )

    blunder: Mapped[Blunder | None] = relationship(back_populates="cards")
    repertoire_move: Mapped["RepertoireMove | None"] = relationship(back_populates="cards")
    repertoire_deviation: Mapped["RepertoireDeviation | None"] = relationship(
        back_populates="cards"
    )

    @property
    def item_key(self) -> tuple[str, int]:
        """(kind, source id): the training item this card belongs to."""
        return (self.kind, self.source_id)

    @property
    def source_id(self) -> int:
        """Id in this card's kind's source table."""
        column = CARD_SOURCE_COLUMNS.get(self.kind)
        if column is None:
            raise ValueError(f"Unknown card kind: {self.kind!r}")
        return getattr(self, column)


# Card kind -> the ReviewCard column that points at its source row. A new
# kind adds its column to ReviewCard (plus its unique index and an ADD
# COLUMN migration in db.create_tables) and an entry here.
CARD_SOURCE_COLUMNS: dict[str, str] = {
    "blunder": "blunder_id",
    "opening": "repertoire_move_id",          # Phase 2: a position in the user's repertoire
    "deviation": "repertoire_deviation_id",   # Phase 2: punish an opponent's slip out of it
}


def new_review_card(
    *, user_id: int, kind: str, card_type: str, step: int = 0, **source: int
) -> ReviewCard:
    """
    Build a ReviewCard, checking that exactly one source is set and that it
    is the one matching `kind`, e.g. new_review_card(user_id=1, kind="blunder",
    card_type="avoid", blunder_id=7).
    """
    if kind not in CARD_SOURCE_COLUMNS:
        raise ValueError(f"Unknown card kind: {kind!r}")
    given = {col for col, value in source.items() if value is not None}
    unknown = set(source) - set(CARD_SOURCE_COLUMNS.values())
    if unknown:
        raise ValueError(f"Not a card source column: {sorted(unknown)}")
    if given != {CARD_SOURCE_COLUMNS[kind]}:
        raise ValueError(
            f"A {kind!r} card needs exactly {CARD_SOURCE_COLUMNS[kind]!r} set, got {sorted(given)}"
        )
    return ReviewCard(user_id=user_id, kind=kind, card_type=card_type, step=step, **source)


class ReviewLog(Base):
    """Raw review history — the substrate for future progress tracking."""

    __tablename__ = "review_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("review_cards.id"), index=True)
    grade: Mapped[str] = mapped_column(String(8))  # "again" | "good" | "easy"
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PasswordResetToken(Base):
    """
    A single-use, expiring token emailed to the user for password reset.

    Opaque random token rather than a JWT — it needs to be revocable
    (single-use, invalidated on password change) and short-lived, which a
    self-contained signed token doesn't buy you anything for.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --- Phase 2: openings (specs/features/02-openings, design D2) -------------
#
# Positions are keyed by python-chess's board.epd(): the first four FEN
# fields (placement, side to move, castling, en passant only when a capture
# is legal), so transpositions share a key.


class RepertoireMove(Base):
    """The user's chosen move in a position where it's their turn."""

    __tablename__ = "repertoire_moves"
    __table_args__ = (UniqueConstraint("user_id", "color", "position_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    color: Mapped[str] = mapped_column(String(5))  # the repertoire: "white" | "black"
    position_key: Mapped[str] = mapped_column(String(100))  # board.epd() before the move
    move_uci: Mapped[str] = mapped_column(String(8))
    move_san: Mapped[str] = mapped_column(String(12))
    # The user's previous repertoire position in the line this was accepted
    # from; NULL at the root. Gives line order and the line shown in review.
    parent_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(10))  # "games" (accepted suggestion) | "manual"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    cards: Mapped[list[ReviewCard]] = relationship(
        back_populates="repertoire_move", cascade="all, delete-orphan"
    )


class OpeningExplanation(Base):
    """Why a move makes sense in a position. Shared across users."""

    __tablename__ = "opening_explanations"
    __table_args__ = (UniqueConstraint("position_key", "move_uci", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_key: Mapped[str] = mapped_column(String(100), index=True)
    move_uci: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(10))  # "claude" | "owner"; owner wins
    body: Mapped[str] = mapped_column(Text)  # JSON: {plan, why_this_move, pawn_structure|null}
    model: Mapped[str | None] = mapped_column(String(40), nullable=True)  # NULL for owner-written
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    report_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ExplanationReport(Base):
    """A user flagging an explanation as wrong; one per user per explanation."""

    __tablename__ = "explanation_reports"
    __table_args__ = (UniqueConstraint("explanation_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    explanation_id: Mapped[int] = mapped_column(ForeignKey("opening_explanations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RepertoireDeviation(Base):
    """The first point where a game left the user's repertoire (one per game)."""

    __tablename__ = "repertoire_deviations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("analyzed_games.id"), unique=True)
    ply: Mapped[int] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String(8))  # "user" | "opponent"
    position_key: Mapped[str] = mapped_column(String(100))
    expected_uci: Mapped[str | None] = mapped_column(String(8), nullable=True)
    played_uci: Mapped[str] = mapped_column(String(8))
    # Engine's best reply when the opponent's deviation was a blunder (R6.3)
    punish_uci: Mapped[str | None] = mapped_column(String(8), nullable=True)
    played_was_theory: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    cards: Mapped[list[ReviewCard]] = relationship(
        back_populates="repertoire_deviation", cascade="all, delete-orphan"
    )


class OpeningName(Base):
    """Named opening positions from the lichess-org/chess-openings dataset (CC0)."""

    __tablename__ = "opening_names"

    position_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    eco: Mapped[str] = mapped_column(String(3))
    name: Mapped[str] = mapped_column(String(200))


class TheoryMove(Base):
    """A move on one of the dataset's named lines: 'theory' (R2.5)."""

    __tablename__ = "theory_moves"

    position_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    move_uci: Mapped[str] = mapped_column(String(8), primary_key=True)
    line_name: Mapped[str] = mapped_column(String(200))  # the shortest named line it's on


class ExplanationBatch(Base):
    """A Message Batch of explanations in flight, so a restart resumes it (D4)."""

    __tablename__ = "explanation_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(100), unique=True)
    model: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ExplorerCache(Base):
    """Optional Lichess opening explorer results, per position and rating band (D8)."""

    __tablename__ = "explorer_cache"

    position_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    rating_band: Mapped[str] = mapped_column(String(20), primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppMeta(Base):
    """Small key-value facts about the app's own data, e.g. which version of
    the opening dataset is loaded (opening_book)."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
