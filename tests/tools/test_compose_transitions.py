"""Tests for the FFmpeg transition (xfade) + canvas + crop upgrades in video_compose.

Join resolution and validation paths are pure (no ffmpeg). Real composes run lavfi
segments through actual ffmpeg behind a skipif and assert measurable outcomes:
crossfades shorten the timeline by their duration, transition-free timelines stay
on the concat path (no xfade in the filtergraph, duration == sum), the canvas
honors compose_target/profile precedence, and transform.crop changes the visible
pixels of the output.
"""

from __future__ import annotations

import json
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


def _clip(path: Path, color: str, dur: float = 2.0, freq: int = 440) -> None:
    """A synthetic clip with audio (solid color so frames are unambiguous)."""
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d={dur}:r=25",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(path)],
        capture_output=True, check=True,
    )


@pytest.fixture
def three_clips(tmp_path):
    """3x 2s clips with audio."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    paths = []
    for name, color, freq in [("a", "red", 330), ("b", "green", 440), ("c", "blue", 550)]:
        p = tmp_path / f"{name}.mp4"
        _clip(p, color, freq=freq)
        paths.append(p)
    return paths


def _probe(path: Path) -> dict:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        text=True,
    )
    data = json.loads(out)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    return {
        "duration": float(data["format"]["duration"]),
        "width": int(v["width"]),
        "height": int(v["height"]),
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
    }


def _cut(source, *, cid="c", in_s=0, out_s=2, **extra):
    return {"id": cid, "source": str(source), "in_seconds": in_s, "out_seconds": out_s, **extra}


# --- join resolution (pure, no ffmpeg) -------------------------------------

def test_join_b_transition_in_wins_over_a_transition_out(vc):
    cuts = [
        {"transition_out": "fadeblack", "transition_duration": 1.5},
        {"transition_in": "wipeleft", "transition_duration": 0.3},
    ]
    joins, warnings = vc._resolve_joins(cuts, {})
    assert joins == [{"type": "wipeleft", "duration": 0.3}]
    assert warnings == []


def test_join_falls_back_to_a_transition_out(vc):
    cuts = [{"transition_out": "fadewhite", "transition_duration": 0.7}, {}]
    joins, _ = vc._resolve_joins(cuts, {})
    assert joins == [{"type": "fadewhite", "duration": 0.7}]


def test_dissolve_maps_to_fade_and_durations_clamp(vc):
    cuts = [
        {},
        {"transition_in": "dissolve", "transition_duration": 9.0},
        {"transition_in": "fade", "transition_duration": 0.01},
    ]
    joins, _ = vc._resolve_joins(cuts, {})
    assert joins[0] == {"type": "fade", "duration": 2.0}  # clamped to max
    assert joins[1] == {"type": "fade", "duration": 0.1}  # clamped to min


def test_unknown_transition_warns_and_degrades_to_fade(vc):
    cuts = [{}, {"transition_in": "starwipe"}]
    joins, warnings = vc._resolve_joins(cuts, {})
    assert joins[0]["type"] == "fade"
    assert any("starwipe" in w for w in warnings)


def test_metadata_default_transition_duration_honored(vc):
    cuts = [{}, {"transition_in": "fade"}]
    joins, _ = vc._resolve_joins(cuts, {"default_transition_duration": 1.2})
    assert joins[0]["duration"] == 1.2


def test_cut_and_none_names_are_hard_cuts(vc):
    cuts = [{}, {"transition_in": "cut"}, {"transition_in": "none"}, {}]
    joins, _ = vc._resolve_joins(cuts, {})
    assert joins == [None, None, None]


# --- canvas / crop validation (pure, no ffmpeg) -----------------------------

def test_odd_compose_target_rejected_before_any_ffmpeg(vc):
    res = vc.execute({
        "operation": "compose",
        "edit_decisions": {
            "metadata": {"compose_target": {"width": 1081, "height": 1920}},
            "cuts": [_cut("/nope/missing.mp4")],
        },
    })
    assert res.success is False and "even" in res.error


def test_non_numeric_compose_target_rejected(vc):
    res = vc.execute({
        "operation": "compose",
        "edit_decisions": {
            "metadata": {"compose_target": {"width": "wide", "height": 1920}},
            "cuts": [_cut("/nope/missing.mp4")],
        },
    })
    assert res.success is False and "compose_target" in res.error


# --- real renders (ffmpeg) ---------------------------------------------------

@needs_ffmpeg
def test_fades_shorten_timeline_and_dissolve_renders(vc, three_clips, tmp_path):
    """3x2s with two 0.5s crossfades -> ~5.0s output ('dissolve' maps to fade)."""
    out = tmp_path / "faded.mp4"
    cuts = [
        _cut(three_clips[0], cid="c1"),
        _cut(three_clips[1], cid="c2", transition_in="fade", transition_duration=0.5),
        _cut(three_clips[2], cid="c3", transition_in="dissolve", transition_duration=0.5),
    ]
    res = vc.execute({
        "operation": "compose",
        "edit_decisions": {"cuts": cuts},
        "output_path": str(out),
        "preset": "ultrafast",
    })
    assert res.success, res.error
    assert res.data["used_xfade"] is True
    assert res.data["transitions_applied"] == 2
    assert "xfade=transition=fade" in res.data["xfade_filtergraph"]
    info = _probe(out)
    assert abs(info["duration"] - 5.0) <= 0.15, info
    assert info["has_audio"]  # acrossfade chain held together


@needs_ffmpeg
def test_transition_free_timeline_stays_on_concat_path(vc, three_clips, tmp_path):
    """Back-compat contract: no transitions -> concat demuxer, duration == sum."""
    out = tmp_path / "cuts.mp4"
    cuts = [_cut(p, cid=f"c{i}") for i, p in enumerate(three_clips)]
    res = vc.execute({
        "operation": "compose",
        "edit_decisions": {"cuts": cuts},
        "output_path": str(out),
        "preset": "ultrafast",
    })
    assert res.success, res.error
    assert res.data["used_xfade"] is False
    assert res.data["transitions_applied"] == 0
    assert not res.data.get("xfade_filtergraph")
    assert abs(_probe(out)["duration"] - 6.0) <= 0.15


@needs_ffmpeg
def test_mixed_hard_cut_and_transition_in_one_timeline(vc, three_clips, tmp_path):
    """Hard cut join + fade join in the same chain: 6.0 - 0.5 = ~5.5s."""
    out = tmp_path / "mixed.mp4"
    cuts = [
        _cut(three_clips[0], cid="c1"),
        _cut(three_clips[1], cid="c2"),  # hard cut join
        _cut(three_clips[2], cid="c3", transition_in="fadeblack", transition_duration=0.5),
    ]
    res = vc.execute({
        "operation": "compose",
        "edit_decisions": {"cuts": cuts},
        "output_path": str(out),
        "preset": "ultrafast",
    })
    assert res.success, res.error
    assert res.data["transitions_applied"] == 1
    fg = res.data["xfade_filtergraph"]
    assert "concat=n=2:v=1:a=0" in fg and "xfade=transition=fadeblack" in fg
    assert abs(_probe(out)["duration"] - 5.5) <= 0.15


@needs_ffmpeg
def test_compose_target_vertical_canvas(vc, three_clips, tmp_path):
    """9:16 compose_target produces 1080x1920 output (with a slide transition)."""
    out = tmp_path / "vertical.mp4"
    cuts = [
        _cut(three_clips[0], cid="c1"),
        _cut(three_clips[1], cid="c2", transition_in="slideleft", transition_duration=0.5),
    ]
    res = vc.execute({
        "operation": "compose",
        "edit_decisions": {
            "cuts": cuts,
            "metadata": {"compose_target": {"width": 1080, "height": 1920, "fps": 30}},
        },
        "output_path": str(out),
        "preset": "ultrafast",
    })
    assert res.success, res.error
    info = _probe(out)
    assert (info["width"], info["height"]) == (1080, 1920)
    assert abs(info["duration"] - 3.5) <= 0.15


@needs_ffmpeg
def test_profile_resolution_now_honored(vc, three_clips, tmp_path):
    """The profile-resolved resolution is applied (was dead code before)."""
    out = tmp_path / "reels.mp4"
    res = vc.execute({
        "operation": "compose",
        "edit_decisions": {"cuts": [_cut(three_clips[0], cid="c1")]},
        "profile": "instagram_reels",
        "output_path": str(out),
        "preset": "ultrafast",
    })
    assert res.success, res.error
    info = _probe(out)
    assert (info["width"], info["height"]) == (1080, 1920)


@needs_ffmpeg
def test_compose_target_beats_profile(vc, three_clips, tmp_path):
    out = tmp_path / "square.mp4"
    res = vc.execute({
        "operation": "compose",
        "edit_decisions": {
            "cuts": [_cut(three_clips[0], cid="c1")],
            "metadata": {"compose_target": {"width": 480, "height": 480}},
        },
        "profile": "instagram_reels",
        "output_path": str(out),
        "preset": "ultrafast",
    })
    assert res.success, res.error
    info = _probe(out)
    assert (info["width"], info["height"]) == (480, 480)


@needs_ffmpeg
def test_transform_crop_changes_visible_pixels(vc, tmp_path):
    """Cropping the white half vs the black half of a split source measurably
    changes the output frames (crop runs before scale/pad)."""
    Image = pytest.importorskip("PIL.Image")
    src = tmp_path / "split.mp4"
    # left half white, right half black
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=2:r=25",
         "-vf", "drawbox=x=0:y=0:w=160:h=240:color=white:t=fill",
         "-pix_fmt", "yuv420p", str(src)],
        capture_output=True, check=True,
    )

    def mean_luma_with_crop(crop: dict, name: str) -> float:
        out = tmp_path / name
        # crop is 4:3 like the canvas, so it fills the frame with no padding
        cuts = [_cut(src, cid="c1", transform={"crop": crop})]
        res = vc.execute({
            "operation": "compose",
            "edit_decisions": {
                "cuts": cuts,
                "metadata": {"compose_target": {"width": 320, "height": 240}},
            },
            "output_path": str(out),
            "preset": "ultrafast",
        })
        assert res.success, res.error
        frame = tmp_path / f"{name}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "1.0", "-i", str(out), "-frames:v", "1", str(frame)],
            capture_output=True, check=True,
        )
        img = Image.open(frame).convert("L")
        hist = img.histogram()
        return sum(i * c for i, c in enumerate(hist)) / sum(hist)

    white_mean = mean_luma_with_crop({"x": 0, "y": 0, "width": 160, "height": 120}, "left.mp4")
    black_mean = mean_luma_with_crop({"x": 160, "y": 0, "width": 160, "height": 120}, "right.mp4")
    assert white_mean > 180, f"crop of white half not applied (mean luma {white_mean:.0f})"
    assert black_mean < 60, f"crop of black half not applied (mean luma {black_mean:.0f})"


@needs_ffmpeg
def test_invalid_crop_rejected(vc, three_clips):
    res = vc.execute({
        "operation": "compose",
        "edit_decisions": {
            "cuts": [_cut(three_clips[0], cid="c1", transform={"crop": {"x": 0, "y": 0}})],
        },
    })
    assert res.success is False and "crop" in res.error


@needs_ffmpeg
def test_transition_with_silent_source_keeps_audio_chain(vc, three_clips, tmp_path):
    """A source with NO audio stream gets anullsrc injected, so acrossfade works."""
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=yellow:s=320x240:d=2:r=25",
         "-pix_fmt", "yuv420p", str(silent)],
        capture_output=True, check=True,
    )
    out = tmp_path / "silentmix.mp4"
    cuts = [
        _cut(three_clips[0], cid="c1"),
        _cut(silent, cid="c2", transition_in="fade", transition_duration=0.5),
    ]
    res = vc.execute({
        "operation": "compose",
        "edit_decisions": {"cuts": cuts},
        "output_path": str(out),
        "preset": "ultrafast",
    })
    assert res.success, res.error
    info = _probe(out)
    assert info["has_audio"]
    assert abs(info["duration"] - 3.5) <= 0.15


@needs_ffmpeg
def test_render_runtime_ffmpeg_renders_transitions(vc, three_clips, tmp_path):
    """Routing contract: render_runtime='ffmpeg' + transitions renders via xfade
    (no Remotion blocker — transitions are no longer a route-to-remotion signal)."""
    out = tmp_path / "render.mp4"
    edit_decisions = {
        "version": "1.0",
        "render_runtime": "ffmpeg",
        "cuts": [
            _cut(three_clips[0], cid="c1"),
            _cut(three_clips[1], cid="c2", transition_in="dissolve", transition_duration=0.5),
        ],
    }
    # Isolate the render wiring from final_review's heuristics (matches
    # tests/tools/test_ffmpeg_render_overlays.py).
    vc._run_final_review = lambda *a, **k: {"status": "pass", "issues_found": []}
    res = vc._render_via_ffmpeg(
        inputs={"preset": "ultrafast"},
        edit_decisions=edit_decisions,
        asset_manifest={},
        resolved_cuts=edit_decisions["cuts"],  # sources already literal paths
        output_path=out,
        profile=None,
    )
    assert res.success, res.error
    assert abs(_probe(out)["duration"] - 3.5) <= 0.15
