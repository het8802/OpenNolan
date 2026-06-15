"""Tests for the overlay renderer upgrade in video_compose (Edits-parity, stage 2 of 3).

Covers: time-varying scale keyframes (center-anchored), easing approximation,
static overlays[].opacity, aspect-preserving one-dimension sizing, animated-GIF
overlays (gif demuxer, looping), piecewise/non-monotonic opacity via geq, and
overlays[].audio_mix.

Validation and expression-builder tests are pure. Live tests run real ffmpeg on
lavfi-generated assets and assert MEASURABLE outcomes: white-pixel counts grow
with scale, luminance halves at opacity 0.5, bounding boxes keep aspect, framemd5
shows GIF motion, and RMS rises inside the audio_mix window.
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


# --- asset generators -------------------------------------------------------

def _black_base(path: Path, dur: float = 3.0, with_audio: bool = False) -> None:
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s=320x240:d={dur}:r=24"]
    if with_audio:
        cmd += ["-f", "lavfi", "-t", str(dur),
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, capture_output=True, check=True)


def _white_png(path: Path, w: int = 60, h: int = 60) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=white:s={w}x{h}:d=1",
         "-frames:v", "1", str(path)],
        capture_output=True, check=True,
    )


def _animated_gif(path: Path) -> None:
    """1s testsrc GIF (moving content, 10 fps) — loops by default GIF metadata."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=80x80:rate=10:duration=1",
         str(path)],
        capture_output=True, check=True,
    )


def _sine_clip(path: Path, dur: float = 2.0) -> None:
    """Red clip with a 440 Hz sine track (for audio_mix)."""
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"color=c=red:s=80x80:d={dur}:r=24",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(path)],
        capture_output=True, check=True,
    )


# --- frame measurement helpers ----------------------------------------------

def _gray_frame(out: Path, t: float, tmp: Path):
    from PIL import Image
    frame = tmp / f"frame_{t}.png"
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", str(out), "-frames:v", "1", str(frame)],
                   capture_output=True, check=True)
    return Image.open(frame).convert("L")


def _bright_count(img, threshold: int = 200) -> int:
    return sum(img.histogram()[threshold + 1:])


def _bright_bbox(img, threshold: int = 200):
    return img.point(lambda p: 255 if p > threshold else 0).getbbox()


def _mean_volume_db(out: Path, ss: float, t: float) -> float:
    proc = subprocess.run(
        ["ffmpeg", "-ss", str(ss), "-t", str(t), "-i", str(out),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    for line in (proc.stderr or "").splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].strip().split()[0])
    pytest.fail(f"volumedetect produced no mean_volume for {out}")


# --- easing subdivision (pure) -----------------------------------------------

def test_eased_points_linear_stays_exact(vc):
    kfs = [{"t": 0, "x": 0}, {"t": 1, "x": 100}]
    assert vc._eased_points(kfs, "x") == [(0.0, 0.0), (1.0, 100.0)]


def test_eased_points_ease_in_lags_linear(vc):
    kfs = [{"t": 0, "x": 0, "easing": "ease-in"}, {"t": 1, "x": 100}]
    pts = vc._eased_points(kfs, "x")
    assert len(pts) == vc.EASING_SUBDIVISIONS + 1
    mid = next(v for t, v in pts if abs(t - 0.5) < 1e-9)
    assert mid == pytest.approx(25.0)  # t^2 at u=0.5
    assert pts[0] == (0.0, 0.0) and pts[-1] == (1.0, 100.0)  # endpoints exact


def test_eased_points_ease_out_leads_linear(vc):
    kfs = [{"t": 0, "x": 0, "easing": "ease-out"}, {"t": 1, "x": 100}]
    mid = next(v for t, v in vc._eased_points(kfs, "x") if abs(t - 0.5) < 1e-9)
    assert mid == pytest.approx(75.0)  # 1-(1-u)^2 at u=0.5


def test_eased_points_step_holds_until_next_keyframe(vc):
    kfs = [{"t": 0, "x": 10, "easing": "step"}, {"t": 1, "x": 90}]
    pts = vc._eased_points(kfs, "x")
    assert pts[0] == (0.0, 10.0)
    assert pts[1][1] == 10.0 and pts[1][0] == pytest.approx(0.999)  # held value
    assert pts[-1] == (1.0, 90.0)


