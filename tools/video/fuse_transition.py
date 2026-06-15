"""Fuse Transition — AI generative morph between two clips (Instagram Edits' "fuse").

Extracts clip A's LAST frame and clip B's FIRST frame, generates a short morph clip
with Seedance 2.0 first/last-frame conditioning, normalizes the morph to clip A's
resolution/fps, and splices A + morph + B via the concat demuxer with a re-encode.

Design (Edits-parity, fuse transition):
  - PAID -> confirm=true gate (or FUSE_TRANSITION_AUTOCONFIRM=1), copied from
    restyle_video: the unconfirmed call returns the dry-run estimate and spends nothing.
  - Generation goes through the registry tool `seedance_video` (fal.ai) because ONLY the
    fal path exposes end-frame conditioning (operation=image_to_video with `image_url` +
    `end_image_url`); seedance_replicate has no end-frame field. If seedance_video is
    unavailable (no FAL key) this tool FAILS FAST with its install_instructions — it
    NEVER silently substitutes a crossfade (AGENT_GUIDE forbids silent substitution);
    use video_compose transitions if you want a free crossfade instead.
  - Seedance's minimum billable generation is 4s. The morph is generated at the minimum
    enum length >= morph_duration and RETIMED (setpts) down to morph_duration — trimming
    would cut the morph off before it reaches B's first frame; retiming keeps both
    endpoints so the splice stays seamless.
  - The concat demuxer requires uniform stream parameters, so all three segments are
    conformed to a mezzanine first (clip A's resolution/fps, yuv420p, aac stereo with
    silence filled in where a segment has no audio), then the demuxer splice re-encodes.
  - Clips A and B must share a resolution (the tool refuses to guess which one wins);
    fps may differ — everything is conformed to clip A's fps.

Documented limitations:
  - INSERTION semantics: the morph is inserted BETWEEN the clips, so the output runs
    len(A) + morph_duration + len(B). It does not consume the clips' tails.
  - A and B are re-encoded once at the conform stage (CRF 18 mezzanine) plus the final
    splice re-encode — two light generations of loss on the original footage.
  - The morph look is stochastic (Seedance); pass `seed` for reproducibility.
"""

from __future__ import annotations

import json
import math
import os
import shutil
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
    ToolStatus,
    ToolTier,
)


class _FuseInputError(Exception):
    """Bad parameters for the fuse (validated before any spend)."""


