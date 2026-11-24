# Repertoire Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Repertoire tab's suggestion list with an interactive graph of the user's lines
plus a Chessbook-style position panel, with master moves as theory and ranked engine moves to pick
from.

**Architecture:** The backend merges the existing game tree (`build_tree`) with the user's
repertoire, cached Lichess masters data and the dataset theory into a folded graph (a pure
function), served by two new endpoints. A background fetcher fills the masters cache; a rationed
multi-line Stockfish call, cached per position, ranks moves. The frontend draws the graph as SVG
laid out with `d3-hierarchy`, with the panel beside it.

**Tech Stack:** FastAPI, async SQLAlchemy/SQLite, python-chess, httpx; React 18 + TypeScript + Vite,
react-chessboard, chess.js, d3-hierarchy.

**Spec:** [repertoire-graph.md](repertoire-graph.md) (approved 2026-11-24). Read it first.

## Global Constraints

- Every change lands on a branch as a PR against `main` (never push to `main`); one PR per task or
  per two small tasks, each with Auto-fix and auto-merge on.
- Constants, verbatim from the spec: `ACT_MIN_GAMES = 2`, `SHOW_MIN_GAMES = 2`, `MAX_PLY = 30`
  (existing), `THEORY_MIN_MASTER_GAMES = 10`, masters cache 90 days, prefetch at most 1 request/s,
  429 → pause 60 s, other failures → retry that position after 1 hour, explorer timeout 5 s,
  engine `MultiPV = 4`, depth 20 or 1.5 s, at most 1 engine run at a time, give up waiting after 5 s.
- Masters URL: `https://explorer.lichess.ovh/masters?fen=…&moves=12&topGames=0`, Bearer
  `settings.LICHESS_TOKEN`, the Blundr User-Agent the shared `httpx.AsyncClient` already sends.
- Without `LICHESS_TOKEN`, or while the explorer fails, everything works from the dataset and the
  response says `theory_source: "dataset"`.
- Tests never call Lichess or real Stockfish for these features: fake transports and stub engines.
- New tables via `create_all` only (no new columns on existing tables). Frontend: one new dependency,
  `d3-hierarchy` (plus `@types/d3-hierarchy` as a dev dependency).
- Position keys are `board.epd()` everywhere; FEN for the explorer is `key + " 0 1"`.

## Review Focus

1. **A colour with no games** (new user, or never played Black): the graph is the root alone, the
   tab shows "Start from my games" disabled with a short note, and nothing errors. → Task 4 test
   `test_no_games_is_just_the_root`, Task 7 browser check.
2. **A bad token** (401) or a malformed explorer body: no retry storm, dataset fallback, logged at
   most once an hour. → Task 2 test `test_unauthorized_stops_the_fetcher`.
3. **A line that comes back to an earlier position** (1.Nf3 Nf6 2.Ng1 Ng8): the graph must not
   loop or recurse forever. → Task 4 test `test_a_line_returning_to_the_start_doesn't_loop`.
4. **A long history** (2,000 games): the default payload stays small because of folding. → Task 4
   test `test_rare_moves_fold_away`.
5. **The engine busy** with a sync's analysis: the panel still answers, without evaluations. →
   Task 3 test `test_busy_engine_returns_none`.

---

### Task 1: Masters data: client, cache and the theory rule

**Files:**
- Modify: `app/services/lichess_client.py` (new method, no change to `_make_request`)
- Create: `app/services/masters_service.py`
- Test: `tests/test_masters.py`

