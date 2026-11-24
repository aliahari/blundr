"""
Benchmark the explanation model before settling on one
(specs/features/02-openings, D4 "Choosing the model", task 8).

Takes up to 50 positions from a player's suggested repertoire (both colours,
mixed depths and openings), asks each candidate model for an explanation in
one Message Batch, and writes a blind comparison sheet: per position the
answers in shuffled order with the model names hidden, a line to rate each,
and the cost per model. The owner rates the sheet; `score()` then tallies
the ratings per model. Nothing is stored in opening_explanations.

The CLI is scripts/benchmark_explanations.py.
"""
import asyncio
import json
import random
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .explanation_service import (
    ClaudeClient,
    ExplanationInput,
    build_input,
    build_params,
    load_book,
    load_user_games,
    parse_explanation,
)
from .repertoire_service import Suggestion, build_tree, reachable, suggest

MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
# List prices in $ per million tokens (input, output); batches cost half.
LIST_PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
BATCH_DISCOUNT = 0.5
# Rough size of one explanation, for the estimate before anything is sent.
EST_INPUT_TOKENS, EST_OUTPUT_TOKENS = 1500, 1000
LABELS = "ABCDEFGH"
RATINGS = ("correct", "useful", "wrong")

DEPTH_BUCKETS = ((0, 5), (6, 13), (14, 29))  # plies played before the position


@dataclass
class Picked:
    color: str
    suggestion: Suggestion


def select_positions(by_color: dict[str, list[Suggestion]], n: int = 50, seed: int = 1) -> list[Picked]:
    """
    Up to `n` positions mixing colours, depths and openings: take from each
    (colour, depth bucket) group in turn, preferring an opening not picked
    yet, so the sample isn't all from one main line.
    """
    rng = random.Random(seed)
    groups: list[list[Picked]] = []
    for color in sorted(by_color):
        for low, high in DEPTH_BUCKETS:
            group = [Picked(color, s) for s in by_color[color] if low <= s.ply <= high]
            rng.shuffle(group)
            if group:
                groups.append(group)

    picked: list[Picked] = []
    seen_openings: set[str] = set()
    while len(picked) < n and any(groups):
        for group in groups:
            if not group or len(picked) >= n:
                continue
            fresh = [p for p in group if _opening(p) not in seen_openings]
            choice = fresh[0] if fresh else group[0]
            group.remove(choice)
            picked.append(choice)
            seen_openings.add(_opening(choice))
    return picked


def _opening(p: Picked) -> str:
    return p.suggestion.opening[1] if p.suggestion.opening else "(unnamed)"


async def collect_inputs(db, user_id: int, n: int, engine) -> list[ExplanationInput]:
    """ExplanationInputs for up to `n` positions of the player's suggested repertoire."""
    theory, names = await load_book(db)
    by_color: dict[str, list[Suggestion]] = {}
    context = {}
    for color in ("white", "black"):
        tree = build_tree(await load_user_games(db, user_id, color), color, theory=theory)
        suggestions = suggest(tree, color, theory=theory, names=names)
        moves = {s.position_key: s.move_uci for s in suggestions}
        by_color[color] = suggestions
        context[color] = (moves, reachable(color, moves), tree)

    inputs = []
    for p in select_positions(by_color, n):
        moves, parents, tree = context[p.color]
        s = p.suggestion
        item = await build_input(s.position_key, s.move_uci, s.move_san, p.color,
                                 moves, parents, tree, theory, names, engine)
        if item is not None:
            inputs.append(item)
    return inputs


def benchmark_requests(inputs: list[ExplanationInput], models: list[str] = MODELS) -> list[dict]:
    """One batch request per (model, position). custom_id: '<model index>-<position index>'."""
    return [
        {"custom_id": f"m{m}-p{i:03d}", "params": build_params(item, model)}
        for m, model in enumerate(models)
        for i, item in enumerate(inputs)
    ]


def estimate_cost(n_positions: int, models: list[str] = MODELS) -> dict[str, float]:
    """$ per model at batch prices, from the rough per-explanation size."""
    out = {}
    for model in models:
        price_in, price_out = LIST_PRICES[model]
        per = (EST_INPUT_TOKENS * price_in + EST_OUTPUT_TOKENS * price_out) / 1e6 * BATCH_DISCOUNT
        out[model] = round(per * n_positions, 4)
    return out


