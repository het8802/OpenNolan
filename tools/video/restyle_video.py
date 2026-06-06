"""Restyle Video — AI video-to-video style transfer (Instagram Edits' "Restyle").

Transforms the visual style of a short clip while preserving its structure, driven by a
text prompt. Defaults to Luma's official `luma/modify-video` on Replicate (modes:
adhere = subtle, flex = stylistic, reimagine = dramatic), but the model is configurable so
you can point at any Replicate v2v model (official or community).

Design (Edits-parity Wave 5, /plan-eng-review):
  - ≤10s clip cap (matches Edits + controls cost) — rejected before any upload/spend.
  - PAID -> confirm=true gate (or RESTYLE_VIDEO_AUTOCONFIRM=1); announce provider + cost.
  - Cached by (video sha + prompt + mode + model) so an identical restyle never re-pays.
  - OFFICIAL vs COMMUNITY endpoint handled explicitly: luma/* is official
    (POST /v1/models/{slug}/predictions); a community model resolves its latest version and
    POSTs /v1/predictions (the content_signal pattern). Picked via `endpoint` (default auto:
    official for owner/name slugs that are known-official, else community-versioned).
  - Discoverable via video_selector (capability=video_generation).
  - Reuses the content_signal/.env footgun token guard and the object_cutout robustness
    (in-flight marker + lock against double-charge, defensive output parsing).
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


class RestyleVideo(BaseTool):
    name = "restyle_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "luma"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    DEFAULT_MODEL = "luma/modify-video"  # official; structure-preserving prompt restyle
    REPLICATE_BASE = "https://api.replicate.com/v1"
    MAX_DURATION_S = 10.0
    SUPPORTED_FORMATS = {".mp4", ".mov", ".webm"}
    _MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
    AUTOCONFIRM_ENV = "RESTYLE_VIDEO_AUTOCONFIRM"
    USD_PER_SEC_ENV = "RESTYLE_VIDEO_USD_PER_SEC"
    _DEFAULT_USD_PER_S = 0.04  # rough; override per the Replicate model page
    # owners we treat as official (official predictions endpoint, no version needed)
    _OFFICIAL_OWNERS = {"luma", "meta", "bytedance", "google", "openai", "stability-ai", "minimax"}

    dependencies = ["env:REPLICATE_API_TOKEN"]
    install_instructions = (
        "Set REPLICATE_API_TOKEN to your Replicate API token.\n"
        "  Get one at https://replicate.com/account/api-tokens\n"
        f"  Default model: https://replicate.com/{DEFAULT_MODEL}"
    )
    agent_skills = ["ai-video-gen"]

    capabilities = ["video_to_video", "style_transfer", "restyle"]
    supports = {"video_to_video": True, "prompt_driven": True, "max_duration_seconds": 10}
    best_for = [
        "restyling a short hero clip (≤10s) into a new look while keeping its motion/structure",
    ]
    not_good_for = [
        "clips longer than 10s (cap)",
        "tight loops — an API run takes tens of seconds and costs money",
    ]
    fallback_tools: list[str] = []

    input_schema = {
        "type": "object",
        "required": ["video_path", "prompt"],
        "properties": {
            "video_path": {"type": "string", "description": "Source clip (≤10s, mp4/mov/webm)."},
            "prompt": {"type": "string", "description": "Target style, e.g. 'claymation', 'cyberpunk neon'."},
            "mode": {
                "type": "string",
                "enum": ["adhere", "flex", "reimagine"],
                "default": "flex",
                "description": "luma/modify-video strength: adhere=subtle, flex=stylistic, reimagine=dramatic.",
            },
            "model_slug": {"type": "string", "description": f"Override the model (default {DEFAULT_MODEL})."},
            "endpoint": {
                "type": "string",
                "enum": ["auto", "official", "community"],
                "default": "auto",
                "description": "Replicate endpoint style. auto = official for known owners, else community-versioned.",
            },
            "video_input_key": {
                "type": "string",
                "default": "video",
                "description": "Input field name the model expects for the source video.",
            },
            "output_path": {"type": "string", "description": "Defaults to {stem}_restyled.mp4"},
            "use_cache": {"type": "boolean", "default": True},
            "max_wait_seconds": {"type": "integer", "default": 600},
            "confirm": {"type": "boolean", "default": False, "description": "Authorize a PAID run."},
            "resume_prediction_id": {"type": "string", "description": "Resume a prior prediction by id."},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=1024, network_required=True)
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["video_path", "prompt", "mode", "model_slug"]
    side_effects = ["uploads the clip to Replicate", "calls the Replicate API (paid)", "writes a restyled clip"]
    user_visible_verification = ["Watch the restyled clip; confirm structure is preserved and the style matches the prompt"]

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
        return 60.0

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

        video_path = inputs.get("video_path")
        if not video_path:
            return ToolResult(success=False, error="video_path is required.")
        path = Path(video_path)
        if not path.exists():
            return ToolResult(success=False, error=f"Video not found: {video_path}")
        if path.suffix.lower() not in self.SUPPORTED_FORMATS:
            return ToolResult(
                success=False,
                error=f"Unsupported format {path.suffix or '(none)'}. Accepts {sorted(self.SUPPORTED_FORMATS)}.",
            )
        prompt = inputs.get("prompt")
        if not prompt or not str(prompt).strip():
            return ToolResult(success=False, error="prompt (target style) is required.")

        # ≤10s cap — reject before any spend
        duration = self._video_duration(path)
        if duration is not None and duration > self.MAX_DURATION_S + 0.5:
            return ToolResult(
                success=False,
                error=(
                    f"Restyle is capped at {self.MAX_DURATION_S:.0f}s (matches Edits + controls cost); "
                    f"this clip is {duration:.1f}s. Trim it first (e.g. video_trimmer)."
                ),
            )

        model = inputs.get("model_slug") or self.DEFAULT_MODEL
        mode = inputs.get("mode", "flex")
        use_cache = inputs.get("use_cache", True)
        max_wait = int(inputs.get("max_wait_seconds", 600))
        sha = self._sha256(path)
        cache_key = self._cache_key(sha, prompt, mode, model)

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
            return self._finalize(url, path, inputs, model, mode, cache_key, start, resumed=True)

        # --- cache ---
        if use_cache:
            hit = self._cached_result(cache_key, inputs, path)
            if hit is not None:
                return hit

        start = time.time()
        created_pred = None
        pred_id = None
        created_new = False

        with self._key_lock(cache_key):
            if use_cache:
                hit = self._cached_result(cache_key, inputs, path)
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
                            f"Confirmation required: a fresh restyle calls {model} on Replicate "
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
                    file_url = self._upload(path, token)
                except Exception as e:
                    return ToolResult(success=False, error=f"Replicate file upload failed: {e}")
                try:
                    created_pred = self._create_prediction(file_url, prompt, mode, model, inputs, token)
                except _PredictionError as e:
                    return ToolResult(success=False, error=str(e))
                except Exception as e:
                    return ToolResult(success=False, error=f"Replicate prediction failed: {e}")
                pred_id = created_pred.get("id")
                created_new = True
                self._write_inflight(cache_key, {"prediction_id": pred_id, "video_sha256": sha, "model": model})

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

        return self._finalize(url, path, inputs, model, mode, cache_key, start, resumed=not created_new)

    def _finalize(
        self, url: str, path: Path, inputs: dict[str, Any], model: str, mode: str,
        cache_key: str, start: float, *, resumed: bool = False,
    ) -> ToolResult:
        out_path = Path(inputs.get("output_path") or path.with_name(f"{path.stem}_restyled.mp4"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._download(url, out_path)
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to download restyled video: {e}")

        cost = self.estimate_cost(inputs)
        record = {
            "output_path": str(out_path), "primary": str(out_path),
            "model": model, "mode": mode, "prompt": inputs.get("prompt"),
            "source": str(path), "cost_usd": cost,
        }
        if inputs.get("use_cache", True):
            self._write_cache(cache_key, record)
        self._clear_inflight(cache_key)

        data = dict(record)
        data["cache_hit"] = False
        if resumed:
            data["resumed"] = True
        return ToolResult(
            success=True, data=data, artifacts=[str(out_path)],
            cost_usd=cost, duration_seconds=round(time.time() - start, 2), model=model,
        )

    # ---- Replicate (official or community endpoint) ----

    def _is_official(self, model: str, endpoint: str) -> bool:
        if endpoint == "official":
            return True
        if endpoint == "community":
            return False
        owner = model.split("/", 1)[0] if "/" in model else ""
        return owner in self._OFFICIAL_OWNERS

    def _create_prediction(
        self, file_url: str, prompt: str, mode: str, model: str, inputs: dict[str, Any], token: str
    ) -> dict[str, Any]:
        import requests

        payload_input: dict[str, Any] = {
            inputs.get("video_input_key", "video"): file_url,
            "prompt": prompt,
        }
        if "modify-video" in model or model.startswith("luma/"):
            payload_input["mode"] = mode

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Prefer": "wait"}
        if self._is_official(model, inputs.get("endpoint", "auto")):
            resp = requests.post(
                f"{self.REPLICATE_BASE}/models/{model}/predictions",
                headers=headers, json={"input": payload_input}, timeout=120,
            )
        else:
            version = self._resolve_version(model, token)
            resp = requests.post(
                f"{self.REPLICATE_BASE}/predictions",
                headers=headers, json={"version": version, "input": payload_input}, timeout=120,
            )
        if resp.status_code == 422:
            raise _PredictionError(
                f"{model} rejected the input (422): {resp.text[:300]}. "
                f"Check prompt/mode and video_input_key (this model may use a different field name)."
            )
        resp.raise_for_status()
        return resp.json()

    def _resolve_version(self, model: str, token: str) -> str:
        import requests

        resp = requests.get(
            f"{self.REPLICATE_BASE}/models/{model}",
            headers={"Authorization": f"Bearer {token}"}, timeout=30,
        )
        resp.raise_for_status()
        version_id = ((resp.json() or {}).get("latest_version") or {}).get("id")
        if not version_id:
            raise _PredictionError(f"Could not resolve a version id for {model}.")
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
                    f"Restyle timed out after {max_wait}s. It may still be running — re-call with "
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
            raise _PredictionError(f"Restyle prediction {status}: {pred.get('error')}")
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
        raise _PredictionError(f"Could not find a restyled video URL in output: {output!r}")

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

    def _video_duration(self, path: Path) -> Optional[float]:
        try:
            from tools.video._shared import probe_output

            info = probe_output(path)
        except Exception:
            return None
        d = info.get("duration_seconds")
        return float(d) if isinstance(d, (int, float)) and d > 0 else None

    # ---- cache + in-flight ----

    def _cache_key(self, sha: str, prompt: str, mode: str, model: str) -> str:
        raw = json.dumps({"sha": sha, "prompt": prompt, "mode": mode, "model": model}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_dir(self) -> Path:
        base = os.environ.get("OPENMONTAGE_CACHE_DIR") or (Path.home() / ".cache" / "openmontage")
        return Path(base) / "restyle_video"

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

    def _cached_result(self, key: str, inputs: dict[str, Any], path: Path) -> Optional[ToolResult]:
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
