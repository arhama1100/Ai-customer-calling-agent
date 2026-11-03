"""
tests/test_sessions.py
──────────────────────
Task 5: simulated call-session RECORDS - lifecycle, consent, transcript,
intents/outcome, honest summaries, the staff list view, and safe errors.

Everything runs against the throwaway per-test database (the db_path /
client fixtures); no network, no Gemini, no real calls, no real people.
"""

import json

import pytest

from app import sessions
from app.database import get_connection, init_db
from app.sessions import (
    SessionAlreadyEndedError,
    SessionNotFoundError,
    build_summary,
    derive_outcome,
    end_session,
    get_session,
    is_valid_session_id,
    list_sessions,
    primary_intent,
    sanitize_turns,
    start_session,
)


# ── schema / migration ──────────────────────────────────────────────────────

def test_init_db_creates_both_tables_and_is_repeatable(db_path):
    # seed_orders already ran init_db once; running it again must be safe and
    # must preserve the existing orders rows (backward-compatible migration).
    init_db(db_path)
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        orders = conn.execute("SELECT COUNT(*) AS c FROM orders").fetchone()["c"]
    finally:
        conn.close()
    assert {"orders", "call_sessions"} <= names
    assert orders == 6  # the six fictional seeded demo orders are untouched


def test_existing_database_file_upgrades_without_losing_data(db_path):
    # Simulate an OLD database: drop the sessions table entirely, then let
    # ensure_schema bring it back. The orders must survive untouched.
    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE call_sessions")
        conn.commit()
    finally:
        conn.close()
    sessions.ensure_schema(db_path)
    conn = get_connection(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS c FROM orders").fetchone()["c"] == 6
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM call_sessions"
        ).fetchone()["c"] == 0
    finally:
        conn.close()


# ── consent behavior ────────────────────────────────────────────────────────

