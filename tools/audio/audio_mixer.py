"""Audio mixer tool wrapping FFmpeg and pydub.

Mixes speech, music, and SFX tracks with support for ducking, fades,
volume normalization, loudness auto-balancing, and audio extraction.
Falls back to FFmpeg-only mode if pydub is not installed.

Design notes / documented limitations:
  - Per-track fades are applied to the SOURCE audio BEFORE any start_seconds
    delay, so fade_in ramps the actual audio start and fade_out lands on the
    actual audio end. fade_out emits an explicit afade st= computed from the
    probed track duration (FFmpeg defaults st=0, which fades the track to
    silence over its FIRST N seconds and keeps it muted — a live-verified bug
    this tool previously had in both mix and full_mix).
  - auto_balance measures integrated LUFS per track (ffmpeg ebur128) and
    computes per-role gains toward a voice-anchored target. In apply mode it
    delegates to the mix pathway; amix scales each input by 1/n, so the
    output preserves the computed RELATIVE balance but the absolute level may
    sit below target — run a loudnorm pass (normalize=true) for delivery
    loudness. Gains are capped at +/-30 dB (near-silent tracks would
    otherwise explode).
  - extract defaults are UNCHANGED (pcm_s16le 16kHz mono, transcription
    grade); pass codec for full-fidelity detach (copy/aac/wav/mp3).
"""

from __future__ import annotations

import math
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
    ToolStatus,
    ToolTier,
)


