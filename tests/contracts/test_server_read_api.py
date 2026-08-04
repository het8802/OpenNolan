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
    (projects / "_analysis").mkdir(parents=True)  # scratch dir
    (projects / ".DS_Store").write_text("noise")  # stray file
    (projects / "legacy-no-manifest").mkdir()  # pre-manifest

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
    (hf_renders / "ov_caption.mov").write_bytes(b"overlay")  # .mov alpha overlay counts too
    (hf_renders / "notes.txt").write_text("not a video")  # non-video is ignored
    (hf_renders / ".hidden.mp4").write_bytes(b"dotfile")  # dotfiles ignored

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


def test_list_assets_marks_the_current_deliverable(tmp_path):
    """The panel used to list every file in renders/ under ONE "Final render" heading, so
    a stale final.mp4 sat next to two fresh renders with nothing to tell them apart."""
    import json as _json

    from lib.project import publish_final_render

    projects = tmp_path / "projects"
    create_project(projects, "Cur Proj", PIPELINE)
    proj = projects / "cur-proj"
    doc = {"version": "1.0", "render_runtime": "ffmpeg", "cuts": []}
    (proj / "artifacts").mkdir(parents=True, exist_ok=True)
    (proj / "artifacts" / "edit_decisions.json").write_text(_json.dumps(doc))
    src = tmp_path / "v.mp4"
    src.write_bytes(b"vid")
    publish_final_render(projects, "cur-proj", src, receipt_doc=doc)
    (proj / "renders" / "overlay_raw.mp4").write_bytes(b"intermediate")

    client = _client(tmp_path)
    renders = client.get("/api/projects/cur-proj/assets").json()["renders"]
    assert [r["name"] for r in renders] == ["final.mp4", "overlay_raw.mp4"]  # deliverable first
    assert renders[0]["current"] is True
    assert renders[1]["current"] is False  # never the deliverable, no receipt
    # The receipt itself is a dotfile, so it cannot appear as a deliverable.
    assert all(not r["name"].startswith(".") for r in renders)

    # Edit the timeline without re-rendering -> the SAME file is no longer current.
    (proj / "artifacts" / "edit_decisions.json").write_text(_json.dumps({**doc, "cuts": [{"id": "c1"}]}))
    renders = client.get("/api/projects/cur-proj/assets").json()["renders"]
    assert renders[0]["current"] is False
    assert "timeline changed" in renders[0]["reason"]


def test_render_cache_bust_token_distinguishes_same_second_replacements(tmp_path):
    """Now that re-renders reuse ONE filename, a whole-second mtime would hand the browser
    the same URL and React key for two different cuts published inside one second."""
    projects = tmp_path / "projects"
    create_project(projects, "Tok Proj", PIPELINE)
    final = projects / "tok-proj" / "renders" / "final.mp4"
    client = _client(tmp_path)

    final.write_bytes(b"first")
    first = client.get("/api/projects/tok-proj/assets").json()["renders"][0]["mtime"]
    final.write_bytes(b"second-cut")
    second = client.get("/api/projects/tok-proj/assets").json()["renders"][0]["mtime"]
    assert first != second

    # Sub-second, and EXACTLY representable in the browser that consumes it. A raw ns
    # timestamp is 19 digits — past float64's exact-integer range — so JSON -> JS silently
    # dropped its low digits and the token in the URL did not equal the file's mtime.
    stat = final.stat()
    assert second == stat.st_mtime_ns // 1000
    assert second != int(stat.st_mtime)  # not whole seconds
    assert second < 2**53, "token must survive JSON -> JS as an exact integer"


# ── folder browsing: what the editor's Assets panel navigates ──────────────


