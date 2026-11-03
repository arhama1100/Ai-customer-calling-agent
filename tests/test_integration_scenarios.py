"""
tests/test_integration_scenarios.py
───────────────────────────────────
TASK 6 - FINAL INTEGRATION. These tests drive the TWELVE assignment customer
scenarios through the REAL Flask app exactly as the browser does:

    1. POST /api/session/start (with explicit consent)   -> a session id
    2. POST /api/chat            for each customer message -> intent + reply
    3. POST /api/session/<id>/end  with the turns          -> stored record

so a single test proves the Task 1/2 routing, the Task 3/4 transcript shape, and
the Task 5 session record + honest summary all WORK TOGETHER. Nothing is mocked
here except the browser itself; the Gemini autouse fixture in conftest keeps
every answer deterministic and OFFLINE (no live model call is ever made).

Honesty assertions are the point of this file: no invented policy, no false
"an agent has been notified" / "a real call happened" claim, an honest outcome
(escalated > unresolved > resolved), consent-gated persistence, and the staff
list view never exposing the raw transcript. These are SIMULATED browser calls -
no telephone call is placed and no real person is contacted.
"""

import re

# A uuid4 hex session id, the same contract the backend validates.
HEX32 = re.compile(r"^[0-9a-f]{32}$")

# Words/phrases that must NEVER appear, because they would be a false claim:
# a live transfer, a person being contacted, or a real telephone call.
FORBIDDEN_CLAIMS = (
    "I have transferred you",
    "I've transferred you",
    "transferring you now",
    "an agent will call you",
    "I will call you",
    "I've notified an agent",
    "I have notified an agent",
    "a real agent is on the line",
    "connecting your call",
)


# ── Helpers that mirror the browser call flow ──────────────────────────────
def start(client, consent=True, channel="voice"):
    resp = client.post(
        "/api/session/start", json={"consent": consent, "channel": channel}
    )
    assert resp.status_code == 200
    return resp.get_json()


def chat(client, message):
    resp = client.post("/api/chat", json={"message": message})
    assert resp.status_code == 200
    return resp.get_json()


def end(client, session_id, turns):
    resp = client.post(f"/api/session/{session_id}/end", json={"turns": turns})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def run_call(client, messages, channel="voice"):
    """One whole SIMULATED call: consented start -> each turn -> end.

    Returns (chat_results, session_record). `chat_results[i]` is the /api/chat
    dict for messages[i]; the recorded turn reuses that EXACT reply and intent
    (just like the frontend onReply hook), so transcript and record cannot drift.
    """
    started = start(client, consent=True, channel=channel)
    assert HEX32.match(started["session_id"]), "a consented start returns a hex id"
    results = []
    turns = []
    for msg in messages:
        r = chat(client, msg)
        results.append(r)
        turns.append({"customer": msg, "reply": r["reply"], "intent": r["intent"]})
    record = end(client, started["session_id"], turns)
    return results, record


def assert_honest(reply):
    for bad in FORBIDDEN_CLAIMS:
        assert bad.lower() not in reply.lower(), f"reply makes a false claim: {bad!r}"


# ── 1) Order status (English, with an id) -> resolved from SQLite ──────────
def test_scenario_order_status_resolved_from_db(client):
    results, record = run_call(client, ["What is the status of order ORD-1002?"])
    r = results[0]
    assert r["intent"] == "order_status"
    assert r["source"] == "orders_db", "order facts come ONLY from SQLite"
    assert "ORD-1002" in r["reply"] and "shipped" in r["reply"]
    assert_honest(r["reply"])
    assert record["outcome"] == "resolved"
    assert record["primary_intent"] == "order_status"
    assert record["human_followup"] is False
    assert record["status"] == "completed"


# ── 2) Business hours -> the SAMPLE schedule, never an invented time ───────
def test_scenario_business_hours_sample_schedule(client):
    results, record = run_call(client, ["what are your opening hours?"])
    r = results[0]
    assert r["intent"] == "business_hours"
    assert r["source"] == "faq_kb:business_hours:sample"
    assert "Monday to Saturday" in r["reply"]
    # Honest that this is a demonstration schedule, not an approved policy.
    assert "fictional sample" in r["reply"].lower()
    assert record["outcome"] == "resolved"


