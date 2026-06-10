"""Tests for caption style presets + word emphasis in remotion_caption_burn.

Python-side only: preset -> props-bundle mapping, emphasis flagging on
WordCaption entries, validation, and back-compat. No ffmpeg or Remotion
process is ever launched — helpers are pure, and the execute() paths under
test fail validation before any external work.
"""

from __future__ import annotations

import pytest

from tools.video.remotion_caption_burn import RemotionCaptionBurn

# Every key a preset bundle may emit must be a CaptionOverlay prop name
# (camelCase) — see remotion-composer/src/components/CaptionOverlay.tsx
# CaptionStyle. Guards against the python/TSX contract drifting.
CAPTION_STYLE_PROPS = {
    "fontSize", "color", "highlightColor", "backgroundColor", "fontFamily",
    "fontWeight", "boxed", "borderRadius", "bottomOffset", "outlineColor",
    "outlineWidth", "emphasisColor", "emphasisScale",
}

SEGMENTS = [
    {
        "words": [
            {"word": "Claude", "start": 0.0, "end": 0.4},
            {"word": "ships", "start": 0.4, "end": 0.8},
            {"word": "fast.", "start": 0.8, "end": 1.2},
            {"word": "Really", "start": 1.2, "end": 1.6},
            {"word": "fast,", "start": 1.6, "end": 2.0},
        ]
    }
]


@pytest.fixture
def tool():
    return RemotionCaptionBurn()


# --- preset -> props bundle mapping ----------------------------------------

def test_default_preset_maps_to_empty_bundle(tool):
    # karaoke_classic == current behavior == component defaults; the bundle
    # must be empty so legacy props files stay byte-identical
    assert tool._build_caption_style({}) == {}
    assert tool._build_caption_style({"style_preset": "karaoke_classic"}) == {}


def test_black_pill_bundle(tool):
    style = tool._build_caption_style({"style_preset": "black_pill"})
    assert style["backgroundColor"] == "#000000"
    assert style["boxed"] is True
    assert style["borderRadius"] >= 100  # pill, not the default 12px card
    assert style["color"] == "#FFFFFF"
    assert style["highlightColor"] == "#FFFFFF"
    assert style["fontWeight"] >= 800


def test_yellow_pop_bundle(tool):
    style = tool._build_caption_style({"style_preset": "yellow_pop"})
    assert style["highlightColor"] == "#FFD60A"  # bold yellow active word
    assert style["color"] == "#FFFFFF"  # white base
    assert style["boxed"] is False
    assert style["outlineWidth"] > 0  # legibility without a box


def test_minimal_lower_bundle(tool):
    style = tool._build_caption_style({"style_preset": "minimal_lower"})
    assert style["boxed"] is False
    assert style["fontSize"] < 52  # smaller than the tool's default font_size
    assert "outlineWidth" not in style  # no outline, no box — minimal


def test_bold_outline_bundle(tool):
    style = tool._build_caption_style({"style_preset": "bold_outline"})
    assert style["boxed"] is False
    assert style["outlineWidth"] >= 6  # thick
    assert style["outlineColor"].startswith("#1")  # dark


def test_all_preset_keys_match_caption_overlay_props(tool):
    for preset, bundle in tool.STYLE_PRESETS.items():
        unknown = set(bundle) - CAPTION_STYLE_PROPS
        assert not unknown, f"{preset} emits unknown CaptionOverlay props: {unknown}"


def test_explicit_inputs_override_preset(tool):
    style = tool._build_caption_style({
        "style_preset": "black_pill",
        "font_size": 70,
        "base_color": "#FAF4EC",
        "font_family": "Georgia, serif",
        "highlight_color": "#FF5C39",
        "emphasis_color": "#C2410C",
        "emphasis_scale": 1.5,
    })
    assert style["fontSize"] == 70
    assert style["color"] == "#FAF4EC"
    assert style["fontFamily"] == "Georgia, serif"
    assert style["highlightColor"] == "#FF5C39"
    assert style["emphasisColor"] == "#C2410C"
    assert style["emphasisScale"] == 1.5
    # untouched preset values survive
    assert style["backgroundColor"] == "#000000"
    assert style["boxed"] is True


