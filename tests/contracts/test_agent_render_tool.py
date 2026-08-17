"""Contract tests for the in-process `render` tool + render resume note + the
background-render deny guard (server/agent_runner.py).

These cover the off-by-one fix: the render runs as a blocking tool call (no CLI
background task, so no unsolicited turn), and a render that finishes after Stop is
surfaced on the user's NEXT message via the resume note. No network/ffmpeg.
"""

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

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
    AGENT_AUTO_ALLOWED_TOOLS,
    AgentRunner,
    bash_uses_videocompose_render,
    decide_tool,
    make_can_use_tool,
    make_pre_tool_use_hook,
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

    def start_op(self, project_id, tool_name, tool_input, *, session_id=None, turn_id=None):
        self.started.append((project_id, tool_name, tool_input))
        return self.job_id

    def status(self, job_id):
        s = self._statuses[self._i] if self._i < len(self._statuses) else self._statuses[-1]
        self._i += 1
        return dict(s) if s is not None else None

    def mark_consumed(self, job_id):
        self.consumed.append(job_id)

    def active_job_for(self, project_id):
        return None

    def latest_unconsumed_agent_job(self, project_id):
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

    async def receive_messages(self):
        # Nothing buffered between turns in this fake — the warm-client drain no-ops.
        for m in ():
            yield m

    async def disconnect(self):
        pass


def _scripted_turn():
    return [
        AssistantMessage(content=[TextBlock(text="ok")], model="m"),
        ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            total_cost_usd=0.01,
            result="done",
        ),
    ]


def _payload(res):
    return json.loads(res["content"][0]["text"].split("\n\n", 1)[1])


# An INLINE edit_decisions doc is now schema-validated before any job starts (it is the
# doc the publisher commits on success), so these tests need a doc that actually validates.
VALID_ED = {
    "version": "1.0",
    "render_runtime": "ffmpeg",
    "renderer_family": "social-reel",
    "cuts": [{"id": "c1", "source": "assets/video/a.mp4", "in_seconds": 0, "out_seconds": 5}],
}


# --- _run_render ----------------------------------------------------------


def test_run_render_success_emits_progress_and_consumes():
    store = FakeStore(
        [
            {"status": "queued"},
            {"status": "running"},
            {"status": "done", "output_path": "renders/final.mp4", "warnings": ["w"], "final_review_status": "pass"},
        ]
    )
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None, render_store=store, render_poll_interval_s=0.0)
    events: list[dict] = []
    runner._emit["proj"] = lambda e: events.append(e)

    res = asyncio.run(runner._run_render("proj", {"edit_decisions": VALID_ED}))
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
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None, render_store=store, render_poll_interval_s=0.0)
    res = asyncio.run(runner._run_render("proj", {"edit_decisions": VALID_ED}))
    payload = _payload(res)
    assert payload["success"] is False
    assert "ffmpeg blew up" in payload["error"]
    assert res["is_error"] is True
    assert store.consumed == ["job1"]


def test_run_render_timeout_does_not_cancel_job():
    store = FakeStore([{"status": "running"}])  # never finishes
    runner = AgentRunner(
        repo_root=".",
        client_factory=lambda pid: None,
        render_store=store,
        render_poll_interval_s=0.0,
        render_timeout_s=0,
    )
    res = asyncio.run(runner._run_render("proj", {"edit_decisions": VALID_ED}))
    payload = _payload(res)
    assert payload["timed_out"] is True
    assert res["is_error"] is True
    # left UNCONSUMED so the next turn's resume note surfaces the finished render
    assert store.consumed == []


def test_run_render_cancellation_keeps_job_running():
    store = FakeStore([{"status": "running"}])
    runner = AgentRunner(
        repo_root=".", client_factory=lambda pid: None, render_store=store, render_poll_interval_s=10
    )  # long sleep -> cancel mid-wait

    async def go():
        task = asyncio.create_task(runner._run_render("proj", {"edit_decisions": VALID_ED}))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert store.consumed == []  # job NOT cancelled, NOT consumed


def test_run_render_without_store_errors_cleanly():
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)  # no render_store
    res = asyncio.run(runner._run_render("proj", {"edit_decisions": VALID_ED}))
    assert res["is_error"] is True
    assert "render store unavailable" in res["content"][0]["text"]


# --- _run_media_op (generalized in-process blocking op) -------------------


