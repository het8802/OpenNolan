"""Lane 0 regression + contract tests for the edit_decisions schema additions.

Instagram-Edits parity plan, Wave 0 (schema foundation). Two ADDITIVE changes were
made to schemas/artifacts/edit_decisions.schema.json:

  1. overlays[].keyframes — per-overlay motion timeline (t, x, y, scale, rotation,
     opacity, easing). Powers Wave 2 (KeyframeAnimate).
  2. renderer_family enum gains "social-reel" — reserved for the instagram-reels-studio
     pipeline (Phase 2).

Because edit_decisions is consumed by ALL pipelines, the IRON RULE here is back-compat:
a pre-edit artifact (overlays WITHOUT keyframes, renderer_family WITHOUT social-reel)
MUST still validate. These tests prove that, prove the new fields are accepted, and
prove malformed keyframes are rejected loudly (not silently accepted).
"""

import copy

import jsonschema
import pytest

from schemas.artifacts import load_schema, validate_artifact


# --- Fixtures -------------------------------------------------------------

def _legacy_edit_decisions() -> dict:
    """An edit_decisions artifact as it looked BEFORE the keyframes/social-reel edit."""
    return {
        "version": "1.0",
        "cuts": [
            {"id": "c1", "source": "asset-1", "in_seconds": 0, "out_seconds": 3.0}
        ],
        "overlays": [
            {
                "asset_id": "logo",
                "start_seconds": 0,
                "end_seconds": 2.0,
                "position": {"x": 10, "y": 10},
                "opacity": 1.0,
            }
        ],
        "render_runtime": "remotion",
        "renderer_family": "animation-first",
    }


def _keyframed_edit_decisions() -> dict:
    """A NEW artifact exercising overlays[].keyframes + renderer_family=social-reel."""
    return {
        "version": "1.0",
        "cuts": [
            {"id": "c1", "source": "asset-1", "in_seconds": 0, "out_seconds": 3.0}
        ],
        "overlays": [
            {
                "asset_id": "logo",
                "start_seconds": 0,
                "end_seconds": 2.0,
                "position": {"x": 10, "y": 10},
                "keyframes": [
                    {"t": 0.0, "x": -200, "opacity": 0, "easing": "ease-out"},
                    {"t": 0.5, "x": 100, "opacity": 1, "scale": 1.0},
                    {"t": 1.0, "scale": 1.1, "rotation": 5},
                ],
            }
        ],
        "render_runtime": "remotion",
        "renderer_family": "social-reel",
    }


# --- Schema shape ---------------------------------------------------------

def test_schema_exposes_keyframes_on_overlays():
    schema = load_schema("edit_decisions")
    overlay_props = schema["properties"]["overlays"]["items"]["properties"]
    assert "keyframes" in overlay_props, "overlays[].keyframes missing from schema"
    kf_item = overlay_props["keyframes"]["items"]
    assert kf_item["required"] == ["t"], "keyframe must require an absolute time `t`"
    assert kf_item["additionalProperties"] is False


def test_schema_exposes_social_reel_renderer_family():
    schema = load_schema("edit_decisions")
    assert "social-reel" in schema["properties"]["renderer_family"]["enum"]


# --- Back-compat (IRON RULE) ---------------------------------------------

def test_legacy_artifact_without_keyframes_or_social_reel_still_validates():
    """REGRESSION: pre-edit artifacts must keep validating after the additive change."""
    validate_artifact("edit_decisions", _legacy_edit_decisions())


def test_legacy_overlay_without_keyframes_is_valid():
    """keyframes is optional — a static overlay (no keyframes key) must validate."""
    art = _legacy_edit_decisions()
    assert "keyframes" not in art["overlays"][0]
    validate_artifact("edit_decisions", art)


# --- New fields accepted --------------------------------------------------

def test_keyframed_overlay_and_social_reel_validate():
    validate_artifact("edit_decisions", _keyframed_edit_decisions())


# --- Malformed input rejected loudly -------------------------------------

def test_keyframe_missing_required_t_is_rejected():
    art = _keyframed_edit_decisions()
    art["overlays"][0]["keyframes"] = [{"x": 5}]  # no `t`
    with pytest.raises(jsonschema.ValidationError):
        validate_artifact("edit_decisions", art)


def test_keyframe_unknown_easing_is_rejected():
    art = _keyframed_edit_decisions()
    art["overlays"][0]["keyframes"][0]["easing"] = "wobble"
    with pytest.raises(jsonschema.ValidationError):
        validate_artifact("edit_decisions", art)


def test_keyframe_unknown_property_is_rejected():
    """additionalProperties:false on a keyframe guards against typo'd fields."""
    art = _keyframed_edit_decisions()
    art["overlays"][0]["keyframes"][0]["xpos"] = 5  # typo for x
    with pytest.raises(jsonschema.ValidationError):
        validate_artifact("edit_decisions", art)


def test_keyframe_opacity_out_of_range_is_rejected():
    art = _keyframed_edit_decisions()
    art["overlays"][0]["keyframes"][0]["opacity"] = 1.5
    with pytest.raises(jsonschema.ValidationError):
        validate_artifact("edit_decisions", art)


def test_unknown_renderer_family_still_rejected():
    """The enum must remain closed — only the documented families are allowed."""
    art = _legacy_edit_decisions()
    art["renderer_family"] = "tiktok-reel"  # not a real family
    with pytest.raises(jsonschema.ValidationError):
        validate_artifact("edit_decisions", art)
