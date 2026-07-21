#!/bin/sh
# Fails fast and clearly if the pinned Stockfish binary's CPU requirement
# (baked in at build time via STOCKFISH_VARIANT) isn't met by this host —
# otherwise the first symptom is a cryptic "Illegal instruction" crash deep
# inside a background analysis job, which is what motivated this check.
set -e

REQUIRED_FLAG=""
# The variant baked into the image is recorded at build time (see Dockerfile)
if [ -f /usr/local/bin/stockfish.variant ]; then
  VARIANT=$(cat /usr/local/bin/stockfish.variant)
  case "$VARIANT" in
    avx2|bmi2|avx512*|vnni*|avxvnni) REQUIRED_FLAG="avx2" ;;
    sse41-popcnt) REQUIRED_FLAG="popcnt" ;;
    *) REQUIRED_FLAG="" ;;
  esac
fi

if [ -n "$REQUIRED_FLAG" ]; then
  if ! grep -qw "$REQUIRED_FLAG" /proc/cpuinfo 2>/dev/null; then
    echo "==================================================================" >&2
    echo "FATAL: this image's Stockfish binary was built for CPU feature" >&2
    echo "'$REQUIRED_FLAG' (variant: $VARIANT), but this host's CPU doesn't" >&2
    echo "advertise it (checked /proc/cpuinfo)." >&2
    echo "" >&2
    echo "Rebuild with a more conservative variant, e.g.:" >&2
    echo "  docker build --build-arg STOCKFISH_VARIANT=sse41-popcnt \\" >&2
    echo "               --build-arg STOCKFISH_SHA256=<matching checksum> ." >&2
    echo "See the Dockerfile header for how to get the matching checksum." >&2
    echo "==================================================================" >&2
    exit 1
  fi
fi

exec "$@"
