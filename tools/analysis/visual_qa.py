"""Visual QA tool for automated video quality checks.

Extracts frames at specified timestamps and runs basic quality checks:
- File existence, resolution, duration, codec validation
- Frame extraction for visual inspection by the agent
- Caption occlusion check (compares brightness in face vs caption zones)
- Transition verification (frame similarity at transition points)

Returns frame paths so the agent can visually inspect them.

MOTION OPS (`motion` / `sheet` / `strip` / `vs_plan`)
----------------------------------------------------
A still frame cannot show motion. Sampling N stills and reading them one by one
is also expensive enough that in practice only a handful get looked at, so a pan
that never panned, an animation that never ran and a frozen tail all survive QA.
These four ops fix that, cheapest first:

  motion   MEASURE instead of look. One ffmpeg decode emits three per-frame
           curves; the result is a compact table plus freeze/dark/cut findings.
           ~1s for a 33s render, zero images.
  sheet    ONE contact sheet with burned-in timestamps instead of N frame files
           — the whole video at a glance for the cost of a single image.
  strip    A dense single-row filmstrip over a short window. Consecutive frames
           side by side are how motion becomes visible at all: a pan drifts, a
           zoom grows, a card builds in.
  vs_plan  Diff edit_decisions against the measurement. Advisory — it reports
           the delta and the agent decides.

Analysis lives in `lib/video_motion.py` and `lib/qa_plan_diff.py` as pure
functions; this module only builds ffmpeg command lines and shuttles text.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from lib import app_paths, qa_plan_diff, video_motion
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolStability,
    ToolTier,
)

#: Timestamp label font. Repo-relative per the styles convention
#: (schemas/styles/playbook.schema.json: "assets/fonts/<Family>/..."), resolved
#: against code_root() so it also works from the packaged bundle. A missing font
#: drops the labels; it must never fail a measurement.
_LABEL_FONT = "assets/fonts/Inter/Inter-Variable.ttf"

#: Analysis is done on a downscale — frame-difference energy needs relative
#: change, not detail, and 128x72 makes a 33s pass take ~1s instead of ~45s.
_ANALYSIS_SCALE = "128:72"

_SHEET_FPS = 1.0
_STRIP_FPS = 12.0
_SHEET_TILE_WIDTH = 200
#: Below this a tile shows nothing useful, so a long video drops its sampling
#: rate instead of shrinking past it.
_SHEET_MIN_TILE_WIDTH = 120
#: final_review.checks.visual_spotcheck.frames_sampled has minimum 4, and a 3-tile
#: sheet is too sparse to review anyway.
_SHEET_MIN_TILES = 4
_STRIP_TILE_WIDTH = 190
_STRIP_MAX_TILES = 24
_STRIP_DEFAULT_SECONDS = 1.5


class VisualQA(BaseTool):
    name = "visual_qa"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "analysis"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = ["cmd:ffmpeg", "cmd:ffprobe"]
    install_instructions = "Install FFmpeg: https://ffmpeg.org/download.html"
    agent_skills = ["ffmpeg"]

    capabilities = [
        "extract_review_frames",
        "probe_video",
        "check_audio_levels",
        "measure_motion",
        "contact_sheet",
        "motion_filmstrip",
        "diff_plan_vs_render",
    ]

    input_schema = {
        "type": "object",
        "required": ["operation", "input_path"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "review",
                    "probe",
                    "audio_levels",
                    "motion",
                    "sheet",
                    "strip",
                    "vs_plan",
                ],
                "description": (
                    "review: extract frames at timestamps for visual inspection. "
                    "probe: get video metadata (duration, resolution, codecs). "
                    "audio_levels: check audio volume at specified timestamps. "
                    "motion: MEASURE per-frame motion/brightness/scene-change over "
                    "the whole video — returns a compact table plus freeze, dark and "
                    "cut findings, no images. Start here: it catches frozen frames, "
                    "dead tails, slideshow pacing and animations that never ran, in "
                    "~1s and a few hundred tokens. "
                    "sheet: ONE contact sheet of the whole video with burned-in "
                    "timestamps (pass `timestamps` for one tile per scene). Replaces "
                    "sampling N separate frames. "
                    "strip: dense single-row filmstrip over a short `window` — the "
                    "only way to actually SEE how a pan, zoom or animation moved. "
                    "vs_plan: advisory diff of an edit_decisions plan against the "
                    "measured render (declared-but-not-rendered + declared-but-flat)."
                ),
            },
            "input_path": {
                "type": "string",
                "description": "Path to the video file to inspect.",
            },
            "timestamps": {
                "type": "array",
                "items": {"type": "number"},
                "description": (
                    "Timestamps (in seconds) at which to extract frames or "
                    "check audio levels. For `sheet`, one tile per timestamp "
                    "(use scene midpoints — a fixed-interval sheet can land tiles "
                    "on transition frames that look like blanks)."
                ),
            },
            "output_path": {
                "type": "string",
                "description": (
                    "Where to write the sheet/strip image. Defaults to a '.qa' directory beside the input."
                ),
            },
            "window": {
                "type": "object",
                "description": (
                    f"Time window for `strip`. Defaults to the first {_STRIP_DEFAULT_SECONDS}s (the hook)."
                ),
                "properties": {
                    "start": {"type": "number", "minimum": 0},
                    "duration": {"type": "number", "minimum": 0.05},
                },
            },
            "fps": {
                "type": "number",
                "minimum": 0.05,
                "description": (f"Sampling rate. Default {_SHEET_FPS} for `sheet`, {_STRIP_FPS} for `strip`."),
            },
            "tile_width": {
                "type": "integer",
                "minimum": 80,
                "description": "Per-tile width in px for `sheet`/`strip`.",
            },
            "max_tiles": {
                "type": "integer",
                "minimum": 2,
                "description": (
                    f"Tile cap for `strip` (default {_STRIP_MAX_TILES}). The fps is "
                    "reduced to fit rather than the window being truncated, and the "
                    "reduction is reported."
                ),
            },
            "label_frames": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Burn the timestamp into each tile so a finding can name a time. "
                    "Silently skipped if the label font is missing."
                ),
            },
            "plan_path": {
                "type": "string",
                "description": (
                    "edit_decisions.json to diff against for `vs_plan`. Defaults to "
                    "'edit_decisions.json' found beside or one level above the input."
                ),
            },
            "analysis": {
                "type": "object",
                "description": (
                    "Detection thresholds for `motion`/`vs_plan`. Calibrated on 1080p "
                    "H.264; frame-difference energy scales with bitrate and resolution, "
                    "so these are knobs, not constants."
                ),
                "properties": {
                    "bucket_seconds": {"type": "number", "minimum": 0.05},
                    "static_threshold": {"type": "number", "minimum": 0},
                    "dark_threshold": {"type": "number", "minimum": 0},
                    "cut_threshold": {"type": "number", "minimum": 0},
                    "min_static_seconds": {"type": "number", "minimum": 0},
                },
            },
            "output_dir": {
                "type": "string",
                "description": (
                    "Directory to save extracted frames. Defaults to a "
                    "'review_frames' subdirectory next to the input file."
                ),
            },
            "checks": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "resolution",
                        "duration",
                        "audio_present",
                        "pixel_format",
                        "file_size",
                    ],
                },
                "description": "Specific checks to run (probe operation).",
            },
            "expected": {
                "type": "object",
                "description": (
                    "Expected values for validation. "
                    "Keys: width, height, min_duration, max_duration, "
                    "pixel_format, has_audio."
                ),
                "properties": {
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                    "min_duration": {"type": "number"},
                    "max_duration": {"type": "number"},
                    "pixel_format": {"type": "string"},
                    "has_audio": {"type": "boolean"},
                },
            },
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=200)
    # Every new op keys off different fields; without them all four collapse onto
    # the same (operation, input_path) key and a cache would hand back the wrong
    # artifact.
    idempotency_key_fields = [
        "operation",
        "input_path",
        "timestamps",
        "window",
        "fps",
        "tile_width",
        "max_tiles",
        "plan_path",
        "analysis",
    ]
    side_effects = ["writes frame images to output_dir", "writes sheet/strip images to output_path"]
    user_visible_verification = [
        "Visually inspect extracted frames for quality issues",
        "Read the contact sheet / filmstrip image the sheet and strip ops write",
    ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        operation = inputs["operation"]
        input_path = inputs["input_path"]

        if not Path(input_path).exists():
            return ToolResult(success=False, error=f"Input not found: {input_path}")

        start = time.time()

        try:
            if operation == "review":
                result = self._review(inputs)
            elif operation == "probe":
                result = self._probe(inputs)
            elif operation == "audio_levels":
                result = self._audio_levels(inputs)
            elif operation == "motion":
                result = self._motion(inputs)
            elif operation == "sheet":
                result = self._sheet(inputs)
            elif operation == "strip":
                result = self._strip(inputs)
            elif operation == "vs_plan":
                result = self._vs_plan(inputs)
            else:
                return ToolResult(success=False, error=f"Unknown operation: {operation}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

        result.duration_seconds = round(time.time() - start, 2)
        return result

    def _review(self, inputs: dict[str, Any]) -> ToolResult:
        """Extract frames at specified timestamps for visual review."""
        input_path = inputs["input_path"]
        timestamps = inputs.get("timestamps", [])

        if not timestamps:
            # Auto-generate timestamps: start, 25%, 50%, 75%, end-1s
            dur = self._get_duration(input_path)
            timestamps = [
                1.0,
                dur * 0.25,
                dur * 0.50,
                dur * 0.75,
                max(dur - 1.0, 0),
            ]

        output_dir = inputs.get("output_dir")
        if not output_dir:
            output_dir = str(Path(input_path).parent / "review_frames")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        frames = []
        for ts in timestamps:
            ts_label = f"{ts:.1f}".replace(".", "_")
            frame_path = str(Path(output_dir) / f"frame_{ts_label}s.jpg")
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(ts),
                "-i",
                input_path,
                "-frames:v",
                "1",
                "-q:v",
                "2",
                frame_path,
            ]
            try:
                self.run_command(cmd)
                if Path(frame_path).exists():
                    frames.append(
                        {
                            "timestamp": ts,
                            "path": frame_path,
                        }
                    )
            except Exception:
                frames.append(
                    {
                        "timestamp": ts,
                        "path": None,
                        "error": f"Failed to extract frame at {ts}s",
                    }
                )

        return ToolResult(
            success=True,
            data={
                "operation": "review",
                "input": input_path,
                "frame_count": len([f for f in frames if f.get("path")]),
                "frames": frames,
            },
            artifacts=[f["path"] for f in frames if f.get("path")],
        )

    def _probe(self, inputs: dict[str, Any]) -> ToolResult:
        """Probe video metadata and optionally validate against expectations."""
        input_path = inputs["input_path"]
        expected = inputs.get("expected", {})

        # Get comprehensive probe data
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=width,height,codec_name,pix_fmt,r_frame_rate,sample_rate,channels,codec_type",
            "-of",
            "json",
            input_path,
        ]
        probe_result = self.run_command(cmd)
        probe_out = probe_result.stdout
        probe_data = json.loads(probe_out)

        # Extract key info
        video_stream = None
        audio_stream = None
        for s in probe_data.get("streams", []):
            if s.get("codec_type") == "video" and not video_stream:
                video_stream = s
            elif s.get("codec_type") == "audio" and not audio_stream:
                audio_stream = s

        info = {
            "duration": float(probe_data.get("format", {}).get("duration", 0)),
            "file_size_mb": round(int(probe_data.get("format", {}).get("size", 0)) / 1048576, 1),
            "has_audio": audio_stream is not None,
        }
        if video_stream:
            info.update(
                {
                    "width": video_stream.get("width"),
                    "height": video_stream.get("height"),
                    "pixel_format": video_stream.get("pix_fmt"),
                    "video_codec": video_stream.get("codec_name"),
                    "frame_rate": video_stream.get("r_frame_rate"),
                }
            )
        if audio_stream:
            info.update(
                {
                    "audio_codec": audio_stream.get("codec_name"),
                    "sample_rate": audio_stream.get("sample_rate"),
                    "channels": audio_stream.get("channels"),
                }
            )

        # Validate against expectations
        issues = []
        if "width" in expected and info.get("width") != expected["width"]:
            issues.append(f"Width: expected {expected['width']}, got {info.get('width')}")
        if "height" in expected and info.get("height") != expected["height"]:
            issues.append(f"Height: expected {expected['height']}, got {info.get('height')}")
        if "min_duration" in expected and info["duration"] < expected["min_duration"]:
            issues.append(f"Duration too short: {info['duration']:.1f}s < {expected['min_duration']}s")
        if "max_duration" in expected and info["duration"] > expected["max_duration"]:
            issues.append(f"Duration too long: {info['duration']:.1f}s > {expected['max_duration']}s")
        if "pixel_format" in expected and info.get("pixel_format") != expected["pixel_format"]:
            issues.append(f"Pixel format: expected {expected['pixel_format']}, got {info.get('pixel_format')}")
        if "has_audio" in expected and info["has_audio"] != expected["has_audio"]:
            issues.append(
                f"Audio: expected {'present' if expected['has_audio'] else 'absent'}, "
                f"got {'present' if info['has_audio'] else 'absent'}"
            )

        info["validation_issues"] = issues
        info["validation_passed"] = len(issues) == 0

        return ToolResult(
            success=True,
            data={
                "operation": "probe",
                "input": input_path,
                **info,
            },
        )

    def _audio_levels(self, inputs: dict[str, Any]) -> ToolResult:
        """Check audio levels at specified timestamps."""
        input_path = inputs["input_path"]
        timestamps = inputs.get("timestamps", [])

        if not timestamps:
            dur = self._get_duration(input_path)
            timestamps = [1.0, dur * 0.5, max(dur - 2.0, 0)]

        levels = []
        for ts in timestamps:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(ts),
                "-t",
                "3",
                "-i",
                input_path,
                "-vn",
                "-af",
                "volumedetect",
                "-f",
                "null",
                "NUL" if __import__("sys").platform == "win32" else "/dev/null",
            ]
            try:
                cmd_result = self.run_command(cmd)
                output = cmd_result.stderr  # volumedetect outputs to stderr
                mean_vol = None
                max_vol = None
                for line in output.split("\n"):
                    if "mean_volume" in line:
                        mean_vol = float(line.split("mean_volume:")[1].strip().split()[0])
                    elif "max_volume" in line:
                        max_vol = float(line.split("max_volume:")[1].strip().split()[0])
                levels.append(
                    {
                        "timestamp": ts,
                        "mean_volume_db": mean_vol,
                        "max_volume_db": max_vol,
                    }
                )
            except Exception as e:
                levels.append(
                    {
                        "timestamp": ts,
                        "error": str(e),
                    }
                )

        return ToolResult(
            success=True,
            data={
                "operation": "audio_levels",
                "input": input_path,
                "levels": levels,
            },
        )

    def _get_duration(self, path: str) -> float:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            path,
        ]
        dur_result = self.run_command(cmd)
        return float(dur_result.stdout.strip().split("\n")[0])

    # ------------------------------------------------------------------
    # motion ops
    # ------------------------------------------------------------------

    _null_device = "NUL" if __import__("sys").platform == "win32" else "/dev/null"

    @staticmethod
    def _esc_filter(value: str) -> str:
        """Escape a path/value for use inside an ffmpeg filtergraph argument.

        The INPUT video never goes through here — it is passed via `-i`, which
        is the whole reason these ops don't inherit the `movie='<path>'`
        filtergraph-escaping bug in scene_detect (a quote or comma in an asset
        name corrupts the graph there and the failure is swallowed). Only paths
        we generate (temp files, the bundled font) are interpolated.

        Escaped TWICE, because a filtergraph is parsed at two levels: the graph
        parser strips one backslash level before each filter's own option parser
        splits on ':' and '='. One level is not enough — a home directory like
        /Users/o'brien, or an OPENNOLAN_CODE_ROOT containing a colon, broke the
        drawtext font path with "No option name near ...". The value is also left
        UNQUOTED at the call sites: ffmpeg cannot escape an apostrophe inside
        single quotes, so quoting would defeat the escaping.
        """
        out = value
        for _ in range(2):
            out = out.replace("\\", "\\\\")
            for ch in ("'", ":", ",", ";", "[", "]", "="):
                out = out.replace(ch, "\\" + ch)
        return out

    def _run_ffmpeg(self, cmd: list[str], *, timeout: int = 900) -> str:
        """Run ffmpeg/ffprobe, surfacing stderr on failure.

        `run_command` uses check=True, so a non-zero exit raises before the
        caller can read stderr — and stderr is where ffmpeg explains itself.
        """
        try:
            return self.run_command(cmd, timeout=timeout).stdout or ""
        except subprocess.CalledProcessError as e:
            tail = ((e.stderr or "").strip().splitlines() or ["<no stderr>"])[-6:]
            raise RuntimeError(f"{Path(cmd[0]).name} failed (exit {e.returncode}): " + " | ".join(tail)) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"{Path(cmd[0]).name} timed out after {timeout}s") from e

    def _video_info(self, path: str) -> dict[str, Any]:
        """width/height/duration/fps/frames in one ffprobe call. Unknown -> None."""
        out = self._run_ffmpeg(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "format=duration:stream=width,height,r_frame_rate,nb_frames",
                "-of",
                "json",
                path,
            ],
            timeout=60,
        )
        data = json.loads(out or "{}")
        streams = data.get("streams") or []
        stream = streams[0] if streams else {}

        def num(value: Any) -> Optional[float]:
            # ffprobe prints 'N/A' for plenty of real files (some MOV/ProRes, images,
            # raw streams). float('N/A') raises, which is how visual_qa._get_duration
            # blows up — never feed it straight to float().
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        fps = None
        rate = stream.get("r_frame_rate") or ""
        if "/" in rate:
            den = num(rate.partition("/")[2])
            n = num(rate.partition("/")[0])
            fps = n / den if n is not None and den else None
        frames = num(stream.get("nb_frames"))
        return {
            "has_video": bool(streams),
            "width": stream.get("width"),
            "height": stream.get("height"),
            "duration": num((data.get("format") or {}).get("duration")),
            "fps": fps,
            "frames": int(frames) if frames else None,
        }

    def _require_video(self, path: str) -> dict[str, Any]:
        """Probe, and fail with a readable reason rather than a raw ffmpeg exit.

        Without this an audio-only input reaches the filtergraph and dies with
        "Output file does not contain any stream" (exit 234), and a still image
        crashes on float('N/A').
        """
        info = self._video_info(path)
        if not info["has_video"]:
            raise RuntimeError(
                f"no video stream in {Path(path).name} — the motion ops need video "
                "(use operation='audio_levels' for audio)"
            )
        if not info["duration"] or info["duration"] <= 0:
            # Recover a duration from frame count when the container omits one.
            if info["frames"] and info["fps"]:
                info["duration"] = info["frames"] / info["fps"]
            elif info["frames"] == 1:
                raise RuntimeError(
                    f"{Path(path).name} is a single still frame — there is no motion to "
                    "measure. Point these ops at a rendered video."
                )
        return info

    def _label_filter(self, offset: float, *, enabled: bool, font_size: int) -> str:
        """A drawtext chunk that burns the absolute timestamp into each tile.

        Without it the agent has to count grid positions to name a time, which it
        gets wrong; with it every tile is directly addressable. Returns "" when
        labelling is off or the bundled font is absent — a missing font must
        never fail the measurement.
        """
        if not enabled:
            return ""
        font = app_paths.code_root() / _LABEL_FONT
        if not font.is_file():
            return ""
        # `-ss` re-bases pts to 0, so the window start is added back explicitly.
        stamp = f"%{{pts\\:hms\\:{offset:g}}}"
        return (
            f",drawtext=fontfile={self._esc_filter(str(font))}"
            f":text='{stamp}':x=4:y=4:fontsize={font_size}"
            ":fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=3"
        )

    @staticmethod
    def _last_frame_time(info: dict[str, Any]) -> float:
        """pts of the last decodable frame.

        Seeking past this decodes zero frames and ffmpeg dies with an opaque
        "-22 Invalid argument" instead of anything a reader could act on.
        """
        return max(0.0, (info.get("duration") or 0.0) - 1.0 / (info.get("fps") or 30))

    @staticmethod
    def _qa_dir(input_path: str) -> Path:
        """Default output home: a dot-dir beside the input, hidden from browsing.

        Matches `_run_final_review`'s `.final_review_frames` precedent rather
        than dropping visible files into the project's renders/ folder.
        """
        out = Path(input_path).parent / ".qa"
        out.mkdir(parents=True, exist_ok=True)
        return out

    def _measure(self, input_path: str, *, timeout: int = 900) -> dict[str, Any]:
        """One decode pass -> three per-frame curves.

        luma   = brightness (black / blown out)
        scd    = scdet scene-change score, 0..100 (hard cuts)
        motion = frame-difference energy after tblend (movement)

        `metadata=print:file=` is used rather than the detect filters' log lines
        because `-loglevel error` SILENTLY SUPPRESSES blackdetect/freezedetect
        output — a parse that quietly returns nothing looks exactly like "no
        issues found". Writing to files survives any loglevel.
        """
        info = self._require_video(input_path)
        with tempfile.TemporaryDirectory(prefix="qa-motion-") as tmp:
            lum_f = Path(tmp) / "luma.txt"
            scd_f = Path(tmp) / "scd.txt"
            mot_f = Path(tmp) / "motion.txt"
            chain = (
                f"scale={_ANALYSIS_SCALE}"
                f",signalstats,metadata=print:key=lavfi.signalstats.YAVG"
                f":file={self._esc_filter(str(lum_f))}"
                f",scdet=s=0,metadata=print:key=lavfi.scd.score"
                f":file={self._esc_filter(str(scd_f))}"
                f",tblend=all_mode=difference"
                f",signalstats,metadata=print:key=lavfi.signalstats.YAVG"
                f":file={self._esc_filter(str(mot_f))}"
            )
            self._run_ffmpeg(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-i",
                    input_path,
                    "-an",
                    "-vf",
                    chain,
                    "-f",
                    "null",
                    self._null_device,
                ],
                timeout=timeout,
            )
            read = lambda p: video_motion.parse_metadata_print(  # noqa: E731
                p.read_text(errors="replace") if p.is_file() else ""
            )
            luma, scd, motion = read(lum_f), read(scd_f), read(mot_f)

        duration = info.get("duration")
        if not duration or duration <= 0:
            # Container duration can be 'N/A'; the last sampled frame is a
            # better answer than 0, which would make every ratio meaningless.
            last = max((t for t, _ in luma), default=0.0)
            duration = last + (1.0 / info["fps"] if info.get("fps") else 0.0)
        return {"info": info, "duration": float(duration), "luma": luma, "scd": scd, "motion": motion}

    def _summary_for(self, inputs: dict[str, Any], measured: dict[str, Any]) -> dict[str, Any]:
        knobs = inputs.get("analysis") or {}
        return video_motion.summarize(
            motion=measured["motion"],
            luma=measured["luma"],
            scd=measured["scd"],
            duration=measured["duration"],
            static_threshold=knobs.get("static_threshold", video_motion.STATIC_THRESHOLD),
            dark_threshold=knobs.get("dark_threshold", video_motion.DARK_THRESHOLD),
            cut_threshold=knobs.get("cut_threshold", video_motion.CUT_THRESHOLD),
            min_static_seconds=knobs.get("min_static_seconds", video_motion.MIN_STATIC_SECONDS),
            bucket_seconds=knobs.get("bucket_seconds", video_motion.BUCKET_SECONDS),
        )

    def _motion(self, inputs: dict[str, Any]) -> ToolResult:
        """Measure motion over the whole video. No images, no opinions."""
        measured = self._measure(inputs["input_path"])
        if not measured["motion"]:
            return ToolResult(
                success=False,
                error=(
                    "no frames measured — the decode produced no per-frame stats. "
                    "Check that the input is a decodable video with at least 2 frames."
                ),
            )
        summary = self._summary_for(inputs, measured)
        return ToolResult(
            success=True,
            data={
                "operation": "motion",
                "input": inputs["input_path"],
                "resolution": f"{measured['info'].get('width')}x{measured['info'].get('height')}",
                **summary,
            },
        )

    def _sheet(self, inputs: dict[str, Any]) -> ToolResult:
        """One contact sheet of the whole video, timestamps burned in."""
        input_path = inputs["input_path"]
        info = self._require_video(input_path)
        duration = info["duration"] or 0.0
        if duration <= 0:
            return ToolResult(
                success=False,
                error=f"could not determine the duration of {Path(input_path).name}",
            )
        width = info.get("width") or 1080
        height = info.get("height") or 1920
        aspect = height / width

        timestamps = [float(t) for t in (inputs.get("timestamps") or [])]
        tile_w = int(inputs.get("tile_width") or _SHEET_TILE_WIDTH)
        label = bool(inputs.get("label_frames", True))
        fps = float(inputs.get("fps") or _SHEET_FPS)

        notes: list[str] = []
        source_fps = info.get("fps")
        if not timestamps:
            # A short clip sampled at 1fps yields a 2- or 3-tile sheet: too sparse to
            # review, and below final_review's visual_spotcheck.frames_sampled minimum
            # of 4. Sample denser rather than hand back a sheet that can't be recorded.
            raised = video_motion.sample_count(duration, fps) < _SHEET_MIN_TILES
            if raised:
                fps = max(fps, _SHEET_MIN_TILES / duration)
            # Sampling faster than the source only duplicates frames.
            if source_fps and fps > source_fps:
                fps = source_fps

        n_tiles = len(timestamps) if timestamps else video_motion.sample_count(duration, fps)
        if not timestamps and info.get("frames"):
            # Never claim more tiles than the video HAS: `tile` pads the remainder with
            # background, so an uncapped count would overstate what was looked at.
            n_tiles = min(n_tiles, info["frames"])
        if not timestamps and raised and n_tiles >= _SHEET_MIN_TILES:
            notes.append(f"short clip ({duration:.2f}s): fps raised to {fps:.3g} for {n_tiles} tiles.")
        grid = video_motion.grid_for(n_tiles, tile_width=tile_w, aspect=aspect, min_tile_width=_SHEET_MIN_TILE_WIDTH)
        if grid["over_budget"]:
            # Too many tiles to stay legible. Sample LESS OFTEN rather than shrink
            # tiles to mush — and never by trimming the video, which would report
            # success while showing only the opening.
            cap = video_motion.max_tiles_for(aspect, tile_width=_SHEET_MIN_TILE_WIDTH, max_pixels=4_000_000)
            if timestamps:
                notes.append(
                    f"{n_tiles} explicit timestamps exceed a legible sheet (~{cap} tiles); "
                    f"tiles are at the {_SHEET_MIN_TILE_WIDTH}px floor. Split into "
                    "several sheets for a readable result."
                )
            elif duration > 0:
                fps = max(0.05, cap / duration)
                n_tiles = video_motion.sample_count(duration, fps)
                grid = video_motion.grid_for(
                    n_tiles,
                    tile_width=tile_w,
                    aspect=aspect,
                    min_tile_width=_SHEET_MIN_TILE_WIDTH,
                )
                notes.append(
                    f"fps reduced to {fps:.3f} ({n_tiles} tiles, one per "
                    f"{duration / max(1, n_tiles):.1f}s) to keep tiles legible; the whole "
                    "duration is still covered, just sampled less often. At this length "
                    "one sheet is a coarse instrument — use `motion` to find the moments "
                    "worth looking at, then `strip` those windows."
                )
        out_path = Path(inputs.get("output_path") or (self._qa_dir(input_path) / "sheet.jpg"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        font_size = max(10, grid["tile_width"] // 15)
        tile = f"tile={grid['cols']}x{grid['rows']}:padding=6:margin=6:color=0x1c1c1c"

        if timestamps:
            # One tile per requested time. Extracted individually because `select`
            # over an arbitrary timestamp list is fiddly and this is exact.
            font = app_paths.code_root() / _LABEL_FONT
            use_label = label and font.is_file()
            kept: list[float] = []
            with tempfile.TemporaryDirectory(prefix="qa-sheet-") as tmp:
                for ts in timestamps:
                    chunk = f"scale={grid['tile_width']}:-2" + (
                        f",drawtext=fontfile={self._esc_filter(str(font))}"
                        f":text='{ts:.2f}s':x=4:y=4:fontsize={font_size}"
                        ":fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=3"
                        if use_label
                        else ""
                    )
                    # `image2` stops at the first gap in t_%04d, so a timestamp that
                    # yields no frame (past the last decodable one) must NOT consume
                    # an index — otherwise it silently truncates every later tile.
                    target = Path(tmp) / f"t_{len(kept):04d}.jpg"
                    try:
                        self._run_ffmpeg(
                            [
                                "ffmpeg",
                                "-y",
                                "-hide_banner",
                                "-loglevel",
                                "error",
                                "-nostdin",
                                "-ss",
                                f"{ts:.4f}",
                                "-i",
                                input_path,
                                "-frames:v",
                                "1",
                                "-vf",
                                chunk,
                                "-q:v",
                                "3",
                                str(target),
                            ],
                            timeout=120,
                        )
                    except RuntimeError:
                        pass
                    if target.is_file():
                        kept.append(ts)
                    else:
                        notes.append(f"no frame at {ts:.2f}s (video is {duration:.2f}s) — tile dropped")
                if not kept:
                    return ToolResult(
                        success=False,
                        error=(f"none of the {len(timestamps)} timestamps fell inside the {duration:.2f}s video"),
                    )
                # Re-grid to what was actually captured, so `tiles` and `empty_cells`
                # describe the image rather than the request.
                n_tiles = len(kept)
                grid = video_motion.grid_for(
                    n_tiles,
                    # Pin the size: the frames were already extracted at this width and
                    # `tile` needs uniform inputs. Fewer tiles can only help the budget.
                    tile_width=grid["tile_width"],
                    aspect=aspect,
                    min_tile_width=grid["tile_width"],
                )
                tile = f"tile={grid['cols']}x{grid['rows']}:padding=6:margin=6:color=0x1c1c1c"
                self._run_ffmpeg(
                    [
                        "ffmpeg",
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-framerate",
                        "1",
                        "-f",
                        "image2",
                        "-i",
                        str(Path(tmp) / "t_%04d.jpg"),
                        "-vf",
                        tile,
                        "-frames:v",
                        "1",
                        "-q:v",
                        "3",
                        str(out_path),
                    ],
                    timeout=180,
                )
            sampling = {"mode": "timestamps", "timestamps": kept}
        else:
            chain = (
                f"fps={fps:g},scale={grid['tile_width']}:-2"
                + self._label_filter(0.0, enabled=label, font_size=font_size)
                + ","
                + tile
            )
            self._run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-i",
                    input_path,
                    "-an",
                    "-vf",
                    chain,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    str(out_path),
                ],
                timeout=600,
            )
            sampling = {"mode": "interval", "fps": fps, "seconds_per_tile": round(1 / fps, 3)}

        if not out_path.is_file():
            return ToolResult(success=False, error=f"sheet not written: {out_path}")
        # Checked against the FINAL count (explicit timestamps can drop tiles too), so
        # the agent is never told to record a number the schema will reject.
        if n_tiles < _SHEET_MIN_TILES:
            notes.append(
                f"only {n_tiles} tile(s) — below the {_SHEET_MIN_TILES} that "
                "final_review.visual_spotcheck.frames_sampled requires. Count the strip "
                "frames toward it too, or QA this clip with `strip` alone."
            )
        return ToolResult(
            success=True,
            data={
                "operation": "sheet",
                "input": input_path,
                "sheet_path": str(out_path),
                "tiles": n_tiles,
                "grid": f"{grid['cols']}x{grid['rows']}",
                # Trailing blanks are padding, not black frames in the video.
                "empty_cells": grid["empty_cells"],
                "tile_size": f"{grid['tile_width']}x{grid['tile_height']}",
                "bytes": out_path.stat().st_size,
                "duration_seconds": round(duration, 3),
                "sampling": sampling,
                "notes": notes,
                "read_this": (
                    "Read the sheet_path image. Tiles run left-to-right, top-to-bottom; "
                    "each carries its own timestamp, so quote that when reporting."
                ),
            },
            artifacts=[str(out_path)],
        )

    def _strip(self, inputs: dict[str, Any]) -> ToolResult:
        """Dense single-row filmstrip over one window — motion made visible."""
        input_path = inputs["input_path"]
        info = self._require_video(input_path)
        total = info["duration"] or 0.0
        if total <= 0:
            return ToolResult(
                success=False,
                error=f"could not determine the duration of {Path(input_path).name}",
            )
        width = info.get("width") or 1080
        height = info.get("height") or 1920

        window = inputs.get("window") or {}
        start = max(0.0, float(window.get("start", 0.0)))
        span = float(window.get("duration", _STRIP_DEFAULT_SECONDS))
        last_frame = self._last_frame_time(info)
        if start > last_frame:
            return ToolResult(
                success=False,
                error=(
                    f"window.start {start:g}s is past the last decodable frame "
                    f"({last_frame:.2f}s) of a {total:.2f}s video"
                ),
            )
        if total:
            span = min(span, max(0.05, total - start))

        fps = float(inputs.get("fps") or _STRIP_FPS)
        max_tiles = int(inputs.get("max_tiles") or _STRIP_MAX_TILES)
        notes: list[str] = []
        source_fps = info.get("fps")
        if source_fps and fps > source_fps:
            # Sampling faster than the source only duplicates frames, and the tile
            # count would overstate how much was actually looked at.
            notes.append(f"fps clamped to the source rate ({source_fps:.3g}); a denser strip would just repeat frames.")
            fps = source_fps
        n_tiles = video_motion.sample_count(span, fps)
        if n_tiles > max_tiles:
            # Thin the sampling rather than truncate the window: a silently
            # shortened window reads as "I looked at all of it".
            fps = max_tiles / span
            n_tiles = video_motion.sample_count(span, fps)
            notes.append(
                f"fps reduced to {fps:.2f} to fit max_tiles={max_tiles}; the full "
                f"{span:.2f}s window is still covered, just sampled less densely."
            )

        tile_w = int(inputs.get("tile_width") or _STRIP_TILE_WIDTH)
        grid = video_motion.grid_for(n_tiles, tile_width=tile_w, aspect=height / width, rows=1)
        out_path = Path(
            inputs.get("output_path") or (self._qa_dir(input_path) / f"strip_{start:g}-{start + span:g}s.jpg")
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        font_size = max(10, grid["tile_width"] // 15)
        chain = (
            f"fps={fps:g},scale={grid['tile_width']}:-2"
            + self._label_filter(start, enabled=bool(inputs.get("label_frames", True)), font_size=font_size)
            + f",tile={grid['cols']}x1:padding=3:margin=3:color=0x1c1c1c"
        )
        self._run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-ss",
                f"{start:.4f}",
                "-t",
                f"{span:.4f}",
                "-i",
                input_path,
                "-an",
                "-vf",
                chain,
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(out_path),
            ],
            timeout=300,
        )

        if not out_path.is_file():
            return ToolResult(success=False, error=f"strip not written: {out_path}")
        return ToolResult(
            success=True,
            data={
                "operation": "strip",
                "input": input_path,
                "strip_path": str(out_path),
                "window": {"start": round(start, 3), "duration": round(span, 3)},
                "tiles": n_tiles,
                "fps": round(fps, 3),
                "frame_interval_seconds": round(1 / fps, 4),
                "empty_cells": grid["empty_cells"],
                "bytes": out_path.stat().st_size,
                "notes": notes,
                "read_this": (
                    "Read the strip_path image. Frames are consecutive left-to-right, "
                    f"{1 / fps:.3f}s apart — compare neighbours to judge whether motion "
                    "actually progresses (a pan drifting, a zoom growing, a card "
                    "building in) rather than jumping or standing still."
                ),
            },
            artifacts=[str(out_path)],
        )

    def _find_plan(self, input_path: str, explicit: Optional[str]) -> Optional[Path]:
        if explicit:
            p = Path(explicit)
            return p if p.is_file() else None
        base = Path(input_path).parent
        for candidate in (
            base / "edit_decisions.json",
            base.parent / "edit_decisions.json",
            base.parent / "artifacts" / "edit_decisions.json",
        ):
            if candidate.is_file():
                return candidate
        return None

    def _vs_plan(self, inputs: dict[str, Any]) -> ToolResult:
        """Advisory diff: what the plan declared vs what the render measurably did."""
        input_path = inputs["input_path"]
        plan_path = self._find_plan(input_path, inputs.get("plan_path"))
        if plan_path is None:
            return ToolResult(
                success=False,
                error=(
                    "no edit_decisions.json found beside the input (looked in "
                    f"{Path(input_path).parent}, its parent, and parent/artifacts). "
                    "Pass plan_path explicitly."
                ),
            )
        try:
            doc = json.loads(plan_path.read_text())
        except (OSError, ValueError) as e:
            return ToolResult(success=False, error=f"could not read {plan_path}: {e}")
        if not isinstance(doc, dict):
            return ToolResult(success=False, error=f"{plan_path} is not a JSON object")

        measured = self._measure(input_path)
        if not measured["motion"]:
            return ToolResult(
                success=False,
                error="no frames measured; cannot diff a plan against nothing",
            )
        summary = self._summary_for(inputs, measured)
        knobs = inputs.get("analysis") or {}
        report = qa_plan_diff.diff(
            doc,
            motion=measured["motion"],
            cut_times=summary["cut_times"],
            frozen_runs=summary["frozen_runs"],
            duration=measured["duration"],
            flat_threshold=knobs.get("flat_threshold"),
        )
        return ToolResult(
            success=True,
            data={
                "operation": "vs_plan",
                "input": input_path,
                "plan_path": str(plan_path),
                "advisory": True,
                "measurement": {
                    "duration_seconds": summary["duration_seconds"],
                    "static_fraction": summary["static_fraction"],
                    "cut_count": summary["cut_count"],
                    "frozen_runs": summary["frozen_runs"],
                    "findings": summary["findings"],
                    "table": summary["table"],
                },
                **report,
                "read_this": (
                    "ADVISORY — nothing here fails the stage. Read `lines` worst-first. "
                    "'not-rendered' findings are certainties derived from the renderer's "
                    "own code and need no confirmation. 'flat' and 'cut-undetected' are "
                    "measurements: confirm with a `strip` over the named window before "
                    "acting on them. Record what you keep in final_review."
                ),
            },
        )
