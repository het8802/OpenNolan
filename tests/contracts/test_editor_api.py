"""Contract tests for the manual-editor API (edit_decisions read/write + render jobs).

Mirrors the existing server contract-test style: real file I/O under tmp_path, TestClient,
`create_app(projects_dir=...)`. The render runner is exercised with a stubbed VideoCompose
so no real ffmpeg/agent runs — these tests assert the WIRING and the governance gate.

The ★ test is `put_invalid_leaves_file`: a schema-invalid save must be rejected (422) and
must NOT corrupt the on-disk artifact.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from lib.project import create_project  # noqa: E402
from server.app import create_app  # noqa: E402
from server.render_jobs import RenderJobStore  # noqa: E402
from tools.base_tool import ToolResult  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")

VALID_ED = {
    "version": "1.0",
    "render_runtime": "ffmpeg",
    "cuts": [{"id": "c1", "source": "clip.mp4", "in_seconds": 0, "out_seconds": 1}],
}


def _client(tmp_path):
    projects = tmp_path / "projects"
    app = create_app(projects_dir=projects, capabilities_provider=lambda: {})
    return TestClient(app), projects


def _seed(projects: Path, *, with_ed: bool = False) -> str:
    create_project(projects, "Reel", "clip-factory")
    pid = "reel"
    if with_ed:
        ed = projects / pid / "artifacts" / "edit_decisions.json"
        ed.parent.mkdir(parents=True, exist_ok=True)
        ed.write_text(json.dumps(VALID_ED))
    return pid


# ── edit_decisions read/write ────────────────────────────────────────────

def test_get_edit_decisions_absent_returns_null(tmp_path):
    client, projects = _client(tmp_path)
    pid = _seed(projects)
    r = client.get(f"/api/projects/{pid}/edit_decisions")
    assert r.status_code == 200
    assert r.json()["content"] is None


def test_get_edit_decisions_present(tmp_path):
    client, projects = _client(tmp_path)
    pid = _seed(projects, with_ed=True)
    r = client.get(f"/api/projects/{pid}/edit_decisions")
    assert r.status_code == 200
    assert r.json()["content"]["render_runtime"] == "ffmpeg"


def test_put_valid_edit_decisions_writes(tmp_path):
    client, projects = _client(tmp_path)
    pid = _seed(projects)
    r = client.put(f"/api/projects/{pid}/edit_decisions", json=VALID_ED)
    assert r.status_code == 200, r.text
    on_disk = json.loads((projects / pid / "artifacts" / "edit_decisions.json").read_text())
    assert on_disk == VALID_ED


def test_put_invalid_edit_decisions_returns_422_and_leaves_file(tmp_path):
    """★ Regression class: an invalid save is rejected and the prior file is intact."""
    client, projects = _client(tmp_path)
    pid = _seed(projects, with_ed=True)
    invalid = {"version": "1.0", "cuts": []}  # missing render_runtime; empty cuts
    r = client.put(f"/api/projects/{pid}/edit_decisions", json=invalid)
    assert r.status_code == 422
    assert "render_runtime" in r.text or "cuts" in r.text or "required" in r.text.lower()
    # the previously-valid file must be untouched
    on_disk = json.loads((projects / pid / "artifacts" / "edit_decisions.json").read_text())
    assert on_disk == VALID_ED


def test_put_unknown_field_rejected(tmp_path):
    """additionalProperties:false — the UI must not be able to write unknown keys."""
    client, projects = _client(tmp_path)
    pid = _seed(projects)
    doc = dict(VALID_ED, bogus_field=123)
    r = client.put(f"/api/projects/{pid}/edit_decisions", json=doc)
    assert r.status_code == 422


def test_edit_decisions_404_unknown_project(tmp_path):
    client, projects = _client(tmp_path)
    assert client.get("/api/projects/nope/edit_decisions").status_code == 404
    assert client.put("/api/projects/nope/edit_decisions", json=VALID_ED).status_code == 404


# ── render jobs (stubbed VideoCompose) ─────────────────────────────────────

def _stub_ok(monkeypatch):
    class _FakeVC:
        def execute(self, inputs):
            out = Path(inputs["output_path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x00\x00")
            return ToolResult(success=True, data={"final_review_status": "pass"}, artifacts=[str(out)])
    monkeypatch.setattr(RenderJobStore, "_video_compose", lambda self: _FakeVC())


def _poll(client, pid, jid, timeout=5.0):
    deadline = time.time() + timeout
    st = {}
    while time.time() < deadline:
        st = client.get(f"/api/projects/{pid}/render/{jid}").json()
        if st.get("status") in ("done", "failed"):
            return st
        time.sleep(0.02)
    return st


def test_render_job_lifecycle_done(tmp_path, monkeypatch):
    _stub_ok(monkeypatch)
    client, projects = _client(tmp_path)
    pid = _seed(projects, with_ed=True)
    r = client.post(f"/api/projects/{pid}/render")
    assert r.status_code == 202
    jid = r.json()["job_id"]
    st = _poll(client, pid, jid)
    assert st["status"] == "done", st
    assert st["output_path"].startswith("renders/")


def test_render_job_failure_reports_error(tmp_path, monkeypatch):
    class _FailVC:
        def execute(self, inputs):
            return ToolResult(success=False, error="ffmpeg exploded")
    monkeypatch.setattr(RenderJobStore, "_video_compose", lambda self: _FailVC())
    client, projects = _client(tmp_path)
    pid = _seed(projects, with_ed=True)
    jid = client.post(f"/api/projects/{pid}/render").json()["job_id"]
    st = _poll(client, pid, jid)
    assert st["status"] == "failed"
    assert "ffmpeg exploded" in st["error"]


def test_render_without_edit_decisions_fails_clearly(tmp_path, monkeypatch):
    _stub_ok(monkeypatch)
    client, projects = _client(tmp_path)
    pid = _seed(projects)  # no edit_decisions
    jid = client.post(f"/api/projects/{pid}/render").json()["job_id"]
    st = _poll(client, pid, jid)
    assert st["status"] == "failed"
    assert "edit_decisions" in st["error"]


def test_render_supersede(tmp_path, monkeypatch):
    """A second render supersedes the first; the first's result is discarded."""
    gate = threading.Event()

    class _GatedVC:
        def execute(self, inputs):
            gate.wait(timeout=5)
            out = Path(inputs["output_path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x00\x00")
            return ToolResult(success=True, data={}, artifacts=[str(out)])
    monkeypatch.setattr(RenderJobStore, "_video_compose", lambda self: _GatedVC())

    client, projects = _client(tmp_path)
    pid = _seed(projects, with_ed=True)
    j1 = client.post(f"/api/projects/{pid}/render").json()["job_id"]
    j2 = client.post(f"/api/projects/{pid}/render").json()["job_id"]
    gate.set()
    st2 = _poll(client, pid, j2)
    assert st2["status"] == "done"
    # j1 was superseded before it could publish — terminal, and named as such
    st1 = client.get(f"/api/projects/{pid}/render/{j1}").json()
    assert st1["status"] == "superseded"


def test_render_injects_default_renderer_family(tmp_path, monkeypatch):
    """Regression (caught by the live smoke): video_compose's pre-compose gate REQUIRES
    renderer_family, but it's optional in the schema. The render job injects a default into
    the render-only copy (NOT persisted) and warns, so a preview isn't blocked."""
    captured = {}

    class _CapVC:
        def execute(self, inputs):
            captured["ed"] = inputs["edit_decisions"]
            out = Path(inputs["output_path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x00\x00")
            return ToolResult(success=True, data={}, artifacts=[str(out)])
    monkeypatch.setattr(RenderJobStore, "_video_compose", lambda self: _CapVC())

    client, projects = _client(tmp_path)
    pid = _seed(projects, with_ed=True)  # VALID_ED has NO renderer_family
    jid = client.post(f"/api/projects/{pid}/render").json()["job_id"]
    st = _poll(client, pid, jid)
    assert st["status"] == "done", st
    assert captured["ed"]["renderer_family"] == "social-reel"  # injected for the render
    assert any("renderer_family" in w for w in (st.get("warnings") or []))
    # injection is NOT persisted to the user's saved doc
    on_disk = json.loads((projects / pid / "artifacts" / "edit_decisions.json").read_text())
    assert "renderer_family" not in on_disk


def test_render_status_404_unknown_job(tmp_path):
    client, projects = _client(tmp_path)
    pid = _seed(projects, with_ed=True)
    assert client.get(f"/api/projects/{pid}/render/deadbeef").status_code == 404


# ── frame extraction ───────────────────────────────────────────────────────

def test_frame_path_traversal_blocked(tmp_path):
    client, projects = _client(tmp_path)
    pid = _seed(projects)
    r = client.get(f"/api/projects/{pid}/frame", params={"path": "../../../../etc/passwd", "t": 0})
    assert r.status_code == 400


@needs_ffmpeg
def test_frame_extracts_jpeg(tmp_path):
    client, projects = _client(tmp_path)
    pid = _seed(projects)
    renders = projects / pid / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    clip = renders / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=2:r=24",
                    "-pix_fmt", "yuv420p", str(clip)], capture_output=True, check=True)
    r = client.get(f"/api/projects/{pid}/frame", params={"path": "renders/clip.mp4", "t": 1.0})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert len(r.content) > 0


