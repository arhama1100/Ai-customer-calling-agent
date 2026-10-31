"""
tests/test_config_env.py
────────────────────────
Phase B3 — verify the ACTUAL configuration defaults and environment wiring for
the optional Gemini layer.

These are behavioural tests, NOT source-text scans: they reload the real
app.config.Config with a controlled environment and drive the runtime gating in
app.gemini. They prove:
  * GEMINI_ENABLED defaults to False (feature OFF by default),
  * GEMINI_MODEL / GEMINI_TIMEOUT defaults, env overrides, and a bad value,
  * the app factory wires those defaults,
  * the wrapper only activates with BOTH the flag on and a key present,
  * disabled mode makes no call and needs no key.
"""

import importlib

import pytest
import dotenv

from app import config as config_module
from app import gemini as gemini_module


GEMINI_ENV = ("GEMINI_ENABLED", "GEMINI_MODEL", "GEMINI_TIMEOUT", "GEMINI_API_KEY")


def _clear_gemini_env(monkeypatch):
    for key in GEMINI_ENV:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch):
    """Keep these tests independent of a developer's local .env.

    Every test in this module drives ``app.config`` through its OWN
    ``os.environ`` (via ``monkeypatch``). But ``app/config.py`` calls
    ``load_dotenv(BASE_DIR/'.env')`` each time it is (re)imported, so a real
    ``.env`` that sets ``GEMINI_ENABLED=true`` would silently repopulate the
    environment during ``importlib.reload`` and break the "defaults are off"
    assertions. We neutralize dotenv loading FOR THESE TESTS ONLY - the
    application's real dotenv behaviour is never changed here. Config still
    reads and applies ``os.environ`` exactly as it does in production.
    """
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    yield


@pytest.fixture(autouse=True)
def _restore_config_after_each_test():
    """Reload config after every test so the module-level Config returns to the
    clean ambient defaults (the developer environment sets no Gemini vars)."""
    yield
    importlib.reload(config_module)


def test_config_defaults_with_clean_environment(monkeypatch):
    _clear_gemini_env(monkeypatch)
    importlib.reload(config_module)
    cfg = config_module.Config
    assert cfg.GEMINI_ENABLED is False
    assert cfg.GEMINI_MODEL == "gemini-flash-latest"
    assert cfg.GEMINI_TIMEOUT == 10.0


def test_config_reads_overrides_from_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_TIMEOUT", "3.5")
    importlib.reload(config_module)
    cfg = config_module.Config
    assert cfg.GEMINI_ENABLED is True
    assert cfg.GEMINI_MODEL == "gemini-2.5-flash"
    assert cfg.GEMINI_TIMEOUT == 3.5


def test_config_rejects_invalid_timeout(monkeypatch):
    # A non-numeric timeout must fall back to the default, never crash startup.
    _clear_gemini_env(monkeypatch)
    monkeypatch.setenv("GEMINI_TIMEOUT", "not-a-number")
    importlib.reload(config_module)
    assert config_module.Config.GEMINI_TIMEOUT == 10.0


def test_app_factory_defaults_gemini_off(monkeypatch):
    _clear_gemini_env(monkeypatch)
    importlib.reload(config_module)
    from app import create_app
    app = create_app(config_module.Config)
    assert app.config["GEMINI_ENABLED"] is False
    assert app.config["GEMINI_MODEL"] == "gemini-flash-latest"
    assert app.config["GEMINI_TIMEOUT"] == 10.0


def test_gemini_off_by_default_never_builds_client(monkeypatch):
    # Real runtime behaviour: flag off => disabled; no network call; no key req.
    _clear_gemini_env(monkeypatch)
    assert gemini_module.is_enabled() is False
    assert gemini_module.generate_text("hello") is None
    assert gemini_module.classify_message("mere order kahan hai") is None


def test_gemini_requires_both_flag_and_key(monkeypatch):
    # Enabled flag WITHOUT a key must NOT enable the feature (never assume a key).
    _clear_gemini_env(monkeypatch)
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    assert gemini_module.is_enabled() is False
    # Flag + a dummy key -> enabled. (No call is made here; we only check state.)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    assert gemini_module.is_enabled() is True