# ── 3) Shipping policy -> verbatim FAQ sample (source marks it as sample) ──
def test_scenario_shipping_from_faq(client):
    results, record = run_call(client, ["How long does delivery usually take?"])
    r = results[0]
    assert r["intent"] == "faq"
    assert r["source"] == "faq_kb:shipping:sample"
    assert "3-5 business days" in r["reply"]
    assert record["outcome"] == "resolved"


# ── 4) Returns policy -> verbatim FAQ sample, never an invented policy ─────
def test_scenario_returns_from_faq(client):
    results, record = run_call(client, ["What is your return policy?"])
    r = results[0]
    assert r["intent"] == "faq"
    assert r["source"] == "faq_kb:returns:sample"
    assert "30 days" in r["reply"]
    assert record["outcome"] == "resolved"


# ── 5) Cancellation policy -> verbatim FAQ sample ──────────────────────────
def test_scenario_cancellation_from_faq(client):
    results, record = run_call(client, ["Can I cancel my order?"])
    r = results[0]
    assert r["intent"] == "faq"
    assert r["source"] == "faq_kb:cancellation:sample"
    assert "before they are shipped" in r["reply"]
    assert record["outcome"] == "resolved"


# ── 6) Payment policy -> verbatim FAQ sample ───────────────────────────────
def test_scenario_payment_from_faq(client):
    results, record = run_call(client, ["What payment methods do you accept?"])
    r = results[0]
    assert r["intent"] == "faq"
    assert r["source"] == "faq_kb:payment:sample"
    assert "credit cards" in r["reply"]
    assert record["outcome"] == "resolved"


# ── 7) Human-agent request -> honest handoff, escalated, follow-up flagged ─
def test_scenario_human_agent_escalates_honestly(client):
    results, record = run_call(
        client, ["I want to speak to a human agent please"]
    )
    r = results[0]
    assert r["intent"] == "human_agent_request"
    assert r["source"] == "fixed"
    assert_honest(r["reply"])
    # It must ADMIT the demo cannot transfer and contacted no one.
    low = r["reply"].lower()
    assert "can't transfer" in low or "cannot transfer" in low
    assert "no person has been contacted" in low
    assert "automated assistant" in low
    assert record["outcome"] == "escalated"
    assert record["human_followup"] is True
    # The summary labels follow-up as still owed and says no agent was notified.
    assert "REQUIRES STAFF FOLLOW-UP" in record["summary"]
    assert "no real agent has been notified" in record["summary"].lower()


# ── 8) Unsupported question -> honest fallback (no invented policy) ────────
def test_scenario_unsupported_is_honest_fallback(client):
    results, record = run_call(client, ["Do you offer a warranty on products?"])
    r = results[0]
    assert r["intent"] == "fallback"
    assert r["source"] == "none"
    assert_honest(r["reply"])
    assert record["outcome"] == "unresolved", "an unanswered ask is NOT resolved"
    assert "not sure how to help" in r["reply"].lower()


# ── 9) Unclear OR empty input -> never a fake answer, never stored as a turn ─
def test_scenario_empty_and_unclear_never_resolve(client):
    # An empty message and an unclear order number both stay unanswered.
    started = start(client, consent=True)
    empty = chat(client, "")
    assert empty["intent"] == "empty"
    unclear = chat(client, "my order number is one two")
    assert unclear["intent"] == "order_status_unclear"
    turns = [
        {"customer": "", "reply": empty["reply"], "intent": empty["intent"]},
        {"customer": "my order number is one two",
         "reply": unclear["reply"], "intent": unclear["intent"]},
    ]
    record = end(client, started["session_id"], turns)
    assert record["outcome"] == "unresolved"
    assert record["human_followup"] is False
    assert_honest(empty["reply"])
    assert_honest(unclear["reply"])


