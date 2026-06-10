"""Lip Sync Photo — hosted photo+audio talking-head via Replicate (Edits' photo lip sync).

Animates a still face photo to speak a provided audio track, using a SadTalker community
model hosted on Replicate. This is the API-backed counterpart to the local `talking_head`
(SadTalker) and `lip_sync` (Wav2Lip) tools, which both require CUDA local installs and are
UNAVAILABLE on macOS hosts — this tool fills that gap with a hosted path.

Design (Edits-parity, mirrors restyle_video — the repo's Replicate exemplar):
  - COMMUNITY ENDPOINT GOTCHA (repo-validated): community models MUST be created via the
    versioned endpoint POST /v1/predictions with {"version": "<hash>"}. The
    /v1/models/{slug}/predictions route is for OFFICIAL models only and 404s for community
    ones. This tool always uses the versioned endpoint — there is no official SadTalker.
  - Default model is `cjwbw/sadtalker` (the well-known community SadTalker deployment).
    Both the model slug and version are configurable inputs. When no model_version is
    pinned, the latest version is resolved via GET /v1/models/{slug} at run time.
    VERSION ROT: pinned hashes go stale when the owner pushes a new version — if a pinned
    hash starts 404ing/422ing, re-pin from the model's Replicate page (or drop the pin to
    auto-resolve latest).
  - ≤60s audio cap — rejected before any upload/spend (cost control; trim narration first).
  - PAID -> confirm=true gate (or LIP_SYNC_PHOTO_AUTOCONFIRM=1); announce model + cost.
  - Cached by (image sha + audio sha + model + version + animation params) so an identical
    run never re-pays; in-flight marker + lock guard against double-charge.
  - Derived clip is re-probed (duration/resolution) and can be registered into an
    asset_manifest with provenance (the motion_ops pattern).
  - Field names mirror talking_head: image_path, audio_path, still_mode, preprocess,
    expression_scale. SadTalker deployments differ in accepted fields — expression_scale is
    only sent when explicitly set, and image_input_key/audio_input_key/extra_inputs let you
    adapt the payload to another deployment without code changes (a 422 says which knob).

Documented limitations:
  - Photo in, video out only — for syncing lips in an existing VIDEO use `lip_sync`.
  - Output is whatever the model emits (mp4, 8-bit SDR) — see not_good_for for the HDR rule.
  - Quality depends on the community deployment; results are stochastic, no seed control.
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
    def __init__(self, message: str, *, terminal: bool = True) -> None:
        super().__init__(message)
        self.terminal = terminal


class LipSyncPhoto(BaseTool):
    name = "lip_sync_photo"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "avatar"
    provider = "replicate"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    DEFAULT_MODEL = "cjwbw/sadtalker"  # community deployment; versioned endpoint REQUIRED
    REPLICATE_BASE = "https://api.replicate.com/v1"
    MAX_AUDIO_S = 60.0
    IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}
    AUDIO_FORMATS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}
    _MIME = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
        ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
    }
    AUTOCONFIRM_ENV = "LIP_SYNC_PHOTO_AUTOCONFIRM"
    USD_PER_SEC_ENV = "LIP_SYNC_PHOTO_USD_PER_SEC"
    _DEFAULT_USD_PER_S = 0.0014  # ~A100 40GB Replicate rate; override per the model page
    _FALLBACK_AUDIO_S = 30.0  # runtime estimate when audio duration can't be probed

    dependencies = ["env:REPLICATE_API_TOKEN"]
    install_instructions = (
        "Set REPLICATE_API_TOKEN to your Replicate API token.\n"
        "  Get one at https://replicate.com/account/api-tokens\n"
        f"  Default model: https://replicate.com/{DEFAULT_MODEL}"
    )
    agent_skills = ["avatar-video"]

    capabilities = [
        "photo_to_video",
        "face_animation",
        "audio_driven_animation",
        "lip_sync",
    ]
    supports = {"photo_to_video": True, "hosted": True, "max_audio_seconds": 60}
    best_for = [
        "photo+audio talking head on hosts without CUDA (the hosted SadTalker path)",
        "animating a still face photo to speak a narration or TTS track",
    ]
    not_good_for = [
        "syncing lips in an existing VIDEO — use lip_sync (Wav2Lip) for video+audio",
        "audio longer than 60s (cap — controls cost; trim or chunk the narration first)",
        "offline/free workflows — this is a paid hosted API; talking_head is the free local path",
        "HDR sources — output is 8-bit SDR; detect with is_hdr_source() and handle HDR per AGENT_GUIDE before using this tool",
    ]
    fallback_tools = ["talking_head", "lip_sync"]

    input_schema = {
        "type": "object",
        "required": ["image_path", "audio_path"],
        "properties": {
            "image_path": {"type": "string", "description": "Path to source still face photo (jpg/png/webp)."},
            "audio_path": {"type": "string", "description": "Path to driving audio file (≤60s)."},
            "output_path": {"type": "string", "description": "Output video path (default: {stem}_talking.mp4)"},
            "enhancer": {
                "type": "string",
                "enum": ["gfpgan", "RestoreFormer", "none"],
                "default": "gfpgan",
                "description": "Face enhancer passthrough ('none' omits the field).",
            },
            "still_mode": {
                "type": "boolean",
                "default": False,
                "description": "Only animate mouth, keep head still (model field 'still').",
            },
            "preprocess": {
                "type": "string",
                "enum": ["crop", "resize", "full"],
                "default": "crop",
                "description": "Face preprocessing mode passthrough.",
            },
            "expression_scale": {
                "type": "number",
                "description": "Expression intensity multiplier. Only sent when set — not every SadTalker deployment accepts it.",
            },
            "model_slug": {"type": "string", "description": f"Override the Replicate model (default {DEFAULT_MODEL})."},
            "model_version": {
                "type": "string",
                "description": (
                    "Pin a specific version hash. Default resolves the model's latest version via the API. "
                    "Pinned hashes rot when the owner pushes — re-pin if a pinned hash 404s/422s."
                ),
            },
            "image_input_key": {
                "type": "string",
                "default": "source_image",
                "description": "Input field name the model expects for the photo.",
            },
            "audio_input_key": {
                "type": "string",
                "default": "driven_audio",
                "description": "Input field name the model expects for the audio.",
            },
            "extra_inputs": {
                "type": "object",
                "description": "Extra model-specific input fields merged into the payload (e.g. pose_style).",
            },
            "use_cache": {"type": "boolean", "default": True},
            "max_wait_seconds": {"type": "integer", "default": 600},
            "confirm": {"type": "boolean", "default": False, "description": "Authorize a PAID run."},
            "resume_prediction_id": {"type": "string", "description": "Resume a prior prediction by id."},
            "asset_manifest_path": {
                "type": "string",
                "description": "Optional: append the generated clip to this asset_manifest (validated, written).",
            },
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=512, network_required=True)
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["image_path", "audio_path", "model_slug", "model_version", "enhancer", "still_mode", "preprocess"]
    side_effects = [
        "uploads the photo and audio to Replicate",
        "calls the Replicate API (paid)",
        "writes a talking-head video",
        "may append to an asset_manifest",
    ]
    user_visible_verification = [
        "Watch generated video for lip-sync accuracy",
        "Check for face distortion or unnatural artifacts",
    ]

    # ---- cost ----

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
        # SadTalker renders several x realtime on hosted GPUs + cold-start overhead.
        audio_s = None
        audio_path = inputs.get("audio_path")
        if audio_path and Path(audio_path).exists():
            audio_s = self._media_duration(Path(audio_path))
        if audio_s is None:
            audio_s = self._FALLBACK_AUDIO_S
        return 30.0 + 6.0 * audio_s

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return round(self._usd_per_sec() * self.estimate_runtime(inputs), 2)

    # ---- execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        token = self._token()
        ok, reason = self._validate_token(token)
        if not ok:
            return ToolResult(success=False, error=reason)
        if not self._requests_available():
            return ToolResult(
                success=False,
                error="The 'requests' package is not importable here; run with the project venv. Nothing was spent.",
            )

        image_path = inputs.get("image_path")
        if not image_path:
            return ToolResult(success=False, error="image_path is required.")
        img = Path(image_path)
        if not img.exists():
            return ToolResult(success=False, error=f"Image not found: {image_path}")
        if img.suffix.lower() not in self.IMAGE_FORMATS:
            return ToolResult(
                success=False,
                error=f"Unsupported image format {img.suffix or '(none)'}. Accepts {sorted(self.IMAGE_FORMATS)}.",
            )

        audio_path = inputs.get("audio_path")
        if not audio_path:
            return ToolResult(success=False, error="audio_path is required.")
        aud = Path(audio_path)
        if not aud.exists():
            return ToolResult(success=False, error=f"Audio not found: {audio_path}")
        if aud.suffix.lower() not in self.AUDIO_FORMATS:
            return ToolResult(
                success=False,
                error=f"Unsupported audio format {aud.suffix or '(none)'}. Accepts {sorted(self.AUDIO_FORMATS)}.",
            )

        # ≤60s cap — reject before any upload/spend
        audio_s = self._media_duration(aud)
        if audio_s is not None and audio_s > self.MAX_AUDIO_S + 0.5:
            return ToolResult(
                success=False,
                error=(
                    f"lip_sync_photo is capped at {self.MAX_AUDIO_S:.0f}s of audio (controls hosted-GPU cost); "
                    f"this track is {audio_s:.1f}s. Trim it first (e.g. audio via ffmpeg or voice_ops)."
                ),
            )

        model = inputs.get("model_slug") or self.DEFAULT_MODEL
        use_cache = inputs.get("use_cache", True)
        max_wait = int(inputs.get("max_wait_seconds", 600))
        cache_key = self._cache_key(self._sha256(img), self._sha256(aud), model, inputs)

        # --- resume ---
        resume_id = inputs.get("resume_prediction_id")
        if resume_id:
            start = time.time()
            try:
                url = self._poll_existing(resume_id, token, max_wait)
            except _PredictionError as e:
                if getattr(e, "terminal", True):
                    self._clear_inflight(cache_key)
                return ToolResult(success=False, error=str(e))
            except Exception as e:
                return ToolResult(success=False, error=f"Replicate resume failed: {e}")
            return self._finalize(url, img, aud, inputs, model, cache_key, start, resumed=True)

        # --- cache ---
        if use_cache:
            hit = self._cached_result(cache_key)
            if hit is not None:
                return hit

        start = time.time()
        created_pred = None
        pred_id = None
        created_new = False

        with self._key_lock(cache_key):
            if use_cache:
                hit = self._cached_result(cache_key)
                if hit is not None:
                    return hit
            marker = self._read_inflight(cache_key)
            if marker and marker.get("prediction_id"):
                pred_id = marker["prediction_id"]
            else:
                if not self._is_confirmed(inputs):
                    return ToolResult(
                        success=False,
                        error=(
                            f"Confirmation required: a fresh run calls {model} on Replicate "
                            f"(~${self.estimate_cost(inputs):.2f}, ~{int(self.estimate_runtime(inputs))}s). "
                            f"Re-call with confirm=true or set {self.AUTOCONFIRM_ENV}=1. Nothing was spent."
                        ),
                        data={
                            "requires_confirmation": True,
                            "estimated_cost_usd": self.estimate_cost(inputs),
                            "model": model,
                            "cache_key": cache_key,
                        },
                    )
                try:
                    image_url = self._upload(img, token)
                    audio_url = self._upload(aud, token)
                except Exception as e:
                    return ToolResult(success=False, error=f"Replicate file upload failed: {e}")
                try:
                    created_pred = self._create_prediction(image_url, audio_url, model, inputs, token)
                except _PredictionError as e:
                    return ToolResult(success=False, error=str(e))
                except Exception as e:
                    return ToolResult(success=False, error=f"Replicate prediction failed: {e}")
                pred_id = created_pred.get("id")
                created_new = True
                self._write_inflight(cache_key, {"prediction_id": pred_id, "model": model})

        try:
            if created_new:
                url = self._poll_prediction(created_pred, token, max_wait)
            else:
                url = self._poll_existing(pred_id, token, max_wait)
        except _PredictionError as e:
            if getattr(e, "terminal", True):
                self._clear_inflight(cache_key)
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"Replicate prediction failed: {e}")

        return self._finalize(url, img, aud, inputs, model, cache_key, start, resumed=not created_new)

    def _finalize(
        self, url: str, img: Path, aud: Path, inputs: dict[str, Any], model: str,
        cache_key: str, start: float, *, resumed: bool = False,
    ) -> ToolResult:
        out_path = Path(
            inputs.get("output_path") or img.with_stem(f"{img.stem}_talking").with_suffix(".mp4")
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._download(url, out_path)
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to download talking-head video: {e}")

        probed = self._probe(out_path)
        cost = self.estimate_cost(inputs)
        record = {
            "output_path": str(out_path), "primary": str(out_path), "output": str(out_path),
            "model": model, "model_version": inputs.get("model_version"),
            "image": str(img), "audio": str(aud),
            "enhancer": inputs.get("enhancer", "gfpgan"),
            "still_mode": inputs.get("still_mode", False),
            "preprocess": inputs.get("preprocess", "crop"),
            "format": "mp4", "cost_usd": cost,
        }
        if isinstance(probed.get("duration_seconds"), (int, float)):
            record["duration_seconds"] = round(float(probed["duration_seconds"]), 4)
        if probed.get("resolution"):
            record["resolution"] = probed["resolution"]
        if inputs.get("use_cache", True):
            self._write_cache(cache_key, record)
        self._clear_inflight(cache_key)

        data = dict(record)
        data["cache_hit"] = False
        if resumed:
            data["resumed"] = True

        am_path = inputs.get("asset_manifest_path")
        if am_path:
            reg_err = self._register_asset(Path(am_path), img, out_path, inputs, probed)
            if reg_err:
                data["asset_manifest_warning"] = reg_err
            else:
                data["asset_manifest_path"] = str(am_path)

        return ToolResult(
            success=True, data=data, artifacts=[str(out_path)],
            cost_usd=cost, duration_seconds=round(time.time() - start, 2), model=model,
        )

    # ---- Replicate (community models: versioned endpoint ONLY) ----

    def _build_payload(self, image_url: str, audio_url: str, inputs: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            inputs.get("image_input_key", "source_image"): image_url,
            inputs.get("audio_input_key", "driven_audio"): audio_url,
            "still": bool(inputs.get("still_mode", False)),
            "preprocess": inputs.get("preprocess", "crop"),
        }
        enhancer = inputs.get("enhancer", "gfpgan")
        if enhancer and enhancer != "none":
            payload["enhancer"] = enhancer
        # not every SadTalker deployment accepts expression_scale — only send when set
        if inputs.get("expression_scale") is not None:
            payload["expression_scale"] = inputs["expression_scale"]
        extra = inputs.get("extra_inputs")
        if isinstance(extra, dict):
            payload.update(extra)
        return payload

    def _create_prediction(
        self, image_url: str, audio_url: str, model: str, inputs: dict[str, Any], token: str
    ) -> dict[str, Any]:
        import requests

        # COMMUNITY GOTCHA: must POST /v1/predictions with {"version": ...}.
        # /v1/models/{slug}/predictions is official-models-only and 404s here.
        model_version = inputs.get("model_version") or self._resolve_version(model, token)
        resp = requests.post(
            f"{self.REPLICATE_BASE}/predictions",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Prefer": "wait"},
            json={"version": model_version, "input": self._build_payload(image_url, audio_url, inputs)},
            timeout=120,
        )
        if resp.status_code == 404:
            raise _PredictionError(
                f"Version {model_version!r} of {model} not found (404). Pinned version hashes rot when the "
                f"owner pushes — re-pin model_version from https://replicate.com/{model} or drop it to auto-resolve."
            )
        if resp.status_code == 422:
            raise _PredictionError(
                f"{model} rejected the input (422): {resp.text[:300]}. "
                f"This deployment may use different field names — adjust image_input_key/audio_input_key "
                f"or pass deployment-specific fields via extra_inputs."
            )
        resp.raise_for_status()
        return resp.json()

    def _resolve_version(self, model: str, token: str) -> str:
        import requests

        resp = requests.get(
            f"{self.REPLICATE_BASE}/models/{model}",
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        if resp.status_code == 404:
            raise _PredictionError(f"Model {model} not found on Replicate (404). Check model_slug.")
        resp.raise_for_status()
        version_id = ((resp.json() or {}).get("latest_version") or {}).get("id")
        if not version_id:
            raise _PredictionError(
                f"Could not resolve a version id for {model}. Pin one explicitly via model_version "
                f"(from https://replicate.com/{model})."
            )
        return version_id

    def _poll_existing(self, prediction_id: str, token: str, max_wait: int) -> str:
        import requests

        resp = requests.get(
            f"{self.REPLICATE_BASE}/predictions/{prediction_id}",
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        if resp.status_code == 404:
            raise _PredictionError(f"Prediction {prediction_id} not found (expired/wrong id).", terminal=True)
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
                    f"lip_sync_photo timed out after {max_wait}s. It may still be running — re-call with "
                    f"resume_prediction_id={pid!r} or use_cache=true. Not re-charging.",
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
            raise _PredictionError(f"lip_sync_photo prediction {status}: {pred.get('error')}")
        return self._extract_video_url(pred.get("output"))

    @staticmethod
    def _extract_video_url(output: Any) -> str:
        if isinstance(output, str):
            return output
        if isinstance(output, list):
            for item in output:
                if isinstance(item, str) and item.startswith("http"):
                    return item
        if isinstance(output, dict):
            for key in ("video", "output", "url"):
                v = output.get(key)
                if isinstance(v, str) and v.startswith("http"):
                    return v
        raise _PredictionError(f"Could not find a talking-head video URL in output: {output!r}")

    def _upload(self, path: Path, token: str) -> str:
        import requests

        mime = self._MIME.get(path.suffix.lower(), "application/octet-stream")
        with open(path, "rb") as fh:
            resp = requests.post(
                f"{self.REPLICATE_BASE}/files",
                headers={"Authorization": f"Bearer {token}"},
                files={"content": (path.name, fh, mime)}, timeout=300,
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

    # ---- token / env (shared footgun guard) ----

    def _token(self) -> Optional[str]:
        return os.environ.get("REPLICATE_API_TOKEN")

    def _validate_token(self, token: Optional[str]) -> tuple[bool, Optional[str]]:
        if not token:
            return False, "REPLICATE_API_TOKEN not set. " + self.install_instructions
        if re.search(r"\s", token) or "#" in token:
            return False, (
                "REPLICATE_API_TOKEN looks malformed (whitespace or '#') — likely the .env "
                "inline-comment footgun (single space before '# comment'). Use TWO spaces or drop the comment."
            )
        if not token.startswith("r8_"):
            return False, "REPLICATE_API_TOKEN does not start with 'r8_'."
        return True, None

    @staticmethod
    def _requests_available() -> bool:
        return importlib.util.find_spec("requests") is not None

    def _is_confirmed(self, inputs: dict[str, Any]) -> bool:
        if inputs.get("confirm") is True:
            return True
        return str(os.environ.get(self.AUTOCONFIRM_ENV, "")).strip().lower() in ("1", "true", "yes", "on")

    def _media_duration(self, path: Path) -> Optional[float]:
        d = self._probe(path).get("duration_seconds")
        return float(d) if isinstance(d, (int, float)) and d > 0 else None

    def _probe(self, path: Path) -> dict[str, Any]:
        try:
            from tools.video._shared import probe_output

            return probe_output(path)
        except Exception:
            return {}

    # ---- asset_manifest registration (the motion_ops pattern) ----

    def _register_asset(
        self, path: Path, img: Path, out: Path, inputs: dict[str, Any], probed: dict[str, Any],
    ) -> Optional[str]:
        """Append the generated clip to an asset_manifest with provenance, validate, write back.
        Returns an error string on failure (manifest left untouched), else None."""
        if not path.exists():
            return f"asset_manifest_path not found: {path}"
        try:
            doc = json.loads(path.read_text())
        except Exception as e:
            return f"could not read asset_manifest: {e}"
        if not isinstance(doc, dict) or not isinstance(doc.get("assets"), list):
            return "asset_manifest is not a valid manifest object with an assets[] list."

        entry = {
            "id": f"lipsyncphoto-{len(doc['assets']) + 1}",
            "type": "video",
            "path": str(out),
            "source_tool": "lip_sync_photo",
            "scene_id": str(inputs.get("scene_id", "derived")),
            "subtype": "talking_head",
            "generation_summary": (
                f"lip_sync_photo via {inputs.get('model_slug') or self.DEFAULT_MODEL} "
                f"from {img.name} + {Path(inputs.get('audio_path', '')).name}"
            ),
            "format": out.suffix.lstrip(".") or "mp4",
        }
        if isinstance(probed.get("duration_seconds"), (int, float)):
            entry["duration_seconds"] = round(float(probed["duration_seconds"]), 4)
        if probed.get("resolution"):
            entry["resolution"] = probed["resolution"]
        doc["assets"].append(entry)
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("asset_manifest", doc)
        except Exception as e:
            return f"generated-asset entry did not validate against asset_manifest schema: {e}"
        self._write_json(path, doc)
        return None

    # ---- cache + in-flight ----

    def _cache_key(self, image_sha: str, audio_sha: str, model: str, inputs: dict[str, Any]) -> str:
        raw = json.dumps(
            {
                "image_sha": image_sha, "audio_sha": audio_sha,
                "model": model, "version": inputs.get("model_version"),
                "enhancer": inputs.get("enhancer", "gfpgan"),
                "still_mode": bool(inputs.get("still_mode", False)),
                "preprocess": inputs.get("preprocess", "crop"),
                "expression_scale": inputs.get("expression_scale"),
                "extra_inputs": inputs.get("extra_inputs"),
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_dir(self) -> Path:
        base = os.environ.get("OPENNOLAN_CACHE_DIR") or (Path.home() / ".cache" / "opennolan")
        return Path(base) / "lip_sync_photo"

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

    def _cached_result(self, key: str) -> Optional[ToolResult]:
        rec = self._read_cache(key)
        if rec is None:
            return None
        primary = rec.get("primary")
        if not primary or not Path(primary).exists():
            return None
        data = dict(rec)
        data["cache_hit"] = True
        return ToolResult(success=True, data=data, artifacts=[primary], cost_usd=0.0, model=rec.get("model"))

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

    def _sha256(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)