def test_eased_points_spring_overshoots(vc):
    kfs = [{"t": 0, "x": 0, "easing": "spring"}, {"t": 1, "x": 100}]
    pts = vc._eased_points(kfs, "x")
    assert max(v for _, v in pts) > 100.0  # damped sine overshoot
    assert pts[-1] == (1.0, 100.0)


# --- fade classification (pure) -----------------------------------------------

def test_simple_fade_plan_fade_in_out_exact(vc):
    plan = vc._simple_fade_plan([(0.0, 0.0), (0.5, 1.0), (2.5, 1.0), (3.0, 0.0)])
    assert plan == [
        "fade=t=in:st=0.0:d=0.5:alpha=1",
        "fade=t=out:st=2.5:d=0.5:alpha=1",
    ]


def test_simple_fade_plan_constant_full_is_noop(vc):
    assert vc._simple_fade_plan([(0.0, 1.0), (2.0, 1.0)]) == []


def test_simple_fade_plan_rejects_partial_and_dips(vc):
    assert vc._simple_fade_plan([(0.0, 1.0), (1.0, 0.5)]) is None   # partial target
    assert vc._simple_fade_plan([(0.0, 1.0), (1.0, 0.0), (2.0, 1.0)]) is None  # dip


def test_constant_partial_opacity_uses_colorchannelmixer(vc):
    kfs = [{"t": 0, "opacity": 0.4}, {"t": 2, "opacity": 0.4}]
    res = vc._keyframe_overlay(kfs, 0, 0, 0, 0, "1:v", "0:v", "v0", "between(t,0,2)")
    joined = ";".join(res["filters"])
    assert "colorchannelmixer=aa=0.4" in joined
    assert "geq" not in joined


def test_nonmonotonic_opacity_emits_geq(vc):
    kfs = [{"t": 0, "opacity": 1.0}, {"t": 1, "opacity": 0.2}, {"t": 2, "opacity": 1.0}]
    res = vc._keyframe_overlay(kfs, 0, 0, 0, 0, "1:v", "0:v", "v0", "between(t,0,2)")
    joined = ";".join(res["filters"])
    assert "geq=" in joined and "alpha(X,Y)" in joined
    assert res["warnings"] == []  # no more "left at full" warning


# --- input validation (pure; files exist but ffmpeg is never invoked) ----------

def test_bad_opacity_rejected(vc, tmp_path):
    base = tmp_path / "b.mp4"; base.write_bytes(b"x")
    ov = tmp_path / "o.png"; ov.write_bytes(b"x")
    res = vc._overlay({
        "input_path": str(base), "output_path": str(tmp_path / "out.mp4"),
        "overlays": [{"asset_path": str(ov), "x": 0, "y": 0, "opacity": 1.5}],
    })
    assert not res.success and "opacity" in res.error


def test_bad_audio_mix_volume_rejected(vc, tmp_path):
    base = tmp_path / "b.mp4"; base.write_bytes(b"x")
    ov = tmp_path / "o.mp4"; ov.write_bytes(b"x")
    res = vc._overlay({
        "input_path": str(base), "output_path": str(tmp_path / "out.mp4"),
        "overlays": [{"asset_path": str(ov), "x": 0, "y": 0,
                      "audio_mix": {"enabled": True, "volume": 3.0}}],
    })
    assert not res.success and "audio_mix.volume" in res.error


def test_bad_width_rejected(vc, tmp_path):
    base = tmp_path / "b.mp4"; base.write_bytes(b"x")
    ov = tmp_path / "o.png"; ov.write_bytes(b"x")
    res = vc._overlay({
        "input_path": str(base), "output_path": str(tmp_path / "out.mp4"),
        "overlays": [{"asset_path": str(ov), "x": 0, "y": 0, "width": -10}],
    })
    assert not res.success and "width" in res.error


# --- live renders -----------------------------------------------------------

