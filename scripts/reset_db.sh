#!/usr/bin/env bash
#
# Reset the Blundr database, entirely or per user.
#
# Usage:
#   ./scripts/reset_db.sh                     # full reset (deletes blundr.db)
#   ./scripts/reset_db.sh --user NAME         # delete one user and all their data
#   ./scripts/reset_db.sh --user NAME --keep-account
#                                             # wipe a user's games/blunders/reviews
#                                             # but keep the login account
#   ./scripts/reset_db.sh --list              # list users and their data counts
#   ./scripts/reset_db.sh -y ...              # skip the confirmation prompt
#
# Tables are recreated automatically the next time the backend starts.
# NOTE: restart the backend after a full reset — a running server keeps the
# deleted file open and would silently write into the void.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB="$SCRIPT_DIR/../blundr.db"

ASSUME_YES=0
MODE="full"
TARGET_USER=""
KEEP_ACCOUNT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)         MODE="user"; TARGET_USER="${2:?--user needs a username}"; shift 2 ;;
    --keep-account) KEEP_ACCOUNT=1; shift ;;
    --list)         MODE="list"; shift ;;
    -y|--yes)       ASSUME_YES=1; shift ;;
    -h|--help)      tail -n +2 "$0" | grep '^#' | sed 's/^# \{0,1\}//' | head -15; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 1 ;;
  esac
done

if [[ ! -f "$DB" ]]; then
  echo "No database found at $DB — nothing to reset."
  exit 0
fi

confirm() {
  [[ $ASSUME_YES -eq 1 ]] && return 0
  read -r -p "$1 [y/N] " reply
  [[ "$reply" == "y" || "$reply" == "Y" ]]
}

case "$MODE" in
  list)
    sqlite3 -column -header "$DB" "
      SELECT u.username,
             u.lichess_username AS lichess,
             (SELECT COUNT(*) FROM analyzed_games g WHERE g.user_id = u.id) AS games,
             (SELECT COUNT(*) FROM blunders b WHERE b.user_id = u.id)       AS blunders,
             (SELECT COUNT(*) FROM review_cards c WHERE c.user_id = u.id)   AS cards,
             (SELECT COUNT(*) FROM review_logs l WHERE l.user_id = u.id)    AS reviews
      FROM users u ORDER BY u.id;"
    ;;

  user)
    USER_ID=$(sqlite3 "$DB" "SELECT id FROM users WHERE username = '$TARGET_USER';")
    if [[ -z "$USER_ID" ]]; then
      echo "User '$TARGET_USER' not found. Use --list to see users." >&2
      exit 1
    fi
    if [[ $KEEP_ACCOUNT -eq 1 ]]; then
      confirm "Wipe all games/blunders/reviews for '$TARGET_USER' (keeping the account)?" || exit 1
    else
      confirm "Delete user '$TARGET_USER' and ALL their data?" || exit 1
    fi
    # FK-safe order: logs -> cards -> blunders -> games (-> user)
    sqlite3 "$DB" "
      DELETE FROM review_logs   WHERE user_id = $USER_ID;
      DELETE FROM review_cards  WHERE user_id = $USER_ID;
      DELETE FROM blunders      WHERE user_id = $USER_ID;
      DELETE FROM analyzed_games WHERE user_id = $USER_ID;
      $( [[ $KEEP_ACCOUNT -eq 0 ]] && echo "DELETE FROM users WHERE id = $USER_ID;" )"
    if [[ $KEEP_ACCOUNT -eq 1 ]]; then
      echo "Wiped data for '$TARGET_USER' (account kept)."
    else
      echo "Deleted user '$TARGET_USER' and all their data."
    fi
    ;;

  full)
    confirm "Delete the ENTIRE database ($DB)?" || exit 1
    rm -f "$DB"
    echo "Database deleted. Restart the backend to recreate empty tables:"
    echo "  lsof -ti :8000 | xargs kill; uv run python run.py"
    ;;
esac
