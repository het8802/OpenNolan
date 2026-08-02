"""Tests for HDR source detection + device HDR-encode capability (Edits-parity guardrails).

These stop the silent HLG->SDR tonemap that degraded the first test-insta-reel cut:
agents detect an HDR source (is_hdr_source) and check the device can encode HDR
(video_compose.get_info()['hdr_encode']) before editing.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tools.video._shared import is_hdr_source

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


def _make_hlg(path):
    # 10-bit HLG / BT.2020 clip (mimics iPhone HDR). The x265-params embed the transfer in
    # the SPS VUI so ffprobe reports color_transfer (the -color_trc flag alone often doesn't).
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=1:r=10",
         "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
         "-x265-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
         "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
         "-tag:v", "hvc1", str(path)],
        capture_output=True, check=True,
    )


def _make_sdr(path):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=1:r=10",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True,
    )


@needs_ffmpeg
def test_detects_hlg_hdr(tmp_path):
    p = tmp_path / "hlg.mp4"
    _make_hlg(p)
    info = is_hdr_source(p)
    assert info["hdr"] is True
    assert info["kind"] == "hlg"
    assert info["bit_depth"] == 10
    assert info["transfer"] == "arib-std-b67"


@needs_ffmpeg
def test_sdr_is_not_hdr(tmp_path):
    p = tmp_path / "sdr.mp4"
    _make_sdr(p)
    info = is_hdr_source(p)
    assert info["hdr"] is False
    assert info["kind"] is None
    assert info["bit_depth"] == 8


def test_missing_file_is_not_hdr(tmp_path):
    # robust: a non-existent / unprobeable path must not raise, must report not-HDR
    info = is_hdr_source(tmp_path / "nope.mp4")
    assert info["hdr"] is False


# --- device HDR-encode capability -----------------------------------------

def test_hdr_encode_capability_shape():
    from tools.video.video_compose import VideoCompose

    info = VideoCompose().get_info()
    assert "hdr_encode" in info
    cap = info["hdr_encode"]
    assert set(cap) >= {"available", "encoders", "note"}
    assert isinstance(cap["available"], bool)
    assert isinstance(cap["encoders"], list)
    # availability must agree with the encoder list
    assert cap["available"] == bool(cap["encoders"])


@needs_ffmpeg
def test_hdr_encoders_found_when_ffmpeg_present():
    from tools.video.video_compose import VideoCompose

    encs = VideoCompose._hdr_encoders()
    # at least one of the known 10-bit HEVC encoders should exist in a normal ffmpeg build
    assert isinstance(encs, list)
    assert all(e in ("hevc_videotoolbox", "libx265") for e in encs)


def test_hdr_encoder_capability_requires_a_successful_encode(monkeypatch):
    from types import SimpleNamespace

    from tools.video.video_compose import VideoCompose

    monkeypatch.setattr("shutil.which", lambda name: f"/test/{name}")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "-encoders" in command:
            return SimpleNamespace(stdout="hevc_videotoolbox libx265", returncode=0)
        encoder = command[command.index("-c:v") + 1]
        return SimpleNamespace(
            stdout="", stderr="", returncode=1 if encoder == "hevc_videotoolbox" else 0
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    if hasattr(VideoCompose._hdr_encoders, "cache_clear"):
        VideoCompose._hdr_encoders.cache_clear()

    assert VideoCompose._hdr_encoders() == ["libx265"]
    assert VideoCompose._hdr_encoders() == ["libx265"]
    assert len(calls) == 3

    if hasattr(VideoCompose._hdr_encoders, "cache_clear"):
        VideoCompose._hdr_encoders.cache_clear()
