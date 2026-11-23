"""
Phase 2, task 2: giving games analyzed before Phase 1 their moves (R1).

The Lichess client is faked and the database is a temp file swapped in for
backfill_service.async_session, so nothing reaches Lichess or the shared
test database.
"""
import json
from datetime import datetime

import chess
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models.db_models import AnalyzedGame, Blunder, User
from app.models.game import LichessGame, Player
from app.services import backfill_service
from app.services.analysis_service import AnalysisJob
from app.services.backfill_service import backfill_moves, positions_from_moves
from app.services.progress_service import GameMoves, blunder_rate_by_week, week_start


class TestPositionsFromMoves:

    def test_replays_moves_with_empty_evals(self):
        positions, user_moves = positions_from_moves(["e4", "e5", "Nf3"], "white")

        assert [p["san"] for p in positions] == ["e4", "e5", "Nf3"]
        assert [p["uci"] for p in positions] == ["e2e4", "e7e5", "g1f3"]
        assert all(p["eval_cp"] is None and p["best_uci"] is None for p in positions)
        assert user_moves == 2

    def test_counts_only_the_users_moves(self):
        _, user_moves = positions_from_moves(["e4", "e5", "Nf3", "Nc6", "Bb5"], "black")
        assert user_moves == 2

    def test_stops_at_a_move_that_cannot_be_replayed(self):
        positions, user_moves = positions_from_moves(["e4", "e5", "Zz9", "Nc6"], "white")
        assert [p["san"] for p in positions] == ["e4", "e5"]
        assert user_moves == 1


def _lichess_game(game_id: str, moves: list[str], speed: str = "rapid") -> LichessGame:
    return LichessGame(
        id=game_id,
        created_at=datetime(2026, 9, 1, 12, 0),
        white=Player(id="tester", name="Tester", color="white"),
        black=Player(id="opp", name="Opp", color="black"),
        moves=moves,
        speed=speed,
    )


class _FakeLichess:
    """Stands in for LichessClient.export_games_by_ids; records each call."""

    def __init__(self, known: dict[str, LichessGame], batch_size: int = 300):
        self.known = known
        self.calls: list[list[str]] = []
        self.EXPORT_BATCH_SIZE = batch_size

    async def export_games_by_ids(self, ids):
        self.calls.append(list(ids))
        return [self.known[i] for i in ids if i in self.known]


@pytest_asyncio.fixture
async def temp_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'backfill.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(backfill_service, "async_session", session_factory)
    yield session_factory
    await engine.dispose()


async def _seed(session_factory, games: list[tuple[str, str, bool]]) -> int:
    """games: (lichess id, user colour, already has positions). Returns user id."""
    async with session_factory() as s:
        user = User(username="filler", password_hash="x")
        s.add(user)
        await s.flush()
        for lichess_id, color, has_positions in games:
            s.add(AnalyzedGame(
                user_id=user.id, lichess_game_id=lichess_id, lichess_username="tester",
                user_color=color, white_name="W", black_name="B", result="1-0",
                played_at=datetime(2026, 9, 1, 12, 0),
                positions='[{"san": "d4", "uci": "d2d4", "eval_cp": 10, "best_uci": "d2d4"}]'
                if has_positions else None,
                user_moves=1 if has_positions else None,
            ))
        await s.commit()
        return user.id


async def _rows(session_factory, user_id):
    async with session_factory() as s:
        rows = (await s.scalars(
            select(AnalyzedGame).where(AnalyzedGame.user_id == user_id).order_by(AnalyzedGame.id)
        )).all()
    return {r.lichess_game_id: r for r in rows}


