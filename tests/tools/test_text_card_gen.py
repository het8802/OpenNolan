"""Tests for tools/graphics/text_card_gen.py (Edits parity — text tool on the FFmpeg path).

Validation/guard paths run pure (no rendering, no files written). Render tests need
Pillow and assert measurable image outcomes: real alpha channel, nonzero ink pixels,
wrap growth, and the black pill box exceeding the text bbox.
"""

from __future__ import annotations

import importlib.util
import json

import pytest

from tools.graphics.text_card_gen import TextCardGen

HAS_PIL = importlib.util.find_spec("PIL") is not None
needs_pil = pytest.mark.skipif(not HAS_PIL, reason="Pillow not installed")


@pytest.fixture
def tool():
    return TextCardGen()


def _render(tool, tmp_path, name="card.png", **kw):
    out = tmp_path / name
    inputs = {"text": "HELLO WORLD", "output_path": str(out)}
    inputs.update(kw)
    res = tool.execute(inputs)
    assert res.success, res.error
    return res, out


# --- validation / guards (pure: no rendering, no files) ---------------------

def test_missing_text_rejected(tool):
    res = tool.execute({})
    assert res.success is False and "text" in res.error


def test_whitespace_text_rejected(tool, tmp_path):
    out = tmp_path / "x.png"
    res = tool.execute({"text": "   \n  ", "output_path": str(out)})
    assert res.success is False and "text" in res.error
    assert not out.exists()


def test_unknown_preset_rejected(tool):
    res = tool.execute({"text": "hi", "preset": "neon_rainbow"})
    assert res.success is False and "preset" in res.error


def test_bad_font_size_rejected(tool):
    assert tool.execute({"text": "hi", "font_size": 0}).success is False
    assert tool.execute({"text": "hi", "font_size": -3}).success is False
    assert tool.execute({"text": "hi", "font_size": "big"}).success is False


def test_missing_font_path_rejected(tool):
    res = tool.execute({"text": "hi", "font_path": "/nope/missing-font.ttf"})
    assert res.success is False and "font_path" in res.error


def test_bad_box_rejected(tool):
    res = tool.execute({"text": "hi", "box": "black"})
    assert res.success is False and "box" in res.error
    res = tool.execute({"text": "hi", "box": {"opacity": 2.0}})
    assert res.success is False and "opacity" in res.error
    res = tool.execute({"text": "hi", "box": {"padding": "wide"}})
    assert res.success is False and "padding" in res.error


def test_bad_max_width_rejected(tool):
    res = tool.execute({"text": "hi", "max_width_px": -100})
    assert res.success is False and "max_width_px" in res.error


@needs_pil
def test_bad_color_rejected(tool, tmp_path):
    out = tmp_path / "x.png"
    res = tool.execute({"text": "hi", "fill": "not-a-color", "output_path": str(out)})
    assert res.success is False and "fill" in res.error
    assert not out.exists()


# --- rendering ---------------------------------------------------------------

@needs_pil
def test_renders_rgba_png_with_ink(tool, tmp_path):
    res, out = _render(tool, tmp_path, preset="bold_center")
    from PIL import Image

    img = Image.open(out)
    assert img.mode == "RGBA"
    assert img.width == res.data["width"] and img.height == res.data["height"]
    assert res.data["lines"] == 1
    alpha = img.getchannel("A")
    lo, hi = alpha.getextrema()
    assert hi == 255  # real ink
    assert lo == 0    # real transparency around the glyphs
    hist = alpha.histogram()
    assert sum(hist[1:]) > 100  # nonzero ink pixels


@needs_pil
def test_wraps_long_text_height_grows(tool, tmp_path):
    long_text = "the quick brown fox jumps over the lazy dog once more"
    res_one, _ = _render(tool, tmp_path, "one.png", text=long_text, preset="minimal_clean", font_size=40)
    res_wrap, _ = _render(
        tool, tmp_path, "wrap.png",
        text=long_text, preset="minimal_clean", font_size=40, max_width_px=320,
    )
    assert res_one.data["lines"] == 1
    assert res_wrap.data["lines"] >= 2
    assert res_wrap.data["height"] > res_one.data["height"]
    assert res_wrap.data["width"] < res_one.data["width"]
    assert res_wrap.data["width"] <= 320


