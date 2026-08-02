"""Contract tests for the dev-observability debug-log sink (server/debug_log.py).

The editor's UI session recorder POSTs batches of events here; the backend appends
them as NDJSON under a per-session file. Tests point OPENNOLAN_DEBUG_LOG_DIR at a
tmp dir so nothing touches the real .agents/ tree.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from server.app import create_app

STUB_CAPS = {
    "composition_runtimes": {"ffmpeg": True},
    "capabilities": [],
    "setup_offers": [],
    "runtime_warnings": [],
}


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNOLAN_DEBUG_LOG_DIR", str(tmp_path / "ui-sessions"))
    app = create_app(projects_dir=tmp_path / "projects", capabilities_provider=lambda: STUB_CAPS)
    return TestClient(app)


def test_debug_log_appends_ndjson(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    session = "2026-07-01T00-00-00-000Z-abcd"
    body = {
        "session": session,
        "events": [{"seq": 0, "type": "session.start"}, {"seq": 1, "type": "ui.seek", "data": {"t": 1.5}}],
    }

    r = client.post("/api/debug/log", json=body)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "written": 2}

    # A second batch appends (does not truncate).
    r2 = client.post("/api/debug/log", json={"session": session, "events": [{"seq": 2, "type": "session.stop"}]})
    assert r2.json()["written"] == 1

    log = tmp_path / "ui-sessions" / f"{session}.ndjson"
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert [json.loads(ln)["type"] for ln in lines] == ["session.start", "ui.seek", "session.stop"]
    assert json.loads(lines[1])["data"]["t"] == 1.5


def test_debug_log_rejects_bad_session_id(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    # Path-traversal / separator attempts must be refused, not written to disk.
    for bad in ["../escape", "a/b", "", "x" * 200]:
        r = client.post("/api/debug/log", json={"session": bad, "events": [{"type": "x"}]})
        assert r.status_code == 400, bad


def test_debug_sessions_lists_newest_first(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/api/debug/log", json={"session": "sess-one", "events": [{"type": "a"}]})
    client.post("/api/debug/log", json={"session": "sess-two", "events": [{"type": "b"}]})

    r = client.get("/api/debug/sessions")
    assert r.status_code == 200
    names = {s["session"] for s in r.json()["sessions"]}
    assert names == {"sess-one", "sess-two"}
    assert all("bytes" in s and "mtime" in s for s in r.json()["sessions"])


def _seek_session_events():
    """A tiny scrub trace: 3 seeks issued, only 1 completes (2 superseded)."""
    return [
        {"seq": 0, "type": "session.start", "data": {"projectId": "p"}},
        {"seq": 1, "type": "preview.seekReq", "data": {"to": 1.0, "fired": True}},
        {"seq": 2, "type": "preview.video.seeking", "data": {"cur": 1.0}},
        {"seq": 3, "type": "preview.seekReq", "data": {"to": 2.0, "fired": True}},
        {"seq": 4, "type": "preview.video.seeking", "data": {"cur": 2.0}},  # supersedes seq2
        {"seq": 5, "type": "preview.seekReq", "data": {"to": 3.0, "fired": True}},
        {"seq": 6, "type": "preview.video.seeking", "data": {"cur": 3.0}},  # supersedes seq4
        {"seq": 7, "type": "preview.video.seeked", "data": {"cur": 3.0}},  # only this one finishes
        {"seq": 8, "type": "console", "level": "error", "data": {"args": ["boom"]}},
    ]


def test_analyze_summarizes_seek_completion_and_flags_freeze(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    session = "seek-sess"
    client.post("/api/debug/log", json={"session": session, "events": _seek_session_events()})

    r = client.get(f"/api/debug/sessions/{session}/analyze")
    assert r.status_code == 200
    rep = r.json()
    assert rep["events"] == 9
    assert rep["histogram"]["preview.seekReq"] == 3
    seeks = rep["seeks"]
    assert seeks["started"] == 3 and seeks["finished"] == 1
    assert seeks["superseded_before_finishing"] == 2
    assert seeks["completion_rate"] == round(1 / 3, 3)
    # low completion rate → a diagnostic note is attached, and the console.error is surfaced verbatim.
    assert rep.get("notes") and "never completed" in rep["notes"][0]
    assert any(e.get("type") == "console" for e in rep["errors"])


def test_analyze_latest_and_404(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/debug/sessions/latest/analyze").status_code == 404  # none yet
    client.post("/api/debug/log", json={"session": "only-one", "events": _seek_session_events()})
    assert client.get("/api/debug/sessions/latest/analyze").json()["session"] == "only-one"
    assert client.get("/api/debug/sessions/nope/analyze").status_code == 404


def test_discard_deletes_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    session = "discard-me"
    client.post("/api/debug/log", json={"session": session, "events": [{"type": "a"}]})
    log = tmp_path / "ui-sessions" / f"{session}.ndjson"
    assert log.exists()

    r = client.delete(f"/api/debug/sessions/{session}")
    assert r.status_code == 200 and r.json() == {"ok": True, "removed": True}
    assert not log.exists()  # logs are gone
    assert client.get(f"/api/debug/sessions/{session}/analyze").status_code == 404  # nothing to send

    # Idempotent: discarding an already-gone session is a 200 with removed=false, not a 404/500.
    assert client.delete(f"/api/debug/sessions/{session}").json() == {"ok": True, "removed": False}


def test_discard_rejects_bad_session_id(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    # An over-long id fails the same guard the writer uses → 400, never an unlink outside the dir.
    assert client.delete("/api/debug/sessions/" + "x" * 200).status_code == 400


def test_analyze_collects_edit_history(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    session = "edits-sess"
    events = [
        {"seq": 0, "type": "edit.commit", "data": {"cuts": {"added": ["c2"]}}},  # split/add
        {
            "seq": 1,
            "type": "edit.commit",
            "data": {"cuts": {"changed": [{"id": "c2", "fields": ["out_seconds"]}]}},
        },  # trim
        {
            "seq": 2,
            "type": "edit.commit",
            "data": {"overlays": {"changed": [{"index": 0, "fields": ["text"]}]}},
        },  # text edit
        {"seq": 3, "type": "edit.undo", "data": {"overlays": {"changed": [{"index": 0, "fields": ["text"]}]}}},
        {"seq": 4, "type": "ui.save", "data": {"result": "ok"}},
    ]
    client.post("/api/debug/log", json={"session": session, "events": events})

    rep = client.get(f"/api/debug/sessions/{session}/analyze").json()
    assert rep["edits"]["total"] == 4  # 3 commits + 1 undo (ui.save is not an edit)
    types = [e["type"] for e in rep["edits"]["log"]]
    assert types == ["edit.commit", "edit.commit", "edit.commit", "edit.undo"]
    assert rep["edits"]["log"][0]["cuts"] == {"added": ["c2"]}
    assert rep["histogram"]["ui.save"] == 1


def test_debug_analyzer_can_redact_exported_evidence(tmp_path):
    session_dir = tmp_path / "ui-sessions"
    session_dir.mkdir()
    session = "redact-me"
    private_text = "private launch script"
    secret = "sk-test-secret-value"
    event = {
        "seq": 0,
        "type": "console",
        "level": "error",
        "data": {
            "projectId": "client-project",
            "message": private_text,
            "token": secret,
            "file": str(Path.home() / "client" / "source.mp4"),
        },
    }
    (session_dir / f"{session}.ndjson").write_text(json.dumps(event) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "debug_session.py"), session, "--json", "--redact"],
        cwd=PROJECT_ROOT,
        env={**os.environ, "OPENNOLAN_DEBUG_LOG_DIR": str(session_dir)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    exported = result.stdout
    assert private_text not in exported
    assert secret not in exported
    assert str(Path.home()) not in exported
    assert "[redacted]" in exported
