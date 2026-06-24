"""Pixel-level tests for per-clip transform (position/scale) + project background
on the FFmpeg compose/assemble path.

The feature: main-timeline cuts can carry transform.scale + transform.position, and
the project can declare metadata.background (color | image) behind all cuts. These
are applied ONLY on the proxy-ASSEMBLE pass (_render_via_ffmpeg sets the
`composite_background` gate on _compose) — never baked into a scene proxy, and the
default (scale=1, position=center, no background) stays byte-identical to legacy.

All real composes run lavfi solid-color clips through actual ffmpeg behind a skipif
and assert measurable PIXELS at known canvas coordinates. We render onto a 1080×1920
vertical canvas so box geometry is deterministic:
  scale=0.5 → box 540×960, centered at PX=270, PY=480.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.video.video_compose import VideoCompose

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")

CANVAS = {"width": 1080, "height": 1920, "fps": 30}


@pytest.fixture
def vc():
    tool = VideoCompose()
    # The final self-review probes/encodes and is orthogonal to compositing geometry;
    # stub it green so tests assert pixels, not the review heuristics.
    tool._run_final_review = lambda *a, **k: {"status": "pass", "issues_found": []}
    return tool


def _solid_clip(path: Path, color: str, *, dur: float = 1.0, size: str = "360x640") -> None:
    """A solid-color video clip (with audio, so concat/xfade have a stream to carry).

    Default size is 9:16 (matches the 1080×1920 canvas aspect) so a scaled clip FILLS
    its box exactly — no intra-box letterbox — and box geometry is sampleable directly.
    """
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={dur}:r=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(path)],
        capture_output=True, check=True,
    )


def _solid_still(path: Path, color: str, size: str = "400x400") -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:d=1",
         "-frames:v", "1", str(path)],
        capture_output=True, check=True,
    )


def _rgb_at(out: Path, t: float, xy: tuple[int, int], tmp: Path) -> tuple[int, int, int]:
    from PIL import Image
    frame = tmp / "frame.png"
    subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", str(out), "-frames:v", "1", str(frame)],
                   capture_output=True, check=True)
    return Image.open(frame).convert("RGB").getpixel(xy)


def _is_red(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r > 150 and g < 90 and b < 90


def _is_blue(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return b > 150 and r < 90 and g < 90


def _is_lime(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return g > 150 and r < 110 and b < 110


def _is_black(rgb: tuple[int, int, int]) -> bool:
    return max(rgb) < 60


def _render_ffmpeg(vc, edit_decisions, asset_manifest, out: Path):
    return vc._render_via_ffmpeg(
        inputs={}, edit_decisions=edit_decisions, asset_manifest=asset_manifest,
        resolved_cuts=edit_decisions["cuts"], output_path=out, profile=None,
    )


# ── 1. default (no transform / no bg) is unchanged ────────────────────────────
@needs_ffmpeg
def test_default_no_transform_no_bg_is_legacy_letterbox(vc, tmp_path):
    """A 640×480 red clip on a 1080×1920 canvas with no transform/bg: corners black
    (letterbox), center red — exactly the legacy fit-and-center behavior. Goes through
    _compose directly with NO composite_background gate."""
    pytest.importorskip("PIL.Image")
    clip = tmp_path / "red.mp4"
    out = tmp_path / "out.mp4"
    # Landscape source on a portrait canvas → fills full width, top/bottom letterbox.
    _solid_clip(clip, "red", size="640x480")

    res = vc._compose({
        "edit_decisions": {
            "version": "1.0", "render_runtime": "ffmpeg",
            "metadata": {"compose_target": CANVAS},
            "cuts": [{"id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 1.0}],
        },
        "output_path": str(out),
    })
    assert res.success, res.error
    # 640×480 → 1080×810 centered: clip y 555..1365.
    assert _is_red(_rgb_at(out, 0.5, (540, 960), tmp_path)), "center must be the clip"
    assert _is_black(_rgb_at(out, 0.5, (540, 50), tmp_path)), "top bar must be black letterbox"


@needs_ffmpeg
def test_default_filter_tail_unchanged_for_legacy_call(vc):
    """The exact legacy filter tail (no transform): scale…decrease, pad…color=black,
    setsar=1, fps — byte-for-byte. Guards the cache/back-compat contract."""
    err, vf, complex_spec = vc._segment_base_vf(
        {"id": "c", "source": "x", "in_seconds": 0, "out_seconds": 1},
        0, 1080, 1920, "30",
    )
    assert err is None
    assert complex_spec is None
    assert vf == [
        "scale=1080:1920:force_original_aspect_ratio=decrease",
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black",
        "setsar=1",
        "fps=30",
    ]


# ── 2. color bg + scale 0.5 (single-input pad path) ───────────────────────────
@needs_ffmpeg
def test_color_bg_scale_half_corner_is_bg(vc, tmp_path):
    """scale=0.5 centered box over a blue color bg: a corner shows blue, center shows clip."""
    pytest.importorskip("PIL.Image")
    clip = tmp_path / "red.mp4"
    out = tmp_path / "out.mp4"
    _solid_clip(clip, "red")

    ed = {
        "version": "1.0", "render_runtime": "ffmpeg",
        "metadata": {"compose_target": CANVAS, "background": {"type": "color", "color": "blue"}},
        "cuts": [{
            "id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 1.0,
            "transform": {"scale": 0.5},
        }],
    }
    res = _render_ffmpeg(vc, ed, {"assets": []}, out)
    assert res.success, res.error
    # box 540×960 centered at (270,480)..(810,1440)
    assert _is_blue(_rgb_at(out, 0.5, (20, 20), tmp_path)), "corner must be bg color (blue)"
    assert _is_red(_rgb_at(out, 0.5, (540, 960), tmp_path)), "center must be the clip (red)"


@needs_ffmpeg
def test_color_bg_accepts_css_hex(vc, tmp_path):
    """A #RRGGBB background color is normalized to ffmpeg's 0xRRGGBB form and fills the canvas."""
    pytest.importorskip("PIL.Image")
    clip = tmp_path / "red.mp4"
    out = tmp_path / "out.mp4"
    _solid_clip(clip, "red")
    ed = {
        "version": "1.0", "render_runtime": "ffmpeg",
        "metadata": {"compose_target": CANVAS, "background": {"type": "color", "color": "#0000FF"}},
        "cuts": [{"id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 1.0,
                  "transform": {"scale": 0.5}}],
    }
    res = _render_ffmpeg(vc, ed, {"assets": []}, out)
    assert res.success, res.error
    assert _is_blue(_rgb_at(out, 0.5, (20, 20), tmp_path)), "corner must be #0000FF → blue"


