"""Motion Ops — freeze / reverse / speed / volume transforms that bake into new clips.

Instagram Edits' timeline tricks that a single scalar field can't express. Each op reads a
source clip and writes a NEW derived clip (like auto_reframe/silence_cutter), which then
becomes a `cuts[].source` at the edit stage. The derived clip's duration is RE-PROBED after
the transform (freeze/speed change runtime) and the result can be registered into an
asset_manifest with provenance.

Ops:
  - freeze        hold the frame at `at_seconds` for `duration` seconds (audio gets silence)
  - reverse       play the whole clip backwards (video + audio)
  - speed         constant speed change, validated 0.5x-4x (video setpts + chained atempo)
  - segment_volume  per-range audio gain ([{start,end,volume}, ...])
  - volume_boost  raise overall loudness, capped at 1.5x (matches Edits) with a true-peak limiter

Design (Edits-parity Wave 4, /plan-eng-review):
  - Outputs derived asset files, NOT edit_decisions fields. cuts[].speed covers CONSTANT
    speed already; motion_ops covers what the scalar can't (freeze/reverse) + audio gain.
  - Re-probes the output duration and (optionally) registers it in an asset_manifest with
    provenance (source_tool=motion_ops, subtype=<op>, generation_summary, duration_seconds).
  - volume_boost is capped at 1.5x with alimiter to prevent clipping (Edits caps boost at 150%).
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

    OPERATIONS = ("freeze", "reverse", "speed", "segment_volume", "volume_boost")
    SPEED_MIN = 0.5
    SPEED_MAX = 4.0
    BOOST_MAX = 1.5  # Edits caps volume boost at 150%

    capabilities = list(OPERATIONS)
    supports = {op: True for op in OPERATIONS}
    best_for = [
        "freeze-frame on a punchline, reverse for a loop, slow/fast motion, per-segment volume",
    ]
    not_good_for = [
        "time-varying speed ramps with easing (this does constant speed only)",
        "edit-stage decisions — this bakes pixels; use cuts[].speed for a simple constant speed",
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
            # provenance registration (optional)
            "asset_manifest_path": {
                "type": "string",
                "description": "Optional: append the derived clip to this asset_manifest (validated, written).",
            },
            "scene_id": {"type": "string", "default": "derived", "description": "scene_id for the registered asset"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=2, ram_mb=1024, vram_mb=0, disk_mb=1024)
    idempotency_key_fields = ["operation", "input_path", "factor", "at_seconds", "duration"]
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

        params = {k: inputs.get(k) for k in ("factor", "at_seconds", "duration", "gain") if inputs.get(k) is not None}
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