class TestBackfillMoves:

    @pytest.mark.asyncio
    async def test_fills_old_games_and_leaves_the_rest(self, temp_db):
        user_id = await _seed(temp_db, [
            ("old0001", "white", False),
            ("old0002", "black", False),
            ("gone0003", "white", False),   # Lichess no longer has it
            ("new0004", "white", True),     # analyzed since Phase 1: untouched
        ])
        fake = _FakeLichess({
            "old0001": _lichess_game("old0001", ["e4", "e5", "Nf3"]),
            "old0002": _lichess_game("old0002", ["d4", "Nf6", "c4", "e6"], speed="blitz"),
        })
        job = AnalysisJob()

        filled = await backfill_moves(job, user_id, fake)

        assert filled == 2
        assert fake.calls == [["old0001", "old0002", "gone0003"]]  # never re-fetches new0004
        rows = await _rows(temp_db, user_id)
        assert rows["old0001"].user_moves == 2
        assert rows["old0001"].speed == "rapid"
        assert rows["old0002"].user_moves == 2
        assert rows["old0002"].speed == "blitz"
        assert rows["gone0003"].positions is None
        assert rows["new0004"].positions.startswith('[{"san": "d4"')  # unchanged
        assert (job.backfill_total, job.backfill_done) == (3, 3)

    @pytest.mark.asyncio
    async def test_second_run_fetches_only_games_still_missing(self, temp_db):
        user_id = await _seed(temp_db, [("old0001", "white", False), ("gone0003", "white", False)])
        fake = _FakeLichess({"old0001": _lichess_game("old0001", ["e4"])})

        await backfill_moves(AnalysisJob(), user_id, fake)
        await backfill_moves(AnalysisJob(), user_id, fake)

        # The second run only asks again for the game Lichess didn't have.
        assert fake.calls == [["old0001", "gone0003"], ["gone0003"]]

    @pytest.mark.asyncio
    async def test_nothing_to_fill_makes_no_call(self, temp_db):
        user_id = await _seed(temp_db, [("new0004", "white", True)])
        fake = _FakeLichess({})

        assert await backfill_moves(AnalysisJob(), user_id, fake) == 0
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_fetches_in_batches_and_saves_each_batch(self, temp_db):
        ids = [f"old{i:04d}" for i in range(5)]
        user_id = await _seed(temp_db, [(i, "white", False) for i in ids])
        fake = _FakeLichess({i: _lichess_game(i, ["e4", "e5"]) for i in ids}, batch_size=2)

        assert await backfill_moves(AnalysisJob(), user_id, fake) == 5
        assert [len(c) for c in fake.calls] == [2, 2, 1]

    @pytest.mark.asyncio
    async def test_filled_games_count_in_the_blunder_rate(self, temp_db):
        user_id = await _seed(temp_db, [("old0001", "white", False)])
        async with temp_db() as s:
            game = (await s.scalars(select(AnalyzedGame))).one()
            s.add(Blunder(
                user_id=user_id, game_id=game.id, ply=3, fen_before=chess.STARTING_FEN,
                move_played_uci="g1f3", move_played_san="Nf3", best_move_uci="d2d4",
                best_move_san="d4", eval_before_cp=0, eval_after_cp=-300, win_prob_drop=30.0,
            ))
            await s.commit()
        fake = _FakeLichess({"old0001": _lichess_game("old0001", ["e4", "e5", "Nf3", "Nc6"])})

        await backfill_moves(AnalysisJob(), user_id, fake)

        row = (await _rows(temp_db, user_id))["old0001"]
        point = blunder_rate_by_week(
            [GameMoves(row.played_at, row.user_color, json.loads(row.positions), {3})],
            first_week=week_start(row.played_at.date()), weeks=1, phase=None,
        )[0]
        assert (point["user_moves"], point["blunders"]) == (2, 1)


class _FakeGameService:
    """Replaces get_game_service for the sync endpoint: no recent games, one old game to fill."""

    def __init__(self, lichess):
        self.lichess_client = lichess


class _SyncLichess(_FakeLichess):
    async def get_user_games(self, **kwargs):
        from app.utils.exceptions import NoGamesFoundError
        raise NoGamesFoundError(kwargs["username"], kwargs["since"], kwargs["until"])


class TestSyncRunsBackfill:

    def test_sync_fills_old_games_and_reports_progress(self):
        import sqlite3
        import time

        from app.main import app
        from app.routes.games import get_game_service
        from tests.conftest import client
        from tests.test_blundr_features import _TEST_DB_PATH, _register

        _register("backfilluser")
        token = client.post(
            "/api/auth/login", json={"username": "backfilluser", "password": "password123"}
        ).json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        uid = client.get("/api/auth/me", headers=h).json()["id"]

        con = sqlite3.connect(str(_TEST_DB_PATH))
        con.execute(
            "INSERT INTO analyzed_games (user_id, lichess_game_id, lichess_username, user_color,"
            " white_name, black_name, result, played_at, analyzed_at)"
            " VALUES (?, 'bfold001', 'someone', 'white', 'W', 'B', '1-0', '2026-08-01', '2026-08-01')",
            (uid,),
        )
        con.commit()

        fake = _SyncLichess({"bfold001": _lichess_game("bfold001", ["e4", "e5", "Nf3"])})
        app.dependency_overrides[get_game_service] = lambda: _FakeGameService(fake)
        try:
            resp = client.post("/api/analysis/sync?force=true", headers=h)
            assert resp.status_code == 202
            for _ in range(50):  # the backfill runs in the app's background task
                status = client.get("/api/analysis/status", headers=h).json()
                if status["backfill_total"] and status["backfill_done"] == status["backfill_total"]:
                    break
                time.sleep(0.05)
        finally:
            app.dependency_overrides.pop(get_game_service, None)

        assert (status["backfill_total"], status["backfill_done"]) == (1, 1)
        row = con.execute(
            "SELECT positions, user_moves FROM analyzed_games WHERE lichess_game_id = 'bfold001'"
        ).fetchone()
        con.close()
        assert json.loads(row[0])[2]["san"] == "Nf3"
        assert row[1] == 2
        assert fake.calls == [["bfold001"]]


class TestSyncInProgress:

    def test_running_analysis_or_running_backfill_count_as_in_progress(self):
        from app.routes.analysis import _sync_in_progress

        class _Task:
            def __init__(self, done):
                self._done = done

            def done(self):
                return self._done

        assert not _sync_in_progress(None)
        assert _sync_in_progress(AnalysisJob(state="running"))
        # Analysis finished, backfill still going in the same task
        assert _sync_in_progress(AnalysisJob(state="done", task=_Task(done=False)))
        assert not _sync_in_progress(AnalysisJob(state="done", task=_Task(done=True)))
        assert not _sync_in_progress(AnalysisJob(state="done"))
