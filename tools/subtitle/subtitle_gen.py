"""Subtitle generation tool.

Converts word-level timestamps from the transcriber into SRT, VTT,
or caption JSON formats. Pure Python — no external dependencies beyond
the standard library.

Censoring (Edits parity — transcript censoring):
  `censor_words=[...]` masks every case-insensitive whole-word occurrence in
  the cues (first char + asterisks: "damn" -> "d***") and emits
  data.mute_ranges = [{start, end, word}] derived from the word-level
  timestamps (padded ±MUTE_PAD_SECONDS, overlaps merged), plus
  data.censor_summary. This tool does NOT render bleeps or mute audio —
  single responsibility. Feed mute_ranges to motion_ops op=segment_volume
  (volume: 0) to mute, or overlay a bleep from assets/sfx via audio_mixer.

Documented censor limitations:
  - corrections run BEFORE censoring, so a corrected word is censorable.
  - segments without word-level timestamps still get their text masked, but
    no mute range can be derived (counted in censor_summary.unmuted_text_matches).
  - matching mirrors corrections: per-token whole-word on the
    punctuation-stripped form. Phrases and sub-word matches ("damnit") are
    out of scope.
"""

from __future__ import annotations

import json
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
    ToolTier,
)


class SubtitleGen(BaseTool):
    name = "subtitle_gen"
    version = "0.2.0"
    tier = ToolTier.CORE
    capability = "subtitle"
    provider = "opennolan"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = []  # pure Python
    install_instructions = "No external dependencies required."
    agent_skills = ["remotion-best-practices"]

    MUTE_PAD_SECONDS = 0.05
    _TRAILING_PUNCT = ".,!?;:'\""

    capabilities = [
        "generate_srt", "generate_vtt", "generate_caption_json", "censor_transcript",
    ]
    best_for = [
        "word-timed SRT/VTT/caption-JSON from transcriber segments (karaoke/word-by-word)",
        "fixing common ASR misrecognitions via the corrections dict before render",
        "transcript censoring: censor_words masks cue text (damn -> d***) and emits "
        "data.mute_ranges — feed mute_ranges to motion_ops op=segment_volume (volume:0) "
        "to mute, or overlay a bleep from assets/sfx via audio_mixer",
    ]
    not_good_for = [
        "bleep/mute audio rendering — this tool only emits mute_ranges; the agent "
        "composes the audio change with motion_ops segment_volume or audio_mixer",
        "burning subtitles into pixels — use video_compose for subtitle burn",
    ]

    input_schema = {
        "type": "object",
        "required": ["segments"],
        "properties": {
            "segments": {
                "type": "array",
                "description": "Transcript segments from transcriber (with words and timestamps)",
            },
            "format": {
                "type": "string",
                "enum": ["srt", "vtt", "json"],
                "default": "srt",
            },
            "output_path": {"type": "string"},
            "max_chars_per_line": {"type": "integer", "default": 42},
            "max_words_per_cue": {"type": "integer", "default": 8},
            "highlight_style": {
                "type": "string",
                "enum": ["none", "word_by_word", "karaoke"],
                "default": "none",
            },
            "corrections": {
                "type": "object",
                "description": (
                    "Dictionary of word corrections for common ASR misrecognitions. "
                    "Keys are the wrong word (case-insensitive), values are the "
                    "correct replacement. Applied before generating subtitles. "
                    "Example: {\"cloud\": \"Claude\", \"co-pilot\": \"Copilot\"}."
                ),
            },
            "censor_words": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Blocklist of single words to censor (case-insensitive, whole-word). "
                    "Each occurrence is masked to first char + asterisks (damn -> d***) "
                    "in the cue text, and data.mute_ranges emits padded, merged "
                    "{start, end, word} ranges from the word timestamps for downstream "
                    "audio muting/bleeping. Applied after corrections."
                ),
            },
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=10)
    idempotency_key_fields = ["segments", "format", "max_words_per_cue", "censor_words"]
    side_effects = ["writes subtitle file to output_path"]
    user_visible_verification = [
        "Play video with generated subtitles and verify timing",
    ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        segments = inputs.get("segments")
        if not isinstance(segments, list):
            return ToolResult(success=False, error="segments must be a list of transcript segments.")
        fmt = inputs.get("format", "srt")
        max_words = inputs.get("max_words_per_cue", 8)
        max_chars = inputs.get("max_chars_per_line", 42)
        highlight_style = inputs.get("highlight_style", "none")
        output_path = inputs.get("output_path")
        corrections = inputs.get("corrections")
        censor_words = inputs.get("censor_words")

        if censor_words is not None:
            err = self._validate_censor_words(censor_words)
            if err:
                return ToolResult(success=False, error=err)

        start = time.time()

        # Apply word corrections if provided (before censoring, so a
        # corrected word is also censorable)
        if corrections:
            segments = self._apply_corrections(segments, corrections)

        mute_ranges: list[dict] = []
        censor_summary: Optional[dict[str, Any]] = None
        if censor_words:
            segments, mute_ranges, censor_summary = self._apply_censor(segments, censor_words)

        # Build cues from word-level timestamps
        cues = self._build_cues(segments, max_words, max_chars)

        if fmt == "srt":
            content = self._render_srt(cues, highlight_style)
            ext = ".srt"
        elif fmt == "vtt":
            content = self._render_vtt(cues, highlight_style)
            ext = ".vtt"
        elif fmt == "json":
            content = json.dumps({"cues": cues, "highlight_style": highlight_style}, indent=2)
            ext = ".caption.json"
        else:
            return ToolResult(success=False, error=f"Unknown format: {fmt}")

        if output_path is None:
            output_path = f"subtitles{ext}"
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")

        elapsed = time.time() - start

        data: dict[str, Any] = {
            "format": fmt,
            "cue_count": len(cues),
            "output": str(out),
        }
        if censor_summary is not None:
            data["mute_ranges"] = mute_ranges
            data["censor_summary"] = censor_summary

        return ToolResult(
            success=True,
            data=data,
            artifacts=[str(out)],
            duration_seconds=round(elapsed, 2),
        )

    @staticmethod
    def _apply_corrections(
        segments: list[dict], corrections: dict[str, str]
    ) -> list[dict]:
        """Apply word-level corrections to transcript segments.

        Handles case-insensitive matching and preserves punctuation.
        """
        import copy

        corr = {k.lower(): v for k, v in corrections.items()}
        result = copy.deepcopy(segments)

        for seg in result:
            words = seg.get("words", [])
            for w in words:
                raw = w.get("word", "").strip()
                # Strip punctuation for lookup, preserve it
                stripped = raw.lower().rstrip(".,!?;:'\"")
                if stripped in corr:
                    trailing = raw[len(stripped):]
                    w["word"] = corr[stripped] + trailing
            # Also fix segment-level text
            if "text" in seg and words:
                seg["text"] = " ".join(w["word"] for w in words)
            elif "text" in seg:
                for wrong, right in corr.items():
                    import re as _re
                    seg["text"] = _re.sub(
                        r"\b" + _re.escape(wrong) + r"\b",
                        right,
                        seg["text"],
                        flags=_re.IGNORECASE,
                    )

        return result

    @staticmethod
    def _validate_censor_words(censor_words: Any) -> Optional[str]:
        if not isinstance(censor_words, list) or not all(
            isinstance(w, str) for w in censor_words
        ):
            return "censor_words must be a list of strings."
        cleaned = [w.strip() for w in censor_words]
        if any(not w for w in cleaned):
            return "censor_words entries must be non-empty strings."
        if any(any(ch.isspace() for ch in w) for w in cleaned):
            return "censor_words entries must be single words (whole-word matching, no phrases)."
        return None

    @staticmethod
    def _mask_word(word: str) -> str:
        """Mask a word to first char + asterisks: damn -> d***."""
        return word[0] + "*" * (len(word) - 1) if word else word

    @classmethod
    def _apply_censor(
        cls, segments: list[dict], censor_words: list[str]
    ) -> tuple[list[dict], list[dict], dict[str, Any]]:
        """Mask blocklisted words and derive padded, merged mute ranges.

        Matching mirrors _apply_corrections: case-insensitive on the
        punctuation-stripped token, trailing punctuation preserved. Word-level
        timestamps become mute ranges padded by MUTE_PAD_SECONDS; matches in
        segments without word timestamps are masked in the text but cannot
        produce a range (counted as unmuted_text_matches).
        """
        import copy
        import re

        blocklist = {w.strip().lower() for w in censor_words}
        result = copy.deepcopy(segments)
        raw_ranges: list[dict] = []
        per_word_counts: dict[str, int] = {}
        unmuted_text_matches = 0

        for seg in result:
            words = seg.get("words", [])
            for w in words:
                raw = w.get("word", "").strip()
                core = raw.lower().rstrip(cls._TRAILING_PUNCT)
                if core not in blocklist:
                    continue
                trailing = raw[len(core):]
                w["word"] = cls._mask_word(raw[: len(core)]) + trailing
                per_word_counts[core] = per_word_counts.get(core, 0) + 1
                w_start, w_end = w.get("start"), w.get("end")
                if isinstance(w_start, (int, float)) and isinstance(w_end, (int, float)):
                    raw_ranges.append({
                        "start": max(0.0, float(w_start) - cls.MUTE_PAD_SECONDS),
                        "end": float(w_end) + cls.MUTE_PAD_SECONDS,
                        "word": core,
                    })
                else:
                    unmuted_text_matches += 1
            if "text" in seg and words:
                seg["text"] = " ".join(w["word"] for w in words)
            elif "text" in seg:
                for blocked in sorted(blocklist):
                    pattern = r"\b" + re.escape(blocked) + r"\b"
                    seg["text"], n = re.subn(
                        pattern,
                        lambda m: cls._mask_word(m.group(0)),
                        seg["text"],
                        flags=re.IGNORECASE,
                    )
                    if n:
                        per_word_counts[blocked] = per_word_counts.get(blocked, 0) + n
                        unmuted_text_matches += n

        mute_ranges = cls._merge_ranges(raw_ranges)
        censor_summary = {
            "censor_words": sorted(blocklist),
            "masked_occurrences": sum(per_word_counts.values()),
            "per_word_counts": per_word_counts,
            "mute_range_count": len(mute_ranges),
            "merged_overlap_count": len(raw_ranges) - len(mute_ranges),
            "unmuted_text_matches": unmuted_text_matches,
        }
        return result, mute_ranges, censor_summary

    @staticmethod
    def _merge_ranges(ranges: list[dict]) -> list[dict]:
        """Sort by start and merge overlapping/touching ranges; round to ms."""
        merged: list[dict] = []
        for r in sorted(ranges, key=lambda r: (r["start"], r["end"])):
            if merged and r["start"] <= merged[-1]["end"]:
                last = merged[-1]
                last["end"] = max(last["end"], r["end"])
                last["word"] = f"{last['word']} {r['word']}"
            else:
                merged.append(dict(r))
        for r in merged:
            r["start"] = round(r["start"], 3)
            r["end"] = round(r["end"], 3)
        return merged

    def _build_cues(
        self, segments: list[dict], max_words: int, max_chars: int
    ) -> list[dict]:
        """Group words into display cues respecting max_words and max_chars."""
        # Collect all words with timestamps
        all_words = []
        for seg in segments:
            words = seg.get("words", [])
            if words:
                all_words.extend(words)
            elif "text" in seg:
                # Fallback: segment-level only (no word timestamps)
                all_words.append({
                    "word": seg["text"],
                    "start": seg["start"],
                    "end": seg["end"],
                })

        if not all_words:
            return []

        cues = []
        buf: list[dict] = []
        buf_text = ""

        for w in all_words:
            word_text = w["word"].strip()
            candidate = f"{buf_text} {word_text}".strip() if buf_text else word_text

            if buf and (len(buf) >= max_words or len(candidate) > max_chars):
                cues.append({
                    "index": len(cues) + 1,
                    "start": buf[0]["start"],
                    "end": buf[-1]["end"],
                    "text": buf_text,
                    "words": [
                        {"word": b["word"].strip(), "start": b["start"], "end": b["end"]}
                        for b in buf
                    ],
                })
                buf = []
                buf_text = ""

            buf.append(w)
            buf_text = f"{buf_text} {word_text}".strip() if buf_text else word_text

        # Flush remaining
        if buf:
            cues.append({
                "index": len(cues) + 1,
                "start": buf[0]["start"],
                "end": buf[-1]["end"],
                "text": buf_text,
                "words": [
                    {"word": b["word"].strip(), "start": b["start"], "end": b["end"]}
                    for b in buf
                ],
            })

        return cues

    def _render_srt(self, cues: list[dict], highlight_style: str = "none") -> str:
        lines = []
        if highlight_style == "word_by_word":
            # Emit one cue per word for word-by-word reveal
            idx = 1
            for cue in cues:
                for word_info in cue.get("words", []):
                    lines.append(str(idx))
                    lines.append(
                        f"{self._ts_srt(word_info['start'])} --> {self._ts_srt(word_info['end'])}"
                    )
                    lines.append(word_info["word"])
                    lines.append("")
                    idx += 1
        elif highlight_style == "karaoke":
            # Show full cue text but bold the active word using SRT HTML tags
            for cue in cues:
                words = cue.get("words", [])
                if not words:
                    lines.append(str(cue["index"]))
                    lines.append(f"{self._ts_srt(cue['start'])} --> {self._ts_srt(cue['end'])}")
                    lines.append(cue["text"])
                    lines.append("")
                    continue
                for wi, word_info in enumerate(words):
                    lines.append(str(cue["index"] * 100 + wi))
                    lines.append(
                        f"{self._ts_srt(word_info['start'])} --> {self._ts_srt(word_info['end'])}"
                    )
                    parts = []
                    for wj, w in enumerate(words):
                        if wj == wi:
                            parts.append(f"<b>{w['word']}</b>")
                        else:
                            parts.append(w["word"])
                    lines.append(" ".join(parts))
                    lines.append("")
        else:
            for cue in cues:
                lines.append(str(cue["index"]))
                lines.append(f"{self._ts_srt(cue['start'])} --> {self._ts_srt(cue['end'])}")
                lines.append(cue["text"])
                lines.append("")
        return "\n".join(lines)

    def _render_vtt(self, cues: list[dict], highlight_style: str = "none") -> str:
        lines = ["WEBVTT", ""]
        if highlight_style == "word_by_word":
            for cue in cues:
                for word_info in cue.get("words", []):
                    lines.append(
                        f"{self._ts_vtt(word_info['start'])} --> {self._ts_vtt(word_info['end'])}"
                    )
                    lines.append(word_info["word"])
                    lines.append("")
        elif highlight_style == "karaoke":
            for cue in cues:
                words = cue.get("words", [])
                if not words:
                    lines.append(f"{self._ts_vtt(cue['start'])} --> {self._ts_vtt(cue['end'])}")
                    lines.append(cue["text"])
                    lines.append("")
                    continue
                for wi, word_info in enumerate(words):
                    lines.append(
                        f"{self._ts_vtt(word_info['start'])} --> {self._ts_vtt(word_info['end'])}"
                    )
                    parts = []
                    for wj, w in enumerate(words):
                        if wj == wi:
                            parts.append(f"<b>{w['word']}</b>")
                        else:
                            parts.append(w["word"])
                    lines.append(" ".join(parts))
                    lines.append("")
        else:
            for cue in cues:
                lines.append(f"{self._ts_vtt(cue['start'])} --> {self._ts_vtt(cue['end'])}")
                lines.append(cue["text"])
                lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _ts_srt(seconds: float) -> str:
        """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds % 1) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def _ts_vtt(seconds: float) -> str:
        """Format seconds as VTT timestamp: HH:MM:SS.mmm"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds % 1) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
