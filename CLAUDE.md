# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Blundr: a full-stack app that fetches a user's Lichess games, runs them through Stockfish to detect
blunders, and turns each blunder into a spaced-repetition (SM-2) flashcard for training. Deployed at
blundr.ch.

Where it's going: personalized training built from the player's own games — openings, tactics,
endgames and strategy, not just blunders. `specs/` holds the plan: `mission.md` (the why),
`roadmap.md` (phases in order), and `features/<NN-name>/` (requirements, design and tasks per phase).
Read the relevant feature spec before working on a roadmap item.

## Commands

```bash
# Backend dev server (http://localhost:8000)
uv run python run.py                    # or: make backend
uv run uvicorn app.main:app --reload

# Frontend dev server (http://localhost:3000, proxies /api to :8000)
cd frontend && npm run dev               # or: make frontend

# Both at once
./run_dev.sh                              # or: make dev

# Backend tests
uv run pytest                             # or: make test
uv run pytest tests/test_games.py
uv run pytest tests/test_blundr_features.py::TestAuth::test_register_returns_token
uv run pytest --cov=app --cov-report=term # or: make test-cov

# Lint / format (ruff)
make lint          # uv run ruff check app tests
make lint-fix
make format

# Frontend build check (no test framework configured for frontend)
cd frontend && npm run build
```

Stockfish must be on `PATH` (`brew install stockfish`) for analysis to run — `run_analysis_job` /
`detect_blunders` will fail without it. Tests exercise the real engine (see `_StubEngine` in
`tests/test_blundr_features.py` for the cases that don't).

`tests/conftest.py` points `DATABASE_URL` at a throwaway `tests/test_blundr.db` and builds one
shared `TestClient` for the whole test session — this must happen before `app.config`/`app.main`
import anywhere, because `app.state.lichess_client`'s connection pool binds to whichever event loop
first touches it. Don't add a second module-level `TestClient`.

## Architecture

**Backend** (`app/`, FastAPI + async SQLAlchemy/aiosqlite): routes are thin; logic lives in
`services/`.

- `services/lichess_client.py` — Lichess API client. Sends a custom `User-Agent`
  (`Blundr/<version> (+https://blundr.ch)`); Lichess silently 404s requests with generic
  httpx/curl-default UAs on the games-export endpoint instead of a real 4xx, so don't drop this
  header. One `httpx.AsyncClient` + `LichessClient` is created once in `main.py`'s lifespan and
  shared via `app.state`, not per-request.
- `services/analysis_service.py` — the core loop: replay a game with python-chess, call
  `engine.analyse()` once per position (gets eval + best-move PV in one call), convert centipawns to
  win% with `win_prob()` (same sigmoid Lichess uses), and flag a blunder when win% drops by
  `BLUNDER_WINPROB_THRESHOLD` *and* the pre-move win% was still above `BLUNDER_MIN_WINPROB_BEFORE`
  (a big drop from an already-lost position isn't worth drilling). Runs as a background asyncio task
  per user (`AnalysisJob`, tracked in `app.state.analysis_jobs`), with games spread across
  `ANALYSIS_CONCURRENCY` parallel Stockfish processes via a shared queue. Each game's blunders are
  persisted as soon as that game finishes, so a mid-job crash keeps completed work.
  `analyze_game()` also returns the per-move data the loop computes anyway (eval after each move,
  engine's best move before it), saved on `AnalyzedGame.positions` — see below.
- `services/game_phase.py` — `game_phase(board, ply)`: opening / middlegame / endgame by piece count.
  The one place those thresholds live.
- `services/deviation_service.py` — checks each game once against the user's repertoire (the pure
  `first_deviation()` lives in `repertoire_service`): records where it first left it, makes that
  card due when the user left it, and makes a punish card when the opponent left it with a blunder.
  Runs after each analyzed game, each backfill batch, and each repertoire accept/set; a game whose
  colour has no repertoire yet stays unchecked until it does.
- `services/masters_service.py` — master move statistics from the offline 40H database, in their
  own SQLite file (`MASTERS_DB_PATH`, default `masters.sqlite` beside the main database): built in
  the background at startup from a pinned, checksummed archive (`MASTERS_AUTO_BUILD`, off in
  tests), read through `app.state.masters_book`; `is_theory()` is the one theory rule.
- `services/engine_lines.py` — Stockfish's top 4 moves for a position, cached in `engine_lines`,
  at most one run at a time across the app (a caller waiting over 5 s gets "engine busy";
  `wait=0` runs only if the engine is free).
- The step-by-step repertoire builder (specs/features/02-openings/repertoire-builder.md):
  - `services/explorer_service.py` — what players at the user's level reply, from the Lichess
    explorer with the user's own token; cached per position and rating band for 30 days in
    `explorer_cache`; one request at a time per user, 60 s back-off on a 429; never raises.
  - `services/repertoire_builder.py` — `walk()`, pure: best-first from the start, likelihood =
    product of the opponent replies' shares; a user-turn position in scope (≥ 1/goal) without a
    repertoire move is a gap.
  - `services/builder_service.py` — the walk's I/O: explorer data within a time budget, master
    games where the explorer can't answer. Behind `/repertoire/{color}/explore` (the navigator:
    any position's named replies with likelihood and completion, or the user's choices),
    `/repertoire/{color}/next` (the first gap) and the pick (`PUT /position`).
  - `services/builder_prefetch.py` — one background job per user preparing the next gaps and the
    likeliest picks (explorer data, engine lines); off in tests (`BUILDER_PREFETCH`).
- `services/repertoire_graph.py` — `build_graph()`, pure: a colour's game tree merged with the
  repertoire, master moves and dataset theory, folded; the one mark is "you left theory". Behind
  `/repertoire/{color}/graph`, now the tab's overview (it also lists the builder's gaps).
