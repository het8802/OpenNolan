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


# ── analytics: internal-machine marker + env props (filter dev/own use out) ──────

def test_is_internal_via_env_var(monkeypatch, tmp_path):
    monkeypatch.setattr(analytics.Path, "home", lambda: tmp_path)  # no sentinel in this dir
    monkeypatch.delenv("OPENNOLAN_INTERNAL", raising=False)
    assert analytics._is_internal() is False
    monkeypatch.setenv("OPENNOLAN_INTERNAL", "1")
    assert analytics._is_internal() is True
    monkeypatch.setenv("OPENNOLAN_INTERNAL", "false")  # explicit off-values don't count
    assert analytics._is_internal() is False


def test_is_internal_via_sentinel_file(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENNOLAN_INTERNAL", raising=False)
    monkeypatch.setattr(analytics.Path, "home", lambda: tmp_path)
    assert analytics._is_internal() is False
    (tmp_path / analytics._INTERNAL_SENTINEL).touch()
    assert analytics._is_internal() is True


def test_env_props_attached_to_every_event(monkeypatch, tmp_path):
    _inject_fake_posthog(monkeypatch)
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    monkeypatch.setattr(analytics.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("OPENNOLAN_INTERNAL", "1")
    settings.set_value("analytics_disabled", False)
    analytics.reset()

    analytics.capture("project_created", {"pipeline_type": "instagram-fast-reel"})
    props = analytics._get_client().captures[0]["properties"]
    assert props["internal"] is True                       # developer machine → filterable
    assert props["env"] in ("packaged", "dev")             # build that fired it
    assert props["pipeline_type"] == "instagram-fast-reel"  # original props preserved


# ── analytics: client-error reporting (frontend / Electron → Error Tracking) ──────

def test_capture_client_error_reports_scrubbed(monkeypatch):
    _inject_fake_posthog(monkeypatch)
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    settings.set_value("analytics_disabled", False)
    analytics.reset()

    analytics.capture_client_error(
        "renderer",
        "boom at /Users/het/app.js",
        stack="Error: boom\n at /Users/het/app.js:10",
        context={"api_key": "sk-leak", "line": 10},
    )
    cap = analytics._get_client().captures[0]
    assert isinstance(cap["exc"], analytics._ClientError)
    assert "[path]" in str(cap["exc"]) and "/Users/het" not in str(cap["exc"])  # message path-redacted
    props = cap["properties"]
    assert props["source"] == "renderer" and props["platform"] == "client"
    assert "/Users/het" not in props["client_stack"]        # stack path-redacted
    assert props["api_key"] == "[redacted]" and props["line"] == 10  # context scrubbed like any props


def test_before_send_redacts_username_in_exception_frames():
    # The SDK builds $exception_list (with abs_path = /Users/<username>/…) AFTER _scrub runs, so the
    # OS username would leak unless _before_send catches it. Use the SDK's REAL serializer + the REAL
    # current username so this fails if the hook ever regresses.
    import getpass
    import json
    import sys

    from posthog.exception_utils import exceptions_from_error_tuple

    try:
        raise RuntimeError("open failed: /Users/nobody/secret.txt")
    except Exception:
        exc_list = exceptions_from_error_tuple(sys.exc_info())

    event = {"event": "$exception", "properties": {"$exception_list": exc_list, "path": "/api/x"}}
    out = analytics._before_send(event)
    blob = json.dumps(out["properties"])
    user = getpass.getuser()
    assert user and user not in blob        # THE invariant: current OS username must not leak
    assert "/Users/" not in blob            # no absolute user path survives (frames or message)
    assert "nobody" not in blob             # path inside the exception message is redacted too
    # Non-path structure is preserved (lineno present, still parseable) and URL routes are NOT
    # over-redacted — only real filesystem prefixes match _PATH_RE.
    assert out["properties"]["$exception_list"][0]["stacktrace"]["frames"][-1]["lineno"] > 0
    assert out["properties"]["path"] == "/api/x"


def test_before_send_never_raises_on_bad_input():
    # Contract: the SDK falls back to the UN-redacted event if this raises, so it must never raise.
    for bad in (None, {}, {"properties": None}, {"properties": {"$exception_list": "oops"}}, 42):
        analytics._before_send(bad)  # must not raise


def test_capture_client_error_noop_when_disabled(monkeypatch):
    constructed = _inject_fake_posthog(monkeypatch)
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    settings.set_value("analytics_disabled", True)
    analytics.reset()
    analytics.capture_client_error("renderer", "x")
    assert constructed == []  # opted out → no client, nothing sent


# ── app wiring: telemetry endpoint + global exception handler ────────────────────

def _client(monkeypatch):
    import tempfile

    from fastapi.testclient import TestClient

    from server.app import create_app

    return TestClient(create_app(projects_dir=tempfile.mkdtemp()), raise_server_exceptions=False)


def test_telemetry_error_endpoint_forwards(monkeypatch):
    seen = {}
    monkeypatch.setattr(analytics, "capture_client_error",
                        lambda *a, **k: seen.setdefault("call", (a, k)))
    resp = _client(monkeypatch).post("/api/telemetry/error",
                                     json={"source": "renderer", "message": "kaboom", "stack": "s"})
    assert resp.status_code == 200 and resp.json()["received"] is True
    assert seen["call"][0][0] == "renderer" and seen["call"][0][1] == "kaboom"


def test_unhandled_route_exception_is_reported_and_500(monkeypatch):
    import server.app as app_mod

    reported = {}
    monkeypatch.setattr(app_mod.analytics_mod, "capture_exception",
                        lambda exc, props=None: reported.setdefault("exc", (exc, props)))

    def boom(_pdir):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(app_mod, "list_projects", boom)  # make a real route throw

    resp = _client(monkeypatch).get("/api/projects")
    assert resp.status_code == 500                                  # clean 500, not a raw crash
    assert isinstance(reported["exc"][0], RuntimeError)             # crash reached PostHog
    assert reported["exc"][1]["path"] == "/api/projects"
