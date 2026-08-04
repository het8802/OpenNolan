"""Contract tests for asset placement (lib.project.place_asset / asset_dir), the
final-render PUBLISHER, and the `store_asset` agent tool's permission decision.

place_asset is the single writer into a project's asset tree: the caller declares
a KIND and the destination folder is derived, never passed in. These tests pin
the kind->folder map, the repo path shape, content-dedup idempotency, and the
collision-suffix behavior — the properties that stop intermediate clips landing
in renders/ and masquerading as the final render.

The second half covers OPN-30: `final_render` is the one kind that REPLACES rather
than hash-suffixing, via publish_final_render, and the receipt it writes last is
what lets anyone tell whether renders/final.mp4 is the video of the live timeline.
"""

import json
import os
import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import lib.project as project_mod
from lib.project import (
    FINAL_RECEIPT_NAME,
    KIND_DIRS,
    asset_dir,
    canonical_doc_hash,
    create_project,
    final_render_status,
    place_asset,
    project_lock,
    publish_final_render,
)
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
    # final_render FORCES the canonical name (it routes to publish_final_render): the
    # deliverable has to be findable, and "reel.mp4" was a second "Final render" tile.
    assert final["path"] == "renders/final.mp4"


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


# --- the final-render publisher (OPN-30) ----------------------------------

DOC_A = {"version": "1.0", "render_runtime": "ffmpeg", "cuts": [{"id": "c1"}]}
DOC_B = {"version": "1.0", "render_runtime": "ffmpeg", "cuts": [{"id": "c1"}, {"id": "c2"}]}


def _renders(projects, pid):
    return projects / pid / "renders"


def _write_doc(projects, pid, doc):
    p = projects / pid / "artifacts" / "edit_decisions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc))
    return p


def test_publish_replaces_instead_of_hash_suffixing(project, tmp_path):
    """THE bug: a re-render used to leave the stale cut at renders/final.mp4 and park the
    new one beside it, so the obvious file was the wrong one."""
    projects, pid = project
    publish_final_render(projects, pid, _mk(tmp_path, "a.mp4", b"first"), receipt_doc=DOC_A)
    res = publish_final_render(projects, pid, _mk(tmp_path, "b.mp4", b"second-bytes"),
                               receipt_doc=DOC_A)

    assert res["path"] == "renders/final.mp4"
    assert res["published"] is True
    assert (_renders(projects, pid) / "final.mp4").read_bytes() == b"second-bytes"
    # Exactly the deliverable and its receipt — no final.<hash>.mp4 sibling, no .part left.
    assert sorted(p.name for p in _renders(projects, pid).iterdir()) == \
        [FINAL_RECEIPT_NAME, "final.mp4"]


def test_publish_copies_non_temp_source_but_moves_when_asked(project, tmp_path):
    """place_asset's contract must survive: only sources the caller is DONE with (temp
    staging files) are consumed; anything else is copied and left alone."""
    projects, pid = project
    keep = _mk(tmp_path, "keep.mp4", b"x")
    publish_final_render(projects, pid, keep)
    assert keep.is_file()

    consume = _mk(tmp_path, "consume.mp4", b"y")
    publish_final_render(projects, pid, consume, move=True)
    assert not consume.exists()


def test_publish_move_across_filesystems(project, tmp_path, monkeypatch):
    """A temp root is often another device, where a bare rename raises EXDEV. Staging goes
    through shutil.move, which falls back to copy+unlink. CI has one filesystem, so make
    os.rename behave like a cross-device one."""
    projects, pid = project
    real_rename = os.rename

    def exdev(src, dst, *a, **kw):
        raise OSError(18, "Cross-device link")

    src = _mk(tmp_path, "far.mp4", b"far-away")
    monkeypatch.setattr(os, "rename", exdev)
    try:
        publish_final_render(projects, pid, src, move=True, receipt_doc=DOC_A)
    finally:
        monkeypatch.setattr(os, "rename", real_rename)
    assert (_renders(projects, pid) / "final.mp4").read_bytes() == b"far-away"
    assert not src.exists()
    assert final_render_status(projects, pid)["current"] is False  # no doc on disk yet


