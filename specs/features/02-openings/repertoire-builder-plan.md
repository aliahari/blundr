# Step-by-step Repertoire Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Repertoire tab opens on the most likely position you haven't prepared, from Lichess
games at your level, and each pick moves to the next one. The graph stays as a separate overview.

**Architecture:**
- **Explorer data:** a cached Lichess explorer client (`explorer_service.py`) provides opponents'
  reply shares per position and rating band, with master games as the fallback.
- **The walk:** a pure best-first walk (`repertoire_builder.py`) turns the repertoire and those
  shares into ranked gaps at a coverage goal.
- **Routes:** `/next` and the pick return the next gap in one response.
- **Prefetch:** a per-user background job prepares the next gaps (explorer data and engine lines).

**Tech Stack:** FastAPI, async SQLAlchemy/aiosqlite, httpx, python-chess, Stockfish, React 18 + TS.

**Spec:** [repertoire-builder.md](repertoire-builder.md). Depends on
[Sign in with Lichess](../lichess-sign-in/plan.md), Tasks 3 and 5: `users.lichess_ratings`,
`user_lichess_token(user)`, `app.state.oauth_http`. Run this plan after that one has merged.

## Global Constraints

- Likelihood: multiply the shares of each opponent reply along the line. Your moves count as 1,
  and the start is 1. A position is in scope when its likelihood is at least `1 / goal`.
- Goals are one of `20, 50, 100, 200`, stored per user and colour, default `20`.
- `EXPLORER_MIN_GAMES = 20`: an opponent-turn position with fewer explorer games ends the walk
  there, counted as covered.
