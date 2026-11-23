# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

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
- `services/progress_service.py` — progress measures from stored game data (so far the weekly
  blunder rate per 100 user moves, behind `/stats/blunder-rate`).
- `services/srs_service.py` — SM-2 scheduling. `apply_grade()` mutates a `ReviewCard` in place:
  again/good/easy map to SM-2 quality 2/4/5; a fail resets the streak and comes back in 10 minutes;
  a pass follows 1 day → 6 days → `interval × ease`.
- `services/auth_service.py` — JWT (pyjwt) + bcrypt. Route-level auth is a single dependency,
  `deps.get_current_user`, decoding the Bearer token and loading the `User` row.
- `services/email_service.py` — password-reset emails via SMTP; with `SMTP_HOST` unset it logs the
  reset link to the console instead (fine for local dev, see README for the VPS caveat).

**Data model** (`app/models/db_models.py`): `User` → `AnalyzedGame` → `Blunder` → `ReviewCard`
(1:many at each level). Each `Blunder` can have up to two `ReviewCard`s: `"avoid"` (play the
blunderer's side, find the best move — always created) and `"punish"` (play the opponent, find the
refutation — only created when `refutation_uci` is non-null, i.e. the blunder didn't end the game).
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
`initial_eval_cp`. All NULL for older games — anything using them must skip those.

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
exception is reading a `?reset_token=` query param once on mount for the password-reset flow, which
it then strips from the URL. `services/api.ts` is the sole fetch layer (JWT from storage, attached
as `Authorization: Bearer`). Chessboard UI (`ReviewPanel.tsx`) uses `react-chessboard` +
`chess.js`, distinct from the backend's `python-chess`.

**Config** (`app/config.py`): a single pydantic-settings `Settings` object read from `.env`.
`CORS_ORIGINS` and `DEFAULT_GAME_TYPES` are comma-separated strings (not JSON arrays) via a custom
`NoDecode` + before-validator — keep new list-typed settings consistent with that if you add any.
`extra = "ignore"` is required because `.env` is shared with `docker-compose.yml` (`DOMAIN`,
`STOCKFISH_*` build args) which aren't `Settings` fields.

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
- **Never push directly to `main`** (it auto-deploys). Every change goes on a branch and is opened
  as a pull request against `main`; the owner merges it.
