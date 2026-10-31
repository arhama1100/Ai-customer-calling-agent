"""
tests/test_security.py
──────────────────────
Phase G3 — security hardening, request validation, error handling, rate
limiting and safe production/debug configuration.

Everything here is offline (Gemini stays off via the conftest autouse fixture)
and uses only the throwaway test database. We also RE-LOCK the deliberate
graceful-degradation contracts (malformed / empty / missing / non-string input
answer a friendly 200) so the new error handlers cannot silently change them.
"""

import importlib

import pytest

from app import routes


# ── G3.4 Safe configuration defaults (real Config) ──────────────────────────
# These reload app.config with dotenv neutralized and a clean environment so we
# assert the ACTUAL production defaults, independent of a developer's local .env.
def _reload_clean_config(monkeypatch):
    import dotenv
    from app import config as config_module

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    for key in (
        "FLASK_DEBUG", "FLASK_HOST", "FLASK_PORT",
        "MAX_CONTENT_LENGTH", "MAX_MESSAGE_LENGTH",
        "RATE_LIMIT_ENABLED", "RATE_LIMIT_MAX", "RATE_LIMIT_WINDOW_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    importlib.reload(config_module)
    return config_module


def test_config_defaults_are_production_safe(monkeypatch):
    config_module = _reload_clean_config(monkeypatch)
    cfg = config_module.Config
    try:
        # Debug must NOT be on by default in a production build.
        assert cfg.DEBUG is False
        # Loopback binding by default (never exposed on the LAN by accident).
        assert cfg.HOST == "127.0.0.1"
        # Hardening limits exist and are sane.
        assert cfg.MAX_CONTENT_LENGTH == 16 * 1024
        assert cfg.MAX_MESSAGE_LENGTH == 500
        assert cfg.RATE_LIMIT_ENABLED is True
        assert cfg.RATE_LIMIT_MAX == 30
        assert cfg.RATE_LIMIT_WINDOW_SECONDS == 60
    finally:
        importlib.reload(config_module)  # restore ambient defaults


def test_config_debug_can_be_enabled_for_local_dev(monkeypatch):
    config_module = _reload_clean_config(monkeypatch)
    monkeypatch.setenv("FLASK_DEBUG", "true")
    importlib.reload(config_module)
    try:
        assert config_module.Config.DEBUG is True
    finally:
        importlib.reload(config_module)


# ── G3.1 Request validation: input shape is never trusted blindly ───────────
@pytest.mark.parametrize("bad", [123, 12.5, ["a", "b"], {"k": "v"}, True, None])
def test_chat_non_string_message_is_handled_gracefully(client, bad):
    # A number/list/dict/bool under "message" is treated like an absent message,
    # answered honestly with the standard 200 contract - never a crash or 500.
    response = client.post("/api/chat", json={"message": bad})
    assert response.status_code == 200
    data = response.get_json()
    assert set(data.keys()) == {"intent", "reply", "source"}
    assert data["intent"] == "empty"
    assert data["source"] == "none"


def test_malformed_json_still_degrades_to_200_empty(client):
    # Re-lock the Phase B4 decision: a bad body -> friendly 200, not 400/500.
    response = client.post(
        "/api/chat", data="{this is not valid json", content_type="application/json")
    assert response.status_code == 200
    assert response.get_json()["intent"] == "empty"


def test_empty_and_missing_message_stay_200_empty(client):
    assert client.post("/api/chat", json={"message": ""}).get_json()["intent"] == "empty"
    assert client.post("/api/chat", json={}).get_json()["intent"] == "empty"


def test_chat_oversized_message_returns_413_json(app, client):
    app.config["MAX_MESSAGE_LENGTH"] = 20
    big = "a" * 100
    response = client.post("/api/chat", json={"message": big})
    assert response.status_code == 413
    data = response.get_json()
    assert data["error"] == "message_too_long"
    assert "message" in data
    # The oversized input must never be echoed back to the client.
    assert "aaaa" not in response.get_data(as_text=True)


def test_chat_message_at_the_limit_is_allowed(app, client):
    app.config["MAX_MESSAGE_LENGTH"] = 20
    response = client.post("/api/chat", json={"message": "a" * 20})
    assert response.status_code == 200  # exactly at the cap is fine


def test_valid_short_message_still_returns_200(app, client):
    # The length guard must not disturb the normal happy path.
    app.config["MAX_MESSAGE_LENGTH"] = 500
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 200
    assert set(response.get_json().keys()) == {"intent", "reply", "source"}


def test_chat_oversized_body_is_rejected_as_413_json(app, client):
    # MAX_CONTENT_LENGTH stops a huge body before the view runs; the error is
    # still a consistent JSON 413 (never an HTML page or a stack trace).
    app.config["MAX_CONTENT_LENGTH"] = 50
    response = client.post(
        "/api/chat", data="x" * 5000, content_type="application/json")
    assert response.status_code == 413
    assert response.get_json()["error"] == "payload_too_large"


# ── G3.2 Error handling: DB failure and any unexpected error are safe ───────
def test_orders_db_error_returns_safe_json(client, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("SECRET-DB-FILE-PATH")

    monkeypatch.setattr(routes, "find_order", _boom)
    response = client.get("/api/orders/ORD-1002")
    assert response.status_code == 500
    data = response.get_json()
    assert data["error"] == "storage_error"
    text = response.get_data(as_text=True)
    assert "SECRET-DB-FILE-PATH" not in text
    assert "Traceback" not in text


def test_uncaught_exception_returns_safe_json(app, client):
    # A route that raises outside any view guard must hit the global handler
    # and return a safe generic JSON 500 with no internals (app.debug is False
    # in TestConfig, which is exactly the production behaviour we want).
    @app.get("/api/_boom")
    def _boom():
        raise RuntimeError("SECRET-INTERNAL-DETAIL")

    response = client.get("/api/_boom")
    assert response.status_code == 500
    assert response.get_json()["error"] == "internal_error"
    text = response.get_data(as_text=True)
    assert "SECRET-INTERNAL-DETAIL" not in text
    assert "Traceback" not in text


def test_api_unknown_route_returns_json_404(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.is_json
    assert response.get_json()["error"] == "not_found"


def test_non_api_unknown_route_keeps_default_html_404(client):
    # The JSON error reshaping is scoped to /api only; the frontend's normal
    # HTML behaviour is preserved (no regression to the static single page).
    response = client.get("/totally-unknown-page")
    assert response.status_code == 404
    assert not response.is_json


# ── G3.3 Rate limiting on /api/chat and /api/orders ─────────────────────────
def test_rate_limit_off_by_default_in_tests(client):
    # TestConfig leaves the limiter off, so a burst is never throttled.
    for _ in range(40):
        assert client.post("/api/chat", json={"message": "hello"}).status_code == 200


def test_chat_rate_limits_after_threshold(app, client):
    app.config["RATE_LIMIT_ENABLED"] = True
    app.config["RATE_LIMIT_MAX"] = 3
    app.config["RATE_LIMIT_WINDOW_SECONDS"] = 60

    for _ in range(3):
        assert client.post("/api/chat", json={"message": "hello"}).status_code == 200

    limited = client.post("/api/chat", json={"message": "hello"})
    assert limited.status_code == 429
    assert limited.get_json()["error"] == "rate_limited"
    assert "Retry-After" in limited.headers
    assert int(limited.headers["Retry-After"]) >= 1


def test_orders_rate_limits_after_threshold(app, client):
    app.config["RATE_LIMIT_ENABLED"] = True
    app.config["RATE_LIMIT_MAX"] = 2
    app.config["RATE_LIMIT_WINDOW_SECONDS"] = 60

    assert client.get("/api/orders/ORD-1002").status_code == 200
    assert client.get("/api/orders/ORD-1002").status_code == 200
    assert client.get("/api/orders/ORD-1002").status_code == 429


def test_rate_limit_is_scoped_per_endpoint(app, client):
    # Exhausting the chat budget must not consume the orders budget.
    app.config["RATE_LIMIT_ENABLED"] = True
    app.config["RATE_LIMIT_MAX"] = 1
    app.config["RATE_LIMIT_WINDOW_SECONDS"] = 60

    assert client.post("/api/chat", json={"message": "hello"}).status_code == 200
    assert client.post("/api/chat", json={"message": "hello"}).status_code == 429
    # Different endpoint key -> still within its own budget.
    assert client.get("/api/orders/ORD-1002").status_code == 200
