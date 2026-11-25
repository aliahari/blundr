# Repertoire Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Repertoire tab's suggestion list with an interactive graph of the user's lines
plus a Chessbook-style position panel, with master moves as theory and ranked engine moves to pick
from.

**Architecture:** The backend merges the existing game tree (`build_tree`) with the user's
repertoire, master move statistics and the dataset theory into a folded graph (a pure function),
served by two new endpoints. The master statistics are built once on the server from the 40H
database of master games, in a separate SQLite file; a rationed multi-line Stockfish call, cached
per position, ranks moves. The frontend draws the graph as SVG
laid out with `d3-hierarchy`, with the panel beside it.

**Tech Stack:** FastAPI, async SQLAlchemy/SQLite, python-chess, httpx, py7zr; React 18 + TypeScript + Vite,
react-chessboard, chess.js, d3-hierarchy.

**Spec:** [repertoire-graph.md](repertoire-graph.md) (approved 2026-11-24). Read it first.

## Global Constraints

- Every change lands on a branch as a PR against `main` (never push to `main`); one PR per task or
  per two small tasks, each with Auto-fix and auto-merge on.
- Constants, verbatim from the spec: `ACT_MIN_GAMES = 2`, `SHOW_MIN_GAMES = 2`, `MAX_PLY = 30`
  (existing), `THEORY_MIN_MASTER_GAMES = 10`, `MASTERS_MIN_POSITION_GAMES = 3` (pruning),
  engine `MultiPV = 4`, depth 20 or 1.5 s, at most 1 engine run at a time, give up waiting after 5 s.
- 40H source, pinned: `MASTERS_ARCHIVE_URL = "https://www.nk-qy.info/40h/40H-PGN-databases.7z"`,
  `MASTERS_ARCHIVE_SHA256 = "5a48b790a486792cbcf9fc3e66dcd0125a47570ebcce9d454124e19ac61cea6f"`
  (the 2026-11-03 release, 112,964,104 bytes), member `gm1981-2026.pgn` only. `MASTERS_DB_PATH`
  defaults to `masters.sqlite` beside the main database file; add it to `docker-compose.yml`.
- Until the masters file is built (or when it failed), everything works from the dataset and the
  response says `theory_source: "dataset"`.
- Tests never download 40H, call Lichess or run real Stockfish: a tiny PGN fixture, fake downloaders
  and stub engines.
- New tables via `create_all` only (no new columns on existing tables). New dependencies: `py7zr`
  (backend); `d3-hierarchy` and `@types/d3-hierarchy` (frontend, the latter as a dev dependency).
- Position keys are `board.epd()` everywhere, in the masters file too.

## Review Focus

1. **A colour with no games** (new user, or never played Black): the graph is the root alone, the
   tab shows "Start from my games" disabled with a short note, and nothing errors. → Task 4 test
   `test_no_games_is_just_the_root`, Task 7 browser check.
2. **The masters download fails or is tampered with** (site down, checksum mismatch, a truncated
   archive): the app starts and serves the dataset, logs the reason once, never uses a half-built
   file, and tries again at the next start. → Task 2 tests `test_checksum_mismatch_keeps_dataset`
   and `test_failed_build_leaves_no_file`.
3. **A line that comes back to an earlier position** (1.Nf3 Nf6 2.Ng1 Ng8): the graph must not
   loop or recurse forever. → Task 4 test `test_a_line_returning_to_the_start_doesn't_loop`.
4. **A long history** (2,000 games): the default payload stays small because of folding. → Task 4
   test `test_rare_moves_fold_away`.
5. **The engine busy** with a sync's analysis: the panel still answers, without evaluations. →
   Task 3 test `test_busy_engine_returns_none`.

---

### Task 1: Master move statistics: builder, lookup and the theory rule