def _browse_proj(tmp_path):
    """A project with the full spread: media, agent chat history, artifacts, proxy cache."""
    projects = tmp_path / "projects"
    create_project(projects, "Browse Proj", PIPELINE)
    proj = projects / "browse-proj"
    (proj / "assets" / "images" / "a.png").write_bytes(b"img")
    (proj / "assets" / "music" / "bed.mp3").write_bytes(b"mus")
    (proj / "assets" / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    (proj / "artifacts" / "script.json").write_text("{}")  # non-media → not listed
    (proj / "hf" / "renders").mkdir(parents=True)
    (proj / "hf" / "renders" / "anim.mp4").write_bytes(b"clip")
    (proj / "hf" / "index.html").write_text("<html>")  # comp source → not listed
    (proj / ".mc" / "threads").mkdir(parents=True)  # agent chat history → hidden
    (proj / ".mc" / "activity.jsonl").write_text("{}\n")
    (proj / "renders" / "proxies").mkdir(parents=True)  # engine cache → hidden
    (proj / "renders" / "proxies" / "b1.deadbeef.mp4").write_bytes(b"proxy")
    (proj / "renders" / "final.mp4").write_bytes(b"final")
    return proj


def test_browse_root_hides_dot_dirs_and_internals(tmp_path):
    """The browser shows folders the user navigates for media — never `.mc/` (the agent's
    chat history), the proxy cache, or artifacts/ (JSON, shown by the Pipeline tab)."""
    _browse_proj(tmp_path)
    body = _client(tmp_path).get("/api/projects/browse-proj/browse").json()

    names = [e["name"] for e in body["entries"]]
    assert names == ["assets", "hf", "renders"]  # folders first, alphabetical
    assert ".mc" not in names and "artifacts" not in names
    assert body["path"] == ""

    inner = _client(tmp_path).get("/api/projects/browse-proj/browse", params={"path": "renders"}).json()
    assert [e["name"] for e in inner["entries"]] == ["final.mp4"]  # proxies/ hidden


def test_browse_lists_media_files_with_kind_and_skips_the_rest(tmp_path):
    _browse_proj(tmp_path)
    c = _client(tmp_path)

    hf = c.get("/api/projects/browse-proj/browse", params={"path": "hf"}).json()
    # index.html isn't media; only the renders/ folder is navigable from here.
    assert [(e["name"], e["is_dir"], e["count"]) for e in hf["entries"]] == [("renders", True, 1)]

    clips = c.get("/api/projects/browse-proj/browse", params={"path": "hf/renders"}).json()
    (clip,) = clips["entries"]
    assert clip["name"] == "anim.mp4" and clip["kind"] == "video" and clip["is_dir"] is False
    assert clip["path"] == "hf/renders/anim.mp4" and clip["size_bytes"] > 0 and "mtime" in clip

    # A file under assets/music is kind 'music' (→ music bed), not generic audio.
    music = c.get("/api/projects/browse-proj/browse", params={"path": "assets/music"}).json()
    assert [e["kind"] for e in music["entries"]] == ["music"]

    # An empty asset folder still lists (count 0) — it stays a visible upload destination.
    # Subtitles sit beside the kind folders and list as kind 'text' (readable in the dialog,
    # no timeline action); the JSON artifact next door stays hidden.
    assets = c.get("/api/projects/browse-proj/browse", params={"path": "assets"}).json()
    assert ("audio", 0) in [(e["name"], e["count"]) for e in assets["entries"] if e["is_dir"]]
    assert [(e["name"], e["kind"]) for e in assets["entries"] if not e["is_dir"]] == [("subtitles.srt", "text")]


def test_browse_rejects_traversal_and_missing_folders(tmp_path):
    _browse_proj(tmp_path)
    c = _client(tmp_path)
    assert c.get("/api/projects/browse-proj/browse", params={"path": "../.."}).status_code == 400
    assert c.get("/api/projects/browse-proj/browse", params={"path": "nope"}).status_code == 404
    assert c.get("/api/projects/no-such-proj/browse").status_code == 404
    # A file is not a folder.
    r = c.get("/api/projects/browse-proj/browse", params={"path": "renders/final.mp4"})
    assert r.status_code == 404


def test_get_file_serves_hf_renders_clip(tmp_path):
    """get_file already serves anything inside the project dir, including hf/renders/."""
    projects = tmp_path / "projects"
    create_project(projects, "Serve Proj", PIPELINE)
    clip = projects / "serve-proj" / "hf" / "renders" / "anim.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"\x00\x01bytes")

    r = _client(tmp_path).get("/api/projects/serve-proj/file", params={"path": "hf/renders/anim.mp4"})
    assert r.status_code == 200
    assert r.content == b"\x00\x01bytes"
