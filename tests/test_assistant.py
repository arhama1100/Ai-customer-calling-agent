"""
tests/test_assistant.py
───────────────────────
Verifies the DETERMINISTIC router picks the right approved source for each
kind of message, and that the /api/chat endpoint exposes it correctly.

Most important guarantee under test: order status comes from the DB and
policies come from the FAQ — the assistant never invents an answer.

Run with:  .\\.venv\\Scripts\\python.exe -m pytest
"""

import pytest

from app import assistant
from app.assistant import respond


def test_routes_order_id_to_database(db_path):
    result = respond("Where is my order ORD-1002?", db_path=db_path)

    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"
    # Reply must reflect the actual stored status, not a guess.
    assert "shipped" in result["reply"]


def test_unknown_order_id_says_not_found(db_path):
    result = respond("status of ORD-9999", db_path=db_path)

    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"
    assert "could not find" in result["reply"].lower()


def test_routes_policy_question_to_faq(db_path):
    # A FAQ-matched policy is acknowledged, but because data/faq.json is still
    # DRAFT / UNAPPROVED the placeholder wording must NOT be quoted as fact.
    result = respond("What is your return policy?", db_path=db_path)

    assert result["intent"] == "faq"
    assert result["source"].startswith("faq_kb:")
    assert "unapproved" in result["source"]
    assert result["reply"] == assistant.UNAPPROVED_POLICY_REPLY
    assert "30 days" not in result["reply"]            # draft answer suppressed
    assert "confirmation" in result["reply"].lower()   # asks to confirm
    assert "support" in result["reply"].lower()        # offers support


def test_greeting_is_fixed_reply(db_path):
    result = respond("hello", db_path=db_path)
    assert result["intent"] == "greeting"
    assert result["source"] == "fixed"


def test_unrelated_message_is_honest_fallback(db_path):
    result = respond("tell me about extraterrestrial cuisine", db_path=db_path)
    assert result["intent"] == "fallback"
    assert result["source"] == "none"


def test_order_id_wins_over_faq_keyword(db_path):
    # Contains both an order id AND a policy word ("cancel"); the specific
    # order lookup must take priority (rule #1 before rule #2).
    result = respond("I want to cancel order ORD-1005", db_path=db_path)
    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"


# ── API test through /api/chat ──

def test_chat_endpoint_uses_database(client):
    response = client.post("/api/chat", json={"message": "status ORD-1001"})

    assert response.status_code == 200
    data = response.get_json()
    assert data["intent"] == "order_status"
    assert "delivered" in data["reply"]


# ── Phase 6 hardening: tricky inputs to the router ──

def test_none_input_is_treated_as_empty(db_path):
    result = respond(None, db_path=db_path)
    assert result["intent"] == "empty"
    assert result["source"] == "none"


def test_whitespace_only_input_is_empty(db_path):
    assert respond("     ", db_path=db_path)["intent"] == "empty"


def test_lowercase_order_id_still_routes_to_db(db_path):
    # Users may type (or dictate) the id in any case.
    result = respond("where is ord-1002?", db_path=db_path)
    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"
    assert "shipped" in result["reply"]


def test_embedded_id_with_extra_digits_is_not_matched(db_path):
    # "ORD-10022" is NOT a valid 4-digit id, so it must not be treated as an
    # order lookup; with no FAQ/greeting signal it falls through honestly.
    result = respond("status of ORD-10022extra", db_path=db_path)
    assert result["intent"] == "fallback"
    assert result["source"] == "none"


def test_long_greeting_like_sentence_is_not_greeting(db_path):
    # A long sentence that merely contains "hi" should not count as a greeting
    # (length guard in _looks_like_greeting); it falls through honestly.
    result = respond("hi there, how are things going today?", db_path=db_path)
    assert result["intent"] == "fallback"


# ── Regression: speech-to-text order-id variations (the reported bug) ──
# Speech often drops the hyphen or adds the word "order". Every variation
# below must normalize to the canonical ORD-1002 and hit the SQLite lookup.
ORDER_VARIATIONS = [
    "where is order ord 1002",    # the exact reported failing input
    "ord 1002",                   # space instead of hyphen
    "order ord 1002",             # spoken "order" prefix + id
    "ORD 1002",                   # uppercase, space separator
    "ord1002",                    # no separator at all
    "ORD-1002",                   # canonical form still works
    "status of Ord-1002 please",  # mixed case + surrounding words
]


