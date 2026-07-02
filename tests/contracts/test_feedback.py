"""Contract tests for server.feedback + the /api/feedback endpoint.

Invariants: feedback is ALWAYS stored locally (never lost), the analytics event carries no body,
and Resend email is best-effort (configured => sent; not configured => graceful, still stored).
"""

from __future__ import annotations

import json

import pytest

from server import feedback


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENNOLAN_HOME", str(tmp_path))
    # Neutralize analytics + email by default; individual tests opt back in.
    monkeypatch.setattr(feedback.analytics, "capture", lambda *a, **k: captured.append((a, k)))
    for var in ("RESEND_API_KEY", "FEEDBACK_TO", "FEEDBACK_FROM"):
        monkeypatch.delenv(var, raising=False)
    captured.clear()
    yield


captured: list = []


# ── validation ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,msg", [
    ("spam", "hi"),          # bad kind
    ("bug", ""),             # empty
    ("bug", "   "),          # whitespace only
    ("feature", "x" * 5001), # too long
])
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
    # the raw body is passed to analytics.capture, which scrubs it (message -> message_len);
    # scrub behavior itself is covered in test_analytics.py.


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
