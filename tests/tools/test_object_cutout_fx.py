"""Tests for object_cutout op="effect" (Edits parity: selective object effects).

The "cutout" fixture is synthetic lavfi — a small colored box on transparency, encoded
qtrle .mov (argb), the exact alpha format the tool's real op="cutout" outputs use — plus
the matching white-on-black mask .mp4. Validation guards are pure (no ffmpeg, no token).
Live tests run real ffmpeg and assert MEASURABLE outcomes: duration preserved per effect,
and bw_background's outside-the-box saturation collapsing while the box keeps its color.
No network/Replicate calls anywhere — op="effect" is local-only by contract.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tools.enhancement.object_cutout import ObjectCutout

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")

# the segmented "object": a box at (100,80) sized 80x60 in a 320x240 frame
BOX = "x=100:y=80:w=80:h=60"
OUTSIDE_CROP = "60:60:0:0"      # top-left corner, fully outside the box
INSIDE_CROP = "40:40:120:90"    # fully inside the box


@pytest.fixture
def tool():
    return ObjectCutout()


@pytest.fixture
def base_clip(tmp_path):
    """2s colorful synthetic source (saturated everywhere, so desaturation is measurable)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "base.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=2:r=25",
         "-pix_fmt", "yuv420p", str(p)],
        capture_output=True, check=True,
    )
    return p


@pytest.fixture
def mask_clip(tmp_path):
    """What the tool's real mask_path output looks like: white object box on black."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "mask.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=2:r=25",
         "-vf", f"drawbox={BOX}:color=white:t=fill",
         "-pix_fmt", "yuv420p", str(p)],
        capture_output=True, check=True,
    )
    return p


@pytest.fixture
def cutout_clip(tmp_path, mask_clip):
    """A synthetic RGBA cutout in the tool's real format: qtrle .mov, argb — a green box
    on transparency (alphamerge of a solid color with the mask, same as _composite_alpha)."""
    p = tmp_path / "cut.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=green:s=320x240:d=2:r=25",
         "-i", str(mask_clip),
         "-filter_complex", "[1:v]format=gray[m];[0:v][m]alphamerge[out]",
         "-map", "[out]", "-c:v", "qtrle", "-an", str(p)],
        capture_output=True, check=True,
    )
    return p


def _duration(path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(proc.stdout.strip())


def _satavg(path, t: float, crop: str) -> float:
    """Average chroma saturation (signalstats SATAVG) of a cropped patch at time t."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-vf", f"crop={crop},signalstats,metadata=print:file=-", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    for line in proc.stdout.splitlines():
        if "SATAVG" in line:
            return float(line.split("=")[-1])
    raise AssertionError(f"no SATAVG from {path} at t={t}")


# --- validation guards (pure: no ffmpeg run, no token, no network) ----------

def test_invalid_op_rejected(tool):
    res = tool.execute({"op": "teleport", "video_path": "x.mp4"})
    assert res.success is False and "op must be one of" in res.error


def test_effect_requires_effect_name(tool):
    res = tool.execute({"op": "effect", "video_path": "x.mp4"})
    assert res.success is False and "effect in" in res.error


def test_effect_unknown_effect_rejected(tool):
    res = tool.execute({"op": "effect", "effect": "explode", "video_path": "x.mp4"})
    assert res.success is False and "explode" in res.error


def test_effect_missing_video_rejected(tool):
    res = tool.execute({"op": "effect", "effect": "blur", "video_path": "/nope/missing.mp4"})
    assert res.success is False and "not found" in res.error


