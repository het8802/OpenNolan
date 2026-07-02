"""Tests for tools/audio/audio_mixer.py fixes + extensions.

Covers three changes:
  1. fade_out bug fix — afade t=out now gets an explicit st= from the probed
     track duration (FFmpeg defaults st=0, which faded the track to silence
     over its FIRST N seconds and kept it muted). Verified the way the bug
     was found: render a faded sine, then measure window loudness with
     ffmpeg volumedetect — the body must stay loud, the tail must fall away.
  2. auto_balance op — per-track integrated LUFS measurement (ebur128) and
     gain computation toward voice-anchored role targets, dry + apply modes.
  3. extract extension — codec/sample_rate/channels/stream_index for
     full-fidelity detach; legacy default (pcm_s16le 16k mono) unchanged.

Validation/guard paths are pure (no ffmpeg). Real paths run actual ffmpeg
behind a skipif and assert measurable outcomes (window loudness, probe values).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from tools.audio.audio_mixer import AudioMixer

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture
def tool():
    return AudioMixer()


def _make_sine(path, seconds, gain_db=0):
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
           "-i", f"sine=frequency=440:duration={seconds}"]
    if gain_db:
        cmd += ["-af", f"volume={gain_db}dB"]
    cmd.append(str(path))
    subprocess.run(cmd, capture_output=True, check=True)
    return path


@pytest.fixture
def sine5(tmp_path):
    """5s sine wav (the lavfi sine sits around -21 dB mean volume)."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    return _make_sine(tmp_path / "sine5.wav", 5)


@pytest.fixture
def quiet5(tmp_path):
    """5s sine 15 dB quieter than sine5."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    return _make_sine(tmp_path / "quiet5.wav", 5, gain_db=-15)


@pytest.fixture
def video_clip(tmp_path):
    """2s mp4 (testsrc video + aac audio) for extract tests."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=s=320x240:d=2:r=25",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-pix_fmt", "yuv420p", "-shortest", str(p)],
        capture_output=True, check=True,
    )
    return p


