"""Tests for tools/audio/voice_ops.py (Edits-parity: voiceover + voice effects).

list_devices/record are validation-only (live mic capture cannot run headless): guard tests
assert input validation, device-listing parsers, and command construction via the pure
_record_cmd()/_pitch_chain() helpers. effect + insert run real ffmpeg on lavfi sine clips
and assert measurable outcomes — spectral shift for pitch effects (the fixture is a 300 Hz
sine, so a band-passed volumedetect proves the new pitch), decoded-PCM change for waveform
effects, and windowed loudness for insert placement/ducking. Duration alone is NOT proof:
a straight remux preserves duration, so every live test here must fail on a no-op.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.audio.voice_ops import VoiceOps, _OpInputError

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


def _mean_db(path, pre=None, start=None, dur=None):
    """volumedetect mean_volume, optionally windowed and behind a pre-filter.
    Pure digital silence reads ~-91 dB; the lavfi sine fixtures read ~-21 dB."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if dur is not None:
        cmd += ["-t", str(dur)]
    af = (f"{pre}," if pre else "") + "volumedetect"
    proc = subprocess.run(cmd + ["-i", str(path), "-af", af, "-f", "null", "-"],
                          capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", proc.stderr)
    assert m, f"no mean_volume in volumedetect output for {path}"
    return float(m.group(1))


def _band_db(path, freq):
    """Loudness inside a narrow band at `freq` (double bandpass ~24 dB/oct skirts).
    A 300 Hz sine reads < -50 dB through a 600 Hz band, so this cleanly separates
    'pitch really shifted' from 'file was copied through'."""
    return _mean_db(path, pre=f"bandpass=f={freq}:w=60,bandpass=f={freq}:w=60")


def _audio_md5(path):
    """md5 of the DECODED audio samples. A dropped -af chain on a wav->wav run
    re-encodes pcm_s16le bit-exactly (verified), so md5 equality == no-op."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "md5", "-"],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


@pytest.fixture
def tool():
    return VoiceOps()


@pytest.fixture
def voice_wav(tmp_path):
    """A 2s mono 48k sine 'voice take'."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "voice.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
         "-ac", "1", "-ar", "48000", str(p)],
        capture_output=True, check=True,
    )
    return p


