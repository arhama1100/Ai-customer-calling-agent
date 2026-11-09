"""
app/gemini.py
─────────────
A thin, SAFE wrapper around the official Gemini Python SDK (``google-genai``).

This is Phase B1: only the client wrapper + configuration. It is deliberately
NOT wired into ``app/assistant.py`` yet, so current behaviour is unchanged.

Design goals / hard rules
─────────────────────────
- Optional and OFF by default. If ``GEMINI_ENABLED`` is false/absent OR the API
  key is missing, NO network call is made and callers receive ``None``.
- Never a source of truth. This wrapper only CLASSIFIES intent/language and
  REPHRASES text that the app has already approved. Real facts (order status,
  policy wording) come from PostgreSQL and ``data/faq.json``. Later phases ground
  on those; they must never let this module fabricate an answer.
- Secrets. ``GEMINI_API_KEY`` is read from the environment at call time and is
  NEVER hardcoded, printed, logged, returned, or stored on an object.
- Failure-safe. Any import / SDK / API / timeout / JSON-parse problem returns
  ``None`` instead of raising, so the Flask app can fall back to the existing
  deterministic assistant rather than crash.
- No live calls happen during tests (tests monkeypatch ``generate_text`` or
  ``_get_client``; the SDK is only touched when a key is actually present).
"""

import json
import os

# ── Defaults (non-secret) ────────────────────────────────────────────────────
DEFAULT_MODEL = "gemini-flash-latest"
DEFAULT_TIMEOUT = 10.0  # seconds; override with GEMINI_TIMEOUT

# Grounded enums: the classifier is only allowed to pick from these. The model
# cannot invent a new category that the app would then act on blindly.
ALLOWED_INTENTS = {
    "order_status",
    "shipping",
    "returns",
    "cancellation",
    "payment",
    "changes",
    "greeting",
    "ambiguous",
    "other",
}
ALLOWED_LANGUAGES = {"en", "ur", "roman-urdu", "mixed", "other"}


# ── Small env helpers ────────────────────────────────────────────────────────
def _as_bool(value, default=False):
    """Interpret env strings like 'true'/'1'/'yes'/'on' as booleans."""
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _model_name():
    return os.getenv("GEMINI_MODEL") or DEFAULT_MODEL


def _timeout_seconds():
    raw = os.getenv("GEMINI_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    # Guard against nonsense values that could hang a request.
    return seconds if seconds > 0 else DEFAULT_TIMEOUT


def is_enabled():
    """True only when the feature flag is ON *and* an API key is present.

    Reveals nothing about the key value — only whether one exists.
    """
    if not _as_bool(os.getenv("GEMINI_ENABLED")):
        return False
    return bool(os.getenv("GEMINI_API_KEY"))


# ── Client + low-level generation ────────────────────────────────────────────
def _get_client():
    """Build the SDK client, or return ``None`` on any problem.

    The API key is read from the environment here and never leaves this scope.
    """
    if not is_enabled():
        return None
    try:
        from google import genai
        from google.genai import types
    except Exception:  # SDK not importable in this environment
        return None
    api_key = os.getenv("GEMINI_API_KEY")
    try:
        # google-genai expects the HTTP timeout in MILLISECONDS, while our
        # GEMINI_TIMEOUT env value is expressed in SECONDS (see app/config.py).
        # Convert here so a realistic per-request budget is actually applied.
        # Passing the raw seconds value (e.g. 10) made the SDK treat it as 10 ms
        # and aborted every live call instantly; it then safely fell back to the
        # deterministic assistant. The API key is read from the environment and
        # is never logged, returned, or stored on any object.
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(_timeout_seconds() * 1000)),
        )
    except Exception:
        return None


def generate_text(prompt, *, system_instruction=None, json_schema=None, temperature=0.2):
    """Low-level, failure-safe generation. Returns raw text or ``None``.

    This is the ONLY function in the module that touches the network, and it
    does so only when :func:`is_enabled` is true. It never raises: a disabled
    flag, missing key, unavailable SDK, API error, timeout, or empty response
    all produce ``None`` so callers can fall back.
    """
    if not is_enabled():
        return None
    client = _get_client()
    if client is None:
        return None
    try:
        from google.genai import types

        config_kwargs = {"temperature": temperature}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if json_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_json_schema"] = json_schema
        config = types.GenerateContentConfig(**config_kwargs)

        response = client.models.generate_content(
            model=_model_name(),
            contents=prompt,
            config=config,
        )
        text = getattr(response, "text", None)
        return text if text else None
    except Exception:
        # Any SDK/API/timeout/parse failure -> safe None. We intentionally do
        # not log the exception text (it could echo request content). The key
        # is never part of it regardless, and is never logged.
        return None


