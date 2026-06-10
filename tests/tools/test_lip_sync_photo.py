"""Tests for tools/avatar/lip_sync_photo.py (hosted photo+audio lip sync).

Replicate is fully mocked — no API, no cost. The repo-validated community-endpoint gotcha
is encoded as a test: predictions MUST be created via the versioned POST /v1/predictions
with a "version" field (the /v1/models/{slug}/predictions route 404s for community models).
"""

from __future__ import annotations

import pytest

from tools.avatar.lip_sync_photo import LipSyncPhoto, _PredictionError

VALID_TOKEN = "r8_" + "y" * 37


@pytest.fixture
def tool(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNOLAN_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv(LipSyncPhoto.AUTOCONFIRM_ENV, raising=False)
    return LipSyncPhoto()


@pytest.fixture
def photo(tmp_path):
    p = tmp_path / "face.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    return p


@pytest.fixture
def audio(tmp_path):
    p = tmp_path / "speech.wav"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake")
    return p


class _Resp:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


# --- guards (pure: no network, no ffmpeg) ----------------------------------

def test_requires_image_and_audio(tool, monkeypatch, photo, audio):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    res = tool.execute({"audio_path": str(audio)})
    assert res.success is False and "image_path" in res.error
    res = tool.execute({"image_path": str(photo)})
    assert res.success is False and "audio_path" in res.error


def test_missing_files_rejected(tool, monkeypatch, photo, audio):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    res = tool.execute({"image_path": "nope.jpg", "audio_path": str(audio)})
    assert res.success is False and "Image not found" in res.error
    res = tool.execute({"image_path": str(photo), "audio_path": "nope.wav"})
    assert res.success is False and "Audio not found" in res.error


def test_unsupported_formats_rejected(tool, monkeypatch, tmp_path, photo, audio):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    gif = tmp_path / "x.gif"
    gif.write_bytes(b"GIF")
    res = tool.execute({"image_path": str(gif), "audio_path": str(audio)})
    assert res.success is False and "Unsupported image format" in res.error
    mid = tmp_path / "x.mid"
    mid.write_bytes(b"MThd")
    res = tool.execute({"image_path": str(photo), "audio_path": str(mid)})
    assert res.success is False and "Unsupported audio format" in res.error


def test_token_guard_footgun(tool, monkeypatch, photo, audio):
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_abc # leaked")
    res = tool.execute({"image_path": str(photo), "audio_path": str(audio)})
    assert res.success is False and "footgun" in res.error


def test_missing_token(tool, monkeypatch, photo, audio):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    res = tool.execute({"image_path": str(photo), "audio_path": str(audio)})
    assert res.success is False and "REPLICATE_API_TOKEN" in res.error


def test_audio_cap_rejects_over_60s(tool, monkeypatch, photo, audio):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    monkeypatch.setattr(tool, "_media_duration", lambda p: 120.0)
    res = tool.execute({"image_path": str(photo), "audio_path": str(audio)})
    assert res.success is False and "capped at 60" in res.error


# --- confirmation gate ------------------------------------------------------

def test_fresh_run_requires_confirmation(tool, monkeypatch, photo, audio):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    monkeypatch.setattr(tool, "_media_duration", lambda p: 10.0)

    def _boom(*a, **k):
        raise AssertionError("must not spend without confirmation")

    monkeypatch.setattr(tool, "_upload", _boom)
    monkeypatch.setattr(tool, "_create_prediction", _boom)
    res = tool.execute({"image_path": str(photo), "audio_path": str(audio)})
    assert res.success is False and res.data.get("requires_confirmation") is True
    assert res.data.get("estimated_cost_usd") > 0


def test_autoconfirm_env_bypasses_gate(tool, monkeypatch, photo, audio, tmp_path):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    monkeypatch.setenv(LipSyncPhoto.AUTOCONFIRM_ENV, "1")
    monkeypatch.setattr(tool, "_media_duration", lambda p: 10.0)
    monkeypatch.setattr(tool, "_upload", lambda path, token: "http://fake/up")
    monkeypatch.setattr(
        tool, "_create_prediction",
        lambda *a, **k: {"id": "p1", "status": "succeeded", "output": "http://fake/out.mp4"},
    )
    monkeypatch.setattr(tool, "_download", lambda url, dest: dest.write_bytes(b"talking"))
    res = tool.execute({
        "image_path": str(photo), "audio_path": str(audio), "output_path": str(tmp_path / "o.mp4"),
    })
    assert res.success, res.error


# --- versioned community endpoint (THE repo-validated gotcha) ---------------

def test_community_versioned_endpoint_used(tool, monkeypatch):
    """Community model -> resolve latest version, then POST /v1/predictions with 'version'.

    NEVER /v1/models/{slug}/predictions — that route is official-models-only and 404s.
    """
    calls = {}

    def fake_get(url, **k):
        calls["get_url"] = url
        return _Resp({"latest_version": {"id": "ver123"}})

    def fake_post(url, **k):
        calls["post_url"] = url
        calls["json"] = k.get("json")
        return _Resp({"id": "p", "status": "succeeded", "output": "http://x/o.mp4"})

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    tool._create_prediction("http://img", "http://aud", "cjwbw/sadtalker", {}, VALID_TOKEN)
    assert calls["get_url"].endswith("/models/cjwbw/sadtalker")
    assert calls["post_url"].endswith("/v1/predictions")
    assert "/models/" not in calls["post_url"]
    assert calls["json"]["version"] == "ver123"


def test_pinned_version_skips_resolution(tool, monkeypatch):
    calls = {}

    def fake_get(url, **k):
        raise AssertionError("pinned model_version must not trigger a version-resolve GET")

    def fake_post(url, **k):
        calls["post_url"] = url
        calls["json"] = k.get("json")
        return _Resp({"id": "p", "status": "succeeded", "output": "http://x/o.mp4"})

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    tool._create_prediction(
        "http://img", "http://aud", "cjwbw/sadtalker", {"model_version": "pinned456"}, VALID_TOKEN
    )
    assert calls["post_url"].endswith("/v1/predictions")
    assert calls["json"]["version"] == "pinned456"


def test_stale_pinned_version_404_explains_version_rot(tool, monkeypatch):
    import requests

    monkeypatch.setattr(requests, "post", lambda url, **k: _Resp(status_code=404))
    with pytest.raises(_PredictionError, match="re-pin"):
        tool._create_prediction(
            "http://img", "http://aud", "cjwbw/sadtalker", {"model_version": "stale"}, VALID_TOKEN
        )


def test_unresolvable_version_errors(tool, monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **k: _Resp({"latest_version": {}}))
    with pytest.raises(_PredictionError, match="version id"):
        tool._resolve_version("cjwbw/sadtalker", VALID_TOKEN)


# --- payload passthroughs (pure) --------------------------------------------

def test_payload_default_field_names_and_passthroughs(tool):
    payload = tool._build_payload("http://img", "http://aud", {"still_mode": True, "preprocess": "full"})
    assert payload["source_image"] == "http://img"
    assert payload["driven_audio"] == "http://aud"
    assert payload["still"] is True
    assert payload["preprocess"] == "full"
    assert payload["enhancer"] == "gfpgan"  # default
    assert "expression_scale" not in payload  # only sent when set


def test_payload_overrides_and_extras(tool):
    payload = tool._build_payload(
        "http://img", "http://aud",
        {
            "image_input_key": "face", "audio_input_key": "audio",
            "enhancer": "none", "expression_scale": 1.3,
            "extra_inputs": {"pose_style": 5},
        },
    )
    assert payload["face"] == "http://img"
    assert payload["audio"] == "http://aud"
    assert "enhancer" not in payload  # 'none' omits the field
    assert payload["expression_scale"] == 1.3
    assert payload["pose_style"] == 5


def test_extract_video_url_shapes(tool):
    assert tool._extract_video_url("http://x/a.mp4") == "http://x/a.mp4"
    assert tool._extract_video_url(["http://x/a.mp4"]) == "http://x/a.mp4"
    assert tool._extract_video_url({"video": "http://x/v.mp4"}) == "http://x/v.mp4"
    with pytest.raises(_PredictionError):
        tool._extract_video_url({"nope": 1})


# --- happy path / cache (network mocked) -------------------------------------

def _mock_success(tool, monkeypatch):
    monkeypatch.setattr(tool, "_media_duration", lambda p: 10.0)
    monkeypatch.setattr(tool, "_upload", lambda path, token: f"http://fake/up/{path.name}")
    monkeypatch.setattr(
        tool, "_create_prediction",
        lambda *a, **k: {"id": "p1", "status": "succeeded", "output": "http://fake/out.mp4"},
    )
    monkeypatch.setattr(tool, "_download", lambda url, dest: dest.write_bytes(b"talking"))


def test_happy_path_downloads_talking_head(tool, monkeypatch, photo, audio, tmp_path):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    _mock_success(tool, monkeypatch)
    out = tmp_path / "talking.mp4"
    res = tool.execute({
        "image_path": str(photo), "audio_path": str(audio),
        "confirm": True, "output_path": str(out),
    })
    assert res.success, res.error
    assert out.exists() and res.artifacts == [str(out)]
    assert res.data["cache_hit"] is False
    assert res.data["model"] == "cjwbw/sadtalker"
    assert res.data["image"] == str(photo) and res.data["audio"] == str(audio)
    assert res.cost_usd > 0


def test_default_output_name_mirrors_talking_head(tool, monkeypatch, photo, audio):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    _mock_success(tool, monkeypatch)
    res = tool.execute({"image_path": str(photo), "audio_path": str(audio), "confirm": True})
    assert res.success, res.error
    assert res.data["output"].endswith("face_talking.mp4")


def test_second_call_is_free_cache_hit(tool, monkeypatch, photo, audio, tmp_path):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    _mock_success(tool, monkeypatch)
    args = {
        "image_path": str(photo), "audio_path": str(audio),
        "confirm": True, "output_path": str(tmp_path / "o.mp4"),
    }
    first = tool.execute(dict(args))
    assert first.success and first.data["cache_hit"] is False

    monkeypatch.setattr(tool, "_upload", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no API on cache hit")))
    monkeypatch.setattr(tool, "_create_prediction", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no API")))
    second = tool.execute(dict(args))
    assert second.success and second.data["cache_hit"] is True and second.cost_usd == 0.0


def test_different_params_miss_cache(tool, monkeypatch, photo, audio, tmp_path):
    monkeypatch.setenv("REPLICATE_API_TOKEN", VALID_TOKEN)
    _mock_success(tool, monkeypatch)
    base = {
        "image_path": str(photo), "audio_path": str(audio),
        "confirm": True, "output_path": str(tmp_path / "o.mp4"),
    }
    assert tool.execute(dict(base)).success
    res = tool.execute(dict(base, still_mode=True))
    assert res.success and res.data["cache_hit"] is False


# --- contract / registry -----------------------------------------------------

def test_contract_fields(tool):
    assert tool.name == "lip_sync_photo"
    assert tool.capability == "avatar"
    assert tool.fallback_tools == ["talking_head", "lip_sync"]
    assert "env:REPLICATE_API_TOKEN" in tool.dependencies
    assert any("HDR" in entry for entry in tool.not_good_for)
    assert tool.estimate_cost({}) > 0


def test_registered_under_avatar(tool):
    from tools.tool_registry import registry
    registry.discover()
    names = [t.get("name") for tools in registry.capability_catalog().values() for t in tools]
    assert "lip_sync_photo" in names