**Files:**
- Create: `app/services/masters_service.py`
- Modify: `app/config.py` (`MASTERS_DB_PATH: str = ""`, empty = `masters.sqlite` beside the main database), `docker-compose.yml` (pass it through)
- Test: `tests/test_masters.py`, fixture `tests/fixtures/masters_sample.pgn` (a dozen short games written for the test, including a `1/2-1/2`, a `*` result to skip, a game with an illegal move, and `{comments}` / `$1` NAGs to ignore)

**Interfaces:**
- Produces:
  - `masters_service.MasterMove` dataclass: `uci: str, games: int, white: int, draws: int, black: int`.
  - `masters_service.build_masters_db(pgn_path: Path, out_path: Path, *, max_ply: int = 30, min_position_games: int = 3, processes: int = 2, source_sha256: str = "") -> int` — streams the PGN (only the `Result` header; movetext tokens, skipping move numbers, results, comments and NAGs), aggregates `(board.epd(), uci) → (white, draws, black)` per batch in worker processes, stages rows in `out_path` and groups them in SQLite, keeps positions with at least `min_position_games` games, writes `master_moves` (schema as in the spec, `WITHOUT ROWID`) and `meta(key, value)` with `source_sha256` and `built_at`. Returns the number of games read. Writes only to `out_path`; the caller renames it.
  - `masters_service.MastersBook(path: Path)` with `available: bool` and `moves(keys: Iterable[str]) -> dict[str, list[MasterMove]]` — synchronous, read-only `sqlite3` connection (`?mode=ro`), keys in chunks of 500, `{}` when not available. Routes call it through `asyncio.to_thread`.
  - `masters_service.is_theory(key: str, uci: str, masters: list[MasterMove] | None, dataset: Mapping[tuple[str, str], str], masters_available: bool) -> bool` — on a dataset line → `True`; else, when `masters_available`: a position totalling fewer than 10 master games → `True` if the move is in `masters`, otherwise the move needs at least 10 games; a position missing from the masters data (`None`) → `False`; when not available → `False` unless on a dataset line.

- [ ] **Step 1: Write the failing tests** in `tests/test_masters.py`:
  - `test_build_counts_results_per_position_and_move`: from the fixture, the start position's `e2e4` has exactly the white/draw/black counts written into the fixture; the `*` game and the moves after the illegal one aren't counted; `meta.source_sha256` is stored.
  - `test_build_prunes_rare_positions`: with `min_position_games=3`, a position reached by 2 fixture games is absent; with `1` it's present.
  - `test_build_stops_at_max_ply`: `max_ply=2` → no position after the second ply.
  - `test_book_lookup_and_unavailable`: `MastersBook` on the built file returns moves for known keys and nothing for unknown ones; on a missing path `available is False` and `moves()` is `{}`.
  - `test_is_theory`, table-driven: 10 games → `True`; 9 games in a position totalling ≥ 10 → `False`; a position totalling 7 with the move at 1 → `True`; a position missing → `False`, unless on a dataset line → `True`; `masters_available=False` → the dataset decides.
- [ ] **Step 2: Run** `uv run pytest tests/test_masters.py -v` — FAIL (missing names).
- [ ] **Step 3: Implement** `masters_service` as specified (the prototype measured in the design did it in about 120 lines: `multiprocessing.Pool.imap_unordered` over batches of 2,000 games, rows staged then `GROUP BY` in SQLite).
- [ ] **Step 4: Run** `uv run pytest tests/test_masters.py -v` — PASS.
- [ ] **Step 5: Commit** `"Master move statistics from PGN, and the theory rule (repertoire graph, task 1)"`

### Task 2: Building the masters file on the server

