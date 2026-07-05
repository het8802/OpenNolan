"""Contract tests for Anthropic account auth (server/auth.py) + the /api/auth/* endpoints.

Pure logic (PKCE, error classification, expiry, method detection, token persistence) is tested with
env/settings/HTTP monkeypatched so nothing touches the real .env, settings.json, or the network. The
endpoints are smoke-tested via TestClient with the auth module functions stubbed.
"""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from server import auth

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Never read/write the real credential env across tests, and reset the in-memory OAuth state."""
    auth._pending.clear()
    for k in (auth.OAUTH_TOKEN_ENV, auth.REFRESH_TOKEN_ENV, auth.API_KEY_ENV):
        monkeypatch.delenv(k, raising=False)
    yield
    auth._pending.clear()


# ── classification ──────────────────────────────────────────────────────────

def test_classify_auth_error_positive():
    for t in [
        "Error 401 Unauthorized", "HTTP 403 Forbidden", "OAuth token has expired",
        "invalid_grant", "authentication_error: invalid x-api-key",
        "Invalid API key provided", "credit balance is too low", "Please run /login",
    ]:
        assert auth.classify_auth_error(t), t


def test_classify_auth_error_negative():
    for t in [
        "ffmpeg exited with code 234", "file not found", "connection reset by peer",
        "listening on port 4013", "timed out after 30s", "render failed", "", None,
    ]:
        assert not auth.classify_auth_error(t), t


# ── PKCE / start_oauth ────────────────────────────────────────────────────────

def test_http_json_sends_non_default_user_agent(monkeypatch):
    """console.anthropic.com's Cloudflare 403s the default Python-urllib User-Agent ('Error 1010:
    browser_signature_banned'), which silently breaks the OAuth token exchange. Every request must
    carry an explicit app User-Agent — regression guard for that fix."""
    captured: dict = {}

    class _Resp:
        status = 200
        def read(self):
            return b"{}"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["ua"] = req.get_header("User-agent")
        return _Resp()

    monkeypatch.setattr(auth.urllib.request, "urlopen", fake_urlopen)
    auth._http_json("https://example.com/x", "POST", body={"a": 1})
    assert captured["ua"] and "urllib" not in captured["ua"].lower()


def test_err_message_reads_cloudflare_and_oauth_bodies():
    assert auth._err_message({"error": "invalid_grant", "error_description": "Invalid 'code'."}) == "Invalid 'code'."
    assert "blocked" in auth._err_message({"detail": "The site owner has blocked access."})
    assert auth._err_message({"error": {"message": "boom"}}) == "boom"


def test_start_oauth_builds_authorize_url_with_valid_pkce():
    out = auth.start_oauth()
    assert out["authorize_url"].startswith(auth.AUTHORIZE_URL + "?")
    q = parse_qs(urlparse(out["authorize_url"]).query)
    assert q["client_id"] == [auth.CLIENT_ID]
    assert q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == [auth.REDIRECT_URI]
    assert q["state"] == [out["state"]]
    # the challenge must be the S256 of the stored verifier
    verifier = auth._pending[out["state"]]["verifier"]
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == [expected]


# ── finish_oauth (token exchange) ───────────────────────────────────────────────

def _stub_persistence(monkeypatch):
    """Route .env writes + settings into in-memory dicts."""
    writes: dict = {}
    saved: dict = {}
    monkeypatch.setattr(auth.env_config, "write_env_vars", lambda u, *a, **k: writes.update(u))
    monkeypatch.setattr(auth.env_config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(auth.settings, "set_value", lambda key, val: saved.__setitem__(key, val))
    monkeypatch.setattr(auth.settings, "get", lambda key, default=None: saved.get(key, default))
    return writes, saved


def test_finish_oauth_exchanges_and_persists(monkeypatch):
    writes, saved = _stub_persistence(monkeypatch)
    monkeypatch.setattr(auth, "_http_json",
                        lambda url, method="GET", **kw: (200, {"access_token": "oauth-xyz",
                                                               "refresh_token": "r-1", "expires_in": 3600}))
    started = auth.start_oauth()
    result = auth.finish_oauth(f"the-code#{started['state']}")
    assert os.environ[auth.OAUTH_TOKEN_ENV] == "oauth-xyz"
    assert os.environ[auth.REFRESH_TOKEN_ENV] == "r-1"
    assert writes[auth.OAUTH_TOKEN_ENV] == "oauth-xyz"
    assert saved["claude_auth"]["method"] == "oauth"
    assert saved["claude_auth"]["expires_at"] is not None
    assert result["authenticated"] is True and result["method"] == "oauth"
    assert result["needs_reauth"] is False


def test_finish_oauth_unknown_state_raises():
    with pytest.raises(auth.AuthError):
        auth.finish_oauth("some-code#nonexistent-state")


def test_finish_oauth_rejects_token_error(monkeypatch):
    _stub_persistence(monkeypatch)
    monkeypatch.setattr(auth, "_http_json",
                        lambda url, method="GET", **kw: (400, {"error": {"message": "invalid_grant"}}))
    started = auth.start_oauth()
    with pytest.raises(auth.AuthError):
        auth.finish_oauth(f"bad#{started['state']}")


# ── API-key fallback ─────────────────────────────────────────────────────────

def test_set_api_key_validates_then_persists(monkeypatch):
    writes, saved = _stub_persistence(monkeypatch)
    monkeypatch.setattr(auth, "_validate_api_key", lambda k: (True, ""))
    result = auth.set_api_key("sk-ant-123")
    assert os.environ[auth.API_KEY_ENV] == "sk-ant-123"
    assert writes[auth.API_KEY_ENV] == "sk-ant-123"
    assert result["method"] == "api_key" and result["authenticated"] is True


def test_set_api_key_rejects_invalid(monkeypatch):
    _stub_persistence(monkeypatch)
    monkeypatch.setattr(auth, "_validate_api_key", lambda k: (False, "Anthropic rejected that key."))
    with pytest.raises(auth.AuthError):
        auth.set_api_key("bad-key")


def test_set_api_key_requires_a_value():
    with pytest.raises(auth.AuthError):
        auth.set_api_key("   ")


# ── expiry + status ──────────────────────────────────────────────────────────

def test_is_expired():
    assert auth._is_expired(None) is False
    assert auth._is_expired("not-a-date") is False
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert auth._is_expired(past) is True
    assert auth._is_expired(future) is False


def test_status_detects_method_and_precedence(monkeypatch):
    monkeypatch.setattr(auth.settings, "get", lambda key, default=None: None)
    monkeypatch.setattr(auth, "_cli_available", lambda: False)

    s = auth.status()
    assert s["authenticated"] is False and s["method"] is None

    monkeypatch.setenv(auth.API_KEY_ENV, "sk-ant-x")
    assert auth.status()["method"] == "api_key"

    # OAuth token wins over an API key.
    monkeypatch.setenv(auth.OAUTH_TOKEN_ENV, "oauth-x")
    assert auth.status()["method"] == "oauth"


def test_status_flags_needs_reauth_on_recorded_error(monkeypatch):
    monkeypatch.setenv(auth.OAUTH_TOKEN_ENV, "oauth-x")
    monkeypatch.setattr(auth, "_cli_available", lambda: False)
    monkeypatch.setattr(auth.settings, "get",
                        lambda key, default=None: {"detail": "401"} if key == "claude_auth_error" else None)
    s = auth.status()
    assert s["authenticated"] is True and s["needs_reauth"] is True and s["error"] == "401"


# ── endpoints ──────────────────────────────────────────────────────────────

def test_auth_status_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr("server.auth.status", lambda: {
        "authenticated": True, "method": "oauth", "needs_reauth": False,
        "expired": False, "expires_at": None, "obtained_at": None, "error": None,
    })
    client = TestClient(create_app(projects_dir=tmp_path / "projects"))
    resp = client.get("/api/auth/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True and body["method"] == "oauth"


def test_auth_api_key_endpoint_ok(tmp_path, monkeypatch):
    monkeypatch.setattr("server.auth.set_api_key", lambda key, app_state=None: {
        "authenticated": True, "method": "api_key", "needs_reauth": False,
        "expired": False, "expires_at": None, "obtained_at": None, "error": None,
    })
    client = TestClient(create_app(projects_dir=tmp_path / "projects"))
    resp = client.post("/api/auth/api-key", json={"api_key": "sk-ant-x"})
    assert resp.status_code == 200 and resp.json()["method"] == "api_key"


def test_auth_api_key_endpoint_rejects_bad_key(tmp_path, monkeypatch):
    def boom(key, app_state=None):
        raise auth.AuthError("Anthropic rejected that API key.")
    monkeypatch.setattr("server.auth.set_api_key", boom)
    client = TestClient(create_app(projects_dir=tmp_path / "projects"))
    resp = client.post("/api/auth/api-key", json={"api_key": "bad"})
    assert resp.status_code == 400
    assert "rejected" in resp.json()["detail"]


def test_auth_oauth_start_endpoint(tmp_path):
    client = TestClient(create_app(projects_dir=tmp_path / "projects"))
    resp = client.post("/api/auth/oauth/start")
    assert resp.status_code == 200
    body = resp.json()
    assert body["authorize_url"].startswith(auth.AUTHORIZE_URL)
    assert body["state"]


def test_chat_result_auth_error_surfaces_auth_error_event(tmp_path, monkeypatch):
    """A turn that fails auth via a ResultMessage (is_error + text in the `result` field, not a raised
    exception) must be re-tagged as an `auth_error` SSE event AND mark the reconnect flag — regression
    guard for the classifier reading the right event field."""
    from lib.project import create_project

    marks: list[str] = []
    monkeypatch.setenv(auth.OAUTH_TOKEN_ENV, "dummy-token")  # pass the auth_configured() 503 gate
    monkeypatch.setattr("server.auth.mark_auth_error", lambda detail: marks.append(detail))
    monkeypatch.setattr("server.auth.clear_auth_error", lambda: None)

    class _FakeRunner:
        async def run_turn(self, project_id, message, on_event=None):
            await on_event({"type": "result", "is_error": True,
                            "result": "authentication_error: OAuth token has expired"})

    create_project(tmp_path / "projects", "Sky", "animated-explainer")
    app = create_app(
        projects_dir=tmp_path / "projects",
        capabilities_provider=lambda: {"composition_runtimes": {}, "capabilities": [],
                                       "setup_offers": [], "runtime_warnings": []},
        agent_runner=_FakeRunner(),
    )
    resp = TestClient(app).post("/api/projects/sky/chat", json={"message": "hi"})
    assert resp.status_code == 200
    assert "auth_error" in resp.text
    assert marks and "authentication_error" in marks[0]
