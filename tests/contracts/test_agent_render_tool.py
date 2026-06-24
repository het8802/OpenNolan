"""Contract tests for the in-process `render` tool + render resume note + the
background-render deny guard (server/agent_runner.py).

These cover the off-by-one fix: the render runs as a blocking tool call (no CLI
background task, so no unsolicited turn), and a render that finishes after Stop is
surfaced on the user's NEXT message via the resume note. No network/ffmpeg.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
)

from server.agent_runner import (
    ACTION_ALLOW,
    ACTION_DENY,
    AgentRunner,
    bash_uses_videocompose_render,
    decide_tool,
    make_can_use_tool,
)
from server.render_jobs import RenderJobStore


# --- fakes ----------------------------------------------------------------

class FakeStore:
    """Drives status() through a scripted sequence (last entry repeats)."""

    def __init__(self, statuses, job_id="job1"):
        self._statuses = statuses
        self._i = 0
        self.job_id = job_id
        self.started: list = []
        self.consumed: list = []

    def start_with_inputs(self, project_id, inputs):
        self.started.append((project_id, inputs))
        return self.job_id

    def status(self, job_id):
        s = self._statuses[self._i] if self._i < len(self._statuses) else self._statuses[-1]
        self._i += 1
        return dict(s) if s is not None else None

    def mark_consumed(self, job_id):
        self.consumed.append(job_id)

    def active_job_for(self, project_id):
        return None


class FakeClient:
    def __init__(self, messages):
        self._messages = messages
        self.queries: list[str] = []

    async def connect(self, prompt=None):
        pass

    async def query(self, prompt, session_id="default"):
        self.queries.append(prompt)

    async def receive_response(self):
        for m in self._messages:
            yield m

    async def disconnect(self):
        pass


def _scripted_turn():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="m"),
        ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                      num_turns=1, session_id="s", total_cost_usd=0.01, result="done"),
    ]


def _payload(res):
    return json.loads(res["content"][0]["text"].split("\n\n", 1)[1])


# --- _run_render ----------------------------------------------------------

def test_run_render_success_emits_progress_and_consumes():
    store = FakeStore([
        {"status": "queued"},
        {"status": "running"},
        {"status": "done", "output_path": "renders/final.mp4",
         "warnings": ["w"], "final_review_status": "pass"},
    ])
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None,
                         render_store=store, render_poll_interval_s=0.0)
    events: list[dict] = []
    runner._emit["proj"] = lambda e: events.append(e)

    res = asyncio.run(runner._run_render("proj", {"edit_decisions": {"cuts": []}}))
    payload = _payload(res)
    assert payload["success"] is True
    assert payload["output_path"] == "renders/final.mp4"
    assert res["is_error"] is False
    assert any(e["type"] == "render_started" for e in events)
    assert any(e["type"] == "render_progress" for e in events)
    # seen in-turn -> marked consumed so the resume note won't re-fire next turn
    assert store.consumed == ["job1"]


def test_run_render_failed_returns_error():
    store = FakeStore([{"status": "failed", "error": "ffmpeg blew up"}])
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None,
                         render_store=store, render_poll_interval_s=0.0)
    res = asyncio.run(runner._run_render("proj", {"edit_decisions": {"cuts": []}}))
    payload = _payload(res)
    assert payload["success"] is False
    assert "ffmpeg blew up" in payload["error"]
    assert res["is_error"] is True
    assert store.consumed == ["job1"]


def test_run_render_timeout_does_not_cancel_job():
    store = FakeStore([{"status": "running"}])  # never finishes
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None,
                         render_store=store, render_poll_interval_s=0.0, render_timeout_s=0)
    res = asyncio.run(runner._run_render("proj", {"edit_decisions": {"cuts": []}}))
    payload = _payload(res)
    assert payload["timed_out"] is True
    assert res["is_error"] is True
    # left UNCONSUMED so the next turn's resume note surfaces the finished render
    assert store.consumed == []


def test_run_render_cancellation_keeps_job_running():
    store = FakeStore([{"status": "running"}])
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None,
                         render_store=store, render_poll_interval_s=10)  # long sleep -> cancel mid-wait

    async def go():
        task = asyncio.create_task(runner._run_render("proj", {"edit_decisions": {"cuts": []}}))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert store.consumed == []  # job NOT cancelled, NOT consumed


def test_run_render_without_store_errors_cleanly():
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)  # no render_store
    res = asyncio.run(runner._run_render("proj", {"edit_decisions": {"cuts": []}}))
    assert res["is_error"] is True
    assert "render store unavailable" in res["content"][0]["text"]


# --- _render_resume_note --------------------------------------------------

def _store_with_job(tmp_path, **job):
    store = RenderJobStore(tmp_path / "projects")
    j = {"job_id": "j1", "project_id": "p", "origin": "agent", "consumed": False}
    j.update(job)
    store._jobs["j1"] = j
    store._active_by_project["p"] = "j1"
    return store


def test_resume_note_done_fires_once(tmp_path):
    store = _store_with_job(tmp_path, status="done",
                            output_path="renders/final.mp4", warnings=["w1"])
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None, render_store=store)
    note = runner._render_resume_note("p")
    assert note and "COMPLETED" in note and "renders/final.mp4" in note
    assert runner._render_resume_note("p") is None       # consumed -> fires once


def test_resume_note_failed(tmp_path):
    store = _store_with_job(tmp_path, status="failed", error="boom")
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None, render_store=store)
    note = runner._render_resume_note("p")
    assert note and "FAILED" in note and "boom" in note
    assert runner._render_resume_note("p") is None


def test_resume_note_running_not_consumed(tmp_path):
    store = _store_with_job(tmp_path, status="running")
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None, render_store=store)
    note1 = runner._render_resume_note("p")
    assert note1 and "still running" in note1
    # still running -> NOT consumed, so the 'done' note can fire on a later turn
    assert runner._render_resume_note("p") == note1


def test_resume_note_ignores_editor_jobs(tmp_path):
    store = _store_with_job(tmp_path, status="done", origin="editor", output_path="renders/x.mp4")
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None, render_store=store)
    assert runner._render_resume_note("p") is None


def test_run_turn_warm_client_prepends_render_note(tmp_path):
    store = RenderJobStore(tmp_path / "projects")
    fake = FakeClient(_scripted_turn())
    runner = AgentRunner(repo_root=tmp_path, client_factory=lambda pid: fake, render_store=store)

    # Turn 1: fresh client, no render job yet.
    asyncio.run(runner.run_turn("p", "first"))
    assert "RENDER UPDATE" not in fake.queries[0]

    # A render the agent started finishes after Stop (warm client kept by interrupt()).
    store._jobs["j1"] = {"job_id": "j1", "project_id": "p", "status": "done",
                         "origin": "agent", "consumed": False,
                         "output_path": "renders/final.mp4", "warnings": None}
    store._active_by_project["p"] = "j1"

    # Turn 2: warm client -> the note is prepended to THIS message (correct attribution).
    asyncio.run(runner.run_turn("p", "did you QA?"))
    assert "RENDER UPDATE" in fake.queries[1]
    assert "COMPLETED" in fake.queries[1]
    assert "did you QA?" in fake.queries[1]


# --- deny guard -----------------------------------------------------------

def test_bash_uses_videocompose_render_detects_signature():
    cmd = ('python3 << EOF\nfrom tools.video.video_compose import VideoCompose\n'
           'VideoCompose().execute({"operation": "render_proxies", "edit_decisions": ed})\nEOF')
    assert bash_uses_videocompose_render(cmd) is True
    assert decide_tool("Bash", {"command": cmd}).action == ACTION_DENY


def test_other_videocompose_ops_not_denied():
    cmd = 'python3 -c "from tools.video.video_compose import VideoCompose; VideoCompose().execute({\'operation\': \'compose\'})"'
    assert bash_uses_videocompose_render(cmd) is False
    assert decide_tool("Bash", {"command": cmd}).action == ACTION_ALLOW


def test_plain_ffmpeg_still_allowed():
    assert decide_tool("Bash", {"command": "ffmpeg -i a.mp4 b.mp4"}).action == ACTION_ALLOW


def test_render_mcp_tool_allowed_by_policy():
    assert decide_tool("mcp__mc__render", {}).action == ACTION_ALLOW


def test_can_use_tool_maps_deny_to_permission_deny():
    can = make_can_use_tool(confirm_handler=None)
    cmd = ('from tools.video.video_compose import VideoCompose\n'
           'VideoCompose().execute({"operation": "render_proxies"})')
    res = asyncio.run(can("Bash", {"command": cmd}, None))
    assert isinstance(res, PermissionResultDeny)
    assert "render" in res.message.lower()


# --- app wiring: agent shares the editor's RenderJobStore -----------------

def test_runner_shares_render_store_with_editor(tmp_path, monkeypatch):
    """create_app()'s _runner builds the AgentRunner with render_store === the
    editor's _render_store(), so the agent's render tool and the editor render
    through ONE store keyed by project."""
    import server.app as app_mod
    from fastapi.testclient import TestClient
    from lib.project import create_project

    captured = {}

    class FakeRunner:
        def __init__(self, repo_root, render_store=None):
            self.render_store = render_store
            captured["render_store"] = render_store

        async def run_turn(self, project_id, message, on_event=None):
            if on_event is not None:
                await on_event({"type": "result", "is_error": False})

    monkeypatch.setattr(app_mod, "auth_configured", lambda: True)
    monkeypatch.setattr(app_mod, "AgentRunner", FakeRunner)

    create_project(tmp_path / "projects", "Sky", "animated-explainer")
    app = app_mod.create_app(
        projects_dir=tmp_path / "projects",
        capabilities_provider=lambda: {"composition_runtimes": {}, "capabilities": [],
                                       "setup_offers": [], "runtime_warnings": []},
    )
    client = TestClient(app)
    r = client.post("/api/projects/sky/chat", json={"message": "hi"})
    assert r.status_code == 200, r.text
    assert captured["render_store"] is app.state.render_store
    assert app.state.agent_runner.render_store is app.state.render_store