- `MAX_PLY = 30`.
- Explorer endpoint: `https://explorer.lichess.ovh/lichess` with these parameters:
  - `fen` (the position key plus `" 0 1"`);
  - `speeds` (the user's `sync_game_types` that are explorer speeds: `ultraBullet`, `bullet`,
    `blitz`, `rapid`, `classical`, `correspondence`; `blitz,rapid` when none are);
  - `ratings` (the user's bucket plus the next one up).
- Rating buckets: `0, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500`. The rating is the highest
  of the user's `lichess_ratings` for those speeds, or `1500` when there are none.
- Band key: `f"{','.join(sorted(speeds))}:{','.join(ratings)}"`, for example
  `blitz,rapid:1600,1800`.
- Cache: the `explorer_cache` table, used for 30 days. Failures are never cached.
- Explorer requests go one at a time per user. On a 429, no explorer requests for that user for
  60 s.
- The first `/next` waits at most 3 s in total on the explorer.
- The engine never preempts game analysis: use `get_engine_lines(..., wait=0)` in the background.
- Copy, from the spec:
  - `"18 prepared · 7 to go at 1 in 20"`;
  - `"Everything you'll meet in 1 in 20 games is prepared. Go deeper: 1 in 50."`;
  - `"Using master games: Lichess isn't reachable"`;
  - `"Sign in with Lichess again to see what players at your level play"`;
  - `"rarely reached at your level"`.
- No explanations on the Repertoire tab. Owners edit explanations in Learn.
- Tests make no real network calls. One shared `TestClient`. PRs with Auto-fix and auto-merge;
  never push to `main`.

## Review Focus

1. **Castling in explorer UCI** (`e1h1` king-takes-rook): normalized with `board.parse_uci`, so
   `O-O` is a real reply, not dropped or illegal. Test in Task 1.
2. **A line that ends the game** (mate or stalemate) **or reaches a position with no explorer
   moves:** the walk treats it as covered and doesn't crash. Test in Task 2.
3. **The goal changing while a prefetch for the old goal runs:** the old job is cancelled and
   `/next` answers for the new goal. Test in Task 4.
4. **The explorer answering 429:** nothing is cached, masters are used, and no request goes out
   for that user for 60 s. Test in Task 1.
5. **A pick at a position that isn't the current gap** (from the overview): it saves, and the
   reply is still the first gap. Test in Task 3.

---

### Task 1: The explorer client and cache

**Files:**
- Create: `app/services/explorer_service.py`
- Modify: `app/routes/analysis.py` (refresh `lichess_ratings` from `/api/account` at sync, with
  the user's token, best-effort)
- Test: `tests/test_explorer_service.py`

**Interfaces:**
- `ExplorerMove(uci: str, san: str, games: int, white: int, draws: int, black: int)`, a frozen
  dataclass. `uci` is normalized.
- `band_for(user: User) -> str`, using the speed and rating rules above.
- `async def explorer_moves(db, http: httpx.AsyncClient, *, user_id: int, token: str | None, key: str, band: str) -> list[ExplorerMove] | None`:
  - returns a fresh cache row when there is one;
  - otherwise fetches, if there's a token and the user isn't in back-off, then stores the result;
  - returns None when it can't (no token, back-off, timeout, any error).

  Requests take a per-user `asyncio.Lock` (a module dict keyed by user ID) and send
  `Authorization: Bearer <token>`.
- `def shares(moves: list[ExplorerMove]) -> list[tuple[str, int]]`: `(uci, games)` pairs, most
  games first.

- [ ] **Step 1: Failing tests,** with a `MockTransport` explorer:
  - `test_band_for`: ratings `{"blitz": 1650, "rapid": 1720}`, sync types `["rapid","blitz"]` give
    `"blitz,rapid:1600,1800"`; rating 2600 gives `":2500"` alone after the speeds; no ratings
    give `"blitz,rapid:1400,1600"`.
  - `test_fetch_and_cache`: the first call makes one request with the expected params and Bearer
    header; a second call makes none.
  - `test_cache_expires_after_30_days`: a row with `fetched_at` 31 days ago triggers a fetch.
  - `test_castling_uci_normalized`: a response with `{"uci": "e1h1", "san": "O-O"}` from a
    position where White can castle gives `uci == "e1g1"` (Review Focus 1).
  - `test_no_token_returns_none_without_request`.
  - `test_429_backs_off_and_caches_nothing`: a 429 gives None and no cache row; a call 10 s later
    (patch the clock) makes no request; one at 61 s does (Review Focus 4).
  - `test_error_not_cached`: a 500 gives None, no row.
- [ ] **Step 2:** Run `uv run pytest tests/test_explorer_service.py -q`. Expected: FAIL.
- [ ] **Step 3:** Implement it. Use a module-level `_now()` for the clock so tests can patch it.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `"Lichess explorer client with a per-band cache"`.

### Task 2: The walk

**Files:**
- Create: `app/services/repertoire_builder.py` (pure: no DB, no I/O)
- Test: `tests/test_repertoire_builder.py`

**Interfaces:**
- `Gap(key: str, line: list[str], likelihood: float, last_reply: str | None, last_share: float | None, my_games: int)`.
- `Walk(gaps: list[Gap], prepared: int, missing: set[str], thin: set[str])`.
- `def walk(color: str, repertoire: Mapping[str, str], replies: Mapping[str, list[tuple[str, int]]], goal: int, *, my_games: Mapping[str, int] = {}, max_ply: int = 30, min_games: int = 20) -> Walk`.
  - `repertoire`: `position_key -> uci` at the user's turn.
  - `replies`: opponent-turn `position_key -> [(uci, games)]`.
  - A key that's needed but absent from `replies` goes into `missing` and isn't expanded.
  - `thin` holds opponent positions with fewer than `min_games` games.
- Best-first by likelihood with a max-heap. A key is visited once, at its first (highest)
  likelihood. At the user's turn: follow the repertoire move, or record a gap. At the opponent's
  turn: push each reply whose child likelihood ≥ `1/goal`. Game-over positions are skipped.
- Gaps are sorted by `(-likelihood, -my_games, len(line))`. `prepared` counts user-turn positions
  in scope that have a repertoire move.

- [ ] **Step 1: Failing tests,** with small hand-built `replies` maps:
  - `test_white_empty_repertoire_gap_is_start`: one gap, the start key, likelihood 1.0.
  - `test_black_gaps_are_whites_replies`: White replies `e4:60, d4:30, c4:6, Nf3:4` at goal 20
    give gaps after e4, d4, c4 (0.06 ≥ 0.05) and not Nf3 (0.04), ordered e4, d4, c4.
  - `test_likelihood_multiplies`: White's repertoire 1.e4; Black replies e5:50, c5:50; after e5,
    with 2.Nf3 in the repertoire, Black replies Nc6:80, d6:20. The gap after 2…d6 has likelihood
    0.1; at goal 20 it's in, at goal 5 it's not.
  - `test_missing_keys_reported`: a needed opponent position absent from `replies` is in
    `missing` and not expanded.
  - `test_thin_position_stops_and_counts_covered`: replies totalling 19 games put the key in
    `thin` and produce no gaps under it.
  - `test_transposition_counted_once`: two lines reaching one position produce one gap, with the
    higher likelihood.
  - `test_game_over_is_covered`: a line into checkmate produces no gap (Review Focus 2).
  - `test_prepared_count`: three repertoire positions in scope give `prepared == 3`.
  - `test_tie_broken_by_my_games`.
- [ ] **Step 2:** Run the file. Expected: FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run it. Expected: PASS.
- [ ] **Step 5:** Commit `"Repertoire builder: best-first walk to the next gaps"`.

### Task 3: `/next`, the pick and the goal

**Files:**
- Modify: `app/models/db_models.py` and `app/db.py` (users columns `goal_white INTEGER NOT NULL DEFAULT 20`
  and `goal_black INTEGER NOT NULL DEFAULT 20`, through `_add_missing_columns`),
  `app/routes/repertoire.py`, `app/models/schemas.py`
- Create: `app/services/builder_service.py` (I/O around the walk)
- Test: `tests/test_repertoire_builder_api.py`

**Interfaces:**
- `async def next_gaps(db, request, user, color, *, budget_s: float = 3.0, limit: int = 1) -> tuple[Walk, str]`
  returns the walk and its `source` (`"lichess"` | `"masters"`):
  1. Walk with the replies already cached for the band.
  2. Fetch `missing` keys through `explorer_moves`, most likely first, until `budget_s` runs out.
  3. Fill whatever is still missing from `MastersBook.moves` as `(uci, games)`.
  4. Walk again. `source` is `"masters"` when any key on the walk came from masters.
- `candidates(db, request, user, color, board, key, *, level: list[ExplorerMove] | None) -> tuple[list[CandidateRow], bool]`:
  candidate building moved out of `get_position_panel` unchanged. The bool is `engine_busy`.
  It also fills `level_share` (that move's games / total here) and `level_score` (points per
  game for the side to move), None without level data.
- Schemas:
  - `CandidateRow` adds `level_share: float | None` and `level_score: float | None`.
  - `GapOut(line: list[str], line_san: list[str], fen: str, key: str, likelihood: float, last_reply_san: str | None, last_share: float | None, my_games: int, opening: OpeningLabel | None, candidates: list[CandidateRow], engine_busy: bool)`.
  - `NextResponse(gap: GapOut | None, prepared: int, left: int, goal: int, source: Literal["lichess","masters"], reconnect: bool)`.
    `reconnect` is true when the user has no usable token.
- Routes:
  - `GET /repertoire/{color}/next?line=`: with `line`, returns that position as the gap if it's
    the user's turn and reachable, otherwise 400; without it, returns the first gap.
  - `PUT /repertoire/{color}/position` returns `RepertoireChangeResponse` with a new field
    `next: NextResponse`. `recheck_games` moves to a background task with its own
    `async_session()`.
  - `PUT /repertoire/{color}/goal`, body `{"goal": int}`: 400 unless the goal is in
    `{20,50,100,200}`; returns `NextResponse`.
  - **Removed:** `POST /{color}/accept` and `POST /{color}/start`, with their tests and schemas
    (`AcceptRequest`).

- [ ] **Step 1: Failing tests.** Patch `explorer_service.explorer_moves` with a dict-backed fake,
  and give users a token through the sign-in helper.
  - `test_next_white_empty_is_start`: `gap.key` is the start, `prepared == 0`, and
    `source == "lichess"`.
  - `test_pick_returns_next_gap`: after picking `e2e4` at the start, `next.gap` is the most
    likely Black reply's position.
  - `test_pick_off_gap_still_returns_first_gap` (Review Focus 5).
  - `test_goal_validation_and_effect`: 30 gives 400; 50 gives `goal == 50` and `left` ≥ the
    `left` at 20.
  - `test_falls_back_to_masters_without_token`: a user without a token gets
    `source == "masters"` and `reconnect is True`, with a gap still returned (needs the masters
    fixture DB, as in `test_repertoire_graph_api.py`).
  - `test_budget_limits_explorer_wait`: a fake that sleeps 2 s per call, `budget_s=3`, makes at
    most 2 calls and returns.
  - `test_level_share_on_candidates`.
  - `test_next_with_line_param`: valid gives that gap; an opponent-turn line gives 400.
  - `test_pick_does_not_wait_for_recheck`: with `recheck_games` patched to sleep 5 s, the PUT
    returns in under 2 s.
  - `test_accept_and_start_gone`: both give 404 or 405.
- [ ] **Step 2:** Run the file. Expected: FAIL.
- [ ] **Step 3:** Implement it. Keep `get_position_panel` working on the extracted
  `candidates()`, but drop `repertoire_move.explanation` from its response.
- [ ] **Step 4:** Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit `"Builder endpoints: next gap, pick in one round trip, coverage goal"`.

### Task 4: Getting ahead of the user

**Files:**
- Create: `app/services/builder_prefetch.py`
- Modify: `app/main.py` (`app.state.builder_jobs = {}`; cancel all on shutdown),
  `app/routes/repertoire.py` (schedule after `/next`, a pick and a goal change)
- Test: `tests/test_builder_prefetch.py`

**Interfaces:**
- `def schedule_prefetch(app, user_id: int, color: str, goal: int) -> asyncio.Task`. It cancels
  that user's previous job in `app.state.builder_jobs` and starts one that:
  - calls `next_gaps(..., budget_s=30, limit=4)` in its own `async_session()`, so all missing
    explorer data for the next gaps gets fetched;
  - then calls `get_engine_lines(db, gap.key, wait=0)` for each of the first 4 gaps;
  - stops early on cancellation.

- [ ] **Step 1: Failing tests:**
  - `test_one_job_per_user`: scheduling twice cancels the first task; a second user's job is
    untouched.
  - `test_goal_change_replaces_job` (Review Focus 3): a job for goal 20 is cancelled when one for
    goal 50 is scheduled.
  - `test_prefetch_fills_cache`: with the fake explorer, after the job, the next 4 gaps' opponent
    positions are cached, and a following `/next` makes no explorer call.
  - `test_engine_not_preempted`: with the engine slot held, the job finishes without engine
    lines and without waiting.
- [ ] **Step 2:** Run the file. Expected: FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit `"Prepare the next gaps in the background"`.

### Task 5: The graph as an overview

**Files:**
- Modify: `app/services/repertoire_graph.py` (drop the `add_answer` and `extend` marks; keep
  `you_left_theory`), `app/routes/repertoire.py` (`/graph` adds `gaps: list[str]`, the keys of
  the walk's gaps at the current goal, from cached data only, with `budget_s=0`),
  `app/models/schemas.py`, `tests/test_repertoire_graph.py`, `tests/test_repertoire_graph_api.py`

- [ ] **Step 1: Failing tests:** `test_graph_lists_gaps` (a user with 1.e4 and cached Black
  replies gets their gap keys in `gaps`); `test_no_answer_or_extend_marks`. Update the old mark
  tests by deleting the two removed marks' cases.
- [ ] **Step 2:** Run the two files. Expected: FAIL.
- [ ] **Step 3:** Implement it.
- [ ] **Step 4:** Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit `"Graph: an overview with gaps, no to-do marks"`.

### Task 6: The builder screen and the overview

**Files:**
- Create: `frontend/src/components/repertoire/BuilderView.tsx`,
  `frontend/src/components/repertoire/OverviewView.tsx`
- Modify: `RepertoireTab.tsx` (colour tabs, then the builder by default and "See your
  repertoire" for the overview), `PositionPanel.tsx` (no `ExplanationPanel`; the replies table
  stays), `RepertoireGraph.tsx` (draw `gaps` with the `extend`-style dashed outline in
  `var(--primary)`; drop the add/extend labels), `services/api.ts` (`getNext`, `setGoal`; the
  pick returns `next`; remove `startFromGames`), `types/index.ts`, `styles/index.css`

**Interfaces:**
- `BuilderView` props: `color`, and `initialLine?: string[]` (set when a gap is clicked in the
  overview). It shows:
  - the header: progress copy, plus a "Deeper" menu of 20/50/100/200;
  - the line, with the opening name and "1 in N games at your level"
    (`N = round(1/likelihood)`), and "Your opponent played X (Y% here)";
  - the board, with the last reply highlighted as an arrow;
  - the candidates table: Move, Engine, "At your level", Masters, Pick;
  - the `source` and `reconnect` notes from the spec's copy.

  A Pick shows "Added 4.d3. Next: after 3…Bc5" for 4 s and renders `next` from the PUT response
  with no second request. A null gap shows the done copy with a button to the next goal.
- `OverviewView`: the graph, the trimmed `PositionPanel`, and "Where your games left the
  repertoire". Clicking a gap node opens `BuilderView` with that line.

- [ ] **Step 1:** Implement it.
- [ ] **Step 2:** Run `cd frontend && npm run build`. Expected: clean.
- [ ] **Step 3: Browser check** on local dev, signed in through Lichess, at desktop and 375 px
  width:
  - build three steps for White and two for Black;
  - each pick shows the next gap without a visible wait;
  - change the goal;
  - open the overview, click a gap, and land in the builder at it;
  - the phone layout shows the board first, then the choices.
- [ ] **Step 4:** Commit `"Repertoire tab: step-by-step builder, graph as overview"`.

### Task 7: Explanations in Learn only

**Files:**
- Move: `frontend/src/components/repertoire/ExplanationPanel.tsx` to
  `frontend/src/components/ExplanationPanel.tsx`
- Modify: `frontend/src/components/LineReview.tsx`

**Interfaces:**
- For an owner on an opening card, the explanation box gets an "Edit" link that opens
  `ExplanationPanel` (`positionKey` = the card's FEN before the move, first four fields, as
  `board.epd()`; `moveUci` = the answer). Non-owners see `ExplanationBox` as today.
- `LineReview` takes `isOwner` from `LearnView`, which gets it from `App`.

- [ ] **Step 1:** Implement it.
- [ ] **Step 2:** Run `npm run build`. Expected: clean.
- [ ] **Step 3:** Browser check: as owner, edit an explanation in Learn and see it saved.
- [ ] **Step 4:** Commit `"Owners edit explanations in Learn"`.

### Task 8: Docs

**Files:**
- Modify: `CLAUDE.md` (lines for `explorer_service`, `repertoire_builder`, `builder_service`,
  `builder_prefetch`; `repertoire_graph` is now the overview), `specs/features/02-openings/tasks.md`
  (a pointer to this plan), `specs/features/02-openings/repertoire-builder.md` (builder notes)

- [ ] **Step 1:** Update the docs. Run `uv run pytest -q` and `make lint`. Expected: all pass,
  lint clean.
- [ ] **Step 2:** Commit `"Docs: step-by-step repertoire builder"`, open the PR, and turn on
  Auto-fix and auto-merge.