**Interfaces:**
- Produces:
  - `LichessClient.masters(fen: str) -> dict` — the explorer's JSON body. Raises
    `MastersUnavailable` (new, in `lichess_client.py`) when `settings.LICHESS_TOKEN` is empty,
    `RateLimitExceededError` on 429, `LichessAPIError` on any other failure or timeout.
  - `masters_service.MasterMove` dataclass: `uci: str, san: str, games: int, white: int, draws: int, black: int`.
  - `masters_service.parse_masters(body: dict) -> list[MasterMove]` (ignores malformed entries).
  - `async masters_service.cached_masters(db, keys: Iterable[str]) -> dict[str, list[MasterMove]]`
    — only fresh rows (`rating_band = "masters"`, `fetched_at` within 90 days).
  - `async masters_service.store_masters(db, key: str, body: dict) -> None` (upsert; caller commits).
  - `masters_service.is_theory(key: str, uci: str, masters: list[MasterMove] | None, dataset: Mapping[tuple[str, str], str], masters_available: bool) -> bool | None`.

- [ ] **Step 1: Write the failing tests** in `tests/test_masters.py`:
  - `test_masters_sends_token_and_parses`: an `httpx.MockTransport` checks the URL host/path
    (`explorer.lichess.ovh/masters`), `fen` param, `topGames=0`, header
    `Authorization: Bearer tok`; returns `{"moves": [{"uci": "e2e4", "san": "e4", "white": 5, "draws": 3, "black": 2}]}`;
    `parse_masters` gives `[MasterMove("e2e4", "e4", 10, 5, 3, 2)]`.
  - `test_no_token_raises_unavailable` (monkeypatch `settings.LICHESS_TOKEN = ""`).
  - `test_429_and_errors` → `RateLimitExceededError`, and 500 / timeout → `LichessAPIError`.
  - `test_cache_round_trip_and_expiry`: store, read back; a row with `fetched_at` 91 days old is not returned.
  - `test_is_theory`, table-driven:
    - masters has the move with 10 games → `True`; with 9 games in a position totalling ≥ 10 → `False`;
    - position totals 7 master games and the move has 1 → `True` (thin-masters rule);
    - move missing from masters but on a dataset line → `True`;
    - masters not cached, `masters_available=True`, dataset has it → `True`; dataset lacks it → `None`;
    - masters not cached, `masters_available=False` → dataset decides (`True`/`False`).
- [ ] **Step 2: Run** `uv run pytest tests/test_masters.py -v` — expect failures (missing names).
- [ ] **Step 3: Implement** `LichessClient.masters` (own `self.client.get`, 5 s timeout, same 429/≥400
  handling as `_make_request`), `MastersUnavailable`, and `masters_service` as specified.
- [ ] **Step 4: Run** `uv run pytest tests/test_masters.py -v` — PASS.
- [ ] **Step 5: Commit** `git commit -m "Masters data: client, cache and the theory rule (repertoire graph, task 1)"`

### Task 2: The masters prefetch worker

