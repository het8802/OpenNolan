"""Tests for tools/video/motion_ops.py (Edits-parity Wave 4).

Validation/guard paths are pure (no ffmpeg). Real transforms run actual ffmpeg behind an
ffmpeg skipif and assert the resulting duration change + asset_manifest provenance.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from tools.video.motion_ops import MotionOps

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture
def tool():
    return MotionOps()


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
def silent_clip(tmp_path):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=2:r=25",
         "-pix_fmt", "yuv420p", str(p)],
        capture_output=True, check=True,
    )
    return p


@pytest.fixture
def static_clip(tmp_path):
    """2s clip where every frame is the same static, horizontally asymmetric pattern
    (smptebars). Any over-time change in a patch of the OUTPUT must come from baked
    motion, not source animation (testsrc animates, so it can't prove panning)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "static.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "smptebars=s=320x240:d=2:r=25",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-pix_fmt", "yuv420p", "-shortest", str(p)],
        capture_output=True, check=True,
    )
    return p


def _dur(tool, path):
    return tool._probe(path).get("duration_seconds")


def _frame_md5(path, t=0.0):
    """md5 of the decoded video frame at time t (video stream only)."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path),
         "-frames:v", "1", "-an", "-map", "0:v:0", "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True,
    )
    for line in proc.stdout.splitlines():
        if line and not line.startswith("#"):
            return line.split(",")[-1].strip()
    raise AssertionError(f"no frame decoded from {path} at t={t}")


def _patch_yavg(path, t=0.0, crop="40:40:0:60"):
    """Average luma of a patch (default: 40x40 at the frame's left edge). Stable to
    ~1 level under encode noise, so a >15-level move proves real pixel motion
    (framemd5 alone differs trivially after any re-encode)."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-vf", f"crop={crop},signalstats,metadata=print:file=-", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    for line in proc.stdout.splitlines():
        if "YAVG" in line:
            return float(line.split("=")[-1])
    raise AssertionError(f"no YAVG from {path} at t={t}")


def _mean_db(path, start, dur):
    """volumedetect mean_volume of a window — the sine fixture reads ~-21 dB,
    pure digital silence ~-91 dB."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-t", str(dur),
         "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", proc.stderr)
    assert m, f"no mean_volume in volumedetect output for {path}"
    return float(m.group(1))


def _frame_ssim(a, b, t):
    """SSIM between the frames of a and b at time t (same fps/timestamps assumed).
    Plain re-encode noise stays > 0.97; a real pixel effect drops well below."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-ss", str(t), "-i", str(a), "-ss", str(t), "-i", str(b),
         "-frames:v", "1", "-filter_complex", "[0:v][1:v]ssim", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"All:([\d.]+)", proc.stderr)
    assert m, f"no SSIM score comparing {a} and {b} at t={t}"
    return float(m.group(1))


def _pix_fmt(path):
    return subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt", "-of", "default=nw=1:nk=1", str(path)],
        text=True,
    ).strip()


# --- validation / guards (no ffmpeg) --------------------------------------

def test_invalid_operation_rejected(tool):
    res = tool.execute({"operation": "warp", "input_path": "x.mp4"})
    assert res.success is False and "operation must be one of" in res.error


def test_missing_input_rejected(tool):
    res = tool.execute({"operation": "reverse", "input_path": "/nope/missing.mp4"})
    assert res.success is False and "not found" in res.error


def test_atempo_chain_covers_full_range(tool):
    # 4.0 = 2.0 * 2.0 (no redundant trailing 1.0); 0.5 = single; 3.0 = 2.0 * 1.5
    assert tool._atempo_chain(4.0) == "atempo=2.0,atempo=2.0"
    assert tool._atempo_chain(0.5) == "atempo=0.5"
    assert tool._atempo_chain(3.0) == "atempo=2.0,atempo=1.5"


# --- speed -----------------------------------------------------------------

@needs_ffmpeg
def test_speed_2x_halves_duration(tool, clip, tmp_path):
    out = tmp_path / "fast.mp4"
    res = tool.execute({"operation": "speed", "input_path": str(clip), "factor": 2.0, "output_path": str(out)})
    assert res.success, res.error
    assert 0.9 <= res.data["duration_seconds"] <= 1.2  # ~1s from 2s


