"""Generate a curated SFX library for educational/informational reels.

Uses ElevenLabs `/v1/sound-generation` (text-to-sound-effects) — distinct from
the `/v1/music` endpoint that `tools/audio/music_gen.py` calls. Writes MP3s to
`assets/sfx/` along with a `manifest.json` describing each effect.

Run:
    python scripts/generate_educational_sfx.py
    python scripts/generate_educational_sfx.py --only "whoosh-fast,ding-positive"
    python scripts/generate_educational_sfx.py --skip-existing
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.env_loader import load_env, require_env  # noqa: E402

API_URL = "https://api.elevenlabs.io/v1/sound-generation"
MODEL_ID = "eleven_text_to_sound_v2"
OUTPUT_FORMAT = "mp3_44100_128"
OUT_DIR = ROOT / "assets" / "sfx"

# Curated library for educational/informational reels.
# Each entry: slug, category, prompt, duration_seconds, prompt_influence, loop, usage hint.
LIBRARY: list[dict] = [
    # --- Transitions -------------------------------------------------------
    {
        "slug": "whoosh-fast",
        "category": "transition",
        "prompt": "Fast clean whoosh transition, short high-frequency air swoop, modern motion graphics style, dry no reverb",
        "duration_seconds": 0.8,
        "prompt_influence": 0.55,
        "loop": False,
        "usage": "Quick cuts between scenes, text reveals.",
    },
    {
        "slug": "whoosh-deep",
        "category": "transition",
        "prompt": "Deep cinematic whoosh with low sub-bass tail, smooth swoosh, premium documentary trailer transition",
        "duration_seconds": 1.4,
        "prompt_influence": 0.55,
        "loop": False,
        "usage": "Big chapter transitions and section breaks.",
    },
    {
        "slug": "swipe-paper",
        "category": "transition",
        "prompt": "Crisp paper card swipe, soft cardboard whoosh, light editorial motion graphics swoosh",
        "duration_seconds": 0.7,
        "prompt_influence": 0.6,
        "loop": False,
        "usage": "Card flips, lower-third reveals, callout entries.",
    },
    {
        "slug": "transition-riser",
        "category": "transition",
        "prompt": "Short upward riser sweep, smooth rising tonal whoosh leading into a soft tap, no impact tail",
        "duration_seconds": 1.6,
        "prompt_influence": 0.5,
        "loop": False,
        "usage": "Build-up before a key point or reveal.",
    },

    # --- Impacts / Stingers ------------------------------------------------
    {
        "slug": "impact-soft",
        "category": "impact",
        "prompt": "Soft cinematic impact thump, warm low boom with short tail, subtle and tasteful, not aggressive",
        "duration_seconds": 1.0,
        "prompt_influence": 0.55,
        "loop": False,
        "usage": "Punctuate a callout or stat reveal.",
    },
    {
        "slug": "impact-cinematic",
        "category": "impact",
        "prompt": "Cinematic boom impact with deep sub-bass and short reverb tail, trailer hit, modern and clean",
        "duration_seconds": 1.6,
        "prompt_influence": 0.55,
        "loop": False,
        "usage": "Hero shots, big number reveals, title cards.",
    },
    {
        "slug": "stinger-opener",
        "category": "impact",
        "prompt": "Short opener stinger: tight upward swoosh into a clean impact, modern explainer intro hit",
        "duration_seconds": 1.5,
        "prompt_influence": 0.55,
        "loop": False,
        "usage": "Opening logo or title sting.",
    },

    # --- UI / Notification -------------------------------------------------
    {
        "slug": "ding-positive",
        "category": "ui",
        "prompt": "Bright positive notification ding, single soft bell tone, friendly app UI sound, very short",
        "duration_seconds": 0.6,
        "prompt_influence": 0.7,
        "loop": False,
        "usage": "Correct answer, positive callout, tip box.",
    },
    {
        "slug": "pop-bubble",
        "category": "ui",
        "prompt": "Soft mouth pop bubble sound, plucky cartoon pop, tight and clean, UI bullet point",
        "duration_seconds": 0.5,
        "prompt_influence": 0.75,
        "loop": False,
        "usage": "Bullet points appearing, badge pop-ins.",
    },
    {
        "slug": "click-soft",
        "category": "ui",
        "prompt": "Soft UI tap click, subtle plastic tick, clean modern interface click, very quiet tail",
        "duration_seconds": 0.5,
        "prompt_influence": 0.75,
        "loop": False,
        "usage": "Step counters, list items, micro-interactions.",
    },
    {
        "slug": "notification-chime",
        "category": "ui",
        "prompt": "Short two-note notification chime, friendly mobile alert tone, warm bell-like timbre",
        "duration_seconds": 0.9,
        "prompt_influence": 0.7,
        "loop": False,
        "usage": "Did-you-know callouts, info boxes.",
    },
    {
        "slug": "tick-check",
        "category": "ui",
        "prompt": "Crisp checkmark tick, single bright high-pitched tick, satisfying confirmation sound",
        "duration_seconds": 0.5,
        "prompt_influence": 0.75,
        "loop": False,
        "usage": "Checklist items, completed steps.",
    },

    # --- Emphasis / Highlight ---------------------------------------------
    {
        "slug": "sparkle-magic",
        "category": "emphasis",
        "prompt": "Magical sparkle shimmer, glittery descending bell tones, light and airy, no harshness",
        "duration_seconds": 1.3,
        "prompt_influence": 0.6,
        "loop": False,
        "usage": "Aha moment, highlight burst, wow reveal.",
    },
    {
        "slug": "lightbulb-idea",
        "category": "emphasis",
        "prompt": "Soft aha moment chime, single bright shimmering bell with gentle reverb, warm idea spark",
        "duration_seconds": 1.0,
        "prompt_influence": 0.65,
        "loop": False,
        "usage": "Key insight, lightbulb icon animations.",
    },
    {
        "slug": "riser-short",
        "category": "emphasis",
        "prompt": "Quick attention riser, rising synth swell with subtle tonal sweep, dry no impact",
        "duration_seconds": 1.2,
        "prompt_influence": 0.5,
        "loop": False,
        "usage": "Buildup before a stat or punchline.",
    },

    # --- Educational textures ---------------------------------------------
    {
        "slug": "typewriter-loop",
        "category": "texture",
        "prompt": "Mechanical typewriter typing, steady keystroke rhythm at moderate pace, paper carriage, no music",
        "duration_seconds": 4.0,
        "prompt_influence": 0.7,
        "loop": True,
        "usage": "Text reveal sequences, intro letters typing on screen.",
    },
    {
        "slug": "paper-flip",
        "category": "texture",
        "prompt": "Single paper page flip turning, crisp clean paper rustle, short and dry",
        "duration_seconds": 0.7,
        "prompt_influence": 0.7,
        "loop": False,
        "usage": "Chapter card flips, slide transitions.",
    },
    {
        "slug": "pencil-write",
        "category": "texture",
        "prompt": "Pencil writing on paper, soft graphite scratch, short scribble across page, intimate close mic",
        "duration_seconds": 1.6,
        "prompt_influence": 0.7,
        "loop": False,
        "usage": "Hand-drawn highlight sweeps, underline animations.",
    },

    # --- Outro / Payoff ----------------------------------------------------
    {
        "slug": "outro-payoff",
        "category": "outro",
        "prompt": "Soft completion chime, gentle resolved two-note bell, warm satisfying end-card sound",
        "duration_seconds": 1.4,
        "prompt_influence": 0.65,
        "loop": False,
        "usage": "End card, conclusion slide.",
    },
    {
        "slug": "bass-drop-soft",
        "category": "outro",
        "prompt": "Soft cinematic bass drop, smooth sub-bass swell with short impact, subtle and tasteful",
        "duration_seconds": 1.8,
        "prompt_influence": 0.55,
        "loop": False,
        "usage": "Final reveal, big takeaway emphasis.",
    },
]


def generate_one(entry: dict, api_key: str, out_dir: Path) -> dict:
    """Generate one SFX and write it to disk. Returns a manifest record."""
    out_path = out_dir / f"{entry['slug']}.mp3"
    payload = {
        "text": entry["prompt"],
        "model_id": MODEL_ID,
        "duration_seconds": entry["duration_seconds"],
        "prompt_influence": entry["prompt_influence"],
        "loop": entry["loop"],
    }
    params = {"output_format": OUTPUT_FORMAT}
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}

    t0 = time.time()
    resp = requests.post(
        API_URL, headers=headers, params=params, json=payload, timeout=120
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"{entry['slug']}: HTTP {resp.status_code} — {resp.text[:300]}"
        )
    out_path.write_bytes(resp.content)
    elapsed = round(time.time() - t0, 2)

    return {
        "slug": entry["slug"],
        "category": entry["category"],
        "file": f"{entry['slug']}.mp3",
        "prompt": entry["prompt"],
        "duration_seconds": entry["duration_seconds"],
        "prompt_influence": entry["prompt_influence"],
        "loop": entry["loop"],
        "usage": entry["usage"],
        "model_id": MODEL_ID,
        "format": OUTPUT_FORMAT,
        "bytes": len(resp.content),
        "gen_seconds": elapsed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only", help="Comma-separated slugs to (re)generate; defaults to all."
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip slugs whose MP3 already exists in assets/sfx/.",
    )
    args = parser.parse_args()

    load_env()
    api_key = require_env("ELEVENLABS_API_KEY")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    only = set(s.strip() for s in args.only.split(",")) if args.only else None

    selected = [e for e in LIBRARY if only is None or e["slug"] in only]
    if not selected:
        print("No SFX matched the --only filter.", file=sys.stderr)
        return 2

    manifest_path = OUT_DIR / "manifest.json"
    existing_manifest: dict[str, dict] = {}
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text())
            for rec in data.get("effects", []):
                existing_manifest[rec["slug"]] = rec
        except Exception:
            existing_manifest = {}

    records: list[dict] = []
    failures: list[tuple[str, str]] = []

    print(f"Generating {len(selected)} SFX -> {OUT_DIR}")
    for i, entry in enumerate(selected, 1):
        out_path = OUT_DIR / f"{entry['slug']}.mp3"
        if args.skip_existing and out_path.exists():
            print(f"  [{i:>2}/{len(selected)}] skip (exists)  {entry['slug']}")
            if entry["slug"] in existing_manifest:
                records.append(existing_manifest[entry["slug"]])
            continue
        try:
            print(
                f"  [{i:>2}/{len(selected)}] generating     {entry['slug']:<22} "
                f"({entry['duration_seconds']}s, {entry['category']})"
            )
            rec = generate_one(entry, api_key, OUT_DIR)
            records.append(rec)
            print(
                f"      ok  {rec['bytes']/1024:6.1f} KB in {rec['gen_seconds']}s"
            )
        except Exception as exc:
            print(f"      FAIL: {exc}", file=sys.stderr)
            failures.append((entry["slug"], str(exc)))

    merged: dict[str, dict] = {r["slug"]: r for r in records}
    for slug, rec in existing_manifest.items():
        merged.setdefault(slug, rec)

    manifest = {
        "generated_with": "ElevenLabs sound-generation v2",
        "endpoint": API_URL,
        "model_id": MODEL_ID,
        "output_format": OUTPUT_FORMAT,
        "count": len(merged),
        "effects": sorted(merged.values(), key=lambda r: (r["category"], r["slug"])),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"\nWrote manifest -> {manifest_path}")
    print(f"Total effects in manifest: {len(merged)}")
    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for slug, err in failures:
            print(f"  - {slug}: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
