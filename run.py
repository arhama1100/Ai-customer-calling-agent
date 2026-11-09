
"""
run.py
──────
Single entry point to start the Flask development server.

Usage (inside the .venv):
    .\\.venv\\Scripts\\python.exe run.py

Then open:  http://127.0.0.1:5000/api/health
"""

import os

from app import create_app
from app.config import Config, _as_bool

# Build the app using our real configuration.
app = create_app()


if __name__ == "__main__":
    # This launcher is the LOCAL development server only. Debug/auto-reload is
    # convenient here and the server binds to loopback by default, so we turn it
    # ON unless the developer explicitly set FLASK_DEBUG=false. Production never
    # uses run.py: it serves create_app() behind a WSGI server, which reads the
    # safe Config.DEBUG default (OFF) and therefore never exposes the debugger.
    debug = _as_bool(os.getenv("FLASK_DEBUG"), default=True)

    # host defaults to 127.0.0.1 (Config.HOST) to keep the dev server local-only
    # (not exposed on the LAN); set FLASK_HOST to override deliberately.
    app.run(host=Config.HOST, port=Config.PORT, debug=debug)