def test_run_media_op_blocks_and_returns_in_turn():
    store = FakeStore(
        [
            {"status": "queued"},
            {"status": "running"},
            {
                "status": "done",
                "output_path": "assets/video/cut.mp4",
                "result_data": {"output": "assets/video/cut.mp4", "silence_removed_seconds": 49.5},
            },
        ]
    )
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None, render_store=store, render_poll_interval_s=0.0)
    events: list[dict] = []
    runner._emit["proj"] = lambda e: events.append(e)

    res = asyncio.run(runner._run_media_op("proj", "silence_cutter", {"input_path": "x.mov"}))
    payload = _payload(res)
    assert payload["success"] is True
    assert payload["output_path"] == "assets/video/cut.mp4"
    assert payload["tool"] == "silence_cutter"
    assert res["is_error"] is False
    assert any(e["type"] == "media_op_started" for e in events)
    assert any(e["type"] == "media_op_progress" for e in events)
    assert store.consumed == ["job1"]  # seen in-turn -> won't re-fire next turn
    assert store.started == [("proj", "silence_cutter", {"input_path": "x.mov"})]


def test_run_media_op_failed_returns_error():
    store = FakeStore([{"status": "failed", "error": "bad codec"}])
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None, render_store=store, render_poll_interval_s=0.0)
    res = asyncio.run(runner._run_media_op("proj", "motion_ops", {"operation": "speed"}))
    payload = _payload(res)
    assert payload["success"] is False
    assert "bad codec" in payload["error"]
    assert res["is_error"] is True
    assert store.consumed == ["job1"]


def test_run_media_op_timeout_does_not_cancel_job():
    store = FakeStore([{"status": "running"}])  # never finishes
    runner = AgentRunner(
        repo_root=".",
        client_factory=lambda pid: None,
        render_store=store,
        render_poll_interval_s=0.0,
        render_timeout_s=0,
    )
    res = asyncio.run(runner._run_media_op("proj", "silence_cutter", {"input_path": "x.mov"}))
    payload = _payload(res)
    assert payload["timed_out"] is True
    assert res["is_error"] is True
    assert store.consumed == []  # left UNCONSUMED -> next turn surfaces it


def test_run_media_op_cancellation_keeps_job_running():
    store = FakeStore([{"status": "running"}])
    runner = AgentRunner(
        repo_root=".", client_factory=lambda pid: None, render_store=store, render_poll_interval_s=10
    )  # long sleep -> cancel mid-wait

    async def go():
        task = asyncio.create_task(runner._run_media_op("proj", "silence_cutter", {"input_path": "x.mov"}))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert store.consumed == []  # job NOT cancelled, NOT consumed


def test_run_media_op_without_store_errors_cleanly():
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)  # no render_store
    res = asyncio.run(runner._run_media_op("proj", "silence_cutter", {"input_path": "x.mov"}))
    assert res["is_error"] is True
    assert "job store unavailable" in res["content"][0]["text"]


def test_media_op_resume_note_completed_fires_once(tmp_path):
    store = _store_with_job(
        tmp_path, origin="agent_op", tool_name="silence_cutter", status="done", output_path="assets/video/cut.mp4"
    )
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None, render_store=store)
    note = runner._render_resume_note("p")
    assert note and "MEDIA OP UPDATE" in note and "COMPLETED" in note
    assert "silence_cutter" in note and "assets/video/cut.mp4" in note
    assert runner._render_resume_note("p") is None  # consumed -> fires once


# --- heavy-media-op deny steer --------------------------------------------


def test_bash_steer_denies_heavy_media_op():
    from server.agent_runner import bash_runs_heavy_media_op

    cmd = (
        'python -c "from tools.video.silence_cutter import SilenceCutter; '
        "SilenceCutter().execute({'input_path': 'x.mov'})\""
    )
    assert bash_runs_heavy_media_op(cmd)
    # The steer lives in the always-run PreToolUse hook: sandbox auto-approval or an allow
    # rule can resolve a Bash call before can_use_tool ever runs.
    out = asyncio.run(make_pre_tool_use_hook(None)({"tool_name": "Bash", "tool_input": {"command": cmd}}, "t", None))
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    assert "run_media_op" in spec["permissionDecisionReason"]


def test_bash_steer_allows_introspection_and_quick_calls():
    from server.agent_runner import bash_runs_heavy_media_op

    ok = [
        "ffprobe -i x.mov",
        ".venv/bin/python scripts/update_stage.py p edit in_progress inst",
        '.venv/bin/python -c "from tools.tool_registry import registry; registry.discover()"',
        (
            '.venv/bin/python -c "from tools.tool_registry import registry; '
            "print(registry.get('silence_cutter').get_info())\""
        ),
    ]
    for cmd in ok:
        assert bash_runs_heavy_media_op(cmd) is None, cmd
        assert decide_tool("Bash", {"command": cmd}).action == ACTION_ALLOW, cmd


