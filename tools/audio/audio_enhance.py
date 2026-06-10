"""Audio enhancement tool for noise reduction and cleanup.

Provides noise reduction, normalization, and EQ via FFmpeg audio
filters. Optional pedalboard integration for higher-quality
processing when available.

Modes (Edits parity — AI voice enhance):
  - preset      (default) the original FFmpeg filter-chain presets / custom_af.
  - deess       standalone de-esser. One `intensity` knob (0-1) maps onto the
                ffmpeg `deesser` filter's i/m/f options (see _deess_af). The
                podcast preset uses the same mapping at a fixed 0.4 intensity —
                its description always claimed a de-esser; now it has one.
  - ai_isolate  ML voice isolation via the ElevenLabs Audio Isolation API
                (POST /v1/audio-isolation, multipart upload, returns the
                isolated-voice audio). Requires ELEVENLABS_API_KEY; this is a
                mode-scoped dependency — the local ffmpeg modes stay available
                without it, so the key is NOT in `dependencies` (image_gen-style
                partial availability) and a missing key fails only ai_isolate
                with an error naming the env var.

Documented limitations:
  - ai_isolate output is AUDIO ONLY (mp3 bytes from the API). For video inputs
    the video stream is dropped; remux the isolated track with ffmpeg/audio_mixer.
  - ai_isolate is paid: ElevenLabs bills roughly 1000 characters-equivalent per
    minute of audio. estimate_cost() probes the input duration and prices it at
    the same per-character rate as elevenlabs_tts (~$0.30/min) — a rough estimate,
    actual billing depends on the subscription plan.
  - The local modes are classical DSP, not ML; ai_isolate is the ML path.
"""

from __future__ import annotations

import mimetypes
import os
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
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


def _deess_af(intensity: float) -> str:
    """Map one intensity knob (0-1) onto the ffmpeg deesser's i/m/f options.

    deesser defaults to i=0 which is a NO-OP, so i must always be set explicitly:
      i = intensity            (how aggressively sibilance is detected)
      m = 0.5 + intensity/2    (max suppression depth scales with intensity)
      f = 0.5                  (keep the default sibilance band)
    """
    return f"deesser=i={round(intensity, 3)}:m={round(0.5 + intensity / 2, 3)}:f=0.5"


PRESETS = {
    "clean_speech": {
        "description": "Noise gate + highpass + compressor + limiter for clean dialogue",
        "af": (
            "highpass=f=80,"
            "lowpass=f=13000,"
            "agate=threshold=0.01:ratio=2:attack=5:release=50,"
            "acompressor=threshold=-20dB:ratio=3:attack=5:release=100,"
            "loudnorm=I=-16:LRA=11:TP=-1.5"
        ),
    },
    "noise_reduce": {
        "description": "Aggressive noise reduction for noisy environments",
        "af": (
            "afftdn=nf=-25:nt=w,"
            "highpass=f=100,"
            "loudnorm=I=-16:LRA=11:TP=-1.5"
        ),
    },
    "normalize_only": {
        "description": "Loudness normalization without other processing",
        "af": "loudnorm=I=-16:LRA=11:TP=-1.5",
    },
    "podcast": {
        "description": "Podcast-style processing: de-ess, compress, normalize",
        "af": (
            "highpass=f=80,"
            + _deess_af(0.4) + ","
            "acompressor=threshold=-18dB:ratio=4:attack=5:release=100:makeup=2,"
            "loudnorm=I=-16:LRA=7:TP=-1.5"
        ),
    },
    "broadcast": {
        "description": "Broadcast-standard processing with tight dynamics",
        "af": (
            "highpass=f=80,"
            "lowpass=f=15000,"
            "acompressor=threshold=-24dB:ratio=4:attack=5:release=80:makeup=3,"
            "alimiter=limit=0.95:attack=1:release=10,"
            "loudnorm=I=-24:LRA=7:TP=-2"
        ),
    },
    "voice_clarity": {
        "description": "Boost vocal presence with EQ and light compression",
        "af": (
            "highpass=f=80,"
            "equalizer=f=200:t=q:w=1.5:g=-3,"
            "equalizer=f=3000:t=q:w=1.0:g=3,"
            "equalizer=f=5000:t=q:w=1.5:g=2,"
            "acompressor=threshold=-20dB:ratio=2.5:attack=10:release=100,"
            "loudnorm=I=-16:LRA=11:TP=-1.5"
        ),
    },
}


