"""Contract tests for RenderJobStore.start_with_inputs — the agent's render path.

Unlike the editor's start() (which reads the timeline from disk), start_with_inputs
takes CALLER-supplied inputs and forwards proposal_packet/hdr_policy. We stub
VideoCompose (store._tool) so no ffmpeg/network runs.
"""

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from server.render_jobs import RenderJobStore


class FakeVC:
    """Stub VideoCompose: records execute() inputs, writes the output file on success."""

    def __init__(self, *, succeed=True, data=None, gate=None):
        self.calls: list[dict] = []
        self.succeed = succeed
        self.data = data if data is not None else {}
        self.gate = gate  # optional threading.Event to block inside execute()

    def execute(self, inputs):
        self.calls.append(inputs)
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if self.succeed:
            out = Path(inputs["output_path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x00\x00")
            return SimpleNamespace(success=True, data=self.data, error=None)
        return SimpleNamespace(success=False, data=None, error="boom")


def _store(tmp_path, vc=None):
    projects = tmp_path / "projects"
    (projects / "demo").mkdir(parents=True)
    store = RenderJobStore(projects)
    store._tool = vc if vc is not None else FakeVC()
    return store


def _wait(store, jid, *, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = store.status(jid)
        if st and st["status"] in ("done", "failed"):
            return st
        time.sleep(0.01)
    return store.status(jid)


def _wait_status(store, jid, status, *, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = store.status(jid)
        if st and st["status"] == status:
            return st
        time.sleep(0.01)
    return store.status(jid)


def test_start_with_inputs_happy_path(tmp_path):
    store = _store(tmp_path, FakeVC(data={
        "n_scenes": 1, "n_rendered": 1, "n_cached": 0, "final_review_status": "pass"}))
    jid = store.start_with_inputs("demo", {
        "edit_decisions": {"renderer_family": "explainer-data", "cuts": []},
        "asset_manifest": {"assets": []},
    })
    st = _wait(store, jid)
    assert st["status"] == "done", st
    assert st["output_path"].startswith("renders/"), st["output_path"]
    assert st["origin"] == "agent"
    assert st["final_review_status"] == "pass"


def test_forwards_proposal_packet_and_hdr_policy_only_when_present(tmp_path):
    vc = FakeVC()
    store = _store(tmp_path, vc)
    jid = store.start_with_inputs("demo", {
        "edit_decisions": {"renderer_family": "x", "cuts": []},
        "proposal_packet": {"production_plan": {"render_runtime": "ffmpeg"}},
        "hdr_policy": "preserve",
    })
    _wait(store, jid)
    call = vc.calls[0]
    assert call["operation"] == "render_proxies"
    assert call["proposal_packet"] == {"production_plan": {"render_runtime": "ffmpeg"}}
    assert call["hdr_policy"] == "preserve"

    # When omitted, the keys are absent (VideoCompose keeps its own defaults).
    jid2 = store.start_with_inputs("demo", {"edit_decisions": {"renderer_family": "x", "cuts": []}})
    _wait(store, jid2)
    call2 = vc.calls[1]
    assert "proposal_packet" not in call2
    assert "hdr_policy" not in call2


def test_missing_edit_decisions_fails(tmp_path):
    store = _store(tmp_path)
    jid = store.start_with_inputs("demo", {})
    st = _wait(store, jid)
    assert st["status"] == "failed"
    assert "edit_decisions" in st["error"]


def test_supersede_drops_first_result(tmp_path):
    gate = threading.Event()
    vc = FakeVC(gate=gate)
    store = _store(tmp_path, vc)
    ed = {"edit_decisions": {"renderer_family": "x", "cuts": []}}
    j1 = store.start_with_inputs("demo", ed)
    _wait_status(store, j1, "running")            # j1 is now blocked inside execute()
    j2 = store.start_with_inputs("demo", ed)      # supersedes j1 (newest wins)
    gate.set()                                    # release both threads
    st2 = _wait(store, j2)
    assert st2["status"] == "done", st2
    # j1's done-update was dropped silently (superseded) — it never advanced past running.
    assert store.status(j1)["status"] == "running"
    assert store.active_job_for("demo")["job_id"] == j2


def test_normalize_output_path_traversal_guard(tmp_path):
    store = _store(tmp_path)
    proj = (tmp_path / "projects" / "demo").resolve()
    norm = store._normalize_output_path
    assert norm("demo", "projects/demo/renders/final.mp4", "fb.mp4") == proj / "renders" / "final.mp4"
    assert norm("demo", "renders/x.mp4", "fb.mp4") == proj / "renders" / "x.mp4"
    assert norm("demo", None, "fb.mp4") == proj / "renders" / "fb.mp4"
    # escapes / cross-project / absolute-outside -> fall back under renders/
    assert norm("demo", "../../etc/passwd", "fb.mp4") == proj / "renders" / "fb.mp4"
    assert norm("demo", "/etc/passwd", "fb.mp4") == proj / "renders" / "fb.mp4"
    assert norm("demo", "projects/other/renders/x.mp4", "fb.mp4") == proj / "renders" / "fb.mp4"


def test_active_job_for_and_mark_consumed(tmp_path):
    store = _store(tmp_path)
    assert store.active_job_for("demo") is None
    jid = store.start_with_inputs("demo", {"edit_decisions": {"renderer_family": "x", "cuts": []}})
    _wait(store, jid)
    job = store.active_job_for("demo")
    assert job["job_id"] == jid and job["consumed"] is False
    store.mark_consumed(jid)
    assert store.active_job_for("demo")["consumed"] is True


# --- start_op (generalized: run any registry tool on a job thread) --------

class FakeOpTool:
    """Stub registry tool: returns success/failure without touching ffmpeg."""

    def __init__(self, name, *, succeed=True, data=None, error=None):
        self.name = name
        self._succeed = succeed
        self._data = data or {}
        self._error = error

    def execute(self, inputs):
        if self._succeed:
            return SimpleNamespace(success=True, data=self._data, error=None)
        return SimpleNamespace(success=False, data=None, error=self._error or "op failed")


@pytest.fixture
def registry_sandbox():
    """Snapshot/restore the process-wide registry so a test can inject a fake tool and
    mark 'tools' as already-discovered (skips the heavy real import) without bleeding."""
    from tools.tool_registry import registry
    tools = dict(registry._tools)
    pkgs = set(registry._discovered_packages)
    registry._discovered_packages.add("tools")  # ensure_discovered() no-ops -> no real import
    try:
        yield registry
    finally:
        registry._tools.clear(); registry._tools.update(tools)
        registry._discovered_packages.clear(); registry._discovered_packages.update(pkgs)


def test_start_op_runs_registry_tool_on_thread(tmp_path, registry_sandbox):
    registry_sandbox._tools["fake_cut"] = FakeOpTool("fake_cut", data={"output": "/tmp/out.mp4", "x": 1})
    store = _store(tmp_path)
    jid = store.start_op("demo", "fake_cut", {"input_path": "a.mov"})
    st = _wait(store, jid)
    assert st["status"] == "done", st
    assert st["output_path"] == "/tmp/out.mp4"
    assert st["result_data"] == {"output": "/tmp/out.mp4", "x": 1}
    assert st["origin"] == "agent_op"
    assert st["tool_name"] == "fake_cut"


def test_start_op_failure_records_error(tmp_path, registry_sandbox):
    registry_sandbox._tools["fake_cut"] = FakeOpTool("fake_cut", succeed=False, error="ffmpeg exploded")
    store = _store(tmp_path)
    jid = store.start_op("demo", "fake_cut", {})
    st = _wait(store, jid)
    assert st["status"] == "failed"
    assert "ffmpeg exploded" in st["error"]


def test_start_op_unknown_tool_fails_cleanly(tmp_path, registry_sandbox):
    store = _store(tmp_path)
    jid = store.start_op("demo", "does_not_exist", {})
    st = _wait(store, jid)
    assert st["status"] == "failed"
    assert "does_not_exist" in st["error"]
