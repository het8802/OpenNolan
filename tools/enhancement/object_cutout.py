"""Object Cutout — SAM 2 video segmentation + tracking into an alpha cutout clip.

Wraps Meta's OFFICIAL Replicate model `meta/sam-2-video`. Given a video plus one or
more point prompts (positive/negative clicks on a frame), SAM 2 tracks the selected
object(s) across every frame and returns a mask video. This tool then composites the
source footage + that mask into an RGBA cutout clip (transparent background) via FFmpeg
`alphamerge`. This is the OpenMontage equivalent of Instagram Edits' "Cutouts".

Design contract (Edits-parity plan, /plan-eng-review 2026-06-06):
  - OFFICIAL model -> official endpoint `POST /v1/models/meta/sam-2-video/predictions`
    (NOT the community/versioned `content_signal` path; meta/* is official). See
    seedance_replicate.py for the same official-endpoint shape.
  - SAM 2 returns MASK video(s), NOT a composited RGBA clip. The mask->alpha step is
    real work done here with FFmpeg `alphamerge` (mask luminance becomes alpha).
  - DETERMINISTIC-ISH -> cached by (video sha256 + serialized prompt + mask_type) so an
    identical cutout request never re-pays the API.
  - PAID -> a fresh run is gated behind confirm=true (or OBJECT_CUTOUT_AUTOCONFIRM=1 for
    headless pipelines); cache hits are free and need no confirmation.
  - NO SILENT FALLBACK. If SAM 2 is unavailable, this tool does NOT quietly call
    bg_remove (that would violate the AGENT_GUIDE Decision Communication Contract). It
    surfaces a structured blocker naming bg_remove as a person-only, no-tracking
    alternative the agent/user must explicitly choose.
  - DETERMINISTIC TARGET: meta/sam-2-video needs explicit clicks — there is no reliable
    "auto" object pick here. If no points are given we fail loudly rather than guess the
    wrong subject on multi-person/multi-product shots.

Execution pipeline:

    video_path + points
       │
       ▼
   [guard]  exists? format ok? ffmpeg present? token valid? points given?  ── fail ──► clear error (no spend)
       │ ok
       ▼
   [downscale] if >1080p, scale down first (Replicate video models stall >1080p)
       │
       ▼
   [cache]  (sha + prompt + mask_type) seen?  ── hit ──► return cached cutout (cost 0)
       │ miss
       ▼
   [confirm]  paid run? require confirm=true (announce cost) ── unconfirmed ──► requires_confirmation result
       │ confirmed
       ▼
   [upload]   POST /v1/files ──► served URL     [inflight marker + lock guard double-charge]
       │
       ▼
   [predict]  POST /v1/models/meta/sam-2-video/predictions  (Prefer: wait, then poll)
       │
       ▼
   [download] fetch mask video
       │
       ▼
   [composite] ffmpeg alphamerge(source, mask) ──► RGBA .mov cutout  (+ keep raw mask)
       │
       ▼
   write outputs + cache
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

    `terminal` separates a prediction that reached a final bad state (failed/canceled —
    safe to forget the in-flight marker) from a client-side timeout (still running
    server-side — KEEP the marker so a later call can resume it instead of re-paying).
    """

    def __init__(self, message: str, *, terminal: bool = True) -> None:
        super().__init__(message)
        self.terminal = terminal