@needs_ffmpeg
def test_speed_4x_atempo_chain_runs(tool, clip, tmp_path):
    out = tmp_path / "f4.mp4"
    res = tool.execute({"operation": "speed", "input_path": str(clip), "factor": 4.0, "output_path": str(out)})
    assert res.success, res.error
    assert res.data["duration_seconds"] <= 0.8  # ~0.5s


@needs_ffmpeg
def test_speed_out_of_range_rejected(tool, clip):
    assert tool.execute({"operation": "speed", "input_path": str(clip), "factor": 10.0}).success is False
    assert tool.execute({"operation": "speed", "input_path": str(clip), "factor": 0.1}).success is False


# --- reverse ---------------------------------------------------------------

@needs_ffmpeg
def test_reverse_reverses_frames_and_keeps_duration(tool, clip, tmp_path):
    """A plain copy also preserves duration — prove the frame ORDER flipped: on the
    animated testsrc, output t~0.1 must match the source's END, not its start.
    (testsrc's bottom strip animates: ~136.5 luma at t=0.1 vs ~123.6 at t=1.86.)"""
    out = tmp_path / "rev.mp4"
    res = tool.execute({"operation": "reverse", "input_path": str(clip), "output_path": str(out)})
    assert res.success, res.error
    assert abs(res.data["duration_seconds"] - 2.0) < 0.3
    strip = "60:40:20:190"  # testsrc's animated bottom-left strip
    src_start = _patch_yavg(clip, 0.1, strip)
    src_end = _patch_yavg(clip, 1.86, strip)
    assert abs(src_start - src_end) > 8, "fixture premise: patch must animate over the clip"
    out_start = _patch_yavg(out, 0.1, strip)
    assert abs(out_start - src_end) < 4, f"reversed start ({out_start}) != source end ({src_end})"
    assert abs(out_start - src_start) > 8, "output still starts with the source's first frames"


@needs_ffmpeg
def test_reverse_silent_clip(tool, silent_clip, tmp_path):
    out = tmp_path / "rev.mp4"
    res = tool.execute({"operation": "reverse", "input_path": str(silent_clip), "output_path": str(out)})
    assert res.success, res.error  # no -af areverse when there's no audio


# --- freeze ----------------------------------------------------------------

@needs_ffmpeg
def test_freeze_extends_duration(tool, clip, tmp_path):
    out = tmp_path / "fz.mp4"
    res = tool.execute({
        "operation": "freeze", "input_path": str(clip),
        "at_seconds": 1.0, "duration": 1.0, "output_path": str(out),
    })
    assert res.success, res.error
    # 2s source + 1s held frame ~= 3s
    assert 2.7 <= res.data["duration_seconds"] <= 3.3


@needs_ffmpeg
def test_freeze_past_end_rejected(tool, clip):
    res = tool.execute({"operation": "freeze", "input_path": str(clip), "at_seconds": 99, "duration": 1})
    assert res.success is False and "past the clip end" in res.error


@needs_ffmpeg
def test_freeze_requires_duration(tool, clip):
    res = tool.execute({"operation": "freeze", "input_path": str(clip), "at_seconds": 1.0})
    assert res.success is False and "duration" in res.error


# --- volume ----------------------------------------------------------------

@needs_ffmpeg
def test_segment_volume_dips_only_the_segment(tool, clip, tmp_path):
    """volume=0.2 over [0,1] is a ~-14 dB windowed change; outside the segment the
    level must stay put. A dropped -af (no-op remux) fails both deltas."""
    out = tmp_path / "sv.mp4"
    res = tool.execute({
        "operation": "segment_volume", "input_path": str(clip),
        "segments": [{"start": 0, "end": 1, "volume": 0.2}], "output_path": str(out),
    })
    assert res.success, res.error
    inside = _mean_db(out, 0.05, 0.8)
    outside = _mean_db(out, 1.1, 0.8)
    src_level = _mean_db(clip, 1.1, 0.8)
    assert outside - inside > 10, f"segment not dipped: {outside} -> {inside} dB (expected ~-14)"
    assert abs(outside - src_level) < 2, f"audio outside the segment changed: {src_level} -> {outside} dB"