@needs_pil
def test_explicit_newlines_respected(tool, tmp_path):
    res, _ = _render(tool, tmp_path, text="TOP\nBOTTOM", preset="minimal_clean")
    assert res.data["lines"] == 2


@needs_pil
def test_black_pill_box_larger_than_text_bbox(tool, tmp_path):
    text = "KARAOKE CAPTION"
    res_plain, _ = _render(tool, tmp_path, "plain.png", text=text, preset="minimal_clean", font_size=54)
    res_pill, out = _render(tool, tmp_path, "pill.png", text=text, preset="black_pill_caption", font_size=54)
    # same font + size, so the pill canvas must exceed the bare text bbox in both axes
    assert res_pill.data["width"] > res_plain.data["width"]
    assert res_pill.data["height"] > res_plain.data["height"]
    from PIL import Image

    img = Image.open(out)
    r, g, b, a = img.getpixel((3, img.height // 2))
    # left edge at mid-height is pill padding, beyond any glyph: opaque black
    assert a == 255 and r < 40 and g < 40 and b < 40


@needs_pil
def test_lower_third_block_box(tool, tmp_path):
    res, out = _render(tool, tmp_path, text="Het Tikawala\nFounder", preset="lower_third")
    assert res.data["lines"] == 2
    from PIL import Image

    img = Image.open(out)
    # block box spans the full canvas: top-middle pixel is the semi-transparent bar
    px = img.getpixel((img.width // 2, 2))
    assert px[3] > 150


@needs_pil
def test_outline_pop_strokes_render(tool, tmp_path):
    res, out = _render(tool, tmp_path, text="POP", preset="outline_pop")
    from PIL import Image

    img = Image.open(out)
    # thick dark outline -> some opaque pixels are dark, some are the white fill
    opaque = [
        img.getpixel((x, y))
        for y in range(0, img.height, 3)
        for x in range(0, img.width, 3)
        if img.getpixel((x, y))[3] > 200
    ]
    assert any(p[0] < 60 and p[1] < 60 and p[2] < 60 for p in opaque)
    assert any(p[0] > 200 and p[1] > 200 and p[2] > 200 for p in opaque)


@needs_pil
def test_data_reports_font_used(tool, tmp_path):
    res, _ = _render(tool, tmp_path, text="FONT CHECK")
    assert res.data["font"]  # resolver always reports something (system bold or PIL-default)


# --- asset_manifest provenance ----------------------------------------------

@needs_pil
def test_registers_image_asset_with_provenance(tool, tmp_path):
    manifest = tmp_path / "asset_manifest.json"
    manifest.write_text(json.dumps({"version": "1.0", "assets": []}))
    res, out = _render(
        tool, tmp_path, text="HOOK", preset="outline_pop",
        asset_manifest_path=str(manifest), scene_id="scene-1",
    )
    doc = json.loads(manifest.read_text())
    assert len(doc["assets"]) == 1
    a = doc["assets"][0]
    assert a["type"] == "image"
    assert a["source_tool"] == "text_card_gen"
    assert a["scene_id"] == "scene-1"
    assert a["subtype"] == "outline_pop"
    assert a["path"] == str(out)
    assert str(manifest) in res.artifacts


@needs_pil
def test_invalid_manifest_warns_but_render_still_succeeds(tool, tmp_path):
    manifest = tmp_path / "bad.json"
    manifest.write_text(json.dumps({"version": "1.0", "assets": "not-a-list"}))
    res, out = _render(tool, tmp_path, text="HOOK", asset_manifest_path=str(manifest))
    # the PNG is valid; only registration failed -> warning, not failure
    assert res.success is True
    assert "asset_manifest_warning" in res.data
    assert out.exists()
