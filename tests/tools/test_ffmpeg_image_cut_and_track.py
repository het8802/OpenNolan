"""Manual-editor backend additions:

1. A still IMAGE can be a MAIN-timeline cut on the FFmpeg path (previously rejected). The
   editor's "render once, edit cheap" model renders each scene SOLO via _compose, so an image
   cut must loop into a video segment of the cut's duration — both directly (_compose) and
   end-to-end through render_proxies (the path server/render_jobs.py drives).
2. overlays[].track sets z-order: overlays composite in ASCENDING track, so a HIGHER track lands
   on top regardless of array order.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.video.video_compose import VideoCompose

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture
def vc():
    return VideoCompose()


def _solid_still(path: Path, color: str, size: str = "200x200") -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:d=1",
         "-frames:v", "1", str(path)],
        capture_output=True, check=True,
    )


def _black_clip(path: Path, *, dur: float = 2.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=320x240:d={dur}:r=24",
         "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True,
    )


def _duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        text=True,
    )
    return float(out.strip())


def _rgb_at(out: Path, t: float, xy: tuple[int, int], tmp: Path) -> tuple[int, int, int]:
    from PIL import Image
    frame = tmp / "frame.png"
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", str(out), "-frames:v", "1", str(frame)],
                   capture_output=True, check=True)
    return Image.open(frame).convert("RGB").getpixel(xy)


# ── still image as a main-timeline cut ───────────────────────────────────────
@needs_ffmpeg
def test_compose_accepts_still_image_as_main_cut(vc, tmp_path):
    """A still image cut renders to a clip of the cut's duration (was: rejected outright)."""
    img = tmp_path / "photo.png"
    out = tmp_path / "out.mp4"
    _solid_still(img, "white", size="640x480")

    res = vc._compose({
        "edit_decisions": {
            "version": "1.0",
            "render_runtime": "ffmpeg",
            "cuts": [{"id": "img1", "source": str(img), "in_seconds": 0, "out_seconds": 2.0}],
        },
        "output_path": str(out),
    })
    assert res.success, res.error
    assert out.exists() and out.stat().st_size > 0
    assert _duration(out) == pytest.approx(2.0, abs=0.25), "image cut must hold for its duration"


@needs_ffmpeg
def test_zero_duration_image_cut_fails_fast_not_hangs(vc, tmp_path):
    """A zero-duration image cut must be REJECTED, not looped forever (`-loop 1 -t 0` hangs)."""
    img = tmp_path / "photo.png"
    out = tmp_path / "out.mp4"
    _solid_still(img, "white")
    res = vc._compose({
        "edit_decisions": {
            "version": "1.0", "render_runtime": "ffmpeg",
            "cuts": [{"id": "img1", "source": str(img), "in_seconds": 2, "out_seconds": 2}],
        },
        "output_path": str(out),
    })
    assert not res.success
    assert "out_seconds > in_seconds" in (res.error or "")


@needs_ffmpeg
def test_render_proxies_one_call_with_an_image_cut(vc, tmp_path):
    """The editor render path (render_proxies → solo _compose → assemble) handles an image cut."""
    pytest.importorskip("PIL.Image")
    img = tmp_path / "photo.png"
    out = tmp_path / "final.mp4"
    _solid_still(img, "white", size="640x480")
    vc._run_final_review = lambda *a, **k: {"status": "pass", "issues_found": []}

    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": {
            "version": "1.0",
            "render_runtime": "ffmpeg",
            "renderer_family": "social-reel",
            "cuts": [{"id": "img1", "source": str(img), "in_seconds": 0, "out_seconds": 2.0}],
        },
        "asset_manifest": {"assets": []},
        "output_path": str(out),
        "proxies_dir": str(tmp_path / "proxies"),
    })
    assert res.success, res.error
    assert out.exists() and out.stat().st_size > 0
    # the white image is centered (letterboxed) on the default 1920×1080 canvas — sample the center
    r, g, b = _rgb_at(out, 1.0, (960, 540), tmp_path)
    assert min(r, g, b) > 200, "image-cut proxy should show the white still"


