"""
tests/test_orders.py
────────────────────
Verifies the SQLite order layer and the /api/orders endpoint.

Key behaviors under test (project policy: DB is the only source of truth):
- A known order id returns exactly the data stored in SQLite.
- An unknown order id returns 404 honestly — never a made-up status.
- Lookups are case-insensitive for the customer's convenience.

Run with:  .\\.venv\\Scripts\\python.exe -m pytest
"""

from app.orders import describe_order, find_order


# ── Unit tests: the orders module against the temporary seeded DB ──

def test_find_order_returns_stored_row(db_path):
    order = find_order("ORD-1002", db_path)

    assert order is not None
    # Values must match the seed data exactly — no transformation, no guessing.
    assert order["customer_name"] == "Liam Ortiz"
    assert order["item"] == "Desk Lamp"
    assert order["quantity"] == 1
    assert order["status"] == "shipped"


def test_find_order_is_case_insensitive(db_path):
    assert find_order("ord-1002", db_path) is not None
    assert find_order("  ORD-1002  ", db_path) is not None  # spaces tolerated


def test_find_order_unknown_returns_none(db_path):
    # The layer never invents an answer for a missing order.
    assert find_order("ORD-9999", db_path) is None


def test_describe_order_wording_is_deterministic(db_path):
    text = describe_order("ORD-1001", db_path)
    assert text == "Order ORD-1001 for 2 x Ceramic Mug is currently delivered."


# ── API tests: the Flask endpoint on top of the same data ──

def test_api_order_found(client):
    response = client.get("/api/orders/ORD-1004")

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "pending"
    assert data["item"] == "Water Bottle"


def test_api_order_not_found_is_404(client):
    response = client.get("/api/orders/ORD-0000")

    assert response.status_code == 404
    data = response.get_json()
    assert data["error"] == "not_found"
    # Honest message only — it must NOT claim any status for a missing order.
    assert "could not find" in data["message"].lower()


# ── Phase 6 hardening: more lookup edge cases ──

def test_find_order_mixed_case(db_path):
    # Mixed upper/lower case must still resolve to the same row.
    assert find_order("OrD-1005", db_path) is not None


def test_find_order_empty_string_returns_none(db_path):
    # An empty id must not match anything (and must not error).
    assert find_order("", db_path) is None


def test_describe_order_unknown_is_honest(db_path):
    text = describe_order("ORD-7777", db_path)
    assert "could not find" in text.lower()
    # It must never assert a status word for a non-existent order.
    for status in ("pending", "processing", "shipped", "delivered", "cancelled"):
        assert status not in text.lower()
