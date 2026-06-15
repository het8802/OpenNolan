"""Tests for tools/video/mask_ops.py (Edits-parity: masks on the main track).

Validation/guard paths are pure (no ffmpeg). Real transforms run actual ffmpeg behind an
ffmpeg skipif and assert measurable outcomes: blur_region changes pixels ONLY inside the
region (framemd5 on crops), dim_outside drops corner luma (signalstats YAVG), reveal_wipe
duration math, image_mask alpha presence (pix_fmt + alphaextract level).
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tools.video.mask_ops import MaskOps

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")

# 320x240 frame; region = center half of the frame -> pixels (80, 60, 160, 120)
CENTER_RECT = {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}


@pytest.fixture
def tool():
    return MaskOps()


@pytest.fixture
def clip(tmp_path):
    """A 2s synthetic clip with audio."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=2:r=25",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-pix_fmt", "yuv420p", "-shortest", str(p)],
        capture_output=True, check=True,
    )
    return p


@pytest.fixture
def clip_b(tmp_path):
    """A second 2s same-size clip (different pattern) for reveal_wipe."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "src_b.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=2:r=25",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
         "-pix_fmt", "yuv420p", "-shortest", str(p)],
        capture_output=True, check=True,
    )
    return p


@pytest.fixture
def small_clip(tmp_path):
    """A mismatched-size clip to trip reveal_wipe's same-size guard."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "small.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=160x120:d=2:r=25",
         "-pix_fmt", "yuv420p", str(p)],
        capture_output=True, check=True,
    )
    return p


@pytest.fixture
def white_mask(tmp_path):
    """An all-white PNG mask (white=keep -> fully opaque output)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "mask.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=white:s=320x240:d=0.1",
         "-frames:v", "1", str(p)],
        capture_output=True, check=True,
    )
    return p


def _crop_framemd5(path, crop):
    """Per-frame md5 of a cropped area of the video stream only."""
    proc = subprocess.run(
        ["ffmpeg", "-i", str(path), "-an", "-vf", f"crop={crop}", "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True,
    )
    return [line.split(",")[-1].strip() for line in proc.stdout.splitlines()
            if line and not line.startswith("#")]


def _crop_yavg(path, crop):
    """Mean signalstats YAVG over a cropped area."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-f", "lavfi",
         "-i", f"movie={path},crop={crop},signalstats",
         "-show_entries", "frame_tags=lavfi.signalstats.YAVG", "-of", "csv=p=0"],
        capture_output=True, text=True, check=True,
    )
    # some rows carry a trailing comma ("16,"), so strip it before parsing
    vals = [float(v.strip(",")) for v in proc.stdout.split() if v.strip(",")]
    assert vals, "signalstats produced no YAVG values"
    return sum(vals) / len(vals)