# ── 3. object position {x:0,y:0} scale 0.5 ────────────────────────────────────
@needs_ffmpeg
def test_object_position_top_left(vc, tmp_path):
    """position {x:0,y:0} scale 0.5: top-left is clip, bottom-right is bg."""
    pytest.importorskip("PIL.Image")
    clip = tmp_path / "red.mp4"
    out = tmp_path / "out.mp4"
    _solid_clip(clip, "red")
    ed = {
        "version": "1.0", "render_runtime": "ffmpeg",
        "metadata": {"compose_target": CANVAS, "background": {"type": "color", "color": "blue"}},
        "cuts": [{
            "id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 1.0,
            "transform": {"scale": 0.5, "position": {"x": 0, "y": 0}},
        }],
    }
    res = _render_ffmpeg(vc, ed, {"assets": []}, out)
    assert res.success, res.error
    # box 540×960 at top-left
    assert _is_red(_rgb_at(out, 0.5, (20, 20), tmp_path)), "top-left must be the clip"
    assert _is_blue(_rgb_at(out, 0.5, (1060, 1900), tmp_path)), "bottom-right must be bg"


# ── 4. partially off-canvas position (overlay-on-color fallback) ──────────────
@needs_ffmpeg
def test_partially_offcanvas_position_uses_overlay_fallback(vc, tmp_path):
    """position {x:-100,y:-100} scale 0.5: the box hangs off the top-left, which `pad`
    cannot express → the overlay-on-color path. Must render with no ffmpeg error, and
    the bottom-right is still bg."""
    pytest.importorskip("PIL.Image")
    clip = tmp_path / "red.mp4"
    out = tmp_path / "out.mp4"
    _solid_clip(clip, "red")
    ed = {
        "version": "1.0", "render_runtime": "ffmpeg",
        "metadata": {"compose_target": CANVAS, "background": {"type": "color", "color": "blue"}},
        "cuts": [{
            "id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 1.0,
            "transform": {"scale": 0.5, "position": {"x": -100, "y": -100}},
        }],
    }
    res = _render_ffmpeg(vc, ed, {"assets": []}, out)
    assert res.success, res.error
    assert out.exists() and out.stat().st_size > 0
    # box covers (-100,-100)..(440,860); (20,20) is inside it → clip
    assert _is_red(_rgb_at(out, 0.5, (20, 20), tmp_path)), "off-canvas box still covers top-left"
    assert _is_blue(_rgb_at(out, 0.5, (1060, 1900), tmp_path)), "uncovered corner is bg"


