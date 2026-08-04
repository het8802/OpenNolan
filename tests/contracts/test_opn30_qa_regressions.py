"""Adversarial QA cases for OPN-30 that are absent from the authored suite.

These tests intentionally describe the required safe behavior.  They are reviewer-owned
regressions: failures demonstrate findings; production code is not changed here.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import project as project_mod
from lib.project import (
    FINAL_RECEIPT_NAME,
    final_render_status,
    project_lock,
    publish_final_render,
)
from server import render_jobs as render_jobs_mod
from server.render_jobs import RenderJobStore, TERMINAL_STATUSES
from tools.video.video_compose import VideoCompose


DOC = {
    "version": "1.0",
    "render_runtime": "ffmpeg",
    "renderer_family": "social-reel",
    "cuts": [],
}


def _project(tmp_path: Path) -> tuple[Path, str, Path]:
    projects = tmp_path / "projects"
    project_id = "qa"
    root = projects / project_id
    (root / "artifacts").mkdir(parents=True)
    (root / "artifacts" / "edit_decisions.json").write_text(json.dumps(DOC))
    return projects, project_id, root


def test_refused_self_move_does_not_delete_the_existing_deliverable(tmp_path: Path) -> None:
    """A commit guard refusal must be non-destructive even when src is final.mp4."""
    projects, project_id, root = _project(tmp_path)
    source = tmp_path / "first.mp4"
    source.write_bytes(b"known-good")
    publish_final_render(projects, project_id, source, receipt_doc=DOC)
    final = root / "renders" / "final.mp4"

    result = publish_final_render(
        projects,
        project_id,
        final,
        move=True,
        commit_guard=lambda: contextlib.nullcontext(False),
    )

    assert result["published"] is False
    assert final.read_bytes() == b"known-good"


def test_failed_receipt_write_with_same_metadata_never_reports_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Receipt-last needs to stay stale even when replacement metadata collides."""
    projects, project_id, root = _project(tmp_path)
    first = tmp_path / "first.mp4"
    first.write_bytes(b"AAAA")
    publish_final_render(projects, project_id, first, receipt_doc=DOC)
    final = root / "renders" / "final.mp4"
    old_stat = final.stat()

    replacement = tmp_path / "replacement.mp4"
    replacement.write_bytes(b"BBBB")
    os.utime(replacement, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))

    real_write = project_mod.atomic_write_json

    def fail_receipt(path, data, **kwargs):
        if Path(path).name == FINAL_RECEIPT_NAME:
            raise OSError("simulated receipt commit failure")
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(project_mod, "atomic_write_json", fail_receipt)
    with pytest.raises(OSError, match="receipt commit failure"):
        publish_final_render(projects, project_id, replacement, receipt_doc=DOC)

    assert final.read_bytes() == b"BBBB"
    assert final_render_status(projects, project_id)["current"] is False


