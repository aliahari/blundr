"""
Phase 2, task 7: opening explanations written by Claude, through the
Message Batches API (design D4; R4.1, R4.2, R4.6).

No real API calls: the Claude adapter is a fake, the engine is a stub, and
the database is a temp file swapped in for explanation_service.async_session.
"""
import json
import random
from types import SimpleNamespace

import chess
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models.db_models import (
    ExplanationBatch,
    OpeningExplanation,
    OpeningName,
    RepertoireMove,
    TheoryMove,
    User,
)
from app.services import explanation_service
from app.services.explanation_service import (
    PROMPT_VERSION,
    ExplanationInput,
    build_params,
    custom_id_for,
    parse_explanation,
    run_explanation_cycle,
)

START = chess.Board().epd()


def _key(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.epd()


def _message(payload=None, stop_reason="end_turn", text=None):
    """A stand-in for an SDK Message: content blocks plus stop_reason."""
    body = text if text is not None else json.dumps(payload)
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=body)],
    )


GOOD = {"plan": "Fight for the centre.", "why_this_move": "It frees the bishop.", "pawn_structure": ""}


class FakeClaude:
    """The adapter explanation_service talks to; records every call."""

    def __init__(self):
        self.batches: dict[str, dict] = {}   # batch id -> {"requests": [...], "status": str}
        self.results: dict[str, list] = {}   # batch id -> [(custom_id, type, message)]
        self.direct_calls: list[dict] = []
        self.direct_reply = _message(GOOD)

    async def create_batch(self, requests):
        batch_id = f"msgbatch_{len(self.batches) + 1}"
        self.batches[batch_id] = {"requests": list(requests), "status": "in_progress"}
        return batch_id

    async def batch_status(self, batch_id):
        return self.batches[batch_id]["status"]

    async def batch_results(self, batch_id):
        return list(self.results.get(batch_id, []))

    async def create_with_fallback(self, params):
        self.direct_calls.append(params)
        return self.direct_reply

    def finish(self, batch_id, outcome=lambda cid: ("succeeded", _message(GOOD))):
        """End a batch; outcome(custom_id) -> (result type, message or None). Shuffled order."""
        reqs = self.batches[batch_id]["requests"]
        out = [(r["custom_id"], *outcome(r["custom_id"])) for r in reqs]
        random.Random(7).shuffle(out)
        self.results[batch_id] = out
        self.batches[batch_id]["status"] = "ended"


class StubEngine:
    """Answers analyse() with a fixed eval and a PV starting from a legal move."""

    async def analyse(self, board, limit):
        import chess.engine
        move = next(iter(board.legal_moves))
        return {"score": chess.engine.PovScore(chess.engine.Cp(35), board.turn), "pv": [move]}

    async def quit(self):
        pass


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'expl.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(explanation_service, "async_session", factory)
    async with factory() as s:
        s.add_all([
            OpeningName(position_key=_key("e4", "e5"), eco="C20", name="King's Pawn Game"),
            TheoryMove(position_key=START, move_uci="e2e4", line_name="King's Pawn Game"),
            TheoryMove(position_key=_key("e4", "e5"), move_uci="g1f3", line_name="King's Knight Opening"),
        ])
        await s.commit()
    yield factory
    await engine.dispose()


async def _accept(factory, username, rows):
    """rows: (position_key, uci, san, parent_key) for a White repertoire."""
    async with factory() as s:
        user = User(username=username, password_hash="x")
        s.add(user)
        await s.flush()
        for key, uci, san, parent in rows:
            s.add(RepertoireMove(user_id=user.id, color="white", position_key=key,
                                 move_uci=uci, move_san=san, parent_key=parent, source="games"))
        await s.commit()


LINE = [
    (START, "e2e4", "e4", None),
    (_key("e4", "e5"), "g1f3", "Nf3", START),
]


async def _explanations(factory):
    async with factory() as s:
        return (await s.scalars(select(OpeningExplanation).order_by(OpeningExplanation.id))).all()


async def _batches(factory):
    async with factory() as s:
        return (await s.scalars(select(ExplanationBatch).order_by(ExplanationBatch.id))).all()


class TestRequest:

    def test_params_shape_and_grounding(self):
        item = ExplanationInput(
            position_key=_key("e4", "e5"), move_uci="g1f3", move_san="Nf3", color="white",
            line_san=["e4", "e5"], fen=chess.Board(_key("e4", "e5") + " 0 1").fen(),
            opening=("C20", "King's Pawn Game"), theory_line="King's Knight Opening",
            eval_before_cp=30, eval_after_cp=28, best_san="Nf3", pv_san=["Nf3", "Nc6", "Bb5"],
            opponent_replies=[("Nc6", 5), ("d6", 1)],
        )
        params = build_params(item, "claude-opus-5-5")

        assert params["model"] == "claude-opus-5-5"
        assert params["output_config"]["effort"] == "medium"
        schema = params["output_config"]["format"]["schema"]
        assert params["output_config"]["format"]["type"] == "json_schema"
        assert set(schema["required"]) == {"plan", "why_this_move", "pawn_structure"}
        assert "thinking" not in params and "fallbacks" not in params
        assert isinstance(params["system"], str) and "club player" in params["system"]
        user_text = params["messages"][0]["content"]
        for expected in ["1. e4 e5", "Nf3", "King's Pawn Game", "King's Knight Opening",
                         "+0.30", "Nc6 (5 games)", item.fen]:
            assert expected in user_text, expected

    def test_custom_id_is_stable_and_api_safe(self):
        import re
        cid = custom_id_for(START, "e2e4")
        assert cid == custom_id_for(START, "e2e4")
        assert cid != custom_id_for(START, "d2d4")
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", cid)


