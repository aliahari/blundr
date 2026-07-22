"""
Application configuration using Pydantic Settings.
"""
from typing import List
from typing_extensions import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode


def _split_csv(value):
    """
    Accept a comma-separated string for List[str] settings fields.

    pydantic-settings auto-decodes list-typed env values as JSON *before*
    any validator runs — NoDecode (below) turns that off for these two
    fields so this "before" validator actually receives the raw string.
    Every place this project documents these fields (.env.example, README,
    docker-compose.yml) uses plain comma-separated values — much more
    natural to hand-write than a quoted JSON array in a shell/.env context.
    """
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


CsvList = Annotated[List[str], NoDecode]


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    APP_NAME: str = "Chess Blunder Analyzer"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    # CORS settings. "*" is rejected by browsers/CORSMiddleware once
    # allow_credentials=True is set (which this app does), so default to an
    # explicit origin list matching the frontend dev server instead.
    CORS_ORIGINS: CsvList = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Lichess API settings
    LICHESS_API_BASE_URL: str = "https://lichess.org/api"
    LICHESS_API_TIMEOUT: float = 30.0
    LICHESS_RATE_LIMIT_DELAY: float = 1.0  # Seconds between requests
    # Optional Personal Access Token (https://lichess.org/account/oauth/token/create,
    # no scopes needed — game data is public). Lichess throttles the games
    # export stream by who's asking: 20 games/s anonymous, 30/s OAuth
    # authenticated, 60/s authenticated fetching your own games. Leave empty
    # to call anonymously.
    LICHESS_TOKEN: str = ""

    # Default settings for game fetching
    DEFAULT_MAX_GAMES: int = 100
    DEFAULT_GAME_TYPES: CsvList = ["rapid", "blitz", "classical"]

    _split_cors = field_validator("CORS_ORIGINS", mode="before")(_split_csv)
    _split_game_types = field_validator("DEFAULT_GAME_TYPES", mode="before")(_split_csv)

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./blundr.db"

    # Auth
    JWT_SECRET: str = "dev-secret-change-me"  # override via .env in any real deployment
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRES_MINUTES: int = 60 * 24 * 7  # 7 days

    # Password reset. Link points back at the frontend, which reads the
    # token from a query param (there's no client-side router — see App.tsx).
    FRONTEND_URL: str = "http://localhost:3000"
    PASSWORD_RESET_TOKEN_EXPIRES_MINUTES: int = 30

    # SMTP for password-reset emails. Leave SMTP_HOST empty for local dev —
    # the email service logs the reset link to the console instead of
    # sending, so the flow works end to end without real credentials.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "Blundr <noreply@blundr.local>"
    SMTP_USE_TLS: bool = True

    # Verify the Lichess account exists during registration (disabled in tests)
    VALIDATE_LICHESS_ACCOUNT: bool = True

    # Engine analysis. Depth-limited search is far faster than a fixed time
    # per position (simple positions resolve in milliseconds instead of
    # always burning the full budget). Depth 12 was the shallowest setting
    # that matched a depth-20 ground truth on real games — depth 10 missed
    # an endgame blunder (see scripts/compare_detection.py).
    STOCKFISH_PATH: str = "stockfish"  # resolved via PATH by default
    ANALYSIS_DEPTH: int = 12  # fixed search depth per position
    ANALYSIS_MAX_TIME_PER_POSITION: float = 0.5  # hard cap so no position can stall
    ANALYSIS_CONCURRENCY: int = 4  # parallel engine workers per analysis job
    BLUNDER_WINPROB_THRESHOLD: float = 25.0  # win-% drop that counts as a blunder
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        # .env is shared with docker-compose (DOMAIN, STOCKFISH_* build args
        # — see .env.example) which aren't Settings fields; without this,
        # Settings() crashes on any key it doesn't recognize.
        extra = "ignore"


# Create settings instance
settings = Settings()