async def run_batch(claude: ClaudeClient, requests: list[dict], poll_seconds: float = 30,
                    sleep=asyncio.sleep) -> list[tuple[str, str, Any]]:
    """Submit one batch and wait for it to end; returns its results."""
    batch_id = await claude.create_batch(requests)
    while await claude.batch_status(batch_id) != "ended":
        await sleep(poll_seconds)
    return await claude.batch_results(batch_id)


def actual_cost(results, models: list[str] = MODELS) -> dict[str, dict]:
    """Tokens and $ per model from each result's usage, at batch prices."""
    totals = {m: {"input_tokens": 0, "output_tokens": 0, "answers": 0, "usable": 0} for m in models}
    for custom_id, result_type, message in results:
        model = models[int(custom_id.split("-")[0][1:])]
        if result_type != "succeeded" or message is None:
            continue
        t = totals[model]
        t["answers"] += 1
        t["input_tokens"] += message.usage.input_tokens
        t["output_tokens"] += message.usage.output_tokens
        if parse_explanation(message) is not None:
            t["usable"] += 1
    for model, t in totals.items():
        price_in, price_out = LIST_PRICES[model]
        t["cost_usd"] = round(
            (t["input_tokens"] * price_in + t["output_tokens"] * price_out) / 1e6 * BATCH_DISCOUNT, 4
        )
    return totals


def write_sheet(inputs, results, out_dir: Path, models: list[str] = MODELS, seed: int = 1) -> Path:
    """
    sheet.md (blind: shuffled labels per position, a rating line per answer),
    key.json (which label is which model) and costs.json, in out_dir.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    answers: dict[tuple[int, int], dict | None] = {}
    for custom_id, result_type, message in results:
        m, p = custom_id.split("-")
        parsed = parse_explanation(message) if result_type == "succeeded" and message else None
        answers[(int(m[1:]), int(p[1:]))] = parsed

    rng = random.Random(seed)
    key: dict[str, dict[str, str]] = {}
    lines = [
        "# Explanation benchmark: blind comparison",
        "",
        "For each answer, fill in its rating line with one word: `correct` (right, but not",
        "much help), `useful` (right and helpful), or `wrong` (a mistake about the position).",
        "Then run `uv run python scripts/benchmark_explanations.py score <this folder>`.",
        "",
    ]
    for i, item in enumerate(inputs):
        order = list(range(len(models)))
        rng.shuffle(order)
        key[str(i)] = {LABELS[slot]: models[m] for slot, m in enumerate(order)}
        opening = f"{item.opening[1]} ({item.opening[0]})" if item.opening else "unnamed"
        moves = " ".join(item.line_san) or "(start)"
        lines += [
            f"## {i + 1}. {opening}: {item.move_san} ({item.color})",
            "",
            f"Moves: {moves}  ",
            f"FEN: `{item.fen}`  ",
            f"Theory: {item.theory_line or 'off-book'}",
            "",
        ]
        for slot, m in enumerate(order):
            answer = answers.get((m, i))
            lines.append(f"**{LABELS[slot]}.**")
            if answer is None:
                lines.append("_(no usable answer)_")
            else:
                lines.append(f"- Plan: {answer['plan']}")
                lines.append(f"- Why {item.move_san}: {answer['why_this_move']}")
                if answer["pawn_structure"]:
                    lines.append(f"- Pawns: {answer['pawn_structure']}")
            lines += [f"Rating {LABELS[slot]}: ", ""]
    sheet = out_dir / "sheet.md"
    sheet.write_text("\n".join(lines))
    (out_dir / "key.json").write_text(json.dumps(key, indent=2))
    (out_dir / "costs.json").write_text(json.dumps(actual_cost(results, models), indent=2))
    return sheet


def score(out_dir: Path) -> dict[str, Counter]:
    """Tally the ratings in out_dir/sheet.md per model, using key.json."""
    key = json.loads((out_dir / "key.json").read_text())
    tally: dict[str, Counter] = {}
    position = None
    for line in (out_dir / "sheet.md").read_text().splitlines():
        header = re.match(r"^## (\d+)\.", line)
        if header:
            position = str(int(header.group(1)) - 1)
            continue
        rating = re.match(r"^Rating ([A-H]):\s*(\w*)", line)
        if rating and position is not None:
            model = key[position][rating.group(1)]
            word = rating.group(2).lower()
            tally.setdefault(model, Counter())[word if word in RATINGS else "unrated"] += 1
    return tally
