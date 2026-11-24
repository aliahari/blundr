"""
Phase 2, task 8: the explanation model benchmark (design D4).

No real API calls: a fake Claude client, no real waiting.
"""
import json
import re
from types import SimpleNamespace

import pytest

from app.services.explanation_benchmark import (
    MODELS,
    Picked,
    actual_cost,
    benchmark_requests,
    estimate_cost,
    run_batch,
    score,
    select_positions,
    write_sheet,
)
from app.services.explanation_service import ExplanationInput
from app.services.repertoire_service import Suggestion


def _suggestion(i, ply, opening):
    return Suggestion(
        position_key=f"key{i}", ply=ply, move_uci="e2e4", move_san="e4", games=3, score=0.5,
        parent_key=None, opening=("A00", opening) if opening else None, theory_line=None,
        theory_alternatives=[], avg_drop=None, better_uci=None,
    )


def _input(i):
    return ExplanationInput(
        position_key=f"key{i}", move_uci="e2e4", move_san="e4", color="white",
        line_san=["d4", "d5"][: i % 3], fen="8/8/8/8/8/8/8/8 w - - 0 1",
        opening=("D00", "Queen's Pawn Game"), theory_line=None,
    )


def _message(plan="Fight for e5.", in_tok=1200, out_tok=800):
    body = json.dumps({"plan": plan, "why_this_move": "It prepares d4.", "pawn_structure": ""})
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=body)],
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


class TestSelect:

    def test_mixes_colours_and_depths_and_caps(self):
        by_color = {
            "white": [_suggestion(i, ply, f"W{i % 4}") for i, ply in enumerate([0, 2, 4, 8, 10, 16, 20] * 5)],
            "black": [_suggestion(100 + i, ply, f"B{i % 4}") for i, ply in enumerate([1, 3, 9, 11, 17] * 5)],
        }
        picked = select_positions(by_color, n=12)

        assert len(picked) == 12
        assert {p.color for p in picked} == {"white", "black"}
        depths = {(p.color, 0 if p.suggestion.ply <= 5 else 1 if p.suggestion.ply <= 13 else 2) for p in picked}
        assert len(depths) == 6                       # every colour x depth bucket
        assert len({p.suggestion.position_key for p in picked}) == 12
        assert select_positions(by_color, n=12) == picked   # deterministic

    def test_fewer_positions_than_asked(self):
        picked = select_positions({"white": [_suggestion(0, 0, None)]}, n=50)
        assert picked == [Picked("white", _suggestion(0, 0, None))]


class TestRequests:

    def test_one_request_per_model_and_position(self):
        requests = benchmark_requests([_input(i) for i in range(4)])

        assert len(requests) == 4 * len(MODELS)
        ids = [r["custom_id"] for r in requests]
        assert len(set(ids)) == len(ids)
        assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", c) for c in ids)
        assert {r["params"]["model"] for r in requests} == set(MODELS)

    def test_estimate_is_cheapest_for_haiku(self):
        est = estimate_cost(50)
        assert est["claude-haiku-5-5"] < est["claude-sonnet-5-5"] < est["claude-opus-5-5"]
        assert sum(est.values()) < 2  # the spec's "about $1"


class FakeClaude:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.requests = None

    async def create_batch(self, requests):
        self.requests = requests
        return "msgbatch_1"

    async def batch_status(self, batch_id):
        return self.statuses.pop(0)

    async def batch_results(self, batch_id):
        return [(r["custom_id"], "succeeded", _message(plan=f"plan for {r['custom_id']}"))
                for r in self.requests]


class TestRun:

    @pytest.mark.asyncio
    async def test_waits_until_the_batch_ends(self):
        claude = FakeClaude(["in_progress", "in_progress", "ended"])
        waits = []

        async def fake_sleep(seconds):
            waits.append(seconds)

        results = await run_batch(claude, benchmark_requests([_input(0)]), poll_seconds=5,
                                  sleep=fake_sleep)

        assert waits == [5, 5]
        assert len(results) == len(MODELS)

    def test_cost_from_usage_at_batch_prices(self):
        results = [("m0-p000", "succeeded", _message(in_tok=1_000_000, out_tok=1_000_000)),
                   ("m2-p000", "errored", None)]
        costs = actual_cost(results)
        assert costs["claude-opus-5-5"]["cost_usd"] == pytest.approx((4 + 20) / 2)
        assert costs["claude-opus-5-5"]["usable"] == 1
        assert costs["claude-haiku-5-5"]["answers"] == 0


class TestSheet:

    def test_blind_sheet_and_scoring(self, tmp_path):
        inputs = [_input(i) for i in range(3)]
        requests = benchmark_requests(inputs)
        results = [(r["custom_id"], "succeeded", _message(plan=f"plan {r['custom_id']}")) for r in requests]

        sheet = write_sheet(inputs, results, tmp_path)
        text = sheet.read_text()
        key = json.loads((tmp_path / "key.json").read_text())

        assert not any(model in text for model in MODELS)   # blind
        assert text.count("Rating ") == 3 * len(MODELS)
        # Each label shows that model's own answer.
        model_index = {m: i for i, m in enumerate(MODELS)}
        for label, model in key["0"].items():
            block = text.split("## 1.")[1].split("## 2.")[0]
            answer = block.split(f"**{label}.**")[1].split("Rating")[0]
            assert f"plan m{model_index[model]}-p000" in answer
        assert json.loads((tmp_path / "costs.json").read_text())["claude-opus-5-5"]["answers"] == 3

        # Rate: Opus useful everywhere, Haiku wrong once, the rest left blank.
        lines = text.splitlines()
        position = None
        for n, line in enumerate(lines):
            if line.startswith("## "):
                position = str(int(line[3:].split(".")[0]) - 1)
            m = re.match(r"^Rating ([A-H]): $", line)
            if m:
                model = key[position][m.group(1)]
                if model == "claude-opus-5-5":
                    lines[n] = line + "useful"
                elif model == "claude-haiku-5-5" and position == "0":
                    lines[n] = line + "wrong"
        sheet.write_text("\n".join(lines))

        tally = score(tmp_path)
        assert tally["claude-opus-5-5"]["useful"] == 3
        assert tally["claude-haiku-5-5"]["wrong"] == 1
        assert tally["claude-haiku-5-5"]["unrated"] == 2
        assert tally["claude-sonnet-5-5"]["unrated"] == 3
