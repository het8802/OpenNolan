"""Content Signal — advisory virality score for a finished short-form render.

Wraps the community Replicate deployment of Meta's TRIBE v2 brain-encoding model
(`prakhar-bhartiya/meta-tribev2-social-media-content-signal`), which turns a video
into a predicted 0-100 virality headline score plus brain-region sub-scores and a
~2Hz per-step timeline.

Design contract (locked in /plan-eng-review, 2026-06-01):
  - ADVISORY ONLY. The score never blocks publishing. The model is new/unvalidated
    and CC-BY-NC ("not for high-stakes decisions"), so it must not gate anything.
  - SHORT-FORM ONLY. The model accepts <=60s clips. A duration guard stays here as
    defense-in-depth even though the tool is only wired into short-form pipelines.
  - DETERMINISTIC -> cached by file content hash so we never pay twice for one render.
  - Mirrors the repo's existing Replicate HTTP pattern (see tools/video/seedance_replicate.py):
    raw `requests` against the Replicate API, no `replicate` pip dependency.

Execution pipeline (see execute()):

    video_path
       │
       ▼
   [guard]  exists? supported format? duration <=60s?   ── fail ──► clear advisory error (no spend)
       │ ok
       ▼
   [cache]  sha256 seen before?  ── hit ──► return cached report (cost 0, no API call)
       │ miss
       ▼
   [upload] POST /v1/files (multipart) ──► served URL
       │
       ▼
   [predict] POST /v1/models/<slug>/predictions {input:{video:url}}  (Prefer: wait, then poll)
       │
       ▼
   [parse]  defensive: extract headline_score / sub_scores / timeline; keep raw_output
       │
       ▼
   [validate] against schemas/artifacts/content_signal_report.schema.json
       │ ok                                   │ invalid
       ▼                                       ▼
   write report + cache                   fail (NO corrupt artifact written)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)


class _PredictionError(Exception):
    """Replicate prediction did not succeed (non-network failure)."""


class ContentSignal(BaseTool):
    name = "content_signal"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "scoring"
    provider = "meta-tribev2"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.API

    MODEL_SLUG = "prakhar-bhartiya/meta-tribev2-social-media-content-signal"
    REPLICATE_BASE = "https://api.replicate.com/v1"
    MAX_DURATION_S = 60.0
    SUPPORTED_FORMATS = {".mp4", ".mov", ".webm"}
    _MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
    # Replicate L40S list rate; this model is too new to publish per-run pricing,
    # so estimate_cost() multiplies this by an assumed run duration. Refine once real.
    _L40S_USD_PER_S = 0.000975

    dependencies = ["env:REPLICATE_API_TOKEN"]
    install_instructions = (
        "Set REPLICATE_API_TOKEN to your Replicate API token.\n"
        "  Get one at https://replicate.com/account/api-tokens\n"
        f"  Model: https://replicate.com/{MODEL_SLUG}"
    )
    agent_skills: list[str] = []

    capabilities = ["predict_virality", "engagement_signal"]
    supports = {
        "short_form_only": True,
        "max_duration_seconds": 60,
        "deterministic": True,
        "advisory_only": True,
    }
    best_for = [
        "advisory virality/engagement signal on a FINISHED short-form render (<=60s)",
        "surfacing weak moments (per-step timeline) before publishing",
    ]
    not_good_for = [
        "videos longer than 60s",
        "any hard publish gate or high-stakes decision (model is new/unvalidated; CC-BY-NC)",
        "tight loops or fast feedback — a run takes several minutes (~7 min observed) and costs ~$0.40+",
    ]
    fallback_tools: list[str] = []
    # Measured on a real 55s clip (predict_time 435s). Helps the scoring engine/UX set expectations.
    latency_p50_seconds = 450.0

    input_schema = {
        "type": "object",
        "required": ["video_path"],
        "properties": {
            "video_path": {
                "type": "string",
                "description": "Path to the finished render to score (<=60s, mp4/mov/webm).",
            },
            "output_path": {
                "type": "string",
                "description": "Where to write content_signal_report.json. Defaults next to the video.",
            },
            "project_id": {
                "type": "string",
                "description": (
                    "Optional. If set and output_path omitted, writes to "
                    "projects/<id>/artifacts/content_signal_report.json."
                ),
            },
            "use_cache": {
                "type": "boolean",
                "default": True,
                "description": "Reuse a cached report for an identical file (model is deterministic).",
            },
            "max_wait_seconds": {
                "type": "integer",
                "default": 600,
                "description": (
                    "Max time to wait for the Replicate prediction. This model is slow "
                    "(~7 min observed; cold starts longer), so the default is generous."
                ),
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=200, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["video_path"]
    side_effects = [
        "uploads the render to Replicate file storage",
        "calls the Replicate API (paid)",
        "writes content_signal_report.json",
    ]
    user_visible_verification = [
        "Sanity-check the 0-100 headline score against your own read of the hook and pacing",
        "Remember the score is advisory and from a new, unvalidated model (CC-BY-NC)",
    ]

    # ---- cost / runtime estimation ----

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        # Observed ~435s on a real 55s clip (trimodal feature extraction is heavy);
        # cold starts can be longer. Keep this realistic so cost/UX expectations are honest.
        return 450.0

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return round(self._L40S_USD_PER_S * self.estimate_runtime(inputs), 2)

    # ---- execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        token = self._token()
        if not token:
            return ToolResult(
                success=False,
                error="REPLICATE_API_TOKEN not set. " + self.install_instructions,
            )

        video_path = inputs.get("video_path")
        if not video_path:
            return ToolResult(success=False, error="video_path is required.")
        path = Path(video_path)
        if not path.exists():
            return ToolResult(success=False, error=f"Video not found: {video_path}")

        suffix = path.suffix.lower()
        if suffix not in self.SUPPORTED_FORMATS:
            return ToolResult(
                success=False,
                error=(
                    f"Skipped: unsupported format {suffix or '(none)'}. "
                    f"content_signal accepts {sorted(self.SUPPORTED_FORMATS)}."
                ),
            )

        warnings: list[str] = []
        duration = self._video_duration(path)
        if duration is not None and duration > self.MAX_DURATION_S + 0.5:
            return ToolResult(
                success=False,
                error=(
                    f"Skipped: video is {duration:.1f}s; content_signal only scores "
                    f"short-form clips <=60s. (Advisory tool — your render is unaffected.)"
                ),
            )
        if duration is None:
            warnings.append(
                "Could not probe duration (ffprobe missing?); proceeding without the <=60s guard."
            )

        sha = self._sha256(path)
        use_cache = inputs.get("use_cache", True)
        if use_cache:
            cached = self._read_cache(sha)
            if cached is not None:
                cached["cache_hit"] = True
                out_path = self._resolve_output_path(inputs, path)
                self._write_json(out_path, cached)
                return ToolResult(
                    success=True,
                    data={
                        "headline_score": cached.get("headline_score"),
                        "sub_scores": cached.get("sub_scores", {}),
                        "advisory": True,
                        "cache_hit": True,
                        "video_sha256": sha,
                        "report_path": str(out_path),
                    },
                    artifacts=[str(out_path)],
                    cost_usd=0.0,
                    model=self.MODEL_SLUG,
                )

        start = time.time()
        try:
            file_url = self._upload(path, token)
        except Exception as e:  # network / upload failure -> advisory skip
            return ToolResult(success=False, error=f"Replicate file upload failed: {e}")

        try:
            version = self._resolve_version(token)
            output, version_id = self._predict(
                file_url, token, int(inputs.get("max_wait_seconds", 600)), version
            )
        except _PredictionError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:  # network / 5xx / timeout
            return ToolResult(success=False, error=f"Replicate prediction failed: {e}")

        parsed = self._parse_output(output)
        if parsed is None:
            return ToolResult(
                success=False,
                error=f"Could not parse a headline score from model output: {output!r}",
            )

        report = {
            "version": "1.0",
            "model": self.MODEL_SLUG,
            "model_version": parsed.get("model_version") or version_id,
            "scoring_version": parsed.get("scoring_version"),
            "provider": "replicate",
            "video_path": str(path),
            "video_sha256": sha,
            "video_duration_s": (
                parsed.get("video_duration_s")
                if parsed.get("video_duration_s") is not None
                else duration
            ),
            "frame_count": parsed.get("frame_count"),
            "headline_score": parsed["headline_score"],
            "sub_scores": parsed.get("sub_scores", {}),
            "timeline": parsed.get("timeline", []),
            "cost_usd": self.estimate_cost(inputs),
            "cache_hit": False,
            "advisory": True,
            "license_note": (
                "Model weights CC-BY-NC-4.0 (Meta TRIBE v2). Advisory only; "
                "not for high-stakes decisions."
            ),
            "raw_output": output,
            "warnings": warnings,
            "generated_at": None,
        }

        # Validate BEFORE writing — never persist a corrupt artifact from a new model.
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("content_signal_report", report)
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Model output did not validate against content_signal_report schema: {e}",
            )

        out_path = self._resolve_output_path(inputs, path)
        self._write_json(out_path, report)
        if use_cache:
            self._write_cache(sha, report)

        data: dict[str, Any] = {
            "headline_score": report["headline_score"],
            "sub_scores": report["sub_scores"],
            "advisory": True,
            "cache_hit": False,
            "video_sha256": sha,
            "report_path": str(out_path),
            "weak_moment_hint": "Inspect `timeline` for response dips to consider re-editing.",
        }
        if warnings:
            data["warnings"] = warnings
        return ToolResult(
            success=True,
            data=data,
            artifacts=[str(out_path)],
            cost_usd=report["cost_usd"],
            duration_seconds=round(time.time() - start, 2),
            model=self.MODEL_SLUG,
        )

    # ---- helpers ----

    def _token(self) -> Optional[str]:
        return os.environ.get("REPLICATE_API_TOKEN")

    def _video_duration(self, path: Path) -> Optional[float]:
        """Best-effort duration probe via the shared ffprobe helper."""
        try:
            from tools.video._shared import probe_output

            info = probe_output(path)
        except Exception:
            return None
        d = info.get("duration_seconds")
        return float(d) if isinstance(d, (int, float)) and d > 0 else None

    def _sha256(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _upload(self, path: Path, token: str) -> str:
        """Upload the local render to Replicate file storage; return its served URL."""
        import requests

        mime = self._MIME.get(path.suffix.lower(), "application/octet-stream")
        with open(path, "rb") as fh:
            resp = requests.post(
                f"{self.REPLICATE_BASE}/files",
                headers={"Authorization": f"Bearer {token}"},
                files={"content": (path.name, fh, mime)},
                timeout=180,
            )
        resp.raise_for_status()
        data = resp.json()
        url = (data.get("urls") or {}).get("get")
        if not url:
            raise RuntimeError(f"upload response missing serving url: {data}")
        return url

    def _resolve_version(self, token: str) -> str:
        """Resolve the model's latest version id.

        This is a COMMUNITY (non-official) Replicate model, so it must be run via the
        versioned endpoint (`POST /v1/predictions` with `{"version": ...}`). The
        `/v1/models/{slug}/predictions` shortcut is official-models-only and 404s here.
        """
        import requests

        resp = requests.get(
            f"{self.REPLICATE_BASE}/models/{self.MODEL_SLUG}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        version_id = ((resp.json() or {}).get("latest_version") or {}).get("id")
        if not version_id:
            raise _PredictionError("Could not resolve a model version id from Replicate.")
        return version_id

    def _predict(
        self, file_url: str, token: str, max_wait: int, version: str
    ) -> tuple[Any, Optional[str]]:
        """Create a prediction (versioned endpoint) and poll to completion."""
        import requests

        max_wait = max(30, min(max_wait, 1800))
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": "wait",
        }
        resp = requests.post(
            f"{self.REPLICATE_BASE}/predictions",
            headers=headers,
            json={"version": version, "input": {"video": file_url}},
            timeout=min(max_wait, 600),
        )
        resp.raise_for_status()
        pred = resp.json()

        deadline = time.time() + max_wait
        poll_headers = {"Authorization": f"Bearer {token}"}
        while pred.get("status") in ("starting", "processing"):
            if time.time() > deadline:
                pid = pred.get("id")
                raise _PredictionError(
                    f"Replicate prediction timed out after {max_wait}s (last status "
                    f"{pred.get('status')}). This model can take 7+ minutes; prediction "
                    f"{pid} is likely still running — retrieve it via "
                    f"GET /v1/predictions/{pid} rather than re-running (avoids paying twice)."
                )
            time.sleep(3)
            get_url = (pred.get("urls") or {}).get("get")
            if not get_url:
                raise _PredictionError("Replicate response missing poll URL.")
            poll = requests.get(get_url, headers=poll_headers, timeout=30)
            poll.raise_for_status()
            pred = poll.json()

        status = pred.get("status")
        if status != "succeeded":
            raise _PredictionError(f"Replicate prediction {status}: {pred.get('error')}")
        return pred.get("output"), pred.get("version") or version

    @staticmethod
    def _num(v: Any) -> Optional[float]:
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    def _parse_output(self, output: Any) -> Optional[dict[str, Any]]:
        """Defensively extract the report fields. Returns None if no headline score.

        The model is new; its output keys could drift. We anchor on the verified
        schema (headline_score / sub_scores / timeline / video_duration_s /
        frame_count / model_version / scoring_version) but fall back across a few
        candidate keys for the headline, and keep the raw output regardless.
        """
        if isinstance(output, list):
            output = output[0] if output and isinstance(output[0], dict) else {}
        if not isinstance(output, dict):
            return None

        headline = self._num(output.get("headline_score"))
        if headline is None:
            for k in ("score", "virality_score", "headline"):
                headline = self._num(output.get(k))
                if headline is not None:
                    break
        if headline is None:
            return None

        raw_subs = output.get("sub_scores") or output.get("subscores") or {}
        sub_scores: dict[str, float] = {}
        if isinstance(raw_subs, dict):
            for k, v in raw_subs.items():
                n = self._num(v)
                if n is not None:
                    sub_scores[k] = n

        timeline: list[dict[str, float]] = []
        raw_tl = output.get("timeline")
        if isinstance(raw_tl, list):
            for step in raw_tl:
                if isinstance(step, dict):
                    timeline.append(
                        {k: n for k, v in step.items() if (n := self._num(v)) is not None}
                    )

        fc = output.get("frame_count")
        return {
            "headline_score": headline,
            "sub_scores": sub_scores,
            "timeline": timeline,
            "video_duration_s": self._num(output.get("video_duration_s")),
            "frame_count": int(fc) if self._num(fc) is not None else None,
            "model_version": output.get("model_version")
            if isinstance(output.get("model_version"), str)
            else None,
            "scoring_version": output.get("scoring_version")
            if isinstance(output.get("scoring_version"), str)
            else None,
        }

    def _resolve_output_path(self, inputs: dict[str, Any], path: Path) -> Path:
        op = inputs.get("output_path")
        if op:
            return Path(op)
        pid = inputs.get("project_id")
        if pid:
            return Path("projects") / pid / "artifacts" / "content_signal_report.json"
        return path.parent / "content_signal_report.json"

    def _cache_dir(self) -> Path:
        base = os.environ.get("OPENMONTAGE_CACHE_DIR") or (Path.home() / ".cache" / "openmontage")
        return Path(base) / "content_signal"

    def _read_cache(self, sha: str) -> Optional[dict[str, Any]]:
        p = self._cache_dir() / f"{sha}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def _write_cache(self, sha: str, report: dict[str, Any]) -> None:
        try:
            self._write_json(self._cache_dir() / f"{sha}.json", report)
        except Exception:
            pass  # cache is best-effort

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)
