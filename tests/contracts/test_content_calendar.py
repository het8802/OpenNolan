"""MVP content-calendar contract: shared storage, REST, and agent scheduling."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from lib.project import create_project
from server.agent_runner import AgentRunner
from server.app import create_app
from server.content_calendar import (
    CHANNELS,
    FinalRenderMissing,
    create_scheduled_entry,
    list_calendar_entries,
    read_timing_cache,
)


PIPELINE = "animated-explainer"
STUB_CAPS = {"composition_runtimes": {}, "capabilities": [], "setup_offers": [], "runtime_warnings": []}


def _project(tmp_path, name="Calendar Reel"):
    projects = tmp_path / "projects"
    manifest = create_project(projects, name, PIPELINE)
    return projects, manifest["project_id"]


def _final(projects, project_id, body=b"video"):
    path = projects / project_id / "renders" / "final.mp4"
    path.write_bytes(body)
    return path


def _future(days=2, hour=13):
    return (
        (datetime.now(timezone.utc) + timedelta(days=days))
        .replace(hour=hour, minute=0, second=0, microsecond=0)
        .isoformat()
    )


def _client(projects, runner=None):
    app = create_app(
        projects_dir=projects,
        capabilities_provider=lambda: STUB_CAPS,
        agent_runner=runner,
    )
    return TestClient(app)


def _tool_payload(result):
    return json.loads(result["content"][0]["text"])


def test_manual_schedule_persists_one_multi_channel_entry(tmp_path):
    projects, project_id = _project(tmp_path)
    _final(projects, project_id)

    response = _client(projects).post(
        f"/api/projects/{project_id}/schedule",
        json={"scheduled_at": _future(), "channels": ["youtube", "tiktok", "youtube"]},
    )

    assert response.status_code == 201
    entry = response.json()["entry"]
    assert entry["channels"] == ["tiktok", "youtube"]
    assert entry["created_by"] == "user"
    assert entry["status"] == "scheduled"
    assert entry["render_ref"]["path"] == "renders/final.mp4"
    saved = json.loads((projects / project_id / "artifacts" / "content_schedule.json").read_text())
    assert saved == {"version": "1.0", "entries": [entry]}


def test_calendar_aggregates_projects_and_returns_channel_vocabulary(tmp_path):
    projects, first = _project(tmp_path, "First Reel")
    _, second = _project(tmp_path, "Second Reel")
    _final(projects, first, b"one")
    _final(projects, second, b"two")
    create_scheduled_entry(projects, first, _future(2, 11), ["instagram"], created_by="user")
    create_scheduled_entry(projects, second, _future(3, 15), ["youtube"], created_by="agent")

    body = _client(projects).get("/api/content-calendar").json()

    assert body["channels"] == list(CHANNELS)
    assert [entry["project_name"] for entry in body["entries"]] == ["First Reel", "Second Reel"]
    assert all(entry["playback"]["mtime"] for entry in body["entries"])


def test_schedule_requires_an_existing_final_render(tmp_path):
    projects, project_id = _project(tmp_path)

    response = _client(projects).post(
        f"/api/projects/{project_id}/schedule",
        json={"scheduled_at": _future(), "channels": ["instagram"]},
    )

    assert response.status_code == 409
    try:
        create_scheduled_entry(projects, project_id, _future(), ["instagram"], created_by="agent")
    except FinalRenderMissing:
        pass
    else:
        raise AssertionError("service accepted a project without renders/final.mp4")


def test_invalid_channels_are_rejected_without_touching_storage(tmp_path):
    projects, project_id = _project(tmp_path)
    _final(projects, project_id)

    response = _client(projects).post(
        f"/api/projects/{project_id}/schedule",
        json={"scheduled_at": _future(), "channels": ["linkedin"]},
    )

    assert response.status_code == 422
    assert not (projects / project_id / "artifacts" / "content_schedule.json").exists()


def test_agent_avoids_collision_and_reuses_learned_niche_time(tmp_path):
    projects, first = _project(tmp_path, "First Agent Reel")
    _, second = _project(tmp_path, "Second Agent Reel")
    _final(projects, first)
    _final(projects, second)
    crowded = _future(2, 13)
    create_scheduled_entry(projects, first, crowded, ["instagram"], created_by="user")
    runner = AgentRunner(repo_root=tmp_path, projects_dir=projects, client_factory=lambda _pid: None)

    learned = _tool_payload(
        asyncio.run(
            runner._schedule_content(
                second,
                {
                    "channels": ["instagram", "tiktok"],
                    "scheduled_at": crowded,
                    "niche": "AI developer tools",
                    "learned_local_time": "13:00",
                },
            )
        )
    )

    assert learned["scheduled"] is True
    assert learned["entry"]["scheduled_at"] != crowded.replace("+00:00", "Z")
    assert learned["entry"]["timing_source"] == "researched"
    assert read_timing_cache(projects)["ai-developer-tools"]["local_time"] == "13:00"

    third_manifest = create_project(projects, "Third Agent Reel", PIPELINE)
    third = third_manifest["project_id"]
    _final(projects, third)
    cached = _tool_payload(
        asyncio.run(
            runner._schedule_content(
                third,
                {"channels": ["youtube"], "niche": "AI developer tools"},
            )
        )
    )
    assert cached["scheduled"] is True
    assert cached["entry"]["timing_source"] == "cache"
    assert len(list_calendar_entries(projects)) == 3
