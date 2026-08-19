"""Tests for TEXT overlays + cuts[].layer routing in video_compose (Edits-parity, stage 3 of 3).

Covers: overlays[] type='text' rendered via drawtext (named anchors, box, static +
keyframed opacity, font resolution), drawtext escaping for both filtergraph parser
layers (colons, quotes, commas, %, backslashes), and the first multi-track step —
cuts with layer='overlay' leaving the base concat and compositing as timed PiP
video overlays (transform.scale/position, list-order timeline placement,
pts_offset_seconds stream shifting).

Validation/escaping tests are pure. Live tests run real ffmpeg on lavfi assets and
assert MEASURABLE outcomes: bright-pixel counts in the text region, enable-window
on/off frames, PiP bounding boxes at the computed anchor geometry, and concat
duration math for the layer back-compat path.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.video.video_compose import VideoCompose

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


def _ffmpeg_has_filter(name: str) -> bool:
    if not HAS_FFMPEG:
        return False
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode == 0 and any(
        name in line.split() for line in result.stdout.splitlines()
    )


HAS_DRAWTEXT = _ffmpeg_has_filter("drawtext")
needs_drawtext = pytest.mark.skipif(
    not HAS_DRAWTEXT, reason="ffmpeg drawtext filter not available"
)

_FONT_AVAILABLE = VideoCompose()._resolve_drawtext_font(None, 0)[0] is not None
needs_font = pytest.mark.skipif(
    not _FONT_AVAILABLE, reason="no system font found for drawtext"
)


@pytest.fixture
def vc():
    return VideoCompose()


# --- asset generators -------------------------------------------------------

def _clip(path: Path, *, color: str = "black", dur: float = 3.0,
          size: str = "320x240") -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={dur}:r=24",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
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


def _duration(path: Path) -> float:
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)], text=True).strip())


# --- drawtext escaping (pure) -------------------------------------------------

def test_escape_drawtext_two_layer_mapping(vc):
    """Each special char gets exactly the escape both parser layers need."""
    assert vc._escape_drawtext_value("a:b") == "a\\\\:b"
    assert vc._escape_drawtext_value("it's") == "it\\\\\\'s"
    assert vc._escape_drawtext_value("a,b") == "a\\,b"
    assert vc._escape_drawtext_value("x;y") == "x\\;y"
    assert vc._escape_drawtext_value("[t]") == "\\[t\\]"
    assert vc._escape_drawtext_value("a\\b") == "a\\\\\\\\b"
    # % stays literal — drawtext runs with expansion=none
    assert vc._escape_drawtext_value("100%") == "100%"


def test_drawtext_filter_shape(vc):
    """Filter carries expansion=none, anchor expressions, box, and enable window."""
    err, flt, warnings = vc._build_drawtext_filter(
        {"text": "Hi", "start_seconds": 0.5, "end_seconds": 2,
         "position": "bottom-center", "box": {}},
        0, "0:v", "v0",
    )
    assert err is None and warnings == []
    assert flt.startswith("[0:v]drawtext=") and flt.endswith("[v0]")
    assert "expansion=none" in flt
    assert "x='(w-text_w)/2'" in flt and "y='h*0.95-text_h'" in flt
    assert "box=1" in flt and "boxcolor=black@0.5" in flt and "boxborderw=10" in flt
    assert "enable='between(t,0.5,2)'" in flt


def test_drawtext_keyframes_become_time_expressions(vc):
    err, flt, warnings = vc._build_drawtext_filter(
        {"text": "Hi", "start_seconds": 0, "end_seconds": 2,
         "keyframes": [{"t": 0, "x": 0, "opacity": 0.0},
                       {"t": 1, "x": 100, "opacity": 1.0},
                       {"t": 1.5, "scale": 2.0}]},
        0, "0:v", "v0",
    )
    assert err is None
    assert "x='if(lt(t," in flt
    assert "alpha='clip(" in flt
    assert any("scale keyframes are not rendered" in w for w in warnings)


# --- text overlay validation (pure; ffmpeg never invoked) ----------------------

def _text_overlay_op(vc, tmp_path, overlay):
    base = tmp_path / "b.mp4"; base.write_bytes(b"x")
    return vc._overlay({
        "input_path": str(base), "output_path": str(tmp_path / "out.mp4"),
        "overlays": [overlay],
    })


def test_text_overlay_requires_text(vc, tmp_path):
    res = _text_overlay_op(vc, tmp_path, {"type": "text", "text": "  ",
                                          "start_seconds": 0, "end_seconds": 1})
    assert not res.success and "text" in res.error


def test_text_overlay_bad_font_size_rejected(vc, tmp_path):
    res = _text_overlay_op(vc, tmp_path, {"type": "text", "text": "Hi",
                                          "font_size": 0,
                                          "start_seconds": 0, "end_seconds": 1})
    assert not res.success and "font_size" in res.error


def test_text_overlay_color_injection_rejected(vc, tmp_path):
    """A color smuggling extra drawtext options must be rejected, not rendered."""
    res = _text_overlay_op(vc, tmp_path, {"type": "text", "text": "Hi",
                                          "color": "red:box=1",
                                          "start_seconds": 0, "end_seconds": 1})
    assert not res.success and "color" in res.error


def test_text_overlay_unknown_anchor_rejected(vc, tmp_path):
    res = _text_overlay_op(vc, tmp_path, {"type": "text", "text": "Hi",
                                          "position": "middle-ish",
                                          "start_seconds": 0, "end_seconds": 1})
    assert not res.success and "anchor" in res.error


def test_text_overlay_missing_font_path_rejected(vc, tmp_path):
    res = _text_overlay_op(vc, tmp_path, {"type": "text", "text": "Hi",
                                          "font_path": str(tmp_path / "nope.ttf"),
                                          "start_seconds": 0, "end_seconds": 1})
    assert not res.success and "font_path" in res.error


def test_image_overlay_named_anchor_rejected(vc, tmp_path):
    """Named anchors are text-only; image overlays must say so clearly."""
    ov = tmp_path / "o.png"; ov.write_bytes(b"x")
    base = tmp_path / "b.mp4"; base.write_bytes(b"x")
    res = vc._overlay({
        "input_path": str(base), "output_path": str(tmp_path / "out.mp4"),
        "overlays": [{"asset_path": str(ov), "position": "top-left",
                      "start_seconds": 0, "end_seconds": 1}],
    })
    assert not res.success and "text overlays" in res.error


def test_bad_pts_offset_rejected(vc, tmp_path):
    ov = tmp_path / "o.mp4"; ov.write_bytes(b"x")
    base = tmp_path / "b.mp4"; base.write_bytes(b"x")
    res = vc._overlay({
        "input_path": str(base), "output_path": str(tmp_path / "out.mp4"),
        "overlays": [{"asset_path": str(ov), "x": 0, "y": 0,
                      "pts_offset_seconds": -1}],
    })
    assert not res.success and "pts_offset_seconds" in res.error


# --- layer routing validation (pure) -------------------------------------------

def _layer_render(vc, tmp_path, cuts, **ed_extra):
    ed = {"version": "1.0", "render_runtime": "ffmpeg", "cuts": cuts,
          "metadata": {"compose_target": {"width": 320, "height": 240, "fps": 24}}}
    ed.update(ed_extra)
    vc._run_final_review = lambda *a, **k: {"status": "pass", "issues_found": []}
    return vc._render_via_ffmpeg(
        inputs={}, edit_decisions=ed, asset_manifest={},
        resolved_cuts=ed["cuts"], output_path=tmp_path / "out.mp4", profile=None,
    )


def test_all_overlay_layer_cuts_rejected(vc, tmp_path):
    clip = tmp_path / "c.mp4"; clip.write_bytes(b"x")
    res = _layer_render(vc, tmp_path, [
        {"id": "c0", "source": str(clip), "in_seconds": 0, "out_seconds": 1,
         "layer": "overlay"},
    ])
    assert not res.success and "primary" in res.error


def test_overlay_layer_bad_scale_rejected_before_any_ffmpeg(vc, tmp_path):
    clip = tmp_path / "c.mp4"; clip.write_bytes(b"x")
    base = tmp_path / "b.mp4"; base.write_bytes(b"x")
    res = _layer_render(vc, tmp_path, [
        {"id": "base", "source": str(base), "in_seconds": 0, "out_seconds": 2},
        {"id": "pip", "source": str(clip), "in_seconds": 0, "out_seconds": 1,
         "layer": "overlay", "transform": {"scale": 1.5}},
    ])
    assert not res.success and "transform.scale" in res.error


def test_overlay_layer_bad_anchor_rejected(vc, tmp_path):
    clip = tmp_path / "c.mp4"; clip.write_bytes(b"x")
    base = tmp_path / "b.mp4"; base.write_bytes(b"x")
    res = _layer_render(vc, tmp_path, [
        {"id": "base", "source": str(base), "in_seconds": 0, "out_seconds": 2},
        {"id": "pip", "source": str(clip), "in_seconds": 0, "out_seconds": 1,
         "layer": "overlay", "transform": {"position": "upper-middle"}},
    ])
    assert not res.success and "named" in res.error


def test_overlay_layer_still_image_rejected(vc, tmp_path):
    img = tmp_path / "i.png"; img.write_bytes(b"x")
    base = tmp_path / "b.mp4"; base.write_bytes(b"x")
    res = _layer_render(vc, tmp_path, [
        {"id": "base", "source": str(base), "in_seconds": 0, "out_seconds": 2},
        {"id": "pip", "source": str(img), "in_seconds": 0, "out_seconds": 1,
         "layer": "overlay"},
    ])
    assert not res.success and "still image" in res.error


# --- schema (pure) --------------------------------------------------------------

def test_schema_accepts_text_overlay_and_layer():
    from schemas.artifacts import validate_artifact
    validate_artifact("edit_decisions", {
        "version": "1.0", "render_runtime": "ffmpeg",
        "cuts": [
            {"id": "c0", "source": "a.mp4", "in_seconds": 0, "out_seconds": 2},
            {"id": "c1", "source": "b.mp4", "in_seconds": 0, "out_seconds": 1,
             "layer": "overlay",
             "transform": {"scale": 0.4, "position": "bottom-left"}},
        ],
        "overlays": [{
            "type": "text", "text": "Hello", "start_seconds": 0, "end_seconds": 2,
            "position": "bottom-center", "font_size": 40, "color": "#FFFFFF",
            "box": {"color": "black", "opacity": 0.5, "padding": 12},
            "keyframes": [{"t": 0, "opacity": 0}, {"t": 0.5, "opacity": 1}],
        }],
    })  # raises on failure


def test_schema_rejects_text_overlay_without_text():
    import jsonschema
    from schemas.artifacts import validate_artifact
    with pytest.raises(jsonschema.ValidationError):
        validate_artifact("edit_decisions", {
            "version": "1.0", "render_runtime": "ffmpeg",
            "cuts": [{"id": "c0", "source": "a.mp4", "in_seconds": 0, "out_seconds": 2}],
            "overlays": [{"type": "text", "start_seconds": 0, "end_seconds": 2}],
        })


def test_schema_still_requires_asset_id_for_untyped_overlays():
    import jsonschema
    from schemas.artifacts import validate_artifact
    with pytest.raises(jsonschema.ValidationError):
        validate_artifact("edit_decisions", {
            "version": "1.0", "render_runtime": "ffmpeg",
            "cuts": [{"id": "c0", "source": "a.mp4", "in_seconds": 0, "out_seconds": 2}],
            "overlays": [{"start_seconds": 0, "end_seconds": 2,
                          "position": {"x": 0, "y": 0}}],
        })


# --- live: text overlays ---------------------------------------------------------

@needs_drawtext
@needs_font
def test_text_overlay_renders_pixels_in_region(vc, tmp_path):
    """White text on black lights up the placed region only inside its window."""
    pytest.importorskip("PIL.Image")
    base, out = tmp_path / "b.mp4", tmp_path / "out.mp4"
    _clip(base, dur=3.0)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{"type": "text", "text": "HELLO", "font_size": 48,
                      "color": "white", "position": {"x": 40, "y": 100},
                      "start_seconds": 0.5, "end_seconds": 2.5}],
    })
    assert res.success, res.error
    mid = _gray_frame(out, 1.5, tmp_path)
    assert _bright_count(mid) > 200, "text not rendered"
    bbox = _bright_bbox(mid)
    # glyphs sit at the requested x/y (ascent puts ink below y=100)
    assert bbox[0] >= 35 and bbox[1] >= 95, f"text not at requested position: {bbox}"
    early = _gray_frame(out, 0.2, tmp_path)
    assert _bright_count(early) == 0, "text visible before start_seconds"


@needs_drawtext
@needs_font
def test_text_overlay_special_chars_safe(vc, tmp_path):
    """Colons, quotes, commas, %, brackets, and backslashes survive escaping."""
    pytest.importorskip("PIL.Image")
    base, out = tmp_path / "b.mp4", tmp_path / "out.mp4"
    _clip(base, dur=2.0)
    tricky = "100%: it's \"done\", 50% [a;b] \\ C:\\path"
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{"type": "text", "text": tricky, "font_size": 20,
                      "color": "white", "position": "center",
                      "start_seconds": 0, "end_seconds": 2}],
    })
    assert res.success, res.error
    assert _bright_count(_gray_frame(out, 1.0, tmp_path)) > 200, (
        "special-char text not rendered"
    )


@needs_drawtext
@needs_font
def test_text_overlay_box_anchor_bottom_center(vc, tmp_path):
    """An opaque white box behind the text lands centered in the bottom band."""
    pytest.importorskip("PIL.Image")
    base, out = tmp_path / "b.mp4", tmp_path / "out.mp4"
    _clip(base, dur=2.0)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{"type": "text", "text": "CAP", "font_size": 30,
                      "color": "black", "position": "bottom-center",
                      "box": {"color": "white", "opacity": 1.0, "padding": 8},
                      "start_seconds": 0, "end_seconds": 2}],
    })
    assert res.success, res.error
    bbox = _bright_bbox(_gray_frame(out, 1.0, tmp_path))
    assert bbox is not None, "box not rendered"
    cx = (bbox[0] + bbox[2]) / 2
    assert abs(cx - 160) <= 12, f"box not horizontally centered: {bbox}"
    assert bbox[1] > 120, f"box not in the bottom band: {bbox}"


@needs_drawtext
@needs_font
def test_text_overlay_keyframed_fade_in(vc, tmp_path):
    """Opacity keyframes 0→1 ramp the text in via the alpha expression."""
    pytest.importorskip("PIL.Image")
    base, out = tmp_path / "b.mp4", tmp_path / "out.mp4"
    _clip(base, dur=3.0)
    res = vc.execute({
        "operation": "overlay", "input_path": str(base), "output_path": str(out),
        "overlays": [{"type": "text", "text": "FADE", "font_size": 48,
                      "color": "white", "position": "center",
                      "start_seconds": 0, "end_seconds": 3,
                      "keyframes": [{"t": 0.0, "opacity": 0.0},
                                    {"t": 1.5, "opacity": 1.0}]}],
    })
    assert res.success, res.error
    early = _bright_count(_gray_frame(out, 0.2, tmp_path))
    late = _bright_count(_gray_frame(out, 2.5, tmp_path))
    assert early == 0, f"text fully visible during fade-in start ({early}px)"
    assert late > 200, f"text missing after fade-in ({late}px)"


@needs_drawtext
@needs_font
def test_text_overlay_via_ffmpeg_render(vc, tmp_path):
    """edit_decisions.overlays[] type='text' flows through the ffmpeg render path."""
    pytest.importorskip("PIL.Image")
    clip, out = tmp_path / "clip.mp4", tmp_path / "out.mp4"
    _clip(clip, dur=3.0)
    ed = {
        "version": "1.0", "render_runtime": "ffmpeg",
        "cuts": [{"id": "c0", "source": str(clip), "in_seconds": 0, "out_seconds": 3}],
        "overlays": [{"type": "text", "text": "TITLE", "font_size": 40,
                      "color": "white", "position": "top-center",
                      "start_seconds": 0, "end_seconds": 3}],
        "metadata": {"compose_target": {"width": 320, "height": 240, "fps": 24}},
    }
    vc._run_final_review = lambda *a, **k: {"status": "pass", "issues_found": []}
    res = vc._render_via_ffmpeg(
        inputs={}, edit_decisions=ed, asset_manifest={},
        resolved_cuts=ed["cuts"], output_path=out, profile=None,
    )
    assert res.success, res.error
    bbox = _bright_bbox(_gray_frame(out, 1.5, tmp_path))
    assert bbox is not None, "text overlay dropped by the ffmpeg render path"
    assert bbox[1] < 60, f"top-center anchor not honored: {bbox}"
    assert not (tmp_path / "out_base.mp4").exists()


# --- live: cuts[].layer routing ---------------------------------------------------

@needs_ffmpeg
def test_layer_overlay_cut_composites_as_pip(vc, tmp_path):
    """An overlay-layer cut renders as a default top-right PiP at 30% width.

    Canvas 320x240 → pip width 96; 80x80 white source keeps aspect → 96x96;
    margin = round(0.03*320) = 10 → bbox exactly (214,10)-(310,106) while the
    cut's window [0,2) is live, and nothing after it ends.
    """
    pytest.importorskip("PIL.Image")
    white, black, out = tmp_path / "w.mp4", tmp_path / "b.mp4", tmp_path / "out.mp4"
    _clip(white, color="white", dur=2.0, size="80x80")
    _clip(black, dur=4.0)
    res = _layer_render(vc, tmp_path, [
        # listed BEFORE the base cut → starts at t=0 over the base timeline
        {"id": "pip", "source": str(white), "in_seconds": 0, "out_seconds": 2,
         "layer": "overlay"},
        {"id": "base", "source": str(black), "in_seconds": 0, "out_seconds": 4},
    ])
    assert res.success, res.error
    assert res.data.get("layer_overlay_cuts") == 1
    bbox = _bright_bbox(_gray_frame(out, 1.0, tmp_path))
    assert bbox is not None, "PiP not visible"
    assert abs(bbox[0] - 214) <= 4 and abs(bbox[1] - 10) <= 4, (
        f"PiP not at top-right anchor: {bbox}"
    )
    assert abs((bbox[2] - bbox[0]) - 96) <= 4, f"PiP not 30% width: {bbox}"
    assert _bright_bbox(_gray_frame(out, 3.0, tmp_path)) is None, (
        "PiP visible past its window"
    )
    assert not (tmp_path / ".pip_tmp").exists(), "PiP temp dir not cleaned up"
    assert _duration(out) == pytest.approx(4.0, abs=0.2), (
        "overlay-layer cut leaked into the base concat"
    )


@needs_ffmpeg
def test_layer_overlay_mid_timeline_with_transform(vc, tmp_path):
    """List-order placement + transform.scale/position drive the PiP window."""
    pytest.importorskip("PIL.Image")
    white, black_a, black_b = tmp_path / "w.mp4", tmp_path / "a.mp4", tmp_path / "c.mp4"
    out = tmp_path / "out.mp4"
    _clip(white, color="white", dur=2.0, size="80x80")
    _clip(black_a, dur=2.0)
    _clip(black_b, dur=2.0)
    res = _layer_render(vc, tmp_path, [
        {"id": "a", "source": str(black_a), "in_seconds": 0, "out_seconds": 2},
        # after a 2s base cut → window [2, 3.5)
        {"id": "pip", "source": str(white), "in_seconds": 0, "out_seconds": 1.5,
         "layer": "overlay",
         "transform": {"scale": 0.5, "position": "bottom-left"}},
        {"id": "b", "source": str(black_b), "in_seconds": 0, "out_seconds": 2},
    ])
    assert res.success, res.error
    assert _bright_bbox(_gray_frame(out, 1.0, tmp_path)) is None, "PiP early"
    bbox = _bright_bbox(_gray_frame(out, 2.5, tmp_path))
    assert bbox is not None, "PiP missing in its window"
    # scale 0.5 → 160px wide; bottom-left margin 10 → x=10, y=240-160-10=70
    assert abs(bbox[0] - 10) <= 4 and abs(bbox[1] - 70) <= 4, (
        f"transform.position bottom-left not honored: {bbox}"
    )
    assert abs((bbox[2] - bbox[0]) - 160) <= 4, f"transform.scale 0.5 not honored: {bbox}"
    assert _bright_bbox(_gray_frame(out, 3.8, tmp_path)) is None, "PiP late"


@needs_ffmpeg
def test_layer_back_compat_primary_background_concat(vc, tmp_path):
    """primary/background/absent layers all concat exactly as before (single pass)."""
    a, b, c, out = (tmp_path / n for n in ("a.mp4", "b.mp4", "c.mp4", "out.mp4"))
    _clip(a, dur=1.0)
    _clip(b, dur=1.0)
    _clip(c, dur=1.0)
    cuts = [
        {"id": "a", "source": str(a), "in_seconds": 0, "out_seconds": 1, "layer": "primary"},
        {"id": "b", "source": str(b), "in_seconds": 0, "out_seconds": 1, "layer": "background"},
        {"id": "c", "source": str(c), "in_seconds": 0, "out_seconds": 1},
    ]
    called = {"overlay": False}
    orig = vc._overlay
    vc._overlay = lambda inputs: called.__setitem__("overlay", True) or orig(inputs)
    res = _layer_render(vc, tmp_path, cuts)
    assert res.success, res.error
    assert called["overlay"] is False, "non-overlay layers must stay single-pass"
    assert "layer_overlay_cuts" not in (res.data or {})
    assert _duration(out) == pytest.approx(3.0, abs=0.25), "concat duration changed"


@needs_ffmpeg
def test_direct_compose_warns_on_overlay_layer(vc, tmp_path):
    """operation='compose' doesn't route layers — it must say so, not silently concat."""
    a, out = tmp_path / "a.mp4", tmp_path / "out.mp4"
    _clip(a, dur=1.0)
    res = vc.execute({
        "operation": "compose", "output_path": str(out),
        "edit_decisions": {
            "version": "1.0", "render_runtime": "ffmpeg",
            "cuts": [
                {"id": "a", "source": str(a), "in_seconds": 0, "out_seconds": 1},
                {"id": "b", "source": str(a), "in_seconds": 0, "out_seconds": 1,
                 "layer": "overlay"},
            ],
            "metadata": {"compose_target": {"width": 320, "height": 240, "fps": 24}},
        },
    })
    assert res.success, res.error
    assert any("layer='overlay'" in w for w in (res.data.get("warnings") or [])), (
        "direct compose must warn that layer routing only happens in render"
    )
    assert _duration(out) == pytest.approx(2.0, abs=0.25)