**Files:**
- Modify: `app/services/masters_service.py`, `app/main.py` (lifespan)
- Test: `tests/test_masters.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `masters_service.MastersFetcher(client: LichessClient, session_factory, *, clock=time.monotonic)` with
  - `enqueue(keys: Iterable[str], urgent: bool = False) -> None` — skips keys queued or failed within
    the last hour; `urgent` puts them at the front;
  - `async step() -> bool` — fetches and stores one key, returns `False` when there was nothing to do
    or it is paused;
  - `async run() -> None` — loop: `step()`, then `asyncio.sleep(1.0)` (or wait for an enqueue when idle);
  - `pending(keys: Iterable[str]) -> set[str]` — which of those keys are queued;
  - `available: bool` — `False` when there's no token or the last response was 401/403.
  `main.py` creates one at startup as `app.state.masters` and runs `run()` as a task, cancelled on shutdown like the explanation worker.

- [ ] **Step 1: Write the failing tests** (fake client object; drive `step()` directly, fake clock):
  - `test_urgent_jumps_the_queue`: enqueue `a, b`, then `c` urgent → fetch order `c, a, b`; each stored.
  - `test_rate_limit_pauses_a_minute`: a 429 → `step()` returns `False` until the clock passes +60 s, then retries the same key.
  - `test_failure_retries_after_an_hour`: a 500 → key dropped; `enqueue(key)` ignored until +3600 s.
  - `test_unauthorized_stops_the_fetcher`: a 401 → `available is False`, the queue is cleared, later `enqueue` is ignored, one warning logged.
  - `test_no_token_is_unavailable`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `MastersFetcher` and the lifespan wiring.
- [ ] **Step 4: Run** `uv run pytest tests/test_masters.py -v` and the full suite `uv run pytest` — PASS.
- [ ] **Step 5: Commit** `"Prefetch masters data in the background (repertoire graph, task 2)"`, then open the PR for tasks 1–2.

### Task 3: Engine lines, rationed and cached

**Files:**
- Modify: `app/models/db_models.py` (new `EngineLine` table `engine_lines`: `position_key String(100) PK`, `depth Integer`, `body Text`, `created_at DateTime`)
- Create: `app/services/engine_lines.py`
- Test: `tests/test_engine_lines.py`

**Interfaces:**
- Produces:
  - `engine_lines.EngineMove` dataclass: `uci: str, san: str, eval_cp: int` (from the side to move, mate as ±`MATE_SCORE`).
  - `async engine_lines.get_engine_lines(db, key: str, *, open_engine=analysis_service._open_engine, wait: float = 5.0) -> list[EngineMove] | None` — cached rows first; otherwise one run under a module-level `asyncio.Semaphore(1)` with `Limit(depth=20, time=1.5)`, `multipv=4`; stores and returns up to 4 moves ranked; `None` when the semaphore isn't free within `wait`; `[]` for a finished game.

- [ ] **Step 1: Write the failing tests** (a stub engine whose `analyse(board, limit, multipv)` returns a list of infos):
  - `test_ranks_four_moves_from_side_to_move`: stub returns 4 PVs with scores; result is 4 `EngineMove`s in order with SANs; with Black to move the evals are from Black's side.
  - `test_second_call_hits_the_cache`: the stub is opened once across two calls.
  - `test_busy_engine_returns_none`: hold the semaphore, call with `wait=0.01` → `None`.
  - `test_game_over_position`: a mate key → `[]`, engine never opened.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** (quit the engine in `finally`).
- [ ] **Step 4: Run** `uv run pytest tests/test_engine_lines.py tests/test_migrations.py -v` — PASS.
- [ ] **Step 5: Commit** `"Ranked engine moves per position, cached (repertoire graph, task 3)"` and open its PR.

### Task 4: The graph (pure)

**Files:**
- Create: `app/services/repertoire_graph.py`
- Test: `tests/test_repertoire_graph.py`

**Interfaces:**
- Consumes: `repertoire_service.Node`, `Edge`, `build_tree`, `reachable`, `START_KEY`; Task 1 `is_theory`, `MasterMove`.
- Produces (all pure, no DB):
  - `GraphEdge` dataclass: `parent: str, child: str, uci: str, san: str, games: int, w: int, d: int, l: int, master_games: int | None, theory: bool | None, repertoire: bool, transposition: bool`.
  - `GraphNode` dataclass: `key: str, user_to_move: bool, games: int, ply: int, line: list[str]` (uci from the start via spanning parents), `parent: str | None, opening: tuple[str, str] | None, mark: str | None` (`"you_left_theory" | "add_answer" | "extend"`), `folded: int`, `masters_pending: bool`.
  - `Graph` dataclass: `nodes: dict[str, GraphNode], edges: list[GraphEdge], todo: list[str]` (marked node keys, most games first).
  - `build_graph(tree: dict[str, Node], color: str, repertoire: Mapping[str, str], masters: Mapping[str, list[MasterMove]], dataset: Mapping[tuple[str, str], str], names: Mapping[str, tuple[str, str]], user_deviation_keys: set[str], masters_available: bool, unfold: set[str] = frozenset(), pending: set[str] = frozenset()) -> Graph`.
- Rules (from the spec, with one builder decision):
  - Add repertoire moves missing from the game tree as edges with `games=0` (and their child nodes).
  - Show an edge when `repertoire` or `games >= SHOW_MIN_GAMES` or its parent is in `unfold`; `folded` counts the hidden ones. Nodes beyond `MAX_PLY` are left out.
  - Spanning parents: breadth-first from the root, children ordered by games (desc) then SAN; the first edge to reach a node is its tree edge, any later shown edge into it has `transposition=True`. Visited keys are never re-entered.
  - Marks at the user's turn only, at most one, checked in this order: `you_left_theory` (no repertoire move here and the most-played edge has `theory is False`, or the key is in `user_deviation_keys`); `add_answer` / `extend` (no repertoire move, `games >= ACT_MIN_GAMES`, the tree edge into it has `theory False` / `True` respectively, **and its parent is a position the repertoire leads to** — the result of a reachable user position's repertoire move). *Builder decision: the spec didn't require that last condition for `add_answer`; without it an empty repertoire marks every off-book reply in every game, which isn't a to-do the user can act on yet. Record it in `repertoire-graph.md`.*

- [ ] **Step 1: Write the failing tests** with hand-made games (reuse `_positions` from `tests/test_deviations.py`) and `build_tree`:
  - `test_counts_scores_and_theory_per_edge`.
  - `test_rare_moves_fold_away`: 50 games all different after move 2 → those edges hidden, parent `folded == 50`; `unfold={parent}` shows them.
  - `test_transposition_is_one_node_with_a_dashed_link`: 1.d4 Nf6 2.c4 e6 and 1.c4 e6 2.d4 Nf6 → one node, one incoming edge `transposition=True`.
  - `test_a_line_returning_to_the_start_doesn't_loop`: 1.Nf3 Nf6 2.Ng1 Ng8 3.e4.
  - `test_marks`: one case each for `you_left_theory` (off-book usual move; and a deviation key), `add_answer`, `extend`, and no mark below `ACT_MIN_GAMES` or with an empty repertoire.
  - `test_repertoire_move_not_in_games_is_drawn`.
  - `test_masters_pending_and_theory_unknown`: a key in `pending` → `masters_pending=True`, and an edge only masters could confirm has `theory is None`.
  - `test_no_games_is_just_the_root`.
  - `test_todo_order`: marked keys sorted by games.
