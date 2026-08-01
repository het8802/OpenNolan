"""Contract tests for the agent endpoints (chat + confirm).

The live streaming path needs real auth, so these cover the wiring we CAN
assert deterministically: project 404, the auth-gated 503 with setup guidance,
and the confirm endpoint's runner handling.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from lib.project import create_project
from server.agent_runner import AgentRunner
from server.app import create_app

PIPELINE = "animated-explainer"
STUB_CAPS = {"composition_runtimes": {}, "capabilities": [], "setup_offers": [], "runtime_warnings": []}


def _client(tmp_path, agent_runner=None):
    app = create_app(
        projects_dir=tmp_path / "projects",
        capabilities_provider=lambda: STUB_CAPS,
        agent_runner=agent_runner,
    )
    return TestClient(app)


def test_chat_unknown_project_404(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = _client(tmp_path).post("/api/projects/ghost/chat", json={"message": "hi"})
    assert r.status_code == 404


def test_chat_without_auth_returns_503_with_guidance(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Force the no-CLI path so the auth-gated 503 is exercised even on a dev
    # machine that has the `claude` CLI installed (which would otherwise pass).
    monkeypatch.setattr("server.agent_runner.claude_cli_available", lambda: False)
    create_project(tmp_path / "projects", "Sky", PIPELINE)
    r = _client(tmp_path).post("/api/projects/sky/chat", json={"message": "hi"})
    assert r.status_code == 503
    assert "setup-token" in r.json()["detail"]


def test_confirm_without_runner_409(tmp_path):
    r = _client(tmp_path).post(
        "/api/projects/sky/agent/confirm", json={"confirm_id": "x", "approved": True}
    )
    assert r.status_code == 409


def test_confirm_unknown_id_returns_resolved_false(tmp_path):
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    r = _client(tmp_path, agent_runner=runner).post(
        "/api/projects/sky/agent/confirm", json={"confirm_id": "nope", "approved": True}
    )
    assert r.status_code == 200
    assert r.json() == {"resolved": False}


def test_answer_without_runner_409(tmp_path):
    r = _client(tmp_path).post(
        "/api/projects/sky/agent/answer", json={"question_id": "x", "answer": "yes"}
    )
    assert r.status_code == 409


def test_answer_unknown_id_returns_resolved_false(tmp_path):
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    r = _client(tmp_path, agent_runner=runner).post(
        "/api/projects/sky/agent/answer", json={"question_id": "nope", "answer": "a"}
    )
    assert r.status_code == 200
    assert r.json() == {"resolved": False}


def test_stop_without_runner_409(tmp_path):
    r = _client(tmp_path).post("/api/projects/sky/agent/stop")
    assert r.status_code == 409


def test_stop_no_live_session_returns_stopped_false(tmp_path):
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    r = _client(tmp_path, agent_runner=runner).post("/api/projects/sky/agent/stop")
    assert r.status_code == 200
    assert r.json() == {"stopped": False}


def test_stop_interrupts_live_client():
    import asyncio

    class FakeClient:
        def __init__(self):
            self.interrupted = False

        async def interrupt(self):
            self.interrupted = True

    fake = FakeClient()
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    runner._clients["proj"] = fake
    assert asyncio.run(runner.interrupt("proj")) is True
    assert fake.interrupted is True
    # No live client for another project -> False, no crash.
    assert asyncio.run(runner.interrupt("other")) is False


# ── request_api_key: the agent's BYOK-key prompt (OPN-5) ─────────────────────

def test_provide_key_without_runner_409(tmp_path):
    r = _client(tmp_path).post(
        "/api/projects/sky/agent/provide-key",
        json={"key_request_id": "x", "env_var": "GOOGLE_API_KEY", "value": "k"},
    )
    assert r.status_code == 409


def test_provide_key_skip_unknown_id_resolved_false(tmp_path):
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    r = _client(tmp_path, agent_runner=runner).post(
        "/api/projects/sky/agent/provide-key",
        json={"key_request_id": "nope", "env_var": "GOOGLE_API_KEY", "skipped": True},
    )
    assert r.status_code == 200
    assert r.json() == {"resolved": False, "saved": False}


def test_provide_key_empty_value_400(tmp_path):
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    r = _client(tmp_path, agent_runner=runner).post(
        "/api/projects/sky/agent/provide-key",
        json={"key_request_id": "x", "env_var": "GOOGLE_API_KEY", "value": "   "},
    )
    assert r.status_code == 400


def test_provide_key_saves_env_then_resolves(tmp_path, monkeypatch):
    """POST /provide-key persists the key (write + reload), THEN unblocks the waiting tool.
    The write/reload are faked so the suite never touches the user's real .env."""
    import asyncio

    from server import env_config

    calls = {"writes": None, "reloaded": False}
    monkeypatch.setattr(env_config, "write_env_vars",
                        lambda updates: (calls.__setitem__("writes", dict(updates)), list(updates))[1])
    monkeypatch.setattr(env_config, "reload_env", lambda: calls.__setitem__("reloaded", True))

    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    runner._key_requests["sky:k1"] = fut

    r = _client(tmp_path, agent_runner=runner).post(
        "/api/projects/sky/agent/provide-key",
        json={"key_request_id": "sky:k1", "env_var": "GOOGLE_API_KEY", "value": "sk-goog-123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resolved"] is True and body["saved"] is True
    assert calls["writes"] == {"GOOGLE_API_KEY": "sk-goog-123"}
    assert calls["reloaded"] is True
    assert fut.done() and fut.result() is True
    loop.close()


def test_provide_key_invalid_env_var_name_400(tmp_path, monkeypatch):
    from server import env_config
    # A bad var name makes write_env_vars raise ValueError -> 400 (and the future stays pending).
    def _raise(_updates):
        raise ValueError("Invalid variable name: 'bad name'")
    monkeypatch.setattr(env_config, "write_env_vars", _raise)
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    r = _client(tmp_path, agent_runner=runner).post(
        "/api/projects/sky/agent/provide-key",
        json={"key_request_id": "sky:k1", "env_var": "bad name", "value": "v"},
    )
    assert r.status_code == 400


async def _drive_key_request(runner, project_id, env_var, provider, reason):
    """Start _request_api_key, wait until it has emitted + registered its future, and return the
    (task, emitted_event) so a test can resolve it."""
    import asyncio
    events: list = []

    async def emit(evt):
        events.append(evt)

    runner._emit[project_id] = emit
    task = asyncio.create_task(runner._request_api_key(project_id, env_var, provider, reason))
    for _ in range(50):
        if events:
            break
        await asyncio.sleep(0)
    return task, (events[0] if events else None)


def test_request_api_key_emits_event_and_decline_path():
    import asyncio

    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)

    async def scenario():
        task, evt = await _drive_key_request(runner, "proj", "GOOGLE_API_KEY", "Google", "to make the video")
        assert evt is not None
        assert evt["type"] == "api_key_request"
        assert evt["env_var"] == "GOOGLE_API_KEY"
        assert evt["label"]  # enriched from the curated BYOK menu
        # decline
        assert runner.resolve_key_request(evt["key_request_id"], False) is True
        return await task

    result = asyncio.run(scenario())
    text = result["content"][0]["text"]
    assert '"provided": false' in text
    assert result["is_error"] is False


def test_request_api_key_provided_returns_retry_and_double_resolve_noop():
    import asyncio

    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)

    async def scenario():
        task, evt = await _drive_key_request(runner, "p", "REPLICATE_API_TOKEN", "Replicate", "")
        kid = evt["key_request_id"]
        assert runner.resolve_key_request(kid, True) is True
        # second resolve of the same id is a no-op (future already done)
        assert runner.resolve_key_request(kid, True) is False
        return await task

    result = asyncio.run(scenario())
    text = result["content"][0]["text"]
    assert '"provided": true' in text
    assert "RETRY" in text


def test_request_api_key_without_stream_skips():
    import asyncio

    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    result = asyncio.run(runner._request_api_key("proj", "FAL_KEY", "", ""))
    text = result["content"][0]["text"]
    assert '"provided": false' in text
