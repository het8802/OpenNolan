"""HDR preservation through render_proxies / _compose / the ffmpeg assemble.

render_proxies must edit HDR (HLG/PQ) footage exactly like SDR: the HDR is
PRESERVED end-to-end (10-bit HEVC main10 + HLG/PQ color tags), never silently
tonemapped; a timeline that MIXES HDR footage with SDR graphics/stills lifts the
SDR into the same HDR (BT.2020) container so every proxy concats in one color
space (Het's decision 2026-06-22). hdr_policy controls the behavior; the decision
is always reported (data.hdr_handling + warnings), never silent.

These assert the MACHINE-checkable contract (pix_fmt, color tags, cache identity,
governance). Color FIDELITY of lifted graphics is a human visual-review item.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.video.video_compose import VideoCompose
from tools.video._shared import is_hdr_source, probe_output

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture
def vc():
    return VideoCompose()


_HAS_HDR_ENC = bool(VideoCompose._hdr_encoders()) if HAS_FFMPEG else False
_HAS_ZSCALE = VideoCompose._zscale_available() if HAS_FFMPEG else False
needs_hdr = pytest.mark.skipif(
    not (_HAS_HDR_ENC and _HAS_ZSCALE),
    reason="needs a 10-bit HEVC encoder (hevc_videotoolbox/libx265) and zscale",
)


def _hlg_clip(path: Path, *, dur: float = 2.0) -> None:
    """A 10-bit HLG / BT.2020 test clip (the shape of modern phone footage)."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=30:d={dur}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
         "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
         "-x265-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
         "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
         "-colorspace", "bt2020nc", "-tag:v", "hvc1", "-c:a", "aac", str(path)],
        capture_output=True, check=True,
    )


def _sdr_clip(path: Path, *, dur: float = 2.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=30:d={dur}",
         "-f", "lavfi", "-i", f"sine=frequency=330:duration={dur}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        capture_output=True, check=True,
    )


def _color(path: Path) -> dict:
    info = probe_output(Path(path))
    return {k: info.get(k) for k in
            ("video_codec", "pix_fmt", "color_transfer", "color_primaries", "color_space")}


def _ed(cuts: list[dict]) -> dict:
    return {
        "version": "1.0", "render_runtime": "ffmpeg", "renderer_family": "social-reel",
        "cuts": cuts,
        "metadata": {"compose_target": {"width": 640, "height": 360, "fps": 30}},
    }


# ── detection ────────────────────────────────────────────────────────────────
@needs_ffmpeg
def test_is_hdr_source_detects_hlg(tmp_path):
    clip = tmp_path / "hlg.mp4"
    _hlg_clip(clip)
    info = is_hdr_source(clip)
    assert info["hdr"] is True
    assert info["kind"] == "hlg"
    assert info["bit_depth"] == 10


@needs_ffmpeg
def test_is_hdr_source_sdr_is_not_hdr(tmp_path):
    clip = tmp_path / "sdr.mp4"
    _sdr_clip(clip)
    assert is_hdr_source(clip)["hdr"] is False


# ── preserve (the core ask) ──────────────────────────────────────────────────
@needs_ffmpeg
@needs_hdr
def test_render_proxies_preserves_hdr_end_to_end(vc, tmp_path):
    """A pure-HDR timeline → 10-bit HEVC HLG output, end to end through assemble."""
    hlg = tmp_path / "hlg.mp4"
    _hlg_clip(hlg)
    out = tmp_path / "out.mp4"
    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": _ed([{"id": "a", "source": str(hlg), "in_seconds": 0, "out_seconds": 2.0}]),
        "asset_manifest": {"assets": []},
        "output_path": str(out), "proxies_dir": str(tmp_path / "px"),
    })
    assert res.success, res.error
    assert out.exists()
    c = _color(out)
    assert c["video_codec"] == "hevc"
    assert c["pix_fmt"] == "yuv420p10le"
    assert c["color_transfer"] == "arib-std-b67"
    assert c["color_primaries"] == "bt2020"
    # decision is reported, never silent
    h = res.data["hdr_handling"]
    assert h["decision"] == "preserve" and h["source_hdr"] is True


