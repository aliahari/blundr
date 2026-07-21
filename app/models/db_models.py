"""
SQLAlchemy ORM models for persistent storage.

Users own analyzed games; analyzed games own blunders; each blunder has one
spaced-repetition review card. ReviewLog keeps the raw review history so
progress tracking can be built on top later without a schema change.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    eval_before_cp: Mapped[int] = mapped_column(Integer)  # from the user's perspective
    eval_after_cp: Mapped[int] = mapped_column(Integer)
    win_prob_drop: Mapped[float] = mapped_column(Float)  # 0..100 percentage points
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    game: Mapped[AnalyzedGame] = relationship(back_populates="blunders")
    card: Mapped["ReviewCard"] = relationship(back_populates="blunder", cascade="all, delete-orphan", uselist=False)


class ReviewCard(Base):
    """SM-2 spaced-repetition state for one blunder."""

    __tablename__ = "review_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    blunder_id: Mapped[int] = mapped_column(ForeignKey("blunders.id"), unique=True)
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[float] = mapped_column(Float, default=0.0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    blunder: Mapped[Blunder] = relationship(back_populates="card")


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
