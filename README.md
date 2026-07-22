# Blundr - Chess Blunder Analyzer

A full-stack application for analyzing chess blunders using spaced repetition learning techniques.

## Overview

Blundr helps chess players improve by:
1. Fetching their games from Lichess
2. Identifying blunders in those games
3. Using spaced repetition to review and learn from mistakes
4. Tracking progress over time

## Tech Stack

- **Backend**: FastAPI (Python), async HTTP client for Lichess API
- **Database**: SQLite via async SQLAlchemy (aiosqlite)
- **Auth**: JWT (pyjwt) + bcrypt password hashing
- **Engine**: Stockfish driven by python-chess (blunder detection)
- **Frontend**: React 18, TypeScript, Vite, react-chessboard + chess.js
- **Styling**: Custom CSS with dark theme

## Prerequisites

- **Stockfish** must be installed and on PATH for blunder analysis:
  ```bash
  brew install stockfish   # macOS
  # apt install stockfish  # Debian/Ubuntu
  ```

## Quick Start

### Option 1: Run with Docker (Recommended for deployment)

Three containers: `backend` (FastAPI + Stockfish), `frontend` (static build
served by nginx), `caddy` (reverse proxy — routes `/api/*` to the backend,
everything else to the frontend, and handles TLS if you set `DOMAIN`).

```bash
cp .env.example .env
# Edit .env: set a real JWT_SECRET (required — the stack refuses to start
# without one). Leave DOMAIN unset to test against the VPS's bare IP first.

docker compose up --build -d
docker compose logs -f          # watch startup
curl http://localhost/api/health
```

Once DNS points at the VPS, set `DOMAIN=yourdomain.com` in `.env` and
`docker compose up -d` again — Caddy provisions a Let's Encrypt cert
automatically.

**Password reset emails**: without `SMTP_HOST` set, reset links are only
logged to `docker compose logs backend` — fine for local testing, useless
for real users on a VPS who can't see server logs. Set `SMTP_HOST` /
`SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` in `.env` (any provider's
SMTP endpoint works — a Gmail app password, Mailgun, Postmark, SES) and set
`FRONTEND_URL=https://yourdomain.com` so the emailed link points somewhere
real.

**Data persistence**: the SQLite database lives on a named Docker volume
(`backend_data`), so `docker compose down` / `up` and image rebuilds don't
lose data. `docker compose down -v` *does* delete it (`-v` removes volumes)
— that's the one command to be careful with.

**Updating Stockfish**: see the Dockerfile header — bump the pinned
version/checksum, validate with `scripts/compare_detection.py`, then
`docker compose up --build -d backend`.

