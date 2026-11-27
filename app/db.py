"""
Async SQLAlchemy database setup.

A single async engine + sessionmaker for the whole app. Tables are created
at startup (see main.py lifespan) — fine for SQLite; if this ever moves to
Postgres, swap create_all for Alembic migrations.
"""
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.schema import CreateTable

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

        result = await conn.exec_driver_sql("PRAGMA table_info(analyzed_games)")
        columns = {row[1] for row in result.fetchall()}
        for name, ddl in (
            ("positions", "TEXT"),
            ("user_moves", "INTEGER"),
            ("speed", "VARCHAR(16)"),
            ("initial_eval_cp", "INTEGER"),
        ):
            if name not in columns:
                # NULL on games analyzed before move data was kept.
                await conn.exec_driver_sql(f"ALTER TABLE analyzed_games ADD COLUMN {name} {ddl}")

        result = await conn.exec_driver_sql("PRAGMA table_info(review_cards)")
        if "kind" not in {row[1] for row in result.fetchall()}:
            await conn.run_sync(_rebuild_review_cards)

        # Lightweight dev migrations — create_all doesn't add new columns to
        # tables that already exist. Replace with Alembic if this grows.
        result = await conn.exec_driver_sql("PRAGMA table_info(blunders)")
        columns = {row[1] for row in result.fetchall()}
        if "refutation_uci" not in columns:
            await conn.exec_driver_sql("ALTER TABLE blunders ADD COLUMN refutation_uci VARCHAR(8)")
            await conn.exec_driver_sql("ALTER TABLE blunders ADD COLUMN refutation_san VARCHAR(12)")
        if "refutation_line" not in columns:
            await conn.exec_driver_sql("ALTER TABLE blunders ADD COLUMN refutation_line TEXT")
        if "best_line" not in columns:
            # Stays NULL on rows analyzed before this column existed — the
            # review UI just omits the best-move continuation for those.
            await conn.exec_driver_sql("ALTER TABLE blunders ADD COLUMN best_line TEXT")

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

        # Phase 2: openings (specs/features/02-openings, design D2). Each
        # column's DDL matches what create_all emits for it, so a migrated
        # database ends up identical to a fresh one (tests/test_migrations.py).
        await _add_missing_columns(conn, "users", [
            ("is_owner", "BOOLEAN DEFAULT '0' NOT NULL"),
        ])
        # Sign in with Lichess (specs/features/lichess-sign-in/design.md)
        await _add_missing_columns(conn, "users", [
            ("auth_provider", "VARCHAR(20)"),
            ("provider_user_id", "VARCHAR(64)"),
            ("lichess_token_enc", "TEXT"),
            ("lichess_token_expires_at", "DATETIME"),
            ("lichess_ratings", "TEXT"),
        ])
        await conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_provider"
            " ON users (auth_provider, provider_user_id)"
        )
        await _add_missing_columns(conn, "analyzed_games", [
            ("repertoire_exit_ply", "INTEGER"),
            ("repertoire_checked_at", "DATETIME"),
            ("repertoire_plies", "INTEGER"),
        ])
        await _add_missing_columns(conn, "review_cards", [
            ("repertoire_move_id", "INTEGER REFERENCES repertoire_moves (id)"),
            ("repertoire_deviation_id", "INTEGER REFERENCES repertoire_deviations (id)"),
        ])
        # The unique (source, card_type, step) index of each card kind;
        # checkfirst makes this a no-op where create_all or the review_cards
        # rebuild already made them.
        from .models.db_models import ReviewCard
        for index in ReviewCard.__table__.indexes:
            await conn.run_sync(lambda sync_conn, i=index: i.create(sync_conn, checkfirst=True))

        # Sign in with Lichess replaces password accounts: the old accounts and
        # everything they own go (owner, 2026-11-27). Last, so the old schema
        # is fully up to date before users is rebuilt.
        result = await conn.exec_driver_sql("PRAGMA table_info(users)")
        if "password_hash" in {row[1] for row in result.fetchall()}:
            await conn.run_sync(_migrate_to_lichess_accounts)


