"""Motion Ops — freeze / reverse / speed / volume / pan-zoom / fx / flip transforms baked into new clips.

Instagram Edits' timeline tricks that a single scalar field can't express. Each op reads a
source clip and writes a NEW derived clip (like auto_reframe/silence_cutter), which then
becomes a `cuts[].source` at the edit stage. The derived clip's duration is RE-PROBED after
the transform (freeze/speed change runtime, rotate swaps WxH) and the result can be
registered into an asset_manifest with provenance.

Ops:
  - freeze        hold the frame at `at_seconds` for `duration` seconds (audio gets silence)
  - reverse       play the whole clip backwards (video + audio)
  - speed         constant speed change, validated 0.5x-4x (video setpts + chained atempo)
  - segment_volume  per-range audio gain ([{start,end,volume}, ...])
  - volume_boost  raise overall loudness, capped at 1.5x (matches Edits) with a true-peak limiter
  - pan_zoom      keyframed punch-in / pan / Ken Burns baked into the footage (zoompan).
                  keyframes=[{t, zoom, x_pan, y_pan}] linearly interpolated, OR
                  preset in {punch_in, punch_out, ken_burns_lr, ken_burns_rl,
                  slow_zoom_in, slow_zoom_out} + preset_params {max_zoom, duration}.
                  zoom validated to [1.0, 3.0]; x_pan/y_pan are 0..1 normalized window
                  centers (0.5 = centered), the window is clamped inside the frame.
  - clip_fx       windowed motion effects so a hit can land on a beat:
                  shake (crop-window jitter, intensity), zoom_pulse (zoompan z oscillation,
                  freq + amount), strobe (periodic eq-brightness flashes, freq + amount),
                  glitch (seeded rgbashift+noise bursts, intensity + seed).
                  start/end default to the whole clip.
  - flip          direction in {horizontal (hflip), vertical (vflip), rotate_90_cw,
                  rotate_90_ccw (transpose)} — rotate swaps the resolution; re-probed.

Design (Edits-parity Wave 4 + base-footage motion wave, /plan-eng-review):
  - Outputs derived asset files, NOT edit_decisions fields. cuts[].speed covers CONSTANT
    speed already; motion_ops covers what the scalar can't (freeze/reverse) + audio gain.
  - Re-probes the output duration and (optionally) registers it in an asset_manifest with
    provenance (source_tool=motion_ops, subtype=<op>, generation_summary, duration_seconds).
  - volume_boost is capped at 1.5x with alimiter to prevent clipping (Edits caps boost at 150%).
  - zoompan ANTI-JITTER GOTCHA: zoompan crops on an integer pixel grid, so slow zooms
    visibly stair-step. pan_zoom and zoom_pulse upscale ~4x first (width capped at 7680)
    and emit s=WxH locked back to the source resolution at the source fps. Audio is
    passed through untouched (-c:a copy).
  - Output is 8-bit SDR (libx264 yuv420p path) — see not_good_for for the HDR rule.
  - NOTE: true time-varying speed RAMPS (ease slow->fast) are out of scope here; `speed` is a
    constant factor. This is a documented limitation, not a silent gap.
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


class MotionOps(BaseTool):
    name = "motion_ops"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "video_post"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:ffmpeg"]
    install_instructions = "Install FFmpeg: https://ffmpeg.org/download.html"
    agent_skills = ["ffmpeg"]

    OPERATIONS = (
        "freeze", "reverse", "speed", "segment_volume", "volume_boost",
        "pan_zoom", "clip_fx", "flip",
    )
    SPEED_MIN = 0.5
    SPEED_MAX = 4.0
    BOOST_MAX = 1.5  # Edits caps volume boost at 150%
    ZOOM_MIN = 1.0
    ZOOM_MAX = 3.0
    UPSCALE_MAX_W = 7680  # cap the anti-jitter pre-upscale so 4K sources don't explode RAM
    PAN_ZOOM_PRESETS = (
        "punch_in", "punch_out", "ken_burns_lr", "ken_burns_rl",
        "slow_zoom_in", "slow_zoom_out",
    )
    PUNCH_SNAP_SECONDS = 0.25  # how fast a punch_in/punch_out snaps to/from max_zoom
    CLIP_EFFECTS = ("shake", "zoom_pulse", "strobe", "glitch")
    FLIP_DIRECTIONS = ("horizontal", "vertical", "rotate_90_cw", "rotate_90_ccw")

    capabilities = list(OPERATIONS)
    supports = {op: True for op in OPERATIONS}
    best_for = [
        "freeze-frame on a punchline, reverse for a loop, slow/fast motion, per-segment volume",
        "punch-in zoom / Ken Burns / pan baked into base footage (the Edits camera-move gap)",
        "beat-timed shake, zoom-pulse, strobe, or glitch hits via clip_fx start/end windows",
        "mirroring or rotating a clip (flip)",
    ]
    not_good_for = [
        "time-varying speed ramps with easing (this does constant speed only)",
        "edit-stage decisions — this bakes pixels; use cuts[].speed for a simple constant speed",
        "HDR sources — output is 8-bit SDR; detect with is_hdr_source() and handle HDR per AGENT_GUIDE before using this tool",
        "3D camera moves / parallax — pan_zoom is a 2D crop-window move over flat footage",
    ]
    fallback_tools: list[str] = []

    input_schema = {
        "type": "object",
        "required": ["operation", "input_path"],
        "properties": {
            "operation": {"type": "string", "enum": list(OPERATIONS)},
            "input_path": {"type": "string"},
            "output_path": {"type": "string", "description": "Defaults to {stem}_{op}.mp4"},
            # freeze
            "at_seconds": {"type": "number", "minimum": 0, "description": "freeze: frame time to hold"},
            "duration": {"type": "number", "minimum": 0, "description": "freeze: hold length (s)"},
            # speed
            "factor": {
                "type": "number",
                "description": f"speed: {SPEED_MIN}x-{SPEED_MAX}x (>1 faster, <1 slower)",
            },
            # segment_volume
            "segments": {
                "type": "array",
                "description": "segment_volume: [{start, end, volume}] per-range gain",
                "items": {
                    "type": "object",
                    "required": ["start", "end", "volume"],
                    "properties": {
                        "start": {"type": "number", "minimum": 0},
                        "end": {"type": "number", "minimum": 0},
                        "volume": {"type": "number", "minimum": 0},
                    },
                },
            },
            # volume_boost
            "gain": {"type": "number", "minimum": 0, "description": f"volume_boost: <= {BOOST_MAX}"},
            # pan_zoom (exactly one of keyframes | preset)
            "keyframes": {
                "type": "array",
                "description": (
                    "pan_zoom: [{t, zoom, x_pan, y_pan}] linearly interpolated; "
                    f"zoom {ZOOM_MIN}-{ZOOM_MAX} (1.0=none), x_pan/y_pan 0-1 normalized "
                    "window center (0.5=centered, clamped inside the frame)"
                ),
                "items": {
                    "type": "object",
                    "required": ["t"],
                    "properties": {
                        "t": {"type": "number", "minimum": 0},
                        "zoom": {"type": "number", "minimum": ZOOM_MIN, "maximum": ZOOM_MAX, "default": 1.0},
                        "x_pan": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                        "y_pan": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                    },
                },
            },
            "preset": {
                "type": "string",
                "enum": list(PAN_ZOOM_PRESETS),
                "description": "pan_zoom: canned move (alternative to keyframes)",
            },
            "preset_params": {
                "type": "object",
                "description": "pan_zoom preset tuning",
                "properties": {
                    "max_zoom": {"type": "number", "minimum": ZOOM_MIN, "maximum": ZOOM_MAX, "default": 1.2},
                    "duration": {"type": "number", "exclusiveMinimum": 0,
                                 "description": "seconds the move runs (default: whole clip; holds after)"},
                },
            },
            # clip_fx
            "effect": {
                "type": "string",
                "enum": list(CLIP_EFFECTS),
                "description": "clip_fx: which effect to bake",
            },
            "start": {"type": "number", "minimum": 0, "description": "clip_fx: effect window start (default 0)"},
            "end": {"type": "number", "exclusiveMinimum": 0, "description": "clip_fx: effect window end (default clip end)"},
            "intensity": {
                "type": "number",
                "description": "clip_fx shake: 0-1 jitter strength (default 0.3); glitch: RGB shift px (default 8)",
            },
            "freq": {"type": "number", "exclusiveMinimum": 0,
                     "description": "clip_fx zoom_pulse/strobe: oscillation/flash frequency Hz (default 2)"},
            "amount": {
                "type": "number",
                "description": "clip_fx zoom_pulse: peak extra zoom 0-2 (default 0.15); strobe: brightness lift 0-1 (default 0.5)",
            },
            "seed": {"type": "integer", "description": "clip_fx glitch: burst-schedule seed (default 0)"},
            # flip
            "direction": {
                "type": "string",
                "enum": list(FLIP_DIRECTIONS),
                "description": "flip: horizontal/vertical mirror or 90-degree rotate (rotate swaps WxH)",
            },
            # provenance registration (optional)
            "asset_manifest_path": {
                "type": "string",
                "description": "Optional: append the derived clip to this asset_manifest (validated, written).",
            },
            "scene_id": {"type": "string", "default": "derived", "description": "scene_id for the registered asset"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=2, ram_mb=1024, vram_mb=0, disk_mb=1024)
    idempotency_key_fields = [
        "operation", "input_path", "factor", "at_seconds", "duration",
        "keyframes", "preset", "preset_params", "effect", "start", "end",
        "intensity", "freq", "amount", "seed", "direction",
    ]
    side_effects = ["writes a derived video file", "may append to an asset_manifest"]
    user_visible_verification = ["Play the derived clip; confirm the motion/audio change is correct"]

    # ---- execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        import shutil

        if shutil.which("ffmpeg") is None:
            return ToolResult(success=False, error="ffmpeg not found on PATH. " + self.install_instructions)

        op = inputs.get("operation")
        if op not in self.OPERATIONS:
            return ToolResult(success=False, error=f"operation must be one of {self.OPERATIONS}.")
        src = inputs.get("input_path")
        if not src:
            return ToolResult(success=False, error="input_path is required.")
        src_path = Path(src)
        if not src_path.exists():
            return ToolResult(success=False, error=f"input not found: {src}")

        out_path = Path(inputs.get("output_path") or src_path.with_name(f"{src_path.stem}_{op}.mp4"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        has_audio = self._has_audio(src_path)

        # build + run the op
        try:
            if op == "reverse":
                err = self._reverse(src_path, out_path, has_audio)
            elif op == "speed":
                err = self._speed(src_path, out_path, inputs, has_audio)
            elif op == "freeze":
                err = self._freeze(src_path, out_path, inputs, has_audio)
            elif op == "segment_volume":
                err = self._segment_volume(src_path, out_path, inputs, has_audio)
            elif op == "volume_boost":
                err = self._volume_boost(src_path, out_path, inputs, has_audio)
            elif op == "pan_zoom":
                err = self._pan_zoom(src_path, out_path, inputs, has_audio)
            elif op == "clip_fx":
                err = self._clip_fx(src_path, out_path, inputs, has_audio)
            elif op == "flip":
                err = self._flip(src_path, out_path, inputs, has_audio)
            else:  # unreachable
                err = f"unhandled operation {op}"
        except _OpInputError as e:
            return ToolResult(success=False, error=str(e))

        if err:
            return ToolResult(success=False, error=err)
        if not out_path.exists() or out_path.stat().st_size == 0:
            return ToolResult(success=False, error=f"{op} produced no output.")

        # re-probe: freeze/speed change the duration, downstream must see the new value
        probed = self._probe(out_path)
        data: dict[str, Any] = {
            "operation": op,
            "input": str(src_path),
            "output": str(out_path),
            "output_path": str(out_path),
            "duration_seconds": probed.get("duration_seconds"),
            "resolution": probed.get("resolution"),
        }
        artifacts = [str(out_path)]

        # optional provenance registration into an asset_manifest
        am_path = inputs.get("asset_manifest_path")
        if am_path:
            reg_err = self._register_asset(Path(am_path), op, src_path, out_path, inputs, probed)
            if reg_err:
                # the derived clip exists and is valid; only the registration failed
                data["asset_manifest_warning"] = reg_err
            else:
                data["asset_manifest_path"] = str(am_path)
                artifacts.append(str(am_path))

        return ToolResult(success=True, data=data, artifacts=artifacts)

    # ---- ops ----

    def _reverse(self, src: Path, out: Path, has_audio: bool) -> Optional[str]:
        cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", "reverse"]
        if has_audio:
            cmd += ["-af", "areverse"]
        else:
            cmd += ["-an"]
        cmd.append(str(out))
        return self._run(cmd)

    def _speed(self, src: Path, out: Path, inputs: dict[str, Any], has_audio: bool) -> Optional[str]:
        factor = inputs.get("factor")
        if not isinstance(factor, (int, float)) or not (self.SPEED_MIN <= factor <= self.SPEED_MAX):
            raise _OpInputError(
                f"speed requires factor in [{self.SPEED_MIN}, {self.SPEED_MAX}]; got {factor!r}."
            )
        vf = f"setpts=PTS/{factor}"
        if has_audio:
            atempo = self._atempo_chain(factor)
            fc = f"[0:v]{vf}[v];[0:a]{atempo}[a]"
            cmd = ["ffmpeg", "-y", "-i", str(src), "-filter_complex", fc,
                   "-map", "[v]", "-map", "[a]", str(out)]
        else:
            cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", vf, "-an", str(out)]
        return self._run(cmd)

    @classmethod
    def _atempo_chain(cls, factor: float) -> str:
        """atempo only accepts 0.5-2.0 per instance; chain to cover 0.5-4.0."""
        remaining = float(factor)
        parts: list[str] = []
        # bring within range using 2.0 steps for speed-up, 0.5 for slow-down
        while remaining > 2.0:
            parts.append("atempo=2.0")
            remaining /= 2.0
        while remaining < 0.5:
            parts.append("atempo=0.5")
            remaining /= 0.5
        parts.append(f"atempo={round(remaining, 6)}")
        return ",".join(parts)

    def _freeze(self, src: Path, out: Path, inputs: dict[str, Any], has_audio: bool) -> Optional[str]:
        at = inputs.get("at_seconds")
        dur = inputs.get("duration")
        if not isinstance(at, (int, float)) or at < 0:
            raise _OpInputError("freeze requires at_seconds >= 0.")
        if not isinstance(dur, (int, float)) or dur <= 0:
            raise _OpInputError("freeze requires duration > 0.")
        probed = self._probe(src)
        total = probed.get("duration_seconds") or 0
        if at > total:
            raise _OpInputError(f"freeze at_seconds ({at}) is past the clip end ({total:.2f}s).")
        w = probed.get("width") or 0
        h = probed.get("height") or 0
        fps = probed.get("fps") or 30

        # Step 1: extract the still frame at `at` (an image input is the only thing `-loop`
        # accepts; `-loop` is NOT a valid option on a video/mp4 input).
        still = out.with_name(f"{out.stem}_still.png")
        err = self._run(["ffmpeg", "-y", "-ss", str(at), "-i", str(src), "-frames:v", "1", str(still)])
        if err:
            return f"freeze: could not extract still frame at {at}s: {err}"

        # Step 2: concat [0,at] + the held still (dur seconds) + [at,end]. Audio gets silence
        # for the held span. input 0 = source, input 1 = the looped still image.
        scale = f"scale={w}:{h}," if w and h else ""
        v = (
            f"[0:v]trim=0:{at},setpts=PTS-STARTPTS[v1];"
            f"[1:v]{scale}fps={fps},setpts=PTS-STARTPTS[vf];"
            f"[0:v]trim={at},setpts=PTS-STARTPTS[v2];"
            f"[v1][vf][v2]concat=n=3:v=1:a=0[v]"
        )
        if has_audio:
            a = (
                f"[0:a]atrim=0:{at},asetpts=PTS-STARTPTS[a1];"
                f"anullsrc=channel_layout=stereo:sample_rate=44100,atrim=0:{dur}[as];"
                f"[0:a]atrim={at},asetpts=PTS-STARTPTS[a2];"
                f"[a1][as][a2]concat=n=3:v=0:a=1[a]"
            )
            filtergraph = f"{v};{a}"
            maps = ["-map", "[v]", "-map", "[a]"]
        else:
            filtergraph = v
            maps = ["-map", "[v]", "-an"]
        cmd = ["ffmpeg", "-y",
               "-i", str(src),
               "-loop", "1", "-t", str(dur), "-i", str(still),
               "-filter_complex", filtergraph, *maps, str(out)]
        err = self._run(cmd)
        with __import__("contextlib").suppress(Exception):
            still.unlink(missing_ok=True)
        return err

    def _segment_volume(self, src: Path, out: Path, inputs: dict[str, Any], has_audio: bool) -> Optional[str]:
        if not has_audio:
            raise _OpInputError("segment_volume needs an audio track; the input has none.")
        segs = inputs.get("segments") or []
        if not isinstance(segs, list) or not segs:
            raise _OpInputError("segment_volume requires segments=[{start,end,volume}, ...].")
        # chain a volume filter per segment, each enabled only within its time window
        filters = []
        for s in segs:
            try:
                start, end, vol = float(s["start"]), float(s["end"]), float(s["volume"])
            except (KeyError, TypeError, ValueError):
                raise _OpInputError("each segment needs numeric start, end, volume.")
            if end <= start or vol < 0:
                raise _OpInputError(f"invalid segment {s!r}: need end>start and volume>=0.")
            filters.append(f"volume=enable='between(t,{start},{end})':volume={vol}")
        af = ",".join(filters)
        cmd = ["ffmpeg", "-y", "-i", str(src), "-af", af, "-c:v", "copy", str(out)]
        return self._run(cmd)

    def _volume_boost(self, src: Path, out: Path, inputs: dict[str, Any], has_audio: bool) -> Optional[str]:
        if not has_audio:
            raise _OpInputError("volume_boost needs an audio track; the input has none.")
        gain = inputs.get("gain")
        if not isinstance(gain, (int, float)) or gain <= 0:
            raise _OpInputError("volume_boost requires gain > 0.")
        if gain > self.BOOST_MAX:
            raise _OpInputError(
                f"volume_boost is capped at {self.BOOST_MAX}x (Edits caps at 150%); got {gain}x."
            )
        # boost then true-peak limit to avoid clipping
        af = f"volume={gain},alimiter=limit=0.97"
        cmd = ["ffmpeg", "-y", "-i", str(src), "-af", af, "-c:v", "copy", str(out)]
        return self._run(cmd)

    # ---- pan_zoom ----

    def _pan_zoom(self, src: Path, out: Path, inputs: dict[str, Any], has_audio: bool) -> Optional[str]:
        keyframes = inputs.get("keyframes")
        preset = inputs.get("preset")
        if (keyframes is None) == (preset is None):
            raise _OpInputError("pan_zoom requires exactly one of keyframes or preset.")
        probed = self._probe(src)
        w, h = probed.get("width"), probed.get("height")
        if not w or not h:
            return "pan_zoom: could not probe the source resolution."
        total = probed.get("duration_seconds") or 0
        fps = probed.get("fps") or 30

        if preset is not None:
            keyframes = self._preset_keyframes(preset, inputs.get("preset_params") or {}, total)
        kfs = self._validate_keyframes(keyframes)

        z_expr = self._lerp_expr([(t, z) for t, z, _, _ in kfs])
        cx_expr = self._lerp_expr([(t, x) for t, _, x, _ in kfs])
        cy_expr = self._lerp_expr([(t, y) for t, _, _, y in kfs])
        # window top-left from the normalized center, clamped fully inside the frame;
        # `zoom` inside x/y is the value the z expression produced for this frame
        x_expr = f"max(0,min(iw-iw/zoom,({cx_expr})*iw-iw/(2*zoom)))"
        y_expr = f"max(0,min(ih-ih/zoom,({cy_expr})*ih-ih/(2*zoom)))"
        vf = (
            f"{self._antijitter_upscale()},"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d=1:s={w}x{h}:fps={fps}"
        )
        return self._run(self._vf_cmd(src, out, vf, has_audio))

    def _preset_keyframes(self, preset: str, params: dict[str, Any], total: float) -> list[dict[str, Any]]:
        if preset not in self.PAN_ZOOM_PRESETS:
            raise _OpInputError(f"pan_zoom preset must be one of {self.PAN_ZOOM_PRESETS}; got {preset!r}.")
        mz = params.get("max_zoom", 1.2)
        if not isinstance(mz, (int, float)) or not (self.ZOOM_MIN < mz <= self.ZOOM_MAX):
            raise _OpInputError(
                f"preset_params.max_zoom must be in ({self.ZOOM_MIN}, {self.ZOOM_MAX}]; got {mz!r}."
            )
        dur = params.get("duration", total or None)
        if dur is None:
            raise _OpInputError("pan_zoom: could not probe the clip duration; pass preset_params.duration.")
        if not isinstance(dur, (int, float)) or dur <= 0:
            raise _OpInputError(f"preset_params.duration must be > 0; got {dur!r}.")
        dur = min(float(dur), total) if total else float(dur)
        mz = float(mz)
        snap = min(self.PUNCH_SNAP_SECONDS, dur)
        if preset == "punch_in":
            return [{"t": 0, "zoom": 1.0}, {"t": snap, "zoom": mz}]
        if preset == "punch_out":
            return [{"t": 0, "zoom": mz}, {"t": snap, "zoom": 1.0}]
        if preset == "slow_zoom_in":
            return [{"t": 0, "zoom": 1.0}, {"t": dur, "zoom": mz}]
        if preset == "slow_zoom_out":
            return [{"t": 0, "zoom": mz}, {"t": dur, "zoom": 1.0}]
        if preset == "ken_burns_lr":
            return [{"t": 0, "zoom": mz, "x_pan": 0.0}, {"t": dur, "zoom": mz, "x_pan": 1.0}]
        return [{"t": 0, "zoom": mz, "x_pan": 1.0}, {"t": dur, "zoom": mz, "x_pan": 0.0}]  # ken_burns_rl

    def _validate_keyframes(self, keyframes: Any) -> list[tuple[float, float, float, float]]:
        """Validate + normalize to sorted (t, zoom, x_pan, y_pan) tuples."""
        if not isinstance(keyframes, list) or not keyframes:
            raise _OpInputError("pan_zoom keyframes must be a non-empty list of {t, zoom, x_pan, y_pan}.")
        kfs: list[tuple[float, float, float, float]] = []
        for kf in keyframes:
            if not isinstance(kf, dict):
                raise _OpInputError(f"each keyframe must be an object: got {kf!r}.")
            t = kf.get("t")
            if not isinstance(t, (int, float)) or t < 0:
                raise _OpInputError(f"keyframe needs t >= 0: {kf!r}.")
            zoom = kf.get("zoom", 1.0)
            if not isinstance(zoom, (int, float)) or not (self.ZOOM_MIN <= zoom <= self.ZOOM_MAX):
                raise _OpInputError(
                    f"keyframe zoom must be in [{self.ZOOM_MIN}, {self.ZOOM_MAX}]: {kf!r}."
                )
            x, y = kf.get("x_pan", 0.5), kf.get("y_pan", 0.5)
            for label, v in (("x_pan", x), ("y_pan", y)):
                if not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
                    raise _OpInputError(f"keyframe {label} must be in [0, 1]: {kf!r}.")
            kfs.append((float(t), float(zoom), float(x), float(y)))
        kfs.sort(key=lambda k: k[0])
        return kfs

    @classmethod
    def _lerp_expr(cls, points: list[tuple[float, float]], var: str = "it") -> str:
        """Piecewise-linear ffmpeg expression over `var` (zoompan input time).

        Holds the first value before the first keyframe and the last value after the
        last one. Built right-to-left as nested if(lt(...)) so each segment only fires
        in its own [t_i, t_i+1) range."""
        if len(points) == 1:
            return f"{points[0][1]:.6g}"
        expr = f"{points[-1][1]:.6g}"
        for (t0, v0), (t1, v1) in reversed(list(zip(points, points[1:]))):
            dt = t1 - t0
            if dt <= 1e-6:  # coincident keyframes: step straight to the later value
                seg = f"{v1:.6g}"
            else:
                seg = f"({v0:.6g}+({v1 - v0:.6g})*(({var}-{t0:.6g})/{dt:.6g}))"
            expr = f"if(lt({var},{t1:.6g}),{seg},{expr})"
        return f"if(lt({var},{points[0][0]:.6g}),{points[0][1]:.6g},{expr})"

    # ---- clip_fx ----

    def _clip_fx(self, src: Path, out: Path, inputs: dict[str, Any], has_audio: bool) -> Optional[str]:
        effect = inputs.get("effect")
        if effect not in self.CLIP_EFFECTS:
            raise _OpInputError(f"clip_fx requires effect in {self.CLIP_EFFECTS}; got {effect!r}.")
        probed = self._probe(src)
        w, h = probed.get("width"), probed.get("height")
        if not w or not h:
            return "clip_fx: could not probe the source resolution."
        total = probed.get("duration_seconds") or 0
        fps = probed.get("fps") or 30
        start = inputs.get("start", 0.0)
        end = inputs.get("end", total or None)
        if end is None:
            raise _OpInputError("clip_fx: could not probe the clip duration; pass end explicitly.")
        if not isinstance(start, (int, float)) or start < 0:
            raise _OpInputError(f"clip_fx start must be >= 0; got {start!r}.")
        if total and start >= total:
            raise _OpInputError(f"clip_fx start ({start}) is past the clip end ({total:.2f}s).")
        if not isinstance(end, (int, float)) or end <= start:
            raise _OpInputError(f"clip_fx requires end > start; got start={start!r}, end={end!r}.")
        s_, e_ = float(start), float(end)

        if effect == "shake":
            intensity = inputs.get("intensity", 0.3)
            if not isinstance(intensity, (int, float)) or not (0 < intensity <= 1):
                raise _OpInputError(f"shake intensity must be in (0, 1]; got {intensity!r}.")
            # crop a window 2m smaller and jitter its origin; sin gives smooth sway,
            # random(.) adds per-frame grit; outside the window the crop sits centered
            m = max(2, int(round(intensity * 0.1 * min(w, h))))
            jx = (
                f"if(between(t,{s_},{e_}),"
                f"clip({m}+{m}*(0.7*sin(t*43)+0.3*(2*random(0)-1)),0,{2 * m}),{m})"
            )
            jy = (
                f"if(between(t,{s_},{e_}),"
                f"clip({m}+{m}*(0.7*sin(t*61+1.3)+0.3*(2*random(1)-1)),0,{2 * m}),{m})"
            )
            vf = f"crop=w=iw-{2 * m}:h=ih-{2 * m}:x='{jx}':y='{jy}',scale={w}:{h}"
        elif effect == "zoom_pulse":
            freq = inputs.get("freq", 2.0)
            amount = inputs.get("amount", 0.15)
            if not isinstance(freq, (int, float)) or freq <= 0:
                raise _OpInputError(f"zoom_pulse freq must be > 0; got {freq!r}.")
            if not isinstance(amount, (int, float)) or not (0 < amount <= self.ZOOM_MAX - 1.0):
                raise _OpInputError(
                    f"zoom_pulse amount must be in (0, {self.ZOOM_MAX - 1.0}]; got {amount!r}."
                )
            # raised-cosine so z starts/ends at exactly 1 on each cycle (no pop at the window edges)
            z = (
                f"if(between(it,{s_},{e_}),"
                f"1+{float(amount):.6g}*0.5*(1-cos(2*PI*{float(freq):.6g}*(it-{s_}))),1)"
            )
            vf = (
                f"{self._antijitter_upscale()},"
                f"zoompan=z='{z}':x='iw/2-iw/(2*zoom)':y='ih/2-ih/(2*zoom)':d=1:s={w}x{h}:fps={fps}"
            )
        elif effect == "strobe":
            freq = inputs.get("freq", 2.0)
            amount = inputs.get("amount", 0.5)
            if not isinstance(freq, (int, float)) or freq <= 0:
                raise _OpInputError(f"strobe freq must be > 0; got {freq!r}.")
            if not isinstance(amount, (int, float)) or not (0 < amount <= 1.0):
                raise _OpInputError(f"strobe amount must be in (0, 1]; got {amount!r}.")
            period = 1.0 / float(freq)
            flash = period * 0.25  # brief flash, not a square wave
            enable = f"between(t,{s_},{e_})*lt(mod(t-{s_},{period:.6g}),{flash:.6g})"
            vf = f"eq=brightness={float(amount):.6g}:enable='{enable}'"
        else:  # glitch
            px = inputs.get("intensity", 8)
            if not isinstance(px, (int, float)) or not (1 <= px <= 64):
                raise _OpInputError(f"glitch intensity must be 1-64 shift px; got {px!r}.")
            seed = inputs.get("seed", 0)
            if not isinstance(seed, int) or isinstance(seed, bool):
                raise _OpInputError(f"glitch seed must be an integer; got {seed!r}.")
            px = int(px)
            bursts = self._glitch_bursts(s_, e_, seed)
            enable = "+".join(f"between(t,{a},{b})" for a, b in bursts)
            vf = (
                f"rgbashift=rh=-{px}:bh={px}:enable='{enable}',"
                f"noise=alls=20:allf=t+u:enable='{enable}'"
            )
        return self._run(self._vf_cmd(src, out, vf, has_audio))

    @staticmethod
    def _glitch_bursts(start: float, end: float, seed: int) -> list[tuple[float, float]]:
        """Seeded burst schedule (computed here, not in ffmpeg expressions, so the same
        seed reproduces the exact same glitch hits)."""
        import random as _random

        rng = _random.Random(seed)
        bursts: list[tuple[float, float]] = []
        t = start
        while True:
            t += rng.uniform(0.15, 0.6)
            if t >= end:
                break
            b_end = min(end, t + rng.uniform(0.06, 0.18))
            bursts.append((round(t, 3), round(b_end, 3)))
            t = b_end
        if not bursts:  # window shorter than the minimum gap: still glitch once
            bursts.append((round(start, 3), round(min(end, start + 0.1), 3)))
        return bursts

    # ---- flip ----

    def _flip(self, src: Path, out: Path, inputs: dict[str, Any], has_audio: bool) -> Optional[str]:
        direction = inputs.get("direction")
        vf = {
            "horizontal": "hflip",
            "vertical": "vflip",
            "rotate_90_cw": "transpose=clock",
            "rotate_90_ccw": "transpose=cclock",
        }.get(direction)
        if not vf:
            raise _OpInputError(f"flip requires direction in {self.FLIP_DIRECTIONS}; got {direction!r}.")
        return self._run(self._vf_cmd(src, out, vf, has_audio))

    # ---- video-filter helpers ----

    @classmethod
    def _antijitter_upscale(cls) -> str:
        """zoompan crops on an integer pixel grid and stair-steps on slow moves;
        upscaling ~4x first (capped) makes sub-source-pixel steps available."""
        return f"scale=w='min(iw*4,{cls.UPSCALE_MAX_W})':h=-2"

    @staticmethod
    def _vf_cmd(src: Path, out: Path, vf: str, has_audio: bool) -> list[str]:
        """Video-only filter command; audio passes through untouched (-c:a copy)."""
        cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", vf]
        cmd += ["-c:a", "copy"] if has_audio else ["-an"]
        cmd.append(str(out))
        return cmd

    # ---- asset_manifest registration ----

    def _register_asset(
        self, path: Path, op: str, src: Path, out: Path,
        inputs: dict[str, Any], probed: dict[str, Any],
    ) -> Optional[str]:
        """Append the derived clip to an asset_manifest with provenance, validate, write back.
        Returns an error string on failure (manifest left untouched), else None."""
        if not path.exists():
            return f"asset_manifest_path not found: {path}"
        try:
            doc = json.loads(path.read_text())
        except Exception as e:
            return f"could not read asset_manifest: {e}"
        if not isinstance(doc, dict) or not isinstance(doc.get("assets"), list):
            return "asset_manifest is not a valid manifest object with an assets[] list."

        param_keys = (
            "factor", "at_seconds", "duration", "gain",
            "preset", "effect", "direction", "start", "end",
            "intensity", "freq", "amount", "seed",
        )
        params = {k: inputs.get(k) for k in param_keys if inputs.get(k) is not None}
        if inputs.get("keyframes") is not None:
            params["keyframes"] = f"{len(inputs['keyframes'])} kf"
        res = probed.get("resolution")
        entry = {
            "id": f"motion-{op}-{len(doc['assets']) + 1}",
            "type": "video",
            "path": str(out),
            "source_tool": "motion_ops",
            "scene_id": str(inputs.get("scene_id", "derived")),
            "subtype": op,
            "generation_summary": f"motion_ops {op}({params}) from {src.name}",
            "format": out.suffix.lstrip(".") or "mp4",
        }
        if isinstance(probed.get("duration_seconds"), (int, float)):
            entry["duration_seconds"] = round(float(probed["duration_seconds"]), 4)
        if res:
            entry["resolution"] = res
        doc["assets"].append(entry)
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("asset_manifest", doc)
        except Exception as e:
            return f"derived-asset entry did not validate against asset_manifest schema: {e}"
        self._write_json(path, doc)
        return None

    # ---- helpers ----

    def _run(self, cmd: list[str]) -> Optional[str]:
        """Run ffmpeg; return None on success or a trimmed stderr string on failure.

        BaseTool.run_command uses check=True, so a non-zero exit raises CalledProcessError
        rather than returning a code — catch it and surface the stderr."""
        import subprocess

        try:
            self.run_command(cmd, timeout=900)
            return None
        except subprocess.CalledProcessError as e:
            return ((e.stderr or "") or "ffmpeg failed").strip()[-500:]
        except subprocess.TimeoutExpired:
            return "ffmpeg timed out."

    def _has_audio(self, path: Path) -> bool:
        import subprocess

        try:
            proc = self.run_command(
                ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                 "stream=index", "-of", "csv=p=0", str(path)],
                timeout=30,
            )
            return bool((proc.stdout or "").strip())
        except subprocess.CalledProcessError:
            return False

    def _probe(self, path: Path) -> dict[str, Any]:
        """Normalize to {duration_seconds, width, height, fps, resolution}.

        probe_output returns video_width/video_height (not width/height) and no fps, so we
        remap those and add an fps probe (freeze needs a frame rate for the held still).
        """
        out: dict[str, Any] = {}
        try:
            from tools.video._shared import probe_output

            info = probe_output(path)
            out["duration_seconds"] = info.get("duration_seconds")
            out["width"] = info.get("video_width") or info.get("width")
            out["height"] = info.get("video_height") or info.get("height")
        except Exception:
            pass
        # fps from r_frame_rate (e.g. "30000/1001")
        try:
            proc = self.run_command(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=r_frame_rate", "-of", "default=nw=1:nk=1", str(path)],
                timeout=30,
            )
            raw = (proc.stdout or "").strip()
            if "/" in raw:
                num, den = raw.split("/", 1)
                out["fps"] = round(float(num) / float(den), 3) if float(den) else None
            elif raw:
                out["fps"] = float(raw)
        except Exception:
            pass
        if out.get("width") and out.get("height"):
            out["resolution"] = f"{out['width']}x{out['height']}"
        return out

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)


class _OpInputError(Exception):
    """Bad parameters for a motion op (validated before any ffmpeg spend)."""