def _alpha_yavg(path):
    """Mean alpha level (alphaextract luma): ~255 opaque, ~0 transparent."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-f", "lavfi",
         "-i", f"movie={path},alphaextract,signalstats",
         "-show_entries", "frame_tags=lavfi.signalstats.YAVG", "-of", "csv=p=0"],
        capture_output=True, text=True, check=True,
    )
    vals = [float(v.strip(",")) for v in proc.stdout.split() if v.strip(",")]
    assert vals, "alphaextract produced no YAVG values"
    return sum(vals) / len(vals)


def _pix_fmt(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


# --- validation / guards (no ffmpeg) ----------------------------------------

def test_invalid_operation_rejected(tool):
    res = tool.execute({"operation": "vignette", "input_path": "x.mp4"})
    assert res.success is False and "operation must be one of" in res.error


def test_missing_input_rejected(tool):
    res = tool.execute({"operation": "blur_region", "input_path": "/nope/missing.mp4"})
    assert res.success is False and "not found" in res.error


def test_region_required(tool, tmp_path):
    fake = tmp_path / "f.mp4"
    fake.touch()
    res = tool.execute({"operation": "blur_region", "input_path": str(fake)})
    assert res.success is False and "region" in res.error and "NORMALIZED" in res.error


def test_bad_shape_rejected(tool, tmp_path):
    fake = tmp_path / "f.mp4"
    fake.touch()
    res = tool.execute({
        "operation": "blur_region", "input_path": str(fake),
        "shape": "hexagon", "region": CENTER_RECT,
    })
    assert res.success is False and "shape" in res.error


def test_region_past_frame_rejected(tool, tmp_path):
    fake = tmp_path / "f.mp4"
    fake.touch()
    res = tool.execute({
        "operation": "blur_region", "input_path": str(fake),
        "region": {"x": 0.8, "y": 0.1, "w": 0.5, "h": 0.5},
    })
    assert res.success is False and "past the frame" in res.error


def test_pixel_looking_region_rejected(tool, tmp_path):
    # pixel coords (e.g. x=80) fail the normalized 0..1 check loudly
    fake = tmp_path / "f.mp4"
    fake.touch()
    res = tool.execute({
        "operation": "blur_region", "input_path": str(fake),
        "region": {"x": 80, "y": 60, "w": 160, "h": 120},
    })
    assert res.success is False


def test_window_needs_both_ends(tool, tmp_path):
    fake = tmp_path / "f.mp4"
    fake.touch()
    res = tool.execute({
        "operation": "blur_region", "input_path": str(fake),
        "region": CENTER_RECT, "start": 0.5,
    })
    assert res.success is False and "together" in res.error


def test_window_end_after_start(tool, tmp_path):
    fake = tmp_path / "f.mp4"
    fake.touch()
    res = tool.execute({
        "operation": "blur_region", "input_path": str(fake),
        "region": CENTER_RECT, "start": 1.5, "end": 0.5,
    })
    assert res.success is False and "start < end" in res.error


def test_blur_strength_out_of_range(tool, tmp_path):
    fake = tmp_path / "f.mp4"
    fake.touch()
    res = tool.execute({
        "operation": "blur_region", "input_path": str(fake),
        "region": CENTER_RECT, "strength": 0,
    })
    assert res.success is False and "strength" in res.error


def test_dim_factor_out_of_range(tool, tmp_path):
    fake = tmp_path / "f.mp4"
    fake.touch()
    res = tool.execute({
        "operation": "dim_outside", "input_path": str(fake),
        "region": CENTER_RECT, "dim_factor": 1.2,
    })
    assert res.success is False and "dim_factor" in res.error


def test_image_mask_requires_mask_path(tool, tmp_path):
    fake = tmp_path / "f.mp4"
    fake.touch()
    res = tool.execute({"operation": "image_mask", "input_path": str(fake)})
    assert res.success is False and "mask_path" in res.error


def test_image_mask_rejects_non_mov_output(tool, tmp_path):
    fake = tmp_path / "f.mp4"
    fake.touch()
    res = tool.execute({
        "operation": "image_mask", "input_path": str(fake),
        "mask_path": str(fake), "output_path": str(tmp_path / "out.mp4"),
    })
    assert res.success is False and ".mov" in res.error and "alpha" in res.error


def test_reveal_wipe_requires_second_and_direction(tool, tmp_path):
    fake = tmp_path / "f.mp4"
    fake.touch()
    res = tool.execute({"operation": "reveal_wipe", "input_path": str(fake), "duration": 1})
    assert res.success is False and "second_path" in res.error
    res = tool.execute({
        "operation": "reveal_wipe", "input_path": str(fake),
        "second_path": str(fake), "direction": "diagonal", "duration": 1,
    })
    assert res.success is False and "direction" in res.error


def test_reveal_wipe_duration_positive(tool, tmp_path):
    fake = tmp_path / "f.mp4"
    fake.touch()
    res = tool.execute({
        "operation": "reveal_wipe", "input_path": str(fake),
        "second_path": str(fake), "direction": "left", "duration": 0,
    })
    assert res.success is False and "duration" in res.error


# --- blur_region -------------------------------------------------------------

@needs_ffmpeg
def test_blur_region_changes_only_inside_region(tool, clip, tmp_path):
    """Out-of-region pixels are bit-exact (lossless encode); in-region pixels change."""
    out = tmp_path / "blur.mp4"
    res = tool.execute({
        "operation": "blur_region", "input_path": str(clip),
        "region": CENTER_RECT, "strength": 12, "lossless": True, "output_path": str(out),
    })
    assert res.success, res.error
    # region in pixels is (80, 60)..(240, 180); top-left corner is far outside it
    in_corner = _crop_framemd5(clip, "32:32:0:0")
    out_corner = _crop_framemd5(out, "32:32:0:0")
    assert len(in_corner) == len(out_corner) > 0
    assert in_corner == out_corner  # untouched outside the region
    # the region center must actually be blurred
    in_center = _crop_framemd5(clip, "32:32:144:104")
    out_center = _crop_framemd5(out, "32:32:144:104")
    assert in_center != out_center


@needs_ffmpeg
def test_blur_region_circle_runs(tool, clip, tmp_path):
    out = tmp_path / "blur_c.mp4"
    res = tool.execute({
        "operation": "blur_region", "input_path": str(clip),
        "shape": "circle", "region": {"cx": 0.5, "cy": 0.5, "r": 0.25},
        "lossless": True, "output_path": str(out),
    })
    assert res.success, res.error
    assert abs(res.data["duration_seconds"] - 2.0) < 0.3
    # corner outside the circle's bounding box stays bit-exact
    assert _crop_framemd5(clip, "32:32:0:0") == _crop_framemd5(out, "32:32:0:0")


@needs_ffmpeg
def test_blur_region_time_window(tool, clip, tmp_path):
    """With a 1s..2s window, frames in 0s..1s stay bit-exact even INSIDE the region."""
    out = tmp_path / "blur_w.mp4"
    res = tool.execute({
        "operation": "blur_region", "input_path": str(clip),
        "region": CENTER_RECT, "start": 1.0, "end": 2.0,
        "lossless": True, "output_path": str(out),
    })
    assert res.success, res.error
    in_center = _crop_framemd5(clip, "32:32:144:104")
    out_center = _crop_framemd5(out, "32:32:144:104")
    assert len(in_center) == len(out_center)
    # 25fps -> first 25 frames are before the window: identical; later frames differ
    assert in_center[:24] == out_center[:24]
    assert in_center[30:] != out_center[30:]


@needs_ffmpeg
def test_blur_region_circle_off_frame_rejected(tool, clip):
    res = tool.execute({
        "operation": "blur_region", "input_path": str(clip),
        "shape": "circle", "region": {"cx": 0.05, "cy": 0.5, "r": 0.3},
    })
    assert res.success is False and "does not fit" in res.error


# --- dim_outside -------------------------------------------------------------

@needs_ffmpeg
def test_dim_outside_darkens_edges_keeps_region(tool, clip, tmp_path):
    out = tmp_path / "dim.mp4"
    res = tool.execute({
        "operation": "dim_outside", "input_path": str(clip),
        "region": CENTER_RECT, "dim_factor": 0.3, "lossless": True, "output_path": str(out),
    })
    assert res.success, res.error
    # top-RIGHT corner: bright in testsrc (~235) and far outside the center region
    corner_in = _crop_yavg(clip, "32:32:288:0")
    corner_out = _crop_yavg(out, "32:32:288:0")
    assert corner_in > 100  # sanity: the sampled corner is not already black
    assert corner_out < corner_in * 0.6  # spotlight dimmed the corner
    # the spotlit region itself is pasted back untouched (bit-exact)
    assert _crop_framemd5(clip, "32:32:144:104") == _crop_framemd5(out, "32:32:144:104")


@needs_ffmpeg
def test_dim_outside_circle_runs(tool, clip, tmp_path):
    out = tmp_path / "dim_c.mp4"
    res = tool.execute({
        "operation": "dim_outside", "input_path": str(clip),
        "shape": "circle", "region": {"cx": 0.5, "cy": 0.5, "r": 0.3},
        "dim_factor": 0.2, "output_path": str(out),
    })
    assert res.success, res.error
    assert _crop_yavg(out, "32:32:288:0") < _crop_yavg(clip, "32:32:288:0") * 0.6


# --- image_mask --------------------------------------------------------------

@needs_ffmpeg
def test_image_mask_output_has_alpha(tool, clip, white_mask, tmp_path):
    out = tmp_path / "masked.mov"
    res = tool.execute({
        "operation": "image_mask", "input_path": str(clip),
        "mask_path": str(white_mask), "output_path": str(out),
    })
    assert res.success, res.error
    assert _pix_fmt(out) in {"argb", "rgba", "bgra", "abgr", "yuva420p", "yuva444p"}
    assert _alpha_yavg(out) > 200  # white mask = keep everything -> opaque
    assert abs(res.data["duration_seconds"] - 2.0) < 0.3
    assert res.data["has_alpha"] is True


@needs_ffmpeg
def test_image_mask_invert(tool, clip, white_mask, tmp_path):
    out = tmp_path / "masked_inv.mov"
    res = tool.execute({
        "operation": "image_mask", "input_path": str(clip),
        "mask_path": str(white_mask), "invert": True, "output_path": str(out),
    })
    assert res.success, res.error
    assert _alpha_yavg(out) < 30  # inverted white mask -> fully transparent


@needs_ffmpeg
def test_image_mask_missing_mask_file(tool, clip):
    res = tool.execute({
        "operation": "image_mask", "input_path": str(clip),
        "mask_path": "/nope/mask.png",
    })
    assert res.success is False and "mask not found" in res.error


# --- reveal_wipe -------------------------------------------------------------

@needs_ffmpeg
def test_reveal_wipe_duration_math(tool, clip, clip_b, tmp_path):
    """2s + 2s clips with a 1s wipe -> ~3s output (offset = durA - duration)."""
    out = tmp_path / "wipe.mp4"
    res = tool.execute({
        "operation": "reveal_wipe", "input_path": str(clip),
        "second_path": str(clip_b), "direction": "left", "duration": 1.0,
        "output_path": str(out),
    })
    assert res.success, res.error
    assert 2.7 <= res.data["duration_seconds"] <= 3.3
    assert res.data["transition"] == "wipeleft"


@needs_ffmpeg
def test_reveal_wipe_circle(tool, clip, clip_b, tmp_path):
    out = tmp_path / "wipe_c.mp4"
    res = tool.execute({
        "operation": "reveal_wipe", "input_path": str(clip),
        "second_path": str(clip_b), "direction": "circle", "duration": 0.5,
        "output_path": str(out),
    })
    assert res.success, res.error
    assert 3.2 <= res.data["duration_seconds"] <= 3.8  # 2 + 2 - 0.5
    assert res.data["transition"] == "circleopen"


@needs_ffmpeg
def test_reveal_wipe_size_mismatch_rejected(tool, clip, small_clip):
    res = tool.execute({
        "operation": "reveal_wipe", "input_path": str(clip),
        "second_path": str(small_clip), "direction": "left", "duration": 1.0,
    })
    assert res.success is False and "same-size" in res.error


@needs_ffmpeg
def test_reveal_wipe_duration_longer_than_clip_rejected(tool, clip, clip_b):
    res = tool.execute({
        "operation": "reveal_wipe", "input_path": str(clip),
        "second_path": str(clip_b), "direction": "left", "duration": 5.0,
    })
    assert res.success is False and "exceeds" in res.error


# --- asset_manifest provenance ----------------------------------------------

@needs_ffmpeg
def test_registers_derived_asset_with_provenance(tool, clip, tmp_path):
    manifest = tmp_path / "asset_manifest.json"
    manifest.write_text(json.dumps({"version": "1.0", "assets": []}))
    out = tmp_path / "blur.mp4"
    res = tool.execute({
        "operation": "blur_region", "input_path": str(clip),
        "region": CENTER_RECT, "output_path": str(out),
        "asset_manifest_path": str(manifest), "scene_id": "scene-7",
    })
    assert res.success, res.error
    doc = json.loads(manifest.read_text())
    assert len(doc["assets"]) == 1
    a = doc["assets"][0]
    assert a["source_tool"] == "mask_ops"
    assert a["subtype"] == "blur_region"
    assert a["scene_id"] == "scene-7"
    assert a["type"] == "video"
    assert a["duration_seconds"] is not None  # re-probed
    assert str(manifest) in res.artifacts


@needs_ffmpeg
def test_invalid_manifest_warns_but_op_still_succeeds(tool, clip, tmp_path):
    manifest = tmp_path / "bad.json"
    manifest.write_text(json.dumps({"version": "1.0", "assets": "not-a-list"}))
    out = tmp_path / "blur.mp4"
    res = tool.execute({
        "operation": "blur_region", "input_path": str(clip),
        "region": CENTER_RECT, "output_path": str(out),
        "asset_manifest_path": str(manifest),
    })
    # the derived clip is valid; only registration failed -> warning, not failure
    assert res.success is True
    assert "asset_manifest_warning" in res.data
    assert out.exists()
