"""The Lichess explorer client and its per-band cache (repertoire builder, task 1). No network."""
import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs

import chess
import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models.db_models import ExplorerCache, User
from app.services import explorer_service
from app.services.explorer_service import band_for, explorer_moves, shares

START = chess.Board().epd()
CASTLE_KEY = chess.Board("r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4").epd()


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'explorer.db'}")
    async with engine.connect() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")  # as the app's database
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def clean_backoff():
    explorer_service._backoff_until.clear()
    yield
    explorer_service._backoff_until.clear()


class FakeExplorer:
    def __init__(self, status=200, moves=None):
        self.status = status
        self.moves = moves or [
            {"uci": "e2e4", "san": "e4", "white": 50, "draws": 10, "black": 40},
            {"uci": "d2d4", "san": "d4", "white": 20, "draws": 5, "black": 15},
        ]
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status)
        return httpx.Response(200, json={"white": 70, "draws": 15, "black": 55, "moves": self.moves})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.handler(r)))


def _user(ratings=None, types=("rapid", "blitz")):
    return User(username="u", sync_game_types=json.dumps(list(types)),
                lichess_ratings=json.dumps(ratings) if ratings is not None else None)


def test_band_for():
    assert band_for(_user({"blitz": 1650, "rapid": 1720, "puzzle": 2400})) == "blitz,rapid:1600,1800"
    assert band_for(_user({"blitz": 2600})) == "blitz,rapid:2500"
    assert band_for(_user(None)) == "blitz,rapid:1400,1600"
    assert band_for(_user({"bullet": 900}, types=("bullet",))) == "bullet:0,1000"


@pytest.mark.asyncio
async def test_fetch_and_cache(db):
    fake = FakeExplorer()
    async with fake.client() as http:
        first = await explorer_moves(db, http, user_id=1, token="lio_x", key=START,
                                     band="blitz,rapid:1600,1800")
        second = await explorer_moves(db, http, user_id=1, token="lio_x", key=START,
                                      band="blitz,rapid:1600,1800")
    assert len(fake.requests) == 1
    req = fake.requests[0]
    assert req.url.host == "explorer.lichess.ovh" and req.url.path == "/lichess"
    q = {k: v[0] for k, v in parse_qs(req.url.query.decode()).items()}
    assert q["fen"] == START + " 0 1"
    assert q["speeds"] == "blitz,rapid"
    assert q["ratings"] == "1600,1800"
    assert req.headers["Authorization"] == "Bearer lio_x"
    assert [m.uci for m in first] == ["e2e4", "d2d4"] == [m.uci for m in second]
    assert first[0].games == 100
    assert shares(first) == [("e2e4", 100), ("d2d4", 40)]


@pytest.mark.asyncio
async def test_cache_expires_after_30_days(db):
    db.add(ExplorerCache(position_key=START, rating_band="b:1600", body="[]",
                         fetched_at=datetime.utcnow() - timedelta(days=31)))
    await db.commit()
    fake = FakeExplorer()
    async with fake.client() as http:
        moves = await explorer_moves(db, http, user_id=1, token="t", key=START, band="b:1600")
    assert len(fake.requests) == 1 and len(moves) == 2


@pytest.mark.asyncio
async def test_castling_uci_normalized(db):
    fake = FakeExplorer(moves=[{"uci": "e1h1", "san": "O-O", "white": 9, "draws": 1, "black": 5}])
    async with fake.client() as http:
        moves = await explorer_moves(db, http, user_id=1, token="t", key=CASTLE_KEY, band="b:1600")
    assert [m.uci for m in moves] == ["e1g1"]


@pytest.mark.asyncio
async def test_no_token_returns_none_without_request(db):
    fake = FakeExplorer()
    async with fake.client() as http:
        assert await explorer_moves(db, http, user_id=1, token=None, key=START, band="b:1600") is None
    assert fake.requests == []


@pytest.mark.asyncio
async def test_429_backs_off_and_caches_nothing(db, monkeypatch):
    now = [datetime(2026, 11, 27, 12, 0, 0)]
    monkeypatch.setattr(explorer_service, "_now", lambda: now[0])
    fake = FakeExplorer(status=429)
    async with fake.client() as http:
        assert await explorer_moves(db, http, user_id=1, token="t", key=START, band="b:1600") is None
        now[0] += timedelta(seconds=10)
        fake.status = 200
        assert await explorer_moves(db, http, user_id=1, token="t", key=START, band="b:1600") is None
        assert len(fake.requests) == 1
        now[0] += timedelta(seconds=51)
        assert await explorer_moves(db, http, user_id=1, token="t", key=START, band="b:1600")
        assert len(fake.requests) == 2
    assert await db.get(ExplorerCache, (START, "b:1600")) is not None


@pytest.mark.asyncio
async def test_error_not_cached(db):
    fake = FakeExplorer(status=500)
    async with fake.client() as http:
        assert await explorer_moves(db, http, user_id=1, token="t", key=START, band="b:1600") is None
    assert await db.get(ExplorerCache, (START, "b:1600")) is None


@pytest.mark.asyncio
async def test_concurrent_fetches_of_one_position_write_it_once(tmp_path):
    import asyncio

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}")
    async with engine.connect() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")  # as the app's database
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    class Slow(FakeExplorer):
        def handler(self, request):
            return super().handler(request)

    fake = Slow()

    async def slow_handler(request):
        await asyncio.sleep(0.05)
        return fake.handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(slow_handler))
    try:
        async with factory() as a, factory() as b:
            first, second = await asyncio.gather(
                explorer_moves(a, http, user_id=7, token="t", key=START, band="b:1600"),
                explorer_moves(b, http, user_id=7, token="t", key=START, band="b:1600"),
            )
        assert first and second and [m.uci for m in first] == [m.uci for m in second]
        assert len(fake.requests) == 1  # the second waited for the lock, then read the cache
    finally:
        await http.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_timeout_bounds_the_wait_not_the_save(db):
    import asyncio

    async def slow_handler(request):
        await asyncio.sleep(0.3)
        return httpx.Response(200, json={"moves": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow_handler)) as http:
        assert await explorer_moves(db, http, user_id=8, token="t", key=START, band="b:1600",
                                    timeout=0.05) is None
    assert await db.get(ExplorerCache, (START, "b:1600")) is None


@pytest.mark.asyncio
async def test_a_failed_cache_write_leaves_the_callers_session_alone(db, monkeypatch):
    user = User(username="keep", sync_game_types='["blitz"]')
    db.add(user)
    await db.commit()

    async def broken(*args, **kwargs):
        raise explorer_service.SQLAlchemyError("disk full")
    monkeypatch.setattr(explorer_service, "_save", broken)
    fake = FakeExplorer()
    async with fake.client() as http:
        moves = await explorer_moves(db, http, user_id=9, token="t", key=START, band="b:1600")
    assert moves and moves[0].uci == "e2e4"  # the answer still comes back
    assert user.username == "keep"  # and the caller's objects are still loaded
