"""SFX Kit — curated sound-effects library + personalized SFX generation.

Instagram Edits ships a built-in sound-effects library plus on-demand custom SFX.
This tool is that parity piece: it fronts the in-repo curated library at
`assets/sfx/` (see `skills/creative/sfx-library.md` for placement/level rules)
and fills the `generate_sfx` capability flag that music_gen advertises but never
implemented.

Ops:
  - search    keyword/category lookup over assets/sfx/manifest.json. Free, LOCAL,
              no network. Returns ranked matches with absolute file paths.
  - generate  ElevenLabs POST /v1/sound-generation -> mp3 at output_path. Paid,
              needs ELEVENLABS_API_KEY. duration_seconds is required (no silent
              defaults — same stance as music_gen).
  - register  append a generated/user SFX into the manifest so the library grows.
              Slug uniqueness validated, category from the fixed library enum,
              file copied next to the manifest, atomic write (tmp + os.replace).

Design notes / documented limitations:
  - search ranking is keyword scoring over slug/category/usage/prompt — it is not
    semantic. Synonyms the manifest doesn't contain ("swish" vs "whoosh") won't hit;
    the library's prompts and usage strings are written to be search-friendly.
  - register requires the manifest to already exist (the library is seeded by
    scripts/generate_educational_sfx.py); it will not bootstrap an empty library.
  - register needs a duration: pass duration_seconds explicitly or have ffprobe
    on PATH so it can be probed. ffprobe is intentionally NOT a hard dependency.
  - generate cost is an estimate (~$0.02/s — the full 20-effect library costs
    roughly $1 in credits per skills/creative/sfx-library.md); ElevenLabs bills
    in credits, not USD.
"""

from __future__ import annotations

