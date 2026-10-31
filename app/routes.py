"""
app/routes.py
─────────────
Defines the REST API "blueprint" — a grouped collection of URL endpoints.

Why a Blueprint?
- It keeps all API routes in one place, separate from the app setup.
- We register this blueprint under the "/api" prefix in create_app(), so the
  function below is reachable at "/api/health".

Phase 1 only has a single "health check" endpoint. More endpoints (orders,
chat, etc.) are added in later phases.
"""

from flask import Blueprint, current_app, jsonify, request, send_from_directory

from .assistant import FALLBACK_REPLY, respond
from .orders import find_order

# A Blueprint is like a mini-app we can attach routes to and register later.
api_bp = Blueprint("api", __name__)


@api_bp.get("/health")
def health():
    """Health check: a simple endpoint to prove the server is running.

    Returns JSON so any client (browser JS, curl, tests) can read it easily.
    """
    return jsonify(status="ok"), 200


@api_bp.get("/orders/<order_id>")
def get_order(order_id):
    """Look up one fictional shop order by its id in SQLite.

    The answer comes ONLY from the database — this endpoint never guesses.
    Found   -> 200 with the order rows's fields as JSON.
    Missing -> 404 with an honest "not found" message.
    """
    # DB_PATH lives in app config, so tests can point at a temporary database.
    db_path = current_app.config["DB_PATH"]
    order = find_order(order_id, db_path)

    if order is None:
        return (
            jsonify(
                error="not_found",
                order_id=order_id,
                message=f"I could not find order {order_id} in our system.",
            ),
            404,
        )

    return jsonify(order), 200


@api_bp.post("/chat")
def chat():
    """The text/voice chat entry point.

    Expects JSON: {"message": "where is my order ORD-1002?"}
    The reply is produced by the DETERMINISTIC assistant, which only uses the
    SQLite orders and the approved FAQ. Returns:
        {"intent": ..., "reply": ..., "source": ...}
    """
    payload = request.get_json(silent=True)
    # Accept only a JSON object; a list/scalar/None body degrades to an empty
    # message instead of crashing (payload.get would fail on non-dicts).
    if not isinstance(payload, dict):
        payload = {}
    message = payload.get("message", "")

    db_path = current_app.config["DB_PATH"]
    try:
        result = respond(message, db_path=db_path)
    except Exception:
        # Defense in depth: the assistant is designed never to raise, but if an
        # unexpected error occurs we must NOT leak a 500 HTML page, a traceback,
        # or any internal detail to the customer. Return the same honest JSON
        # shape with a polite fallback (never claim an issue was resolved).
        result = {"intent": "fallback", "reply": FALLBACK_REPLY, "source": "none"}
    return jsonify(result), 200