def test_receipt_doc_without_persist_doc_leaves_the_live_doc_alone(project, tmp_path):
    """The editor route: it gets a receipt for the snapshot it rendered and never writes
    the doc back, because autosave keeps running during a render."""
    projects, pid = project
    doc_file = _write_doc(projects, pid, DOC_B)
    before = doc_file.read_bytes()

    publish_final_render(projects, pid, _mk(tmp_path, "v.mp4", b"vid"),
                         receipt_doc=DOC_A, persist_doc=None)

    assert doc_file.read_bytes() == before          # byte-identical: B survived
    receipt = json.loads((_renders(projects, pid) / FINAL_RECEIPT_NAME).read_text())
    assert receipt["doc_hash"] == canonical_doc_hash(DOC_A)
    # A ≠ B, so the render is honestly reported as stale rather than falsely current.
    assert final_render_status(projects, pid)["current"] is False


def test_persist_doc_commits_the_doc_with_the_video(project, tmp_path):
    projects, pid = project
    _write_doc(projects, pid, DOC_A)
    publish_final_render(projects, pid, _mk(tmp_path, "v.mp4", b"vid"),
                         receipt_doc=DOC_B, persist_doc=DOC_B)

    on_disk = json.loads((projects / pid / "artifacts" / "edit_decisions.json").read_text())
    assert on_disk == DOC_B
    assert final_render_status(projects, pid)["current"] is True


def test_publish_without_receipt_doc_unlinks_the_receipt(project, tmp_path):
    """store_asset(final_render) hands over bytes that may be unrelated to anything on
    disk. Leaving the old receipt would be a forged provenance — and it would NOT reliably
    read as stale, because copy2 preserves the source mtime and sizes can collide. So the
    receipt is removed."""
    projects, pid = project
    _write_doc(projects, pid, DOC_A)
    publish_final_render(projects, pid, _mk(tmp_path, "a.mp4", b"1234"),
                         receipt_doc=DOC_A, persist_doc=None)
    assert final_render_status(projects, pid)["current"] is True
    published = _renders(projects, pid) / "final.mp4"
    old = published.stat()

    # Same SIZE, and force the same mtime_ns, so ONLY the unlink can save us.
    unrelated = _mk(tmp_path, "b.mp4", b"abcd")
    os.utime(unrelated, ns=(old.st_mtime_ns, old.st_mtime_ns))
    place_asset(projects, pid, "final_render", unrelated)

    assert published.read_bytes() == b"abcd"
    assert published.stat().st_mtime_ns == old.st_mtime_ns   # identity check alone is fooled
    assert not (_renders(projects, pid) / FINAL_RECEIPT_NAME).exists()
    status = final_render_status(projects, pid)
    assert status["current"] is False and "receipt" in status["reason"]


def test_publisher_is_reentrant_for_the_render_thread(project, tmp_path):
    """A render holds project_lock for its whole duration and then publishes. A plain Lock
    would self-deadlock right there."""
    projects, pid = project
    with project_lock(projects, pid):
        res = publish_final_render(projects, pid, _mk(tmp_path, "v.mp4", b"vid"),
                                   receipt_doc=DOC_A)
    assert res["published"] is True


def test_commit_guard_refusal_publishes_nothing(project, tmp_path):
    """The supersede hook: a refusal must leave the previous deliverable, the previous
    receipt, and no .part behind."""
    projects, pid = project
    publish_final_render(projects, pid, _mk(tmp_path, "a.mp4", b"first"), receipt_doc=DOC_A)
    import contextlib

    res = publish_final_render(
        projects, pid, _mk(tmp_path, "b.mp4", b"second"), receipt_doc=DOC_B,
        persist_doc=DOC_B, commit_guard=lambda: contextlib.nullcontext(False),
    )
    assert res["published"] is False and "superseded" in res["reason"]
    assert (_renders(projects, pid) / "final.mp4").read_bytes() == b"first"
    receipt = json.loads((_renders(projects, pid) / FINAL_RECEIPT_NAME).read_text())
    assert receipt["doc_hash"] == canonical_doc_hash(DOC_A)
    assert sorted(p.name for p in _renders(projects, pid).iterdir()) == \
        [FINAL_RECEIPT_NAME, "final.mp4"]


