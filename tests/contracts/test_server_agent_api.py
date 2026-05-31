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
