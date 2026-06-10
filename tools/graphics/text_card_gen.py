"""Text Card Gen — render styled text to a transparent PNG for overlay use.

Instagram Edits parity: the "text tool + text animations" gap on the FFmpeg path.
FFmpeg has no timed styled-text primitive, so the bridge is: bake the styled text
HERE as a tight transparent PNG, then place + animate it with overlays[] and
overlays[].keyframes (keyframe_animate presets: slide_in_*, fade_in/out, pop, ...).
The compose stage renders the motion; this tool only bakes the text pixels.

Presets:
  - bold_center         big bold white text with a thin dark stroke (hero/title)
  - lower_third         left-aligned white text on a dark rounded block
  - black_pill_caption  white bold text on a black rounded pill PER LINE — the
                        karaoke-caption look
  - outline_pop         white fill + thick dark outline (pop text)
  - minimal_clean       plain white text, no stroke, no box

Design notes / documented limitations:
  - Output is sized TIGHT to content (text block + box padding only) so overlay
    positioning math downstream is predictable; data reports {width, height, lines}.
  - Greedy word-wrap at max_width_px; no hyphenation — a single word wider than
    max_width_px stays on its own (overflowing) line and the canvas grows to fit.
  - Font resolution: font_path override > first bold sans found in common system
    font dirs (macOS /System/Library/Fonts + Supplemental + /Library/Fonts, Linux
    DejaVu/Liberation, Windows Arial/Segoe) > PIL's built-in default font. The
    PIL default is NOT bold (and ignores font_size on Pillow < 10.1) — pass
    font_path when exact typography matters; data.font reports what was used.
  - Monochrome glyph rendering only: emoji render as outlines/tofu, not color.
"""

from __future__ import annotations

import json
import os
import time
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


_ALIGNS = ("left", "center", "right")

# Default box used when a boxless preset gets a user-supplied `box`.
_BOX_BASE = {"color": "#000000", "opacity": 1.0, "corner_radius": 12, "padding": [24, 14], "per_line": False}

PRESETS: dict[str, dict[str, Any]] = {
    "bold_center": {
        "font_size": 72, "fill": "#FFFFFF",
        "stroke_width": 2, "stroke_fill": "#000000",
        "align": "center", "box": None,
    },
    "lower_third": {
        "font_size": 44, "fill": "#FFFFFF",
        "stroke_width": 0, "stroke_fill": "#000000",
        "align": "left",
        "box": {"color": "#101418", "opacity": 0.85, "corner_radius": 12, "padding": [28, 16], "per_line": False},
    },
    "black_pill_caption": {
        "font_size": 54, "fill": "#FFFFFF",
        "stroke_width": 0, "stroke_fill": "#000000",
        "align": "center",
        "box": {"color": "#000000", "opacity": 1.0, "corner_radius": 18, "padding": [26, 14], "per_line": True},
    },
    "outline_pop": {
        "font_size": 80, "fill": "#FFFFFF",
        "stroke_width": 8, "stroke_fill": "#111111",
        "align": "center", "box": None,
    },
    "minimal_clean": {
        "font_size": 48, "fill": "#FFFFFF",
        "stroke_width": 0, "stroke_fill": "#000000",
        "align": "center", "box": None,
    },
}

# (path, is_ttc_collection) — first hit wins; .ttc files get a bold-face scan.
_FONT_CANDIDATES = (
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", False),
    ("/Library/Fonts/Arial Bold.ttf", False),
    ("/System/Library/Fonts/Helvetica.ttc", True),
    ("/System/Library/Fonts/HelveticaNeue.ttc", True),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", False),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", False),
    ("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", False),
    ("C:/Windows/Fonts/arialbd.ttf", False),
    ("C:/Windows/Fonts/segoeuib.ttf", False),
)


