"""Tests for tools/analysis/content_signal.py (Meta TRIBE v2 virality scorer).

The HTTP boundary is mocked in every test — nothing here touches the network or
spends money on the real paid Replicate model. We exercise:

  - availability (token present / missing)
  - the guards that must run BEFORE any paid call (missing file, bad format, >60s)
  - the content-hash cache (hit returns cached report, makes no HTTP call)
  - the happy path (upload -> predict -> parse -> validate -> write + cache)
  - the polling path (processing -> succeeded)
  - failure paths (upload error, prediction failed, malformed output, schema-invalid)
    each of which must NOT write a corrupt artifact
  - dry_run (estimate only, no HTTP)
  - the artifact schema itself
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from schemas.artifacts import validate_artifact
from tools.analysis.content_signal import ContentSignal
from tools.base_tool import ToolStatus


# ----------------------------------------------------------------------
# Fakes / helpers
# ----------------------------------------------------------------------


class FakeResp:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


GOOD_OUTPUT = {
    "headline_score": 78,
    "sub_scores": {
        "reward": 80,
        "emotion": 75,
        "attention": 70,
        "social_relevance": 60,
        "novelty": 55,
    },
    "timeline": [
        {"t": 0.0, "reward": 80.0, "attention": 70.0},
        {"t": 0.5, "reward": 78.0, "attention": 69.0},
    ],
    "video_duration_s": 30.0,
    "frame_count": 60,
    "model_version": "tribe-v2-abc",
    "scoring_version": "sig-1.2",
}


def _dummy_video(tmp_path: Path, name: str = "final.mp4", data: bytes = b"FAKEMP4DATA") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _tool(monkeypatch, tmp_path, *, token: str = "r8_testtoken", duration: float | None = 30.0):
    """A ContentSignal wired for tests: token set, cache scoped to tmp, duration stubbed.

    Sets CONTENT_SIGNAL_AUTOCONFIRM so the (now-gated) paid-path tests proceed without
    passing confirm=true everywhere — this mirrors a headless/non-interactive run. The
    confirm gate itself is exercised separately in test_confirm_gate_*."""
    monkeypatch.setenv("REPLICATE_API_TOKEN", token)
    monkeypatch.setenv("OPENMONTAGE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CONTENT_SIGNAL_AUTOCONFIRM", "1")
    if duration is not None:
        monkeypatch.setattr(ContentSignal, "_video_duration", lambda self, p: duration)
    # Stub version resolution so execute() makes no extra GET (community-model versioned path).
    monkeypatch.setattr(ContentSignal, "_resolve_version", lambda self, token: "ver-test")
    return ContentSignal()


def _boom(*args, **kwargs):  # any HTTP call here is a test failure
    raise AssertionError("HTTP call made when none was expected")


def _make_post(monkeypatch, *, upload_url="https://files.example/v.mp4", predict_payload=None):
    """Patch requests.post to fake the /files upload and /predictions create calls."""

    def fake_post(url, **kwargs):
        if url.endswith("/files"):
            return FakeResp({"id": "f1", "urls": {"get": upload_url}})
        if "/predictions" in url:
            return FakeResp(predict_payload)
        raise AssertionError(f"unexpected POST url: {url}")

    monkeypatch.setattr(requests, "post", fake_post)


# ----------------------------------------------------------------------
# Availability
# ----------------------------------------------------------------------


def test_status_available_with_token(monkeypatch):
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_x")
    assert ContentSignal().get_status() == ToolStatus.AVAILABLE


def test_status_unavailable_without_token(monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    assert ContentSignal().get_status() == ToolStatus.UNAVAILABLE


# ----------------------------------------------------------------------
# Guards — must run before any paid call
# ----------------------------------------------------------------------


def test_missing_token_no_http(monkeypatch, tmp_path):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    monkeypatch.setattr(requests, "post", _boom)
    res = ContentSignal().execute({"video_path": str(_dummy_video(tmp_path))})
    assert res.success is False
    assert "REPLICATE_API_TOKEN" in res.error


def test_missing_file_no_http(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    monkeypatch.setattr(requests, "post", _boom)
    res = tool.execute({"video_path": str(tmp_path / "nope.mp4")})
    assert res.success is False
    assert "not found" in res.error.lower()


def test_unsupported_format_no_http(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    monkeypatch.setattr(requests, "post", _boom)
    res = tool.execute({"video_path": str(_dummy_video(tmp_path, "clip.mkv"))})
    assert res.success is False
    assert "unsupported format" in res.error.lower()


def test_over_60s_skipped_no_http(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path, duration=90.0)
    monkeypatch.setattr(requests, "post", _boom)
    res = tool.execute({"video_path": str(_dummy_video(tmp_path))})
    assert res.success is False
    assert "60s" in res.error
    # advisory: the user's render is explicitly unaffected
    assert "unaffected" in res.error.lower()


def test_unprobed_duration_warns_and_proceeds(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path, duration=None)
    monkeypatch.setattr(ContentSignal, "_video_duration", lambda self, p: None)
    _make_post(monkeypatch, predict_payload={"status": "succeeded", "output": GOOD_OUTPUT, "version": "v1"})
    out = tmp_path / "report.json"
    res = tool.execute({"video_path": str(_dummy_video(tmp_path)), "output_path": str(out)})
    assert res.success is True
    assert any("duration" in w for w in res.data.get("warnings", []))


# ----------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------


def test_cache_hit_returns_cached_no_http(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    video = _dummy_video(tmp_path)
    sha = tool._sha256(video)
    cached = {
        "version": "1.0",
        "model": ContentSignal.MODEL_SLUG,
        "video_path": str(video),
        "headline_score": 42,
        "sub_scores": {"reward": 40},
        "timeline": [],
        "advisory": True,
    }
    cache_file = tool._cache_dir() / f"{sha}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cached))

    monkeypatch.setattr(requests, "post", _boom)  # cache hit must not call HTTP
    out = tmp_path / "report.json"
    res = tool.execute({"video_path": str(video), "output_path": str(out)})
    assert res.success is True
    assert res.data["cache_hit"] is True
    assert res.cost_usd == 0.0
    assert res.data["headline_score"] == 42
    assert out.exists()


def test_use_cache_false_skips_cache(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    video = _dummy_video(tmp_path)
    sha = tool._sha256(video)
    cache_file = tool._cache_dir() / f"{sha}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"headline_score": 1, "advisory": True}))
    _make_post(monkeypatch, predict_payload={"status": "succeeded", "output": GOOD_OUTPUT, "version": "v1"})
    out = tmp_path / "report.json"
    res = tool.execute({"video_path": str(video), "output_path": str(out), "use_cache": False})
    assert res.success is True
    assert res.data["headline_score"] == 78  # from the model, not the stale cache


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


def test_happy_path_writes_validated_report_and_cache(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    video = _dummy_video(tmp_path)
    _make_post(monkeypatch, predict_payload={"status": "succeeded", "output": GOOD_OUTPUT, "version": "ver-1"})
    out = tmp_path / "report.json"

    res = tool.execute({"video_path": str(video), "output_path": str(out)})

    assert res.success is True
    assert res.data["headline_score"] == 78
    assert res.data["advisory"] is True
    assert res.cost_usd > 0
    assert res.model == ContentSignal.MODEL_SLUG
    assert out.exists()

    report = json.loads(out.read_text())
    validate_artifact("content_signal_report", report)  # must be schema-valid
    assert report["headline_score"] == 78
    assert report["sub_scores"]["reward"] == 80
    assert report["video_sha256"] == tool._sha256(video)
    assert report["advisory"] is True

    # cached for next time, keyed by content hash
    assert (tool._cache_dir() / f"{tool._sha256(video)}.json").exists()


def test_polling_path_processing_then_succeeded(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    video = _dummy_video(tmp_path)
    monkeypatch.setattr("tools.analysis.content_signal.time.sleep", lambda s: None)

    def fake_post(url, **kwargs):
        if url.endswith("/files"):
            return FakeResp({"urls": {"get": "https://files.example/v.mp4"}})
        return FakeResp({"status": "processing", "urls": {"get": "https://api/pred/1"}})

    def fake_get(url, **kwargs):
        return FakeResp({"status": "succeeded", "output": GOOD_OUTPUT, "version": "ver-1"})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)

    out = tmp_path / "report.json"
    res = tool.execute({"video_path": str(video), "output_path": str(out)})
    assert res.success is True
    assert res.data["headline_score"] == 78


def test_uses_versioned_endpoint_for_community_model(monkeypatch, tmp_path):
    """Community models must run via /v1/predictions + a resolved version id, NOT the
    official-model /v1/models/{slug}/predictions shortcut (which 404s). This exercises
    the real _resolve_version path (no stub) and asserts the versioned endpoint + body."""
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_x")
    monkeypatch.setenv("OPENMONTAGE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("CONTENT_SIGNAL_AUTOCONFIRM", "1")
    monkeypatch.setattr(ContentSignal, "_video_duration", lambda self, p: 30.0)
    tool = ContentSignal()
    video = _dummy_video(tmp_path)
    posted: dict = {}

    def fake_get(url, **kwargs):
        assert url.endswith(f"/models/{ContentSignal.MODEL_SLUG}"), f"unexpected GET: {url}"
        return FakeResp({"latest_version": {"id": "ver-xyz"}})

    def fake_post(url, **kwargs):
        if url.endswith("/files"):
            return FakeResp({"urls": {"get": "https://files/v.mp4"}})
        assert url.endswith("/v1/predictions"), f"must use versioned endpoint, got: {url}"
        body = kwargs.get("json") or {}
        posted["version"] = body.get("version")
        posted["input"] = body.get("input")
        return FakeResp({"status": "succeeded", "output": GOOD_OUTPUT, "version": "ver-xyz"})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    out = tmp_path / "report.json"
    res = tool.execute({"video_path": str(video), "output_path": str(out)})
    assert res.success is True
    assert posted["version"] == "ver-xyz"
    assert posted["input"] == {"video": "https://files/v.mp4"}


# ----------------------------------------------------------------------
# Failure paths — never write a corrupt artifact
# ----------------------------------------------------------------------


def test_upload_failure_no_artifact(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)

    def fake_post(url, **kwargs):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(requests, "post", fake_post)
    out = tmp_path / "report.json"
    res = tool.execute({"video_path": str(_dummy_video(tmp_path)), "output_path": str(out)})
    assert res.success is False
    assert "upload failed" in res.error.lower()
    assert not out.exists()


def test_prediction_failed_status_no_artifact(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    _make_post(monkeypatch, predict_payload={"status": "failed", "error": "model boom"})
    out = tmp_path / "report.json"
    res = tool.execute({"video_path": str(_dummy_video(tmp_path)), "output_path": str(out)})
    assert res.success is False
    assert "failed" in res.error.lower()
    assert not out.exists()


def test_malformed_output_no_headline_no_artifact(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    _make_post(monkeypatch, predict_payload={"status": "succeeded", "output": {"foo": 1}, "version": "v1"})
    out = tmp_path / "report.json"
    res = tool.execute({"video_path": str(_dummy_video(tmp_path)), "output_path": str(out)})
    assert res.success is False
    assert "parse" in res.error.lower()
    assert not out.exists()


def test_out_of_range_score_fails_schema_no_artifact(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    bad = dict(GOOD_OUTPUT, headline_score=150)  # > 100, violates schema
    _make_post(monkeypatch, predict_payload={"status": "succeeded", "output": bad, "version": "v1"})
    out = tmp_path / "report.json"
    res = tool.execute({"video_path": str(_dummy_video(tmp_path)), "output_path": str(out)})
    assert res.success is False
    assert "schema" in res.error.lower()
    assert not out.exists()


# ----------------------------------------------------------------------
# dry_run + schema
# ----------------------------------------------------------------------


def test_dry_run_no_http(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    monkeypatch.setattr(requests, "post", _boom)
    info = tool.dry_run({"video_path": str(_dummy_video(tmp_path))})
    assert info["estimated_cost_usd"] > 0
    assert info["would_execute"] is True


def test_dry_run_cache_hit_is_free(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    monkeypatch.setattr(requests, "post", _boom)
    video = _dummy_video(tmp_path)
    sha = tool._sha256(video)
    cache_file = tool._cache_dir() / f"{sha}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"headline_score": 50, "advisory": True}))
    info = tool.dry_run({"video_path": str(video)})
    assert info["cache_hit"] is True
    assert info["estimated_cost_usd"] == 0.0
    assert info["requires_confirmation"] is False
    assert info["would_execute"] is True


def test_dry_run_unconfirmed_paid_run_requires_confirmation(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    monkeypatch.delenv("CONTENT_SIGNAL_AUTOCONFIRM", raising=False)
    monkeypatch.setattr(requests, "post", _boom)
    info = tool.dry_run({"video_path": str(_dummy_video(tmp_path))})
    assert info["cache_hit"] is False
    assert info["requires_confirmation"] is True
    assert info["estimated_cost_usd"] > 0
    assert info["would_execute"] is False


# ----------------------------------------------------------------------
# Token preflight — catch the .env inline-comment footgun before any upload
# ----------------------------------------------------------------------


def test_malformed_token_whitespace_no_http(monkeypatch, tmp_path):
    # A single space before "# comment" in .env leaks the comment into the value.
    tool = _tool(monkeypatch, tmp_path, token="r8_abcdef # Replicate token")
    monkeypatch.setattr(requests, "post", _boom)
    res = tool.execute({"video_path": str(_dummy_video(tmp_path))})
    assert res.success is False
    assert "malformed" in res.error.lower()
    assert "footgun" in res.error.lower()


def test_token_wrong_prefix_no_http(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path, token="sk-not-a-replicate-token")
    monkeypatch.setattr(requests, "post", _boom)
    res = tool.execute({"video_path": str(_dummy_video(tmp_path))})
    assert res.success is False
    assert "r8_" in res.error


def test_requests_missing_no_http(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    monkeypatch.setattr(ContentSignal, "_requests_available", staticmethod(lambda: False))
    monkeypatch.setattr(requests, "post", _boom)
    res = tool.execute({"video_path": str(_dummy_video(tmp_path))})
    assert res.success is False
    assert "requests" in res.error.lower()
    assert "venv" in res.error.lower()


# ----------------------------------------------------------------------
# Confirm-before-paid gate
# ----------------------------------------------------------------------


def test_confirm_gate_blocks_unconfirmed_paid_run(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    monkeypatch.delenv("CONTENT_SIGNAL_AUTOCONFIRM", raising=False)
    monkeypatch.setattr(requests, "post", _boom)  # must NOT upload/spend
    res = tool.execute({"video_path": str(_dummy_video(tmp_path))})
    assert res.success is False
    assert res.data["requires_confirmation"] is True
    assert res.data["estimated_cost_usd"] > 0
    assert "confirm" in res.error.lower()


def test_confirm_true_authorizes_paid_run(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    monkeypatch.delenv("CONTENT_SIGNAL_AUTOCONFIRM", raising=False)
    _make_post(monkeypatch, predict_payload={"status": "succeeded", "output": GOOD_OUTPUT, "version": "v1"})
    out = tmp_path / "report.json"
    res = tool.execute({"video_path": str(_dummy_video(tmp_path)), "output_path": str(out), "confirm": True})
    assert res.success is True
    assert res.data["headline_score"] == 78


def test_cache_hit_bypasses_confirm_gate(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    monkeypatch.delenv("CONTENT_SIGNAL_AUTOCONFIRM", raising=False)
    video = _dummy_video(tmp_path)
    sha = tool._sha256(video)
    cache_file = tool._cache_dir() / f"{sha}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"headline_score": 42, "advisory": True, "sub_scores": {}}))
    monkeypatch.setattr(requests, "post", _boom)
    res = tool.execute({"video_path": str(video), "output_path": str(tmp_path / "r.json")})
    assert res.success is True  # cached -> free -> no confirmation needed
    assert res.data["cache_hit"] is True


# ----------------------------------------------------------------------
# In-flight marker + resume (double-charge guard)
# ----------------------------------------------------------------------


def test_auto_resume_from_inflight_marker_no_upload(monkeypatch, tmp_path):
    """A marker for this file's hash -> poll the existing prediction, never re-upload/create."""
    tool = _tool(monkeypatch, tmp_path)
    video = _dummy_video(tmp_path)
    sha = tool._sha256(video)
    tool._write_inflight(sha, {"prediction_id": "pred-running", "video_sha256": sha})

    monkeypatch.setattr(requests, "post", _boom)  # no upload, no create

    def fake_get(url, **kwargs):
        assert url.endswith("/predictions/pred-running"), f"must resume by id, got {url}"
        return FakeResp({"status": "succeeded", "output": GOOD_OUTPUT, "version": "v1"})

    monkeypatch.setattr(requests, "get", fake_get)
    out = tmp_path / "report.json"
    res = tool.execute({"video_path": str(video), "output_path": str(out)})
    assert res.success is True
    assert res.data["headline_score"] == 78
    assert res.data.get("resumed") is True
    assert not tool._inflight_path(sha).exists()  # cleared on success