# ── 10) Human agent WHILE mentioning an order number -> handoff wins ───────
def test_scenario_human_agent_with_order_number_still_handoff(client):
    results, record = run_call(
        client, ["I need to talk to a human about order ORD-1001"]
    )
    r = results[0]
    # The human ask is never silently dropped by the order-id rule.
    assert r["intent"] == "human_agent_request"
    # The reply still offers what Nova CAN do (check the order it was told about).
    assert "order number" in r["reply"].lower()
    assert_honest(r["reply"])
    assert record["outcome"] == "escalated"
    assert record["human_followup"] is True


# ── 11) English spoken order status with NO id -> ask for the number ───────
def test_scenario_english_spoken_status_asks_for_id(client):
    results, record = run_call(client, ["where is my order right now?"])
    r = results[0]
    assert r["intent"] == "order_status_missing_id"
    assert "order number" in r["reply"].lower()
    assert_honest(r["reply"])
    assert record["outcome"] == "unresolved", "no lookup happened, so not resolved"


# ── 12) Roman Urdu + Urdu-script order status -> ask for id (multilingual) ──
def test_scenario_roman_urdu_and_urdu_script(client):
    results, record = run_call(
        client, ["mera order kahan hai?", "آپ کی دکان کب کھلتی ہے؟"]
    )
    # Roman Urdu order-status ask with no id -> request the number.
    assert results[0]["intent"] == "order_status_missing_id"
    # Urdu-script business hours -> the SAMPLE schedule (deterministic, offline).
    assert results[1]["intent"] == "business_hours"
    assert results[1]["source"] == "faq_kb:business_hours:sample"
    for r in results:
        assert_honest(r["reply"])
    # One answered, one unanswered -> honestly NOT fully resolved.
    assert record["outcome"] == "unresolved"
    stored_intents = [t["intent"] for t in record["transcript"]]
    assert stored_intents == ["order_status_missing_id", "business_hours"]


# ── Consent: DECLINED stores nothing; the conversation still works ─────────
def test_consent_declined_persists_nothing(client):
    started = start(client, consent=False)
    assert started["session_id"] is None
    assert started["consent"] is False
    assert "nothing from this simulated call is stored" in started["notice"].lower()
    # The call itself still answers normally even with no recording.
    r = chat(client, "what are your hours?")
    assert r["intent"] == "business_hours"
    # And nothing appears in the staff history because nothing was written.
    listing = client.get("/api/sessions")
    assert listing.status_code == 200
    assert listing.get_json()["sessions"] == []


# ── Multi-turn transcript: order + human request -> chronological + escalated ─
def test_multi_turn_transcript_order_and_human(client):
    results, record = run_call(
        client,
        [
            "What is the status of order ORD-1003?",
            "actually I would like to speak with a person",
        ],
    )
    assert [r["intent"] for r in results] == ["order_status", "human_agent_request"]
    turns = record["transcript"]
    assert len(turns) == 2, "the whole conversation is preserved in order"
    assert turns[0]["customer"] == "What is the status of order ORD-1003?"
    assert turns[1]["customer"] == "actually I would like to speak with a person"
    # Escalation outranks a resolved order lookup.
    assert record["outcome"] == "escalated"
    assert record["human_followup"] is True


# ── Staff history view: metadata only, no raw transcript leaked ────────────
def test_history_view_shows_metadata_without_transcript(client):
    _, record = run_call(client, ["what are your opening hours?"])
    sid = record["session_id"]
    listing = client.get("/api/sessions").get_json()["sessions"]
    row = next(s for s in listing if s["session_id"] == sid)
    assert row["outcome"] == "resolved"
    assert "summary" in row and "started_at" in row
    assert "transcript" not in row, "the dashboard list never exposes raw words"
    # The full record (single-session endpoint) still holds the transcript.
    detail = client.get(f"/api/session/{sid}").get_json()
    assert detail["transcript"][0]["intent"] == "business_hours"
