"""
Async SQLAlchemy database setup.

A single async engine + sessionmaker for the whole app. Tables are created
at startup (see main.py lifespan) — fine for SQLite; if this ever moves to
Postgres, swap create_all for Alembic migrations.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


engine = create_async_engine(settings.DATABASE_URL, echo=False)

async_session = async_sessionmaker(engine, expire_on_commit=False)


async def create_tables() -> None:
    """Create all tables that don't exist yet."""
    # Import models so they register on Base.metadata before create_all
    from .models import db_models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Lightweight dev migrations — create_all doesn't add new columns to
        # tables that already exist. Replace with Alembic if this grows.
        result = await conn.exec_driver_sql("PRAGMA table_info(blunders)")
        columns = {row[1] for row in result.fetchall()}
        if "refutation_uci" not in columns:
            await conn.exec_driver_sql("ALTER TABLE blunders ADD COLUMN refutation_uci VARCHAR(8)")
            await conn.exec_driver_sql("ALTER TABLE blunders ADD COLUMN refutation_san VARCHAR(12)")

        result = await conn.exec_driver_sql("PRAGMA table_info(users)")
        columns = {row[1] for row in result.fetchall()}
        if "display_name" not in columns:
            await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN display_name VARCHAR(100)")
            await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN avatar_url VARCHAR(500)")
            await conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN sync_game_types VARCHAR(200) "
                "NOT NULL DEFAULT '[\"rapid\", \"blitz\"]'"
            )
            await conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN sync_days_back INTEGER NOT NULL DEFAULT 7"
            )

        result = await conn.exec_driver_sql("PRAGMA table_info(users)")
        columns = {row[1] for row in result.fetchall()}
        if "max_new_per_day" not in columns:
            await conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN max_new_per_day INTEGER NOT NULL DEFAULT 10"
            )
        if "last_synced_at" not in columns:
            await conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN last_synced_at DATETIME"
            )
        if "email" not in columns:
            # No UNIQUE here — SQLite can't add a unique constraint via
            # ALTER TABLE ADD COLUMN. Enforced instead by a unique index,
            # which behaves identically for query/insert purposes.
            await conn.exec_driver_sql("ALTER TABLE users ADD COLUMN email VARCHAR(255)")
            await conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"
            )


async def get_db() -> AsyncSession:
    """FastAPI dependency that yields a database session."""
    async with async_session() as session:
        yield session