def _mean_db(path, start, dur):
    """volumedetect mean_volume of a window — pure digital silence reads ~-91 dB."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-t", str(dur),
         "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", proc.stderr)
    assert m, f"no mean_volume in volumedetect output for {path}"
    return float(m.group(1))


def _probe_stream(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,sample_rate,channels",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)["streams"][0]


def _duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(out.strip())


# --- validation / guards (no ffmpeg) --------------------------------------

def test_per_track_chain_orders_fades_before_delay(tool):
    # no fades -> no duration probe needed; adelay must come last in the chain
    chain = tool._per_track_chain(0, {"path": "x.wav", "volume": 0.5, "start_seconds": 1})
    assert chain == "[0:a]volume=0.5,adelay=1000|1000[a0]"
    assert tool._per_track_chain(2, {"path": "x.wav"}) == "[2:a]acopy[a2]"


def test_auto_balance_requires_tracks(tool):
    res = tool.execute({"operation": "auto_balance", "tracks": []})
    assert res.success is False and "No tracks" in res.error


def test_auto_balance_rejects_unknown_role(tool):
    res = tool.execute({
        "operation": "auto_balance",
        "tracks": [{"path": "/nope/missing.wav", "role": "drums"}],
    })
    assert res.success is False and "role" in res.error


def test_extract_rejects_unknown_codec(tool):
    res = tool.execute({"operation": "extract", "input_path": "x.mp4", "codec": "flac"})
    assert res.success is False and "codec" in res.error


def test_extract_copy_rejects_resample_params(tool):
    res = tool.execute({
        "operation": "extract", "input_path": "x.mp4",
        "codec": "copy", "sample_rate": 44100,
    })
    assert res.success is False and "copy" in res.error


def test_extract_rejects_bad_stream_index(tool):
    res = tool.execute({"operation": "extract", "input_path": "x.mp4", "stream_index": -1})
    assert res.success is False and "stream_index" in res.error


# --- fade_out fix ----------------------------------------------------------

@needs_ffmpeg
def test_mix_fade_out_lands_at_track_end(tool, sine5, tmp_path):
    """The live-verified bug: afade=t=out without st= faded the FIRST N seconds.

    Fixed output must stay loud through the body and only fall away at the end.
    """
    out = tmp_path / "faded.wav"
    res = tool.execute({
        "operation": "mix",
        "tracks": [{"path": str(sine5), "role": "music", "fade_out_seconds": 1}],
        "normalize": False,
        "output_path": str(out),
    })
    assert res.success, res.error

    # the emitted filter carries an explicit fade start at duration - N
    chain = tool._per_track_chain(0, {"path": str(sine5), "fade_out_seconds": 1})
    assert "afade=t=out:st=4.0:d=1" in chain

    first_2s = _mean_db(out, 0, 2)
    body = _mean_db(out, 1.5, 0.5)   # buggy code: already silent here (~-91 dB)
    tail_500ms = _mean_db(out, 4.5, 0.5)
    tail_200ms = _mean_db(out, 4.8, 0.2)

    assert first_2s > -30, f"first 2s should be loud, got {first_2s} dB"
    assert body > -30, f"1.5-2.0s should be loud (bug fades it to silence), got {body} dB"
    assert tail_500ms < body - 8, f"last 0.5s should be fading out, got {tail_500ms} vs body {body}"
    assert tail_200ms < body - 12, f"last 0.2s should be near-silent, got {tail_200ms} vs body {body}"


@needs_ffmpeg
def test_full_mix_fade_out_lands_at_track_end(tool, sine5, tmp_path):
    out = tmp_path / "faded_full.wav"
    res = tool.execute({
        "operation": "full_mix",
        "tracks": [{"path": str(sine5), "role": "speech", "fade_out_seconds": 1}],
        "normalize": False,
        "output_path": str(out),
    })
    assert res.success, res.error

    body = _mean_db(out, 1.5, 0.5)
    tail = _mean_db(out, 4.5, 0.5)
    assert body > -30, f"body should be loud (bug fades it to silence), got {body} dB"
    assert tail < body - 8, f"tail should fade out, got {tail} vs body {body}"


@needs_ffmpeg
def test_mix_fade_out_with_start_seconds(tool, tmp_path):
    """Fade timing is computed from the SOURCE length, then the delay shifts it."""
    sine3 = _make_sine(tmp_path / "sine3.wav", 3)
    out = tmp_path / "delayed_fade.wav"
    res = tool.execute({
        "operation": "mix",
        "tracks": [{"path": str(sine3), "start_seconds": 1, "fade_out_seconds": 1, "role": "music"}],
        "normalize": False,
        "output_path": str(out),
    })
    assert res.success, res.error
    assert 3.8 <= _duration(out) <= 4.3  # 1s pad + 3s audio

    pad = _mean_db(out, 0, 0.8)
    body = _mean_db(out, 1.2, 1.0)
    tail = _mean_db(out, 3.7, 0.3)
    assert pad < -60, f"0-0.8s is the delay pad, should be silent, got {pad} dB"
    assert body > -30, f"audio body should be loud, got {body} dB"
    assert tail < body - 8, f"fade should land at the shifted end, got {tail} vs body {body}"


# --- auto_balance ------------------------------------------------------------

@needs_ffmpeg
def test_auto_balance_dry_reports_gains_toward_targets(tool, sine5, quiet5):
    res = tool.execute({
        "operation": "auto_balance",
        "apply": False,
        "tracks": [
            {"path": str(sine5), "role": "voice"},
            {"path": str(quiet5), "role": "music"},
        ],
    })
    assert res.success, res.error
    assert res.data["applied"] is False
    assert res.artifacts == []

    voice, music = res.data["tracks"]
    assert voice["role"] == "voice" and music["role"] == "music"

    # the two sines were generated 15 dB apart — measurements must reflect that
    assert voice["measured_lufs"] - music["measured_lufs"] > 10

    # gains move each track exactly onto its role target (none capped here)
    assert res.data["targets_lufs"] == {"voice": -16.0, "music": -28.0, "sfx": -24.0}
    for entry in (voice, music):
        assert "gain_capped" not in entry
        assert abs(entry["measured_lufs"] + entry["gain_db"] - entry["target_lufs"]) < 0.05
        # linear volume matches the dB gain
        assert entry["volume"] == pytest.approx(10 ** (entry["gain_db"] / 20), rel=1e-3)


@needs_ffmpeg
def test_auto_balance_apply_writes_mix(tool, sine5, quiet5, tmp_path):
    out = tmp_path / "balanced.wav"
    res = tool.execute({
        "operation": "auto_balance",
        "tracks": [
            {"path": str(sine5), "role": "voice"},
            {"path": str(quiet5), "role": "music"},
        ],
        "output_path": str(out),
    })
    assert res.success, res.error
    assert res.data["applied"] is True
    assert res.data["output"] == str(out)
    assert str(out) in res.artifacts
    assert out.exists() and out.stat().st_size > 0
    # measurement report still present in apply mode
    assert len(res.data["tracks"]) == 2


# --- extract extension -------------------------------------------------------

@needs_ffmpeg
def test_extract_default_unchanged(tool, video_clip, tmp_path):
    """Back-compat: no codec given -> transcription-grade pcm_s16le 16k mono."""
    out = tmp_path / "legacy.wav"
    res = tool.execute({
        "operation": "extract", "input_path": str(video_clip), "output_path": str(out),
    })
    assert res.success, res.error
    stream = _probe_stream(out)
    assert stream["codec_name"] == "pcm_s16le"
    assert int(stream["sample_rate"]) == 16000
    assert int(stream["channels"]) == 1


@needs_ffmpeg
def test_extract_copy_preserves_codec(tool, video_clip):
    source_codec = _probe_stream(video_clip)["codec_name"]  # aac in an mp4
    res = tool.execute({"operation": "extract", "input_path": str(video_clip), "codec": "copy"})
    assert res.success, res.error
    assert res.data["stream_copied"] is True
    assert res.data["codec"] == source_codec
    assert res.data["output"].endswith(".m4a")
    assert _probe_stream(res.data["output"])["codec_name"] == source_codec


@needs_ffmpeg
def test_extract_sample_rate_honored(tool, video_clip, tmp_path):
    out = tmp_path / "hifi.wav"
    res = tool.execute({
        "operation": "extract", "input_path": str(video_clip),
        "codec": "wav", "sample_rate": 22050, "output_path": str(out),
    })
    assert res.success, res.error
    stream = _probe_stream(out)
    assert stream["codec_name"] == "pcm_s16le"
    assert int(stream["sample_rate"]) == 22050
    # full-fidelity mode: channels not forced to mono when unspecified
    assert res.data["sample_rate"] == 22050


@needs_ffmpeg
def test_extract_mp3(tool, video_clip, tmp_path):
    out = tmp_path / "detached.mp3"
    res = tool.execute({
        "operation": "extract", "input_path": str(video_clip),
        "codec": "mp3", "output_path": str(out),
    })
    assert res.success, res.error
    assert _probe_stream(out)["codec_name"] == "mp3"


@needs_ffmpeg
def test_full_mix_ducking_speech_music_sfx(tool, tmp_path):
    """Regression: the ducking branch (speech + music) used to consume the speech
    filter pad multiple times and leave an orphan [speech_dup], failing with
    'Filter output ... unconnected'. A single-speech + music + SFX full_mix must
    now succeed and emit a real audio stream (this is exactly the shape the render's
    _mix_structured_audio builds from edit_decisions.audio stems)."""
    speech = _make_sine(tmp_path / "vo.wav", 3)
    music = _make_sine(tmp_path / "music.wav", 3, gain_db=-10)
    fx = _make_sine(tmp_path / "fx.wav", 1)
    out = tmp_path / "mixed.m4a"
    res = tool.execute({
        "operation": "full_mix",
        "tracks": [
            {"path": str(speech), "role": "speech", "start_seconds": 0},
            {"path": str(music), "role": "music", "volume": 0.3, "fade_in_seconds": 0.5},
            {"path": str(fx), "role": "sfx", "start_seconds": 1.0, "volume": 0.5},
        ],
        "ducking": {"enabled": True, "attack_ms": 60, "release_ms": 350},
        "normalize": True,
        "output_path": str(out),
    })
    assert res.success, res.error
    assert out.exists() and out.stat().st_size > 0
    # _probe_stream selects the first AUDIO stream — a codec_name means the master
    # actually carries mixed audio (the bug produced no output at all).
    assert _probe_stream(out)["codec_name"]  # e.g. "aac" for .m4a
