# Chess Blunder Analyzer Backend Dockerfile
# Uses uv for dependency management

# --- Stockfish: pin an exact, checksum-verified release build -------------
# python-chess spawns Stockfish as a local subprocess (stdin/stdout pipes),
# so it lives in this image rather than a separate container.
#
# To upgrade:
#   1. Pick a new tag + asset from https://github.com/official-stockfish/Stockfish/releases
#      (asset naming: stockfish-ubuntu-x86-64-<variant>.tar)
#   2. Download it and compute: shasum -a 256 <file>
#   3. Pass the three values as --build-arg (or update the defaults below)
#   4. Validate BEFORE deploying: uv run python scripts/compare_detection.py
#      (old vs new engine agreement) and scripts/bench_analysis.py (timing) —
#      a new engine version can shift which moves cross the blunder threshold.
#
# STOCKFISH_VARIANT picks the CPU instruction-set build. avx2 fits most
# current VPS hardware (Hetzner/DigitalOcean/Linode/Vultr standard tiers).
# If your VPS's CPU lacks AVX2 (check `grep avx2 /proc/cpuinfo` on the VPS),
# override with sse41-popcnt and its own sha256 — the checksum only matches
# one exact (version, variant) pair on purpose, so a mismatched override
# fails the build instead of silently running the wrong binary.
FROM python:3.11-slim AS stockfish
ARG STOCKFISH_VERSION=sf_18
ARG STOCKFISH_VARIANT=avx2
ARG STOCKFISH_SHA256=536c0c2c0cf06450df0bfb5e876ef0d3119950703a8f143627f990c7b5417964

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/sf
RUN curl -fsSL -o stockfish.tar \
      "https://github.com/official-stockfish/Stockfish/releases/download/${STOCKFISH_VERSION}/stockfish-ubuntu-x86-64-${STOCKFISH_VARIANT}.tar" \
    && echo "${STOCKFISH_SHA256}  stockfish.tar" | sha256sum -c - \
    && tar -xf stockfish.tar "stockfish/stockfish-ubuntu-x86-64-${STOCKFISH_VARIANT}" \
    && mv "stockfish/stockfish-ubuntu-x86-64-${STOCKFISH_VARIANT}" /usr/local/bin/stockfish \
    && chmod +x /usr/local/bin/stockfish \
    && echo "${STOCKFISH_VARIANT}" > /usr/local/bin/stockfish.variant

# --- App image --------------------------------------------------------------
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -Ls https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Copy project files. README.md is required here (not just for docs) —
# pyproject.toml's hatchling build reads it as package metadata, so `uv
# sync` fails without it.
COPY pyproject.toml .
COPY uv.lock .
COPY .uvignore .
COPY .python-version .
COPY README.md .

# Install dependencies with uv (uv will use pyproject.toml)
RUN uv sync --frozen

# Copy the rest of the application
COPY . .

# Make the entrypoint executable
RUN chmod +x run.py

# Pinned, checksum-verified Stockfish binary (see stage above)
COPY --from=stockfish /usr/local/bin/stockfish /usr/local/bin/stockfish
COPY --from=stockfish /usr/local/bin/stockfish.variant /usr/local/bin/stockfish.variant

# Fails fast with an actionable message if this host's CPU can't run the
# baked-in Stockfish variant, instead of "Illegal instruction" surfacing
# deep inside a background analysis job the first time someone syncs.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV STOCKFISH_PATH=/usr/local/bin/stockfish

# Expose port
EXPOSE 8000

# Run the application using uv
CMD ["uv", "run", "python", "run.py"]