@pytest.fixture
def video_clip(tmp_path):
    """A 4s synthetic video with audio (the 'timeline base')."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "base.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=4:r=25",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
         "-pix_fmt", "yuv420p", "-shortest", str(p)],
        capture_output=True, check=True,
    )
    return p


@pytest.fixture
def silent_clip(tmp_path):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=2:r=25",
         "-pix_fmt", "yuv420p", str(p)],
        capture_output=True, check=True,
    )
    return p


# --- validation / guards (no ffmpeg) ----------------------------------------

def test_invalid_operation_rejected(tool):
    res = tool.execute({"operation": "autotune"})
    assert res.success is False and "operation must be one of" in res.error


def test_record_requires_duration(tool):
    res = tool.execute({"operation": "record"})
    assert res.success is False and "duration_seconds" in res.error


def test_record_duration_capped_at_600(tool):
    res = tool.execute({"operation": "record", "duration_seconds": 601})
    assert res.success is False and "600" in res.error


def test_record_rejects_bad_sample_rate(tool):
    res = tool.execute({"operation": "record", "duration_seconds": 5, "sample_rate": 99})
    assert res.success is False and "sample_rate" in res.error


def test_effect_requires_exactly_one_of_preset_or_pitch(tool):
    both = tool.execute({"operation": "effect", "input_path": "x.wav",
                         "preset": "helium", "pitch_semitones": 3})
    neither = tool.execute({"operation": "effect", "input_path": "x.wav"})
    assert both.success is False and "exactly one" in both.error
    assert neither.success is False and "exactly one" in neither.error


def test_effect_rejects_unknown_preset(tool):
    res = tool.execute({"operation": "effect", "input_path": "x.wav", "preset": "chipmunk"})
    assert res.success is False and "preset must be one of" in res.error


def test_effect_rejects_pitch_out_of_range(tool):
    res = tool.execute({"operation": "effect", "input_path": "x.wav", "pitch_semitones": 13})
    assert res.success is False and "pitch_semitones" in res.error


def test_insert_requires_paths_and_at_seconds(tool):
    res = tool.execute({"operation": "insert", "base_path": "b.mp4"})
    assert res.success is False and "voice_path" in res.error
    res = tool.execute({"operation": "insert", "base_path": "b.mp4",
                        "voice_path": "v.wav", "at_seconds": -1})
    assert res.success is False and "at_seconds" in res.error


# --- record command construction (pure) -------------------------------------

def test_record_cmd_macos_default_device():
    cmd = VoiceOps._record_cmd("Darwin", None, 5, Path("out.wav"), 48000)
    assert cmd[:6] == ["ffmpeg", "-y", "-f", "avfoundation", "-i", ":0"]
    assert cmd[cmd.index("-t") + 1] == "5"
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-ar") + 1] == "48000"
    assert cmd[-1] == "out.wav"


def test_record_cmd_macos_device_normalization():
    # bare index and integer get the audio-only colon; full specs pass through
    assert VoiceOps._record_cmd("Darwin", "1", 5, "o.wav", 48000)[5] == ":1"
    assert VoiceOps._record_cmd("Darwin", 2, 5, "o.wav", 48000)[5] == ":2"
    assert VoiceOps._record_cmd("Darwin", ":3", 5, "o.wav", 48000)[5] == ":3"
    assert (
        VoiceOps._record_cmd("Darwin", "MacBook Pro Microphone", 5, "o.wav", 48000)[5]
        == ":MacBook Pro Microphone"
    )


def test_record_cmd_windows_requires_named_device():
    with pytest.raises(_OpInputError):
        VoiceOps._record_cmd("Windows", None, 5, "o.wav", 48000)
    cmd = VoiceOps._record_cmd("Windows", "Microphone (Realtek)", 5, "o.wav", 48000)
    assert cmd[2:6] == ["-f", "dshow", "-i", "audio=Microphone (Realtek)"]


def test_record_cmd_linux_pulse_default_and_alsa():
    cmd = VoiceOps._record_cmd("Linux", None, 5, "o.wav", 48000)
    assert cmd[2:6] == ["-f", "pulse", "-i", "default"]
    cmd = VoiceOps._record_cmd("Linux", "hw:1,0", 5, "o.wav", 48000)
    assert cmd[2:6] == ["-f", "alsa", "-i", "hw:1,0"]


# --- device-listing parsers (pure) -------------------------------------------

AVF_STDERR = """\
[AVFoundation indev @ 0x7f8e4a604700] AVFoundation video devices:
[AVFoundation indev @ 0x7f8e4a604700] [0] FaceTime HD Camera
[AVFoundation indev @ 0x7f8e4a604700] [1] Capture screen 0
[AVFoundation indev @ 0x7f8e4a604700] AVFoundation audio devices:
[AVFoundation indev @ 0x7f8e4a604700] [0] MacBook Pro Microphone
[AVFoundation indev @ 0x7f8e4a604700] [1] BlackHole 2ch
: Input/output error
"""


def test_parse_avfoundation_audio_section_only():
    devices = VoiceOps._parse_avfoundation_devices(AVF_STDERR)
    assert devices == [
        {"index": 0, "name": "MacBook Pro Microphone"},
        {"index": 1, "name": "BlackHole 2ch"},
    ]


DSHOW_STDERR_TAGGED = """\
[dshow @ 000001a] "Integrated Camera" (video)
[dshow @ 000001a]   Alternative name "@device_pnp_x"
[dshow @ 000001a] "Microphone (Realtek(R) Audio)" (audio)
[dshow @ 000001a]   Alternative name "@device_cm_x"
dummy: Immediate exit requested
"""

DSHOW_STDERR_SECTIONS = """\
[dshow @ x] DirectShow video devices (some may be both video and audio devices)
[dshow @ x]  "Integrated Camera"
[dshow @ x] DirectShow audio devices
[dshow @ x]  "Microphone Array"
"""


def test_parse_dshow_devices_both_formats():
    assert VoiceOps._parse_dshow_devices(DSHOW_STDERR_TAGGED) == [
        {"index": 0, "name": "Microphone (Realtek(R) Audio)"},
    ]
    assert VoiceOps._parse_dshow_devices(DSHOW_STDERR_SECTIONS) == [
        {"index": 0, "name": "Microphone Array"},
    ]


# --- pitch math (pure) --------------------------------------------------------

def test_pitch_chain_plus_12_doubles_asetrate():
    chain = VoiceOps._pitch_chain(12, 48000)
    assert chain.startswith("asetrate=96000,aresample=48000,")
    assert chain.endswith("atempo=0.5")


def test_pitch_chain_minus_12_halves_asetrate():
    chain = VoiceOps._pitch_chain(-12, 48000)
    assert chain.startswith("asetrate=24000,aresample=48000,")
    assert chain.endswith("atempo=2.0")


def test_pitch_chain_zero_is_identity_rate():
    assert VoiceOps._pitch_chain(0, 48000).startswith("asetrate=48000,")


def test_atempo_chain_stays_in_legal_range():
    assert VoiceOps._atempo_chain(2.0) == "atempo=2.0"
    assert VoiceOps._atempo_chain(0.5) == "atempo=0.5"
    # helium compensation 1/1.35 is a single in-range instance
    assert VoiceOps._atempo_chain(1 / 1.35) == f"atempo={round(1 / 1.35, 6)}"


def test_preset_chains_build_for_all_presets():
    for preset in VoiceOps.PRESETS:
        chain = VoiceOps._preset_chain(preset, 48000)
        assert isinstance(chain, str) and chain


# --- effect (live ffmpeg) ------------------------------------------------------

def _dur(tool, path):
    return tool._probe_duration(Path(path))


@needs_ffmpeg
def test_effect_helium_shifts_pitch_and_preserves_duration(tool, voice_wav, tmp_path):
    out = tmp_path / "helium.wav"
    res = tool.execute({"operation": "effect", "input_path": str(voice_wav),
                        "preset": "helium", "output_path": str(out)})
    assert res.success, res.error
    assert abs(res.data["duration_seconds"] - 2.0) <= 0.1  # +-5%
    # helium = 1.35x rate: 300 Hz fixture -> ~405 Hz. The 405 band is silent on
    # the input and loud on the output (calibrated: -74.5 -> -21.4 dB).
    assert _band_db(voice_wav, 405) < -50, "fixture unexpectedly has 405 Hz energy"
    assert _band_db(out, 405) > -32, "helium output has no energy at the shifted pitch"


@needs_ffmpeg
def test_effect_pitch_plus_12_doubles_frequency_and_preserves_duration(tool, voice_wav, tmp_path):
    out = tmp_path / "p12.wav"
    res = tool.execute({"operation": "effect", "input_path": str(voice_wav),
                        "pitch_semitones": 12, "output_path": str(out)})
    assert res.success, res.error
    assert abs(res.data["duration_seconds"] - 2.0) <= 0.1
    # +12 semitones doubles 300 Hz -> 600 Hz (calibrated: -84.3 -> -21.3 dB in band)
    assert _band_db(voice_wav, 600) < -50, "fixture unexpectedly has 600 Hz energy"
    assert _band_db(out, 600) > -32, "pitch +12 output has no energy at 600 Hz"


@needs_ffmpeg
@pytest.mark.parametrize("preset", ["robot", "echo"])
def test_effect_robot_and_echo_change_the_waveform(tool, voice_wav, tmp_path, preset):
    out = tmp_path / f"{preset}.wav"
    res = tool.execute({"operation": "effect", "input_path": str(voice_wav),
                        "preset": preset, "output_path": str(out)})
    assert res.success, res.error
    assert out.exists() and out.stat().st_size > 0
    # a wav->wav run with the -af chain dropped is PCM-bit-exact to the input
    assert _audio_md5(out) != _audio_md5(voice_wav), f"{preset} output is a straight copy"


@needs_ffmpeg
def test_effect_on_video_keeps_video_stream(tool, video_clip, tmp_path):
    out = tmp_path / "tele.mp4"
    res = tool.execute({"operation": "effect", "input_path": str(video_clip),
                        "preset": "telephone", "output_path": str(out)})
    assert res.success, res.error
    assert tool._has_video(out)  # -c:v copy kept the video stream
    assert abs(res.data["duration_seconds"] - 4.0) <= 0.3


@needs_ffmpeg
def test_effect_needs_audio(tool, silent_clip):
    res = tool.execute({"operation": "effect", "input_path": str(silent_clip), "preset": "echo"})
    assert res.success is False and "audio" in res.error


# --- insert (live ffmpeg) -------------------------------------------------------

@needs_ffmpeg
def test_insert_keeps_base_duration_with_duck(tool, video_clip, voice_wav, tmp_path):
    """Voice (300 Hz) onto a 440 Hz base at t=1 for 2s: the voice band must be loud
    only inside [1,3], and the 440 Hz bed must dip there (duck). Band-passed
    volumedetect separates the two sines, so a base-file copy fails every assert."""
    out = tmp_path / "voiced.mp4"
    res = tool.execute({
        "operation": "insert", "base_path": str(video_clip),
        "voice_path": str(voice_wav), "at_seconds": 1.0, "output_path": str(out),
    })
    assert res.success, res.error
    assert abs(res.data["duration_seconds"] - 4.0) <= 0.2  # output == base duration
    assert tool._has_video(out) and tool._has_audio(out)
    assert res.data["duck_music"] is True
    # the voice is audible exactly in its window (calibrated: -72.5 / -21.6 dB)
    voice_in = _mean_db(out, pre="bandpass=f=300:w=60,bandpass=f=300:w=60", start=1.1, dur=1.6)
    voice_out = _mean_db(out, pre="bandpass=f=300:w=60,bandpass=f=300:w=60", start=0.0, dur=0.9)
    assert voice_in > -32, f"voice not audible in its window: {voice_in} dB"
    assert voice_out < -50, f"voice leaked outside its window: {voice_out} dB"
    # the bed ducks while the voice plays (DUCK_LEVEL=0.3 ~= -10.5 dB; calibrated -21.8 -> -31.9)
    bed_in = _mean_db(out, pre="bandpass=f=440:w=60,bandpass=f=440:w=60", start=1.1, dur=1.6)
    bed_out = _mean_db(out, pre="bandpass=f=440:w=60,bandpass=f=440:w=60", start=0.0, dur=0.9)
    assert bed_in < bed_out - 6, f"bed did not duck: {bed_out} -> {bed_in} dB"


@needs_ffmpeg
def test_insert_duck_level_drops_bed_about_10db(tool, tmp_path):
    """Pin DUCK_LEVEL=0.3 (~-10.5 dB) numerically: a SILENT voice track isolates the
    bed, so the windowed level change is exactly the duck (calibrated -21.1 -> -31.5),
    and the bed must recover after the voice window ends."""
    base = tmp_path / "bed.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
         "-ac", "1", "-ar", "48000", str(base)],
        capture_output=True, check=True,
    )
    silent_voice = tmp_path / "silent_voice.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         "anullsrc=channel_layout=mono:sample_rate=48000", "-t", "1", str(silent_voice)],
        capture_output=True, check=True,
    )
    out = tmp_path / "ducked.wav"
    res = tool.execute({
        "operation": "insert", "base_path": str(base), "voice_path": str(silent_voice),
        "at_seconds": 1.0, "output_path": str(out),
    })
    assert res.success, res.error
    pre = _mean_db(out, start=0.0, dur=0.9)
    inside = _mean_db(out, start=1.05, dur=0.9)
    post = _mean_db(out, start=2.1, dur=0.9)
    assert 8 < pre - inside < 13, f"duck depth off: {pre} -> {inside} dB (expected ~10.5)"
    assert abs(post - pre) < 1.5, f"bed did not recover after the voice window: {post} dB"


@needs_ffmpeg
def test_insert_audio_base_no_duck(tool, voice_wav, tmp_path):
    base = tmp_path / "music.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=3",
         "-ar", "48000", str(base)],
        capture_output=True, check=True,
    )
    out = tmp_path / "mixed.wav"
    res = tool.execute({
        "operation": "insert", "base_path": str(base), "voice_path": str(voice_wav),
        "at_seconds": 0.5, "duck_music": False, "output_path": str(out),
    })
    assert res.success, res.error
    assert abs(res.data["duration_seconds"] - 3.0) <= 0.15


@needs_ffmpeg
def test_insert_silent_video_base_gets_voice_audio(tool, silent_clip, voice_wav, tmp_path):
    """The anullsrc silence bed alone would satisfy a bare has_audio check — assert
    the voice is actually AUDIBLE in its window and absent before it (the insert is
    at t=0.5, so [0, 0.4] must still be the silent bed)."""
    out = tmp_path / "narrated.mp4"
    res = tool.execute({
        "operation": "insert", "base_path": str(silent_clip),
        "voice_path": str(voice_wav), "at_seconds": 0.5, "output_path": str(out),
    })
    assert res.success, res.error
    assert tool._has_audio(out)  # silence bed + voice mixed in
    assert abs(res.data["duration_seconds"] - 2.0) <= 0.2
    before = _mean_db(out, start=0.0, dur=0.4)
    inside = _mean_db(out, start=0.6, dur=1.2)
    assert inside > -35, f"voice not audible on the silent base: {inside} dB"
    assert before < -70, f"audio before the insert point should be silence: {before} dB"


@needs_ffmpeg
def test_insert_past_base_end_rejected(tool, video_clip, voice_wav):
    res = tool.execute({"operation": "insert", "base_path": str(video_clip),
                        "voice_path": str(voice_wav), "at_seconds": 99})
    assert res.success is False and "past the base end" in res.error


# --- asset_manifest provenance ---------------------------------------------------

@needs_ffmpeg
def test_effect_registers_audio_asset_with_provenance(tool, voice_wav, tmp_path):
    manifest = tmp_path / "asset_manifest.json"
    manifest.write_text(json.dumps({"version": "1.0", "assets": []}))
    out = tmp_path / "echo.wav"
    res = tool.execute({
        "operation": "effect", "input_path": str(voice_wav), "preset": "echo",
        "output_path": str(out), "asset_manifest_path": str(manifest), "scene_id": "vo-1",
    })
    assert res.success, res.error
    doc = json.loads(manifest.read_text())
    assert len(doc["assets"]) == 1
    a = doc["assets"][0]
    assert a["source_tool"] == "voice_ops"
    assert a["subtype"] == "effect"
    assert a["type"] == "audio"
    assert a["scene_id"] == "vo-1"
    assert a["duration_seconds"] is not None
    assert str(manifest) in res.artifacts