- `services/progress_service.py` — progress measures from stored game data (so far the weekly
  blunder rate per 100 user moves, behind `/stats/blunder-rate`).
- `services/explanation_service.py` — opening explanations written by Claude (Anthropic SDK,
  Message Batches API at half price), grounded in engine and game data; a background worker
  started in `main.py`'s lifespan, off when `ANTHROPIC_API_KEY` is empty. Every API call goes
  through the `AnthropicClaude` adapter; tests use a fake and never call the API.
- `services/srs_service.py` — SM-2 scheduling. `apply_grade()` mutates a `ReviewCard` in place:
  again/good/easy map to SM-2 quality 2/4/5; a fail resets the streak and comes back in 10 minutes;
  a pass follows 1 day → 6 days → `interval × ease`.
- `services/lichess_auth.py` + `routes/auth.py` — Sign in with Lichess, the only way in
  (specs/features/lichess-sign-in/design.md): OAuth 2 with PKCE, run by the server
  (`/auth/lichess/start` and `/callback`); the user's Lichess token is stored Fernet-encrypted
  (`LICHESS_TOKEN_KEY`), and every Lichess call made for a user gets it through
  `user_lichess_token()`. A token Lichess rejects is cleared and the app asks to sign in again.
  The callback hands Blundr's JWT to the browser in a `/#auth=` fragment. Never log or return the
  Lichess token.
- `services/auth_service.py` — Blundr's own JWT (pyjwt). Route-level auth is a single dependency,
  `deps.get_current_user`, decoding the Bearer token and loading the `User` row.

