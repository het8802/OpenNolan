"""Contract tests for the agent activity log: the module (op/category/summary
derivation) and the /activity endpoint.

The runner appends one event per tool_use during a turn; here we drive the
module directly (no live agent needed) and assert the read endpoint shape.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from lib.project import create_project
from server import activity as activity_mod
from server.app import create_app

PIPELINE = "animated-explainer"
STUB_CAPS = {"composition_runtimes": {}, "capabilities": [], "setup_offers": [], "runtime_warnings": []}


def _client(tmp_path):
    app = create_app(projects_dir=tmp_path / "projects", capabilities_provider=lambda: STUB_CAPS)
    return TestClient(app), tmp_path / "projects"


# --- op / category derivation --------------------------------------------

@pytest.mark.parametrize("tool,target,op,cat", [
    ("Read", "projects/sky/artifacts/scene_plan.json", "read", "project"),
    ("Read", "pipeline_defs/animated-explainer.yaml", "read", "pipeline_def"),
    ("Read", "/Users/x/.claude/skills/scene-planner/SKILL.md", "read", "skill"),
    ("Skill", "scene-planner", "skill", "skill"),
    ("Write", "projects/sky/artifacts/script.json", "write", "project"),
    ("Edit", "projects/sky/artifacts/edit_decisions.json", "edit", "project"),
    ("Bash", "python -m tools.elevenlabs_tts --text hi", "exec", "tool"),
    ("Bash", "ls -la", "exec", "exec"),
    ("WebFetch", "https://example.com/post", "fetch", "web"),
    ("WebSearch", "cheap inference 2026", "search", "web"),
    ("Read", "lib/checkpoint.py", "read", "framework"),
])
def test_event_op_and_category(tool, target, op, cat):
    e = activity_mod.make_event(tool, target, "sky", ts="2026-05-31T00:00:00+00:00")
    assert e["op"] == op
    assert e["category"] == cat


def test_record_skips_internal_ask_user(tmp_path):
    _, projects = _client(tmp_path)
    assert activity_mod.record_tool_use(projects, "sky", "mcp__mc__ask_user", "q") is None
    # nothing written
    assert activity_mod.read_activity(projects, "sky")["events"] == []


def test_record_and_read_roundtrip(tmp_path):
    _, projects = _client(tmp_path)
    for tool, target in [
        ("Read", "pipeline_defs/animated-explainer.yaml"),
        ("Read", "/repo/.claude/skills/scene-planner/SKILL.md"),
        ("Skill", "research-director"),
        ("Bash", "python -m tools.elevenlabs_tts --text hi"),
        ("Write", "projects/sky/artifacts/script.json"),
        ("WebFetch", "https://example.com"),
    ]:
        activity_mod.record_tool_use(projects, "sky", tool, target)

    data = activity_mod.read_activity(projects, "sky")
    assert len(data["events"]) == 6
    s = data["summary"]
    assert "research-director" in s["skills"]
    assert "scene-planner" in s["skills"]            # derived from the skills/ path
    assert "animated-explainer" in s["pipeline_defs"]
    assert "elevenlabs_tts" in s["tools"]            # parsed from the Bash command
    assert s["counts"]["read"] == 2
    assert s["event_count"] == 6


# --- endpoint -------------------------------------------------------------

def test_activity_unknown_project_404(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/projects/ghost/activity").status_code == 404


def test_activity_empty_project_ok(tmp_path):
    client, projects = _client(tmp_path)
    create_project(projects, "Sky", PIPELINE)
    r = client.get("/api/projects/sky/activity")
    assert r.status_code == 200
    body = r.json()
    assert body["events"] == []
    assert body["summary"]["event_count"] == 0


def test_activity_endpoint_returns_events_and_summary(tmp_path):
    client, projects = _client(tmp_path)
    create_project(projects, "Sky", PIPELINE)
    activity_mod.record_tool_use(projects, "sky", "Read", "pipeline_defs/animated-explainer.yaml")
    activity_mod.record_tool_use(projects, "sky", "Write", "projects/sky/artifacts/script.json")

    body = client.get("/api/projects/sky/activity").json()
    assert len(body["events"]) == 2
    assert body["events"][0]["category"] == "pipeline_def"
    assert "animated-explainer" in body["summary"]["pipeline_defs"]


def test_activity_limit_and_since(tmp_path):
    client, projects = _client(tmp_path)
    create_project(projects, "Sky", PIPELINE)
    activity_mod.record_tool_use(projects, "sky", "Read", "a.py", ts="2026-05-31T00:00:01+00:00")
    activity_mod.record_tool_use(projects, "sky", "Read", "b.py", ts="2026-05-31T00:00:02+00:00")
    activity_mod.record_tool_use(projects, "sky", "Read", "c.py", ts="2026-05-31T00:00:03+00:00")

    last = client.get("/api/projects/sky/activity", params={"limit": 1}).json()
    assert len(last["events"]) == 1 and last["events"][0]["target"] == "c.py"

    after = client.get("/api/projects/sky/activity",
                       params={"since": "2026-05-31T00:00:01+00:00"}).json()
    assert [e["target"] for e in after["events"]] == ["b.py", "c.py"]
