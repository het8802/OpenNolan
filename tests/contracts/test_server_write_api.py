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


def test_create_project_no_pipeline_lets_agent_decide(tmp_path):
    client, projects = _ctx(tmp_path)
    # Omitting pipeline_type entirely is allowed — the agent picks one later.
    r = client.post("/api/projects", json={"name": "Decide Later"})
    assert r.status_code == 201
    assert r.json()["pipeline_type"] is None
    assert (projects / "decide-later" / "project.json").exists()


def test_create_project_empty_pipeline_normalized_to_none(tmp_path):
    client, _ = _ctx(tmp_path)
    # An empty/whitespace pipeline_type is treated as "agent decides", not a 422.
    r = client.post("/api/projects", json={"name": "Blank PT", "pipeline_type": "   "})
    assert r.status_code == 201
    assert r.json()["pipeline_type"] is None


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


# --- list + serve assets --------------------------------------------------

def test_list_assets_groups_by_kind_and_renders(tmp_path):
    client, projects = _ctx(tmp_path)
    pid = _make_project(client)
    # upload one image, one audio (under music/), and drop a render on disk
    client.post(f"/api/projects/{pid}/assets", data={"kind": "images"},
                files={"file": ("logo.png", b"\x89PNG", "image/png")})
    client.post(f"/api/projects/{pid}/assets", data={"kind": "music"},
                files={"file": ("track.mp3", b"ID3", "audio/mpeg")})
    (projects / pid / "renders").mkdir(parents=True, exist_ok=True)
    (projects / pid / "renders" / "final.mp4").write_bytes(b"\x00\x00\x00\x18ftyp")

    r = client.get(f"/api/projects/{pid}/assets")
    assert r.status_code == 200
    body = r.json()
    assert any(f["name"] == "logo.png" for f in body["kinds"]["images"])
    assert any(f["name"] == "track.mp3" for f in body["kinds"]["music"])
    assert any(f["name"] == "final.mp4" for f in body["renders"])


def test_get_file_serves_and_blocks_traversal(tmp_path):
    client, projects = _ctx(tmp_path)
    pid = _make_project(client)
    client.post(f"/api/projects/{pid}/assets", data={"kind": "images"},
                files={"file": ("pic.png", b"PNGDATA", "image/png")})

    # serve a real file
    ok = client.get(f"/api/projects/{pid}/file", params={"path": "assets/images/pic.png"})
    assert ok.status_code == 200
    assert ok.content == b"PNGDATA"

    # traversal attempt is blocked
    bad = client.get(f"/api/projects/{pid}/file", params={"path": "../../etc/hosts"})
    assert bad.status_code in (400, 404)

    # missing file
    missing = client.get(f"/api/projects/{pid}/file", params={"path": "assets/images/nope.png"})
    assert missing.status_code == 404