@needs_ffmpeg
@needs_hdr
def test_mixed_timeline_promotes_sdr_into_hdr_and_concats(vc, tmp_path):
    """HDR footage + an SDR clip → SDR is lifted into the HDR container; the two
    proxies concat with no pix_fmt mismatch; output is one consistent 10-bit HLG."""
    hlg = tmp_path / "hlg.mp4"
    sdr = tmp_path / "sdr.mp4"
    _hlg_clip(hlg)
    _sdr_clip(sdr)
    out = tmp_path / "out.mp4"
    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": _ed([
            {"id": "a", "source": str(hlg), "in_seconds": 0, "out_seconds": 1.5},
            {"id": "b", "source": str(sdr), "in_seconds": 0, "out_seconds": 1.5},
        ]),
        "asset_manifest": {"assets": []},
        "output_path": str(out), "proxies_dir": str(tmp_path / "px"),
    })
    assert res.success, res.error
    c = _color(out)
    assert c["pix_fmt"] == "yuv420p10le"
    assert c["color_transfer"] == "arib-std-b67"
    # both scenes are in the HDR timeline; the SDR one was promoted, so the concat
    # (which would fail on a pix_fmt mismatch) succeeded into one 10-bit HLG output.
    assert res.data["n_scenes"] == 2


# ── tonemap / sdr ────────────────────────────────────────────────────────────
@needs_ffmpeg
@needs_hdr
def test_tonemap_policy_converts_hdr_to_sdr(vc, tmp_path):
    hlg = tmp_path / "hlg.mp4"
    _hlg_clip(hlg)
    out = tmp_path / "out.mp4"
    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": _ed([{"id": "a", "source": str(hlg), "in_seconds": 0, "out_seconds": 2.0}]),
        "asset_manifest": {"assets": []},
        "output_path": str(out), "proxies_dir": str(tmp_path / "px"),
        "hdr_policy": "tonemap",
    })
    assert res.success, res.error
    c = _color(out)
    assert c["pix_fmt"] == "yuv420p"
    assert c["color_transfer"] in ("bt709", "", None) or "2020" not in (c["color_transfer"] or "")
    assert res.data["hdr_handling"]["decision"] == "tonemap"


@needs_ffmpeg
def test_sdr_only_timeline_is_unchanged_sdr(vc, tmp_path):
    """Pure-SDR timeline must stay the legacy 8-bit path (no HDR tags, no 10-bit)."""
    sdr = tmp_path / "sdr.mp4"
    _sdr_clip(sdr)
    out = tmp_path / "out.mp4"
    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": _ed([{"id": "a", "source": str(sdr), "in_seconds": 0, "out_seconds": 2.0}]),
        "asset_manifest": {"assets": []},
        "output_path": str(out), "proxies_dir": str(tmp_path / "px"),
    })
    assert res.success, res.error
    c = _color(out)
    assert c["pix_fmt"] == "yuv420p"
    assert (c["color_transfer"] or "") in ("", "bt709", "unknown")
    assert res.data["hdr_handling"]["decision"] == "sdr"


# ── cache identity (no stale-pixel collision) ────────────────────────────────
@needs_ffmpeg
@needs_hdr
def test_preserve_and_tonemap_proxies_do_not_collide(vc, tmp_path):
    """Same source, different HDR decision → DISTINCT proxy files (the cache key
    folds in the HDR output decision)."""
    hlg = tmp_path / "hlg.mp4"
    _hlg_clip(hlg)
    pdir = tmp_path / "px"
    ed = _ed([{"id": "a", "source": str(hlg), "in_seconds": 0, "out_seconds": 2.0}])
    r1 = vc.execute({"operation": "render_proxies", "edit_decisions": ed,
                     "asset_manifest": {"assets": []}, "proxies_dir": str(pdir),
                     "hdr_policy": "preserve"})
    r2 = vc.execute({"operation": "render_proxies", "edit_decisions": ed,
                     "asset_manifest": {"assets": []}, "proxies_dir": str(pdir),
                     "hdr_policy": "tonemap"})
    assert r1.success and r2.success
    assert r1.data["proxies"][0]["proxy_path"] != r2.data["proxies"][0]["proxy_path"]


