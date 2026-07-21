"""
One-off: compare blunder detection between the old time-limit config and
the new depth-limit config on recent real games.
"""
import asyncio
import json
import time
from datetime import datetime, timezone

import chess.engine
import httpx

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.models.game import LichessGame, Player  # noqa: E402
from app.services import analysis_service  # noqa: E402
from app.services.analysis_service import detect_blunders, _open_engine  # noqa: E402


async def fetch_games(n: int) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://lichess.org/api/games/user/Igitqara",
            params={"max": n, "pgnInJson": "true"},
            headers={"Accept": "application/x-ndjson"},
        )
    games = []
    for line in r.text.strip().split("\n"):
        d = json.loads(line)
        players = d["players"]
        games.append(LichessGame(
            id=d["id"],
            created_at=datetime.now(timezone.utc),
            white=Player(id=players["white"]["user"]["id"], name="", color="white"),
            black=Player(id=players["black"]["user"]["id"], name="", color="black"),
            moves=d["moves"].split(),
        ))
    return games


async def run(games, limit_factory):
    analysis_service._analysis_limit = limit_factory
    engine = await _open_engine()
    results = {}
    t0 = time.perf_counter()
    try:
        for g in games:
            color = chess.WHITE if g.white.id == "igitqara" else chess.BLACK
            found = await detect_blunders(engine, g, color)
            results[g.id] = sorted((b.ply, b.move_played_san) for b in found)
    finally:
        await engine.quit()
    return results, time.perf_counter() - t0


async def main():
    games = await fetch_games(4)
    print(f"comparing on {len(games)} games ({sum(len(g.moves) for g in games)} plies total)\n")

    old, t_old = await run(games, lambda: chess.engine.Limit(time=0.08))
    new, t_new = await run(games, lambda: chess.engine.Limit(depth=12, time=0.5))

    agree = 0
    for gid in old:
        match = "SAME" if old[gid] == new[gid] else "DIFF"
        agree += old[gid] == new[gid]
        print(f"{gid}: old={old[gid]} new={new[gid]} -> {match}")
    print(f"\nold config: {t_old:.1f}s   new config: {t_new:.1f}s   "
          f"speedup: {t_old/t_new:.1f}x   agreement: {agree}/{len(old)} games")


asyncio.run(main())