class TestParse:

    def test_valid_result(self):
        assert parse_explanation(_message(GOOD)) == {**GOOD, "pawn_structure": None}

    @pytest.mark.parametrize("message", [
        _message(GOOD, stop_reason="refusal"),
        _message(text="not json"),
        _message({"plan": "x"}),                                     # missing fields
        _message({**GOOD, "plan": ""}),                              # empty plan
        _message({**GOOD, "why_this_move": 3}),                      # wrong type
    ])
    def test_unusable_results(self, message):
        assert parse_explanation(message) is None


class TestCycle:

    @pytest.mark.asyncio
    async def test_submits_one_request_per_position_and_move_and_stores_results(self, db):
        await _accept(db, "alice", LINE)
        await _accept(db, "bob", LINE)            # same positions: no extra requests
        claude = FakeClaude()

        await run_explanation_cycle(claude, StubEngine())

        (batch_id,) = claude.batches
        requests = claude.batches[batch_id]["requests"]
        assert len(requests) == 2
        assert {r["custom_id"] for r in requests} == {
            custom_id_for(START, "e2e4"), custom_id_for(_key("e4", "e5"), "g1f3"),
        }
        assert (await _batches(db))[0].status != "ended"

        # Nothing new while the batch is open.
        await run_explanation_cycle(claude, StubEngine())
        assert len(claude.batches) == 1

        claude.finish(batch_id)
        await run_explanation_cycle(claude, StubEngine())

        stored = await _explanations(db)
        assert {(e.position_key, e.move_uci) for e in stored} == {(START, "e2e4"), (_key("e4", "e5"), "g1f3")}
        assert all(e.source == "claude" and e.model == "claude-opus-5-5"
                   and e.prompt_version == PROMPT_VERSION for e in stored)
        assert json.loads(stored[0].body)["plan"] == GOOD["plan"]
        assert (await _batches(db))[0].status == "ended"
        assert len(claude.batches) == 1           # nothing left to explain

    @pytest.mark.asyncio
    async def test_errored_and_expired_items_are_resubmitted(self, db):
        await _accept(db, "carol", LINE)
        claude = FakeClaude()
        await run_explanation_cycle(claude, StubEngine())
        first = next(iter(claude.batches))
        failed = custom_id_for(START, "e2e4")
        claude.finish(first, lambda cid: ("errored", None) if cid == failed else ("succeeded", _message(GOOD)))

        await run_explanation_cycle(claude, StubEngine())

        assert len(await _explanations(db)) == 1
        second = [b for b in claude.batches if b != first]
        assert [r["custom_id"] for r in claude.batches[second[0]]["requests"]] == [failed]

    @pytest.mark.asyncio
    async def test_refusal_gets_one_direct_retry(self, db):
        await _accept(db, "dave", LINE[:1])
        claude = FakeClaude()
        await run_explanation_cycle(claude, StubEngine())
        batch = next(iter(claude.batches))
        claude.finish(batch, lambda cid: ("succeeded", _message(GOOD, stop_reason="refusal")))

        await run_explanation_cycle(claude, StubEngine())

        assert len(claude.direct_calls) == 1
        assert len(await _explanations(db)) == 1  # the retry succeeded

    @pytest.mark.asyncio
    async def test_refused_twice_stores_nothing(self, db):
        await _accept(db, "erin", LINE[:1])
        claude = FakeClaude()
        claude.direct_reply = _message(GOOD, stop_reason="refusal")
        await run_explanation_cycle(claude, StubEngine())
        batch = next(iter(claude.batches))
        claude.finish(batch, lambda cid: ("succeeded", _message(GOOD, stop_reason="refusal")))

        await run_explanation_cycle(claude, StubEngine())

        # No explanation, but a marker so the position isn't requested again
        # (and paid for) on every later batch. The owner can still write one.
        stored = await _explanations(db)
        assert [(e.source, e.position_key) for e in stored] == [("refused", START)]
        await run_explanation_cycle(claude, StubEngine())
        assert len(claude.batches) == 1

    @pytest.mark.asyncio
    async def test_owner_text_means_no_request(self, db):
        await _accept(db, "fred", LINE[:1])
        async with db() as s:
            s.add(OpeningExplanation(position_key=START, move_uci="e2e4", source="owner",
                                     body=json.dumps(GOOD)))
            await s.commit()
        claude = FakeClaude()

        await run_explanation_cycle(claude, StubEngine())

        assert claude.batches == {}

    @pytest.mark.asyncio
    async def test_open_batch_is_collected_after_a_restart(self, db):
        await _accept(db, "gina", LINE[:1])
        claude = FakeClaude()
        await run_explanation_cycle(claude, StubEngine())
        batch = next(iter(claude.batches))
        claude.finish(batch)

        # A new process: same database and same Claude account, fresh adapter state.
        restarted = FakeClaude()
        restarted.batches, restarted.results = claude.batches, claude.results
        await run_explanation_cycle(restarted, StubEngine())

        assert len(await _explanations(db)) == 1

    @pytest.mark.asyncio
    async def test_without_a_client_nothing_happens(self, db):
        await _accept(db, "hank", LINE)
        assert await run_explanation_cycle(None, StubEngine()) == {"skipped": "no API key"}
        assert await _batches(db) == []
