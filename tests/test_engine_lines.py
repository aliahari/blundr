"""Ranked engine moves per position, rationed and cached (repertoire graph, task 3)."""
import asyncio

import chess
import chess.engine
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models.db_models import EngineLine
from app.services import engine_lines
from app.services.analysis_service import MATE_SCORE
from app.services.engine_lines import EngineMove, get_engine_lines


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'engine.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class StubEngine:
    """Returns one info per PV, scores given from White's side as (cp or None, mate or None)."""

    def __init__(self, lines: list[tuple[str, int | None, int | None]]):
        self.lines = lines
        self.calls: list[dict] = []
        self.quit_called = False

    async def analyse(self, board, limit, multipv=1):
        self.calls.append({"limit": limit, "multipv": multipv})
        infos = []
        for uci, cp, mate in self.lines[:multipv]:
            score = chess.engine.Mate(mate) if mate is not None else chess.engine.Cp(cp)
            infos.append({"score": chess.engine.PovScore(score, chess.WHITE),
                          "pv": [chess.Move.from_uci(uci)], "depth": 20})
        return infos

    async def quit(self):
        self.quit_called = True


class Opener:
    def __init__(self, engine):
        self.engine, self.opened = engine, 0

    async def __call__(self):
        self.opened += 1
        return self.engine


def key_after(*sans):
    b = chess.Board()
    for s in sans:
        b.push_san(s)
    return b.epd()


@pytest.mark.asyncio
async def test_ranks_four_moves_from_side_to_move(db):
    stub = StubEngine([("e2e4", 30, None), ("d2d4", 25, None), ("g1f3", 20, None),
                       ("c2c4", 18, None), ("a2a3", -10, None)])
    opener = Opener(stub)
    moves = await get_engine_lines(db, key_after(), open_engine=opener)
    assert moves == [EngineMove("e2e4", "e4", 30), EngineMove("d2d4", "d4", 25),
                     EngineMove("g1f3", "Nf3", 20), EngineMove("c2c4", "c4", 18)]
    assert stub.calls[0]["multipv"] == 4
    assert stub.calls[0]["limit"].depth == 20 and stub.calls[0]["limit"].time == 1.5
    assert stub.quit_called

    # Black to move: evals are from Black's side; mate is ±MATE_SCORE
    black = StubEngine([("e7e5", -30, None), ("c7c5", -40, None), ("f7f6", None, 2)])
    moves = await get_engine_lines(db, key_after("e4"), open_engine=Opener(black))
    assert moves == [EngineMove("e7e5", "e5", 30), EngineMove("c7c5", "c5", 40),
                     EngineMove("f7f6", "f6", -MATE_SCORE)]


@pytest.mark.asyncio
async def test_second_call_hits_the_cache(db):
    opener = Opener(StubEngine([("e2e4", 30, None)]))
    first = await get_engine_lines(db, key_after(), open_engine=opener)
    second = await get_engine_lines(db, key_after(), open_engine=opener)
    assert first == second == [EngineMove("e2e4", "e4", 30)]
    assert opener.opened == 1
    row = (await db.execute(select(EngineLine))).scalar_one()
    assert row.position_key == key_after() and row.depth == 20


@pytest.mark.asyncio
async def test_busy_engine_returns_none(db):
    opener = Opener(StubEngine([("e2e4", 30, None)]))
    async with engine_lines._engine_slot():
        assert await get_engine_lines(db, key_after(), open_engine=opener, wait=0.01) is None
    assert opener.opened == 0
    assert (await db.execute(select(EngineLine))).first() is None  # nothing cached


@pytest.mark.asyncio
async def test_game_over_position(db):
    opener = Opener(StubEngine([]))
    mate = key_after("f3", "e5", "g4", "Qh4")  # fool's mate
    assert await get_engine_lines(db, mate, open_engine=opener) == []
    assert opener.opened == 0


@pytest.mark.asyncio
async def test_engine_failure_returns_none_and_quits(db):
    class Broken(StubEngine):
        async def analyse(self, board, limit, multipv=1):
            raise chess.engine.EngineTerminatedError("crashed")

    broken = Broken([])
    assert await get_engine_lines(db, key_after(), open_engine=Opener(broken)) is None
    assert broken.quit_called
    assert engine_lines._engine_slot().locked() is False


@pytest.mark.asyncio
async def test_slot_is_free_again_after_waiting(db):
    opener = Opener(StubEngine([("e2e4", 30, None)]))

    async def hold():
        async with engine_lines._engine_slot():
            await asyncio.sleep(0.05)

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0)
    assert await get_engine_lines(db, key_after(), open_engine=opener, wait=1.0) == [
        EngineMove("e2e4", "e4", 30)]
    await holder


@pytest.mark.asyncio
async def test_no_wait_runs_when_the_engine_is_free(db):
    # The builder's background job asks with wait=0: run now if free, else skip
    opener = Opener(StubEngine([("e2e4", 30, None)]))
    assert await get_engine_lines(db, key_after(), open_engine=opener, wait=0) == [
        EngineMove("e2e4", "e4", 30)]
    other = Opener(StubEngine([("d2d4", 20, None)]))
    async with engine_lines._engine_slot():
        assert await get_engine_lines(db, key_after("d4"), open_engine=other, wait=0) is None
    assert other.opened == 0