class ObjectCutout(BaseTool):
    name = "object_cutout"
    version = "0.1.0"
    tier = ToolTier.ENHANCE
    capability = "segmentation"
    provider = "meta-sam2"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.API

    MODEL_SLUG = "meta/sam-2-video"  # OFFICIAL model -> official predictions endpoint
    REPLICATE_BASE = "https://api.replicate.com/v1"
    SUPPORTED_FORMATS = {".mp4", ".mov", ".webm", ".mkv"}
    _MIME = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
    }
    MAX_DIM = 1080  # downscale longest side to this before upload (>1080p stalls the model)
    AUTOCONFIRM_ENV = "OBJECT_CUTOUT_AUTOCONFIRM"
    USD_PER_SEC_ENV = "OBJECT_CUTOUT_USD_PER_SEC"
    # Rough per-second hardware rate (override per Replicate model page). SAM 2 video is
    # far cheaper/faster than TRIBE-v2; this yields a few cents on a short clip.
    _DEFAULT_USD_PER_S = 0.0014

    dependencies = ["env:REPLICATE_API_TOKEN", "cmd:ffmpeg"]
    install_instructions = (
        "Set REPLICATE_API_TOKEN to your Replicate API token and install FFmpeg.\n"
        "  Token: https://replicate.com/account/api-tokens\n"
        "  FFmpeg: https://ffmpeg.org/download.html\n"
        f"  Model: https://replicate.com/{MODEL_SLUG}"
    )
    agent_skills = ["sam2-cutouts", "ffmpeg"]

    capabilities = ["object_segmentation", "object_tracking", "alpha_cutout"]
    supports = {
        "video_tracking": True,
        "multi_object": True,
        "positive_negative_clicks": True,
        "alpha_output": True,
        "deterministic": True,
    }
    best_for = [
        "isolating a tracked subject/object across a clip into a transparent cutout",
        "Instagram-Edits-style cutouts to overlay, keyframe, or restyle separately",
    ]
    not_good_for = [
        "auto subject pick with no clicks (meta/sam-2-video needs explicit point prompts)",
        "stills (use bg_remove for a single image)",
        "tight feedback loops — an API run takes ~30s+ and costs money",
    ]
    fallback_tools = ["bg_remove"]  # person-only, no tracking; agent must opt in explicitly
    latency_p50_seconds = 45.0

    input_schema = {
        "type": "object",
        "required": ["video_path", "points"],
        "properties": {
            "video_path": {
                "type": "string",
                "description": "Path to the source video (mp4/mov/webm/mkv).",
            },
            "points": {
                "type": "array",
                "description": (
                    "Click prompts that tell SAM 2 what to cut out. At least one positive "
                    "(label=1) click is required — there is no auto mode. Same object_id = "
                    "same object; different ids = separate tracked objects."
                ),
                "items": {
                    "type": "object",
                    "required": ["x", "y"],
                    "properties": {
                        "x": {"type": "number", "description": "X pixel in the frame"},
                        "y": {"type": "number", "description": "Y pixel in the frame"},
                        "label": {
                            "type": "integer",
                            "enum": [0, 1],
                            "default": 1,
                            "description": "1 = include (foreground), 0 = exclude (background)",
                        },
                        "frame": {
                            "type": "integer",
                            "default": 0,
                            "description": "Frame index this click refers to",
                        },
                        "object_id": {
                            "type": "string",
                            "default": "subject",
                            "description": "Group clicks into objects by id",
                        },
                    },
                },
            },
            "output_path": {
                "type": "string",
                "description": "Where to write the RGBA cutout (.mov). Defaults to {stem}_cutout.mov.",
            },
            "mask_type": {
                "type": "string",
                "enum": ["binary", "highlighted", "greenscreen"],
                "default": "binary",
                "description": (
                    "Mask style requested from SAM 2. 'binary' (white subject on black) is "
                    "required for the alpha composite; others are passed through for debugging."
                ),
            },
            "use_cache": {"type": "boolean", "default": True},
            "max_wait_seconds": {"type": "integer", "default": 600},
            "confirm": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Authorize a PAID run. Without it, a fresh (non-cached) request returns "
                    f"requires_confirmation and spends nothing. {AUTOCONFIRM_ENV}=1 bypasses "
                    "this for headless pipelines."
                ),
            },
            "resume_prediction_id": {
                "type": "string",
                "description": "Resume a prediction created earlier (avoids paying twice after a client timeout).",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=1024, vram_mb=0, disk_mb=1024, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["video_path", "points", "mask_type"]
    side_effects = [
        "uploads the source video to Replicate file storage",
        "calls the Replicate API (paid)",
        "writes an RGBA cutout .mov and the raw mask video",
    ]
    user_visible_verification = [
        "Scrub the cutout clip — the subject edge should stay clean across the whole shot",
        "If tracking drifts, add more positive/negative clicks on the frame where it breaks",
    ]

    # ---- cost / runtime ----

    def _usd_per_sec(self) -> float:
        raw = os.environ.get(self.USD_PER_SEC_ENV)
        if raw:
            try:
                v = float(raw)
                if v > 0:
                    return v
            except ValueError:
                pass
        return self._DEFAULT_USD_PER_S

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 45.0

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return round(self._usd_per_sec() * self.estimate_runtime(inputs), 2)

    # ---- execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        token = self._token()
        ok, reason = self._validate_token(token)
        if not ok:
            # SAM2 unavailable -> NAME the fallback, do NOT auto-swap (Decision Comm. Contract).
            return ToolResult(
                success=False,
                error=reason,
                data={
                    "fallback_available": "bg_remove",
                    "fallback_note": (
                        "bg_remove can remove the background of a single subject locally, but it is "
                        "person-only with NO cross-frame tracking. It is NOT a drop-in for a tracked "
                        "object cutout — invoke it explicitly only if that limitation is acceptable."
                    ),
                },
            )

        if not self._requests_available():
            return ToolResult(
                success=False,
                error=(
                    "The 'requests' package is not importable here, so object_cutout cannot reach "
                    "Replicate. Run with the project venv (e.g. `.venv/bin/python ...`). Nothing "
                    "was uploaded or spent."
                ),
            )

        import shutil as _shutil

        if _shutil.which("ffmpeg") is None:
            return ToolResult(
                success=False,
                error="ffmpeg not found on PATH — required to composite the mask into an alpha cutout.",
            )

        video_path = inputs.get("video_path")
        if not video_path:
            return ToolResult(success=False, error="video_path is required.")
        path = Path(video_path)
        if not path.exists():
            return ToolResult(success=False, error=f"Video not found: {video_path}")
        if path.suffix.lower() not in self.SUPPORTED_FORMATS:
            return ToolResult(
                success=False,
                error=(
                    f"Unsupported format {path.suffix or '(none)'}. "
                    f"object_cutout accepts {sorted(self.SUPPORTED_FORMATS)}."
                ),
            )

        points = inputs.get("points") or []
        prompt_err = self._validate_points(points)
        if prompt_err:
            return ToolResult(success=False, error=prompt_err)

        mask_type = inputs.get("mask_type", "binary")
        use_cache = inputs.get("use_cache", True)
        max_wait = int(inputs.get("max_wait_seconds", 600))
        sha = self._sha256(path)
        cache_key = self._cache_key(sha, points, mask_type)

        # --- explicit resume by prediction id ---
        resume_id = inputs.get("resume_prediction_id")
        if resume_id:
            start = time.time()
            try:
                mask_url = self._poll_existing(resume_id, token, max_wait)
            except _PredictionError as e:
                if getattr(e, "terminal", True):
                    self._clear_inflight(cache_key)
                return ToolResult(success=False, error=str(e))
            except Exception as e:
                return ToolResult(success=False, error=f"Replicate resume failed: {e}")
            return self._finalize(mask_url, path, inputs, points, mask_type, cache_key, start, resumed=True)

        # --- cache fast path ---
        if use_cache:
            hit = self._cached_result(cache_key, inputs, path)
            if hit is not None:
                return hit

        start = time.time()
        created_new = False
        pred_id = None
        created_pred = None

        with self._key_lock(cache_key):
            if use_cache:  # re-check after acquiring the lock
                hit = self._cached_result(cache_key, inputs, path)
                if hit is not None:
                    return hit

            marker = self._read_inflight(cache_key)
            if marker and marker.get("prediction_id"):
                pred_id = marker["prediction_id"]
                created_new = False
            else:
                if not self._is_confirmed(inputs):
                    return ToolResult(
                        success=False,
                        error=(
                            f"Confirmation required: a fresh object_cutout run calls "
                            f"meta/sam-2-video on Replicate (~${self.estimate_cost(inputs):.2f}, "
                            f"~{int(self.estimate_runtime(inputs))}s). Re-call with confirm=true or "
                            f"set {self.AUTOCONFIRM_ENV}=1 for headless pipelines. Nothing was spent."
                        ),
                        data={
                            "requires_confirmation": True,
                            "estimated_cost_usd": self.estimate_cost(inputs),
                            "estimated_runtime_seconds": self.estimate_runtime(inputs),
                            "cache_key": cache_key,
                        },
                    )
                # downscale (best-effort) before upload to keep the model from stalling >1080p
                upload_path = self._maybe_downscale(path)
                try:
                    file_url = self._upload(upload_path, token)
                except Exception as e:
                    return ToolResult(success=False, error=f"Replicate file upload failed: {e}")
                try:
                    created_pred = self._create_prediction(file_url, points, mask_type, token)
                except _PredictionError as e:
                    return ToolResult(success=False, error=str(e))
                except Exception as e:
                    return ToolResult(success=False, error=f"Replicate prediction failed: {e}")
                pred_id = created_pred.get("id")
                created_new = True
                self._write_inflight(
                    cache_key,
                    {"prediction_id": pred_id, "video_sha256": sha, "model": self.MODEL_SLUG},
                )

        # poll OUTSIDE the lock
        try:
            if created_new:
                mask_url = self._poll_prediction(created_pred, token, max_wait)
            else:
                mask_url = self._poll_existing(pred_id, token, max_wait)
        except _PredictionError as e:
            if getattr(e, "terminal", True):
                self._clear_inflight(cache_key)
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"Replicate prediction failed: {e}")

        return self._finalize(
            mask_url, path, inputs, points, mask_type, cache_key, start, resumed=not created_new
        )

    def _finalize(
        self,
        mask_url: str,
        path: Path,
        inputs: dict[str, Any],
        points: list[dict[str, Any]],
        mask_type: str,
        cache_key: str,
        start: float,
        *,
        resumed: bool = False,
    ) -> ToolResult:
        """Download the mask video, composite it into an RGBA cutout, write + cache outputs."""
        out_path = self._resolve_output_path(inputs, path)
        mask_path = out_path.with_name(f"{out_path.stem}_mask.mp4")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._download(mask_url, mask_path)
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to download SAM 2 mask video: {e}")

        # Composite: mask luminance -> alpha channel of the source. 'binary' mask required.
        composite_warning = None
        if mask_type == "binary":
            try:
                self._composite_alpha(path, mask_path, out_path)
            except Exception as e:
                return ToolResult(
                    success=False,
                    error=f"FFmpeg alpha composite failed: {e}",
                    data={"mask_path": str(mask_path)},
                )
            primary = out_path
        else:
            # Non-binary mask types can't be alphamerged into a clean cutout; return the
            # mask video itself and tell the agent why no alpha clip was produced.
            composite_warning = (
                f"mask_type={mask_type!r} is not 'binary', so no alpha cutout was composited — "
                f"returning the raw mask video. Re-run with mask_type='binary' for a transparent cutout."
            )
            primary = mask_path

        object_ids = sorted({str(p.get("object_id", "subject")) for p in points})
        cost = self.estimate_cost(inputs)
        record = {
            "cutout_path": str(out_path) if mask_type == "binary" else None,
            "mask_path": str(mask_path),
            "primary": str(primary),
            "model": self.MODEL_SLUG,
            "mask_type": mask_type,
            "object_ids": object_ids,
            "source": str(path),
            "cost_usd": cost,
        }
        if inputs.get("use_cache", True):
            self._write_cache(cache_key, record)
        self._clear_inflight(cache_key)

        data: dict[str, Any] = dict(record)
        data["cache_hit"] = False
        if resumed:
            data["resumed"] = True
        warnings = []
        if composite_warning:
            warnings.append(composite_warning)
        if warnings:
            data["warnings"] = warnings

        artifacts = [str(primary)]
        if mask_type == "binary":
            artifacts.append(str(mask_path))
        return ToolResult(
            success=True,
            data=data,
            artifacts=artifacts,
            cost_usd=cost,
            duration_seconds=round(time.time() - start, 2),
            model=self.MODEL_SLUG,
        )

    # ---- prompt handling ----

    @staticmethod
    def _validate_points(points: Any) -> Optional[str]:
        if not isinstance(points, list) or not points:
            return (
                "At least one point prompt is required — meta/sam-2-video has no auto mode. "
                "Pass points=[{x, y, label, frame, object_id}, ...] with at least one positive "
                "(label=1) click on the object to cut out."
            )
        has_positive = False
        for i, p in enumerate(points):
            if not isinstance(p, dict) or "x" not in p or "y" not in p:
                return f"points[{i}] must be an object with at least x and y."
            if p.get("label", 1) == 1:
                has_positive = True
        if not has_positive:
            return "At least one positive click (label=1) is required; all given points are exclusions."
        return None

    @staticmethod
    def _serialize_points(points: list[dict[str, Any]]) -> dict[str, str]:
        """Turn structured points into meta/sam-2-video's comma-string inputs."""
        coords = ",".join(f"[{int(round(p['x']))},{int(round(p['y']))}]" for p in points)
        labels = ",".join(str(int(p.get("label", 1))) for p in points)
        frames = ",".join(str(int(p.get("frame", 0))) for p in points)
        ids = ",".join(str(p.get("object_id", "subject")) for p in points)
        return {
            "click_coordinates": coords,
            "click_labels": labels,
            "click_frames": frames,
            "click_object_ids": ids,
        }

    # ---- Replicate (versioned endpoint) ----
    # Live testing showed meta/sam-2-video's official /v1/models/{slug}/predictions shortcut
    # 404s — it must be run via the VERSIONED endpoint (resolve latest_version -> POST
    # /v1/predictions with {version, input}), the content_signal community pattern.

    def _resolve_version(self, token: str) -> str:
        import requests

        resp = requests.get(
            f"{self.REPLICATE_BASE}/models/{self.MODEL_SLUG}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        version_id = ((resp.json() or {}).get("latest_version") or {}).get("id")
        if not version_id:
            raise _PredictionError(f"Could not resolve a version id for {self.MODEL_SLUG}.")
        return version_id

    def _create_prediction(
        self, file_url: str, points: list[dict[str, Any]], mask_type: str, token: str
    ) -> dict[str, Any]:
        import requests

        payload_input: dict[str, Any] = {"input_video": file_url, "mask_type": mask_type}
        payload_input.update(self._serialize_points(points))
        version = self._resolve_version(token)
        resp = requests.post(
            f"{self.REPLICATE_BASE}/predictions",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "wait",
            },
            json={"version": version, "input": payload_input},
            timeout=120,
        )
        if resp.status_code == 422:
            raise _PredictionError(
                f"meta/sam-2-video rejected the input (422): {resp.text[:300]}. "
                f"Check the click_* prompt fields and mask_type."
            )
        resp.raise_for_status()
        return resp.json()

    def _poll_existing(self, prediction_id: str, token: str, max_wait: int) -> str:
        import requests

        resp = requests.get(
            f"{self.REPLICATE_BASE}/predictions/{prediction_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code == 404:
            raise _PredictionError(
                f"Prediction {prediction_id} not found (expired or wrong id).", terminal=True
            )
        resp.raise_for_status()
        return self._poll_prediction(resp.json(), token, max_wait)

    def _poll_prediction(self, pred: dict[str, Any], token: str, max_wait: int) -> str:
        import requests

        max_wait = max(30, min(max_wait, 1800))
        deadline = time.time() + max_wait
        headers = {"Authorization": f"Bearer {token}"}
        while pred.get("status") in ("starting", "processing"):
            if time.time() > deadline:
                pid = pred.get("id")
                raise _PredictionError(
                    f"SAM 2 prediction timed out after {max_wait}s (status {pred.get('status')}). "
                    f"It may still be running — re-call with resume_prediction_id={pid!r} or "
                    f"use_cache=true (auto-resume) rather than re-running.",
                    terminal=False,
                )
            time.sleep(3)
            get_url = (pred.get("urls") or {}).get("get") or (
                f"{self.REPLICATE_BASE}/predictions/{pred.get('id')}" if pred.get("id") else None
            )
            if not get_url:
                raise _PredictionError("Replicate response missing poll URL.")
            poll = requests.get(get_url, headers=headers, timeout=30)
            poll.raise_for_status()
            pred = poll.json()

        status = pred.get("status")
        if status != "succeeded":
            raise _PredictionError(f"SAM 2 prediction {status}: {pred.get('error')}")
        return self._extract_mask_url(pred.get("output"))

    @staticmethod
    def _extract_mask_url(output: Any) -> str:
        """Defensively pull a mask-video URL out of the model output.

        meta/sam-2-video's output shape can be a single URL string, a list of URLs, or a
        dict (e.g. {"masks": [...], "combined": "..."}). We anchor on a combined mask when
        present, else the first URL we can find.
        """
        if isinstance(output, str):
            return output
        if isinstance(output, list):
            for item in output:
                if isinstance(item, str) and item.startswith("http"):
                    return item
        if isinstance(output, dict):
            for key in ("combined_mask", "combined", "mask", "masks", "output", "video"):
                val = output.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    return val
                if isinstance(val, list) and val and isinstance(val[0], str):
                    return val[0]
        raise _PredictionError(f"Could not find a mask video URL in SAM 2 output: {output!r}")

    def _upload(self, path: Path, token: str) -> str:
        import requests

        mime = self._MIME.get(path.suffix.lower(), "application/octet-stream")
        with open(path, "rb") as fh:
            resp = requests.post(
                f"{self.REPLICATE_BASE}/files",
                headers={"Authorization": f"Bearer {token}"},
                files={"content": (path.name, fh, mime)},
                timeout=300,
            )
        resp.raise_for_status()
        url = (resp.json().get("urls") or {}).get("get")
        if not url:
            raise RuntimeError(f"upload response missing serving url: {resp.json()}")
        return url

    def _download(self, url: str, dest: Path) -> None:
        import requests

        dest.parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(url, timeout=300)
        resp.raise_for_status()
        dest.write_bytes(resp.content)

    # ---- FFmpeg: mask -> alpha composite ----

    def _composite_alpha(self, source: Path, mask: Path, out_path: Path) -> None:
        """alphamerge the source with the (binary) mask -> RGBA .mov (qtrle keeps alpha).

        The mask's luminance becomes the alpha channel: white subject -> opaque,
        black background -> transparent.

        scale2ref resizes the mask to the SOURCE's exact dimensions first, so this stays
        correct even when _maybe_downscale shrank the uploaded video (the mask comes back
        at the downscaled size while we composite against the full-res original).
        """
        # [1:v] = mask, [0:v] = source. scale2ref scales input #1 to match input #2.
        filtergraph = (
            "[1:v][0:v]scale2ref=w=iw:h=ih[mask][src];"
            "[mask]format=gray[m];"
            "[src][m]alphamerge[out]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(source),
            "-i", str(mask),
            "-filter_complex", filtergraph,
            "-map", "[out]",
            "-c:v", "qtrle",  # lossless RGBA codec for .mov
            "-an",
            str(out_path),
        ]
        proc = self.run_command(cmd, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "ffmpeg failed").strip()[-500:])
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError("alphamerge produced no output file.")

    def _maybe_downscale(self, path: Path) -> Path:
        """If the longest side exceeds MAX_DIM, write a downscaled copy and upload that.

        Replicate video models stall on >1080p inputs (see reference_content_signal_input_prep).
        Best-effort: on any probe/scale failure we fall back to uploading the original.
        """
        try:
            from tools.video._shared import probe_output

            info = probe_output(path)
            w = int(info.get("width") or 0)
            h = int(info.get("height") or 0)
        except Exception:
            return path
        if not w or not h or max(w, h) <= self.MAX_DIM:
            return path
        scaled = path.with_name(f"{path.stem}_1080.mp4")
        # scale longest side to MAX_DIM, keep aspect, even dims
        vf = (
            f"scale='if(gt(iw,ih),{self.MAX_DIM},-2)':'if(gt(iw,ih),-2,{self.MAX_DIM})'"
        )
        cmd = ["ffmpeg", "-y", "-i", str(path), "-vf", vf, "-c:a", "copy", str(scaled)]
        try:
            proc = self.run_command(cmd, timeout=600)
            if proc.returncode == 0 and scaled.exists() and scaled.stat().st_size > 0:
                return scaled
        except Exception:
            pass
        return path

    # ---- token / env ----

    def _token(self) -> Optional[str]:
        return os.environ.get("REPLICATE_API_TOKEN")

    def _validate_token(self, token: Optional[str]) -> tuple[bool, Optional[str]]:
        """Catch the failure modes that otherwise surface as a confusing 401 after upload —
        especially the .env inline-comment footgun (single space before '# comment')."""
        if not token:
            return False, "REPLICATE_API_TOKEN not set. " + self.install_instructions
        if re.search(r"\s", token) or "#" in token:
            return False, (
                "REPLICATE_API_TOKEN looks malformed — it contains whitespace or '#'. This is "
                "almost always the .env inline-comment footgun: a single space before '# comment' "
                "leaks the comment into the value. Use TWO spaces before '#', or drop the comment."
            )
        if not token.startswith("r8_"):
            return False, (
                "REPLICATE_API_TOKEN does not start with 'r8_' — that does not look like a Replicate "
                "token. Get one at https://replicate.com/account/api-tokens."
            )
        return True, None

    @staticmethod
    def _requests_available() -> bool:
        return importlib.util.find_spec("requests") is not None

    def _is_confirmed(self, inputs: dict[str, Any]) -> bool:
        if inputs.get("confirm") is True:
            return True
        return str(os.environ.get(self.AUTOCONFIRM_ENV, "")).strip().lower() in (
            "1", "true", "yes", "on",
        )

    # ---- cache + in-flight (double-charge guard) ----

    def _cache_key(self, sha: str, points: list[dict[str, Any]], mask_type: str) -> str:
        raw = json.dumps(
            {"sha": sha, "points": points, "mask_type": mask_type, "model": self.MODEL_SLUG},
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_dir(self) -> Path:
        base = os.environ.get("OPENMONTAGE_CACHE_DIR") or (Path.home() / ".cache" / "openmontage")
        return Path(base) / "object_cutout"

    def _read_cache(self, key: str) -> Optional[dict[str, Any]]:
        p = self._cache_dir() / f"{key}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def _write_cache(self, key: str, record: dict[str, Any]) -> None:
        try:
            self._write_json(self._cache_dir() / f"{key}.json", record)
        except Exception:
            pass

    def _cached_result(
        self, key: str, inputs: dict[str, Any], path: Path
    ) -> Optional[ToolResult]:
        """Return a free ToolResult if a cached cutout exists AND its files are still present."""
        rec = self._read_cache(key)
        if rec is None:
            return None
        primary = rec.get("primary")
        if not primary or not Path(primary).exists():
            return None  # files were cleaned up — treat as miss so we regenerate
        data = dict(rec)
        data["cache_hit"] = True
        return ToolResult(
            success=True,
            data=data,
            artifacts=[primary],
            cost_usd=0.0,
            model=self.MODEL_SLUG,
        )

    def _inflight_dir(self) -> Path:
        return self._cache_dir() / "inflight"

    def _inflight_path(self, key: str) -> Path:
        return self._inflight_dir() / f"{key}.json"

    def _read_inflight(self, key: str) -> Optional[dict[str, Any]]:
        p = self._inflight_path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def _write_inflight(self, key: str, data: dict[str, Any]) -> None:
        try:
            self._write_json(self._inflight_path(key), data)
        except Exception:
            pass

    def _clear_inflight(self, key: str) -> None:
        with contextlib.suppress(Exception):
            self._inflight_path(key).unlink(missing_ok=True)

    @contextlib.contextmanager
    def _key_lock(self, key: str):
        """Best-effort per-request exclusive lock so two identical cutout calls can't both
        create (and pay for) a prediction. POSIX flock; no-op where unavailable."""
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
        fh = open(self._inflight_dir() / f"{key}.lock", "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(Exception):
                fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    # ---- misc ----

    def _sha256(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _resolve_output_path(self, inputs: dict[str, Any], path: Path) -> Path:
        op = inputs.get("output_path")
        if op:
            return Path(op)
        return path.with_name(f"{path.stem}_cutout.mov")

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)
