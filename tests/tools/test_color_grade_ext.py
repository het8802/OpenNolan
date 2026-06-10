"""Tests for the color_grade extensions (Edits parity: Adjustments + curves + saved looks).

Validation paths are pure (no ffmpeg invoked: they fail before any encode).
Live tests run real ffmpeg and assert MEASURABLE outcomes (signalstats YAVG
deltas, look-file round-trips). Looks are ALWAYS written under tmp_path via the
looks_dir override — never to the real assets/looks.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tools.enhancement.color_grade import ColorGrade

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture
def tool():
    return ColorGrade()


@pytest.fixture
def fake_clip(tmp_path):
    """Existing-but-undecodable file: lets validation get past the input-exists
    check. Every test using it must fail validation BEFORE ffmpeg runs."""
    p = tmp_path / "fake.mp4"
    p.write_bytes(b"\x00")
    return p


@pytest.fixture
def clip(tmp_path):
    """2s synthetic clip."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=2:r=25",
         "-pix_fmt", "yuv420p", str(p)],
        capture_output=True, check=True,
    )
    return p


@pytest.fixture
def dark_clip(tmp_path):
    """2s underexposed clip (testsrc pulled down) for the auto-correct test."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "dark.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=2:r=25",
         "-vf", "eq=brightness=-0.35", "-pix_fmt", "yuv420p", str(p)],
        capture_output=True, check=True,
    )
    return p


def _yavg(path, t=1.0):
    """Full-frame mean luma at time t via signalstats."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    for line in proc.stdout.splitlines():
        if "YAVG" in line:
            return float(line.split("=")[-1])
    raise AssertionError(f"no YAVG from {path} at t={t}")


# --- validation / guards (pure, no ffmpeg) ----------------------------------

def test_unknown_op_rejected(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "op": "vibe"})
    assert res.success is False and "op must be one of" in res.error


def test_missing_input_rejected(tool):
    res = tool.execute({"input_path": "/nope/missing.mp4", "op": "adjust", "brightness": 0.1})
    assert res.success is False and "not found" in res.error


def test_adjust_requires_some_param(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "op": "adjust"})
    assert res.success is False and "at least one" in res.error


@pytest.mark.parametrize("key,bad", [
    ("brightness", 2), ("brightness", -1.5),
    ("contrast", -0.1), ("contrast", 3.5),
    ("saturation", 4),
    ("gamma", 0.05), ("gamma", 11),
    ("temperature", 150), ("tint", -101),
    ("sharpness", 1.5), ("vignette", 2),
])
def test_adjust_ranges_rejected(tool, fake_clip, key, bad):
    res = tool.execute({"input_path": str(fake_clip), "op": "adjust", key: bad})
    assert res.success is False and key in res.error


def test_adjust_non_numeric_rejected(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "op": "adjust", "brightness": "bright"})
    assert res.success is False and "must be a number" in res.error


def test_intensity_validated_for_new_ops(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "op": "adjust",
                        "brightness": 0.2, "intensity": 1.5})
    assert res.success is False and "intensity" in res.error


def test_curves_requires_points_or_wheels(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "op": "curves"})
    assert res.success is False and "points" in res.error


def test_curves_bad_channel_rejected(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "op": "curves",
                        "points": {"cyan": [[0, 0], [1, 1]]}})
    assert res.success is False and "channels" in res.error


def test_curves_point_out_of_range_rejected(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "op": "curves",
                        "points": {"master": [[0, 0], [1, 1.5]]}})
    assert res.success is False and "out of [0, 1]" in res.error


def test_curves_single_point_rejected(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "op": "curves",
                        "points": {"master": [[0.5, 0.5]]}})
    assert res.success is False and ">= 2" in res.error


def test_curves_non_increasing_x_rejected(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "op": "curves",
                        "points": {"red": [[0, 0], [0.5, 0.4], [0.5, 0.6]]}})
    assert res.success is False and "strictly increasing" in res.error


def test_wheels_bad_band_rejected(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "op": "curves",
                        "wheels": {"midtone": {"r": 0.1}}})
    assert res.success is False and "bands" in res.error


def test_wheels_offset_out_of_range_rejected(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "op": "curves",
                        "wheels": {"shadows": {"r": 1.5}}})
    assert res.success is False and "[-1, 1]" in res.error


def test_auto_sample_frames_validated(tool, fake_clip):
    for bad in (0, 51, 2.5, True):
        res = tool.execute({"input_path": str(fake_clip), "op": "auto", "sample_frames": bad})
        assert res.success is False and "sample_frames" in res.error