# --- _render_resume_note --------------------------------------------------


def _store_with_job(tmp_path, **job):
    store = RenderJobStore(tmp_path / "projects")
    j = {"job_id": "j1", "project_id": "p", "origin": "agent", "consumed": False}
    j.update(job)
    store._jobs["j1"] = j
    store._active_by_project["p"] = "j1"
    return store


def test_resume_note_done_fires_once(tmp_path):
    store = _store_with_job(tmp_path, status="done", output_path="renders/final.mp4", warnings=["w1"])
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None, render_store=store)
    note = runner._render_resume_note("p")
    assert note and "COMPLETED" in note and "renders/final.mp4" in note
    assert runner._render_resume_note("p") is None  # consumed -> fires once


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
    store._jobs["j1"] = {
        "job_id": "j1",
        "project_id": "p",
        "status": "done",
        "origin": "agent",
        "consumed": False,
        "output_path": "renders/final.mp4",
        "warnings": None,
    }
    store._active_by_project["p"] = "j1"

    # Turn 2: warm client -> the note is prepended to THIS message (correct attribution).
    asyncio.run(runner.run_turn("p", "did you QA?"))
    assert "RENDER UPDATE" in fake.queries[1]
    assert "COMPLETED" in fake.queries[1]
    assert "did you QA?" in fake.queries[1]


# --- OPN-30: validate before, commit with the video, never forge a receipt ---


class _FakeVC:
    """Minimal VideoCompose stub for the store the runner actually drives."""

    def __init__(self, *, succeed=True):
        self.succeed = succeed
        self.calls: list[dict] = []

    def execute(self, inputs):
        self.calls.append(inputs)
        if not self.succeed:
            return SimpleNamespace(success=False, data=None, error="ffmpeg exploded")
        out = Path(inputs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"video-bytes")
        return SimpleNamespace(success=True, data={}, error=None)


def _real_store(tmp_path, *, succeed=True):
    from lib.project import create_project

    projects = tmp_path / "projects"
    create_project(projects, "Demo")
    store = RenderJobStore(projects)
    store._tool = _FakeVC(succeed=succeed)
    return store, projects


def _runner(tmp_path, store, projects):
    return AgentRunner(repo_root=tmp_path, projects_dir=projects, render_store=store, render_poll_interval_s=0.01)


def test_invalid_inline_doc_fails_before_any_job_starts(tmp_path):
    """A doc that can't be persisted IS the desync: rendering it would produce a video no
    edit_decisions.json describes. Fail the call, start nothing, write nothing."""
    store, projects = _real_store(tmp_path)
    runner = _runner(tmp_path, store, projects)
    doc_file = projects / "demo" / "artifacts" / "edit_decisions.json"

    res = asyncio.run(runner._run_render("demo", {"edit_decisions": {"cuts": []}}))

    payload = _payload(res)
    assert payload["success"] is False
    assert "schema validation" in payload["error"]
    assert store._jobs == {}  # no job started
    assert not doc_file.exists()  # disk untouched
    assert not (projects / "demo" / "renders" / "final.mp4").exists()


def test_valid_inline_doc_is_committed_with_the_video(tmp_path):
    from lib.project import FINAL_RECEIPT_NAME, canonical_doc_hash
    from server.editor import read_edit_decisions

    store, projects = _real_store(tmp_path)
    runner = _runner(tmp_path, store, projects)

    res = asyncio.run(runner._run_render("demo", {"edit_decisions": VALID_ED}))

    assert _payload(res)["success"] is True
    assert _payload(res)["output_path"] == "renders/final.mp4"
    # The CALLER's doc, not the source-resolved render copy.
    assert read_edit_decisions(projects, "demo") == VALID_ED
    receipt = json.loads((projects / "demo" / "renders" / FINAL_RECEIPT_NAME).read_text())
    assert receipt["doc_hash"] == canonical_doc_hash(VALID_ED)


def test_a_failed_render_does_not_commit_the_doc(tmp_path):
    """Persisting the doc BEFORE rendering would recreate the bug in the other direction:
    the editor showing a timeline that never produced a video."""
    store, projects = _real_store(tmp_path, succeed=False)
    runner = _runner(tmp_path, store, projects)
    doc_file = projects / "demo" / "artifacts" / "edit_decisions.json"

    res = asyncio.run(runner._run_render("demo", {"edit_decisions": VALID_ED}))

    assert _payload(res)["success"] is False
    assert not doc_file.exists()


