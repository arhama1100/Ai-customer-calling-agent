"""
app/__init__.py
───────────────
Makes "app" a Python package and exposes the application factory.

Why an app FACTORY function (create_app) instead of one global app object?
- Tests can build a fresh, isolated app with a test configuration and a
  throwaway database. This is the standard, testable way to structure Flask.
- It avoids messy global state and import cycles.
"""

from flask import Flask, send_from_directory

from .config import Config, BASE_DIR
from .routes import api_bp


def create_app(config_class=Config):
    """Build and return a configured Flask application instance.

    config_class defaults to the real Config, but tests pass their own
    TestConfig to override settings (see tests/conftest.py).
    """
    # Point Flask's static file handling at our project-root "static/" folder
    # (the plain HTML/CSS/JS frontend added in Phase 4).
    static_dir = str(BASE_DIR / "static")
    app = Flask(
        __name__,
        static_folder=static_dir,
        static_url_path="/static",
    )

    # Load settings (PORT, DEBUG, GEMINI_ENABLED, ...) from the config class.
    app.config.from_object(config_class)

    # Attach the API routes under the /api prefix.
    app.register_blueprint(api_bp, url_prefix="/api")

    # Serve the single-page frontend at the site root ("/").
    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    return app
