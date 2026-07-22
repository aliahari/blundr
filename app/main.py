"""
FastAPI application entry point for Chess Blunder Analyzer.

This module sets up the FastAPI application with:
- CORS middleware
- Exception handlers
- API routers
- Health check endpoints
"""
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import create_tables
from .routes import (
    games_router,
    auth_router,
    analysis_router,
    reviews_router,
    settings_router,
    stats_router,
)
from .services.lichess_client import LichessClient
from .utils.exceptions import (
    LichessAPIError,
    UserNotFoundError,
    GameNotFoundError,
    InvalidDateRangeError,
    RateLimitExceededError,
    NoGamesFoundError
)

# Configure logging
logging.basicConfig(
    level=logging.INFO if settings.DEBUG else logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Lifespan manager for startup/shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager."""
    # Startup: one pooled HTTP client and LichessClient shared by every
    # request, instead of a new connection (and a naive per-instance rate
    # limiter) being created and leaked on each request.
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    await create_tables()
    # A distinct User-Agent matters here, not just as good API-citizen
    # practice: Lichess (or infra in front of it) started silently 404-ing
    # requests carrying generic HTTP-library User-Agents (httpx's/curl's
    # defaults) on /api/games/user/{username} instead of a clearer 4xx —
    # confirmed by testing the same request with a custom UA, which reached
    # the real backend (got a genuine 429) instead of the disguised 404.
    http_client = httpx.AsyncClient(
        timeout=settings.LICHESS_API_TIMEOUT,
        headers={"User-Agent": f"Blundr/{settings.APP_VERSION} (+https://blundr.ch)"},
    )
    app.state.lichess_client = LichessClient(client=http_client)
    app.state.analysis_jobs = {}  # user_id -> AnalysisJob (in-memory progress)
    logger.info("Application is ready")

    yield

    # Shutdown
    await http_client.aclose()
    logger.info("Shutting down application")


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    description="Backend service for analyzing chess blunders using spaced repetition. "
                "Fetch games from Lichess, detect blunders, and review them with spaced repetition.",
    version=settings.APP_VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"]
)

# Include API routers
app.include_router(games_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(reviews_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(stats_router, prefix="/api")


# Global exception handlers
@app.exception_handler(LichessAPIError)
async def lichess_api_error_handler(request: Request, exc: LichessAPIError):
    """Handle Lichess API errors."""
    logger.error(f"Lichess API error: {exc}")
    return JSONResponse(
        status_code=exc.status_code or 502,
        content={
            "error": "lichess_api_error",
            "detail": exc.message,
            "status_code": exc.status_code
        }
    )


@app.exception_handler(UserNotFoundError)
async def user_not_found_error_handler(request: Request, exc: UserNotFoundError):
    """Handle user not found errors."""
    logger.warning(f"User not found: {exc.username}")
    return JSONResponse(
        status_code=404,
        content={
            "error": "user_not_found",
            "detail": exc.message,
            "username": exc.username
        }
    )


@app.exception_handler(GameNotFoundError)
async def game_not_found_error_handler(request: Request, exc: GameNotFoundError):
    """Handle game not found errors."""
    logger.warning(f"Game not found: {exc.game_id}")
    return JSONResponse(
        status_code=404,
        content={
            "error": "game_not_found",
            "detail": exc.message,
            "game_id": exc.game_id
        }
    )


@app.exception_handler(InvalidDateRangeError)
async def invalid_date_range_error_handler(request: Request, exc: InvalidDateRangeError):
    """Handle invalid date range errors."""
    logger.warning(f"Invalid date range: {exc}")
    return JSONResponse(
        status_code=400,
        content={
            "error": "invalid_date_range",
            "detail": exc.message,
            "start_date": str(exc.start_date),
            "end_date": str(exc.end_date)
        }
    )


@app.exception_handler(RateLimitExceededError)
async def rate_limit_exceeded_error_handler(request: Request, exc: RateLimitExceededError):
    """Handle rate limit exceeded errors."""
    logger.warning(f"Rate limit exceeded: {exc}")
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "detail": exc.message,
            "retry_after": exc.retry_after
        },
        headers={"Retry-After": str(exc.retry_after or 60)}
    )


@app.exception_handler(NoGamesFoundError)
async def no_games_found_error_handler(request: Request, exc: NoGamesFoundError):
    """Handle no-games-found as an empty result, not an error."""
    logger.info(f"No games found: {exc}")
    return JSONResponse(
        status_code=200,
        content={
            "games": [],
            "total": 0,
            "username": exc.username,
            "date_range": {"start": str(exc.start_date), "end": str(exc.end_date)}
        }
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all for anything not covered by a specific handler above."""
    logger.error(f"Unhandled error on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": f"An unexpected error occurred: {exc}"
        }
    )


# Health check endpoints
@app.get(
    "/api/health",
    summary="Health check",
    description="Check if the API is running and healthy"
)
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "app_name": settings.APP_NAME
    }


@app.get(
    "/api/ready",
    summary="Readiness check",
    description="Check if the API is ready to receive requests"
)
async def readiness_check():
    """Readiness check endpoint."""
    return {
        "status": "ready",
        "version": settings.APP_VERSION
    }


@app.get(
    "/",
    summary="Root endpoint",
    description="Root endpoint with API information"
)
async def root():
    """Root endpoint."""
    return {
        "app_name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/api/docs",
        "health": "/api/health"
    }


# Run the application
def main():
    """Run the application with uvicorn."""
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info"
    )


if __name__ == "__main__":
    main()
