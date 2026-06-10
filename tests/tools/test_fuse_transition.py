"""Tests for tools/video/fuse_transition.py (Edits-parity "fuse" morph).

Generation is MOCKED (a fake seedance tool writes a lavfi clip) — no API calls, no cost.
Frame extraction, morph normalization, and the concat splice run real ffmpeg on synthetic
lavfi clips and assert measurable outcomes (duration math, probe values, audio presence).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.base_tool import ToolResult, ToolStatus
from tools.video.fuse_transition import FuseTransition

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.delenv(FuseTransition.AUTOCONFIRM_ENV, raising=False)
    return FuseTransition()


@pytest.fixture
def clip_a(tmp_path):
    """2s animated clip WITH audio (A's last frame seeds the morph)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "a.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=2:r=25",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-pix_fmt", "yuv420p", "-shortest", str(p)],
        capture_output=True, check=True,
    )
    return p


@pytest.fixture
def clip_b(tmp_path):
    """2s static SILENT clip (exercises the silence-fill conform path)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "b.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "smptebars=s=320x240:d=2:r=25",
         "-pix_fmt", "yuv420p", str(p)],
        capture_output=True, check=True,
    )
    return p


class FakeSeedance:
    """Stands in for the registry's seedance_video: writes a lavfi 'morph' clip at a
    DIFFERENT resolution/fps (640x360@30) so normalization is actually exercised."""

    name = "seedance_video"
    install_instructions = "Set FAL_KEY to your fal.ai API key."

    def __init__(self):
        self.calls: list[dict] = []

    def get_status(self):
        return ToolStatus.AVAILABLE

    def execute(self, inputs):
        self.calls.append(dict(inputs))
        out = Path(inputs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        dur = int(inputs["duration"])
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=s=640x360:d={dur}:r=30",
             "-pix_fmt", "yuv420p", str(out)],
            capture_output=True, check=True,
        )
        return ToolResult(
            success=True,
            data={"output_path": str(out)},
            artifacts=[str(out)],
            cost_usd=0.97,
            model="bytedance/seedance-2.0/fast/image-to-video",
        )


def _wire_mocks(tool, monkeypatch, fake):
    monkeypatch.setattr(tool, "_get_generator", lambda: fake)
    monkeypatch.setattr(
        tool, "_upload_frame",
        lambda p: f"https://fake.upload/{Path(p).name}" if Path(p).exists()
        else (_ for _ in ()).throw(AssertionError(f"uploaded frame missing: {p}")),
    )
    return fake


def _ffprobe_image_size(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    w, h = proc.stdout.strip().split(",")
    return int(w), int(h)


# --- validation / guards (no generation) ------------------------------------

@needs_ffmpeg
def test_missing_clips_rejected(tool, tmp_path):
    res = tool.execute({"clip_b": "x.mp4"})
    assert res.success is False and "clip_a" in res.error
    res = tool.execute({"clip_a": str(tmp_path / "nope.mp4"), "clip_b": str(tmp_path / "nope2.mp4")})
    assert res.success is False and "not found" in res.error


@needs_ffmpeg
def test_unsupported_format_rejected(tool, tmp_path):
    bad = tmp_path / "a.gif"
    bad.write_bytes(b"GIF")
    res = tool.execute({"clip_a": str(bad), "clip_b": str(bad)})
    assert res.success is False and "unsupported format" in res.error


@needs_ffmpeg
def test_bad_morph_duration_rejected(tool, clip_a, clip_b):
    for bad in (0.0, 99, "1"):
        res = tool.execute({"clip_a": str(clip_a), "clip_b": str(clip_b), "morph_duration": bad})
        assert res.success is False and "morph_duration" in res.error


@needs_ffmpeg
def test_bad_variant_and_seed_rejected(tool, clip_a, clip_b):
    res = tool.execute({"clip_a": str(clip_a), "clip_b": str(clip_b), "model_variant": "ultra"})
    assert res.success is False and "model_variant" in res.error
    res = tool.execute({"clip_a": str(clip_a), "clip_b": str(clip_b), "seed": 1.5})
    assert res.success is False and "seed" in res.error


@needs_ffmpeg
def test_resolution_mismatch_rejected(tool, monkeypatch, clip_a, tmp_path):
    small = tmp_path / "small.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=160x120:d=1:r=25",
         "-pix_fmt", "yuv420p", str(small)],
        capture_output=True, check=True,
    )
    fake = _wire_mocks(tool, monkeypatch, FakeSeedance())
    res = tool.execute({"clip_a": str(clip_a), "clip_b": str(small), "confirm": True})
    assert res.success is False and "refuses to guess" in res.error
    assert fake.calls == []


# --- confirmation gate -------------------------------------------------------

@needs_ffmpeg
def test_no_confirm_returns_dry_run_estimate_without_generating(tool, monkeypatch, clip_a, clip_b):
    fake = FakeSeedance()
    monkeypatch.setattr(tool, "_get_generator", lambda: fake)

    def _boom(*a, **k):
        raise AssertionError("must not upload/generate without confirmation")

    monkeypatch.setattr(tool, "_upload_frame", _boom)
    res = tool.execute({"clip_a": str(clip_a), "clip_b": str(clip_b), "morph_duration": 1.0})
    assert res.success is False
    assert res.data.get("requires_confirmation") is True
    # 1s morph still bills Seedance's 4s minimum at the fast rate
    assert res.data["billable_seconds"] == 4
    assert res.data["estimated_cost_usd"] == pytest.approx(0.2419 * 4, abs=0.01)
    assert "Nothing was spent" in res.error
    assert fake.calls == []


@needs_ffmpeg
def test_autoconfirm_env_passes_gate(tool, monkeypatch, clip_a, clip_b, tmp_path):
    fake = _wire_mocks(tool, monkeypatch, FakeSeedance())
    monkeypatch.setenv(FuseTransition.AUTOCONFIRM_ENV, "1")
    out = tmp_path / "auto.mp4"
    res = tool.execute({"clip_a": str(clip_a), "clip_b": str(clip_b), "output_path": str(out)})
    assert res.success, res.error
    assert len(fake.calls) == 1


# --- unavailable provider (never substitute a crossfade) ---------------------

@needs_ffmpeg
def test_generator_unavailable_fails_fast_with_install_instructions(tool, monkeypatch, clip_a, clip_b):
    from tools.video.seedance_video import SeedanceVideo

    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("FAL_AI_API_KEY", raising=False)
    monkeypatch.setattr(tool, "_get_generator", lambda: SeedanceVideo())
    res = tool.execute({"clip_a": str(clip_a), "clip_b": str(clip_b), "confirm": True})
    assert res.success is False
    assert "UNAVAILABLE" in res.error
    assert "fal.ai" in res.error  # the generator's install_instructions, verbatim
    assert "crossfade" in res.error  # points at the free alternative, does NOT run it


@needs_ffmpeg
def test_generator_missing_from_registry_fails_fast(tool, monkeypatch, clip_a, clip_b):
    monkeypatch.setattr(tool, "_get_generator", lambda: None)
    res = tool.execute({"clip_a": str(clip_a), "clip_b": str(clip_b), "confirm": True})
    assert res.success is False and "seedance_video" in res.error


# --- frame extraction --------------------------------------------------------

@needs_ffmpeg
def test_extracts_first_and_last_frames(tool, clip_a, tmp_path):
    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    assert tool._extract_first_frame(clip_a, first) is None
    assert tool._extract_last_frame(clip_a, last, tool._probe(clip_a)) is None
    assert first.exists() and last.exists()
    assert _ffprobe_image_size(first) == (320, 240)
    assert _ffprobe_image_size(last) == (320, 240)
    # testsrc animates, so the boundary frames must be different images
    assert hashlib.md5(first.read_bytes()).hexdigest() != hashlib.md5(last.read_bytes()).hexdigest()


# --- morph normalization (resolution / fps / retime / silence fill) ----------

@needs_ffmpeg
def test_conform_retimes_and_normalizes_morph(tool, tmp_path):
    raw = tmp_path / "morph_raw.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=4:r=30",
         "-pix_fmt", "yuv420p", str(raw)],
        capture_output=True, check=True,
    )
    dst = tmp_path / "morph_norm.mp4"
    err = tool._conform(raw, dst, 320, 240, 25.0, True,
                        retime_ratio=1.0 / 4.0, trim=1.0, src_duration=1.0, drop_src_audio=True)
    assert err is None, err
    probed = tool._probe(dst)
    assert probed["resolution"] == "320x240"
    assert probed["fps"] == pytest.approx(25.0, abs=0.1)
    assert probed["duration_seconds"] == pytest.approx(1.0, abs=0.2)  # 4s retimed to 1s, not cut
    assert tool._has_audio(dst)  # silence-filled so the concat demuxer sees uniform streams


# --- splice (mocked generation, live ffmpeg) ----------------------------------

@needs_ffmpeg
def test_fuse_splices_a_morph_b(tool, monkeypatch, clip_a, clip_b, tmp_path):
    fake = _wire_mocks(tool, monkeypatch, FakeSeedance())
    out = tmp_path / "fused.mp4"
    res = tool.execute({
        "clip_a": str(clip_a), "clip_b": str(clip_b),
        "morph_duration": 1.0, "confirm": True, "output_path": str(out),
    })
    assert res.success, res.error
    # 2s A + 1s morph + 2s B
    assert res.data["duration_seconds"] == pytest.approx(5.0, abs=0.5)
    assert res.data["resolution"] == "320x240"  # morph normalized from 640x360 to A's res
    assert tool._has_audio(out)  # A has audio -> uniform audio across the splice
    assert res.cost_usd == pytest.approx(0.97)
    assert str(out) in res.artifacts

    # the generator was conditioned start=A's last frame, end=B's first frame
    assert len(fake.calls) == 1
    payload = fake.calls[0]
    assert payload["operation"] == "image_to_video"
    assert payload["prompt"] == FuseTransition.DEFAULT_PROMPT
    assert payload["duration"] == "4"  # 1s morph still needs the 4s generation floor
    assert payload["generate_audio"] is False
    assert payload["image_url"].endswith("a_last.png")
    assert payload["end_image_url"].endswith("b_first.png")
    assert payload["aspect_ratio"] == "4:3"  # 320x240
    # intermediates cleaned up by default
    assert not (tmp_path / "fused_fuse_work").exists()


@needs_ffmpeg
def test_fuse_silent_clips_produce_video_only_output(tool, monkeypatch, clip_b, tmp_path):
    # both inputs silent -> no fabricated audio track on the splice
    fake = _wire_mocks(tool, monkeypatch, FakeSeedance())
    other = tmp_path / "b2.mp4"
    shutil.copyfile(clip_b, other)
    out = tmp_path / "fused_silent.mp4"
    res = tool.execute({
        "clip_a": str(clip_b), "clip_b": str(other),
        "morph_duration": 1.0, "confirm": True, "output_path": str(out),
    })
    assert res.success, res.error
    assert res.data["duration_seconds"] == pytest.approx(5.0, abs=0.5)
    assert not tool._has_audio(out)
    assert len(fake.calls) == 1


@needs_ffmpeg
def test_generation_failure_propagates(tool, monkeypatch, clip_a, clip_b, tmp_path):
    fake = FakeSeedance()
    fake.execute = lambda inputs: ToolResult(success=False, error="quota exceeded")
    _wire_mocks(tool, monkeypatch, fake)
    res = tool.execute({
        "clip_a": str(clip_a), "clip_b": str(clip_b), "confirm": True,
        "output_path": str(tmp_path / "x.mp4"),
    })
    assert res.success is False and "morph generation failed" in res.error and "quota" in res.error


# --- provenance ---------------------------------------------------------------

@needs_ffmpeg
def test_registers_spliced_asset_with_provenance(tool, monkeypatch, clip_a, clip_b, tmp_path):
    _wire_mocks(tool, monkeypatch, FakeSeedance())
    manifest = tmp_path / "asset_manifest.json"
    manifest.write_text(json.dumps({"version": "1.0", "assets": []}))
    out = tmp_path / "fused.mp4"
    res = tool.execute({
        "clip_a": str(clip_a), "clip_b": str(clip_b), "confirm": True,
        "output_path": str(out), "asset_manifest_path": str(manifest), "scene_id": "scene-7",
    })
    assert res.success, res.error
    doc = json.loads(manifest.read_text())
    assert len(doc["assets"]) == 1
    a = doc["assets"][0]
    assert a["source_tool"] == "fuse_transition"
    assert a["subtype"] == "fuse"
    assert a["scene_id"] == "scene-7"
    assert a["duration_seconds"] == pytest.approx(5.0, abs=0.5)
    assert str(manifest) in res.artifacts
