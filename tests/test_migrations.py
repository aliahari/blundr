"""
Migration tests for db.create_tables().

These run against their own temporary SQLite file, never the shared
tests/test_blundr.db, so the shared TestClient in conftest.py is untouched.
`app.db.engine` is swapped for an engine on the temp file for the duration
of each test — create_tables() looks the module-level name up at call time.
"""
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import db as app_db
from app.db import Base
from app.models.db_models import AnalyzedGame, Blunder, ReviewLog, User

# review_cards exactly as production has it before Phase 1 (spec:
# specs/features/01-foundations/design.md, D1). Copied DDL, not the ORM
# model, because the model is what the migration changes.
OLD_REVIEW_CARDS_DDL = [
    """
    CREATE TABLE review_cards (
        id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        blunder_id INTEGER NOT NULL,
        card_type VARCHAR(10) NOT NULL,
        ease FLOAT NOT NULL,
        interval_days FLOAT NOT NULL,
        repetitions INTEGER NOT NULL,
        lapses INTEGER NOT NULL,
        due_at DATETIME NOT NULL,
        last_reviewed_at DATETIME,
        PRIMARY KEY (id),
        UNIQUE (blunder_id, card_type),
        FOREIGN KEY(user_id) REFERENCES users (id),
        FOREIGN KEY(blunder_id) REFERENCES blunders (id)
    )
    """,
    "CREATE INDEX ix_review_cards_due_at ON review_cards (due_at)",
    "CREATE INDEX ix_review_cards_user_id ON review_cards (user_id)",
]

# Columns that exist both before and after the migration and must survive
# it byte-for-byte.
PRESERVED_COLUMNS = (
    "id, user_id, blunder_id, card_type, ease, interval_days, "
    "repetitions, lapses, due_at, last_reviewed_at"
)


