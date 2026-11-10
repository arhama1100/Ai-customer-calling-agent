"""
app/__init__.py
───────────────
Makes "app" a Python package and exposes the application factory.

Why an app FACTORY function (create_app) instead of one global app object?
- Tests can build a fresh, isolated app with a test configuration and a
  throwaway database. This is the standard, testable way to structure Flask.
- It avoids messy global state and import cycles.
"""

import os

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from .config import Config, BASE_DIR
from .routes import api_bp


def _register_error_handlers(app):
    """Give the JSON API consistent, safe error responses (Phase G3).

    Only requests under ``/api`` are reshaped, so the static single-page
    frontend keeps Flask's normal HTML behaviour. Every API error becomes a
    JSON object ``{"error", "message"}`` with an appropriate status code, and a
    real server error is logged server-side but NEVER returned with a traceback
    or any internal detail. In local debug mode an unexpected exception is
    re-raised so the developer still gets the interactive debugger; production
    (debug off) always receives the safe generic JSON.
    """

    def _is_api():
        return (request.path or "").startswith("/api")

    @app.errorhandler(413)
    def _payload_too_large(exc):  # body over MAX_CONTENT_LENGTH
        if _is_api():
            return (
                jsonify(
                    error="payload_too_large",
                    message="That request is too large to process. Please send less at once.",
                ),
                413,
            )
        return exc  # non-API: keep Flask's default response

    @app.errorhandler(429)
    def _too_many_requests(exc):
        if _is_api():
            return (
                jsonify(
                    error="rate_limited",
                    message="Too many requests - please slow down and try again shortly.",
                ),
                429,
            )
        return exc

    @app.errorhandler(HTTPException)
    def _http_error(exc):
        # 404/405/400/etc. raised by Flask for an API route -> consistent JSON.
        if _is_api():
            code = exc.code or 500
            return (
                jsonify(
                    error=exc.name.lower().replace(" ", "_"),
                    message=exc.description,
                ),
                code,
            )
        return exc  # non-API: default HTML error page

    @app.errorhandler(Exception)
    def _unexpected_error(exc):
        if app.debug:
            raise exc  # local dev: let the interactive debugger handle it
        if isinstance(exc, HTTPException):
            return _http_error(exc)
        # Real, unexpected failure: log it for the operator, tell the customer
        # nothing but a safe, generic message (no leak of internals).
        app.logger.error("Unhandled application error (%s)", type(exc).__name__)
        return (
            jsonify(
                error="internal_error",
                message="Something went wrong on our side. Please try again shortly.",
            ),
            500,
        )


def create_app(config_class=Config):
    """Build and return a configured Flask application instance.

    config_class defaults to the real Config, but tests pass their own
    TestConfig to override settings (see tests/conftest.py).
    """
    # Point Flask's static file handling at our project-root "static/" folder
    # (the plain HTML/CSS/JS frontend added in Phase 4).
    on_vercel = bool(os.getenv("VERCEL"))
    static_dir = str(
        BASE_DIR / "public" / "static" if on_vercel else BASE_DIR / "static"
    )
    # Vercel serves public/** through its CDN, but that generated directory is
    # not guaranteed to be included in the Python function bundle. Read the
    # homepage from its tracked source; its /static/... assets remain CDN files.
    homepage_dir = str(BASE_DIR / "static")
    app = Flask(
        __name__,
        # Vercel serves public/** through its CDN. Flask only serves the
        # homepage there; local runs use Flask for both the page and assets.
        static_folder=None if on_vercel else static_dir,
        static_url_path=None if on_vercel else "/static",
    )

    # Load settings (PORT, DEBUG, GEMINI_ENABLED, ...) from the config class.
    app.config.from_object(config_class)

    # Consistent, safe JSON error responses for the API (Phase G3).
    _register_error_handlers(app)

    # Attach the API routes under the /api prefix.
    app.register_blueprint(api_bp, url_prefix="/api")

    # Serve the single-page frontend at the site root ("/").
    @app.get("/")
    def index():
        return send_from_directory(homepage_dir, "index.html")

    if on_vercel:
        @app.get("/static/<path:filename>")
        def vercel_static_fallback(filename):
            """Serve source assets if a generated Vercel CDN file is absent.

            Existing files in public/static remain CDN-served. If a deployment
            is missing one of those generated files, this route keeps the same
            URL working from the tracked static/ directory.
            """
            return send_from_directory(str(BASE_DIR / "static"), filename)

    return app
