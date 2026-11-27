"""Preparing the next gaps in the background (repertoire builder, task 4)."""
import asyncio
from types import SimpleNamespace

import chess
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models.db_models import RepertoireMove, User
from app.services import builder_prefetch, explorer_service
from app.services.explorer_service import ExplorerMove
from app.services.lichess_auth import encrypt_token

START = chess.Board().epd()


def key_after(*sans):
    b = chess.Board()
    for s in sans:
        b.push_san(s)
    return b.epd()


def mv(uci, games):
    return ExplorerMove(uci=uci, san=uci, games=games, white=games, draws=0, black=0)


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'prefetch.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _app():
    return SimpleNamespace(state=SimpleNamespace(builder_jobs={}, oauth_http=None,
                                                 masters_book=None))


@pytest.mark.asyncio
async def test_one_job_per_user(monkeypatch):
    async def forever(*args, **kwargs):
        await asyncio.sleep(60)
    monkeypatch.setattr(builder_prefetch, "_run", forever)
    app = _app()
    first = builder_prefetch.schedule_prefetch(app, 1, "white", 20)
    other = builder_prefetch.schedule_prefetch(app, 2, "white", 20)
    second = builder_prefetch.schedule_prefetch(app, 1, "white", 20)
    await asyncio.sleep(0)
    assert first.cancelled() or first.cancelling()
    assert not other.done() and not second.done()
    assert app.state.builder_jobs[1] is second
    for t in (other, second):
        t.cancel()


@pytest.mark.asyncio
async def test_goal_change_replaces_job(monkeypatch):
    goals = []

    async def record(app, user_id, color, goal, session_factory):
        goals.append(goal)
        await asyncio.sleep(60)
    monkeypatch.setattr(builder_prefetch, "_run", record)
    app = _app()
    old = builder_prefetch.schedule_prefetch(app, 1, "white", 20)
    await asyncio.sleep(0)
    new = builder_prefetch.schedule_prefetch(app, 1, "white", 50)
    await asyncio.sleep(0)
    assert old.cancelled() or old.cancelling()
    assert goals == [20, 50]
    new.cancel()


class CachingExplorer:
    """Fetches each key once, then answers from its 'cache'."""

    def __init__(self, data):
        self.data, self.fetched, self.cache = data, [], {}

    async def __call__(self, db, http, *, user_id, token, key, band):
        if key in self.cache:
            return self.cache[key]
        if token is None:
            return None
        self.fetched.append(key)
        self.cache[key] = self.data.get(key, [])
        return self.cache[key]


async def _user_with_e4(factory):
    async with factory() as db:
        user = User(username="pf", sync_game_types='["blitz"]',
                    lichess_token_enc=encrypt_token("lio_pf"))
        db.add(user)
        await db.flush()
        db.add(RepertoireMove(user_id=user.id, color="white", position_key=START,
                              move_uci="e2e4", move_san="e4", source="manual"))
        await db.commit()
        return user.id


@pytest.mark.asyncio
async def test_prefetch_fills_cache(factory, monkeypatch):
    fake = CachingExplorer({key_after("e4"): [mv("e7e5", 60), mv("c7c5", 40)]})
    monkeypatch.setattr(explorer_service, "explorer_moves", fake)
    engine_keys = []

    async def engine(db, key, **kwargs):
        engine_keys.append((key, kwargs.get("wait")))
        return []
    monkeypatch.setattr(builder_prefetch, "get_engine_lines", engine)
    uid = await _user_with_e4(factory)
    task = builder_prefetch.schedule_prefetch(_app(), uid, "white", 20, session_factory=factory)
    await task
    gaps = [key_after("e4", "e5"), key_after("e4", "c5")]
    assert key_after("e4") in fake.fetched
    assert set(gaps) <= set(fake.fetched)  # level data for each gap
    assert [k for k, _ in engine_keys] == gaps
    # The next step finds everything it needs in the cache
    before = list(fake.fetched)
    await builder_prefetch.schedule_prefetch(_app(), uid, "white", 20, session_factory=factory)
    assert fake.fetched == before


@pytest.mark.asyncio
async def test_engine_not_preempted(factory, monkeypatch):
    monkeypatch.setattr(explorer_service, "explorer_moves",
                        CachingExplorer({key_after("e4"): [mv("e7e5", 100)]}))
    waits = []

    async def engine(db, key, **kwargs):
        waits.append(kwargs.get("wait"))
        return None  # busy
    monkeypatch.setattr(builder_prefetch, "get_engine_lines", engine)
    uid = await _user_with_e4(factory)
    await asyncio.wait_for(
        builder_prefetch.schedule_prefetch(_app(), uid, "white", 20, session_factory=factory), 5)
    assert waits == [0]
