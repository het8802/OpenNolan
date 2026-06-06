"""Tests for tools/video/motion_ops.py (Edits-parity Wave 4).

Validation/guard paths are pure (no ffmpeg). Real transforms run actual ffmpeg behind an
ffmpeg skipif and assert the resulting duration change + asset_manifest provenance.
"""

from __future__ import annotations

import json
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


def _dur(tool, path):
    return tool._probe(path).get("duration_seconds")


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
def test_reverse_keeps_duration(tool, clip, tmp_path):
    out = tmp_path / "rev.mp4"
    res = tool.execute({"operation": "reverse", "input_path": str(clip), "output_path": str(out)})
    assert res.success, res.error
    assert abs(res.data["duration_seconds"] - 2.0) < 0.3


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
def test_segment_volume(tool, clip, tmp_path):
    out = tmp_path / "sv.mp4"
    res = tool.execute({
        "operation": "segment_volume", "input_path": str(clip),
        "segments": [{"start": 0, "end": 1, "volume": 0.2}], "output_path": str(out),
    })
    assert res.success, res.error


@needs_ffmpeg
def test_segment_volume_needs_audio(tool, silent_clip):
    res = tool.execute({
        "operation": "segment_volume", "input_path": str(silent_clip),
        "segments": [{"start": 0, "end": 1, "volume": 0.2}],
    })
    assert res.success is False and "audio" in res.error


@needs_ffmpeg
def test_volume_boost_ok(tool, clip, tmp_path):
    out = tmp_path / "vb.mp4"
    res = tool.execute({"operation": "volume_boost", "input_path": str(clip), "gain": 1.4, "output_path": str(out)})
    assert res.success, res.error


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
