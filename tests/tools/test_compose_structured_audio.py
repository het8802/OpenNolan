"""Structured-audio stem mixing in the render path (video_compose).

When edit_decisions.audio carries structured stems (music bed + narration
segments + sfx) instead of a pre-mixed `path`, the render mixes them into one
master via audio_mixer.full_mix — so a timeline edited in the manual editor
(which has no agent to run full_mix by hand) still gets music/SFX in the output.

The mapping (edit_decisions.audio -> full_mix tracks) is pure and covered without
ffmpeg; the actual mix is ffmpeg-gated.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from tools.video.video_compose import VideoCompose

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


# --- pure mapping (no ffmpeg) ------------------------------------------------

def test_has_structured_audio():
    assert VideoCompose._has_structured_audio({"music": {"asset_id": "bed"}}) is True
    assert VideoCompose._has_structured_audio(
        {"narration": {"segments": [{"asset_id": "vo", "start_seconds": 0}]}}) is True
    assert VideoCompose._has_structured_audio({"sfx": [{"asset_id": "fx", "start_seconds": 1}]}) is True
    # a pre-mixed master or an empty/absent audio block is NOT structured
    assert VideoCompose._has_structured_audio({"path": "master.m4a"}) is False
    assert VideoCompose._has_structured_audio({"music": {}}) is False
    assert VideoCompose._has_structured_audio({}) is False
    assert VideoCompose._has_structured_audio(None) is False


def test_structured_audio_tracks_maps_stems_in_order():
    present = {"bed", "vo1", "vo2", "fx1", "fx2"}
    resolve = lambda a: a if a in present else None
    audio = {
        "music": {"asset_id": "bed", "volume": 0.1, "fade_in_seconds": 0.75,
                  "fade_out_seconds": 1.5,
                  "ducking": {"enabled": True, "attack_ms": 60, "release_ms": 350}},
        "narration": {"segments": [
            {"asset_id": "vo1", "start_seconds": 0},
            {"asset_id": "vo2", "start_seconds": 4.7}]},
        "sfx": [
            {"asset_id": "fx1", "start_seconds": 0.12, "volume": 0.24},
            {"asset_id": "missing", "start_seconds": 2},   # unresolvable -> skipped
            {"asset_id": "fx2", "start_seconds": 3.7}],
    }
    spec = VideoCompose._structured_audio_tracks(audio, resolve)
    assert [t["role"] for t in spec["tracks"]] == ["speech", "speech", "music", "sfx", "sfx"]
    assert spec["tracks"][0]["start_seconds"] == 0 and spec["tracks"][1]["start_seconds"] == 4.7
    assert spec["tracks"][2]["volume"] == 0.1 and spec["tracks"][2]["fade_in_seconds"] == 0.75
    assert spec["tracks"][3]["start_seconds"] == 0.12 and spec["tracks"][3]["volume"] == 0.24
    assert spec["ducking"] == {"enabled": True, "attack_ms": 60, "release_ms": 350}


def test_structured_audio_tracks_ducking_bool_and_none():
    resolve = lambda a: a
    # boolean ducking
    spec = VideoCompose._structured_audio_tracks(
        {"music": {"asset_id": "bed", "ducking": True},
         "narration": {"segments": [{"asset_id": "vo", "start_seconds": 0}]}}, resolve)
    assert spec["ducking"] == {"enabled": True}
    # no music -> nothing to duck (enabled False)
    spec2 = VideoCompose._structured_audio_tracks(
        {"sfx": [{"asset_id": "fx", "start_seconds": 0}]}, resolve)
    assert spec2["ducking"] == {"enabled": False}
    # nothing resolves -> None
    assert VideoCompose._structured_audio_tracks({"music": {"asset_id": "x"}},
                                                 lambda a: None) is None


# --- real mix (ffmpeg) -------------------------------------------------------

def _sine(path, seconds, gain_db=0):
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
           "-i", f"sine=frequency=440:duration={seconds}"]
    if gain_db:
        cmd += ["-af", f"volume={gain_db}dB"]
    cmd.append(str(path))
    subprocess.run(cmd, capture_output=True, check=True)
    return path


@needs_ffmpeg
def test_mix_structured_audio_produces_master(tmp_path):
    """_mix_structured_audio resolves literal paths (editor path) and returns a
    master with a real audio stream."""
    bed = _sine(tmp_path / "bed.wav", 3, gain_db=-10)
    vo = _sine(tmp_path / "vo.wav", 3)
    fx = _sine(tmp_path / "fx.wav", 1)
    audio = {
        "music": {"asset_id": str(bed), "volume": 0.2, "fade_in_seconds": 0.5,
                  "ducking": {"enabled": True}},
        "narration": {"segments": [{"asset_id": str(vo), "start_seconds": 0}]},
        "sfx": [{"asset_id": str(fx), "start_seconds": 1.0, "volume": 0.4}],
    }
    out = VideoCompose()._mix_structured_audio(audio, {}, tmp_path)
    assert out is not None
    from pathlib import Path
    assert Path(out).exists() and Path(out).stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", out], capture_output=True, text=True)
    assert "audio" in probe.stdout


@needs_ffmpeg
def test_mix_structured_audio_none_when_unresolvable(tmp_path):
    audio = {"music": {"asset_id": "does/not/exist.mp3"}}
    assert VideoCompose()._mix_structured_audio(audio, {}, tmp_path) is None


def _silent_clip(path, seconds=2):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=s=320x568:d={seconds}:r=24", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True,
    )
    return path


def _voiced_clip(path, seconds=4):
    """A clip WITH a 440Hz tone as its audio (stands in for a footage VO)."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc=s=320x568:d={seconds}:r=24",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-pix_fmt", "yuv420p", "-shortest", str(path)],
        capture_output=True, check=True,
    )
    return path