import json
import os
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
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class SfxKit(BaseTool):
    name = "sfx_kit"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "music_generation"  # matches music_gen; this implements its generate_sfx flag
    provider = "elevenlabs"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC  # generate is stochastic; search/register are deterministic
    runtime = ToolRuntime.HYBRID  # search/register run locally; generate calls the API

    # env key is only needed for the generate op — get_status() degrades instead
    # of going unavailable so the free local search keeps working without a key.
    dependencies = ["env:ELEVENLABS_API_KEY"]
    install_instructions = (
        "Set the ELEVENLABS_API_KEY environment variable (only needed for op=generate):\n"
        "  export ELEVENLABS_API_KEY=your_key_here\n"
        "Get a key at https://elevenlabs.io"
    )

    agent_skills = ["sound-effects", "elevenlabs"]

    OPERATIONS = ("search", "generate", "register")
    # Fixed category enum — mirrors the categories in assets/sfx/manifest.json and
    # the section structure of skills/creative/sfx-library.md.
    CATEGORIES = ("emphasis", "impact", "outro", "texture", "transition", "ui")
    SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

    API_URL = "https://api.elevenlabs.io/v1/sound-generation"
    MODEL_ID = "eleven_text_to_sound_v2"
    OUTPUT_FORMAT = "mp3_44100_128"
    DURATION_MIN = 0.5
    DURATION_MAX = 30.0
    COST_PER_SECOND_USD = 0.02

    DEFAULT_MANIFEST = REPO_ROOT / "assets" / "sfx" / "manifest.json"

    capabilities = ["search_sfx_library", "generate_sfx", "register_sfx"]
    supports = {op: True for op in OPERATIONS}
    best_for = [
        "SFX accents for explainers/reels — search the curated library FIRST (free, local); "
        "placement/level rules live in skills/creative/sfx-library.md",
        "personalized one-off SFX the library lacks — generate via ElevenLabs, then register "
        "the keeper back into assets/sfx/ so the library grows",
    ]
    not_good_for = [
        "background music or anything melodic/longform — use music_gen",
        "speech or voiceover — use a TTS tool (elevenlabs_tts / tts_selector)",
        "bespoke project-only sounds — generate to projects/<name>/assets/audio/ and "
        "skip register; the shared library is for reusable effects only",
    ]
    fallback_tools: list[str] = []

    input_schema = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {"type": "string", "enum": list(OPERATIONS)},
            "manifest_path": {
                "type": "string",
                "description": "Library manifest (defaults to assets/sfx/manifest.json in the repo)",
            },
            # search
            "query": {"type": "string", "description": "search: keywords, e.g. 'whoosh fast'"},
            "category": {
                "type": "string",
                "enum": list(CATEGORIES),
                "description": "search: filter / register: category for the new effect",
            },
            "limit": {"type": "integer", "minimum": 1, "default": 10, "description": "search: max matches"},
            # generate
            "prompt": {
                "type": "string",
                "description": "generate: sound description / register: prompt or description (searchable)",
            },
            "duration_seconds": {
                "type": "number",
                "minimum": DURATION_MIN,
                "maximum": DURATION_MAX,
                "description": (
                    f"generate: REQUIRED, {DURATION_MIN}-{DURATION_MAX}s. "
                    "register: optional if ffprobe is on PATH (probed from the file)"
                ),
            },
            "prompt_influence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "generate: 0.6-0.8 for tight UI sounds, 0.4-0.6 for atmospheric (default 0.65)",
            },
            "loop": {"type": "boolean", "default": False, "description": "generate/register: seamless loop"},
            "output_path": {"type": "string", "description": "generate: where to write the mp3 (must end .mp3)"},
            # register
            "slug": {"type": "string", "description": "register: unique lowercase-hyphen id, e.g. 'crowd-gasp'"},
            "file_path": {"type": "string", "description": "register: existing mp3 to add to the library"},
            "usage": {"type": "string", "description": "register: when to reach for this effect"},
        },
    }

    # Envelope covers the worst-case op (generate needs network; search is offline).
    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["operation", "query", "category", "prompt", "duration_seconds", "slug"]
    side_effects = [
        "generate: calls the ElevenLabs API and writes an mp3 to output_path",
        "register: copies the file into the library dir and rewrites manifest.json",
    ]
    user_visible_verification = [
        "Listen to the SFX; confirm placement/levels per skills/creative/sfx-library.md "
        "(-18 to -12 dB peak, start 10-20 ms before the visual it accents)",
    ]

    def get_status(self) -> ToolStatus:
        # Never UNAVAILABLE: search/register are local and free. Without the API
        # key only the paid generate op is missing -> DEGRADED.
        if os.environ.get("ELEVENLABS_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.DEGRADED

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        if inputs.get("operation") != "generate":
            return 0.0
        duration = inputs.get("duration_seconds")
        if duration is None:
            raise ValueError(
                "sfx_kit.estimate_cost: duration_seconds is required for generate. "
                "Silent defaults are not permitted."
            )
        return round(float(duration) * self.COST_PER_SECOND_USD, 4)

    # ---- execution ----

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        op = inputs.get("operation")
        if op not in self.OPERATIONS:
            return ToolResult(success=False, error=f"operation must be one of {self.OPERATIONS}.")

        manifest_path = Path(inputs.get("manifest_path") or self.DEFAULT_MANIFEST)

        start = time.time()
        try:
            if op == "search":
                result = self._search(inputs, manifest_path)
            elif op == "generate":
                result = self._generate(inputs)
            else:
                result = self._register(inputs, manifest_path)
        except _OpInputError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"sfx_kit {op} failed: {e}")

        result.duration_seconds = round(time.time() - start, 2)
        return result

    # ---- search (free, local) ----

    def _search(self, inputs: dict[str, Any], manifest_path: Path) -> ToolResult:
        query = (inputs.get("query") or "").strip()
        category = inputs.get("category")
        if not query and not category:
            raise _OpInputError("search requires query and/or category.")
        if category and category not in self.CATEGORIES:
            raise _OpInputError(f"category must be one of {self.CATEGORIES}; got {category!r}.")
        limit = inputs.get("limit", 10)
        if not isinstance(limit, int) or limit < 1:
            raise _OpInputError("limit must be a positive integer.")

        _, effects = self._load_manifest(manifest_path)
        sfx_dir = manifest_path.resolve().parent
        terms = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if t]
        normalized = "-".join(terms)

        matches: list[dict[str, Any]] = []
        for fx in effects:
            if category and fx.get("category") != category:
                continue
            score = self._score(fx, terms, normalized)
            if terms and score <= 0:
                continue
            path = sfx_dir / str(fx.get("file", ""))
            matches.append({
                "slug": fx.get("slug"),
                "category": fx.get("category"),
                "path": str(path),
                "file_exists": path.is_file(),
                "duration_seconds": fx.get("duration_seconds"),
                "loop": fx.get("loop", False),
                "usage": fx.get("usage"),
                "prompt": fx.get("prompt"),
                "score": score,
            })
        matches.sort(key=lambda m: (-m["score"], m["slug"] or ""))
        matches = matches[:limit]

        return ToolResult(
            success=True,
            data={
                "operation": "search",
                "query": query or None,
                "category": category,
                "count": len(matches),
                "matches": matches,
                "manifest_path": str(manifest_path),
            },
        )

    @staticmethod
    def _score(fx: dict[str, Any], terms: list[str], normalized: str) -> int:
        """Keyword score: slug hits dominate, then category, usage, prompt."""
        slug = str(fx.get("slug", "")).lower()
        cat = str(fx.get("category", "")).lower()
        usage = str(fx.get("usage", "")).lower()
        prompt = str(fx.get("prompt", "")).lower()
        score = 0
        if normalized and normalized == slug:
            score += 20
        for t in terms:
            if t in slug:
                score += 6
            if t == cat:
                score += 4
            if t in usage:
                score += 3
            if t in prompt:
                score += 1
        return score

    # ---- generate (ElevenLabs, paid) ----

    def _generate(self, inputs: dict[str, Any]) -> ToolResult:
        import requests

        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="No ElevenLabs API key. " + self.install_instructions,
            )

        prompt = (inputs.get("prompt") or "").strip()
        if not prompt:
            raise _OpInputError("generate requires a non-empty prompt.")
        duration = inputs.get("duration_seconds")
        if duration is None:
            raise _OpInputError(
                "generate requires duration_seconds (match it to the visual moment; "
                "silent defaults are not permitted)."
            )
        if not isinstance(duration, (int, float)) or not (self.DURATION_MIN <= duration <= self.DURATION_MAX):
            raise _OpInputError(
                f"duration_seconds must be in [{self.DURATION_MIN}, {self.DURATION_MAX}]; got {duration!r}."
            )
        influence = inputs.get("prompt_influence", 0.65)
        if not isinstance(influence, (int, float)) or not (0 <= influence <= 1):
            raise _OpInputError(f"prompt_influence must be in [0, 1]; got {influence!r}.")
        out = inputs.get("output_path")
        if not out:
            raise _OpInputError("generate requires output_path.")
        out_path = Path(out)
        if out_path.suffix.lower() != ".mp3":
            raise _OpInputError(f"output_path must end in .mp3 (API returns {self.OUTPUT_FORMAT}); got {out!r}.")

        payload = {
            "text": prompt,
            "model_id": self.MODEL_ID,
            "duration_seconds": float(duration),
            "prompt_influence": float(influence),
            "loop": bool(inputs.get("loop", False)),
        }
        response = requests.post(
            self.API_URL,
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            params={"output_format": self.OUTPUT_FORMAT},
            json=payload,
            timeout=120,
        )
        if response.status_code != 200:
            return ToolResult(
                success=False,
                error=f"ElevenLabs sound-generation HTTP {response.status_code}: {(response.text or '')[:300]}",
            )
        if not response.content:
            return ToolResult(success=False, error="ElevenLabs returned empty audio.")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(response.content)

        return ToolResult(
            success=True,
            data={
                "operation": "generate",
                "provider": "elevenlabs",
                "model": self.MODEL_ID,
                "prompt": prompt,
                "duration_seconds": float(duration),
                "prompt_influence": float(influence),
                "loop": bool(inputs.get("loop", False)),
                "output": str(out_path),
                "format": self.OUTPUT_FORMAT,
                "bytes": len(response.content),
            },
            artifacts=[str(out_path)],
            cost_usd=self.estimate_cost(inputs),
            model=self.MODEL_ID,
        )

    # ---- register (grow the library) ----

    def _register(self, inputs: dict[str, Any], manifest_path: Path) -> ToolResult:
        slug = inputs.get("slug")
        if not isinstance(slug, str) or not self.SLUG_RE.match(slug):
            raise _OpInputError(
                f"register requires a lowercase-hyphen slug (e.g. 'crowd-gasp'); got {slug!r}."
            )
        category = inputs.get("category")
        if category not in self.CATEGORIES:
            raise _OpInputError(f"category must be one of {self.CATEGORIES}; got {category!r}.")
        prompt = (inputs.get("prompt") or "").strip()
        if not prompt:
            raise _OpInputError("register requires prompt (the generation prompt or a searchable description).")
        usage = (inputs.get("usage") or "").strip()
        if not usage:
            raise _OpInputError("register requires usage (when to reach for this effect).")
        src = inputs.get("file_path")
        if not src:
            raise _OpInputError("register requires file_path.")
        src_path = Path(src)
        if not src_path.is_file() or src_path.stat().st_size == 0:
            raise _OpInputError(f"file_path not found or empty: {src}")
        if src_path.suffix.lower() != ".mp3":
            raise _OpInputError(f"the library is mp3-only; got {src_path.suffix!r}.")

        duration = inputs.get("duration_seconds")
        if duration is None:
            duration = self._probe_duration(src_path)
            if duration is None:
                raise _OpInputError(
                    "register could not determine the duration: pass duration_seconds "
                    "or put ffprobe on PATH."
                )
        if not isinstance(duration, (int, float)) or duration <= 0:
            raise _OpInputError(f"duration_seconds must be > 0; got {duration!r}.")

        doc, effects = self._load_manifest(manifest_path)
        if any(fx.get("slug") == slug for fx in effects):
            raise _OpInputError(f"slug {slug!r} already exists in the library; slugs must be unique.")

        sfx_dir = manifest_path.resolve().parent
        dest = sfx_dir / f"{slug}.mp3"
        if src_path.resolve() != dest.resolve():
            if dest.exists():
                raise _OpInputError(f"{dest} already exists on disk; pick a different slug.")
            shutil.copyfile(src_path, dest)

        entry: dict[str, Any] = {
            "slug": slug,
            "category": category,
            "file": dest.name,
            "prompt": prompt,
            "duration_seconds": round(float(duration), 2),
            "loop": bool(inputs.get("loop", False)),
            "usage": usage,
            "format": "mp3",
            "bytes": dest.stat().st_size,
            "registered_by": "sfx_kit",
        }
        if isinstance(inputs.get("prompt_influence"), (int, float)):
            entry["prompt_influence"] = float(inputs["prompt_influence"])

        effects.append(entry)
        # keep the manifest canonical with scripts/generate_educational_sfx.py
        doc["effects"] = sorted(effects, key=lambda r: (r.get("category", ""), r.get("slug", "")))
        doc["count"] = len(effects)
        self._write_json_atomic(manifest_path, doc)

        return ToolResult(
            success=True,
            data={
                "operation": "register",
                "slug": slug,
                "category": category,
                "path": str(dest),
                "duration_seconds": entry["duration_seconds"],
                "manifest_path": str(manifest_path),
                "library_count": doc["count"],
            },
            artifacts=[str(dest), str(manifest_path)],
        )

    # ---- helpers ----

    def _load_manifest(self, path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not path.is_file():
            raise _OpInputError(
                f"SFX manifest not found: {path}. The library lives at assets/sfx/manifest.json "
                "(see skills/creative/sfx-library.md)."
            )
        try:
            doc = json.loads(path.read_text())
        except Exception as e:
            raise _OpInputError(f"could not parse SFX manifest {path}: {e}")
        effects = doc.get("effects") if isinstance(doc, dict) else None
        if not isinstance(effects, list):
            raise _OpInputError(f"SFX manifest {path} has no effects[] list.")
        return doc, effects

    def _probe_duration(self, path: Path) -> Optional[float]:
        if shutil.which("ffprobe") is None:
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
    def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)


class _OpInputError(Exception):
    """Bad parameters for an sfx_kit op (validated before any spend or write)."""