# ── source resolution + serving (scrub preview) ─────────────────────────────

def _seed_source(projects: Path, rel: str = "assets/video/clip.mp4", body: bytes = b"\x00\x01") -> str:
    """Seed a project with a (dummy) source file at `rel`; returns the project id."""
    pid = _seed(projects)
    f = projects / pid / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(body)
    return pid


def test_source_serves_project_relative_ref(tmp_path):
    client, projects = _client(tmp_path)
    pid = _seed_source(projects)
    r = client.get(f"/api/projects/{pid}/source", params={"ref": "assets/video/clip.mp4"})
    assert r.status_code == 200, r.text
    assert r.content == b"\x00\x01"


def test_source_resolves_asset_id_via_manifest(tmp_path):
    client, projects = _client(tmp_path)
    pid = _seed_source(projects, body=b"abc")
    # asset_manifest maps an id -> the project-relative path
    man = projects / pid / "artifacts" / "asset_manifest.json"
    man.parent.mkdir(parents=True, exist_ok=True)
    man.write_text(json.dumps({"assets": [{"id": "hero", "path": "assets/video/clip.mp4"}]}))
    r = client.get(f"/api/projects/{pid}/source", params={"ref": "hero"})
    assert r.status_code == 200
    assert r.content == b"abc"


def test_source_serves_shared_repo_asset_library(tmp_path):
    """SFX/kit audio lives in the repo's shared `assets/` (outside any project) and the renderer
    reads it from repo-root cwd; the preview must serve it too, or SFX play in the export but are
    silent in the editor. (Regression: the music bed — a project file — resolved, SFX did not.)"""
    client, projects = _client(tmp_path)
    pid = _seed(projects)
    shared = projects.parent / "assets" / "sfx" / "whoosh.wav"  # <repo>/assets/sfx/whoosh.wav
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_bytes(b"WAVE")
    man = projects / pid / "artifacts" / "asset_manifest.json"
    man.parent.mkdir(parents=True, exist_ok=True)
    man.write_text(json.dumps({"assets": [{"id": "sfx_whoosh", "path": "assets/sfx/whoosh.wav"}]}))
    r = client.get(f"/api/projects/{pid}/source", params={"ref": "sfx_whoosh"})
    assert r.status_code == 200, r.text
    assert r.content == b"WAVE"


