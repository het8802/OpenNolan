"""Contract tests for server.feedback + the /api/feedback endpoint.

Invariants: feedback is ALWAYS stored locally (never lost), the analytics event carries no body,
and Resend email is best-effort (configured => sent; not configured => graceful, still stored).
"""

from __future__ import annotations

import base64
import json

import pytest

from server import feedback


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENNOLAN_HOME", str(tmp_path))
    # Neutralize analytics + email by default; individual tests opt back in.
    monkeypatch.setattr(feedback.analytics, "capture", lambda *a, **k: captured.append((a, k)))
    for var in ("RESEND_API_KEY", "FEEDBACK_TO", "FEEDBACK_FROM", "FEEDBACK_RELAY_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    # _DEFAULT_RELAY_URL now ships a real URL, so blank the relay by default — otherwise every test
    # would POST to the live endpoint. Relay tests below opt back in by setting it to a URL.
    monkeypatch.setenv("FEEDBACK_RELAY_URL", "")
    captured.clear()
    yield


captured: list = []


# ── validation ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kind,msg",
    [
        ("spam", "hi"),  # bad kind
        ("bug", ""),  # empty
        ("bug", "   "),  # whitespace only
        ("feature", "x" * 5001),  # too long
    ],
)
def test_invalid_submissions_raise(kind, msg):
    with pytest.raises(feedback.FeedbackError):
        feedback.submit(kind, msg)


# ── durable local record ────────────────────────────────────────────────────────