class TextCardGen(BaseTool):
    name = "text_card_gen"
    version = "0.1.0"
    tier = ToolTier.CORE
    # Deliberately "graphics" (like code_snippet), NOT "image_generation":
    # image_selector must never route AI-image requests to this deterministic renderer.
    capability = "graphics"
    provider = "pillow"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["python:PIL"]
    install_instructions = "pip install Pillow"
    agent_skills: list[str] = []

    capabilities = ["styled_text_png", "caption_pill", "lower_third", "title_card_text"]
    supports = {"presets": list(PRESETS), "word_wrap": True, "transparent_alpha": True}
    best_for = [
        "pair with keyframe_animate presets (slide_in/fade/pop) for animated text on the ffmpeg runtime",
        "karaoke black-pill captions, lower thirds, and bold titles as tight transparent overlay PNGs",
    ]
    not_good_for = [
        "HDR sources — output is 8-bit SDR; detect with is_hdr_source() and handle HDR per AGENT_GUIDE before using this tool",
        "AI/stylized imagery — deterministic local text rendering only (not an image_generation provider)",
        "burning text into video directly — render the PNG here, then place it via overlays[] at compose",
        "color emoji — glyphs render monochrome/tofu",
    ]
    fallback_tools: list[str] = []

    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "description": "Text to render; use \\n for manual line breaks."},
            "preset": {"type": "string", "enum": list(PRESETS), "default": "bold_center"},
            "font_path": {
                "type": "string",
                "description": "Optional .ttf/.ttc override; default resolves a bold sans from system font dirs, "
                               "falling back to PIL's default font.",
            },
            "font_size": {"type": "integer", "minimum": 1},
            "fill": {"type": "string", "description": "Text color (any PIL color, e.g. #FFFFFF)."},
            "stroke_width": {"type": "integer", "minimum": 0},
            "stroke_fill": {"type": "string"},
            "align": {"type": "string", "enum": list(_ALIGNS)},
            "box": {
                "type": "object",
                "description": "Background box behind the text: color, opacity 0-1, corner_radius px, "
                               "padding (int or [pad_x, pad_y]), per_line (pill per line vs one block).",
                "properties": {
                    "color": {"type": "string"},
                    "opacity": {"type": "number", "minimum": 0, "maximum": 1},
                    "corner_radius": {"type": "integer", "minimum": 0},
                    "padding": {"description": "int or [pad_x, pad_y]"},
                    "per_line": {"type": "boolean"},
                },
            },
            "max_width_px": {
                "type": "integer", "minimum": 1,
                "description": "Word-wrap width for the TEXT block; ~900-960 fits a 1080-wide reel. Omit = no wrap.",
            },
            "line_spacing": {"type": "number", "default": 1.15},
            "output_path": {"type": "string", "description": "Defaults to text_card.png"},
            # provenance registration (optional)
            "asset_manifest_path": {
                "type": "string",
                "description": "Optional: append the PNG to this asset_manifest as an image asset (validated, written).",
            },
            "scene_id": {"type": "string", "default": "overlay", "description": "scene_id for the registered asset"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=20)
    idempotency_key_fields = ["text", "preset", "font_size", "font_path", "max_width_px"]
    side_effects = ["writes a PNG to output_path", "may append to an asset_manifest"]
    user_visible_verification = [
        "Open the PNG over a colored background; confirm transparency, wrapping, and pill/box styling",
    ]

    DEFAULT_LINE_SPACING = 1.15

    # ---- execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            cfg = self._validate(inputs)
        except _CardInputError as e:
            return ToolResult(success=False, error=str(e))

        try:
            from PIL import Image, ImageColor, ImageDraw, ImageFont  # noqa: F401
        except ImportError:
            return ToolResult(success=False, error="Pillow required. Run: pip install Pillow")

        start = time.time()
        try:
            self._validate_colors(cfg)
            font, font_label = self._resolve_font(cfg["font_path"], cfg["font_size"])
            img, lines = self._render(cfg, font)
        except _CardInputError as e:
            return ToolResult(success=False, error=str(e))

        out_path = cfg["output_path"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG")
        if not out_path.exists() or out_path.stat().st_size == 0:
            return ToolResult(success=False, error="text_card_gen produced no output.")

        data: dict[str, Any] = {
            "output": str(out_path),
            "output_path": str(out_path),
            "width": img.width,
            "height": img.height,
            "lines": len(lines),
            "preset": cfg["preset"],
            "font": font_label,
        }
        artifacts = [str(out_path)]

        am_path = inputs.get("asset_manifest_path")
        if am_path:
            reg_err = self._register_asset(Path(am_path), cfg, out_path, img.width, img.height)
            if reg_err:
                # the PNG exists and is valid; only the registration failed
                data["asset_manifest_warning"] = reg_err
            else:
                data["asset_manifest_path"] = str(am_path)
                artifacts.append(str(am_path))

        return ToolResult(
            success=True,
            data=data,
            artifacts=artifacts,
            duration_seconds=round(time.time() - start, 2),
        )

    # ---- validation (pure: no PIL, no I/O writes) ----

    def _validate(self, inputs: dict[str, Any]) -> dict[str, Any]:
        text = inputs.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _CardInputError("text (non-empty string) is required.")

        preset = inputs.get("preset", "bold_center")
        if preset not in PRESETS:
            raise _CardInputError(f"preset must be one of {tuple(PRESETS)}; got {preset!r}.")
        p = PRESETS[preset]

        font_size = inputs.get("font_size", p["font_size"])
        if isinstance(font_size, bool) or not isinstance(font_size, int) or font_size <= 0:
            raise _CardInputError(f"font_size must be a positive integer; got {font_size!r}.")

        stroke_width = inputs.get("stroke_width", p["stroke_width"])
        if isinstance(stroke_width, bool) or not isinstance(stroke_width, int) or stroke_width < 0:
            raise _CardInputError(f"stroke_width must be an integer >= 0; got {stroke_width!r}.")

        line_spacing = inputs.get("line_spacing", self.DEFAULT_LINE_SPACING)
        if not isinstance(line_spacing, (int, float)) or isinstance(line_spacing, bool) or line_spacing <= 0:
            raise _CardInputError(f"line_spacing must be a number > 0; got {line_spacing!r}.")

        max_width = inputs.get("max_width_px")
        if max_width is not None and (
            isinstance(max_width, bool) or not isinstance(max_width, int) or max_width <= 0
        ):
            raise _CardInputError(f"max_width_px must be a positive integer; got {max_width!r}.")

        align = inputs.get("align", p["align"])
        if align not in _ALIGNS:
            raise _CardInputError(f"align must be one of {_ALIGNS}; got {align!r}.")

        box = self._validate_box(inputs.get("box"), p["box"])

        font_path = inputs.get("font_path")
        if font_path is not None:
            if not isinstance(font_path, str) or not Path(font_path).is_file():
                raise _CardInputError(f"font_path not found: {font_path!r}")

        return {
            "text": text,
            "preset": preset,
            "font_size": font_size,
            "fill": inputs.get("fill", p["fill"]),
            "stroke_width": stroke_width,
            "stroke_fill": inputs.get("stroke_fill", p["stroke_fill"]),
            "align": align,
            "box": box,
            "max_width_px": max_width,
            "line_spacing": float(line_spacing),
            "font_path": font_path,
            "output_path": Path(inputs.get("output_path") or "text_card.png"),
            "scene_id": str(inputs.get("scene_id", "overlay")),
        }

    def _validate_box(self, box_in: Any, preset_box: Optional[dict]) -> Optional[dict[str, Any]]:
        if box_in is None:
            box = dict(preset_box) if preset_box else None
        else:
            if not isinstance(box_in, dict):
                raise _CardInputError("box must be an object {color, opacity, corner_radius, padding, per_line}.")
            box = {**(preset_box or _BOX_BASE), **box_in}
        if box is None:
            return None

        opacity = box.get("opacity", 1.0)
        if not isinstance(opacity, (int, float)) or isinstance(opacity, bool) or not (0 <= opacity <= 1):
            raise _CardInputError(f"box.opacity must be a number in [0, 1]; got {opacity!r}.")
        radius = box.get("corner_radius", 0)
        if isinstance(radius, bool) or not isinstance(radius, int) or radius < 0:
            raise _CardInputError(f"box.corner_radius must be an integer >= 0; got {radius!r}.")
        if not isinstance(box.get("color", "#000000"), str):
            raise _CardInputError("box.color must be a color string.")
        return {
            "color": box.get("color", "#000000"),
            "opacity": float(opacity),
            "corner_radius": radius,
            "padding": self._norm_padding(box.get("padding", [24, 14])),
            "per_line": bool(box.get("per_line", False)),
        }

    @staticmethod
    def _norm_padding(value: Any) -> tuple[int, int]:
        if isinstance(value, bool):
            raise _CardInputError("box.padding must be an int or [pad_x, pad_y].")
        if isinstance(value, int):
            if value < 0:
                raise _CardInputError("box.padding must be >= 0.")
            return (value, value)
        if (
            isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value)
        ):
            return (int(value[0]), int(value[1]))
        raise _CardInputError(f"box.padding must be an int or [pad_x, pad_y] of ints >= 0; got {value!r}.")

    def _validate_colors(self, cfg: dict[str, Any]) -> None:
        from PIL import ImageColor

        for label, value in (
            ("fill", cfg["fill"]),
            ("stroke_fill", cfg["stroke_fill"]),
            ("box.color", cfg["box"]["color"] if cfg["box"] else "#000000"),
        ):
            try:
                ImageColor.getrgb(value)
            except (ValueError, TypeError):
                raise _CardInputError(f"{label} is not a valid color: {value!r}")

    # ---- font resolution ----

    def _resolve_font(self, font_path: Optional[str], size: int):
        """Resolve a bold sans: explicit override > system candidates > PIL default."""
        from PIL import ImageFont

        if font_path:
            font = self._load_face(font_path, size)
            if font is None:
                raise _CardInputError(f"could not load font_path: {font_path!r}")
            return font, f"{Path(font_path).name} ({' '.join(font.getname())})"

        for cand, is_ttc in _FONT_CANDIDATES:
            if not Path(cand).is_file():
                continue
            font = self._bold_ttc_face(cand, size) if is_ttc else self._load_face(cand, size)
            if font is not None:
                return font, f"{Path(cand).name} ({' '.join(font.getname())})"

        # Documented fallback: PIL's bundled default. Not bold; Pillow < 10.1
        # ignores the size argument (bitmap font), so cards come out small there.
        try:
            return ImageFont.load_default(size=size), "PIL-default"
        except TypeError:
            return ImageFont.load_default(), "PIL-default (bitmap, fixed size)"

    @staticmethod
    def _load_face(path: str, size: int):
        from PIL import ImageFont

        try:
            return ImageFont.truetype(path, size)
        except OSError:
            return None

    @staticmethod
    def _bold_ttc_face(path: str, size: int):
        """Scan a .ttc collection for the face whose style name is exactly Bold."""
        from PIL import ImageFont

        for i in range(24):
            try:
                font = ImageFont.truetype(path, size, index=i)
            except OSError:
                break
            if (font.getname()[1] or "").strip().lower() == "bold":
                return font
        return None

    # ---- rendering ----

    def _render(self, cfg: dict[str, Any], font):
        from PIL import Image, ImageColor, ImageDraw

        sw = cfg["stroke_width"]
        measurer = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        try:
            ascent, descent = font.getmetrics()
        except AttributeError:
            # PIL bitmap fallback font: approximate metrics, no stroke support
            sw = 0
            bbox = measurer.textbbox((0, 0), "Ag", font=font)
            ascent, descent = bbox[3] - bbox[1], 0

        lines = self._wrap(measurer, cfg["text"], font, cfg["max_width_px"], sw)

        widths: list[int] = []
        offsets: list[int] = []
        for ln in lines:
            if ln:
                bbox = measurer.textbbox((0, 0), ln, font=font, stroke_width=sw)
                widths.append(bbox[2] - bbox[0])
                offsets.append(bbox[0])
            else:
                widths.append(0)
                offsets.append(0)
        text_w = max(widths)
        if text_w <= 0:
            raise _CardInputError("text rendered to zero width; nothing to draw.")

        base_line_h = ascent + descent
        line_step = max(1, int(round(base_line_h * cfg["line_spacing"])))
        align = cfg["align"]
        box = cfg["box"]
        text_kwargs = {"font": font, "fill": cfg["fill"]}
        if sw > 0:
            text_kwargs.update(stroke_width=sw, stroke_fill=cfg["stroke_fill"])

        if box and box["per_line"]:
            # one rounded pill hugging each line — the karaoke-caption look
            pad_x, pad_y = box["padding"]
            rgb = ImageColor.getrgb(box["color"])[:3]
            box_rgba = (*rgb, int(round(box["opacity"] * 255)))
            pill_h = base_line_h + 2 * sw + 2 * pad_y
            gap = max(0, line_step - base_line_h)
            canvas_w = max(w + 2 * pad_x for w in widths)
            canvas_h = len(lines) * pill_h + (len(lines) - 1) * gap
            img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            y = 0
            for ln, w, off in zip(lines, widths, offsets):
                if ln:
                    pw = w + 2 * pad_x
                    x0 = {"left": 0, "center": (canvas_w - pw) // 2, "right": canvas_w - pw}[align]
                    radius = min(box["corner_radius"], pill_h // 2, pw // 2)
                    draw.rounded_rectangle([x0, y, x0 + pw - 1, y + pill_h - 1], radius=radius, fill=box_rgba)
                    draw.text((x0 + pad_x - off, y + pad_y + sw), ln, **text_kwargs)
                y += pill_h + gap
            return img, lines

        block_h = (len(lines) - 1) * line_step + base_line_h + 2 * sw
        if box:
            pad_x, pad_y = box["padding"]
            rgb = ImageColor.getrgb(box["color"])[:3]
            box_rgba = (*rgb, int(round(box["opacity"] * 255)))
            canvas_w, canvas_h = text_w + 2 * pad_x, block_h + 2 * pad_y
            img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            radius = min(box["corner_radius"], canvas_h // 2, canvas_w // 2)
            draw.rounded_rectangle([0, 0, canvas_w - 1, canvas_h - 1], radius=radius, fill=box_rgba)
            origin_x, origin_y = pad_x, pad_y
        else:
            canvas_w, canvas_h = text_w, block_h
            img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            origin_x, origin_y = 0, 0

        for i, (ln, w, off) in enumerate(zip(lines, widths, offsets)):
            if not ln:
                continue
            ax = {"left": 0, "center": (text_w - w) // 2, "right": text_w - w}[align]
            draw.text((origin_x + ax - off, origin_y + sw + i * line_step), ln, **text_kwargs)
        return img, lines

    @staticmethod
    def _wrap(measurer, text: str, font, max_width: Optional[int], stroke_width: int) -> list[str]:
        """Greedy word-wrap per paragraph. No wrap when max_width is None."""
        lines: list[str] = []
        for para in text.split("\n"):
            if max_width is None:
                lines.append(para.rstrip())
                continue
            words = para.split()
            if not words:
                lines.append("")
                continue
            cur = words[0]
            for word in words[1:]:
                candidate = f"{cur} {word}"
                if measurer.textlength(candidate, font=font) + 2 * stroke_width <= max_width:
                    cur = candidate
                else:
                    lines.append(cur)
                    cur = word
            lines.append(cur)
        # drop a trailing all-empty tail but keep interior blank lines
        while len(lines) > 1 and not lines[-1]:
            lines.pop()
        return lines

    # ---- asset_manifest registration ----

    def _register_asset(
        self, path: Path, cfg: dict[str, Any], out: Path, width: int, height: int
    ) -> Optional[str]:
        """Append the PNG to an asset_manifest with provenance, validate, write back.
        Returns an error string on failure (manifest left untouched), else None."""
        if not path.exists():
            return f"asset_manifest_path not found: {path}"
        try:
            doc = json.loads(path.read_text())
        except Exception as e:
            return f"could not read asset_manifest: {e}"
        if not isinstance(doc, dict) or not isinstance(doc.get("assets"), list):
            return "asset_manifest is not a valid manifest object with an assets[] list."

        snippet = cfg["text"].replace("\n", " ")
        if len(snippet) > 60:
            snippet = snippet[:57] + "..."
        entry = {
            "id": f"textcard-{cfg['preset']}-{len(doc['assets']) + 1}",
            "type": "image",
            "path": str(out),
            "source_tool": "text_card_gen",
            "scene_id": cfg["scene_id"],
            "subtype": cfg["preset"],
            "generation_summary": f"text_card_gen {cfg['preset']} PNG for text: {snippet!r}",
            "format": "png",
            "resolution": f"{width}x{height}",
        }
        doc["assets"].append(entry)
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("asset_manifest", doc)
        except Exception as e:
            return f"text-card entry did not validate against asset_manifest schema: {e}"
        self._write_json(path, doc)
        return None

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)


class _CardInputError(Exception):
    """Bad parameters for a text card (validated before any rendering)."""
