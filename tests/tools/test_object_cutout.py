"""Tests for tools/enhancement/object_cutout.py (Edits-parity Wave 1).

Network calls to Replicate are MOCKED (monkeypatched _upload/_create_prediction/
_poll_prediction/_download) — CI never hits the API. The mask->alpha composite runs
REAL FFmpeg where available (guarded by an ffmpeg skipif) because the scale2ref
filtergraph is the riskiest line and worth exercising for real.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.enhancement.object_cutout import ObjectCutout, _PredictionError

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not on PATH")

VALID_TOKEN = "r8_" + "x" * 37


@pytest.fixture
def tool(tmp_path, monkeypatch):
    # Isolate the cache so tests never read/write the real ~/.cache.
    monkeypatch.setenv("OPENMONTAGE_CACHE_DIR", str(tmp_path / "cache"))
    return ObjectCutout()


@pytest.fixture
def source_video(tmp_path):
    """A tiny synthetic source clip."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not on PATH")
    p = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:d=1:r=10",
         "-pix_fmt", "yuv420p", str(p)],
        capture_output=True, check=True,
    )
    return p


def _write_synthetic_mask(dest: Path) -> None:
    """A binary mask: white box (subject) on black (bg), smaller than the source on purpose."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=240x180:d=1:r=10",
         "-vf", "drawbox=x=60:y=45:w=120:h=90:color=white:t=fill",
         "-pix_fmt", "yuv420p", str(dest)],
        capture_output=True, check=True,
    )


# --- prompt handling ------------------------------------------------------

def test_validate_points_requires_at_least_one(tool):
    assert tool._validate_points([]) is not None
    assert tool._validate_points(None) is not None
    assert "no auto mode" in tool._validate_points([])


def test_validate_points_requires_a_positive_click(tool):
    only_negative = [{"x": 1, "y": 1, "label": 0}]
    msg = tool._validate_points(only_negative)
    assert msg is not None and "positive" in msg


def test_validate_points_accepts_good_prompt(tool):
    assert tool._validate_points([{"x": 10, "y": 10, "label": 1}]) is None


def test_serialize_points_formats_sam2_strings(tool):
    pts = [
        {"x": 320.4, "y": 240.6, "label": 1, "frame": 0, "object_id": "subject"},
        {"x": 10, "y": 10, "label": 0, "frame": 5, "object_id": "subject"},
    ]
    out = tool._serialize_points(pts)
    assert out["click_coordinates"] == "[320,241],[10,10]"  # rounds to int px
    assert out["click_labels"] == "1,0"
    assert out["click_frames"] == "0,5"
    assert out["click_object_ids"] == "subject,subject"


# --- defensive output parsing --------------------------------------------

def test_extract_mask_url_handles_shapes(tool):
    assert tool._extract_mask_url("http://x/m.mp4") == "http://x/m.mp4"
    assert tool._extract_mask_url(["http://x/a.mp4", "http://x/b.mp4"]) == "http://x/a.mp4"
    assert tool._extract_mask_url({"combined_mask": "http://x/c.mp4"}) == "http://x/c.mp4"
    assert tool._extract_mask_url({"masks": ["http://x/d.mp4"]}) == "http://x/d.mp4"


def test_extract_mask_url_raises_on_garbage(tool):
    with pytest.raises(_PredictionError):
        tool._extract_mask_url({"nope": 123})


# --- token guard / no silent fallback ------------------------------------

def test_missing_token_names_fallback_not_silent(tool, monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    res = tool.execute({"video_path": "whatever.mp4", "points": [{"x": 1, "y": 1}]})
    assert res.success is False
    # NOT silent: it surfaces bg_remove as an explicit, opt-in alternative.
    assert res.data.get("fallback_available") == "bg_remove"
    assert "person-only" in res.data.get("fallback_note", "")


def test_dotenv_inline_comment_token_is_rejected(tool, monkeypatch):
    # The .env footgun: a leaked "# comment" in the token value.
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_abc # my token")
    res = tool.execute({"video_path": "x.mp4", "points": [{"x": 1, "y": 1}]})
    assert res.success is False
    assert "footgun" in res.error or "whitespace" in res.error


# --- confirmation gate (no spend without consent) -------------------------

@needs_ffmpeg
def test_fresh_run_requires_confirmation(tool, source_video, monkeypatch):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)

    def _boom(*a, **k):
        raise AssertionError("network must NOT be called without confirmation")

    monkeypatch.setattr(tool, "_upload", _boom)
    monkeypatch.setattr(tool, "_create_prediction", _boom)

    res = tool.execute({"video_path": str(source_video), "points": [{"x": 100, "y": 100}]})
    assert res.success is False
    assert res.data.get("requires_confirmation") is True
    assert res.data.get("estimated_cost_usd") is not None


# --- happy path (network mocked, real composite) --------------------------

@needs_ffmpeg
def test_happy_path_produces_rgba_cutout(tool, source_video, tmp_path, monkeypatch):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    monkeypatch.setattr(tool, "_upload", lambda path, token: "http://fake/upload")

    def fake_create(file_url, points, mask_type, token):
        return {"id": "pred-1", "status": "succeeded", "output": "http://fake/mask.mp4"}

    monkeypatch.setattr(tool, "_create_prediction", fake_create)
    # _download writes a synthetic binary mask so the REAL composite runs.
    monkeypatch.setattr(tool, "_download", lambda url, dest: _write_synthetic_mask(dest))

    out = tmp_path / "cut.mov"
    res = tool.execute({
        "video_path": str(source_video),
        "points": [{"x": 160, "y": 120, "label": 1}],
        "output_path": str(out),
        "confirm": True,
    })
    assert res.success is True, res.error
    assert out.exists() and out.stat().st_size > 0
    assert res.data["cache_hit"] is False
    assert res.data["object_ids"] == ["subject"]
    # the composited cutout must actually carry an alpha channel
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt", "-of", "default=nw=1:nk=1", str(out)],
        capture_output=True, text=True,
    )
    assert probe.stdout.strip() in ("argb", "rgba", "yuva420p"), probe.stdout


@needs_ffmpeg
def test_second_identical_call_is_a_free_cache_hit(tool, source_video, tmp_path, monkeypatch):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    monkeypatch.setattr(tool, "_upload", lambda path, token: "http://fake/upload")
    monkeypatch.setattr(
        tool, "_create_prediction",
        lambda *a, **k: {"id": "p", "status": "succeeded", "output": "http://fake/mask.mp4"},
    )
    monkeypatch.setattr(tool, "_download", lambda url, dest: _write_synthetic_mask(dest))

    args = {
        "video_path": str(source_video),
        "points": [{"x": 160, "y": 120, "label": 1}],
        "output_path": str(tmp_path / "cut.mov"),
        "confirm": True,
    }
    first = tool.execute(dict(args))
    assert first.success and first.data["cache_hit"] is False

    # Second call must NOT touch the network at all.
    def _boom(*a, **k):
        raise AssertionError("cache hit must not call the API")

    monkeypatch.setattr(tool, "_upload", _boom)
    monkeypatch.setattr(tool, "_create_prediction", _boom)
    second = tool.execute(dict(args))
    assert second.success is True
    assert second.data["cache_hit"] is True
    assert second.cost_usd == 0.0


# --- real composite (scale2ref dimension-mismatch handling) ---------------

@needs_ffmpeg
def test_composite_alpha_handles_dimension_mismatch(tool, tmp_path):
    src = tmp_path / "s.mp4"
    mask = tmp_path / "m.mp4"
    out = tmp_path / "o.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1:r=10",
         "-pix_fmt", "yuv420p", str(src)], capture_output=True, check=True,
    )
    _write_synthetic_mask(mask)  # 240x180 — deliberately different size
    tool._composite_alpha(src, mask, out)
    assert out.exists() and out.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,pix_fmt", "-of", "default=nw=1", str(out)],
        capture_output=True, text=True,
    )
    # output matches the SOURCE dims (320x240) and has alpha
    assert "width=320" in probe.stdout and "height=240" in probe.stdout
    assert "argb" in probe.stdout or "rgba" in probe.stdout
