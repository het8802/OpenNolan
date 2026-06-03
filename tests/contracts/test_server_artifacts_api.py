"""Contract tests for the artifact manifest + content endpoints.

  GET /api/projects/{id}/artifacts        -> manifest grouped by stage
  GET /api/projects/{id}/artifacts/{key}  -> one artifact's parsed content

Fixtures write raw checkpoint + standalone artifact files (rather than going
through the validating writer) on purpose: that's exactly what the defensive
reader must tolerate for legacy / schema-drifted projects, and it keeps the
tests free of schema gymnastics.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from lib.project import create_project
from server import artifacts as artifacts_mod
from server.app import create_app

PIPELINE = "animated-explainer"
STUB_CAPS = {"composition_runtimes": {}, "capabilities": [], "setup_offers": [], "runtime_warnings": []}


def _client(tmp_path):
    app = create_app(projects_dir=tmp_path / "projects", capabilities_provider=lambda: STUB_CAPS)
    return TestClient(app), tmp_path / "projects"


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _seed(projects: Path) -> str:
    """A project with: a completed research checkpoint embedding research_brief
    (no standalone file -> checkpoint-fallback path), a standalone scene_plan
    artifact with no checkpoint (-> stage attribution), and a root decision_log."""
    create_project(projects, "Sky", PIPELINE)
    proj = projects / "sky"
    _write(proj / "checkpoint_research.json", {
        "stage": "research", "status": "completed", "pipeline_type": PIPELINE,
        "human_approval_required": True, "human_approved": True,
        "timestamp": "2026-05-31T10:00:00+00:00",
        "review": {"verdict": "pass"},
        "artifacts": {"research_brief": {"topic": "cheap inference", "sources": [{"url": "http://x"}]}},
    })
    _write(proj / "artifacts" / "scene_plan.json", {
        "style_playbook": "greg-isenberg-product-explainer",
        "scenes": [{"id": "sc01", "description": "hook"}],
    })
    _write(proj / "decision_log.json", {
        "version": "1.0", "project_id": "sky",
        "decisions": [{"decision_id": "d-001", "selected": "remotion"},
                      {"decision_id": "d-002", "selected": "elevenlabs"}],
    })
    return "sky"


# --- manifest -------------------------------------------------------------

def test_list_artifacts_groups_by_stage(tmp_path):
    client, projects = _client(tmp_path)
    _seed(projects)

    r = client.get("/api/projects/sky/artifacts")
    assert r.status_code == 200
    body = r.json()
    assert body["pipeline_type"] == PIPELINE
    by_stage = {s["stage"]: s for s in body["stages"]}

    # research: completed, approval recorded, research_brief listed as canonical
    research = by_stage["research"]
    assert research["status"] == "completed"
    assert research["human_approved"] is True
    assert research["review"] == {"verdict": "pass"}
    keys = {a["key"]: a for a in research["artifacts"]}
    assert "research_brief" in keys
    assert keys["research_brief"]["canonical"] is True
    assert keys["research_brief"]["known"] is True

    # scene_plan: no checkpoint (pending) but the standalone file is attributed here
    scene = by_stage["scene_plan"]
    assert scene["status"] == "pending"
    assert any(a["key"] == "scene_plan" for a in scene["artifacts"])

    # cross-cutting decision_log summarized, not inlined into a stage
    assert body["decision_log"]["present"] is True
    assert body["decision_log"]["decision_count"] == 2


def test_list_artifacts_unknown_project_404(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/projects/ghost/artifacts").status_code == 404


# --- content --------------------------------------------------------------

def test_get_artifact_from_standalone_file(tmp_path):
    client, projects = _client(tmp_path)
    _seed(projects)
    r = client.get("/api/projects/sky/artifacts/scene_plan")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "file"
    assert body["stage"] == "scene_plan"
    assert body["content"]["style_playbook"] == "greg-isenberg-product-explainer"


def test_get_artifact_falls_back_to_checkpoint(tmp_path):
    client, projects = _client(tmp_path)
    _seed(projects)
    # research_brief has no standalone file — only the checkpoint embeds it.
    r = client.get("/api/projects/sky/artifacts/research_brief")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "checkpoint"
    assert body["stage"] == "research"
    assert body["content"]["topic"] == "cheap inference"


def test_get_decision_log_prefers_root(tmp_path):
    client, projects = _client(tmp_path)
    _seed(projects)
    r = client.get("/api/projects/sky/artifacts/decision_log")
    assert r.status_code == 200
    assert len(r.json()["content"]["decisions"]) == 2


def test_get_artifact_unknown_key_404(tmp_path):
    client, projects = _client(tmp_path)
    _seed(projects)
    assert client.get("/api/projects/sky/artifacts/nope_missing").status_code == 404


def test_get_artifact_unsafe_key_400(tmp_path):
    client, projects = _client(tmp_path)
    _seed(projects)
    # A single-segment but malformed key (uppercase) reaches the handler -> 400.
    assert client.get("/api/projects/sky/artifacts/NotValid").status_code == 400


def test_get_artifact_unknown_project_404(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/projects/ghost/artifacts/scene_plan").status_code == 404


def test_read_artifact_rejects_traversal_key(tmp_path):
    # Defense in depth at the module level (the router blocks slashed keys, but
    # the resolver must reject them too).
    _, projects = _client(tmp_path)
    _seed(projects)
    with pytest.raises(artifacts_mod.BadArtifactKey):
        artifacts_mod.read_artifact(projects, "sky", "../decision_log")
