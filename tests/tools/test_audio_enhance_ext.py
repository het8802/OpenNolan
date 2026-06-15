"""Tests for the audio_enhance extensions (Edits parity: AI voice enhance).

ai_isolate is fully HTTP-mocked (never calls the paid ElevenLabs API). deess and the
podcast preset chain run real ffmpeg on a synthetic sine behind a skipif.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tools.audio.audio_enhance import PRESETS, AudioEnhance, _deess_af

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture
def tool():
    return AudioEnhance()


@pytest.fixture
def sine(tmp_path):
    """A 2s synthetic sine in an aac/m4a container (the tool's default codec is aac)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "src.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:a", "aac", str(p)],
        capture_output=True, check=True,
    )
    return p


def _dur(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(proc.stdout.strip())


class _FakeResponse:
    def __init__(self, content=b"ISOLATED_MP3_BYTES"):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        pass


# --- validation / guards (no ffmpeg, no HTTP) -------------------------------

def test_missing_input_path_rejected(tool):
    res = tool.execute({})
    assert res.success is False and "input_path" in res.error


def test_unknown_mode_rejected(tool, tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    res = tool.execute({"input_path": str(f), "mode": "warp"})
    assert res.success is False and "mode must be one of" in res.error


def test_deess_intensity_out_of_range_rejected(tool, tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    res = tool.execute({"input_path": str(f), "mode": "deess", "intensity": 1.5})
    assert res.success is False and "intensity" in res.error


def test_deess_af_mapping():
    # i defaults to 0 in ffmpeg (a no-op), so the mapping must always set it
    assert _deess_af(0.5) == "deesser=i=0.5:m=0.75:f=0.5"
    assert _deess_af(0.0) == "deesser=i=0.0:m=0.5:f=0.5"
    assert _deess_af(1.0) == "deesser=i=1.0:m=1.0:f=0.5"


def test_podcast_preset_now_contains_deesser():
    # the docstring/description always claimed a de-esser; verify it's really in the chain
    assert "deesser=i=" in PRESETS["podcast"]["af"]
    assert "acompressor" in PRESETS["podcast"]["af"]
    assert "loudnorm" in PRESETS["podcast"]["af"]


def test_other_presets_unchanged():
    # byte-compat guard for the non-podcast presets
    assert PRESETS["normalize_only"]["af"] == "loudnorm=I=-16:LRA=11:TP=-1.5"
    for name in ("clean_speech", "noise_reduce", "broadcast", "voice_clarity"):
        assert "deesser" not in PRESETS[name]["af"]


def test_estimate_cost_zero_for_local_modes(tool):
    assert tool.estimate_cost({"mode": "preset", "input_path": "x.wav"}) == 0.0
    assert tool.estimate_cost({"mode": "deess", "input_path": "x.wav"}) == 0.0
    assert tool.estimate_cost({"input_path": "x.wav"}) == 0.0


def test_estimate_cost_ai_isolate_rough_per_minute(tool):
    # unprobeable input falls back to 1 minute (~1000 chars-equivalent)
    cost = tool.estimate_cost({"mode": "ai_isolate", "input_path": "/nope/missing.wav"})
    assert cost == pytest.approx(tool.ISOLATION_COST_PER_MINUTE_USD)


# --- ai_isolate (HTTP fully mocked) -----------------------------------------

def test_ai_isolate_missing_key_names_env_var(tool, tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    f = tmp_path / "voice.wav"
    f.write_bytes(b"RIFFfake")
    res = tool.execute({"input_path": str(f), "mode": "ai_isolate"})
    assert res.success is False
    assert "ELEVENLABS_API_KEY" in res.error


def test_ai_isolate_calls_endpoint_and_writes_output(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key-123")
    f = tmp_path / "voice.wav"
    f.write_bytes(b"RIFFfake")
    out = tmp_path / "isolated.mp3"

    calls = {}

    def fake_post(url, headers=None, files=None, timeout=None, **kwargs):
        calls["url"] = url
        calls["headers"] = headers
        calls["files"] = files
        calls["timeout"] = timeout
        # consume the multipart body like requests would
        calls["uploaded"] = files["audio"][1].read()
        return _FakeResponse(b"ISOLATED_MP3_BYTES")

    import requests

    monkeypatch.setattr(requests, "post", fake_post)

    res = tool.execute({"input_path": str(f), "mode": "ai_isolate", "output_path": str(out)})
    assert res.success, res.error

    assert calls["url"] == "https://api.elevenlabs.io/v1/audio-isolation"
    assert calls["headers"]["xi-api-key"] == "test-key-123"
    filename, _fh, mime = calls["files"]["audio"]
    assert filename == "voice.wav"
    assert mime  # a content type was supplied
    assert calls["uploaded"] == b"RIFFfake"

    # output written from the mocked response bytes
    assert out.read_bytes() == b"ISOLATED_MP3_BYTES"
    assert str(out) in res.artifacts
    assert res.data["mode"] == "ai_isolate"
    assert res.cost_usd > 0


def test_ai_isolate_default_output_path(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    f = tmp_path / "voice.wav"
    f.write_bytes(b"RIFFfake")

    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(b"mp3"))

    res = tool.execute({"input_path": str(f), "mode": "ai_isolate"})
    assert res.success, res.error
    assert res.data["output"] == str(tmp_path / "voice_isolated.mp3")
    assert (tmp_path / "voice_isolated.mp3").read_bytes() == b"mp3"


def test_ai_isolate_http_error_surfaces(tool, tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    f = tmp_path / "voice.wav"
    f.write_bytes(b"RIFFfake")

    def fake_post(*a, **kw):
        raise RuntimeError("401 unauthorized")

    import requests

    monkeypatch.setattr(requests, "post", fake_post)

    res = tool.execute({"input_path": str(f), "mode": "ai_isolate"})
    assert res.success is False and "isolation failed" in res.error


def test_tool_stays_available_without_elevenlabs_key(tool, monkeypatch):
    # partial availability: the local ffmpeg modes keep the tool usable
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert tool.get_status().value == "available"


# --- deess (real ffmpeg) -----------------------------------------------------

@needs_ffmpeg
def test_deess_runs_and_preserves_duration(tool, sine, tmp_path):
    out = tmp_path / "deessed.m4a"
    res = tool.execute({
        "input_path": str(sine), "mode": "deess", "intensity": 0.7, "output_path": str(out),
    })
    assert res.success, res.error
    assert out.exists() and out.stat().st_size > 0
    assert abs(_dur(out) - _dur(sine)) < 0.3
    assert res.data["filter"] == _deess_af(0.7)
    assert res.data["intensity"] == 0.7


@needs_ffmpeg
def test_deess_default_intensity_and_output_path(tool, sine):
    res = tool.execute({"input_path": str(sine), "mode": "deess"})
    assert res.success, res.error
    assert res.data["output"].endswith("src_deessed.m4a")
    assert res.data["intensity"] == AudioEnhance.DEESS_DEFAULT_INTENSITY


# --- podcast preset chain (real ffmpeg) --------------------------------------

@needs_ffmpeg
def test_podcast_preset_runs_with_deesser_in_chain(tool, sine, tmp_path):
    out = tmp_path / "podcast.m4a"
    res = tool.execute({"input_path": str(sine), "preset": "podcast", "output_path": str(out)})
    assert res.success, res.error
    assert out.exists() and out.stat().st_size > 0
    assert "deesser" in res.data["filter"]
    assert abs(_dur(out) - _dur(sine)) < 0.3


@needs_ffmpeg
def test_default_preset_mode_unchanged(tool, sine, tmp_path):
    # no mode key at all -> original preset behavior (clean_speech default)
    out = tmp_path / "enhanced.m4a"
    res = tool.execute({"input_path": str(sine), "output_path": str(out)})
    assert res.success, res.error
    assert res.data["filter"] == PRESETS["clean_speech"]["af"]
    assert out.exists() and out.stat().st_size > 0