def test_submit_always_stores_locally(tmp_path):
    out = feedback.submit("bug", "scrub bar freezes on seek", email="het@example.com")
    assert out["stored"] is True and out["emailed"] is False  # no Resend key configured
    lines = (tmp_path / "feedback.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["kind"] == "bug"
    assert rec["message"] == "scrub bar freezes on seek"
    assert rec["email"] == "het@example.com"
    assert "ts" in rec


def test_submit_emits_metadata_only_event():
    feedback.submit("feature", "add auto-captions", email="a@b.com")
    assert len(captured) == 1
    (args, kwargs) = captured[0]
    assert args[0] == "feedback_submitted"
    props = args[1]
    assert props["kind"] == "feature" and props["has_email"] is True
    # Only a LENGTH leaves this function — the body is never handed to analytics at all.
    # `feedback_chars`, not `message_len`: _scrub matches the KEY, so anything containing
    # "message" is rewritten to "<key>_len" and a numeric length becomes None.
    assert props["feedback_chars"] == len("add auto-captions")
    assert not any("message" in k for k in props)


# ── Resend email (best effort) ───────────────────────────────────────────────────


def test_email_sent_when_configured(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("FEEDBACK_TO", "het@example.com")
    sent = {}

    class FakeResp:
        status_code = 200

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["url"] = url
        sent["json"] = json
        sent["auth"] = headers.get("Authorization")
        return FakeResp()

    monkeypatch.setattr(feedback.requests, "post", fake_post)
    out = feedback.submit("bug", "crash on export", email="user@x.com")
    assert out["emailed"] is True
    assert sent["url"] == feedback._RESEND_ENDPOINT
    assert sent["json"]["to"] == ["het@example.com"]
    assert sent["json"]["reply_to"] == "user@x.com"
    assert sent["auth"] == "Bearer re_test"


def test_email_failure_is_graceful(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("FEEDBACK_TO", "het@example.com")

    def boom(*a, **k):
        raise feedback.requests.RequestException("network down")

    monkeypatch.setattr(feedback.requests, "post", boom)
    out = feedback.submit("bug", "still works")
    assert out["stored"] is True and out["emailed"] is False  # stored despite email failure
    assert (tmp_path / "feedback.jsonl").exists()


# ── relay (public path — no secret shipped in the app) ──────────────────────────


def test_relay_used_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("FEEDBACK_RELAY_URL", "https://opennolan.app/api/feedback")
    monkeypatch.setenv("FEEDBACK_RELAY_TOKEN", "tok123")
    sent = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True, "emailed": True}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["url"] = url
        sent["token"] = headers.get("X-Feedback-Token")
        sent["json"] = json
        return FakeResp()

    monkeypatch.setattr(feedback.requests, "post", fake_post)
    out = feedback.submit("bug", "relay this", email="u@x.com")
    assert out["stored"] is True and out["emailed"] is True
    assert sent["url"] == "https://opennolan.app/api/feedback"
    assert sent["token"] == "tok123"
    assert sent["json"]["kind"] == "bug" and sent["json"]["message"] == "relay this"
    assert (tmp_path / "feedback.jsonl").exists()  # still stored locally regardless


def test_relay_failure_falls_back_and_is_graceful(monkeypatch, tmp_path):
    monkeypatch.setenv("FEEDBACK_RELAY_URL", "https://opennolan.app/api/feedback")

    def boom(*a, **k):
        raise feedback.requests.RequestException("relay down")

    monkeypatch.setattr(feedback.requests, "post", boom)
    out = feedback.submit("bug", "still stored")  # no direct Resend key either
    assert out["stored"] is True and out["emailed"] is False
    assert (tmp_path / "feedback.jsonl").exists()


# ── debug-report attachment (editor "send debug session" flow) ──────────────────


def _write_session(session, events):
    from server import debug_log

    debug_log.append_events(session, events)


def test_debug_attachment_built_from_session_log(tmp_path):
    session = "2026-07-09T00-00-00-000Z-abcd"
    _write_session(
        session,
        [
            {"seq": 0, "type": "session.start"},
            {"seq": 1, "type": "console", "level": "error", "data": {"args": ["boom"]}},
            {"seq": 2, "type": "session.stop"},
        ],
    )
    att = feedback._debug_attachment(session)
    assert att["filename"] == f"debug-session-{session}.ndjson"
    decoded = base64.b64decode(att["content"]).decode("utf-8")  # base64 round-trips to the raw NDJSON
    assert "session.stop" in decoded and "boom" in decoded


def test_debug_attachment_missing_or_none_is_none(tmp_path):
    assert feedback._debug_attachment("does-not-exist") is None
    assert feedback._debug_attachment(None) is None


def test_submit_emails_the_log_as_a_file_attachment_not_body(monkeypatch, tmp_path):
    session = "sess-attach"
    _write_session(session, [{"seq": 0, "type": "session.start"}, {"seq": 1, "type": "session.stop"}])
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("FEEDBACK_TO", "het@example.com")
    sent = {}

    class FakeResp:
        status_code = 200

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["json"] = json
        return FakeResp()

    monkeypatch.setattr(feedback.requests, "post", fake_post)
    out = feedback.submit("bug", "canvas froze", debug_session=session)
    assert out["emailed"] is True

    att = sent["json"]["attachments"][0]  # the log is a Resend attachment…
    assert att["filename"] == f"debug-session-{session}.ndjson"
    assert "session.stop" in base64.b64decode(att["content"]).decode("utf-8")
    assert "session.stop" not in sent["json"]["text"]  # …NOT pasted into the email body
    assert captured[-1][0][1]["has_attachment"] is True  # analytics flags it (metadata only)


def test_debug_session_missing_still_sends_feedback(tmp_path):
    # A bad/missing session must NOT lose the feedback and must NOT fold anything into the body.
    out = feedback.submit("bug", "no logs here", debug_session="does-not-exist")
    assert out["stored"] is True
    rec = json.loads((tmp_path / "feedback.jsonl").read_text().strip().splitlines()[-1])
    assert rec["debug_session"] == "does-not-exist"
    assert rec["diagnostics"] is None


def test_endpoint_passes_debug_session_through(tmp_path):
    # The HTTP route must carry debug_session into submit() (not silently drop it).
    import tempfile

    from fastapi.testclient import TestClient

    from server.app import create_app

    client = TestClient(create_app(projects_dir=tempfile.mkdtemp()))
    r = client.post("/api/feedback", json={"kind": "bug", "message": "x", "debug_session": "no-such"})
    assert r.status_code == 200 and r.json()["stored"] is True
    rec = json.loads((tmp_path / "feedback.jsonl").read_text().strip().splitlines()[-1])
    assert rec["debug_session"] == "no-such"


# ── endpoint ──────────────────────────────────────────────────────────────────


def test_endpoint_ok_and_validation(monkeypatch, tmp_path):
    import tempfile

    from fastapi.testclient import TestClient

    from server.app import create_app

    client = TestClient(create_app(projects_dir=tempfile.mkdtemp()))
    ok = client.post("/api/feedback", json={"kind": "bug", "message": "hello"})
    assert ok.status_code == 200 and ok.json()["stored"] is True
    bad = client.post("/api/feedback", json={"kind": "nope", "message": "hi"})
    assert bad.status_code == 400