def test_look_bad_name_rejected(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "look": "No Spaces!"})
    assert res.success is False and "slug" in res.error


def test_look_not_found(tool, fake_clip, tmp_path):
    res = tool.execute({"input_path": str(fake_clip), "look": "ghost",
                        "looks_dir": str(tmp_path / "looks")})
    assert res.success is False and "look not found" in res.error


def test_save_look_requires_slug_name(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "op": "adjust",
                        "brightness": 0.2, "save_look": True})
    assert res.success is False and "slug" in res.error


def test_save_look_rejected_for_profile_op(tool, fake_clip):
    res = tool.execute({"input_path": str(fake_clip), "profile": "neutral",
                        "save_look": True, "look_name": "my-look"})
    assert res.success is False and "save_look only applies" in res.error


def test_look_op_mismatch_rejected(tool, fake_clip, tmp_path):
    looks = tmp_path / "looks"
    looks.mkdir()
    (looks / "teal.json").write_text(json.dumps({
        "look_format": 1, "name": "teal", "op": "curves",
        "params": {"points": {"master": [[0, 0], [1, 1]]}},
    }))
    res = tool.execute({"input_path": str(fake_clip), "op": "adjust",
                        "look": "teal", "looks_dir": str(looks)})
    assert res.success is False and "saved for op" in res.error


def test_corrupt_look_file_rejected(tool, fake_clip, tmp_path):
    looks = tmp_path / "looks"
    looks.mkdir()
    (looks / "bad.json").write_text(json.dumps({"op": "profile", "params": []}))
    res = tool.execute({"input_path": str(fake_clip), "look": "bad", "looks_dir": str(looks)})
    assert res.success is False and "not a valid look" in res.error


# --- filter builders (pure) --------------------------------------------------

def test_temperature_uses_colortemperature_when_available(tool, monkeypatch):
    monkeypatch.setattr(ColorGrade, "_available_filters", frozenset({"colortemperature"}))
    vf = tool._build_adjust_vf({"temperature": 100.0}, None)
    assert "colortemperature=temperature=3500" in vf
    vf_cool = tool._build_adjust_vf({"temperature": -100.0}, None)
    assert "colortemperature=temperature=9500" in vf_cool


def test_temperature_falls_back_to_colorbalance(tool, monkeypatch):
    monkeypatch.setattr(ColorGrade, "_available_filters", frozenset({"colorbalance"}))
    vf = tool._build_adjust_vf({"temperature": 60.0}, None)
    assert "colortemperature" not in vf
    assert "rm=0.18" in vf and "bm=-0.18" in vf


def test_tint_maps_to_green_magenta(tool, monkeypatch):
    monkeypatch.setattr(ColorGrade, "_available_filters", frozenset({"colortemperature"}))
    vf = tool._build_adjust_vf({"tint": 100.0}, None)
    assert "gm=-0.3" in vf  # +tint = magenta = pull green down


def test_adjust_filter_order(tool, monkeypatch):
    monkeypatch.setattr(ColorGrade, "_available_filters", frozenset({"colortemperature"}))
    vf = tool._build_adjust_vf(
        {"brightness": 0.1, "temperature": 20.0, "sharpness": 0.5, "vignette": 0.5}, None
    )
    order = [vf.index(tok) for tok in ("colortemperature", "eq=", "unsharp", "vignette")]
    assert order == sorted(order)


def test_blend_intensity_wraps_graph():
    # all_opacity weights the ORIGINAL input: intensity 0.75 -> opacity 0.25
    vf = ColorGrade._blend_intensity("eq=brightness=0.3", 0.75)
    assert vf.startswith("split[original]") and "all_opacity=0.25" in vf
    assert ColorGrade._blend_intensity("eq=brightness=0.3", 1.0) == "eq=brightness=0.3"


def test_auto_corrections_heuristic():
    # crushed dark clip: expand contrast (clamped at 2) and lift hard (clamped at 0.5)
    p = ColorGrade._auto_corrections({"yavg": 50.0, "ymin": 40.0, "ymax": 60.0})
    assert p["contrast"] == 2.0 and p["brightness"] == 0.5
    # already balanced full-range clip: no-op corrections
    q = ColorGrade._auto_corrections({"yavg": 128.0, "ymin": 16.0, "ymax": 235.0})
    assert q["contrast"] == 1.0 and abs(q["brightness"]) < 0.01


# --- adjust (live ffmpeg) ----------------------------------------------------