**Data model** (`app/models/db_models.py`): `User` → `AnalyzedGame` → `Blunder` → `ReviewCard`
(1:many at each level). Each `Blunder` can have up to two `ReviewCard`s: `"avoid"` (play the
blunderer's side, find the best move — always created) and `"punish"` (play the opponent, find the
refutation — only created when `refutation_uci` is non-null, i.e. the blunder didn't end the game).
Opening cards are practised as whole lines (`GET /reviews/line`, `OpeningPractice.tsx`): from the
start to the end of the branch holding the most urgent due opening card; the user plays every one of
their moves, only due ones are graded (one card each, once per session), a new move is shown with
its explanation first, and later explanations only appear after a wrong move. Punish cards keep the
one-position flow (`LineReview` with `kinds=['deviation']`).

`ReviewLog` is an append-only history of grades, separate from `ReviewCard`'s current scheduling
state, so future analytics don't require a schema change.

`ReviewCard` is generic over the kind of training item it drills: `kind` (only `"blunder"` so far)
plus one nullable source column per kind (`blunder_id`; later kinds add their own column and an
entry in `CARD_SOURCE_COLUMNS`), and `step` = which move of the item's line the card drills, so a
line is scheduled move by move (blunder cards are always step 0). Create cards with
`new_review_card()`, which checks exactly one matching source is set. The review queue
(`routes/reviews.py`) groups cards by item — `(kind, source id)` — so an item spends one slot of
`max_new_per_day` however many cards it has; `/stats/overview` reuses the same helpers.

`AnalyzedGame` also keeps the game's move data for games analyzed since Phase 1: `positions` (JSON,
one `{san, uci, eval_cp, best_uci}` per ply; FENs come from replaying), `user_moves`, `speed`,
`initial_eval_cp`. Games analyzed before Phase 1 get their moves from Lichess later (`services/backfill_service.py`,
run at the tail of each sync): for those, every `eval_cp` and `best_uci` is null and
`initial_eval_cp` stays NULL. Anything reading them must handle null; a game Lichess no longer
has keeps `positions` NULL.

**SQLite runs in WAL mode** (set in `create_tables()`): several sessions run at once (a builder
step, its Lichess lookup, the background job), and in the default journal a reader blocks another
session's commit ("database is locked"). The database directory therefore also holds
`blundr.db-wal` and `blundr.db-shm`; back up with the SQLite backup API (as below), not by
copying `blundr.db` alone.

