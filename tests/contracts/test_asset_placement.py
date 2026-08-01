"""Contract tests for asset placement (lib.project.place_asset / asset_dir) and
the `store_asset` agent tool's permission decision.

place_asset is the single writer into a project's asset tree: the caller declares
a KIND and the destination folder is derived, never passed in. These tests pin
the kind->folder map, the repo path shape, content-dedup idempotency, and the
collision-suffix behavior — the properties that stop intermediate clips landing
in renders/ and masquerading as the final render.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.project import KIND_DIRS, asset_dir, create_project, place_asset
from server.agent_runner import ACTION_ALLOW, decide_tool


@pytest.fixture
def project(tmp_path):
    projects = tmp_path / "projects"
    create_project(projects, "My Reel")
    return projects, "my-reel"


def _mk(tmp_path, name, data=b"data"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_kinds_map_to_expected_folders(project):
    projects, pid = project
    assert asset_dir(projects, pid, "image").as_posix().endswith(f"{pid}/assets/images")
    assert asset_dir(projects, pid, "video").as_posix().endswith(f"{pid}/assets/video")
    assert asset_dir(projects, pid, "audio").as_posix().endswith(f"{pid}/assets/audio")
    assert asset_dir(projects, pid, "music").as_posix().endswith(f"{pid}/assets/music")
    # The distinction the whole feature exists for:
    assert asset_dir(projects, pid, "render").as_posix().endswith(f"{pid}/hf/renders")
    assert asset_dir(projects, pid, "final_render").as_posix().endswith(f"{pid}/renders")


def test_place_copies_and_returns_project_relative_path(project, tmp_path):
    projects, pid = project
    src = _mk(tmp_path, "hook.png")
    res = place_asset(projects, pid, "image", src)

    assert res["path"] == "assets/images/hook.png"
    assert res["kind"] == "image"
    assert res["deduped"] is False
    assert (projects / pid / "assets/images/hook.png").is_file()
    assert src.is_file()  # copy (not move) leaves the source in place


def test_render_and_final_render_go_to_distinct_trees(project, tmp_path):
    projects, pid = project
    clip = place_asset(projects, pid, "render", _mk(tmp_path, "b1.mp4", b"clip"))
    final = place_asset(projects, pid, "final_render", _mk(tmp_path, "reel.mp4", b"final"))
    assert clip["path"] == "hf/renders/b1.mp4"
    assert final["path"] == "renders/reel.mp4"


def test_custom_name_overrides_basename(project, tmp_path):
    projects, pid = project
    res = place_asset(projects, pid, "music", _mk(tmp_path, "tmp_xyz.mp3"), name="theme.mp3")
    assert res["path"] == "assets/music/theme.mp3"


def test_idempotent_on_identical_content(project, tmp_path):
    projects, pid = project
    src = _mk(tmp_path, "card.png", b"same-bytes")
    first = place_asset(projects, pid, "image", src)
    second = place_asset(projects, pid, "image", _mk(tmp_path, "card.png", b"same-bytes"))

    assert first["deduped"] is False
    assert second["deduped"] is True
    assert first["path"] == second["path"]
    # Only one file — no card.<hash>.png duplicate.
    assert sorted(p.name for p in (projects / pid / "assets/images").iterdir()) == ["card.png"]


def test_name_collision_different_content_is_not_clobbered(project, tmp_path):
    projects, pid = project
    a = place_asset(projects, pid, "image", _mk(tmp_path, "card.png", b"first"))
    b = place_asset(projects, pid, "image", _mk(tmp_path, "card.png", b"second"))

    assert a["path"] == "assets/images/card.png"
    assert b["path"] != a["path"]            # got a content-hash suffix
    assert b["path"].startswith("assets/images/card.")
    assert len(list((projects / pid / "assets/images").iterdir())) == 2


def test_move_relocates_source(project, tmp_path):
    projects, pid = project
    src = _mk(tmp_path, "clip.mp4", b"bytes")
    place_asset(projects, pid, "video", src, move=True)
    assert not src.exists()
    assert (projects / pid / "assets/video/clip.mp4").is_file()


def test_move_with_dedup_removes_source(project, tmp_path):
    # move=True means "I'm done with src". On the same-name dedup path the bytes
    # already live in the project, so src must be consumed anyway — else every
    # re-store strands a temp original (the litter move semantics exist to kill).
    projects, pid = project
    place_asset(projects, pid, "image", _mk(tmp_path, "card.png", b"same-bytes"))
    src2 = _mk(tmp_path, "card.png", b"same-bytes")
    res = place_asset(projects, pid, "image", src2, move=True)
    assert res["deduped"] is True
    assert not src2.exists()
    assert sorted(p.name for p in (projects / pid / "assets/images").iterdir()) == ["card.png"]


def test_move_with_hash_suffix_dedup_removes_source(project, tmp_path):
    projects, pid = project
    place_asset(projects, pid, "image", _mk(tmp_path, "card.png", b"first"))
    # bytes B collide on name -> get a hash suffix...
    b1 = place_asset(projects, pid, "image", _mk(tmp_path, "card.png", b"second"))
    assert b1["deduped"] is False
    # ...re-placing bytes B with move=True dedups against the suffixed file AND consumes src.
    src3 = _mk(tmp_path, "card.png", b"second")
    b2 = place_asset(projects, pid, "image", src3, move=True)
    assert b2["deduped"] is True
    assert b2["path"] == b1["path"]
    assert not src3.exists()


def test_move_src_equals_target_keeps_file(project):
    # Re-placing a file FROM its own canonical location must never delete it —
    # the src != target guard is load-bearing.
    projects, pid = project
    dest_dir = projects / pid / "assets/images"
    dest_dir.mkdir(parents=True, exist_ok=True)
    canonical = dest_dir / "logo.png"
    canonical.write_bytes(b"logo")
    res = place_asset(projects, pid, "image", canonical, move=True)
    assert res["deduped"] is True
    assert canonical.is_file()


def test_unknown_kind_raises(project, tmp_path):
    projects, pid = project
    with pytest.raises(ValueError):
        place_asset(projects, pid, "gif", _mk(tmp_path, "x.gif"))


def test_missing_source_raises(project):
    projects, pid = project
    with pytest.raises(FileNotFoundError):
        place_asset(projects, pid, "image", projects / "nope.png")


def test_store_asset_tool_is_auto_allowed():
    # Discovery/permission: the tool rides the mc MCP prefix, so decide_tool
    # allows it with no special-casing (same path as ask_user / render).
    decision = decide_tool("mcp__mc__store_asset", {"kind": "image", "src": "x.png"})
    assert decision.action == ACTION_ALLOW


def test_kind_dirs_cover_the_tool_enum():
    # The SDK tool's enum and the placement map must not drift apart.
    assert set(KIND_DIRS) == {"image", "video", "audio", "music", "render", "final_render"}
