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

import contextlib
import hashlib
import importlib.util
import json
import os
import re
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
    """Replicate prediction did not succeed (non-network failure).

    `terminal` distinguishes a prediction that reached a final bad state (failed/
    canceled — safe to forget the in-flight marker) from a client-side timeout
    (the prediction is likely still running server-side — KEEP the marker so a
    later call can auto-resume it instead of paying again).
    """

    def __init__(self, message: str, *, terminal: bool = True) -> None:
        super().__init__(message)
        self.terminal = terminal


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
    # Set this env var truthy (e.g. "1") to skip the interactive confirm gate for a
    # PAID run — for headless/batch pipelines. The gate exists because every fresh run
    # costs real money (~$0.44) and several minutes; in an interactive session the agent
    # must announce the cost and pass confirm=True instead of setting this.
    AUTOCONFIRM_ENV = "CONTENT_SIGNAL_AUTOCONFIRM"
    SUPPORTED_FORMATS = {".mp4", ".mov", ".webm"}
    _MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
    # Per-second hardware rate used to turn Replicate predict_time into a dollar cost.
    # IMPORTANT: this MUST match the GPU the model actually runs on (see the "Run time
    # and cost" box on the Replicate model page). The default is the Nvidia L40S list
    # rate; override it per-environment with CONTENT_SIGNAL_USD_PER_SEC when the model
    # runs on different hardware (A100/H100 are materially pricier). The Replicate API
    # exposes neither the hardware nor a per-prediction dollar figure, so this rate is
    # the one knob that has to be set correctly.
    _DEFAULT_USD_PER_S = 0.000975
    USD_PER_SEC_ENV = "CONTENT_SIGNAL_USD_PER_SEC"

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
                "description": (
                    "Where to write the report. Defaults next to the video (or "
                    "projects/<id>/artifacts/ when project_id is set). The canonical "
                    "name 'content_signal_report.json' is preferred, but the Mission "
                    "Control UI detects content-signal reports by their content shape, "
                    "not the filename, so re-scores under any name still render as the "
                    "interactive chart."
                ),
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
                "default": 900,
                "description": (
                    "Max time to wait for the Replicate prediction. This model is slow "
                    "(~7 min observed, up to ~575s; cold starts longer), so the default is generous. "
                    "If the wait elapses the prediction keeps running server-side — re-call with "
                    "use_cache=True (auto-resume) or resume_prediction_id rather than re-running."
                ),
            },
            "confirm": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Required to authorize a PAID run (a fresh, non-cached, non-resumed "
                    "prediction costs ~$0.44 and several minutes). Without it, execute() "
                    "returns a requires_confirmation result and spends nothing. Cache hits "
                    "and resumes never need it. The CONTENT_SIGNAL_AUTOCONFIRM env var "
                    "bypasses this for headless pipelines."
                ),
            },
            "resume_prediction_id": {
                "type": "string",
                "description": (
                    "Resume an already-created Replicate prediction by id instead of "
                    "uploading + creating a new one (avoids paying twice for a run that timed "
                    "out client-side). NOTE: a raw id cannot be verified against this file's "
                    "hash, so make sure it was created for this exact video."
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
        # PRE-RUN ESTIMATE ONLY. Real predict_time is highly variable (observed
        # 407s–1739s across runs — cold starts/queue dominate), so this is a rough
        # planning number for the confirmation gate. The actual cost written to the
        # report is computed from the prediction's real predict_time (see _finalize).
        return 450.0

    def _usd_per_sec(self) -> float:
        """Per-second hardware rate. Env override wins so an operator can set the
        exact rate from the Replicate model page without code changes."""
        raw = os.environ.get(self.USD_PER_SEC_ENV)
        if raw:
            try:
                v = float(raw)
                if v > 0:
                    return v
            except ValueError:
                pass
        return self._DEFAULT_USD_PER_S

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Rough pre-run estimate (rate × assumed runtime). Not the billed amount —
        # _finalize overwrites cost_usd with predict_time × rate after the run.
        return round(self._usd_per_sec() * self.estimate_runtime(inputs), 2)

    def dry_run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Preflight WITHOUT spending or uploading: answers 'what would happen and what
        would it cost?'. Reports whether the call would hit the cache (free), resume an
        in-flight prediction (free), or be a fresh paid run, plus token/format/duration
        validity. No HTTP. This is what an agent should call before a paid run."""
        info: dict[str, Any] = {
            "tool": self.name,
            "status": self.get_status().value,
            "estimated_runtime_seconds": self.estimate_runtime(inputs),
        }

        token = self._token()
        token_ok, token_reason = self._validate_token(token)
        info["token_ok"] = token_ok
        if not token_ok:
            info["token_issue"] = token_reason
        info["requests_available"] = self._requests_available()

        video_path = inputs.get("video_path")
        path = Path(video_path) if video_path else None
        if path is None or not path.exists():
            info.update(
                {
                    "video_exists": False,
                    "estimated_cost_usd": 0.0,
                    "requires_confirmation": False,
                    "would_execute": False,
                    "reason": "video_path missing or not found",
                }
            )
            return info
        info["video_exists"] = True

        suffix = path.suffix.lower()
        info["format_supported"] = suffix in self.SUPPORTED_FORMATS
        duration = self._video_duration(path)
        info["duration_seconds"] = duration
        info["within_duration_limit"] = duration is None or duration <= self.MAX_DURATION_S + 0.5

        sha = self._sha256(path)
        info["video_sha256"] = sha
        use_cache = inputs.get("use_cache", True)
        cache_hit = bool(use_cache and self._read_cache(sha) is not None)
        marker = self._read_inflight(sha)
        in_flight = bool(marker and marker.get("prediction_id"))
        resume_id = bool(inputs.get("resume_prediction_id"))

        info["cache_hit"] = cache_hit
        info["in_flight"] = in_flight
        if in_flight:
            info["in_flight_prediction_id"] = marker.get("prediction_id")

        free = cache_hit or in_flight or resume_id
        confirmed = self._is_confirmed(inputs)

        blockers: list[str] = []
        if not token_ok:
            blockers.append("invalid_token")
        if not info["requests_available"]:
            blockers.append("requests_not_installed")
        if not info["format_supported"]:
            blockers.append("unsupported_format")
        if not info["within_duration_limit"]:
            blockers.append("over_60s")

        info["estimated_cost_usd"] = 0.0 if free else self.estimate_cost(inputs)
        info["requires_confirmation"] = (not free) and not confirmed
        info["would_execute"] = (not blockers) and (free or confirmed)
        if blockers:
            info["blockers"] = blockers
        return info

    # ---- execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        # --- preflight: cheap checks that must run before any upload/spend ---
        token = self._token()
        ok, reason = self._validate_token(token)
        if not ok:
            return ToolResult(success=False, error=reason)

        if not self._requests_available():
            return ToolResult(
                success=False,
                error=(
                    "The 'requests' package is not importable in this interpreter, so "
                    "content_signal cannot reach Replicate. Run it with the project venv "
                    "(e.g. `.venv/bin/python ...`) — the system python typically lacks it. "
                    "Nothing was uploaded or spent."
                ),
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
        max_wait = int(inputs.get("max_wait_seconds", 900))

        # --- explicit resume by prediction id: skip cache/upload/create, just poll ---
        resume_id = inputs.get("resume_prediction_id")
        if resume_id:
            warnings.append(
                f"Resuming prediction {resume_id} by id — cannot verify it was created for "
                f"this exact file (sha {sha[:12]}…); ensure it matches."
            )
            start = time.time()
            try:
                output, version_id, predict_time = self._poll_existing(resume_id, token, max_wait)
            except _PredictionError as e:
                if getattr(e, "terminal", True):
                    self._clear_inflight(sha)
                return ToolResult(success=False, error=str(e))
            except Exception as e:
                return ToolResult(success=False, error=f"Replicate resume failed: {e}")
            return self._finalize(
                output, version_id, None, sha, path, inputs, duration, warnings, start,
                resumed=True, predict_time=predict_time,
            )

        # --- fast path: a cache hit needs no lock, no spend, no confirmation ---
        if use_cache:
            hit = self._cached_result(sha, path, inputs)
            if hit is not None:
                return hit

        start = time.time()
        version = None
        created_pred = None
        pred_id = None
        created_new = False

        # Serialize the cache/marker/create decision per file so two concurrent runs can't
        # both create (and pay for) a prediction for the same video.
        with self._sha_lock(sha):
            if use_cache:  # re-check: another process may have finished while we waited
                hit = self._cached_result(sha, path, inputs)
                if hit is not None:
                    return hit

            marker = self._read_inflight(sha)
            if marker and marker.get("prediction_id"):
                # A prediction for this exact file is already running — resume it, no new charge.
                pred_id = marker["prediction_id"]
                warnings.append(
                    f"Resumed in-flight prediction {pred_id} for this file — not re-charging."
                )
                created_new = False
            else:
                # Fresh PAID run — gate it (cost + time announced; nothing spent without consent).
                if not self._is_confirmed(inputs):
                    return ToolResult(
                        success=False,
                        error=(
                            f"Confirmation required: a fresh content_signal run costs "
                            f"~${self.estimate_cost(inputs):.2f} and ~{int(self.estimate_runtime(inputs))}s "
                            f"on Replicate (Meta TRIBE v2). Re-call with confirm=true, or set "
                            f"{self.AUTOCONFIRM_ENV}=1 for headless pipelines. Nothing was spent."
                        ),
                        data={
                            "requires_confirmation": True,
                            "estimated_cost_usd": self.estimate_cost(inputs),
                            "estimated_runtime_seconds": self.estimate_runtime(inputs),
                            "video_sha256": sha,
                            "cache_hit": False,
                        },
                    )
                try:
                    file_url = self._upload(path, token)
                except Exception as e:  # network / upload failure -> advisory skip
                    return ToolResult(success=False, error=f"Replicate file upload failed: {e}")
                try:
                    version = self._resolve_version(token)
                    created_pred = self._create_prediction(file_url, token, version)
                except _PredictionError as e:
                    return ToolResult(success=False, error=str(e))
                except Exception as e:  # network / 5xx
                    return ToolResult(success=False, error=f"Replicate prediction failed: {e}")
                pred_id = created_pred.get("id")
                created_new = True
                # Persist the in-flight marker BEFORE the long poll so a client-side
                # timeout/crash can resume this prediction instead of paying again.
                self._write_inflight(
                    sha,
                    {
                        "prediction_id": pred_id,
                        "video_sha256": sha,
                        "video_path": str(path),
                        "file_url": file_url,
                        "model": self.MODEL_SLUG,
                    },
                )

        # --- poll OUTSIDE the lock so a concurrent waiter can resume/poll the same prediction ---
        try:
            if created_new:
                output, version_id, predict_time = self._poll_prediction(created_pred, token, max_wait)
            else:
                output, version_id, predict_time = self._poll_existing(pred_id, token, max_wait)
        except _PredictionError as e:
            # Terminal failure -> forget the marker. Timeout -> KEEP it (still running; resumable).
            if getattr(e, "terminal", True):
                self._clear_inflight(sha)
            return ToolResult(success=False, error=str(e))
        except Exception as e:  # network / 5xx
            return ToolResult(success=False, error=f"Replicate prediction failed: {e}")

        return self._finalize(
            output, version_id, version, sha, path, inputs, duration, warnings, start,
            resumed=not created_new, predict_time=predict_time,
        )

    def _finalize(
        self,
        output: Any,
        version_id: Optional[str],
        resolved_version: Optional[str],
        sha: str,
        path: Path,
        inputs: dict[str, Any],
        duration: Optional[float],
        warnings: list[str],
        start: float,
        *,
        resumed: bool = False,
        predict_time: Optional[float] = None,
    ) -> ToolResult:
        """Parse -> validate -> write report + cache -> clear in-flight marker. Shared by the
        fresh-run, auto-resume, and explicit-resume paths so they can't drift apart."""
        parsed = self._parse_output(output)
        if parsed is None:
            return ToolResult(
                success=False,
                error=f"Could not parse a headline score from model output: {output!r}",
            )

        # Cost from the ACTUAL prediction time, not a fixed estimate. predict_time
        # varies 4x run-to-run, so a hardcoded number is wrong far more often than
        # right. Fall back to the pre-run estimate only if Replicate omitted metrics.
        rate = self._usd_per_sec()
        if isinstance(predict_time, (int, float)) and predict_time > 0:
            cost_usd = round(predict_time * rate, 2)
            warnings.append(
                f"cost_usd = predict_time {predict_time:.0f}s × ${rate}/s "
                f"(hardware-rate approx; set {self.USD_PER_SEC_ENV} to match the model's "
                f"GPU and reconcile with Replicate billing)."
            )
        else:
            cost_usd = self.estimate_cost(inputs)
            predict_time = None
            warnings.append(
                "cost_usd is a PRE-RUN ESTIMATE — Replicate returned no predict_time metric."
            )

        report = {
            "version": "1.0",
            "model": self.MODEL_SLUG,
            "model_version": parsed.get("model_version") or version_id or resolved_version,
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
            "cost_usd": cost_usd,
            "predict_time_s": predict_time,
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
        if inputs.get("use_cache", True):
            self._write_cache(sha, report)
        self._clear_inflight(sha)  # succeeded -> no longer in-flight

        data: dict[str, Any] = {
            "headline_score": report["headline_score"],
            "sub_scores": report["sub_scores"],
            "advisory": True,
            "cache_hit": False,
            "video_sha256": sha,
            "report_path": str(out_path),
            "weak_moment_hint": "Inspect `timeline` for response dips to consider re-editing.",
        }
        if resumed:
            data["resumed"] = True
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

    def _cached_result(
        self, sha: str, path: Path, inputs: dict[str, Any]
    ) -> Optional[ToolResult]:
        """If a report for this content hash is cached, write it to the output path and
        return a free (cost 0) ToolResult. Otherwise None."""
        cached = self._read_cache(sha)
        if cached is None:
            return None
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

    # ---- helpers ----

    def _token(self) -> Optional[str]:
        return os.environ.get("REPLICATE_API_TOKEN")

    def _validate_token(self, token: Optional[str]) -> tuple[bool, Optional[str]]:
        """Catch the failure modes that otherwise surface as a confusing 401 AFTER a 50MB
        upload — most notably the .env inline-comment footgun (a single space before
        `# comment` leaks the comment into the value)."""
        if not token:
            return False, "REPLICATE_API_TOKEN not set. " + self.install_instructions
        if re.search(r"\s", token) or "#" in token:
            return False, (
                "REPLICATE_API_TOKEN looks malformed — it contains whitespace or '#'. "
                "This is almost always the .env inline-comment footgun: a single space "
                "before '# comment' leaks the comment into the value (a 401 on every "
                "Replicate call). Use TWO spaces before the '#', or drop the comment. A "
                "valid token is 'r8_' + 37 chars with no whitespace."
            )
        if not token.startswith("r8_"):
            return False, (
                "REPLICATE_API_TOKEN does not start with 'r8_' — that does not look like a "
                "Replicate API token. Get one at https://replicate.com/account/api-tokens."
            )
        return True, None

    @staticmethod
    def _requests_available() -> bool:
        """Is `requests` importable in this interpreter? (System python often lacks it; the
        upload then dies mid-run with a cryptic ImportError.)"""
        return importlib.util.find_spec("requests") is not None

    def _is_confirmed(self, inputs: dict[str, Any]) -> bool:
        """A paid run is authorized by confirm=true or the headless autoconfirm env var."""
        if inputs.get("confirm") is True:
            return True
        return str(os.environ.get(self.AUTOCONFIRM_ENV, "")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    # ---- in-flight prediction tracking (double-charge guard) ----

    def _inflight_dir(self) -> Path:
        return self._cache_dir() / "inflight"

    def _inflight_path(self, sha: str) -> Path:
        return self._inflight_dir() / f"{sha}.json"

    def _read_inflight(self, sha: str) -> Optional[dict[str, Any]]:
        p = self._inflight_path(sha)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def _write_inflight(self, sha: str, data: dict[str, Any]) -> None:
        try:
            self._write_json(self._inflight_path(sha), data)
        except Exception:
            pass  # best-effort; losing the marker only risks a re-run, never corruption

    def _clear_inflight(self, sha: str) -> None:
        with contextlib.suppress(Exception):
            self._inflight_path(sha).unlink(missing_ok=True)

    @contextlib.contextmanager
    def _sha_lock(self, sha: str):
        """Best-effort per-file exclusive lock so two concurrent runs of the SAME video
        can't both create (and pay for) a prediction. POSIX `fcntl.flock`; degrades to a
        no-op where unavailable (e.g. Windows) — the in-flight marker still narrows the
        race even without the lock."""
        try:
            self._inflight_dir().mkdir(parents=True, exist_ok=True)
        except Exception:
            yield
            return
        try:
            import fcntl
        except ImportError:
            yield
            return
        lock_path = self._inflight_dir() / f"{sha}.lock"
        fh = open(lock_path, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(Exception):
                fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

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

    def _create_prediction(self, file_url: str, token: str, version: str) -> dict[str, Any]:
        """Create a prediction via the versioned (community-model) endpoint. Returns the
        initial prediction object (which may already be succeeded, or still processing)."""
        import requests

        resp = requests.post(
            f"{self.REPLICATE_BASE}/predictions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "wait",
            },
            json={"version": version, "input": {"video": file_url}},
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()

    def _poll_existing(
        self, prediction_id: str, token: str, max_wait: int
    ) -> tuple[Any, Optional[str]]:
        """Fetch an already-created prediction by id, then poll it to completion. Used to
        resume a run that timed out client-side without uploading or paying again."""
        import requests

        resp = requests.get(
            f"{self.REPLICATE_BASE}/predictions/{prediction_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code == 404:
            raise _PredictionError(
                f"Prediction {prediction_id} not found on Replicate (expired or wrong id).",
                terminal=True,
            )
        resp.raise_for_status()
        return self._poll_prediction(resp.json(), token, max_wait)

    def _poll_prediction(
        self, pred: dict[str, Any], token: str, max_wait: int
    ) -> tuple[Any, Optional[str]]:
        """Poll an in-flight prediction object until it reaches a terminal state."""
        import requests

        max_wait = max(30, min(max_wait, 1800))
        deadline = time.time() + max_wait
        poll_headers = {"Authorization": f"Bearer {token}"}
        while pred.get("status") in ("starting", "processing"):
            if time.time() > deadline:
                pid = pred.get("id")
                raise _PredictionError(
                    f"Replicate prediction timed out after {max_wait}s (last status "
                    f"{pred.get('status')}). This model can take 7+ minutes; prediction "
                    f"{pid} is likely still running — re-call content_signal with "
                    f"resume_prediction_id={pid!r} (or just rerun with use_cache=True; the "
                    f"in-flight marker auto-resumes it) rather than re-running, to avoid "
                    f"paying twice.",
                    terminal=False,  # still running server-side -> keep the marker
                )
            time.sleep(3)
            get_url = (pred.get("urls") or {}).get("get") or (
                f"{self.REPLICATE_BASE}/predictions/{pred.get('id')}" if pred.get("id") else None
            )
            if not get_url:
                raise _PredictionError("Replicate response missing poll URL.")
            poll = requests.get(get_url, headers=poll_headers, timeout=30)
            poll.raise_for_status()
            pred = poll.json()

        status = pred.get("status")
        if status != "succeeded":
            raise _PredictionError(f"Replicate prediction {status}: {pred.get('error')}")
        predict_time = (pred.get("metrics") or {}).get("predict_time")
        return pred.get("output"), pred.get("version"), predict_time

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
        # output_path is honored verbatim. The default name is canonical
        # (content_signal_report.json), which is preferred, but the Mission Control
        # UI no longer depends on the filename: it routes to the chart view by the
        # report's content shape (headline_score + sub_scores), so any filename and
        # any write path renders correctly. See web/src/App.jsx::ArtifactView.
        op = inputs.get("output_path")
        if op:
            return Path(op)
        pid = inputs.get("project_id")
        if pid:
            return Path("projects") / pid / "artifacts" / "content_signal_report.json"
        return path.parent / "content_signal_report.json"

    def _cache_dir(self) -> Path:
        base = os.environ.get("OPENNOLAN_CACHE_DIR") or (Path.home() / ".cache" / "opennolan")
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