@needs_ffmpeg
def test_segment_volume_needs_audio(tool, silent_clip):
    res = tool.execute({
        "operation": "segment_volume", "input_path": str(silent_clip),
        "segments": [{"start": 0, "end": 1, "volume": 0.2}],
    })
    assert res.success is False and "audio" in res.error


@needs_ffmpeg
def test_volume_boost_raises_level(tool, clip, tmp_path):
    """gain=1.4 is +2.9 dB; assert the measured lift (calibrated +3.1 dB on the sine
    fixture) — a no-op remux reads ~+0 dB and fails."""
    out = tmp_path / "vb.mp4"
    res = tool.execute({"operation": "volume_boost", "input_path": str(clip), "gain": 1.4, "output_path": str(out)})
    assert res.success, res.error
    lift = _mean_db(out, 0.0, 1.8) - _mean_db(clip, 0.0, 1.8)
    assert 1.5 < lift < 4.5, f"volume_boost gain=1.4 measured {lift:+.1f} dB (expected ~+2.9)"


@needs_ffmpeg
def test_volume_boost_capped_at_150_percent(tool, clip):
    res = tool.execute({"operation": "volume_boost", "input_path": str(clip), "gain": 2.0})
    assert res.success is False and "150%" in res.error


# --- asset_manifest provenance --------------------------------------------

@needs_ffmpeg
def test_registers_derived_asset_with_provenance(tool, clip, tmp_path):
    manifest = tmp_path / "asset_manifest.json"
    manifest.write_text(json.dumps({"version": "1.0", "assets": []}))
    out = tmp_path / "rev.mp4"
    res = tool.execute({
        "operation": "reverse", "input_path": str(clip),
        "output_path": str(out), "asset_manifest_path": str(manifest), "scene_id": "scene-3",
    })
    assert res.success, res.error
    doc = json.loads(manifest.read_text())
    assert len(doc["assets"]) == 1
    a = doc["assets"][0]
    assert a["source_tool"] == "motion_ops"
    assert a["subtype"] == "reverse"
    assert a["scene_id"] == "scene-3"
    assert a["type"] == "video"
    assert a["duration_seconds"] is not None  # re-probed
    assert str(manifest) in res.artifacts


@needs_ffmpeg
def test_invalid_manifest_warns_but_op_still_succeeds(tool, clip, tmp_path):
    manifest = tmp_path / "bad.json"
    manifest.write_text(json.dumps({"version": "1.0", "assets": "not-a-list"}))
    out = tmp_path / "rev.mp4"
    res = tool.execute({
        "operation": "reverse", "input_path": str(clip),
        "output_path": str(out), "asset_manifest_path": str(manifest),
    })
    # the derived clip is valid; only registration failed -> warning, not failure
    assert res.success is True
    assert "asset_manifest_warning" in res.data
    assert out.exists()


# --- pan_zoom ----------------------------------------------------------------

def test_lerp_expr_constant_and_segments():
    # single keyframe -> constant expression
    assert MotionOps._lerp_expr([(0.0, 1.5)]) == "1.5"
    e = MotionOps._lerp_expr([(0.0, 1.0), (2.0, 1.5)])
    assert e.startswith("if(lt(it,0)")  # holds the first value before the first keyframe
    assert "(it-0)/2" in e and "0.5" in e  # the 0->2s segment lerps by +0.5
    # coincident keyframes must not divide by zero
    e2 = MotionOps._lerp_expr([(1.0, 1.0), (1.0, 2.0)])
    assert "/0" not in e2.replace("/0.", "/X")


@needs_ffmpeg
def test_pan_zoom_requires_keyframes_xor_preset(tool, clip):
    res = tool.execute({"operation": "pan_zoom", "input_path": str(clip)})
    assert res.success is False and "exactly one of" in res.error
    res = tool.execute({
        "operation": "pan_zoom", "input_path": str(clip),
        "preset": "punch_in", "keyframes": [{"t": 0}],
    })
    assert res.success is False and "exactly one of" in res.error


@needs_ffmpeg
def test_pan_zoom_zoom_out_of_range_rejected(tool, clip):
    for bad_zoom in (5.0, 0.5):
        res = tool.execute({
            "operation": "pan_zoom", "input_path": str(clip),
            "keyframes": [{"t": 0, "zoom": bad_zoom}],
        })
        assert res.success is False and "zoom" in res.error