@needs_ffmpeg
def test_sdr_cache_key_unchanged_by_hdr_field(vc, tmp_path):
    """A pure-SDR scene carries NO hdr block in its identity, so existing SDR
    cache entries keep their keys (no cache-wide invalidation)."""
    sdr = tmp_path / "sdr.mp4"
    _sdr_clip(sdr)
    pdir = tmp_path / "px"
    ed = _ed([{"id": "a", "source": str(sdr), "in_seconds": 0, "out_seconds": 2.0}])
    r1 = vc.execute({"operation": "render_proxies", "edit_decisions": ed,
                     "asset_manifest": {"assets": []}, "proxies_dir": str(pdir)})
    r2 = vc.execute({"operation": "render_proxies", "edit_decisions": ed,
                     "asset_manifest": {"assets": []}, "proxies_dir": str(pdir)})
    assert r1.success and r2.success
    # second run is a pure cache hit (same key)
    assert r2.data["n_cached"] == 1 and r2.data["n_rendered"] == 0


# ── governance: proxy assemble is not a runtime swap ─────────────────────────
@needs_ffmpeg
@needs_hdr
def test_assemble_metadata_and_no_false_runtime_swap(vc, tmp_path):
    hlg = tmp_path / "hlg.mp4"
    _hlg_clip(hlg)
    ed = _ed([{"id": "a", "source": str(hlg), "in_seconds": 0, "out_seconds": 2.0}])
    res = vc.execute({"operation": "render_proxies", "edit_decisions": ed,
                      "asset_manifest": {"assets": []}, "proxies_dir": str(tmp_path / "px"),
                      "hdr_policy": "preserve"})
    assert res.success, res.error
    ae = res.data["assemble_edit_decisions"]
    assert ae["metadata"]["assemble_of_proxies"] is True
    assert ae["metadata"]["hdr"]["enabled"] is True
    assert ae["metadata"]["hdr"]["kind"] == "hlg"

    proxy = res.data["proxies"][0]["proxy_path"]
    # A proposal locking a DIFFERENT runtime must NOT be flagged as a swap here.
    fr = vc._run_final_review(Path(proxy), ae, {"production_plan": {"render_runtime": "remotion"}})
    pp = fr["checks"]["promise_preservation"]
    assert not pp.get("runtime_swap_detected")
    assert "two-phase proxy assemble" in pp.get("runtime_swap_check", "")


# ── blocker: preserve requested but no encoder available ─────────────────────
@needs_ffmpeg
def test_preserve_without_encoder_blocks(vc, tmp_path, monkeypatch):
    """hdr_policy='preserve' with no 10-bit HEVC encoder is a structured blocker,
    not a silent tonemap."""
    hlg = tmp_path / "hlg.mp4"
    _hlg_clip(hlg)
    monkeypatch.setattr(VideoCompose, "_hdr_encoders", staticmethod(lambda: []))
    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": _ed([{"id": "a", "source": str(hlg), "in_seconds": 0, "out_seconds": 2.0}]),
        "asset_manifest": {"assets": []}, "proxies_dir": str(tmp_path / "px"),
        "hdr_policy": "preserve",
    })
    assert not res.success
    assert "no 10-bit HEVC encoder" in (res.error or "")


@needs_ffmpeg
def test_auto_without_encoder_falls_back_to_tonemap_with_warning(vc, tmp_path, monkeypatch):
    hlg = tmp_path / "hlg.mp4"
    _hlg_clip(hlg)
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(VideoCompose, "_hdr_encoders", staticmethod(lambda: []))
    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": _ed([{"id": "a", "source": str(hlg), "in_seconds": 0, "out_seconds": 2.0}]),
        "asset_manifest": {"assets": []}, "output_path": str(out),
        "proxies_dir": str(tmp_path / "px"), "hdr_policy": "auto",
    })
    assert res.success, res.error
    assert res.data["hdr_handling"]["decision"] == "tonemap"
    assert any("no 10-bit HEVC encoder" in w for w in (res.data.get("warnings") or []))


# ── zscale-availability guard (the critical color-mislabel fix) ──────────────
@needs_ffmpeg
@needs_hdr
def test_preserve_mixed_without_zscale_blocks(vc, tmp_path, monkeypatch):
    """preserve + a MIXED timeline + no zscale can't promote SDR → must BLOCK,
    not silently encode SDR pixels with HDR tags."""
    hlg = tmp_path / "hlg.mp4"
    sdr = tmp_path / "sdr.mp4"
    _hlg_clip(hlg)
    _sdr_clip(sdr)
    monkeypatch.setattr(VideoCompose, "_zscale_available", classmethod(lambda cls: False))
    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": _ed([
            {"id": "a", "source": str(hlg), "in_seconds": 0, "out_seconds": 1.0},
            {"id": "b", "source": str(sdr), "in_seconds": 0, "out_seconds": 1.0},
        ]),
        "asset_manifest": {"assets": []}, "proxies_dir": str(tmp_path / "px"),
        "hdr_policy": "preserve",
    })
    assert not res.success
    assert "zscale" in (res.error or "") and "libzimg" in (res.error or "")


