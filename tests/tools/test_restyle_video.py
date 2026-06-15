"""Tests for tools/video/restyle_video.py (Edits-parity Wave 5).

Replicate is mocked — no API, no cost. The official/community endpoint selection is checked
both as pure logic (_is_official) and by capturing the URL via a fake requests module.
"""

from __future__ import annotations

import types

import pytest

from tools.video.restyle_video import RestyleVideo, _PredictionError

VALID_TOKEN = "r8_" + "y" * 37


@pytest.fixture
def tool(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNOLAN_CACHE_DIR", str(tmp_path / "cache"))
    return RestyleVideo()


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "src.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")  # exists; duration is mocked in tests
    return p


# --- guards ---------------------------------------------------------------

def test_requires_video_and_prompt(tool, monkeypatch):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    assert tool.execute({"prompt": "x"}).success is False
    assert tool.execute({"video_path": "nope.mp4", "prompt": "x"}).success is False


def test_token_guard_footgun(tool, monkeypatch, clip):
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_abc # leaked")
    res = tool.execute({"video_path": str(clip), "prompt": "claymation"})
    assert res.success is False and "footgun" in res.error


def test_unsupported_format(tool, monkeypatch, tmp_path):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    f = tmp_path / "x.gif"
    f.write_bytes(b"GIF")
    res = tool.execute({"video_path": str(f), "prompt": "x"})
    assert res.success is False and "Unsupported format" in res.error


def test_duration_cap_rejects_over_10s(tool, monkeypatch, clip):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    monkeypatch.setattr(tool, "_video_duration", lambda p: 15.0)
    res = tool.execute({"video_path": str(clip), "prompt": "neon"})
    assert res.success is False and "capped at 10" in res.error


# --- confirmation gate ----------------------------------------------------

def test_fresh_run_requires_confirmation(tool, monkeypatch, clip):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    monkeypatch.setattr(tool, "_video_duration", lambda p: 5.0)

    def _boom(*a, **k):
        raise AssertionError("must not spend without confirmation")

    monkeypatch.setattr(tool, "_upload", _boom)
    monkeypatch.setattr(tool, "_create_prediction", _boom)
    res = tool.execute({"video_path": str(clip), "prompt": "claymation"})
    assert res.success is False and res.data.get("requires_confirmation") is True


# --- happy path / cache (network mocked) ----------------------------------

def test_happy_path_downloads_restyled_clip(tool, monkeypatch, clip, tmp_path):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    monkeypatch.setattr(tool, "_video_duration", lambda p: 5.0)
    monkeypatch.setattr(tool, "_upload", lambda path, token: "http://fake/up")
    monkeypatch.setattr(
        tool, "_create_prediction",
        lambda *a, **k: {"id": "p1", "status": "succeeded", "output": "http://fake/out.mp4"},
    )
    monkeypatch.setattr(tool, "_download", lambda url, dest: dest.write_bytes(b"restyled"))
    out = tmp_path / "styled.mp4"
    res = tool.execute({"video_path": str(clip), "prompt": "cyberpunk", "confirm": True, "output_path": str(out)})
    assert res.success, res.error
    assert out.exists() and res.data["cache_hit"] is False
    assert res.data["model"] == "luma/modify-video"


def test_second_call_is_free_cache_hit(tool, monkeypatch, clip, tmp_path):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    monkeypatch.setattr(tool, "_video_duration", lambda p: 5.0)
    monkeypatch.setattr(tool, "_upload", lambda path, token: "http://fake/up")
    monkeypatch.setattr(
        tool, "_create_prediction",
        lambda *a, **k: {"id": "p1", "status": "succeeded", "output": "http://fake/out.mp4"},
    )
    monkeypatch.setattr(tool, "_download", lambda url, dest: dest.write_bytes(b"restyled"))
    args = {"video_path": str(clip), "prompt": "cyberpunk", "confirm": True, "output_path": str(tmp_path / "o.mp4")}
    first = tool.execute(dict(args))
    assert first.success and first.data["cache_hit"] is False

    monkeypatch.setattr(tool, "_upload", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no API on cache hit")))
    monkeypatch.setattr(tool, "_create_prediction", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no API")))
    second = tool.execute(dict(args))
    assert second.success and second.data["cache_hit"] is True and second.cost_usd == 0.0


# --- output parsing / endpoint selection ----------------------------------

def test_extract_video_url_shapes(tool):
    assert tool._extract_video_url("http://x/a.mp4") == "http://x/a.mp4"
    assert tool._extract_video_url(["http://x/a.mp4"]) == "http://x/a.mp4"
    assert tool._extract_video_url({"video": "http://x/v.mp4"}) == "http://x/v.mp4"
    with pytest.raises(_PredictionError):
        tool._extract_video_url({"nope": 1})


def test_is_official_detection(tool):
    assert tool._is_official("luma/modify-video", "auto") is True
    assert tool._is_official("someuser/cool-restyle", "auto") is False
    assert tool._is_official("someuser/cool-restyle", "official") is True
    assert tool._is_official("luma/modify-video", "community") is False


def test_official_endpoint_url(tool, monkeypatch):
    """Official model -> POST /v1/models/{slug}/predictions (no version resolve)."""
    calls = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"id": "p", "status": "succeeded", "output": "http://x/o.mp4"}

    def fake_post(url, **k):
        calls["post_url"] = url
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    tool._create_prediction("http://up", "neon", "flex", "luma/modify-video", {"endpoint": "auto"}, VALID_TOKEN)
    assert calls["post_url"].endswith("/models/luma/modify-video/predictions")


def test_community_endpoint_resolves_version(tool, monkeypatch):
    """Community model -> resolve version, then POST /v1/predictions."""
    calls = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"id": "p", "status": "succeeded", "output": "http://x/o.mp4", "latest_version": {"id": "ver123"}}

    def fake_get(url, **k):
        calls["get_url"] = url
        return _Resp()

    def fake_post(url, **k):
        calls["post_url"] = url
        calls["json"] = k.get("json")
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    tool._create_prediction("http://up", "neon", "flex", "someuser/restyle", {"endpoint": "community"}, VALID_TOKEN)
    assert calls["get_url"].endswith("/models/someuser/restyle")
    assert calls["post_url"].endswith("/v1/predictions")
    assert calls["json"]["version"] == "ver123"


def test_registered_under_video_generation(tool):
    from tools.tool_registry import registry
    registry.discover()
    names = [t.get("name") for tools in registry.capability_catalog().values() for t in tools]
    assert "restyle_video" in names
