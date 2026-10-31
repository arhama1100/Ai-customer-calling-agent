"""
run.py
──────
Single entry point to start the Flask development server.

Usage (inside the .venv):
    .\\.venv\\Scripts\\python.exe run.py

Then open:  http://127.0.0.1:5000/api/health
"""

from app import create_app
from app.config import Config

# Build the app using our real configuration.
app = create_app()


if __name__ == "__main__":
    # host 127.0.0.1 keeps the dev server local-only (not exposed on the LAN).
    # debug=True enables auto-reload when files change (development only).
    app.run(host="127.0.0.1", port=Config.PORT, debug=Config.DEBUG)
