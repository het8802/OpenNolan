"""Color grading tool wrapping FFmpeg LUT and filter chains.

Ops (Edits parity: Adjustments + Curves + saved Looks):
  - profile (default, legacy): built-in preset profiles, external .cube LUT
    (lut_path), or raw custom_vf. Behavior unchanged: custom_vf and a bare
    lut_path bypass the intensity blend (pre-existing quirk kept for
    compatibility — use op=adjust with lut_path for an intensity-blended LUT).
  - adjust: parametric controls, all optional and composable in ONE call:
    brightness/contrast/saturation/gamma -> eq; temperature -> colortemperature
    when the installed ffmpeg has it (detected once via `ffmpeg -filters` and
    cached per process), else a colorbalance warm/cool midtone shift;
    tint -> colorbalance green/magenta; sharpness -> unsharp (negative blurs);
    vignette -> vignette. Composable with lut_path.
    Filter order: lut3d -> temperature/tint -> eq -> unsharp -> vignette.
  - curves: points per channel {master|red|green|blue: [[x, y], ...]} in 0..1
    -> ffmpeg curves (master is ffmpeg's second-pass master mapping), plus
    lift/gamma/gain-style wheels {shadows|midtones|highlights: {r, g, b}}
    offsets in [-1, 1] -> colorbalance.
  - auto: one-shot luma auto-correct. Samples N frames via signalstats and
    computes (simple documented heuristic, luma-only):
        spread     = max(1, YMAX - YMIN)             observed 8-bit luma range
        contrast   = clamp(219 / spread, 1.0, 2.0)   expand toward 16-235; never reduces
        brightness = clamp((128 - (contrast*(YAVG-128) + 128)) / 255, -0.5, 0.5)
    (eq scales contrast about mid-gray THEN adds brightness, so the brightness
    term compensates the post-contrast mean toward 128). Applied via the
    adjust path.

intensity (0..1) blends the WHOLE graded result against the original
(split + blend=all_mode=normal — the existing profile mechanism): 0 = original,
1 = full grade. Applies to adjust/curves/auto and to profile presets. NOTE:
blend's all_opacity weights the FIRST (original) input, so opacity is
1 - intensity; the previous code passed intensity directly, silently inverting
the blend (intensity=0.85 produced 85% ORIGINAL) — fixed in 0.2.0 for both the
new ops and the legacy preset path. Because existing op=profile callers may have
tuned intensity against the old inverted output, profile calls with
0 < intensity < 1 also return data["intensity_warning"] explaining the change
and the value (1 - intensity) that reproduces the pre-0.2.0 look.

Saved looks: save_look=true + look_name persists the RESOLVED params as JSON
under assets/looks/<name>.json (slug-validated name, atomic write). auto saves
its computed values as an `adjust` look. look="name" loads one: the saved op is
used (an explicit conflicting op errors) and explicitly-passed params override
the loaded values. looks_dir overrides the directory (used by tests).

Limitations (documented, not silent):
  - auto is luma-only; no white-balance or color-cast correction.
  - temperature fallback is a fixed +/-0.3 colorbalance midtone shift and tint
    is always a colorbalance approximation — neither is Kelvin-accurate.
  - wheels are colorbalance offsets, not true ASC CDL lift/gamma/gain math.
  - Output is 8-bit SDR (libx264 yuv420p path) — see not_good_for.
"""

from __future__ import annotations

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
    ToolResult,
    ToolStability,
    ToolTier,
)


