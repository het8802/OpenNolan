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
