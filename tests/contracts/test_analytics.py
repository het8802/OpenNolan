"""Contract tests for server.settings (atomic prefs) + server.analytics (opt-out-honored PostHog).

The load-bearing invariant: OPT-OUT IS HONORED AT INIT. When disabled, no PostHog client is ever
constructed — we assert the constructor is never called, not merely that events are dropped.

Analytics is hard-disabled under pytest, so tests that exercise the enabled path monkeypatch
`analytics._under_pytest` to lift that guard, then inject a fake `posthog` module.
"""

from __future__ import annotations

import sys
import types

import pytest

from server import analytics, settings


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # Route settings.json into a temp home so tests never touch the real repo/App-Support file.
    monkeypatch.setenv("OPENNOLAN_HOME", str(tmp_path))
    analytics.reset()
    yield
    analytics.reset()


def _inject_fake_posthog(monkeypatch):
    constructed: list[dict] = []

    class FakeClient:
        def __init__(self, **kwargs):
            constructed.append(kwargs)
            self.captures: list[dict] = []

        def capture(self, **kwargs):
            self.captures.append(kwargs)

        def capture_exception(self, exc, **kwargs):
            self.captures.append({"exc": exc, **kwargs})

        def shutdown(self):
            pass

    mod = types.ModuleType("posthog")
    mod.Posthog = FakeClient
    monkeypatch.setitem(sys.modules, "posthog", mod)
    return constructed


# ── settings ────────────────────────────────────────────────────────────────

def test_settings_defaults_when_no_file():
    assert settings.get("analytics_disabled") is False
    assert settings.read_all()["analytics_disabled"] is False


def test_settings_set_persists_and_is_atomic(tmp_path):
    settings.set_value("analytics_disabled", True)
    assert settings.get("analytics_disabled") is True
    # atomic writer must not leave temp files behind
    leftovers = list(tmp_path.glob(".settings.*"))
    assert leftovers == []


def test_settings_corrupt_file_falls_back_to_defaults(tmp_path):
    (tmp_path / "settings.json").write_text("{not json")
    assert settings.get("analytics_disabled") is False  # no crash, defaults


def test_device_id_is_stable_and_anonymous():
    a = settings.device_id()
    b = settings.device_id()
    assert a == b and a.startswith("dev-")


# ── analytics: opt-out honored AT INIT ────────────────────────────────────────

def test_disabled_never_constructs_client(monkeypatch):
    constructed = _inject_fake_posthog(monkeypatch)
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    settings.set_value("analytics_disabled", True)
    analytics.reset()

    assert analytics.is_enabled() is False
    analytics.capture("app_opened", {"os": "mac"})
    analytics.capture_exception(RuntimeError("x"))
    assert constructed == []  # NO client built while opted out — the whole point


def test_enabled_captures_with_device_id_and_scrub(monkeypatch):
    constructed = _inject_fake_posthog(monkeypatch)
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    settings.set_value("analytics_disabled", False)
    analytics.reset()

    assert analytics.is_enabled() is True
    analytics.capture("export_completed", {"path": "/Users/het/secret.mp4", "resolution": "1080x1920"})

    from posthog import Posthog  # the fake
    assert len(constructed) == 1
    # one client, one event, keyed by the anonymous device id, path scrubbed
    client = None
    # grab the singleton the module built
    client = analytics._get_client()
    assert client.captures[0]["distinct_id"] == settings.device_id()
    assert client.captures[0]["event"] == "export_completed"
    assert client.captures[0]["properties"]["path"] == "[path]"
    assert client.captures[0]["properties"]["resolution"] == "1080x1920"


def test_pytest_guard_disables_by_default():
    # Without lifting the guard, analytics is off even when not opted out (no test telemetry).
    settings.set_value("analytics_disabled", False)
    analytics.reset()
    assert analytics.is_enabled() is False


# ── analytics: scrub ──────────────────────────────────────────────────────────

def test_scrub_redacts_paths_secrets_and_drops_freetext():
    out = analytics._scrub({
        "output_path": "/Users/het/Movies/reel.mp4 done",
        "api_key": "sk-123",
        "prompt": "make me a viral reel about cats",
        "count": 3,
    })
    assert out["output_path"] == "[path] done"
    assert out["api_key"] == "[redacted]"
    assert "prompt" not in out and out["prompt_len"] == len("make me a viral reel about cats")
    assert out["count"] == 3


def test_scrub_empty_is_empty():
    assert analytics._scrub(None) == {}
    assert analytics._scrub({}) == {}