@needs_ffmpeg
def test_adjust_brightness_raises_yavg(tool, clip, tmp_path):
    out = tmp_path / "bright.mp4"
    res = tool.execute({"input_path": str(clip), "op": "adjust",
                        "brightness": 0.3, "output_path": str(out)})
    assert res.success, res.error
    assert res.data["op"] == "adjust"
    assert res.data["adjust"]["brightness"] == 0.3
    assert _yavg(out) > _yavg(clip) + 15


@needs_ffmpeg
def test_adjust_full_stack_one_call(tool, clip, tmp_path):
    out = tmp_path / "stack.mp4"
    res = tool.execute({
        "input_path": str(clip), "op": "adjust", "output_path": str(out),
        "brightness": 0.05, "contrast": 1.2, "saturation": 1.3, "gamma": 1.1,
        "temperature": 40, "tint": -20, "sharpness": 0.4, "vignette": 0.5,
        "intensity": 0.8,
    })
    assert res.success, res.error
    assert out.exists() and out.stat().st_size > 0
    for token in ("eq=", "unsharp", "vignette"):
        assert token in res.data["filter"]
    # intensity 0.8 blends the whole chain: opacity of the ORIGINAL input is 0.2
    assert "all_opacity=0.2" in res.data["filter"]


@needs_ffmpeg
def test_adjust_intensity_zero_is_original(tool, clip, tmp_path):
    out = tmp_path / "noop.mp4"
    res = tool.execute({"input_path": str(clip), "op": "adjust", "brightness": 0.5,
                        "intensity": 0.0, "output_path": str(out)})
    assert res.success, res.error
    assert abs(_yavg(out) - _yavg(clip)) < 4  # blend at opacity 0 == original


# --- curves (live ffmpeg) ------------------------------------------------------

@needs_ffmpeg
def test_curves_and_wheels_lift_luma(tool, clip, tmp_path):
    out = tmp_path / "curves.mp4"
    res = tool.execute({
        "input_path": str(clip), "op": "curves", "output_path": str(out),
        "points": {"master": [[0, 0.2], [0.5, 0.65], [1, 1]]},
        "wheels": {"shadows": {"r": 0.1, "b": -0.05}, "midtones": {"g": 0.05}},
    })
    assert res.success, res.error
    assert "curves=master=" in res.data["filter"]
    assert "colorbalance=" in res.data["filter"]
    assert _yavg(out) > _yavg(clip) + 5  # the lifting master curve must raise mean luma


# --- auto (live ffmpeg) --------------------------------------------------------

@needs_ffmpeg
def test_auto_raises_dark_clip(tool, dark_clip, tmp_path):
    out = tmp_path / "auto.mp4"
    res = tool.execute({"input_path": str(dark_clip), "op": "auto", "output_path": str(out)})
    assert res.success, res.error
    assert res.data["auto"]["computed"]["brightness"] > 0
    assert res.data["auto"]["measured"]["frames_sampled"] >= 1
    assert _yavg(out) > _yavg(dark_clip) + 10


@needs_ffmpeg
def test_auto_respects_intensity(tool, dark_clip, tmp_path):
    full = tmp_path / "full.mp4"
    half = tmp_path / "half.mp4"
    res_full = tool.execute({"input_path": str(dark_clip), "op": "auto", "output_path": str(full)})
    res_half = tool.execute({"input_path": str(dark_clip), "op": "auto",
                             "intensity": 0.5, "output_path": str(half)})
    assert res_full.success and res_half.success
    in_y = _yavg(dark_clip)
    # half-intensity correction lands strictly between the original and the full fix
    assert in_y < _yavg(half) < _yavg(full)


# --- saved looks (live ffmpeg; looks_dir always under tmp_path) ----------------

@needs_ffmpeg
def test_look_save_load_roundtrip(tool, clip, tmp_path):
    looks = tmp_path / "looks"
    out1 = tmp_path / "a.mp4"
    res = tool.execute({
        "input_path": str(clip), "op": "adjust", "brightness": 0.25, "contrast": 1.2,
        "save_look": True, "look_name": "warm-pop", "looks_dir": str(looks),
        "output_path": str(out1),
    })
    assert res.success, res.error
    look_file = looks / "warm-pop.json"
    assert look_file.exists() and str(look_file) in res.artifacts
    assert not (looks / "warm-pop.json.tmp").exists()  # atomic write left no temp file
    doc = json.loads(look_file.read_text())
    assert doc["op"] == "adjust"
    assert doc["params"] == {"brightness": 0.25, "contrast": 1.2}

    # load: op comes from the look, params round-trip
    out2 = tmp_path / "b.mp4"
    res2 = tool.execute({"input_path": str(clip), "look": "warm-pop",
                         "looks_dir": str(looks), "output_path": str(out2)})
    assert res2.success, res2.error
    assert res2.data["op"] == "adjust" and res2.data["look"] == "warm-pop"
    assert res2.data["adjust"] == {"brightness": 0.25, "contrast": 1.2}
    assert _yavg(out2) > _yavg(clip) + 10

    # explicit params override the loaded look
    out3 = tmp_path / "c.mp4"
    res3 = tool.execute({"input_path": str(clip), "look": "warm-pop", "brightness": 0.0,
                         "looks_dir": str(looks), "output_path": str(out3)})
    assert res3.success, res3.error
    assert res3.data["adjust"] == {"brightness": 0.0, "contrast": 1.2}


