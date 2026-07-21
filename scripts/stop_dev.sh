#!/usr/bin/env bash
#
# Stop the Blundr dev servers (backend :8000, frontend :3000).
#
# Kills the port listeners AND their supervisor parents (the uvicorn
# --reload watcher and the npm wrapper) — killing only the listener leaves
# a supervisor behind that either respawns it or holds the old code.
#
# Usage:
#   ./scripts/stop_dev.sh
#   BACKEND_PORT=8001 FRONTEND_PORT=3001 ./scripts/stop_dev.sh

set -uo pipefail

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

stop_port() {
  local port=$1 name=$2 pids pid ppid pcmd
  pids=$(lsof -ti ":$port" 2>/dev/null || true)
  if [[ -z "$pids" ]]; then
    echo "$name: nothing listening on :$port"
    return 0
  fi

  for pid in $pids; do
    # Kill a respawning/wrapping parent first (uvicorn reload supervisor,
    # `npm run dev`), but never something unrelated
    ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    if [[ -n "${ppid:-}" && "$ppid" != "1" ]]; then
      pcmd=$(ps -o command= -p "$ppid" 2>/dev/null || true)
      case "$pcmd" in
        *run.py*|*uvicorn*|*vite*|*"npm run dev"*)
          kill "$ppid" 2>/dev/null || true ;;
      esac
    fi
    kill "$pid" 2>/dev/null || true
  done

  sleep 1
  pids=$(lsof -ti ":$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "$name: still alive, escalating to SIGKILL"
    kill -9 $pids 2>/dev/null || true
    sleep 1
  fi

  if lsof -ti ":$port" >/dev/null 2>&1; then
    echo "$name: FAILED to free port :$port" >&2
    return 1
  fi
  echo "$name: stopped (port :$port free)"
}

stop_port "$BACKEND_PORT" "backend"
stop_port "$FRONTEND_PORT" "frontend"

# Sweep stray backend supervisors from THIS project that no longer hold the
# port (e.g. a reload watcher whose child died) — matched by working dir so
# other projects' servers are never touched
for pid in $(pgrep -f "python run.py" 2>/dev/null || true); do
  cwd=$(lsof -a -d cwd -p "$pid" 2>/dev/null | awk 'NR==2{print $NF}')
  if [[ "$cwd" == "$PROJECT_DIR" ]]; then
    kill "$pid" 2>/dev/null || true
    echo "killed stray backend process (pid $pid)"
  fi
done

echo "Done. Start fresh with ./run_dev.sh"
