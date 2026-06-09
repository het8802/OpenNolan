"""Keystone regression: the FFmpeg render path must APPLY edit_decisions.overlays[].

Before the manual-editor work, `_render_via_ffmpeg` -> `_compose` rendered cuts/concat/
audio/subtitles but silently DROPPED overlays — the Wave-2 keyframe renderer was only
reachable via operation="overlay", never via operation="render". A project that locked
render_runtime="ffmpeg" with overlays lost them.

These tests pin the fix: a keyframed overlay is actually VISIBLE in the rendered output,
and the no-overlays case stays a single _compose pass (no behavior change, no stray base
file). final_review is stubbed to isolate the overlay WIRING from its heuristics.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.video.video_compose import VideoCompose

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")


@pytest.fixture
def vc():
    return VideoCompose()


def _black_clip(path: Path, *, dur: float = 3.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=320x240:d={dur}:r=24",
         "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True,
    )


def _white_still(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=white:s=120x120:d=1",
         "-frames:v", "1", str(path)],
        capture_output=True, check=True,
    )


def _max_luma_at(out: Path, t: float, tmp: Path) -> int:
    from PIL import Image
    frame = tmp / "frame.png"
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", str(out), "-frames:v", "1", str(frame)],
                   capture_output=True, check=True)
    return Image.open(frame).convert("L").getextrema()[1]


@needs_ffmpeg
def test_ffmpeg_render_applies_keyframed_overlay(vc, tmp_path):
    """A keyframed still overlay on an ffmpeg-runtime render is visible after the fade."""
    pytest.importorskip("PIL.Image")
    clip = tmp_path / "clip.mp4"
    overlay = tmp_path / "ov.png"
    out = tmp_path / "out.mp4"
    _black_clip(clip)
    _white_still(overlay)

    edit_decisions = {
        "version": "1.0",
        "render_runtime": "ffmpeg",
        "cuts": [{"id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 3}],
        "overlays": [{
            "asset_id": "title",
            "start_seconds": 0,
            "end_seconds": 3,
            "position": {"x": 100, "y": 60},
            "keyframes": [
                {"t": 0.0, "x": -120, "opacity": 0.0},
                {"t": 0.5, "x": 100, "opacity": 1.0},
            ],
        }],
    }
    asset_manifest = {"assets": [{"id": "title", "type": "image", "path": str(overlay)}]}

    # Isolate overlay WIRING from final_review's heuristics.
    vc._run_final_review = lambda *a, **k: {"status": "pass", "issues_found": []}

    res = vc._render_via_ffmpeg(
        inputs={},
        edit_decisions=edit_decisions,
        asset_manifest=asset_manifest,
        resolved_cuts=edit_decisions["cuts"],  # source already a path
        output_path=out,
        profile=None,
    )
    assert res.success, res.error
    assert out.exists() and out.stat().st_size > 0
    # well after the fade, the white overlay must be present on the black base
    assert _max_luma_at(out, 2.0, tmp_path) > 200, "overlay not visible — ffmpeg render dropped overlays"
    # the intermediate base file must be cleaned up
    assert not (tmp_path / "out_base.mp4").exists()


@needs_ffmpeg
def test_ffmpeg_render_without_overlays_is_single_pass(vc, tmp_path):
    """No overlays -> _compose writes output directly; _overlay is never called."""
    clip = tmp_path / "clip.mp4"
    out = tmp_path / "out.mp4"
    _black_clip(clip, dur=2.0)

    edit_decisions = {
        "version": "1.0",
        "render_runtime": "ffmpeg",
        "cuts": [{"id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 2}],
    }

    vc._run_final_review = lambda *a, **k: {"status": "pass", "issues_found": []}
    called = {"overlay": False}
    orig_overlay = vc._overlay
    def _spy(inputs):
        called["overlay"] = True
        return orig_overlay(inputs)
    vc._overlay = _spy

    res = vc._render_via_ffmpeg(
        inputs={},
        edit_decisions=edit_decisions,
        asset_manifest={},
        resolved_cuts=edit_decisions["cuts"],
        output_path=out,
        profile=None,
    )
    assert res.success, res.error
    assert out.exists()
    assert called["overlay"] is False, "single-pass render must not invoke the overlay pass"
    assert not (tmp_path / "out_base.mp4").exists()


@needs_ffmpeg
def test_overlay_asset_id_resolves_via_manifest(vc, tmp_path):
    """An overlay asset_id is resolved to a path via the asset_manifest."""
    pytest.importorskip("PIL.Image")
    clip = tmp_path / "clip.mp4"
    overlay = tmp_path / "logo.png"
    out = tmp_path / "out.mp4"
    _black_clip(clip)
    _white_still(overlay)

    edit_decisions = {
        "version": "1.0",
        "render_runtime": "ffmpeg",
        "cuts": [{"id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 3}],
        "overlays": [{
            "asset_id": "logo-1",  # resolved via manifest below, not a literal path
            "start_seconds": 0,
            "end_seconds": 3,
            "position": {"x": 50, "y": 50, "width": 80, "height": 80},
        }],
    }
    asset_manifest = {"assets": [{"id": "logo-1", "type": "image", "path": str(overlay)}]}

    vc._run_final_review = lambda *a, **k: {"status": "pass", "issues_found": []}
    res = vc._render_via_ffmpeg(
        inputs={}, edit_decisions=edit_decisions, asset_manifest=asset_manifest,
        resolved_cuts=edit_decisions["cuts"], output_path=out, profile=None,
    )
    assert res.success, res.error
    assert _max_luma_at(out, 1.5, tmp_path) > 200, "manifest-resolved overlay not visible"