@pytest.mark.parametrize("message", ORDER_VARIATIONS)
def test_respond_accepts_order_id_speech_variations(message, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"
    # ORD-1002 is "shipped" in the seed data -> answer reflects the DB, and is
    # NEVER the generic topic fallback.
    assert result["reply"].startswith("Order ORD-1002")
    assert "shipped" in result["reply"]


def test_respond_unknown_order_id_is_honest_not_generic_fallback(db_path):
    # A well-formed id that isn't in the DB still routes to the order lookup
    # and reports honestly that it was not found (not the topic fallback).
    result = respond("where is order ord 9999", db_path=db_path)
    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"
    assert "could not find" in result["reply"].lower()


# These must NOT be misread as order ids (no word boundary before "ord").
NON_ORDER_TEXTS = [
    "tell me about extraterrestrial cuisine",  # generic fallback
    "word 1002",                               # "ord" inside "word"
    "record 1002",                             # "ord" inside "record"
]


@pytest.mark.parametrize("message", NON_ORDER_TEXTS)
def test_respond_does_not_misread_non_order_text_as_order(message, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] != "order_status"


# ── Regression: Urdu + Roman Urdu order-status intent (the reported bug) ──
# These phrasings ask for an order status but give NO order id, so the router
# must ask for the id — never the generic fallback, and never an invented
# status. The DB only has ORD-1002 "shipped"; none of these may leak that.
URDU_NO_ID = [
    "میرا آرڈر کہاں تک پہنچا",   # where has my order reached
    "میرا آرڈر کہاں ہے",          # where is my order
    "میرے آرڈر کا کیا اسٹیٹس ہے",   # what is my order status
    "میرا آرڈر کہاں ہے؟",              # Urdu question mark (punctuation norm.)
]

ROMAN_NO_ID = [
    "mera order kahan tak pohncha",
    "mera order kahan hai",
    "mere order ka kya status hai",
]


@pytest.mark.parametrize("message", URDU_NO_ID + ROMAN_NO_ID)
def test_urdu_order_status_without_id_asks_for_id(message, db_path):
    result = respond(message, db_path=db_path)
    # Not the generic topic fallback.
    assert result["intent"] == "order_status_missing_id"
    assert "not sure how to help" not in result["reply"].lower()
    # Asks the user for the order number.
    assert "order number" in result["reply"].lower()
    # Never invents / guesses a status.
    assert result["source"] == "none"
    assert "shipped" not in result["reply"]
    assert "delivered" not in result["reply"]


# With an order id present, the SAME Urdu/Roman phrasing must hit the DB and
# return the real status (existing lookup preserved).
@pytest.mark.parametrize(
    "message",
    [
        "میرا آرڈر کہاں ہے ORD-1002",   # Urdu script + id
        "میرے آرڈر کا کیا اسٹیٹس ہے ord 1002",  # Urdu + spaced id
        "mera order kahan hai ORD-1002",   # Roman Urdu + id
        "mere order ka status ord 1002 please",  # Roman + id, extra words
    ],
)
def test_urdu_order_status_with_id_uses_database(message, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"
    assert result["reply"].startswith("Order ORD-1002")
    assert "shipped" in result["reply"]


def test_plain_english_order_status_without_id_asks_for_id(db_path):
    # Phase E: an English order-status wording with NO id is now handled the
    # SAME way as its Urdu/Roman counterparts - we ask for the order id
    # instead of guessing. (Previously this fell through to the generic
    # fallback; routing is now consistent across all languages.)
    result = respond("what is my order status", db_path=db_path)
    assert result["intent"] == "order_status_missing_id"
    assert "order number" in result["reply"].lower()
    assert "shipped" not in result["reply"]


# ════════════════════════════════════════════════════════════════════════════
# Phase E — greeting-detection regression (the reported 'hi' inside 'nahi' bug)
# and multilingual intent routing. These are DETERMINISTIC: the conftest
# autouse fixture clears every GEMINI_* env var, so gemini.is_enabled() is
# False and the router never reaches the network.
# ════════════════════════════════════════════════════════════════════════════
GREETING_MESSAGES = ["hi", "hello", "assalam o alaikum"]


@pytest.mark.parametrize("message", GREETING_MESSAGES)
def test_phase_e_greeting_detected(message, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "greeting"
    assert result["source"] == "fixed"


# 'hi' is a SUBSTRING of 'nahi', so whole-word matching must NOT greet these.
NOT_GREETING_MESSAGES = ["nahi", "order pohncha ya nahi"]


@pytest.mark.parametrize("message", NOT_GREETING_MESSAGES)
def test_phase_e_substring_hi_is_not_a_greeting(message, db_path):
    assert respond(message, db_path=db_path)["intent"] != "greeting"


def test_phase_e_pohncha_ya_nahi_is_order_related(db_path):
    # 'order pohncha ya nahi' references the order + an arrival cue, so it is
    # routed as an (id-less) order-status request, never a greeting.
    result = respond("order pohncha ya nahi", db_path=db_path)
    assert result["intent"] == "order_status_missing_id"
    assert "order number" in result["reply"].lower()


# Order-status requests in every supported language, WITHOUT an id.
PHASE_E_ORDER_STATUS_NO_ID = [
    # English
    "Where is my order?",
    "Has my parcel been shipped?",
    "I haven't received my order.",
    # Roman Urdu
    "Mera order kahan hai?",
    "Mera parcel abhi tak nahi aya.",
    "Mera order dispatch hua?",
    "bhai mera parcel abhi tak nahi aya",
    # Mixed Urdu-English
    "Mera parcel abhi tak deliver nahi hua.",
    # Urdu script
    "میرا آرڈر کہاں ہے؟",
    "میرا پارسل ابھی تک نہیں آیا۔",
    "کیا میرا آرڈر بھیج دیا گیا ہے؟",
]


@pytest.mark.parametrize("message", PHASE_E_ORDER_STATUS_NO_ID)
def test_phase_e_order_status_without_id_asks_for_id(message, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "order_status_missing_id"
    assert result["source"] == "none"
    assert "order number" in result["reply"].lower()
    # Never fabricate a status for an unknown / id-less order.
    for leaked in ("shipped", "delivered", "processing", "pending", "cancelled"):
        assert leaked not in result["reply"]


# Policy questions in every language -> (topic id). The DRAFT faq.json answer
# is never surfaced; only the safe "needs confirmation + support" notice is.
PHASE_E_POLICY_MESSAGES = [
    # English
    ("Can I cancel my order?", "cancellation"),
    ("I want to return my product.", "returns"),
    ("What is your refund policy?", "returns"),
    ("How long does shipping take?", "shipping"),
    ("I need to change my address.", "changes"),
    ("How do I pay with a card?", "payment"),
    # Roman Urdu
    ("Kya main apna order cancel kar sakti hoon?", "cancellation"),
    ("Mujhe product return karna hai.", "returns"),
    ("Refund kab milega?", "returns"),
    ("Mera refund abhi tak receive nahi hua.", "returns"),
    ("Payment kaise karuna?", "payment"),
    # Mixed Urdu-English
    ("I want to order cancel karwana hai.", "cancellation"),
    ("Return policy kya hai?", "returns"),
    # Urdu script
    ("میں اپنا آرڈر منسوخ کرنا چاہتی ہوں؟", "cancellation"),
    ("مجھے پروڈکٹ واپس کرنی ہے۔", "returns"),
]


@pytest.mark.parametrize("message,topic", PHASE_E_POLICY_MESSAGES)
def test_phase_e_policy_question_returns_safe_notice(message, topic, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "faq"
    assert result["source"] == f"faq_kb:{topic}:unapproved"
    assert result["reply"] == assistant.UNAPPROVED_POLICY_REPLY
    assert "confirmation" in result["reply"].lower()
    assert "support" in result["reply"].lower()


# All five policy categories must keep their correct source identifier, so the
# safe-notice path covers shipping / returns / cancellation / changes / payment.
@pytest.mark.parametrize(
    "message,topic",
    [
        ("how long does delivery usually take", "shipping"),
        ("can I get a refund on a return", "returns"),
        ("can I cancel my order", "cancellation"),
        ("I need to change the wrong address on my order", "changes"),
        ("how do I pay with a card", "payment"),
    ],
)
def test_phase_e_all_five_categories_safe(message, topic, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "faq"
    assert result["source"] == f"faq_kb:{topic}:unapproved"
    assert result["reply"] == assistant.UNAPPROVED_POLICY_REPLY


def test_phase_e_mixed_language_with_id_uses_db(db_path):
    # 'Bhai mera order ORD-1002 ka status bata do.' carries an id, so the
    # reliable SQLite path wins before any intent wording is considered.
    result = respond("Bhai mera order ORD-1002 ka status bata do", db_path=db_path)
    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"
    assert result["reply"].startswith("Order ORD-1002")
    assert "shipped" in result["reply"]


def test_phase_e_unknown_id_honest_not_found(db_path):
    result = respond("mera order ORD-9999 kahan hai", db_path=db_path)
    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"
    assert "could not find" in result["reply"].lower()


@pytest.mark.parametrize(
    "message",
    ["hi", "Where is my order?", "Can I cancel my order?",
     "میرا آرڈر کہاں ہے",
     "Mera order kahan hai?", "Bhai mera order ORD-1002 ka status bata do"],
)
def test_phase_e_response_contract_preserved(message, db_path):
    # The {intent, reply, source} contract is unchanged for every language.
    result = respond(message, db_path=db_path)
    assert set(result.keys()) == {"intent", "reply", "source"}
    assert isinstance(result["reply"], str) and result["reply"].strip()


def test_phase_e_policy_safety_when_gemini_enabled(db_path, monkeypatch):
    # Gemini ON (mocked): a Roman Urdu policy question must STILL never
    # surface the DRAFT answer - the Gemini branch returns the safe notice.
    _enable_gemini(
        monkeypatch,
        {"intent": "cancellation", "language": "roman-urdu"},
        translation=None,
    )
    result = respond("Kya main apna order cancel kar sakti hoon?", db_path=db_path)
    assert result["reply"] == assistant.UNAPPROVED_POLICY_REPLY
    assert result["source"] == "gemini:policy_unapproved"
    assert "30 days" not in result["reply"]


def test_phase_e_policy_safety_when_gemini_fails(db_path, monkeypatch):
    # Gemini ON but classify fails (None): the deterministic branch still
    # returns the safe unapproved notice for the Roman Urdu cancellation topic.
    _enable_gemini(monkeypatch, None)
    result = respond("Kya main apna order cancel kar sakti hoon?", db_path=db_path)
    assert result["intent"] == "faq"
    assert result["source"] == "faq_kb:cancellation:unapproved"
    assert result["reply"] == assistant.UNAPPROVED_POLICY_REPLY


# ════════════════════════════════════════════════════════════════════════════
# Phase B2 — Gemini-enabled routing. EVERY test here MOCKS the wrapper
# (classify_message + rephrase_to_language), so no live API call is ever made.
# ════════════════════════════════════════════════════════════════════════════
def _enable_gemini(monkeypatch, classification, translation=None):
    """Turn the feature flag on (with a dummy key) and mock the wrapper so the
    classification result and any translation are fully controlled."""
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(
        assistant.gemini_module, "classify_message",
        lambda *a, **k: classification,
    )
    monkeypatch.setattr(
        assistant.gemini_module, "rephrase_to_language",
        lambda *a, **k: translation,
    )


def test_gemini_enabled_valid_id_still_uses_database(db_path, monkeypatch):
    # Order facts must ALWAYS come from SQLite, even when Gemini is enabled.
    _enable_gemini(monkeypatch, {"intent": "order_status", "language": "en"})
    result = respond("where is ORD-1002", db_path=db_path)
    assert result["source"] == "orders_db"
    assert result["reply"].startswith("Order ORD-1002")
    assert "shipped" in result["reply"]


def test_gemini_enabled_unknown_id_is_honest(db_path, monkeypatch):
    # An unknown id is answered honestly from the DB, never invented.
    _enable_gemini(monkeypatch, {"intent": "order_status", "language": "en"})
    result = respond("status of ORD-9999", db_path=db_path)
    assert "could not find order ord-9999" in result["reply"].lower()
    assert "shipped" not in result["reply"]


def test_gemini_policy_intent_not_quoted_as_fact(db_path, monkeypatch):
    # UNAPPROVED FAQ placeholders must NOT be presented as confirmed policy.
    _enable_gemini(monkeypatch, {"intent": "shipping", "language": "en"})
    result = respond("how long does delivery take", db_path=db_path)
    assert result["intent"] == "shipping"
    assert result["source"] == "gemini:policy_unapproved"
    assert result["reply"] == assistant.UNAPPROVED_POLICY_REPLY
    assert "1-2 business days" not in result["reply"]   # placeholder suppressed


def test_gemini_urdu_order_status_missing_id_asks(monkeypatch):
    # Urdu script, order intent, no id -> ask for the order id (never guess).
    _enable_gemini(monkeypatch, {"intent": "order_status", "language": "ur"}, translation=None)
    result = respond("\u0645\u06cc\u0631\u0627 \u0622\u0631\u068d\u0631 \u06a9\u06c1\u0627\u06ba \u062a\u06a9 \u067e\u06c1\u0646\u0686\u0627", db_path=None)
    assert result["intent"] == "order_status_missing_id"
    assert result["source"] == "gemini:fixed"
    assert "order number" in result["reply"].lower()
    assert "shipped" not in result["reply"]


def test_gemini_roman_urdu_payment_localized(monkeypatch):
    _enable_gemini(
        monkeypatch,
        {"intent": "payment", "language": "roman-urdu"},
        translation="Yeh policy confirm karni zaroori hai, support se rabta karein.",
    )
    result = respond("mujhe refund chahiye payment", db_path=None)
    assert result["intent"] == "payment"
    assert result["source"] == "gemini:policy_unapproved"
    assert result["reply"].startswith("Yeh policy")


def test_gemini_ambiguous_asks_clarification(monkeypatch):
    _enable_gemini(monkeypatch, {"intent": "ambiguous", "language": "en"})
    result = respond("hmm", db_path=None)
    assert result["intent"] == "ambiguous"
    assert result["source"] == "gemini:fixed"
    assert result["reply"] == assistant.CLARIFICATION_REPLY


def test_gemini_other_returns_fallback(monkeypatch):
    _enable_gemini(monkeypatch, {"intent": "other", "language": "en"})
    result = respond("tell me about the weather", db_path=None)
    assert result["intent"] == "fallback"
    assert result["source"] == "gemini:fixed"
    assert result["reply"] == assistant.FALLBACK_REPLY


def test_gemini_greeting(monkeypatch):
    _enable_gemini(monkeypatch, {"intent": "greeting", "language": "en"})
    result = respond("hello there", db_path=None)
    assert result["intent"] == "greeting"
    assert result["source"] == "gemini:fixed"
    assert result["reply"] == assistant.GREETING_REPLY


def test_gemini_failure_falls_back_to_deterministic(monkeypatch):
    # classify_message returning None simulates an API error/timeout: the
    # router must use the existing deterministic behavior, not pretend.
    _enable_gemini(monkeypatch, None)
    result = respond("hello", db_path=None)
    assert result["intent"] == "greeting"
    assert result["source"] == "fixed"   # deterministic marker, not gemini:*


def test_gemini_disabled_not_consulted(db_path, monkeypatch):
    # With the flag off (default), Gemini must never be called at all.
    monkeypatch.delenv("GEMINI_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def _boom(*a, **k):
        raise AssertionError("classify must not run when disabled")

    monkeypatch.setattr(assistant.gemini_module, "classify_message", _boom)
    result = respond("how long does delivery take", db_path=db_path)
    assert result["source"].startswith("faq_kb")   # unchanged deterministic path


def test_invalid_input_empty(monkeypatch):
    _enable_gemini(monkeypatch, {"intent": "other", "language": "en"})
    assert respond("", db_path=None)["intent"] == "empty"
    assert respond(None, db_path=None)["intent"] == "empty"
    assert respond("   ", db_path=None)["intent"] == "empty"


def test_gemini_result_shape_is_compatible(db_path, monkeypatch):
    # The frontend contract {intent, reply, source} must be preserved.
    _enable_gemini(monkeypatch, {"intent": "returns", "language": "en"})
    result = respond("can I return this", db_path=db_path)
    assert set(result.keys()) == {"intent", "reply", "source"}
    assert isinstance(result["reply"], str) and result["reply"]


# ── Unapproved-FAQ protection holds in EVERY mode (Gemini on / off / failed) ──
def test_faq_unapproved_when_gemini_disabled(db_path, monkeypatch):
    # Feature off (default): the deterministic FAQ branch still refuses to
    # present the DRAFT answer as a confirmed policy.
    monkeypatch.delenv("GEMINI_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = respond("how long does shipping take", db_path=db_path)
    assert result["intent"] == "faq"
    assert "unapproved" in result["source"]
    assert result["reply"] == assistant.UNAPPROVED_POLICY_REPLY
    assert "1-2 business days" not in result["reply"]


def test_faq_unapproved_when_gemini_api_fails(db_path, monkeypatch):
    # Enabled but classify_message returns None (timeout / API failure): we
    # fall through to the deterministic FAQ branch, which STILL withholds the
    # unapproved placeholder rather than quoting it.
    _enable_gemini(monkeypatch, None)
    result = respond("what is your return policy", db_path=db_path)
    assert result["intent"] == "faq"
    assert "unapproved" in result["source"]
    assert result["reply"] == assistant.UNAPPROVED_POLICY_REPLY
    assert "30 days" not in result["reply"]


def test_faq_unapproved_when_gemini_enabled_intent_wins(db_path, monkeypatch):
    # Enabled with a successful policy classification: the Gemini branch handles
    # it and also returns the unapproved notice (never the placeholder).
    _enable_gemini(monkeypatch, {"intent": "cancellation", "language": "en"})
    result = respond("can I cancel my order", db_path=db_path)
    assert result["source"] == "gemini:policy_unapproved"
    assert result["reply"] == assistant.UNAPPROVED_POLICY_REPLY


# ════════════════════════════════════════════════════════════════════════════
# Phase B4 Tier A — order accuracy over EVERY seeded row + Gemini grounding.
# ════════════════════════════════════════════════════════════════════════════
SEEDED_STATUSES = [
    ("ORD-1001", "delivered"),
    ("ORD-1002", "shipped"),
    ("ORD-1003", "processing"),
    ("ORD-1004", "pending"),
    ("ORD-1005", "cancelled"),
    ("ORD-1006", "shipped"),
]


@pytest.mark.parametrize("order_id,status", SEEDED_STATUSES)
def test_every_seeded_order_reports_exact_db_status(order_id, status, db_path):
    # The status word must come straight from SQLite for all six orders and
    # all five distinct statuses in the seed data.
    result = respond(f"where is {order_id}", db_path=db_path)
    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"
    assert result["reply"].startswith(f"Order {order_id}")
    assert status in result["reply"]


def test_multiple_ids_use_first_match(db_path):
    # Documented existing behavior: the FIRST id wins (no merge / no 2nd).
    result = respond("compare ORD-1001 and ORD-1002", db_path=db_path)
    assert result["source"] == "orders_db"
    assert result["reply"].startswith("Order ORD-1001")


@pytest.mark.parametrize(
    "msg", ["status of ORD-100", "status of ORD-12", "ORD-10023"],
)
def test_bad_digit_length_is_not_an_order(msg, db_path):
    result = respond(msg, db_path=db_path)
    assert result["intent"] != "order_status"
    assert result["source"] != "orders_db"


def test_mixed_urdu_english_with_id_uses_db(db_path):
    result = respond("mera order ORD-1002 kahan hai", db_path=db_path)
    assert result["source"] == "orders_db"
    assert "shipped" in result["reply"]


def test_mixed_urdu_english_without_id_asks(db_path, monkeypatch):
    monkeypatch.delenv("GEMINI_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = respond("where is mera order ka status", db_path=db_path)
    assert result["intent"] == "order_status_missing_id"
    assert "order number" in result["reply"].lower()
    assert "shipped" not in result["reply"]


# ── Gemini grounding spies (all MOCKED; no live call) ──
def _enable_gemini_spy(monkeypatch, classification, translation=None):
    """Enable the flag (dummy key), mock the wrapper, and record exactly how
    classify_message / rephrase_to_language are called."""
    calls = {"classify": 0, "rephrase": []}
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

    def _classify(text):
        calls["classify"] += 1
        return classification

    def _rephrase(approved_text, language):
        calls["rephrase"].append((approved_text, language))
        return translation

    monkeypatch.setattr(assistant.gemini_module, "classify_message", _classify)
    monkeypatch.setattr(assistant.gemini_module, "rephrase_to_language", _rephrase)
    return calls


def test_order_facts_are_never_localized_by_gemini(db_path, monkeypatch):
    # An id present -> the DB answers BEFORE any Gemini call; the exact English
    # DB sentence is returned unchanged and the model is never consulted.
    calls = _enable_gemini_spy(
        monkeypatch, {"intent": "order_status", "language": "ur"})
    result = respond("where is ORD-1002", db_path=db_path)
    assert result["source"] == "orders_db"
    assert result["reply"].startswith("Order ORD-1002")
    assert "shipped" in result["reply"]
    assert calls["classify"] == 0        # model never consulted for facts
    assert calls["rephrase"] == []       # status text never rephrased


def test_policy_notice_is_localized_only_from_fixed_text(monkeypatch):
    calls = _enable_gemini_spy(
        monkeypatch,
        {"intent": "shipping", "language": "ur"},
        translation="MOCK-LOCALIZED-POLICY",
    )
    result = respond("how long does delivery take", db_path=None)
    assert result["source"] == "gemini:policy_unapproved"
    # Localization is applied to OUR approved constant, never a DRAFT answer.
    assert calls["rephrase"], "localization should have been attempted"
    assert calls["rephrase"][0][0] == assistant.UNAPPROVED_POLICY_REPLY
    assert calls["rephrase"][0][1] == "ur"
    assert result["reply"] == "MOCK-LOCALIZED-POLICY"


def test_localization_falls_back_when_gemini_returns_none(monkeypatch):
    calls = _enable_gemini_spy(
        monkeypatch, {"intent": "payment", "language": "roman-urdu"},
        translation=None)
    result = respond("how do I pay", db_path=None)
    assert result["source"] == "gemini:policy_unapproved"
    # rephrase returned None -> safe fallback to the English approved text.
    assert result["reply"] == assistant.UNAPPROVED_POLICY_REPLY
    assert calls["rephrase"]  # localization was attempted, then fell back


def test_model_supplied_order_id_is_never_trusted(monkeypatch):
    # classify returns an order_id, but the customer message has NO id, so we
    # must NOT look that id up; we ask the customer for their number instead.
    _enable_gemini_spy(
        monkeypatch,
        {"intent": "order_status", "language": "en",
         "order_id": "ORD-1002", "needs_clarification": False})
    result = respond("where is my order", db_path=None)
    assert result["intent"] == "order_status_missing_id"
    assert result["source"] == "gemini:fixed"
    assert "order number" in result["reply"].lower()
    assert "shipped" not in result["reply"]  # never leaked from the model id


# ════════════════════════════════════════════════════════════════════════════
# Phase E.1 — Farewell response. Deterministic (Gemini off via conftest), and
# scoped so it NEVER changes greeting / order / FAQ / return-policy behavior.
# ════════════════════════════════════════════════════════════════════════════
# English + Roman-Urdu farewell phrases must all be recognized.
@pytest.mark.parametrize("message", ["bye", "goodbye", "see you", "see you later",
                                     "okay bye", "thank you bye",
                                     "allah hafiz", "khuda hafiz"])
def test_farewell_detected_all_languages(message, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "farewell"
    assert result["source"] == "fixed"
    assert result["reply"] == assistant.FAREWELL_REPLY


# The exact required farewell wording must be present for the customer.
def test_farewell_reply_wording(db_path):
    result = respond("bye", db_path=db_path)
    assert result["reply"] == "Thank you for contacting us! Have a great day. Goodbye!"


# False-positive guards.
def test_bare_thank_you_is_not_a_farewell(db_path):
    # 'thank you' alone has no bye/goodbye, so it must NOT be a farewell.
    assert respond("thank you", db_path=db_path)["intent"] != "farewell"


def test_goodbye_inside_a_long_sentence_is_not_a_farewell(db_path):
    msg = "I want to say goodbye to my friend"
    assert respond(msg, db_path=db_path)["intent"] != "farewell"


def test_farewell_does_not_detect_substring_bye(db_path):
    # 'bye'/'goodbye' embedded in a longer, unrelated sentence must NOT fire.
    msg = "can I say my goodbyes to everyone"
    assert respond(msg, db_path=db_path)["intent"] != "farewell"


# The farewell rule must not disturb the existing greeting path.
def test_greeting_still_wins_over_farewell_area(db_path):
    assert respond("hello", db_path=db_path)["intent"] == "greeting"


# REQUIRED: the return/refund policy response must remain EXACTLY unchanged.
EXPECTED_RETURN_REPLY = (
    "I want to give you the right answer, but that policy still needs "
    "confirmation from our team. Could you please reach out to our support "
    "team so they can confirm the exact details for you?"
)


def test_return_refund_response_is_unchanged(db_path):
    for message in ["what is your return policy?", "can I get a refund on a return"]:
        result = respond(message, db_path=db_path)
        assert result["intent"] == "faq"
        assert result["source"].startswith("faq_kb:returns:unapproved")
        assert result["reply"] == EXPECTED_RETURN_REPLY
        assert result["reply"] == assistant.UNAPPROVED_POLICY_REPLY
        assert result["reply"] != assistant.FAREWELL_REPLY
