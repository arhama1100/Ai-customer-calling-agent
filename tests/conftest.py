"""
tests/conftest.py
─────────────────
Shared pytest "fixtures" — setup helpers that tests can request by name.

Why here:
- pytest automatically loads conftest.py and makes these fixtures available
  to every test file in tests/.
- We build the app with a dedicated TestConfig so tests never touch the real
  .env settings (and, in later phases, never touch the real database).

Two fixtures are defined:
- app    -> a fresh Flask app instance configured for testing
- client -> a test client that can call our API without starting a real server
"""

import pytest

from app import create_app
from app.seed import FICTIONAL_ORDERS, seed_orders


@pytest.fixture(autouse=True)
def _no_live_gemini(monkeypatch):
    """Guarantee the suite never makes a real Gemini network call.

    ``app.gemini.is_enabled()`` reads ``os.environ`` at call time, so a
    developer's local ``.env`` (GEMINI_ENABLED=true + a real key) would
    otherwise push ``assistant.respond()`` down the live network path during
    unrelated tests. We clear those variables for every test so Gemini stays
    off by default and no call leaves the machine. Tests that need Gemini ON
    re-enable it explicitly (and always MOCK the wrapper), so none of them
    touch the network either. This is test infrastructure only - the
    application's dotenv/env behaviour is unchanged.
    """
    for key in ("GEMINI_ENABLED", "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)
    yield


class TestConfig:
    """Configuration used ONLY during tests."""

    TESTING = True
    DEBUG = False
    # Keep the optional Gemini feature off during tests.
    GEMINI_ENABLED = False
    PORT = 5000
    # Placeholder; the real value is injected per-test by the db_path fixture.
    DB_PATH = None


@pytest.fixture
def db_path(tmp_path):
    """Create a throwaway, seeded SQLite DB unique to each test.

    tmp_path is a pytest built-in that gives every test its own temporary
    folder that is cleaned up automatically. This guarantees tests never
    read from or write to the developer's real data/shop.db.
    """
    path = str(tmp_path / "test_shop.db")
    seed_orders(FICTIONAL_ORDERS, db_path=path)
    return path


@pytest.fixture
def app(db_path):
    """Create a new, isolated app wired to the temporary test database."""
    app = create_app(TestConfig)
    app.config["DB_PATH"] = db_path  # override to use this test's temp DB
    return app


@pytest.fixture
def client(app):
    """A test client bound to the app fixture above."""
    return app.test_client()
