"""
Phase 1, task 6: keep each game's move data from the analysis (R3).

analyze_game() returns the blunders plus one entry per move: the move, the
engine's eval after it (analyzed user's POV) and the engine's best move in
the position before it. All of that is already computed by the analysis
loop, so collecting it must not add a single engine call.
"""
import json
from datetime import datetime

import chess
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models.db_models import AnalyzedGame
from app.models.game import LichessGame, Player
from app.services import analysis_service
from app.services.analysis_service import MATE_SCORE, AnalysisJob, analyze_game
from tests.test_blundr_features import _StubEngine


def _game(moves, game_id="g1", speed="blitz"):
    return LichessGame(
        id=game_id,
        created_at=datetime(2026, 11, 1, 12, 0),
        white=Player(id="tester", name="Tester", color="white"),
        black=Player(id="opp", name="Opp", color="black"),
        moves=moves,
        speed=speed,
    )


def _mv(uci):
    return chess.Move.from_uci(uci)


class TestAnalyzeGame:

    @pytest.mark.asyncio
    async def test_one_entry_per_move_with_eval_and_best_move(self):
        game = _game(["e4", "e5", "Nf3", "Nc6"])
        # One response per position: the start, then after each move.
        responses = [
            (20, [_mv("e2e4")]),
            (35, [_mv("c7c5")]),
            (30, [_mv("g1f3")]),
            (40, [_mv("b8c6")]),
            (38, [_mv("f1b5")]),
        ]
        engine = _StubEngine(responses)

        result = await analyze_game(engine, game, chess.WHITE)

        assert engine._i == len(responses)  # no extra engine calls
        assert result.initial_eval_cp == 20
        assert result.user_moves == 2
        assert [p["san"] for p in result.positions] == ["e4", "e5", "Nf3", "Nc6"]
        assert [p["uci"] for p in result.positions] == ["e2e4", "e7e5", "g1f3", "b8c6"]
        assert [p["eval_cp"] for p in result.positions] == [35, 30, 40, 38]
        # best_uci is the engine's pick in the position BEFORE each move
        assert [p["best_uci"] for p in result.positions] == ["e2e4", "c7c5", "g1f3", "b8c6"]

    @pytest.mark.asyncio
    async def test_moves_replay_legally(self):
        game = _game(["d4", "d5", "c4", "e6", "Nc3"])
        engine = _StubEngine([(0, [_mv("d2d4")])] * 6)

        result = await analyze_game(engine, game, chess.WHITE)

        board = chess.Board()
        for p in result.positions:
            move = chess.Move.from_uci(p["uci"])
            assert move in board.legal_moves
            assert board.san(move) == p["san"]
            board.push(move)
        assert len(result.positions) == len(game.moves)

    @pytest.mark.asyncio
    async def test_user_moves_counts_only_the_analyzed_side(self):
        game = _game(["e4", "e5", "Nf3"])
        engine = _StubEngine([(0, [_mv("e2e4")])] * 4, pov=chess.BLACK)

        result = await analyze_game(engine, game, chess.BLACK)

        assert result.user_moves == 1  # black played only e5

    @pytest.mark.asyncio
    async def test_unreplayable_move_keeps_the_moves_before_it(self):
        game = _game(["e4", "e5", "Zz9", "Nc6"])
        responses = [(0, [_mv("e2e4")])] * 3  # start + the two good moves
        engine = _StubEngine(responses)

        result = await analyze_game(engine, game, chess.WHITE)

        assert engine._i == len(responses)
        assert [p["san"] for p in result.positions] == ["e4", "e5"]
        assert result.user_moves == 1

    @pytest.mark.asyncio
    async def test_game_ending_move_is_scored_without_the_engine(self):
        game = _game(["f3", "e5", "g4", "Qh4#"])
        # Start + 3 moves; the mated position is scored directly. A blunder
        # (g4) is flagged here too, which walks its PV lines — give those
        # empty PVs so they cost no calls either.
        responses = [
            (0, [_mv("e2e4")]),
            (-20, [_mv("e7e5")]),
            (-30, [_mv("e2e4")]),
            (-9999, []),
        ]
        engine = _StubEngine(responses)

        result = await analyze_game(engine, game, chess.WHITE)

        assert len(result.positions) == 4
        assert result.positions[-1]["san"] == "Qh4#"
        assert result.positions[-1]["eval_cp"] == -MATE_SCORE
        assert [b.ply for b in result.blunders] == [3]


class _QuittableStub(_StubEngine):
    async def quit(self):
        pass


class TestRunAnalysisJobSavesMoveData:

    @pytest.mark.asyncio
    async def test_positions_and_counts_are_stored(self, tmp_path, monkeypatch):
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'job.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(analysis_service, "async_session", session_factory)

        stub = _QuittableStub([(0, [_mv("e2e4")])] * 4)

        async def open_stub():
            return stub
        monkeypatch.setattr(analysis_service, "_open_engine", open_stub)

        game = _game(["e4", "e5", "Nf3"], game_id="jobgame", speed="rapid")
        job = AnalysisJob(games_total=1)
        await analysis_service.run_analysis_job(job, user_id=1, lichess_username="tester", games=[game])

        assert job.state == "done"
        async with session_factory() as s:
            row = await s.scalar(select(AnalyzedGame).where(AnalyzedGame.lichess_game_id == "jobgame"))
        assert row.speed == "rapid"
        assert row.user_moves == 2
        assert row.initial_eval_cp == 0
        stored = json.loads(row.positions)
        assert [p["san"] for p in stored] == ["e4", "e5", "Nf3"]
        assert set(stored[0]) == {"san", "uci", "eval_cp", "best_uci"}
        await engine.dispose()


class TestLichessSpeed:

    def test_speed_is_parsed(self):
        from app.services.lichess_client import LichessClient
        client = LichessClient.__new__(LichessClient)  # parser needs no HTTP state
        game = client._parse_game_data({
            "id": "abc", "createdAt": 0, "speed": "bullet",
            "players": {"white": {"user": {"id": "a", "name": "A"}},
                        "black": {"user": {"id": "b", "name": "B"}}},
        })
        assert game.speed == "bullet"