# ── 5. image background (object-fit: cover) ───────────────────────────────────
@needs_ffmpeg
def test_image_background_covers_canvas(vc, tmp_path):
    """metadata.background.type='image' (a solid lime still) covers the canvas; with a
    scaled clip on top, a corner shows the bg image color and the center shows the clip."""
    pytest.importorskip("PIL.Image")
    clip = tmp_path / "red.mp4"
    bg = tmp_path / "bg.png"
    out = tmp_path / "out.mp4"
    _solid_clip(clip, "red")
    _solid_still(bg, "lime", size="300x300")  # tiny → forces a scale-up-cover

    ed = {
        "version": "1.0", "render_runtime": "ffmpeg",
        "metadata": {
            "compose_target": CANVAS,
            "background": {"type": "image", "asset_id": "bgimg"},
        },
        "cuts": [{
            "id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 1.0,
            "transform": {"scale": 0.5},
        }],
    }
    asset_manifest = {"assets": [{"id": "bgimg", "type": "image", "path": str(bg)}]}
    res = _render_ffmpeg(vc, ed, asset_manifest, out)
    assert res.success, res.error
    assert _is_lime(_rgb_at(out, 0.5, (20, 20), tmp_path)), "corner must be the bg image (lime)"
    assert _is_red(_rgb_at(out, 0.5, (540, 960), tmp_path)), "center must be the clip (red)"


@needs_ffmpeg
def test_image_background_missing_path_falls_back_to_black(vc, tmp_path):
    """An image background whose asset can't be resolved degrades to black (no hard fail)."""
    pytest.importorskip("PIL.Image")
    clip = tmp_path / "red.mp4"
    out = tmp_path / "out.mp4"
    _solid_clip(clip, "red")
    ed = {
        "version": "1.0", "render_runtime": "ffmpeg",
        "metadata": {
            "compose_target": CANVAS,
            "background": {"type": "image", "asset_id": "does-not-exist"},
        },
        "cuts": [{"id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 1.0,
                  "transform": {"scale": 0.5}}],
    }
    res = _render_ffmpeg(vc, ed, {"assets": []}, out)
    assert res.success, res.error
    assert _is_black(_rgb_at(out, 0.5, (20, 20), tmp_path)), "unresolved bg image → black"
    assert _is_red(_rgb_at(out, 0.5, (540, 960), tmp_path)), "center still shows the clip"


# ── 6. named anchor 'top-right' scale 0.5 ─────────────────────────────────────
@needs_ffmpeg
def test_named_anchor_top_right(vc, tmp_path):
    """position='top-right' scale 0.5 (margin 0 → flush): the clip sits top-right, the
    bottom-left corner is bg."""
    pytest.importorskip("PIL.Image")
    clip = tmp_path / "red.mp4"
    out = tmp_path / "out.mp4"
    _solid_clip(clip, "red")
    ed = {
        "version": "1.0", "render_runtime": "ffmpeg",
        "metadata": {"compose_target": CANVAS, "background": {"type": "color", "color": "blue"}},
        "cuts": [{
            "id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 1.0,
            "transform": {"scale": 0.5, "position": "top-right"},
        }],
    }
    res = _render_ffmpeg(vc, ed, {"assets": []}, out)
    assert res.success, res.error
    # box 540×960 at top-right: x in [540,1080), y in [0,960)
    assert _is_red(_rgb_at(out, 0.5, (1060, 20), tmp_path)), "top-right must be the clip"
    assert _is_blue(_rgb_at(out, 0.5, (20, 1900), tmp_path)), "bottom-left must be bg"