async def _build_old_schema_db(engine) -> dict:
    """
    Create a pre-Phase-1 database: every table from the current metadata
    except review_cards, which gets the old DDL. Seed it with two users'
    worth of games, blunders, cards (one blunder with no punish card) and
    review logs. Returns the seeded ids for assertions.
    """
    other_tables = [t for name, t in Base.metadata.tables.items() if name != "review_cards"]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=other_tables))
        for stmt in OLD_REVIEW_CARDS_DDL:
            await conn.exec_driver_sql(stmt)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        users = [
            User(username="alice", password_hash="x"),
            User(username="bob", password_hash="x"),
        ]
        s.add_all(users)
        await s.flush()

        blunders = []
        for i, user in enumerate(users):
            game = AnalyzedGame(
                user_id=user.id,
                lichess_game_id=f"game{i}",
                lichess_username=user.username,
                user_color="white",
                white_name=user.username,
                black_name="opponent",
                result="0-1",
                played_at=datetime(2026, 10, 1 + i, 12, 0),
            )
            s.add(game)
            await s.flush()
            for ply in (12, 25):
                b = Blunder(
                    user_id=user.id,
                    game_id=game.id,
                    ply=ply,
                    fen_before="8/8/8/8/8/8/8/K6k w - - 0 1",
                    move_played_uci="a1a2",
                    move_played_san="Ka2",
                    best_move_uci="a1b1",
                    best_move_san="Kb1",
                    eval_before_cp=100,
                    eval_after_cp=-400,
                    win_prob_drop=40.0,
                    # Distinct per blunder so the created_at backfill is
                    # checked against the right row, not just any blunder.
                    created_at=datetime(2026, 10, 1 + i, 13, ply),
                )
                s.add(b)
                blunders.append(b)
        await s.flush()
        await s.commit()

    # Cards via raw SQL — the ORM model no longer matches this table.
    # The last blunder gets only an avoid card (its blunder ended the game).
    card_rows = []
    for n, b in enumerate(blunders):
        types = ["avoid"] if n == len(blunders) - 1 else ["avoid", "punish"]
        for card_type in types:
            reviewed = n % 2 == 0
            card_rows.append((
                b.user_id, b.id, card_type,
                2.36 if reviewed else 2.5,
                6.0 if reviewed else 0.0,
                2 if reviewed else 0,
                1 if reviewed else 0,
                f"2026-11-{10 + n:02d} 08:30:00.000000",
                f"2026-11-0{1 + n} 08:30:00.000000" if reviewed else None,
            ))
    async with engine.begin() as conn:
        for row in card_rows:
            await conn.exec_driver_sql(
                "INSERT INTO review_cards (user_id, blunder_id, card_type, ease, "
                "interval_days, repetitions, lapses, due_at, last_reviewed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
        result = await conn.exec_driver_sql(
            "SELECT id, user_id FROM review_cards WHERE last_reviewed_at IS NOT NULL"
        )
        reviewed_cards = result.fetchall()

    async with session_factory() as s:
        for card_id, user_id in reviewed_cards:
            s.add(ReviewLog(user_id=user_id, card_id=card_id, grade="good"))
            s.add(ReviewLog(user_id=user_id, card_id=card_id, grade="again"))
        await s.commit()

    return {"blunder_ids": [b.id for b in blunders], "card_count": len(card_rows)}


async def _snapshot(engine) -> dict:
    """Everything the migration must leave unchanged, read with raw SQL."""
    async with engine.connect() as conn:
        cards = (await conn.exec_driver_sql(
            f"SELECT {PRESERVED_COLUMNS} FROM review_cards ORDER BY id"
        )).fetchall()
        logs = (await conn.exec_driver_sql(
            "SELECT id, user_id, card_id, grade, reviewed_at FROM review_logs ORDER BY id"
        )).fetchall()
    return {"cards": cards, "logs": logs}


async def _full_card_rows(engine) -> list:
    async with engine.connect() as conn:
        return (await conn.exec_driver_sql(
            "SELECT * FROM review_cards ORDER BY id"
        )).fetchall()


@pytest.fixture
def temp_engine(tmp_path, monkeypatch):
    """An engine on a fresh temp DB file, installed as app.db.engine."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'migrate.db'}")
    monkeypatch.setattr(app_db, "engine", engine)
    yield engine


class TestReviewCardsRebuild:
    """Phase 1, task 1: the review_cards rebuild keeps every card and log (R1.2, R1.3)."""

    @pytest.mark.asyncio
    async def test_cards_and_logs_survive_unchanged(self, temp_engine):
        seeded = await _build_old_schema_db(temp_engine)
        before = await _snapshot(temp_engine)
        assert len(before["cards"]) == seeded["card_count"]

        await app_db.create_tables()

        after = await _snapshot(temp_engine)
        assert after["cards"] == before["cards"]
        assert after["logs"] == before["logs"]

    @pytest.mark.asyncio
    async def test_new_columns_are_backfilled(self, temp_engine):
        await _build_old_schema_db(temp_engine)

        await app_db.create_tables()

        async with temp_engine.connect() as conn:
            rows = (await conn.exec_driver_sql(
                "SELECT rc.kind, rc.step, rc.created_at, b.created_at "
                "FROM review_cards rc JOIN blunders b ON b.id = rc.blunder_id"
            )).fetchall()
            total = await conn.exec_driver_sql("SELECT COUNT(*) FROM review_cards")
            total = total.scalar()
        assert len(rows) == total  # every card still joins to its blunder
        for kind, step, card_created, blunder_created in rows:
            assert kind == "blunder"
            assert step == 0
            assert card_created == blunder_created

    @pytest.mark.asyncio
    async def test_logs_still_join_to_their_cards(self, temp_engine):
        await _build_old_schema_db(temp_engine)

        await app_db.create_tables()

        async with temp_engine.connect() as conn:
            orphans = (await conn.exec_driver_sql(
                "SELECT COUNT(*) FROM review_logs rl "
                "LEFT JOIN review_cards rc ON rc.id = rl.card_id WHERE rc.id IS NULL"
            )).scalar()
            fk_problems = (await conn.exec_driver_sql("PRAGMA foreign_key_check")).fetchall()
        assert orphans == 0
        assert fk_problems == []

    @pytest.mark.asyncio
    async def test_schema_after_rebuild(self, temp_engine):
        """blunder_id is nullable, indexes are back, and the unique key includes step."""
        seeded = await _build_old_schema_db(temp_engine)

        await app_db.create_tables()

        async with temp_engine.connect() as conn:
            columns = {
                row[1]: row for row in
                (await conn.exec_driver_sql("PRAGMA table_info(review_cards)")).fetchall()
            }
            indexes = {
                row[1] for row in
                (await conn.exec_driver_sql("PRAGMA index_list(review_cards)")).fetchall()
            }
        assert columns["blunder_id"][3] == 0  # notnull flag off
        assert {"kind", "step", "created_at"} <= columns.keys()
        assert {"ix_review_cards_user_id", "ix_review_cards_due_at"} <= indexes

        # Unique key is now (blunder_id, card_type, step): a second step of
        # the same card is allowed, a duplicate step is not.
        blunder_id = seeded["blunder_ids"][0]
        insert = (
            "INSERT INTO review_cards (user_id, kind, blunder_id, card_type, step, ease, "
            "interval_days, repetitions, lapses, due_at, created_at) "
            "VALUES (1, 'blunder', ?, 'avoid', ?, 2.5, 0, 0, 0, "
            "'2026-11-23 00:00:00.000000', '2026-11-23 00:00:00.000000')"
        )
        async with temp_engine.begin() as conn:
            await conn.exec_driver_sql(insert, (blunder_id, 1))
        with pytest.raises(Exception, match="UNIQUE"):
            async with temp_engine.begin() as conn:
                await conn.exec_driver_sql(insert, (blunder_id, 1))

    @pytest.mark.asyncio
    async def test_running_twice_changes_nothing(self, temp_engine):
        await _build_old_schema_db(temp_engine)

        await app_db.create_tables()
        once = await _full_card_rows(temp_engine)
        await app_db.create_tables()
        twice = await _full_card_rows(temp_engine)

        assert twice == once

    @pytest.mark.asyncio
    async def test_migrated_schema_matches_a_fresh_one(self, temp_engine, tmp_path):
        """A rebuilt table must look exactly like one create_all makes from the model."""
        await _build_old_schema_db(temp_engine)
        await app_db.create_tables()

        fresh = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
        async with fresh.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async def describe(engine):
            async with engine.connect() as conn:
                cols = (await conn.exec_driver_sql("PRAGMA table_info(review_cards)")).fetchall()
                idx = (await conn.exec_driver_sql("PRAGMA index_list(review_cards)")).fetchall()
                fks = (await conn.exec_driver_sql("PRAGMA foreign_key_list(review_cards)")).fetchall()
            # index_list's seq column depends on creation order; compare the rest
            return cols, sorted(row[1:] for row in idx), sorted(fks)

        assert await describe(temp_engine) == await describe(fresh)
        await fresh.dispose()


class TestAnalyzedGameMoveDataColumns:
    """Phase 1, task 6: move-data columns are added to existing databases (R3.4)."""

    @pytest.mark.asyncio
    async def test_columns_added_and_old_games_left_empty(self, temp_engine):
        await _build_old_schema_db(temp_engine)
        # The harness builds analyzed_games from the current model; take the
        # new columns back off to get the pre-task-6 table.
        async with temp_engine.begin() as conn:
            for col in ("positions", "user_moves", "speed", "initial_eval_cp"):
                await conn.exec_driver_sql(f"ALTER TABLE analyzed_games DROP COLUMN {col}")

        await app_db.create_tables()
        await app_db.create_tables()  # and again: must be a no-op

        async with temp_engine.connect() as conn:
            cols = {
                row[1] for row in
                (await conn.exec_driver_sql("PRAGMA table_info(analyzed_games)")).fetchall()
            }
            rows = (await conn.exec_driver_sql(
                "SELECT positions, user_moves, speed, initial_eval_cp FROM analyzed_games"
            )).fetchall()
        assert {"positions", "user_moves", "speed", "initial_eval_cp"} <= cols
        assert rows and all(row == (None, None, None, None) for row in rows)


# The three tables Phase 2 alters, exactly as production has them after
# Phase 1 (compiled from the models on main at b4711d9). The new Phase 2
# tables don't exist yet in that schema.
PHASE1_ALTERED_DDL = [
    """
    CREATE TABLE users (
        id INTEGER NOT NULL,
        username VARCHAR(50) NOT NULL,
        password_hash VARCHAR(128) NOT NULL,
        email VARCHAR(255),
        lichess_username VARCHAR(50),
        display_name VARCHAR(100),
        avatar_url VARCHAR(500),
        sync_game_types VARCHAR(200) NOT NULL,
        sync_days_back INTEGER NOT NULL,
        max_new_per_day INTEGER NOT NULL,
        last_synced_at DATETIME,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id)
    )
    """,
    "CREATE UNIQUE INDEX ix_users_email ON users (email)",
    "CREATE UNIQUE INDEX ix_users_username ON users (username)",
    """
    CREATE TABLE analyzed_games (
        id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        lichess_game_id VARCHAR(16) NOT NULL,
        lichess_username VARCHAR(50) NOT NULL,
        user_color VARCHAR(5) NOT NULL,
        white_name VARCHAR(50) NOT NULL,
        black_name VARCHAR(50) NOT NULL,
        result VARCHAR(8) NOT NULL,
        played_at DATETIME NOT NULL,
        analyzed_at DATETIME NOT NULL,
        positions TEXT,
        user_moves INTEGER,
        speed VARCHAR(16),
        initial_eval_cp INTEGER,
        PRIMARY KEY (id),
        UNIQUE (user_id, lichess_game_id),
        FOREIGN KEY(user_id) REFERENCES users (id)
    )
    """,
    "CREATE INDEX ix_analyzed_games_lichess_game_id ON analyzed_games (lichess_game_id)",
    "CREATE INDEX ix_analyzed_games_user_id ON analyzed_games (user_id)",
    """
    CREATE TABLE review_cards (
        id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        kind VARCHAR(20) DEFAULT 'blunder' NOT NULL,
        blunder_id INTEGER,
        card_type VARCHAR(20) NOT NULL,
        step INTEGER DEFAULT '0' NOT NULL,
        ease FLOAT NOT NULL,
        interval_days FLOAT NOT NULL,
        repetitions INTEGER NOT NULL,
        lapses INTEGER NOT NULL,
        due_at DATETIME NOT NULL,
        last_reviewed_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (blunder_id, card_type, step),
        FOREIGN KEY(user_id) REFERENCES users (id),
        FOREIGN KEY(blunder_id) REFERENCES blunders (id)
    )
    """,
    "CREATE INDEX ix_review_cards_due_at ON review_cards (due_at)",
    "CREATE INDEX ix_review_cards_user_id ON review_cards (user_id)",
]

PHASE2_NEW_TABLES = {
    "repertoire_moves", "opening_explanations", "explanation_reports",
    "repertoire_deviations", "opening_names", "theory_moves",
    "explanation_batches", "explorer_cache",
}
PHASE2_ALTERED = ("users", "analyzed_games", "review_cards")


async def _build_phase1_schema_db(engine) -> None:
    """A database as it is after Phase 1: no Phase 2 tables or columns."""
    keep = [
        t for name, t in Base.metadata.tables.items()
        if name not in PHASE2_NEW_TABLES and name not in PHASE2_ALTERED
    ]
    async with engine.begin() as conn:
        for stmt in PHASE1_ALTERED_DDL:
            await conn.exec_driver_sql(stmt)
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=keep))
        await conn.exec_driver_sql(
            "INSERT INTO users (username, password_hash, sync_game_types, sync_days_back,"
            " max_new_per_day, created_at) VALUES ('old', 'x', '[]', 7, 10, '2026-01-01')"
        )


async def _describe(engine, table: str):
    """Columns, explicitly created indexes (with their columns), and foreign keys."""
    async with engine.connect() as conn:
        cols = (await conn.exec_driver_sql(f"PRAGMA table_info({table})")).fetchall()
        index_rows = (await conn.exec_driver_sql(f"PRAGMA index_list({table})")).fetchall()
        indexes = {}
        for _, name, unique, origin, _partial in index_rows:
            if origin != "c":
                continue  # inline UNIQUE constraints: compared via the DDL, not by name
            index_cols = (await conn.exec_driver_sql(f"PRAGMA index_info({name})")).fetchall()
            indexes[name] = (unique, [c[2] for c in index_cols])
        fks = (await conn.exec_driver_sql(f"PRAGMA foreign_key_list({table})")).fetchall()
    return cols, indexes, sorted(fk[2:] for fk in fks)


class TestPhase2Schema:
    """Phase 2, task 3: the Phase 2 tables and columns reach existing databases."""

    @pytest.mark.asyncio
    async def test_new_tables_and_columns_added(self, temp_engine):
        await _build_phase1_schema_db(temp_engine)

        await app_db.create_tables()

        async with temp_engine.connect() as conn:
            tables = {
                r[0] for r in (await conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )).fetchall()
            }
            owner = (await conn.exec_driver_sql("SELECT is_owner FROM users")).scalar()
        assert PHASE2_NEW_TABLES <= tables
        cols = {t: {c[1] for c in (await _describe(temp_engine, t))[0]} for t in PHASE2_ALTERED}
        assert {"repertoire_move_id", "repertoire_deviation_id"} <= cols["review_cards"]
        assert {"repertoire_exit_ply", "repertoire_checked_at", "repertoire_plies"} <= cols["analyzed_games"]
        assert "is_owner" in cols["users"]
        assert owner == 0  # existing users are not owners

    @pytest.mark.asyncio
    async def test_migrated_tables_match_fresh_ones(self, temp_engine, tmp_path):
        await _build_phase1_schema_db(temp_engine)
        await app_db.create_tables()

        fresh = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh2.db'}")
        async with fresh.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
            for table in PHASE2_ALTERED:
                assert await _describe(temp_engine, table) == await _describe(fresh, table), table
        finally:
            await fresh.dispose()

    @pytest.mark.asyncio
    async def test_running_twice_changes_nothing(self, temp_engine):
        await _build_phase1_schema_db(temp_engine)

        async def schema():
            async with temp_engine.connect() as conn:
                return (await conn.exec_driver_sql(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                )).fetchall()

        await app_db.create_tables()
        once = await schema()
        await app_db.create_tables()
        assert await schema() == once

    @pytest.mark.asyncio
    async def test_opening_cards_unique_per_position_and_step(self, temp_engine):
        await _build_phase1_schema_db(temp_engine)
        await app_db.create_tables()

        insert = (
            "INSERT INTO review_cards (user_id, kind, repertoire_move_id, card_type, step, ease,"
            " interval_days, repetitions, lapses, due_at)"
            " VALUES (1, 'opening', 5, 'play', 0, 2.5, 0, 0, 0, '2026-11-23')"
        )
        async with temp_engine.begin() as conn:
            await conn.exec_driver_sql(insert)
        with pytest.raises(Exception, match="UNIQUE"):
            async with temp_engine.begin() as conn:
                await conn.exec_driver_sql(insert)
