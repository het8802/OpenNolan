"""Tests for tools/search/sticker_search.py (Edits parity: stickers & GIFs).

The HTTP boundary is mocked in every test — nothing here touches GIPHY or Tenor
for real. We exercise:

  - missing-both-keys -> clear error naming both env vars (before any HTTP)
  - endpoint + key-param routing per provider and per kind (gifs vs stickers)
  - auto provider selection (GIPHY preferred when both keys are set) + overrides
  - _pick_rendition as a pure helper (mp4-first for gifs, alpha-first for stickers)
  - result shape (chosen rendition url, gif_url always recorded, attribution)
  - download path + asset-manifest-ready entries (schema-validated)
  - optional asset_manifest registration with the warn-don't-fail contract
"""

from __future__ import annotations

import json

import pytest
import requests

from schemas.artifacts import validate_artifact
from tools.search.sticker_search import (
    GIPHY_GIF_ENDPOINT,
    GIPHY_STICKER_ENDPOINT,
    TENOR_ENDPOINT,
    StickerSearch,
)


# ----------------------------------------------------------------------
# Fakes / helpers
# ----------------------------------------------------------------------


class FakeResp:
    def __init__(self, payload: dict | None = None, content: bytes = b"", status_code: int = 200):
        self._payload = payload or {}
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


GIPHY_PAYLOAD = {
    "data": [
        {
            "id": "abc123",
            "title": "Deal With It",
            "url": "https://giphy.com/gifs/abc123",
            "user": {"display_name": "SomeArtist"},
            "images": {
                "original": {
                    "url": "https://media.giphy.com/abc123/giphy.gif",
                    "mp4": "https://media.giphy.com/abc123/giphy.mp4",
                    "webp": "https://media.giphy.com/abc123/giphy.webp",
                    "width": "480",
                    "height": "270",
                },
                "preview_gif": {"url": "https://media.giphy.com/abc123/preview.gif"},
            },
        }
    ]
}

TENOR_PAYLOAD = {
    "results": [
        {
            "id": "t1",
            "title": "wow",
            "itemurl": "https://tenor.com/view/wow-t1",
            "media_formats": {
                "gif": {"url": "https://media.tenor.com/t1/g.gif", "dims": [498, 280]},
                "mp4": {"url": "https://media.tenor.com/t1/v.mp4", "dims": [640, 360]},
                "webm": {"url": "https://media.tenor.com/t1/v.webm", "dims": [640, 360]},
                "tinygif": {"url": "https://media.tenor.com/t1/tiny.gif", "dims": [220, 124]},
            },
        }
    ]
}

TENOR_STICKER_PAYLOAD = {
    "results": [
        {
            "id": "ts1",
            "title": "sparkle sticker",
            "itemurl": "https://tenor.com/view/sparkle-ts1",
            "media_formats": {
                "gif_transparent": {"url": "https://media.tenor.com/ts1/alpha.gif", "dims": [320, 320]},
                "gif": {"url": "https://media.tenor.com/ts1/flat.gif", "dims": [320, 320]},
                "mp4": {"url": "https://media.tenor.com/ts1/flat.mp4", "dims": [320, 320]},
                "webm": {"url": "https://media.tenor.com/ts1/flat.webm", "dims": [320, 320]},
            },
        }
    ]
}


@pytest.fixture
def tool():
    return StickerSearch()


@pytest.fixture
def no_keys(monkeypatch):
    # base_tool loads .env at import — strip any real keys before each test
    monkeypatch.delenv("GIPHY_API_KEY", raising=False)
    monkeypatch.delenv("TENOR_API_KEY", raising=False)


def _fake_get(monkeypatch, payload_by_host: dict, media_bytes: bytes = b"FAKEMEDIA"):
    """Patch requests.get: search hosts return payloads, anything else returns media bytes.
    Returns the list of recorded (url, params) calls."""
    calls: list[tuple[str, dict]] = []

    def fake(url, params=None, timeout=None, headers=None):
        calls.append((url, params or {}))
        for host, payload in payload_by_host.items():
            if host in url:
                return FakeResp(payload=payload)
        return FakeResp(content=media_bytes)

    monkeypatch.setattr(requests, "get", fake)
    return calls