class _FakeCompose:
    def execute(self, inputs):
        output = Path(inputs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered")
        return SimpleNamespace(success=True, data={}, error=None)


def test_job_superseded_after_publish_still_becomes_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A newer start between publish and _set(done) must not strand the old job."""
    projects, project_id, root = _project(tmp_path)
    store = RenderJobStore(projects)
    store._tool = _FakeCompose()
    store._jobs["old"] = {
        "job_id": "old",
        "project_id": project_id,
        "origin": "agent",
        "consumed": False,
        "status": "running",
    }
    store._active_by_project[project_id] = "old"
    real_publish = render_jobs_mod.publish_final_render

    def publish_then_supersede(*args, **kwargs):
        result = real_publish(*args, **kwargs)
        # This is the state transition performed by start()/start_with_inputs().
        with store._lock:
            store._jobs["new"] = {
                "job_id": "new",
                "project_id": project_id,
                "origin": "editor",
                "status": "queued",
            }
            store._active_by_project[project_id] = "new"
        return result

    monkeypatch.setattr(render_jobs_mod, "publish_final_render", publish_then_supersede)
    with project_lock(projects, project_id):
        store._render_locked(
            "old",
            project_id,
            DOC,
            {"assets": []},
            root / "renders" / "final.mp4",
            root / "renders" / "proxies",
            receipt_doc=DOC,
            publish=True,
        )

    # Once the commit guard allowed the replace, these bytes really were published.
    # The force=True completion path must therefore report done, not merely "some"
    # terminal status that could hide a false superseded result.
    assert store.status("old")["status"] == "done"
    assert store.status("old")["output_path"] == "renders/final.mp4"


def test_superseded_media_op_does_not_stay_running_forever(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """agent_op shares supersede state with renders and must share terminal semantics."""
    projects, project_id, _ = _project(tmp_path)
    store = RenderJobStore(projects)
    entered = threading.Event()
    release = threading.Event()

    class BlockingTool:
        def execute(self, _inputs):
            entered.set()
            assert release.wait(timeout=5)
            return SimpleNamespace(success=True, data={"output": "elsewhere.mp4"}, error=None)

    from tools.tool_registry import registry

    monkeypatch.setattr(registry, "ensure_discovered", lambda: None)
    monkeypatch.setattr(registry, "get", lambda _name: BlockingTool())
    store._jobs["op"] = {
        "job_id": "op",
        "project_id": project_id,
        "origin": "agent_op",
        "consumed": False,
        "status": "queued",
    }
    store._active_by_project[project_id] = "op"
    thread = threading.Thread(
        target=store._run_op,
        args=("op", project_id, "blocking", {}),
    )
    thread.start()
    assert entered.wait(timeout=5)
    with store._lock:
        store._jobs["new"] = {
            "job_id": "new",
            "project_id": project_id,
            "origin": "editor",
            "status": "queued",
        }
        store._active_by_project[project_id] = "new"
    release.set()
    thread.join(timeout=5)

    assert store.status("op")["status"] in TERMINAL_STATUSES


def test_output_path_cannot_target_the_internal_proxy_cache(tmp_path: Path) -> None:
    """An assembled output must not be allowed to overwrite a cached scene clip."""
    projects, project_id, root = _project(tmp_path)
    store = RenderJobStore(projects)

    normalized = store._normalize_output_path(
        project_id,
        "renders/proxies/scene.content-key.mp4",
        "final.mp4",
    )

    assert normalized == root.resolve() / "renders" / "final.mp4"


# AUTHOR-ADJUSTED (round 2). The escape is real and is closed. The reviewer's version
# asserted the helper still RETURNS a contained path; it now RAISES instead — the other
# option the finding itself offered ("reject a symlinked renders root"). There is no
# contained path to return: renders/ IS the symlink, so any "contained" answer would
# silently write to a second location the user never pointed at. Refusing names the cause.
# Asserted at the JOB level too, which is the guarantee that actually matters: nothing is
# written outside the project, and the failure is legible.
def test_output_path_cannot_escape_through_a_symlinked_renders_directory(tmp_path: Path) -> None:
    projects, project_id, root = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "renders").symlink_to(outside, target_is_directory=True)
    store = RenderJobStore(projects)
    store._tool = _FakeCompose()

    with pytest.raises(project_mod.RendersDirEscapes):
        store._normalize_output_path(project_id, "renders/output.mp4", "final.mp4")
    # ...and the canonical route, whose lexical fallback used to follow the same symlink.
    with pytest.raises(project_mod.RendersDirEscapes):
        store._normalize_output_path(project_id, None, "final.mp4")

    job_id = store.start_with_inputs(project_id, {"edit_decisions": DOC})
    deadline = time.time() + 5
    while time.time() < deadline and store.status(job_id)["status"] not in TERMINAL_STATUSES:
        time.sleep(0.01)
    status = store.status(job_id)
    assert status["status"] == "failed"
    assert "outside the project" in status["error"]
    assert not any(outside.iterdir())  # nothing written through the link
    # The publisher and the read path refuse it too, rather than raising into the UI.
    with pytest.raises(project_mod.RendersDirEscapes):
        publish_final_render(projects, project_id, _mkfile(tmp_path, "v.mp4"), receipt_doc=DOC)
    assert final_render_status(projects, project_id)["current"] is False


def test_relative_projects_dir_still_publishes_the_canonical_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolving the storage directory must not mix an absolute final with a relative
    project root when constructing the public renders/final.mp4 path."""
    monkeypatch.chdir(tmp_path)
    projects, project_id, _root = _project(Path("."))
    source = Path("source.mp4")
    source.write_bytes(b"rendered")

    result = publish_final_render(projects, project_id, source, receipt_doc=DOC)

    assert result["path"] == "renders/final.mp4"


def test_internal_renders_symlink_does_not_change_the_public_deliverable_path(
    tmp_path: Path,
) -> None:
    """If an in-project symlink is allowed, callers must still receive the canonical
    renders/final.mp4 path that the API and pipeline contract promise."""
    projects, project_id, root = _project(tmp_path)
    storage = root / "render-storage"
    storage.mkdir()
    (root / "renders").symlink_to(storage, target_is_directory=True)

    try:
        result = publish_final_render(projects, project_id, _mkfile(tmp_path, "internal.mp4"), receipt_doc=DOC)
    except project_mod.RendersDirEscapes:
        return  # Rejecting every renders symlink is also a safe, canonical policy.

    assert result["path"] == "renders/final.mp4"


# AUTHOR-ADJUSTED (round 3). REFUTED, with the codebase's own precedent — inverted into a
# positive contract for the behaviour we agreed on instead of deleted.
#
# A symlinked PROJECT directory is a first-class layout everywhere else in this app, not an
# escape: server/app.py:463 `get_file` defines "inside the project" as the RESOLVED project
# dir and happily serves a symlinked project's files. Requiring the project to be a direct
# child of the projects root would be a NEW policy, inconsistent with the read layer, and it
# would break the plausible workflow it exists for — a video project parked on an external
# drive. The user chose that location; the agent cannot create it (it is a filesystem layout
# decision, not a tool argument).
#
# What DID need fixing here is what the reviewer's run actually surfaced: the job wrote a
# .part.mp4 and then failed on a relative_to() ValueError. That was a real bug of mine
# (round-3 finding 3) and is fixed — the public path is now a constant. So the symlinked
# project now works END TO END, which is what these two tests pin.
#
# The out-of-project renders/ SYMLINK stays refused, and for a concrete reason rather than a
# policy preference: get_file resolves before its containment check, so a deliverable
# published through such a link would be listed by the assets API and then 400 on playback
# and download. Refusing at publish time beats advertising a file the app cannot serve.
def test_symlinked_project_directory_is_a_supported_layout(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    outside_project = tmp_path / "outside-project"
    (outside_project / "artifacts").mkdir(parents=True)
    (outside_project / "artifacts" / "edit_decisions.json").write_text(json.dumps(DOC))
    (projects / "qa").symlink_to(outside_project, target_is_directory=True)

    result = publish_final_render(projects, "qa", _mkfile(tmp_path, "project-link.mp4"), receipt_doc=DOC)

    # The PUBLIC path is the canonical constant, never the physical/resolved one.
    assert result["path"] == "renders/final.mp4"
    assert (outside_project / "renders" / "final.mp4").is_file()
    assert final_render_status(projects, "qa")["current"] is True
    # ...and it is servable, which is the property that makes this layout legitimate:
    # get_file resolves both sides, so the resolved target stays under the resolved project.
    proj = (projects / "qa").resolve()
    target = (projects / "qa" / "renders" / "final.mp4").resolve()
    assert proj in target.parents


def test_symlinked_project_render_job_completes_and_reports_the_canonical_path(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    outside_project = tmp_path / "outside-job"
    (outside_project / "artifacts").mkdir(parents=True)
    (outside_project / "artifacts" / "edit_decisions.json").write_text(json.dumps(DOC))
    (projects / "qa").symlink_to(outside_project, target_is_directory=True)
    store = RenderJobStore(projects)
    store._tool = _FakeCompose()

    job_id = store.start_with_inputs("qa", {"edit_decisions": DOC})
    deadline = time.time() + 5
    while time.time() < deadline and store.status(job_id)["status"] not in TERMINAL_STATUSES:
        time.sleep(0.01)

    status = store.status(job_id)
    assert status["status"] == "done", status.get("error")
    assert status["output_path"] == "renders/final.mp4"
    # No .part.mp4 stranded by a late failure — that was the actual defect here.
    assert sorted(p.name for p in (outside_project / "renders").iterdir()) == [FINAL_RECEIPT_NAME, "final.mp4"]


# AUTHOR-ADJUSTED (round 3). The TOCTOU is REAL and is NOT fixed; xfail rather than deleted,
# so it stays visible and re-checkable. Refuted for this ticket on threat model:
# swapping renders/ mid-render requires an actor that ALREADY has write access to the project
# directory — and such an actor can simply overwrite renders/final.mp4 directly, with no race
# at all. The receipt is what makes either case detectable (final_render_status reads STALE),
# which is the invariant plan.md §3 actually claims: presentation and provability, not
# exclusive write access. Closing it properly means openat/O_NOFOLLOW directory handles
# threaded through shutil.copy2, os.replace AND atomic_write_json — a filesystem-hardening
# change with its own risk surface, which belongs in its own ticket, not in a desync fix.
@pytest.mark.xfail(reason="accepted TOCTOU: needs O_NOFOLLOW dir handles; see annotation", strict=True)
def test_renders_directory_swap_between_check_and_copy_cannot_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bind the checked directory to the write; reusing its pathname leaves a TOCTOU
    where another project writer can replace it with a symlink after validation."""
    projects, project_id, root = _project(tmp_path)
    outside = tmp_path / "race-outside"
    outside.mkdir()
    renders = root / "renders"
    real_copy2 = project_mod.shutil.copy2

    def swap_then_copy(src, dst, *args, **kwargs):
        renders.rmdir()
        renders.symlink_to(outside, target_is_directory=True)
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(project_mod.shutil, "copy2", swap_then_copy)
    with contextlib.suppress(project_mod.RendersDirEscapes):
        publish_final_render(projects, project_id, _mkfile(tmp_path, "race.mp4"), receipt_doc=DOC)

    assert not any(outside.iterdir())


@pytest.mark.parametrize(
    "raw",
    [r"renders\\nested.mp4", r"C:\\temp\\outside.mp4", r"\\\\server\\share\\out.mp4"],
)
def test_windows_style_paths_fall_back_on_posix(tmp_path: Path, raw: str) -> None:
    projects, project_id, root = _project(tmp_path)
    store = RenderJobStore(projects)

    assert store._normalize_output_path(project_id, raw, "final.mp4") == (root.resolve() / "renders" / "final.mp4")


def _mkfile(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"bytes")
    return p


def _overlay(box: dict) -> dict:
    return {"type": "text", "text": "alpha", "box": box, "start_seconds": 0, "end_seconds": 1}


# AUTHOR-ADJUSTED (round 2). The reviewer's original version asserted the RENDERER should
# fold an embedded @alpha into box.opacity (emit `red@0.27`). The finding — preview and
# export disagreed — is confirmed and fixed, but on the preview side, because ffmpeg's real
# behaviour (measured against ffmpeg 8, not its docs) is:
#     boxcolor=#CC785C80@0.9  renders IDENTICALLY to  boxcolor=#CC785C@0.9
#         -> the @suffix OVERRIDES a hex AA byte; it does not multiply with it
#     boxcolor=red@0.3@0.9    -> "Invalid alpha value specifier '0.3@0.9'"  (render fails)
#     boxcolor=#CCC@0.9       -> "Invalid 0xRRGGBB[AA] color string: 'CCC'" (render fails)
# So teaching the renderer to multiply would INVENT an export semantic for an input ffmpeg
# rejects, and would change the pixels of every existing caption. The preview now uses
# box.opacity alone (web/src/studio/model.js ffBoxBackground, mirrored in model.test.js),
# and the two shapes ffmpeg rejects are refused early, by field name, instead of failing
# deep in the filtergraph. No repo playbook or skill passes either shape.
def test_renderer_emits_one_alpha_suffix_and_refuses_a_second() -> None:
    error, drawtext, _warnings = VideoCompose()._build_drawtext_filter(
        _overlay({"color": "#CC785C", "opacity": 0.9, "padding": 10}), 0, 1080, 1920
    )
    assert error is None
    assert "boxcolor=#CC785C@0.9" in drawtext  # exactly ONE alpha, from box.opacity

    error, drawtext, _warnings = VideoCompose()._build_drawtext_filter(
        _overlay({"color": "red@0.3", "opacity": 0.9, "padding": 10}), 0, 1080, 1920
    )
    assert drawtext is None
    assert "box.color" in error and "@alpha" in error  # named field, not an ffmpeg parse error
    assert "box.opacity" in error  # and what to use instead


@pytest.mark.parametrize("color", ["#CCC", "notacolor", "red@"])
def test_renderer_rejects_invalid_box_colors_before_ffmpeg(color: str) -> None:
    """The preview fallback cannot claim an early, field-specific refusal unless the
    renderer actually rejects every shape/name that ffmpeg rejects before filtergraph
    execution.  A charset-only check accepts both of these today."""
    error, drawtext, _warnings = VideoCompose()._build_drawtext_filter(
        _overlay({"color": color, "opacity": 0.9, "padding": 10}), 0, 1080, 1920
    )

    assert drawtext is None
    assert "box.color" in error


@pytest.mark.parametrize(
    "color",
    ["white@00.5", "white@1.", "white@0x0"],
)
def test_renderer_keeps_ffmpeg_legal_fontcolor_alpha_spellings(color: str) -> None:
    """Do not turn a working reel into a validation failure merely because ffmpeg
    accepts more numeric spellings than the helper regex anticipated."""
    error, drawtext, _warnings = VideoCompose()._build_drawtext_filter(
        {"type": "text", "text": "alpha", "color": color}, 0, 1080, 1920
    )

    assert error is None
    assert drawtext is not None


def test_renderer_rejects_uppercase_hex_alpha_prefix_before_ffmpeg() -> None:
    """FFmpeg accepts lowercase ``0x80`` but rejects uppercase ``0X80``."""
    error, drawtext, _warnings = VideoCompose()._build_drawtext_filter(
        {"type": "text", "text": "alpha", "color": "white@0X80"}, 0, 1080, 1920
    )

    assert drawtext is None
    assert "color" in error


# AUTHOR-ADJUSTED (round 2). The finding is confirmed and fixed: the documented gate
# hardcoded the projects root AND a dev-only interpreter path. The reviewer's original test
# ran the OLD command under OPENNOLAN_PROJECTS_DIR, which can never pass — final_render_status
# takes an explicit projects_dir by design (the app injects it; see server/app.py), so it is
# the DOC that had to change. This version pins both halves of the fix: the doc no longer
# hardcodes either, and the mechanism it now documents actually resolves the configured root.
QA_DIRECTOR = Path(__file__).resolve().parents[2] / "skills/pipelines/instagram-fast-reel/qa-director.md"


def test_documented_qa_gate_uses_the_configured_projects_directory(tmp_path: Path) -> None:
    projects, project_id, _ = _project(tmp_path)
    source = tmp_path / "render.mp4"
    source.write_bytes(b"rendered")
    publish_final_render(projects, project_id, source, receipt_doc=DOC)

    doc = QA_DIRECTOR.read_text()
    assert "app_paths.projects_dir()" in doc
    assert "final_render_status('projects'" not in doc  # not the repo-relative guess
    assert ".venv/bin/python" not in doc  # AGENT_GUIDE.md:278

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json;from lib import app_paths;from lib.project import final_render_status;"
            f"print(json.dumps(final_render_status(app_paths.projects_dir(),{project_id!r})))",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=dict(os.environ, OPENNOLAN_PROJECTS_DIR=str(projects)),
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(result.stdout)["current"] is True
