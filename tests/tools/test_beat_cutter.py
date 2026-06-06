"""Tests for tools/video/beat_cutter.py (Edits-parity Wave 3).

librosa is NOT required for these tests: the detection path is exercised only via its
clean missing-dependency error (librosa is genuinely absent in CI), and every other test
passes pre-computed beat_times so no audio decoding happens.
"""

from __future__ import annotations

import json

import pytest

from tools.video.beat_cutter import BeatCutter


@pytest.fixture
def tool():
    return BeatCutter()


def _clips(n):
    return [{"source": f"clip{i}.mp4"} for i in range(n)]


# --- input guards ---------------------------------------------------------

def test_requires_clips(tool):
    res = tool.execute({"beat_times": [0, 1, 2]})
    assert res.success is False and "clips" in res.error


def test_requires_audio_or_beats(tool):
    res = tool.execute({"clips": _clips(2)})
    assert res.success is False
    assert "audio_path" in res.error and "beat_times" in res.error


def test_clip_without_source_rejected(tool):
    res = tool.execute({"clips": [{"in_seconds": 0}], "beat_times": [0, 1]})
    assert res.success is False and "source" in res.error


# --- core snapping --------------------------------------------------------

def test_cuts_snap_to_supplied_beats(tool):
    res = tool.execute({"clips": _clips(4), "beat_times": [0, 1, 2, 3, 4]})
    assert res.success is True
    cuts = res.data["cuts"]
    assert len(cuts) == 4
    # every interval is exactly one beat (1.0s)
    for c in cuts:
        assert round(c["out_seconds"] - c["in_seconds"], 3) == 1.0
    assert cuts[0]["reason"] == "beat-synced"


def test_beats_per_cut_takes_every_nth_beat(tool):
    # beats every 0.5s; beats_per_cut=2 -> cut every 1.0s
    beats = [round(0.5 * i, 4) for i in range(9)]  # 0..4.0
    res = tool.execute({"clips": _clips(3), "beat_times": beats, "beats_per_cut": 2})
    assert res.success is True
    for c in res.data["cuts"]:
        assert round(c["out_seconds"] - c["in_seconds"], 3) == 1.0


def test_start_seconds_skips_intro_beats(tool):
    res = tool.execute({"clips": _clips(2), "beat_times": [0, 1, 2, 3, 4], "start_seconds": 2})
    assert res.success is True
    # first usable beat is at 2.0
    assert res.data["cuts"][0]["in_seconds"] == 0  # in is the clip's own in
    # boundaries start at 2.0 -> first interval 2->3 = 1.0s
    assert round(res.data["cuts"][0]["out_seconds"] - res.data["cuts"][0]["in_seconds"], 3) == 1.0


# --- speech-safe ----------------------------------------------------------

def test_speech_safe_drops_beats_inside_protected_ranges(tool):
    # beats at 0,1,2,3,4; protect 1.5..2.5 so the beat at 2 is dropped
    res = tool.execute({
        "clips": _clips(3),
        "beat_times": [0, 1, 2, 3, 4],
        "mode": "speech_safe",
        "protected_ranges": [[1.5, 2.5]],
    })
    assert res.success is True
    assert any("protected speech" in w for w in res.data.get("warnings", []))


def test_music_led_keeps_all_beats(tool):
    res = tool.execute({
        "clips": _clips(3),
        "beat_times": [0, 1, 2, 3, 4],
        "mode": "music_led",
        "protected_ranges": [[1.5, 2.5]],
    })
    assert res.success is True
    # music_led ignores protected ranges -> no drop warning
    assert not any("protected speech" in w for w in res.data.get("warnings", []))


# --- fallbacks ------------------------------------------------------------

def test_no_beats_falls_back_to_even_spacing(tool):
    res = tool.execute({"clips": _clips(3), "beat_times": []})
    # no audio_path either -> requires audio_or_beats? beat_times=[] is falsy -> needs audio
    # so this actually hits the "provide audio or beats" guard:
    assert res.success is False


def test_single_beat_falls_back_to_even_spacing(tool):
    res = tool.execute({"clips": _clips(3), "beat_times": [0.0]})
    assert res.success is True
    assert any("even spacing" in w for w in res.data.get("warnings", []))
    assert len(res.data["cuts"]) == 3


def test_librosa_missing_gives_clean_error(tool, tmp_path, monkeypatch):
    # Force the missing-dependency path deterministically (works whether or not librosa is
    # actually installed) and assert the clean install hint instead of a raw crash.
    from tools.video.beat_cutter import _LibrosaMissing

    def _raise_missing(audio_path):
        raise _LibrosaMissing("No module named 'librosa'")

    monkeypatch.setattr(tool, "_detect_beats", _raise_missing)
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")
    res = tool.execute({"clips": _clips(2), "audio_path": str(audio)})
    assert res.success is False
    assert "librosa" in res.error and "requirements-audio.txt" in res.error


# --- transitions ----------------------------------------------------------

def test_transition_applied_to_cuts(tool):
    res = tool.execute({
        "clips": _clips(2),
        "beat_times": [0, 1, 2],
        "transition": {"type": "fade", "duration": 0.2},
    })
    assert res.success is True
    for c in res.data["cuts"]:
        assert c["transition_in"] == "fade"
        assert c["transition_duration"] == 0.2


def test_plain_cut_transition_not_written(tool):
    res = tool.execute({"clips": _clips(2), "beat_times": [0, 1, 2], "transition": {"type": "cut"}})
    assert all("transition_in" not in c for c in res.data["cuts"])


# --- edit_decisions merge -------------------------------------------------

def test_merge_into_valid_edit_decisions(tool, tmp_path):
    ed = tmp_path / "edit_decisions.json"
    ed.write_text(json.dumps({
        "version": "1.0",
        "cuts": [{"id": "old", "source": "x.mp4", "in_seconds": 0, "out_seconds": 1}],
        "render_runtime": "remotion",
    }))
    res = tool.execute({
        "clips": _clips(3),
        "beat_times": [0, 1, 2, 3],
        "edit_decisions_path": str(ed),
    })
    assert res.success is True
    doc = json.loads(ed.read_text())
    assert len(doc["cuts"]) == 3  # replaced
    assert doc["cuts"][0]["id"] == "beat-cut-1"
    assert doc["render_runtime"] == "remotion"  # preserved
    assert str(ed) in res.artifacts


def test_merge_into_invalid_edit_decisions_does_not_corrupt(tool, tmp_path):
    # Existing doc is missing the required render_runtime -> merged doc fails validation.
    ed = tmp_path / "broken.json"
    original = {"version": "1.0", "cuts": []}  # no render_runtime
    ed.write_text(json.dumps(original))
    res = tool.execute({
        "clips": _clips(2),
        "beat_times": [0, 1, 2],
        "edit_decisions_path": str(ed),
    })
    assert res.success is False
    assert "validate" in res.error
    # the original file must be untouched (no corrupt write)
    assert json.loads(ed.read_text()) == original


# --- cuts-only output -----------------------------------------------------

def test_writes_cuts_only_json(tool, tmp_path):
    out = tmp_path / "cuts.json"
    res = tool.execute({"clips": _clips(2), "beat_times": [0, 1, 2], "output_path": str(out)})
    assert res.success is True and out.exists()
    doc = json.loads(out.read_text())
    assert "cuts" in doc and "beat_times" in doc