@needs_ffmpeg
def test_pan_zoom_bad_preset_rejected(tool, clip):
    res = tool.execute({"operation": "pan_zoom", "input_path": str(clip), "preset": "dolly_3d"})
    assert res.success is False and "preset" in res.error


@needs_ffmpeg
def test_pan_zoom_bad_pan_rejected(tool, clip):
    res = tool.execute({
        "operation": "pan_zoom", "input_path": str(clip),
        "keyframes": [{"t": 0, "zoom": 1.2, "x_pan": 1.5}],
    })
    assert res.success is False and "x_pan" in res.error


@needs_ffmpeg
def test_pan_zoom_ken_burns_pans_static_source(tool, static_clip, tmp_path):
    # premise: the static source's left-edge patch luma is time-invariant
    assert abs(_patch_yavg(static_clip, 0.0) - _patch_yavg(static_clip, 1.8)) < 3
    out = tmp_path / "kb.mp4"
    res = tool.execute({
        "operation": "pan_zoom", "input_path": str(static_clip),
        "preset": "ken_burns_lr", "preset_params": {"max_zoom": 1.3},
        "output_path": str(out),
    })
    assert res.success, res.error
    assert abs(res.data["duration_seconds"] - 2.0) < 0.3  # duration preserved
    assert res.data["resolution"] == "320x240"  # s=WxH locked to source
    # the pan must actually move content through the left-edge patch over time
    assert abs(_patch_yavg(out, 0.0) - _patch_yavg(out, 1.8)) > 15


@needs_ffmpeg
def test_pan_zoom_keyframes_duration_resolution_audio(tool, clip, tmp_path):
    out = tmp_path / "kf.mp4"
    res = tool.execute({
        "operation": "pan_zoom", "input_path": str(clip),
        "keyframes": [{"t": 0, "zoom": 1.0}, {"t": 2, "zoom": 1.5, "x_pan": 0.3}],
        "output_path": str(out),
    })
    assert res.success, res.error
    assert abs(res.data["duration_seconds"] - 2.0) < 0.3
    assert res.data["resolution"] == "320x240"
    assert tool._has_audio(out)  # audio passed through (-c:a copy)


# --- clip_fx -----------------------------------------------------------------

def test_glitch_bursts_seeded_deterministic():
    a = MotionOps._glitch_bursts(0.0, 2.0, seed=7)
    assert a == MotionOps._glitch_bursts(0.0, 2.0, seed=7)
    assert a != MotionOps._glitch_bursts(0.0, 2.0, seed=8)
    assert a and all(0.0 <= b0 < b1 <= 2.0 for b0, b1 in a)
    # tiny window still produces at least one burst
    assert MotionOps._glitch_bursts(0.5, 0.55, seed=1)


@needs_ffmpeg
def test_clip_fx_invalid_effect_rejected(tool, clip):
    res = tool.execute({"operation": "clip_fx", "input_path": str(clip), "effect": "explode"})
    assert res.success is False and "effect" in res.error


@needs_ffmpeg
def test_clip_fx_bad_window_rejected(tool, clip):
    res = tool.execute({
        "operation": "clip_fx", "input_path": str(clip),
        "effect": "shake", "start": 1.5, "end": 0.5,
    })
    assert res.success is False and "end > start" in res.error
    res = tool.execute({
        "operation": "clip_fx", "input_path": str(clip),
        "effect": "shake", "start": 99,
    })
    assert res.success is False and "past the clip end" in res.error


@needs_ffmpeg
def test_clip_fx_shake_jitters_static_source(tool, static_clip, tmp_path):
    out = tmp_path / "shake.mp4"
    res = tool.execute({
        "operation": "clip_fx", "input_path": str(static_clip),
        "effect": "shake", "intensity": 0.5, "output_path": str(out),
    })
    assert res.success, res.error
    assert res.data["resolution"] == "320x240"
    assert abs(res.data["duration_seconds"] - 2.0) < 0.3
    # static source, so two in-window frames can only differ if the crop window moved
    assert _frame_md5(out, 0.2) != _frame_md5(out, 1.0)