def test_explicit_resume_prediction_id_no_upload(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    monkeypatch.setattr(requests, "post", _boom)

    def fake_get(url, **kwargs):
        assert url.endswith("/predictions/pred-xyz")
        return FakeResp({"status": "succeeded", "output": GOOD_OUTPUT, "version": "v1"})

    monkeypatch.setattr(requests, "get", fake_get)
    out = tmp_path / "report.json"
    res = tool.execute(
        {"video_path": str(_dummy_video(tmp_path)), "output_path": str(out), "resume_prediction_id": "pred-xyz"}
    )
    assert res.success is True
    assert res.data["headline_score"] == 78
    assert any("cannot verify" in w for w in res.data.get("warnings", []))


def test_terminal_failure_clears_inflight_marker(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    video = _dummy_video(tmp_path)
    _make_post(monkeypatch, predict_payload={"status": "failed", "error": "boom", "id": "pred-f"})
    res = tool.execute({"video_path": str(video), "output_path": str(tmp_path / "r.json")})
    assert res.success is False
    sha = tool._sha256(video)
    assert not tool._inflight_path(sha).exists()  # failed -> forget the marker


def test_timeout_keeps_inflight_marker_for_resume(monkeypatch, tmp_path):
    tool = _tool(monkeypatch, tmp_path)
    video = _dummy_video(tmp_path)
    monkeypatch.setattr("tools.analysis.content_signal.time.sleep", lambda s: None)
    # start=1000, deadline=1000+max_wait, next check far past deadline -> timeout
    times = iter([1000.0, 1000.0, 1_000_000.0])
    monkeypatch.setattr("tools.analysis.content_signal.time.time", lambda: next(times, 1_000_000.0))

    def fake_post(url, **kwargs):
        if url.endswith("/files"):
            return FakeResp({"urls": {"get": "https://files/v.mp4"}})
        return FakeResp({"status": "processing", "id": "pred-t", "urls": {"get": "https://api/pred/t"}})

    def fake_get(url, **kwargs):
        return FakeResp({"status": "processing", "id": "pred-t", "urls": {"get": "https://api/pred/t"}})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    res = tool.execute({"video_path": str(video), "output_path": str(tmp_path / "r.json")})
    assert res.success is False
    assert "resume_prediction_id" in res.error
    sha = tool._sha256(video)
    assert tool._inflight_path(sha).exists()  # still running server-side -> keep marker


def test_schema_valid_and_invalid():
    valid = {
        "version": "1.0",
        "model": ContentSignal.MODEL_SLUG,
        "video_path": "x.mp4",
        "headline_score": 80,
        "sub_scores": {"reward": 70.0},
        "timeline": [{"t": 0.0, "reward": 70.0}],
        "advisory": True,
    }
    validate_artifact("content_signal_report", valid)  # should not raise

    import jsonschema

    missing_headline = dict(valid)
    missing_headline.pop("headline_score")
    with pytest.raises(jsonschema.ValidationError):
        validate_artifact("content_signal_report", missing_headline)