# Built-in grading profiles using FFmpeg colorbalance/curves/eq filters
PROFILES = {
    "cinematic_warm": {
        "description": "Warm cinematic look with lifted shadows and orange highlights",
        "vf": (
            "colorbalance=rs=0.08:gs=0.02:bs=-0.05:rh=0.06:gh=0.02:bh=-0.04,"
            "curves=all='0/0.03 0.25/0.22 0.5/0.50 0.75/0.78 1/0.97',"
            "eq=contrast=1.05:saturation=1.1"
        ),
    },
    "cinematic_cool": {
        "description": "Cool teal-and-orange cinematic grade",
        "vf": (
            "colorbalance=rs=-0.02:gs=-0.03:bs=0.08:rh=0.06:gh=-0.02:bh=-0.06,"
            "curves=all='0/0.02 0.25/0.20 0.5/0.48 0.75/0.78 1/0.98',"
            "eq=contrast=1.08:saturation=1.05"
        ),
    },
    "moody_dark": {
        "description": "Crushed blacks, desaturated midtones, dark atmosphere",
        "vf": (
            "curves=all='0/0.05 0.15/0.12 0.5/0.45 0.85/0.82 1/0.95',"
            "eq=contrast=1.12:saturation=0.8:brightness=-0.03"
        ),
    },
    "bright_clean": {
        "description": "Bright, clean look with lifted shadows and vivid color",
        "vf": (
            "curves=all='0/0.05 0.25/0.30 0.5/0.55 0.75/0.80 1/1.0',"
            "eq=contrast=1.0:saturation=1.15:brightness=0.02"
        ),
    },
    "vintage_film": {
        "description": "Faded film look with grain texture and warm tint",
        "vf": (
            "colorbalance=rs=0.06:gs=0.03:bs=-0.03:ms=0.03:mh=-0.02,"
            "curves=all='0/0.06 0.25/0.25 0.5/0.50 0.75/0.74 1/0.94',"
            "eq=saturation=0.85:contrast=0.95"
        ),
    },
    "high_contrast": {
        "description": "Punchy high-contrast grade for dynamic content",
        "vf": (
            "curves=all='0/0 0.20/0.12 0.5/0.50 0.80/0.88 1/1',"
            "eq=contrast=1.2:saturation=1.1"
        ),
    },
    "neutral": {
        "description": "Minimal correction — normalize levels and light contrast",
        "vf": "eq=contrast=1.02:saturation=1.02:brightness=0.01",
    },
}

DEFAULT_LOOKS_DIR = Path(__file__).resolve().parents[2] / "assets" / "looks"