- [ ] **Step 2: Run** `uv run pytest tests/test_repertoire_graph.py -v` — FAIL.
- [ ] **Step 3: Implement** `build_graph` and the dataclasses.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `"Build the repertoire graph from games, repertoire and theory (repertoire graph, task 4)"`.

### Task 5: Graph and position endpoints, and "Start from my games"

**Files:**
- Modify: `app/routes/repertoire.py`, `app/models/schemas.py`
- Test: `tests/test_repertoire_graph_api.py`

**Interfaces:**
- Consumes: Tasks 1–4; `_load_book`, `_load_games`, `_accepted_rows`, `explanation_response`, `preferred_explanation`, `suggest`, `check_games`.
- Produces (schemas mirror Task 4's dataclasses field for field, plus):
  - `GET /api/repertoire/{color}/graph?unfold=<key>&unfold=<key>` → `RepertoireGraphResponse { nodes: list[GraphNodeOut], edges: list[GraphEdgeOut], todo: list[str], theory_source: "masters" | "dataset", avg_moves_in_repertoire: float | None, empty_repertoire: bool }`. Queues the masters prefetch for every shown node (`app.state.masters.enqueue`).
  - `GET /api/repertoire/{color}/position?line=e2e4,e7e5` → `PositionPanelResponse { key, fen, line_san: list[str], user_to_move, opening, games, repertoire_move: {uci, san, explanation: ExplanationResponse | None} | None, candidates: list[CandidateRow] (user's turn), replies: list[ReplyRow] (opponent's turn), answered_pct: float | None, pick_blocked_at: list[str] | None (the uci line of the first user position on this line whose repertoire move is missing or different), masters_pending: bool, engine_busy: bool, usual_move_note: str | None }`. `usual_move_note`, at the user's turn only: when their most-played move here is off-book, "You usually play {san} here, which is off-book."; else when its `avg_drop >= WORSE_WINPROB` (the existing R2.3 rule), "You usually play {san} here; {better} is clearly better."; otherwise null.
    - `CandidateRow { uci, san, engine_rank: int | None, eval_cp: int | None, master_games: int | None, master_score: float | None, games: int, score: float | None, theory: bool | None }` — union of engine top 4, master moves with `is_theory` true, and the user's moves here; sorted by engine rank, then master games.
    - `ReplyRow { uci, san, games, share, score, master_share: float | None, theory, answer_san: str | None, in_games: bool }` — the user's replies first by games, then unmet master replies (`in_games=False`).
    - Masters missing → enqueue urgent, `masters_pending=True`. A bad `line` (illegal move) → 400.
  - `POST /api/repertoire/{color}/start` → `RepertoireChangeResponse`; 409 if the colour already has positions. Accepts every `suggest()` suggestion, using the first `theory_alternatives` move where the suggestion is off-book and one exists, then prunes and runs `check_games` as `accept` does.
- [ ] **Step 1: Write the failing tests** (shared `client`, games via `_user_with_games`, masters rows seeded with `store_masters`, `get_engine_lines` monkeypatched to return fixed moves or `None`):
  - `test_graph_shape_and_theory_source`: RUY games ×3 → nodes include the root and `1.e4`, `theory_source == "dataset"` with no token.
  - `test_graph_queues_prefetch` (fake `app.state.masters` records enqueued keys).
  - `test_position_at_users_turn_lists_candidates`: engine moves + a seeded master move + the user's own move appear once each, ranked.
  - `test_position_at_opponents_turn_lists_replies_and_answers`: replies with games/share, `answer_san` for the one with a repertoire move, an unmet master reply with `in_games False`, `answered_pct`.
  - `test_unreachable_position_says_where_to_start`.
  - `test_engine_busy_flag` and `test_bad_line_is_400`.
  - `test_usual_move_note`: the off-book wording for a user whose usual move is off-book; null when it's theory and not worse.
  - `test_start_from_my_games_prefers_theory`: a user who plays an off-book move with 3+ games where the dataset has a theory move ends with the theory move; a second call → 409.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** the routes and schemas.
- [ ] **Step 4: Run** `uv run pytest` — PASS.
- [ ] **Step 5: Commit** `"Repertoire graph and position endpoints (repertoire graph, task 5)"`, PR for tasks 3–5 if not already.

### Task 6: The graph component

**Files:**
- Modify: `frontend/package.json` (`d3-hierarchy`, `@types/d3-hierarchy`), `frontend/src/types/index.ts`, `frontend/src/services/api.ts`, `frontend/src/styles/index.css`
- Create: `frontend/src/components/repertoire/layoutGraph.ts`, `frontend/src/components/repertoire/RepertoireGraph.tsx`

**Interfaces:**
- Consumes: Task 5's JSON.
- Produces:
  - Types `GraphNode`, `GraphEdge`, `RepertoireGraph`, `PositionPanel`, `CandidateRow`, `ReplyRow` (same field names as the schemas).
  - `getRepertoireGraph(color: Color, unfold: string[]): Promise<RepertoireGraph>`, `getRepertoirePosition(color: Color, line: string[]): Promise<PositionPanel>`, `startFromGames(color: Color): Promise<RepertoireChange>`.
  - `layoutGraph(graph: RepertoireGraph): { nodes: Map<string, {x: number, y: number}>, width: number, height: number }` — `d3-hierarchy` `tree()` over spanning parents, left to right, 118 px per ply column and 30 px per row as in the mockup.
  - `<RepertoireGraph graph selectedKey onSelect(key) onUnfold(key) />` — SVG as in mockup A: pill per move with move-numbered SAN, game count and score strip; curved edges with stroke width ∝ games and colour by kind; transposition edges dashed; mark labels ("+ add your answer", "+ extend here", "theory: …"); "+N more" on folded nodes calls `onUnfold`; drag to pan, buttons to zoom (0.5×–2×); selected node highlighted and scrolled into view.
- [ ] **Step 1:** Add the dependency (`npm install d3-hierarchy && npm install -D @types/d3-hierarchy`), the types and API functions.
- [ ] **Step 2:** Implement `layoutGraph` and `RepertoireGraph`.
- [ ] **Step 3: Run** `cd frontend && npx tsc --noEmit && npm run build` — passes.
- [ ] **Step 4: Commit** `"Draw the repertoire graph (repertoire graph, task 6)"`.

### Task 7: The position panel, the new tab, and retiring the old view

**Files:**
- Create: `frontend/src/components/repertoire/PositionPanel.tsx`, `frontend/src/components/repertoire/RepertoireTab.tsx`
- Modify: `frontend/src/App.tsx` (render `RepertoireTab` instead of `RepertoireView`), `frontend/src/styles/index.css`
- Delete: `frontend/src/components/RepertoireView.tsx`; in `app/routes/repertoire.py` the `GET /{color}` and `GET /{color}/options` routes and the schemas only they use; their tests in `tests/test_repertoire_api.py` (keep the accept/set/remove/deviations tests, switching any that read `GET /{color}` to the graph endpoint)
- Modify docs: `specs/features/02-openings/tasks.md` (task 14 → "superseded by repertoire-graph-plan.md, tasks 1–2"; add a pointer to this plan), `specs/features/02-openings/repertoire-graph.md` (the Task 4 builder decision), `CLAUDE.md` (the new services, one line each)

**Interfaces:**
- Consumes: Task 6.
- Produces:
  - `<PositionPanel color panel isOwner onPick(uci) onRemove() onSelectLine(line: string[]) />` as in mockup C and the spec's panel section: breadcrumb, board, repertoire move with the existing `ExplanationPanel` (moved from `RepertoireView` into `repertoire/ExplanationPanel.tsx`), candidates table with **Pick** (hidden when `pick_blocked_at` is set, replaced by "Your repertoire doesn't reach this position yet. Start at …" linking to that line), replies table (rows select the resulting line), `answered_pct`, engine-busy and theory-source notes.
  - `<RepertoireTab isOwner />`: White/Black tabs, the to-do summary ("N replies to answer, M places to extend", "Your games stay in your repertoire for X moves on average"), "Start from my games" when `empty_repertoire` (disabled with a note when the colour has no games), graph left / panel right (stacked below 820 px), recent deviations list under the graph (each row selects its position). Polls the graph and the open position every 4 s while anything is `masters_pending`; stops otherwise. After Pick/Remove it reloads both.
- [ ] **Step 1:** Implement the panel, the tab, the App swap; remove the old view and routes.
- [ ] **Step 2: Run** `uv run pytest`, `make lint` (no new errors in changed files), `cd frontend && npx tsc --noEmit && npm run build` — all pass.
- [ ] **Step 3: Browser check** on a scratch copy of the database with a test account (as in Phase 1), with `LICHESS_TOKEN` empty and then set: the graph shows real lines; folding/unfolding; picking a candidate at the user's turn; answering a marked reply and stepping deeper; the unreachable-position link; a colour with no games; phone width (375 px) with no horizontal page scroll.
- [ ] **Step 4: Commit** `"Repertoire tab: graph and position panel replace the suggestion list (repertoire graph, task 7)"` and open the PR.

---

**Rollout** stays in the Phase 2 rollout task (15): check that `LICHESS_TOKEN` is set in the VPS
`.env` (it's already passed through `docker-compose.yml`), back up the database before the deploy
that adds `engine_lines`, and open the tab once to let the masters prefetch run.