async def _add_missing_columns(conn, table: str, columns: list[tuple[str, str]]) -> None:
    """ALTER TABLE ... ADD COLUMN for each (name, DDL) the table doesn't have yet."""
    result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
    existing = {row[1] for row in result.fetchall()}
    for name, ddl in columns:
        if name not in existing:
            await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _rebuild_review_cards(conn) -> None:
    """
    Move review_cards to the generic card model (specs/features/01-foundations,
    D1): add kind/step/created_at, make blunder_id nullable, and widen the
    unique key to (blunder_id, card_type, step).

    SQLite can't drop NOT NULL or change a table constraint in place, so this
    is the standard rebuild: create the new table, copy, drop the old one,
    rename the new one into place. The order matters: renaming the OLD table
    aside instead would make SQLite (3.26+) rewrite review_logs' foreign key to
    follow it, leaving review_logs pointing at a dropped table.

    The new table's DDL comes from the ORM model itself, so a migrated
    database and a freshly created one have the same schema. Card ids are
    copied as-is, so review_logs.card_id keeps pointing at the right cards.
    """
    from .models.db_models import ReviewCard

    table = ReviewCard.__table__
    ddl = str(CreateTable(table).compile(dialect=conn.dialect))
    prefix = "CREATE TABLE review_cards "
    if prefix not in ddl:
        raise RuntimeError(f"Unexpected review_cards DDL: {ddl!r}")
    new_table_ddl = ddl.replace(prefix, "CREATE TABLE review_cards_new ", 1)

    # pysqlite only opens a transaction on the first DML statement, so a
    # CREATE TABLE here is committed on its own. If the copy below fails, the
    # empty review_cards_new survives the rollback, so clear it out first.
    conn.exec_driver_sql("DROP TABLE IF EXISTS review_cards_new")
    conn.exec_driver_sql(new_table_ddl)

    conn.exec_driver_sql(
        "INSERT INTO review_cards_new (id, user_id, kind, blunder_id, card_type, step, "
        "ease, interval_days, repetitions, lapses, due_at, last_reviewed_at, created_at) "
        "SELECT rc.id, rc.user_id, 'blunder', rc.blunder_id, rc.card_type, 0, "
        "rc.ease, rc.interval_days, rc.repetitions, rc.lapses, rc.due_at, rc.last_reviewed_at, "
        # A card whose blunder row is gone shouldn't block startup: fall back
        # to its due date (always set, same storage format) as its age.
        "COALESCE(b.created_at, rc.due_at) "
        "FROM review_cards rc LEFT JOIN blunders b ON b.id = rc.blunder_id"
    )
    old_count = conn.exec_driver_sql("SELECT COUNT(*) FROM review_cards").scalar()
    new_count = conn.exec_driver_sql("SELECT COUNT(*) FROM review_cards_new").scalar()
    if old_count != new_count:
        # Raising rolls back the copy; the old table is untouched.
        raise RuntimeError(
            f"review_cards rebuild copied {new_count} of {old_count} rows; aborting"
        )

    conn.exec_driver_sql("DROP TABLE review_cards")
    conn.exec_driver_sql("ALTER TABLE review_cards_new RENAME TO review_cards")
    # Indexes went with the old table; recreate them under their usual names.
    for index in table.indexes:
        index.create(conn)


def _user_owned_tables(conn) -> list[str]:
    """Every table with a foreign key to users, and every table under those."""
    tables = [r[0] for r in conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()]
    parents = {
        t: {fk[2] for fk in conn.exec_driver_sql(f"PRAGMA foreign_key_list({t})").fetchall()}
        for t in tables
    }
    owned: set[str] = set()
    changed = True
    while changed:
        changed = False
        for t, refs in parents.items():
            if t not in owned and t != "users" and refs & (owned | {"users"}):
                owned.add(t)
                changed = True
    return sorted(owned)


def _migrate_to_lichess_accounts(conn) -> None:
    """
    Move to Lichess sign-in (specs/features/lichess-sign-in/design.md): delete
    the password accounts and everything they own, drop password_reset_tokens,
    and rebuild users without password_hash and email.

    Shared data (opening names, theory, engine lines, explanations, the
    explorer cache) has no foreign key to users and stays. The users rebuild
    follows _rebuild_review_cards()'s order: create new, copy, check the
    count, drop old, rename new.
    """
    from .models.db_models import AppMeta, User

    # Foreign keys aren't enforced (SQLite's default; the app never turns
    # them on), so the order of these deletes doesn't matter.
    for table in _user_owned_tables(conn):
        conn.exec_driver_sql(f"DELETE FROM {table}")
    conn.exec_driver_sql("DELETE FROM users")
    # The explanation reports went with their accounts; their counts go too
    conn.exec_driver_sql("UPDATE opening_explanations SET report_count = 0")
    conn.exec_driver_sql("DROP TABLE IF EXISTS password_reset_tokens")

    table = User.__table__
    ddl = str(CreateTable(table).compile(dialect=conn.dialect))
    prefix = "CREATE TABLE users "
    if prefix not in ddl:
        raise RuntimeError(f"Unexpected users DDL: {ddl!r}")
    conn.exec_driver_sql("DROP TABLE IF EXISTS users_new")
    conn.exec_driver_sql(ddl.replace(prefix, "CREATE TABLE users_new ", 1))
    # Nothing is left to copy, but check, as every rebuild does
    old_count = conn.exec_driver_sql("SELECT COUNT(*) FROM users").scalar()
    new_count = conn.exec_driver_sql("SELECT COUNT(*) FROM users_new").scalar()
    if old_count != new_count:
        raise RuntimeError(f"users rebuild: {new_count} of {old_count} rows; aborting")
    conn.exec_driver_sql("DROP TABLE users")
    conn.exec_driver_sql("ALTER TABLE users_new RENAME TO users")
    for index in table.indexes:
        index.create(conn)
    conn.execute(AppMeta.__table__.insert().values(
        key="lichess_accounts_migrated", value=datetime.now(UTC).isoformat()))


async def get_db() -> AsyncSession:
    """FastAPI dependency that yields a database session."""
    async with async_session() as session:
        yield session
