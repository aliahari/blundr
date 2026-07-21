"""
Test configuration.

Points the app at a throwaway SQLite file BEFORE any app module is imported
(pydantic-settings reads the environment when app.config instantiates), so
tests never touch the real blundr.db.
"""
import os
import pathlib

_TEST_DB = pathlib.Path(__file__).parent / "test_blundr.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB}"
os.environ["JWT_SECRET"] = "test-secret"
# Don't call the real Lichess API to validate accounts during tests
os.environ["VALIDATE_LICHESS_ACCOUNT"] = "false"

# ONE shared TestClient for every test module. Each TestClient runs its own
# event loop, but the app's shared httpx pool (app.state.lichess_client)
# binds its locks to whichever loop first uses it — two module-level
# TestClients therefore poison each other the moment both touch Lichess.
# The env setup above must happen before this import.
from contextlib import ExitStack  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

_client_stack = ExitStack()
client = _client_stack.enter_context(TestClient(app))
