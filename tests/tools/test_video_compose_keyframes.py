"""Tests for the keyframe overlay renderer in video_compose._overlay (Edits-parity Wave 2).

Expr-builder and warning logic are pure (no ffmpeg). The full render is exercised behind an
ffmpeg skipif and confirms a keyframed overlay (slide + fade) actually encodes, while static
overlays keep working (regression).
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


# --- expression builder (pure) -------------------------------------------

def test_piecewise_expr_single_point_is_constant(vc):
    assert vc._piecewise_linear_expr([(0.0, 5.0)]) == "5.0"


def test_piecewise_expr_two_points_interpolate_and_hold(vc):
    expr = vc._piecewise_linear_expr([(0.0, 0.0), (1.0, 100.0)])
    # holds 0 before t=0, interpolates in (0,1), holds 100 after
    assert "if(lt(t,0.0),0.0" in expr
    assert "if(lt(t,1.0)" in expr
    assert expr.rstrip(")").endswith("100.0") or "100.0" in expr


def test_kf_points_extracts_only_specified_dimension(vc):
    kfs = [{"t": 0, "x": 10}, {"t": 1, "opacity": 1}, {"t": 2, "x": 50}]
    assert vc._kf_points(kfs, "x") == [(0.0, 10.0), (2.0, 50.0)]
    assert vc._kf_points(kfs, "opacity") == [(1.0, 1.0)]


def test_kf_points_sorted_by_time(vc):
    kfs = [{"t": 2, "x": 1}, {"t": 0, "x": 2}]
    assert [t for t, _ in vc._kf_points(kfs, "x")] == [0.0, 2.0]


# --- warning logic (pure) -------------------------------------------------

def test_scale_rotation_keyframes_warn(vc):
    kfs = [{"t": 0, "x": 0}, {"t": 1, "x": 100, "scale": 1.2}]
    res = vc._keyframe_overlay(kfs, 0, 0, 0, 0, "1:v", "0:v", "v0", "between(t,0,2)")
    assert any("scale/rotation" in w for w in res["warnings"])
    assert any("overlay=x=" in f for f in res["filters"])


def test_fade_in_emits_fade_filter(vc):
    kfs = [{"t": 0, "x": -50, "opacity": 0.0}, {"t": 0.5, "x": 100, "opacity": 1.0}]
    res = vc._keyframe_overlay(kfs, 100, 0, 0, 0, "1:v", "0:v", "v0", "between(t,0,2)")
    joined = ";".join(res["filters"])
    assert "fade=t=in" in joined and "format=yuva420p" in joined


# --- real render ----------------------------------------------------------

@needs_ffmpeg
def test_keyframed_overlay_renders(vc, tmp_path):
    base = tmp_path / "base.mp4"
    ov = tmp_path / "ov.png"
    out = tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2:r=25",
                    "-pix_fmt", "yuv420p", str(base)], capture_output=True, check=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=50x50:d=1",
                    "-frames:v", "1", str(ov)], capture_output=True, check=True)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{
            "asset_path": str(ov), "x": 130, "y": 95, "start_seconds": 0, "end_seconds": 2,
            "keyframes": [
                {"t": 0.0, "x": -60, "opacity": 0.0, "easing": "ease-out"},
                {"t": 0.6, "x": 130, "opacity": 1.0},
            ],
        }],
    })
    assert res.success, res.error
    assert out.exists() and out.stat().st_size > 0


@needs_ffmpeg
def test_static_overlay_still_works(vc, tmp_path):
    base = tmp_path / "base.mp4"
    ov = tmp_path / "ov.png"
    out = tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1:r=25",
                    "-pix_fmt", "yuv420p", str(base)], capture_output=True, check=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=green:s=40x40:d=1",
                    "-frames:v", "1", str(ov)], capture_output=True, check=True)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{"asset_path": str(ov), "x": 10, "y": 10, "start_seconds": 0, "end_seconds": 1}],
    })
    assert res.success, res.error
    assert out.exists()
