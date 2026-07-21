"""
Benchmark blunder analysis: time-limit vs depth-limit engine settings.

Usage: uv run python scripts/bench_analysis.py
"""
import asyncio
import time

import chess
import chess.engine
import httpx

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.models.game import LichessGame, Player  # noqa: E402
from app.services.analysis_service import win_prob, MATE_SCORE  # noqa: E402
from app.config import settings  # noqa: E402


async def fetch_game() -> LichessGame:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://lichess.org/api/games/user/Igitqara",
            params={"max": 1, "pgnInJson": "true"},
            headers={"Accept": "application/x-ndjson"},
        )
        import json
        data = json.loads(r.text.strip().split("\n")[0])
        return LichessGame(
            id=data["id"],
            created_at=__import__("datetime").datetime.now(),
            white=Player(id="w", name="w", color="white"),
            black=Player(id="b", name="b", color="black"),
            moves=data["moves"].split(),
        )


async def analyze(game: LichessGame, limit: chess.engine.Limit, options: dict) -> tuple:
    transport, engine = await chess.engine.popen_uci(settings.STOCKFISH_PATH)
    try:
        if options:
            await engine.configure(options)
        board = chess.Board()
        t0 = time.perf_counter()
        flags = 0
        info = await engine.analyse(board, limit)
        prev = info["score"].pov(chess.WHITE).score(mate_score=MATE_SCORE)
        for san in game.moves:
            board.push_san(san)
            if board.is_game_over():
                break
            info = await engine.analyse(board, limit)
            cp = info["score"].pov(chess.WHITE).score(mate_score=MATE_SCORE)
            if abs(win_prob(prev) - win_prob(cp)) >= settings.BLUNDER_WINPROB_THRESHOLD:
                flags += 1
            prev = cp
        dt = time.perf_counter() - t0
        return dt, len(game.moves), flags
    finally:
        await engine.quit()


async def main():
    game = await fetch_game()
    print(f"game {game.id}: {len(game.moves)} plies\n")

    configs = [
        ("time=0.08s (current)", chess.engine.Limit(time=0.08), {}),
        ("depth=8", chess.engine.Limit(depth=8, time=0.5), {"Threads": 1, "Hash": 64}),
        ("depth=10", chess.engine.Limit(depth=10, time=0.5), {"Threads": 1, "Hash": 64}),
        ("depth=12", chess.engine.Limit(depth=12, time=0.5), {"Threads": 1, "Hash": 64}),
    ]
    for name, limit, opts in configs:
        dt, plies, flags = await analyze(game, limit, opts)
        print(f"{name:24s} {dt:6.2f}s total  {dt/plies*1000:6.1f} ms/pos  swings flagged: {flags}")


asyncio.run(main())
