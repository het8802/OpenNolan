"""Keyframe Animate — emit a per-overlay motion timeline (overlays[].keyframes).

Instagram Edits' keyframe animation: make an overlay (a cutout, sticker, text, image) move,
scale, rotate, or fade over time. This is a SPEC EMITTER (eng-review decision): it produces
the `keyframes` list that lives inside an `overlays[]` item in edit_decisions. It does NOT
render — the compose stage's renderer interprets the keyframes.

`t` is ABSOLUTE project time in seconds (same axis as the overlay's start_seconds).

You can supply raw keyframes, or a high-level `preset` (slide_in_left, fade_in, pop,
ken_burns, ...) that expands into keyframes relative to the overlay's position/timing — so
an agent can say "slide the logo in from the left over 0.5s" without hand-authoring frames.

Design (Edits-parity Wave 2, /plan-eng-review):
  - Pure emitter: no ffmpeg, no rendering. Output validates against the edited
    edit_decisions schema (overlays[].keyframes).
  - Can attach the keyframes to an overlay in an existing edit_decisions artifact and write
    it back (validate-before-write).
  - NOTE: renderer support. The FFmpeg overlay path (video_compose._overlay /
    _keyframe_overlay) renders POSITION, SCALE (center-anchored, time-varying scale
    expressions), and OPACITY keyframes — including piecewise/non-monotonic opacity —
    with non-linear easings approximated by curve-sampled piecewise-linear subdivision.
    ROTATION keyframes are NOT rendered on the FFmpeg path (dropped with a warning);
    route rotation to Remotion/HyperFrames. This tool emits the spec regardless of
    which renderer is wired up.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)

_EASINGS = {"linear", "ease-in", "ease-out", "ease-in-out", "spring", "step"}
_PRESETS = (
    "slide_in_left", "slide_in_right", "slide_in_top", "slide_in_bottom",
    "slide_out_left", "slide_out_right",
    "fade_in", "fade_out", "pop", "pulse", "ken_burns",
)


class KeyframeAnimate(BaseTool):
    name = "keyframe_animate"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "video_post"
    provider = "keyframes"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies: list[str] = []  # pure spec emitter — no ffmpeg
    install_instructions = ""
    agent_skills: list[str] = []

    capabilities = ["keyframe_animation", "overlay_motion_presets"]
    supports = {"raw_keyframes": True, "presets": list(_PRESETS), "merge_into_edit_decisions": True}
    best_for = [
        "animating an overlay (cutout/text/sticker) over time — move, scale, rotate, fade",
        "expanding a high-level motion preset into keyframes on an overlay",
    ]
    not_good_for = [
        "rendering — this only emits the spec; compose renders it",
        "animating the main video track (overlays only)",
        "rotation keyframes on render_runtime='ffmpeg' — video_compose drops rotation "
        "with a warning (position/scale/opacity render); use Remotion/HyperFrames for rotation",
    ]
    fallback_tools: list[str] = []

    DEFAULT_PRESET_DURATION = 0.5
    DEFAULT_SLIDE_DISTANCE = 300.0

    input_schema = {
        "type": "object",
        "required": ["overlay"],
        "properties": {
            "overlay": {
                "type": "object",
                "description": "The base overlay to animate (must already have asset_id, start_seconds, end_seconds, position).",
            },
            "keyframes": {
                "type": "array",
                "description": "Raw keyframes [{t, x, y, scale, rotation, opacity, easing}]. Mutually exclusive with preset.",
                "items": {"type": "object"},
            },
            "preset": {"type": "string", "enum": list(_PRESETS), "description": "High-level motion that expands into keyframes."},
            "preset_params": {
                "type": "object",
                "description": "Tuning: duration (s), distance (px), from_scale, to_scale, easing.",
            },
            "edit_decisions_path": {
                "type": "string",
                "description": "Optional: attach the animated overlay to this edit_decisions (matched by overlay asset_id), validated + written back.",
            },
            "output_path": {"type": "string", "description": "Optional: write the animated overlay JSON here."},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=10)
    idempotency_key_fields = ["overlay", "keyframes", "preset"]
    side_effects = ["may write/merge an edit_decisions artifact or an overlay JSON"]
    user_visible_verification = ["Confirm the motion reads naturally once a renderer is wired to keyframes"]

    # ---- execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        overlay = inputs.get("overlay")
        if not isinstance(overlay, dict):
            return ToolResult(success=False, error="overlay (object) is required.")
        for req in ("asset_id", "start_seconds", "end_seconds", "position"):
            if req not in overlay:
                return ToolResult(success=False, error=f"overlay is missing required field {req!r}.")

        raw = inputs.get("keyframes")
        preset = inputs.get("preset")
        if raw and preset:
            return ToolResult(success=False, error="Provide either keyframes or preset, not both.")
        if not raw and not preset:
            return ToolResult(success=False, error="Provide keyframes or a preset.")

        try:
            if preset:
                keyframes = self._expand_preset(preset, overlay, inputs.get("preset_params") or {})
            else:
                keyframes = self._normalize_keyframes(raw)
        except _KeyframeError as e:
            return ToolResult(success=False, error=str(e))

        animated = dict(overlay)
        animated["keyframes"] = keyframes

        # validate by embedding in a throwaway edit_decisions and running the real validator
        err = self._validate_overlay(animated)
        if err:
            return ToolResult(success=False, error=err)

        data: dict[str, Any] = {"overlay": animated, "keyframes": keyframes, "n_keyframes": len(keyframes)}
        if preset:
            data["preset"] = preset
        artifacts: list[str] = []

        ed_path = inputs.get("edit_decisions_path")
        if ed_path:
            merge_err = self._merge_into_edit_decisions(Path(ed_path), animated)
            if merge_err:
                return ToolResult(success=False, error=merge_err, data=data)
            artifacts.append(str(ed_path))
            data["edit_decisions_path"] = str(ed_path)

        out_path = inputs.get("output_path")
        if out_path:
            self._write_json(Path(out_path), animated)
            artifacts.append(str(out_path))

        return ToolResult(success=True, data=data, artifacts=artifacts)

    # ---- keyframe building ----

    def _normalize_keyframes(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list) or not raw:
            raise _KeyframeError("keyframes must be a non-empty list.")
        out: list[dict[str, Any]] = []
        for i, kf in enumerate(raw):
            if not isinstance(kf, dict) or "t" not in kf:
                raise _KeyframeError(f"keyframes[{i}] must be an object with a 't' (seconds).")
            try:
                t = float(kf["t"])
            except (TypeError, ValueError):
                raise _KeyframeError(f"keyframes[{i}].t must be a number.")
            if t < 0:
                raise _KeyframeError(f"keyframes[{i}].t must be >= 0.")
            entry: dict[str, Any] = {"t": round(t, 4)}
            for k in ("x", "y", "scale", "rotation", "opacity"):
                if k in kf and kf[k] is not None:
                    entry[k] = float(kf[k])
            if "easing" in kf and kf["easing"] is not None:
                if kf["easing"] not in _EASINGS:
                    raise _KeyframeError(f"keyframes[{i}].easing {kf['easing']!r} not in {sorted(_EASINGS)}.")
                entry["easing"] = kf["easing"]
            out.append(entry)
        out.sort(key=lambda e: e["t"])
        return out

    def _expand_preset(self, preset: str, overlay: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]]:
        if preset not in _PRESETS:
            raise _KeyframeError(f"unknown preset {preset!r}; choose from {list(_PRESETS)}.")
        start = float(overlay["start_seconds"])
        end = float(overlay["end_seconds"])
        pos = overlay.get("position") or {}
        x = float(pos.get("x", 0))
        y = float(pos.get("y", 0))
        dur = float(params.get("duration", self.DEFAULT_PRESET_DURATION))
        dist = float(params.get("distance", self.DEFAULT_SLIDE_DISTANCE))
        easing = params.get("easing", "ease-out")
        if easing not in _EASINGS:
            raise _KeyframeError(f"preset_params.easing {easing!r} not in {sorted(_EASINGS)}.")
        if dur <= 0 or dur > (end - start) + 1e-6:
            # clamp the animation to the overlay's lifetime
            dur = max(0.05, min(dur, end - start))

        t0, t1 = round(start, 4), round(start + dur, 4)

        if preset.startswith("slide_in_"):
            sx, sy = x, y
            if preset == "slide_in_left": sx = x - dist
            elif preset == "slide_in_right": sx = x + dist
            elif preset == "slide_in_top": sy = y - dist
            elif preset == "slide_in_bottom": sy = y + dist
            return [
                {"t": t0, "x": sx, "y": sy, "opacity": 0.0, "easing": easing},
                {"t": t1, "x": x, "y": y, "opacity": 1.0},
            ]
        if preset.startswith("slide_out_"):
            te = round(end - dur, 4)
            ex = x - dist if preset == "slide_out_left" else x + dist
            return [
                {"t": te, "x": x, "y": y, "opacity": 1.0, "easing": "ease-in"},
                {"t": round(end, 4), "x": ex, "y": y, "opacity": 0.0},
            ]
        if preset == "fade_in":
            return [{"t": t0, "opacity": 0.0, "easing": easing}, {"t": t1, "opacity": 1.0}]
        if preset == "fade_out":
            return [{"t": round(end - dur, 4), "opacity": 1.0, "easing": "ease-in"}, {"t": round(end, 4), "opacity": 0.0}]
        if preset == "pop":
            mid = round(start + dur * 0.6, 4)
            return [
                {"t": t0, "scale": float(params.get("from_scale", 0.6)), "opacity": 0.0, "easing": "ease-out"},
                {"t": mid, "scale": float(params.get("to_scale", 1.1))},
                {"t": t1, "scale": 1.0, "opacity": 1.0},
            ]
        if preset == "pulse":
            mid = round((start + end) / 2, 4)
            return [
                {"t": t0, "scale": 1.0, "easing": "ease-in-out"},
                {"t": mid, "scale": float(params.get("to_scale", 1.08))},
                {"t": round(end, 4), "scale": 1.0},
            ]
        if preset == "ken_burns":
            return [
                {"t": round(start, 4), "scale": float(params.get("from_scale", 1.0)), "easing": "linear"},
                {"t": round(end, 4), "scale": float(params.get("to_scale", 1.15))},
            ]
        raise _KeyframeError(f"preset {preset!r} not implemented.")  # unreachable

    # ---- validation + merge ----

    def _validate_overlay(self, overlay: dict[str, Any]) -> Optional[str]:
        """Validate the animated overlay by embedding it in a minimal valid edit_decisions
        and running the canonical validator (so keyframes are checked against the real schema)."""
        doc = {
            "version": "1.0",
            "cuts": [{"id": "c0", "source": "placeholder", "in_seconds": 0, "out_seconds": 1}],
            "overlays": [overlay],
            "render_runtime": "remotion",
        }
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("edit_decisions", doc)
        except Exception as e:
            return f"animated overlay did not validate against edit_decisions schema: {e}"
        return None

    def _merge_into_edit_decisions(self, path: Path, animated: dict[str, Any]) -> Optional[str]:
        if not path.exists():
            return f"edit_decisions_path not found: {path}"
        try:
            doc = json.loads(path.read_text())
        except Exception as e:
            return f"could not read edit_decisions: {e}"
        if not isinstance(doc, dict):
            return "edit_decisions is not a JSON object."
        overlays = doc.get("overlays")
        if not isinstance(overlays, list):
            overlays = []
            doc["overlays"] = overlays
        # replace the overlay with the same asset_id, else append
        asset_id = animated.get("asset_id")
        for i, ov in enumerate(overlays):
            if isinstance(ov, dict) and ov.get("asset_id") == asset_id:
                overlays[i] = animated
                break
        else:
            overlays.append(animated)
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("edit_decisions", doc)
        except Exception as e:
            return f"merged edit_decisions did not validate: {e}"
        self._write_json(path, doc)
        return None

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)


class _KeyframeError(Exception):
    """Invalid keyframe input (caught and surfaced as a clean error)."""