def test_start_without_consent_stores_nothing(db_path):
    record = start_session(db_path=db_path, channel="voice", consent=False)
    assert record["session_id"] is None
    assert record["consent"] is False
    assert record["status"] == "not_recorded"
    conn = get_connection(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM call_sessions").fetchone()["c"]
    finally:
        conn.close()
    assert count == 0, "declined consent must persist NOTHING at all"


def test_start_with_consent_creates_active_row(db_path):
    record = start_session(db_path=db_path, channel="voice", consent=True)
    assert is_valid_session_id(record["session_id"])
    assert record["status"] == "active"
    assert record["started_at"]
    # The notice identifies Nova AI as automated - never a person.
    assert "automated" in record["notice"].lower()
    stored = get_session(db_path=db_path, session_id=record["session_id"])
    assert stored["consent"] is True
    assert stored["status"] == "active"


def test_start_rejects_unknown_channel(db_path):
    with pytest.raises(ValueError):
        start_session(db_path=db_path, channel="telephony", consent=True)


# ── session lifecycle: create -> end -> record ──────────────────────────────

def _two_turn_session(db_path):
    sid = start_session(db_path=db_path, channel="voice", consent=True)["session_id"]
    turns = [
        {
            "customer": "What is the status of order ORD-1002?",
            "reply": "Order ORD-1002 for 1 x Desk Lamp is currently shipped.",
            "intent": "order_status",
        }
    ]
    return sid, turns


def test_end_session_stores_transcript_intents_and_outcome(db_path):
    sid, turns = _two_turn_session(db_path)
    record = end_session(db_path=db_path, session_id=sid, turns=turns)
    assert record["status"] == "completed"
    assert record["ended_at"]
    assert record["outcome"] == "resolved"
    assert record["primary_intent"] == "order_status"
    assert record["intents"] == ["order_status"]
    assert record["human_followup"] is False
    assert record["transcript"][0]["customer"] == turns[0]["customer"]
    assert record["transcript"][0]["intent"] == "order_status"


def test_end_empty_session_is_handled_safely(db_path):
    sid = start_session(db_path=db_path, channel="voice", consent=True)["session_id"]
    record = end_session(db_path=db_path, session_id=sid, turns=[])
    assert record["status"] == "completed"
    assert record["outcome"] == "no_conversation"
    assert "empty session" in record["summary"].lower()
    # An honest empty record never claims anything was resolved.
    assert "resolved" not in record["summary"]


def test_end_twice_refuses_honestly(db_path):
    sid, turns = _two_turn_session(db_path)
    end_session(db_path=db_path, session_id=sid, turns=turns)
    with pytest.raises(SessionAlreadyEndedError):
        end_session(db_path=db_path, session_id=sid, turns=turns)


def test_end_unknown_or_invalid_id(db_path):
    with pytest.raises(SessionNotFoundError):
        end_session(db_path=db_path, session_id="f" * 32, turns=[])
    with pytest.raises(ValueError):
        end_session(db_path=db_path, session_id="../../etc/passwd", turns=[])
    with pytest.raises(ValueError):
        end_session(db_path=db_path, session_id=None, turns=[])


# ── outcome + honesty rules ─────────────────────────────────────────────────

def test_fallback_turn_is_never_reported_as_resolved(db_path):
    sid = start_session(db_path=db_path, channel="voice", consent=True)["session_id"]
    record = end_session(
        db_path=db_path,
        session_id=sid,
        turns=[
            {
                "customer": "what's the weather like?",
                "reply": "I'm not sure how to help with that yet.",
                "intent": "fallback",
            }
        ],
    )
    assert record["outcome"] == "unresolved"
    assert "NOT resolved" in record["summary"]


def test_human_agent_request_escalates_and_flags_followup(db_path):
    sid = start_session(db_path=db_path, channel="voice", consent=True)["session_id"]
    record = end_session(
        db_path=db_path,
        session_id=sid,
        turns=[
            {
                "customer": "I want to speak to a human",
                "reply": "I'm Nova AI, an automated assistant...",
                "intent": "human_agent_request",
            }
        ],
    )
    assert record["outcome"] == "escalated"
    assert record["human_followup"] is True
    assert "REQUIRES STAFF FOLLOW-UP" in record["summary"]
    # Honesty: it must also SAY no real agent was notified.
    lowered = record["summary"].lower()
    assert "no real agent has been notified" in lowered
    # And never pretend a person was contacted.
    for banned in ("has been notified that", "we contacted", "we will call you"):
        assert banned not in lowered


def test_escalation_wins_even_beside_an_answered_order_question(db_path):
    sid = start_session(db_path=db_path, channel="voice", consent=True)["session_id"]
    record = end_session(
        db_path=db_path,
        session_id=sid,
        turns=[
            {"customer": "status of ORD-1002?", "reply": "It is shipped.",
             "intent": "order_status"},
            {"customer": "connect me to an agent", "reply": "I'm automated...",
             "intent": "human_agent_request"},
        ],
    )
    assert record["outcome"] == "escalated"
    assert record["human_followup"] is True


def test_pleasantries_only_is_not_called_resolved(db_path):
    sid = start_session(db_path=db_path, channel="voice", consent=True)["session_id"]
    record = end_session(
        db_path=db_path,
        session_id=sid,
        turns=[{"customer": "hello", "reply": "Hi there!", "intent": "greeting"}],
    )
    assert record["outcome"] == "no_conversation"
    assert "substantive answer" not in record["summary"]


# ── turn sanitization (client input is never trusted) ───────────────────────

def test_sanitize_drops_bad_shapes_and_caps_sizes():
    turns, truncated = sanitize_turns("not even a list")
    assert turns == [] and truncated is False

    turns, _ = sanitize_turns(
        [
            {"customer": "x" * 5000, "reply": "ok", "intent": "faq"},  # capped
            {"customer": "", "reply": "   ", "intent": "faq"},          # dropped
            "garbage",                                                    # dropped
            {"customer": "hi", "reply": "hello", "intent": "definitely_not_real"},
            None,
        ]
    )
    assert len(turns) == 2
    assert len(turns[0]["customer"]) == sessions.MAX_TEXT_CHARS
    assert turns[1]["intent"] == "other", "unknown intents are sanitized, never trusted"


def test_turn_cap_truncates_and_the_summary_says_so():
    many = [
        {"customer": f"q{i}", "reply": f"a{i}", "intent": "faq"}
        for i in range(sessions.MAX_TURNS + 25)
    ]
    turns, truncated = sanitize_turns(many)
    assert len(turns) == sessions.MAX_TURNS and truncated
    summary = build_summary(turns, "resolved", truncated)
    assert "truncated" in summary.lower()


def test_sql_text_is_inert_data_not_code(db_path):
    # Parameterized SQL: even a classic injection payload is stored verbatim
    # as harmless text and nothing else happens.
    sid = start_session(db_path=db_path, channel="voice", consent=True)["session_id"]
    evil = "Robert'); DROP TABLE orders;--"
    record = end_session(
        db_path=db_path,
        session_id=sid,
        turns=[{"customer": evil, "reply": "harmless", "intent": "fallback"}],
    )
    assert record["transcript"][0]["customer"] == evil
    conn = get_connection(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS c FROM orders").fetchone()["c"] == 6
    finally:
        conn.close()


# ── list / detail views (privacy) ───────────────────────────────────────────

def test_list_sessions_newest_first_without_raw_transcript(db_path):
    s1, turns = _two_turn_session(db_path)
    end_session(db_path=db_path, session_id=s1, turns=turns)
    s2 = start_session(db_path=db_path, channel="voice", consent=True)["session_id"]
    end_session(
        db_path=db_path,
        session_id=s2,
        turns=[{"customer": "time?", "reply": "here are the hours",
                "intent": "business_hours"}],
    )
    items = list_sessions(db_path=db_path, limit=10)
    assert len(items) == 2
    assert items[0]["session_id"] == s2, "newest first"
    for item in items:
        assert "transcript" not in item, "the list must not expose raw conversation text"
        assert item["summary"]
        assert item["outcome"] in sessions.OUTCOMES


def test_list_limit_is_bounded_and_lenient(db_path):
    for _ in range(3):
        sid = start_session(db_path=db_path, channel="voice", consent=True)["session_id"]
        end_session(db_path=db_path, session_id=sid, turns=[])
    assert len(list_sessions(db_path=db_path, limit="bogus")) == 3  # falls back to 20
    assert len(list_sessions(db_path=db_path, limit=2)) == 2
    assert len(list_sessions(db_path=db_path, limit=9999)) == 3  # capped, not crashing


def test_detail_includes_transcript_and_404s_unknown(db_path):
    sid, turns = _two_turn_session(db_path)
    end_session(db_path=db_path, session_id=sid, turns=turns)
    full = get_session(db_path=db_path, session_id=sid)
    assert full["transcript"][0]["customer"] == turns[0]["customer"]
    with pytest.raises(SessionNotFoundError):
        get_session(db_path=db_path, session_id="a" * 32)


# ── API endpoints (contract + safe errors) ──────────────────────────────────

def _assert_json_error(response, code, error_name):
    assert response.status_code == code
    body = response.get_json()
    assert body["error"] == error_name
    assert isinstance(body["message"], str) and body["message"]


def test_api_full_consented_voice_session_flow(client):
    started = client.post("/api/session/start", json={"consent": True, "channel": "voice"})
    assert started.status_code == 200
    sid = started.get_json()["session_id"]
    assert is_valid_session_id(sid)

    answered = client.post("/api/chat", json={"message": "What is your return policy?"})
    intent = answered.get_json()["intent"]

    ended = client.post(
        f"/api/session/{sid}/end",
        json={"turns": [{"customer": "What is your return policy?",
                         "reply": answered.get_json()["reply"], "intent": intent}]},
    )
    assert ended.status_code == 200
    record = ended.get_json()
    assert record["status"] == "completed"
    assert record["outcome"] == "resolved"

    listing = client.get("/api/sessions")
    assert listing.status_code == 200
    items = listing.get_json()["sessions"]
    assert items[0]["session_id"] == sid
    assert "transcript" not in items[0]

    detail = client.get(f"/api/session/{sid}")
    assert detail.status_code == 200
    assert detail.get_json()["transcript"][0]["customer"] == "What is your return policy?"


def test_api_declined_consent_never_creates_a_record(client):
    response = client.post("/api/session/start", json={"consent": False})
    assert response.status_code == 200
    body = response.get_json()
    assert body["session_id"] is None
    assert body["consent"] is False
    listing = client.get("/api/sessions").get_json()["sessions"]
    assert listing == [], "declined consent persists nothing"


def test_api_start_with_no_body_defaults_to_no_consent(client):
    response = client.post("/api/session/start")
    assert response.status_code == 200
    assert response.get_json()["session_id"] is None


def test_api_rejects_bad_channel(client):
    _assert_json_error(
        client.post("/api/session/start", json={"consent": True, "channel": "sms"}),
        400, "invalid_request",
    )


def test_api_end_validation(client):
    _assert_json_error(client.post("/api/session/not-a-real-id/end", json={}),
                       400, "invalid_request")
    _assert_json_error(client.post(f"/api/session/{'f' * 32}/end", json={"turns": []}),
                       404, "not_found")
    sid = client.post("/api/session/start", json={"consent": True}).get_json()["session_id"]
    client.post(f"/api/session/{sid}/end", json={"turns": []})
    _assert_json_error(client.post(f"/api/session/{sid}/end", json={"turns": []}),
                       409, "already_ended")


def test_api_detail_404_and_invalid_id(client):
    _assert_json_error(client.get("/api/session/" + "e" * 32), 404, "not_found")
    _assert_json_error(client.get("/api/session/../../windows/win.ini"),
                       404, "not_found")  # no route match -> safe JSON 404


def test_api_session_end_survives_garbage_turns(client):
    sid = client.post("/api/session/start", json={"consent": True}).get_json()["session_id"]
    response = client.post(
        f"/api/session/{sid}/end",
        json={"turns": [None, "text", {"intent": "not_real"}]},
    )
    assert response.status_code == 200
    record = response.get_json()
    assert record["outcome"] == "no_conversation"  # nothing valid survived


def test_api_database_error_is_a_safe_json_500(client, app, tmp_path):
    # Point the app at a path inside a directory that cannot be a database.
    bogus_dir = tmp_path / "nope"
    bogus_dir.mkdir()
    (bogus_dir / "impostor").write_text("this is not a sqlite file")
    app.config["DB_PATH"] = str(bogus_dir / "impostor")
    response = client.get("/api/sessions")
    assert response.status_code == 500
    body = response.get_json()
    assert body["error"] == "storage_error"
    # Safe leak test: no path, no traceback wording in the customer answer.
    assert "Traceback" not in body["message"]
    assert str(bogus_dir) not in body["message"]
