"""Beat Cutter — snap a sequence of clips to the beats of a music track.

Detects beats in an audio track (librosa) and lays an ordered list of clips onto those
beats so every cut lands on the rhythm — the classic beat-synced montage. This is a
PLANNING tool: it emits a beat-aligned `cuts[]` list compatible with
schemas/artifacts/edit_decisions.schema.json. It does NOT render video (compose does that).

Design (Edits-parity Wave 3, /plan-eng-review 2026-06-06):
  - Beat detection is BUILT here via librosa.beat.beat_track. `audio_energy` is an ebur128
    loudness profiler and has NO beat detection — do not confuse the two.
  - librosa is an OPTIONAL dependency (heavy numba/llvmlite stack). It is only needed for
    the detection path; if you pass pre-computed `beat_times`, librosa is not imported.
    Install with `pip install -r requirements-audio.txt`.
  - SPEECH-SAFE BY DEFAULT. Shredding a montage to the beat over a talking voice destroys
    comprehension. In speech_safe mode a cut is never placed inside a `protected_ranges`
    span (your narration/dialogue). Use music_led only when the audio is music, not speech.
  - SILENT/AMBIENT audio -> no beats -> fall back to even spacing (with a warning), never
    a hard failure.
  - Emits cuts compatible with edit_decisions; can also merge them into an existing
    edit_decisions artifact (read -> replace cuts -> validate -> write).

         beats:   |    |    |    |    |        (from librosa, or pre-supplied, or even fallback)
         clips:   [ A ][ B  ][ C ][ D ]        each clip's duration = its beat interval
                  ^cut ^cut  ^cut ^cut          every cut lands on a beat
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


class BeatCutter(BaseTool):
    name = "beat_cutter"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "video_post"
    provider = "librosa"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = ["python:librosa"]
    install_instructions = (
        "Beat detection needs librosa (optional, heavy):\n"
        "  pip install -r requirements-audio.txt\n"
        "  (or pass pre-computed beat_times to skip librosa entirely)"
    )
    agent_skills: list[str] = []

    capabilities = ["beat_detection", "beat_synced_cutting"]
    supports = {
        "beat_detection": True,
        "speech_safe": True,
        "pre_supplied_beats": True,
        "merge_into_edit_decisions": True,
    }
    best_for = [
        "music-led montages where every cut should land on the beat",
        "turning an ordered clip list + a track into a rhythm-synced cut plan",
    ]
    not_good_for = [
        "talking-head edits where cuts must follow speech, not music (use speech_safe + protected_ranges)",
        "rendering video — this only plans cuts; compose renders them",
    ]
    fallback_tools: list[str] = []

    MIN_CUT_SECONDS = 0.2  # floor; merge beats until an interval is at least this long
    DEFAULT_CLIP_SECONDS = 1.5  # used by the even-spacing fallback when no audio duration known

    input_schema = {
        "type": "object",
        "required": ["clips"],
        "properties": {
            "clips": {
                "type": "array",
                "description": "Ordered clips to lay on the beats. Each becomes one cut.",
                "items": {
                    "type": "object",
                    "required": ["source"],
                    "properties": {
                        "source": {"type": "string", "description": "Source file path or asset id"},
                        "in_seconds": {"type": "number", "minimum": 0, "default": 0},
                        "id": {"type": "string", "description": "Optional cut id (auto if omitted)"},
                    },
                },
            },
            "audio_path": {
                "type": "string",
                "description": "Music track to detect beats from (required unless beat_times given).",
            },
            "beat_times": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Pre-computed beat timestamps (seconds). If given, librosa is not used.",
            },
            "beats_per_cut": {
                "type": "integer",
                "minimum": 1,
                "default": 1,
                "description": "Cut every N beats (1 = every beat, 2 = every other, 4 = per bar).",
            },
            "start_seconds": {
                "type": "number",
                "minimum": 0,
                "default": 0,
                "description": "Ignore beats before this time (e.g. an intro).",
            },
            "mode": {
                "type": "string",
                "enum": ["speech_safe", "music_led"],
                "default": "speech_safe",
                "description": "speech_safe: never cut inside protected_ranges. music_led: snap freely.",
            },
            "protected_ranges": {
                "type": "array",
                "items": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                "description": "[[start,end], ...] spans (speech) where cuts must NOT land (speech_safe).",
            },
            "transition": {
                "type": "object",
                "description": "Optional transition applied entering each cut.",
                "properties": {
                    "type": {"type": "string", "default": "cut"},
                    "duration": {"type": "number", "minimum": 0, "default": 0},
                },
            },
            "edit_decisions_path": {
                "type": "string",
                "description": "Optional: merge the beat-aligned cuts into this existing edit_decisions artifact (validated, written back).",
            },
            "output_path": {
                "type": "string",
                "description": "Optional: write a cuts-only JSON ({cuts, beat_times, tempo}) here.",
            },
        },
    }

    resource_profile = ResourceProfile(cpu_cores=2, ram_mb=1024, vram_mb=0, disk_mb=100)
    idempotency_key_fields = ["audio_path", "clips", "beats_per_cut", "mode"]
    side_effects = ["may write/merge an edit_decisions artifact or a cuts JSON"]
    user_visible_verification = [
        "Play the edit against the track — cuts should land on the beat",
        "In speech_safe mode, confirm no cut chops a spoken word mid-phrase",
    ]

    # ---- execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        clips = inputs.get("clips") or []
        if not isinstance(clips, list) or not clips:
            return ToolResult(success=False, error="clips (a non-empty ordered list) is required.")
        for i, c in enumerate(clips):
            if not isinstance(c, dict) or not c.get("source"):
                return ToolResult(success=False, error=f"clips[{i}] must have a 'source'.")

        beats_per_cut = max(1, int(inputs.get("beats_per_cut", 1)))
        start_seconds = float(inputs.get("start_seconds", 0) or 0)
        mode = inputs.get("mode", "speech_safe")
        protected = self._normalize_ranges(inputs.get("protected_ranges"))
        warnings: list[str] = []

        # --- 1. obtain beat times ---
        beat_times = inputs.get("beat_times")
        tempo: Optional[float] = None
        if beat_times:
            beat_times = sorted(float(t) for t in beat_times)
        else:
            audio_path = inputs.get("audio_path")
            if not audio_path:
                return ToolResult(
                    success=False,
                    error="Provide audio_path (to detect beats) or beat_times (pre-computed).",
                )
            if not Path(audio_path).exists():
                return ToolResult(success=False, error=f"audio_path not found: {audio_path}")
            try:
                beat_times, tempo = self._detect_beats(audio_path)
            except _LibrosaMissing:
                return ToolResult(
                    success=False,
                    error=(
                        "librosa is not installed, so beats can't be detected. "
                        + self.install_instructions
                    ),
                )
            except Exception as e:
                return ToolResult(success=False, error=f"Beat detection failed: {e}")

        # --- 2. choose cut boundaries (beats, spacing, speech-safe filtering) ---
        boundaries, fellback = self._cut_boundaries(
            beat_times, beats_per_cut, start_seconds, mode, protected, inputs, len(clips), warnings
        )
        if fellback:
            warnings.append(
                "No usable beats detected (silent/ambient track?) — fell back to even spacing."
            )

        # --- 3. lay clips onto boundaries ---
        cuts = self._build_cuts(clips, boundaries, inputs.get("transition"), warnings)

        data: dict[str, Any] = {
            "cuts": cuts,
            "n_cuts": len(cuts),
            "beat_times": beat_times,
            "tempo": tempo,
            "mode": mode,
            "beats_per_cut": beats_per_cut,
        }
        if warnings:
            data["warnings"] = warnings

        artifacts: list[str] = []

        # --- 4a. optional: merge into an existing edit_decisions artifact ---
        ed_path = inputs.get("edit_decisions_path")
        if ed_path:
            merged_err = self._merge_into_edit_decisions(Path(ed_path), cuts)
            if merged_err:
                return ToolResult(success=False, error=merged_err, data=data)
            artifacts.append(str(ed_path))
            data["edit_decisions_path"] = str(ed_path)

        # --- 4b. optional: write a cuts-only JSON ---
        out_path = inputs.get("output_path")
        if out_path:
            self._write_json(Path(out_path), {
                "cuts": cuts, "beat_times": beat_times, "tempo": tempo, "mode": mode,
            })
            artifacts.append(str(out_path))

        return ToolResult(success=True, data=data, artifacts=artifacts)

    # ---- beat detection ----

    def _detect_beats(self, audio_path: str) -> tuple[list[float], Optional[float]]:
        """librosa beat tracking -> (beat_times_seconds, tempo_bpm). Raises _LibrosaMissing
        if librosa is unavailable so the caller can give a clean install message."""
        try:
            import librosa  # noqa
            import numpy as np
        except Exception as e:  # ImportError or a broken numba/llvmlite install
            raise _LibrosaMissing(str(e))
        y, sr = librosa.load(audio_path, mono=True)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        # librosa 0.11 + numpy 2.x return `tempo` as a 1-D array, not a scalar — float()
        # on a non-0-d array raises. Flatten + take the first element robustly.
        tempo_arr = np.asarray(tempo).flatten()
        tempo_val = float(tempo_arr[0]) if tempo_arr.size else None
        return [round(float(t), 4) for t in np.asarray(beat_times).flatten()], tempo_val

    # ---- boundary selection ----

    def _cut_boundaries(
        self,
        beat_times: list[float],
        beats_per_cut: int,
        start_seconds: float,
        mode: str,
        protected: list[tuple[float, float]],
        inputs: dict[str, Any],
        n_clips: int,
        warnings: list[str],
    ) -> tuple[list[float], bool]:
        """Return ordered cut-boundary timestamps (>= start_seconds), one more than the
        number of cuts we can place. Falls back to even spacing if beats are unusable."""
        usable = [t for t in (beat_times or []) if t >= start_seconds]
        # take every beats_per_cut-th beat
        picked = usable[::beats_per_cut] if usable else []

        # speech_safe: drop boundaries that fall inside a protected (speech) span
        if mode == "speech_safe" and protected:
            kept = [t for t in picked if not self._in_any(t, protected)]
            if len(kept) < len(picked):
                warnings.append(
                    f"speech_safe: dropped {len(picked) - len(kept)} beat(s) that fell inside "
                    f"protected speech ranges."
                )
            picked = kept

        # enforce a minimum interval (merge beats that are too close)
        picked = self._enforce_min_interval(picked, self.MIN_CUT_SECONDS)

        # We need n_clips+1 boundaries to give every clip a start and end.
        if len(picked) >= 2:
            return picked, False

        # --- fallback: even spacing ---
        total = self._audio_duration(inputs.get("audio_path"))
        per = (total / n_clips) if (total and n_clips) else self.DEFAULT_CLIP_SECONDS
        per = max(per, self.MIN_CUT_SECONDS)
        boundaries = [round(start_seconds + i * per, 4) for i in range(n_clips + 1)]
        return boundaries, True

    @staticmethod
    def _enforce_min_interval(times: list[float], min_gap: float) -> list[float]:
        if not times:
            return times
        out = [times[0]]
        for t in times[1:]:
            if t - out[-1] >= min_gap:
                out.append(t)
        return out

    @staticmethod
    def _in_any(t: float, ranges: list[tuple[float, float]]) -> bool:
        return any(a <= t <= b for a, b in ranges)

    @staticmethod
    def _normalize_ranges(raw: Any) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    a, b = float(item[0]), float(item[1])
                    out.append((min(a, b), max(a, b)))
        return out

    # ---- cut building ----

    def _build_cuts(
        self,
        clips: list[dict[str, Any]],
        boundaries: list[float],
        transition: Optional[dict[str, Any]],
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        """Assign each clip a duration equal to its beat interval. If there are fewer
        intervals than clips, the leftover clips get the min duration (and a warning)."""
        cuts: list[dict[str, Any]] = []
        intervals = [
            (boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1)
        ]
        if len(intervals) < len(clips):
            warnings.append(
                f"Only {len(intervals)} beat interval(s) for {len(clips)} clip(s); "
                f"trailing clips use the minimum cut length."
            )
        ttype = (transition or {}).get("type", "cut")
        tdur = float((transition or {}).get("duration", 0) or 0)

        for i, clip in enumerate(clips):
            if i < len(intervals):
                dur = round(intervals[i][1] - intervals[i][0], 4)
            else:
                dur = self.MIN_CUT_SECONDS
            in_s = float(clip.get("in_seconds", 0) or 0)
            cut: dict[str, Any] = {
                "id": str(clip.get("id") or f"beat-cut-{i + 1}"),
                "source": clip["source"],
                "in_seconds": round(in_s, 4),
                "out_seconds": round(in_s + max(dur, self.MIN_CUT_SECONDS), 4),
            }
            if ttype and ttype != "cut":
                cut["transition_in"] = ttype
                if tdur > 0:
                    cut["transition_duration"] = tdur
            cut["reason"] = "beat-synced"
            cuts.append(cut)
        return cuts

    # ---- edit_decisions merge ----

    def _merge_into_edit_decisions(self, path: Path, cuts: list[dict[str, Any]]) -> Optional[str]:
        """Read an existing edit_decisions, replace its cuts, validate, write back. Returns
        an error string on failure (so we never write a corrupt artifact), else None."""
        if not path.exists():
            return f"edit_decisions_path not found: {path}"
        try:
            doc = json.loads(path.read_text())
        except Exception as e:
            return f"Could not read edit_decisions at {path}: {e}"
        if not isinstance(doc, dict):
            return f"edit_decisions at {path} is not a JSON object."
        doc["cuts"] = cuts
        try:
            from schemas.artifacts import validate_artifact

            validate_artifact("edit_decisions", doc)
        except Exception as e:
            return f"Beat-aligned cuts did not validate against edit_decisions schema: {e}"
        self._write_json(path, doc)
        return None

    # ---- helpers ----

    def _audio_duration(self, audio_path: Optional[str]) -> Optional[float]:
        if not audio_path:
            return None
        try:
            from tools.video._shared import probe_output

            info = probe_output(Path(audio_path))
            d = info.get("duration_seconds")
            return float(d) if isinstance(d, (int, float)) and d > 0 else None
        except Exception:
            return None

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)


class _LibrosaMissing(Exception):
    """librosa (or its native deps) could not be imported."""