class ColorGrade(BaseTool):
    name = "color_grade"
    version = "0.2.0"
    tier = ToolTier.CORE
    capability = "enhancement"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = ["cmd:ffmpeg"]
    install_instructions = "Install FFmpeg: https://ffmpeg.org/download.html"
    agent_skills = ["ffmpeg"]

    OPERATIONS = ("profile", "adjust", "curves", "auto")
    ADJUST_RANGES = {
        "brightness": (-1.0, 1.0),
        "contrast": (0.0, 3.0),
        "saturation": (0.0, 3.0),
        "gamma": (0.1, 10.0),
        "temperature": (-100.0, 100.0),
        "tint": (-100.0, 100.0),
        "sharpness": (-1.0, 1.0),
        "vignette": (0.0, 1.0),
    }
    CURVE_CHANNELS = ("master", "red", "green", "blue")
    WHEEL_BANDS = ("shadows", "midtones", "highlights")
    LOOK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
    TEMP_FALLBACK_SHIFT = 0.3  # colorbalance midtone shift at temperature/tint = +/-100
    AUTO_SAMPLE_DEFAULT = 5
    AUTO_TARGET_MID = 128.0    # mid-gray target for mean luma (8-bit)
    AUTO_TARGET_RANGE = 219.0  # 16-235 video range
    AUTO_CONTRAST_MAX = 2.0
    AUTO_BRIGHTNESS_MAX = 0.5

    # `ffmpeg -filters` output, probed once per process (None = not probed yet)
    _available_filters: Optional[frozenset] = None

    capabilities = [
        "grade_preset",
        "grade_lut",
        "grade_custom",
        "adjust",
        "curves",
        "auto_correct",
        "saved_looks",
    ]
    best_for = [
        "preset cinematic grades, .cube LUTs, parametric Edits-style adjustments",
        "per-channel curves + shadows/midtones/highlights color wheels",
        "one-shot luma auto-correct toward mid-gray and full video range",
        "saving and reusing looks across clips (assets/looks/*.json)",
    ]
    not_good_for = [
        "HDR sources — output is 8-bit SDR; detect with is_hdr_source() and handle HDR per AGENT_GUIDE before using this tool",
        "auto white balance / color-cast removal — op=auto is luma-only",
    ]

    input_schema = {
        "type": "object",
        "required": ["input_path"],
        "properties": {
            "input_path": {"type": "string"},
            "output_path": {"type": "string"},
            "op": {
                "type": "string",
                "enum": list(OPERATIONS),
                "description": (
                    "profile (default; legacy presets/LUT/custom_vf), adjust "
                    "(parametric), curves (points + wheels), auto (luma auto-correct)"
                ),
            },
            "profile": {
                "type": "string",
                "enum": list(PROFILES.keys()),
                "default": "cinematic_warm",
            },
            "lut_path": {
                "type": "string",
                "description": "Path to external .cube LUT file (op=profile or op=adjust)",
            },
            "intensity": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 1.0,
                "description": "Blend intensity: 0 = original, 1 = full grade",
            },
            "custom_vf": {"type": "string"},
            # op=adjust (all optional, composable)
            "brightness": {"type": "number", "minimum": -1, "maximum": 1,
                           "description": "adjust: additive luma shift (eq)"},
            "contrast": {"type": "number", "minimum": 0, "maximum": 3,
                         "description": "adjust: contrast about mid-gray (eq)"},
            "saturation": {"type": "number", "minimum": 0, "maximum": 3,
                           "description": "adjust: saturation (eq)"},
            "gamma": {"type": "number", "minimum": 0.1, "maximum": 10,
                      "description": "adjust: gamma (eq)"},
            "temperature": {"type": "number", "minimum": -100, "maximum": 100,
                            "description": "adjust: +warm/-cool; colortemperature when available, else colorbalance fallback"},
            "tint": {"type": "number", "minimum": -100, "maximum": 100,
                     "description": "adjust: +magenta/-green (colorbalance)"},
            "sharpness": {"type": "number", "minimum": -1, "maximum": 1,
                          "description": "adjust: unsharp amount; negative blurs"},
            "vignette": {"type": "number", "minimum": 0, "maximum": 1,
                         "description": "adjust: vignette strength"},
            # op=curves
            "points": {
                "type": "object",
                "description": ("curves: per-channel [[x, y], ...] points in 0..1 "
                                "(x strictly increasing, >= 2 points per channel)"),
                "additionalProperties": False,
                "properties": {
                    ch: {
                        "type": "array",
                        "minItems": 2,
                        "items": {"type": "array", "minItems": 2, "maxItems": 2,
                                  "items": {"type": "number", "minimum": 0, "maximum": 1}},
                    }
                    for ch in ("master", "red", "green", "blue")
                },
            },
            "wheels": {
                "type": "object",
                "description": ("curves: lift/gamma/gain-style {r, g, b} offsets in "
                                "[-1, 1] per band (colorbalance)"),
                "additionalProperties": False,
                "properties": {
                    band: {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {c: {"type": "number", "minimum": -1, "maximum": 1}
                                       for c in ("r", "g", "b")},
                    }
                    for band in ("shadows", "midtones", "highlights")
                },
            },
            # op=auto
            "sample_frames": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5,
                              "description": "auto: frames sampled via signalstats"},
            # saved looks
            "look": {"type": "string",
                     "description": "Load a saved look by slug name; explicit params override"},
            "save_look": {"type": "boolean", "default": False,
                          "description": "Persist the resolved adjust/curves params as look_name"},
            "look_name": {"type": "string", "description": "Slug name for save_look"},
            "looks_dir": {"type": "string",
                          "description": "Override the looks directory (default assets/looks; mainly for tests)"},
            "codec": {"type": "string", "default": "libx264"},
            "crf": {"type": "integer", "default": 20},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=2, ram_mb=1024, vram_mb=0, disk_mb=2000)
    idempotency_key_fields = [
        "input_path", "op", "profile", "lut_path", "intensity",
        "brightness", "contrast", "saturation", "gamma",
        "temperature", "tint", "sharpness", "vignette",
        "points", "wheels", "sample_frames", "look",
    ]
    side_effects = [
        "writes graded video to output_path",
        "save_look writes a look JSON under the looks directory",
    ]
    user_visible_verification = [
        "Compare graded output with original for color accuracy",
        "Verify skin tones look natural, not oversaturated",
    ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        src = inputs.get("input_path")
        if not src:
            return ToolResult(success=False, error="input_path is required")
        input_path = Path(src)
        if not input_path.exists():
            return ToolResult(success=False, error=f"Input not found: {input_path}")

        output_path = Path(
            inputs.get("output_path", str(input_path.with_stem(f"{input_path.stem}_graded")))
        )
        codec = inputs.get("codec", "libx264")
        crf = inputs.get("crf", 20)

        resolved: dict[str, Any] = {}
        auto_info: Optional[dict[str, Any]] = None
        try:
            op, look_doc = self._resolve_op(inputs)
            if op == "profile":
                vf = self._build_filter(inputs)
                if not vf:
                    return ToolResult(success=False, error="No profile, lut_path, or custom_vf specified")
            else:
                intensity = self._validate_intensity(inputs.get("intensity", 1.0))
                if op == "adjust":
                    params = self._merge_look_params(inputs, look_doc, tuple(self.ADJUST_RANGES))
                    resolved = self._validate_adjust(params, allow_empty=bool(inputs.get("lut_path")))
                    core = self._build_adjust_vf(resolved, inputs.get("lut_path"))
                elif op == "curves":
                    params = self._merge_look_params(inputs, look_doc, ("points", "wheels"))
                    resolved, core = self._build_curves_vf(params)
                else:  # auto
                    measured = self._auto_measure(
                        input_path, inputs.get("sample_frames", self.AUTO_SAMPLE_DEFAULT)
                    )
                    resolved = self._auto_corrections(measured)
                    auto_info = {"measured": measured, "computed": resolved}
                    core = self._build_adjust_vf(resolved, None)
                vf = self._blend_intensity(core, intensity)
            save_name = self._validate_save_request(inputs, op)
            if save_name and not resolved:
                raise _GradeInputError("save_look: nothing to save (no resolved adjust/curves params).")
        except _GradeInputError as e:
            return ToolResult(success=False, error=str(e))

        start = time.time()

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", codec, "-crf", str(crf),
            "-c:a", "copy",
            str(output_path),
        ]
        err = self._run_ffmpeg(cmd)
        if err:
            return ToolResult(success=False, error=f"FFmpeg failed: {err}")
        if not output_path.exists() or output_path.stat().st_size == 0:
            return ToolResult(success=False, error=f"{op} produced no output.")

        elapsed = time.time() - start

        data: dict[str, Any] = {
            "input": str(input_path),
            "output": str(output_path),
            "output_path": str(output_path),
            "op": op,
            "intensity": inputs.get("intensity", 1.0),
            "filter": vf,
        }
        artifacts = [str(output_path)]
        if op == "profile":
            data["profile"] = inputs.get("profile")
            data["lut"] = inputs.get("lut_path")
            intensity_in = inputs.get("intensity", 1.0)
            if isinstance(intensity_in, (int, float)) and 0 < intensity_in < 1:
                # Pre-0.2.0 the blend was silently inverted (intensity=0.85 gave
                # 85% ORIGINAL). Callers tuned against the old behavior get the
                # complement now — surface that loudly instead of silently flipping.
                data["intensity_warning"] = (
                    f"intensity semantics were fixed in color_grade 0.2.0: "
                    f"intensity={intensity_in:g} now blends {intensity_in:g} of the GRADE "
                    f"(schema contract: 0 = original, 1 = full grade). Before 0.2.0 the "
                    f"blend was inverted ({intensity_in:g} of the original). If this value "
                    f"was tuned against pre-0.2.0 output, use intensity={1 - intensity_in:g} "
                    f"to reproduce the old look."
                )
        if op in ("adjust", "auto"):
            data["adjust"] = resolved
        if op == "adjust" and inputs.get("lut_path"):
            data["lut"] = inputs.get("lut_path")
        if op == "curves":
            data["curves"] = resolved
        if auto_info:
            data["auto"] = auto_info
        if inputs.get("look"):
            data["look"] = inputs["look"]

        if save_name:
            # auto persists its computed values as an adjust look (reusable as-is)
            save_op = "curves" if op == "curves" else "adjust"
            try:
                look_path = self._save_look(save_name, self._looks_dir(inputs), save_op, resolved)
                data["look_path"] = str(look_path)
                artifacts.append(str(look_path))
            except OSError as e:
                data["look_warning"] = f"graded output OK but the look could not be saved: {e}"

        return ToolResult(
            success=True,
            data=data,
            artifacts=artifacts,
            duration_seconds=round(elapsed, 2),
        )

    # ---- op resolution + saved looks ----

    def _resolve_op(self, inputs: dict[str, Any]) -> tuple[str, Optional[dict[str, Any]]]:
        op = inputs.get("op")
        if op is not None and op not in self.OPERATIONS:
            raise _GradeInputError(f"op must be one of {self.OPERATIONS}; got {op!r}.")
        look_doc = None
        look = inputs.get("look")
        if look is not None:
            name = self._validate_look_name(look)
            look_doc = self._load_look(name, self._looks_dir(inputs))
            if op is None:
                op = look_doc["op"]
            elif op != look_doc["op"]:
                raise _GradeInputError(
                    f"look {name!r} was saved for op {look_doc['op']!r}; got op={op!r}."
                )
        return op or "profile", look_doc

    @staticmethod
    def _merge_look_params(
        inputs: dict[str, Any], look_doc: Optional[dict[str, Any]], keys: tuple[str, ...]
    ) -> dict[str, Any]:
        """Saved-look params as defaults, explicitly-passed inputs override."""
        params: dict[str, Any] = {}
        if look_doc:
            stored = look_doc.get("params") or {}
            params.update({k: v for k, v in stored.items() if k in keys})
        for k in keys:
            if inputs.get(k) is not None:
                params[k] = inputs[k]
        return params

    def _validate_save_request(self, inputs: dict[str, Any], op: str) -> Optional[str]:
        if not inputs.get("save_look"):
            return None
        if op not in ("adjust", "curves", "auto"):
            raise _GradeInputError("save_look only applies to adjust/curves/auto (the parametric ops).")
        return self._validate_look_name(inputs.get("look_name"))

    def _looks_dir(self, inputs: dict[str, Any]) -> Path:
        return Path(inputs.get("looks_dir") or DEFAULT_LOOKS_DIR)

    @classmethod
    def _validate_look_name(cls, name: Any) -> str:
        if not isinstance(name, str) or not cls.LOOK_NAME_RE.match(name):
            raise _GradeInputError(
                "look names must be slugs (lowercase letters/digits/'-'/'_', "
                f"starting alphanumeric, max 64 chars); got {name!r}."
            )
        return name

    @staticmethod
    def _load_look(name: str, looks_dir: Path) -> dict[str, Any]:
        path = looks_dir / f"{name}.json"
        if not path.exists():
            raise _GradeInputError(f"look not found: {path}")
        try:
            doc = json.loads(path.read_text())
        except Exception as e:
            raise _GradeInputError(f"could not read look {name!r}: {e}")
        if (not isinstance(doc, dict) or doc.get("op") not in ("adjust", "curves")
                or not isinstance(doc.get("params"), dict)):
            raise _GradeInputError(
                f"look file {path} is not a valid look (needs op adjust|curves and a params object)."
            )
        return doc

    def _save_look(self, name: str, looks_dir: Path, op: str, params: dict[str, Any]) -> Path:
        path = looks_dir / f"{name}.json"
        looks_dir.mkdir(parents=True, exist_ok=True)
        doc = {
            "look_format": 1,
            "name": name,
            "op": op,
            "params": params,
            "saved_by": f"{self.name}@{self.version}",
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2))
        os.replace(tmp, path)
        return path

    # ---- adjust ----

    @staticmethod
    def _validate_intensity(v: Any) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
            raise _GradeInputError(f"intensity must be a number in [0, 1]; got {v!r}.")
        return float(v)

    @classmethod
    def _validate_adjust(cls, params: dict[str, Any], allow_empty: bool) -> dict[str, float]:
        unknown = set(params) - set(cls.ADJUST_RANGES)
        if unknown:  # only reachable via a hand-edited look file
            raise _GradeInputError(f"unknown adjust params: {sorted(unknown)}.")
        out: dict[str, float] = {}
        for key, (lo, hi) in cls.ADJUST_RANGES.items():
            if key not in params or params[key] is None:
                continue
            v = params[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise _GradeInputError(f"adjust {key} must be a number; got {v!r}.")
            if not (lo <= float(v) <= hi):
                raise _GradeInputError(f"adjust {key} must be in [{lo}, {hi}]; got {v}.")
            out[key] = float(v)
        if not out and not allow_empty:
            raise _GradeInputError(
                f"adjust requires at least one of {list(cls.ADJUST_RANGES)} "
                "(or a lut_path / look to apply)."
            )
        return out

    def _build_adjust_vf(self, p: dict[str, float], lut_path: Optional[str]) -> str:
        chain: list[str] = []
        if lut_path:
            lp = Path(lut_path)
            if not lp.exists():
                raise _GradeInputError(f"lut_path not found: {lut_path}")
            safe_path = str(lp.resolve()).replace("\\", "/").replace(":", "\\:")
            chain.append(f"lut3d='{safe_path}'")

        temp = p.get("temperature") or 0.0
        tint = p.get("tint") or 0.0
        balance: dict[str, float] = {}
        if temp and self._ffmpeg_has_filter("colortemperature"):
            # +100 -> 3500K (warm/orange), -100 -> 9500K (cool/blue)
            kelvin = max(1000, min(40000, int(round(6500 - temp * 30))))
            chain.append(f"colortemperature=temperature={kelvin}")
        elif temp:
            shift = round(temp / 100.0 * self.TEMP_FALLBACK_SHIFT, 4)
            balance["rm"] = shift
            balance["bm"] = -shift
        if tint:
            # positive tint = magenta = pull green down
            balance["gm"] = round(-tint / 100.0 * self.TEMP_FALLBACK_SHIFT, 4)
        if balance:
            chain.append("colorbalance=" + ":".join(f"{k}={v}" for k, v in sorted(balance.items())))

        eq_parts = [
            f"{k}={p[k]:.6g}"
            for k in ("brightness", "contrast", "saturation", "gamma")
            if k in p
        ]
        if eq_parts:
            chain.append("eq=" + ":".join(eq_parts))
        if p.get("sharpness"):
            chain.append(f"unsharp=5:5:{p['sharpness'] * 1.5:.4g}")
        if p.get("vignette"):
            chain.append(f"vignette=angle={p['vignette'] * 1.5707:.4g}")
        if not chain:
            raise _GradeInputError("adjust resolved to a no-op (all params neutral and no lut_path).")
        return ",".join(chain)

    def _ffmpeg_has_filter(self, name: str) -> bool:
        cls = type(self)
        if cls._available_filters is None:
            names: set[str] = set()
            try:
                proc = self.run_command(["ffmpeg", "-hide_banner", "-filters"], timeout=30)
                for line in (proc.stdout or "").splitlines():
                    parts = line.split()
                    # " TS. colortemperature  V->V  description" — name is parts[1]
                    if len(parts) >= 3 and "->" in parts[2]:
                        names.add(parts[1])
            except Exception:
                pass
            cls._available_filters = frozenset(names)
        return name in cls._available_filters

    # ---- curves ----

    def _build_curves_vf(self, params: dict[str, Any]) -> tuple[dict[str, Any], str]:
        points = params.get("points")
        wheels = params.get("wheels")
        if points is None and wheels is None:
            raise _GradeInputError(
                "curves requires points ({master|red|green|blue: [[x,y],...]}) and/or wheels."
            )
        chain: list[str] = []
        resolved: dict[str, Any] = {}
        if points is not None:
            if not isinstance(points, dict) or not points:
                raise _GradeInputError(
                    f"curves points must be a non-empty object keyed by {self.CURVE_CHANNELS}."
                )
            unknown = set(points) - set(self.CURVE_CHANNELS)
            if unknown:
                raise _GradeInputError(
                    f"curves channels must be in {self.CURVE_CHANNELS}; got {sorted(unknown)}."
                )
            args: list[str] = []
            norm_points: dict[str, list[list[float]]] = {}
            for ch in self.CURVE_CHANNELS:
                pts = points.get(ch)
                if pts is None:
                    continue
                norm = self._validate_curve_points(ch, pts)
                norm_points[ch] = [[x, y] for x, y in norm]
                args.append(f"{ch}='" + " ".join(f"{x:.6g}/{y:.6g}" for x, y in norm) + "'")
            chain.append("curves=" + ":".join(args))
            resolved["points"] = norm_points
        if wheels is not None:
            resolved_wheels, balance = self._validate_wheels(wheels)
            chain.append("colorbalance=" + ":".join(f"{k}={v}" for k, v in balance.items()))
            resolved["wheels"] = resolved_wheels
        return resolved, ",".join(chain)

    @staticmethod
    def _validate_curve_points(channel: str, pts: Any) -> list[tuple[float, float]]:
        if not isinstance(pts, list) or len(pts) < 2:
            raise _GradeInputError(f"curves {channel!r} needs >= 2 [x, y] points.")
        norm: list[tuple[float, float]] = []
        for pt in pts:
            if (not isinstance(pt, (list, tuple)) or len(pt) != 2
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in pt)):
                raise _GradeInputError(
                    f"curves {channel!r} points must be [x, y] number pairs; got {pt!r}."
                )
            x, y = float(pt[0]), float(pt[1])
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise _GradeInputError(f"curves {channel!r} point ({x}, {y}) out of [0, 1].")
            norm.append((x, y))
        xs = [x for x, _ in norm]
        if any(b <= a for a, b in zip(xs, xs[1:])):
            raise _GradeInputError(f"curves {channel!r} x values must be strictly increasing.")
        return norm

    @classmethod
    def _validate_wheels(cls, wheels: Any) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
        if not isinstance(wheels, dict) or not wheels:
            raise _GradeInputError(
                f"curves wheels must be a non-empty object keyed by {cls.WHEEL_BANDS}."
            )
        unknown = set(wheels) - set(cls.WHEEL_BANDS)
        if unknown:
            raise _GradeInputError(
                f"wheel bands must be in {cls.WHEEL_BANDS}; got {sorted(unknown)}."
            )
        suffix = {"shadows": "s", "midtones": "m", "highlights": "h"}
        resolved: dict[str, dict[str, float]] = {}
        balance: dict[str, float] = {}
        for band in cls.WHEEL_BANDS:
            w = wheels.get(band)
            if w is None:
                continue
            if not isinstance(w, dict) or not w:
                raise _GradeInputError(f"wheel {band!r} must be a non-empty {{r, g, b}} object.")
            bad = set(w) - {"r", "g", "b"}
            if bad:
                raise _GradeInputError(
                    f"wheel {band!r} offsets must be keyed r/g/b; got {sorted(bad)}."
                )
            resolved[band] = {}
            for c in ("r", "g", "b"):
                v = w.get(c)
                if v is None:
                    continue
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not (-1.0 <= v <= 1.0):
                    raise _GradeInputError(
                        f"wheel {band}.{c} must be a number in [-1, 1]; got {v!r}."
                    )
                resolved[band][c] = float(v)
                balance[f"{c}{suffix[band]}"] = round(float(v), 4)
        if not balance:
            raise _GradeInputError("wheels provided but no r/g/b offsets set.")
        return resolved, balance

    # ---- auto ----

    def _auto_measure(self, src: Path, sample_frames: Any) -> dict[str, float]:
        import subprocess

        if (isinstance(sample_frames, bool) or not isinstance(sample_frames, int)
                or not (1 <= sample_frames <= 50)):
            raise _GradeInputError(
                f"sample_frames must be an integer in [1, 50]; got {sample_frames!r}."
            )
        duration = self._probe_duration(src)
        rate = sample_frames / duration if duration and duration > 0 else 1.0
        rate = max(0.05, min(rate, 30.0))
        cmd = ["ffmpeg", "-v", "error", "-i", str(src),
               "-vf", f"fps={rate:.6g},signalstats,metadata=print:file=-",
               "-f", "null", "-"]
        try:
            proc = self.run_command(cmd, timeout=300)
        except subprocess.CalledProcessError as e:
            raise _GradeInputError(
                f"auto: signalstats sampling failed: {((e.stderr or '').strip())[-300:]}"
            )
        except subprocess.TimeoutExpired:
            raise _GradeInputError("auto: signalstats sampling timed out.")
        yavg: list[float] = []
        ymin: list[float] = []
        ymax: list[float] = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            try:
                if "signalstats.YAVG=" in line:
                    yavg.append(float(line.rsplit("=", 1)[1]))
                elif "signalstats.YMIN=" in line:
                    ymin.append(float(line.rsplit("=", 1)[1]))
                elif "signalstats.YMAX=" in line:
                    ymax.append(float(line.rsplit("=", 1)[1]))
            except ValueError:
                continue
        if not yavg or not ymin or not ymax:
            raise _GradeInputError("auto: could not read luma stats from the source (no video frames?).")
        return {
            "frames_sampled": len(yavg),
            "yavg": round(sum(yavg) / len(yavg), 2),
            "ymin": round(min(ymin), 2),
            "ymax": round(max(ymax), 2),
        }

    @classmethod
    def _auto_corrections(cls, m: dict[str, float]) -> dict[str, float]:
        """Documented heuristic: expand luma toward 16-235, then recenter the
        post-contrast mean on mid-gray (eq applies contrast about 128 THEN adds
        brightness, so brightness compensates the post-contrast mean)."""
        spread = max(1.0, m["ymax"] - m["ymin"])
        contrast = max(1.0, min(cls.AUTO_CONTRAST_MAX, cls.AUTO_TARGET_RANGE / spread))
        mean_after = contrast * (m["yavg"] - cls.AUTO_TARGET_MID) + cls.AUTO_TARGET_MID
        brightness = max(
            -cls.AUTO_BRIGHTNESS_MAX,
            min(cls.AUTO_BRIGHTNESS_MAX, (cls.AUTO_TARGET_MID - mean_after) / 255.0),
        )
        return {"brightness": round(brightness, 4), "contrast": round(contrast, 4)}

    def _probe_duration(self, src: Path) -> Optional[float]:
        import subprocess

        try:
            proc = self.run_command(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(src)],
                timeout=30,
            )
            return float((proc.stdout or "").strip())
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
            return None

    # ---- shared helpers ----

    @staticmethod
    def _blend_intensity(vf: str, intensity: float) -> str:
        """Blend the WHOLE graded chain against the original (0 = original).

        blend's all_opacity weights the FIRST input (the original here), so the
        graded share is 1 - opacity -> opacity = 1 - intensity. (Live-verified:
        all_opacity=0 outputs the second input.)"""
        if intensity >= 1.0:
            return vf
        return (
            f"split[original][tograde];"
            f"[tograde]{vf}[graded];"
            f"[original][graded]blend=all_mode=normal:all_opacity={1.0 - intensity:.6g}"
        )

    def _run_ffmpeg(self, cmd: list[str]) -> Optional[str]:
        """Run ffmpeg; None on success, trimmed stderr on failure (run_command
        uses check=True so non-zero exits raise CalledProcessError)."""
        import subprocess

        try:
            self.run_command(cmd, timeout=900)
            return None
        except subprocess.CalledProcessError as e:
            return ((e.stderr or "") or "ffmpeg failed").strip()[-500:]
        except subprocess.TimeoutExpired:
            return "ffmpeg timed out."

    # ---- legacy profile path (op=profile, unchanged behavior) ----

    def _build_filter(self, inputs: dict[str, Any]) -> str:
        if "custom_vf" in inputs:
            return inputs["custom_vf"]

        lut_path = inputs.get("lut_path")
        if lut_path and Path(lut_path).exists():
            safe_path = str(Path(lut_path).resolve()).replace("\\", "/").replace(":", "\\:")
            return f"lut3d='{safe_path}'"

        profile_name = inputs.get("profile", "cinematic_warm")
        profile = PROFILES.get(profile_name)
        if not profile:
            return ""

        vf = profile["vf"]

        # Apply intensity blending if < 1.0 (intensity=0 keeps the legacy
        # full-grade quirk; new ops treat 0 as original via _blend_intensity)
        intensity = inputs.get("intensity", 1.0)
        if 0 < intensity < 1.0:
            vf = self._blend_intensity(vf, intensity)

        return vf

    @staticmethod
    def list_profiles() -> dict[str, str]:
        """Return available profiles and their descriptions."""
        return {name: p["description"] for name, p in PROFILES.items()}


class _GradeInputError(Exception):
    """Invalid grade request (rejected before any encode work)."""
