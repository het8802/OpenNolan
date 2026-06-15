"""Voice Ops — mic device listing, voiceover recording, voice effects, and timeline insert.

Instagram Edits' voiceover workflow: record a take, character-ize the voice, drop it onto
the timeline over (ducked) music. Everything is local ffmpeg — no API spend, no upload.

Ops:
  - list_devices  enumerate audio INPUT devices per-OS (avfoundation / dshow / pulse+alsa)
  - record        audio-only mic capture to wav (48k mono default), duration capped at 600s
  - effect        voice effects on an audio OR video file (video stream is `-c:v copy`'d,
                  never re-encoded): presets helium/deep/robot/alien/echo/telephone/whisper,
                  or a custom pitch_semitones shift (-12..12) — all duration-preserving via
                  asetrate + compensating atempo chain
  - insert        place a voice take into a video/audio timeline at `at_seconds` with
                  optional music ducking (adelay + amix, simple volume dip on the bed)

Design (Edits-parity, voiceover gap):
  - `record` captures LIVE microphone audio: it needs OS mic permission for the terminal
    process, the take is inherently non-repeatable, and the agent MUST tell the user that
    recording is starting before running it (see side_effects). It cannot run headless,
    so tests cover input validation + command construction via the pure _record_cmd().
  - Effects are classic DSP (asetrate/atempo, afftfilt, vibrato, aecho, eq), NOT AI voice
    conversion/cloning — use an API voice tool for that.
  - `insert` ducking is a fixed-level volume dip (DUCK_LEVEL) over the voice window, not
    sidechain compression — predictable, no pumping artifacts, but also no pumping aesthetic.
  - `insert` bounds output to the BASE duration (amix duration=first): a voice tail running
    past the base end is truncated, by design.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
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


class VoiceOps(BaseTool):
    name = "voice_ops"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "audio_processing"
    provider = "ffmpeg"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    # effect/insert are deterministic DSP; `record` captures live audio (see docstring)
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["cmd:ffmpeg"]
    install_instructions = "Install FFmpeg: https://ffmpeg.org/download.html"
    agent_skills = ["ffmpeg"]

    OPERATIONS = ("list_devices", "record", "effect", "insert")
    PRESETS = ("helium", "deep", "robot", "alien", "echo", "telephone", "whisper")
    RECORD_MAX_SECONDS = 600
    PITCH_MIN, PITCH_MAX = -12.0, 12.0
    DEFAULT_SAMPLE_RATE = 48000
    DUCK_LEVEL = 0.3  # bed gain while the voice plays (duck_music=true)

    capabilities = list(OPERATIONS)
    supports = {op: True for op in OPERATIONS}
    best_for = [
        "record a voiceover take from the mic, voice-effect it, drop it on the timeline over ducked music",
    ]
    not_good_for = [
        "HDR sources — output is 8-bit SDR; detect with is_hdr_source() and handle HDR per AGENT_GUIDE before using this tool",
        "AI voice conversion / cloning — these are classic DSP effects, not ML voice transfer",
        "sidechain pumping aesthetics — insert ducking is a flat volume dip, not a compressor",
    ]
    fallback_tools: list[str] = []

    input_schema = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {"type": "string", "enum": list(OPERATIONS)},
            "output_path": {"type": "string", "description": "Defaults per-op (record: voice_take_<ts>.wav; effect/insert: {stem}_<op>{ext})"},
            # record
            "device": {
                "type": ["string", "integer"],
                "description": 'record: device index/name. Defaults: macOS ":0", Linux pulse "default"; Windows REQUIRES a dshow name from list_devices.',
            },
            "duration_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": RECORD_MAX_SECONDS, "description": f"record: required, capped at {RECORD_MAX_SECONDS}s"},
            "sample_rate": {"type": "integer", "default": DEFAULT_SAMPLE_RATE, "description": "record: output sample rate (wav, mono)"},
            # effect
            "input_path": {"type": "string", "description": "effect: audio or video file (video stream is copied untouched)"},
            "preset": {"type": "string", "enum": list(PRESETS), "description": "effect: one of preset OR pitch_semitones"},
            "pitch_semitones": {
                "type": "number",
                "minimum": PITCH_MIN,
                "maximum": PITCH_MAX,
                "description": "effect: duration-preserving pitch shift (rate factor 2^(n/12), atempo-compensated)",
            },
            # insert
            "base_path": {"type": "string", "description": "insert: timeline base (video or audio)"},
            "voice_path": {"type": "string", "description": "insert: the voice take to place"},
            "at_seconds": {"type": "number", "minimum": 0, "description": "insert: where the voice starts on the base"},
            "duck_music": {"type": "boolean", "default": True, "description": f"insert: dip base audio to {DUCK_LEVEL}x while the voice plays"},
            "voice_volume": {"type": "number", "default": 1.0, "exclusiveMinimum": 0, "description": "insert: gain on the voice take"},
            # provenance registration (optional)
            "asset_manifest_path": {
                "type": "string",
                "description": "Optional: append the derived file to this asset_manifest (validated, written).",
            },
            "scene_id": {"type": "string", "default": "derived", "description": "scene_id for the registered asset"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=2, ram_mb=512, vram_mb=0, disk_mb=512)
    idempotency_key_fields = [
        "operation", "input_path", "preset", "pitch_semitones",
        "base_path", "voice_path", "at_seconds",
    ]
    side_effects = [
        "record: captures LIVE microphone audio — the agent MUST tell the user recording is starting BEFORE running this op",
        "record: requires OS microphone permission for the terminal/ffmpeg process (macOS: System Settings > Privacy & Security > Microphone)",
        "writes a derived audio/video file",
        "may append to an asset_manifest",
    ]
    user_visible_verification = [
        "Play the output; confirm the voice sounds as intended and lands at the right time",
    ]

    # ---- execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        op = inputs.get("operation")
        if op not in self.OPERATIONS:
            return ToolResult(success=False, error=f"operation must be one of {self.OPERATIONS}.")
        try:
            if op == "list_devices":
                return self._list_devices()
            if op == "record":
                return self._record(inputs)
            if op == "effect":
                return self._effect(inputs)
            return self._insert(inputs)
        except _OpInputError as e:
            return ToolResult(success=False, error=str(e))

    # ---- list_devices ----

    def _list_devices(self) -> ToolResult:
        system = platform.system()
        if system == "Darwin":
            if shutil.which("ffmpeg") is None:
                return ToolResult(success=False, error="ffmpeg not found on PATH. " + self.install_instructions)
            stderr = self._capture_stderr(
                ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""]
            )
            devices = self._parse_avfoundation_devices(stderr)
        elif system == "Windows":
            if shutil.which("ffmpeg") is None:
                return ToolResult(success=False, error="ffmpeg not found on PATH. " + self.install_instructions)
            stderr = self._capture_stderr(
                ["ffmpeg", "-hide_banner", "-f", "dshow", "-list_devices", "true", "-i", "dummy"]
            )
            devices = self._parse_dshow_devices(stderr)
        else:
            devices = self._linux_devices()
        return ToolResult(success=True, data={"platform": system, "devices": devices})

    @staticmethod
    def _parse_avfoundation_devices(stderr: str) -> list[dict[str, Any]]:
        """Parse `-f avfoundation -list_devices true` stderr; audio section only."""
        devices: list[dict[str, Any]] = []
        in_audio = False
        for line in stderr.splitlines():
            if "AVFoundation audio devices" in line:
                in_audio = True
                continue
            if "AVFoundation video devices" in line:
                in_audio = False
                continue
            if not in_audio:
                continue
            m = re.search(r"\[(\d+)\]\s+(.+?)\s*$", line)
            if m:
                devices.append({"index": int(m.group(1)), "name": m.group(2)})
        return devices

    @staticmethod
    def _parse_dshow_devices(stderr: str) -> list[dict[str, Any]]:
        """Parse `-f dshow -list_devices true` stderr.

        Modern ffmpeg tags each line with (audio)/(video); older builds print
        "DirectShow audio devices" section headers with untagged quoted names."""
        devices: list[dict[str, Any]] = []
        in_audio_section = False
        for line in stderr.splitlines():
            if "DirectShow audio devices" in line:
                in_audio_section = True
                continue
            if "DirectShow video devices" in line:
                in_audio_section = False
                continue
            if "Alternative name" in line:
                continue
            m = re.search(r'"([^"]+)"\s*\(audio\)', line)
            if m:
                devices.append({"index": len(devices), "name": m.group(1)})
                continue
            if in_audio_section:
                m = re.search(r'"([^"]+)"', line)
                if m:
                    devices.append({"index": len(devices), "name": m.group(1)})
        return devices

    def _linux_devices(self) -> list[dict[str, Any]]:
        if shutil.which("pactl"):
            try:
                proc = self.run_command(["pactl", "list", "short", "sources"], timeout=15)
                devices = []
                for line in (proc.stdout or "").splitlines():
                    parts = line.split("\t")
                    # .monitor sources are output loopbacks, not microphones
                    if len(parts) >= 2 and not parts[1].endswith(".monitor"):
                        devices.append({"index": int(parts[0]), "name": parts[1]})
                return devices
            except Exception:
                pass
        if shutil.which("arecord"):
            try:
                proc = self.run_command(["arecord", "-l"], timeout=15)
                devices = []
                for m in re.finditer(r"card (\d+): ([^\[]+)\[[^\]]*\], device (\d+)", proc.stdout or ""):
                    devices.append({
                        "index": len(devices),
                        "name": f"hw:{m.group(1)},{m.group(3)} ({m.group(2).strip()})",
                    })
                return devices
            except Exception:
                pass
        raise _OpInputError("could not enumerate audio inputs: need pactl (PulseAudio) or arecord (ALSA) on Linux.")

    def _capture_stderr(self, cmd: list[str]) -> str:
        """ffmpeg -list_devices exits non-zero by design; the listing is on stderr either way."""
        import subprocess

        try:
            proc = self.run_command(cmd, timeout=30)
            return proc.stderr or ""
        except subprocess.CalledProcessError as e:
            return e.stderr or ""
        except subprocess.TimeoutExpired:
            raise _OpInputError("ffmpeg device listing timed out.")

    # ---- record ----

    def _record(self, inputs: dict[str, Any]) -> ToolResult:
        dur = inputs.get("duration_seconds")
        if not isinstance(dur, (int, float)) or isinstance(dur, bool) or dur <= 0:
            raise _OpInputError("record requires duration_seconds > 0.")
        if dur > self.RECORD_MAX_SECONDS:
            raise _OpInputError(f"record is capped at {self.RECORD_MAX_SECONDS}s; got {dur}s.")
        sr = inputs.get("sample_rate", self.DEFAULT_SAMPLE_RATE)
        if not isinstance(sr, int) or isinstance(sr, bool) or not (8000 <= sr <= 192000):
            raise _OpInputError(f"sample_rate must be an int in [8000, 192000]; got {sr!r}.")
        out_path = Path(
            inputs.get("output_path") or f"voice_take_{time.strftime('%Y%m%d_%H%M%S')}.wav"
        )
        system = platform.system()
        cmd = self._record_cmd(system, inputs.get("device"), dur, out_path, sr)

        if shutil.which("ffmpeg") is None:
            return ToolResult(success=False, error="ffmpeg not found on PATH. " + self.install_instructions)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        err = self._run(cmd, timeout=int(dur) + 60)
        if err:
            return ToolResult(
                success=False,
                error=(
                    f"recording failed: {err} — if this is a permission error, grant microphone "
                    "access to your terminal (macOS: System Settings > Privacy & Security > Microphone)."
                ),
            )
        if not out_path.exists() or out_path.stat().st_size == 0:
            return ToolResult(success=False, error="recording produced no output (is the mic accessible?).")
        return ToolResult(
            success=True,
            data={
                "operation": "record",
                "output_path": str(out_path),
                "duration_seconds": self._probe_duration(out_path),
                "sample_rate": sr,
                "device": cmd[cmd.index("-i") + 1],
            },
            artifacts=[str(out_path)],
        )

    @classmethod
    def _record_cmd(
        cls,
        system: str,
        device: Any,
        duration_seconds: float,
        output_path: Path | str,
        sample_rate: int,
    ) -> list[str]:
        """Pure command builder, exposed for tests (recording itself cannot run headless)."""
        if system == "Darwin":
            dev = str(device) if device not in (None, "") else ":0"
            # avfoundation spec is "video:audio"; audio-only needs the leading colon.
            # Accept bare index (0 -> ":0") or device name; pass full specs through.
            if ":" not in dev:
                dev = f":{dev}"
            in_args = ["-f", "avfoundation", "-i", dev]
        elif system == "Windows":
            if device in (None, ""):
                raise _OpInputError(
                    "record on Windows requires device (a dshow device name from list_devices)."
                )
            dev = str(device)
            if not dev.startswith("audio="):
                dev = f"audio={dev}"
            in_args = ["-f", "dshow", "-i", dev]
        else:
            dev = str(device) if device not in (None, "") else "default"
            fmt = "alsa" if dev.startswith(("hw:", "plughw:")) else "pulse"
            in_args = ["-f", fmt, "-i", dev]
        return [
            "ffmpeg", "-y", *in_args,
            "-t", str(duration_seconds),
            "-ac", "1", "-ar", str(sample_rate),
            str(output_path),
        ]

    # ---- effect ----

    def _effect(self, inputs: dict[str, Any]) -> ToolResult:
        src = inputs.get("input_path")
        if not src:
            raise _OpInputError("effect requires input_path.")
        preset = inputs.get("preset")
        pitch = inputs.get("pitch_semitones")
        if (preset is None) == (pitch is None):
            raise _OpInputError("effect requires exactly one of preset or pitch_semitones.")
        if preset is not None and preset not in self.PRESETS:
            raise _OpInputError(f"preset must be one of {self.PRESETS}.")
        if pitch is not None:
            if not isinstance(pitch, (int, float)) or isinstance(pitch, bool) or not (
                self.PITCH_MIN <= pitch <= self.PITCH_MAX
            ):
                raise _OpInputError(
                    f"pitch_semitones must be in [{self.PITCH_MIN}, {self.PITCH_MAX}]; got {pitch!r}."
                )
        src_path = Path(src)
        if not src_path.exists():
            raise _OpInputError(f"input not found: {src}")

        if shutil.which("ffmpeg") is None:
            return ToolResult(success=False, error="ffmpeg not found on PATH. " + self.install_instructions)
        if not self._has_audio(src_path):
            raise _OpInputError("effect needs an audio stream; the input has none.")
        has_video = self._has_video(src_path)
        sr = self._probe_sample_rate(src_path) or self.DEFAULT_SAMPLE_RATE
        chain = self._preset_chain(preset, sr) if preset else self._pitch_chain(float(pitch), sr)
        label = preset or f"pitch{float(pitch):+g}"
        out_path = Path(
            inputs.get("output_path")
            or src_path.with_name(f"{src_path.stem}_voice_{label}{src_path.suffix or '.wav'}")
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = ["ffmpeg", "-y", "-i", str(src_path), "-af", chain]
        if has_video:
            cmd += ["-c:v", "copy"]
        cmd.append(str(out_path))
        err = self._run(cmd)
        if err:
            return ToolResult(success=False, error=err)
        return self._finish(
            "effect", src_path, out_path, inputs,
            extra={"preset": preset, "pitch_semitones": pitch, "filter": chain},
            asset_type="video" if has_video else "audio",
        )

    @classmethod
    def _preset_chain(cls, preset: str, sample_rate: int) -> str:
        """Audio filter chain for a named preset at the source's sample rate."""
        sr = int(sample_rate)
        if preset == "helium":
            return f"asetrate={int(round(sr * 1.35))},aresample={sr},{cls._atempo_chain(1 / 1.35)}"
        if preset == "deep":
            return f"asetrate={int(round(sr * 0.8))},aresample={sr},{cls._atempo_chain(1 / 0.8)}"
        if preset == "robot":
            # classic phase-zero robot voice from the ffmpeg afftfilt docs
            return (
                "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)'"
                ":win_size=512:overlap=0.75"
            )
        if preset == "alien":
            return f"{cls._pitch_chain(3.0, sr)},vibrato=f=6:d=0.5"
        if preset == "echo":
            return "aecho=0.8:0.7:60|120:0.4|0.2"
        if preset == "telephone":
            return "highpass=f=300,lowpass=f=3400,acrusher=bits=10:mode=log:aa=1"
        if preset == "whisper":
            return "afftdn=nf=-25,highpass=f=150,treble=g=4,volume=0.5"
        raise _OpInputError(f"unknown preset {preset!r}.")

    @classmethod
    def _pitch_chain(cls, semitones: float, sample_rate: int) -> str:
        """Duration-preserving pitch shift: rate factor 2^(n/12), atempo compensates 1/factor."""
        factor = 2.0 ** (float(semitones) / 12.0)
        sr = int(sample_rate)
        return f"asetrate={int(round(sr * factor))},aresample={sr},{cls._atempo_chain(1.0 / factor)}"

    @classmethod
    def _atempo_chain(cls, factor: float) -> str:
        """atempo only accepts 0.5-2.0 per instance; chain to cover wider factors."""
        remaining = float(factor)
        parts: list[str] = []
        while remaining > 2.0:
            parts.append("atempo=2.0")
            remaining /= 2.0
        while remaining < 0.5:
            parts.append("atempo=0.5")
            remaining /= 0.5
        parts.append(f"atempo={round(remaining, 6)}")
        return ",".join(parts)

    # ---- insert ----

    def _insert(self, inputs: dict[str, Any]) -> ToolResult:
        base = inputs.get("base_path")
        voice = inputs.get("voice_path")
        if not base or not voice:
            raise _OpInputError("insert requires base_path and voice_path.")
        at = inputs.get("at_seconds")
        if not isinstance(at, (int, float)) or isinstance(at, bool) or at < 0:
            raise _OpInputError("insert requires at_seconds >= 0.")
        vv = inputs.get("voice_volume", 1.0)
        if not isinstance(vv, (int, float)) or isinstance(vv, bool) or vv <= 0:
            raise _OpInputError("insert requires voice_volume > 0.")
        duck = bool(inputs.get("duck_music", True))
        base_path, voice_path = Path(base), Path(voice)
        if not base_path.exists():
            raise _OpInputError(f"base not found: {base}")
        if not voice_path.exists():
            raise _OpInputError(f"voice not found: {voice}")

        if shutil.which("ffmpeg") is None:
            return ToolResult(success=False, error="ffmpeg not found on PATH. " + self.install_instructions)
        if not self._has_audio(voice_path):
            raise _OpInputError("voice_path has no audio stream.")
        base_dur = self._probe_duration(base_path)
        if not base_dur:
            raise _OpInputError(f"could not probe base duration: {base}")
        if at >= base_dur:
            raise _OpInputError(f"at_seconds ({at}) is past the base end ({base_dur:.2f}s).")
        voice_dur = self._probe_duration(voice_path) or 0.0
        base_has_video = self._has_video(base_path)
        base_has_audio = self._has_audio(base_path)

        # bed = base audio (ducked during the voice window) or silence sized to the base;
        # amix duration=first then bounds the output to the bed == base duration.
        parts: list[str] = []
        if base_has_audio:
            if duck:
                duck_end = min(at + voice_dur, base_dur)
                parts.append(
                    f"[0:a]volume=enable='between(t,{at},{duck_end})':volume={self.DUCK_LEVEL}[bed]"
                )
            else:
                parts.append("[0:a]anull[bed]")
        else:
            parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate={self.DEFAULT_SAMPLE_RATE},"
                f"atrim=0:{base_dur}[bed]"
            )
        delay_ms = int(round(at * 1000))
        parts.append(f"[1:a]volume={vv},adelay={delay_ms}:all=1[vo]")
        parts.append("[bed][vo]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
        fc = ";".join(parts)

        out_path = Path(
            inputs.get("output_path")
            or base_path.with_name(f"{base_path.stem}_insert{base_path.suffix or '.wav'}")
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-y", "-i", str(base_path), "-i", str(voice_path), "-filter_complex", fc]
        if base_has_video:
            cmd += ["-map", "0:v", "-map", "[a]", "-c:v", "copy"]
        else:
            cmd += ["-map", "[a]"]
        cmd.append(str(out_path))
        err = self._run(cmd)
        if err:
            return ToolResult(success=False, error=err)
        return self._finish(
            "insert", base_path, out_path, inputs,
            extra={"voice": str(voice_path), "at_seconds": at, "duck_music": duck},
            asset_type="video" if base_has_video else "audio",
        )

    # ---- shared result + asset_manifest registration ----

    def _finish(
        self, op: str, src: Path, out: Path, inputs: dict[str, Any],
        *, extra: dict[str, Any], asset_type: str,
    ) -> ToolResult:
        if not out.exists() or out.stat().st_size == 0:
            return ToolResult(success=False, error=f"{op} produced no output.")
        duration = self._probe_duration(out)
        data: dict[str, Any] = {
            "operation": op,
            "input": str(src),
            "output_path": str(out),
            "duration_seconds": duration,
            **{k: v for k, v in extra.items() if v is not None},
        }
        artifacts = [str(out)]
        am_path = inputs.get("asset_manifest_path")
        if am_path:
            reg_err = self._register_asset(Path(am_path), op, src, out, inputs, duration, asset_type)
            if reg_err:
                # the derived file exists and is valid; only the registration failed
                data["asset_manifest_warning"] = reg_err
            else:
                data["asset_manifest_path"] = str(am_path)
                artifacts.append(str(am_path))
        return ToolResult(success=True, data=data, artifacts=artifacts)

    def _register_asset(
        self, path: Path, op: str, src: Path, out: Path,
        inputs: dict[str, Any], duration: Optional[float], asset_type: str,
    ) -> Optional[str]:
        """Append the derived file to an asset_manifest with provenance, validate, write back.
        Returns an error string on failure (manifest left untouched), else None."""
        if not path.exists():
            return f"asset_manifest_path not found: {path}"
        try:
            doc = json.loads(path.read_text())
        except Exception as e:
            return f"could not read asset_manifest: {e}"
        if not isinstance(doc, dict) or not isinstance(doc.get("assets"), list):
            return "asset_manifest is not a valid manifest object with an assets[] list."

        params = {
            k: inputs.get(k)
            for k in ("preset", "pitch_semitones", "at_seconds", "voice_volume")
            if inputs.get(k) is not None
        }
        entry = {
            "id": f"voice-{op}-{len(doc['assets']) + 1}",
            "type": asset_type,
            "path": str(out),
            "source_tool": "voice_ops",
            "scene_id": str(inputs.get("scene_id", "derived")),
            "subtype": op,
            "generation_summary": f"voice_ops {op}({params}) from {src.name}",
            "format": out.suffix.lstrip(".") or "wav",
        }
        if isinstance(duration, (int, float)):
            entry["duration_seconds"] = round(float(duration), 4)
        doc["assets"].append(entry)
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("asset_manifest", doc)
        except Exception as e:
            return f"derived-asset entry did not validate against asset_manifest schema: {e}"
        self._write_json(path, doc)
        return None

    # ---- helpers ----

    def _run(self, cmd: list[str], timeout: int = 900) -> Optional[str]:
        """Run ffmpeg; return None on success or a trimmed stderr string on failure."""
        import subprocess

        try:
            self.run_command(cmd, timeout=timeout)
            return None
        except subprocess.CalledProcessError as e:
            return ((e.stderr or "") or "ffmpeg failed").strip()[-500:]
        except subprocess.TimeoutExpired:
            return "ffmpeg timed out."

    def _has_audio(self, path: Path) -> bool:
        return self._has_stream(path, "a")

    def _has_video(self, path: Path) -> bool:
        return self._has_stream(path, "v")

    def _has_stream(self, path: Path, kind: str) -> bool:
        import subprocess

        try:
            proc = self.run_command(
                ["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries",
                 "stream=index", "-of", "csv=p=0", str(path)],
                timeout=30,
            )
            return bool((proc.stdout or "").strip())
        except subprocess.CalledProcessError:
            return False

    def _probe_duration(self, path: Path) -> Optional[float]:
        import subprocess

        try:
            proc = self.run_command(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                timeout=30,
            )
            raw = (proc.stdout or "").strip()
            return float(raw) if raw and raw != "N/A" else None
        except (subprocess.CalledProcessError, ValueError):
            return None

    def _probe_sample_rate(self, path: Path) -> Optional[int]:
        import subprocess

        try:
            proc = self.run_command(
                ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                 "stream=sample_rate", "-of", "default=nw=1:nk=1", str(path)],
                timeout=30,
            )
            raw = (proc.stdout or "").strip()
            return int(raw) if raw.isdigit() else None
        except subprocess.CalledProcessError:
            return None

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)


class _OpInputError(Exception):
    """Bad parameters for a voice op (validated before any ffmpeg spend)."""