**DB migrations**: no Alembic — `db.create_tables()` runs `Base.metadata.create_all` plus a series of
manual `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`-style checks (see `app/db.py`) for columns added
after the table already existed in production. Follow that pattern for new columns rather than
introducing a migration framework, unless this moves off SQLite. A change SQLite can't do in place
(dropping NOT NULL, changing a constraint) needs a table rebuild — see `_rebuild_review_cards()`
for the safe order (create new, copy, check row count, drop old, rename new; never rename the old
table aside, or SQLite rewrites other tables' foreign keys to follow it) and
`tests/test_migrations.py` for how to test one against the old schema.

**Frontend** (`frontend/src/`, React 18 + TypeScript + Vite, no router): `App.tsx` holds all
top-level view state (`'dashboard' | 'learn' | 'settings'`) in `useState`, not a router — the one
exception is reading the `#auth=` / `#auth_error=` fragment once on mount after signing in with
Lichess, which it then strips from the URL. `services/api.ts` is the sole fetch layer (JWT from storage, attached
as `Authorization: Bearer`). Chessboard UI (`ReviewPanel.tsx`) uses `react-chessboard` +
`chess.js`, distinct from the backend's `python-chess`.

**Config** (`app/config.py`): a single pydantic-settings `Settings` object read from `.env`.
`CORS_ORIGINS` and `DEFAULT_GAME_TYPES` are comma-separated strings (not JSON arrays) via a custom
`NoDecode` + before-validator — keep new list-typed settings consistent with that if you add any.
`extra = "ignore"` is required because `.env` is shared with `docker-compose.yml` (`DOMAIN`,
`STOCKFISH_*` build args) which aren't `Settings` fields. In production the backend container gets
its settings **only** through the `environment:` block in `docker-compose.yml` (`.env` isn't
copied into the image), so a new setting must be added there too, or it silently keeps its
default on the VPS.

## Notes

- Three Docker containers in production (`backend`, `frontend`, `caddy`) plus an optional
  `goatcounter` analytics container — see the README's Docker section for the deployment details,
  and `.github/workflows/deploy.yml` for how a push to `main` reaches the VPS.
- `blundr.db` in the repo root is the local dev SQLite database — never the one tests use.
- Production VPS: `ssh blundr-vps` (alias in `~/.ssh/config` for `root@212.227.30.55`). The app is
  checked out at `/root/blundr` and runs as the compose project `blundr`; the production database
  is `/app/data/blundr.db` inside the `backend` container (volume `blundr_backend_data`). Back it up
  before any deploy that migrates the schema:
  `ssh blundr-vps 'cd /root/blundr && docker compose exec -T backend python -c "import sqlite3; s=sqlite3.connect(\"/app/data/blundr.db\"); d=sqlite3.connect(\"/app/data/blundr.db.backup\"); s.backup(d); d.close()"'`
  then `docker compose cp backend:/app/data/blundr.db.backup .` and `scp` it down, keeping the
  `blundr.db.*` name so `.gitignore` covers it locally. Deploys happen only
  through CI on `main`, never by editing files on the server.
- **Never push directly to `main`** (it auto-deploys, and branch protection blocks it). Every change
  goes on a branch and is opened as a pull request against `main`, with GitHub auto-merge turned on.
  `main` requires two checks: `test` (`deploy.yml`) and `review` (`claude-code-review.yml`), which
  passes only when Claude reviewed that commit and found no issues. So a clean PR merges and deploys
  by itself; anything else waits for the owner, who can merge by hand (admins bypass the checks).
  PRs that change the review workflow can't pass `review` (the action skips them), so they're merged
  by hand.

## How work is split across sessions

Long sessions are expensive: every step re-reads the whole conversation (measured 2026-11-28: 78%
of a week-long session's usage was re-reading its own history). So:

- **One planning session** for ideas and design. It ends with concrete files, not code: the spec in
  `specs/features/<NN-name>/` (requirements, design, decisions) and a plan with self-contained
  tasks (files, interfaces, exact tests). A task must make sense to someone who never saw the
  planning conversation.
- **Each feature is implemented in a new session**, started from its plan file, never by
  continuing the planning session. Inside it, tasks can go to subagents given only the task's
  text and the spec path, not the conversation.
- **Don't carry a session into an unrelated feature.** Start fresh instead; the project log below
  and the specs carry the context.
- **Reviews:** the GitHub review checks every PR (on Sonnet, ~$0.10 each). Run an extra in-session
  reviewer only for large or risky branches (data migrations, auth). While iterating, run the test
  files you touched; run the full suite before committing.
- **At the end of each feature**, add an entry to the project log: what shipped (PR), decisions a
  later session needs, follow-ups left open. One to three lines each, newest first; drop entries
  once they stop mattering.

## Project log

Newest first. What a fresh session needs to know that the code and specs don't already say.

- **2026-11-28 · Line practice (#40), builder depth (#39).** Learn → Openings practises whole lines
  (`/reviews/line`): explanations on a move's first time, then only after a wrong move. The builder's
  goal is "up to move N" (4–12, default 6) with a fixed 1-in-100 floor at the user's level,
  shallowest gaps first; the old `goal_white/black` columns are unused. Wrong-move path not yet
  tried in a browser.
- **2026-11-28 · Colour page (#38).** Repertoire opens on a page per colour: % complete (weighted by
  how often positions come up) and % mastered (opening cards with a 6+ day interval).
- **2026-11-27 · Navigator (#37), builder (#36).** The builder walks the tree Chessbook-style
  (named replies, moves played out). SQLite runs in WAL mode; backups must use the SQLite backup
  API. Learn queues are per kind with "Review more" (`extra_new`). The graph is only an overview now.
- **2026-11-27 · Sign in with Lichess (#35).** Only way in; tokens Fernet-encrypted
  (`LICHESS_TOKEN_KEY` in the VPS `.env`). The old password accounts were deleted on deploy; backup
  `blundr.db.backup-pre-lichess-signin` on the VPS and locally.
- **Open follow-ups:** Chessbook-style stats row (effectiveness, soundness, learnability) and
  "autofill a gap" not built; background game rechecks aren't coalesced; the blunder tab's
  "today's limit" note counts all card kinds; app INFO logs don't show in production logs.