# ── 7. assemble-only re-render: proxy cache hits, final pixels change ─────────
@needs_ffmpeg
def test_assemble_only_rerender_keeps_proxy_cache_but_changes_pixels(vc, tmp_path, monkeypatch):
    """Render once (scale=1, no bg), capture the proxy file + its mtime. Re-render with
    ONLY a background+scale change → the scene proxy is a CACHE HIT (not re-rendered:
    same file, same mtime) but the FINAL pixels differ (a corner is now the bg color).
    Proves transform/bg live on the cheap assemble layer, not in the proxy."""
    pytest.importorskip("PIL.Image")
    # Isolate the persistent proxy cache to this test (else a prior run's record makes
    # the FIRST render a stale cache hit).
    monkeypatch.setenv("OPENNOLAN_CACHE_DIR", str(tmp_path / "cache"))
    clip = tmp_path / "red.mp4"
    proxies = tmp_path / "proxies"
    out1 = tmp_path / "final1.mp4"
    out2 = tmp_path / "final2.mp4"
    _solid_clip(clip, "red")

    base_ed = {
        "version": "1.0", "render_runtime": "ffmpeg", "renderer_family": "social-reel",
        "metadata": {"compose_target": CANVAS},
        "cuts": [{"id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 1.0}],
    }
    res1 = vc.execute({
        "operation": "render_proxies", "edit_decisions": base_ed,
        "asset_manifest": {"assets": []}, "output_path": str(out1),
        "proxies_dir": str(proxies),
    })
    assert res1.success, res1.error
    assert res1.data["n_rendered"] == 1 and res1.data["n_cached"] == 0
    proxy_files = sorted(proxies.glob("*.mp4"))
    assert len(proxy_files) == 1, "one scene → one proxy"
    proxy_mtime = proxy_files[0].stat().st_mtime_ns

    # Re-render: SAME scene content, only add a background + scale (assemble-layer edits).
    ed2 = {
        "version": "1.0", "render_runtime": "ffmpeg", "renderer_family": "social-reel",
        "metadata": {"compose_target": CANVAS, "background": {"type": "color", "color": "blue"}},
        "cuts": [{"id": "c1", "source": str(clip), "in_seconds": 0, "out_seconds": 1.0,
                  "transform": {"scale": 0.5}}],
    }
    res2 = vc.execute({
        "operation": "render_proxies", "edit_decisions": ed2,
        "asset_manifest": {"assets": []}, "output_path": str(out2),
        "proxies_dir": str(proxies),
    })
    assert res2.success, res2.error
    assert res2.data["n_cached"] == 1 and res2.data["n_rendered"] == 0, "scene proxy must be a cache HIT"
    assert sorted(proxies.glob("*.mp4")) == proxy_files, "no new proxy written"
    assert proxy_files[0].stat().st_mtime_ns == proxy_mtime, "cached proxy untouched"

    # Pixels differ: render 1 (scale=1, 9:16 source) fills the frame → corner red;
    # render 2 (scale=0.5 over blue bg) leaves the corner outside the box → bg blue.
    assert _is_red(_rgb_at(out1, 0.5, (20, 20), tmp_path)), "first render: full-frame clip"
    assert _is_blue(_rgb_at(out2, 0.5, (20, 20), tmp_path)), "second render: corner now bg blue"


# ── 8. xfade transition + background ──────────────────────────────────────────
@needs_ffmpeg
def test_xfade_with_color_bg(vc, tmp_path):
    """Two scaled cuts over a blue bg, joined by a fade: renders cleanly and the bg color
    is present (the box is scaled, so corners are bg on both sides of the join)."""
    pytest.importorskip("PIL.Image")
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    out = tmp_path / "out.mp4"
    _solid_clip(a, "red", dur=1.5)
    _solid_clip(b, "lime", dur=1.5)
    ed = {
        "version": "1.0", "render_runtime": "ffmpeg",
        "metadata": {"compose_target": CANVAS, "background": {"type": "color", "color": "blue"}},
        "cuts": [
            {"id": "c1", "source": str(a), "in_seconds": 0, "out_seconds": 1.5,
             "transform": {"scale": 0.5}, "transition_out": "fade", "transition_duration": 0.4},
            {"id": "c2", "source": str(b), "in_seconds": 0, "out_seconds": 1.5,
             "transform": {"scale": 0.5}, "transition_in": "fade", "transition_duration": 0.4},
        ],
    }
    res = _render_ffmpeg(vc, ed, {"assets": []}, out)
    assert res.success, res.error
    assert out.exists() and out.stat().st_size > 0
    # well before and well after the join the corner is the bg color
    assert _is_blue(_rgb_at(out, 0.4, (20, 20), tmp_path)), "first scene: corner is bg"
    assert _is_blue(_rgb_at(out, 2.2, (20, 20), tmp_path)), "second scene: corner is bg"
    assert _is_red(_rgb_at(out, 0.4, (540, 960), tmp_path)), "first scene center is its clip"
    assert _is_lime(_rgb_at(out, 2.2, (540, 960), tmp_path)), "second scene center is its clip"


# ── 10. non-uniform scale {x,y} → a split-screen panel box ────────────────────
@needs_ffmpeg
def test_nonuniform_scale_makes_a_half_height_panel(vc, tmp_path):
    """transform.scale={x:1,y:0.5} on a 1080×1920 canvas builds a full-width,
    half-height (1080×960) box. A 9:16 clip FILLS its 9:8 box only after a crop, but
    a same-aspect-as-box clip fills it; here we assert the box LOCATION: a 1080×960
    lime clip placed at y=960 fills the BOTTOM half (lime), leaving the TOP half bg."""
    pytest.importorskip("PIL.Image")
    face = tmp_path / "face.mp4"
    out = tmp_path / "out.mp4"
    # 1080×960 source (matches the half-height box aspect) so it fills with no letterbox
    _solid_clip(face, "lime", dur=1.5, size="1080x960")
    ed = {
        "version": "1.0", "render_runtime": "ffmpeg",
        "metadata": {"compose_target": CANVAS, "background": {"type": "color", "color": "blue"}},
        "cuts": [{"id": "face", "source": str(face), "in_seconds": 0, "out_seconds": 1.5,
                  "transform": {"scale": {"x": 1.0, "y": 0.5}, "position": {"x": 0, "y": 960}}}],
    }
    res = _render_ffmpeg(vc, ed, {"assets": []}, out)
    assert res.success, res.error
    assert _is_blue(_rgb_at(out, 0.5, (540, 200), tmp_path)), "top half is bg"
    assert _is_lime(_rgb_at(out, 0.5, (540, 1400), tmp_path)), "bottom half is the panel clip"


# ── 9. PiP (layer='overlay') rejects an {x,y} position ────────────────────────
@needs_ffmpeg
def test_pip_overlay_rejects_object_position(vc, tmp_path):
    """A layer='overlay' (PiP) cut whose transform.position is an {x,y} object must be
    rejected with a clear structured error — PiP uses named anchors only."""
    base = tmp_path / "base.mp4"
    pip = tmp_path / "pip.mp4"
    out = tmp_path / "out.mp4"
    _solid_clip(base, "red", dur=2.0)
    _solid_clip(pip, "lime", dur=2.0)
    ed = {
        "version": "1.0", "render_runtime": "ffmpeg",
        "metadata": {"compose_target": CANVAS},
        "cuts": [
            {"id": "base", "source": str(base), "in_seconds": 0, "out_seconds": 2.0},
            {"id": "pip", "source": str(pip), "in_seconds": 0, "out_seconds": 2.0,
             "layer": "overlay", "transform": {"scale": 0.3, "position": {"x": 10, "y": 10}}},
        ],
    }
    res = _render_ffmpeg(vc, ed, {"assets": []}, out)
    assert not res.success
    assert "named anchor" in (res.error or "").lower(), res.error
