"""MVP content-calendar contract: shared storage, REST, and agent scheduling."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from lib.project import create_project
from server.agent_runner import AgentRunner
from server.app import create_app
from server.content_calendar import (
    CHANNELS,
    DEFAULT_LOCAL_MINUTES,
    FinalRenderMissing,
    ScheduleValidationError,
    create_scheduled_entry,
    list_calendar_entries,
    read_timing_cache,
    schedule_path,
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


@contextlib.contextmanager
def _local_tz(name):
    """Run a block under a real IANA zone so DST assertions are deterministic."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


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


def test_rescheduling_moves_the_same_entry_instead_of_duplicating_it(tmp_path):
    """The Schedule dialog re-saves an existing slot; a project holds exactly one."""
    projects, project_id = _project(tmp_path)
    _final(projects, project_id)
    client = _client(projects)

    first = client.post(
        f"/api/projects/{project_id}/schedule",
        json={"scheduled_at": _future(2, 11), "channels": ["instagram"]},
    ).json()["entry"]
    assert first["replaced"] is False

    # An agent slot for the SAME project must also move that one entry, not add a second.
    runner = AgentRunner(repo_root=tmp_path, projects_dir=projects, client_factory=lambda _pid: None)
    moved = _tool_payload(
        asyncio.run(runner._schedule_content(project_id, {"channels": ["youtube"], "scheduled_at": _future(5, 16)}))
    )["entry"]

    assert moved["id"] == first["id"]
    assert moved["created_at"] == first["created_at"]
    assert moved["replaced"] is True
    assert moved["channels"] == ["youtube"]
    assert moved["scheduled_at"] != first["scheduled_at"]

    entries = list_calendar_entries(projects)
    assert len(entries) == 1
    assert entries[0]["scheduled_at"] == moved["scheduled_at"]
    # And the UI reads exactly that: one entry keyed by this project id.
    body = client.get("/api/content-calendar").json()
    assert [entry["project_id"] for entry in body["entries"]] == [project_id]


def test_upsert_collapses_every_pre_existing_duplicate_for_the_project(tmp_path):
    """Files written before the one-slot rule can hold several entries; a save collapses ALL
    of them onto the earliest id/created_at, and leaves another project's entry alone."""
    projects, project_id = _project(tmp_path)
    _, neighbour = _project(tmp_path, "Neighbour Reel")
    _final(projects, project_id)
    _final(projects, neighbour)
    create_scheduled_entry(projects, neighbour, _future(9, 12), ["tiktok"], created_by="user")
    path = schedule_path(projects, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    duplicates = [
        {
            "id": f"dup-{index}",
            "project_id": project_id,
            "render_ref": {"path": "renders/final.mp4", "size_bytes": 5, "mtime_us": 1},
            "scheduled_at": _future(3 + index, 9).replace("+00:00", "Z"),
            "channels": ["tiktok"],
            "status": "scheduled",
            "created_by": "agent",
            "created_at": f"2026-01-0{index + 1}T00:00:00Z",
            "timing_source": "baseline",
        }
        for index in range(3)
    ]
    path.write_text(json.dumps({"version": "1.0", "entries": duplicates}), encoding="utf-8")

    entry = create_scheduled_entry(projects, project_id, _future(6, 10), ["youtube"], created_by="user")

    assert entry["id"] == "dup-0"  # earliest scheduled_at wins, not file order
    assert entry["created_at"] == "2026-01-01T00:00:00Z"
    assert entry["replaced"] is True
    assert json.loads(path.read_text())["entries"] == [entry]
    assert sorted(e["project_id"] for e in list_calendar_entries(projects)) == sorted([project_id, neighbour])


def test_a_traversal_project_id_cannot_reach_outside_the_projects_dir(tmp_path):
    """`%2e%2e` decodes to `..` before the path param binds, and a parent dir with renders/
    passes the legacy-project test — so the id itself has to be refused."""
    projects, project_id = _project(tmp_path)
    _final(projects, project_id)
    (tmp_path / "renders").mkdir(parents=True, exist_ok=True)
    (tmp_path / "renders" / "final.mp4").write_bytes(b"video")

    response = _client(projects).post(
        "/api/projects/%2e%2e/schedule",
        json={"scheduled_at": _future(), "channels": ["tiktok"]},
    )

    assert response.status_code == 404
    assert not (tmp_path / "artifacts" / "content_schedule.json").exists()
    for unsafe in ("..", "../evil", "/etc", "a/b"):
        with pytest.raises(ScheduleValidationError):
            create_scheduled_entry(projects, unsafe, _future(), ["tiktok"], created_by="agent")
        with pytest.raises(ScheduleValidationError):
            schedule_path(projects, unsafe)


def test_naive_times_take_the_offset_in_force_on_that_date(tmp_path):
    """A frozen `datetime.now().astimezone().tzinfo` stamped today's offset onto every date,
    so a slot on the far side of a DST transition read back an hour off."""
    projects, project_id = _project(tmp_path)
    _final(projects, project_id)

    with _local_tz("America/Los_Angeles"):
        summer = create_scheduled_entry(projects, project_id, "2027-07-20T18:45:00", ["tiktok"], created_by="user")
        winter = create_scheduled_entry(projects, project_id, "2027-11-20T18:45:00", ["tiktok"], created_by="user")
        # The agent's auto-picked slot walks wall-clock days, so it must read back AS one of
        # the baseline local times — true across a transition only if the offset is per-date.
        auto = create_scheduled_entry(projects, project_id, None, ["tiktok"], created_by="agent")
        chosen = datetime.fromisoformat(auto["scheduled_at"].replace("Z", "+00:00")).astimezone()

    assert summer["scheduled_at"] == "2027-07-21T01:45:00Z"  # PDT, UTC-7
    assert winter["scheduled_at"] == "2027-11-21T02:45:00Z"  # PST, UTC-8
    assert (chosen.hour, chosen.minute) in {divmod(minutes, 60) for minutes in DEFAULT_LOCAL_MINUTES}


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
