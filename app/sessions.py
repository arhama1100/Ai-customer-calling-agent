"""
app/sessions.py
───────────────
SIMULATED call-session records: start/end lifecycle, stored transcript,
detected intents, honest outcome, and a concise deterministic summary.

What this is and is NOT:
- This is a LOCAL demo record of a browser-simulated call. No real telephone
  call takes place and no real person is ever contacted or notified.
- The summary is built ONLY from what actually happened in the turns
  (topics = the intents the assistant really returned). It never invents
  customer information and never claims a problem was resolved unless every
  customer message received a substantive answer from a local source.

Consent policy (privacy requirement):
- A session row is created ONLY when the customer explicitly consents.
  Without consent nothing is stored at all - the call/chat still works,
  there is simply no record. That is the honest, minimal-data behavior.
- The stored transcript is kept in the same local SQLite file as the demo
  orders; the list endpoint deliberately omits it (staff dashboard shows
  date, time, intents, outcome, summary - not the raw words).

Schema/compatibility notes:
- Reuses app/database.py's connection helper (row_factory, PRAGMA).
- One new table, `call_sessions`, created with CREATE TABLE IF NOT EXISTS
  on every operation (ensure_schema), so an existing shop.db upgrades
  backward-compatibly with no manual migration step.
- All writes are parameterized SQL (? placeholders) - never string
  concatenation - so transcript text can never inject SQL.
"""

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from .database import get_connection

# A session id is a uuid4 hex (32 lowercase hex chars). Anything else coming
# back from a client is rejected BEFORE it reaches a SQL parameter - cheap
# validation, no enumeration of odd ids, and no injection surface at all.
SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")

CHANNELS = ("voice", "text")

# The intents respond() can actually return (see app/assistant.py) plus
# "other" for anything unexpected from a client. Any other label arriving
# from a caller is sanitized to "other", never trusted.
TURN_INTENTS = {
    "order_status",
    "order_status_missing_id",
    "order_status_unclear",
    "business_hours",
    "faq",
    "greeting",
    "farewell",
    "gratitude",
    "human_agent_request",
    "ambiguous",
    "empty",
    "fallback",
    "other",
}

# Intents that DID answer the question from a real local source.
SUBSTANTIVE_INTENTS = {"order_status", "business_hours", "faq"}

# Intents that mean "this message was not substantively answered" - an
# honest "I'm not sure", a missing/unclear order number, a clarification.
UNANSWERED_INTENTS = {
    "fallback",
    "ambiguous",
    "empty",
    "other",
    "order_status_missing_id",
    "order_status_unclear",
}

OUTCOMES = ("resolved", "unresolved", "escalated", "no_conversation")

# Caps that keep a bad/greedy client from bloating one row. Exceeding the
# turn cap truncates (and the summary says so) instead of erroring - the
# customer's call data should not be lost over size.
MAX_TURNS = 200
MAX_TEXT_CHARS = 2000

SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS call_sessions (
    session_id      TEXT PRIMARY KEY,
    channel         TEXT NOT NULL CHECK (channel IN ('voice', 'text')),
    consent         INTEGER NOT NULL CHECK (consent IN (0, 1)),
    status          TEXT NOT NULL CHECK (status IN ('active', 'completed')),
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    transcript      TEXT,
    intents         TEXT,
    primary_intent  TEXT,
    outcome         TEXT CHECK (outcome IN
                        ('resolved', 'unresolved', 'escalated', 'no_conversation')),
    human_followup  INTEGER NOT NULL CHECK (human_followup IN (0, 1)) DEFAULT 0,
    summary         TEXT
);
"""


class SessionNotFoundError(Exception):
    """The given id is not a stored session (route answers with 404)."""


class SessionAlreadyEndedError(Exception):
    """Ending a second time must not silently overwrite (route answers 409)."""


def is_valid_session_id(value):
    return bool(SESSION_ID_RE.match(value or ""))


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_text(value):
    """Coerce to a trimmed string, hard-capped in length. Never raises."""
    if value is None:
        return ""
    text = str(value).strip()
    return text[:MAX_TEXT_CHARS]


def sanitize_turns(raw_turns):
    """Validate the client-sent turn list into a safe, canonical form.

    Accepted shape: a list of objects {"customer": str, "reply": str,
    "intent": str}. Unknown intents become "other"; entries that are not
    objects or carry no text at all are dropped. Returns (turns, truncated)
    where `truncated` means more than MAX_TURNS valid turns arrived.
    """
    if not isinstance(raw_turns, list):
        return [], False
    turns = []
    truncated = False
    for item in raw_turns:
        if not isinstance(item, dict):
            continue
        customer = _clean_text(item.get("customer"))
        reply = _clean_text(item.get("reply"))
        intent = str(item.get("intent") or "").strip().lower()
        if intent not in TURN_INTENTS:
            intent = "other"
        if not customer and not reply:
            continue  # nothing happened in this "turn" - drop it
        if len(turns) >= MAX_TURNS:
            truncated = True
            break
        turns.append({"customer": customer, "reply": reply, "intent": intent})
    return turns, truncated


def derive_outcome(turns):
    """Honest outcome classification - deterministic, no guessing.

    escalated      a human agent was requested (needs staff follow-up)
    unresolved     at least one customer message was NOT substantively answered
    resolved       at least one substantive answer, nothing left unanswered
    no_conversation only pleasantries (or literally nothing) was exchanged
    """
    if not turns:
        return "no_conversation"
    intents = [t["intent"] for t in turns]
    if "human_agent_request" in intents:
        return "escalated"
    if any(i in UNANSWERED_INTENTS for i in intents):
        return "unresolved"
    if any(i in SUBSTANTIVE_INTENTS for i in intents):
        return "resolved"
    return "no_conversation"


def primary_intent(turns):
    """The most frequent intent; pleasantries never win over business topics."""
    if not turns:
        return None
    counts = {}
    for t in turns:
        counts[t["intent"]] = counts.get(t["intent"], 0) + 1
    social = {"greeting", "farewell", "gratitude"}
    business = {i: c for i, c in counts.items() if i not in social}
    pool = business or counts
    return max(sorted(pool), key=lambda i: pool[i])


def build_summary(turns, outcome, truncated, started_at=None, ended_at=None):
    """A concise, honest summary assembled ONLY from what actually happened.

    It reports the topics (real detected intents), the outcome in plain
    words, and whether staff follow-up is required - and it never claims a
    human was notified or a problem was fixed when it was not.
    """
    customer_msgs = sum(1 for t in turns if t["customer"])
    parts = []
    if not turns:
        parts.append(
            "Simulated call with no recorded exchange (empty session)."
        )
    else:
        parts.append(
            f"Simulated call: {len(turns)} turn(s), "
            f"{customer_msgs} customer message(s)."
        )
        topics = []
        for t in turns:
            if t["intent"] not in topics:
                topics.append(t["intent"])
        parts.append("Topics detected: " + ", ".join(topics) + ".")
    if truncated:
        parts.append(
            f"Transcript truncated to the first {MAX_TURNS} turns."
        )
    duration = _duration_seconds(started_at, ended_at)
    if duration is not None and turns:
        parts.append(f"Length: {duration} second(s).")
    outcome_line = {
        "resolved": (
            "Outcome: every customer message received a substantive answer "
            "from the local order data or sample FAQ."
        ),
        "unresolved": (
            "Outcome: at least one message could not be answered "
            "substantively - treat this session as NOT resolved."
        ),
        "escalated": (
            "Outcome: the customer asked for a human agent. REQUIRES STAFF "
            "FOLLOW-UP. Note: no real agent has been notified by this demo."
        ),
        "no_conversation": (
            "Outcome: no substantive exchange took place (no business "
            "question was recorded as answered or unanswered)."
        ),
    }[outcome]
    parts.append(outcome_line)
    if any(t["intent"] == "human_agent_request" for t in turns):
        parts.append(
            "Human follow-up requested: yes - marked for staff review only; "
            "this demo does not contact or notify any person."
        )
    return " ".join(parts)


def _duration_seconds(started_at, ended_at):
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
        return max(0, int((end - start).total_seconds()))
    except (TypeError, ValueError):
        return None


def ensure_schema(db_path=None):
    """Backward-compatible migration: create call_sessions if it is missing.

    Safe to call on every operation (CREATE TABLE IF NOT EXISTS). The
    existing `orders` table is never touched, and no data is deleted.
    """
    conn = get_connection(db_path)
    try:
        conn.execute(SESSIONS_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row, include_transcript=True):
    """Convert a sqlite3.Row into a JSON-safe dict.

    The raw transcript is EXPENSIVE privacy-wise, so the dashboard list
    asks for include_transcript=False and gets metadata + summary only.
    """
    data = {
        "session_id": row["session_id"],
        "channel": row["channel"],
        "consent": bool(row["consent"]),
        "status": row["status"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "primary_intent": row["primary_intent"],
        "outcome": row["outcome"],
        "human_followup": bool(row["human_followup"]),
        "summary": row["summary"],
    }
    if include_transcript:
        try:
            data["transcript"] = json.loads(row["transcript"] or "[]")
        except (TypeError, ValueError):
            data["transcript"] = []
        try:
            data["intents"] = json.loads(row["intents"] or "[]")
        except (TypeError, ValueError):
            data["intents"] = []
    return data


def start_session(db_path=None, channel="voice", consent=False):
    """Open a consented simulated-call session record.

    WITHOUT consent nothing is stored: we return session_id None and the
    caller keeps working with no persistence (the honest, minimal-data
    path). WITH consent a row is written with status 'active'.
    """
    ensure_schema(db_path)
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}")
    if not consent:
        return {
            "session_id": None,
            "channel": channel,
            "consent": False,
            "status": "not_recorded",
            "started_at": None,
            "notice": (
                "Recording consent was not given, so nothing from this "
                "simulated call is stored. The call works exactly the same."
            ),
        }
    session_id = uuid.uuid4().hex
    started_at = _utc_now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO call_sessions
                (session_id, channel, consent, status, started_at)
            VALUES (?, ?, 1, 'active', ?)
            """,
            (session_id, channel, started_at),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "session_id": session_id,
        "channel": channel,
        "consent": True,
        "status": "active",
        "started_at": started_at,
        "notice": (
            "Recording with consent: this simulated call's transcript will "
            "be stored in the demo's local database only. Nova AI is an "
            "automated assistant; no real person is contacted."
        ),
    }


