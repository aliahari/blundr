"""
Phase 2, task 1: fetching games by ID in bulk (POST /api/games/export/_ids).

Runs against an httpx MockTransport, so nothing reaches Lichess. Per the
Lichess API spec, the endpoint takes up to 300 comma-separated IDs as a
text/plain body and answers with NDJSON when asked for it.
"""
import json

import httpx
import pytest

from app.services import lichess_client as lichess_module
from app.services.lichess_client import LichessClient
from app.utils.exceptions import RateLimitExceededError

UA = "Blundr/test (+https://blundr.ch)"


def _game_json(game_id: str, moves: str = "e4 e5 Nf3") -> dict:
    return {
        "id": game_id,
        "createdAt": 1_790_000_000_000,
        "status": "resign",
        "winner": "white",
        "speed": "blitz",
        "moves": moves,
        "players": {
            "white": {"user": {"id": "tester", "name": "Tester"}, "rating": 1500},
            "black": {"user": {"id": "opp", "name": "Opp"}, "rating": 1510},
        },
    }


class _FakeLichess:
    """Answers bulk exports from a dict of known games; records every request."""

    def __init__(self, known: dict[str, dict], responses_before_ok: list[int] | None = None):
        self.known = known
        self.requests: list[httpx.Request] = []
        # Status codes to return (in order) before answering normally, e.g. [429].
        self.pending = list(responses_before_ok or [])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.pending:
            return httpx.Response(self.pending.pop(0), headers={"Retry-After": "7"})
        ids = request.content.decode().split(",")
        body = "\n".join(json.dumps(self.known[i]) for i in ids if i in self.known)
        return httpx.Response(200, text=body + ("\n" if body else ""))


def _client(fake: _FakeLichess) -> LichessClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), headers={"User-Agent": UA})
    return LichessClient(client=http, rate_limit_delay=0.001)


@pytest.fixture
def no_sleep(monkeypatch):
    """Record the waits instead of sleeping them."""
    waits: list[float] = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(lichess_module.asyncio, "sleep", fake_sleep)
    return waits


class TestExportGamesByIds:

    @pytest.mark.asyncio
    async def test_batches_of_300_posted_as_text(self, no_sleep):
        ids = [f"g{i:05d}" for i in range(650)]
        fake = _FakeLichess({i: _game_json(i) for i in ids})

        games = await _client(fake).export_games_by_ids(ids)

        assert [len(r.content.decode().split(",")) for r in fake.requests] == [300, 300, 50]
        first = fake.requests[0]
        assert first.method == "POST"
        assert first.url.path == "/api/games/export/_ids"
        assert first.headers["Accept"] == "application/x-ndjson"
        assert first.headers["Content-Type"].startswith("text/plain")
        assert first.headers["User-Agent"] == UA  # the custom UA must survive
        assert first.url.params["moves"] == "true"
        assert sorted(g.id for g in games) == ids

    @pytest.mark.asyncio
    async def test_parses_games_and_skips_ids_lichess_did_not_return(self, no_sleep):
        fake = _FakeLichess({"aaaa1111": _game_json("aaaa1111", "d4 d5 c4")})

        games = await _client(fake).export_games_by_ids(["aaaa1111", "gone0000"])

        assert len(games) == 1
        assert games[0].id == "aaaa1111"
        assert games[0].moves == ["d4", "d5", "c4"]
        assert games[0].speed == "blitz"

    @pytest.mark.asyncio
    async def test_duplicates_sent_once_and_empty_input_sends_nothing(self, no_sleep):
        fake = _FakeLichess({"aaaa1111": _game_json("aaaa1111")})
        client = _client(fake)

        assert await client.export_games_by_ids([]) == []
        assert fake.requests == []

        await client.export_games_by_ids(["aaaa1111", "aaaa1111"])
        assert fake.requests[0].content.decode() == "aaaa1111"

    @pytest.mark.asyncio
    async def test_bad_lines_are_skipped(self, no_sleep):
        def handler(request):
            good = json.dumps(_game_json("aaaa1111"))
            return httpx.Response(200, text=f"{good}\nnot json\n\n")
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        games = await LichessClient(client=http, rate_limit_delay=0.001).export_games_by_ids(
            ["aaaa1111", "bbbb2222"]
        )

        assert [g.id for g in games] == ["aaaa1111"]

    @pytest.mark.asyncio
    async def test_rate_limit_waits_retry_after_then_retries(self, no_sleep):
        fake = _FakeLichess({"aaaa1111": _game_json("aaaa1111")}, responses_before_ok=[429])

        games = await _client(fake).export_games_by_ids(["aaaa1111"])

        assert [g.id for g in games] == ["aaaa1111"]
        assert len(fake.requests) == 2
        assert 7 in no_sleep  # waited the Retry-After the response asked for

    @pytest.mark.asyncio
    async def test_gives_up_after_too_many_rate_limits(self, no_sleep):
        fake = _FakeLichess({}, responses_before_ok=[429] * (LichessClient.MAX_RATE_LIMIT_RETRIES + 1))

        with pytest.raises(RateLimitExceededError):
            await _client(fake).export_games_by_ids(["aaaa1111"])
        assert len(fake.requests) == LichessClient.MAX_RATE_LIMIT_RETRIES + 1
