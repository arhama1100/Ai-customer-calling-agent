"""Initialize the PostgreSQL schema without inserting sample orders."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import init_db


if __name__ == "__main__":
    try:
        init_db()
    except Exception as exc:
        # Do not print driver messages, which may include connection details.
        print(f"PostgreSQL schema initialization failed ({type(exc).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None
    print("PostgreSQL connected; orders and call_sessions schemas are ready.")