class AudioMixer(BaseTool):
    name = "audio_mixer"
    version = "0.2.0"
    tier = ToolTier.CORE
    capability = "audio_processing"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = ["cmd:ffmpeg"]
    install_instructions = (
        "FFmpeg is required. pydub is optional for advanced mixing:\n"
        "pip install pydub"
    )
    agent_skills = ["ffmpeg", "video_toolkit"]

    capabilities = [
        "mix", "duck", "fade", "normalize", "extract_audio", "segmented_music",
        "auto_balance",
    ]
    not_good_for = [
        "HDR sources — output is 8-bit SDR; detect with is_hdr_source() and "
        "handle HDR per AGENT_GUIDE before using this tool",
    ]

    # auto_balance role normalization + gain safety cap
    ROLE_ALIASES = {
        "voice": "voice", "speech": "voice", "primary": "voice",
        "music": "music", "secondary": "music",
        "sfx": "sfx",
    }
    MAX_BALANCE_GAIN_DB = 30.0

    # extract: codec -> (ffmpeg encoder, default extension); copy is resolved at runtime
    EXTRACT_CODECS = {
        "copy": (None, None),
        "aac": ("aac", ".m4a"),
        "wav": ("pcm_s16le", ".wav"),
        "pcm_s16le": ("pcm_s16le", ".wav"),
        "mp3": ("libmp3lame", ".mp3"),
    }
    COPY_EXT_BY_CODEC = {
        "aac": ".m4a", "mp3": ".mp3", "opus": ".ogg", "vorbis": ".ogg",
        "flac": ".flac", "ac3": ".ac3", "eac3": ".eac3",
    }

    input_schema = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["mix", "duck", "extract", "full_mix", "segmented_music", "auto_balance"],
                "description": (
                    "mix: layer multiple tracks with volume/delay/fades. "
                    "duck: lower music volume when speech is present. "
                    "extract: extract audio from video file. "
                    "full_mix: combine narration tracks + music with ducking + normalize "
                    "in a single call (preferred for compose-director). "
                    "segmented_music: mix music into a video only during specified "
                    "time segments (e.g. music during talking head, silence during "
                    "showcase clips). "
                    "auto_balance: measure each track's integrated LUFS and gain-match "
                    "voice/music/sfx toward targets (dry-run with apply=false, or mix)."
                ),
            },
            "tracks": {
                "type": "array",
                "description": (
                    "Audio tracks for mix/duck operations (advanced format). "
                    "For duck, each track needs a 'role' of 'speech' or 'music'. "
                    "For the simple duck API, use primary_audio/secondary_audio instead."
                ),
                "items": {
                    "type": "object",
                    "required": ["path", "role"],
                    "properties": {
                        "path": {"type": "string"},
                        "role": {
                            "type": "string",
                            "enum": ["speech", "music", "sfx", "primary", "secondary", "voice"],
                        },
                        "volume": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1.0,
                            "default": 1.0,
                        },
                        "start_seconds": {"type": "number", "minimum": 0},
                        "fade_in_seconds": {"type": "number", "minimum": 0},
                        "fade_out_seconds": {"type": "number", "minimum": 0},
                    },
                },
            },
            "primary_audio": {
                "type": "string",
                "description": (
                    "Path to primary/speech audio track (duck operation, simple format). "
                    "This is the track that stays at full volume (e.g. narration/dialogue). "
                    "Use with secondary_audio as an alternative to the tracks array."
                ),
            },
            "secondary_audio": {
                "type": "string",
                "description": (
                    "Path to secondary/music audio track (duck operation, simple format). "
                    "This track gets ducked (volume lowered) when primary audio is present. "
                    "Use with primary_audio as an alternative to the tracks array."
                ),
            },
            "duck_level": {
                "type": "number",
                "description": (
                    "Ducking attenuation in dB for the secondary track (duck operation, "
                    "simple format). Negative values reduce volume, e.g. -12 means duck "
                    "by 12dB. Converted to a linear ratio internally. Default: -12."
                ),
                "default": -12,
            },
            "input_path": {"type": "string", "description": "Input for extract operation"},
            "output_path": {"type": "string"},
            "codec": {
                "type": "string",
                "enum": ["copy", "aac", "wav", "pcm_s16le", "mp3"],
                "description": (
                    "extract: output codec. Omit for the legacy transcription-grade "
                    "default (pcm_s16le 16kHz mono). copy = stream-copy the source "
                    "audio untouched (full fidelity; incompatible with "
                    "sample_rate/channels)."
                ),
            },
            "sample_rate": {
                "type": "integer",
                "minimum": 1,
                "description": "extract: output sample rate in Hz (not valid with codec=copy).",
            },
            "channels": {
                "type": "integer",
                "minimum": 1,
                "description": "extract: output channel count (not valid with codec=copy).",
            },
            "stream_index": {
                "type": "integer",
                "minimum": 0,
                "description": "extract: which audio stream to take (0-based among audio streams).",
            },
            "target_lufs_voice": {
                "type": "number",
                "default": -16,
                "description": "auto_balance: integrated LUFS target for voice tracks.",
            },
            "music_offset_db": {
                "type": "number",
                "default": -12,
                "description": "auto_balance: music target in dB relative to the voice target.",
            },
            "sfx_offset_db": {
                "type": "number",
                "default": -8,
                "description": "auto_balance: sfx target in dB relative to the voice target.",
            },
            "apply": {
                "type": "boolean",
                "default": True,
                "description": (
                    "auto_balance: false = dry run; return measured LUFS + computed "
                    "gains in data without writing any file."
                ),
            },
            "ducking": {
                "type": "object",
                "description": (
                    "Advanced ducking parameters. Works with both the simple "
                    "(primary_audio/secondary_audio) and advanced (tracks) formats."
                ),
                "properties": {
                    "enabled": {"type": "boolean", "default": True},
                    "music_volume_during_speech": {
                        "type": "number", "minimum": 0, "maximum": 1.0, "default": 0.15,
                    },
                    "attack_ms": {"type": "number", "default": 200},
                    "release_ms": {"type": "number", "default": 500},
                },
            },
            "normalize": {"type": "boolean", "default": True},
            "video_path": {
                "type": "string",
                "description": (
                    "Path to the assembled video (segmented_music operation). "
                    "Music is mixed into this video's audio at specified segments."
                ),
            },
            "music_path": {
                "type": "string",
                "description": "Path to background music file (segmented_music operation).",
            },
            "music_volume": {
                "type": "number",
                "minimum": 0,
                "maximum": 1.0,
                "default": 0.20,
                "description": "Volume level for music during active segments.",
            },
            "segments": {
                "type": "array",
                "description": (
                    "Time segments where music should play (segmented_music operation). "
                    "Each segment: {start: seconds, end: seconds}. Music fades in/out "
                    "at segment boundaries. Outside these segments, music is silent."
                ),
                "items": {
                    "type": "object",
                    "required": ["start", "end"],
                    "properties": {
                        "start": {"type": "number", "minimum": 0},
                        "end": {"type": "number", "minimum": 0},
                    },
                },
            },
            "fade_duration": {
                "type": "number",
                "default": 0.5,
                "description": "Duration of fade in/out at segment boundaries (seconds).",
            },
        },
    }

    resource_profile = ResourceProfile(cpu_cores=2, ram_mb=1024, vram_mb=0, disk_mb=500)
    idempotency_key_fields = ["operation", "tracks", "ducking"]
    side_effects = ["writes mixed audio file to output_path"]
    user_visible_verification = [
        "Listen to mixed output and verify speech clarity and music ducking",
    ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        operation = inputs["operation"]
        start = time.time()

        try:
            if operation == "mix":
                result = self._mix(inputs)
            elif operation == "duck":
                result = self._duck(inputs)
            elif operation == "extract":
                result = self._extract(inputs)
            elif operation == "full_mix":
                result = self._full_mix(inputs)
            elif operation == "segmented_music":
                result = self._segmented_music(inputs)
            elif operation == "auto_balance":
                result = self._auto_balance(inputs)
            else:
                return ToolResult(success=False, error=f"Unknown operation: {operation}")
        except _MixInputError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=str(e))

        result.duration_seconds = round(time.time() - start, 2)
        return result

    def _mix(self, inputs: dict[str, Any]) -> ToolResult:
        """Mix multiple audio tracks into one output."""
        tracks = inputs.get("tracks", [])
        if not tracks:
            return ToolResult(success=False, error="No tracks provided")

        output_path = Path(inputs.get("output_path", "mixed_audio.wav"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        normalize = inputs.get("normalize", True)

        # Validate all inputs exist
        for t in tracks:
            if not Path(t["path"]).exists():
                return ToolResult(success=False, error=f"Track not found: {t['path']}")

        # Build FFmpeg complex filter for mixing
        filter_parts = []
        input_args = []

        for i, track in enumerate(tracks):
            input_args.extend(["-i", track["path"]])
            filter_parts.append(self._per_track_chain(i, track))

        # Amix all processed streams
        mix_inputs = "".join(f"[a{i}]" for i in range(len(tracks)))
        filter_parts.append(
            f"{mix_inputs}amix=inputs={len(tracks)}:duration=longest:dropout_transition=2[mixed]"
        )

        if normalize:
            filter_parts.append("[mixed]loudnorm=I=-16:LRA=11:TP=-1.5[out]")
            out_label = "[out]"
        else:
            out_label = "[mixed]"

        filter_complex = ";".join(filter_parts)

        cmd = ["ffmpeg", "-y"]
        cmd.extend(input_args)
        cmd.extend(["-filter_complex", filter_complex])
        cmd.extend(["-map", out_label, str(output_path)])

        err = self._run(cmd)
        if err:
            return ToolResult(success=False, error=f"mix failed: {err}")

        return ToolResult(
            success=True,
            data={
                "operation": "mix",
                "track_count": len(tracks),
                "output": str(output_path),
                "normalized": normalize,
            },
            artifacts=[str(output_path)],
        )

    def _duck(self, inputs: dict[str, Any]) -> ToolResult:
        """Apply ducking: lower music volume when speech is present.

        Accepts two input formats:

        Simple format (preferred for agents):
            {
                "operation": "duck",
                "primary_audio": "speech.mp3",
                "secondary_audio": "music.mp3",
                "duck_level": -12,
                "output_path": "out.wav"
            }

        Advanced format (tracks array):
            {
                "operation": "duck",
                "tracks": [
                    {"path": "speech.mp3", "role": "primary"},  # or "speech"
                    {"path": "music.mp3", "role": "secondary"}  # or "music"
                ],
                "output_path": "out.wav"
            }
        """
        ducking = inputs.get("ducking", {})
        output_path = Path(inputs.get("output_path", "ducked_audio.wav"))

        # --- Resolve speech/music paths from either input format ---
        speech_path = None
        music_path = None

        # Simple format: primary_audio / secondary_audio
        if "primary_audio" in inputs or "secondary_audio" in inputs:
            speech_path = inputs.get("primary_audio")
            music_path = inputs.get("secondary_audio")
            # If duck_level (dB) is provided, convert to linear ratio for
            # music_volume_during_speech.  e.g. -12 dB -> 10^(-12/20) ~ 0.25
            if "duck_level" in inputs and "ducking" not in inputs:
                import math
                db = inputs["duck_level"]
                ducking = dict(ducking)  # copy so we don't mutate caller
                ducking.setdefault(
                    "music_volume_during_speech",
                    round(math.pow(10, db / 20), 4),
                )

        # Advanced format: tracks array with role field
        tracks = inputs.get("tracks", [])
        if tracks and speech_path is None and music_path is None:
            # Support both naming conventions: speech/music and primary/secondary
            speech_tracks = [
                t for t in tracks if t.get("role") in ("speech", "primary")
            ]
            music_tracks = [
                t for t in tracks if t.get("role") in ("music", "secondary")
            ]
            if speech_tracks:
                speech_path = speech_tracks[0]["path"]
            if music_tracks:
                music_path = music_tracks[0]["path"]

        if not speech_path or not music_path:
            return ToolResult(
                success=False,
                error=(
                    "Ducking requires a primary (speech) and secondary (music) track. "
                    "Provide either primary_audio/secondary_audio params, or a tracks "
                    "array with role='speech'/'primary' and role='music'/'secondary'."
                ),
            )

        # Use FFmpeg sidechaincompress for ducking
        music_vol = ducking.get("music_volume_during_speech", 0.15)
        attack = ducking.get("attack_ms", 200) / 1000
        release = ducking.get("release_ms", 500) / 1000

        # Sidechain compress: use speech as the key signal to duck music
        filter_complex = (
            f"[1:a]sidechaincompress="
            f"threshold=0.02:ratio=9:attack={attack}:release={release}:"
            f"level_sc=1:mix=0.9[ducked];"
            f"[ducked]volume={music_vol * 3}[music_out];"  # compensate sidechain level
            f"[0:a][music_out]amix=inputs=2:duration=longest[out]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", speech_path,
            "-i", music_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            str(output_path),
        ]

        self.run_command(cmd)

        return ToolResult(
            success=True,
            data={
                "operation": "duck",
                "speech_track": speech_path,
                "music_track": music_path,
                "output": str(output_path),
            },
            artifacts=[str(output_path)],
        )

    def _extract(self, inputs: dict[str, Any]) -> ToolResult:
        """Extract audio from a video file.

        Default behavior (no codec given) is UNCHANGED for back-compat:
        pcm_s16le 16kHz mono — transcription grade, existing callers rely on it.
        Pass codec (copy/aac/wav/pcm_s16le/mp3) for a full-fidelity detach;
        sample_rate/channels/stream_index are honored when given.
        """
        codec = inputs.get("codec")
        sample_rate = inputs.get("sample_rate")
        channels = inputs.get("channels")
        stream_index = inputs.get("stream_index")

        if codec is not None and codec not in self.EXTRACT_CODECS:
            return ToolResult(
                success=False,
                error=f"extract: codec must be one of {sorted(self.EXTRACT_CODECS)}; got {codec!r}",
            )
        for label, val in (("sample_rate", sample_rate), ("channels", channels)):
            if val is not None and (not isinstance(val, int) or isinstance(val, bool) or val <= 0):
                return ToolResult(success=False, error=f"extract: {label} must be a positive integer.")
        if stream_index is not None and (
            not isinstance(stream_index, int) or isinstance(stream_index, bool) or stream_index < 0
        ):
            return ToolResult(success=False, error="extract: stream_index must be a non-negative integer.")
        if codec == "copy" and (sample_rate is not None or channels is not None):
            return ToolResult(
                success=False,
                error=(
                    "extract: codec=copy stream-copies the source audio; "
                    "sample_rate/channels would require re-encoding. Drop them or "
                    "pick an encoding codec (aac/wav/mp3)."
                ),
            )

        input_path = Path(inputs.get("input_path", ""))
        if not str(input_path) or not input_path.exists():
            return ToolResult(success=False, error=f"Input not found: {input_path}")

        source_codec = None
        if codec is None:
            # Legacy default (back-compat): transcription-grade wav
            acodec, ext = "pcm_s16le", ".wav"
            sample_rate = sample_rate if sample_rate is not None else 16000
            channels = channels if channels is not None else 1
        elif codec == "copy":
            source_codec = self._probe_audio_codec(input_path, stream_index)
            if not source_codec:
                return ToolResult(success=False, error="extract: no audio stream found to copy.")
            acodec = "copy"
            ext = self.COPY_EXT_BY_CODEC.get(
                source_codec, ".wav" if source_codec.startswith("pcm_") else ".mka"
            )
        else:
            acodec, ext = self.EXTRACT_CODECS[codec]

        output_path = Path(inputs.get("output_path", str(input_path.with_suffix(ext))))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["ffmpeg", "-y", "-i", str(input_path), "-vn"]
        if stream_index is not None:
            cmd.extend(["-map", f"0:a:{stream_index}"])
        cmd.extend(["-acodec", acodec])
        if acodec != "copy":
            if sample_rate is not None:
                cmd.extend(["-ar", str(sample_rate)])
            if channels is not None:
                cmd.extend(["-ac", str(channels)])
        cmd.append(str(output_path))

        err = self._run(cmd)
        if err:
            return ToolResult(success=False, error=f"extract failed: {err}")
        if not output_path.exists() or output_path.stat().st_size == 0:
            return ToolResult(success=False, error="extract produced no output.")

        data: dict[str, Any] = {
            "operation": "extract",
            "input": str(input_path),
            "output": str(output_path),
            "codec": source_codec if codec == "copy" else acodec,
        }
        if codec == "copy":
            data["stream_copied"] = True
        if sample_rate is not None:
            data["sample_rate"] = sample_rate
        if channels is not None:
            data["channels"] = channels
        if stream_index is not None:
            data["stream_index"] = stream_index

        return ToolResult(success=True, data=data, artifacts=[str(output_path)])

    def _auto_balance(self, inputs: dict[str, Any]) -> ToolResult:
        """Per-track loudness matching toward a voice-anchored LUFS target.

        Measures each track's integrated LUFS (ffmpeg ebur128 summary), then
        computes the per-track gain that moves it to its role target:
          voice -> target_lufs_voice (default -16)
          music -> target_lufs_voice + music_offset_db (default -12)
          sfx   -> target_lufs_voice + sfx_offset_db (default -8)

        apply=false returns measured/computed values only (dry run). Otherwise
        the computed volumes are handed to the existing mix pathway. normalize
        defaults OFF here — a post-mix loudnorm would re-target the overall
        loudness and obscure the balance just computed.
        """
        tracks = inputs.get("tracks", [])
        if not tracks:
            return ToolResult(success=False, error="No tracks provided for auto_balance")

        target_voice = float(inputs.get("target_lufs_voice", -16))
        targets = {
            "voice": target_voice,
            "music": target_voice + float(inputs.get("music_offset_db", -12)),
            "sfx": target_voice + float(inputs.get("sfx_offset_db", -8)),
        }
        apply_mix = inputs.get("apply", True)

        resolved: list[tuple[dict[str, Any], str]] = []
        for t in tracks:
            role = self.ROLE_ALIASES.get(t.get("role"))
            if role is None:
                return ToolResult(
                    success=False,
                    error=f"auto_balance: track role must be voice/music/sfx; got {t.get('role')!r}",
                )
            path = t.get("path")
            if not path or not Path(path).exists():
                return ToolResult(success=False, error=f"Track not found: {path}")
            resolved.append((t, role))

        report: list[dict[str, Any]] = []
        mix_tracks: list[dict[str, Any]] = []
        for t, role in resolved:
            measured = self._measure_lufs(Path(t["path"]))
            if measured is None:
                return ToolResult(
                    success=False,
                    error=f"auto_balance: could not measure integrated LUFS of {t['path']}",
                )
            target = targets[role]
            gain_db = target - measured
            capped = abs(gain_db) > self.MAX_BALANCE_GAIN_DB
            if capped:
                gain_db = max(-self.MAX_BALANCE_GAIN_DB, min(self.MAX_BALANCE_GAIN_DB, gain_db))
            volume = round(math.pow(10, gain_db / 20), 6)

            entry: dict[str, Any] = {
                "path": t["path"],
                "role": role,
                "measured_lufs": round(measured, 2),
                "target_lufs": round(target, 2),
                "gain_db": round(gain_db, 2),
                "volume": volume,
            }
            if capped:
                entry["gain_capped"] = True
            report.append(entry)

            mt: dict[str, Any] = {"path": t["path"], "role": role, "volume": volume}
            for k in ("start_seconds", "fade_in_seconds", "fade_out_seconds"):
                if k in t:
                    mt[k] = t[k]
            mix_tracks.append(mt)

        data: dict[str, Any] = {
            "operation": "auto_balance",
            "targets_lufs": {k: round(v, 2) for k, v in targets.items()},
            "tracks": report,
            "applied": bool(apply_mix),
        }
        if not apply_mix:
            return ToolResult(success=True, data=data)

        mix_result = self._mix({
            "tracks": mix_tracks,
            "output_path": inputs.get("output_path", "auto_balanced_audio.wav"),
            "normalize": inputs.get("normalize", False),
        })
        if not mix_result.success:
            return mix_result
        data["output"] = mix_result.data.get("output")
        data["normalized"] = mix_result.data.get("normalized")
        return ToolResult(success=True, data=data, artifacts=mix_result.artifacts)

    def _full_mix(self, inputs: dict[str, Any]) -> ToolResult:
        """One-call mix: layer narration tracks, add music with ducking, normalize.

        This is the preferred operation for the compose-director skill.
        It combines mix + duck + normalize in a single FFmpeg filter graph.

        Input format:
            {
                "operation": "full_mix",
                "tracks": [
                    {"path": "narration_s1.mp3", "role": "speech", "start_seconds": 0},
                    {"path": "narration_s2.mp3", "role": "speech", "start_seconds": 10.5},
                    {"path": "music.mp3", "role": "music", "volume": 0.3}
                ],
                "ducking": {
                    "enabled": true,
                    "music_volume_during_speech": 0.15,
                    "attack_ms": 200,
                    "release_ms": 500
                },
                "normalize": true,
                "output_path": "mixed_audio.wav"
            }
        """
        tracks = inputs.get("tracks", [])
        if not tracks:
            return ToolResult(success=False, error="No tracks provided for full_mix")

        output_path = Path(inputs.get("output_path", "full_mix_output.wav"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        normalize = inputs.get("normalize", True)
        ducking = inputs.get("ducking", {"enabled": True})

        speech_tracks = [t for t in tracks if t.get("role") in ("speech", "primary")]
        music_tracks = [t for t in tracks if t.get("role") in ("music", "secondary")]
        sfx_tracks = [t for t in tracks if t.get("role") == "sfx"]
        all_tracks = speech_tracks + music_tracks + sfx_tracks

        if not all_tracks:
            return ToolResult(success=False, error="No valid tracks (need speech/music/sfx roles)")

        # Validate all files exist
        for t in all_tracks:
            if not Path(t["path"]).exists():
                return ToolResult(success=False, error=f"Track not found: {t['path']}")

        # Build FFmpeg inputs and filter graph
        input_args = []
        filter_parts = []

        for i, track in enumerate(all_tracks):
            input_args.extend(["-i", track["path"]])
            filter_parts.append(self._per_track_chain(i, track))

        # If ducking is enabled and we have both speech and music, apply sidechain
        duck_enabled = ducking.get("enabled", True) if isinstance(ducking, dict) else bool(ducking)

        if duck_enabled and speech_tracks and music_tracks:
            # Sidechain-duck the music under the speech. The speech signal is needed
            # BOTH as the sidechain KEY and in the final output mix — and an ffmpeg
            # filter pad can be consumed only ONCE — so mix the speech to a single
            # label and asplit it into a key copy + an output copy. (The previous
            # implementation consumed the speech pad 2–3× and left an orphan
            # [speech_dup], which errored with "output ... unconnected".)
            speech_indices = list(range(len(speech_tracks)))
            speech_labels = "".join(f"[a{i}]" for i in speech_indices)
            if len(speech_tracks) > 1:
                filter_parts.append(
                    f"{speech_labels}amix=inputs={len(speech_tracks)}:duration=longest[speech_all]"
                )
            else:
                filter_parts.append(f"[a{speech_indices[0]}]anull[speech_all]")
            filter_parts.append("[speech_all]asplit=2[speech_key][speech_out]")

            # Mix music tracks to a single label.
            music_start = len(speech_tracks)
            music_indices = list(range(music_start, music_start + len(music_tracks)))
            music_labels = "".join(f"[a{i}]" for i in music_indices)
            if len(music_tracks) > 1:
                filter_parts.append(
                    f"{music_labels}amix=inputs={len(music_tracks)}:duration=longest[music_all]"
                )
            else:
                filter_parts.append(f"[a{music_indices[0]}]anull[music_all]")

            # Sidechain compress: input 0 = music (compressed), input 1 = speech key.
            # attack/release are in MILLISECONDS (ffmpeg range 0.01–2000) — pass the
            # *_ms values through, clamped (a previous /1000 made the attack ~1000× too
            # fast and could fall below the 0.01 floor, erroring the whole filtergraph).
            duck_params = ducking if isinstance(ducking, dict) else {}
            _clamp_ms = lambda v: max(0.01, min(2000.0, float(v)))
            attack = _clamp_ms(duck_params.get("attack_ms", 20))
            release = _clamp_ms(duck_params.get("release_ms", 250))
            music_vol = duck_params.get("music_volume_during_speech", 0.15)
            filter_parts.append(
                f"[music_all][speech_key]sidechaincompress="
                f"threshold=0.02:ratio=9:attack={attack}:release={release}:"
                f"level_sc=1:mix=0.9[ducked_music];"
                f"[ducked_music]volume={music_vol * 3}[music_out]"
            )

            # Final mix: speech + ducked music + any SFX, in one amix.
            sfx_start = len(speech_tracks) + len(music_tracks)
            if sfx_tracks:
                sfx_labels = "".join(f"[a{i}]" for i in range(sfx_start, sfx_start + len(sfx_tracks)))
                filter_parts.append(
                    f"[speech_out][music_out]{sfx_labels}"
                    f"amix=inputs={2 + len(sfx_tracks)}:duration=longest[premix]"
                )
            else:
                filter_parts.append("[speech_out][music_out]amix=inputs=2:duration=longest[premix]")

        else:
            # No ducking: simple amix of all tracks
            all_labels = "".join(f"[a{i}]" for i in range(len(all_tracks)))
            filter_parts.append(
                f"{all_labels}amix=inputs={len(all_tracks)}:duration=longest:dropout_transition=2[premix]"
            )

        # Normalize
        if normalize:
            filter_parts.append("[premix]loudnorm=I=-16:LRA=11:TP=-1.5[out]")
            out_label = "[out]"
        else:
            out_label = "[premix]"

        filter_complex = ";".join(p for p in filter_parts if p)

        cmd = ["ffmpeg", "-y"]
        cmd.extend(input_args)
        cmd.extend(["-filter_complex", filter_complex])
        cmd.extend(["-map", out_label, str(output_path)])

        err = self._run(cmd)
        if err:
            return ToolResult(success=False, error=f"full_mix failed: {err}")

        return ToolResult(
            success=True,
            data={
                "operation": "full_mix",
                "speech_tracks": len(speech_tracks),
                "music_tracks": len(music_tracks),
                "sfx_tracks": len(sfx_tracks),
                "ducking_enabled": duck_enabled,
                "normalized": normalize,
                "output": str(output_path),
            },
            artifacts=[str(output_path)],
        )

    def _segmented_music(self, inputs: dict[str, Any]) -> ToolResult:
        """Mix background music into a video only during specified time segments.

        Uses FFmpeg volume expressions with smooth fades at segment boundaries.
        Music is silent outside the specified segments.

        Input format:
            {
                "operation": "segmented_music",
                "video_path": "assembled.mp4",
                "music_path": "bg_music.mp3",
                "music_volume": 0.20,
                "segments": [
                    {"start": 0, "end": 17.0},
                    {"start": 167.0, "end": 175.0}
                ],
                "fade_duration": 0.5,
                "output_path": "final_with_music.mp4"
            }
        """
        video_path = inputs.get("video_path")
        music_path = inputs.get("music_path")
        output_path = Path(inputs.get("output_path", "segmented_music_output.mp4"))
        segments = inputs.get("segments", [])
        music_volume = inputs.get("music_volume", 0.20)
        fade_dur = inputs.get("fade_duration", 0.5)

        if not video_path or not Path(video_path).exists():
            return ToolResult(success=False, error=f"Video not found: {video_path}")
        if not music_path or not Path(music_path).exists():
            return ToolResult(success=False, error=f"Music not found: {music_path}")
        if not segments:
            return ToolResult(success=False, error="No segments specified")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Get video duration
        dur_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            video_path,
        ]
        total_dur = float(self.run_command(dur_cmd).stdout.strip().split("\n")[0])

        # Build volume expression for each segment with smooth fades
        parts = []
        for seg in sorted(segments, key=lambda s: s["start"]):
            s = seg["start"]
            e = seg["end"]
            fade_in_end = s + fade_dur
            fade_out_start = e - fade_dur
            parts.append(
                f"if(lt(t,{s}),0,"
                f"if(lt(t,{fade_in_end}),{music_volume}*(t-{s})/{fade_dur},"
                f"if(lt(t,{fade_out_start}),{music_volume},"
                f"if(lt(t,{e}),{music_volume}*({e}-t)/{fade_dur},"
                f"0))))"
            )

        vol_expr = "+".join(f"({p})" for p in parts) if len(parts) > 1 else parts[0]

        filter_complex = (
            f"[1:a]atrim=0:{total_dur},asetpts=PTS-STARTPTS,"
            f"volume='{vol_expr}':eval=frame[music_shaped];"
            f"[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[speech];"
            f"[music_shaped]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[music_fmt];"
            f"[speech][music_fmt]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-stream_loop", "-1",
            "-i", music_path,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ]

        self.run_command(cmd)

        if not output_path.exists():
            return ToolResult(success=False, error="No output produced")

        return ToolResult(
            success=True,
            data={
                "operation": "segmented_music",
                "video": video_path,
                "music": music_path,
                "segments": segments,
                "music_volume": music_volume,
                "fade_duration": fade_dur,
                "output": str(output_path),
            },
            artifacts=[str(output_path)],
        )

    # ---- helpers ----

    def _per_track_chain(self, index: int, track: dict[str, Any]) -> str:
        """Build the per-track filter chain '[i:a]...[ai]' for mix/full_mix.

        Fades are applied BEFORE adelay so they act on the actual audio (not
        the silence pad), and afade t=out gets an explicit st= computed from
        the probed track duration — FFmpeg defaults st=0, which fades the
        track to silence over its FIRST N seconds and keeps it muted.
        """
        volume = track.get("volume", 1.0)
        delay_ms = int(track.get("start_seconds", 0) * 1000)
        fade_in = track.get("fade_in_seconds", 0)
        fade_out = track.get("fade_out_seconds", 0)

        filters = []
        if volume != 1.0:
            filters.append(f"volume={volume}")
        if fade_in > 0:
            filters.append(f"afade=t=in:st=0:d={fade_in}")
        if fade_out > 0:
            duration = self._audio_duration(Path(track["path"]))
            if duration is None:
                raise _MixInputError(
                    f"fade_out_seconds requires a probeable duration; "
                    f"could not probe {track['path']}"
                )
            st = max(0.0, round(duration - fade_out, 3))
            filters.append(f"afade=t=out:st={st}:d={fade_out}")
        if delay_ms > 0:
            filters.append(f"adelay={delay_ms}|{delay_ms}")

        if filters:
            return f"[{index}:a]{','.join(filters)}[a{index}]"
        return f"[{index}:a]acopy[a{index}]"

    def _audio_duration(self, path: Path) -> Optional[float]:
        """Container duration in seconds, or None if unprobeable."""
        import subprocess

        try:
            proc = self.run_command(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(path)],
                timeout=30,
            )
            raw = (proc.stdout or "").strip().split("\n")[0]
            return float(raw) if raw and raw != "N/A" else None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
            return None

    def _measure_lufs(self, path: Path) -> Optional[float]:
        """Integrated loudness (LUFS) via the ebur128 summary I: line."""
        import subprocess

        try:
            proc = self.run_command(
                ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
                 "-vn", "-af", "ebur128", "-f", "null", "-"],
                timeout=600,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        stderr = proc.stderr or ""
        # live per-frame lines also contain "I:"; only the final summary counts
        idx = stderr.rfind("Summary:")
        tail = stderr[idx:] if idx >= 0 else stderr
        m = re.search(r"I:\s*(-?(?:\d+(?:\.\d+)?|inf))\s+LUFS", tail)
        if not m or "inf" in m.group(1):
            return None
        return float(m.group(1))

    def _probe_audio_codec(self, path: Path, stream_index: Optional[int] = None) -> Optional[str]:
        import subprocess

        sel = f"a:{stream_index}" if stream_index is not None else "a:0"
        try:
            proc = self.run_command(
                ["ffprobe", "-v", "error", "-select_streams", sel,
                 "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
                timeout=30,
            )
            return (proc.stdout or "").strip().split("\n")[0] or None
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None

    def _run(self, cmd: list[str]) -> Optional[str]:
        """Run ffmpeg; return None on success or a trimmed stderr string on failure.

        BaseTool.run_command uses check=True, so a non-zero exit raises
        CalledProcessError rather than returning a code — catch it and surface
        the stderr tail."""
        import subprocess

        try:
            self.run_command(cmd, timeout=900)
            return None
        except subprocess.CalledProcessError as e:
            return ((e.stderr or "") or "ffmpeg failed").strip()[-500:]
        except subprocess.TimeoutExpired:
            return "ffmpeg timed out."


class _MixInputError(Exception):
    """Bad parameters for an audio op (validated before any ffmpeg spend)."""
