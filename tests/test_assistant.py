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
from app import faq as faq_module
from app.assistant import respond


def _sample(topic):
    """The exact SAMPLE answer text stored in data/faq.json for a topic id.

    Tests assert against this rather than hardcoding, so if the owner edits a
    sample answer the regression suite keeps tracking the file (single source).
    """
    return faq_module.get_answer_by_id(topic)


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
    # G2: data/faq.json now holds activated SAMPLE demonstration policies, so a
    # matched policy question returns that topic's sample answer verbatim from
    # the file (source marks it as sample data, never an invented policy).
    result = respond("What is your return policy?", db_path=db_path)

    assert result["intent"] == "faq"
    assert result["source"] == "faq_kb:returns:sample"
    assert result["reply"] == _sample("returns")
    assert "30 days" in result["reply"]                # sample answer surfaced


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


def test_chat_endpoint_resolves_spoken_order_number(client):
    # Reproduces the exact reported live failure: a full spoken sentence whose
    # order number arrives as WORDS. It must go through /api/chat, resolve to
    # ORD-1002, and answer from SQLite - NOT the generic "I'm not sure" fallback.
    response = client.post(
        "/api/chat", json={"message": "I want to check order number one zero zero two."}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["intent"] == "order_status"
    assert data["source"] == "orders_db"
    assert data["reply"].startswith("Order ORD-1002")
    assert "shipped" in data["reply"]


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


# ── Regression: SPOKEN order numbers delivered as WORDS (the reported bug) ──
# The call's speech recognition transcribes an order number as number words, a
# spelled value, or the word "order"/"order number" instead of the literal ORD
# token. Each form below must normalize to the canonical ORD-#### id and hit the
# unchanged SQLite lookup. (None of these matched before the fix.)
SPOKEN_ORDER_FORMS = [
    "order number one zero zero two",   # digit words after the spoken cue
    "I want to check order number one zero zero two.",  # EXACT reported failing sentence
    "one zero zero two",                # the customer replying with just digits
    "where is order 1002",              # spoken cue + digits, no literal ORD
    "order number 1002",                # "order number" + digits
    "my order is 1002 delivered",       # digits embedded in an order sentence
    "ORD one zero zero two",            # ORD stem + spelled digits
    "order one thousand two",           # compound value -> 1002
    "\u0645\u06cc\u0631\u0627 \u0622\u0631\u0688\u0631 \u06f1\u06f0\u06f0\u06f2",  # Urdu "my order" + Urdu digits
]


@pytest.mark.parametrize("message", SPOKEN_ORDER_FORMS)
def test_respond_recognizes_spoken_order_number(message, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"
    assert result["reply"].startswith("Order ORD-1002")
    assert "shipped" in result["reply"]


def test_respond_recognizes_spoken_order_number_other_ids(db_path):
    # 1003 is "processing" and 1006 "shipped" in the seed — the number words
    # must resolve to the RIGHT id and reflect the DB, never a fixed answer.
    assert respond("order one zero zero three", db_path=db_path)["reply"].startswith("Order ORD-1003")
    assert "processing" in respond("order one zero zero three", db_path=db_path)["reply"]
    assert respond("order number one zero zero six", db_path=db_path)["reply"].startswith("Order ORD-1006")


def test_spoken_number_recognizes_1001_and_1002_symmetrically(db_path):
    # The reported issue: "one zero zero two" was recognized but "one zero zero
    # one" was not. Recognition is order-number AGNOSTIC, so the first two
    # seeded ids must resolve identically from spoken words, and each status
    # must come from SQLite (1001 delivered, 1002 shipped) - never hardcoded.
    cases = [
        ("one zero zero one", "Order ORD-1001", "delivered"),
        ("one zero zero two", "Order ORD-1002", "shipped"),
    ]
    for words, expect_prefix, expect_status in cases:
        for msg in (
            f"I want to check order number {words}.",
            f"order number {words}",
            words,                              # bare digits (a call "repeat" reply)
        ):
            result = respond(msg, db_path=db_path)
            assert result["intent"] == "order_status", msg
            assert result["source"] == "orders_db", msg
            assert result["reply"].startswith(expect_prefix), msg
            assert expect_status in result["reply"], msg


def test_chat_endpoint_resolves_spoken_order_1001(client):
    # Exact reported sentence, through the real /api/chat route (not just respond).
    data = client.post(
        "/api/chat", json={"message": "I want to check order number one zero zero one."}
    ).get_json()
    assert data["intent"] == "order_status"
    assert data["source"] == "orders_db"
    assert data["reply"].startswith("Order ORD-1001")
    assert "delivered" in data["reply"]


# ── Regression: fixed replies that get SPOKEN must not leak a live order id ──
_DIGIT_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


def test_spoken_guidance_never_leaks_a_live_order_id(db_path):
    # In a voice call these fixed lines are spoken out loud. If one contains a real
    # order id, the microphone can re-hear it and submit it as though the customer
    # said it: the assistant then appears to "recognize" a number nobody spoke, and
    # only that one. The prompts must describe the FORMAT instead. Checked against
    # every id actually stored in SQLite (the source of truth), never hardcoded.
    from app.database import get_connection

    conn = get_connection(db_path)
    try:
        ids = [row["order_id"] for row in conn.execute("SELECT order_id FROM orders")]
    finally:
        conn.close()
    assert ids, "this check is only meaningful against seeded orders"

    spoken_lines = [
        assistant.ORDER_ID_REQUEST_REPLY,
        assistant.ORDER_ID_REPEAT_REPLY,
        assistant.FALLBACK_REPLY,
    ]
    for text in spoken_lines:
        lowered = text.lower()
        for order_id in ids:
            digits = order_id.split("-")[-1]
            spelled = " ".join(_DIGIT_WORDS[d] for d in digits)
            assert order_id.lower() not in lowered, f"{order_id} leaked into {text!r}"
            assert digits not in text, f"{digits} leaked into {text!r}"
            assert spelled not in lowered, f"'{spelled}' leaked into {text!r}"


# ── Regression: an UNCLEAR spoken number asks to repeat, never invents an id ──
UNCLEAR_ORDER_FORMS = [
    "order one two three status",              # only 3 digits heard
    "order number one hundred twenty-three",   # 123 - not a complete id
    "order one two three four five",           # 5 digits - not a 4-digit id
]


@pytest.mark.parametrize("message", UNCLEAR_ORDER_FORMS)
def test_respond_unclear_number_asks_repeat_without_guessing(message, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "order_status_unclear"
    assert result["source"] == "none"
    assert "repeat" in result["reply"].lower()
    # We NEVER fabricate an order id or leak a real status for a heard-but-
    # incomplete number.
    assert "Order ORD-" not in result["reply"]
    assert "shipped" not in result["reply"]


# ── Guards: numbers that are NOT an order must not be hijacked ──
def test_unclear_number_does_not_override_policy_action(db_path):
    # "cancel order 123" gives an order word + a number, but the intent is a
    # CANCELLATION policy; the policy branch must still win (no repeat prompt,
    # no order lookup).
    result = respond("cancel order 123", db_path=db_path)
    assert result["intent"] not in ("order_status", "order_status_unclear")


def test_quantity_phrase_is_not_read_as_an_order_id(db_path):
    # "ordered" is not an order-reference cue and 1000 is not the id being
    # asked about, so this stays on the non-order path.
    result = respond("I ordered 1000 items", db_path=db_path)
    assert result["intent"] != "order_status"


def test_number_normalizer_units():
    toks = assistant._number_tokens
    groups = assistant._digit_groups
    assert groups(toks("one zero zero two")) == ["1002"]      # digit sequence
    assert groups(toks("one hundred twenty-three")) == ["123"]  # compound value
    assert groups(toks("one thousand two")) == ["1002"]        # scale phrase
    assert groups(toks("twenty three")) == ["23"]              # tens + ones
    assert groups(toks("1002")) == ["1002"]                    # literal digits
    # A lone common number word is too weak to be treated as a number.
    assert groups(toks("one")) == []
    assert groups(toks("five")) == []


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


# Policy questions in every language -> (topic id). G2 returns that topic's
# SAMPLE demonstration answer from faq.json (never an invented policy).
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
def test_phase_e_policy_question_returns_sample_answer(message, topic, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "faq"
    assert result["source"] == f"faq_kb:{topic}:sample"
    assert result["reply"] == _sample(topic)
    assert result["reply"].strip()


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
def test_phase_e_all_five_categories_return_sample(message, topic, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "faq"
    assert result["source"] == f"faq_kb:{topic}:sample"
    assert result["reply"] == _sample(topic)


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


def test_phase_e_policy_answer_when_gemini_enabled(db_path, monkeypatch):
    # Gemini ON (mocked): a Roman Urdu cancellation question returns the
    # cancellation SAMPLE answer (grounded in faq.json). With the mocked
    # translation returning None, it falls back to the English sample text.
    _enable_gemini(
        monkeypatch,
        {"intent": "cancellation", "language": "roman-urdu"},
        translation=None,
    )
    result = respond("Kya main apna order cancel kar sakti hoon?", db_path=db_path)
    assert result["reply"] == _sample("cancellation")
    assert result["source"] == "gemini:policy_sample"


def test_phase_e_policy_answer_when_gemini_fails(db_path, monkeypatch):
    # Gemini ON but classify fails (None): the deterministic branch still
    # answers the Roman Urdu cancellation topic from the SAMPLE FAQ.
    _enable_gemini(monkeypatch, None)
    result = respond("Kya main apna order cancel kar sakti hoon?", db_path=db_path)
    assert result["intent"] == "faq"
    assert result["source"] == "faq_kb:cancellation:sample"
    assert result["reply"] == _sample("cancellation")


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


def test_gemini_policy_intent_returns_sample_answer(db_path, monkeypatch):
    # G2: with Gemini ON a policy intent returns that topic's SAMPLE answer,
    # grounded verbatim in data/faq.json (translation mocked off -> English).
    _enable_gemini(monkeypatch, {"intent": "shipping", "language": "en"})
    result = respond("how long does delivery take", db_path=db_path)
    assert result["intent"] == "shipping"
    assert result["source"] == "gemini:policy_sample"
    assert result["reply"] == _sample("shipping")
    assert "1-2 business days" in result["reply"]        # sample answer surfaced


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
    assert result["source"] == "gemini:policy_sample"
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


# ── SAMPLE policy answers are consistent in EVERY mode (Gemini on/off/failed) ─
def test_faq_sample_when_gemini_disabled(db_path, monkeypatch):
    # Feature off (default): the deterministic FAQ branch returns the SAMPLE
    # answer for the shipping topic, grounded in data/faq.json.
    monkeypatch.delenv("GEMINI_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = respond("how long does shipping take", db_path=db_path)
    assert result["intent"] == "faq"
    assert result["source"] == "faq_kb:shipping:sample"
    assert result["reply"] == _sample("shipping")


def test_faq_sample_when_gemini_api_fails(db_path, monkeypatch):
    # Enabled but classify_message returns None (timeout / API failure): we
    # fall through to the deterministic FAQ branch, which still answers from
    # the SAMPLE FAQ.
    _enable_gemini(monkeypatch, None)
    result = respond("what is your return policy", db_path=db_path)
    assert result["intent"] == "faq"
    assert result["source"] == "faq_kb:returns:sample"
    assert result["reply"] == _sample("returns")


def test_faq_sample_when_gemini_enabled_intent_wins(db_path, monkeypatch):
    # Enabled with a successful policy classification: the Gemini branch answers
    # from the SAMPLE FAQ (rephrased; translation mocked off -> English here).
    _enable_gemini(monkeypatch, {"intent": "cancellation", "language": "en"})
    result = respond("can I cancel my order", db_path=db_path)
    assert result["source"] == "gemini:policy_sample"
    assert result["reply"] == _sample("cancellation")


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


def test_policy_sample_is_localized_only_from_faq_text(monkeypatch):
    calls = _enable_gemini_spy(
        monkeypatch,
        {"intent": "shipping", "language": "ur"},
        translation="MOCK-LOCALIZED-POLICY",
    )
    result = respond("how long does delivery take", db_path=None)
    assert result["source"] == "gemini:policy_sample"
    # Localization is applied to OUR approved SAMPLE text from faq.json, never
    # to a model-invented policy.
    assert calls["rephrase"], "localization should have been attempted"
    assert calls["rephrase"][0][0] == _sample("shipping")
    assert calls["rephrase"][0][1] == "ur"
    assert result["reply"] == "MOCK-LOCALIZED-POLICY"


def test_localization_falls_back_when_gemini_returns_none(monkeypatch):
    calls = _enable_gemini_spy(
        monkeypatch, {"intent": "payment", "language": "roman-urdu"},
        translation=None)
    result = respond("how do I pay", db_path=None)
    assert result["source"] == "gemini:policy_sample"
    # rephrase returned None -> safe fallback to the English SAMPLE text.
    assert result["reply"] == _sample("payment")
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


# G2 SUPERSEDES the old "unapproved" notice: a return/refund question now
# returns the RETURNS SAMPLE demonstration answer from data/faq.json. The
# farewell / greeting / order paths stay exactly unchanged, as G1 required.
def test_return_refund_returns_sample_policy(db_path):
    for message in ["what is your return policy?", "can I get a refund on a return"]:
        result = respond(message, db_path=db_path)
        assert result["intent"] == "faq"
        assert result["source"] == "faq_kb:returns:sample"
        assert result["reply"] == _sample("returns")
        assert result["reply"] != assistant.FAREWELL_REPLY
        assert result["reply"] != assistant.GREETING_REPLY


# ════════════════════════════════════════════════════════════════════════════
# Phase G1 — farewell must behave IDENTICALLY with Gemini enabled and disabled.
# The deterministic farewell check now runs at the TOP of the Gemini branch,
# BEFORE classify_message (Gemini has no `farewell` intent and would otherwise
# classify 'bye' as 'other' and answer with the generic fallback). EVERY case
# here MOCKS the wrapper, so no live API call is ever made.
# ════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("message", [
    "bye", "goodbye", "see you", "see you later",
    "okay bye", "allah hafiz", "khuda hafiz",
])
def test_g1_farewell_works_with_gemini_enabled(message, monkeypatch):
    # Gemini ON and it insists the farewell is unrelated 'other'.
    _enable_gemini(monkeypatch, {"intent": "other", "language": "en"})
    result = respond(message, db_path=None)
    assert result["intent"] == "farewell"
    assert result["source"] == "fixed"
    assert result["reply"] == assistant.FAREWELL_REPLY


def test_g1_farewell_beats_gemini_greeting_misclassification(monkeypatch):
    # Even when Gemini classifies 'bye' as 'greeting', we still say farewell.
    _enable_gemini(monkeypatch, {"intent": "greeting", "language": "en"})
    result = respond("bye", db_path=None)
    assert result["intent"] == "farewell"
    assert result["reply"] == assistant.FAREWELL_REPLY


def test_g1_farewell_with_gemini_disabled(db_path, monkeypatch):
    monkeypatch.delenv("GEMINI_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = respond("khuda hafiz", db_path=db_path)
    assert result["intent"] == "farewell"
    assert result["source"] == "fixed"


def test_g1_farewell_when_gemini_api_fails(monkeypatch):
    # Enabled but classify returns None (timeout / API failure): the farewell
    # still resolves deterministically rather than degrading to the fallback.
    _enable_gemini(monkeypatch, None)
    result = respond("see you later", db_path=None)
    assert result["intent"] == "farewell"
    assert result["reply"] == assistant.FAREWELL_REPLY


def test_g1_gemini_never_consulted_for_farewell(monkeypatch):
    # A farewell must not spend a Gemini call at all.
    calls = _enable_gemini_spy(monkeypatch, {"intent": "other", "language": "en"})
    result = respond("goodbye", db_path=None)
    assert result["intent"] == "farewell"
    assert calls["classify"] == 0


@pytest.mark.parametrize("message", [
    "thank you",
    "I want to say goodbye to my friend",
    "can I say my goodbyes to everyone",
])
def test_g1_farewell_false_positives_stay_with_gemini_enabled(message, monkeypatch):
    # The guards survive the reorder: none of these become a farewell, even
    # with Gemini ON classifying them as 'other'.
    _enable_gemini(monkeypatch, {"intent": "other", "language": "en"})
    assert respond(message, db_path=None)["intent"] != "farewell"


def test_g1_return_refund_with_gemini_enabled(db_path, monkeypatch):
    # G2: with Gemini ON the return question returns the RETURNS SAMPLE answer.
    _enable_gemini(monkeypatch, {"intent": "returns", "language": "en"})
    result = respond("what is your return policy?", db_path=db_path)
    assert result["reply"] == _sample("returns")


def test_g1_order_lookup_unaffected_with_gemini_enabled(db_path, monkeypatch):
    # The pre-Gemini farewell check must not disturb the SQLite order path.
    _enable_gemini(monkeypatch, {"intent": "other", "language": "en"})
    result = respond("where is ORD-1002", db_path=db_path)
    assert result["source"] == "orders_db"
    assert "shipped" in result["reply"]


def test_g1_greeting_still_routes_through_gemini_when_classified(monkeypatch):
    # Preserved: greeting keeps its Gemini classification path (only farewell
    # was lifted above the model), so this lineage marker is unchanged.
    _enable_gemini(monkeypatch, {"intent": "greeting", "language": "en"})
    result = respond("hello there", db_path=None)
    assert result["intent"] == "greeting"
    assert result["source"] == "gemini:fixed"


# ════════════════════════════════════════════════════════════════════════════
# Phase E.2 — Gratitude response. A thank-you used to reach the honest fallback
# ("I'm not sure how to help with that yet") because it is deliberately NOT a
# farewell. It now has its own DETERMINISTIC intent, matched with the same
# whole-word / short-message guards as greeting and farewell, so it can never
# steal a message that carries a real request. Gemini is mocked wherever it
# appears below: no live call is ever made.
# ════════════════════════════════════════════════════════════════════════════
GRATITUDE_CASES = [
    # English
    "thank you", "thanks", "thanks a lot", "thank you so much",
    "thank you very much", "many thanks", "thanks so much",
    # Roman Urdu
    "shukriya", "bohat shukriya", "bahut shukriya", "aap ka shukriya",
    "shukriya bohat", "shukria",
    # Urdu script
    "شکریہ", "بہت شکریہ", "آپ کا شکریہ",
]


@pytest.mark.parametrize("message", GRATITUDE_CASES)
def test_gratitude_detected_all_languages(message, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "gratitude"
    assert result["source"] == "fixed"


@pytest.mark.parametrize("message", [
    "Thank you", "Thanks", "Thanks a lot", "Thank you so much",
])
def test_gratitude_ignores_capitalization_and_punctuation(message, db_path):
    # The voice path hands us whatever the browser recognized, often
    # capitalised and sometimes punctuated: "Thank you!" must count too.
    assert respond(message, db_path=db_path)["intent"] == "gratitude"
    assert respond(message + "!", db_path=db_path)["intent"] == "gratitude"


@pytest.mark.parametrize("message", ["Thank you.", "Thanks a lot.", "shukriya."])
def test_gratitude_survives_the_voice_turn_terminator(message, db_path):
    # call.js submits a buffer as soon as it ends with .!? - the trailing period
    # arrives here and must not hide the thank-you.
    assert respond(message, db_path=db_path)["intent"] == "gratitude"


def test_gratitude_reply_wording(db_path):
    result = respond("thank you", db_path=db_path)
    assert result["reply"] == "You're welcome! Is there anything else I can help you with?"
    assert result["reply"] == assistant.GRATITUDE_REPLY
    # Thanking us is NOT leaving: it must not reuse the farewell sentence.
    assert result["reply"] != assistant.FAREWELL_REPLY
    assert "goodbye" not in result["reply"].lower()


def test_gratitude_in_urdu_script_answers_in_urdu(db_path):
    result = respond("بہت شکریہ", db_path=db_path)
    assert result["intent"] == "gratitude"
    assert result["reply"] == assistant.GRATITUDE_REPLY_URDU


# Requirement 3: gratitude is a STANDALONE-message intent and never overrides a
# message that also contains a real question — whichever rule understands the
# request wins.
@pytest.mark.parametrize("message", [
    "thanks, what is my order status?",
    "thank you, but what is the status of my order?",
    "thanks order status",
    "shukriya, mera order status kya hai",
])
def test_gratitude_does_not_override_an_order_question(message, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] != "gratitude"
    assert result["intent"] == "order_status_missing_id", result
    assert result["reply"] == assistant.ORDER_ID_REQUEST_REPLY


@pytest.mark.parametrize("message,topic", [
    ("thanks, what is your return policy?", "returns"),
    ("thank you, how long does shipping take?", "shipping"),
])
def test_gratitude_does_not_override_a_policy_question(message, topic, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "faq"
    assert result["source"] == f"faq_kb:{topic}:sample"
    assert result["reply"] == _sample(topic)


def test_gratitude_does_not_override_a_known_order(db_path):
    # A thank-you glued onto a real lookup still reads SQLite.
    result = respond("thanks, status of ORD-1001", db_path=db_path)
    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"
    assert "ORD-1001" in result["reply"]


# False-positive guards, mirroring the farewell ones.
def test_thanks_mentioned_in_a_long_sentence_is_not_gratitude(db_path):
    # > 4 tokens, so the short-message guard rejects it: the customer is
    # describing something else and must NOT get a "you're welcome".
    msg = "thanks for letting me know about the delay"
    result = respond(msg, db_path=db_path)
    assert result["intent"] != "gratitude"
    assert result["reply"] != assistant.GRATITUDE_REPLY
    # Nothing else in that sentence names an order or a policy topic, so the
    # honest fallback is the correct, unchanged verdict.
    assert result["intent"] == "fallback"
    assert result["source"] == "none"
    assert result["reply"] == assistant.FALLBACK_REPLY


def test_bare_thank_you_is_still_not_a_farewell(db_path):
    # The pre-existing guard: a thank-you does not close the call.
    assert respond("thank you", db_path=db_path)["intent"] != "farewell"


def test_thank_you_with_goodbye_is_a_farewell_not_gratitude(db_path):
    # When both are said, leaving wins - that message ends the conversation.
    assert respond("thank you bye", db_path=db_path)["intent"] == "farewell"


# Preservation: the response contract, the fallback, and the model budget.
def test_gratitude_keeps_the_chat_response_contract(client):
    data = client.post("/api/chat", json={"message": "Thank you"}).get_json()
    assert set(data) == {"intent", "reply", "source"}
    assert data["intent"] == "gratitude"
    assert data["reply"] == assistant.GRATITUDE_REPLY
    assert data["source"] == "fixed"


def test_gratitude_leaves_the_unknown_question_fallback_honest(db_path):
    # The fallback is still there for genuinely unknown asks; gratitude did not
    # become a catch-all.
    result = respond("do you sell helicopters", db_path=db_path)
    assert result["intent"] == "fallback"
    assert result["source"] == "none"
    assert result["reply"] == assistant.FALLBACK_REPLY


def test_gratitude_works_with_gemini_disabled(db_path, monkeypatch):
    monkeypatch.delenv("GEMINI_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert respond("thanks", db_path=db_path)["intent"] == "gratitude"


@pytest.mark.parametrize("message", ["thanks", "thank you so much", "shukriya"])
def test_gratitude_is_deterministic_with_gemini_enabled(message, monkeypatch):
    # Same verdict whether the model is on or off, and the model's own opinion
    # (here 'other') is never allowed to downgrade it to the fallback.
    _enable_gemini(monkeypatch, {"intent": "other", "language": "en"})
    result = respond(message, db_path=None)
    assert result["intent"] == "gratitude"
    assert result["source"] == "fixed"
    assert result["reply"] == assistant.GRATITUDE_REPLY


def test_gemini_never_consulted_for_gratitude(monkeypatch):
    calls = _enable_gemini_spy(monkeypatch, {"intent": "other", "language": "en"})
    assert respond("thank you", db_path=None)["intent"] == "gratitude"
    assert calls["classify"] == 0
    assert calls["rephrase"] == []


def test_gratitude_does_not_disturb_greeting_or_farewell(db_path):
    assert respond("hello", db_path=db_path)["intent"] == "greeting"
    assert respond("goodbye", db_path=db_path)["intent"] == "farewell"
    assert respond("bye", db_path=db_path)["intent"] == "farewell"


# The exact phrase list from the bug report, checked end-to-end through the real
# /api/chat route (Text Chat) AND through respond() (the path the voice call
# reaches after the browser turns speech into text). This is the contract that
# proves the fix: if a live server disagrees with these verdicts, it is running
# OUTDATED code (needs a restart), NOT a missing intent.
REPORT_PHRASES = [
    # (message, expected intent)
    ("Thank you", "gratitude"),
    ("Shukriya", "gratitude"),
    ("Bohat shukriya", "gratitude"),
    ("Mera order kahan hai?", "order_status_missing_id"),
    ("Mera order kab deliver hoga?", "order_status_missing_id"),
    ("آرڈر کی حالت بتائیں", "order_status_missing_id"),
]


@pytest.mark.parametrize("message,expected", REPORT_PHRASES)
def test_report_phrases_via_api_and_rules(message, expected, client, db_path):
    via_api = client.post("/api/chat", json={"message": message}).get_json()
    assert via_api["intent"] == expected, (message, via_api)
    assert set(via_api) == {"intent", "reply", "source"}
    via_rules = respond(message, db_path=db_path)
    assert via_rules["intent"] == expected
    assert via_api["reply"] == via_rules["reply"]


@pytest.mark.parametrize("message", ["Thank you", "Shukriya", "Bohat shukriya"])
def test_report_gratitude_phrases_are_never_the_fallback(message, client):
    # Regression for the reported symptom: these used to answer
    # "I'm not sure how to help with that yet" over the voice call.
    data = client.post("/api/chat", json={"message": message}).get_json()
    assert data["intent"] == "gratitude"
    assert data["reply"] != assistant.FALLBACK_REPLY
    assert "welcome" in data["reply"].lower()



# ════════════════════════════════════════════════════════════════════════════
# Phase G2 — activated SAMPLE FAQ policies across English / Urdu script / Roman
# Urdu / mixed. Deterministic (conftest clears GEMINI_*), so no network. Every
# category must route to its correct topic and return that topic's sample
# answer verbatim from data/faq.json — never an invented policy.
# ════════════════════════════════════════════════════════════════════════════
G2_FAQ_CASES = [
    # English
    ("What is your return policy?", "returns"),
    ("Can I return my product?", "returns"),
    ("How long does shipping take?", "shipping"),
    ("Can I change my order?", "changes"),
    ("What payment methods do you accept?", "payment"),
    ("I want to cancel my order", "cancellation"),
    # Roman Urdu
    ("Refund kab milega?", "returns"),
    ("Mujhe product wapas karna hai.", "returns"),
    ("Shipping kitne din mein hogi?", "shipping"),
    ("Mera order cancel karna hai.", "cancellation"),
    ("Payment methods kya hain?", "payment"),
    ("Address tabdeel karna hai", "changes"),
    # Urdu script
    ("میں اپنا آرڈر منسوخ کرنا چاہتی ہوں؟", "cancellation"),
    ("مجھے پروڈکٹ واپس کرنی ہے۔", "returns"),
    ("ادائیگی کے طریقے کیا ہیں؟", "payment"),
    ("میں اپنا پتہ تبدیل کرنا چاہتا ہوں", "changes"),
]


@pytest.mark.parametrize("message,topic", G2_FAQ_CASES)
def test_g2_multilingual_faq_returns_correct_sample(message, topic, db_path):
    result = respond(message, db_path=db_path)
    assert result["intent"] == "faq"
    assert result["source"] == f"faq_kb:{topic}:sample"
    assert result["reply"] == _sample(topic)
    assert result["reply"].strip()


def test_g2_all_five_categories_have_sample_answers():
    # Data integrity: every sample topic must carry a non-empty answer text.
    for topic in ("shipping", "returns", "cancellation", "changes", "payment"):
        assert _sample(topic) and _sample(topic).strip()


def test_g2_generic_policy_ask_clarifies(db_path):
    # "policy" with no specific topic is ambiguous -> ask which one (never
    # guess a sample answer).
    result = respond("what is your policy", db_path=db_path)
    assert result["intent"] == "ambiguous"
    assert result["reply"] == assistant.CLARIFICATION_REPLY
    assert result["source"] == "none"


def test_g2_short_unmatched_message_is_polite_fallback(db_path):
    # A short message with no order/policy/greeting/farewell signal gets the
    # honest, friendly fallback rather than a fabricated answer.
    result = respond("ok", db_path=db_path)
    assert result["intent"] == "fallback"
    assert result["reply"] == assistant.FALLBACK_REPLY


def test_g2_repeated_message_is_stable(db_path):
    # Stateless router: asking the same question twice gives the same grounded
    # sample answer both times (no cross-message memory needed).
    a = respond("how long does shipping take", db_path=db_path)
    b = respond("how long does shipping take", db_path=db_path)
    assert a == b
    assert a["source"] == "faq_kb:shipping:sample"


def test_g2_gemini_cannot_override_order_facts(db_path, monkeypatch):
    # Gemini ON and it insists the message is a shipping policy; an order id in
    # the text must still be answered from SQLite (rule 1 wins over Gemini).
    _enable_gemini(monkeypatch, {"intent": "shipping", "language": "en"})
    result = respond("status of ORD-1002", db_path=db_path)
    assert result["source"] == "orders_db"
    assert "shipped" in result["reply"]


def test_g2_gemini_cannot_invent_policy_details(db_path, monkeypatch):
    # Gemini ON classifies a returns question; the answer must be BYTE-IDENTICAL
    # to the faq.json sample (translation mocked off), never model-invented text.
    _enable_gemini(monkeypatch, {"intent": "returns", "language": "en"},
                   translation=None)
    result = respond("what is your return policy", db_path=db_path)
    assert result["source"] == "gemini:policy_sample"
    assert result["reply"] == _sample("returns")


# ── Unsupported-policy classification: warranty/guarantee -> honest fallback ──
def test_warranty_policy_returns_honest_fallback(db_path):
    # "warranty" is a policy question but NOT one of the five sample topics, so
    # the app gives its existing honest fallback - never the clarification ask.
    result = respond("What is your warranty policy?", db_path=db_path)
    assert result["intent"] == "fallback"
    assert result["reply"] == assistant.FALLBACK_REPLY
    assert result["reply"] != assistant.CLARIFICATION_REPLY
    assert result["source"] == "none"


def test_guarantee_policy_returns_honest_fallback(db_path):
    # Same class as warranty: a specific policy subject with no matching FAQ
    # topic is unsupported -> honest fallback, not a vague clarification.
    result = respond("what is your guarantee policy?", db_path=db_path)
    assert result["intent"] == "fallback"
    assert result["reply"] == assistant.FALLBACK_REPLY


def test_bare_policy_still_asks_clarification(db_path):
    # A vague policy ask with no specific unmapped subject must STILL clarify
    # (behavior explicitly required to stay unchanged).
    result = respond("Tell me about your policy.", db_path=db_path)
    assert result["intent"] == "ambiguous"
    assert result["reply"] == assistant.CLARIFICATION_REPLY
    assert result["source"] == "none"


@pytest.mark.parametrize("topic,msg", [
    ("shipping", "how long does delivery usually take"),
    ("returns", "can I get a refund on a return"),
    ("cancellation", "can I cancel my order"),
    ("changes", "I need to change the wrong address on my order"),
    ("payment", "how do I pay with a card"),
])
def test_supported_faq_topics_still_work(topic, msg, db_path):
    # The five sample topics are untouched: each still returns its VERBATIM
    # SAMPLE answer from data/faq.json (never the fallback or a clarification).
    result = respond(msg, db_path=db_path)
    assert result["intent"] == "faq"
    assert result["source"] == f"faq_kb:{topic}:sample"
    assert result["reply"] == _sample(topic)


# ════════════════════════════════════════════════════════════════════════
# Natural-language understanding: the SAME question said DIFFERENT ways.
# Every test here runs with Gemini OFF (the conftest autouse fixture clears the
# flag and the key), so what is under test is the DETERMINISTIC router alone:
# wording that used to fall through to the generic fallback must now resolve.
# ════════════════════════════════════════════════════════════════════════
_ORDER_STATUS_NO_ID_PHRASES = [
    # English, including phrasings with no "status / where / track" wording.
    "What is the status of my order?",
    "What is my order status?",
    "Where is my parcel?",
    "Has my order arrived?",
    "Can you tell me about my order?",
    "When will I get my order?",
    "Any update on my shipment?",
    "Did my order get delivered yet?",
    "Is my shipment moving?",
    # Roman Urdu
    "Mera order kahan tak pohancha hai?",
    "Mera parcel deliver hua ya nahi?",
    "kya mera order aa gaya",
    "mera order track kar sakte hain",
    "order ka status check karo",
    # Urdu script (escaped so the file bytes are exact): "where is my order?",
    # "when will my order come", "tell me the condition of the order".
    "\u0645\u06cc\u0631\u0627 \u0622\u0631\u0688\u0631 \u06a9\u06c1\u0627\u06ba \u06c1\u06d2\u061f",
    "\u0645\u06cc\u0631\u0627 \u0622\u0631\u0688\u0631 \u06a9\u0628 \u0622\u0626\u06d2 \u06af\u0627\u061f",
    "\u0622\u0631\u0688\u0631 \u06a9\u06cc \u062d\u0627\u0644\u062a \u0628\u062a\u0627\u0626\u06cc\u06ba",
    # Mixed Urdu-English
    "where is mera order ka status",
]


@pytest.mark.parametrize("msg", _ORDER_STATUS_NO_ID_PHRASES)
def test_varied_order_status_wording_asks_for_the_order_number(msg, db_path):
    # Every phrasing is the SAME intent: a question about their own parcel with no
    # id given. The honest answer is to ASK for the number - never to guess an
    # order, and never to shrug with the generic fallback.
    result = respond(msg, db_path=db_path)
    assert result["intent"] == "order_status_missing_id", msg
    assert result["reply"] == assistant.ORDER_ID_REQUEST_REPLY, msg
    assert result["source"] == "none", msg


@pytest.mark.parametrize("msg,status", [
    ("where is my order ORD-1002", "shipped"),
    ("order number one zero zero two status", "shipped"),
    ("tracking 1002 please", "shipped"),
    ("Mera order 1001 kahan hai", "delivered"),
    ("can you check order 1003 for me", "processing"),
    # Urdu script: "where is my order 1003" written with Urdu digit glyphs.
    ("\u0645\u06cc\u0631\u0627 \u0622\u0631\u0688\u0631 \u0661\u0660\u0660\u0663 "
     "\u06a9\u06c1\u0627\u06ba \u06c1\u06d2", "processing"),
])
def test_varied_wording_with_a_number_reads_the_real_status(msg, status, db_path):
    # The wording changes but the DATA never does: the status is whatever SQLite
    # holds for that id, word for word from describe_order().
    result = respond(msg, db_path=db_path)
    assert result["intent"] == "order_status", msg
    assert result["source"] == "orders_db", msg
    assert status in result["reply"], (msg, result["reply"])


_VARIED_POLICY_PHRASES = [
    # Order status / tracking vs SHIPPING POLICY must not be confused: these
    # name no order at all, so they are policy questions.
    ("shipping", "how long does delivery usually take"),
    ("shipping", "which courier company delivers my parcel"),
    ("shipping", "delivery mein kitne din lagte hain"),
    ("shipping", "courier kaunsa use karte ho"),
    # Urdu script: "how many days does delivery take"
    ("shipping", "\u062a\u0631\u0633\u06cc\u0644\u0627\u062a \u0645\u06cc\u06ba \u06a9\u062a\u0646\u06d2 "
                 "\u062f\u0646 \u0644\u06af\u062a\u06d2 \u06c1\u06cc\u06ba\u061f"),
    # Returns / refunds, said without ever using the word "return"
    ("returns", "can I send this item back to you"),
    ("returns", "I want my money back"),
    ("returns", "main ye item wapas bhejna chahta hoon"),
    # Urdu script: "I want to send this thing back"
    ("returns", "\u0645\u06cc\u06ba \u06cc\u06c1 \u0686\u06cc\u0632 \u0648\u0627\u067e\u0633 "
                "\u0628\u06be\u06cc\u062c\u0646\u0627 \u0686\u0627\u06c1\u062a\u0627 "
                "\u06c1\u0648\u06ba"),
    # Cancellation, said without the word "cancel"
    ("cancellation", "please stop my order"),
    ("cancellation", "please remove my order from your system"),
    # Urdu script: "can I get my order cancelled?"
    ("cancellation", "\u0622\u0631\u0688\u0631 \u06a9\u0646\u0633\u0644 \u06a9\u0631\u0648\u0627 "
                     "\u0633\u06a9\u062a\u06d2 \u06c1\u06cc\u06ba\u061f"),
    # Changes: a shifted address, or a wrong item to fix
    ("changes", "I shifted house update my delivery location"),
    ("changes", "the address on my order is wrong"),
    ("changes", "order mein item galat ho gaya hai theek kar dein"),
    # Urdu script: "I need to change the address"
    ("changes", "\u067e\u062a\u06c1 \u0628\u062f\u0644\u0646\u0627 \u06c1\u06d2"),
    # Payment
    ("payment", "my card was charged twice"),
    ("payment", "can I pay cash on delivery"),
    ("payment", "do you accept apple pay"),
    # Urdu script: "how will the money be returned", "how do I pay"
    ("payment", "\u067e\u06cc\u0633\u06d2 \u06a9\u06cc\u0633 \u0645\u0644\u06cc\u06ba "
                "\u06af\u06d2"),
    ("payment", "\u0627\u062f\u0627\u0626\u06cc\u06af\u06cc \u06a9\u06cc\u0633\u06d2 "
                "\u06a9\u0631\u0648\u06ba"),
]


@pytest.mark.parametrize("topic,msg", _VARIED_POLICY_PHRASES)
def test_varied_policy_wording_maps_to_the_right_sample_topic(topic, msg, db_path):
    # Different wording must not change the ANSWER: the reply is always the
    # verbatim SAMPLE text stored in data/faq.json for that topic.
    result = respond(msg, db_path=db_path)
    assert result["intent"] == "faq", msg
    assert result["source"] == f"faq_kb:{topic}:sample", msg
    assert result["reply"] == _sample(topic), msg


def test_personal_order_question_is_not_answered_with_a_shipping_policy(db_path):
    # Data accuracy: faq.json matches keywords as SUBSTRINGS, so "shipment"
    # contains "ship". A customer describing their OWN parcel must be asked for
    # the order number, not handed the generic shipping policy as if it were
    # their status.
    for msg in ("any update on my shipment?", "where is my shipment now?",
                "has my shipment been dispatched?"):
        result = respond(msg, db_path=db_path)
        assert result["intent"] == "order_status_missing_id", msg
        assert "1-2 business days" not in result["reply"], msg


def test_change_request_beats_the_order_status_reading(db_path):
    # "...theek kar dein" (please fix it) mentions the order AND uses a status
    # cue ("gaya"), but it is a request to CHANGE the order: the policy branch
    # must win, so the customer gets the change rules instead of "give me your
    # order number".
    result = respond("order mein item galat ho gaya hai theek kar dein",
                     db_path=db_path)
    assert result["source"] == "faq_kb:changes:sample"


@pytest.mark.parametrize("msg", [
    "when do you open",
    "I will come back later",
    "what is your favourite food",
])
def test_broadened_cues_do_not_invent_an_order_question(msg, db_path):
    # Guard for the widened vocabulary: an everyday sentence that merely contains
    # a word like "when", "back" or "get" must NOT be read as an order question.
    result = respond(msg, db_path=db_path)
    assert result["intent"] == "fallback", msg
    assert result["reply"] == assistant.FALLBACK_REPLY, msg


@pytest.mark.parametrize("msg", [
    "do you have a physical store in Lahore",
    "tell me about the weather",
    "who founded this company",
])
def test_questions_we_cannot_answer_stay_honest(msg, db_path):
    # Understanding more wording must not mean claiming to know more topics.
    result = respond(msg, db_path=db_path)
    assert result["intent"] == "fallback", msg
    assert result["source"] == "none", msg
    assert result["reply"] == assistant.FALLBACK_REPLY, msg


# ════════════════════════════════════════════════════════════════════════
# Gemini used PROPORTIONATELY: it classifies wording the rules cannot settle,
# but it can neither invent facts, discard a rule answer, nor be consulted when
# the rules already know. EVERY test below MOCKS the wrapper - no live call.
# ════════════════════════════════════════════════════════════════════════
def test_gemini_other_verdict_does_not_discard_a_rule_answer(db_path, monkeypatch):
    # The model shrugs ("other") at a natural order question. Before, that verdict
    # ended the turn with the generic fallback; now the deterministic rules still
    # get their turn and ask for the order number.
    _enable_gemini(monkeypatch, {"intent": "other", "language": "en"})
    result = respond("when will I get my order", db_path=db_path)
    assert result["intent"] == "order_status_missing_id"
    assert result["reply"] == assistant.ORDER_ID_REQUEST_REPLY
    assert result["source"] == "none"


def test_gemini_ambiguous_verdict_does_not_discard_a_rule_answer(db_path, monkeypatch):
    _enable_gemini(monkeypatch, {"intent": "ambiguous", "language": "en"})
    result = respond("how long does delivery take", db_path=db_path)
    assert result["intent"] == "faq"
    assert result["source"] == "faq_kb:shipping:sample"
    assert result["reply"] == _sample("shipping")


def test_gemini_only_says_fallback_when_the_rules_also_fail(db_path, monkeypatch):
    # The model's hesitation is used HONESTLY at the end: nothing matched, so we
    # admit it (still no invented answer), localized per the model's language hint.
    _enable_gemini(monkeypatch, {"intent": "other", "language": "en"})
    result = respond("who founded this company", db_path=db_path)
    assert result["intent"] == "fallback"
    assert result["reply"] == assistant.FALLBACK_REPLY
    assert result["source"] == "gemini:fixed"


def test_gemini_low_confidence_asks_instead_of_acting(db_path, monkeypatch):
    # needs_clarification is the model's own "I am not sure" signal. We take it
    # seriously even when our rules WOULD have guessed: a clarifying question is
    # safer than acting on a shaky classification.
    _enable_gemini(monkeypatch, {
        "intent": "order_status", "language": "en",
        "order_id": "", "needs_clarification": True,
    })
    result = respond("where is my parcel", db_path=db_path)
    assert result["intent"] == "ambiguous"
    assert result["reply"] == assistant.CLARIFICATION_REPLY
    assert result["source"] == "gemini:fixed"


def test_gemini_failure_leaves_the_deterministic_answer_unchanged(db_path, monkeypatch):
    # classify_message returning None is what a timeout / API failure / malformed
    # payload looks like from here. The reply must be EXACTLY the rules-only reply.
    phrasing = "can you tell me about my order"
    monkeypatch.delenv("GEMINI_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    without_model = respond(phrasing, db_path=db_path)

    _enable_gemini(monkeypatch, None)
    with_model_failed = respond(phrasing, db_path=db_path)

    assert without_model == with_model_failed
    assert with_model_failed["intent"] == "order_status_missing_id"


def test_gemini_rogue_intent_label_is_validated_before_routing(db_path, monkeypatch):
    # The REAL classify_message runs here; only the raw model text is faked. An
    # intent outside the approved list is normalized to "other", so a rogue (or
    # prompt-injected) label can never reach - or steer - the router.
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(
        assistant.gemini_module, "generate_text",
        lambda *a, **k: '{"intent": "delete_everything", "language": "en"}',
    )
    result = respond("when will I get my order", db_path=db_path)
    assert result["intent"] == "order_status_missing_id"
    assert result["source"] == "none"


def test_gemini_unparseable_payload_is_dropped(db_path, monkeypatch):
    # Truncated JSON: classify_message returns None and the rules answer alone.
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(
        assistant.gemini_module, "generate_text",
        lambda *a, **k: '{"intent": "shipp',
    )
    result = respond("my card was charged twice", db_path=db_path)
    assert result["source"] == "faq_kb:payment:sample"
    assert result["reply"] == _sample("payment")


def test_gemini_extracted_number_is_used_only_because_the_customer_said_it(db_path, monkeypatch):
    # "what about 1002" gives no order-reference word, so rule 1 does not resolve
    # it and the model is consulted. It extracted 1002 from THIS message, which we
    # can verify against the customer's own words - so the number is usable, but
    # the STATUS still comes from SQLite (source orders_db).
    _enable_gemini(monkeypatch, {
        "intent": "order_status", "language": "en",
        "order_id": "ORD-1002", "needs_clarification": False,
    })
    result = respond("what about 1002", db_path=db_path)
    assert result["intent"] == "order_status"
    assert result["source"] == "orders_db"
    assert result["reply"].startswith("Order ORD-1002")


def test_gemini_invented_number_is_discarded_not_looked_up(db_path, monkeypatch):
    # The customer named NO number, so whatever the model claims is unverifiable:
    # it must never be sent to SQLite (a fabricated id could otherwise return a
    # real stranger's order) - we ask the customer instead.
    _enable_gemini(monkeypatch, {
        "intent": "order_status", "language": "en",
        "order_id": "ORD-1002", "needs_clarification": False,
    })
    result = respond("where is my parcel right now", db_path=db_path)
    assert result["intent"] == "order_status_missing_id"
    assert result["source"] == "gemini:fixed"
    assert "ORD-1002" not in result["reply"]
    assert "shipped" not in result["reply"]


def test_model_is_not_consulted_when_the_rules_already_resolve(db_path, monkeypatch):
    # "Do not call Gemini unnecessarily": an order id is answered by SQLite alone,
    # and a short goodbye is resolved by the deterministic farewell rule, so the
    # model is never asked (no cost, no latency, no new failure mode) even when
    # the feature is fully enabled.
    calls = _enable_gemini_spy(monkeypatch, {"intent": "other", "language": "en"})
    assert respond("status of ORD-1002", db_path=db_path)["source"] == "orders_db"
    assert respond("bye", db_path=db_path)["intent"] == "farewell"
    assert calls["classify"] == 0


def test_chat_contract_unchanged_for_new_wording(client):
    # Step 5: /api/chat keeps the SAME response contract for these phrasings.
    data = client.post("/api/chat", json={"message": "Where is my parcel?"}).get_json()
    assert set(data.keys()) == {"intent", "reply", "source"}
    assert data["intent"] == "order_status_missing_id"


# ════════════════════════════════════════════════════════════════════════
# VOICE vs TEXT must reach the SAME backend with the SAME turn. The reported
# "I am not sure" during calls was not intent detection: recognition split one
# spoken sentence into fragments at a natural pause, and each fragment on its
# own really is unanswerable. These tests pin both halves of that contract -
# what the voice layer now assembles, and how honestly a lone fragment is
# treated (no guessing an order question from half a sentence).
# ════════════════════════════════════════════════════════════════════════
_VOICE_ASSEMBLED_TURNS = [
    # Exactly what tests/js/test_call_state.js asserts the call controller now
    # POSTs as {"message": ...} for a sentence spoken across a pause.
    "What is the status of my order?",
    "the status of my order?",
    "one zero zero one",
]


@pytest.mark.parametrize("turn", _VOICE_ASSEMBLED_TURNS)
def test_voice_turn_and_text_chat_get_the_same_answer(turn, client, db_path):
    # One backend, one router: the voice path must not be answered differently
    # from the typed path, so the two replies have to be identical.
    via_text = client.post("/api/chat", json={"message": turn}).get_json()
    via_rules = respond(turn, db_path=db_path)
    assert via_text["intent"] == via_rules["intent"]
    assert via_text["reply"] == via_rules["reply"]
    assert via_text["source"] == via_rules["source"]


def test_voice_turn_with_a_number_reads_the_database(client):
    # The assembled voice turn is a real order question, answered from SQLite.
    data = client.post(
        "/api/chat", json={"message": "What is the status of my order? one zero zero one"}
    ).get_json()
    assert data["intent"] == "order_status"
    assert data["source"] == "orders_db"
    assert data["reply"].startswith("Order ORD-1001")


# The pieces the old 750 ms window used to submit on their own.
_VOICE_FRAGMENTS = [
    "What is the status",
    "of my order?",
    "What is",
    "the status of my",
    "tell me about the",
]


@pytest.mark.parametrize("fragment", _VOICE_FRAGMENTS)
def test_half_a_question_is_answered_honestly(fragment, db_path):
    # No order reference (or no question at all) survives the split, so the
    # router must NOT invent an intent from a fragment: it admits it cannot tell
    # and asks again. This is correct behaviour - the fix belongs in turn
    # assembly, not in guessing here.
    result = respond(fragment, db_path=db_path)
    assert result["intent"] == "fallback", fragment
    assert result["source"] == "none", fragment
    assert result["reply"] == assistant.FALLBACK_REPLY, fragment