def end_session(db_path=None, session_id=None, turns=None):
    """Close a session: store the sanitized transcript, intents, outcome and
    summary, and flip status to 'completed'. Raises SessionNotFoundError /
    SessionAlreadyEndedError for the routes to map to 404 / 409."""
    ensure_schema(db_path)
    if not is_valid_session_id(session_id):
        raise ValueError("session_id must be a 32-character hex id")
    clean, truncated = sanitize_turns(turns)
    outcome = derive_outcome(clean)
    prim = primary_intent(clean)
    ended_at = _utc_now_iso()

    row = None
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "SELECT * FROM call_sessions WHERE session_id = ?", (session_id,)
        )
        row = cur.fetchone()
        if row is None:
            raise SessionNotFoundError(session_id)
        if row["status"] == "completed":
            raise SessionAlreadyEndedError(session_id)
        summary = build_summary(
            clean, outcome, truncated, row["started_at"], ended_at
        )
        intents_list = []
        for t in clean:
            if t["intent"] not in intents_list:
                intents_list.append(t["intent"])
        conn.execute(
            """
            UPDATE call_sessions
               SET status = 'completed',
                   ended_at = ?,
                   transcript = ?,
                   intents = ?,
                   primary_intent = ?,
                   outcome = ?,
                   human_followup = ?,
                   summary = ?
             WHERE session_id = ? AND status = 'active'
            """,
            (
                ended_at,
                json.dumps(clean, ensure_ascii=False),
                json.dumps(intents_list, ensure_ascii=False),
                prim,
                outcome,
                1 if "human_agent_request" in intents_list else 0,
                summary,
                session_id,
            ),
        )
        conn.commit()
        cur = conn.execute(
            "SELECT * FROM call_sessions WHERE session_id = ?", (session_id,)
        )
        row = cur.fetchone()
    except sqlite3.Error:
        raise  # let the route layer map it to a safe 500
    finally:
        conn.close()
    return _row_to_dict(row)


def get_session(db_path=None, session_id=None, include_transcript=True):
    """Fetch one stored session (full record with transcript by default)."""
    ensure_schema(db_path)
    if not is_valid_session_id(session_id):
        raise ValueError("session_id must be a 32-character hex id")
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "SELECT * FROM call_sessions WHERE session_id = ?", (session_id,)
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise SessionNotFoundError(session_id)
    return _row_to_dict(row, include_transcript=include_transcript)


def list_sessions(db_path=None, limit=20):
    """Most recent sessions for the staff history view.

    Deliberately EXCLUDES the raw transcript (privacy: the dashboard needs
    date, time, intents, outcome and summary only). Newest first.
    """
    ensure_schema(db_path)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 100))
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            SELECT session_id, channel, consent, status, started_at, ended_at,
                   primary_intent, outcome, human_followup, summary
              FROM call_sessions
             ORDER BY started_at DESC, rowid DESC
             LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r, include_transcript=False) for r in rows]