def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(requests, "get", boom)


# --- missing keys / validation (no HTTP) ------------------------------------


def test_missing_both_keys_names_both_env_vars(tool, no_keys, monkeypatch):
    _no_network(monkeypatch)
    res = tool.execute({"query": "thumbs up"})
    assert res.success is False
    assert "GIPHY_API_KEY" in res.error and "TENOR_API_KEY" in res.error


def test_requested_provider_without_its_key_rejected(tool, no_keys, monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setenv("TENOR_API_KEY", "tk")
    res = tool.execute({"query": "wow", "provider": "giphy"})
    assert res.success is False and "GIPHY_API_KEY" in res.error


def test_invalid_kind_rejected_before_http(tool, no_keys, monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setenv("GIPHY_API_KEY", "gk")
    res = tool.execute({"query": "wow", "kind": "meme"})
    assert res.success is False and "kind" in res.error


def test_invalid_limit_rejected_before_http(tool, no_keys, monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setenv("GIPHY_API_KEY", "gk")
    assert tool.execute({"query": "wow", "limit": 0}).success is False
    assert tool.execute({"query": "wow", "limit": 99}).success is False


def test_empty_query_rejected(tool, no_keys, monkeypatch):
    _no_network(monkeypatch)
    monkeypatch.setenv("GIPHY_API_KEY", "gk")
    res = tool.execute({"query": "   "})
    assert res.success is False and "query" in res.error


# --- endpoint / key-param / kind routing -------------------------------------


def test_giphy_gif_search_endpoint_and_key_param(tool, no_keys, monkeypatch):
    monkeypatch.setenv("GIPHY_API_KEY", "gk-123")
    calls = _fake_get(monkeypatch, {"api.giphy.com": GIPHY_PAYLOAD})
    res = tool.execute({"query": "deal with it", "kind": "gif"})
    assert res.success, res.error
    url, params = calls[0]
    assert url == GIPHY_GIF_ENDPOINT
    assert params["api_key"] == "gk-123"
    assert params["q"] == "deal with it"


def test_giphy_sticker_kind_routes_to_stickers_endpoint(tool, no_keys, monkeypatch):
    monkeypatch.setenv("GIPHY_API_KEY", "gk-123")
    calls = _fake_get(monkeypatch, {"api.giphy.com": GIPHY_PAYLOAD})
    res = tool.execute({"query": "sparkle", "kind": "sticker"})
    assert res.success, res.error
    assert calls[0][0] == GIPHY_STICKER_ENDPOINT


def test_tenor_endpoint_key_and_sticker_searchfilter(tool, no_keys, monkeypatch):
    monkeypatch.setenv("TENOR_API_KEY", "tk-456")
    calls = _fake_get(monkeypatch, {"tenor.googleapis.com": TENOR_STICKER_PAYLOAD})
    res = tool.execute({"query": "sparkle", "kind": "sticker"})
    assert res.success, res.error
    url, params = calls[0]
    assert url == TENOR_ENDPOINT
    assert params["key"] == "tk-456"
    assert params["q"] == "sparkle"
    assert params["searchfilter"] == "sticker"


def test_tenor_gif_kind_has_no_searchfilter(tool, no_keys, monkeypatch):
    monkeypatch.setenv("TENOR_API_KEY", "tk-456")
    calls = _fake_get(monkeypatch, {"tenor.googleapis.com": TENOR_PAYLOAD})
    res = tool.execute({"query": "wow", "kind": "gif"})
    assert res.success, res.error
    assert "searchfilter" not in calls[0][1]


def test_auto_prefers_giphy_when_both_keys_set(tool, no_keys, monkeypatch):
    monkeypatch.setenv("GIPHY_API_KEY", "gk")
    monkeypatch.setenv("TENOR_API_KEY", "tk")
    calls = _fake_get(monkeypatch, {"api.giphy.com": GIPHY_PAYLOAD})
    res = tool.execute({"query": "wow", "kind": "gif"})
    assert res.success, res.error
    assert "api.giphy.com" in calls[0][0]
    assert res.data["provider"] == "giphy"


def test_provider_override_forces_tenor(tool, no_keys, monkeypatch):
    monkeypatch.setenv("GIPHY_API_KEY", "gk")
    monkeypatch.setenv("TENOR_API_KEY", "tk")
    calls = _fake_get(monkeypatch, {"tenor.googleapis.com": TENOR_PAYLOAD})
    res = tool.execute({"query": "wow", "kind": "gif", "provider": "tenor"})
    assert res.success, res.error
    assert calls[0][0] == TENOR_ENDPOINT
    assert res.data["provider"] == "tenor"


# --- _pick_rendition (pure) ---------------------------------------------------


def test_pick_rendition_gif_prefers_mp4_over_gif():
    url, label = StickerSearch._pick_rendition(
        {"gif": "u.gif", "mp4": "u.mp4", "webm": "u.webm", "webp": "u.webp"}, "gif"
    )
    assert (url, label) == ("u.mp4", "mp4")


def test_pick_rendition_gif_falls_back_webm_then_gif():
    assert StickerSearch._pick_rendition({"gif": "u.gif", "webm": "u.webm"}, "gif") == ("u.webm", "webm")
    assert StickerSearch._pick_rendition({"gif": "u.gif"}, "gif") == ("u.gif", "gif")


def test_pick_rendition_sticker_prefers_alpha_gif_over_flattened_mp4():
    # H.264 mp4 cannot carry alpha — sticker kind must keep the transparent gif
    url, label = StickerSearch._pick_rendition(
        {"gif": "alpha.gif", "mp4": "flat.mp4", "webm": "flat.webm"}, "sticker"
    )
    assert (url, label) == ("alpha.gif", "gif")


def test_pick_rendition_sticker_mp4_is_last_resort():
    assert StickerSearch._pick_rendition({"mp4": "flat.mp4"}, "sticker") == ("flat.mp4", "mp4")


def test_pick_rendition_none_when_no_renditions():
    assert StickerSearch._pick_rendition({"gif": None, "mp4": None}, "gif") is None


# --- result shape -------------------------------------------------------------


def test_results_choose_mp4_and_always_record_gif_url(tool, no_keys, monkeypatch):
    monkeypatch.setenv("GIPHY_API_KEY", "gk")
    _fake_get(monkeypatch, {"api.giphy.com": GIPHY_PAYLOAD})
    res = tool.execute({"query": "deal with it", "kind": "gif"})
    assert res.success, res.error
    r = res.data["results"][0]
    assert r["url"].endswith(".mp4") and r["rendition"] == "mp4"
    assert r["gif_url"].endswith(".gif")
    assert r["width"] == 480 and r["height"] == 270  # GIPHY string dims coerced
    assert r["preview_url"].endswith("preview.gif")
    assert r["source"] == "https://giphy.com/gifs/abc123"
    assert "GIPHY" in r["attribution"]
    assert "GIPHY" in res.data["attribution_required"]
    assert r["path"] is None  # no download_dir -> URL-only


def test_tenor_sticker_uses_transparent_gif_rendition(tool, no_keys, monkeypatch):
    monkeypatch.setenv("TENOR_API_KEY", "tk")
    _fake_get(monkeypatch, {"tenor.googleapis.com": TENOR_STICKER_PAYLOAD})
    res = tool.execute({"query": "sparkle", "kind": "sticker"})
    assert res.success, res.error
    r = res.data["results"][0]
    assert r["url"] == "https://media.tenor.com/ts1/alpha.gif" and r["rendition"] == "gif"
    assert r["gif_url"] == "https://media.tenor.com/ts1/alpha.gif"
    assert r["attribution"] == "Via Tenor"


def test_no_results_returns_clear_error(tool, no_keys, monkeypatch):
    monkeypatch.setenv("GIPHY_API_KEY", "gk")
    _fake_get(monkeypatch, {"api.giphy.com": {"data": []}})
    res = tool.execute({"query": "zxqj-nothing", "kind": "gif"})
    assert res.success is False and "No gif" in res.error


def test_search_http_error_is_surfaced_not_raised(tool, no_keys, monkeypatch):
    monkeypatch.setenv("GIPHY_API_KEY", "gk")
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp(status_code=429))
    res = tool.execute({"query": "wow", "kind": "gif"})
    assert res.success is False and "search failed" in res.error


# --- download + manifest entries ----------------------------------------------


def test_download_writes_files_and_manifest_entries(tool, no_keys, monkeypatch, tmp_path):
    monkeypatch.setenv("GIPHY_API_KEY", "gk")
    _fake_get(monkeypatch, {"api.giphy.com": GIPHY_PAYLOAD}, media_bytes=b"MP4BYTES")
    res = tool.execute({"query": "deal with it", "kind": "gif", "download_dir": str(tmp_path)})
    assert res.success, res.error
    r = res.data["results"][0]
    assert r["path"] is not None and r["path"].endswith(".mp4")
    with open(r["path"], "rb") as f:
        assert f.read() == b"MP4BYTES"
    assert r["path"] in res.artifacts

    entries = res.data["manifest_entries"]
    assert len(entries) == 1
    e = entries[0]
    assert e["type"] == "video"  # mp4 rendition
    assert e["source_tool"] == "sticker_search"
    assert e["subtype"] == "gif"
    assert e["provider"] == "giphy"
    assert "GIPHY" in e["license"]  # attribution requirement travels with the asset
    assert e["resolution"] == "480x270"
    # asset-manifest-ready: must validate against the real schema as-is
    validate_artifact("asset_manifest", {"version": "1.0", "assets": entries})


def test_sticker_download_gets_gif_extension(tool, no_keys, monkeypatch, tmp_path):
    monkeypatch.setenv("TENOR_API_KEY", "tk")
    _fake_get(monkeypatch, {"tenor.googleapis.com": TENOR_STICKER_PAYLOAD})
    res = tool.execute({"query": "sparkle", "kind": "sticker", "download_dir": str(tmp_path)})
    assert res.success, res.error
    assert res.data["results"][0]["path"].endswith(".gif")
    assert res.data["manifest_entries"][0]["type"] == "animation"


def test_download_failure_is_nonfatal_url_still_returned(tool, no_keys, monkeypatch, tmp_path):
    monkeypatch.setenv("GIPHY_API_KEY", "gk")

    def fake(url, params=None, timeout=None, headers=None):
        if "api.giphy.com" in url:
            return FakeResp(payload=GIPHY_PAYLOAD)
        return FakeResp(status_code=500)  # media download fails

    monkeypatch.setattr(requests, "get", fake)
    res = tool.execute({"query": "wow", "kind": "gif", "download_dir": str(tmp_path)})
    assert res.success, res.error
    assert res.data["results"][0]["path"] is None
    assert res.data["results"][0]["url"].endswith(".mp4")
    assert res.data["downloaded_paths"] == []


# --- asset_manifest registration -----------------------------------------------


def test_registers_downloaded_assets_into_manifest(tool, no_keys, monkeypatch, tmp_path):
    monkeypatch.setenv("GIPHY_API_KEY", "gk")
    _fake_get(monkeypatch, {"api.giphy.com": GIPHY_PAYLOAD})
    manifest = tmp_path / "asset_manifest.json"
    manifest.write_text(json.dumps({"version": "1.0", "assets": []}))
    res = tool.execute({
        "query": "wow", "kind": "gif", "download_dir": str(tmp_path / "dl"),
        "asset_manifest_path": str(manifest), "scene_id": "scene-2",
    })
    assert res.success, res.error
    doc = json.loads(manifest.read_text())
    assert len(doc["assets"]) == 1
    a = doc["assets"][0]
    assert a["source_tool"] == "sticker_search"
    assert a["scene_id"] == "scene-2"
    assert str(manifest) in res.artifacts


def test_invalid_manifest_warns_but_search_still_succeeds(tool, no_keys, monkeypatch, tmp_path):
    monkeypatch.setenv("GIPHY_API_KEY", "gk")
    _fake_get(monkeypatch, {"api.giphy.com": GIPHY_PAYLOAD})
    manifest = tmp_path / "bad.json"
    manifest.write_text(json.dumps({"version": "1.0", "assets": "not-a-list"}))
    res = tool.execute({
        "query": "wow", "kind": "gif", "download_dir": str(tmp_path / "dl"),
        "asset_manifest_path": str(manifest),
    })
    # the downloads are valid; only registration failed -> warning, not failure
    assert res.success is True
    assert "asset_manifest_warning" in res.data