# ── High-level, grounded helpers (used by the assistant in Phase B2) ──────────
_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "language": {"type": "string"},
        "order_id": {"type": "string"},
        "needs_clarification": {"type": "boolean"},
    },
    "required": ["intent", "language"],
}

# The prompt frames Gemini strictly as a CLASSIFIER. It is told not to answer
# the customer and not to invent anything — the app decides facts afterwards.
# The intent definitions matter: customers ask the SAME question in many
# different wordings, and the two labels most often confused are `order_status`
# (asking about THEIR OWN parcel) and `shipping` (asking about the POLICY in
# general), so that boundary is stated explicitly instead of left to guesswork.
_CLASSIFY_PROMPT = (
    "You are an intent classifier for a small shop's customer-care line. "
    "Do NOT answer the customer and do NOT invent any facts, dates, statuses, "
    "prices, refunds, or approvals. Your ONLY job is to classify the message. "
    "The customer may write in English, Urdu script, Roman Urdu, or a mix, and "
    "in many different wordings; judge the MEANING, not specific keywords.\n"
    "Intent meanings:\n"
    "- order_status: asking about THEIR OWN order/parcel/shipment (where it is, "
    "whether it arrived, when it will come, an update, tracking it).\n"
    "- shipping: asking about delivery in GENERAL - times, cost, areas, courier "
    "or method (not about one specific order).\n"
    "- returns: sending an item back, refunds, money back, exchanges.\n"
    "- cancellation: wanting an order to be stopped/voided/no longer wanted.\n"
    "- changes: wanting to alter an order or its address/details.\n"
    "- payment: charges, methods, whether a payment went through.\n"
    "- greeting: only a hello or how-are-you with nothing else asked.\n"
    "- ambiguous: the customer seems to want something but it cannot be routed "
    "without a short follow-up question.\n"
    "- other: not a support request at all.\n"
    "Return a JSON object with: "
    "'intent' set to exactly one of: order_status, shipping, returns, "
    "cancellation, payment, changes, greeting, ambiguous, other; "
    "'language' set to one of: en, ur, roman-urdu, mixed, other; "
    "'order_id' set to ORD-<four digits> ONLY if the customer actually stated a "
    "number in this message - including one said as words such as 'one zero "
    "zero two' or written '1002' - otherwise leave it EMPTY; never infer, "
    "complete or invent a number from anything else; and 'needs_clarification' "
    "(true only if the request is too vague to route without asking a short "
    "follow-up question)."
)


def classify_message(message):
    """Classify a customer message into a grounded, normalized dict.

    Returns e.g. {"intent": "order_status", "language": "roman-urdu",
    "order_id": "ORD-1002", "needs_clarification": False}, or ``None`` when
    disabled, empty, or on any failure. The intent/language are validated
    against the allowed enums so a rogue model value can never leak through.
    """
    if not is_enabled() or not message or not str(message).strip():
        return None

    prompt = f"{_CLASSIFY_PROMPT}\n\nUser message:\n{message}"
    raw = generate_text(prompt, json_schema=_CLASSIFY_SCHEMA, temperature=0.0)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    intent = str(data.get("intent", "")).strip().lower()
    if intent not in ALLOWED_INTENTS:
        intent = "other"
    language = str(data.get("language", "")).strip().lower()
    if language not in ALLOWED_LANGUAGES:
        language = "other"
    order_id = data.get("order_id")
    order_id = str(order_id).strip() if order_id else None

    return {
        "intent": intent,
        "language": language,
        "order_id": order_id,
        "needs_clarification": bool(data.get("needs_clarification", False)),
    }


def rephrase_to_language(approved_text, language):
    """Rephrase ALREADY-APPROVED text into the customer's language.

    This only re-words text the app has already produced from an approved
    source (PostgreSQL status or FAQ policy). It is instructed to keep every fact
    identical — no added/removed/changed status, date, price, or approval.
    Returns the rephrased string, or ``None`` when disabled/failing (the
    caller then uses the original approved text unchanged).
    """
    if not is_enabled() or not approved_text or not str(approved_text).strip():
        return None

    prompt = (
        "Rephrase the following APPROVED customer-care text so a "
        f"'{language}' speaker understands it. Keep every fact identical. "
        "Do NOT add, remove, or change any order status, date, price, address, "
        "refund decision, or policy rule. Output ONLY the rephrased text.\n\n"
        f"{approved_text}"
    )
    return generate_text(prompt, temperature=0.2)