@needs_ffmpeg
def test_curves_look_roundtrip(tool, clip, tmp_path):
    looks = tmp_path / "looks"
    res = tool.execute({
        "input_path": str(clip), "op": "curves",
        "points": {"master": [[0, 0.1], [1, 1]]}, "wheels": {"shadows": {"b": 0.1}},
        "save_look": True, "look_name": "lifted_blues", "looks_dir": str(looks),
        "output_path": str(tmp_path / "a.mp4"),
    })
    assert res.success, res.error
    doc = json.loads((looks / "lifted_blues.json").read_text())
    assert doc["op"] == "curves"
    assert doc["params"]["points"]["master"] == [[0, 0.1], [1, 1]]
    res2 = tool.execute({"input_path": str(clip), "look": "lifted_blues",
                         "looks_dir": str(looks), "output_path": str(tmp_path / "b.mp4")})
    assert res2.success, res2.error
    assert res2.data["op"] == "curves"
    assert "curves=master=" in res2.data["filter"]


@needs_ffmpeg
def test_auto_save_look_persists_computed_adjust(tool, dark_clip, tmp_path):
    looks = tmp_path / "looks"
    res = tool.execute({"input_path": str(dark_clip), "op": "auto",
                        "save_look": True, "look_name": "auto_fix",
                        "looks_dir": str(looks), "output_path": str(tmp_path / "o.mp4")})
    assert res.success, res.error
    doc = json.loads((looks / "auto_fix.json").read_text())
    assert doc["op"] == "adjust"  # auto persists its computed values as an adjust look
    assert set(doc["params"]) == {"brightness", "contrast"}
    assert doc["params"] == res.data["auto"]["computed"]


# --- legacy path stays intact --------------------------------------------------

@needs_ffmpeg
def test_legacy_profile_intensity_blend_direction(tool, dark_clip, tmp_path):
    """Locks the inverted-opacity fix on the legacy preset path: higher intensity
    must move the output FURTHER from the original (was silently backwards)."""
    full = tmp_path / "full.mp4"
    half = tmp_path / "half.mp4"
    for path, intensity in ((full, 1.0), (half, 0.4)):
        res = tool.execute({"input_path": str(dark_clip), "profile": "bright_clean",
                            "intensity": intensity, "output_path": str(path)})
        assert res.success, res.error
    in_y = _yavg(dark_clip)
    assert abs(_yavg(half) - in_y) < abs(_yavg(full) - in_y)


@needs_ffmpeg
def test_legacy_profile_partial_intensity_warns_about_020_semantics(tool, clip, tmp_path):
    """Existing preset callers tuned against the pre-0.2.0 inverted blend silently
    flip — the tool must flag the semantics change at runtime (and tell them the
    complement value that reproduces the old look). Full intensity is unaffected."""
    out = tmp_path / "warned.mp4"
    res = tool.execute({"input_path": str(clip), "profile": "neutral",
                        "intensity": 0.85, "output_path": str(out)})
    assert res.success, res.error
    warning = res.data.get("intensity_warning")
    assert warning and "0.2.0" in warning and "0.15" in warning
    # intensity=1.0 (the default) is identical pre/post fix: no warning
    out2 = tmp_path / "nowarn.mp4"
    res2 = tool.execute({"input_path": str(clip), "profile": "neutral",
                         "output_path": str(out2)})
    assert res2.success, res2.error
    assert "intensity_warning" not in res2.data


@needs_ffmpeg
def test_legacy_profile_path_unchanged(tool, clip, tmp_path):
    out = tmp_path / "neutral.mp4"
    res = tool.execute({"input_path": str(clip), "profile": "neutral", "output_path": str(out)})
    assert res.success, res.error
    assert res.data["op"] == "profile" and res.data["profile"] == "neutral"
    assert out.exists() and out.stat().st_size > 0
