"""
Benchmark the opening-explanation model (Phase 2, task 8; design D4).

Runs up to 50 positions from a player's suggested repertoire through
claude-opus-5-5, claude-sonnet-5-5 and claude-haiku-5-5 in one Message
Batch (half price), and writes a blind comparison sheet to rate. Nothing
is stored in the database.

Needs ANTHROPIC_API_KEY in the environment or the repo's .env, Stockfish on
PATH, and a database whose games have stored moves.

    # 1. See what it would send and cost; calls nothing:
    uv run python scripts/benchmark_explanations.py run --user <username> --db <path to .db>

    # 2. Send it (costs real money, about $1 for 50 positions):
    uv run python scripts/benchmark_explanations.py run --user <username> --db <path> --yes

    # 3. Rate the answers in <out>/sheet.md, then tally them per model:
    uv run python scripts/benchmark_explanations.py score <out>
"""
import argparse
import asyncio
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _use_database(path: str | None) -> None:
    # Must happen before app.config is imported, which reads DATABASE_URL.
    if path:
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{pathlib.Path(path).resolve()}"


async def _run(args) -> int:
    from sqlalchemy import select

    from app.config import settings
    from app.db import async_session
    from app.models.db_models import User
    from app.services.analysis_service import _open_engine
    from app.services.explanation_benchmark import (
        MODELS,
        benchmark_requests,
        collect_inputs,
        estimate_cost,
        run_batch,
        write_sheet,
    )
    from app.services.explanation_service import AnthropicClaude

    async with async_session() as db:
        user = await db.scalar(select(User).where(User.username == args.user))
        if user is None:
            print(f"No user {args.user!r} in {settings.DATABASE_URL}")
            return 1
        engine = await _open_engine()
        try:
            inputs = await collect_inputs(db, user.id, args.n, engine)
        finally:
            await engine.quit()

    if not inputs:
        print("No suggested positions: the user needs games with stored moves (3+ games per line).")
        return 1
    estimate = estimate_cost(len(inputs))
    print(f"{len(inputs)} positions x {len(MODELS)} models = {len(inputs) * len(MODELS)} requests")
    for model, usd in estimate.items():
        print(f"  {model}: about ${usd:.2f} (batch price, rough estimate)")
    print(f"  total: about ${sum(estimate.values()):.2f}")

    if not args.yes:
        print("\nDry run: nothing sent. Add --yes to submit the batch.")
        return 0
    if not settings.ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY isn't set (environment or .env).")
        return 1

    claude = AnthropicClaude(settings.ANTHROPIC_API_KEY)
    print("\nSubmitted; waiting for the batch (usually minutes, at most 24 hours)...")
    results = await run_batch(claude, benchmark_requests(inputs), poll_seconds=30)
    out = pathlib.Path(args.out)
    sheet = write_sheet(inputs, results, out)
    costs = json.loads((out / "costs.json").read_text())
    print(f"\nWrote {sheet} (rate it), {out / 'key.json'} (don't peek), {out / 'costs.json'}")
    for model, c in costs.items():
        print(f"  {model}: {c['usable']}/{len(inputs)} usable answers, ${c['cost_usd']:.3f}")
    return 0


def _score(args) -> int:
    from app.services.explanation_benchmark import RATINGS, score

    out = pathlib.Path(args.out)
    tally = score(out)
    costs = json.loads((out / "costs.json").read_text())
    print(f"{'model':<20}" + "".join(f"{r:>9}" for r in (*RATINGS, "unrated")) + f"{'cost':>9}")
    for model, counts in tally.items():
        print(f"{model:<20}" + "".join(f"{counts.get(r, 0):>9}" for r in (*RATINGS, "unrated"))
              + f"{'$' + format(costs[model]['cost_usd'], '.3f'):>9}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="build (and with --yes, send) the benchmark batch")
    run.add_argument("--user", required=True, help="Blundr username whose repertoire to sample")
    run.add_argument("--db", help="SQLite database file (default: DATABASE_URL from .env)")
    run.add_argument("--n", type=int, default=50, help="positions to sample (default 50)")
    run.add_argument("--out", default="benchmark-explanations", help="folder for the sheet")
    run.add_argument("--yes", action="store_true", help="actually submit the batch (costs money)")
    sc = sub.add_parser("score", help="tally the ratings in a rated sheet")
    sc.add_argument("out", help="the folder the run wrote")
    args = parser.parse_args()

    if args.command == "run":
        _use_database(args.db)
        return asyncio.run(_run(args))
    return _score(args)


if __name__ == "__main__":
    sys.exit(main())
