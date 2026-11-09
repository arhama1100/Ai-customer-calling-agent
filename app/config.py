"""
app/config.py
─────────────
Central configuration for the Flask app.

Why this file exists:
- Instead of hard-coding settings (port, debug, feature flags) all over the
  code, we read them from environment variables supplied by the shell/host.
- This module deliberately does not load dotenv files.
"""

import os
from pathlib import Path
from dotenv import load_dotenv


# BASE_DIR = the project root folder (the one that contains "app/", "static/", etc.)
# __file__ is this config.py, so .parent goes app/ -> project root.
BASE_DIR = Path(__file__).resolve().parent.parent

# Local convenience only; hosting environments provide variables directly.
# Tests set PYTHON_DOTENV_DISABLED before importing this module so no local
# dotenv file is touched during the offline suite.
load_dotenv(BASE_DIR / ".env")

def _as_bool(value, default=False):
    """Turn env strings like 'true'/'false'/'1'/'0' into real Python booleans."""
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _as_int(value, default):
    """Parse an env integer safely, falling back to ``default``.

    A bad or missing value must never crash startup (G3.4: safe environment
    defaults), so anything non-numeric simply uses the documented default.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Config:
    """Settings the Flask app reads at startup."""

    # Absolute path to the plain frontend folder that Flask will serve.
    BASE_DIR = BASE_DIR
    STATIC_DIR = BASE_DIR / "static"

    # Production uses PostgreSQL. DB_PATH remains unset except in tests, where
    # an explicit temporary path selects the isolated SQLite test adapter.
    DATABASE_URL = os.getenv("DATABASE_URL")
    DB_PATH = None

    # Host the server binds to. Default is loopback (127.0.0.1) so a local run
    # is never accidentally exposed on the LAN. Override with FLASK_HOST only
    # when you deliberately want to listen on another interface.
    HOST = os.getenv("FLASK_HOST", "127.0.0.1")

    # Port the dev server listens on (default 5000).
    PORT = int(os.getenv("FLASK_PORT", "5000"))

    # Debug/reloader is now OFF by default: enabling the Werkzeug interactive
    # debugger is dangerous on a public/prod host, so production must opt in
    # explicitly via FLASK_DEBUG. Local developers keep their convenience via
    # run.py, which turns debug/auto-reload ON for the loopback dev server
    # unless they set FLASK_DEBUG=false (see run.py).
    DEBUG = _as_bool(os.getenv("FLASK_DEBUG"), default=False)

    # ── Request hardening (Phase G3) ───────────────────────────────────────
    # Hard cap on the SIZE of any request body Flask will read (bytes). A body
    # larger than this is rejected with 413 before our views run, so a client
    # cannot make the server buffer an unbounded payload. Sized for the app's
    # real traffic: a short chat message is well under 1 KB, so 16 KB is ample.
    MAX_CONTENT_LENGTH = _as_int(os.getenv("MAX_CONTENT_LENGTH"), 16 * 1024)

    # Softer, application-level cap on the chat MESSAGE length (characters).
    # A longer message returns a friendly 413 JSON error instead of being
    # processed. Set it to 0 to disable the length check (hard cap still applies).
    MAX_MESSAGE_LENGTH = _as_int(os.getenv("MAX_MESSAGE_LENGTH"), 500)

    # ── Lightweight in-memory rate limiting (Phase G3, see app/rate_limit.py) ──
    # ON by default for real runs; disabled in tests (TestConfig omits these).
    # Counts are PER PROCESS and are NOT shared across workers/restarts - that
    # limitation is documented in the README and the final report.
    RATE_LIMIT_ENABLED = _as_bool(os.getenv("RATE_LIMIT_ENABLED"), default=True)
    RATE_LIMIT_MAX = _as_int(os.getenv("RATE_LIMIT_MAX"), 30)
    RATE_LIMIT_WINDOW_SECONDS = _as_int(os.getenv("RATE_LIMIT_WINDOW_SECONDS"), 60)

    # Gemini is OPTIONAL and OFF by default. It is never a source of truth
    # for orders or policies: order facts come only from PostgreSQL and policy
    # answers only from the approved FAQ, both routed in app/assistant.py.
    GEMINI_ENABLED = _as_bool(os.getenv("GEMINI_ENABLED"), default=False)

    # Non-secret Gemini settings. The API KEY is intentionally NOT stored here
    # (it is read from os.environ at call time inside app/gemini.py, and is
    # never hardcoded, logged, returned, or kept on the config object).
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

    def _gemini_timeout():
        raw = os.getenv("GEMINI_TIMEOUT")
        try:
            seconds = float(raw) if raw else 10.0
        except (TypeError, ValueError):
            seconds = 10.0
        return seconds if seconds > 0 else 10.0

    # Per-request timeout (seconds) so a slow Gemini call can never hang Flask;
    # app/gemini.py reads the same env and falls back to this default.
    GEMINI_TIMEOUT = _gemini_timeout()
