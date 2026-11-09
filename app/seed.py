"""
app/seed.py
───────────
Fills the configured PostgreSQL database with FICTIONAL sample orders.
An explicit db_path uses the isolated SQLite test adapter.

IMPORTANT — all data below is invented for demo purposes:
fake names, fake order ids, no real contact details. Never put real customer
data in this file.

Run it manually (from the project root, inside the venv):
    .\\.venv\\Scripts\\python.exe -m app.seed
"""

from .database import get_connection, init_db

# Each tuple: (order_id, customer_name, item, quantity, status, created_at)
# The list deliberately covers ALL five allowed statuses so we can test each.
FICTIONAL_ORDERS = [
    ("ORD-1001", "Ava Chen", "Ceramic Mug", 2, "delivered", "2026-10-01"),
    ("ORD-1002", "Liam Ortiz", "Desk Lamp", 1, "shipped", "2026-10-05"),
    ("ORD-1003", "Noor Haddad", "Yoga Mat", 1, "processing", "2026-10-10"),
    ("ORD-1004", "Sofia Rossi", "Water Bottle", 3, "pending", "2026-10-12"),
    ("ORD-1005", "Jack Miller", "Notebook Set", 2, "cancelled", "2026-10-14"),
    ("ORD-1006", "Mina Park", "Fountain Pen", 1, "shipped", "2026-10-18"),
]


def seed_orders(orders, db_path=None):
    """Insert orders into the database (replaces any row with the same id).

    Returns how many rows were written. Uses parameterized queries — never
    string concatenation — which prevents
    SQL injection and formatting bugs.
    """
    orders = list(orders)
    init_db(db_path)  # make sure the table exists before writing
    conn = get_connection(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO orders
                (order_id, customer_name, item, quantity, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (order_id) DO UPDATE SET
                customer_name = excluded.customer_name,
                item = excluded.item,
                quantity = excluded.quantity,
                status = excluded.status,
                created_at = excluded.created_at
            """,
            orders,
        )
        conn.commit()
        return len(orders)
    finally:
        conn.close()


def main():
    """Entry point when run as: python -m app.seed"""
    count = seed_orders(FICTIONAL_ORDERS)
    print(f"Seeded fictional orders into the database. Rows written: {count}")
    for order in FICTIONAL_ORDERS:
        # order[0] = id, order[1] = name, order[4] = status
        print(f"  {order[0]}  {order[1]:<12} {order[4]}")


if __name__ == "__main__":
    main()