@needs_ffmpeg
def test_scale_keyframes_grow_overlay_over_time(vc, tmp_path):
    """Scale 1.0 → 2.0 quadruples the overlay's white-pixel area; center stays put."""
    pytest.importorskip("PIL.Image")
    base, ov, out = tmp_path / "b.mp4", tmp_path / "o.png", tmp_path / "out.mp4"
    _black_base(base, dur=3.0)
    _white_png(ov, 60, 60)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{
            "asset_path": str(ov), "x": 130, "y": 90, "start_seconds": 0, "end_seconds": 3,
            "keyframes": [{"t": 0.0, "scale": 1.0}, {"t": 2.5, "scale": 2.0}],
        }],
    })
    assert res.success, res.error
    assert not any("scale" in w.lower() for w in (res.data.get("warnings") or []))

    early = _gray_frame(out, 0.2, tmp_path)
    late = _gray_frame(out, 2.8, tmp_path)
    n_early, n_late = _bright_count(early), _bright_count(late)
    assert n_early > 2000, f"overlay missing at start ({n_early}px)"
    assert n_late > 2.5 * n_early, f"scale did not animate: {n_early} -> {n_late} px"

    # center-anchored: bbox centers must coincide (within encoder noise)
    eb, lb = _bright_bbox(early), _bright_bbox(late)
    e_cx, e_cy = (eb[0] + eb[2]) / 2, (eb[1] + eb[3]) / 2
    l_cx, l_cy = (lb[0] + lb[2]) / 2, (lb[1] + lb[3]) / 2
    assert abs(e_cx - l_cx) <= 6 and abs(e_cy - l_cy) <= 6, (
        f"scale not center-anchored: center moved {eb} -> {lb}"
    )


@needs_ffmpeg
def test_scale_with_fade_and_static_opacity_still_animates(vc, tmp_path):
    """Regression: opacity filters must precede the animated scale in the chain.

    colorchannelmixer locks its frame size at config time — placed after an
    eval=frame scale it silently froze the scale animation at the first frame.
    This pins the combined fade-in + static opacity + scale-up case.
    """
    pytest.importorskip("PIL.Image")
    base, ov, out = tmp_path / "b.mp4", tmp_path / "o.png", tmp_path / "out.mp4"
    _black_base(base, dur=4.0)
    _white_png(ov, 60, 60)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{
            "asset_path": str(ov), "x": 130, "y": 90,
            "start_seconds": 0, "end_seconds": 4, "opacity": 0.8,
            "keyframes": [
                {"t": 0.0, "scale": 1.0, "opacity": 0.0, "easing": "ease-out"},
                {"t": 0.5, "opacity": 1.0},
                {"t": 3.5, "scale": 2.0},
            ],
        }],
    })
    assert res.success, res.error
    bb_early = _bright_bbox(_gray_frame(out, 0.7, tmp_path), threshold=120)
    bb_late = _bright_bbox(_gray_frame(out, 3.6, tmp_path), threshold=120)
    assert bb_early is not None, "overlay missing after fade-in"
    assert bb_late is not None, "overlay missing at end"
    w_early = bb_early[2] - bb_early[0]   # ~72px (scale ≈ 1.2 at t=0.7)
    w_late = bb_late[2] - bb_late[0]      # ~120px (scale = 2.0)
    assert w_late > 1.4 * w_early, (
        f"scale froze when combined with opacity filters: width {w_early} -> {w_late}"
    )
    assert abs(w_late - 120) <= 6, f"final scale wrong: width {w_late}, expected ~120"


@needs_ffmpeg
def test_ease_in_position_lags_linear_midpoint(vc, tmp_path):
    """ease-in x: at the temporal midpoint the overlay is well short of halfway."""
    pytest.importorskip("PIL.Image")
    base, ov, out = tmp_path / "b.mp4", tmp_path / "o.png", tmp_path / "out.mp4"
    _black_base(base, dur=2.5)
    _white_png(ov, 40, 40)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{
            "asset_path": str(ov), "x": 20, "y": 100, "start_seconds": 0, "end_seconds": 2.5,
            "keyframes": [
                {"t": 0.0, "x": 20, "opacity": 1.0, "easing": "ease-in"},
                {"t": 2.0, "x": 220, "opacity": 1.0},
            ],
        }],
    })
    assert res.success, res.error
    bbox = _bright_bbox(_gray_frame(out, 1.0, tmp_path))
    assert bbox is not None, "overlay not visible"
    # linear midpoint would be x=120; ease-in (t^2) puts it at 20+0.25*200=70
    assert bbox[0] < 100, f"ease-in not applied: x at midpoint = {bbox[0]} (linear would be 120)"


