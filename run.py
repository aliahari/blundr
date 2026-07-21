#!/usr/bin/env python3
"""
Run script for the Chess Blunder Analyzer backend.

This script starts the FastAPI application using uvicorn.
Usage: uv run python run.py
Or: python run.py (if running in the virtual environment)
"""
import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info",
        server_header=False,
        date_header=False
    )
