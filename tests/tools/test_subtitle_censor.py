"""Tests for subtitle_gen censor_words (Edits-parity transcript censoring).

subtitle_gen is pure Python (no ffmpeg), so everything here runs pure. The
synthetic word-timestamped transcripts mirror the transcriber segment shape
already used by tests/contracts/test_phase1_contracts.py.
"""

from __future__ import annotations

import json

import pytest

from tools.subtitle.subtitle_gen import SubtitleGen


@pytest.fixture
def tool():
    return SubtitleGen()


def _segments():
    """Synthetic transcriber output: two segments with word timestamps."""
    return [
        {
            "text": "Well damn that hurt",
            "start": 0.0,
            "end": 2.0,
            "words": [
                {"word": "Well", "start": 0.0, "end": 0.4},
                {"word": "damn", "start": 0.5, "end": 0.9},
                {"word": "that", "start": 1.0, "end": 1.3},
                {"word": "hurt", "start": 1.4, "end": 2.0},
            ],
        },
        {
            "text": "What the hell, man",
            "start": 3.0,
            "end": 5.0,
            "words": [
                {"word": "What", "start": 3.0, "end": 3.3},
                {"word": "the", "start": 3.35, "end": 3.5},
                {"word": "hell,", "start": 3.55, "end": 3.9},
                {"word": "man", "start": 4.0, "end": 5.0},
            ],
        },
    ]


def _run(tool, tmp_path, fmt="srt", **kwargs):
    result = tool.execute({
        "segments": kwargs.pop("segments", _segments()),
        "format": fmt,
        "output_path": str(tmp_path / f"out.{fmt}"),
        **kwargs,
    })
    assert result.success, result.error
    return result


# --- validation / guards ----------------------------------------------------

def test_censor_words_must_be_list_of_strings(tool, tmp_path):
    result = tool.execute({
        "segments": _segments(),
        "censor_words": "damn",
        "output_path": str(tmp_path / "out.srt"),
    })
    assert not result.success
    assert "list of strings" in result.error


def test_censor_words_rejects_phrases(tool, tmp_path):
    result = tool.execute({
        "segments": _segments(),
        "censor_words": ["oh damn"],
        "output_path": str(tmp_path / "out.srt"),
    })
    assert not result.success
    assert "single words" in result.error


def test_censor_words_rejects_empty_entries(tool, tmp_path):
    result = tool.execute({
        "segments": _segments(),
        "censor_words": ["damn", "  "],
        "output_path": str(tmp_path / "out.srt"),
    })
    assert not result.success
    assert "non-empty" in result.error


# --- masking ------------------------------------------------------------------

def test_masked_text_in_srt(tool, tmp_path):
    result = _run(tool, tmp_path, fmt="srt", censor_words=["damn", "hell"])
    content = (tmp_path / "out.srt").read_text()
    assert "d***" in content
    assert "h***," in content  # trailing punctuation preserved
    assert "damn" not in content.lower()
    assert "hell" not in content.lower()
    assert result.data["censor_summary"]["masked_occurrences"] == 2


def test_masked_text_in_vtt(tool, tmp_path):
    _run(tool, tmp_path, fmt="vtt", censor_words=["damn"])
    content = (tmp_path / "out.vtt").read_text()
    assert content.startswith("WEBVTT")
    assert "d***" in content
    assert "damn" not in content.lower()


def test_masked_words_in_caption_json(tool, tmp_path):
    _run(tool, tmp_path, fmt="json", censor_words=["damn"])
    data = json.loads((tmp_path / "out.json").read_text())
    words = [w["word"] for cue in data["cues"] for w in cue["words"]]
    assert "d***" in words
    assert "damn" not in [w.lower() for w in words]


def test_case_insensitive_match_and_case_preserving_mask(tool, tmp_path):
    segments = [{
        "text": "Damn right",
        "start": 0.0,
        "end": 1.0,
        "words": [
            {"word": "Damn,", "start": 0.0, "end": 0.4},
            {"word": "right", "start": 0.5, "end": 1.0},
        ],
    }]
    result = _run(tool, tmp_path, segments=segments, censor_words=["DAMN"])
    content = (tmp_path / "out.srt").read_text()
    assert "D***," in content
    assert "damn" not in content.lower()
    # pad clamps at 0 for a word starting at t=0
    assert result.data["mute_ranges"][0]["start"] == 0.0
    assert result.data["mute_ranges"][0]["end"] == pytest.approx(0.45, abs=1e-6)