def test_schema_defaults_do_not_clobber_preset(tool):
    # absent keys (the schema-default case) must NOT override the preset
    style = tool._build_caption_style({"style_preset": "black_pill"})
    assert style["fontSize"] == 54  # preset's size, not the schema default 52
    assert style["highlightColor"] == "#FFFFFF"  # not the schema's cyan


def test_direct_inputs_work_without_a_preset(tool):
    # surfacing the existing-but-unexposed CaptionOverlay props
    style = tool._build_caption_style({
        "base_color": "#EEEEEE",
        "font_family": "Inter",
    })
    assert style == {"color": "#EEEEEE", "fontFamily": "Inter"}


# --- emphasis flag on WordCaption entries ----------------------------------

def test_emphasis_flags_land_on_matching_words(tool):
    captions = tool._segments_to_word_captions(SEGMENTS)
    count = tool._apply_emphasis(captions, ["FAST", "claude"])
    # "Claude" (case-insensitive), "fast." and "fast," (punctuation stripped)
    assert count == 3
    assert captions[0]["emphasis"] is True
    assert captions[2]["emphasis"] is True
    assert captions[4]["emphasis"] is True
    # non-matching words keep the original WordCaption shape (no key at all)
    assert "emphasis" not in captions[1]
    assert "emphasis" not in captions[3]


def test_emphasis_no_match_leaves_captions_untouched(tool):
    captions = tool._segments_to_word_captions(SEGMENTS)
    count = tool._apply_emphasis(captions, ["nonexistent"])
    assert count == 0
    assert all("emphasis" not in c for c in captions)


# --- validation -------------------------------------------------------------

def test_unknown_preset_rejected(tool, tmp_path):
    # validation fires before the input-file check, so no file is needed
    res = tool.execute({
        "input_path": "/nope/missing.mp4",
        "output_path": str(tmp_path / "out.mp4"),
        "segments": SEGMENTS,
        "style_preset": "neon_rain",
    })
    assert res.success is False
    assert "style_preset must be one of" in res.error


def test_emphasis_words_must_be_string_list(tool, tmp_path):
    base = {
        "input_path": "/nope/missing.mp4",
        "output_path": str(tmp_path / "out.mp4"),
        "segments": SEGMENTS,
    }
    res = tool.execute({**base, "emphasis_words": "fast"})
    assert res.success is False and "emphasis_words" in res.error
    res = tool.execute({**base, "emphasis_words": ["fast", 7]})
    assert res.success is False and "emphasis_words" in res.error


def test_emphasis_scale_out_of_range_rejected(tool, tmp_path):
    res = tool.execute({
        "input_path": "/nope/missing.mp4",
        "output_path": str(tmp_path / "out.mp4"),
        "segments": SEGMENTS,
        "emphasis_scale": 5.0,
    })
    assert res.success is False and "emphasis_scale" in res.error


# --- back-compat ------------------------------------------------------------

def test_backcompat_props_unchanged_for_default_style(tool):
    captions = tool._segments_to_word_captions(SEGMENTS)
    props = tool._build_props(
        "clip.mp4", captions, None, 4, 52, "#22D3EE",
        tool._build_caption_style({}),
    )
    # exactly the legacy key set — no captionStyle key for the default look
    assert set(props) == {
        "videoSrc", "captions", "overlays",
        "wordsPerPage", "fontSize", "highlightColor",
    }
    assert props["videoSrc"] == "public/talking-head/clip.mp4"
    assert props["fontSize"] == 52
    assert props["highlightColor"] == "#22D3EE"
    # WordCaption shape unchanged when emphasis is unused
    assert all(set(c) == {"word", "startMs", "endMs"} for c in props["captions"])


def test_props_carry_caption_style_for_presets(tool):
    captions = tool._segments_to_word_captions(SEGMENTS)
    style = tool._build_caption_style({"style_preset": "black_pill"})
    props = tool._build_props(
        "clip.mp4", captions, None, 4, 52, "#22D3EE", style,
    )
    assert props["captionStyle"] == style
