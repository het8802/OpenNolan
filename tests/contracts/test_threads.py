"""Contract tests for chat-thread persistence (server/threads.py + endpoints)."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from lib.project import create_project
from server import threads as ts
from server.agent_runner import AgentRunner
from server.app import create_app

PIPELINE = "animated-explainer"
STUB = {"composition_runtimes": {}, "capabilities": [], "setup_offers": [], "runtime_warnings": []}


# --- store -----------------------------------------------------------------

def test_create_list_get_save_thread(tmp_path):
    projects = tmp_path / "projects"
    rec = ts.create_thread(projects, "p1", title="First")
    tid = rec["thread_id"]
    assert rec["messages"] == [] and rec["session_id"] is None

    ts.save_thread(projects, "p1", tid,
                   messages=[{"role": "user", "text": "hi"}], session_id="sess-1", title="Hi chat")
    got = ts.get_thread(projects, "p1", tid)
    assert got["session_id"] == "sess-1"
    assert got["title"] == "Hi chat"
    assert got["messages"][0]["text"] == "hi"
    assert got["created_at"] == rec["created_at"]  # preserved

    summaries = ts.list_threads(projects, "p1")
    assert len(summaries) == 1
    assert summaries[0]["thread_id"] == tid
    assert summaries[0]["message_count"] == 1
    assert "messages" not in summaries[0]  # summaries omit bodies


def test_get_missing_thread_returns_none(tmp_path):
    assert ts.get_thread(tmp_path / "projects", "p1", "nope") is None


# --- endpoints -------------------------------------------------------------

def _client(tmp_path, runner=None):
    return TestClient(create_app(projects_dir=tmp_path / "projects",
                                 capabilities_provider=lambda: STUB, agent_runner=runner))


def test_thread_crud_endpoints(tmp_path):
    projects = tmp_path / "projects"
    create_project(projects, "Sky", PIPELINE)
    c = _client(tmp_path)

    created = c.post("/api/projects/sky/threads", json={"title": "T1"})
    assert created.status_code == 201
    tid = created.json()["thread_id"]

    c.put(f"/api/projects/sky/threads/{tid}",
          json={"messages": [{"role": "user", "text": "make a video"}], "session_id": "s9", "title": "Vid"})

    listed = c.get("/api/projects/sky/threads").json()["threads"]
    assert any(t["thread_id"] == tid and t["title"] == "Vid" for t in listed)

    full = c.get(f"/api/projects/sky/threads/{tid}").json()
    assert full["session_id"] == "s9"
    assert full["messages"][0]["text"] == "make a video"

    assert c.get("/api/projects/sky/threads/ghost").status_code == 404


def test_switch_session_resumes_thread_session():
    import asyncio
    runner = AgentRunner(repo_root=".", client_factory=lambda key: None)
    # an existing thread session -> flagged to resume
    asyncio.run(runner.switch_session("proj", "sess-abc"))
    assert runner._session_ids.get("proj") == "sess-abc"
    assert runner._resume_next.get("proj") is True
    # a brand-new thread (None) -> fresh, no resume
    asyncio.run(runner.switch_session("proj", None))
    assert runner._session_ids.get("proj") is None
    assert runner._resume_next.get("proj") in (None, False)