class FuseTransition(BaseTool):
    name = "fuse_transition"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_post"
    provider = "seedance"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    GENERATOR_TOOL = "seedance_video"  # fal path: the only one with end_image_url
    AUTOCONFIRM_ENV = "FUSE_TRANSITION_AUTOCONFIRM"
    DEFAULT_PROMPT = "smooth seamless morph transition"
    SUPPORTED_FORMATS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
    MORPH_MIN_S = 0.2
    MORPH_MAX_S = 15.0
    GEN_MIN_S = 4   # Seedance duration enum floor — also the minimum billable length
    GEN_MAX_S = 15
    # fal.ai Seedance 2.0 per-second rates (mirrors seedance_video.estimate_cost)
    _RATES = {"fast": 0.2419, "standard": 0.3034}
    _ASPECTS = {"21:9": 21 / 9, "16:9": 16 / 9, "4:3": 4 / 3,
                "1:1": 1.0, "3:4": 3 / 4, "9:16": 9 / 16}

    dependencies = ["cmd:ffmpeg"]
    install_instructions = (
        "Install FFmpeg: https://ffmpeg.org/download.html\n"
        "Set FAL_KEY to your fal.ai API key (powers the seedance_video generator).\n"
        "  Get one at https://fal.ai/dashboard/keys"
    )
    agent_skills = ["seedance-2-0", "ffmpeg"]

    capabilities = ["fuse_transition", "generative_morph", "transition"]
    supports = {
        "generative_morph": True,
        "first_last_frame_conditioning": True,
        "seed": True,
        "min_billable_seconds": GEN_MIN_S,
    }
    best_for = [
        "an AI 'fuse' morph between two shots that a crossfade can't sell (Edits parity)",
        "seamless scene hand-offs where the content of A literally transforms into B",
    ]
    not_good_for = [
        "free/offline transitions — generation is PAID (min 4 billable seconds); use video_compose transitions for crossfades/wipes",
        "clips with mismatched resolutions (normalize one first, e.g. auto_reframe/video_compose)",
        "HDR sources — output is 8-bit SDR; detect with is_hdr_source() and handle HDR per AGENT_GUIDE before using this tool",
    ]
    fallback_tools: list[str] = []

    input_schema = {
        "type": "object",
        "required": ["clip_a", "clip_b"],
        "properties": {
            "clip_a": {"type": "string", "description": "First clip; its LAST frame seeds the morph."},
            "clip_b": {"type": "string", "description": "Second clip; its FIRST frame ends the morph."},
            "prompt": {
                "type": "string",
                "default": DEFAULT_PROMPT,
                "description": "Morph style prompt passed to the generator.",
            },
            "morph_duration": {
                "type": "number",
                "minimum": MORPH_MIN_S,
                "maximum": MORPH_MAX_S,
                "default": 1.0,
                "description": "Final morph length in seconds (generated longer, then retimed down).",
            },
            "model_variant": {
                "type": "string",
                "enum": ["fast", "standard"],
                "default": "fast",
                "description": "Seedance tier; fast is cheaper and plenty for a ~1s morph.",
            },
            "seed": {"type": "integer", "description": "Optional generator seed for reproducibility."},
            "output_path": {"type": "string", "description": "Defaults to {a_stem}_fuse_{b_stem}.mp4"},
            "confirm": {"type": "boolean", "default": False, "description": "Authorize the PAID generation."},
            "keep_intermediates": {
                "type": "boolean",
                "default": False,
                "description": "Keep the work dir (extracted frames, raw morph, conformed segments).",
            },
            # provenance registration (optional)
            "asset_manifest_path": {
                "type": "string",
                "description": "Optional: append the spliced clip to this asset_manifest (validated, written).",
            },
            "scene_id": {"type": "string", "default": "derived", "description": "scene_id for the registered asset"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=1024, vram_mb=0, disk_mb=1024, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=0)
    idempotency_key_fields = ["clip_a", "clip_b", "prompt", "morph_duration", "model_variant", "seed"]
    side_effects = ["uploads two frames to fal.ai storage", "calls the fal.ai Seedance API (paid)",
                    "writes a spliced video file", "may append to an asset_manifest"]
    user_visible_verification = [
        "Scrub the splice points: the morph must start on A's last frame and land on B's first frame"
    ]

    # ---- status / cost ----

    def get_status(self) -> ToolStatus:
        # Mirrors seedance_video's key check without touching the registry (avoids
        # discovery side effects during status reporting).
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            return ToolStatus.UNAVAILABLE
        if os.environ.get("FAL_KEY") or os.environ.get("FAL_AI_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def _gen_seconds(self, morph_duration: float) -> int:
        return max(self.GEN_MIN_S, min(self.GEN_MAX_S, math.ceil(morph_duration)))

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        variant = inputs.get("model_variant", "fast")
        rate = self._RATES.get(variant, self._RATES["fast"])
        try:
            morph = float(inputs.get("morph_duration", 1.0))
        except (TypeError, ValueError):
            morph = 1.0
        return round(rate * self._gen_seconds(morph), 2)

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 90.0 if inputs.get("model_variant", "fast") == "fast" else 150.0

    # ---- execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
            return ToolResult(success=False, error="ffmpeg/ffprobe not found on PATH. " + self.install_instructions)

        try:
            clip_a, clip_b, prompt, morph_duration, variant, seed = self._validate(inputs)
        except _FuseInputError as e:
            return ToolResult(success=False, error=str(e))

        # Generator availability — fail fast, never silently substitute a crossfade.
        gen = self._get_generator()
        if gen is None:
            return ToolResult(
                success=False,
                error=(
                    f"Generator tool {self.GENERATOR_TOOL!r} is not registered; the fuse morph "
                    "cannot run. This tool never substitutes a crossfade — use video_compose "
                    "transitions if you want a free one. " + self.install_instructions
                ),
            )
        if gen.get_status() != ToolStatus.AVAILABLE:
            return ToolResult(
                success=False,
                error=(
                    f"Generator {gen.name!r} is UNAVAILABLE, so the fuse morph cannot run. "
                    "This tool never silently substitutes a crossfade — use video_compose "
                    "transitions if you want a free one. Setup:\n" + gen.install_instructions
                ),
            )

        probed_a = self._probe(clip_a)
        probed_b = self._probe(clip_b)
        w, h, fps = probed_a.get("width"), probed_a.get("height"), probed_a.get("fps")
        if not w or not h or not fps:
            return ToolResult(success=False, error=f"could not probe resolution/fps of clip_a: {clip_a}")
        if not probed_b.get("width") or not probed_b.get("height"):
            return ToolResult(success=False, error=f"could not probe resolution of clip_b: {clip_b}")
        if (probed_a.get("resolution") or "") != (probed_b.get("resolution") or ""):
            return ToolResult(
                success=False,
                error=(
                    f"clip_a is {probed_a.get('resolution')} but clip_b is {probed_b.get('resolution')}; "
                    "fuse_transition refuses to guess which wins. Normalize one clip first "
                    "(e.g. auto_reframe or a video_compose scale pass)."
                ),
            )

        gen_secs = self._gen_seconds(morph_duration)
        if not self._is_confirmed(inputs):
            est = self.estimate_cost(inputs)
            return ToolResult(
                success=False,
                error=(
                    f"Confirmation required: a fuse morph generates {gen_secs}s of Seedance 2.0 "
                    f"({variant}) via {gen.name} (~${est:.2f}, ~{int(self.estimate_runtime(inputs))}s). "
                    f"Re-call with confirm=true or set {self.AUTOCONFIRM_ENV}=1. Nothing was spent."
                ),
                data={
                    "requires_confirmation": True,
                    "estimated_cost_usd": est,
                    "billable_seconds": gen_secs,
                    "morph_duration": morph_duration,
                    "generator": gen.name,
                },
            )

        start = time.time()
        out_path = Path(inputs.get("output_path") or clip_a.with_name(f"{clip_a.stem}_fuse_{clip_b.stem}.mp4"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        workdir = out_path.parent / f"{out_path.stem}_fuse_work"
        workdir.mkdir(parents=True, exist_ok=True)

        try:
            # (1) boundary frames
            frame_a = workdir / "a_last.png"
            frame_b = workdir / "b_first.png"
            err = self._extract_last_frame(clip_a, frame_a, probed_a)
            if err:
                return ToolResult(success=False, error=f"could not extract clip_a's last frame: {err}")
            err = self._extract_first_frame(clip_b, frame_b)
            if err:
                return ToolResult(success=False, error=f"could not extract clip_b's first frame: {err}")

            # (2) generative morph, conditioned start=frame_a end=frame_b
            morph_raw = workdir / "morph_raw.mp4"
            try:
                image_url = self._upload_frame(str(frame_a))
                end_image_url = self._upload_frame(str(frame_b))
            except Exception as e:
                return ToolResult(success=False, error=f"frame upload for morph conditioning failed: {e}")
            payload: dict[str, Any] = {
                "operation": "image_to_video",
                "prompt": prompt,
                "image_url": image_url,
                "end_image_url": end_image_url,
                "duration": str(gen_secs),
                "model_variant": variant,
                "resolution": "480p" if min(w, h) <= 480 else "720p",
                "aspect_ratio": self._aspect_for(w, h),
                "generate_audio": False,
                "output_path": str(morph_raw),
            }
            if seed is not None:
                payload["seed"] = seed
            gen_result = gen.execute(payload)
            if not gen_result.success:
                return ToolResult(success=False, error=f"morph generation failed: {gen_result.error}")
            if not morph_raw.exists() or morph_raw.stat().st_size == 0:
                return ToolResult(success=False, error="morph generation reported success but produced no file.")

            morph_probed = self._probe(morph_raw)
            gen_dur = morph_probed.get("duration_seconds")
            if not gen_dur:
                return ToolResult(success=False, error="could not probe the generated morph's duration.")

            # (3) + splice prep: conform all three segments to A's resolution/fps mezzanine.
            # The morph is RETIMED (not trimmed) so it still lands exactly on B's first frame.
            with_audio = self._has_audio(clip_a) or self._has_audio(clip_b)
            seg_a = workdir / "seg_a.mp4"
            seg_m = workdir / "seg_m.mp4"
            seg_b = workdir / "seg_b.mp4"
            err = self._conform(clip_a, seg_a, w, h, fps, with_audio,
                                src_duration=probed_a.get("duration_seconds"))
            if err:
                return ToolResult(success=False, error=f"conforming clip_a failed: {err}")
            err = self._conform(morph_raw, seg_m, w, h, fps, with_audio,
                                retime_ratio=morph_duration / float(gen_dur),
                                trim=morph_duration, src_duration=morph_duration, drop_src_audio=True)
            if err:
                return ToolResult(success=False, error=f"normalizing the morph failed: {err}")
            err = self._conform(clip_b, seg_b, w, h, fps, with_audio,
                                src_duration=probed_b.get("duration_seconds"))
            if err:
                return ToolResult(success=False, error=f"conforming clip_b failed: {err}")

            # (4) splice via the concat demuxer with a re-encode for safety
            err = self._concat([seg_a, seg_m, seg_b], out_path, with_audio, workdir)
            if err:
                return ToolResult(success=False, error=f"concat splice failed: {err}")
            if not out_path.exists() or out_path.stat().st_size == 0:
                return ToolResult(success=False, error="splice produced no output.")
        finally:
            if not inputs.get("keep_intermediates"):
                shutil.rmtree(workdir, ignore_errors=True)

        probed_out = self._probe(out_path)
        data: dict[str, Any] = {
            "output": str(out_path),
            "output_path": str(out_path),
            "clip_a": str(clip_a),
            "clip_b": str(clip_b),
            "prompt": prompt,
            "morph_duration": morph_duration,
            "generated_seconds": gen_secs,
            "generated_duration_seconds": gen_dur,
            "generator": gen.name,
            "model": gen_result.model,
            "duration_seconds": probed_out.get("duration_seconds"),
            "resolution": probed_out.get("resolution"),
        }
        artifacts = [str(out_path)]

        am_path = inputs.get("asset_manifest_path")
        if am_path:
            reg_err = self._register_asset(Path(am_path), clip_a, clip_b, out_path, inputs, probed_out)
            if reg_err:
                data["asset_manifest_warning"] = reg_err
            else:
                data["asset_manifest_path"] = str(am_path)
                artifacts.append(str(am_path))

        return ToolResult(
            success=True,
            data=data,
            artifacts=artifacts,
            cost_usd=gen_result.cost_usd or self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 2),
            model=gen_result.model,
        )

    # ---- validation ----

    def _validate(self, inputs: dict[str, Any]) -> tuple[Path, Path, str, float, str, Optional[int]]:
        paths: list[Path] = []
        for key in ("clip_a", "clip_b"):
            raw = inputs.get(key)
            if not raw:
                raise _FuseInputError(f"{key} is required.")
            p = Path(raw)
            if not p.exists():
                raise _FuseInputError(f"{key} not found: {raw}")
            if p.suffix.lower() not in self.SUPPORTED_FORMATS:
                raise _FuseInputError(
                    f"{key} has unsupported format {p.suffix or '(none)'}; accepts {sorted(self.SUPPORTED_FORMATS)}."
                )
            paths.append(p)

        prompt = inputs.get("prompt", self.DEFAULT_PROMPT)
        if not isinstance(prompt, str) or not prompt.strip():
            raise _FuseInputError("prompt must be a non-empty string.")

        morph = inputs.get("morph_duration", 1.0)
        if not isinstance(morph, (int, float)) or isinstance(morph, bool) or not (
            self.MORPH_MIN_S <= morph <= self.MORPH_MAX_S
        ):
            raise _FuseInputError(
                f"morph_duration must be in [{self.MORPH_MIN_S}, {self.MORPH_MAX_S}] seconds; got {morph!r}."
            )

        variant = inputs.get("model_variant", "fast")
        if variant not in self._RATES:
            raise _FuseInputError(f"model_variant must be one of {sorted(self._RATES)}; got {variant!r}.")

        seed = inputs.get("seed")
        if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
            raise _FuseInputError(f"seed must be an integer; got {seed!r}.")

        return paths[0], paths[1], prompt.strip(), float(morph), variant, seed

    def _is_confirmed(self, inputs: dict[str, Any]) -> bool:
        if inputs.get("confirm") is True:
            return True
        return str(os.environ.get(self.AUTOCONFIRM_ENV, "")).strip().lower() in ("1", "true", "yes", "on")

    # ---- generator plumbing (patch points for tests) ----

    def _get_generator(self) -> Optional[BaseTool]:
        from tools.tool_registry import registry

        registry.ensure_discovered()
        return registry.get(self.GENERATOR_TOOL)

    def _upload_frame(self, frame_path: str) -> str:
        from tools.video._shared import upload_image_fal

        return upload_image_fal(frame_path)

    @staticmethod
    def _aspect_for(w: int, h: int) -> str:
        ratio = w / h
        return min(FuseTransition._ASPECTS, key=lambda k: abs(math.log(FuseTransition._ASPECTS[k] / ratio)))

    # ---- ffmpeg stages ----

    def _extract_first_frame(self, src: Path, dst: Path) -> Optional[str]:
        return self._run(["ffmpeg", "-y", "-i", str(src), "-map", "0:v:0", "-an",
                          "-frames:v", "1", str(dst)])

    def _extract_last_frame(self, src: Path, dst: Path, probed: dict[str, Any]) -> Optional[str]:
        """Decode the tail with -update 1 so the final overwrite IS the last frame."""
        dur = probed.get("duration_seconds") or 0
        ss = max(0.0, float(dur) - 0.5)
        err = self._run(["ffmpeg", "-y", "-ss", str(ss), "-i", str(src), "-map", "0:v:0",
                         "-an", "-update", "1", str(dst)])
        if err is None and dst.exists() and dst.stat().st_size > 0:
            return None
        # seek overshot the last packet (probe duration can exceed the last frame): decode all
        return self._run(["ffmpeg", "-y", "-i", str(src), "-map", "0:v:0",
                          "-an", "-update", "1", str(dst)])

    def _conform(
        self, src: Path, dst: Path, w: int, h: int, fps: float, with_audio: bool, *,
        retime_ratio: Optional[float] = None, trim: Optional[float] = None,
        src_duration: Optional[float] = None, drop_src_audio: bool = False,
    ) -> Optional[str]:
        """Re-encode a segment to the uniform mezzanine the concat demuxer needs."""
        vf_parts = [f"scale={w}:{h}:flags=lanczos", "setsar=1"]
        if retime_ratio is not None:
            vf_parts.append(f"setpts=PTS*{retime_ratio:.6f}")
        vf_parts.append(f"fps={fps}")
        vf = ",".join(vf_parts)

        cmd = ["ffmpeg", "-y", "-i", str(src)]
        if with_audio:
            if not drop_src_audio and self._has_audio(src):
                maps = ["-map", "0:v:0", "-map", "0:a:0"]
            else:
                silence_dur = src_duration or trim or 60.0
                cmd += ["-f", "lavfi", "-t", f"{float(silence_dur):.3f}",
                        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
                maps = ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
            audio_opts = ["-c:a", "aac", "-ar", "44100", "-ac", "2"]
        else:
            maps = ["-map", "0:v:0"]
            audio_opts = ["-an"]
        cmd += [*maps, "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                *audio_opts]
        if trim is not None:
            cmd += ["-t", f"{float(trim):.3f}"]
        cmd.append(str(dst))
        return self._run(cmd)

    def _concat(self, segments: list[Path], out: Path, with_audio: bool, workdir: Path) -> Optional[str]:
        list_path = workdir / "concat.txt"
        # concat-demuxer escaping: single quotes inside paths become '\''
        lines = "\n".join("file '" + str(p.resolve()).replace("'", "'\\''") + "'" for p in segments)
        list_path.write_text(lines + "\n")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
        cmd += ["-c:a", "aac"] if with_audio else ["-an"]
        cmd.append(str(out))
        return self._run(cmd)

    # ---- asset_manifest registration ----

    def _register_asset(
        self, path: Path, clip_a: Path, clip_b: Path, out: Path,
        inputs: dict[str, Any], probed: dict[str, Any],
    ) -> Optional[str]:
        if not path.exists():
            return f"asset_manifest_path not found: {path}"
        try:
            doc = json.loads(path.read_text())
        except Exception as e:
            return f"could not read asset_manifest: {e}"
        if not isinstance(doc, dict) or not isinstance(doc.get("assets"), list):
            return "asset_manifest is not a valid manifest object with an assets[] list."

        entry = {
            "id": f"fuse-{len(doc['assets']) + 1}",
            "type": "video",
            "path": str(out),
            "source_tool": "fuse_transition",
            "scene_id": str(inputs.get("scene_id", "derived")),
            "subtype": "fuse",
            "generation_summary": (
                f"fuse_transition morph ({inputs.get('morph_duration', 1.0)}s, "
                f"prompt={inputs.get('prompt', self.DEFAULT_PROMPT)!r}) spliced between "
                f"{clip_a.name} and {clip_b.name}"
            ),
            "format": out.suffix.lstrip(".") or "mp4",
        }
        if isinstance(probed.get("duration_seconds"), (int, float)):
            entry["duration_seconds"] = round(float(probed["duration_seconds"]), 4)
        if probed.get("resolution"):
            entry["resolution"] = probed["resolution"]
        doc["assets"].append(entry)
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("asset_manifest", doc)
        except Exception as e:
            return f"derived-asset entry did not validate against asset_manifest schema: {e}"
        self._write_json(path, doc)
        return None

    # ---- helpers (motion_ops conventions) ----

    def _run(self, cmd: list[str]) -> Optional[str]:
        """Run ffmpeg; return None on success or a trimmed stderr string on failure."""
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
        """Normalize to {duration_seconds, width, height, fps, resolution}."""
        out: dict[str, Any] = {}
        try:
            from tools.video._shared import probe_output

            info = probe_output(path)
            out["duration_seconds"] = info.get("duration_seconds")
            out["width"] = info.get("video_width") or info.get("width")
            out["height"] = info.get("video_height") or info.get("height")
        except Exception:
            pass
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