class AudioEnhance(BaseTool):
    name = "audio_enhance"
    version = "0.2.0"
    tier = ToolTier.CORE
    capability = "audio_processing"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.HYBRID  # local ffmpeg modes OR the ElevenLabs ai_isolate API

    # env:ELEVENLABS_API_KEY is deliberately NOT listed: it gates only the
    # ai_isolate mode. Listing it would mark the whole tool UNAVAILABLE even
    # though every local mode works (see get_status / docstring).
    dependencies = ["cmd:ffmpeg"]
    install_instructions = (
        "Install FFmpeg: https://ffmpeg.org/download.html\n"
        "For mode=ai_isolate also set ELEVENLABS_API_KEY (https://elevenlabs.io)."
    )
    agent_skills = ["ffmpeg", "elevenlabs"]

    MODES = ("preset", "deess", "ai_isolate")
    ISOLATION_ENV_VAR = "ELEVENLABS_API_KEY"
    ISOLATION_ENDPOINT = "https://api.elevenlabs.io/v1/audio-isolation"
    # ElevenLabs bills ~1000 chars-equivalent per minute of audio; priced at the
    # same ~$0.0003/char rate elevenlabs_tts uses -> ~$0.30/min. Rough estimate.
    ISOLATION_COST_PER_MINUTE_USD = 0.30
    DEESS_DEFAULT_INTENSITY = 0.5

    capabilities = [
        "noise_reduction",
        "normalization",
        "compression",
        "eq",
        "speech_cleanup",
        "de_essing",
        "voice_isolation",
    ]
    not_good_for = [
        "HDR sources — output is 8-bit SDR; detect with is_hdr_source() and handle HDR per AGENT_GUIDE before using this tool",
        "ai_isolate on music/full mixes — it extracts voice and discards everything else",
        "keeping the video stream through ai_isolate — that mode outputs audio only",
    ]

    input_schema = {
        "type": "object",
        "required": ["input_path"],
        "properties": {
            "input_path": {"type": "string"},
            "output_path": {"type": "string"},
            "mode": {
                "type": "string",
                "enum": list(MODES),
                "default": "preset",
                "description": (
                    "preset = FFmpeg filter chains (default); deess = standalone de-esser; "
                    "ai_isolate = ElevenLabs ML voice isolation (needs ELEVENLABS_API_KEY, "
                    "paid: ~1000 chars-equivalent per audio minute)"
                ),
            },
            "preset": {
                "type": "string",
                "enum": list(PRESETS.keys()),
                "default": "clean_speech",
            },
            "custom_af": {
                "type": "string",
                "description": "Custom FFmpeg audio filter string",
            },
            "intensity": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "default": DEESS_DEFAULT_INTENSITY,
                "description": "deess: 0-1, drives the deesser filter's i/m/f options",
            },
            "audio_codec": {"type": "string", "default": "aac"},
            "audio_bitrate": {"type": "string", "default": "192k"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=500)
    idempotency_key_fields = ["input_path", "mode", "preset", "custom_af", "intensity"]
    side_effects = [
        "writes enhanced audio/video to output_path",
        "mode=ai_isolate uploads the input to the ElevenLabs API (paid)",
    ]
    user_visible_verification = [
        "Listen to enhanced audio and compare with original",
        "Verify speech is clear without artifacts or pumping",
    ]

    def get_status(self) -> ToolStatus:
        # Multi-mode partial availability (image_gen pattern): local ffmpeg modes
        # and the ai_isolate API mode are independent providers — AVAILABLE if
        # either one can run.
        if shutil.which("ffmpeg") is not None or os.environ.get(self.ISOLATION_ENV_VAR):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        if inputs.get("mode") != "ai_isolate":
            return 0.0
        # ~1000 chars-equivalent per minute; falls back to 1 minute when the
        # duration can't be probed. Rough — actual billing is plan-dependent.
        minutes = (self._probe_duration(Path(str(inputs.get("input_path", "")))) or 60.0) / 60.0
        return round(max(minutes, 1 / 60) * self.ISOLATION_COST_PER_MINUTE_USD, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        src = inputs.get("input_path")
        if not src:
            return ToolResult(success=False, error="input_path is required.")
        input_path = Path(src)
        if not input_path.exists():
            return ToolResult(success=False, error=f"Input not found: {input_path}")

        mode = inputs.get("mode", "preset")
        if mode not in self.MODES:
            return ToolResult(success=False, error=f"mode must be one of {self.MODES}; got {mode!r}.")

        if mode == "ai_isolate":
            return self._ai_isolate(input_path, inputs)
        if mode == "deess":
            intensity = inputs.get("intensity", self.DEESS_DEFAULT_INTENSITY)
            if not isinstance(intensity, (int, float)) or not (0 <= intensity <= 1):
                return ToolResult(
                    success=False, error=f"deess requires intensity in [0, 1]; got {intensity!r}."
                )
            af = _deess_af(float(intensity))
            output_path = Path(
                inputs.get("output_path", str(input_path.with_stem(f"{input_path.stem}_deessed")))
            )
            extra_data = {"mode": "deess", "intensity": float(intensity)}
        else:
            af = inputs.get("custom_af")
            if not af:
                preset_name = inputs.get("preset", "clean_speech")
                preset = PRESETS.get(preset_name)
                if not preset:
                    return ToolResult(success=False, error=f"Unknown preset: {preset_name}")
                af = preset["af"]
            output_path = Path(
                inputs.get("output_path", str(input_path.with_stem(f"{input_path.stem}_enhanced")))
            )
            extra_data = {"preset": inputs.get("preset")}

        audio_codec = inputs.get("audio_codec", "aac")
        audio_bitrate = inputs.get("audio_bitrate", "192k")

        start = time.time()

        # Determine if input is video or audio-only
        is_video = input_path.suffix.lower() in {".mp4", ".mkv", ".avi", ".mov", ".webm"}

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-af", af,
        ]
        if is_video:
            cmd.extend(["-c:v", "copy"])
        cmd.extend(["-c:a", audio_codec, "-b:a", audio_bitrate])
        cmd.append(str(output_path))

        try:
            self.run_command(cmd)
        except Exception as e:
            return ToolResult(success=False, error=f"FFmpeg failed: {self._trim_err(e)}")

        elapsed = time.time() - start

        return ToolResult(
            success=True,
            data={
                "input": str(input_path),
                "output": str(output_path),
                **extra_data,
                "filter": af,
            },
            artifacts=[str(output_path)],
            duration_seconds=round(elapsed, 2),
        )

    # ---- ai_isolate (ElevenLabs Audio Isolation) ----

    def _ai_isolate(self, input_path: Path, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get(self.ISOLATION_ENV_VAR)
        if not api_key:
            return ToolResult(
                success=False,
                error=(
                    f"mode=ai_isolate requires the {self.ISOLATION_ENV_VAR} environment "
                    "variable (get a key at https://elevenlabs.io). The local modes "
                    "(preset, deess) work without it."
                ),
            )

        import requests

        # The API returns isolated-voice audio bytes (mp3) — audio only, even for video inputs.
        output_path = Path(
            inputs.get("output_path", str(input_path.with_name(f"{input_path.stem}_isolated.mp3")))
        )
        mime = mimetypes.guess_type(input_path.name)[0] or "application/octet-stream"

        start = time.time()
        try:
            with open(input_path, "rb") as fh:
                response = requests.post(
                    self.ISOLATION_ENDPOINT,
                    headers={"xi-api-key": api_key},
                    files={"audio": (input_path.name, fh, mime)},
                    timeout=600,
                )
            response.raise_for_status()
        except Exception as e:
            return ToolResult(success=False, error=f"ElevenLabs audio isolation failed: {e}")

        if not response.content:
            return ToolResult(success=False, error="ElevenLabs audio isolation returned no audio.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)

        return ToolResult(
            success=True,
            data={
                "input": str(input_path),
                "output": str(output_path),
                "mode": "ai_isolate",
                "provider": "elevenlabs",
                "note": "output is isolated-voice audio only (no video stream)",
            },
            artifacts=[str(output_path)],
            cost_usd=self.estimate_cost({"mode": "ai_isolate", "input_path": str(input_path)}),
            duration_seconds=round(time.time() - start, 2),
        )

    # ---- helpers ----

    def _probe_duration(self, path: Path) -> float | None:
        if not path.is_file() or shutil.which("ffprobe") is None:
            return None
        try:
            proc = self.run_command(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                timeout=30,
            )
            return float((proc.stdout or "").strip())
        except Exception:
            return None

    @staticmethod
    def _trim_err(e: Exception) -> str:
        stderr = getattr(e, "stderr", None)
        if stderr:
            return str(stderr).strip()[-500:]
        return str(e)

    @staticmethod
    def list_presets() -> dict[str, str]:
        """Return available presets and their descriptions."""
        return {name: p["description"] for name, p in PRESETS.items()}
