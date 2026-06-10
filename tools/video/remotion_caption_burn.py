"""Remotion caption burn tool — **runtime-specific (Remotion-only)**.

Renders animated word-by-word captions onto a talking-head video using
the Remotion CaptionOverlay component. Falls back to FFmpeg subtitle
burning if Remotion is not available.

The tool:
1. Converts word-level transcript segments to Remotion WordCaption JSON
2. Writes a props file for the TalkingHead composition
3. Renders via ``npx remotion render``
4. Returns the captioned video path

Fallback: if Remotion is unavailable, burns subtitles at the bottom of
the frame using FFmpeg's ``subtitles`` filter with bold styling.

## Caption styling (Edits parity)

``style_preset`` selects a full caption look (font family/size/weight,
base + highlight colors, box on/off + radius, bottom offset, outline):

  - ``karaoke_classic`` (default) — current behavior, unchanged: boxed dark
    strip, white base, cyan active-word highlight. Maps to an EMPTY props
    bundle so legacy props files stay byte-identical.
  - ``black_pill``     — white extra-bold on a solid black rounded pill
    (the standard reels look).
  - ``yellow_pop``     — bold yellow active word over a white base, no box,
    black outline for legibility.
  - ``minimal_lower``  — small, bottom-third, no box.
  - ``bold_outline``   — thick dark text outline, no box.

Explicit inputs (``font_size``, ``highlight_color``, ``base_color``,
``font_family``, ``emphasis_color``, ``emphasis_scale``) override the
preset only when actually present in the inputs dict — schema defaults do
NOT clobber preset values.

``emphasis_words`` marks listed words (case-insensitive, punctuation
stripped — same matching as ``corrections``) with ``emphasis: true`` on
their WordCaption entries; the CaptionOverlay component renders those
bigger (``emphasis_scale``) and in ``emphasis_color``.

LIMITATION (honest): the FFmpeg fallback path IGNORES all of the above —
it burns one fixed bold-white bottom subtitle style. When a non-default
style was requested but the fallback ran, the result carries a
``style_warning`` and ``data.method == "ffmpeg_fallback"``.

## Runtime scope

This tool is **Remotion-specific** and deliberately has no HyperFrames
counterpart in Phase 1. Word-level caption burn parity on the HyperFrames
runtime is explicitly deferred work (see ``skills/core/hyperframes.md`` →
"What stays Remotion-only in Phase 1").

If a brief requires word-level/karaoke captions, lock
``render_runtime="remotion"`` at proposal even if the rest of the
composition would otherwise be a good fit for HyperFrames. Do NOT attempt
to bolt this tool onto a HyperFrames workspace — the TalkingHead
composition ID and ``WordCaption`` prop shape it emits are tied to the
React scene stack in ``remotion-composer/``.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolStability,
    ToolTier,
)


class RemotionCaptionBurn(BaseTool):
    name = "remotion_caption_burn"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "subtitle"
    provider = "remotion"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = ["cmd:ffmpeg", "cmd:ffprobe"]
    install_instructions = (
        "Remotion (optional, preferred): npm install in remotion-composer/\n"
        "FFmpeg (required for fallback): https://ffmpeg.org/download.html"
    )
    agent_skills = ["remotion-best-practices", "ffmpeg"]

    capabilities = [
        "burn_remotion_captions",
        "burn_ffmpeg_captions_fallback",
        "caption_style_presets",
        "word_emphasis",
    ]

    not_good_for = [
        "styled captions without Remotion — the FFmpeg fallback IGNORES "
        "style_preset, base_color, font_family, emphasis_words, emphasis_color, "
        "and emphasis_scale; it burns one fixed bold-white bottom style. Check "
        "data.method == 'remotion' to confirm the styles actually rendered",
        "HyperFrames workspaces — the TalkingHead composition and WordCaption "
        "prop shape are Remotion-only (Phase 1)",
    ]

    DEFAULT_STYLE_PRESET = "karaoke_classic"
    EMPHASIS_SCALE_MIN = 1.0
    EMPHASIS_SCALE_MAX = 2.0
    _WORD_PUNCT = ".,!?;:"  # matches the corrections-lookup strip set

    # Bundle keys are CaptionOverlay prop names (camelCase) — they pass straight
    # through to the composition as the `captionStyle` props object. The default
    # preset is an EMPTY bundle so legacy props files stay byte-identical and
    # the component's own defaults (= pre-preset behavior) apply.
    STYLE_PRESETS: dict[str, dict[str, Any]] = {
        "karaoke_classic": {},
        "black_pill": {
            "fontFamily": "Inter, 'Helvetica Neue', system-ui, sans-serif",
            "fontSize": 54,
            "fontWeight": 800,
            "color": "#FFFFFF",
            "highlightColor": "#FFFFFF",
            "backgroundColor": "#000000",
            "boxed": True,
            "borderRadius": 999,
            "bottomOffset": 110,
        },
        "yellow_pop": {
            "fontFamily": "Inter, system-ui, sans-serif",
            "fontSize": 56,
            "fontWeight": 800,
            "color": "#FFFFFF",
            "highlightColor": "#FFD60A",
            "boxed": False,
            "outlineColor": "#000000",
            "outlineWidth": 6,
            "bottomOffset": 100,
            # gold emphasis would vanish into the yellow highlight — use coral
            "emphasisColor": "#FF5C39",
        },
        "minimal_lower": {
            "fontFamily": "Inter, system-ui, sans-serif",
            "fontSize": 34,
            "fontWeight": 600,
            "color": "#FFFFFF",
            "highlightColor": "#FFFFFF",
            "boxed": False,
            "bottomOffset": 96,
        },
        "bold_outline": {
            "fontFamily": "'Arial Black', Inter, system-ui, sans-serif",
            "fontSize": 58,
            "fontWeight": 900,
            "color": "#FFFFFF",
            "boxed": False,
            "outlineColor": "#111111",
            "outlineWidth": 8,
            "bottomOffset": 100,
        },
    }

    input_schema = {
        "type": "object",
        "required": ["input_path", "output_path"],
        "properties": {
            "input_path": {
                "type": "string",
                "description": "Path to the input video (enhanced talking-head footage).",
            },
            "output_path": {
                "type": "string",
                "description": "Path for the output video with captions burned in.",
            },
            "segments": {
                "type": "array",
                "description": (
                    "Word-level transcript segments from transcriber tool. "
                    "Each segment has 'words' array with {word, start, end}."
                ),
            },
            "srt_path": {
                "type": "string",
                "description": (
                    "Path to an SRT file. Used as an alternative to segments. "
                    "If both provided, segments take priority."
                ),
            },
            "words_per_page": {
                "type": "integer",
                "default": 4,
                "description": "Words shown at once in the caption overlay.",
            },
            "font_size": {
                "type": "integer",
                "default": 52,
                "description": "Font size for captions.",
            },
            "highlight_color": {
                "type": "string",
                "default": "#22D3EE",
                "description": "Highlight color for the active word (hex).",
            },
            "style_preset": {
                "type": "string",
                "enum": [
                    "karaoke_classic", "black_pill", "yellow_pop",
                    "minimal_lower", "bold_outline",
                ],
                "default": "karaoke_classic",
                "description": (
                    "Caption look preset (Remotion path only — the FFmpeg "
                    "fallback ignores it). karaoke_classic = current behavior; "
                    "black_pill = white bold on a black rounded pill; "
                    "yellow_pop = yellow active word, white base, outlined; "
                    "minimal_lower = small, bottom-third, no box; "
                    "bold_outline = thick dark outline, no box. Explicit "
                    "font_size/highlight_color/base_color/font_family inputs "
                    "override the preset."
                ),
            },
            "base_color": {
                "type": "string",
                "description": (
                    "Base (non-active) word color (hex). Maps to the "
                    "CaptionOverlay `color` prop. Overrides the preset."
                ),
            },
            "font_family": {
                "type": "string",
                "description": (
                    "CSS font-family for captions. Maps to the CaptionOverlay "
                    "`fontFamily` prop. Overrides the preset."
                ),
            },
            "emphasis_words": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Words to render with the emphasis style (bigger + "
                    "emphasis_color). Matched case-insensitively with "
                    "surrounding punctuation stripped, same as corrections. "
                    "Remotion path only."
                ),
            },
            "emphasis_color": {
                "type": "string",
                "default": "#FFD60A",
                "description": "Color for emphasized words (hex).",
            },
            "emphasis_scale": {
                "type": "number",
                "default": 1.25,
                "minimum": 1.0,
                "maximum": 2.0,
                "description": "Font-size multiplier for emphasized words.",
            },
            "corrections": {
                "type": "object",
                "description": (
                    "Dictionary of word corrections for common misrecognitions. "
                    "Keys are the wrong word (case-insensitive), values are the "
                    "correct replacement. Example: {\"cloud\": \"Claude\"}."
                ),
            },
            "overlays": {
                "type": "array",
                "description": (
                    "Array of overlay objects to render on top of the video. "
                    "Each overlay has: type (text_card, stat_card, callout, "
                    "comparison, bar_chart, line_chart, pie_chart, kpi_grid, "
                    "hero_title, section_title, stat_reveal), in_seconds, "
                    "out_seconds, position (lower_third, upper_third, "
                    "left_panel, right_panel, full_overlay), and component-"
                    "specific props (text, stat, chartData, etc.). "
                    "See asset_manifest overlays from the asset-director."
                ),
            },
            "force_ffmpeg": {
                "type": "boolean",
                "default": False,
                "description": "Force FFmpeg fallback even if Remotion is available.",
            },
        },
    }

    resource_profile = ResourceProfile(cpu_cores=4, ram_mb=2048, vram_mb=0, disk_mb=500)
    idempotency_key_fields = [
        "input_path", "segments", "srt_path", "style_preset", "emphasis_words",
    ]
    side_effects = ["writes captioned video to output_path"]
    user_visible_verification = [
        "Play the output video and verify captions appear at the bottom of the frame",
        "Check that the active word is highlighted in the specified color",
        "Verify face is not occluded by caption text",
    ]

    # ------------------------------------------------------------------ #
    #  Remotion detection
    # ------------------------------------------------------------------ #

    def _find_remotion_root(self) -> Path | None:
        """Find the remotion-composer directory relative to the repo."""
        candidates = [
            Path.cwd() / "remotion-composer",
            Path(__file__).resolve().parent.parent.parent / "remotion-composer",
        ]
        for p in candidates:
            if (
                p.is_dir()
                and (p / "package.json").exists()
                and (p / "node_modules").is_dir()
            ):
                return p
        return None

    def _remotion_available(self) -> bool:
        return (
            shutil.which("npx") is not None
            and self._find_remotion_root() is not None
        )

    # ------------------------------------------------------------------ #
    #  Word caption conversion
    # ------------------------------------------------------------------ #

    def _segments_to_word_captions(
        self, segments: list[dict], corrections: dict[str, str] | None = None
    ) -> list[dict]:
        """Convert transcriber segments to [{word, startMs, endMs}, ...]."""
        captions: list[dict] = []
        corr = {k.lower(): v for k, v in (corrections or {}).items()}

        for seg in segments:
            words = seg.get("words", [])
            if words:
                for w in words:
                    raw = w["word"].strip()
                    fixed = corr.get(raw.lower().strip(".,!?;:"), raw)
                    # Preserve trailing punctuation from original
                    trailing = ""
                    if raw and raw[-1] in ".,!?;:":
                        trailing = raw[-1]
                    if fixed != raw and not fixed.endswith(trailing):
                        fixed = fixed + trailing
                    captions.append({
                        "word": fixed,
                        "startMs": int(w["start"] * 1000),
                        "endMs": int(w["end"] * 1000),
                    })
            elif "text" in seg:
                text_words = seg["text"].strip().split()
                dur = seg["end"] - seg["start"]
                per_word = dur / max(len(text_words), 1)
                for i, tw in enumerate(text_words):
                    fixed = corr.get(tw.lower().strip(".,!?;:"), tw)
                    captions.append({
                        "word": fixed,
                        "startMs": int((seg["start"] + i * per_word) * 1000),
                        "endMs": int((seg["start"] + (i + 1) * per_word) * 1000),
                    })
        return captions

    def _srt_to_word_captions(
        self, srt_path: str, corrections: dict[str, str] | None = None
    ) -> list[dict]:
        """Parse SRT file into word captions."""
        content = Path(srt_path).read_text(encoding="utf-8")
        blocks = re.split(r"\n\n+", content.strip())
        corr = {k.lower(): v for k, v in (corrections or {}).items()}
        captions: list[dict] = []

        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) < 3:
                continue
            m = re.match(
                r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*"
                r"(\d{2}):(\d{2}):(\d{2}),(\d{3})",
                lines[1],
            )
            if not m:
                continue
            start_ms = (
                int(m.group(1)) * 3600000
                + int(m.group(2)) * 60000
                + int(m.group(3)) * 1000
                + int(m.group(4))
            )
            end_ms = (
                int(m.group(5)) * 3600000
                + int(m.group(6)) * 60000
                + int(m.group(7)) * 1000
                + int(m.group(8))
            )
            text = " ".join(lines[2:]).strip()
            words = text.split()
            per_word = (end_ms - start_ms) / max(len(words), 1)
            for i, w in enumerate(words):
                fixed = corr.get(w.lower().strip(".,!?;:"), w)
                captions.append({
                    "word": fixed,
                    "startMs": int(start_ms + i * per_word),
                    "endMs": int(start_ms + (i + 1) * per_word),
                })
        return captions

    # ------------------------------------------------------------------ #
    #  Caption styling (presets + word emphasis)
    # ------------------------------------------------------------------ #

    def _build_caption_style(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Resolve style_preset + explicit overrides into CaptionOverlay props.

        Returns {} for the default preset with no overrides so the props file
        stays byte-identical to pre-preset versions (back-compat). Overrides
        apply only when the key is PRESENT in inputs — schema defaults must
        not clobber preset values.
        """
        preset = inputs.get("style_preset", self.DEFAULT_STYLE_PRESET)
        style = dict(self.STYLE_PRESETS[preset])
        if "font_size" in inputs:
            style["fontSize"] = int(inputs["font_size"])
        if "highlight_color" in inputs:
            style["highlightColor"] = inputs["highlight_color"]
        if "base_color" in inputs:
            style["color"] = inputs["base_color"]
        if "font_family" in inputs:
            style["fontFamily"] = inputs["font_family"]
        if "emphasis_color" in inputs:
            style["emphasisColor"] = inputs["emphasis_color"]
        if "emphasis_scale" in inputs:
            style["emphasisScale"] = float(inputs["emphasis_scale"])
        return style

    def _apply_emphasis(
        self, captions: list[dict], emphasis_words: list[str]
    ) -> int:
        """Set emphasis=True on captions matching emphasis_words (in place).

        Matching mirrors the corrections lookup: case-insensitive with
        surrounding punctuation stripped. Non-matching entries are left
        untouched (no emphasis key) so the WordCaption shape is unchanged
        when the feature is unused. Returns the number of words flagged.
        """
        targets = {
            w.lower().strip(self._WORD_PUNCT) for w in emphasis_words if w.strip()
        }
        if not targets:
            return 0
        count = 0
        for cap in captions:
            if cap["word"].lower().strip(self._WORD_PUNCT) in targets:
                cap["emphasis"] = True
                count += 1
        return count

    @staticmethod
    def _build_props(
        video_filename: str,
        captions: list[dict],
        overlays: list[dict] | None,
        words_per_page: int,
        font_size: int,
        highlight_color: str,
        caption_style: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Props JSON for the TalkingHead composition. captionStyle is only
        emitted when non-empty (back-compat: default look = legacy props)."""
        props: dict[str, Any] = {
            "videoSrc": f"public/talking-head/{video_filename}",
            "captions": captions,
            "overlays": overlays or [],
            "wordsPerPage": words_per_page,
            "fontSize": font_size,
            "highlightColor": highlight_color,
        }
        if caption_style:
            props["captionStyle"] = caption_style
        return props

    # ------------------------------------------------------------------ #
    #  Remotion render
    # ------------------------------------------------------------------ #

    def _render_remotion(
        self,
        input_path: str,
        output_path: str,
        captions: list[dict],
        words_per_page: int,
        font_size: int,
        highlight_color: str,
        overlays: list[dict] | None = None,
        caption_style: dict[str, Any] | None = None,
    ) -> ToolResult:
        root = self._find_remotion_root()
        if root is None:
            return ToolResult(success=False, error="Remotion root not found")

        # Get video duration in frames
        dur_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            input_path,
        ]
        dur_result = self.run_command(dur_cmd)
        dur_out = dur_result.stdout
        duration_s = float(dur_out.strip().split("\n")[0])
        total_frames = math.ceil(duration_s * 30)

        # Detect video dimensions
        dim_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x",
            input_path,
        ]
        dim_result = self.run_command(dim_cmd)
        dim_parts = dim_result.stdout.strip().split("x")
        width = int(dim_parts[0])
        height = int(dim_parts[1])

        # Copy video to Remotion public folder
        pub_dir = root / "public" / "talking-head"
        pub_dir.mkdir(parents=True, exist_ok=True)
        video_filename = Path(input_path).name
        dest_video = pub_dir / video_filename
        shutil.copy2(input_path, dest_video)

        # Build props JSON
        props = self._build_props(
            video_filename, captions, overlays,
            words_per_page, font_size, highlight_color, caption_style,
        )
        props_dir = root / "public" / "demo-props"
        props_dir.mkdir(parents=True, exist_ok=True)
        props_file = props_dir / f"caption-burn-{Path(input_path).stem}.json"
        props_file.write_text(json.dumps(props, indent=2), encoding="utf-8")

        # Render (use npx.cmd on Windows for subprocess compatibility)
        import sys
        npx_bin = "npx.cmd" if sys.platform == "win32" else "npx"
        render_cmd = [
            npx_bin, "remotion", "render",
            "TalkingHead",
            f"--props={props_file.relative_to(root)}",
            f"--width={width}", f"--height={height}", "--fps=30",
            f"--frames=0-{total_frames - 1}",
            "--codec=h264", "--crf=18",
            f"--output={str(Path(output_path).resolve())}",
        ]
        self.run_command(render_cmd, cwd=str(root))

        if not Path(output_path).exists():
            return ToolResult(success=False, error="Remotion render produced no output")

        return ToolResult(
            success=True,
            data={
                "method": "remotion",
                "output": output_path,
                "duration_seconds": round(duration_s, 2),
                "total_frames": total_frames,
                "caption_count": len(captions),
                "overlay_count": len(overlays or []),
                "words_per_page": words_per_page,
            },
            artifacts=[output_path],
        )

    # ------------------------------------------------------------------ #
    #  FFmpeg fallback
    # ------------------------------------------------------------------ #

    def _render_ffmpeg(
        self,
        input_path: str,
        output_path: str,
        captions: list[dict],
    ) -> ToolResult:
        """Fall back to FFmpeg subtitle burning at bottom of frame."""
        # Generate temporary SRT from word captions
        tmp_srt = Path(output_path).parent / f"_tmp_captions_{int(time.time())}.srt"
        tmp_srt.parent.mkdir(parents=True, exist_ok=True)

        srt_lines = []
        idx = 1
        # Group into pages of ~4 words
        page_size = 4
        for i in range(0, len(captions), page_size):
            page = captions[i : i + page_size]
            text = " ".join(c["word"] for c in page)
            start = page[0]["startMs"]
            end = page[-1]["endMs"]
            srt_lines.append(str(idx))
            srt_lines.append(
                f"{self._ms_to_srt(start)} --> {self._ms_to_srt(end)}"
            )
            srt_lines.append(text)
            srt_lines.append("")
            idx += 1

        tmp_srt.write_text("\n".join(srt_lines), encoding="utf-8")

        # Escape path for FFmpeg subtitles filter (Windows colon issue)
        srt_escaped = str(tmp_srt).replace("\\", "/").replace(":", "\\:")

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", (
                f"subtitles='{srt_escaped}'"
                ":force_style='FontName=Segoe UI,FontSize=24,Bold=1,"
                "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                "Outline=3,Shadow=2,Alignment=2,MarginV=100'"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            output_path,
        ]
        self.run_command(cmd)

        # Clean up temp SRT
        try:
            tmp_srt.unlink()
        except OSError:
            pass

        if not Path(output_path).exists():
            return ToolResult(success=False, error="FFmpeg subtitle burn produced no output")

        return ToolResult(
            success=True,
            data={
                "method": "ffmpeg_fallback",
                "output": output_path,
                "caption_count": len(captions),
                "note": "Used FFmpeg fallback. Install Remotion for animated captions.",
            },
            artifacts=[output_path],
        )

    @staticmethod
    def _ms_to_srt(ms: int) -> str:
        h = ms // 3600000
        m = (ms % 3600000) // 60000
        s = (ms % 60000) // 1000
        rem = ms % 1000
        return f"{h:02d}:{m:02d}:{s:02d},{rem:03d}"

    # ------------------------------------------------------------------ #
    #  Main execute
    # ------------------------------------------------------------------ #

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        input_path = inputs["input_path"]
        output_path = inputs["output_path"]
        corrections = inputs.get("corrections")
        force_ffmpeg = inputs.get("force_ffmpeg", False)
        words_per_page = inputs.get("words_per_page", 4)
        font_size = inputs.get("font_size", 52)
        highlight_color = inputs.get("highlight_color", "#22D3EE")
        style_preset = inputs.get("style_preset", self.DEFAULT_STYLE_PRESET)
        emphasis_words = inputs.get("emphasis_words") or []

        # Validate styling inputs before any filesystem work
        if style_preset not in self.STYLE_PRESETS:
            return ToolResult(
                success=False,
                error=(
                    f"style_preset must be one of {sorted(self.STYLE_PRESETS)}; "
                    f"got '{style_preset}'."
                ),
            )
        if not isinstance(emphasis_words, list) or any(
            not isinstance(w, str) or not w.strip() for w in emphasis_words
        ):
            return ToolResult(
                success=False,
                error="emphasis_words must be a list of non-empty strings.",
            )
        for key in ("base_color", "font_family", "emphasis_color"):
            if key in inputs and not isinstance(inputs[key], str):
                return ToolResult(success=False, error=f"{key} must be a string.")
        if "emphasis_scale" in inputs:
            try:
                scale = float(inputs["emphasis_scale"])
            except (TypeError, ValueError):
                scale = float("nan")
            if not (self.EMPHASIS_SCALE_MIN <= scale <= self.EMPHASIS_SCALE_MAX):
                return ToolResult(
                    success=False,
                    error=(
                        f"emphasis_scale must be a number between "
                        f"{self.EMPHASIS_SCALE_MIN} and {self.EMPHASIS_SCALE_MAX}."
                    ),
                )

        caption_style = self._build_caption_style(inputs)

        if not Path(input_path).exists():
            return ToolResult(success=False, error=f"Input video not found: {input_path}")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        start = time.time()

        # Build word captions from segments or SRT
        segments = inputs.get("segments")
        srt_path = inputs.get("srt_path")

        if segments:
            captions = self._segments_to_word_captions(segments, corrections)
        elif srt_path:
            captions = self._srt_to_word_captions(srt_path, corrections)
        else:
            return ToolResult(
                success=False,
                error="Provide either 'segments' (from transcriber) or 'srt_path'.",
            )

        if not captions:
            return ToolResult(success=False, error="No caption words extracted.")

        emphasis_count = self._apply_emphasis(captions, emphasis_words)

        overlays = inputs.get("overlays")

        # Choose render method
        if not force_ffmpeg and self._remotion_available():
            result = self._render_remotion(
                input_path, output_path, captions,
                words_per_page, font_size, highlight_color,
                overlays=overlays,
                caption_style=caption_style,
            )
            if result.success:
                result.data["style_preset"] = style_preset
                result.data["emphasis_word_count"] = emphasis_count
        else:
            result = self._render_ffmpeg(input_path, output_path, captions)
            styles_requested = (
                style_preset != self.DEFAULT_STYLE_PRESET
                or bool(emphasis_words)
                or any(
                    k in inputs
                    for k in (
                        "base_color", "font_family",
                        "emphasis_color", "emphasis_scale",
                    )
                )
            )
            if result.success and styles_requested:
                result.data["style_warning"] = (
                    "FFmpeg fallback ignores style_preset/base_color/"
                    "font_family/emphasis_words — captions were burned with "
                    "the fixed bold-white bottom style. Install Remotion "
                    "(npm install in remotion-composer/) for styled captions."
                )

        result.duration_seconds = round(time.time() - start, 2)
        return result
