"""Offline database adapter checks; these never contact Neon."""

import pytest

from app.database import get_connection, init_db
from app.seed import seed_orders


def test_explicit_sqlite_path_is_an_isolated_dictionary_row_adapter(tmp_path):
    path = str(tmp_path / "isolated.db")
    seed_orders([("ORD-9001", "Test Customer", "Test Item", 1, "pending", "2026-01-01")], path)

    conn = get_connection(path)
    try:
        row = conn.execute(
            "SELECT order_id, status FROM orders WHERE order_id = %s", ("ORD-9001",)
        ).fetchone()
        assert row["order_id"] == "ORD-9001"
        assert row["status"] == "pending"
    finally:
        conn.close()


def test_init_db_is_idempotent_and_creates_both_tables(tmp_path):
    path = str(tmp_path / "schema.db")
    init_db(path)
    init_db(path)
    conn = get_connection(path)
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert {"orders", "call_sessions"} <= tables


def test_postgres_access_requires_explicit_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        get_connection()


def test_postgres_pool_is_bounded_ssl_and_does_not_connect_at_import(monkeypatch):
    import psycopg_pool
    import app.database as database

    captured = {}

    class FakePool:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def open(self, wait=False):
            captured["opened_without_wait"] = not wait

    monkeypatch.setattr(psycopg_pool, "ConnectionPool", FakePool)
    monkeypatch.setattr(database, "_pool", None)
    monkeypatch.setattr(database, "_pool_dsn", None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://placeholder.invalid/db")

    database._get_pool()

    assert captured["min_size"] == 0
    assert captured["max_size"] == 5
    assert captured["kwargs"]["sslmode"] == "require"
    assert captured["kwargs"]["row_factory"].__name__ == "dict_row"
    assert captured["opened_without_wait"] is True