def test_effect_requires_mask_or_cutout(tool, tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")  # existence/format check only — validation never probes
    res = tool.execute({"op": "effect", "effect": "blur", "video_path": str(v)})
    assert res.success is False
    assert "mask_path" in res.error and "cutout_path" in res.error
    assert "never re-run" in res.error  # the op must say it won't re-segment


def test_effect_nonexistent_mask_rejected(tool, tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    res = tool.execute({
        "op": "effect", "effect": "blur", "video_path": str(v),
        "mask_path": str(tmp_path / "no_mask.mp4"),
    })
    assert res.success is False and "mask_path not found" in res.error


def test_effect_bad_params_rejected(tool, tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    m = tmp_path / "m.mp4"
    m.write_bytes(b"x")
    common = {"op": "effect", "video_path": str(v), "mask_path": str(m)}

    res = tool.execute({**common, "effect": "blur", "strength": -1})
    assert res.success is False and "strength" in res.error
    res = tool.execute({**common, "effect": "pixelate", "pixel_size": 1})
    assert res.success is False and "pixel_size" in res.error
    res = tool.execute({**common, "effect": "pixelate", "pixel_size": 2.5})
    assert res.success is False and "pixel_size" in res.error
    res = tool.execute({**common, "effect": "outline", "thickness": 0})
    assert res.success is False and "thickness" in res.error
    # filtergraph-injection guard: only color names / hex pass
    res = tool.execute({**common, "effect": "outline", "color": "white[x];movie=evil"})
    assert res.success is False and "color" in res.error


def test_effect_is_free_and_estimates_zero(tool):
    assert tool.estimate_cost({"op": "effect", "effect": "blur"}) == 0.0
    # the paid cutout estimate is untouched
    assert tool.estimate_cost({"video_path": "x.mp4"}) > 0


# --- live ffmpeg: every effect succeeds, duration preserved ------------------

@needs_ffmpeg
@pytest.mark.parametrize("effect", list(ObjectCutout.EFFECTS))
def test_each_effect_succeeds_and_preserves_duration(tool, base_clip, mask_clip, tmp_path, effect):
    out = tmp_path / f"fx_{effect}.mp4"
    res = tool.execute({
        "op": "effect", "effect": effect,
        "video_path": str(base_clip), "mask_path": str(mask_clip),
        "output_path": str(out),
    })
    assert res.success is True, res.error
    assert out.exists() and out.stat().st_size > 0
    assert res.cost_usd == 0.0
    assert res.data["effect"] == effect and res.data["mask_kind"] == "mask"
    assert abs(_duration(out) - _duration(base_clip)) < 0.2
    assert abs(res.data["duration_seconds"] - 2.0) < 0.2  # re-probed, not assumed


@needs_ffmpeg
def test_effect_runs_without_replicate_token(tool, base_clip, mask_clip, tmp_path, monkeypatch):
    """op='effect' is local-only: it must work with NO token in the environment."""
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    out = tmp_path / "no_token.mp4"
    res = tool.execute({
        "op": "effect", "effect": "blur",
        "video_path": str(base_clip), "mask_path": str(mask_clip),
        "output_path": str(out),
    })
    assert res.success is True, res.error
    assert out.exists()


@needs_ffmpeg
def test_effect_accepts_rgba_cutout_via_alphaextract(tool, base_clip, cutout_clip, tmp_path):
    out = tmp_path / "from_cutout.mp4"
    res = tool.execute({
        "op": "effect", "effect": "blur",
        "video_path": str(base_clip), "cutout_path": str(cutout_clip),
        "output_path": str(out),
    })
    assert res.success is True, res.error
    assert res.data["mask_kind"] == "cutout"
    assert abs(_duration(out) - 2.0) < 0.2


@needs_ffmpeg
def test_effect_rejects_cutout_without_alpha(tool, base_clip, mask_clip, tmp_path):
    # an opaque mp4 passed as cutout_path: alphaextract would explode mid-graph, so the
    # tool must catch it up front with a pointer to mask_path
    res = tool.execute({
        "op": "effect", "effect": "blur",
        "video_path": str(base_clip), "cutout_path": str(mask_clip),
        "output_path": str(tmp_path / "x.mp4"),
    })
    assert res.success is False
    assert "alpha" in res.error and "mask_path" in res.error


@needs_ffmpeg
def test_bw_background_desaturates_background_keeps_object(tool, base_clip, mask_clip, tmp_path):
    out = tmp_path / "bw.mp4"
    res = tool.execute({
        "op": "effect", "effect": "bw_background",
        "video_path": str(base_clip), "mask_path": str(mask_clip),
        "output_path": str(out),
    })
    assert res.success is True, res.error

    in_outside = _satavg(base_clip, 1.0, OUTSIDE_CROP)
    out_outside = _satavg(out, 1.0, OUTSIDE_CROP)
    assert in_outside > 20  # premise: the source background patch is genuinely saturated
    # background (outside the box) collapses toward grayscale
    assert out_outside < in_outside * 0.4
    assert out_outside < 10
    # the object (inside the box) keeps its color
    in_inside = _satavg(base_clip, 1.0, INSIDE_CROP)
    out_inside = _satavg(out, 1.0, INSIDE_CROP)
    assert out_inside > in_inside * 0.6
