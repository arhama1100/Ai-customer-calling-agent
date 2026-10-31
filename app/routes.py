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

from . import rate_limit
from .assistant import FALLBACK_REPLY, respond
from .orders import find_order

# A Blueprint is like a mini-app we can attach routes to and register later.
api_bp = Blueprint("api", __name__)

# Fallback message length used when app.config has no MAX_MESSAGE_LENGTH.
# TestConfig does not set it, so the API still guards runaway input in tests
# (no existing test sends a message anywhere near this limit).
DEFAULT_MAX_MESSAGE_LENGTH = 500


def _rate_limit_response():
    """Return a 429 JSON response if this endpoint/IP is over its sliding-window
    budget, otherwise ``None`` so the request proceeds.

    Rate limiting is active only when ``app.config['RATE_LIMIT_ENABLED']`` is
    truthy. Tests leave it unset (falsy), so the automated suite is never
    throttled; real runs enable it by default (see app/config.py).
    """
    if not current_app.config.get("RATE_LIMIT_ENABLED", False):
        return None
    key = f"{request.endpoint}:{request.remote_addr}"
    allowed, retry_after = rate_limit.check(
        key,
        current_app.config.get("RATE_LIMIT_MAX", 30),
        current_app.config.get("RATE_LIMIT_WINDOW_SECONDS", 60),
    )
    if allowed:
        return None
    response = jsonify(
        error="rate_limited",
        message="Too many requests - please slow down and try again shortly.",
    )
    response.status_code = 429
    # Round up so a client never retries a hair too early.
    response.headers["Retry-After"] = str(max(1, int(retry_after + 0.999)))
    return response


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
    limited = _rate_limit_response()
    if limited is not None:
        return limited

    # DB_PATH lives in app config, so tests can point at a temporary database.
    db_path = current_app.config["DB_PATH"]
    try:
        order = find_order(order_id, db_path)
    except Exception:
        # The DB is unavailable or corrupted: log server-side (never to the
        # customer) and return a safe, stable JSON error - no traceback, no
        # file path, no internal detail (G3.2).
        current_app.logger.exception("Order lookup failed")
        return (
            jsonify(
                error="storage_error",
                message=(
                    "We could not reach our order system right now. "
                    "Please try again shortly."
                ),
            ),
            500,
        )

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
    SQLite orders and the sample FAQ. Returns:
        {"intent": ..., "reply": ..., "source": ...}
    """
    limited = _rate_limit_response()
    if limited is not None:
        return limited

    payload = request.get_json(silent=True)
    # Accept only a JSON object; a list/scalar/None body degrades to an empty
    # message instead of crashing (payload.get would fail on non-dicts). This
    # also preserves the deliberate malformed-JSON contract: silent=True turns a
    # bad body into an empty message answered with a friendly 200, never a 400/500.
    if not isinstance(payload, dict):
        payload = {}
    message = payload.get("message", "")
    # Unexpected input type (a number/list/dict under "message"): treat it like an
    # absent message rather than feeding a non-string to the assistant.
    if not isinstance(message, str):
        message = ""

    # Application-level length guard. MAX_CONTENT_LENGTH already caps the raw
    # body (a hard DoS limit that never reaches here); this is the friendlier,
    # message-sized limit that returns a clean JSON error instead of processing
    # an oversized payload. Set MAX_MESSAGE_LENGTH=0 to disable this check.
    max_len = current_app.config.get("MAX_MESSAGE_LENGTH", DEFAULT_MAX_MESSAGE_LENGTH)
    if max_len and len(message) > max_len:
        return (
            jsonify(
                error="message_too_long",
                message=(
                    "That message is too long to send at once - please shorten "
                    "it and try again."
                ),
            ),
            413,
        )

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