def test_editor_render_survives_an_autosave_mid_render(tmp_path):
    """Autosave is NOT suspended during a render (Studio.jsx gates it on the AGENT being
    busy). So the user can save doc B while doc A renders — and B must win."""
    from lib.project import final_render_status

    doc_a = dict(VALID_ED)
    doc_b = {
        **VALID_ED,
        "cuts": VALID_ED["cuts"] + [{"id": "c2", "source": "assets/video/b.mp4", "in_seconds": 0, "out_seconds": 2}],
    }
    gate = threading.Event()

    class _GatedVC(_FakeVC):
        def execute(self, inputs):
            gate.wait(timeout=5)
            return super().execute(inputs)

    store, projects = _real_store(tmp_path)
    store._tool = _GatedVC()
    doc_file = projects / "demo" / "artifacts" / "edit_decisions.json"
    doc_file.write_text(json.dumps(doc_a))

    jid = store.start("demo")  # editor Render of doc A
    time.sleep(0.05)
    doc_file.write_text(json.dumps(doc_b))  # the user keeps editing -> autosave B
    gate.set()
    deadline = time.time() + 5
    while time.time() < deadline and store.status(jid)["status"] != "done":
        time.sleep(0.01)

    assert store.status(jid)["status"] == "done"
    assert json.loads(doc_file.read_text()) == doc_b  # B survived
    status = final_render_status(projects, "demo")
    assert status["current"] is False  # ...and the render reads stale
    assert "timeline changed" in status["reason"]


def test_store_asset_final_render_replaces_and_invalidates_the_receipt(tmp_path):
    """store_asset hands over bytes that may be unrelated to the timeline. It must replace
    the deliverable (not park a final.<hash>.mp4 beside it) and must NOT inherit a receipt
    that would certify them as the current cut."""
    from lib.project import FINAL_RECEIPT_NAME, final_render_status

    store, projects = _real_store(tmp_path)
    runner = _runner(tmp_path, store, projects)
    asyncio.run(runner._run_render("demo", {"edit_decisions": VALID_ED}))
    assert final_render_status(projects, "demo")["current"] is True
    published = projects / "demo" / "renders" / "final.mp4"
    old = published.stat()

    # Same size, same mtime_ns: the file-identity half of the check CANNOT catch this, so
    # only unlinking the receipt keeps the answer honest.
    src = tmp_path / "elsewhere.mp4"
    src.write_bytes(b"x" * old.st_size)
    os.utime(src, ns=(old.st_mtime_ns, old.st_mtime_ns))
    res = asyncio.run(runner._store_asset("demo", {"kind": "final_render", "src": str(src)}))

    assert json.loads(res["content"][0]["text"])["project_relative"] == "renders/final.mp4"
    assert published.read_bytes() == b"x" * old.st_size
    assert len(list((projects / "demo" / "renders").glob("final*.mp4"))) == 1
    assert not (projects / "demo" / "renders" / FINAL_RECEIPT_NAME).exists()
    assert final_render_status(projects, "demo")["current"] is False


def test_store_asset_does_not_block_the_event_loop(tmp_path):
    """The publisher waits on project_lock for as long as a render holds it. Called
    directly from the async handler that would freeze the SSE stream and the whole turn."""
    from lib.project import project_lock

    store, projects = _real_store(tmp_path)
    runner = _runner(tmp_path, store, projects)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"vid")
    held, release = threading.Event(), threading.Event()

    def hold_the_lock():
        with project_lock(projects, "demo"):
            held.set()
            release.wait(timeout=5)

    t = threading.Thread(target=hold_the_lock, daemon=True)
    t.start()
    assert held.wait(timeout=5)

    async def go():
        task = asyncio.create_task(runner._store_asset("demo", {"kind": "final_render", "src": str(src)}))
        ticks = 0
        while not task.done() and ticks < 100:
            await asyncio.sleep(0.005)
            ticks += 1
        assert ticks > 2, "the loop never got a turn — the publisher blocked it"
        assert not task.done()
        release.set()
        return await asyncio.wait_for(task, timeout=5)

    res = asyncio.run(go())
    t.join(timeout=5)
    assert json.loads(res["content"][0]["text"])["project_relative"] == "renders/final.mp4"


def test_run_render_reports_supersede_in_turn_and_consumes_it(tmp_path):
    """Superseded is terminal, so the waiter returns instead of timing out — and marks it
    consumed, or the next turn's resume note would report the same supersede again."""
    store = FakeStore([{"status": "running"}, {"status": "superseded"}])
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None, render_store=store, render_poll_interval_s=0.0)
    res = asyncio.run(runner._run_render("proj", {"edit_decisions": VALID_ED}))
    payload = _payload(res)
    assert payload["success"] is False
    assert "superseded" in payload["error"]
    assert store.consumed == ["job1"]


