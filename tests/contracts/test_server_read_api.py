"""Contract tests for the Mission Control read API (server/).

Read-only endpoints over the existing libs. Uses FastAPI's TestClient. The
capabilities provider is stubbed so tests stay fast and deterministic (the
real provider imports every tool module); one explicit caching test verifies
the provider is invoked at most once.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from lib.checkpoint import write_checkpoint
from lib.project import create_project
from server.app import create_app

PIPELINE = "animated-explainer"

STUB_CAPS = {
    "composition_runtimes": {"ffmpeg": True, "remotion": True, "hyperframes": True},
    "capabilities": [{"capability": "tts", "configured": 1, "total": 3}],
    "setup_offers": [],
    "runtime_warnings": [],
}


def _client(tmp_path, capabilities_provider=lambda: STUB_CAPS):
    app = create_app(projects_dir=tmp_path / "projects", capabilities_provider=capabilities_provider)
    return TestClient(app)


def test_health(tmp_path):
    r = _client(tmp_path).get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_list_pipelines_includes_animated_explainer(tmp_path):
    r = _client(tmp_path).get("/api/pipelines")
    assert r.status_code == 200
    by_name = {p["name"]: p for p in r.json()["pipelines"]}
    assert PIPELINE in by_name
    stages = by_name[PIPELINE]["stages"]
    assert "research" in stages and "proposal" in stages
    assert "error" not in by_name[PIPELINE]


def test_pipeline_detail_and_404(tmp_path):
    c = _client(tmp_path)
    r = c.get(f"/api/pipelines/{PIPELINE}")
    assert r.status_code == 200
    assert r.json()["name"] == PIPELINE

    r = c.get("/api/pipelines/does-not-exist")
    assert r.status_code == 404


def test_list_projects_filters_junk(tmp_path):
    projects = tmp_path / "projects"
    create_project(projects, "Real One", PIPELINE)
    (projects / "_analysis").mkdir(parents=True)        # scratch dir
    (projects / ".DS_Store").write_text("noise")        # stray file
    (projects / "legacy-no-manifest").mkdir()           # pre-manifest

    r = _client(tmp_path).get("/api/projects")
    assert r.status_code == 200
    ids = [p["project_id"] for p in r.json()["projects"]]
    assert ids == ["real-one"]


def test_project_state(tmp_path):
    projects = tmp_path / "projects"
    create_project(projects, "Sky Test", PIPELINE)
    write_checkpoint(projects, "sky-test", "research", "in_progress", {}, pipeline_type=PIPELINE)

    r = _client(tmp_path).get("/api/projects/sky-test/state")
    assert r.status_code == 200
    body = r.json()
    assert body["pipeline_type"] == PIPELINE
    by_stage = {s["stage"]: s for s in body["stages"]}
    assert by_stage["research"]["status"] == "in_progress"
    assert by_stage["proposal"]["status"] == "pending"  # no checkpoint yet
    # research is in_progress (not completed), so it's still the next stage.
    assert body["next_stage"] == "research"


def test_project_state_404(tmp_path):
    r = _client(tmp_path).get("/api/projects/nope/state")
    assert r.status_code == 404


def test_project_state_reports_detected_runtime(tmp_path):
    import json

    projects = tmp_path / "projects"
    create_project(projects, "Sky Runtime", PIPELINE)
    art = projects / "sky-runtime" / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    # render_report.json is the authoritative source: workspace.runtime.
    (art / "render_report.json").write_text(json.dumps({"workspace": {"runtime": "remotion"}}))

    body = _client(tmp_path).get("/api/projects/sky-runtime/state").json()
    assert body["runtime"] == "remotion"


def test_project_state_runtime_none_when_undecided(tmp_path):
    projects = tmp_path / "projects"
    create_project(projects, "Sky Blank", PIPELINE)
    body = _client(tmp_path).get("/api/projects/sky-blank/state").json()
    assert body["runtime"] is None


def test_detect_runtime_priority_and_fallbacks(tmp_path):
    import json

    from server.state import detect_runtime

    projects = tmp_path / "projects"
    proj = projects / "p1"
    art = proj / "artifacts"
    art.mkdir(parents=True, exist_ok=True)

    # Directory fallback only.
    (proj / "hyperframes").mkdir()
    assert detect_runtime(projects, "p1") == "hyperframes"

    # scene_plan beats the directory heuristic.
    (art / "scene_plan.json").write_text(json.dumps({"render_runtime": "ffmpeg"}))
    assert detect_runtime(projects, "p1") == "ffmpeg"

    # render_report wins over everything.
    (art / "render_report.json").write_text(json.dumps({"workspace": {"runtime": "remotion"}}))
    assert detect_runtime(projects, "p1") == "remotion"

    # Unknown values are ignored (not surfaced as a runtime).
    (art / "render_report.json").write_text(json.dumps({"workspace": {"runtime": "bogus"}}))
    assert detect_runtime(projects, "p1") == "ffmpeg"  # falls back to scene_plan


def test_capabilities_cached(tmp_path):
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return STUB_CAPS

    c = _client(tmp_path, capabilities_provider=provider)
    r1 = c.get("/api/capabilities")
    r2 = c.get("/api/capabilities")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == STUB_CAPS
    # Discovered at most once, then served from cache.
    assert calls["n"] == 1


def test_capabilities_discovery_failure_is_surfaced_not_500(tmp_path):
    def boom():
        raise RuntimeError("registry exploded")

    r = _client(tmp_path, capabilities_provider=boom).get("/api/capabilities")
    assert r.status_code == 200
    assert "error" in r.json()


# ── assets listing: kinds / renders / agent_renders ────────────────────────


def test_list_assets_groups_kinds(tmp_path):
    projects = tmp_path / "projects"
    create_project(projects, "Asset Proj", PIPELINE)
    proj = projects / "asset-proj"
    (proj / "assets" / "images" / "a.png").write_bytes(b"img")
    (proj / "assets" / "video" / "clip.mp4").write_bytes(b"vid")
    (proj / "assets" / "music" / "bed.mp3").write_bytes(b"mus")
    (proj / "assets" / "audio" / "whoosh.wav").write_bytes(b"sfx")

    body = _client(tmp_path).get("/api/projects/asset-proj/assets").json()
    names = {k: [f["name"] for f in v] for k, v in body["kinds"].items()}
    assert names["images"] == ["a.png"]
    assert names["video"] == ["clip.mp4"]
    assert names["music"] == ["bed.mp3"]
    assert names["audio"] == ["whoosh.wav"]
    # agent_renders is always present (empty when there's no hf/renders).
    assert body["agent_renders"] == []


def test_list_assets_agent_renders_from_hf_renders(tmp_path):
    """The agent's HyperFrames clips under hf/renders/ surface as `agent_renders`,
    kept distinct from the editor's final output in renders/."""
    projects = tmp_path / "projects"
    create_project(projects, "HF Proj", PIPELINE)
    proj = projects / "hf-proj"

    hf_renders = proj / "hf" / "renders"
    hf_renders.mkdir(parents=True)
    (hf_renders / "anim_intro.mp4").write_bytes(b"clip")
    (hf_renders / "ov_caption.mov").write_bytes(b"overlay")   # .mov alpha overlay counts too
    (hf_renders / "notes.txt").write_text("not a video")      # non-video is ignored
    (hf_renders / ".hidden.mp4").write_bytes(b"dotfile")      # dotfiles ignored

    # The editor's final output lives in renders/ — must NOT leak into agent_renders.
    # (create_project already made renders/, so don't re-create it.)
    (proj / "renders" / "final.mp4").write_bytes(b"final")
    # Render-engine internals under renders/ MUST NOT surface as "Final render":
    # the content-keyed proxy cache and the review-frame scratch dir are not
    # deliverables. (Regression guard: the renders bucket used to rglob these in.)
    proxies = proj / "renders" / "proxies"
    proxies.mkdir(parents=True)
    (proxies / "b1.deadbeef.mp4").write_bytes(b"proxy")
    (proxies / "b2.cafef00d.mp4").write_bytes(b"proxy")
    (proj / "renders" / ".final_review_frames").mkdir(parents=True)
    (proj / "renders" / ".final_review_frames" / "f0.png").write_bytes(b"png")

    body = _client(tmp_path).get("/api/projects/hf-proj/assets").json()

    ar_names = sorted(f["name"] for f in body["agent_renders"])
    assert ar_names == ["anim_intro.mp4", "ov_caption.mov"]
    # Paths are project-relative and point under hf/renders/ (so /file can serve them).
    assert all(f["path"].startswith("hf/renders/") for f in body["agent_renders"])
    assert all("mtime" in f and "size_bytes" in f for f in body["agent_renders"])

    # The final output stays in the separate `renders` bucket, not agent_renders.
    # Only the top-level deliverable — NOT the proxy cache — appears here.
    assert [f["name"] for f in body["renders"]] == ["final.mp4"]
    assert "final.mp4" not in ar_names


def test_get_file_serves_hf_renders_clip(tmp_path):
    """get_file already serves anything inside the project dir, including hf/renders/."""
    projects = tmp_path / "projects"
    create_project(projects, "Serve Proj", PIPELINE)
    clip = projects / "serve-proj" / "hf" / "renders" / "anim.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"\x00\x01bytes")

    r = _client(tmp_path).get(
        "/api/projects/serve-proj/file", params={"path": "hf/renders/anim.mp4"}
    )
    assert r.status_code == 200
    assert r.content == b"\x00\x01bytes"
