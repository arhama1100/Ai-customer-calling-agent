"""
tests/test_gemini.py
────────────────────
Tests for the Phase B1 Gemini client wrapper (app/gemini.py).

These NEVER hit the network:
- "disabled / missing key" cases short-circuit before any client is built.
- Higher-level cases monkeypatch ``generate_text`` (so the SDK is not touched).
- The cases that exercise ``generate_text`` itself monkeypatch ``_get_client``
  with a fake client object.

They also assert the safety guarantees: feature OFF by default, a rogue intent
can never leak through, malformed/failed responses degrade to ``None`` (so the
caller can fall back), and the API key is never revealed in any return value.
"""

import json

from app import gemini


# ── helpers ──────────────────────────────────────────────────────────────────
def _enable(monkeypatch, key="test-key-dummy"):
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", key)


def _disable(monkeypatch):
    monkeypatch.delenv("GEMINI_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


class _FakeResp:
    text = ""


class _FakeModels:
    def __init__(self, text=None, exc=None):
        self._text = text
        self._exc = exc

    def generate_content(self, model=None, contents=None, config=None):
        if self._exc is not None:
            raise self._exc
        resp = _FakeResp()
        resp.text = self._text
        return resp


class _FakeClient:
    def __init__(self, text=None, exc=None):
        self.models = _FakeModels(text, exc)


# ── enablement / default-OFF ─────────────────────────────────────────────────
def test_is_enabled_requires_flag_and_key(monkeypatch):
    _disable(monkeypatch)
    assert gemini.is_enabled() is False                 # nothing set -> OFF
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert gemini.is_enabled() is False                 # flag but no key -> OFF
    monkeypatch.setenv("GEMINI_API_KEY", "present")
    assert gemini.is_enabled() is True                  # flag + key -> ON
    monkeypatch.setenv("GEMINI_ENABLED", "false")
    assert gemini.is_enabled() is False                 # key but flag off -> OFF


def test_disabled_makes_no_client_call(monkeypatch):
    _disable(monkeypatch)

    def _tripwire():
        raise AssertionError("client must NOT be built while disabled")

    monkeypatch.setattr(gemini, "_get_client", _tripwire)
    assert gemini.generate_text("hi") is None
    assert gemini.classify_message("mere order kahan hai") is None
    assert gemini.rephrase_to_language("Your order is shipped.", "ur") is None


def test_missing_key_makes_no_call(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def _tripwire():
        raise AssertionError("client must NOT be built without a key")

    monkeypatch.setattr(gemini, "_get_client", _tripwire)
    assert gemini.generate_text("where is my parcel?") is None
    assert gemini.classify_message("where is my parcel?") is None


# ── non-secret configuration defaults ────────────────────────────────────────
def test_model_name_default_and_override(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert gemini._model_name() == "gemini-flash-latest"
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    assert gemini._model_name() == "gemini-2.5-flash"


def test_timeout_default_and_override(monkeypatch):
    monkeypatch.delenv("GEMINI_TIMEOUT", raising=False)
    assert gemini._timeout_seconds() == 10.0
    monkeypatch.setenv("GEMINI_TIMEOUT", "3.5")
    assert gemini._timeout_seconds() == 3.5
    monkeypatch.setenv("GEMINI_TIMEOUT", "not-a-number")
    assert gemini._timeout_seconds() == 10.0            # falls back safely
    monkeypatch.setenv("GEMINI_TIMEOUT", "-1")
    assert gemini._timeout_seconds() == 10.0            # rejects non-positive


# ── generate_text with a fake client (validates the body, no network) ────────
def test_generate_text_success_with_fake_client(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(gemini, "_get_client", lambda: _FakeClient(text="hello world"))
    assert gemini.generate_text("ping") == "hello world"


def test_generate_text_api_failure_returns_none(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(gemini, "_get_client", lambda: _FakeClient(exc=RuntimeError("boom")))
    assert gemini.generate_text("ping") is None


def test_generate_text_empty_response_returns_none(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(gemini, "_get_client", lambda: _FakeClient(text=""))
    assert gemini.generate_text("ping") is None


def test_generate_text_timeout_returns_none(monkeypatch):
    # Phase B4: a timeout surfaces as an exception from the SDK; generate_text
    # must swallow it and return None so the caller falls back, and the failure
    # must propagate cleanly through classify_message too.
    _enable(monkeypatch)
    monkeypatch.setattr(
        gemini, "_get_client",
        lambda: _FakeClient(exc=TimeoutError("request timed out")),
    )
    assert gemini.generate_text("ping") is None
    assert gemini.classify_message("where is my parcel") is None
    assert gemini.rephrase_to_language("Your order is shipped.", "ur") is None


# ── classify_message (grounding + validation) ────────────────────────────────
def test_classify_success_normalizes(monkeypatch):
    _enable(monkeypatch)
    payload = {
        "intent": "ORDER_STATUS",
        "language": "ROMAN-URDU",
        "order_id": "ORD-1002",
        "needs_clarification": False,
    }
    monkeypatch.setattr(gemini, "generate_text", lambda *a, **k: json.dumps(payload))
    out = gemini.classify_message("Mera order ORD-1002 kahan hai")
    assert out == {
        "intent": "order_status",
        "language": "roman-urdu",
        "order_id": "ORD-1002",
        "needs_clarification": False,
    }


def test_classify_english_and_urdu_examples(monkeypatch):
    _enable(monkeypatch)
    for msg, intent, lang in [
        ("Where is my parcel?", "order_status", "en"),
        ("میرا آرڈر کہاں تک پہنچا؟", "order_status", "ur"),
        ("Mujhe refund chahiye.", "returns", "roman-urdu"),
    ]:
        canned = json.dumps({"intent": intent, "language": lang})
        monkeypatch.setattr(gemini, "generate_text", lambda *a, _c=canned, **k: _c)
        out = gemini.classify_message(msg)
        assert out["intent"] == intent
        assert out["language"] == lang


def test_classify_unknown_intent_becomes_other(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        gemini, "generate_text",
        lambda *a, **k: json.dumps({"intent": "launch_missiles", "language": "fr"}),
    )
    out = gemini.classify_message("hi")
    assert out["intent"] == "other"          # rogue intent is rejected
    assert out["language"] == "other"        # unknown language normalized


def test_classify_invalid_json_returns_none(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(gemini, "generate_text", lambda *a, **k: "<html>not json</html>")
    assert gemini.classify_message("hi") is None


def test_classify_non_object_json_returns_none(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(gemini, "generate_text", lambda *a, **k: json.dumps(["a", "b"]))
    assert gemini.classify_message("hi") is None


def test_classify_api_failure_returns_none(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(gemini, "generate_text", lambda *a, **k: None)
    assert gemini.classify_message("hi") is None


def test_classify_empty_message_does_not_call_model(monkeypatch):
    _enable(monkeypatch)
    calls = {"n": 0}

    def _spy(*a, **k):
        calls["n"] += 1
        return "{}"

    monkeypatch.setattr(gemini, "generate_text", _spy)
    assert gemini.classify_message("   ") is None
    assert calls["n"] == 0                    # never spends a call on blank input


# ── rephrase_to_language (only re-words approved text) ───────────────────────
def test_rephrase_success_mocked(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(
        gemini, "generate_text",
        lambda *a, **k: "Aap ka order shipping ho chuka hai.",
    )
    out = gemini.rephrase_to_language("Your order is shipped.", "roman-urdu")
    assert out == "Aap ka order shipping ho chuka hai."


def test_rephrase_failure_returns_none(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(gemini, "generate_text", lambda *a, **k: None)
    assert gemini.rephrase_to_language("Your order is shipped.", "ur") is None


def test_rephrase_empty_text_returns_none(monkeypatch):
    _enable(monkeypatch)
    assert gemini.rephrase_to_language("", "en") is None


# ── secret hygiene ───────────────────────────────────────────────────────────
def test_api_key_is_never_revealed(monkeypatch):
    _enable(monkeypatch, key="SECRET-KEY-DO-NOT-LEAK")
    monkeypatch.setattr(gemini, "_get_client", lambda: _FakeClient(text="ok"))

    # is_enabled only exposes a boolean, not the key.
    assert "SECRET-KEY-DO-NOT-LEAK" not in str(gemini.is_enabled())
    # A successful return is just the model text; the key never appears.
    result = gemini.generate_text("hi")
    assert result == "ok"
    assert "SECRET-KEY-DO-NOT-LEAK" not in str(result)