# ── crop baked into the proxy (regression: source-px crop > canvas crashed at assemble) ──
@needs_ffmpeg
def test_render_proxies_bakes_sourcepx_crop_larger_than_canvas(vc, tmp_path):
    """A cut whose SOURCE-pixel crop is larger than the output canvas must render. Before the fix,
    crop was re-applied at the assemble layer on the canvas-sized proxy → `crop=1440x2560 on a
    1080x1920 proxy` → ffmpeg exit 234. Now crop bakes into the proxy at native resolution."""
    big = tmp_path / "big.mp4"   # 1600x2400 source, larger than the 1080x1920 canvas
    out = tmp_path / "final.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1600x2400:d=2:r=30",
         "-pix_fmt", "yuv420p", str(big)],
        capture_output=True, check=True,
    )
    vc._run_final_review = lambda *a, **k: {"status": "pass", "issues_found": []}
    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": {
            "version": "1.0", "render_runtime": "ffmpeg", "renderer_family": "social-reel",
            "metadata": {"compose_target": {"width": 1080, "height": 1920, "fps": 30}},
            "cuts": [{
                "id": "c1", "source": str(big), "in_seconds": 0, "out_seconds": 2, "speed": 1.5,
                "transform": {"crop": {"x": 100, "y": 0, "width": 1440, "height": 2400}},
            }],
        },
        "asset_manifest": {"assets": []},
        "output_path": str(out),
        "proxies_dir": str(tmp_path / "proxies"),
    })
    assert res.success, res.error
    assert out.exists() and out.stat().st_size > 0


# ── overlay track z-order ─────────────────────────────────────────────────────
@needs_ffmpeg
def test_overlay_track_controls_zorder_regardless_of_array_order(vc, tmp_path):
    """Two fully-overlapping overlays: the HIGHER `track` wins even though it's FIRST in the
    array (array order alone would put the last one on top)."""
    pytest.importorskip("PIL.Image")
    clip = tmp_path / "base.mp4"
    lime = tmp_path / "lime.png"
    red = tmp_path / "red.png"
    out = tmp_path / "out.mp4"
    _black_clip(clip)
    _solid_still(lime, "lime")  # (0,255,0)
    _solid_still(red, "red")    # (255,0,0)
    vc._run_final_review = lambda *a, **k: {"status": "pass", "issues_found": []}

    edit_decisions = {
        "version": "1.0",
        "render_runtime": "ffmpeg",
        "cuts": [{"id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 2}],
        # lime is FIRST in the array but on a HIGHER track → it must end up on top.
        "overlays": [
            {"asset_id": "lime", "start_seconds": 0, "end_seconds": 2, "position": {"x": 0, "y": 0}, "track": 1},
            {"asset_id": "red", "start_seconds": 0, "end_seconds": 2, "position": {"x": 0, "y": 0}, "track": 0},
        ],
    }
    asset_manifest = {"assets": [
        {"id": "lime", "type": "image", "path": str(lime)},
        {"id": "red", "type": "image", "path": str(red)},
    ]}

    res = vc._render_via_ffmpeg(
        inputs={}, edit_decisions=edit_decisions, asset_manifest=asset_manifest,
        resolved_cuts=edit_decisions["cuts"], output_path=out, profile=None,
    )
    assert res.success, res.error
    r, g, b = _rgb_at(out, 1.0, (50, 50), tmp_path)
    assert g > 150 and r < 100, f"higher track (lime) must be on top; got rgb=({r},{g},{b})"


@needs_ffmpeg
def test_default_track_preserves_legacy_array_order(vc, tmp_path):
    """Without a track field (legacy docs), z-order falls back to array order (last on top)."""
    pytest.importorskip("PIL.Image")
    clip = tmp_path / "base.mp4"
    lime = tmp_path / "lime.png"
    red = tmp_path / "red.png"
    out = tmp_path / "out.mp4"
    _black_clip(clip)
    _solid_still(lime, "lime")
    _solid_still(red, "red")
    vc._run_final_review = lambda *a, **k: {"status": "pass", "issues_found": []}

    edit_decisions = {
        "version": "1.0",
        "render_runtime": "ffmpeg",
        "cuts": [{"id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 2}],
        # no track on either → stable sort keeps array order → red (last) on top.
        "overlays": [
            {"asset_id": "lime", "start_seconds": 0, "end_seconds": 2, "position": {"x": 0, "y": 0}},
            {"asset_id": "red", "start_seconds": 0, "end_seconds": 2, "position": {"x": 0, "y": 0}},
        ],
    }
    asset_manifest = {"assets": [
        {"id": "lime", "type": "image", "path": str(lime)},
        {"id": "red", "type": "image", "path": str(red)},
    ]}

    res = vc._render_via_ffmpeg(
        inputs={}, edit_decisions=edit_decisions, asset_manifest=asset_manifest,
        resolved_cuts=edit_decisions["cuts"], output_path=out, profile=None,
    )
    assert res.success, res.error
    r, g, b = _rgb_at(out, 1.0, (50, 50), tmp_path)
    assert r > 150 and g < 100, f"legacy array order must put the last overlay (red) on top; got ({r},{g},{b})"
