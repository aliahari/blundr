"""
API routes for the Chess Blunder Analyzer.
"""
from .games import router as games_router
from .auth import router as auth_router
from .analysis import router as analysis_router
from .reviews import router as reviews_router
from .settings import router as settings_router
from .stats import router as stats_router

__all__ = [
    "games_router",
    "auth_router",
    "analysis_router",
    "reviews_router",
    "settings_router",
    "stats_router",
]
