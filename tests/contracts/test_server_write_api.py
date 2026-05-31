"""Contract tests for the Mission Control write endpoints.

POST /api/projects (create) and POST /api/projects/{id}/assets (upload).
The upload tests focus on the security-critical path: a traversal filename
must never write outside the project's assets directory.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from server.app import create_app

PIPELINE = "animated-explainer"

STUB_CAPS = {"composition_runtimes": {}, "capabilities": [], "setup_offers": [], "runtime_warnings": []}


def _ctx(tmp_path):
    projects = tmp_path / "projects"
    app = create_app(projects_dir=projects, capabilities_provider=lambda: STUB_CAPS)
    return TestClient(app), projects


# --- create project -------------------------------------------------------

def test_create_project_201(tmp_path):
    client, projects = _ctx(tmp_path)
    r = client.post("/api/projects", json={"name": "Sky Test", "pipeline_type": PIPELINE})
    assert r.status_code == 201
    body = r.json()
    assert body["project_id"] == "sky-test"
    assert body["pipeline_type"] == PIPELINE
    assert (projects / "sky-test" / "project.json").exists()
    assert (projects / "sky-test" / "assets" / "images").is_dir()


def test_create_project_collision_409(tmp_path):
    client, _ = _ctx(tmp_path)
    client.post("/api/projects", json={"name": "Sky", "pipeline_type": PIPELINE})
    r = client.post("/api/projects", json={"name": "sky", "pipeline_type": PIPELINE})
    assert r.status_code == 409


def test_create_project_unknown_pipeline_422(tmp_path):
    client, _ = _ctx(tmp_path)
    r = client.post("/api/projects", json={"name": "X", "pipeline_type": "not-a-pipeline"})
    assert r.status_code == 422


# --- upload asset ---------------------------------------------------------

def _make_project(client):
    return client.post("/api/projects", json={"name": "Sky", "pipeline_type": PIPELINE}).json()["project_id"]


def test_upload_asset_201(tmp_path):
    client, projects = _ctx(tmp_path)
    pid = _make_project(client)
    r = client.post(
        f"/api/projects/{pid}/assets",
        data={"kind": "images"},
        files={"file": ("photo.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["filename"] == "photo.png"
    assert body["size_bytes"] > 0
    saved = projects / pid / "assets" / "images" / "photo.png"
    assert saved.exists()


def test_upload_asset_path_traversal_is_contained(tmp_path):
    """A malicious filename must land inside the assets dir as a basename and
    write nothing outside it."""
    client, projects = _ctx(tmp_path)
    pid = _make_project(client)
    r = client.post(
        f"/api/projects/{pid}/assets",
        data={"kind": "images"},
        files={"file": ("../../../escape.png", b"data", "image/png")},
    )
    assert r.status_code == 201
    assert r.json()["filename"] == "escape.png"
    # Written inside the assets dir...
    assert (projects / pid / "assets" / "images" / "escape.png").exists()
    # ...and nothing escaped above the project.
    assert not (projects / "escape.png").exists()
    assert not (tmp_path / "escape.png").exists()
    assert not (projects.parent / "escape.png").exists()


def test_upload_invalid_kind_422(tmp_path):
    client, _ = _ctx(tmp_path)
    pid = _make_project(client)
    r = client.post(
        f"/api/projects/{pid}/assets",
        data={"kind": "weapons"},
        files={"file": ("x.png", b"d", "image/png")},
    )
    assert r.status_code == 422


def test_upload_unknown_project_404(tmp_path):
    client, _ = _ctx(tmp_path)
    r = client.post(
        "/api/projects/ghost/assets",
        data={"kind": "images"},
        files={"file": ("x.png", b"d", "image/png")},
    )
    assert r.status_code == 404
