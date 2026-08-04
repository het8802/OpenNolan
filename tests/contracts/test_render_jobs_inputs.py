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

from lib.project import FINAL_RECEIPT_NAME, canonical_doc_hash
from server.render_jobs import RenderJobStore


class FakeVC:
    """Stub VideoCompose: records execute() inputs, writes the output file on success.

    Each call writes DISTINCT bytes so a test can tell which job's render ended up as the
    published deliverable."""

    def __init__(self, *, succeed=True, data=None, gate=None):
        self.calls: list[dict] = []
        self.succeed = succeed
        self.data = data if data is not None else {}
        self.gate = gate  # optional threading.Event to block inside execute()

    def execute(self, inputs):
        self.calls.append(inputs)
        n = len(self.calls)
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if self.succeed:
            out = Path(inputs["output_path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(f"render-{n}".encode())
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


def _wait_calls(vc, n, *, timeout=5):
    """Wait until the renderer has actually been ENTERED n times. Status "running" is set
    before the project lock is taken, so it does not mean "inside execute()"."""
    deadline = time.time() + timeout
    while time.time() < deadline and len(vc.calls) < n:
        time.sleep(0.01)
    return len(vc.calls)


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
    # j1 lands in the TERMINAL "superseded" state rather than sitting at running forever
    # (which is what hid it from both the poller and the agent's resume note).
    assert store.status(j1)["status"] == "superseded"
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


def test_normalize_output_path_is_confined_to_the_renders_subtree(tmp_path):
    """Staying "inside the project" was not enough: it let a render overwrite a SOURCE
    asset or an artifact with the assembled video."""
    store = _store(tmp_path)
    proj = (tmp_path / "projects" / "demo").resolve()
    norm = store._normalize_output_path
    for hostile in ("assets/video/source.mp4", "artifacts/edit_decisions.json",
                    "project.json", "hf/renders/scene1.mp4"):
        assert norm("demo", hostile, "final.mp4") == proj / "renders" / "final.mp4", hostile
    # A DESCENDANT of renders/ is not safe either: renders/proxies/ is the content-keyed
    # per-scene cache, so an assembled reel dropped at a proxy's path would be trusted as
    # that scene's clip on the next render.
    for managed in ("renders/proxies/scene.deadbeef.mp4", "renders/.final_review_frames/f0.mp4",
                    "renders/sub/x.mp4"):
        assert norm("demo", managed, "final.mp4") == proj / "renders" / "final.mp4", managed


# --- the publish route (OPN-30) -------------------------------------------

ED = {"version": "1.0", "render_runtime": "ffmpeg", "renderer_family": "x", "cuts": []}


def _renders(tmp_path):
    return tmp_path / "projects" / "demo" / "renders"


def _receipt(tmp_path):
    return _renders(tmp_path) / FINAL_RECEIPT_NAME


def _parts(tmp_path):
    return sorted(p.name for p in _renders(tmp_path).glob("*.part.mp4"))


@pytest.mark.parametrize("output_path", [None, "renders/final.mp4"])
def test_agent_render_publishes_canonically(tmp_path, output_path):
    """Omitted output_path used to mint agent_render_<job>.mp4 and never touch final.mp4 —
    the reproduced failure. Both spellings now publish, with a receipt."""
    store = _store(tmp_path)
    inputs = {"edit_decisions": ED}
    if output_path:
        inputs["output_path"] = output_path
    st = _wait(store, store.start_with_inputs("demo", inputs))

    assert st["status"] == "done", st
    assert st["output_path"] == "renders/final.mp4"
    assert (_renders(tmp_path) / "final.mp4").is_file()
    assert json.loads(_receipt(tmp_path).read_text())["doc_hash"] == canonical_doc_hash(ED)
    assert _parts(tmp_path) == []


def test_persist_edit_decisions_commits_the_doc_only_when_asked(tmp_path):
    doc_file = tmp_path / "projects" / "demo" / "artifacts" / "edit_decisions.json"
    store = _store(tmp_path)

    _wait(store, store.start_with_inputs("demo", {"edit_decisions": ED}))
    assert not doc_file.exists()          # no flag -> receipt only, disk untouched

    _wait(store, store.start_with_inputs(
        "demo", {"edit_decisions": ED, "persist_edit_decisions": True}))
    assert json.loads(doc_file.read_text()) == ED


def test_non_final_output_path_writes_directly_with_no_receipt(tmp_path):
    """A live pipeline contract: animation-talking-head-50-50 renders a genuine
    INTERMEDIATE to renders/overlay_raw.mp4. It must not claim to be the deliverable, and
    it must not disturb the one that is."""
    store = _store(tmp_path)
    _wait(store, store.start_with_inputs("demo", {"edit_decisions": ED}))
    final_bytes = (_renders(tmp_path) / "final.mp4").read_bytes()
    receipt_before = _receipt(tmp_path).read_bytes()

    st = _wait(store, store.start_with_inputs(
        "demo", {"edit_decisions": ED, "output_path": "renders/overlay_raw.mp4"}))

    assert st["status"] == "done" and st["output_path"] == "renders/overlay_raw.mp4"
    assert (_renders(tmp_path) / "overlay_raw.mp4").is_file()
    assert (_renders(tmp_path) / "final.mp4").read_bytes() == final_bytes
    assert _receipt(tmp_path).read_bytes() == receipt_before


def test_hostile_output_path_falls_back_and_leaves_the_target_alone(tmp_path):
    store = _store(tmp_path)
    source = tmp_path / "projects" / "demo" / "assets" / "video" / "source.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"precious-original-footage")

    st = _wait(store, store.start_with_inputs(
        "demo", {"edit_decisions": ED, "output_path": "assets/video/source.mp4"}))

    assert st["output_path"] == "renders/final.mp4"
    assert source.read_bytes() == b"precious-original-footage"


def test_failed_render_leaves_the_previous_deliverable_intact(tmp_path):
    store = _store(tmp_path)
    _wait(store, store.start_with_inputs("demo", {"edit_decisions": ED}))
    good = (_renders(tmp_path) / "final.mp4").read_bytes()
    receipt_before = _receipt(tmp_path).read_bytes()

    store._tool = FakeVC(succeed=False)
    st = _wait(store, store.start_with_inputs("demo", {"edit_decisions": ED}))

    assert st["status"] == "failed"
    assert (_renders(tmp_path) / "final.mp4").read_bytes() == good   # byte-for-byte
    assert _receipt(tmp_path).read_bytes() == receipt_before
    assert _parts(tmp_path) == []                                   # no litter


def test_editor_render_publishes_canonically(tmp_path):
    """The editor's Render button used to add an editor_preview_<job>.mp4 tile per click."""
    store = _store(tmp_path)
    doc_file = tmp_path / "projects" / "demo" / "artifacts" / "edit_decisions.json"
    doc_file.parent.mkdir(parents=True)
    doc_file.write_text(json.dumps(ED))
    before = doc_file.read_bytes()

    st = _wait(store, store.start("demo"))

    assert st["status"] == "done" and st["output_path"] == "renders/final.mp4"
    assert json.loads(_receipt(tmp_path).read_text())["doc_hash"] == canonical_doc_hash(ED)
    # The editor route NEVER writes the doc back: autosave runs during a render, so doing
    # so would overwrite edits the user made while waiting.
    assert doc_file.read_bytes() == before
    assert sorted(p.name for p in _renders(tmp_path).iterdir()) == [FINAL_RECEIPT_NAME, "final.mp4"]


def test_a_superseded_job_never_invokes_the_renderer(tmp_path):
    """The lock makes a queue of stale jobs cheap: each re-checks on acquiring it and bails
    instead of burning a full render nobody will read."""
    gate = threading.Event()
    vc = FakeVC(gate=gate)
    store = _store(tmp_path, vc)
    a = store.start_with_inputs("demo", {"edit_decisions": ED})
    assert _wait_calls(vc, 1) == 1               # a is INSIDE execute(), holding the lock
    b = store.start_with_inputs("demo", {"edit_decisions": ED})
    c = store.start_with_inputs("demo", {"edit_decisions": ED})   # supersedes b
    gate.set()

    assert _wait(store, c)["status"] == "done"
    assert _wait_status(store, b, "superseded")["status"] == "superseded"
    # a rendered (it was already inside) then declined to publish; c rendered. b, queued
    # behind the lock and superseded before reaching it, never called the renderer at all.
    assert len(vc.calls) == 2, vc.calls
    assert _parts(tmp_path) == []


def test_a_job_superseded_mid_render_does_not_publish(tmp_path):
    """The race the commit guard closes: A finishes rendering AFTER B became the active
    job. Checking supersede and replacing final.mp4 as two steps would let A publish over
    B; they are one critical section, so A publishes nothing."""
    gate = threading.Event()
    vc = FakeVC(gate=gate)
    store = _store(tmp_path, vc)
    a = store.start_with_inputs("demo", {"edit_decisions": ED})
    assert _wait_calls(vc, 1) == 1                                # A is mid-render
    b = store.start_with_inputs("demo", {"edit_decisions": ED})   # A is now superseded
    gate.set()

    assert _wait(store, b)["status"] == "done"
    assert _wait_status(store, a, "superseded")["status"] == "superseded"
    assert store.status(a).get("output_path") is None
    # B's render is what got published (each FakeVC call writes distinct bytes).
    assert (_renders(tmp_path) / "final.mp4").read_bytes() == b"render-2"
    assert _parts(tmp_path) == []


def test_latest_unconsumed_agent_job_finds_the_superseded_one(tmp_path):
    """active_job_for() cannot: by definition something newer displaced it. Without this
    the agent is never told, and its in-turn waiter just times out."""
    store = _store(tmp_path)
    store._jobs["ja"] = {"job_id": "ja", "project_id": "demo", "origin": "agent",
                         "consumed": False, "status": "superseded"}
    store._jobs["jb"] = {"job_id": "jb", "project_id": "demo", "origin": "editor",
                         "status": "done"}
    store._active_by_project["demo"] = "jb"

    assert store.active_job_for("demo")["job_id"] == "jb"
    found = store.latest_unconsumed_agent_job("demo")
    assert found["job_id"] == "ja" and found["status"] == "superseded"
    store.mark_consumed("ja")
    assert store.latest_unconsumed_agent_job("demo") is None      # one-shot


def test_latest_unconsumed_agent_job_prefers_the_newest_and_skips_running(tmp_path):
    store = _store(tmp_path)
    for jid, status in (("j1", "done"), ("j2", "failed"), ("j3", "running")):
        store._jobs[jid] = {"job_id": jid, "project_id": "demo", "origin": "agent",
                            "consumed": False, "status": status}
    assert store.latest_unconsumed_agent_job("demo")["job_id"] == "j2"   # newest TERMINAL
    store._jobs["other"] = {"job_id": "other", "project_id": "elsewhere", "origin": "agent",
                            "consumed": False, "status": "done"}
    assert store.latest_unconsumed_agent_job("demo")["job_id"] == "j2"   # project-scoped


def test_two_renders_of_one_project_share_a_stable_proxies_dir(tmp_path):
    """Proxy cache keys are content-based (tools/video/render_cache.py), so publishing to
    one stable filename doesn't cost a cache hit — the proxies dir must stay put."""
    vc = FakeVC(data={"n_scenes": 1, "n_rendered": 0, "n_cached": 1})
    store = _store(tmp_path, vc)
    _wait(store, store.start_with_inputs("demo", {"edit_decisions": ED}))
    st = _wait(store, store.start_with_inputs("demo", {"edit_decisions": ED}))
    assert vc.calls[0]["proxies_dir"] == vc.calls[1]["proxies_dir"]
    assert any("reused from cache" in w for w in st["warnings"])


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


def test_media_op_warns_when_it_writes_into_the_deliverable_folder(tmp_path, registry_sandbox):
    """The publisher CANNOT be the only writer under renders/ (run_media_op forwards an
    arbitrary input dict to any registry tool). So say so out loud instead of policing every
    tool schema — an unreceipted final.mp4 already reads as stale; this names the cause."""
    store = _store(tmp_path)
    renders = _renders(tmp_path)
    renders.mkdir(parents=True, exist_ok=True)
    landed = renders / "final.mp4"
    landed.write_bytes(b"written-behind-the-publishers-back")
    registry_sandbox._tools["fake_op"] = FakeOpTool("fake_op", data={"output": str(landed)})

    st = _wait(store, store.start_op("demo", "fake_op", {}))
    assert st["status"] == "done"
    assert any("renders/" in w for w in st["warnings"])

    # An op that writes anywhere else says nothing.
    elsewhere = tmp_path / "projects" / "demo" / "assets" / "video" / "cut.mp4"
    elsewhere.parent.mkdir(parents=True, exist_ok=True)
    elsewhere.write_bytes(b"x")
    registry_sandbox._tools["fake_op"] = FakeOpTool("fake_op", data={"output": str(elsewhere)})
    st = _wait(store, store.start_op("demo", "fake_op", {}))
    assert st["warnings"] is None