**Files:**
- Modify: `app/services/masters_service.py`, `app/main.py` (lifespan), `app/config.py` (`MASTERS_AUTO_BUILD: bool = True`), `docker-compose.yml`, `pyproject.toml` (`py7zr`), `tests/conftest.py` (`MASTERS_AUTO_BUILD=false`)
- Test: `tests/test_masters.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `async masters_service.ensure_masters_db(path: Path, *, url: str = MASTERS_ARCHIVE_URL, sha256: str = MASTERS_ARCHIVE_SHA256, download=_download) -> bool` — returns `True` at once when `path` exists and its `meta.source_sha256 == sha256`. Otherwise: `download(url, dest)` (streaming, the shared User-Agent, 10-minute timeout) into a temporary directory; verify the SHA-256 (mismatch → log, return `False`); extract only `gm1981-2026.pgn` with `py7zr`; run `build_masters_db` in a thread into `path.with_suffix(".building")`; `os.replace` it onto `path`. The temporary directory and any `.building` file are always removed on failure. Returns whether a valid file is in place.
  - `main.py`: `app.state.masters_book = MastersBook(path)`; when `MASTERS_AUTO_BUILD`, a background task runs `ensure_masters_db(path)` and on success swaps in a fresh `MastersBook(path)`. Startup never waits on it; the task is cancelled on shutdown like the explanation worker.
- [ ] **Step 1: Write the failing tests** (a fake `download` that writes a 7z made in the test from the fixture with `py7zr`, and the fixture's SHA-256 as the pin):
  - `test_builds_once_and_reuses`: the first call builds and returns `True`; the second doesn't call `download`.
  - `test_checksum_mismatch_keeps_dataset`: a wrong `sha256` → `False`, no file at `path`, one error logged.
  - `test_failed_build_leaves_no_file`: `download` raises → `False`, no `path`, no `.building`, temporary directory gone.
  - `test_new_pinned_checksum_rebuilds`: a file built with another `source_sha256` is rebuilt.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `ensure_masters_db`, the settings and the lifespan wiring.
- [ ] **Step 4: Run** `uv run pytest` — PASS.
- [ ] **Step 5: Commit** `"Build the masters file on the server from 40H (repertoire graph, task 2)"`, then open the PR for tasks 1–2.

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
  - `GraphEdge` dataclass: `parent: str, child: str, uci: str, san: str, games: int, w: int, d: int, l: int, master_games: int, theory: bool, repertoire: bool, transposition: bool`.
  - `GraphNode` dataclass: `key: str, user_to_move: bool, games: int, ply: int, line: list[str]` (uci from the start via spanning parents), `parent: str | None, opening: tuple[str, str] | None, mark: str | None` (`"you_left_theory" | "add_answer" | "extend"`), `folded: int`.
  - `Graph` dataclass: `nodes: dict[str, GraphNode], edges: list[GraphEdge], todo: list[str]` (marked node keys, most games first).
  - `build_graph(tree: dict[str, Node], color: str, repertoire: Mapping[str, str], masters: Mapping[str, list[MasterMove]], dataset: Mapping[tuple[str, str], str], names: Mapping[str, tuple[str, str]], user_deviation_keys: set[str], masters_available: bool, unfold: set[str] = frozenset()) -> Graph`.
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
  - `test_master_counts_decide_theory`: with masters data, an off-dataset move masters play 50 times is theory and a move with 2 master games in a busy position isn't; with `masters_available=False` the dataset decides.
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
  - `GET /api/repertoire/{color}/graph?unfold=<key>&unfold=<key>` → `RepertoireGraphResponse { nodes: list[GraphNodeOut], edges: list[GraphEdgeOut], todo: list[str], theory_source: "masters" | "dataset", avg_moves_in_repertoire: float | None, empty_repertoire: bool }`. Master moves come from `app.state.masters_book.moves(...)` (through `asyncio.to_thread`) for every node in the tree.
  - `GET /api/repertoire/{color}/position?line=e2e4,e7e5` → `PositionPanelResponse { key, fen, line_san: list[str], user_to_move, opening, games, repertoire_move: {uci, san, explanation: ExplanationResponse | None} | None, candidates: list[CandidateRow] (user's turn), replies: list[ReplyRow] (opponent's turn), answered_pct: float | None, pick_blocked_at: list[str] | None (the uci line of the first user position on this line whose repertoire move is missing or different), theory_source: "masters" | "dataset", engine_busy: bool, usual_move_note: str | None }`. `usual_move_note`, at the user's turn only: when their most-played move here is off-book, "You usually play {san} here, which is off-book."; else when its `avg_drop >= WORSE_WINPROB` (the existing R2.3 rule), "You usually play {san} here; {better} is clearly better."; otherwise null.
    - `CandidateRow { uci, san, engine_rank: int | None, eval_cp: int | None, master_games: int, master_score: float | None, games: int, score: float | None, theory: bool }` — union of engine top 4, master moves with `is_theory` true, and the user's moves here; sorted by engine rank, then master games.
    - `ReplyRow { uci, san, games, share, score, master_share: float | None, theory, answer_san: str | None, in_games: bool }` — the user's replies first by games, then unmet master replies (`in_games=False`).
    - A bad `line` (illegal move) → 400.
  - `POST /api/repertoire/{color}/start` → `RepertoireChangeResponse`; 409 if the colour already has positions. Accepts every `suggest()` suggestion, using the first `theory_alternatives` move where the suggestion is off-book and one exists, then prunes and runs `check_games` as `accept` does.
- [ ] **Step 1: Write the failing tests** (shared `client`, games via `_user_with_games`, a masters file built from the fixture with `build_masters_db(..., min_position_games=1)` and swapped into `app.state.masters_book`, `get_engine_lines` monkeypatched to return fixed moves or `None`):
  - `test_graph_shape_and_theory_source`: RUY games ×3 → nodes include the root and `1.e4`, `theory_source == "dataset"` with no masters file and `"masters"` with the fixture file.
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
- Modify docs: `specs/features/02-openings/tasks.md` (task 14 → "replaced by the offline masters data, repertoire-graph-plan.md tasks 1–2"; add a pointer to this plan), `specs/features/02-openings/repertoire-graph.md` (the Task 4 builder decision), `CLAUDE.md` (the new services, one line each)

**Interfaces:**
- Consumes: Task 6.
- Produces:
  - `<PositionPanel color panel isOwner onPick(uci) onRemove() onSelectLine(line: string[]) />` as in mockup C and the spec's panel section: breadcrumb, board, repertoire move with the existing `ExplanationPanel` (moved from `RepertoireView` into `repertoire/ExplanationPanel.tsx`), candidates table with **Pick** (hidden when `pick_blocked_at` is set, replaced by "Your repertoire doesn't reach this position yet. Start at …" linking to that line), replies table (rows select the resulting line), `answered_pct`, engine-busy and theory-source notes.
  - `<RepertoireTab isOwner />`: White/Black tabs, the to-do summary ("N replies to answer, M places to extend", "Your games stay in your repertoire for X moves on average"), "Start from my games" when `empty_repertoire` (disabled with a note when the colour has no games), graph left / panel right (stacked below 820 px), recent deviations list under the graph (each row selects its position). Shows "Theory from named opening lines only while master games load." when `theory_source == "dataset"`. After Pick/Remove it reloads both.
- [ ] **Step 1:** Implement the panel, the tab, the App swap; remove the old view and routes.
- [ ] **Step 2: Run** `uv run pytest`, `make lint` (no new errors in changed files), `cd frontend && npx tsc --noEmit && npm run build` — all pass.
- [ ] **Step 3: Browser check** on a scratch copy of the database with a test account (as in Phase 1), first without a masters file, then with the real one built from 40H: the graph shows real lines; folding/unfolding; picking a candidate at the user's turn; answering a marked reply and stepping deeper; the unreachable-position link; a colour with no games; phone width (375 px) with no horizontal page scroll.
- [ ] **Step 4: Commit** `"Repertoire tab: graph and position panel replace the suggestion list (repertoire graph, task 7)"` and open the PR.

---

**Rollout** stays in the Phase 2 rollout task (15): back up the database before the deploy that
adds `engine_lines`; after the deploy, check the logs for the masters build finishing (a few
minutes on the VPS's 6 cores) and that `/app/data/masters.sqlite` is about 75 MB. No Lichess token
is needed.