def _mean_db(path, start, dur):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-t", str(dur),
         "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True)
    import re
    m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", proc.stderr)
    return float(m.group(1)) if m else -91.0


@needs_ffmpeg
def test_render_mixes_stems_into_output(tmp_path):
    """End-to-end: a render whose base clips are SILENT + structured stems must emit
    an output with audio — that audio can ONLY come from the stem mix. Guards the
    whole editor render path (render_proxies -> assemble -> _render_via_ffmpeg bridge
    -> _mix_structured_audio), which previously dropped structured music/SFX."""
    c1 = _silent_clip(tmp_path / "c1.mp4")
    c2 = _silent_clip(tmp_path / "c2.mp4")
    bed = _sine(tmp_path / "bed.wav", 4, gain_db=-8)
    fx = _sine(tmp_path / "fx.wav", 1)
    ed = {
        "render_runtime": "ffmpeg", "renderer_family": "social-reel",
        "cuts": [
            {"id": "a", "source": str(c1), "in_seconds": 0, "out_seconds": 2},
            {"id": "b", "source": str(c2), "in_seconds": 0, "out_seconds": 2},
        ],
        "audio": {
            "music": {"asset_id": str(bed), "volume": 0.5, "ducking": False},
            "sfx": [{"asset_id": str(fx), "start_seconds": 1.5, "volume": 0.8}],
        },
    }
    out = tmp_path / "final.mp4"
    res = VideoCompose().execute({
        "operation": "render_proxies", "edit_decisions": ed,
        "asset_manifest": {"assets": []}, "output_path": str(out),
        "proxies_dir": str(tmp_path / "proxies"),
    })
    assert res.success, res.error
    assert out.exists()
    astreams = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_name", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True).stdout.strip()
    assert astreams, "output has no audio — the stem mix did not reach the render"


@needs_ffmpeg
def test_render_layers_music_over_base_vo_when_no_narration(tmp_path):
    """No narration stem → the voice lives in the base clips (footage). The render must
    LAYER music over the base-clip audio, not replace it: the output stays audibly loud
    where the base tone plays (proves the base VO survived the mix)."""
    clip = _voiced_clip(tmp_path / "vo_clip.mp4", 4)     # base carries a 440Hz "VO"
    bed = _sine(tmp_path / "bed.wav", 4, gain_db=-14)
    ed = {
        "render_runtime": "ffmpeg", "renderer_family": "social-reel",
        "cuts": [{"id": "a", "source": str(clip), "in_seconds": 0, "out_seconds": 4}],
        "audio": {"music": {"asset_id": str(bed), "volume": 0.3, "ducking": True}},  # NO narration
    }
    out = tmp_path / "final.mp4"
    res = VideoCompose().execute({
        "operation": "render_proxies", "edit_decisions": ed,
        "asset_manifest": {"assets": []}, "output_path": str(out),
        "proxies_dir": str(tmp_path / "proxies"),
    })
    assert res.success, res.error
    # The base tone must still be clearly audible in the body (layered, not dropped).
    assert _mean_db(out, 1.0, 2.0) > -40.0


@needs_ffmpeg
def test_render_replaces_base_when_narration_present(tmp_path):
    """A narration stem IS the voice → it replaces the base-clip audio. Output has audio
    from the narration even though the base clip is silent."""
    clip = _silent_clip(tmp_path / "c.mp4", 3)
    vo = _sine(tmp_path / "vo.wav", 3)
    ed = {
        "render_runtime": "ffmpeg", "renderer_family": "social-reel",
        "cuts": [{"id": "a", "source": str(clip), "in_seconds": 0, "out_seconds": 3}],
        "audio": {"narration": {"segments": [{"asset_id": str(vo), "start_seconds": 0}]}},
    }
    out = tmp_path / "final.mp4"
    res = VideoCompose().execute({
        "operation": "render_proxies", "edit_decisions": ed,
        "asset_manifest": {"assets": []}, "output_path": str(out),
        "proxies_dir": str(tmp_path / "proxies"),
    })
    assert res.success, res.error
    assert _mean_db(out, 0.5, 1.5) > -40.0  # narration audible