@needs_ffmpeg
def test_static_opacity_blends(vc, tmp_path):
    """opacity 0.5 white-on-black lands mid-gray — not opaque, not invisible."""
    pytest.importorskip("PIL.Image")
    base, ov, out = tmp_path / "b.mp4", tmp_path / "o.png", tmp_path / "out.mp4"
    _black_base(base, dur=2.0)
    _white_png(ov)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{"asset_path": str(ov), "x": 100, "y": 80,
                      "start_seconds": 0, "end_seconds": 2, "opacity": 0.5}],
    })
    assert res.success, res.error
    peak = _gray_frame(out, 1.0, tmp_path).getextrema()[1]
    assert 80 < peak < 200, f"opacity 0.5 not blended: max luma {peak} (opaque ~235, dropped ~16)"


@needs_ffmpeg
def test_width_only_scale_preserves_aspect(vc, tmp_path):
    """A 100x50 overlay sized with width=80 only must render ~80x40."""
    pytest.importorskip("PIL.Image")
    base, ov, out = tmp_path / "b.mp4", tmp_path / "o.png", tmp_path / "out.mp4"
    _black_base(base, dur=2.0)
    _white_png(ov, 100, 50)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{"asset_path": str(ov), "x": 50, "y": 50,
                      "start_seconds": 0, "end_seconds": 2, "width": 80}],
    })
    assert res.success, res.error
    bbox = _bright_bbox(_gray_frame(out, 1.0, tmp_path))
    assert bbox is not None, "overlay not visible"
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    assert abs(bw - 80) <= 2, f"width {bw} != 80"
    assert abs(bh - 40) <= 2, f"height {bh} != 40 — aspect not preserved"


@needs_ffmpeg
def test_gif_overlay_animates_and_loops(vc, tmp_path):
    """Animated GIF renders distinct frames and keeps animating past its 1s length."""
    base, gif, out = tmp_path / "b.mp4", tmp_path / "a.gif", tmp_path / "out.mp4"
    _black_base(base, dur=3.0)
    _animated_gif(gif)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{"asset_path": str(gif), "x": 100, "y": 60,
                      "start_seconds": 0, "end_seconds": 3}],
    })
    assert res.success, res.error

    md5s = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-i", str(out), "-f", "framemd5", "-"], text=True,
    )
    hashes = {line.split(",")[-1].strip() for line in md5s.splitlines()
              if line and not line.startswith("#")}
    assert len(hashes) >= 2, "GIF overlay frozen — all output frames identical"

    # past the GIF's natural 1s duration it must STILL be animating (loop, not freeze)
    f_a = subprocess.run(["ffmpeg", "-y", "-ss", "2.0", "-i", str(out), "-frames:v", "1",
                          str(tmp_path / "fa.png")], capture_output=True)
    f_b = subprocess.run(["ffmpeg", "-y", "-ss", "2.4", "-i", str(out), "-frames:v", "1",
                          str(tmp_path / "fb.png")], capture_output=True)
    assert f_a.returncode == 0 and f_b.returncode == 0
    assert (tmp_path / "fa.png").read_bytes() != (tmp_path / "fb.png").read_bytes(), (
        "GIF stopped animating after its first play-through"
    )


@needs_ffmpeg
def test_keyframed_gif_overlay_renders(vc, tmp_path):
    """GIF takes the video-like keyframe path (no image2 -loop 1) and stays visible."""
    pytest.importorskip("PIL.Image")
    base, gif, out = tmp_path / "b.mp4", tmp_path / "a.gif", tmp_path / "out.mp4"
    _black_base(base, dur=3.0)
    _animated_gif(gif)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{
            "asset_path": str(gif), "x": 100, "y": 60, "start_seconds": 0, "end_seconds": 3,
            "keyframes": [{"t": 0.0, "opacity": 0.0}, {"t": 0.5, "opacity": 1.0}],
        }],
    })
    assert res.success, res.error
    assert _gray_frame(out, 2.0, tmp_path).getextrema()[1] > 150, "keyframed GIF not visible"


