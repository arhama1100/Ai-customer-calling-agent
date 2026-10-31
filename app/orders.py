"""
app/orders.py
─────────────
Read-only queries over the orders table. This is the ONLY place order
information is turned into answers.

Design rule:
- Functions here return data strictly as stored in SQLite.
- They never guess: unknown order id => None (and the caller says so honestly).
"""

from .database import get_connection


def find_order(order_id, db_path=None):
    """Look up one order by its id.

    Returns a plain dict (easy to convert to JSON) or None if not found.
    The lookup is case-insensitive on the order id for user friendliness
    ("ord-1001" works as well as "ORD-1001").
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM orders WHERE upper(order_id) = upper(?)",
            (order_id.strip(),),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return dict(row)  # sqlite3.Row -> ordinary dict


def describe_order(order_id, db_path=None):
    """Turn one DB row into the exact sentence the assistant may say.

    Keeping this in ONE function means the wording is deterministic and
    testable — the API, the assistant, and the frontend all reuse it.
    """
    order = find_order(order_id, db_path)
    if order is None:
        return (
            f"I could not find order {order_id} in our system. "
            "Please double-check the order number and try again."
        )
    return (
        f"Order {order['order_id']} for {order['quantity']} x "
        f"{order['item']} is currently {order['status']}."
    )