@needs_ffmpeg
def test_clip_fx_zoom_pulse_oscillates(tool, static_clip, tmp_path):
    out = tmp_path / "zp.mp4"
    res = tool.execute({
        "operation": "clip_fx", "input_path": str(static_clip),
        "effect": "zoom_pulse", "freq": 1.0, "amount": 0.3, "output_path": str(out),
    })
    assert res.success, res.error
    assert res.data["resolution"] == "320x240"
    assert abs(res.data["duration_seconds"] - 2.0) < 0.3
    # z=1 at t=0 vs z=1.3 at the half-period peak
    assert _frame_md5(out, 0.04) != _frame_md5(out, 0.5)


@needs_ffmpeg
def test_clip_fx_strobe_brightens_flash_frames(tool, static_clip, tmp_path):
    out = tmp_path / "st.mp4"
    res = tool.execute({
        "operation": "clip_fx", "input_path": str(static_clip),
        "effect": "strobe", "freq": 1.0, "output_path": str(out),
    })
    assert res.success, res.error
    # freq=1 -> flash window [0, 0.25): t=0.1 flashed, t=0.5 untouched
    assert _patch_yavg(out, 0.1) - _patch_yavg(out, 0.5) > 15


@needs_ffmpeg
def test_clip_fx_glitch_hits_only_inside_window(tool, clip, tmp_path):
    """Pixel-level proof like its shake/zoom_pulse/strobe siblings: frames inside a
    seeded burst diverge hard from the source (rgbashift+noise: SSIM ~0.72-0.86,
    calibrated) while out-of-window frames stay encode-noise close (> 0.99)."""
    out = tmp_path / "gl.mp4"
    res = tool.execute({
        "operation": "clip_fx", "input_path": str(clip),
        "effect": "glitch", "seed": 7, "start": 0.5, "end": 1.5, "output_path": str(out),
    })
    assert res.success, res.error
    assert abs(res.data["duration_seconds"] - 2.0) < 0.3
    # rgbashift forces an RGB pipeline; without an explicit -pix_fmt, libx264 emits
    # yuv444p (High 4:4:4 — unplayable on iOS/QuickTime). Lock the SDR contract.
    assert _pix_fmt(out) == "yuv420p"
    # seeded bursts are computed in Python, so the test knows exactly where to look
    bursts = MotionOps._glitch_bursts(0.5, 1.5, seed=7)
    b_start, _ = bursts[0]  # min burst len 0.06s @25fps always contains a frame
    in_burst = _frame_ssim(out, clip, b_start)
    before = _frame_ssim(out, clip, 0.1)
    after = _frame_ssim(out, clip, 1.9)
    assert before > 0.97 and after > 0.97, f"glitch leaked outside window ({before}, {after})"
    assert in_burst < 0.95, f"no glitch inside the burst window (SSIM {in_burst})"
    assert in_burst < min(before, after) - 0.05


# --- flip --------------------------------------------------------------------

@needs_ffmpeg
def test_flip_invalid_direction_rejected(tool, clip):
    res = tool.execute({"operation": "flip", "input_path": str(clip), "direction": "diagonal"})
    assert res.success is False and "direction" in res.error


@needs_ffmpeg
def test_flip_horizontal_changes_pixels(tool, static_clip, tmp_path):
    out = tmp_path / "hf.mp4"
    res = tool.execute({
        "operation": "flip", "input_path": str(static_clip),
        "direction": "horizontal", "output_path": str(out),
    })
    assert res.success, res.error
    assert res.data["resolution"] == "320x240"
    assert _frame_md5(out, 0.0) != _frame_md5(static_clip, 0.0)
    # stronger than md5 (any re-encode changes md5): smptebars' left edge is gray
    # (~Y180); after hflip the blue bar (~Y35) must be there instead
    assert abs(_patch_yavg(out, 0.0) - _patch_yavg(static_clip, 0.0)) > 15


@needs_ffmpeg
def test_flip_rotate_swaps_resolution(tool, clip, tmp_path):
    for direction in ("rotate_90_cw", "rotate_90_ccw"):
        out = tmp_path / f"{direction}.mp4"
        res = tool.execute({
            "operation": "flip", "input_path": str(clip),
            "direction": direction, "output_path": str(out),
        })
        assert res.success, res.error
        assert res.data["resolution"] == "240x320"  # 320x240 rotated, re-probed
