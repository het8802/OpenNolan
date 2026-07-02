"""Video composition tool — FFmpeg + Remotion + HyperFrames (runtime-aware).

Pipeline-facing orchestration surface for composition. Takes `edit_decisions`,
`asset_manifest`, and audio, and delegates to the technical runtime chosen
at proposal stage.

Routing is driven by `edit_decisions.render_runtime` (locked at proposal):

- `remotion`   → React-based frame-accurate render via `npx remotion render`.
                 Handles the existing scene-component stack, word-level captions,
                 TalkingHead/CinematicRenderer. Current default.
- `hyperframes` → HTML/CSS/GSAP render via `hyperframes_compose`.
                 Handles kinetic typography, product promos, website-to-video,
                 registry blocks. Added in the parallel-runtime initiative.
- `ffmpeg`     → FFmpeg concat/trim. Used only for simple video cuts without
                 composition, or when the approved path explicitly names FFmpeg.

Silent runtime swaps are forbidden by governance. If the chosen runtime is
unavailable or fails, this tool surfaces a structured blocker and waits for
the agent to re-ask the user rather than substituting a different engine.
"""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ResumeSupport,
    ToolResult,
    ToolStability,
    ToolTier,
)


class VideoCompose(BaseTool):
    name = "video_compose"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "video_post"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = ["cmd:ffmpeg"]
    install_instructions = "Install FFmpeg: https://ffmpeg.org/download.html"
    agent_skills = ["remotion-best-practices", "remotion", "ffmpeg"]

    capabilities = [
        "compose_cuts",
        "burn_subtitles",
        "overlay_assets",
        "encode_profile",
        "remotion_render",
    ]

    input_schema = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["compose", "render", "render_proxies", "remotion_render", "burn_subtitles", "overlay", "encode"],
                "description": (
                    "compose: low-level concat cuts + audio + subtitles. "
                    "render: high-level — resolves asset IDs, auto-routes to Remotion "
                    "for images/animations or FFmpeg for video-only. Preferred for compose-director. "
                    "render_proxies: render each scene SOLO to a content-cached proxy clip, then "
                    "return an ffmpeg-runtime assemble EDL (render-once / NLE model — re-edits are cheap concats). "
                    "remotion_render: render via Remotion (Node.js). "
                    "burn_subtitles: burn subtitle file into existing video. "
                    "overlay: composite overlays onto base video. "
                    "encode: re-encode to a target profile/codec."
                ),
            },
            "input_path": {"type": "string"},
            "output_path": {"type": "string"},
            "edit_decisions": {
                "type": "object",
                "description": "Full edit_decisions artifact (required for compose/render)",
            },
            "asset_manifest": {
                "type": "object",
                "description": (
                    "Full asset_manifest artifact (required for render). "
                    "Used to resolve asset IDs in cuts[].source to file paths."
                ),
            },
            "proposal_packet": {
                "type": "object",
                "description": (
                    "Full proposal_packet artifact. Optional but STRONGLY "
                    "recommended — when present, final_review compares "
                    "proposal_packet.production_plan.render_runtime against "
                    "edit_decisions.render_runtime and flags runtime_swap_detected. "
                    "Without it, runtime-swap detection falls back to checking "
                    "edit_decisions.metadata.proposal_render_runtime."
                ),
            },
            "narration_transcript_path": {
                "type": "string",
                "description": (
                    "Path to a word-level transcript JSON (from `transcriber` "
                    "tool output). Optional but STRONGLY recommended: when "
                    "combined with script_path/script_text, final_review "
                    "runs transcript_comparison and catches TTS failures "
                    "like 'Chirp3-HD reads ... as the word dot'. Without "
                    "it, content-level audio bugs ship silently."
                ),
            },
            "script_path": {
                "type": "string",
                "description": (
                    "Path to the source narration script (plain text). "
                    "Used by transcript_comparison to diff against the "
                    "transcribed audio. Provide this OR script_text."
                ),
            },
            "script_text": {
                "type": "string",
                "description": (
                    "Inline source narration script. Used by "
                    "transcript_comparison when a file path is unavailable."
                ),
            },
            "subtitle_path": {"type": "string"},
            "subtitle_style": {
                "type": "object",
                "description": "ASS subtitle styling. Also extracted from edit_decisions.subtitles if not provided.",
                "properties": {
                    "font": {"type": "string", "default": "Arial"},
                    "font_size": {"type": "integer", "default": 24},
                    "primary_color": {"type": "string", "default": "&HFFFFFF"},
                    "outline_color": {"type": "string", "default": "&H000000"},
                    "outline_width": {"type": "number", "default": 2},
                    "margin_v": {"type": "integer", "default": 40},
                    "alignment": {"type": "integer", "default": 2},
                },
            },
            "overlays": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "asset_path": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["image", "video", "text"],
                            "description": (
                                "Overlay kind. 'text' renders via drawtext (no asset needed; "
                                "requires `text`). Absent → inferred: asset_path present = "
                                "image/video, `text` present = text."
                            ),
                        },
                        "text": {
                            "type": "string",
                            "description": (
                                "Text content for text overlays. Special characters (colons, "
                                "quotes, commas, %) are escaped automatically; %{...} expansion "
                                "is disabled."
                            ),
                        },
                        "font_path": {
                            "type": "string",
                            "description": (
                                "Optional .ttf/.ttc for text overlays; default resolves a bold "
                                "sans from system font dirs (same candidates as text_card_gen)."
                            ),
                        },
                        "font_size": {"type": "integer", "minimum": 1, "default": 48},
                        "color": {
                            "type": "string", "default": "white",
                            "description": "Text color (ffmpeg color: name, #RRGGBB, 0xRRGGBB[AA]).",
                        },
                        "box": {
                            "type": "object",
                            "description": "Background box behind text overlays.",
                            "properties": {
                                "color": {"type": "string", "default": "black"},
                                "opacity": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                                "padding": {"type": "integer", "minimum": 0, "default": 10},
                            },
                        },
                        "position": {
                            "description": (
                                "Named anchor string (text overlays, e.g. 'bottom-center') or "
                                "{x, y} object; flat x/y below also accepted."
                            ),
                        },
                        "pts_offset_seconds": {
                            "type": "number", "minimum": 0,
                            "description": (
                                "Shift a VIDEO overlay's stream so its first frame lands at this "
                                "project time (pair with start_seconds=pts_offset_seconds for "
                                "delayed video overlays; used internally by cuts[].layer='overlay')."
                            ),
                        },
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "width": {
                            "type": "number",
                            "description": "Overlay width in px. May be given without height — the other dimension is derived aspect-preserving.",
                        },
                        "height": {
                            "type": "number",
                            "description": "Overlay height in px. May be given without width — the other dimension is derived aspect-preserving.",
                        },
                        "start_seconds": {"type": "number"},
                        "end_seconds": {"type": "number"},
                        "opacity": {
                            "type": "number", "minimum": 0, "maximum": 1,
                            "description": "Static overlay opacity (1.0 = opaque). Applied exactly; combines multiplicatively with keyframed opacity.",
                        },
                        "audio_mix": {
                            "type": "object",
                            "description": (
                                "Mix the overlay source's own audio into the base track: "
                                "delayed to start_seconds, trimmed to the overlay window, "
                                "amix duration=first (base length wins). Skipped with a "
                                "warning when the source has no audio stream."
                            ),
                            "properties": {
                                "enabled": {"type": "boolean", "default": False},
                                "volume": {"type": "number", "minimum": 0, "maximum": 2, "default": 1.0},
                            },
                        },
                    },
                },
            },
            "audio_path": {"type": "string", "description": "Mixed audio to mux into output"},
            "profile": {
                "type": "string",
                "description": (
                    "Media profile name from media_profiles.py "
                    "(e.g. youtube_landscape, tiktok, instagram_reels). "
                    "Applied in render and encode operations."
                ),
            },
            "options": {
                "type": "object",
                "description": "Render options (used by the render operation)",
                "properties": {
                    "subtitle_burn": {"type": "boolean", "default": True},
                    "two_pass_encode": {"type": "boolean", "default": False},
                },
            },
            "codec": {"type": "string", "default": "libx264"},
            "crf": {"type": "integer", "default": 23},
            "preset": {"type": "string", "default": "medium"},
            "hdr_policy": {
                "type": "string",
                "enum": ["auto", "preserve", "tonemap", "sdr"],
                "default": "auto",
                "description": (
                    "How render_proxies handles HDR (HLG/PQ) source footage. "
                    "auto (default): if ANY video source is HDR, PRESERVE it (10-bit "
                    "HEVC main10 + color tags) and lift SDR graphics/stills into the "
                    "HDR container so the whole timeline shares one color space; pure-SDR "
                    "timelines are unchanged. preserve: force HDR (blocker if no 10-bit "
                    "HEVC encoder). tonemap (alias sdr): convert HDR sources down to SDR. "
                    "Never tonemaps silently — the decision is reported in "
                    "data.hdr_handling and warnings[]."
                ),
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=4, ram_mb=2048, vram_mb=0, disk_mb=5000, network_required=False
    )

    # Remotion scene types that trigger React-based rendering
    _REMOTION_COMPONENTS = [
        "text_card", "stat_card", "callout", "comparison",
        "progress", "chart", "bar_chart", "line_chart", "pie_chart", "kpi_grid",
    ]

    best_for = [
        "Final render for explainer and animation pipelines",
        "Image-to-video with spring animations (Remotion)",
        "Animated text cards, stat cards, charts (Remotion)",
        "Complex transitions between scenes (Remotion)",
        "Pure video concat, trim, and xfade cross-transitions (FFmpeg)",
    ]
    not_good_for = [
        "HDR sources — output is 8-bit SDR; detect with is_hdr_source() and "
        "handle HDR per AGENT_GUIDE before using this tool",
    ]
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["Conversion failed"])
    resume_support = ResumeSupport.FROM_START
    idempotency_key_fields = ["operation", "input_path", "edit_decisions"]
    side_effects = ["writes video file to output_path"]
    user_visible_verification = [
        "Play the composed output and verify cuts, subtitles, and overlays",
    ]

    def _remotion_available(self) -> bool:
        """Check if Remotion rendering is available (requires npx + composer project + node_modules)."""
        import shutil as _shutil

        if not _shutil.which("npx"):
            return False
        composer_dir = Path(__file__).resolve().parent.parent.parent / "remotion-composer"
        if not composer_dir.exists() or not (composer_dir / "package.json").exists():
            return False
        # Check that node_modules are actually installed — without this,
        # npx remotion render will fail even though the project exists.
        if not (composer_dir / "node_modules").exists():
            return False
        return True

    def _hyperframes_available(self) -> bool:
        """Check if HyperFrames rendering is available.

        Delegates to the dedicated tool so the availability check stays in
        one place (node 22 floor, ffmpeg + npx on PATH).
        """
        try:
            from tools.video.hyperframes_compose import HyperFramesCompose
            return bool(HyperFramesCompose()._runtime_check()["runtime_available"])
        except Exception:
            return False

    def get_info(self) -> dict[str, Any]:
        """Extend base get_info to surface all available render runtimes.

        Preflight reports each runtime's availability separately so the agent
        can choose an appropriate `render_runtime` at proposal stage. Silent
        fallback between runtimes is forbidden.
        """
        info = super().get_info()
        remotion_ok = self._remotion_available()
        hyperframes_ok = self._hyperframes_available()
        info["render_engines"] = {
            "ffmpeg": True,
            "remotion": remotion_ok,
            "hyperframes": hyperframes_ok,
        }
        # Backwards-compat alias — some proposal skills inspect this name.
        info["render_runtimes"] = info["render_engines"]

        if remotion_ok:
            info["remotion_components"] = self._REMOTION_COMPONENTS
            info["remotion_note"] = (
                "Remotion is available for React-based rendering. Use it for "
                "image-to-video with spring animations, animated text/stat cards, "
                "charts, callouts, comparisons, and word-level caption burn. "
                "Prefer Remotion over Ken Burns pan-and-zoom for explainer "
                "and motion-graphics pipelines that already use the scene-component stack."
            )
        else:
            composer_dir = Path(__file__).resolve().parent.parent.parent / "remotion-composer"
            if composer_dir.exists() and (composer_dir / "package.json").exists() and not (composer_dir / "node_modules").exists():
                info["remotion_note"] = (
                    "Remotion project exists but node_modules are NOT installed. "
                    "Run 'cd remotion-composer && npm install' to enable Remotion rendering."
                )
            else:
                info["remotion_note"] = (
                    "Remotion is NOT available (needs Node.js/npx + remotion-composer + node_modules)."
                )

        if hyperframes_ok:
            info["hyperframes_note"] = (
                "HyperFrames is available for HTML/CSS/GSAP composition. Use it "
                "for kinetic typography, product promos, launch reels, "
                "website-to-video, and registry-block-driven scenes. Consumed via "
                "'npx hyperframes' (npm package: 'hyperframes'). "
                "Before locking render_runtime='hyperframes' at the proposal stage, "
                "verify the runtime with `hyperframes_compose` operation='doctor' "
                "or `make hyperframes-doctor`. An 'available' flag from the runtime "
                "check means node + ffmpeg + the npm package all resolve; it does "
                "not guarantee a render will succeed on the first specific "
                "composition."
            )
        else:
            info["hyperframes_note"] = (
                "HyperFrames is NOT available. Requires Node.js >= 22, FFmpeg, "
                "npx on PATH, and the 'hyperframes' npm package to be resolvable. "
                "Run `make hyperframes-doctor` to see the specific missing piece, "
                "or call `hyperframes_compose` operation='doctor' directly."
            )

        # Governance note — agents and reviewers consume this.
        info["runtime_governance"] = (
            "render_runtime is locked at proposal stage and carried unchanged "
            "through edit_decisions. Silent swaps are forbidden. If the "
            "chosen runtime fails, surface a structured blocker and wait for "
            "user approval before switching."
        )

        # HDR encode capability — preflight gate. If the SOURCE footage is HDR (HLG/PQ),
        # the agent must check this BEFORE editing and must NOT silently tonemap to SDR.
        # Detect the source with tools.video._shared.is_hdr_source(); if hdr and
        # hdr_encode.available is False, surface the limitation and get explicit consent
        # before falling back to SDR.
        hdr_encoders = self._hdr_encoders()
        info["hdr_encode"] = {
            "available": bool(hdr_encoders),
            "encoders": hdr_encoders,
            "note": (
                "10-bit HEVC HDR encode available via " + ", ".join(hdr_encoders) + ". "
                "When the source is HDR (is_hdr_source().hdr), preserve it: encode HEVC "
                "main10 yuv420p10le with the source's color_primaries/color_trc/colorspace "
                "and -tag:v hvc1; do NOT tonemap unless the user opts in."
                if hdr_encoders else
                "NO 10-bit HEVC HDR encoder found (need hevc_videotoolbox or a 10-bit libx265). "
                "If the source is HDR, you cannot preserve it on this machine — surface this "
                "and get explicit consent before tonemapping to SDR."
            ),
        }
        return info

    @staticmethod
    def _hdr_encoders() -> list[str]:
        """Names of available 10-bit-HEVC-capable encoders (for HDR preservation)."""
        import shutil
        import subprocess

        if not shutil.which("ffmpeg"):
            return []
        try:
            out = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=15, check=False,
            ).stdout
        except Exception:
            return []
        found = []
        for enc in ("hevc_videotoolbox", "libx265"):
            if enc in out:
                found.append(enc)
        return found

    # ──────────────────────────────────────────────────────────────────────
    # HDR preservation (render_proxies / _compose / assemble)
    #
    # Goal: render_proxies edits HDR footage exactly like SDR — the HDR is
    # PRESERVED end-to-end (10-bit HEVC main10 + HLG/PQ color tags), never
    # silently tonemapped. When a timeline MIXES HDR footage with SDR graphics
    # (text/HyperFrames cards/stills), the chosen policy is "lift graphics UP"
    # into the HDR (BT.2020) container so everything composites in one color
    # space (Het's decision 2026-06-22). The whole timeline shares ONE target
    # color (taken from the first HDR source) so proxies concat with -c copy.
    #
    # NOTE (needs visual review): the SDR→HDR promotion of graphics and the
    # drawtext/overlay-in-HDR compositing are color-management chains that are
    # plausibly-correct but only a human eye on real HDR footage can confirm the
    # ivory cards / white text don't shift. The machine-checkable part (10-bit,
    # HLG/PQ tags on the output) is asserted in tests.
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_valid_color_token(v: Any) -> bool:
        """ffprobe sometimes reports 'unknown'/'reserved'/'' for color fields."""
        s = (str(v) if v is not None else "").strip().lower()
        return bool(s) and s not in ("unknown", "reserved", "unspecified", "n/a")

    def _resolve_hdr_target(self, src_info: dict[str, Any]) -> dict[str, Any]:
        """Resolve the canonical HDR target color from an is_hdr_source() dict.

        Fills sensible BT.2020 fallbacks when ffprobe left a field 'unknown'.
        `trc` defaults from `kind` (HLG → arib-std-b67, PQ → smpte2084).
        """
        kind = src_info.get("kind")
        trc = src_info.get("transfer")
        if not self._is_valid_color_token(trc):
            trc = "smpte2084" if kind == "pq" else "arib-std-b67"
        prim = src_info.get("primaries")
        if not self._is_valid_color_token(prim):
            prim = "bt2020"
        cspace = src_info.get("color_space")
        if not self._is_valid_color_token(cspace):
            cspace = "bt2020nc"
        return {"kind": kind, "primaries": prim, "trc": trc, "colorspace": cspace}

    @classmethod
    def _zscale_available(cls) -> bool:
        """True iff this ffmpeg has the zscale filter (libzimg) for color convert."""
        import shutil
        cached = getattr(cls, "_ZSCALE_CACHE", None)
        if cached is not None:
            return cached
        ok = False
        if shutil.which("ffmpeg"):
            try:
                out = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-filters"],
                    capture_output=True, text=True, timeout=15, check=False,
                ).stdout
                ok = " zscale " in out or out.strip().endswith("zscale") or "zscale" in out
            except Exception:
                ok = False
        cls._ZSCALE_CACHE = ok
        return ok

    def _promote_sdr_to_hdr_vf(self, target: dict[str, Any]) -> str:
        """Filter chain that lifts an SDR (BT.709) source INTO the HDR container.

        ONE zscale node, fully specifying BOTH input and output: SDR graphics/
        stills are usually UNTAGGED, and zimg refuses a partial / transfer-only
        conversion ("code 3074: no path between colorspaces") — so we declare the
        input as full-range-limited BT.709 and convert straight to BT.2020 + the
        timeline's HLG/PQ transfer in a single step (verified on this ffmpeg).
        HLG is backward-compatible so SDR mapped into it stays close to the
        original; PQ is absolute so its mapping is approximate (visual-review item).
        Returns "" if zscale is unavailable (caller then keeps the source SDR).
        """
        if not self._zscale_available():
            return ""
        out_trc = "smpte2084" if target.get("kind") == "pq" else "arib-std-b67"
        return (
            "zscale=tin=bt709:min=bt709:pin=bt709:rin=tv:"
            f"t={out_trc}:m=bt2020nc:p=bt2020:r=tv,format=yuv420p10le"
        )

    def _tonemap_hdr_to_sdr_vf(self, src_kind: str = "hlg") -> str:
        """Filter chain that tonemaps an HDR source DOWN to SDR (BT.709 8-bit).

        Used only when hdr_policy='tonemap' (or 'auto' with no HDR encoder). The
        HDR source is always tagged (that's how is_hdr_source detects it), so the
        input spec here matches its kind; linearize → Hable tonemap → BT.709 SDR.
        """
        if not self._zscale_available():
            # Best-effort without zscale; will look flat but won't fail the render.
            return "format=yuv420p"
        in_trc = "smpte2084" if src_kind == "pq" else "arib-std-b67"
        return (
            f"zscale=tin={in_trc}:min=bt2020nc:pin=bt2020:t=linear:npl=100,"
            "tonemap=hable,"
            "zscale=t=bt709:m=bt709:p=bt709:r=tv,format=yuv420p"
        )

    def _video_output_args(
        self,
        hdr_encode: Optional[dict[str, Any]],
        codec: str,
        crf: Any,
        preset: str,
        fps_str: Optional[str] = None,
    ) -> list[str]:
        """Build the `-c:v …` output tail for one segment.

        hdr_encode is None  → legacy 8-bit SDR (yuv420p, libx264), byte-identical
                              to before.
        hdr_encode present  → 10-bit HEVC main10 with HLG/PQ color tags when its
                              `encoder` is set (preserve/promote); when `encoder`
                              is None the output is SDR (the tonemap case, whose
                              vf already converts the pixels).

        fps_str: append `-r <fps>` when given; omit it (e.g. the overlay pass,
        which keeps the base's fps) when falsy.
        """
        rtail = ["-r", fps_str] if fps_str else []
        if not hdr_encode:
            return ["-c:v", codec, "-crf", str(crf), "-preset", preset,
                    "-pix_fmt", "yuv420p"] + rtail

        enc = hdr_encode.get("encoder")
        pix = hdr_encode.get("pix_fmt") or "yuv420p10le"
        prim = hdr_encode.get("primaries")
        trc = hdr_encode.get("trc")
        cspace = hdr_encode.get("colorspace")

        if enc == "hevc_videotoolbox":
            # videotoolbox ignores -crf; use a generous bitrate target for ≤1080p
            # vertical reels. main10 + 10-bit pixfmt = HDR-capable HEVC.
            args = ["-c:v", "hevc_videotoolbox", "-profile:v", "main10",
                    "-pix_fmt", pix, "-b:v", "16M", "-maxrate", "20M", "-bufsize", "32M"]
        elif enc == "libx265":
            args = ["-c:v", "libx265", "-crf", str(crf), "-preset", preset, "-pix_fmt", pix]
            x265p = []
            if self._is_valid_color_token(prim):
                x265p.append(f"colorprim={prim}")
            if self._is_valid_color_token(trc):
                x265p.append(f"transfer={trc}")
            if self._is_valid_color_token(cspace):
                x265p.append(f"colormatrix={cspace}")
            if x265p:
                args += ["-x265-params", ":".join(x265p)]
        else:
            # No HDR encoder requested (tonemap path) → SDR via libx264. Emit EXPLICIT
            # BT.709 tags: the source carries HDR (BT.2020/PQ) metadata and ffmpeg would
            # otherwise copy those tags onto the 8-bit SDR output, mislabeling it as HDR.
            return ["-c:v", codec, "-crf", str(crf), "-preset", preset,
                    "-pix_fmt", pix,
                    "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-colorspace", "bt709"] + rtail

        # Container/stream color signaling for BOTH HDR encoders. Without these
        # tags a 10-bit file is silently treated as SDR by players.
        if self._is_valid_color_token(prim):
            args += ["-color_primaries", prim]
        if self._is_valid_color_token(trc):
            args += ["-color_trc", trc]
        if self._is_valid_color_token(cspace):
            args += ["-colorspace", cspace]
        if hdr_encode.get("tag"):
            args += ["-tag:v", hdr_encode["tag"]]
        args += rtail
        return args

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        operation = inputs["operation"]
        start = time.time()

        try:
            if operation == "compose":
                result = self._compose(inputs)
            elif operation == "render":
                result = self._render(inputs)
            elif operation == "render_proxies":
                result = self._render_proxies(inputs)
            elif operation == "remotion_render":
                result = self._remotion_render(inputs)
            elif operation == "burn_subtitles":
                result = self._burn_subtitles(inputs)
            elif operation == "overlay":
                result = self._overlay(inputs)
            elif operation == "encode":
                result = self._encode(inputs)
            else:
                return ToolResult(success=False, error=f"Unknown operation: {operation}")
        except Exception as e:
            return ToolResult(success=False, error=str(e))

        result.duration_seconds = round(time.time() - start, 2)
        return result

    _IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}

    @staticmethod
    def _is_image(path: Path) -> bool:
        """Check if a file is a still image (routes to Remotion, not FFmpeg)."""
        return path.suffix.lower() in VideoCompose._IMAGE_EXTENSIONS

    @staticmethod
    def _has_alpha(path: Path) -> bool:
        """True iff the asset's pixel format carries an alpha channel.

        Used by the HDR overlay path: an alpha asset (transparent .mov/.png) can't
        be zscale-converted into the HDR color space without dropping the alpha
        plane, so it composites as-is (color approximate, warned) instead.
        """
        try:
            from tools.video._shared import probe_output
            pf = (probe_output(Path(path)).get("pix_fmt") or "").lower()
        except Exception:
            return False
        if not pf:
            return False
        return (
            pf.startswith("yuva")
            or pf.startswith("ya")
            or any(t in pf for t in ("rgba", "argb", "abgr", "bgra", "rgba64", "pal8"))
        )

    @staticmethod
    def _normalize_ffmpeg_color(value: Any, fallback: str = "black") -> str:
        """Coerce a user/agent-supplied color into something ffmpeg accepts.

        - '#RRGGBB' (CSS hex)        → '0xRRGGBB' (ffmpeg's hex form)
        - '0x...' / '0X...'          → passed through (lowercased prefix)
        - a bare color NAME / token  → passed through (ffmpeg resolves names)
        - anything non-string/empty  → `fallback` (black by default)

        Defensive against junk so a malformed background can never wedge the
        whole render — a bad color silently degrades to black."""
        if not isinstance(value, str):
            return fallback
        v = value.strip()
        if not v:
            return fallback
        if v.startswith("#"):
            hex_part = v[1:]
            if len(hex_part) in (6, 8) and all(
                c in "0123456789abcdefABCDEF" for c in hex_part
            ):
                return "0x" + hex_part
            return fallback
        if v[:2] in ("0x", "0X"):
            return "0x" + v[2:]
        # A plain name/token: allow word chars + '@' (ffmpeg "name@alpha") only.
        if all(c.isalnum() or c in "_@." for c in v):
            return v
        return fallback

    @staticmethod
    def _segment_base_vf(
        cut: dict[str, Any], idx: int, target_w: int, target_h: int, fps_str: str,
        *,
        bg_color: str = "black",
        bg_image: Optional[str] = None,
        apply_transform: bool = False,
        seg_seconds: Optional[float] = None,
        hdr_vf_prefix: str = "",
    ) -> tuple[Optional[str], list[str], Optional[dict[str, Any]]]:
        """Shared per-segment video filter chain (crop → scale → pad → setsar → fps)
        used by BOTH video and still-image cuts so they normalize to the canvas the
        same way. Crop (if present) runs first, in SOURCE pixels (matches the schema).

        Returns (error_message_or_None, vf_parts, complex_spec).

        - `complex_spec is None` (the DEFAULT, and ALWAYS when apply_transform is
          False): single-input case. `_compose` splices `-filter:v <vf_parts>` +
          `-map 0:v:0`, exactly as before. With apply_transform=False and the
          default bg_color='black', the emitted tail is byte-identical to the
          legacy chain (scale…decrease, pad…color=black, setsar=1, fps=…), so
          cached proxies + legacy docs stay valid.
        - `complex_spec` is a dict {"inputs": [...extra ffmpeg -i args...],
          "filtergraph": "...[v]", "vlabel": "[v]"} for the multi-input cases
          (off-canvas color, or an image background) — `_compose` adds the extra
          `-i` inputs AFTER source(+anullsrc) and uses `-filter_complex` + `-map [v]`.

        Speed/setpts is NOT added here — the video branch appends setpts and the
        image branch folds speed into its looped length, so each owns its timing.

        apply_transform is set ONLY on the proxy-ASSEMBLE pass (via the
        composite_background gate in `_compose`); the solo-proxy render always
        passes apply_transform=False so proxy content (and its cache key) is
        unchanged."""
        # --- crop block: unchanged, always FIRST, source-pixel coordinates ---
        crop_parts: list[str] = []
        crop = (cut.get("transform") or {}).get("crop") or {}
        if crop:
            crop_w, crop_h = crop.get("width"), crop.get("height")
            if (
                not isinstance(crop_w, (int, float))
                or not isinstance(crop_h, (int, float))
                or crop_w <= 0 or crop_h <= 0
            ):
                return (
                    f"cuts[{idx}].transform.crop requires positive numeric "
                    f"width and height; got {crop!r}",
                    [],
                    None,
                )
            crop_x = int(round(crop.get("x", 0) or 0))
            crop_y = int(round(crop.get("y", 0) or 0))
            crop_parts.append(
                f"crop={int(round(crop_w))}:{int(round(crop_h))}:{crop_x}:{crop_y}"
            )

        # --- HDR color conversion runs AFTER crop (source pixels) and BEFORE
        #     scale, at the source's native resolution. Empty for SDR and for the
        #     proxy-assemble pass (proxies are already in the target color space).
        hdr_parts = [hdr_vf_prefix] if hdr_vf_prefix else []

        # --- legacy single-input path (no transform): identical tail to before,
        #     only the pad color is parameterized (defaults to black) ---
        if not apply_transform:
            vf_parts = list(crop_parts) + hdr_parts + [
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease",
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color={bg_color}",
                "setsar=1",
                f"fps={fps_str}",
            ]
            return (None, vf_parts, None)

        # --- transform path: resolve scale + position into an even box on canvas ---
        # `scale` is either a uniform number OR a per-axis {x, y} object (e.g. a
        # split-screen panel = {x:1.0, y:0.5} → a full-width half-height box). The
        # clip fits INSIDE the box aspect-preserved (never stretched); a panel that
        # should FILL its box pre-shapes its aspect with transform.crop.
        transform = cut.get("transform") or {}
        scale = transform.get("scale", 1.0)

        def _pos_float(v: Any, default: float = 1.0) -> float:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return default
            return f if f > 0 else default

        if isinstance(scale, dict):
            sx = _pos_float(scale.get("x", 1.0))
            sy = _pos_float(scale.get("y", 1.0))
        else:
            sx = sy = _pos_float(scale)
        # Even box dims (yuv420p), >= 2.
        boxw = max(2, int(target_w * sx / 2) * 2)
        boxh = max(2, int(target_h * sy / 2) * 2)

        position = transform.get("position", "center")
        if isinstance(position, dict):
            px = int(round(position.get("x", 0) or 0))
            py = int(round(position.get("y", 0) or 0))
        else:
            # Named anchor (margin=0 → flush to the canvas edges). Default 'center'.
            parts = VideoCompose._split_anchor(position) or ("center", "center")
            px, py = VideoCompose._anchor_xy(parts, target_w, target_h, boxw, boxh, 0)

        # The clip box, scaled-to-fit inside boxw×boxh and centered within it (so a
        # mismatched source aspect ratio letterboxes inside its own box, not the canvas).
        fg_chain = list(crop_parts) + hdr_parts + [
            f"scale={boxw}:{boxh}:force_original_aspect_ratio=decrease",
            "setsar=1",
        ]

        fully_inside = (0 <= px <= target_w - boxw) and (0 <= py <= target_h - boxh)

        if bg_image is None and fully_inside:
            # Simplest case: one input, pad the scaled clip into place over a solid
            # color. `pad` x/y must be within [0, W-iw]/[0, H-ih] — guaranteed here.
            vf_parts = fg_chain + [
                f"pad={target_w}:{target_h}:{px}:{py}:color={bg_color}",
                f"fps={fps_str}",
            ]
            return (None, vf_parts, None)

        # Multi-input compositing: build a background layer, overlay the clip on it.
        # `seg_seconds` bounds any synthesized/looped bg input (mandatory — an
        # unbounded lavfi/-loop input is an infinite encode).
        try:
            seg_t = float(seg_seconds) if seg_seconds and seg_seconds > 0 else 0.0
        except (TypeError, ValueError):
            seg_t = 0.0
        if seg_t <= 0:
            return (
                f"cuts[{idx}]: a positive segment duration is required to "
                f"composite an off-canvas/image background (got {seg_seconds!r})",
                [],
                None,
            )
        # Trim the float to a stable string ffmpeg parses.
        seg_t_str = f"{seg_t:.6f}".rstrip("0").rstrip(".") or "0"

        fg_graph = "[0:v]" + ",".join(fg_chain) + "[fg]"

        if bg_image is not None:
            # IMAGE background: object-fit:cover (scale to fill, crop overflow),
            # then overlay the clip box. The bg is a looped still bounded by -t.
            inputs = ["-loop", "1", "-t", seg_t_str, "-i", str(bg_image)]
            bg_idx = "{bg}"  # _compose substitutes the resolved input index
            bg_graph = (
                f"[{bg_idx}:v]"
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                f"crop={target_w}:{target_h},setsar=1,fps={fps_str}[bg]"
            )
        else:
            # COLOR background, box off-canvas: a lavfi color source as the bg,
            # overlay the (possibly clipped) box at PX,PY.
            inputs = [
                "-f", "lavfi", "-t", seg_t_str,
                "-i", f"color=c={bg_color}:s={target_w}x{target_h}:r={fps_str}",
            ]
            bg_idx = "{bg}"
            bg_graph = f"[{bg_idx}:v]setsar=1[bg]"

        filtergraph = (
            f"{bg_graph};{fg_graph};[bg][fg]overlay={px}:{py}[v]"
        )
        return (
            None,
            [],
            {"inputs": inputs, "filtergraph": filtergraph, "vlabel": "[v]"},
        )

    @staticmethod
    def _has_audio_stream(path: Path) -> bool:
        """Return True iff ffprobe reports at least one audio stream.

        Many stock video clips (especially from Pexels) ship with no audio
        stream at all. If we blindly tell ffmpeg to transcode the 0:a stream
        on such a file it errors out. This helper lets the segment builder
        branch on stream presence so it can synthesize a silent track when
        needed, keeping the concat segment layout consistent.
        """
        try:
            out = subprocess.check_output(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "a",
                    "-show_entries", "stream=codec_type",
                    "-of", "default=nw=1:nk=1",
                    str(path),
                ],
                stderr=subprocess.STDOUT,
                text=True,
            )
            return "audio" in out
        except Exception:
            return False

    # ---- per-cut transitions (FFmpeg xfade path) ----

    # transition name → ffmpeg xfade transition. dissolve/crossfade collapse to
    # fade so beat_cutter/template_apply output renders on the FFmpeg runtime
    # instead of acting as a route-to-Remotion signal. Snake_case aliases match
    # how skills/templates spell them.
    _XFADE_MAP = {
        "fade": "fade",
        "dissolve": "fade",
        "crossfade": "fade",
        "fadeblack": "fadeblack",
        "fade_black": "fadeblack",
        "fadewhite": "fadewhite",
        "fade_white": "fadewhite",
        "wipe": "wipeleft",
        "wipeleft": "wipeleft", "wipe_left": "wipeleft",
        "wiperight": "wiperight", "wipe_right": "wiperight",
        "wipeup": "wipeup", "wipe_up": "wipeup",
        "wipedown": "wipedown", "wipe_down": "wipedown",
        "slideleft": "slideleft", "slide_left": "slideleft",
        "slideright": "slideright", "slide_right": "slideright",
        "slideup": "slideup", "slide_up": "slideup",
        "slidedown": "slidedown", "slide_down": "slidedown",
        "circleopen": "circleopen", "circle_open": "circleopen",
        "circleclose": "circleclose", "circle_close": "circleclose",
        "zoom": "zoomin", "zoomin": "zoomin", "zoom_in": "zoomin",
    }
    _HARD_CUT_NAMES = {"", "cut", "none", "hard", "hard_cut"}
    TRANSITION_DUR_MIN = 0.1
    TRANSITION_DUR_MAX = 2.0
    TRANSITION_DUR_DEFAULT = 0.5

    @classmethod
    def _clamp_transition_duration(cls, value: Any, fallback: float) -> float:
        try:
            d = float(value)
        except (TypeError, ValueError):
            return fallback
        return min(max(d, cls.TRANSITION_DUR_MIN), cls.TRANSITION_DUR_MAX)

    @classmethod
    def _resolve_joins(
        cls,
        cuts: list[dict],
        metadata: dict[str, Any] | None,
    ) -> tuple[list[Optional[dict[str, Any]]], list[str]]:
        """Resolve the transition at each A→B join for the FFmpeg xfade path.

        joins[i-1] describes the join between cuts[i-1] (A) and cuts[i] (B):
        None for a hard cut, else {"type": <xfade transition>, "duration": s}.

        Precedence per join: B.transition_in wins over A.transition_out when
        both are set (B owns its own entrance). The transition_duration is
        read from whichever cut supplied the winning transition name, falling
        back to metadata.default_transition_duration, then 0.5s — always
        clamped to [0.1, 2.0]. Unknown transition names degrade to 'fade'
        with a warning rather than failing the render.
        """
        default_dur = cls._clamp_transition_duration(
            (metadata or {}).get("default_transition_duration"),
            cls.TRANSITION_DUR_DEFAULT,
        )
        joins: list[Optional[dict[str, Any]]] = []
        warnings: list[str] = []
        for i in range(1, len(cuts)):
            a, b = cuts[i - 1], cuts[i]
            name = b.get("transition_in")
            owner = b
            if not name:
                name = a.get("transition_out")
                owner = a
            norm = str(name or "").strip().lower()
            if norm in cls._HARD_CUT_NAMES:
                joins.append(None)
                continue
            xfade = cls._XFADE_MAP.get(norm)
            if xfade is None:
                warnings.append(
                    f"unknown transition {name!r} between cuts {i - 1} and {i} "
                    f"— rendered as 'fade'"
                )
                xfade = "fade"
            duration = cls._clamp_transition_duration(
                owner.get("transition_duration"), default_dur
            )
            joins.append({"type": xfade, "duration": duration})
        return joins, warnings

    @staticmethod
    def _probe_duration_seconds(path: Path) -> Optional[float]:
        """Container duration of a clip in seconds, or None if unprobeable."""
        try:
            out = subprocess.check_output(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=nw=1:nk=1",
                    str(path),
                ],
                stderr=subprocess.STDOUT,
                text=True,
            )
            return float(out.strip())
        except Exception:
            return None

    def _transitions_concat(
        self,
        temp_segments: list[Path],
        joins: list[Optional[dict[str, Any]]],
        fps_str: str,
        codec: str,
        crf: int,
        preset: str,
        concat_out: Path,
        hdr_encode: Optional[dict[str, Any]] = None,
    ) -> tuple[Optional[str], str]:
        """Join normalized segments with xfade/acrossfade, encoding `concat_out`.

        Transition joins use xfade (video) + acrossfade (audio); hard-cut joins
        inside the same timeline use the concat filter, so mixed timelines work
        in a single pass. xfade offsets are computed from the POST-normalization
        probed segment durations (fps-normalized, so reliable) — the requested
        in/out math can drift via fps rounding and AAC priming, which would
        misplace every transition after the first.

        Every normalized segment is guaranteed an audio track (silent sources
        get anullsrc injected during segment encode), so the acrossfade chain
        never breaks on a missing stream.

        Returns (error, filtergraph); error is None on success.
        """
        durs: list[float] = []
        for seg in temp_segments:
            d = self._probe_duration_seconds(seg)
            if not d or d <= 0:
                return f"could not probe duration of normalized segment {seg.name}", ""
            durs.append(d)

        cmd = ["ffmpeg", "-y"]
        for seg in temp_segments:
            cmd.extend(["-i", str(seg)])

        filters: list[str] = []
        # xfade refuses mismatched timebases — a concat-filter output runs at
        # 1/1000000 while demuxed mp4 video is e.g. 1/12800 — so normalize
        # every video input to AVTB before chaining.
        for i in range(len(temp_segments)):
            filters.append(f"[{i}:v]settb=AVTB[vtb{i}]")
        cur_v, cur_a = "vtb0", "0:a"
        cum = durs[0]  # visible duration of the chain built so far
        for i in range(1, len(temp_segments)):
            join = joins[i - 1]
            out_v, out_a = f"vx{i}", f"ax{i}"
            if join is None:
                filters.append(f"[{cur_v}][vtb{i}]concat=n=2:v=1:a=0[{out_v}]")
                filters.append(f"[{cur_a}][{i}:a]concat=n=2:v=0:a=1[{out_a}]")
                cum += durs[i]
            else:
                # Cap the fade by the material actually available on both
                # sides — xfade/acrossfade fail outright when the requested
                # duration exceeds either input.
                avail = min(cum, durs[i]) - 0.05
                if avail <= 0.05:
                    return (
                        f"transition into segment {i} needs ≥ ~0.1s of material on "
                        f"both sides (have {min(cum, durs[i]):.2f}s); shorten "
                        f"transition_duration or lengthen the adjacent cuts",
                        "",
                    )
                d = round(min(join["duration"], avail), 4)
                offset = round(cum - d, 4)
                filters.append(
                    f"[{cur_v}][vtb{i}]xfade=transition={join['type']}:"
                    f"duration={d}:offset={offset}[{out_v}]"
                )
                filters.append(f"[{cur_a}][{i}:a]acrossfade=d={d}[{out_a}]")
                cum = cum + durs[i] - d
            cur_v, cur_a = out_v, out_a

        filtergraph = ";".join(filters)
        cmd.extend([
            "-filter_complex", filtergraph,
            "-map", f"[{cur_v}]", "-map", f"[{cur_a}]",
            *self._video_output_args(hdr_encode, codec, crf, preset, fps_str),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            str(concat_out),
        ])
        try:
            self.run_command(cmd, timeout=1800)
        except subprocess.CalledProcessError as e:
            return ((e.stderr or "") or "ffmpeg xfade chain failed").strip()[-500:], filtergraph
        except subprocess.TimeoutExpired:
            return "ffmpeg xfade chain timed out", filtergraph
        return None, filtergraph

    @staticmethod
    def _resolve_canvas(
        edit_decisions: dict[str, Any],
        profile_name: Optional[str],
    ) -> tuple[Optional[str], int, int, float]:
        """(error, width, height, fps) — metadata.compose_target > profile > 1920x1080@30.

        Shared by _compose (segment normalization) and the layer='overlay' PiP
        builder so PiP sizing always matches the canvas the base render uses.
        """
        target_w, target_h, target_fps = 1920, 1080, 30.0
        if profile_name:
            try:
                from lib.media_profiles import get_profile
                p = get_profile(profile_name)
                target_w, target_h, target_fps = p.width, p.height, float(p.fps)
            except (ImportError, ValueError):
                pass
        compose_target = (edit_decisions.get("metadata") or {}).get("compose_target") or {}
        if compose_target:
            try:
                ct_w = int(compose_target.get("width", target_w))
                ct_h = int(compose_target.get("height", target_h))
                ct_fps = float(compose_target.get("fps", target_fps))
            except (TypeError, ValueError):
                return (
                    f"invalid metadata.compose_target {compose_target!r}: "
                    "width/height/fps must be numeric",
                    target_w, target_h, target_fps,
                )
            if ct_w <= 0 or ct_h <= 0 or ct_fps <= 0:
                return (
                    f"invalid metadata.compose_target {compose_target!r}: "
                    "width/height/fps must be positive",
                    target_w, target_h, target_fps,
                )
            if ct_w % 2 or ct_h % 2:
                return (
                    f"metadata.compose_target {ct_w}x{ct_h} must use even "
                    "dimensions (yuv420p 4:2:0 requirement)",
                    target_w, target_h, target_fps,
                )
            target_w, target_h, target_fps = ct_w, ct_h, ct_fps
        return None, target_w, target_h, target_fps

    def _render_proxies(self, inputs: dict[str, Any]) -> ToolResult:
        """Render each scene to its OWN cached clip, then emit an FFmpeg assemble EDL.

        The render-once / NLE model (M2): rendering a scene (Remotion/HyperFrames)
        is expensive, so we render each scene SOLO to a proxy clip ONCE, cache it
        by content, and hand back an `ffmpeg`-runtime edit_decisions whose cuts
        point at those proxies. From then on, editing the ARRANGEMENT (order,
        transitions, audio, subtitles) is a cheap FFmpeg concat — proxies are
        reused from cache. Only a scene whose content changes misses and re-renders.

        Boundary (v1): a proxy bakes the scene's CONTENT rendered solo at native
        speed (no transform/transitions). The assemble EDL applies ordering,
        cross-scene transitions, per-scene speed + transform/crop, audio,
        subtitles and overlays — all cheap FFmpeg. So reordering, retiming,
        cropping, re-transitioning and re-scoring are FREE; only a change to a
        scene's own content (source / trim window / animation / playbook)
        re-renders that one scene.

        The returned `assemble_edit_decisions` MUST be rendered via
        operation="render" (render_runtime=ffmpeg) — NOT operation="compose",
        which drops overlays[] and layer routing.

        Inputs: edit_decisions (with the locked render_runtime), asset_manifest,
        proxies_dir (where clips are written), optional profile/output_profile,
        optional assemble_edl_path (also persist the assemble EDL there).
        """
        import hashlib
        from tools.video.render_cache import ProxyCache, file_content_hash

        edit_decisions = inputs.get("edit_decisions")
        if not edit_decisions:
            return ToolResult(success=False, error="edit_decisions required for render_proxies")
        asset_manifest = inputs.get("asset_manifest") or {"assets": []}
        cuts = edit_decisions.get("cuts", [])
        if not cuts:
            return ToolResult(success=False, error="No cuts in edit_decisions")

        render_runtime = (edit_decisions.get("render_runtime") or "").strip().lower()
        if render_runtime not in ("remotion", "hyperframes", "ffmpeg"):
            return ToolResult(
                success=False,
                error=(
                    f"render_proxies needs a valid render_runtime locked in "
                    f"edit_decisions (got {render_runtime!r}). Valid: remotion, "
                    f"hyperframes, ffmpeg."
                ),
            )

        renderer_family = edit_decisions.get("renderer_family")
        profile = inputs.get("profile") or inputs.get("output_profile")
        canvas_err, cw, ch, cfps = self._resolve_canvas(edit_decisions, profile)
        if canvas_err:
            return ToolResult(success=False, error=canvas_err)
        canvas = {"width": cw, "height": ch, "fps": cfps}

        # Playbook materially changes Remotion/HyperFrames pixels (palette, fonts,
        # motion), so fold its identity into the key for animated runtimes — else a
        # playbook edit is a stale cache HIT. ffmpeg ignores the playbook.
        playbook_identity = None
        if render_runtime in ("remotion", "hyperframes"):
            playbook_ref = (
                (edit_decisions.get("metadata") or {}).get("playbook")
                or inputs.get("playbook") or inputs.get("playbook_name")
            )
            if playbook_ref:
                _pb = Path(str(playbook_ref))
                playbook_identity = {
                    "ref": str(playbook_ref),
                    "hash": file_content_hash(_pb) if _pb.exists() else "",
                }

        proxies_dir = Path(inputs.get("proxies_dir") or "renders/proxies")
        proxies_dir.mkdir(parents=True, exist_ok=True)

        asset_lookup = {a["id"]: a for a in asset_manifest.get("assets", []) if a.get("id")}
        cache = ProxyCache()

        # ── HDR decision (resolved ONCE for the whole timeline) ──────────────
        # render_proxies edits HDR exactly like SDR: detect HDR among the on-disk
        # VIDEO sources, then PRESERVE it (10-bit HEVC main10 + HLG/PQ color tags)
        # — lifting any SDR graphics/stills UP into the SAME HDR (BT.2020)
        # container so every proxy concats in one color space (Het's choice
        # 2026-06-22). One target color (from the first HDR source) is shared by
        # the whole timeline. Policy: auto (preserve when HDR present, else SDR),
        # preserve (force; blocker if no encoder), tonemap / sdr (force SDR).
        # HDR preservation only applies to render_runtime='ffmpeg' (the only path
        # that re-encodes from a source file); remotion/hyperframes proxies are
        # SDR by construction.
        from tools.video._shared import is_hdr_source

        hdr_policy = str(inputs.get("hdr_policy") or "auto").strip().lower()
        if hdr_policy not in ("auto", "preserve", "tonemap", "sdr"):
            hdr_policy = "auto"

        def _resolve_src_path(c: dict[str, Any]) -> str:
            ref = c.get("source", "")
            return asset_lookup.get(ref, {}).get("path", ref)

        src_hdr_map: dict[str, dict[str, Any]] = {}
        first_hdr: Optional[dict[str, Any]] = None
        if render_runtime == "ffmpeg":
            for c in cuts:
                sp = _resolve_src_path(c)
                if sp and sp not in src_hdr_map and Path(sp).exists() and not self._is_image(Path(sp)):
                    info = is_hdr_source(Path(sp))
                    src_hdr_map[sp] = info
                    if info.get("hdr") and first_hdr is None:
                        first_hdr = info
        any_hdr = first_hdr is not None
        # A timeline that mixes HDR with anything non-HDR needs SDR→HDR PROMOTION
        # (zscale) to preserve. A timeline where EVERY cut is already an HDR video
        # needs no promotion, so preserve works even without zscale.
        all_cuts_hdr = bool(cuts) and all(
            src_hdr_map.get(_resolve_src_path(c), {}).get("hdr") for c in cuts
        )
        zscale_ok = self._zscale_available()

        hdr_warnings: list[str] = []
        hdr_target: Optional[dict[str, Any]] = None   # timeline target color (preserve)
        hdr_encoder: Optional[str] = None             # chosen 10-bit HEVC encoder
        hdr_mode_timeline = "sdr"                      # 'preserve' | 'tonemap' | 'sdr'
        if render_runtime == "ffmpeg" and any_hdr:
            encs = self._hdr_encoders()
            hdr_encoder = (
                "hevc_videotoolbox" if "hevc_videotoolbox" in encs
                else ("libx265" if "libx265" in encs else None)
            )
            if hdr_policy in ("tonemap", "sdr"):
                hdr_mode_timeline = "tonemap"
                if not zscale_ok:
                    hdr_warnings.append(
                        "hdr_policy='tonemap' but ffmpeg lacks the zscale filter (libzimg) "
                        "— the HDR→SDR tone curve can't be applied properly; output is "
                        "tagged SDR but may look flat. Install an ffmpeg with libzimg."
                    )
            elif hdr_encoder is None:
                if hdr_policy == "preserve":
                    return ToolResult(
                        success=False,
                        error=(
                            "hdr_policy='preserve' but no 10-bit HEVC encoder "
                            "(hevc_videotoolbox or libx265) is available on this machine "
                            "— cannot preserve HDR. Install one, or re-run with "
                            "hdr_policy='tonemap' to convert the HDR source to SDR."
                        ),
                    )
                hdr_mode_timeline = "tonemap"
                hdr_warnings.append(
                    "HDR source detected but no 10-bit HEVC encoder available — "
                    "tonemapping to SDR (hdr_policy='auto'). Install hevc_videotoolbox "
                    "or libx265, or pass hdr_policy explicitly to silence this."
                )
            elif not all_cuts_hdr and not zscale_ok:
                # Mixed HDR+SDR timeline but no zscale → SDR sources CANNOT be lifted
                # into the HDR container. Preserving here would encode SDR pixels with
                # HDR tags (a silent color mislabel). Block (preserve) or tonemap (auto).
                if hdr_policy == "preserve":
                    return ToolResult(
                        success=False,
                        error=(
                            "hdr_policy='preserve' on a mixed HDR+SDR timeline, but ffmpeg "
                            "lacks the zscale filter (libzimg) needed to lift the SDR "
                            "graphics/stills into the HDR (BT.2020) container — preserving "
                            "would mislabel SDR pixels as HDR. Install an ffmpeg with libzimg, "
                            "or re-run with hdr_policy='tonemap'."
                        ),
                    )
                hdr_mode_timeline = "tonemap"
                hdr_warnings.append(
                    "Mixed HDR+SDR timeline but ffmpeg lacks zscale (libzimg) to promote the "
                    "SDR parts into HDR — tonemapping the HDR source to SDR instead "
                    "(hdr_policy='auto'). Install an ffmpeg with libzimg to preserve HDR."
                )
            else:
                hdr_mode_timeline = "preserve"
                hdr_target = self._resolve_hdr_target(first_hdr)
                if all_cuts_hdr:
                    hdr_warnings.append(
                        f"HDR timeline: preserving HDR (10-bit {hdr_encoder}, "
                        f"{hdr_target.get('kind')})."
                    )
                else:
                    hdr_warnings.append(
                        f"HDR timeline: preserving HDR (10-bit {hdr_encoder}, "
                        f"{hdr_target.get('kind')}) and lifting SDR graphics/stills into the "
                        "HDR container — confirm graphics color on real HDR footage."
                    )

        proxies: list[dict[str, Any]] = []
        for i, cut in enumerate(cuts):
            scene_id = str(cut.get("id") or f"scene_{i:03d}")

            if str(cut.get("layer") or "").lower() == "overlay":
                return ToolResult(
                    success=False,
                    error=(
                        f"scene {scene_id!r}: layer='overlay' (PiP) cuts aren't "
                        "supported by the proxy path yet — a solo render flattens "
                        "PiP. Render PiP via the direct ffmpeg path, or split those "
                        "cuts out before render_proxies."
                    ),
                )

            source_ref = cut.get("source", "")
            source_path = asset_lookup.get(source_ref, {}).get("path", source_ref)
            if source_path and Path(source_path).exists():
                src_hash = file_content_hash(source_path)
            else:
                # No on-disk source (animated scene): key on the asset record (or the
                # bare ref) so distinct unresolved sources don't share one bucket.
                _blob = json.dumps(asset_lookup.get(source_ref, source_ref), sort_keys=True, default=str)
                src_hash = "unresolved:" + hashlib.sha256(_blob.encode()).hexdigest()

            try:
                orig_in = float(cut.get("in_seconds", 0))
                orig_out = float(cut.get("out_seconds", 0))
            except (TypeError, ValueError):
                orig_in, orig_out = 0.0, 0.0
            dur = round(orig_out - orig_in, 4)
            if dur <= 0:
                return ToolResult(
                    success=False,
                    error=f"scene {scene_id!r}: out_seconds must be > in_seconds (duration {dur})",
                )

            # Solo scene spec. Transitions, speed and transform are applied at the
            # cheap ASSEMBLE layer, so they're stripped from the proxy render.
            solo_cut = {
                k: v for k, v in cut.items()
                if k not in ("transition_in", "transition_out", "transition_duration",
                             "speed", "transform", "reason")
            }
            solo_cut["id"] = scene_id
            solo_cut["source"] = source_path
            if render_runtime == "ffmpeg":
                # _compose reads in/out as the SOURCE trim — keep the real window so
                # the proxy bakes the requested seconds, not always 0..dur.
                solo_cut["in_seconds"] = orig_in
                solo_cut["out_seconds"] = orig_out
            else:
                # Remotion/HyperFrames: in/out are the TIMELINE position; source trim
                # rides on source_in_seconds (preserved). Re-zero to render from t=0.
                solo_cut["in_seconds"] = 0.0
                solo_cut["out_seconds"] = dur

            # Bake CROP into the proxy (ffmpeg path). Crop is in SOURCE pixels, so it can only be
            # applied at the native source resolution — re-applying it at the assemble layer runs it
            # against the canvas-sized proxy and goes out of bounds (crop W/H can exceed the proxy's
            # W/H), which is the `crop=1440:2560 on a 1080x1920 proxy → exit 234` failure. Baking it
            # here makes crop part of the proxy's content identity (a crop edit re-renders just this
            # scene); _build_assemble_edl drops crop from the assemble so it isn't applied twice.
            # Other transform fields (scale/position) are PiP-only and never reach this path
            # (layer='overlay' is rejected above), so crop is the only transform the proxy carries.
            if render_runtime == "ffmpeg":
                _crop = (cut.get("transform") or {}).get("crop")
                if _crop:
                    solo_cut["transform"] = {"crop": _crop}

            # Per-scene HDR encode (from the timeline decision). preserve: an HDR
            # source keeps its color (vf_prefix=""), an SDR source/still is PROMOTED
            # into the HDR container. tonemap: an HDR source is converted to SDR,
            # SDR sources are untouched (None). The HDR decision is part of the
            # cache identity — but only when it actually affects the proxy, so plain
            # SDR proxies keep their existing keys (no cache-wide invalidation).
            scene_hdr_encode: Optional[dict[str, Any]] = None
            scene_hdr_identity: Optional[dict[str, Any]] = None
            if hdr_mode_timeline == "preserve" and hdr_target:
                base_enc = {
                    "encoder": hdr_encoder,
                    "pix_fmt": "yuv420p10le",
                    "primaries": hdr_target["primaries"],
                    "trc": hdr_target["trc"],
                    "colorspace": hdr_target["colorspace"],
                    "tag": "hvc1",
                }
                src_info = src_hdr_map.get(source_path)
                if src_info and src_info.get("hdr"):
                    scene_hdr_encode = dict(base_enc, vf_prefix="")
                    _mode = "preserve"
                else:
                    scene_hdr_encode = dict(
                        base_enc, vf_prefix=self._promote_sdr_to_hdr_vf(hdr_target)
                    )
                    _mode = "promote"
                scene_hdr_identity = {
                    "mode": _mode, "encoder": hdr_encoder, "pix_fmt": "yuv420p10le",
                    "primaries": hdr_target["primaries"], "trc": hdr_target["trc"],
                    "colorspace": hdr_target["colorspace"],
                }
            elif hdr_mode_timeline == "tonemap":
                src_info = src_hdr_map.get(source_path)
                if src_info and src_info.get("hdr"):
                    scene_hdr_encode = {
                        "encoder": None, "pix_fmt": "yuv420p",
                        "vf_prefix": self._tonemap_hdr_to_sdr_vf(src_info.get("kind") or "hlg"),
                    }
                    scene_hdr_identity = {"mode": "tonemap", "pix_fmt": "yuv420p"}

            identity = {
                "v": 2,
                "render_runtime": render_runtime,
                "renderer_family": renderer_family,
                "canvas": canvas,
                "playbook": playbook_identity,
                "trim": {"in": orig_in, "out": orig_out},
                "scene": {k: v for k, v in solo_cut.items() if k != "source"},
                "source_hash": src_hash,
            }
            if scene_hdr_identity:
                identity["hdr"] = scene_hdr_identity
            key = cache.key(identity)
            # Content-addressed filename: each distinct identity owns a distinct file,
            # so a re-render with new content can never clobber an older proxy that a
            # cached record still points at (the stale-pixel-HIT bug).
            proxy_path = proxies_dir / f"{scene_id}.{key[:16]}.mp4"

            with cache.lock(key):
                rec = cache.get(key)
                if rec:
                    proxies.append({
                        "scene_id": scene_id,
                        "proxy_path": rec["proxy_path"],
                        "duration_seconds": rec.get("duration_seconds", dur),
                        "cache_hit": True,
                        "render_runtime": render_runtime,
                    })
                    continue

                solo_ed: dict[str, Any] = {
                    "version": edit_decisions.get("version", "1.0"),
                    "render_runtime": render_runtime,
                    "cuts": [solo_cut],
                }
                if renderer_family:
                    solo_ed["renderer_family"] = renderer_family
                if edit_decisions.get("metadata"):
                    solo_ed["metadata"] = edit_decisions["metadata"]

                render_res = self._render_scene_proxy(
                    render_runtime, solo_ed, asset_manifest, proxy_path, profile,
                    hdr_encode=scene_hdr_encode,
                )
                if not render_res.success:
                    return ToolResult(
                        success=False,
                        error=f"proxy render failed for scene {scene_id!r}: {render_res.error}",
                    )
                cache.put(key, {
                    "proxy_path": str(proxy_path),
                    "scene_id": scene_id,
                    "render_runtime": render_runtime,
                    "duration_seconds": dur,
                    "source_hash": src_hash,
                })
                proxies.append({
                    "scene_id": scene_id,
                    "proxy_path": str(proxy_path),
                    "duration_seconds": dur,
                    "cache_hit": False,
                    "render_runtime": render_runtime,
                })

        hdr_meta: Optional[dict[str, Any]] = None
        if hdr_mode_timeline == "preserve" and hdr_target:
            hdr_meta = {
                "enabled": True,
                "kind": hdr_target.get("kind"),
                "encoder": hdr_encoder,
                "pix_fmt": "yuv420p10le",
                "primaries": hdr_target["primaries"],
                "trc": hdr_target["trc"],
                "colorspace": hdr_target["colorspace"],
            }
        assemble_ed = self._build_assemble_edl(edit_decisions, proxies, hdr_meta=hdr_meta)

        assemble_edl_path = inputs.get("assemble_edl_path")
        if assemble_edl_path:
            try:
                from lib.atomic_io import atomic_write_json
                atomic_write_json(Path(assemble_edl_path), assemble_ed)
            except Exception:
                Path(assemble_edl_path).write_text(json.dumps(assemble_ed, indent=2))

        n_cached = sum(1 for p in proxies if p["cache_hit"])
        proxy_summary = {
            "operation": "render_proxies",
            "proxies": proxies,
            "assemble_edit_decisions": assemble_ed,
            "n_scenes": len(proxies),
            "n_cached": n_cached,
            "n_rendered": len(proxies) - n_cached,
            "canvas": canvas,
            "render_runtime": render_runtime,
            "hdr_handling": {
                "policy": hdr_policy,
                "source_hdr": any_hdr,
                "decision": hdr_mode_timeline,
                "encoder": hdr_encoder,
                "target": hdr_target,
            },
        }
        if hdr_warnings:
            proxy_summary["warnings"] = list(hdr_warnings)

        # One-call mode: with an output_path, also assemble + render the proxies to
        # a final video — the cached drop-in for operation="render" the editor uses
        # (only changed scenes re-rendered above; this pass is a cheap ffmpeg concat
        # that also applies overlays/audio and runs the final review once). Without
        # output_path, return just the proxies + the assemble EDL.
        output_path = inputs.get("output_path")
        if not output_path:
            return ToolResult(success=True, data=proxy_summary, artifacts=[p["proxy_path"] for p in proxies])

        final_res = self._render({
            "edit_decisions": assemble_ed,
            "asset_manifest": {"assets": []},  # proxy sources are absolute file paths
            "output_path": str(output_path),
            "profile": profile,
        })
        data = dict(final_res.data or {})
        # Union the assemble pass's warnings with the proxy/HDR warnings (the
        # proxy_summary.update below would otherwise clobber one with the other).
        merged_warnings = list(data.get("warnings") or []) + list(hdr_warnings or [])
        data.update(proxy_summary)
        if merged_warnings:
            data["warnings"] = merged_warnings
        return ToolResult(
            success=final_res.success,
            data=data,
            artifacts=final_res.artifacts or [p["proxy_path"] for p in proxies],
            error=final_res.error,
        )

    def _render_scene_proxy(
        self,
        render_runtime: str,
        solo_ed: dict[str, Any],
        asset_manifest: dict[str, Any],
        proxy_path: Path,
        profile: Optional[str],
        hdr_encode: Optional[dict[str, Any]] = None,
    ) -> ToolResult:
        """Render ONE solo scene to proxy_path via the locked runtime (lean path).

        `hdr_encode` (ffmpeg path only) carries the per-scene HDR preserve/promote/
        tonemap decision into `_compose`. remotion/hyperframes emit SDR proxies, so
        an HDR-preserve request there is silently SDR — render_proxies only sets
        hdr_encode when render_runtime='ffmpeg', so this can't happen in practice.
        """
        proxy_path = Path(proxy_path)
        proxy_path.parent.mkdir(parents=True, exist_ok=True)

        if render_runtime == "ffmpeg":
            # _compose wants cut sources as real file paths (already resolved here).
            return self._compose({
                "edit_decisions": solo_ed,
                "output_path": str(proxy_path),
                "profile": profile,
                "hdr_encode": hdr_encode,
            })
        if render_runtime == "remotion":
            inp: dict[str, Any] = {"edit_decisions": solo_ed, "output_path": str(proxy_path)}
            if profile:
                inp["profile"] = profile
            return self._remotion_render(inp)
        if render_runtime == "hyperframes":
            return self._render_via_hyperframes(
                inputs={"output_path": str(proxy_path)},
                edit_decisions=solo_ed,
                asset_manifest=asset_manifest,
                resolved_cuts=solo_ed["cuts"],
                output_path=proxy_path,
                profile=profile,
            )
        return ToolResult(success=False, error=f"unknown render_runtime {render_runtime!r}")

    def _build_assemble_edl(
        self,
        original: dict[str, Any],
        proxies: list[dict[str, Any]],
        hdr_meta: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Build an ffmpeg-runtime edit_decisions that concatenates the proxies.

        Each assemble cut references its proxy 1:1 (in=0..duration) and re-applies
        the scene's per-cut speed / transform / cross-scene transitions at the
        cheap FFmpeg layer (matched to the original cut by scene id). overlays /
        audio / music / subtitles / renderer_family pass through.

        The result MUST be rendered via operation="render" (render_runtime=ffmpeg),
        NOT operation="compose" — compose drops overlays[] and layer routing.
        """
        orig_by_id = {
            str(c.get("id") or f"scene_{i:03d}"): c
            for i, c in enumerate(original.get("cuts", []))
        }
        assemble_cuts: list[dict[str, Any]] = []
        for p in proxies:
            sid = p["scene_id"]
            oc = orig_by_id.get(sid, {})
            try:
                out_s = round(float(p["duration_seconds"]), 4)
            except (TypeError, ValueError):
                out_s = 0.0
            cut: dict[str, Any] = {
                "id": sid,
                "source": p["proxy_path"],
                "in_seconds": 0.0,
                "out_seconds": out_s,
            }
            for k in ("speed", "transform", "transition_in", "transition_out", "transition_duration"):
                if k in oc:
                    if k == "transform":
                        # CROP is already baked into the proxy (it's source-px and can't run on the
                        # canvas-sized proxy) — carry only any NON-crop transform so it isn't applied
                        # twice. On the ffmpeg proxy path nothing else lives in transform today.
                        t = {tk: tv for tk, tv in (oc["transform"] or {}).items() if tk != "crop"}
                        if t:
                            cut[k] = t
                    else:
                        cut[k] = oc[k]
            assemble_cuts.append(cut)

        assembled: dict[str, Any] = {
            "version": original.get("version", "1.0"),
            "render_runtime": "ffmpeg",
            "cuts": assemble_cuts,
        }
        for k in ("renderer_family", "overlays", "audio", "music", "subtitles", "transitions"):
            if original.get(k) is not None:
                assembled[k] = original[k]

        # Make the two-phase render legible to governance: this ffmpeg pass
        # ASSEMBLES proxies that were rendered in the locked runtime — it is NOT a
        # runtime swap. Drop the carried proposal_render_runtime (which would read
        # as a remotion->ffmpeg swap in the final-review check) and record the real
        # proxy runtime for the audit trail.
        meta = dict(original.get("metadata") or {})
        meta.pop("proposal_render_runtime", None)
        meta["assemble_of_proxies"] = True
        meta["proxy_render_runtime"] = original.get("render_runtime")
        # The proxies were rendered HDR (10-bit + tags); record the target color so
        # _render_via_ffmpeg keeps the assemble re-encode (concat/overlays/subtitle
        # burn) 10-bit + tagged rather than auto-negotiating back down to SDR.
        if hdr_meta:
            meta["hdr"] = hdr_meta
        assembled["metadata"] = meta
        return assembled

    def _compose(self, inputs: dict[str, Any]) -> ToolResult:
        """FFmpeg composition: cut segments, transitions, audio, subtitles.

        Handles video sources only. Still images and animated scene types
        are routed to Remotion via the render operation — call compose
        directly only for pure video pipelines (e.g. talking-head).

        Canvas: every segment is normalized to a single target canvas
        (scale + letterbox pad + fps + sar=1). Precedence:
        edit_decisions.metadata.compose_target {width,height,fps} >
        profile-resolved resolution > 1920x1080@30.

        Transitions: cuts[].transition_in/transition_out render via xfade /
        acrossfade. The join A→B is owned by B's transition_in; if both
        A.transition_out and B.transition_in are set, B.transition_in wins
        (B owns its own entrance). Durations clamp to [0.1, 2.0]s (default
        0.5, overridable globally via metadata.default_transition_duration).
        xfade offsets are computed from the POST-normalization probed
        segment durations — never from the requested in/out math. A
        timeline with no transitions takes the original concat-demuxer
        copy path, byte-for-byte unchanged behavior.

        Crop: cuts[].transform.crop {x,y,width,height} is applied to the
        source BEFORE scale/pad.

        Limitations: output is 8-bit SDR yuv420p (HDR sources must be
        handled per AGENT_GUIDE before composing); each crossfade shortens
        the timeline by its duration (standard xfade semantics); cuts[].layer
        is NOT routed here — all cuts concatenate sequentially (with a
        warning). Multi-track layer routing lives in _render_via_ffmpeg.
        """
        edit_decisions = inputs.get("edit_decisions")
        if not edit_decisions:
            return ToolResult(success=False, error="edit_decisions required for compose")

        output_path = Path(inputs.get("output_path", "composed_output.mp4"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path = inputs.get("audio_path")
        subtitle_path = inputs.get("subtitle_path")
        codec = inputs.get("codec", "libx264")
        crf = inputs.get("crf", 23)
        preset = inputs.get("preset", "medium")
        profile_name = inputs.get("profile")

        cuts = edit_decisions.get("cuts", [])
        if not cuts:
            return ToolResult(success=False, error="No cuts in edit_decisions")

        # Canvas precedence: metadata.compose_target > profile > 1920x1080@30.
        canvas_err, target_w, target_h, target_fps = self._resolve_canvas(
            edit_decisions, profile_name
        )
        if canvas_err:
            return ToolResult(success=False, error=canvas_err)
        fps_str = f"{target_fps:g}"

        # Per-clip position/scale + project background — applied ONLY when the
        # ASSEMBLE pass (_render_via_ffmpeg) sets this gate. NEVER derived from
        # metadata.background here, so a solo-proxy render (which carries the full
        # metadata) leaves proxy content + cache key untouched. None ⇒ legacy
        # black-letterbox/fit/center, byte-identical to before.
        composite_background = inputs.get("composite_background")
        if isinstance(composite_background, dict):
            bg_color = self._normalize_ffmpeg_color(
                composite_background.get("color"), "black"
            )
            bg_image = composite_background.get("image") or None
        else:
            bg_color = "black"
            bg_image = None

        # HDR preservation: when render_proxies resolves the timeline as HDR it
        # passes a per-scene `hdr_encode` dict here (encoder + 10-bit pixfmt +
        # HLG/PQ color tags, and a `vf_prefix` color-conversion chain for
        # promote/tonemap). None (the default) ⇒ legacy 8-bit SDR, byte-identical
        # to before. `_video_output_args` owns the encode tail; `vf_prefix` is
        # spliced into each segment's filterchain by `_segment_base_vf`.
        hdr_encode = inputs.get("hdr_encode") or None
        hdr_vf_prefix = (
            hdr_encode.get("vf_prefix") if isinstance(hdr_encode, dict) else ""
        ) or ""

        # Per-join transition resolution (B.transition_in wins over A.transition_out).
        joins, transition_warnings = self._resolve_joins(cuts, edit_decisions.get("metadata"))
        has_transitions = any(j is not None for j in joins)

        # Direct compose calls do NOT route layers — only operation='render'
        # (render_runtime='ffmpeg') lifts overlay-layer cuts into the PiP pass.
        if any((c.get("layer") or "primary") == "overlay" for c in cuts):
            transition_warnings.append(
                "cuts[].layer='overlay' is only routed to the PiP overlay pass by "
                "operation='render' (render_runtime='ffmpeg'); operation='compose' "
                "concatenates ALL cuts sequentially."
            )

        # Resolve subtitle style using the layered priority resolver
        # (explicit > edit_decisions > playbook > defaults)
        playbook_data = inputs.get("playbook")
        resolved_sub_style = self._resolve_subtitle_style(
            inputs.get("subtitle_style"),
            edit_decisions,
            playbook_data,
        )
        inputs = dict(inputs)
        inputs["subtitle_style"] = resolved_sub_style

        ed_subs = edit_decisions.get("subtitles", {})
        if ed_subs.get("source") and not subtitle_path:
            subtitle_path = ed_subs["source"]

        temp_dir = output_path.parent / ".compose_tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_segments: list[Path] = []
        concat_path: Path | None = None
        concat_out: Path | None = None

        try:
            for i, cut in enumerate(cuts):
                source = Path(cut["source"])
                if not source.exists():
                    return ToolResult(success=False, error=f"Cut source not found: {source}")

                seg_path = temp_dir / f"seg_{i:04d}.mp4"
                in_s = cut["in_seconds"]
                out_s = cut["out_seconds"]
                duration = out_s - in_s
                speed = cut.get("speed", 1.0)

                # Per-clip position/scale + project background compositing is applied
                # ONLY on the proxy-assemble pass, which sets inputs['composite_background']
                # (a {color, image} dict). It is NEVER read from metadata here, so the
                # solo-proxy render (which carries metadata.background) does NOT bake a
                # background and its content/cache key is unchanged. See _render_via_ffmpeg.
                cut_bg_color = bg_color
                cut_bg_image = bg_image
                # Only deviate from the legacy (black, fit, centered) path when there's
                # a real reason to: a configured background, or a non-default transform.
                t = cut.get("transform") or {}
                pos = t.get("position", "center")
                sc = t.get("scale", 1.0)
                non_default_transform = (
                    (composite_background is not None)
                    and ((pos != "center") or (sc not in (1.0, 1)))
                )
                cut_apply_transform = bool(
                    composite_background is not None
                    and (cut_bg_image is not None or non_default_transform)
                )

                if self._is_image(source):
                    # Reject a zero/negative-duration image cut up front: `-loop 1` with a
                    # non-positive `-t` makes ffmpeg loop the still FOREVER (infinite encode →
                    # hang + disk fill). The video branch fails fast on this implicitly; mirror it.
                    if duration <= 0:
                        return ToolResult(
                            success=False,
                            error=(
                                f"cuts[{i}]: image source requires out_seconds > in_seconds "
                                f"(got duration {duration})"
                            ),
                        )
                    # Still image as a MAIN-timeline clip: loop it into a video
                    # segment of the cut's PROJECT duration. Speed has no real meaning
                    # for a still, so fold it into the looped length (duration / speed)
                    # to match the timeline's cutDuration without a setpts pass. Stills
                    # carry no audio — synthesize a silent stereo track (mirrors the
                    # silent-video path) so every concat segment has the same layout.
                    seg_seconds = duration / speed if speed and speed > 0 else duration
                    # A still cannot carry SOURCE HDR (it's generated/looped); in an
                    # HDR timeline it is PROMOTED into the HDR container via the same
                    # vf_prefix as any SDR source, so it concats uniformly.
                    vf_err, vf_parts, complex_spec = self._segment_base_vf(
                        cut, i, target_w, target_h, fps_str,
                        bg_color=cut_bg_color, bg_image=cut_bg_image,
                        apply_transform=cut_apply_transform, seg_seconds=seg_seconds,
                        hdr_vf_prefix=hdr_vf_prefix,
                    )
                    if vf_err:
                        return ToolResult(success=False, error=vf_err)
                    cmd = [
                        "ffmpeg", "-y",
                        "-loop", "1",
                        "-t", str(seg_seconds),
                        "-i", str(source),
                        "-f", "lavfi",
                        "-t", str(seg_seconds),
                        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    ]
                    # Source=0, anullsrc=1; any bg input lands at index 2. Video
                    # always maps from input 0, silent audio from input 1.
                    if complex_spec is None:
                        cmd += ["-filter:v", ",".join(vf_parts), "-map", "0:v:0"]
                    else:
                        graph = complex_spec["filtergraph"].replace("{bg}", "2")
                        cmd += complex_spec["inputs"]
                        cmd += ["-filter_complex", graph, "-map", complex_spec["vlabel"]]
                    cmd += ["-map", "1:a:0"]
                    cmd += self._video_output_args(hdr_encode, codec, crf, preset, fps_str)
                    cmd += [
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-ar", "48000",
                        "-ac", "2",
                        str(seg_path),
                    ]
                    self.run_command(cmd)
                else:
                    # Video source: trim to segment.
                    #
                    # Semantics:
                    #   -ss BEFORE -i   → fast input-level seek to in_s
                    #   -t  AFTER  -i   → "play for `duration` seconds"
                    #                     (unambiguous regardless of seek mode)
                    #
                    # We MUST re-encode here — `-c copy` cannot do frame-accurate
                    # cuts because it snaps to keyframes. With sparse GOPs (common
                    # in Pexels / AI-generated clips), stream-copy can produce
                    # segments significantly longer than `duration`, breaking the
                    # target timeline. Re-encoding with libx264/AAC is slower but
                    # gives exact cut boundaries. Same resolution in → same
                    # resolution out, so same-res inputs concat cleanly.
                    # Normalize every segment to a consistent container so the
                    # concat-copy step is always safe (and xfade inputs match).
                    # The concat demuxer with `-c copy` requires identical codec /
                    # resolution / fps / pix_fmt / sar across ALL segments —
                    # otherwise it throws "Non-monotonous DTS" or silently
                    # produces corrupt output. xfade likewise requires matching
                    # resolution/fps/pix_fmt on both inputs.
                    #
                    # Target canvas comes from compose_target > profile >
                    # 1920x1080@30 (resolved above). Smaller sources letterbox;
                    # larger ones downscale.
                    seg_seconds = duration / speed if speed and speed > 0 else duration
                    vf_err, vf_parts, complex_spec = self._segment_base_vf(
                        cut, i, target_w, target_h, fps_str,
                        bg_color=cut_bg_color, bg_image=cut_bg_image,
                        apply_transform=cut_apply_transform, seg_seconds=seg_seconds,
                        hdr_vf_prefix=hdr_vf_prefix,
                    )
                    if vf_err:
                        return ToolResult(success=False, error=vf_err)
                    af_parts: list[str] = []
                    if speed != 1.0:
                        af_parts.append(self._build_atempo(speed))
                        if complex_spec is None:
                            # Single-input path: setpts rides on the -filter:v chain.
                            vf_parts.append(f"setpts={1.0/speed}*PTS")
                        else:
                            # Complex path: fold setpts into the foreground clip chain
                            # (right after the [0:v] label) so retiming applies to the
                            # composited clip, not the bg.
                            complex_spec = dict(complex_spec)
                            complex_spec["filtergraph"] = complex_spec["filtergraph"].replace(
                                "[0:v]", f"[0:v]setpts={1.0/speed}*PTS,", 1
                            )

                    # Audio handling: some source clips have no audio stream
                    # (Pexels stock often ships silent). If we unconditionally
                    # ask ffmpeg to copy/encode the 0:a stream it errors out.
                    # Probe for an audio stream first — if present, transcode
                    # to AAC; if absent, synthesize a silent stereo track so
                    # concat segments have a consistent stream layout.
                    has_audio = self._has_audio_stream(source)

                    # Source is input 0. A synthesized silent track (when the
                    # source has no audio) is input 1. Any background input
                    # (complex path only) lands AFTER those — index 1 (real
                    # audio) or 2 (silent audio injected).
                    cmd = [
                        "ffmpeg", "-y",
                        "-ss", str(in_s),
                        "-t", str(duration),
                        "-i", str(source),
                    ]
                    if not has_audio:
                        cmd += [
                            "-f", "lavfi", "-t", str(duration),
                            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                        ]
                    bg_input_idx = "2" if not has_audio else "1"

                    if complex_spec is None:
                        cmd += ["-filter:v", ",".join(vf_parts)]
                        if af_parts:
                            cmd += ["-filter:a", ",".join(af_parts)]
                        if not has_audio:
                            cmd += ["-map", "0:v:0", "-map", "1:a:0"]
                        # has_audio + simple: leave ffmpeg's default stream selection
                        # (matches the legacy path that emitted no -map here).
                    else:
                        # Complex (filter_complex) path: -filter:a / -af cannot
                        # coexist with -filter_complex, so any atempo (speed) is
                        # folded INTO the complex graph as a labeled audio chain.
                        graph = complex_spec["filtergraph"].replace("{bg}", bg_input_idx)
                        audio_in = "0:a" if has_audio else "1:a"
                        if af_parts:
                            graph += f";[{audio_in}]{','.join(af_parts)}[aout]"
                            audio_map = "[aout]"
                        else:
                            audio_map = audio_in
                        cmd += complex_spec["inputs"]
                        cmd += [
                            "-filter_complex", graph,
                            "-map", complex_spec["vlabel"],
                            "-map", audio_map,
                        ]

                    cmd += self._video_output_args(hdr_encode, codec, crf, preset, fps_str)
                    cmd += [
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    ]

                    cmd.append(str(seg_path))
                    self.run_command(cmd)

                temp_segments.append(seg_path)

            # Step 2: Join segments. Transition-free timelines keep the original
            # concat-demuxer copy path (back-compat contract: zero behavior
            # change). Any declared transition switches to a filter_complex
            # chain of xfade/acrossfade (+ concat filter for hard-cut joins).
            concat_out = temp_dir / "concat.mp4"
            used_xfade = has_transitions and len(temp_segments) > 1
            xfade_filtergraph = ""
            if used_xfade:
                err, xfade_filtergraph = self._transitions_concat(
                    temp_segments, joins, fps_str, codec, crf, preset, concat_out,
                    hdr_encode=hdr_encode,
                )
                if err:
                    return ToolResult(success=False, error=f"transition render failed: {err}")
            else:
                concat_path = temp_dir / "concat_list.txt"
                with open(concat_path, "w", encoding="utf-8") as f:
                    for seg in temp_segments:
                        safe = str(seg.resolve()).replace("\\", "/")
                        f.write(f"file '{safe}'\n")

                cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", str(concat_path),
                    "-c", "copy",
                    str(concat_out),
                ]
                self.run_command(cmd)

            # Step 3: Apply subtitles and/or replace audio
            final_input = concat_out
            vfilters = []

            if subtitle_path and Path(subtitle_path).exists():
                style = inputs.get("subtitle_style", {})
                ass_style = self._build_subtitle_style(style)
                sub_escaped = str(Path(subtitle_path).resolve()).replace("\\", "/").replace(":", "\\:")
                vfilters.append(f"subtitles='{sub_escaped}':force_style='{ass_style}'")

            cmd = ["ffmpeg", "-y", "-i", str(final_input)]

            if audio_path and Path(audio_path).exists():
                cmd.extend(["-i", audio_path])

            # The canvas (resolution + fps, including any profile) was already
            # applied per segment, so no output-level -s/-r is needed here —
            # a late -s on the letterboxed canvas would only distort it.
            needs_reencode = bool(vfilters)

            if needs_reencode:
                cmd.extend(["-vf", ",".join(vfilters)])
                if hdr_encode:
                    # Keep the subtitle-burn re-encode 10-bit + HLG/PQ tags so HDR
                    # isn't dropped at the last pass. (-r here is harmless.)
                    cmd.extend(self._video_output_args(hdr_encode, codec, crf, preset, fps_str))
                else:
                    cmd.extend(["-c:v", codec, "-crf", str(crf), "-preset", preset])
            else:
                cmd.extend(["-c:v", "copy"])

            if audio_path and Path(audio_path).exists():
                # Use type-based selectors (0:v, 1:a) instead of index-based
                # (0:v:0) because source videos may have audio as stream 0
                # and video as stream 1 (e.g. Kling-generated clips).
                cmd.extend(["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-shortest"])
            else:
                cmd.extend(["-c:a", "copy"])

            cmd.append(str(output_path))
            self.run_command(cmd)

            data: dict[str, Any] = {
                "operation": "compose",
                "cut_count": len(cuts),
                "has_subtitles": subtitle_path is not None,
                "has_mixed_audio": audio_path is not None,
                "profile": profile_name,
                "canvas": {"width": target_w, "height": target_h, "fps": target_fps},
                "used_xfade": used_xfade,
                "transitions_applied": (
                    sum(1 for j in joins if j is not None) if used_xfade else 0
                ),
                "xfade_filtergraph": xfade_filtergraph or None,
                "output": str(output_path),
            }
            if transition_warnings:
                data["warnings"] = transition_warnings
            return ToolResult(
                success=True,
                data=data,
                artifacts=[str(output_path)],
            )
        except subprocess.CalledProcessError as e:
            # Surface the ffmpeg stderr (the segment/mux calls in this block are
            # otherwise bare run_command — a raw CalledProcessError drops the reason,
            # e.g. an HDR encoder rejecting a profile/pixfmt). Mirrors the pattern in
            # _transitions_concat / _overlay so encoder failures self-diagnose.
            stderr = ((getattr(e, "stderr", "") or "") or "ffmpeg compose failed").strip()[-800:]
            enc = (hdr_encode or {}).get("encoder") or codec
            return ToolResult(
                success=False,
                error=f"ffmpeg compose failed (video codec={enc}): {stderr}",
            )
        finally:
            # Cleanup temp files
            for f in temp_segments:
                if f.exists():
                    f.unlink()
            for f in [concat_path, concat_out]:
                if f is not None and f.exists():
                    f.unlink()
            if temp_dir.exists():
                try:
                    temp_dir.rmdir()
                except OSError:
                    pass

    _REMOTION_SCENE_TYPES = {
        "text_card", "stat_card", "callout", "comparison", "progress", "chart",
    }

    # Maps renderer_family (set at proposal stage) to Remotion composition ID.
    # Each family MUST map to a distinct composition — collapsing defeats visual grammar.
    # Maps renderer_family → Remotion composition ID.
    # Only compositions registered in remotion-composer/src/Root.tsx are valid.
    # Current compositions: Explainer, CinematicRenderer, TalkingHead
    RENDERER_FAMILY_MAP = {
        "explainer-data": "Explainer",
        "explainer-teacher": "Explainer",
        "cinematic-trailer": "CinematicRenderer",
        "documentary-montage": "CinematicRenderer",
        "product-reveal": "Explainer",
        "screen-demo": "Explainer",
        "presenter": "TalkingHead",
        "animation-first": "Explainer",
        "social-reel": "SocialReel",
    }

    @classmethod
    def _get_composition_id(cls, renderer_family: str) -> str:
        """Resolve renderer_family to Remotion composition ID.

        Raises ValueError if renderer_family is not recognized — the caller
        must set it at proposal stage.
        """
        comp = cls.RENDERER_FAMILY_MAP.get(renderer_family)
        if comp is None:
            raise ValueError(
                f"Unknown renderer_family {renderer_family!r}. "
                f"Valid families: {sorted(cls.RENDERER_FAMILY_MAP)}. "
                f"Set renderer_family at proposal stage."
            )
        return comp

    @staticmethod
    def _build_theme_from_playbook(
        playbook_name: str | None,
        composition_data: dict | None,
    ) -> dict[str, Any] | None:
        """Derive a Remotion ThemeConfig from a playbook's actual color values.

        Instead of passing a playbook name and hoping Remotion has a matching
        preset, we read the playbook YAML and extract concrete colors/fonts.
        This means custom playbooks, overridden palettes, and per-project
        styles all flow through to Remotion automatically.

        Falls back to extracting colors from edit_decisions metadata if
        no playbook is loadable.
        """
        theme: dict[str, Any] = {}

        # Try to load the playbook YAML
        playbook: dict[str, Any] = {}
        if playbook_name:
            try:
                from styles.playbook_loader import load_playbook
                playbook = load_playbook(playbook_name)
            except Exception:
                pass

        if playbook:
            vl = playbook.get("visual_language", {})
            palette = vl.get("color_palette", {})
            typo = playbook.get("typography", {})

            # Extract primary/accent — may be a list (gradient stops) or string
            primary_raw = palette.get("primary", ["#2563EB"])
            accent_raw = palette.get("accent", ["#F59E0B"])
            primary = primary_raw[0] if isinstance(primary_raw, list) else primary_raw
            accent = accent_raw[0] if isinstance(accent_raw, list) else accent_raw

            bg = palette.get("background", "#FFFFFF")
            text = palette.get("text", "#1F2937")
            surface = palette.get("surface", bg)
            muted = palette.get("muted_text", "#6B7280")

            # Build chart colors from all palette entries
            chart_colors = []
            for key in ["primary", "accent", "secondary", "success", "warning", "info"]:
                val = palette.get(key)
                if val:
                    chart_colors.append(val[0] if isinstance(val, list) else val)
            if len(chart_colors) < 3:
                chart_colors = [primary, accent, "#10B981", "#8B5CF6", "#EC4899", "#06B6D4"]

            theme = {
                "primaryColor": primary,
                "accentColor": accent,
                "backgroundColor": bg,
                "surfaceColor": surface,
                "textColor": text,
                "mutedTextColor": muted,
                "headingFont": typo.get("heading", {}).get("font", "Inter"),
                "bodyFont": typo.get("body", {}).get("font", "Inter"),
                "monoFont": typo.get("code", {}).get("font", "JetBrains Mono"),
                "chartColors": chart_colors[:6],
                "springConfig": {"damping": 20, "stiffness": 120, "mass": 1},
                "transitionDuration": 0.4,
            }

            # Derive caption colors from the palette
            theme["captionHighlightColor"] = primary
            # Caption background: semi-transparent version of the bg color
            theme["captionBackgroundColor"] = (
                f"rgba(255, 255, 255, 0.85)" if bg.upper() in ("#FFFFFF", "#FAFAFA", "#F9FAFB")
                else f"rgba(15, 23, 42, 0.75)"
            )

            # Motion style from playbook
            motion = playbook.get("motion", {})
            pace = motion.get("pace", "moderate")
            if pace == "fast":
                theme["springConfig"] = {"damping": 12, "stiffness": 80, "mass": 1}
                theme["transitionDuration"] = 0.3
            elif pace == "slow":
                theme["springConfig"] = {"damping": 25, "stiffness": 150, "mass": 1}
                theme["transitionDuration"] = 0.6

        # Fallback: try to extract from edit_decisions metadata
        if not theme and composition_data:
            meta = composition_data.get("metadata", {})
            if meta.get("primary_color"):
                theme = {
                    "primaryColor": meta["primary_color"],
                    "accentColor": meta.get("accent_color", "#F59E0B"),
                    "backgroundColor": meta.get("background_color", "#FFFFFF"),
                    "surfaceColor": meta.get("surface_color", "#F9FAFB"),
                    "textColor": meta.get("text_color", "#1F2937"),
                    "mutedTextColor": "#6B7280",
                    "headingFont": meta.get("heading_font", "Inter"),
                    "bodyFont": meta.get("body_font", "Inter"),
                    "monoFont": "JetBrains Mono",
                    "chartColors": meta.get("chart_colors", ["#2563EB", "#F59E0B", "#10B981"]),
                    "springConfig": {"damping": 20, "stiffness": 120, "mass": 1},
                    "transitionDuration": 0.4,
                    "captionHighlightColor": meta["primary_color"],
                    "captionBackgroundColor": "rgba(255, 255, 255, 0.85)",
                }

        return theme if theme else None

    def _needs_remotion(self, cuts: list[dict]) -> bool:
        """Determine whether Remotion should handle this composition.

        Remotion is the DEFAULT composition engine when available.  It handles
        video clips (via <OffthreadVideo>), still images, animated scene types,
        component types, transitions, and mixed content — all in a single
        React-based render pass.

        Returns False (i.e. use FFmpeg) only when Remotion is not
        available. For `operation="render"` the governance default is
        Remotion-first: the renderer family was chosen earlier, and the
        tool should preserve that decision instead of silently
        downgrading to FFmpeg.

        This "Remotion-first" policy means mixed content (video clips +
        animated stills + text cards) is always composed in Remotion, which
        can embed <OffthreadVideo> alongside React components natively.
        """
        # If Remotion isn't installed, fall back to FFmpeg
        if not self._remotion_available():
            return False

        # Any rich content → Remotion (fast path, catches the obvious cases).
        # NOTE: cuts[].transition_in/out is deliberately NOT checked here —
        # the FFmpeg path renders transitions natively via xfade (_compose),
        # so a transition is no longer a Remotion-only feature. When
        # render_runtime='ffmpeg' this function is never consulted; the
        # locked runtime routes straight to _render_via_ffmpeg.
        for cut in cuts:
            source = cut.get("source", "")
            if source and Path(source).suffix.lower() in self._IMAGE_EXTENSIONS:
                return True
            if cut.get("type") in self._REMOTION_SCENE_TYPES:
                return True
            if cut.get("animation"):
                return True
            transform = cut.get("transform", {})
            if transform and transform.get("animation"):
                return True

        # Even for pure-video cuts, default to Remotion — it handles video
        # clips natively via <OffthreadVideo> and gives us transitions,
        # overlays, and profile scaling for free.
        return True

    def _pre_compose_validation(
        self,
        edit_decisions: dict[str, Any],
        resolved_cuts: list[dict],
        scene_plan: list[dict] | None = None,
    ) -> ToolResult | None:
        """Pre-compose quality gate — blocks render on critical violations.

        Checks:
        1. Delivery promise violation: motion-required brief with >70% still cuts → BLOCK
        2. Slideshow risk score "fail" (average ≥ 4.0) → BLOCK
        3. Missing renderer_family → WARN (log only, don't block)

        Returns a failed ToolResult if render should be blocked, None if OK to proceed.
        """
        log = logging.getLogger("video_compose")
        warnings: list[str] = []
        blocks: list[str] = []

        # --- 1. Delivery promise check ---
        delivery_data = edit_decisions.get("metadata", {}).get("delivery_promise")
        if not delivery_data:
            # Also check top-level (proposal_packet nests it at top level)
            delivery_data = edit_decisions.get("delivery_promise")

        if delivery_data:
            try:
                from lib.delivery_promise import DeliveryPromise
                promise = DeliveryPromise.from_dict(delivery_data)
                result = promise.validate_cuts(resolved_cuts)
                if not result["valid"]:
                    for v in result["violations"]:
                        blocks.append(f"Delivery promise violation: {v}")
            except Exception as e:
                log.warning("Could not validate delivery promise: %s", e)
        else:
            warnings.append("No delivery_promise in edit_decisions — skipping promise validation")

        # --- 2. Slideshow risk check ---
        renderer_family = edit_decisions.get("renderer_family")
        scenes = scene_plan or []

        # If no scene_plan passed, try to extract scene info from cuts
        if not scenes and resolved_cuts:
            scenes = [
                {
                    "type": c.get("type", ""),
                    "description": c.get("reason", ""),
                    "shot_language": c.get("shot_language", {}),
                    "shot_intent": c.get("shot_intent"),
                    "narrative_role": c.get("narrative_role"),
                    "information_role": c.get("information_role"),
                    "hero_moment": c.get("hero_moment", False),
                }
                for c in resolved_cuts
            ]

        if scenes:
            try:
                from lib.slideshow_risk import score_slideshow_risk
                render_runtime = edit_decisions.get("render_runtime")
                risk = score_slideshow_risk(
                    scenes, edit_decisions, renderer_family, render_runtime
                )
                if risk["verdict"] == "fail":
                    blocks.append(
                        f"Slideshow risk score {risk['average']:.1f}/5.0 (verdict: fail). "
                        f"Video plan looks like a slideshow — revise scene plan before rendering."
                    )
                elif risk["verdict"] == "revise":
                    warnings.append(
                        f"Slideshow risk score {risk['average']:.1f}/5.0 (verdict: revise). "
                        f"Consider improving scene variety before final render."
                    )
            except Exception as e:
                log.warning("Could not compute slideshow risk: %s", e)

        # --- 3. Missing renderer_family (BLOCK — must be set at proposal) ---
        if not renderer_family:
            blocks.append(
                "No renderer_family in edit_decisions. "
                "renderer_family must be set at proposal stage and locked before compose. "
                "Re-run the proposal stage with a renderer_family selection."
            )

        # Log warnings
        for w in warnings:
            log.warning("[pre-compose] %s", w)

        # Block on critical violations
        if blocks:
            return ToolResult(
                success=False,
                error=(
                    "Pre-compose validation failed — render blocked.\n"
                    + "\n".join(f"  • {b}" for b in blocks)
                    + ("\n\nWarnings:\n" + "\n".join(f"  • {w}" for w in warnings) if warnings else "")
                ),
            )

        return None

    def _render(self, inputs: dict[str, Any]) -> ToolResult:
        """High-level render: assemble edit decisions + asset manifest into final video.

        This is the primary entry point for the compose-director skill.
        It resolves asset IDs and routes to the composition engine:

        - **Remotion (default):** Used for all compositions when available —
          video clips, images, animated scenes, component types, mixed content.
          Remotion embeds video via <OffthreadVideo> and handles transitions,
          overlays, and profile scaling natively.
        - **FFmpeg (fallback):** Used only when Remotion is unavailable, or
          when the agent explicitly calls operation='compose' for simple
          trim/concat operations.

        The agent should pass edit_decisions, asset_manifest, and optionally
        profile, subtitle_path, audio_path, and options.
        """
        edit_decisions = inputs.get("edit_decisions")
        asset_manifest = inputs.get("asset_manifest")
        if not edit_decisions:
            return ToolResult(success=False, error="edit_decisions required for render")
        if not asset_manifest:
            return ToolResult(success=False, error="asset_manifest required for render")

        output_path = Path(inputs.get("output_path", "renders/output.mp4"))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build asset lookup: id -> asset info
        asset_lookup = {a["id"]: a for a in asset_manifest.get("assets", [])}

        cuts = edit_decisions.get("cuts", [])
        if not cuts:
            return ToolResult(success=False, error="No cuts in edit_decisions")

        # Resolve asset IDs in cuts to file paths
        resolved_cuts = []
        for cut in cuts:
            source_id = cut.get("source", "")
            resolved_cut = dict(cut)
            if source_id in asset_lookup:
                resolved_cut["source"] = asset_lookup[source_id]["path"]
            resolved_cuts.append(resolved_cut)

        # --- Pre-compose validation gate ---
        scene_plan = inputs.get("scene_plan")
        validation_block = self._pre_compose_validation(edit_decisions, resolved_cuts, scene_plan)
        if validation_block is not None:
            return validation_block

        # Also accept profile as "output_profile" (skill convention) or "profile"
        profile = inputs.get("profile") or inputs.get("output_profile")

        # --- Runtime routing: honor render_runtime locked at proposal ---
        # Silent swaps are forbidden by governance. If the chosen runtime
        # is unavailable, surface a structured blocker rather than quietly
        # picking a different engine. Missing render_runtime is itself a
        # governance violation — edit_decisions.schema.json requires it.
        render_runtime = (edit_decisions.get("render_runtime") or "").strip().lower()

        if not render_runtime:
            return ToolResult(
                success=False,
                error=(
                    "render_runtime is not set in edit_decisions. Per governance, "
                    "it MUST be locked at proposal stage (proposal_packet."
                    "production_plan.render_runtime) and carried forward through "
                    "edit_decisions.render_runtime. Valid values: 'remotion', "
                    "'hyperframes', 'ffmpeg'. Re-run the proposal stage with an "
                    "explicit runtime choice — do NOT default this field."
                ),
            )

        if render_runtime == "hyperframes":
            return self._render_via_hyperframes(
                inputs=inputs,
                edit_decisions=edit_decisions,
                asset_manifest=asset_manifest,
                resolved_cuts=resolved_cuts,
                output_path=output_path,
                profile=profile,
            )
        if render_runtime == "ffmpeg":
            # Caller explicitly asked for FFmpeg — don't auto-upgrade to Remotion.
            return self._render_via_ffmpeg(
                inputs=inputs,
                edit_decisions=edit_decisions,
                asset_manifest=asset_manifest,
                resolved_cuts=resolved_cuts,
                output_path=output_path,
                profile=profile,
            )
        if render_runtime != "remotion":
            return ToolResult(
                success=False,
                error=(
                    f"Unknown render_runtime {render_runtime!r}. "
                    f"Valid values: remotion, hyperframes, ffmpeg. "
                    f"render_runtime must be set at proposal stage."
                ),
            )

        # --- Explicit Remotion path (render_runtime == 'remotion') ---
        if self._needs_remotion(resolved_cuts):
            remotion_inputs: dict[str, Any] = {
                "edit_decisions": dict(edit_decisions, cuts=resolved_cuts),
                "output_path": str(output_path),
            }
            if profile:
                remotion_inputs["profile"] = profile
            render_result = self._remotion_render(remotion_inputs)

            # Governance: NEVER silently fall back to FFmpeg when Remotion fails.
            # The agent must decide the fallback path, not the tool.
            if not render_result.success:
                renderer_family = edit_decisions.get("renderer_family", "unknown")
                return ToolResult(
                    success=False,
                    error=(
                        f"Remotion render failed for renderer_family={renderer_family!r}. "
                        f"Underlying error: {render_result.error}\n\n"
                        f"This composition requires Remotion (images, text cards, animations). "
                        f"Options:\n"
                        f"  1. Fix Remotion setup (cd remotion-composer && npm install)\n"
                        f"  2. Re-run with operation='compose' for FFmpeg-only (video cuts only)\n"
                        f"  3. Approve a degraded FFmpeg render (still images → Ken Burns)\n\n"
                        f"Per governance: renderer downgrade requires user approval."
                    ),
                )
        else:
            # --- FFmpeg fallback: only when Remotion is unavailable ---
            options = inputs.get("options", {})
            subtitle_burn = options.get("subtitle_burn", True)

            # Resolve subtitle_path from edit_decisions if not provided
            subtitle_path = inputs.get("subtitle_path")
            if subtitle_burn and not subtitle_path:
                ed_subs = edit_decisions.get("subtitles", {})
                if ed_subs.get("enabled") and ed_subs.get("source"):
                    subtitle_path = ed_subs["source"]

            # Build compose inputs
            compose_inputs = dict(inputs)
            compose_inputs["edit_decisions"] = dict(edit_decisions, cuts=resolved_cuts)
            compose_inputs["output_path"] = str(output_path)
            if subtitle_path:
                compose_inputs["subtitle_path"] = subtitle_path
            if profile:
                compose_inputs["profile"] = profile

            render_result = self._compose(compose_inputs)

        # --- Post-render: mandatory final self-review ---
        if render_result.success and output_path.exists():
            final_review = self._run_final_review(
                output_path,
                edit_decisions,
                inputs.get("proposal_packet"),
                narration_transcript_path=inputs.get("narration_transcript_path"),
                script_text=inputs.get("script_text") or self._read_text_file(
                    inputs.get("script_path")
                ),
            )

            # Attach final_review to the ToolResult data so the compose-director
            # skill can include it in the checkpoint alongside the render_report.
            if render_result.data is None:
                render_result.data = {}
            render_result.data["final_review"] = final_review
            render_result.data["final_review_status"] = final_review["status"]

            # If the self-review says fail, downgrade the ToolResult
            if final_review["status"] == "fail":
                return ToolResult(
                    success=False,
                    error=(
                        "Post-render self-review FAILED. The output is not presentable.\n"
                        + "\n".join(f"  • {i}" for i in final_review.get("issues_found", []))
                    ),
                    data=render_result.data,
                )

        return render_result

    def _render_via_hyperframes(
        self,
        *,
        inputs: dict[str, Any],
        edit_decisions: dict[str, Any],
        asset_manifest: dict[str, Any],
        resolved_cuts: list[dict],
        output_path: Path,
        profile: Optional[str],
    ) -> ToolResult:
        """Delegate to hyperframes_compose and run the mandatory final self-review.

        Governance: if HyperFrames is unavailable or fails, return a structured
        blocker — do NOT silently route to Remotion or FFmpeg. The agent must
        surface the blocker and get user approval before any runtime swap.
        """
        if not self._hyperframes_available():
            return ToolResult(
                success=False,
                error=(
                    "render_runtime='hyperframes' was locked at proposal, but "
                    "the HyperFrames runtime is not available on this machine. "
                    "Per governance this is a BLOCKER — surface it to the user "
                    "per AGENT_GUIDE.md > 'Escalate Blockers Explicitly' and wait "
                    "for approval before switching runtime. Requirements: "
                    "Node.js >= 22, FFmpeg, and npx on PATH. See "
                    "tools/video/hyperframes_compose.py for the specific missing piece."
                ),
            )

        try:
            from tools.video.hyperframes_compose import HyperFramesCompose
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Could not import hyperframes_compose: {e}",
            )

        workspace_path = (
            inputs.get("workspace_path")
            or str(output_path.parent.parent / "hyperframes")
        )

        # Pass the playbook through so the style bridge can emit CSS vars.
        playbook_data = inputs.get("playbook")
        if not playbook_data:
            playbook_name = (
                inputs.get("playbook_name")
                or (edit_decisions.get("metadata") or {}).get("playbook")
            )
            if playbook_name:
                try:
                    from styles.playbook_loader import load_playbook  # type: ignore
                    playbook_data = load_playbook(playbook_name)
                except Exception:
                    playbook_data = None

        hf_inputs: dict[str, Any] = {
            "operation": "render",
            "workspace_path": workspace_path,
            "output_path": str(output_path),
            "edit_decisions": dict(edit_decisions, cuts=resolved_cuts),
            "asset_manifest": asset_manifest,
        }
        if playbook_data:
            hf_inputs["playbook"] = playbook_data
        if profile:
            hf_inputs["profile"] = profile
        if "quality" in inputs:
            hf_inputs["quality"] = inputs["quality"]
        if "fps" in inputs:
            hf_inputs["fps"] = inputs["fps"]
        if "strict" in inputs:
            hf_inputs["strict"] = inputs["strict"]
        if "skip_contrast" in inputs:
            hf_inputs["skip_contrast"] = inputs["skip_contrast"]

        render_result = HyperFramesCompose().execute(hf_inputs)

        if not render_result.success:
            return ToolResult(
                success=False,
                error=(
                    f"HyperFrames render failed: {render_result.error}. "
                    "Per governance: do NOT silently fall back to Remotion or "
                    "FFmpeg. Surface the failure to the user along with the "
                    "hyperframes_compose step log before proposing a swap."
                ),
                data=render_result.data,
            )

        # Post-render: mandatory final self-review (identical contract to the Remotion path).
        if output_path.exists():
            final_review = self._run_final_review(
                output_path,
                edit_decisions,
                inputs.get("proposal_packet"),
                narration_transcript_path=inputs.get("narration_transcript_path"),
                script_text=inputs.get("script_text") or self._read_text_file(
                    inputs.get("script_path")
                ),
            )
            if render_result.data is None:
                render_result.data = {}
            render_result.data["final_review"] = final_review
            render_result.data["final_review_status"] = final_review["status"]
            if final_review["status"] == "fail":
                return ToolResult(
                    success=False,
                    error=(
                        "Post-render self-review FAILED (HyperFrames). The output is not presentable.\n"
                        + "\n".join(f"  • {i}" for i in final_review.get("issues_found", []))
                    ),
                    data=render_result.data,
                )

        return render_result

    def _assemble_hdr_encode(
        self,
        edit_decisions: dict[str, Any],
        base_cuts: list[dict[str, Any]],
        inputs: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Resolve the HDR encode for the assemble / direct-ffmpeg render.

        Two sources, in priority order:
          1. metadata.hdr — stamped by _build_assemble_edl when render_proxies
             resolved the timeline as HDR. The proxies are ALREADY in the target
             color space, so this is a pure PRESERVE pass (vf_prefix="").
          2. Direct render of raw footage (no proxies): preserve only when EVERY
             non-image base cut is HDR (one color decision applies to all segments
             in _compose, so a mixed direct render can't per-cut promote — route
             that through render_proxies). Returns None ⇒ legacy SDR.
        """
        meta_hdr = (edit_decisions.get("metadata") or {}).get("hdr")
        if isinstance(meta_hdr, dict) and meta_hdr.get("enabled"):
            return {
                "encoder": meta_hdr.get("encoder"),
                "pix_fmt": meta_hdr.get("pix_fmt") or "yuv420p10le",
                "primaries": meta_hdr.get("primaries"),
                "trc": meta_hdr.get("trc"),
                "colorspace": meta_hdr.get("colorspace"),
                "tag": "hvc1",
                "vf_prefix": "",
            }

        policy = str(inputs.get("hdr_policy") or "auto").strip().lower()
        if policy in ("tonemap", "sdr"):
            return None
        from tools.video._shared import is_hdr_source
        vids = [
            c for c in base_cuts
            if c.get("source") and Path(str(c["source"])).exists()
            and not self._is_image(Path(str(c["source"])))
        ]
        if not vids:
            return None
        infos = [is_hdr_source(Path(str(c["source"]))) for c in vids]
        if not all(x.get("hdr") for x in infos):
            return None  # mixed HDR/SDR direct render → SDR here; use render_proxies
        encs = self._hdr_encoders()
        enc = (
            "hevc_videotoolbox" if "hevc_videotoolbox" in encs
            else ("libx265" if "libx265" in encs else None)
        )
        if enc is None:
            return None  # can't preserve; don't block the direct path
        target = self._resolve_hdr_target(infos[0])
        return {
            "encoder": enc, "pix_fmt": "yuv420p10le",
            "primaries": target["primaries"], "trc": target["trc"],
            "colorspace": target["colorspace"], "tag": "hvc1", "vf_prefix": "",
        }

    # ── Structured-audio stem mixing (music bed + narration + sfx → one master) ──
    # When edit_decisions.audio carries STRUCTURED stems instead of a pre-mixed
    # `path`, the render mixes them here into a single master via the audio_mixer
    # full_mix engine and muxes that. This is what lets a timeline edited in the
    # MANUAL EDITOR (which has no agent to run audio_mixer.full_mix by hand)
    # produce music/SFX in the rendered output — and lets pipeline directors just
    # emit stems and rely on the render to mix. An explicit audio.path always wins
    # (see the caller); this only fires when no pre-mixed master is present. The
    # master REPLACES the base-clip audio, matching audio.path semantics — footage
    # whose own audio must survive should carry it as a narration stem (e.g.
    # asset_id referencing the source clip), which is the existing convention.
    @staticmethod
    def _music_regions(audio: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize `audio.music` to a LIST of region dicts. The schema allows a single OBJECT
        (one bed — legacy/agent shape) OR an ARRAY of region objects (multiple beds, e.g. after a
        timeline split). Each region may carry a [start_seconds, end_seconds] window. Returns []
        when there's no music."""
        m = (audio or {}).get("music")
        if m is None:
            return []
        seq = m if isinstance(m, list) else [m]
        return [r for r in seq if isinstance(r, dict)]

    @staticmethod
    def _has_structured_audio(audio: dict[str, Any]) -> bool:
        """True if `audio` carries any resolvable stem (music / narration / sfx)."""
        if not isinstance(audio, dict):
            return False
        if any(r.get("asset_id") for r in VideoCompose._music_regions(audio)):
            return True
        if any((s or {}).get("asset_id") for s in (audio.get("narration") or {}).get("segments") or []):
            return True
        if any((s or {}).get("asset_id") for s in audio.get("sfx") or []):
            return True
        return False

    @staticmethod
    def _structured_audio_tracks(
        audio: dict[str, Any], resolve
    ) -> Optional[dict[str, Any]]:
        """Map a structured `edit_decisions.audio` block → {tracks, ducking} for the
        audio_mixer `full_mix` operation. `resolve(asset_id) -> path | None` turns a
        stem ref into an on-disk file (returns None for missing/unresolvable stems,
        which are skipped). Narration segments become speech tracks anchored at their
        `start_seconds`; the music bed a single music track (volume + fades); each SFX
        an sfx track at its `start_seconds`. Ducking is derived from `music.ducking`
        (bool | object). Returns None when nothing resolves. Pure (no I/O)."""
        tracks: list[dict[str, Any]] = []

        for seg in (audio.get("narration") or {}).get("segments") or []:
            p = resolve((seg or {}).get("asset_id"))
            if not p:
                continue
            tracks.append({
                "path": p, "role": "speech",
                "start_seconds": max(0.0, float(seg.get("start_seconds") or 0)),
            })

        # Music: one track PER REGION. Each region can carry a [start_seconds, end_seconds] window
        # (delay to start + truncate to the window length) plus its own volume/fades. Ducking is
        # taken from the first region that specifies it (music ducks under speech, all beds together).
        music_regions = VideoCompose._music_regions(audio)
        has_music = False
        duck_raw: Any = None
        for region in music_regions:
            mp = resolve(region.get("asset_id"))
            if not mp:
                continue
            has_music = True
            mt: dict[str, Any] = {"path": mp, "role": "music"}
            if region.get("volume") is not None:
                mt["volume"] = float(region["volume"])
            if region.get("fade_in_seconds"):
                mt["fade_in_seconds"] = float(region["fade_in_seconds"])
            if region.get("fade_out_seconds"):
                mt["fade_out_seconds"] = float(region["fade_out_seconds"])
            start = max(0.0, float(region.get("start_seconds") or 0))
            if start > 0:
                mt["start_seconds"] = start
            if region.get("end_seconds") is not None:
                dur = float(region["end_seconds"]) - start
                if dur > 0:
                    mt["duration_seconds"] = dur
            tracks.append(mt)
            if duck_raw is None and region.get("ducking") is not None:
                duck_raw = region.get("ducking")

        for fx in audio.get("sfx") or []:
            p = resolve((fx or {}).get("asset_id"))
            if not p:
                continue
            st: dict[str, Any] = {
                "path": p, "role": "sfx",
                "start_seconds": max(0.0, float((fx or {}).get("start_seconds") or 0)),
            }
            if (fx or {}).get("volume") is not None:
                st["volume"] = float(fx["volume"])
            tracks.append(st)

        if not tracks:
            return None

        # Duck the music under speech only when there's both a bed AND a ducking
        # request. `duck_raw` (a region's `ducking`) is a bool or an object (attack_ms / release_ms).
        ducking: dict[str, Any] = {"enabled": False}
        if has_music and duck_raw:
            if isinstance(duck_raw, dict):
                ducking = {"enabled": bool(duck_raw.get("enabled", True))}
                for k in ("attack_ms", "release_ms"):
                    if duck_raw.get(k) is not None:
                        ducking[k] = duck_raw[k]
            else:
                ducking = {"enabled": bool(duck_raw)}
        return {"tracks": tracks, "ducking": ducking}

    def _mix_structured_audio(
        self, audio: dict[str, Any], asset_lookup: dict[str, Any], workdir: Path,
        base_audio_path: Optional[str] = None,
    ) -> Optional[str]:
        """Mix the structured stems into a single master file and return its path
        (or None if nothing resolved / the mix failed). `asset_lookup` maps asset_id →
        manifest entry; a ref that isn't a manifest id is treated as a literal path (so
        the editor path, which pre-resolves stems to absolute paths, also works).
        `base_audio_path`, when given, is layered in as a speech track (the footage VO
        the render already assembled) so music ducks under it and it isn't lost — used
        when there's no narration stem to act as the voice. Reuses AudioMixer.full_mix."""
        def resolve(asset_id: Any) -> Optional[str]:
            if not asset_id:
                return None
            info = asset_lookup.get(asset_id)
            cand = info.get("path") if isinstance(info, dict) and info.get("path") else asset_id
            try:
                return str(cand) if cand and Path(cand).exists() else None
            except OSError:
                return None

        spec = self._structured_audio_tracks(audio, resolve)
        tracks = list(spec["tracks"]) if spec else []
        ducking = spec["ducking"] if spec else {"enabled": False}
        if base_audio_path:
            # Base VO first so full_mix treats it as the speech anchor (music ducks under it).
            tracks = [{"path": str(base_audio_path), "role": "speech"}] + tracks
            if any(r.get("asset_id") for r in VideoCompose._music_regions(audio)):
                ducking = ducking if ducking.get("enabled") else {"enabled": True}
        if not tracks:
            return None
        out = Path(workdir) / "structured_mix.m4a"
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            from tools.audio.audio_mixer import AudioMixer
            res = AudioMixer().execute({
                "operation": "full_mix",
                "tracks": tracks,
                "ducking": ducking,
                "normalize": True,
                "output_path": str(out),
            })
        except Exception:
            return None
        if not getattr(res, "success", False) or not out.exists():
            return None
        return str(out)

    def _apply_structured_audio_mix(
        self, output_path: Path, edit_decisions: dict[str, Any],
        asset_manifest: Optional[dict[str, Any]], inputs: dict[str, Any],
    ) -> Optional[str]:
        """POST-assemble audio pass: when edit_decisions.audio carries structured stems
        (and no pre-mixed audio.path was muxed), mix music/narration/sfx and remux over
        the finished video. Two behaviors, chosen automatically:
          • narration stem present → that IS the voice: master = narration+music+sfx,
            REPLACING the base-clip audio (matches audio.path semantics).
          • no narration stem → the voice lives in the base clips (footage): extract the
            assembled audio and LAYER music+sfx over it (music ducks under the VO), so
            the footage audio survives.
        Returns a warning string on a non-fatal failure (mix skipped), else None. The
        video stream is copied (no re-encode → HDR-safe)."""
        audio = edit_decisions.get("audio") or {}
        if not isinstance(audio, dict) or inputs.get("audio_path") or audio.get("path"):
            return None  # a pre-mixed master already owns the output audio
        if not self._has_structured_audio(audio):
            return None
        asset_lookup = {a["id"]: a for a in (asset_manifest or {}).get("assets", [])}
        narration_present = any(
            (s or {}).get("asset_id") for s in (audio.get("narration") or {}).get("segments") or []
        )
        workdir = output_path.parent
        base_audio: Optional[str] = None
        if not narration_present and self._has_audio_stream(output_path):
            base_audio = str(workdir / "base_audio.m4a")
            try:
                self.run_command(["ffmpeg", "-y", "-v", "error", "-i", str(output_path),
                                  "-vn", "-c:a", "aac", "-b:a", "192k", base_audio])
            except Exception:
                base_audio = None
        master = self._mix_structured_audio(audio, asset_lookup, workdir, base_audio_path=base_audio)
        if not master:
            return "structured audio present but no stems resolved — output kept base-clip audio only"
        muxed = workdir / f"{output_path.stem}_amix{output_path.suffix}"
        try:
            self.run_command(["ffmpeg", "-y", "-v", "error", "-i", str(output_path), "-i", master,
                              "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                              "-shortest", str(muxed)])
            muxed.replace(output_path)
        except Exception as exc:
            return f"structured audio mix failed to remux ({exc}); output kept base-clip audio"
        finally:
            for tmp in (base_audio, master):
                try:
                    if tmp:
                        Path(tmp).unlink(missing_ok=True)
                except OSError:
                    pass
        return None

    def _has_audio_stream(self, path: Path) -> bool:
        """True if `path` has at least one audio stream (ffprobe). Best-effort."""
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                 "stream=index", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True,
            )
            return bool(out.stdout.strip())
        except Exception:
            return False

    def _render_via_ffmpeg(
        self,
        *,
        inputs: dict[str, Any],
        edit_decisions: dict[str, Any],
        resolved_cuts: list[dict],
        output_path: Path,
        profile: Optional[str],
        asset_manifest: Optional[dict[str, Any]] = None,
    ) -> ToolResult:
        """Explicit FFmpeg-only render path.

        Use when the proposal locked `render_runtime="ffmpeg"` — e.g. simple
        source-footage concat/trim jobs that don't benefit from composition,
        and Edits-style reels whose motion lives in FFmpeg filter expressions.
        Still runs the mandatory final self-review.

        Two-pass pipeline (overlays are applied here, NOT inside `_compose`):

            edit_decisions.cuts ─▶ _compose ─▶ <base>.mp4   (concat + audio + subtitles)
                                                  │
              edit_decisions.overlays (resolve    ▼
              asset_id→path via asset_manifest) ─▶ _overlay ─▶ output_path
                                                  (keyframes via _keyframe_overlay)

        Historically `_compose` ignored `edit_decisions.overlays[]`, so an
        ffmpeg-runtime render silently dropped every overlay/keyframe (the
        Wave-2 keyframe renderer was only reachable via operation="overlay").
        Applying overlays here fixes that for the agent pipeline AND the manual
        editor. When there are no overlays this collapses to the original
        single _compose call (no behavior change, no extra encode).

        Multi-track (first real step — the Mission Control editor's layer
        dropdown feeds this): cuts with layer='overlay' are lifted OUT of the
        base concat and composited in the overlay pass as timed PiP video
        overlays. Timeline placement comes from cuts[] order: an overlay-layer
        cut starts at the accumulated (xfade-shortened) duration of the
        primary/background cuts listed BEFORE it, and runs (out-in)/speed.
        Sizing/placement come from cuts[].transform — `scale` is a fraction of
        canvas width in (0, 1] (default 0.30), `position` a named anchor
        (default top-right). transform.crop applies in source pixels before
        scaling. PiP cuts composite UNDER edit_decisions.overlays[] graphics.

        Text overlays: overlays[] items with type='text' render via drawtext
        in the same overlay pass (no asset_manifest entry needed).

        Limitations: overlay-layer cut audio is NOT mixed (PiP is visual —
        use overlays[].audio_mix for source audio); overlay-cut timing uses
        the nominal in/out math, so xfade-induced drift on the base timeline
        (probed vs requested durations) can offset PiP windows by frames;
        still images cannot be overlay-layer cuts (use overlays[] instead).
        """
        options = inputs.get("options", {})
        subtitle_burn = options.get("subtitle_burn", True)

        # Bridge the timeline's audio track into compose's audio_path the same way
        # subtitles are bridged below — _compose only muxes inputs["audio_path"], so
        # without this an edit_decisions.audio (e.g. the VO carried onto a
        # proxy-assemble EDL) is silently dropped. An explicit audio_path wins.
        if not inputs.get("audio_path"):
            _ed_audio = edit_decisions.get("audio") or {}
            _ed_audio_path = _ed_audio.get("path") if isinstance(_ed_audio, dict) else None
            if _ed_audio_path and Path(_ed_audio_path).exists():
                inputs = dict(inputs, audio_path=_ed_audio_path)
            # Structured stems (music/narration/sfx, no pre-mixed master) are mixed AFTER
            # the assemble, in _apply_structured_audio_mix — so it can LAYER music/SFX over
            # the base-clip audio (footage VO) or REPLACE it (when a narration stem is the
            # voice). Doing it pre-compose could only replace, which would drop a footage VO.

        subtitle_path = inputs.get("subtitle_path")
        if subtitle_burn and not subtitle_path:
            ed_subs = edit_decisions.get("subtitles", {})
            if ed_subs.get("enabled") and ed_subs.get("source"):
                subtitle_path = ed_subs["source"]

        # Resolve overlay asset_id → file path (mirror the cuts resolution in
        # _render). An overlay whose asset_id isn't in the manifest is passed
        # through as a literal path; _overlay surfaces a clear "not found" error.
        overlays = edit_decisions.get("overlays") or []
        resolved_overlays: list[dict] = []
        if overlays:
            asset_lookup = {a["id"]: a for a in (asset_manifest or {}).get("assets", [])}
            for ov in overlays:
                if self._is_text_overlay(ov):
                    # Text overlays carry their content inline — no asset to resolve.
                    resolved_overlays.append(dict(ov))
                    continue
                resolved = dict(ov)
                aid = ov.get("asset_id", "")
                resolved["asset_path"] = (
                    asset_lookup[aid]["path"] if aid in asset_lookup else aid
                )
                # _overlay reads width/height at the item's top level for scaling,
                # but edit_decisions nests them in `position`. Lift them so overlay
                # sizing is honored (top-level wins if both are somehow present).
                pos = ov.get("position") or {}
                if "width" not in resolved and "width" in pos:
                    resolved["width"] = pos["width"]
                if "height" not in resolved and "height" in pos:
                    resolved["height"] = pos["height"]
                resolved_overlays.append(resolved)

        # --- cuts[].layer routing: overlay-layer cuts leave the base concat
        # and become timed PiP video overlays (composited UNDER overlays[]).
        base_cuts = [
            c for c in resolved_cuts if (c.get("layer") or "primary") != "overlay"
        ]
        layer_overlay_count = len(resolved_cuts) - len(base_cuts)
        pip_temp_dir: Optional[Path] = None
        if layer_overlay_count:
            if not base_cuts:
                return ToolResult(
                    success=False,
                    error=(
                        "all cuts have layer='overlay' — at least one "
                        "primary/background cut is required to form the base timeline"
                    ),
                )
            pip_temp_dir = output_path.parent / ".pip_tmp"
            pip_temp_dir.mkdir(parents=True, exist_ok=True)
            pip_err, pip_entries = self._build_layer_overlay_entries(
                resolved_cuts, base_cuts, edit_decisions, profile, pip_temp_dir,
                inputs.get("codec", "libx264"),
                inputs.get("crf", 23),
                inputs.get("preset", "medium"),
            )
            if pip_err:
                self._cleanup_dir(pip_temp_dir)
                return ToolResult(success=False, error=pip_err)
            resolved_overlays = pip_entries + resolved_overlays

        # When overlays exist, _compose writes a base file and _overlay composites
        # onto it to produce output_path. Otherwise _compose writes output_path
        # directly (unchanged single-pass behavior).
        compose_target = (
            output_path.with_name(f"{output_path.stem}_base{output_path.suffix}")
            if resolved_overlays
            else output_path
        )

        # --- project background (metadata.background) → _compose gate ---
        # This is the ONLY place metadata.background is read. Setting
        # compose_inputs['composite_background'] here (and never reading
        # metadata.background inside _compose) is what keeps the solo-proxy
        # render — which ALSO carries metadata.background — from baking the bg
        # into a proxy (proxy content + cache key stay unchanged). A malformed
        # background degrades to black rather than failing the render.
        composite_background: Optional[dict[str, Any]] = None
        bg = (edit_decisions.get("metadata") or {}).get("background")
        if isinstance(bg, dict):
            bg_type = (bg.get("type") or "").strip().lower()
            if bg_type == "color":
                composite_background = {
                    "color": self._normalize_ffmpeg_color(bg.get("color"), "black"),
                    "image": None,
                }
            elif bg_type == "image":
                bg_lookup = {a["id"]: a for a in (asset_manifest or {}).get("assets", []) if a.get("id")}
                aid = bg.get("asset_id") or ""
                bg_path = bg_lookup[aid]["path"] if aid in bg_lookup else aid
                if bg_path and Path(str(bg_path)).exists():
                    composite_background = {"color": "black", "image": str(bg_path)}
                else:
                    # Missing image → degrade to a BLACK color background (not a hard
                    # fail, and not silently dropping the transform): a declared bg still
                    # means "honor the per-clip transform", just over black.
                    composite_background = {"color": "black", "image": None}

        # HDR: keep the assemble re-encode (concat / subtitle burn / overlays)
        # 10-bit + HLG/PQ tagged. metadata.hdr is the proxy-assemble signal
        # (stamped by _build_assemble_edl); a DIRECT ffmpeg render of raw footage
        # is preserved only when EVERY base cut is HDR (a single color decision is
        # applied to all segments here, so a mixed direct render can't per-cut
        # promote — route mixed HDR/SDR through render_proxies). vf_prefix="" means
        # the inputs are already in the target color space (no conversion).
        assemble_hdr_encode = self._assemble_hdr_encode(edit_decisions, base_cuts, inputs)

        # Surface a silent HDR drop: a DIRECT render (no proxy metadata.hdr) of a
        # mixed HDR/SDR timeline (or with no HDR encoder) can't preserve HDR here —
        # _assemble_hdr_encode returns None and the HDR footage is left SDR. The
        # "never tonemap silently" contract means we must say so (the proxy path
        # already warns; this brings the direct path in line).
        direct_hdr_drop_warning: Optional[str] = None
        if not (assemble_hdr_encode and assemble_hdr_encode.get("encoder")) \
                and not (edit_decisions.get("metadata") or {}).get("hdr"):
            _pol = str(inputs.get("hdr_policy") or "auto").strip().lower()
            if _pol not in ("tonemap", "sdr"):
                from tools.video._shared import is_hdr_source
                _has_hdr = any(
                    c.get("source") and Path(str(c["source"])).exists()
                    and not self._is_image(Path(str(c["source"])))
                    and is_hdr_source(Path(str(c["source"]))).get("hdr")
                    for c in base_cuts
                )
                if _has_hdr:
                    direct_hdr_drop_warning = (
                        "HDR source(s) in this direct ffmpeg render were left SDR — one "
                        "color decision applies to all cuts here, so a mixed HDR/SDR "
                        "timeline (or one with no 10-bit HEVC encoder) can't preserve HDR. "
                        "Render via render_proxies to preserve HDR, or pass hdr_policy."
                    )

        compose_inputs = dict(inputs)
        compose_inputs["edit_decisions"] = dict(edit_decisions, cuts=base_cuts)
        compose_inputs["output_path"] = str(compose_target)
        if composite_background is not None:
            compose_inputs["composite_background"] = composite_background
        if subtitle_path:
            compose_inputs["subtitle_path"] = subtitle_path
        if profile:
            compose_inputs["profile"] = profile
        if assemble_hdr_encode:
            compose_inputs["hdr_encode"] = assemble_hdr_encode

        try:
            render_result = self._compose(compose_inputs)

            if render_result.success and direct_hdr_drop_warning:
                if render_result.data is None:
                    render_result.data = {}
                render_result.data.setdefault("warnings", []).append(direct_hdr_drop_warning)

            # Second pass: composite overlays (incl. keyframed motion) onto the base.
            if render_result.success and resolved_overlays:
                if not compose_target.exists():
                    return ToolResult(
                        success=False,
                        error=f"FFmpeg compose reported success but base file is missing: {compose_target}",
                        data=render_result.data,
                    )
                overlay_result = self._overlay({
                    "input_path": str(compose_target),
                    "overlays": resolved_overlays,
                    "output_path": str(output_path),
                    "hdr_encode": assemble_hdr_encode,
                })
                try:
                    compose_target.unlink()  # drop the intermediate base
                except OSError:
                    pass
                if not overlay_result.success:
                    return ToolResult(
                        success=False,
                        error=f"Overlay pass failed: {overlay_result.error}",
                        data=render_result.data,
                    )
                # Carry overlay warnings (e.g. rotation not supported in FFmpeg).
                if render_result.data is None:
                    render_result.data = {}
                ov_warn = (overlay_result.data or {}).get("warnings")
                if ov_warn:
                    render_result.data.setdefault("warnings", []).extend(ov_warn)
                if layer_overlay_count:
                    render_result.data["layer_overlay_cuts"] = layer_overlay_count
                render_result.artifacts = [str(output_path)]
        finally:
            if pip_temp_dir is not None:
                self._cleanup_dir(pip_temp_dir)

        # Structured-audio stems (no pre-mixed audio.path): mix music/narration/sfx and
        # remux over the finished video (layer over the footage VO, or replace it when a
        # narration stem is the voice). Runs before the review so it sees the final audio.
        if render_result.success and output_path.exists():
            _audio_warn = self._apply_structured_audio_mix(output_path, edit_decisions, asset_manifest, inputs)
            if _audio_warn:
                if render_result.data is None:
                    render_result.data = {}
                render_result.data.setdefault("warnings", []).append(_audio_warn)

        if render_result.success and output_path.exists():
            final_review = self._run_final_review(
                output_path,
                edit_decisions,
                inputs.get("proposal_packet"),
                narration_transcript_path=inputs.get("narration_transcript_path"),
                script_text=inputs.get("script_text") or self._read_text_file(
                    inputs.get("script_path")
                ),
            )
            if render_result.data is None:
                render_result.data = {}
            render_result.data["final_review"] = final_review
            render_result.data["final_review_status"] = final_review["status"]
            if final_review["status"] == "fail":
                return ToolResult(
                    success=False,
                    error=(
                        "Post-render self-review FAILED (FFmpeg). The output is not presentable.\n"
                        + "\n".join(f"  • {i}" for i in final_review.get("issues_found", []))
                    ),
                    data=render_result.data,
                )

        return render_result

    # ---- cuts[].layer routing (multi-track step 1: overlay-layer PiP cuts) ----

    PIP_DEFAULT_SCALE = 0.30   # default PiP width as a fraction of canvas width
    PIP_MARGIN_FRAC = 0.03     # anchor margin as a fraction of canvas width

    @staticmethod
    def _cleanup_dir(d: Path) -> None:
        """Best-effort removal of a flat temp dir and its files."""
        try:
            for f in d.iterdir():
                f.unlink()
            d.rmdir()
        except OSError:
            pass

    _ANCHOR_NAMES = (
        "top-left", "top-center", "top-right",
        "center-left", "center", "center-right",
        "bottom-left", "bottom-center", "bottom-right",
    )

    @classmethod
    def _split_anchor(cls, anchor: Any) -> Optional[tuple[str, str]]:
        """Named anchor → (vertical, horizontal) parts, or None if unrecognized."""
        if not isinstance(anchor, str):
            return None
        if anchor == "center":
            return ("center", "center")
        if anchor not in cls._ANCHOR_NAMES:
            return None
        v, h = anchor.split("-", 1)
        return (v, h)

    @staticmethod
    def _anchor_xy(
        parts: tuple[str, str],
        canvas_w: int, canvas_h: int,
        w: int, h: int,
        margin: int,
    ) -> tuple[int, int]:
        """Pixel x/y of a w×h box placed at a named anchor on the canvas."""
        v, hz = parts
        x = (
            margin if hz == "left"
            else (canvas_w - w) // 2 if hz == "center"
            else canvas_w - w - margin
        )
        y = (
            margin if v == "top"
            else (canvas_h - h) // 2 if v == "center"
            else canvas_h - h - margin
        )
        return x, y

    def _build_layer_overlay_entries(
        self,
        all_cuts: list[dict],
        base_cuts: list[dict],
        edit_decisions: dict[str, Any],
        profile_name: Optional[str],
        temp_dir: Path,
        codec: str,
        crf: int,
        preset: str,
    ) -> tuple[Optional[str], list[dict]]:
        """Trim layer='overlay' cuts into temp segments and synthesize _overlay entries.

        Timeline placement: walking cuts[] in order, an overlay-layer cut starts
        at the visible end-time of the base (primary/background) cuts listed
        before it — accounting for xfade shortening via the same join resolution
        the base concat uses — and spans (out-in)/speed. To PiP over the FIRST
        base cut, list the overlay cut BEFORE it.

        Each entry is a video overlay dict for _overlay: trimmed segment path,
        width = transform.scale (default 0.30) × canvas width (aspect preserved),
        x/y from the transform.position named anchor (default top-right), and
        pts_offset_seconds so the segment's first frame lands exactly at its
        start_seconds. Audio is stripped (-an) — PiP cuts are visual only.

        Validates every overlay cut BEFORE trimming anything. Returns
        (error, entries); temp segments live in temp_dir (caller cleans up).
        """
        canvas_err, target_w, target_h, _fps = self._resolve_canvas(
            edit_decisions, profile_name
        )
        if canvas_err:
            return canvas_err, []
        joins, _ = self._resolve_joins(base_cuts, edit_decisions.get("metadata"))
        margin = int(round(self.PIP_MARGIN_FRAC * target_w))

        # --- pass 1: validate all overlay cuts + compute timeline placement ---
        plans: list[dict] = []  # one per overlay-layer cut
        pos = 0.0               # visible end-time of base cuts walked so far
        base_index = 0
        for idx, cut in enumerate(all_cuts):
            speed = cut.get("speed", 1.0)
            if isinstance(speed, bool) or not isinstance(speed, (int, float)) or speed <= 0:
                return f"cuts[{idx}].speed must be a positive number; got {speed!r}", []
            try:
                in_s = float(cut["in_seconds"])
                out_s = float(cut["out_seconds"])
            except (KeyError, TypeError, ValueError):
                return f"cuts[{idx}] needs numeric in_seconds/out_seconds", []
            span = out_s - in_s
            if span <= 0:
                return f"cuts[{idx}]: out_seconds must exceed in_seconds", []
            dur = span / float(speed)

            if (cut.get("layer") or "primary") != "overlay":
                if base_index == 0:
                    pos = dur
                else:
                    join = joins[base_index - 1]
                    pos += dur - (join["duration"] if join else 0.0)
                base_index += 1
                continue

            source = Path(cut["source"]) if cut.get("source") else None
            if source is None or not source.exists():
                return f"cuts[{idx}] (layer='overlay') source not found: {cut.get('source')!r}", []
            if self._is_image(source):
                return (
                    f"cuts[{idx}]: still image '{source.name}' cannot be a "
                    "layer='overlay' cut — use edit_decisions.overlays[] for "
                    "image overlays",
                    [],
                )

            transform = cut.get("transform") or {}
            pip_scale = transform.get("scale")
            if pip_scale is None:
                pip_scale = self.PIP_DEFAULT_SCALE
            if (
                isinstance(pip_scale, bool)
                or not isinstance(pip_scale, (int, float))
                or not (0 < pip_scale <= 1)
            ):
                return (
                    f"cuts[{idx}].transform.scale for layer='overlay' must be in "
                    f"(0, 1] (fraction of canvas width); got {pip_scale!r}",
                    [],
                )
            anchor = transform.get("position") or "top-right"
            if isinstance(anchor, dict):
                return (
                    f"cuts[{idx}].transform.position for layer='overlay' (PiP) must "
                    f"be a named anchor string, not an {{x,y}} object — got {anchor!r}",
                    [],
                )
            anchor_parts = self._split_anchor(anchor)
            if anchor_parts is None:
                return (
                    f"cuts[{idx}].transform.position {anchor!r} is not a named "
                    f"anchor; expected one of: {', '.join(self._ANCHOR_NAMES)}",
                    [],
                )
            crop = transform.get("crop") or {}
            if crop:
                crop_w, crop_h = crop.get("width"), crop.get("height")
                if (
                    not isinstance(crop_w, (int, float))
                    or not isinstance(crop_h, (int, float))
                    or crop_w <= 0 or crop_h <= 0
                ):
                    return (
                        f"cuts[{idx}].transform.crop requires positive numeric "
                        f"width and height; got {crop!r}",
                        [],
                    )

            plans.append({
                "idx": idx, "source": source, "in_s": in_s, "span": span,
                "speed": float(speed), "start_t": pos, "dur": dur,
                "pip_scale": float(pip_scale), "anchor_parts": anchor_parts,
                "crop": crop,
            })

        # --- pass 2: trim segments + build overlay entries ---
        entries: list[dict] = []
        for plan in plans:
            idx = plan["idx"]
            seg = temp_dir / f"pip_{idx:04d}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(plan["in_s"]),
                "-t", str(plan["span"]),
                "-i", str(plan["source"]),
            ]
            vf_parts: list[str] = []
            crop = plan["crop"]
            if crop:
                crop_x = int(round(crop.get("x", 0) or 0))
                crop_y = int(round(crop.get("y", 0) or 0))
                vf_parts.append(
                    f"crop={int(round(crop['width']))}:{int(round(crop['height']))}:"
                    f"{crop_x}:{crop_y}"
                )
            if plan["speed"] != 1.0:
                vf_parts.append(f"setpts={1.0 / plan['speed']}*PTS")
            if vf_parts:
                cmd.extend(["-filter:v", ",".join(vf_parts)])
            cmd.extend([
                "-an",  # PiP audio is dropped by design (see docstring)
                "-c:v", codec, "-crf", str(crf), "-preset", preset,
                "-pix_fmt", "yuv420p",
                str(seg),
            ])
            try:
                self.run_command(cmd, timeout=600)
            except subprocess.CalledProcessError as e:
                stderr = ((e.stderr or "") or "ffmpeg failed").strip()[-300:]
                return f"layer='overlay' cut {idx} trim failed: {stderr}", []
            except subprocess.TimeoutExpired:
                return f"layer='overlay' cut {idx} trim timed out", []

            dims = self._probe_dimensions(seg)
            if not dims:
                return (
                    f"could not probe dimensions of layer='overlay' segment "
                    f"for cuts[{idx}]",
                    [],
                )
            sw, sh = dims
            pip_w = max(2, int(round(target_w * plan["pip_scale"] / 2)) * 2)
            pip_h = max(2, int(round(pip_w * sh / sw / 2)) * 2)
            x, y = self._anchor_xy(
                plan["anchor_parts"], target_w, target_h, pip_w, pip_h, margin
            )
            start_t = round(plan["start_t"], 4)
            entries.append({
                "asset_path": str(seg),
                "x": x, "y": y, "width": pip_w,
                "start_seconds": start_t,
                "end_seconds": round(plan["start_t"] + plan["dur"], 4),
                "pts_offset_seconds": start_t,
            })
        return None, entries

    def _remotion_render(self, inputs: dict[str, Any]) -> ToolResult:
        """Render via Remotion (requires Node.js + npx).

        Handles compositions with still images, animated scenes, component
        types, and transitions using React-based frame-accurate rendering.
        Accepts edit_decisions (with resolved file paths) or raw composition_data.
        """
        import shutil

        if not shutil.which("npx"):
            return ToolResult(
                success=False,
                error="npx not found. Install Node.js to use Remotion rendering.",
            )

        composition_data = inputs.get("edit_decisions") or inputs.get("composition_data")
        if not composition_data:
            return ToolResult(
                success=False,
                error="edit_decisions or composition_data required for remotion_render",
            )

        output_path = Path(inputs.get("output_path", "renders/remotion_output.mp4"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Absolutise so the CLI can resolve the output regardless of cwd.
        output_path = output_path.resolve()

        # Deep-copy props so we don't mutate the original
        props = json.loads(json.dumps(composition_data))

        # Convert absolute file paths to file:// URIs for Remotion's
        # Img and OffthreadVideo components
        for cut in props.get("cuts", []):
            source = cut.get("source", "")
            if source and not source.startswith(("http://", "https://", "file://")):
                resolved = Path(source).resolve()
                if resolved.exists():
                    posix = resolved.as_posix()
                    cut["source"] = f"file:///{posix}" if not posix.startswith("/") else f"file://{posix}"

        # Build a custom themeConfig from the playbook's actual colors.
        # This ensures every video gets a unique visual identity derived
        # from its production decisions — not picked from a preset menu.
        if "themeConfig" not in props:
            playbook_name = (
                props.get("playbook")
                or props.get("theme")
                or props.get("metadata", {}).get("playbook")
            )
            theme_config = self._build_theme_from_playbook(playbook_name, composition_data)
            if theme_config:
                props["themeConfig"] = theme_config

        # Write props to temp file for Remotion CLI
        props_path = output_path.parent / ".remotion_props.json"
        with open(props_path, "w", encoding="utf-8") as f:
            json.dump(props, f)

        # remotion-composer lives at project root
        composer_dir = Path(__file__).resolve().parent.parent.parent / "remotion-composer"
        if not composer_dir.exists():
            return ToolResult(
                success=False,
                error=f"Remotion composer project not found at {composer_dir}",
            )

        # Route to the correct Remotion composition based on renderer_family.
        # This prevents all pipelines from collapsing into the Explainer visual grammar.
        renderer_family = (composition_data or {}).get("renderer_family", "explainer-data")
        composition_id = self._get_composition_id(renderer_family)

        cmd = [
            "npx", "remotion", "render",
            str(composer_dir / "src" / "index.tsx"),
            composition_id,
            str(output_path),
            "--props", str(props_path),
        ]

        # Apply media profile dimensions
        profile_name = inputs.get("profile")
        if profile_name:
            try:
                from lib.media_profiles import get_profile
                p = get_profile(profile_name)
                cmd.extend(["--width", str(p.width), "--height", str(p.height)])
            except (ImportError, ValueError):
                pass

        try:
            # Invoke from inside the composer dir so npx can resolve the
            # local remotion binary via node_modules/.bin. Without this,
            # Windows npx cannot locate the CLI and returns "could not
            # determine executable to run".
            self.run_command(cmd, timeout=600, cwd=composer_dir)
        except Exception as e:
            return ToolResult(success=False, error=f"Remotion render failed: {e}")
        finally:
            if props_path.exists():
                props_path.unlink()

        if not output_path.exists():
            return ToolResult(
                success=False,
                error=f"Remotion render completed but output file missing: {output_path}",
            )

        return ToolResult(
            success=True,
            data={
                "operation": "remotion_render",
                "output": str(output_path),
                "profile": profile_name,
            },
            artifacts=[str(output_path)],
        )

    # ------------------------------------------------------------------
    # Final self-review — mandatory post-render inspection
    # ------------------------------------------------------------------

    # Punctuation/SSML-leak words that should NEVER appear in rendered audio.
    # When a TTS engine reads a literal "..." as the word "dot", or a "—" as
    # "hyphen", those leak into the transcript. Catching these in the final
    # review is the difference between catching a bad voice render in-tool
    # vs. shipping a video that says "dot dot dot" twelve times. CRITICAL.
    _TTS_PUNCTUATION_LEAK_WORDS = {
        "dot", "dots", "ellipsis", "period", "periods",
        "comma", "commas", "semicolon", "colon",
        "dash", "hyphen", "emdash", "endash",
        "parenthesis", "bracket", "brace",
        "asterisk", "slash", "backslash",
        "exclamation", "question mark",
    }

    @staticmethod
    def _read_text_file(path: str | Path | None) -> str | None:
        """Read a small text file if given a path; None-safe and exception-safe."""
        if not path:
            return None
        try:
            return Path(path).read_text(encoding="utf-8")
        except Exception:
            return None

    @classmethod
    def _tokenize(cls, text: str) -> list[str]:
        """Split text into comparable word tokens (lowercased, punctuation
        stripped, numeric-word-aware). Empty tokens dropped."""
        import re

        # Preserve hyphenated words as single tokens ("many-worlds" -> "many-worlds").
        # Drop everything except letters, digits, hyphens, apostrophes.
        cleaned = re.sub(r"[^A-Za-z0-9\-' ]+", " ", text.lower())
        return [t for t in cleaned.split() if t and t != "-"]

    @classmethod
    def _compare_transcript_to_script(
        cls,
        transcript_path: Path,
        script_text: str,
    ) -> dict[str, Any]:
        """Compare a word-level transcript against the source script.

        Purpose: catch TTS failures that look fine on audio-volume/duration
        checks but produce garbage content. The canonical example is
        Chirp3-HD reading ellipses ("...") literally as the word "dot" — our
        volume check says "narration present, not clipped" and the video
        ships. This check diffs the actual transcribed audio against what
        was supposed to be said, and flags:

        - Spurious punctuation-leak words ("dot", "comma", "hyphen", etc.)
          that appear in audio but not script → CRITICAL
        - Overall word-accuracy ratio against script → SUGGESTION if < 0.9

        Returns the transcript_comparison section of final_review, or a
        placeholder with an issue describing why the check couldn't run
        (missing transcript, missing script) so the review never goes
        silently quiet on this contract.
        """
        result: dict[str, Any] = {
            "transcript_matches_script": False,
            "word_accuracy": None,
            "script_word_count": 0,
            "transcript_word_count": 0,
            "spurious_punctuation_words": [],
            "issues": [],
        }

        if not transcript_path or not Path(transcript_path).is_file():
            result["issues"].append(
                "transcript_comparison skipped: narration_transcript not provided"
            )
            return result
        if not script_text:
            result["issues"].append(
                "transcript_comparison skipped: script_text not provided"
            )
            return result

        try:
            transcript_data = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
        except Exception as e:
            result["issues"].append(f"transcript_comparison could not parse transcript: {e}")
            return result

        transcript_words = [
            w.get("word", "").strip() for w in transcript_data.get("word_timestamps", [])
        ]
        transcript_tokens = cls._tokenize(" ".join(transcript_words))
        script_tokens = cls._tokenize(script_text)

        result["script_word_count"] = len(script_tokens)
        result["transcript_word_count"] = len(transcript_tokens)

        if not script_tokens or not transcript_tokens:
            result["issues"].append(
                f"transcript_comparison: empty token set "
                f"(script={len(script_tokens)}, transcript={len(transcript_tokens)})"
            )
            return result

        # --- Punctuation-leak detection (TTS reading literal punctuation) ---
        script_set = set(script_tokens)
        leak_occurrences: dict[str, int] = {}
        for token in transcript_tokens:
            if token in cls._TTS_PUNCTUATION_LEAK_WORDS and token not in script_set:
                leak_occurrences[token] = leak_occurrences.get(token, 0) + 1

        if leak_occurrences:
            formatted = ", ".join(
                f"{w!r}×{n}" for w, n in sorted(leak_occurrences.items(), key=lambda x: -x[1])
            )
            result["spurious_punctuation_words"] = [
                {"word": w, "count": n} for w, n in leak_occurrences.items()
            ]
            result["issues"].append(
                f"TTS punctuation leak: transcript contains {formatted} — "
                f"these words are NOT in the script, which means the voice "
                f"engine is reading literal punctuation aloud. Rewrite the "
                f"script to eliminate the corresponding characters (ellipses, "
                f"em-dashes, etc.) and regenerate narration."
            )

        # --- Word accuracy via set overlap (cheap & ordering-insensitive) ---
        # We don't penalize small word-order differences or minor TTS
        # hallucinations; we just want to know "did 90%+ of the script's
        # content make it into the audio." Using set overlap on the script
        # side is robust to transcription noise.
        matched = sum(1 for t in script_tokens if t in set(transcript_tokens))
        accuracy = matched / max(1, len(script_tokens))
        result["word_accuracy"] = round(accuracy, 3)
        result["transcript_matches_script"] = accuracy >= 0.9 and not leak_occurrences

        if accuracy < 0.9:
            result["issues"].append(
                f"Low transcript-to-script match: only {accuracy:.0%} of script "
                f"words appear in the transcribed audio ({matched}/"
                f"{len(script_tokens)}). Narration may be truncated, mispronounced, "
                f"or the wrong script was used."
            )

        return result

    def _run_final_review(
        self,
        output_path: Path,
        edit_decisions: dict[str, Any] | None = None,
        proposal_packet: dict[str, Any] | None = None,
        narration_transcript_path: str | Path | None = None,
        script_text: str | None = None,
    ) -> dict[str, Any]:
        """Run post-render self-review and produce a final_review artifact.

        This is the governance contract: the compose runtime MUST inspect
        the actual rendered output before marking the stage complete.
        Never claim a video is ready without a real probe + frame sample.

        When `proposal_packet` is provided, its
        `production_plan.render_runtime` is compared against
        `edit_decisions.render_runtime` so `runtime_swap_detected` can
        actually flip. Without it, we fall back to
        `edit_decisions.metadata.proposal_render_runtime` (which the edit
        director can set explicitly to opt into swap detection).

        Returns a dict conforming to final_review.schema.json.
        """
        log = logging.getLogger("video_compose.final_review")
        issues: list[str] = []

        # --- 1. Technical probe via ffprobe ---
        technical_probe: dict[str, Any] = {
            "valid_container": False,
            "issues": [],
        }
        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(output_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode == 0:
                probe_data = json.loads(proc.stdout)
                fmt = probe_data.get("format", {})
                streams = probe_data.get("streams", [])
                video_stream = next(
                    (s for s in streams if s.get("codec_type") == "video"), {}
                )
                audio_stream = next(
                    (s for s in streams if s.get("codec_type") == "audio"), {}
                )

                duration = float(fmt.get("duration", 0))
                width = int(video_stream.get("width", 0))
                height = int(video_stream.get("height", 0))
                fps_str = video_stream.get("r_frame_rate", "0/1")
                fps = self._parse_probe_fps(fps_str)

                technical_probe = {
                    "valid_container": bool(video_stream),
                    "duration_seconds": round(duration, 2),
                    "resolution": f"{width}x{height}",
                    "fps": fps,
                    "has_audio": bool(audio_stream),
                    "codec": video_stream.get("codec_name", "unknown"),
                    "file_size_bytes": int(fmt.get("size", 0)),
                    "issues": [],
                }

                # Sanity checks
                if duration < 1.0:
                    technical_probe["issues"].append(
                        f"Output is only {duration:.1f}s — suspiciously short"
                    )

                # Check target duration from edit_decisions
                target_dur = None
                if edit_decisions:
                    target_dur = (
                        edit_decisions.get("total_duration_seconds")
                        or edit_decisions.get("metadata", {}).get("target_duration_seconds")
                    )
                if target_dur and target_dur > 0:
                    drift_pct = abs(duration - target_dur) / target_dur
                    if drift_pct > 0.25:
                        technical_probe["issues"].append(
                            f"Duration drift: rendered {duration:.1f}s vs target {target_dur}s "
                            f"({drift_pct:.0%} off). Review pacing or trim."
                        )
                    technical_probe["target_duration"] = target_dur
                    technical_probe["duration_drift_pct"] = round(drift_pct * 100, 1)
                if width < 320 or height < 240:
                    technical_probe["issues"].append(
                        f"Resolution {width}x{height} is very low"
                    )
                if not audio_stream:
                    technical_probe["issues"].append("No audio stream in output")
            else:
                technical_probe["issues"].append(
                    f"ffprobe failed with exit code {proc.returncode}"
                )
        except FileNotFoundError:
            technical_probe["issues"].append("ffprobe not found — cannot validate output")
        except Exception as e:
            technical_probe["issues"].append(f"ffprobe error: {e}")

        issues.extend(technical_probe.get("issues", []))

        # --- 2. Visual spotcheck: sample 4 frames ---
        visual_spotcheck: dict[str, Any] = {
            "frames_sampled": 0,
            "frame_paths": [],
            "black_frames_detected": False,
            "broken_overlays": False,
            "missing_assets": False,
            "unreadable_text": False,
            "issues": [],
        }
        duration = technical_probe.get("duration_seconds", 0)
        if duration > 0 and technical_probe.get("valid_container"):
            try:
                frame_dir = output_path.parent / ".final_review_frames"
                frame_dir.mkdir(parents=True, exist_ok=True)
                # Sample at 10%, 35%, 65%, 90% of duration
                sample_points = [0.10, 0.35, 0.65, 0.90]
                frame_paths = []
                for i, pct in enumerate(sample_points):
                    ts = round(duration * pct, 2)
                    frame_path = frame_dir / f"review_frame_{i}.png"
                    cmd = [
                        "ffmpeg", "-y", "-ss", str(ts),
                        "-i", str(output_path),
                        "-frames:v", "1", "-q:v", "2",
                        str(frame_path),
                    ]
                    subprocess.run(cmd, capture_output=True, timeout=15)
                    if frame_path.exists():
                        frame_paths.append(str(frame_path))

                        # Check for black frames (file size heuristic:
                        # a 1920x1080 PNG of pure black is ~5KB)
                        if frame_path.stat().st_size < 2000:
                            visual_spotcheck["black_frames_detected"] = True

                visual_spotcheck["frames_sampled"] = len(frame_paths)
                visual_spotcheck["frame_paths"] = frame_paths

                if len(frame_paths) < 4:
                    visual_spotcheck["issues"].append(
                        f"Only {len(frame_paths)}/4 frames extracted — some timestamps may be out of range"
                    )
                if visual_spotcheck["black_frames_detected"]:
                    visual_spotcheck["issues"].append(
                        "Black frame detected — possible missing asset or failed render segment"
                    )
            except Exception as e:
                visual_spotcheck["issues"].append(f"Frame sampling error: {e}")

        issues.extend(visual_spotcheck.get("issues", []))

        # --- 3. Audio spotcheck ---
        audio_spotcheck: dict[str, Any] = {
            "narration_present": False,
            "music_present": False,
            "unexpected_silence": False,
            "clipping_detected": False,
            "mix_intelligible": True,
            "issues": [],
        }
        if technical_probe.get("has_audio") and duration > 0:
            try:
                # Use ffmpeg volumedetect to check audio levels
                cmd = [
                    "ffmpeg", "-i", str(output_path),
                    "-af", "volumedetect", "-f", "null", "-",
                ]
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60
                )
                stderr = proc.stderr or ""
                # Parse mean_volume and max_volume
                mean_vol = None
                max_vol = None
                for line in stderr.split("\n"):
                    if "mean_volume:" in line:
                        try:
                            mean_vol = float(line.split("mean_volume:")[1].strip().split()[0])
                        except (ValueError, IndexError):
                            pass
                    if "max_volume:" in line:
                        try:
                            max_vol = float(line.split("max_volume:")[1].strip().split()[0])
                        except (ValueError, IndexError):
                            pass

                if mean_vol is not None:
                    if mean_vol < -60:
                        audio_spotcheck["unexpected_silence"] = True
                        audio_spotcheck["issues"].append(
                            f"Mean volume {mean_vol:.1f} dB — effectively silent"
                        )
                    # Assume narration present if mean volume is reasonable
                    if mean_vol > -40:
                        audio_spotcheck["narration_present"] = True
                    # Assume music present if audio exists (conservative)
                    if mean_vol > -50:
                        audio_spotcheck["music_present"] = True

                if max_vol is not None and max_vol > -0.5:
                    audio_spotcheck["clipping_detected"] = True
                    audio_spotcheck["issues"].append(
                        f"Max volume {max_vol:.1f} dB — possible clipping"
                    )
            except Exception as e:
                audio_spotcheck["issues"].append(f"Audio analysis error: {e}")

        issues.extend(audio_spotcheck.get("issues", []))

        # --- 4. Promise preservation ---
        promise_preservation: dict[str, Any] = {
            "delivery_promise_honored": True,
            "silent_downgrade_detected": False,
            "runtime_swap_detected": False,
            "issues": [],
        }
        if edit_decisions:
            renderer_family = edit_decisions.get("renderer_family", "")
            promise_preservation["renderer_family_used"] = renderer_family

            # Runtime governance — record what actually ran and flag a swap.
            # Three sources of truth, in priority order:
            #   1. proposal_packet.production_plan.render_runtime (authoritative)
            #   2. edit_decisions.metadata.proposal_render_runtime (if edit stage
            #      explicitly copied it to opt into in-tool swap detection)
            #   3. edit_decisions.render_runtime itself (cannot detect a swap in
            #      this case — reviewer does cross-artifact comparison instead)
            render_runtime_edit = (edit_decisions.get("render_runtime") or "").strip().lower()
            if render_runtime_edit:
                promise_preservation["render_runtime_used"] = render_runtime_edit

                _meta = edit_decisions.get("metadata") or {}
                if _meta.get("assemble_of_proxies"):
                    # Render-once / NLE two-phase render: each scene's proxy was
                    # rendered in the LOCKED runtime (meta.proxy_render_runtime),
                    # then the proxies are ASSEMBLED with ffmpeg. The assemble EDL
                    # is always render_runtime='ffmpeg' by construction, so the
                    # naive proposal!=edit check would mis-flag a legitimate
                    # remotion/hyperframes → ffmpeg proxy assemble as a runtime
                    # swap. It is NOT one — skip the swap detection here. (The
                    # editor path already avoids this by carrying no proposal_packet;
                    # this guard covers the pipeline path that DOES pass one.)
                    promise_preservation["runtime_swap_check"] = (
                        "skipped — two-phase proxy assemble "
                        f"(proxy_render_runtime={_meta.get('proxy_render_runtime')}); "
                        "an ffmpeg assemble of proxies is not a runtime swap"
                    )
                else:
                    proposal_runtime: str | None = None
                    runtime_source: str | None = None
                    if proposal_packet:
                        pp_runtime = (
                            (proposal_packet.get("production_plan") or {}).get("render_runtime")
                            or ""
                        ).strip().lower()
                        if pp_runtime:
                            proposal_runtime = pp_runtime
                            runtime_source = "proposal_packet.production_plan.render_runtime"
                    if proposal_runtime is None:
                        md_runtime = (
                            (edit_decisions.get("metadata") or {}).get("proposal_render_runtime")
                            or ""
                        ).strip().lower()
                        if md_runtime:
                            proposal_runtime = md_runtime
                            runtime_source = "edit_decisions.metadata.proposal_render_runtime"

                    if proposal_runtime is None:
                        promise_preservation["runtime_swap_check"] = (
                            "skipped — no proposal_packet or proposal_render_runtime "
                            "metadata provided. Reviewer skill does cross-artifact "
                            "comparison separately."
                        )
                    elif proposal_runtime != render_runtime_edit:
                        promise_preservation["runtime_swap_detected"] = True
                        promise_preservation["runtime_swap_check"] = (
                            f"detected — source: {runtime_source}"
                        )
                        promise_preservation["issues"].append(
                            f"render_runtime changed between proposal ({proposal_runtime}) "
                            f"and compose ({render_runtime_edit}) — this is a contract "
                            f"violation unless a render_runtime_selection decision was logged."
                        )
                    else:
                        promise_preservation["runtime_swap_check"] = (
                            f"ok — proposal and edit agree ({runtime_source})"
                        )

            delivery_data = (
                edit_decisions.get("metadata", {}).get("delivery_promise")
                or edit_decisions.get("delivery_promise")
            )
            if delivery_data:
                try:
                    from lib.delivery_promise import DeliveryPromise
                    promise = DeliveryPromise.from_dict(delivery_data)
                    cuts = edit_decisions.get("cuts", [])
                    result = promise.validate_cuts(cuts)
                    motion_ratio = result.get("motion_ratio", 0)
                    promise_preservation["motion_ratio_actual"] = round(motion_ratio, 3)

                    if not result["valid"]:
                        promise_preservation["delivery_promise_honored"] = False
                        for v in result["violations"]:
                            promise_preservation["issues"].append(v)

                    # Detect silent downgrade: motion-led promise but <50% motion
                    if (delivery_data.get("type") == "motion_led"
                            and motion_ratio < 0.5):
                        promise_preservation["silent_downgrade_detected"] = True
                        promise_preservation["issues"].append(
                            f"Motion-led promise but only {motion_ratio:.0%} motion — "
                            f"silent downgrade to still-led"
                        )
                except Exception as e:
                    promise_preservation["issues"].append(
                        f"Could not validate delivery promise: {e}"
                    )

        issues.extend(promise_preservation.get("issues", []))

        # --- 5. Subtitle check ---
        subtitle_check: dict[str, Any] = {
            "subtitles_expected": False,
            "subtitles_present": False,
            "issues": [],
        }
        if edit_decisions:
            ed_subs = edit_decisions.get("subtitles", {})
            subtitle_check["subtitles_expected"] = bool(ed_subs.get("enabled"))

            # Check if output has subtitle stream
            if technical_probe.get("valid_container"):
                try:
                    cmd = [
                        "ffprobe", "-v", "quiet", "-print_format", "json",
                        "-show_streams", "-select_streams", "s",
                        str(output_path),
                    ]
                    proc = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=15
                    )
                    if proc.returncode == 0:
                        sub_data = json.loads(proc.stdout)
                        sub_streams = sub_data.get("streams", [])
                        subtitle_check["subtitles_present"] = len(sub_streams) > 0

                    # If subtitles were expected but not found as a stream,
                    # they may be burned in (which is fine — not a failure)
                    if (subtitle_check["subtitles_expected"]
                            and not subtitle_check["subtitles_present"]):
                        # Check if subtitle_path was used (burned in)
                        sub_source = ed_subs.get("source")
                        if sub_source and Path(sub_source).exists():
                            # Burned-in subtitles are not detectable as streams
                            subtitle_check["subtitles_present"] = True
                            subtitle_check["coverage_ratio"] = 1.0
                        else:
                            subtitle_check["issues"].append(
                                "Subtitles expected but not found in output and "
                                "no subtitle source file exists for burn-in"
                            )
                except Exception as e:
                    subtitle_check["issues"].append(f"Subtitle check error: {e}")

        issues.extend(subtitle_check.get("issues", []))

        # --- 6. Transcript-vs-script comparison ---
        # Catches content-level TTS failures (the classic "Chirp reads `...`
        # as the word 'dot'" trap) that volume-based audio checks miss.
        # Only runs when caller provides both the transcript and script; when
        # skipped, issues list records that so the silence is visible.
        transcript_comparison = self._compare_transcript_to_script(
            Path(narration_transcript_path) if narration_transcript_path else None,
            script_text,
        )
        issues.extend(transcript_comparison.get("issues", []))

        # --- 7. Determine overall status ---
        critical_issues = [
            i for i in issues
            if any(kw in i.lower() for kw in [
                "silent downgrade", "delivery promise violation",
                "effectively silent", "ffprobe failed", "suspiciously short",
                "tts punctuation leak",  # reading literal punctuation aloud
            ])
        ]

        if critical_issues:
            status = "revise"
            recommended_action = "re_render"
        elif issues:
            status = "pass"
            recommended_action = "present_to_user"
        else:
            status = "pass"
            recommended_action = "present_to_user"

        if not technical_probe.get("valid_container"):
            status = "fail"
            recommended_action = "re_render"

        final_review = {
            "version": "1.0",
            "output_path": str(output_path),
            "status": status,
            "checks": {
                "technical_probe": technical_probe,
                "visual_spotcheck": visual_spotcheck,
                "audio_spotcheck": audio_spotcheck,
                "promise_preservation": promise_preservation,
                "subtitle_check": subtitle_check,
                "transcript_comparison": transcript_comparison,
            },
            "issues_found": issues,
            "recommended_action": recommended_action,
        }

        log.info(
            "Final review: status=%s, issues=%d, action=%s",
            status, len(issues), recommended_action,
        )

        return final_review

    @staticmethod
    def _parse_probe_fps(fps_str: str) -> float:
        """Parse ffprobe fps string like '30/1' or '24000/1001'."""
        try:
            if "/" in fps_str:
                num, den = fps_str.split("/")
                return round(int(num) / max(int(den), 1), 2)
            return float(fps_str)
        except (ValueError, ZeroDivisionError):
            return 0.0

    def _burn_subtitles(self, inputs: dict[str, Any]) -> ToolResult:
        """Burn subtitle file into video."""
        input_path = Path(inputs["input_path"])
        subtitle_path = Path(inputs["subtitle_path"])
        output_path = Path(inputs.get("output_path", str(input_path.with_stem(f"{input_path.stem}_subtitled"))))

        if not input_path.exists():
            return ToolResult(success=False, error=f"Input not found: {input_path}")
        if not subtitle_path.exists():
            return ToolResult(success=False, error=f"Subtitle file not found: {subtitle_path}")

        style = inputs.get("subtitle_style", {})
        ass_style = self._build_subtitle_style(style)
        sub_escaped = str(subtitle_path.resolve()).replace("\\", "/").replace(":", "\\:")
        codec = inputs.get("codec", "libx264")
        crf = inputs.get("crf", 23)

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", f"subtitles='{sub_escaped}':force_style='{ass_style}'",
            "-c:v", codec, "-crf", str(crf),
            "-c:a", "copy",
            str(output_path),
        ]

        self.run_command(cmd)

        return ToolResult(
            success=True,
            data={
                "operation": "burn_subtitles",
                "output": str(output_path),
            },
            artifacts=[str(output_path)],
        )

    # Still-image overlay formats. .gif is deliberately ABSENT: GIFs go through the
    # gif demuxer (video-like timeline) — image2's `-loop 1` is invalid for it and
    # classifying GIFs as stills froze them after one play.
    _STILL_OVERLAY_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

    def _overlay(self, inputs: dict[str, Any]) -> ToolResult:
        """Composite overlay images/videos/GIFs/TEXT on top of a base video.

        Text overlays (type='text' or a `text` field with no asset): rendered
        via drawtext in the same pass — `text`, `font_size`, `color`, optional
        `box` {color, opacity, padding}, `font_path` (default: bold sans from
        the text_card_gen system candidates), position as {x,y}/flat x/y or a
        named anchor (e.g. 'bottom-center'; default bottom-center), and
        keyframes for x/y/opacity (scale/rotation keyframes are warned and
        ignored — drawtext has no per-frame scale). Special characters in the
        text (colons, quotes, commas, %) are escaped for both filtergraph
        parser layers; %{...} expansion is disabled (expansion=none).

        Per-overlay support on the FFmpeg path:
          - position: static x/y, or keyframed via time-varying overlay expressions
          - scale: static width/height — either may be omitted and the other
            dimension is derived aspect-preserving (-2); scale keyframes render
            center-anchored via scale=eval=frame expressions
          - opacity: static overlays[].opacity via colorchannelmixer (exact);
            keyframed 0→1/1→0 fades stay on the exact fade-filter fast path; any
            other piecewise/non-monotonic curve renders via a geq alpha expression
            (per-pixel eval — correct but slower, fine for overlay-sized assets)
          - easing: non-linear easings (ease-in/out/in-out, spring, step) are
            approximated by subdividing each keyframe interval into
            EASING_SUBDIVISIONS piecewise-linear sub-segments sampled from the
            easing curve; linear stays exact
          - audio_mix: overlays[].audio_mix {enabled, volume 0..2} mixes the
            overlay source's audio into the base track (trimmed to the overlay
            window, adelay to start_seconds, amix duration=first so the base
            length wins; normalize=0 so volume math is predictable)
          - GIFs: rendered with the gif demuxer's -ignore_loop 0 so they animate
            and loop for the whole window, bounded by -t end_seconds

        Limitations: rotation keyframes are NOT rendered (warned + dropped) — use
        Remotion/HyperFrames for rotation. Overlay-stream time is assumed to equal
        project time (exact for looped stills/GIFs and overlays starting at t=0);
        a video overlay with start_seconds>0 is sampled at `start` seconds into
        the source UNLESS pts_offset_seconds shifts it — set
        pts_offset_seconds=start_seconds to play a video overlay from its first
        frame inside the window (this is how layer='overlay' PiP cuts render).
        Mixed audio still plays from the source's beginning.
        """
        input_path = Path(inputs["input_path"])
        overlays = inputs.get("overlays", [])
        output_path = Path(inputs.get("output_path", str(input_path.with_stem(f"{input_path.stem}_overlay"))))
        codec = inputs.get("codec", "libx264")
        crf = inputs.get("crf", 23)

        # HDR base: keep the OUTPUT 10-bit + HLG/PQ tagged so the overlay pass
        # doesn't silently drop the base's HDR. OPAQUE graphic overlays are lifted
        # into the HDR color space before compositing; ALPHA overlays (transparent
        # .mov/.png) and drawtext can't be safely zscale-converted without dropping
        # alpha, so they're composited as-is with a warning (their color is
        # approximate over HDR — the documented visual-review boundary).
        hdr_encode = inputs.get("hdr_encode") or None
        hdr_promote_vf = ""
        # The `overlay` filter defaults to 8-bit (format=yuv420), which would crush
        # the 10-bit HDR base at every composite. Tell it to composite in 10-bit so
        # the HDR base keeps its bit depth. Empty for SDR (unchanged).
        ov_hdr_fmt = ":format=yuv420p10" if (hdr_encode and hdr_encode.get("encoder")) else ""
        if hdr_encode and hdr_encode.get("encoder"):
            hdr_promote_vf = self._promote_sdr_to_hdr_vf({
                "kind": "pq" if (hdr_encode.get("trc") == "smpte2084") else "hlg",
                "primaries": hdr_encode.get("primaries"),
                "trc": hdr_encode.get("trc"),
                "colorspace": hdr_encode.get("colorspace"),
            })

        if not input_path.exists():
            return ToolResult(success=False, error=f"Input not found: {input_path}")
        if not overlays:
            return ToolResult(success=False, error="No overlays provided")

        # Z-order: composite in ASCENDING `track` so a higher track lands on top
        # (rendered later in the filter chain). Python's sort is stable, so overlays
        # sharing a track keep their array order, and the default track=0 makes this a
        # no-op for legacy docs (array-order z-stacking preserved). PiP entries lifted
        # from cuts[].layer='overlay' have no track → 0, so they stay under track-0
        # overlays[], unchanged from before.
        overlays = sorted(overlays, key=lambda o: int((o or {}).get("track") or 0))

        # Build complex filter for each overlay
        input_args = ["-i", str(input_path)]
        filter_parts = []
        prev_label = "0:v"
        warnings: list[str] = []
        n_inputs = 1  # running ffmpeg input index (base is 0)
        mix_requests: list[tuple[int, float, float, Optional[float]]] = []

        for i, ov in enumerate(overlays):
            out_label = f"v{i}"

            if self._is_text_overlay(ov):
                dt_err, dt_filter, dt_warnings = self._build_drawtext_filter(
                    ov, i, prev_label, out_label
                )
                if dt_err:
                    return ToolResult(success=False, error=dt_err)
                filter_parts.append(dt_filter)
                warnings.extend(dt_warnings)
                if hdr_promote_vf:
                    warnings.append(
                        f"overlays[{i}]: drawtext rendered onto an HDR base — text "
                        "color is interpreted in BT.2020 and may shift slightly "
                        "(white/black are fine). Review on real HDR footage."
                    )
                prev_label = out_label
                continue

            asset_path = Path(ov["asset_path"])
            if not asset_path.exists():
                return ToolResult(success=False, error=f"Overlay asset not found: {asset_path}")

            # base position supports both the flat (x/y) and edit_decisions (position{}) shapes
            pos = ov.get("position") or {}
            if isinstance(pos, str):
                return ToolResult(
                    success=False,
                    error=(
                        f"overlays[{i}]: named-anchor position {pos!r} is only "
                        "supported for text overlays; image/video overlays need "
                        "{x, y} (or flat x/y)"
                    ),
                )
            base_x = int(ov.get("x", pos.get("x", 0)))
            base_y = int(ov.get("y", pos.get("y", 0)))
            start = float(ov.get("start_seconds", 0) or 0)
            end = ov.get("end_seconds")
            keyframes = ov.get("keyframes")

            # --- validate per-overlay fields before any work ---
            w_in, h_in = ov.get("width"), ov.get("height")
            for dim_name, dim_val in (("width", w_in), ("height", h_in)):
                if dim_val is not None and (
                    not isinstance(dim_val, (int, float)) or dim_val <= 0
                ):
                    return ToolResult(
                        success=False,
                        error=f"overlays[{i}].{dim_name} must be a positive number; got {dim_val!r}",
                    )
            static_opacity = ov.get("opacity")
            if static_opacity is not None:
                if not isinstance(static_opacity, (int, float)) or not (0.0 <= static_opacity <= 1.0):
                    return ToolResult(
                        success=False,
                        error=f"overlays[{i}].opacity must be a number in [0, 1]; got {static_opacity!r}",
                    )
                static_opacity = float(static_opacity)
            audio_mix = ov.get("audio_mix") or {}
            if audio_mix.get("enabled"):
                mix_volume = audio_mix.get("volume", 1.0)
                if not isinstance(mix_volume, (int, float)) or not (0.0 <= mix_volume <= 2.0):
                    return ToolResult(
                        success=False,
                        error=f"overlays[{i}].audio_mix.volume must be a number in [0, 2]; got {mix_volume!r}",
                    )
            pts_offset = ov.get("pts_offset_seconds")
            if pts_offset is not None:
                if (
                    isinstance(pts_offset, bool)
                    or not isinstance(pts_offset, (int, float))
                    or pts_offset < 0
                ):
                    return ToolResult(
                        success=False,
                        error=f"overlays[{i}].pts_offset_seconds must be a non-negative number; got {pts_offset!r}",
                    )

            suffix = asset_path.suffix.lower()
            is_gif = suffix == ".gif"
            is_still = suffix in self._STILL_OVERLAY_EXTENSIONS

            if is_gif:
                # gif demuxer: image2's `-loop 1` is invalid here; -ignore_loop 0
                # honors the GIF's own loop count (usually infinite) so it keeps
                # animating across the window. The input MUST be bounded with -t:
                # framesync repeats the ended base against an infinite secondary,
                # so an unbounded looping GIF makes ffmpeg encode forever
                # (live-verified). Fall back to the probed base duration.
                gif_bound = end if end else self._probe_duration_seconds(input_path)
                if not gif_bound:
                    return ToolResult(
                        success=False,
                        error=f"overlays[{i}]: GIF overlay needs end_seconds (or a "
                              "probeable base duration) to bound its looping input",
                    )
                input_args.extend(
                    ["-ignore_loop", "0", "-t", str(gif_bound), "-i", str(asset_path)]
                )
            elif keyframes and is_still and end:
                # A keyframed STILL image needs a timeline for fade/scale expressions
                # to ramp across — a single frame would be captured at the first
                # evaluation and held. Loop the still to its on-screen window.
                input_args.extend(["-loop", "1", "-t", str(end), "-i", str(asset_path)])
            else:
                if keyframes and is_still and not end:
                    warnings.append(
                        f"overlays[{i}]: keyframed still image has no end_seconds — "
                        "the single frame is evaluated once, so motion/fade/scale "
                        "keyframes will not animate. Set end_seconds."
                    )
                input_args.extend(["-i", str(asset_path)])
            ov_idx = n_inputs
            n_inputs += 1
            overlay_input = f"{ov_idx}:v"

            if audio_mix.get("enabled"):
                if not is_still and self._has_audio_stream(asset_path):
                    mix_requests.append((
                        ov_idx,
                        float(audio_mix.get("volume", 1.0)),
                        start,
                        float(end) if isinstance(end, (int, float)) else None,
                    ))
                else:
                    warnings.append(
                        f"overlays[{i}].audio_mix enabled but '{asset_path.name}' "
                        "has no audio stream — skipped."
                    )

            # --- static sizing (aspect-preserving when one dimension is omitted) ---
            pre_chain: list[str] = []
            if hdr_promote_vf:
                # Lift an OPAQUE graphic into the HDR color space before it's
                # composited on the 10-bit base. zscale can't carry an alpha plane,
                # so transparent assets composite as-is (color approximate, warned).
                if self._has_alpha(asset_path):
                    warnings.append(
                        f"overlays[{i}]: transparent overlay '{asset_path.name}' "
                        "composited onto an HDR base without BT.2020 color conversion "
                        "(alpha can't be zscale'd) — review its color, or set "
                        "hdr_policy='tonemap' if it looks wrong."
                    )
                else:
                    pre_chain.append(hdr_promote_vf)
            if pts_offset:
                # Shift the overlay stream so its first frame lands at
                # pts_offset_seconds on the project timeline (delayed video
                # overlays / layer='overlay' PiP cuts). Must precede scaling.
                pre_chain.append(f"setpts=PTS-STARTPTS+{pts_offset}/TB")
            nat_w: Optional[int] = None
            nat_h: Optional[int] = None
            has_scale_kf = bool(keyframes) and bool(self._kf_points(keyframes, "scale"))
            if w_in is not None and h_in is not None:
                nat_w, nat_h = int(w_in), int(h_in)
                pre_chain.append(f"scale={nat_w}:{nat_h}")
            elif w_in is not None or h_in is not None:
                if w_in is not None:
                    pre_chain.append(f"scale={int(w_in)}:-2")
                else:
                    pre_chain.append(f"scale=-2:{int(h_in)}")
                if has_scale_kf:
                    src_dims = self._probe_dimensions(asset_path)
                    if src_dims:
                        sw, sh = src_dims
                        if w_in is not None:
                            nat_w = int(w_in)
                            nat_h = max(2, int(round(nat_w * sh / sw / 2)) * 2)
                        else:
                            nat_h = int(h_in)
                            nat_w = max(2, int(round(nat_h * sw / sh / 2)) * 2)
            elif has_scale_kf:
                src_dims = self._probe_dimensions(asset_path)
                if src_dims:
                    nat_w, nat_h = src_dims

            enable = f"between(t,{start},{end})" if end else f"gte(t,{start})"

            if keyframes:
                # --- keyframed (Edits-style) motion via time-varying expressions ---
                kfw = self._keyframe_overlay(
                    keyframes, base_x, base_y, start, i, overlay_input, prev_label,
                    out_label, enable,
                    pre_chain=pre_chain, nat_w=nat_w, nat_h=nat_h,
                    static_opacity=static_opacity, ov_format=ov_hdr_fmt,
                )
                filter_parts.extend(kfw["filters"])
                warnings.extend(kfw["warnings"])
            else:
                # --- static overlay ---
                if static_opacity is not None and static_opacity < 1.0:
                    pre_chain.append(f"format=rgba,colorchannelmixer=aa={static_opacity}")
                if pre_chain:
                    lbl = f"ovp_{i}"
                    filter_parts.append(f"[{overlay_input}]{','.join(pre_chain)}[{lbl}]")
                    overlay_input = lbl
                filter_parts.append(
                    f"[{prev_label}][{overlay_input}]overlay={base_x}:{base_y}:enable='{enable}'{ov_hdr_fmt}[{out_label}]"
                )
            prev_label = out_label

        # --- overlay audio mixing (overlays[].audio_mix) ---
        audio_out_label: Optional[str] = None
        if mix_requests:
            anchor = "0:a"
            if not self._has_audio_stream(input_path):
                # No base audio: anchor the mix with silence the length of the base
                # so amix duration=first still pins the output to the base duration.
                base_dur = self._probe_duration_seconds(input_path)
                if base_dur is None:
                    return ToolResult(
                        success=False,
                        error="audio_mix requested but the base has no audio stream "
                              "and its duration could not be probed for a silent anchor track",
                    )
                input_args.extend([
                    "-f", "lavfi", "-t", str(base_dur),
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                ])
                anchor = f"{n_inputs}:a"
                n_inputs += 1
            mix_labels = [anchor]
            for j, (idx, vol, m_start, m_end) in enumerate(mix_requests):
                parts: list[str] = []
                if m_end is not None and m_end > m_start:
                    parts.append(f"atrim=duration={round(m_end - m_start, 4)}")
                parts.append(f"volume={vol}")
                if m_start > 0:
                    parts.append(f"adelay={int(round(m_start * 1000))}:all=1")
                lbl = f"aov{j}"
                filter_parts.append(f"[{idx}:a]{','.join(parts)}[{lbl}]")
                mix_labels.append(lbl)
            audio_out_label = "aout"
            filter_parts.append(
                "".join(f"[{lbl}]" for lbl in mix_labels)
                + f"amix=inputs={len(mix_labels)}:duration=first:normalize=0[{audio_out_label}]"
            )

        filter_complex = ";".join(filter_parts)

        # Video tail: HDR keeps 10-bit + HLG/PQ tags (no -r — base fps is kept);
        # SDR is the exact legacy ["-c:v", codec, "-crf", crf] tail.
        video_tail = (
            self._video_output_args(hdr_encode, codec, crf, "medium", None)
            if hdr_encode else ["-c:v", codec, "-crf", str(crf)]
        )

        cmd = ["ffmpeg", "-y"]
        cmd.extend(input_args)
        cmd.extend(["-filter_complex", filter_complex])
        if audio_out_label:
            cmd.extend(["-map", f"[{prev_label}]", "-map", f"[{audio_out_label}]"])
            cmd.extend([*video_tail, "-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.extend(["-map", f"[{prev_label}]", "-map", "0:a?"])
            cmd.extend([*video_tail, "-c:a", "copy"])
        cmd.append(str(output_path))

        try:
            self.run_command(cmd, timeout=1800)
        except subprocess.CalledProcessError as e:
            stderr = ((e.stderr or "") or "ffmpeg overlay failed").strip()[-500:]
            return ToolResult(success=False, error=f"ffmpeg overlay failed: {stderr}")
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error="ffmpeg overlay timed out")

        data = {
            "operation": "overlay",
            "overlay_count": len(overlays),
            "audio_mixed_count": len(mix_requests),
            "output": str(output_path),
        }
        if warnings:
            data["warnings"] = warnings
        return ToolResult(success=True, data=data, artifacts=[str(output_path)])

    # ---- text overlays (FFmpeg drawtext; Edits-parity stage 3) ----

    TEXT_FONT_SIZE_DEFAULT = 48
    TEXT_COLOR_DEFAULT = "white"
    TEXT_ANCHOR_DEFAULT = "bottom-center"
    TEXT_BOX_COLOR_DEFAULT = "black"
    TEXT_BOX_OPACITY_DEFAULT = 0.5
    TEXT_BOX_PADDING_DEFAULT = 10
    # 5% canvas margin for named anchors; expressions use drawtext variables
    # (w/h = canvas, text_w/text_h = rendered text box) so they hold on any canvas.
    _TEXT_ANCHOR_X = {
        "left": "w*0.05",
        "center": "(w-text_w)/2",
        "right": "w*0.95-text_w",
    }
    _TEXT_ANCHOR_Y = {
        "top": "h*0.05",
        "center": "(h-text_h)/2",
        "bottom": "h*0.95-text_h",
    }

    # ffmpeg colors are names, #RRGGBB, or 0xRRGGBB[AA], optionally with @alpha.
    # Restricting the charset also blocks filtergraph option injection
    # (e.g. color="red:box=1").
    _FF_COLOR_RE = re.compile(r"^[A-Za-z0-9#@.]+$")

    # Mirrors tools/graphics/text_card_gen._FONT_CANDIDATES (paths only) for
    # when that module isn't importable; keep the two lists in sync so drawtext
    # and Pillow text cards resolve the same faces.
    _FALLBACK_FONT_CANDIDATES = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
    )

    @staticmethod
    def _is_text_overlay(ov: Any) -> bool:
        """True when an overlays[] item should render via drawtext."""
        if not isinstance(ov, dict):
            return False
        if ov.get("type") == "text":
            return True
        return (
            isinstance(ov.get("text"), str)
            and not ov.get("asset_path")
            and not ov.get("asset_id")
        )

    @staticmethod
    def _escape_drawtext_value(value: str) -> str:
        """Escape a drawtext option value for BOTH filtergraph parser layers.

        The filter_complex string is parsed twice: the graph parser (special:
        ``\\ ' [ ] , ;``) runs first and consumes one escape level, then the
        filter-option parser (special: ``\\ ' :``) consumes another. Characters
        special to both layers therefore need two escape levels. ``%`` is NOT
        escaped here — text overlays always set expansion=none, so drawtext
        never interprets %{...} sequences.
        """
        out: list[str] = []
        for ch in value:
            if ch == "\\":
                out.append("\\\\\\\\")   # both layers: \ -> \\ -> \\\\
            elif ch == "'":
                out.append("\\\\\\'")    # both layers: ' -> \' -> \\\'
            elif ch == ":":
                out.append("\\\\:")      # option layer only (graph passes \: through \\:)
            elif ch in ",;[]":
                out.append("\\" + ch)    # graph layer only
            else:
                out.append(ch)
        return "".join(out)

    def _resolve_drawtext_font(
        self, font_path: Any, idx: int
    ) -> tuple[Optional[str], Optional[str]]:
        """(font_file, error): explicit font_path > text_card_gen candidates > error.

        Reuses the system-font candidate list from text_card_gen so drawtext and
        Pillow cards pick the same face. For .ttc collections drawtext loads
        face 0 (no bold-face scan — pass font_path for exact typography).
        """
        if font_path is not None:
            if not isinstance(font_path, str) or not Path(font_path).is_file():
                return None, f"overlays[{idx}].font_path not found: {font_path!r}"
            return font_path, None
        try:
            from tools.graphics.text_card_gen import _FONT_CANDIDATES
            candidates = [p for p, _ in _FONT_CANDIDATES]
        except Exception:
            candidates = list(self._FALLBACK_FONT_CANDIDATES)
        for cand in candidates:
            if Path(cand).is_file():
                return cand, None
        return None, (
            f"overlays[{idx}]: no usable font found in system font dirs — "
            "pass font_path (.ttf/.ttc) explicitly"
        )

    @classmethod
    def _validate_ff_color(cls, value: Any, label: str) -> Optional[str]:
        """Error string unless `value` looks like a safe ffmpeg color token."""
        if not isinstance(value, str) or not value or not cls._FF_COLOR_RE.match(value):
            return (
                f"{label} must be an ffmpeg color (name, #RRGGBB, or "
                f"0xRRGGBB[AA]); got {value!r}"
            )
        return None

    def _build_drawtext_filter(
        self, ov: dict, i: int, prev_label: str, out_label: str
    ) -> tuple[Optional[str], Optional[str], list[str]]:
        """Build one drawtext filter for a text overlay.

        Returns (error, filter, warnings); error is None on success. Position
        precedence: keyframed x/y > position {x,y} > flat x/y > named anchor
        (default bottom-center). Opacity: keyframed opacity renders via a
        time-varying alpha expression (multiplied under any static
        overlays[].opacity); drawtext evaluates x/y/alpha per frame, so the
        same piecewise-linear + eased-points machinery as image overlays
        applies. Scale/rotation keyframes are warned and ignored (drawtext
        cannot animate fontsize per frame portably).
        """
        warnings: list[str] = []

        text = ov.get("text")
        if not isinstance(text, str) or not text.strip():
            return (
                f"overlays[{i}]: text overlay requires a non-empty 'text' string",
                None, [],
            )

        font_size = ov.get("font_size", self.TEXT_FONT_SIZE_DEFAULT)
        if isinstance(font_size, bool) or not isinstance(font_size, int) or font_size <= 0:
            return (
                f"overlays[{i}].font_size must be a positive integer; got {font_size!r}",
                None, [],
            )

        color = ov.get("color", self.TEXT_COLOR_DEFAULT)
        color_err = self._validate_ff_color(color, f"overlays[{i}].color")
        if color_err:
            return color_err, None, []

        static_opacity = ov.get("opacity")
        if static_opacity is not None:
            if (
                isinstance(static_opacity, bool)
                or not isinstance(static_opacity, (int, float))
                or not (0.0 <= static_opacity <= 1.0)
            ):
                return (
                    f"overlays[{i}].opacity must be a number in [0, 1]; got {static_opacity!r}",
                    None, [],
                )
            static_opacity = float(static_opacity)

        font_file, font_err = self._resolve_drawtext_font(ov.get("font_path"), i)
        if font_err:
            return font_err, None, []

        # --- box (optional) ---
        box_parts: list[str] = []
        box = ov.get("box")
        if box is not None:
            if not isinstance(box, dict):
                return f"overlays[{i}].box must be an object; got {box!r}", None, []
            box_color = box.get("color", self.TEXT_BOX_COLOR_DEFAULT)
            box_color_err = self._validate_ff_color(box_color, f"overlays[{i}].box.color")
            if box_color_err:
                return box_color_err, None, []
            box_opacity = box.get("opacity", self.TEXT_BOX_OPACITY_DEFAULT)
            if (
                isinstance(box_opacity, bool)
                or not isinstance(box_opacity, (int, float))
                or not (0.0 <= box_opacity <= 1.0)
            ):
                return (
                    f"overlays[{i}].box.opacity must be a number in [0, 1]; got {box_opacity!r}",
                    None, [],
                )
            padding = box.get("padding", self.TEXT_BOX_PADDING_DEFAULT)
            if isinstance(padding, bool) or not isinstance(padding, int) or padding < 0:
                return (
                    f"overlays[{i}].box.padding must be a non-negative integer; got {padding!r}",
                    None, [],
                )
            box_parts = [
                "box=1",
                f"boxcolor={box_color}@{float(box_opacity):g}",
                f"boxborderw={padding}",
            ]

        # --- timing ---
        start = float(ov.get("start_seconds", 0) or 0)
        end = ov.get("end_seconds")
        if end is not None and (
            isinstance(end, bool) or not isinstance(end, (int, float))
        ):
            return f"overlays[{i}].end_seconds must be a number; got {end!r}", None, []
        enable = f"between(t,{start},{end})" if end else f"gte(t,{start})"

        # --- position: keyframes > {x,y} > flat x/y > named anchor ---
        keyframes = ov.get("keyframes")
        if keyframes:
            for dim in ("scale", "rotation"):
                if any(isinstance(k, dict) and dim in k for k in keyframes):
                    warnings.append(
                        f"overlays[{i}]: {dim} keyframes are not rendered for text "
                        "overlays (drawtext animates position/opacity only); ignored."
                    )
        pos = ov.get("position")
        anchor = None
        pos_x = ov.get("x")
        pos_y = ov.get("y")
        if isinstance(pos, str):
            anchor = pos
        elif isinstance(pos, dict):
            pos_x = pos.get("x", pos_x)
            pos_y = pos.get("y", pos_y)
        elif pos is not None:
            return (
                f"overlays[{i}].position must be a named anchor or an object "
                f"with x/y; got {pos!r}",
                None, [],
            )
        for label, v in ((f"overlays[{i}].x", pos_x), (f"overlays[{i}].y", pos_y)):
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                return f"{label} must be a number; got {v!r}", None, []
        if anchor is None and pos_x is None and pos_y is None:
            anchor = self.TEXT_ANCHOR_DEFAULT
        anchor_x_expr = anchor_y_expr = None
        if anchor is not None:
            parts_split = self._split_anchor(anchor)
            if parts_split is None:
                return (
                    f"overlays[{i}].position {anchor!r} is not a named anchor; "
                    f"expected one of: {', '.join(self._ANCHOR_NAMES)}",
                    None, [],
                )
            v_part, h_part = parts_split
            anchor_x_expr = self._TEXT_ANCHOR_X[h_part]
            anchor_y_expr = self._TEXT_ANCHOR_Y[v_part]

        kf_x = self._eased_points(keyframes, "x") if keyframes else []
        kf_y = self._eased_points(keyframes, "y") if keyframes else []
        if kf_x:
            x_expr = self._piecewise_linear_expr(kf_x)
        elif pos_x is not None:
            x_expr = f"{pos_x:g}"
        else:
            x_expr = anchor_x_expr or self._TEXT_ANCHOR_X["center"]
        if kf_y:
            y_expr = self._piecewise_linear_expr(kf_y)
        elif pos_y is not None:
            y_expr = f"{pos_y:g}"
        else:
            y_expr = anchor_y_expr or self._TEXT_ANCHOR_Y["bottom"]

        # --- opacity → alpha (drawtext evaluates alpha per frame) ---
        alpha_expr = None
        kf_opacity = self._eased_points(keyframes, "opacity") if keyframes else []
        if kf_opacity:
            alpha_expr = f"clip({self._piecewise_linear_expr(kf_opacity)},0,1)"
            if static_opacity is not None and static_opacity < 1.0:
                alpha_expr = f"{static_opacity:g}*{alpha_expr}"
        elif static_opacity is not None and static_opacity < 1.0:
            alpha_expr = f"{static_opacity:g}"

        parts = [
            f"fontfile={self._escape_drawtext_value(font_file)}",
            f"text={self._escape_drawtext_value(text)}",
            "expansion=none",
            f"fontsize={font_size}",
            f"fontcolor={color}",
            f"x='{x_expr}'",
            f"y='{y_expr}'",
        ]
        if alpha_expr:
            parts.append(f"alpha='{alpha_expr}'")
        parts.extend(box_parts)
        parts.append(f"enable='{enable}'")
        return (
            None,
            f"[{prev_label}]drawtext={':'.join(parts)}[{out_label}]",
            warnings,
        )

    # ---- keyframe rendering (FFmpeg path; Edits-parity Wave 2 renderer) ----

    @staticmethod
    def _kf_points(keyframes: list, dim: str) -> list:
        """(t, value) pairs for one dimension, from keyframes that specify it (sorted by t)."""
        pts = [
            (float(k["t"]), float(k[dim]))
            for k in keyframes
            if isinstance(k, dict) and "t" in k and dim in k and k[dim] is not None
        ]
        return sorted(pts, key=lambda p: p[0])

    @staticmethod
    def _piecewise_linear_expr(pts: list, var: str = "t") -> str:
        """FFmpeg expr: linear interpolation between keyframes, constant-held outside the ends.

        `var` is the time variable name — "t" for overlay/scale expressions,
        "T" for geq (which exposes frame time as T).
        """
        if len(pts) == 1:
            return f"{pts[0][1]}"
        expr = f"{pts[-1][1]}"  # after the last keyframe: hold the final value
        for i in range(len(pts) - 2, -1, -1):
            t0, v0 = pts[i]
            t1, v1 = pts[i + 1]
            if t1 == t0:
                seg = f"{v1}"
            else:
                seg = f"({v0}+({v1}-{v0})*({var}-{t0})/({t1}-{t0}))"
            expr = f"if(lt({var},{t1}),{seg},{expr})"
        t0, v0 = pts[0]
        return f"if(lt({var},{t0}),{v0},{expr})"  # before the first keyframe: hold the initial value

    # Non-linear easings are approximated as piecewise-linear: each eased keyframe
    # interval is subdivided into this many sub-segments sampled from the easing
    # curve before _piecewise_linear_expr. Linear intervals stay endpoint-exact.
    EASING_SUBDIVISIONS = 8

    @staticmethod
    def _ease_progress(name: str, u: float) -> float:
        """Eased progress e(u) for u in [0,1]; e(0)=0 and e(1)=1 are exact by construction."""
        if name == "ease-in":
            return u * u
        if name == "ease-out":
            return 1.0 - (1.0 - u) ** 2
        if name == "ease-in-out":
            return u * u * (3.0 - 2.0 * u)  # smoothstep
        if name == "spring":
            # damped sine: ~9% overshoot around u≈0.25, settled by u=1
            return 1.0 - math.exp(-6.0 * u) * math.cos(8.0 * u)
        return u  # linear

    @classmethod
    def _eased_points(cls, keyframes: list, dim: str) -> list:
        """(t, value) pairs for one dimension with per-interval easing baked in.

        The easing of the EARLIER keyframe owns the interval toward the next one
        (schema contract). 'step' holds the previous value until just before the
        next keyframe. The final sub-point of every interval is the exact keyframe
        value, so endpoints never drift.
        """
        pts = sorted(
            (
                (float(k["t"]), float(k[dim]), str(k.get("easing") or "linear"))
                for k in keyframes
                if isinstance(k, dict) and "t" in k and dim in k and k[dim] is not None
            ),
            key=lambda p: p[0],
        )
        if not pts:
            return []
        out: list = [(pts[0][0], pts[0][1])]
        for (t0, v0, easing), (t1, v1, _) in zip(pts, pts[1:]):
            if t1 <= t0 or v1 == v0 or easing == "linear":
                out.append((t1, v1))
                continue
            if easing == "step":
                out.append((round(max(t0, t1 - 1e-3), 4), v0))
                out.append((t1, v1))
                continue
            n = cls.EASING_SUBDIVISIONS
            for j in range(1, n):
                u = j / n
                out.append((
                    round(t0 + u * (t1 - t0), 4),
                    round(v0 + cls._ease_progress(easing, u) * (v1 - v0), 4),
                ))
            out.append((t1, v1))
        return out

    @staticmethod
    def _simple_fade_plan(opac: list) -> Optional[list[str]]:
        """Exact fade filters for plain fade-in/out opacity curves, else None.

        Returns [] when the curve is constant ~1 (nothing to render) and a list of
        fade filters for 0→1 entrances / 1→0 exits. Any other curve — partial
        opacities, mid-timeline dips, delayed rises — returns None and takes the
        geq alpha-expression path. ffmpeg's fade is linear, so easing on a fade
        interval is approximated as linear here (the fast path wins; route through
        a non-fade-shaped curve if eased opacity matters).
        """
        vals = [v for _, v in opac]
        if all(v >= 0.95 for v in vals):
            return []
        if len(opac) < 2:
            return None
        if not all(v <= 0.05 or v >= 0.95 for v in vals):
            return None
        if any(v <= 0.05 for v in vals[1:-1]):
            return None
        fades: list[str] = []
        if vals[0] <= 0.05:
            t0, t1 = opac[0][0], opac[1][0]
            fades.append(f"fade=t=in:st={t0}:d={max(0.05, t1 - t0)}:alpha=1")
        if vals[-1] <= 0.05:
            t0, t1 = opac[-2][0], opac[-1][0]
            fades.append(f"fade=t=out:st={t0}:d={max(0.05, t1 - t0)}:alpha=1")
        return fades or None

    @staticmethod
    def _probe_dimensions(path: Path) -> Optional[tuple[int, int]]:
        """(width, height) of the first video/image stream, or None if unprobeable."""
        try:
            out = subprocess.check_output(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=width,height",
                    "-of", "csv=p=0",
                    str(path),
                ],
                stderr=subprocess.STDOUT,
                text=True,
            )
            w, h = out.strip().split("\n")[0].split(",")[:2]
            return int(w), int(h)
        except Exception:
            return None

    def _keyframe_overlay(
        self, keyframes, base_x, base_y, start, i, overlay_input, prev_label, out_label, enable,
        *,
        pre_chain: Optional[list[str]] = None,
        nat_w: Optional[int] = None,
        nat_h: Optional[int] = None,
        static_opacity: Optional[float] = None,
        ov_format: str = "",
    ) -> dict:
        """Build the filtergraph parts for one keyframed overlay.

        Renders POSITION (x/y) via time-varying overlay expressions, SCALE via
        scale=eval=frame width/height expressions (center-anchored: x/y are
        compensated with the natural dims `nat_w`/`nat_h` so growth is symmetric),
        and OPACITY — exact fade filters for plain fade in/out, a geq alpha
        expression for any other piecewise curve, colorchannelmixer for a constant.
        Non-linear easings are piecewise-linear approximations (_eased_points).

        ROTATION is NOT supported by the FFmpeg path (documented limitation) —
        warn so the agent can switch to a richer renderer if needed.

        `pre_chain` carries static sizing filters from _overlay; `static_opacity`
        (overlays[].opacity) multiplies under any keyframed opacity curve.
        """
        filters: list[str] = []
        warnings: list[str] = []
        chain: list[str] = list(pre_chain or [])

        if any(isinstance(k, dict) and "rotation" in k for k in keyframes):
            warnings.append(
                "keyframe rotation is not rendered by the FFmpeg overlay path "
                "(position/scale/opacity only); rotation was ignored."
            )

        # --- opacity (MUST precede the time-varying scale) ---
        # Opacity is pointwise, so it commutes with scaling — but the reverse
        # order breaks: colorchannelmixer locks its frame size at config time and
        # silently freezes an eval=frame scale animation downstream (live-tested).
        if static_opacity is not None and static_opacity < 1.0:
            chain.append(f"format=rgba,colorchannelmixer=aa={static_opacity}")
        opac = self._kf_points(keyframes, "opacity")
        if opac:
            distinct_vals = {round(v, 4) for _, v in opac}
            fade_plan = self._simple_fade_plan(opac)
            if len(distinct_vals) == 1 and distinct_vals != {1.0}:
                # constant partial opacity — exact, no per-pixel eval needed
                chain.append(f"format=rgba,colorchannelmixer=aa={opac[0][1]}")
            elif fade_plan is not None:
                if fade_plan:
                    chain.append("format=yuva420p")
                    chain.extend(fade_plan)
            else:
                # piecewise / non-monotonic curve → per-pixel alpha expression.
                # geq exposes frame time as T (not t).
                a_expr = self._piecewise_linear_expr(
                    self._eased_points(keyframes, "opacity"), var="T"
                )
                chain.append("format=yuva420p")
                chain.append(
                    f"geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':"
                    f"a='alpha(X,Y)*clip({a_expr},0,1)'"
                )

        # --- time-varying scale (center-anchored) ---
        scale_pts = self._eased_points(keyframes, "scale")
        anchored = False
        if scale_pts and not (len(scale_pts) == 1 and scale_pts[0][1] == 1.0):
            s_expr = self._piecewise_linear_expr(scale_pts)
            # trunc-to-even keeps yuva420p happy; max(2,...) guards scale→0 frames.
            chain.append(
                f"scale=w='max(2,trunc(iw*({s_expr})/2)*2)':"
                f"h='max(2,trunc(ih*({s_expr})/2)*2)':eval=frame"
            )
            if nat_w is not None and nat_h is not None:
                anchored = True
            else:
                warnings.append(
                    "scale keyframes rendered without probeable natural dimensions — "
                    "overlay scales from its top-left corner instead of its center."
                )

        # --- position (with center compensation when scale animates) ---
        xpts = self._eased_points(keyframes, "x") or [(start, float(base_x))]
        ypts = self._eased_points(keyframes, "y") or [(start, float(base_y))]
        x_expr = self._piecewise_linear_expr(xpts)
        y_expr = self._piecewise_linear_expr(ypts)
        if anchored:
            # Keyframe x/y describe the top-left at natural size; keep the CENTER
            # fixed while the frame size changes (overlay_w/h track the per-frame size).
            x_expr = f"({x_expr})+{nat_w / 2:g}-overlay_w/2"
            y_expr = f"({y_expr})+{nat_h / 2:g}-overlay_h/2"

        src = overlay_input
        if chain:
            pre = f"ovk_{i}"
            filters.append(f"[{src}]{','.join(chain)}[{pre}]")
            src = pre
        filters.append(
            f"[{prev_label}][{src}]overlay=x='{x_expr}':y='{y_expr}':enable='{enable}'{ov_format}[{out_label}]"
        )
        return {"filters": filters, "warnings": warnings}

    def _encode(self, inputs: dict[str, Any]) -> ToolResult:
        """Re-encode video with a specific profile/codec settings."""
        input_path = Path(inputs["input_path"])
        output_path = Path(inputs.get("output_path", str(input_path.with_stem(f"{input_path.stem}_encoded"))))
        codec = inputs.get("codec", "libx264")
        crf = inputs.get("crf", 23)
        preset = inputs.get("preset", "medium")
        profile_name = inputs.get("profile")

        if not input_path.exists():
            return ToolResult(success=False, error=f"Input not found: {input_path}")

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-c:v", codec, "-crf", str(crf), "-preset", preset,
            "-c:a", "aac", "-b:a", "192k",
        ]

        # Apply media profile if specified
        if profile_name:
            try:
                from lib.media_profiles import get_profile, ffmpeg_output_args
                profile = get_profile(profile_name)
                cmd.extend(["-s", f"{profile.width}x{profile.height}"])
                cmd.extend(["-r", str(profile.fps)])
            except (ImportError, ValueError):
                pass  # proceed without profile

        cmd.append(str(output_path))
        self.run_command(cmd)

        return ToolResult(
            success=True,
            data={
                "operation": "encode",
                "codec": codec,
                "crf": crf,
                "profile": profile_name,
                "output": str(output_path),
            },
            artifacts=[str(output_path)],
        )

    @staticmethod
    def _resolve_subtitle_style(
        explicit_style: dict | None,
        edit_decisions: dict | None,
        playbook: dict | None,
    ) -> dict:
        """Resolve subtitle style with layered priority.

        Priority: explicit_style > edit_decisions.subtitles.style > playbook > defaults.
        This prevents every video from looking identical (Arial bold white).
        """
        # Start with minimal fallback defaults
        resolved = {
            "font": "Inter",
            "font_size": 28,
            "bold": True,
            "outline_width": 2,
            "shadow": 0,
            "margin_v": 40,
            "alignment": 2,
        }

        # Layer 1: Playbook-derived style
        if playbook:
            typo = playbook.get("typography", {})
            colors = playbook.get("visual_language", {}).get("color_palette", {})
            if typo.get("body", {}).get("family"):
                resolved["font"] = typo["body"]["family"]
            if colors.get("text"):
                resolved["primary_color"] = colors["text"]
            if colors.get("background"):
                resolved["outline_color"] = colors["background"]
                # Semi-transparent background for readability
                bg = colors["background"]
                resolved["back_color"] = bg

        # Layer 2: edit_decisions subtitle style
        if edit_decisions:
            ed_style = edit_decisions.get("subtitles", {}).get("style", {})
            for k, v in ed_style.items():
                if v is not None:
                    resolved[k] = v

        # Layer 3: Explicit override (highest priority)
        if explicit_style:
            for k, v in explicit_style.items():
                if v is not None:
                    resolved[k] = v

        return resolved

    @staticmethod
    def _build_subtitle_style(style: dict) -> str:
        """Build ASS force_style string from style dict."""
        parts = []
        parts.append(f"FontName={style.get('font', 'Inter')}")
        parts.append(f"FontSize={style.get('font_size', 28)}")
        parts.append(f"Bold={1 if style.get('bold', True) else 0}")
        if style.get("primary_color"):
            parts.append(f"PrimaryColour={style['primary_color']}")
        if style.get("outline_color"):
            parts.append(f"OutlineColour={style['outline_color']}")
        if style.get("back_color"):
            parts.append(f"BackColour={style['back_color']}")
        border_style = style.get("border_style", 1)
        parts.append(f"BorderStyle={border_style}")
        parts.append(f"Outline={style.get('outline_width', 2)}")
        parts.append(f"Shadow={style.get('shadow', 0)}")
        parts.append(f"MarginV={style.get('margin_v', 40)}")
        parts.append(f"Alignment={style.get('alignment', 2)}")
        return ",".join(parts)

    @staticmethod
    def _build_atempo(factor: float) -> str:
        """Build atempo filter chain for audio speed adjustment."""
        filters = []
        remaining = factor
        while remaining > 100.0:
            filters.append("atempo=100.0")
            remaining /= 100.0
        while remaining < 0.5:
            filters.append("atempo=0.5")
            remaining /= 0.5
        filters.append(f"atempo={remaining:.4f}")
        return ",".join(filters)
