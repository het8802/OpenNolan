"""Tests for tools/audio/sfx_kit.py (Edits parity: SFX library + personalized SFX).

search runs live against the real in-repo manifest (free, local). generate mocks
requests.post — no API spend. register round-trips on a tmp COPY of the manifest;
the real assets/sfx/manifest.json is never mutated (asserted by hash).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.audio.sfx_kit import SfxKit
from tools.base_tool import ToolStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_MANIFEST = REPO_ROOT / "assets" / "sfx" / "manifest.json"

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture
def tool():
    return SfxKit()


@pytest.fixture
def tmp_manifest(tmp_path):
    """A tmp COPY of the real manifest — register tests must never touch the real one."""
    p = tmp_path / "manifest.json"
    shutil.copyfile(REAL_MANIFEST, p)
    return p


@pytest.fixture
def fake_mp3(tmp_path):
    p = tmp_path / "new-effect.mp3"
    p.write_bytes(b"ID3\x03\x00" + b"\x00" * 64)
    return p


def _real_manifest_hash():
    return hashlib.sha256(REAL_MANIFEST.read_bytes()).hexdigest()


# --- search (live, against the real library) -------------------------------

def test_search_finds_whoosh_fast(tool):
    res = tool.execute({"operation": "search", "query": "whoosh fast"})
    assert res.success, res.error
    slugs = [m["slug"] for m in res.data["matches"]]
    assert "whoosh-fast" in slugs
    top = res.data["matches"][0]
    assert top["slug"] == "whoosh-fast"  # exact-slug query must rank first
    assert Path(top["path"]).is_absolute()
    assert top["file_exists"] is True
    assert res.cost_usd == 0.0  # search is free/local


def test_search_by_category_only(tool):
    res = tool.execute({"operation": "search", "category": "transition"})
    assert res.success, res.error
    assert res.data["count"] >= 4  # whoosh-fast/deep, swipe-paper, transition-riser
    assert all(m["category"] == "transition" for m in res.data["matches"])


def test_search_query_plus_category_filter(tool):
    res = tool.execute({"operation": "search", "query": "whoosh", "category": "ui"})
    assert res.success, res.error
    assert res.data["count"] == 0  # whooshes are transitions, not ui


def test_search_limit_respected(tool):
    res = tool.execute({"operation": "search", "category": "ui", "limit": 2})
    assert res.success, res.error
    assert res.data["count"] == 2


def test_search_usage_text_is_searchable(tool):
    # "checklist" only appears in tick-check's usage string
    res = tool.execute({"operation": "search", "query": "checklist"})
    assert res.success, res.error
    assert res.data["matches"][0]["slug"] == "tick-check"


# --- search guards (pure) ---------------------------------------------------

def test_search_requires_query_or_category(tool):
    res = tool.execute({"operation": "search"})
    assert res.success is False and "query and/or category" in res.error


def test_search_invalid_category_rejected(tool):
    res = tool.execute({"operation": "search", "category": "explosions"})
    assert res.success is False and "category must be one of" in res.error


def test_search_missing_manifest(tool, tmp_path):
    res = tool.execute({
        "operation": "search", "query": "whoosh",
        "manifest_path": str(tmp_path / "nope.json"),
    })
    assert res.success is False and "manifest not found" in res.error


def test_invalid_operation_rejected(tool):
    res = tool.execute({"operation": "remix"})
    assert res.success is False and "operation must be one of" in res.error


# --- generate (HTTP mocked, no spend) ---------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, content=b"ID3fake-mp3-bytes", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


def _patch_post(monkeypatch, response, captured):
    import requests

    def fake_post(url, headers=None, params=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, params=params, json=json, timeout=timeout)
        return response

    monkeypatch.setattr(requests, "post", fake_post)


def test_generate_writes_mp3(tool, monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k-test")
    captured: dict = {}
    _patch_post(monkeypatch, _FakeResponse(), captured)
    out = tmp_path / "crowd-gasp.mp3"
    res = tool.execute({
        "operation": "generate", "prompt": "crowd gasp, short, clean",
        "duration_seconds": 1.2, "prompt_influence": 0.7, "output_path": str(out),
    })
    assert res.success, res.error
    assert out.read_bytes() == b"ID3fake-mp3-bytes"
    assert captured["url"] == "https://api.elevenlabs.io/v1/sound-generation"
    assert captured["headers"]["xi-api-key"] == "k-test"
    assert captured["params"] == {"output_format": "mp3_44100_128"}
    assert captured["json"]["text"] == "crowd gasp, short, clean"
    assert captured["json"]["duration_seconds"] == 1.2
    assert captured["json"]["prompt_influence"] == 0.7
    assert res.cost_usd == pytest.approx(0.024)
    assert str(out) in res.artifacts


def test_generate_requires_api_key(tool, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    res = tool.execute({
        "operation": "generate", "prompt": "gasp", "duration_seconds": 1.0,
        "output_path": "/tmp/x.mp3",
    })
    assert res.success is False and "ELEVENLABS_API_KEY" in res.error


def test_generate_requires_duration_no_silent_default(tool, monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k-test")
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("must not hit the API"))
    res = tool.execute({
        "operation": "generate", "prompt": "gasp",
        "output_path": str(tmp_path / "x.mp3"),
    })
    assert res.success is False and "duration_seconds" in res.error


def test_generate_duration_out_of_range(tool, monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k-test")
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("must not hit the API"))
    base = {"operation": "generate", "prompt": "gasp", "output_path": str(tmp_path / "x.mp3")}
    assert tool.execute({**base, "duration_seconds": 0.1}).success is False
    assert tool.execute({**base, "duration_seconds": 60}).success is False


def test_generate_requires_prompt_and_mp3_output(tool, monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k-test")
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("must not hit the API"))
    res = tool.execute({
        "operation": "generate", "duration_seconds": 1.0,
        "output_path": str(tmp_path / "x.mp3"),
    })
    assert res.success is False and "prompt" in res.error
    res = tool.execute({
        "operation": "generate", "prompt": "gasp", "duration_seconds": 1.0,
        "output_path": str(tmp_path / "x.wav"),
    })
    assert res.success is False and ".mp3" in res.error


def test_generate_http_error_surfaced(tool, monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k-bad")
    captured: dict = {}
    _patch_post(monkeypatch, _FakeResponse(status_code=401, content=b"", text="invalid api key"), captured)
    res = tool.execute({
        "operation": "generate", "prompt": "gasp", "duration_seconds": 1.0,
        "output_path": str(tmp_path / "x.mp3"),
    })
    assert res.success is False and "401" in res.error and "invalid api key" in res.error


def test_estimate_cost(tool):
    assert tool.estimate_cost({"operation": "search", "query": "whoosh"}) == 0.0
    assert tool.estimate_cost({"operation": "generate", "duration_seconds": 2.0}) == pytest.approx(0.04)
    with pytest.raises(ValueError):
        tool.estimate_cost({"operation": "generate"})


def test_status_degrades_without_key_search_still_works(tool, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert tool.get_status() == ToolStatus.DEGRADED
    assert tool.execute({"operation": "search", "query": "whoosh"}).success is True
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k-test")
    assert tool.get_status() == ToolStatus.AVAILABLE


# --- register (tmp copy of the manifest only) -------------------------------

def test_register_round_trip(tool, tmp_manifest, fake_mp3):
    real_hash_before = _real_manifest_hash()
    res = tool.execute({
        "operation": "register", "manifest_path": str(tmp_manifest),
        "slug": "crowd-gasp", "category": "emphasis",
        "prompt": "Crowd gasp of surprise, short and clean",
        "usage": "Shock reveal, plot-twist beat.",
        "file_path": str(fake_mp3), "duration_seconds": 0.8,
    })
    assert res.success, res.error
    dest = tmp_manifest.parent / "crowd-gasp.mp3"
    assert dest.is_file() and dest.read_bytes() == fake_mp3.read_bytes()

    doc = json.loads(tmp_manifest.read_text())
    assert doc["count"] == len(doc["effects"])
    entry = next(fx for fx in doc["effects"] if fx["slug"] == "crowd-gasp")
    assert entry["category"] == "emphasis"
    assert entry["duration_seconds"] == 0.8
    assert entry["file"] == "crowd-gasp.mp3"
    # canonical ordering preserved (category, slug) like the generator script
    keys = [(fx["category"], fx["slug"]) for fx in doc["effects"]]
    assert keys == sorted(keys)

    # the new effect is immediately searchable -> round trip complete
    found = tool.execute({
        "operation": "search", "query": "gasp", "manifest_path": str(tmp_manifest),
    })
    assert found.success and found.data["matches"][0]["slug"] == "crowd-gasp"

    assert _real_manifest_hash() == real_hash_before  # real library untouched


def test_register_duplicate_slug_rejected(tool, tmp_manifest, fake_mp3):
    res = tool.execute({
        "operation": "register", "manifest_path": str(tmp_manifest),
        "slug": "whoosh-fast", "category": "transition",
        "prompt": "p", "usage": "u",
        "file_path": str(fake_mp3), "duration_seconds": 0.8,
    })
    assert res.success is False and "already exists" in res.error


def test_register_invalid_slug_rejected(tool, tmp_manifest, fake_mp3):
    for bad in ("Crowd Gasp", "crowd_gasp", "-leading", "UPPER"):
        res = tool.execute({
            "operation": "register", "manifest_path": str(tmp_manifest),
            "slug": bad, "category": "ui", "prompt": "p", "usage": "u",
            "file_path": str(fake_mp3), "duration_seconds": 0.5,
        })
        assert res.success is False and "slug" in res.error


def test_register_invalid_category_rejected(tool, tmp_manifest, fake_mp3):
    res = tool.execute({
        "operation": "register", "manifest_path": str(tmp_manifest),
        "slug": "crowd-gasp", "category": "explosions", "prompt": "p", "usage": "u",
        "file_path": str(fake_mp3), "duration_seconds": 0.5,
    })
    assert res.success is False and "category must be one of" in res.error


def test_register_missing_file_rejected(tool, tmp_manifest):
    res = tool.execute({
        "operation": "register", "manifest_path": str(tmp_manifest),
        "slug": "crowd-gasp", "category": "ui", "prompt": "p", "usage": "u",
        "file_path": "/nope/missing.mp3", "duration_seconds": 0.5,
    })
    assert res.success is False and "not found" in res.error


def test_register_requires_prompt_and_usage(tool, tmp_manifest, fake_mp3):
    base = {
        "operation": "register", "manifest_path": str(tmp_manifest),
        "slug": "crowd-gasp", "category": "ui",
        "file_path": str(fake_mp3), "duration_seconds": 0.5,
    }
    assert "prompt" in tool.execute({**base, "usage": "u"}).error
    assert "usage" in tool.execute({**base, "prompt": "p"}).error


@needs_ffmpeg
def test_register_probes_duration_with_ffprobe(tool, tmp_manifest, tmp_path):
    mp3 = tmp_path / "real.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
         "-c:a", "libmp3lame", str(mp3)],
        capture_output=True, check=True,
    )
    res = tool.execute({
        "operation": "register", "manifest_path": str(tmp_manifest),
        "slug": "test-tone", "category": "ui",
        "prompt": "test tone", "usage": "tests only",
        "file_path": str(mp3),  # no duration_seconds -> probed
    })
    assert res.success, res.error
    assert 0.3 <= res.data["duration_seconds"] <= 0.8