def test_source_blocks_escape_outside_project(tmp_path):
    """A ref that resolves outside the project dir (traversal or absolute) is rejected."""
    client, projects = _client(tmp_path)
    pid = _seed_source(projects)
    assert client.get(f"/api/projects/{pid}/source",
                      params={"ref": "../../../../etc/passwd"}).status_code == 404
    assert client.get(f"/api/projects/{pid}/source",
                      params={"ref": "/etc/hosts"}).status_code == 404


def test_source_404_missing_and_unknown_project(tmp_path):
    client, projects = _client(tmp_path)
    pid = _seed(projects)  # no source file written
    assert client.get(f"/api/projects/{pid}/source",
                      params={"ref": "assets/video/missing.mp4"}).status_code == 404
    assert client.get("/api/projects/nope/source",
                      params={"ref": "assets/video/clip.mp4"}).status_code == 404


def test_source_meta_404_outside_project(tmp_path):
    client, projects = _client(tmp_path)
    pid = _seed_source(projects)
    assert client.get(f"/api/projects/{pid}/source_meta",
                      params={"ref": "/etc/hosts"}).status_code == 404


@needs_ffmpeg
def test_source_meta_reports_duration(tmp_path):
    client, projects = _client(tmp_path)
    pid = _seed(projects)
    clip = projects / pid / "assets" / "video" / "real.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=160x120:d=3:r=24",
                    "-pix_fmt", "yuv420p", str(clip)], capture_output=True, check=True)
    r = client.get(f"/api/projects/{pid}/source_meta", params={"ref": "assets/video/real.mp4"})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == "assets/video/real.mp4"
    assert abs(body["duration"] - 3.0) < 0.5
    assert body["width"] == 160 and body["height"] == 120