def test_crash_between_video_and_receipt_reads_stale_not_current(project, tmp_path, monkeypatch):
    """Why the receipt is written LAST. If it were written first (or the check were
    doc-only), the window between the video replace and the receipt would report the new
    bytes as current under the OLD receipt."""
    projects, pid = project
    _write_doc(projects, pid, DOC_A)
    publish_final_render(projects, pid, _mk(tmp_path, "a.mp4", b"first"),
                         receipt_doc=DOC_A, persist_doc=None)
    assert final_render_status(projects, pid)["current"] is True

    real_write = project_mod.atomic_write_json

    def fail_on_receipt(path, data, **kw):
        if Path(path).name == FINAL_RECEIPT_NAME:
            raise OSError("disk died writing the receipt")
        return real_write(path, data, **kw)

    monkeypatch.setattr(project_mod, "atomic_write_json", fail_on_receipt)
    with pytest.raises(OSError):
        publish_final_render(projects, pid, _mk(tmp_path, "b.mp4", b"second-cut"),
                             receipt_doc=DOC_A, persist_doc=None)

    # New bytes, old receipt, and the doc hash still matches -> only the file-identity
    # half of the check catches this.
    assert (_renders(projects, pid) / "final.mp4").read_bytes() == b"second-cut"
    assert final_render_status(projects, pid)["current"] is False


def test_concurrent_publishes_never_interleave(project, tmp_path):
    """Two threads publishing different (bytes, doc) pairs: one pair wins WHOLE. A mix —
    A's video with B's receipt — is the state that would make `current` a lie."""
    projects, pid = project
    pairs = {b"aaaa": DOC_A, b"bbbb": DOC_B}
    start = threading.Barrier(2)

    def go(payload):
        start.wait(timeout=5)
        for _ in range(12):
            src = tmp_path / f"{payload.decode()}-{threading.get_ident()}.mp4"
            src.write_bytes(payload)
            publish_final_render(projects, pid, src, receipt_doc=pairs[payload],
                                 persist_doc=pairs[payload], move=True)

    threads = [threading.Thread(target=go, args=(p,)) for p in pairs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    video = (_renders(projects, pid) / "final.mp4").read_bytes()
    receipt = json.loads((_renders(projects, pid) / FINAL_RECEIPT_NAME).read_text())
    assert receipt["doc_hash"] == canonical_doc_hash(pairs[video])
    assert final_render_status(projects, pid)["current"] is True
    assert sorted(p.name for p in _renders(projects, pid).iterdir()) == \
        [FINAL_RECEIPT_NAME, "final.mp4"]


def test_project_lock_identity(tmp_path):
    """Same project -> same lock. Two CHECKOUTS that share a project id -> different
    locks, or one worktree's render would serialize against another's."""
    a, b = tmp_path / "one" / "projects", tmp_path / "two" / "projects"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert project_lock(a, "reel") is project_lock(a, "reel")
    assert project_lock(a, "reel") is not project_lock(b, "reel")
    assert project_lock(a, "reel") is not project_lock(a, "other")


def test_canonical_doc_hash_is_key_order_independent():
    assert canonical_doc_hash({"a": 1, "b": [2, 3]}) == canonical_doc_hash({"b": [2, 3], "a": 1})
    assert canonical_doc_hash({"a": 1}) != canonical_doc_hash({"a": 2})


def test_final_render_status_reasons(project, tmp_path):
    projects, pid = project
    assert final_render_status(projects, pid) == \
        {"current": False, "reason": "no renders/final.mp4 yet"}

    # A video with no receipt (an outside writer, or a store_asset publish).
    (_renders(projects, pid)).mkdir(parents=True, exist_ok=True)
    (_renders(projects, pid) / "final.mp4").write_bytes(b"stranger")
    assert final_render_status(projects, pid)["current"] is False

    _write_doc(projects, pid, DOC_A)
    publish_final_render(projects, pid, _mk(tmp_path, "v.mp4", b"vid"), receipt_doc=DOC_A)
    assert final_render_status(projects, pid)["current"] is True

    # The timeline moves on without a re-render.
    _write_doc(projects, pid, DOC_B)
    status = final_render_status(projects, pid)
    assert status["current"] is False and "timeline changed" in status["reason"]

    # ...and an outside writer replacing the video behind the publisher's back.
    _write_doc(projects, pid, DOC_A)
    assert final_render_status(projects, pid)["current"] is True
    (_renders(projects, pid) / "final.mp4").write_bytes(b"replaced-out-of-band")
    status = final_render_status(projects, pid)
    assert status["current"] is False and "replaced" in status["reason"]