# --- mute ranges ----------------------------------------------------------------

def test_mute_ranges_match_word_timestamps(tool, tmp_path):
    result = _run(tool, tmp_path, censor_words=["damn"])
    ranges = result.data["mute_ranges"]
    assert len(ranges) == 1
    assert ranges[0]["word"] == "damn"
    # word at 0.5-0.9, padded by ±0.05
    assert ranges[0]["start"] == pytest.approx(0.45, abs=1e-6)
    assert ranges[0]["end"] == pytest.approx(0.95, abs=1e-6)


def test_non_overlapping_ranges_stay_separate(tool, tmp_path):
    result = _run(tool, tmp_path, censor_words=["damn", "hell"])
    ranges = result.data["mute_ranges"]
    assert len(ranges) == 2
    assert ranges[0]["word"] == "damn"
    assert ranges[1]["word"] == "hell"
    summary = result.data["censor_summary"]
    assert summary["mute_range_count"] == 2
    assert summary["merged_overlap_count"] == 0


def test_overlapping_ranges_merged(tool, tmp_path):
    # "the" 3.35-3.5 -> padded 3.30-3.55; "hell," 3.55-3.9 -> padded 3.50-3.95: overlap
    result = _run(tool, tmp_path, censor_words=["the", "hell"])
    ranges = result.data["mute_ranges"]
    assert len(ranges) == 1
    assert ranges[0]["start"] == pytest.approx(3.30, abs=1e-6)
    assert ranges[0]["end"] == pytest.approx(3.95, abs=1e-6)
    assert ranges[0]["word"] == "the hell"
    summary = result.data["censor_summary"]
    assert summary["masked_occurrences"] == 2
    assert summary["mute_range_count"] == 1
    assert summary["merged_overlap_count"] == 1


def test_mute_ranges_sorted_by_start(tool, tmp_path):
    result = _run(tool, tmp_path, censor_words=["hell", "damn", "well"])
    starts = [r["start"] for r in result.data["mute_ranges"]]
    assert starts == sorted(starts)


# --- summary / fallback / back-compat -----------------------------------------

def test_no_matches_yields_empty_ranges_and_zero_summary(tool, tmp_path):
    result = _run(tool, tmp_path, censor_words=["frick"])
    assert result.data["mute_ranges"] == []
    summary = result.data["censor_summary"]
    assert summary["masked_occurrences"] == 0
    assert summary["mute_range_count"] == 0
    content = (tmp_path / "out.srt").read_text()
    assert "damn" in content  # untouched


def test_text_only_segment_masked_but_no_mute_range(tool, tmp_path):
    segments = [{"text": "oh damn here", "start": 0.0, "end": 2.0}]
    result = _run(tool, tmp_path, segments=segments, censor_words=["damn"])
    content = (tmp_path / "out.srt").read_text()
    assert "d***" in content
    assert "damn" not in content.lower()
    assert result.data["mute_ranges"] == []
    assert result.data["censor_summary"]["unmuted_text_matches"] == 1


def test_corrections_apply_before_censor(tool, tmp_path):
    segments = [{
        "text": "oh dang it",
        "start": 0.0,
        "end": 1.5,
        "words": [
            {"word": "oh", "start": 0.0, "end": 0.3},
            {"word": "dang", "start": 0.4, "end": 0.8},
            {"word": "it", "start": 0.9, "end": 1.5},
        ],
    }]
    result = _run(
        tool, tmp_path,
        segments=segments,
        corrections={"dang": "damn"},
        censor_words=["damn"],
    )
    content = (tmp_path / "out.srt").read_text()
    assert "d***" in content
    assert "dang" not in content
    assert result.data["mute_ranges"][0]["start"] == pytest.approx(0.35, abs=1e-6)


def test_back_compat_without_censor_words(tool, tmp_path):
    result = _run(tool, tmp_path)
    assert "mute_ranges" not in result.data
    assert "censor_summary" not in result.data
    content = (tmp_path / "out.srt").read_text()
    assert "damn" in content


def test_input_segments_do_not_mutate(tool, tmp_path):
    segments = _segments()
    _run(tool, tmp_path, segments=segments, censor_words=["damn"])
    assert segments[0]["words"][1]["word"] == "damn"
