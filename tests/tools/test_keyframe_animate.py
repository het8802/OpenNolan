"""Tests for tools/video/keyframe_animate.py (Edits-parity Wave 2 — spec emitter).

No ffmpeg/rendering: this tool emits overlays[].keyframes and validates them against the
real (edited) edit_decisions schema.
"""

from __future__ import annotations

import json

import pytest

from tools.video.keyframe_animate import KeyframeAnimate


@pytest.fixture
def tool():
    return KeyframeAnimate()


def _overlay():
    return {"asset_id": "logo", "start_seconds": 0.0, "end_seconds": 2.0, "position": {"x": 100, "y": 50}}


# --- guards ---------------------------------------------------------------

def test_requires_overlay(tool):
    assert tool.execute({"keyframes": [{"t": 0}]}).success is False


def test_overlay_missing_fields_rejected(tool):
    res = tool.execute({"overlay": {"asset_id": "x"}, "preset": "fade_in"})
    assert res.success is False and "missing required field" in res.error


def test_needs_keyframes_or_preset(tool):
    assert tool.execute({"overlay": _overlay()}).success is False


def test_keyframes_and_preset_mutually_exclusive(tool):
    res = tool.execute({"overlay": _overlay(), "keyframes": [{"t": 0}], "preset": "fade_in"})
    assert res.success is False and "not both" in res.error


# --- raw keyframes --------------------------------------------------------

def test_raw_keyframes_sorted_by_time(tool):
    res = tool.execute({"overlay": _overlay(), "keyframes": [
        {"t": 1.0, "opacity": 1}, {"t": 0.0, "opacity": 0},
    ]})
    assert res.success, res.error
    ts = [k["t"] for k in res.data["keyframes"]]
    assert ts == sorted(ts)


def test_keyframe_missing_t_rejected(tool):
    res = tool.execute({"overlay": _overlay(), "keyframes": [{"x": 5}]})
    assert res.success is False and "'t'" in res.error


def test_keyframe_bad_easing_rejected(tool):
    res = tool.execute({"overlay": _overlay(), "keyframes": [{"t": 0, "easing": "wobble"}]})
    assert res.success is False and "easing" in res.error


def test_keyframe_opacity_out_of_range_rejected_by_schema(tool):
    # passes normalization (it's a float) but the edit_decisions schema caps opacity at 1
    res = tool.execute({"overlay": _overlay(), "keyframes": [{"t": 0, "opacity": 1.5}]})
    assert res.success is False and "schema" in res.error


# --- presets --------------------------------------------------------------

def test_slide_in_left_preset(tool):
    res = tool.execute({"overlay": _overlay(), "preset": "slide_in_left", "preset_params": {"duration": 0.5, "distance": 200}})
    assert res.success, res.error
    kfs = res.data["keyframes"]
    assert len(kfs) == 2
    assert kfs[0]["x"] == 100 - 200  # starts left of final position
    assert kfs[0]["opacity"] == 0.0
    assert kfs[1]["x"] == 100 and kfs[1]["opacity"] == 1.0


def test_fade_in_preset(tool):
    res = tool.execute({"overlay": _overlay(), "preset": "fade_in"})
    assert res.success, res.error
    kfs = res.data["keyframes"]
    assert kfs[0]["opacity"] == 0.0 and kfs[-1]["opacity"] == 1.0


def test_pop_preset_has_three_keyframes(tool):
    res = tool.execute({"overlay": _overlay(), "preset": "pop"})
    assert res.success, res.error
    assert len(res.data["keyframes"]) == 3
    assert all("scale" in k for k in res.data["keyframes"])


def test_ken_burns_spans_full_overlay(tool):
    res = tool.execute({"overlay": _overlay(), "preset": "ken_burns", "preset_params": {"to_scale": 1.2}})
    assert res.success, res.error
    kfs = res.data["keyframes"]
    assert kfs[0]["t"] == 0.0 and kfs[-1]["t"] == 2.0
    assert kfs[-1]["scale"] == 1.2


def test_unknown_preset_rejected(tool):
    res = tool.execute({"overlay": _overlay(), "preset": "explode"})
    # enum is enforced at the schema layer for callers, but the tool also guards:
    assert res.success is False


def test_preset_duration_clamped_to_overlay_lifetime(tool):
    # duration longer than the overlay (2s) gets clamped; still valid
    res = tool.execute({"overlay": _overlay(), "preset": "slide_in_left", "preset_params": {"duration": 99}})
    assert res.success, res.error
    assert res.data["keyframes"][-1]["t"] <= 2.0


# --- merge into edit_decisions -------------------------------------------

def test_merge_replaces_overlay_by_asset_id(tool, tmp_path):
    ed = tmp_path / "edit_decisions.json"
    ed.write_text(json.dumps({
        "version": "1.0",
        "cuts": [{"id": "c1", "source": "x.mp4", "in_seconds": 0, "out_seconds": 2}],
        "overlays": [{"asset_id": "logo", "start_seconds": 0, "end_seconds": 2, "position": {"x": 100, "y": 50}}],
        "render_runtime": "remotion",
    }))
    res = tool.execute({"overlay": _overlay(), "preset": "fade_in", "edit_decisions_path": str(ed)})
    assert res.success, res.error
    doc = json.loads(ed.read_text())
    assert len(doc["overlays"]) == 1  # replaced, not appended
    assert "keyframes" in doc["overlays"][0]


def test_merge_appends_new_overlay(tool, tmp_path):
    ed = tmp_path / "edit_decisions.json"
    ed.write_text(json.dumps({
        "version": "1.0",
        "cuts": [{"id": "c1", "source": "x.mp4", "in_seconds": 0, "out_seconds": 2}],
        "overlays": [{"asset_id": "other", "start_seconds": 0, "end_seconds": 1, "position": {"x": 0, "y": 0}}],
        "render_runtime": "remotion",
    }))
    res = tool.execute({"overlay": _overlay(), "preset": "fade_in", "edit_decisions_path": str(ed)})
    assert res.success, res.error
    doc = json.loads(ed.read_text())
    assert len(doc["overlays"]) == 2


def test_merge_invalid_does_not_corrupt(tool, tmp_path):
    ed = tmp_path / "broken.json"
    original = {"version": "1.0", "cuts": [], "overlays": []}  # missing render_runtime
    ed.write_text(json.dumps(original))
    res = tool.execute({"overlay": _overlay(), "preset": "fade_in", "edit_decisions_path": str(ed)})
    assert res.success is False and "validate" in res.error
    assert json.loads(ed.read_text()) == original  # untouched


def test_writes_overlay_json(tool, tmp_path):
    out = tmp_path / "ov.json"
    res = tool.execute({"overlay": _overlay(), "preset": "pop", "output_path": str(out)})
    assert res.success and out.exists()
    doc = json.loads(out.read_text())
    assert doc["asset_id"] == "logo" and "keyframes" in doc
