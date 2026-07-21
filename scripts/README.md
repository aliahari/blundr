# Development Scripts

This directory contains helper scripts for development.

## setup_dev.sh

Sets up the development environment using uv:
- Installs uv if not present
- Creates a virtual environment
- Installs dependencies with `uv sync`

**Usage:**
```bash
./scripts/setup_dev.sh
```

## stop_dev.sh

Stops the dev servers (backend on :8000, frontend on :3000), including the
uvicorn reload supervisor and npm wrapper that would otherwise linger or
respawn. Safe to run when nothing is running. `run_dev.sh` calls it
automatically, so restarting is just `./run_dev.sh`.

**Usage:**
```bash
./scripts/stop_dev.sh
# custom ports:
BACKEND_PORT=8001 FRONTEND_PORT=3001 ./scripts/stop_dev.sh
```

## reset_db.sh

Resets the Blundr SQLite database, entirely or per user. Tables are recreated
automatically on the next backend startup.

**Usage:**
```bash
./scripts/reset_db.sh --list                     # users + data counts
./scripts/reset_db.sh                            # full reset (deletes blundr.db)
./scripts/reset_db.sh --user NAME                # delete one user + all their data
./scripts/reset_db.sh --user NAME --keep-account # wipe a user's data, keep the login
./scripts/reset_db.sh -y ...                     # skip the confirmation prompt
```

After a **full** reset, restart the backend — a running server keeps the
deleted file open:
```bash
lsof -ti :8000 | xargs kill; uv run python run.py
```
(Per-user deletes are safe while the server runs.)

## Manual Setup with uv

Alternatively, you can set up manually:

```bash
# Install uv (if not installed)
curl -Ls https://astral.sh/uv/install.sh | sh

# Create virtual environment
uv venv

# Install dependencies
uv sync

# Activate the environment
source .venv/bin/activate

# Run the backend
uv run python run.py
```

## UV Commands Reference

| Command | Description |
|---------|-------------|
| `uv sync` | Install all dependencies |
| `uv add <package>` | Add a new dependency |
| `uv remove <package>` | Remove a dependency |
| `uv run <command>` | Run command in venv context |
| `uv pip list` | List installed packages |
| `uv pip install <package>` | Install a package |
| `uv venv` | Create virtual environment |
| `uv --version` | Check uv version |
