"""Idempotently copy the legacy SQLite orders and call_sessions to Neon.

Run with DATABASE_URL set to Neon's pooled connection string. The source file
is read-only and is never renamed, modified, or removed. Existing destination
rows are retained; conflicting values for an existing primary key abort the
transaction instead of silently overwriting data.
"""

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DB = ROOT / "data" / "shop.db"
sys.path.insert(0, str(ROOT))
TABLES = {
    "orders": ("order_id", (
        "order_id", "customer_name", "item", "quantity", "status", "created_at",
    )),
    "call_sessions": ("session_id", (
        "session_id", "channel", "consent", "status", "started_at", "ended_at",
        "transcript", "intents", "primary_intent", "outcome", "human_followup",
        "summary",
    )),
}


def _source_rows(source, table, columns):
    names = ", ".join(columns)
    if table == "call_sessions":
        rows = source.execute(
            f"SELECT {names} FROM {table} ORDER BY started_at, session_id"
        ).fetchall()
    else:
        rows = source.execute(f"SELECT {names} FROM {table} ORDER BY order_id").fetchall()
    return [dict(row) for row in rows]


def migrate(source_path=SOURCE_DB):
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL must contain the Neon pooled connection string")
    if not Path(source_path).is_file():
        raise FileNotFoundError(f"SQLite source database not found: {source_path}")

    source = sqlite3.connect(f"file:{Path(source_path).resolve().as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        for table in TABLES:
            if source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone() is None:
                raise RuntimeError(f"Required source table is missing: {table}")
        rows = {
            table: _source_rows(source, table, columns)
            for table, (_, columns) in TABLES.items()
        }
    finally:
        source.close()

    # Lazy import lets offline tests inspect the utility without Psycopg.
    from app.database import get_connection, init_db

    init_db()  # creates both schemas only; never seeds or overwrites records
    destination = get_connection()
    try:
        for table, (primary_key, columns) in TABLES.items():
            names = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))
            insert = (
                f"INSERT INTO {table} ({names}) VALUES ({placeholders}) "
                f"ON CONFLICT ({primary_key}) DO NOTHING"
            )
            for record in rows[table]:
                destination.execute(insert, tuple(record[column] for column in columns))

            # Compare every source row and column, including JSON text, NULLs,
            # consent/outcome values and original timestamp strings.
            for record in rows[table]:
                found = destination.execute(
                    f"SELECT {names} FROM {table} WHERE {primary_key} = %s",
                    (record[primary_key],),
                ).fetchone()
                if found is None or dict(found) != record:
                    raise RuntimeError(
                        f"Data verification failed for {table} "
                        f"primary key {record[primary_key]!r}"
                    )

            destination_count = destination.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()["count"]
            source_count = len(rows[table])
            if destination_count != source_count:
                raise RuntimeError(
                    f"Row-count mismatch for {table}: source={source_count}, "
                    f"destination={destination_count}"
                )
        destination.commit()
        return {table: len(table_rows) for table, table_rows in rows.items()}
    except Exception:
        destination.rollback()
        raise
    finally:
        destination.close()


def main():
    try:
        counts = migrate()
    except Exception as exc:
        # Driver exception strings can contain connection details. Never print
        # the exception text or the DATABASE_URL.
        print(f"Migration failed ({type(exc).__name__}); no success declared.", file=sys.stderr)
        raise SystemExit(1) from None
    print("Migration and row-by-row verification completed:")
    for table, count in counts.items():
        print(f"  {table}: {count} source rows verified in Neon")


if __name__ == "__main__":
    main()