def test_resume_note_surfaces_a_superseded_agent_render(tmp_path):
    """active_job_for() returns the EDITOR job that displaced it, so without
    latest_unconsumed_agent_job the agent is simply never told."""
    store = RenderJobStore(tmp_path / "projects")
    store._jobs["ja"] = {
        "job_id": "ja",
        "project_id": "p",
        "origin": "agent",
        "consumed": False,
        "status": "superseded",
    }
    store._jobs["jb"] = {"job_id": "jb", "project_id": "p", "origin": "editor", "status": "done"}
    store._active_by_project["p"] = "jb"
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None, render_store=store)

    note = runner._render_resume_note("p")
    assert note and "SUPERSEDED" in note and "ja" in note
    assert runner._render_resume_note("p") is None  # one-shot


def test_qa_gate_one_liner_fails_a_stale_pair(tmp_path):
    """The instagram-fast-reel QA director runs exactly this before it may `pass`; it hands
    the user renders/final.mp4 AND the live edit_decisions.json, the two things that can
    disagree."""
    import subprocess

    store, projects = _real_store(tmp_path)
    runner = _runner(tmp_path, store, projects)
    asyncio.run(runner._run_render("demo", {"edit_decisions": VALID_ED}))

    def gate():
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json;from lib.project import final_render_status;"
                f"print(json.dumps(final_render_status({str(projects)!r}, 'demo')))",
            ],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            check=True,
        )
        return json.loads(out.stdout)

    assert gate()["current"] is True
    doc_file = projects / "demo" / "artifacts" / "edit_decisions.json"
    doc_file.write_text(json.dumps({**VALID_ED, "cuts": []}))  # user trims in the editor
    assert gate()["current"] is False


# --- deny guard -----------------------------------------------------------


def test_bash_uses_videocompose_render_detects_signature():
    cmd = (
        "python3 << EOF\nfrom tools.video.video_compose import VideoCompose\n"
        'VideoCompose().execute({"operation": "render_proxies", "edit_decisions": ed})\nEOF'
    )
    assert bash_uses_videocompose_render(cmd) is True
    out = asyncio.run(make_pre_tool_use_hook(None)({"tool_name": "Bash", "tool_input": {"command": cmd}}, "t", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_other_videocompose_ops_not_denied():
    cmd = "python3 -c \"from tools.video.video_compose import VideoCompose; VideoCompose().execute({'operation': 'compose'})\""
    assert bash_uses_videocompose_render(cmd) is False
    assert decide_tool("Bash", {"command": cmd}).action == ACTION_ALLOW


def test_plain_ffmpeg_still_allowed():
    assert decide_tool("Bash", {"command": "ffmpeg -i a.mp4 b.mp4"}).action == ACTION_ALLOW


def test_render_mcp_tool_allowed_by_policy():
    # One SDK wildcard replaces the old `startswith("mcp__mc__")` branch.
    assert "mcp__mc__*" in AGENT_AUTO_ALLOWED_TOOLS


def test_the_hook_maps_a_route_violation_to_a_deny_decision():
    cmd = 'from tools.video.video_compose import VideoCompose\nVideoCompose().execute({"operation": "render_proxies"})'
    out = asyncio.run(make_pre_tool_use_hook(None)({"tool_name": "Bash", "tool_input": {"command": cmd}}, "t", None))
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    assert "render" in spec["permissionDecisionReason"].lower()


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
        def __init__(self, repo_root, projects_dir=None, render_store=None):
            self.render_store = render_store
            captured["render_store"] = render_store
            captured["projects_dir"] = projects_dir

        async def run_turn(self, project_id, message, on_event=None, session_id=None):
            if on_event is not None:
                await on_event({"type": "result", "is_error": False})

    monkeypatch.setattr(app_mod, "auth_configured", lambda: True)
    monkeypatch.setattr(app_mod, "AgentRunner", FakeRunner)

    create_project(tmp_path / "projects", "Sky", "animated-explainer")
    app = app_mod.create_app(
        projects_dir=tmp_path / "projects",
        capabilities_provider=lambda: {
            "composition_runtimes": {},
            "capabilities": [],
            "setup_offers": [],
            "runtime_warnings": [],
        },
    )
    client = TestClient(app)
    r = client.post("/api/projects/sky/chat", json={"message": "hi"})
    assert r.status_code == 200, r.text
    assert captured["render_store"] is app.state.render_store
    assert app.state.agent_runner.render_store is app.state.render_store