**Higher Lichess rate limits**: sync calls Lichess anonymously by default
(20 games/s on the export endpoint). Set `LICHESS_TOKEN` in `.env` to a
Personal Access Token (no scopes needed — [create one
here](https://lichess.org/account/oauth/token/create)) to raise that to
30 games/s.

**Analytics**: a self-hosted [GoatCounter](https://www.goatcounter.com/)
instance ships as a fourth container — privacy-friendly, cookie-free
pageview tracking with no consent banner required. It serves on its own
subdomain (GoatCounter is vhost-based). Set `STATS_DOMAIN=stats.yourdomain.com`
in `.env` (plus a matching DNS A record at your VPS, same as `DOMAIN`), bring
the stack up, then create the site/admin account once:

```bash
docker compose exec goatcounter goatcounter db create site \
  -vhost=stats.yourdomain.com -user.email=you@example.com
```

It'll prompt for a password. Log in at `https://stats.yourdomain.com` to see
the dashboard; the tracking script in `frontend/index.html` is already wired
up (and self-skips on localhost, so local dev is never tracked). Leaving
`STATS_DOMAIN` unset is safe — the container still runs, just unreachable
from outside.

Building on an ARM machine (e.g. Apple Silicon) works but is emulated and
slow — Stockfish's official releases don't ship a generic Linux ARM64
binary, so this stack targets x86-64 VPS hardware either way. Building
directly on the (x86-64) VPS is both simpler and faster.

### Option 2: Local Development with UV (Recommended for development)

#### Install UV (if not already installed)

```bash
# Install uv (see https://docs.astral.sh/uv/getting-started/installation)
curl -Ls https://astral.sh/uv/install.sh | sh

# Or with pip
pip install uv
```

#### Backend Setup

```bash
# Create virtual environment and install dependencies with uv
uv sync

# Activate the virtual environment
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Start backend server
uv run python run.py
# Or with uvicorn directly
uv run uvicorn app.main:app --reload
```

#### Using pip (legacy)

```bash
# Create virtual environment (optional)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start backend server
python run.py
# Or with uvicorn directly
uvicorn app.main:app --reload
```

Backend will be available at `http://localhost:8000`

#### Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Frontend will be available at `http://localhost:3000`

### Option 3: Run Both Together

```bash
./run_dev.sh
```

This will start both backend and frontend servers.

## Project Structure

```
blundr/
├── app/                          # Backend
│   ├── main.py                   # FastAPI app entry
│   ├── config.py                 # Application config
│   ├── models/                   # Data models
│   │   ├── schemas.py            # API request/response models
│   │   └── game.py               # Internal game models
│   ├── routes/                   # API endpoints
│   │   └── games.py              # Game routes
│   ├── services/                 # Business logic
│   │   ├── lichess_client.py     # Lichess API client
│   │   └── game_service.py       # Game service
│   └── utils/                    # Utilities
│       ├── exceptions.py         # Custom exceptions
│       └── date_utils.py         # Date utilities
├── frontend/                     # Frontend
│   ├── src/
│   │   ├── App.tsx               # Main component
│   │   ├── main.tsx              # Entry point
│   │   ├── types/                # TypeScript types
│   │   ├── services/             # API services
│   │   ├── utils/                # Utility functions
│   │   └── styles/               # CSS styles
│   ├── package.json
│   └── vite.config.ts
├── requirements.txt              # Python dependencies
├── Dockerfile                    # Backend Dockerfile
└── README.md
```

## API Endpoints

### Auth
- `POST /api/auth/register` - Create an account, requires email (returns JWT)
- `POST /api/auth/login` - Log in (returns JWT)
- `GET /api/auth/me` - Current user 🔒
- `POST /api/auth/forgot-password` - Request a reset link by email (always the same response, whether or not the email exists)
- `POST /api/auth/reset-password` - Complete a reset with a valid token

### Settings
- `GET /api/settings` - Profile + sync preferences 🔒
- `PUT /api/settings` - Update profile / sync preferences (partial) 🔒

### Stats
- `GET /api/stats/overview` - Dashboard headline numbers 🔒
- `GET /api/stats/timeline?days=30` - Per-day games/blunders for charts 🔒

### Games
- `POST /api/games/fetch` - Fetch user games with filters
- `GET /api/games/{username}` - GET alternative for fetching games
- `GET /api/games/{username}/{game_id}` - Get specific game

### Analysis
- `POST /api/analysis/sync` - Sync + analyze games using saved preferences 🔒
- `POST /api/analysis/start` - Fetch + engine-analyze games with explicit params 🔒
- `GET /api/analysis/status` - Progress of the running/last job 🔒
- `GET /api/analysis/blunders` - All detected blunders 🔒
- `POST /api/analysis/best-reply` - Engine's punish of a candidate move 🔒

### Reviews (spaced repetition)
- `GET /api/reviews/due` - Cards due for review 🔒
- `POST /api/reviews/{card_id}` - Grade a card (again/good/easy) 🔒
- `GET /api/reviews/stats` - Queue summary 🔒

### Health
- `GET /api/health` - Health check
- `GET /api/ready` - Readiness check

🔒 = requires `Authorization: Bearer <token>`

## How Blunder Detection Works

1. Games are fetched from Lichess and replayed move by move with python-chess.
2. Stockfish evaluates every position (depth-limited, default depth 12 with a
   0.5s safety cap; several games are analyzed in parallel).
3. Evals are converted to win probability with the same sigmoid Lichess uses;
   a user move that drops win probability ≥ 25 percentage points (configurable)
   and differs from the engine's choice is flagged as a blunder.
4. Each blunder becomes an SM-2 spaced-repetition card: the review UI shows the
   position you faced, you find the better move (drag or click-to-move), and
   grade yourself Again/Good/Easy. Failed cards return in 10 minutes; passed
   cards follow the 1 day → 6 days → interval×ease progression.

## Current Features

### ✅ Implemented
- [x] Backend: FastAPI server setup
- [x] Backend: Lichess API integration
- [x] Backend: Game fetching with date range and filters
- [x] Backend: Error handling and rate limiting
- [x] Backend: User authentication (register/login, JWT, bcrypt)
- [x] Backend: Password reset via emailed link (SMTP, with a console-log fallback for local dev)
- [x] Backend: SQLite storage (async SQLAlchemy)
- [x] Backend: Blunder detection (Stockfish + python-chess, background jobs)
- [x] Backend: Spaced repetition scheduling (SM-2)
- [x] Backend: Comprehensive testing
- [x] Frontend: Vite + React + TypeScript setup
- [x] Frontend: Login/register, session persistence
- [x] Frontend: Dashboard with stats (games analyzed, blunders, mastered) and activity charts
- [x] Frontend: Automatic background sync with progress (from saved preferences)
- [x] Frontend: Learn tab — blunder review (interactive board, drag or click-to-move)
- [x] Frontend: Settings — profile (name, picture, Lichess account) + sync preferences
- [x] Learning: daily new-blunder limit (Anki-style new-card intake, default 10/day)
- [x] Frontend: Responsive design

### 🚧 Coming Soon
- [ ] Advanced analytics (review history is already logged)
- [ ] Periodic auto-resync while the app is open
- [ ] PostgreSQL option for multi-user deployments

## Configuration

### Backend

Create a `.env` file in the project root:

```env
APP_NAME=Chess Blunder Analyzer
APP_VERSION=0.1.0
DEBUG=True
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
LICHESS_API_TIMEOUT=30.0
LICHESS_RATE_LIMIT_DELAY=1.0

# Database
DATABASE_URL=sqlite+aiosqlite:///./blundr.db

# Auth — set a real secret in any non-local deployment
JWT_SECRET=dev-secret-change-me
JWT_EXPIRES_MINUTES=10080

# Engine analysis
STOCKFISH_PATH=stockfish
ANALYSIS_DEPTH=12
ANALYSIS_MAX_TIME_PER_POSITION=0.5
ANALYSIS_CONCURRENCY=4
BLUNDER_WINPROB_THRESHOLD=25.0
```

### Frontend

The frontend automatically proxies API requests to the backend. 
To change the backend URL, modify `frontend/vite.config.ts`:

```typescript
proxy: {
  '/api': {
    target: 'http://your-backend-url',
    changeOrigin: true,
  },
}
```

## Testing

### Backend Tests

```bash
# Using uv (recommended)
uv run pytest
uv run pytest tests/
uv run pytest tests/test_games.py

# Run with coverage
uv run pytest tests/ --cov=app

# Using pip (legacy)
python -m pytest tests/
python -m pytest tests/test_games.py
python -m pytest tests/ --cov=app
```

### Frontend Tests

No frontend test framework is configured yet (`frontend/package.json` has no `test` script or test dependencies).

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- [Lichess](https://lichess.org/) for their amazing free chess API
- [FastAPI](https://fastapi.tiangolo.com/) for the backend framework
- [React](https://react.dev/) for the frontend library
- [Vite](https://vitejs.dev/) for the fast frontend tooling