@needs_ffmpeg
@needs_hdr
def test_auto_mixed_without_zscale_tonemaps_with_warning(vc, tmp_path, monkeypatch):
    hlg = tmp_path / "hlg.mp4"
    sdr = tmp_path / "sdr.mp4"
    _hlg_clip(hlg)
    _sdr_clip(sdr)
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(VideoCompose, "_zscale_available", classmethod(lambda cls: False))
    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": _ed([
            {"id": "a", "source": str(hlg), "in_seconds": 0, "out_seconds": 1.0},
            {"id": "b", "source": str(sdr), "in_seconds": 0, "out_seconds": 1.0},
        ]),
        "asset_manifest": {"assets": []}, "output_path": str(out),
        "proxies_dir": str(tmp_path / "px"), "hdr_policy": "auto",
    })
    assert res.success, res.error
    assert res.data["hdr_handling"]["decision"] == "tonemap"
    assert any("zscale" in w for w in (res.data.get("warnings") or []))


@needs_ffmpeg
@needs_hdr
def test_pure_hdr_preserve_works_without_zscale(vc, tmp_path, monkeypatch):
    """A timeline where every cut is already HDR needs NO promotion, so preserve
    must still work even when zscale is unavailable."""
    hlg = tmp_path / "hlg.mp4"
    _hlg_clip(hlg)
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(VideoCompose, "_zscale_available", classmethod(lambda cls: False))
    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": _ed([{"id": "a", "source": str(hlg), "in_seconds": 0, "out_seconds": 2.0}]),
        "asset_manifest": {"assets": []}, "output_path": str(out),
        "proxies_dir": str(tmp_path / "px"), "hdr_policy": "preserve",
    })
    assert res.success, res.error
    assert _color(out)["pix_fmt"] == "yuv420p10le"


# ── tonemap honesty: explicit SDR tags (no mistagged HDR metadata) ───────────
@needs_ffmpeg
@needs_hdr
def test_tonemap_output_is_explicitly_bt709_tagged(vc, tmp_path):
    """Tonemap must emit explicit BT.709 tags so the 8-bit output isn't left
    wearing the source's HDR (BT.2020/PQ) metadata."""
    hlg = tmp_path / "hlg.mp4"
    _hlg_clip(hlg)
    out = tmp_path / "out.mp4"
    res = vc.execute({
        "operation": "render_proxies",
        "edit_decisions": _ed([{"id": "a", "source": str(hlg), "in_seconds": 0, "out_seconds": 2.0}]),
        "asset_manifest": {"assets": []}, "output_path": str(out),
        "proxies_dir": str(tmp_path / "px"), "hdr_policy": "tonemap",
    })
    assert res.success, res.error
    c = _color(out)
    assert c["color_primaries"] == "bt709"
    assert "2020" not in (c["color_transfer"] or "")


# ── overlays composite in 10-bit on an HDR base (no silent downgrade) ────────
@needs_ffmpeg
@needs_hdr
def test_overlay_on_hdr_base_stays_10bit(vc, tmp_path):
    hlg = tmp_path / "hlg.mp4"
    badge = tmp_path / "badge.png"
    _hlg_clip(hlg)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=orange:s=200x120:d=1",
                    "-frames:v", "1", str(badge)], capture_output=True, check=True)
    out = tmp_path / "out.mp4"
    ed = _ed([{"id": "a", "source": str(hlg), "in_seconds": 0, "out_seconds": 2.0}])
    ed["overlays"] = [{"type": "image", "asset_id": str(badge),
                       "position": {"x": 40, "y": 40}, "width": 200,
                       "start_seconds": 0, "end_seconds": 2.0}]
    res = vc.execute({"operation": "render_proxies", "edit_decisions": ed,
                      "asset_manifest": {"assets": []}, "output_path": str(out),
                      "proxies_dir": str(tmp_path / "px")})
    assert res.success, res.error
    c = _color(out)
    assert c["pix_fmt"] == "yuv420p10le"
    assert c["color_transfer"] == "arib-std-b67"
