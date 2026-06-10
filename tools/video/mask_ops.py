"""Mask Ops — region blur / spotlight / image-mask / masked reveal that bake into new clips.

Instagram Edits' "masks on the main track" that nothing else here covers (verified gap: no
alphamerge/geq/shape-mask support anywhere). Each op reads source clip(s) and writes a NEW
derived clip (the motion_ops pattern), which then becomes a `cuts[].source` at the edit
stage. The derived clip is RE-PROBED and can be registered into an asset_manifest with
provenance.

Ops:
  - blur_region   blur INSIDE a rect/circle region (crop + boxblur + overlay back), with an
                  optional start/end time window (overlay enable=between(t,..))
  - dim_outside   spotlight: darken everything OUTSIDE the region (lutyuv luma multiply +
                  original region overlaid back on top), dim_factor 0..1
  - image_mask    grayscale/alpha PNG as a mask over the clip: white=keep, black=transparent
                  (alphamerge), invert flag; output carries an alpha channel
  - reveal_wipe   masked reveal transition between two SAME-SIZE clips via xfade
                  (wipeleft/wiperight/wipeup/wipedown/circleopen)

Coordinates are NORMALIZED 0..1, not pixels: rect {x,y,w,h} as fractions of frame W/H;
circle {cx,cy} as fractions of W/H and r as a fraction of min(W,H). They are converted to
even pixel values after probing the source, so the same region spec works on any resolution.

Design (Edits-parity, masks-on-main-track gap):
  - blur_region uses the crop+boxblur+overlay-back approach (robust across ffmpeg builds)
    rather than geq alpha tricks; boxblur luma_power=2 approximates a gaussian. Circle
    shapes cut the patch with a geq alpha circle (geq is CPU-heavy but the patch is small).
  - image_mask encodes qtrle .mov — the SAME lossless-RGBA encode object_cutout uses for
    its alpha cutouts — so every alpha-carrying derived clip in the project is one format
    the compose path already accepts. ProRes 4444 .mov or VP9 .webm also carry alpha;
    qtrle was chosen for parity with object_cutout (and it needs no extra encoders).
  - reveal_wipe OVERLAPS with future video_compose timeline transitions by design: it is
    for baking a ONE-OFF pair reveal into a single derived clip, not for sequencing a
    whole timeline. When video_compose grows transitions, prefer it for multi-cut edits.
  - `lossless: true` encodes blur_region/dim_outside/reveal_wipe with libx264 -qp 0 so
    pixels outside the masked region survive bit-exact (useful for intermediate bakes
    that get re-encoded downstream, and for verification).

Documented limitations:
  - 8-bit SDR pipeline (see not_good_for): HDR sources get tonemapped/clipped.
  - blur_region/dim_outside shape edges are HARD; for feathered/soft edges use image_mask
    with a feathered PNG.
  - circle regions must fit fully inside the frame (bounding box clamping is rejected,
    not silently adjusted).
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


class MaskOps(BaseTool):
    name = "mask_ops"
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

    OPERATIONS = ("blur_region", "dim_outside", "image_mask", "reveal_wipe")
    SHAPES = ("rect", "circle")
    # direction -> xfade transition name
    WIPES = {
        "left": "wipeleft",
        "right": "wiperight",
        "up": "wipeup",
        "down": "wipedown",
        "circle": "circleopen",
    }
    STRENGTH_DEFAULT = 10
    STRENGTH_MAX = 64
    MIN_REGION_PX = 8

    capabilities = list(OPERATIONS)
    supports = {op: True for op in OPERATIONS}
    best_for = [
        "blur a face/plate/UI region, spotlight a subject, PNG shape masks, one-off masked reveals",
    ]
    not_good_for = [
        "HDR sources — output is 8-bit SDR; detect with is_hdr_source() and handle HDR per AGENT_GUIDE before using this tool",
        "feathered/gradient mask edges with rect/circle shapes (hard edges only — use image_mask with a feathered PNG)",
        "timeline-wide transition sequencing — reveal_wipe bakes ONE pair; multi-cut transitions belong to video_compose",
    ]
    fallback_tools: list[str] = []

    input_schema = {
        "type": "object",
        "required": ["operation", "input_path"],
        "properties": {
            "operation": {"type": "string", "enum": list(OPERATIONS)},
            "input_path": {"type": "string"},
            "output_path": {
                "type": "string",
                "description": "Defaults to {stem}_{op}.mp4 (.mov for image_mask — alpha needs qtrle/mov)",
            },
            # blur_region / dim_outside
            "shape": {"type": "string", "enum": list(SHAPES), "default": "rect"},
            "region": {
                "type": "object",
                "description": (
                    "NORMALIZED 0..1 (not pixels). rect: {x,y,w,h} as fractions of frame "
                    "width/height. circle: {cx,cy} fractions of width/height, r fraction of "
                    "min(width,height). Circle bounding box must fit inside the frame."
                ),
            },
            "start": {"type": "number", "minimum": 0, "description": "optional effect window start (s); requires end"},
            "end": {"type": "number", "description": "optional effect window end (s); requires start"},
            "strength": {
                "type": "number",
                "default": STRENGTH_DEFAULT,
                "description": f"blur_region: boxblur radius in pixels, 1..{STRENGTH_MAX} (clamped to the region size)",
            },
            "dim_factor": {
                "type": "number",
                "default": 0.3,
                "description": "dim_outside: luma multiplier outside the region, 0 (black) <= f < 1 (no-op)",
            },
            "lossless": {
                "type": "boolean",
                "default": False,
                "description": "encode mp4 outputs with libx264 -qp 0 (bit-exact outside the mask; large files)",
            },
            # image_mask
            "mask_path": {"type": "string", "description": "image_mask: PNG mask, white=keep black=transparent"},
            "invert": {"type": "boolean", "default": False, "description": "image_mask: swap keep/transparent"},
            # reveal_wipe
            "second_path": {"type": "string", "description": "reveal_wipe: the incoming clip (same resolution)"},
            "direction": {"type": "string", "enum": list(WIPES), "description": "reveal_wipe: wipe direction"},
            "duration": {"type": "number", "description": "reveal_wipe: transition length (s), <= both clip durations"},
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
        "operation", "input_path", "second_path", "mask_path",
        "shape", "region", "strength", "dim_factor", "direction", "duration", "invert",
    ]
    side_effects = ["writes a derived video file", "may append to an asset_manifest"]
    user_visible_verification = [
        "Scrub the derived clip; confirm the mask sits on the intended region and edges look right",
    ]

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

        ext = ".mov" if op == "image_mask" else ".mp4"
        out_path = Path(inputs.get("output_path") or src_path.with_name(f"{src_path.stem}_{op}{ext}"))
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            if op == "blur_region":
                err = self._blur_region(src_path, out_path, inputs)
            elif op == "dim_outside":
                err = self._dim_outside(src_path, out_path, inputs)
            elif op == "image_mask":
                err = self._image_mask(src_path, out_path, inputs)
            elif op == "reveal_wipe":
                err = self._reveal_wipe(src_path, out_path, inputs)
            else:  # unreachable
                err = f"unhandled operation {op}"
        except _OpInputError as e:
            return ToolResult(success=False, error=str(e))

        if err:
            return ToolResult(success=False, error=err)
        if not out_path.exists() or out_path.stat().st_size == 0:
            return ToolResult(success=False, error=f"{op} produced no output.")

        # re-probe: reveal_wipe changes the duration, downstream must see the new value
        probed = self._probe(out_path)
        data: dict[str, Any] = {
            "operation": op,
            "input": str(src_path),
            "output": str(out_path),
            "output_path": str(out_path),
            "duration_seconds": probed.get("duration_seconds"),
            "resolution": probed.get("resolution"),
        }
        if op == "reveal_wipe":
            data["second_input"] = str(inputs.get("second_path"))
            data["transition"] = self.WIPES[inputs["direction"]]
        if op == "image_mask":
            data["has_alpha"] = True
            data["alpha_format"] = "qtrle .mov (lossless RGBA, same encode as object_cutout)"
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

    def _blur_region(self, src: Path, out: Path, inputs: dict[str, Any]) -> Optional[str]:
        strength = inputs.get("strength", self.STRENGTH_DEFAULT)
        if not isinstance(strength, (int, float)) or not (0 < strength <= self.STRENGTH_MAX):
            raise _OpInputError(f"blur_region requires strength in (0, {self.STRENGTH_MAX}]; got {strength!r}.")
        enable = self._enable_clause(inputs)
        shape, reg = self._parse_region(inputs)  # pure normalized checks before any probe

        w, h = self._dimensions(src)
        px = self._region_pixels(shape, reg, w, h)

        if shape == "rect":
            x, y, rw, rh = px["x"], px["y"], px["w"], px["h"]
            # boxblur radii are capped at half the (cropped) plane size; chroma plane is
            # half-size for yuv420p, so cap its radius separately
            rad = max(1, min(int(strength), min(rw, rh) // 2 - 1))
            crad = max(1, min(rad // 2, min(rw, rh) // 4 - 1))
            fc = (
                f"[0:v]split=2[base][reg];"
                f"[reg]crop={rw}:{rh}:{x}:{y},"
                f"boxblur=luma_radius={rad}:luma_power=2:chroma_radius={crad}:chroma_power=2[blur];"
                f"[base][blur]overlay={x}:{y}{enable}[v]"
            )
        else:  # circle: blur the bounding box, then cut a circular alpha so only the disc lands
            bx, by, r, d = px["bx"], px["by"], px["r"], px["d"]
            rad = max(1, min(int(strength), d // 2 - 1))
            crad = max(1, min(rad // 2, d // 4 - 1))
            fc = (
                f"[0:v]split=2[base][reg];"
                f"[reg]crop={d}:{d}:{bx}:{by},"
                f"boxblur=luma_radius={rad}:luma_power=2:chroma_radius={crad}:chroma_power=2,"
                f"format=rgba,"
                f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*lte(hypot(X-{r},Y-{r}),{r})'[blur];"
                f"[base][blur]overlay={bx}:{by}{enable}[v]"
            )
        return self._run_filter(src, out, fc, inputs)

    def _dim_outside(self, src: Path, out: Path, inputs: dict[str, Any]) -> Optional[str]:
        dim = inputs.get("dim_factor", 0.3)
        if not isinstance(dim, (int, float)) or not (0 <= dim < 1):
            raise _OpInputError(f"dim_outside requires dim_factor in [0, 1); got {dim!r}.")
        enable = self._enable_clause(inputs)
        shape, reg = self._parse_region(inputs)

        w, h = self._dimensions(src)
        px = self._region_pixels(shape, reg, w, h)

        # dim the whole frame (luma multiply), then paste the ORIGINAL region back on top
        if shape == "rect":
            x, y, rw, rh = px["x"], px["y"], px["w"], px["h"]
            fc = (
                f"[0:v]split=2[orig][dimsrc];"
                f"[dimsrc]lutyuv=y='val*{dim}'{enable}[dimmed];"
                f"[orig]crop={rw}:{rh}:{x}:{y}[spot];"
                f"[dimmed][spot]overlay={x}:{y}{enable}[v]"
            )
        else:
            bx, by, r, d = px["bx"], px["by"], px["r"], px["d"]
            fc = (
                f"[0:v]split=2[orig][dimsrc];"
                f"[dimsrc]lutyuv=y='val*{dim}'{enable}[dimmed];"
                f"[orig]crop={d}:{d}:{bx}:{by},format=rgba,"
                f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*lte(hypot(X-{r},Y-{r}),{r})'[spot];"
                f"[dimmed][spot]overlay={bx}:{by}{enable}[v]"
            )
        return self._run_filter(src, out, fc, inputs)

    def _image_mask(self, src: Path, out: Path, inputs: dict[str, Any]) -> Optional[str]:
        mask = inputs.get("mask_path")
        if not mask:
            raise _OpInputError("image_mask requires mask_path (PNG: white=keep, black=transparent).")
        if out.suffix.lower() != ".mov":
            raise _OpInputError(
                "image_mask output carries an alpha channel and is encoded qtrle, which needs a "
                f".mov container (same as object_cutout); got {out.suffix!r}. Use a .mov output_path."
            )
        mask_path = Path(mask)
        if not mask_path.exists():
            raise _OpInputError(f"mask not found: {mask}")
        dur = self._probe(src).get("duration_seconds")
        if not dur:
            raise _OpInputError(f"could not probe duration of {src} (needed to loop the still mask).")

        invert = ",negate" if inputs.get("invert") else ""
        # scale2ref sizes the mask to the source; mask luminance becomes the alpha channel
        # (object_cutout's alphamerge approach, with a looped still instead of a mask video)
        fc = (
            "[1:v][0:v]scale2ref=w=iw:h=ih[mask][src];"
            f"[mask]format=gray{invert}[m];"
            "[src][m]alphamerge=shortest=1[out]"
        )
        # loop the still slightly PAST the clip end; shortest=1 then ends on the video,
        # so the output keeps the source duration even if the probe is a hair off
        cmd = [
            "ffmpeg", "-y",
            "-i", str(src),
            "-loop", "1", "-t", str(round(float(dur) + 0.5, 3)), "-i", str(mask_path),
            "-filter_complex", fc,
            "-map", "[out]", "-c:v", "qtrle",
            "-map", "0:a?", "-c:a", "copy",
            str(out),
        ]
        return self._run(cmd)

    def _reveal_wipe(self, src: Path, out: Path, inputs: dict[str, Any]) -> Optional[str]:
        second = inputs.get("second_path")
        if not second:
            raise _OpInputError("reveal_wipe requires second_path (the incoming clip).")
        direction = inputs.get("direction")
        if direction not in self.WIPES:
            raise _OpInputError(f"reveal_wipe direction must be one of {sorted(self.WIPES)}; got {direction!r}.")
        dur = inputs.get("duration")
        if not isinstance(dur, (int, float)) or dur <= 0:
            raise _OpInputError("reveal_wipe requires duration > 0 (transition seconds).")
        second_path = Path(second)
        if not second_path.exists():
            raise _OpInputError(f"second clip not found: {second}")

        pa, pb = self._probe(src), self._probe(second_path)
        if not pa.get("width") or not pb.get("width"):
            raise _OpInputError("could not probe both clips (width/height needed for xfade).")
        if (pa["width"], pa["height"]) != (pb["width"], pb["height"]):
            raise _OpInputError(
                f"reveal_wipe requires same-size clips (xfade constraint); got "
                f"{pa.get('resolution')} vs {pb.get('resolution')}."
            )
        dur_a = pa.get("duration_seconds") or 0
        dur_b = pb.get("duration_seconds") or 0
        if dur > min(dur_a, dur_b):
            raise _OpInputError(
                f"transition duration ({dur}s) exceeds a clip ({dur_a:.2f}s / {dur_b:.2f}s); "
                f"it must be <= both."
            )

        fps = pa.get("fps") or 30
        offset = round(dur_a - float(dur), 3)
        trans = self.WIPES[direction]
        # xfade requires matched size/fps/timebase; normalize both legs first
        fc = (
            f"[0:v]fps={fps},format=yuv420p,settb=AVTB,setsar=1[va];"
            f"[1:v]fps={fps},format=yuv420p,settb=AVTB,setsar=1[vb];"
            f"[va][vb]xfade=transition={trans}:duration={dur}:offset={offset}[v]"
        )
        maps = ["-map", "[v]"]
        if self._has_audio(src) and self._has_audio(second_path):
            fc += (
                ";[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[aa]"
                ";[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[ab]"
                f";[aa][ab]acrossfade=d={dur}[a]"
            )
            maps += ["-map", "[a]"]
        else:
            maps += ["-an"]
        cmd = ["ffmpeg", "-y", "-i", str(src), "-i", str(second_path),
               "-filter_complex", fc, *maps, *self._vcodec_args(inputs), str(out)]
        return self._run(cmd)

    # ---- region / window parsing ----

    def _parse_region(self, inputs: dict[str, Any]) -> tuple[str, dict[str, float]]:
        """Validate shape + normalized region values. Pure — no probe, no ffmpeg."""
        shape = inputs.get("shape", "rect")
        if shape not in self.SHAPES:
            raise _OpInputError(f"shape must be one of {self.SHAPES}; got {shape!r}.")
        region = inputs.get("region")
        if not isinstance(region, dict):
            raise _OpInputError(
                "region is required: rect {x,y,w,h} or circle {cx,cy,r}, all NORMALIZED 0..1 "
                "(fractions of the frame, not pixels)."
            )
        keys = ("x", "y", "w", "h") if shape == "rect" else ("cx", "cy", "r")
        vals: dict[str, float] = {}
        for k in keys:
            v = region.get(k)
            if not isinstance(v, (int, float)):
                raise _OpInputError(f"region.{k} must be a number (normalized 0..1); got {v!r}.")
            vals[k] = float(v)
        if shape == "rect":
            if not (0 <= vals["x"] < 1 and 0 <= vals["y"] < 1):
                raise _OpInputError(f"region x/y must be in [0, 1); got {vals!r}.")
            if not (0 < vals["w"] <= 1 and 0 < vals["h"] <= 1):
                raise _OpInputError(f"region w/h must be in (0, 1]; got {vals!r}.")
            if vals["x"] + vals["w"] > 1.0001 or vals["y"] + vals["h"] > 1.0001:
                raise _OpInputError(f"region extends past the frame (x+w or y+h > 1): {vals!r}.")
        else:
            if not (0 <= vals["cx"] <= 1 and 0 <= vals["cy"] <= 1):
                raise _OpInputError(f"region cx/cy must be in [0, 1]; got {vals!r}.")
            if not (0 < vals["r"] <= 0.5):
                raise _OpInputError(f"region r must be in (0, 0.5] of min(width,height); got {vals!r}.")
        return shape, vals

    def _region_pixels(self, shape: str, reg: dict[str, float], w: int, h: int) -> dict[str, int]:
        """Convert normalized region -> even pixel values (yuv420p-safe crop/overlay)."""
        if not w or not h:
            raise _OpInputError("could not probe the source resolution (needed to place the region).")
        if shape == "rect":
            x, y = self._even(reg["x"] * w), self._even(reg["y"] * h)
            rw = max(self.MIN_REGION_PX, self._even(reg["w"] * w))
            rh = max(self.MIN_REGION_PX, self._even(reg["h"] * h))
            if x + rw > w or y + rh > h:  # even-snapping can push past the edge; pull back
                rw, rh = min(rw, self._even(w - x)), min(rh, self._even(h - y))
            if rw < self.MIN_REGION_PX or rh < self.MIN_REGION_PX:
                raise _OpInputError(f"region is too small: {rw}x{rh}px (minimum {self.MIN_REGION_PX}px per side).")
            return {"x": x, "y": y, "w": rw, "h": rh}
        r = max(self.MIN_REGION_PX // 2, self._even(reg["r"] * min(w, h)))
        cx, cy = reg["cx"] * w, reg["cy"] * h
        d = 2 * r
        if cx - r < 0 or cy - r < 0 or cx + r > w or cy + r > h:
            raise _OpInputError(
                f"circle (cx={reg['cx']}, cy={reg['cy']}, r={reg['r']}) does not fit in the "
                f"{w}x{h} frame — its bounding box must lie fully inside (no silent clamping)."
            )
        # even-snapping the corner can round 1px past the edge; pull back inside the frame
        bx = min(max(0, self._even(cx - r)), w - d)
        by = min(max(0, self._even(cy - r)), h - d)
        return {"bx": bx, "by": by, "r": r, "d": d}

    def _enable_clause(self, inputs: dict[str, Any]) -> str:
        """Optional start/end window -> ':enable=between(t,..)' (empty = whole clip)."""
        start, end = inputs.get("start"), inputs.get("end")
        if start is None and end is None:
            return ""
        if start is None or end is None:
            raise _OpInputError("start and end must be given together (the effect time window).")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or start < 0 or end <= start:
            raise _OpInputError(f"invalid window: need 0 <= start < end; got start={start!r}, end={end!r}.")
        return f":enable='between(t,{start},{end})'"

    @staticmethod
    def _even(v: float) -> int:
        return max(0, int(round(v / 2.0)) * 2)

    # ---- ffmpeg helpers ----

    def _vcodec_args(self, inputs: dict[str, Any]) -> list[str]:
        if inputs.get("lossless"):
            return ["-c:v", "libx264", "-preset", "veryfast", "-qp", "0", "-pix_fmt", "yuv420p"]
        return ["-c:v", "libx264", "-pix_fmt", "yuv420p"]

    def _run_filter(self, src: Path, out: Path, filtergraph: str, inputs: dict[str, Any]) -> Optional[str]:
        """Single-input filter_complex run keeping (optional) audio untouched."""
        cmd = ["ffmpeg", "-y", "-i", str(src),
               "-filter_complex", filtergraph,
               "-map", "[v]", "-map", "0:a?", "-c:a", "copy",
               *self._vcodec_args(inputs), str(out)]
        return self._run(cmd)

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

    def _dimensions(self, path: Path) -> tuple[int, int]:
        probed = self._probe(path)
        return int(probed.get("width") or 0), int(probed.get("height") or 0)

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
        remap those and add an fps probe (reveal_wipe needs a frame rate to match legs).
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

        param_keys = ("shape", "region", "strength", "dim_factor", "start", "end",
                      "mask_path", "invert", "second_path", "direction", "duration")
        params = {k: inputs.get(k) for k in param_keys if inputs.get(k) is not None}
        res = probed.get("resolution")
        entry = {
            "id": f"mask-{op}-{len(doc['assets']) + 1}",
            "type": "video",
            "path": str(out),
            "source_tool": "mask_ops",
            "scene_id": str(inputs.get("scene_id", "derived")),
            "subtype": op,
            "generation_summary": f"mask_ops {op}({params}) from {src.name}",
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

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)


class _OpInputError(Exception):
    """Bad parameters for a mask op (validated before any ffmpeg spend)."""
