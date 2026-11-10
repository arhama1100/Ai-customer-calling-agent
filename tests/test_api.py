"""
tests/test_api.py
─────────────────
First automated test: verifies the /api/health endpoint.

Run with:  .\\.venv\\Scripts\\python.exe -m pytest
"""

import pytest

from app import assistant
from app import routes
from app.faq import load_topics, get_answer_by_id


def test_health_returns_ok(client):
    """The health endpoint should respond 200 with {"status": "ok"}."""
    response = client.get("/api/health")

    # HTTP 200 means "success".
    assert response.status_code == 200

    # The body should be JSON with a status field equal to "ok".
    data = response.get_json()
    assert data is not None
    assert data["status"] == "ok"


# ── Frontend serving (Phase 4): the page and its assets must be reachable ──

def test_root_serves_frontend_page(client):
    response = client.get("/")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    # The page must reference the chat elements and scripts wired in app.js.
    assert "Nova AI" in page
    assert 'id="chat-form"' in page
    assert "/static/js/app.js" in page
    # Phase 5: voice input assets must be present (mic + language selector).
    assert 'id="mic-btn"' in page
    assert 'id="stt-lang"' in page
    assert "/static/js/stt.js" in page


def test_vercel_homepage_uses_tracked_html_and_keeps_asset_urls(monkeypatch):
    """Vercel's function serves tracked HTML; CDN serves generated assets."""
    from app import create_app

    monkeypatch.setenv("VERCEL", "1")
    app = create_app()
    client = app.test_client()

    response = client.get("/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Nova AI" in page
    assert 'href="/static/css/style.css"' in page
    for script in ("tts.js", "stt.js", "app.js", "call.js", "sessions.js", "nova.js"):
        assert f'src="/static/js/{script}"' in page

    # Static assets are intentionally served by Vercel's CDN, not Flask.
    assert client.get("/static/css/style.css").status_code == 404
    assert client.get("/static/js/app.js").status_code == 404

    # Flask API routing remains active under the Vercel runtime setting.
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.get_json()["status"] == "ok"


def test_static_assets_are_served(client):
    for path in [
        "/static/css/style.css",
        "/static/js/app.js",
        "/static/js/tts.js",
        "/static/js/stt.js",
    ]:
        response = client.get(path)
        assert response.status_code == 200, f"{path} should be served"


def test_stt_script_contract(client):
    """stt.js must keep the integration points app.js and index.html rely on.

    This is a cheap static check (no JS engine needed) that catches accidental
    breakage of the corrected voice flow: recognized text is shown for review
    via ChatApp.setDraft, results are read from event.resultIndex (so partials
    and the final are handled and never double-sent), both languages are wired,
    and the main failure modes are handled.
    """
    js = client.get("/static/js/stt.js").get_data(as_text=True)

    # Recognized speech is placed in the input for review before sending.
    assert "ChatApp.setDraft" in js
    # Correct partial/final handling (not a blind results[0] read).
    assert "event.resultIndex" in js
    assert "isFinal" in js
    # Configurable languages required by the owner (English + Urdu).
    assert "en-US" in js
    assert "ur-PK" in js
    # Fallbacks required by policy: denial, no-speech, network, unsupported.
    assert "not-allowed" in js
    assert "no-speech" in js
    assert "network" in js
    assert "webkitSpeechRecognition" in js
    # Phase B4: no debug logging that would expose speech transcripts.
    assert "console.log" not in js


def test_tts_control_contract(client):
    """The visible TTS controls must exist and be wired.

    Guards against accidentally removing the per-reply Speak button or the
    global Stop Speaking control, and keeps tts.js language-aware.
    """
    html = client.get("/").get_data(as_text=True)
    assert 'id="stop-speak"' in html  # global Stop Speaking control present

    appjs = client.get("/static/js/app.js").get_data(as_text=True)
    assert "Speak Response" in appjs  # visible per-reply speak control
    assert "TTS.speak(" in appjs      # clicking actually speaks the reply
    # Phase B4: auto-speak must honor the selected language (parity with the
    # per-reply button) and must validate the reply before rendering it.
    assert "TTS.speak(data.reply, getPreferredLang())" in appjs
    assert "typeof data.reply" in appjs
    # Phase B4: no debug logging of customer messages.
    assert "console.log" not in appjs
    assert "TTS.stop(" in appjs       # stop control cancels speech
    # The button toggles: it checks whether THIS reply is already speaking.
    assert "TTS.isSpeaking(" in appjs
    assert "Stop Speaking" in appjs   # state-aware label text

    ttsjs = client.get("/static/js/tts.js").get_data(as_text=True)
    assert "speechSynthesis" in ttsjs
    assert "SpeechSynthesisUtterance" in ttsjs
    assert "getVoices" in ttsjs       # English/Urdu voice selection
    # Reliable stop/toggle state (not just "always restart on click").
    assert "function stop" in ttsjs
    assert "cancel()" in ttsjs        # uses speechSynthesis.cancel()
    assert "onend" in ttsjs           # utterance end/error reset state
    assert "onerror" in ttsjs
    assert "pending" in ttsjs         # consults speechSynthesis.pending
    assert "function isSpeaking" in ttsjs
    assert "onChange" in ttsjs        # UI subscribes for label updates


# ── Phase 6 hardening: bad input and wrong methods must never crash the API ──

def test_health_rejects_post(client):
    # /api/health is GET-only; Flask should answer 405, not 500.
    assert client.post("/api/health").status_code == 405


def test_orders_rejects_post(client):
    assert client.post("/api/orders/ORD-1001").status_code == 405


def test_chat_rejects_get(client):
    # /api/chat consumes a message, so it is POST-only.
    assert client.get("/api/chat").status_code == 405


def test_chat_empty_message_is_handled(client):
    response = client.post("/api/chat", json={"message": ""})

    assert response.status_code == 200
    data = response.get_json()
    # Empty input is answered honestly, never with a fabricated reply.
    assert data["intent"] == "empty"
    assert data["source"] == "none"


def test_chat_missing_message_key_is_handled(client):
    # JSON without the "message" field must behave like an empty message.
    response = client.post("/api/chat", json={"not_message": "hello"})

    assert response.status_code == 200
    assert response.get_json()["intent"] == "empty"


def test_chat_invalid_json_body_does_not_crash(client):
    response = client.post(
        "/api/chat",
        data="{this is not valid json",
        content_type="application/json",
    )

    # The route uses get_json(silent=True), so malformed input degrades to
    # the empty-message path with a 200 — instead of a 400/500 crash.
    assert response.status_code == 200
    assert response.get_json()["intent"] == "empty"


def test_chat_non_dict_json_body_does_not_crash(client):
    response = client.post("/api/chat", json=["not", "a", "dict"])

    assert response.status_code == 200
    assert response.get_json()["intent"] == "empty"


def test_chat_speech_variation_resolves_order(client):
    # Spoken form without a hyphen must still return the DB answer, and the
    # response format (intent/reply/source keys) stays unchanged.
    response = client.post("/api/chat", json={"message": "where is order ord 1002"})

    assert response.status_code == 200
    data = response.get_json()
    assert set(data.keys()) == {"intent", "reply", "source"}
    assert data["intent"] == "order_status"
    assert data["source"] == "orders_db"
    assert "shipped" in data["reply"]


def test_chat_urdu_order_status_without_id_asks_for_id(client):
    response = client.post("/api/chat", json={"message": "میرا آرڈر کہاں ہے"})

    assert response.status_code == 200
    data = response.get_json()
    assert set(data.keys()) == {"intent", "reply", "source"}  # format kept
    assert data["intent"] == "order_status_missing_id"
    assert "order number" in data["reply"].lower()
    assert "not sure how to help" not in data["reply"].lower()


def test_chat_roman_urdu_order_status_without_id_asks_for_id(client):
    response = client.post("/api/chat", json={"message": "mera order kahan hai"})

    assert response.status_code == 200
    assert response.get_json()["intent"] == "order_status_missing_id"


def test_chat_urdu_order_status_with_id_returns_status(client):
    response = client.post("/api/chat", json={"message": "میرا آرڈر کہاں ہے ORD-1002"})

    data = response.get_json()
    assert data["intent"] == "order_status"
    assert data["source"] == "orders_db"
    assert "shipped" in data["reply"]


# ── Phase B2: Gemini-enabled chat (fully mocked; no live API calls) ──
def test_chat_gemini_policy_returns_sample(client, monkeypatch):
    # With Gemini on (mocked), a shipping policy question returns the SAMPLE
    # shipping answer from data/faq.json (translation mocked off -> English),
    # and the {intent, reply, source} format is unchanged.
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(
        assistant.gemini_module, "classify_message",
        lambda *a, **k: {"intent": "shipping", "language": "en"},
    )
    monkeypatch.setattr(
        assistant.gemini_module, "rephrase_to_language", lambda *a, **k: None)

    response = client.post("/api/chat", json={"message": "how long is delivery"})
    assert response.status_code == 200
    data = response.get_json()
    assert set(data.keys()) == {"intent", "reply", "source"}
    assert data["source"] == "gemini:policy_sample"
    assert data["reply"] == get_answer_by_id("shipping")


def test_chat_gemini_order_still_from_database(client, monkeypatch):
    # Even with Gemini enabled, an order id is answered from SQLite by the
    # reliable regex path, so classify_message is never needed.
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    calls = {"n": 0}

    def _spy(*a, **k):
        calls["n"] += 1
        return {"intent": "order_status", "language": "en"}

    monkeypatch.setattr(assistant.gemini_module, "classify_message", _spy)

    response = client.post("/api/chat", json={"message": "where is ORD-1002"})
    data = response.get_json()
    assert data["source"] == "orders_db"
    assert "shipped" in data["reply"]
    assert calls["n"] == 0


# ════════════════════════════════════════════════════════════════════════════
# Phase B4 Tier A — end-to-end contract, order accuracy, FAQ safety matrix,
# multilingual coverage and a security-leak check. Gemini is always MOCKED.
# ════════════════════════════════════════════════════════════════════════════
def _assert_chat_contract(data):
    """Every /api/chat reply keeps the exact {intent, reply, source} shape with
    a non-empty string reply the frontend can render safely."""
    assert set(data.keys()) == {"intent", "reply", "source"}
    assert isinstance(data["reply"], str) and data["reply"].strip()


def _gemini_off(monkeypatch):
    monkeypatch.delenv("GEMINI_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


# ── End-to-end request -> response contract (order / FAQ / greeting) ──
def test_e2e_order_contract(client):
    r = client.post("/api/chat", json={"message": "where is ORD-1002"})
    assert r.status_code == 200
    data = r.get_json()
    _assert_chat_contract(data)
    assert data["intent"] == "order_status"
    assert data["source"] == "orders_db"
    assert "shipped" in data["reply"]


def test_e2e_policy_faq_contract(client, monkeypatch):
    _gemini_off(monkeypatch)
    r = client.post("/api/chat", json={"message": "what is your return policy"})
    assert r.status_code == 200
    data = r.get_json()
    _assert_chat_contract(data)
    assert data["source"] == "faq_kb:returns:sample"
    assert data["reply"] == get_answer_by_id("returns")


def test_e2e_greeting_contract(client, monkeypatch):
    _gemini_off(monkeypatch)
    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200
    data = r.get_json()
    _assert_chat_contract(data)
    assert data["intent"] == "greeting"
    assert data["source"] == "fixed"


# ── Order accuracy across the API: accepted formats + invalid lengths ──
ORDER_ID_FORMATS = ["ord1002", "ORD 1002", "order ord 1002", "ORD-1002", "ord-1002"]


@pytest.mark.parametrize("fmt", ORDER_ID_FORMATS)
def test_chat_accepts_order_id_formats(fmt, client):
    data = client.post("/api/chat", json={"message": f"where is {fmt}"}).get_json()
    _assert_chat_contract(data)
    assert data["intent"] == "order_status"
    assert data["source"] == "orders_db"
    assert data["reply"].startswith("Order ORD-1002")
    assert "shipped" in data["reply"]


@pytest.mark.parametrize(
    "msg", ["status of ORD-100", "status of ORD-12", "status of ORD-10023"],
)
def test_chat_rejects_bad_length_order_ids(msg, client, monkeypatch):
    # Fewer/more than 4 digits must NOT be treated as an order lookup.
    _gemini_off(monkeypatch)
    data = client.post("/api/chat", json={"message": msg}).get_json()
    assert data["intent"] != "order_status"
    assert data["source"] != "orders_db"


def test_chat_multiple_ids_uses_first(client):
    # Documents existing behavior: ORDER_ID_RE.search returns the FIRST id.
    data = client.post(
        "/api/chat", json={"message": "compare ORD-1001 and ORD-1002"}
    ).get_json()
    assert data["intent"] == "order_status"
    assert data["source"] == "orders_db"
    assert data["reply"].startswith("Order ORD-1001")  # first match, not merged


# ── Multilingual through the API (English / Urdu / Roman / mixed) ──
def test_chat_mixed_language_with_id_uses_db(client):
    data = client.post(
        "/api/chat", json={"message": "mera order ORD-1002 kahan hai"}
    ).get_json()
    assert data["intent"] == "order_status"
    assert data["source"] == "orders_db"
    assert "shipped" in data["reply"]


def test_chat_mixed_language_without_id_asks(client, monkeypatch):
    _gemini_off(monkeypatch)
    data = client.post(
        "/api/chat", json={"message": "where is mera order ka status"}
    ).get_json()
    assert data["intent"] == "order_status_missing_id"
    assert "order number" in data["reply"].lower()
    assert "shipped" not in data["reply"]  # never leaks a status


# ── FAQ safety matrix: five policy topics x three failure modes ──
POLICY_TOPICS = {
    "shipping": "how long does delivery usually take",
    "returns": "can I get a refund on a return",
    "cancellation": "can I cancel my order",
    "changes": "I need to change the wrong address on my order",
    "payment": "how do I pay with a card",
}


@pytest.mark.parametrize("topic", list(POLICY_TOPICS))
@pytest.mark.parametrize("mode", ["disabled", "no_key", "classify_fails"])
def test_faq_sample_consistency_matrix(client, monkeypatch, topic, mode):
    # G2: in EVERY mode a policy question returns that topic's SAMPLE answer
    # from data/faq.json (disabled / no-key / classify-fail all fall through to
    # the deterministic FAQ branch, so the sample answer is stable and grounded).
    if mode == "disabled":
        _gemini_off(monkeypatch)
    elif mode == "no_key":
        monkeypatch.setenv("GEMINI_ENABLED", "true")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    else:  # enabled with a key, but classification fails (timeout / API error)
        monkeypatch.setenv("GEMINI_ENABLED", "true")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
        monkeypatch.setattr(
            assistant.gemini_module, "classify_message", lambda *a, **k: None)

    data = client.post(
        "/api/chat", json={"message": POLICY_TOPICS[topic]}).get_json()
    _assert_chat_contract(data)
    assert data["source"] == f"faq_kb:{topic}:sample"
    assert data["reply"] == get_answer_by_id(topic)


# ── Security: no API key / secret ever reaches a served file or API body ──
def test_no_secret_in_api_response_or_frontend(client, monkeypatch):
    sentinel = "sk-SENTINEL-SECRET-DO-NOT-LEAK"
    monkeypatch.setenv("GEMINI_API_KEY", sentinel)  # if echoed anywhere, we'd see it
    bodies = [
        client.get("/").get_data(as_text=True),
        client.get("/static/js/app.js").get_data(as_text=True),
        client.get("/static/js/stt.js").get_data(as_text=True),
        client.get("/static/js/tts.js").get_data(as_text=True),
        client.get("/static/css/style.css").get_data(as_text=True),
    ]
    for msg in ["where is ORD-1002", "what is your return policy", "hello"]:
        r = client.post("/api/chat", json={"message": msg})
        assert set(r.get_json().keys()) == {"intent", "reply", "source"}
        bodies.append(r.get_data(as_text=True))
    for text in bodies:
        assert sentinel not in text


# ════════════════════════════════════════════════════════════════════════════
# Phase B4 Tier C — a raised internal error must degrade to safe JSON.
# ════════════════════════════════════════════════════════════════════════════
def test_chat_internal_error_returns_safe_json(client, monkeypatch):
    # If the assistant unexpectedly raises, /api/chat must still return the
    # {intent, reply, source} JSON with an honest fallback — never a 500 HTML
    # page, traceback, or internal detail.
    def _boom(*a, **k):
        raise RuntimeError("SECRET-INTERNAL-DETAIL")

    monkeypatch.setattr(routes, "respond", _boom)
    r = client.post("/api/chat", json={"message": "anything"})
    assert r.status_code == 200
    data = r.get_json()
    _assert_chat_contract(data)
    assert data["intent"] == "fallback"
    assert data["source"] == "none"
    text = r.get_data(as_text=True)
    assert "SECRET-INTERNAL-DETAIL" not in text
    assert "Traceback" not in text


# ── Task 2: human_agent_request over the real endpoint ──
def test_chat_human_agent_request_contract(client):
    # /api/chat stays backward compatible: same {intent, reply, source} shape,
    # the new intent is exposed through it, and the reply is the honest fixed
    # handoff (deterministic - no model involvement).
    r = client.post("/api/chat", json={"message": "I want to speak to a human"})
    assert r.status_code == 200
    data = r.get_json()
    _assert_chat_contract(data)
    assert data["intent"] == "human_agent_request"
    assert data["source"] == "fixed"
    assert "automated assistant" in data["reply"].lower()
