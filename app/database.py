"""Database connections and portable schema initialization.

Production uses Neon PostgreSQL through a small bounded Psycopg 3 pool. Passing
an explicit path selects SQLite, which is intentionally retained for isolated
offline tests and the one-time legacy-data migration source.
"""

import os
import sqlite3
import threading

_pool = None
_pool_dsn = None
_pool_lock = threading.Lock()

ORDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    item TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'processing', 'shipped', 'delivered', 'cancelled')
    ),
    created_at TEXT NOT NULL
)
"""


class _SQLiteAdapter:
    """Expose the small connection API used by the app over SQLite for tests."""

    def __init__(self, connection):
        self._connection = connection

    @property
    def total_changes(self):
        return self._connection.total_changes

    @staticmethod
    def _sql(statement):
        return statement.replace("%s", "?")

    def execute(self, statement, params=()):
        if statement.lstrip().startswith("INSERT INTO call_sessions"):
            # Serialize the test adapter's sequence assignment. PostgreSQL
            # uses its native identity sequence and never enters this branch.
            self._connection.execute("BEGIN IMMEDIATE")
            next_order = self._connection.execute(
                "SELECT COALESCE(MAX(created_order), 0) + 1 FROM call_sessions"
            ).fetchone()[0]
            statement = statement.replace(
                "(session_id, channel, consent, status, started_at)",
                "(session_id, channel, consent, status, started_at, created_order)",
            ).replace(
                "VALUES (%s, %s, 1, 'active', %s)",
                "VALUES (%s, %s, 1, 'active', %s, %s)",
            )
            params = tuple(params) + (next_order,)
        return self._connection.execute(self._sql(statement), params)

    def executemany(self, statement, params):
        return self._connection.executemany(self._sql(statement), params)

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        return self._connection.close()


class _PooledConnection:
    """Return checked-out connections to psycopg_pool when callers close them."""

    def __init__(self, pool, connection):
        self._pool = pool
        self._connection = connection
        self._closed = False

    def execute(self, statement, params=()):
        return self._connection.execute(statement, params)

    def executemany(self, statement, params):
        return self._connection.executemany(statement, params)

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._connection.info.transaction_status != 0:  # 0 = IDLE
                self._connection.rollback()
        finally:
            self._pool.putconn(self._connection)


def _get_pool():
    """Create a lazy, bounded pool using the Neon pooled URL and required TLS."""
    global _pool, _pool_dsn
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL database access")
    with _pool_lock:
        if _pool is None or _pool_dsn != dsn:
            if _pool is not None:
                _pool.close()
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            _pool = ConnectionPool(
                conninfo=dsn,
                min_size=0,
                max_size=5,
                open=False,
                kwargs={"sslmode": "require", "row_factory": dict_row},
            )
            _pool.open(wait=False)
            _pool_dsn = dsn
        return _pool


def get_connection(db_path=None):
    """Get a SQLite test/migration connection or pooled PostgreSQL connection.

    SQLite is selected only by an explicit path. Normal application traffic
    requires DATABASE_URL and can never silently fall back to a local file.
    """
    if db_path is not None:
        connection = sqlite3.connect(str(db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return _SQLiteAdapter(connection)
    pool = _get_pool()
    return _PooledConnection(pool, pool.getconn())


def _statements(script):
    return [part.strip() for part in script.split(";") if part.strip()]


def init_db(db_path=None):
    """Create both application tables without deleting or seeding any data."""
    from .sessions import SESSIONS_SCHEMA

    conn = get_connection(db_path)
    try:
        sessions_schema = SESSIONS_SCHEMA
        if db_path is not None:
            # SQLite is a test-only adapter; production uses PostgreSQL identity.
            sessions_schema = sessions_schema.replace(
                "BIGINT GENERATED BY DEFAULT AS IDENTITY UNIQUE",
                "INTEGER NOT NULL DEFAULT 0 UNIQUE",
            )
        for statement in _statements(ORDERS_SCHEMA):
            conn.execute(statement)
        for statement in _statements(sessions_schema):
            conn.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