@needs_ffmpeg
def test_nonmonotonic_opacity_dips_mid_timeline(vc, tmp_path):
    """Piecewise opacity 1→0.1→1 visibly dims at the midpoint (geq alpha path)."""
    pytest.importorskip("PIL.Image")
    base, ov, out = tmp_path / "b.mp4", tmp_path / "o.png", tmp_path / "out.mp4"
    _black_base(base, dur=3.0)
    _white_png(ov)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{
            "asset_path": str(ov), "x": 100, "y": 80, "start_seconds": 0, "end_seconds": 3,
            "keyframes": [
                {"t": 0.0, "opacity": 1.0},
                {"t": 1.5, "opacity": 0.1},
                {"t": 2.9, "opacity": 1.0},
            ],
        }],
    })
    assert res.success, res.error
    early = _gray_frame(out, 0.1, tmp_path).getextrema()[1]
    mid = _gray_frame(out, 1.5, tmp_path).getextrema()[1]
    late = _gray_frame(out, 2.8, tmp_path).getextrema()[1]
    assert early > 180 and late > 180, f"overlay not visible at ends ({early}, {late})"
    assert mid < 100, f"opacity dip not rendered: mid-luma {mid}"


@needs_ffmpeg
def test_audio_mix_raises_rms_in_window(vc, tmp_path):
    """Overlay audio is delayed into [1,3] and mixed: loud inside, silent outside."""
    base, clip, out = tmp_path / "b.mp4", tmp_path / "c.mp4", tmp_path / "out.mp4"
    _black_base(base, dur=4.0, with_audio=True)
    _sine_clip(clip, dur=2.0)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{
            "asset_path": str(clip), "x": 100, "y": 60,
            "start_seconds": 1, "end_seconds": 3,
            "audio_mix": {"enabled": True, "volume": 1.0},
        }],
    })
    assert res.success, res.error
    assert res.data.get("audio_mixed_count") == 1
    inside = _mean_volume_db(out, 1.2, 1.6)
    outside = _mean_volume_db(out, 0.0, 0.9)
    assert inside > -50, f"overlay audio not mixed: window RMS {inside} dB"
    assert outside < -70, f"audio leaked outside the window: {outside} dB"
    # duration=first — base length wins
    dur = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(out)], text=True).strip())
    assert dur == pytest.approx(4.0, abs=0.2)


@needs_ffmpeg
def test_audio_mix_on_silent_source_warns_and_skips(vc, tmp_path):
    """audio_mix on a still image (no audio stream) warns instead of failing."""
    base, ov, out = tmp_path / "b.mp4", tmp_path / "o.png", tmp_path / "out.mp4"
    _black_base(base, dur=2.0)
    _white_png(ov)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{"asset_path": str(ov), "x": 10, "y": 10,
                      "start_seconds": 0, "end_seconds": 2,
                      "audio_mix": {"enabled": True, "volume": 1.0}}],
    })
    assert res.success, res.error
    assert res.data.get("audio_mixed_count") == 0
    assert any("no audio stream" in w for w in (res.data.get("warnings") or []))


@needs_ffmpeg
def test_audio_mix_schema_validates(vc):
    """audio_mix is a legal overlays[] field in the edit_decisions artifact schema."""
    from schemas.artifacts import validate_artifact
    doc = {
        "version": "1.0",
        "render_runtime": "ffmpeg",
        "cuts": [{"id": "c0", "source": "x.mp4", "in_seconds": 0, "out_seconds": 1}],
        "overlays": [{
            "asset_id": "a1", "start_seconds": 0, "end_seconds": 1,
            "position": {"x": 0, "y": 0},
            "audio_mix": {"enabled": True, "volume": 1.5},
        }],
    }
    validate_artifact("edit_decisions", doc)  # raises on failure
