# SFX Library for OpenMontage

> Curated in-repo sound-effects library at `assets/sfx/`. Generated with
> ElevenLabs `/v1/sound-generation` (text-to-sound-effects v2). Lives outside
> the per-project `projects/<name>/assets/audio/` tree because the same SFX are
> reused across many videos.

## Quick Reference Card

```
LOCATION:        assets/sfx/
MANIFEST:        assets/sfx/manifest.json
GENERATOR:       scripts/generate_educational_sfx.py
PROVIDER:        ElevenLabs (eleven_text_to_sound_v2)
LEVELS:          -18 to -12 dB (6 dB below dialogue) — see sound-design.md
PLACEMENT:       Start SFX 10-20 ms BEFORE the visual it accents
DEFAULT FORMAT:  mp3_44100_128
COUNT:           20 effects across 6 categories
```

## When to Use This Skill

Use this skill from any pipeline's **asset** or **edit** stage when:

- The brief is educational, informational, explainer, tutorial, or product-walkthrough.
- The brief asks for "engaging", "snappy", "punchy", "polished motion graphics", or any phrasing that implies SFX accents.
- The edit needs whooshes, callout pops, stat-reveal impacts, or text-reveal textures.
- A scene has a key visual moment (number reveal, text appearance, bullet drop, transition) that benefits from a non-music audio accent.

**Do NOT use** when:

- The brief is a calm meditation / ambient / cinematic-only piece (SFX will feel cheap).
- A talking-head pipeline is already crowded with dialogue and music — pick at most 1-2 well-placed effects.
- Generating bespoke project-specific sounds — those go to `projects/<name>/assets/audio/` via `scripts/generate_educational_sfx.py` patterns, not this shared library.

## The Library

All durations and prompts are recorded in `assets/sfx/manifest.json`. Use that file as the source of truth; this table is a quick map of *when to reach for each effect*.

### Transitions (4)

| Slug | Dur | Loop | Use when... |
|---|---|---|---|
| `whoosh-fast` | 0.8s | – | Quick scene cut, text reveal, lower-third entry. Default whoosh. |
| `whoosh-deep` | 1.4s | – | Chapter break, big section change. Heavier, with sub-bass tail. |
| `swipe-paper` | 0.7s | – | Card flip, paper-style callout entry, editorial transitions. |
| `transition-riser` | 1.6s | – | Build-up into a reveal. Pair with `impact-soft` or `impact-cinematic` at the apex. |

### Impacts / Stingers (3)

| Slug | Dur | Loop | Use when... |
|---|---|---|---|
| `impact-soft` | 1.0s | – | Punctuate a callout, bullet point, or stat. Tasteful — won't crush dialogue. |
| `impact-cinematic` | 1.6s | – | Hero shot, big number reveal, title card. Trailer-grade hit. |
| `stinger-opener` | 1.5s | – | Opening logo or title sting. Once per video, at the very top. |

### UI / Notification (5)

| Slug | Dur | Loop | Use when... |
|---|---|---|---|
| `ding-positive` | 0.6s | – | Correct answer, "tip" box, success state. |
| `pop-bubble` | 0.5s | – | Bullet points appearing one-by-one, badge pop-ins. |
| `click-soft` | 0.5s | – | Step counters, list items, micro-interactions, cursor clicks in screen demos. |
| `notification-chime` | 0.9s | – | "Did you know" callouts, info boxes, alert overlays. |
| `tick-check` | 0.5s | – | Checklist items completing, "step done" indicator. |

### Emphasis / Highlight (3)

| Slug | Dur | Loop | Use when... |
|---|---|---|---|
| `sparkle-magic` | 1.3s | – | Highlight burst, wow reveal, magical product appearance. |
| `lightbulb-idea` | 1.0s | – | Aha-moment, key insight, lightbulb icon animation. |
| `riser-short` | 1.2s | – | Build-up before a punchline or stat. Lighter than `transition-riser`. |

### Educational Textures (3)

| Slug | Dur | Loop | Use when... |
|---|---|---|---|
| `typewriter-loop` | 4.0s | **yes** | Background under text typing animations. Loops seamlessly — trim to actual length. |
| `paper-flip` | 0.7s | – | Slide transition, chapter card flip, "next page" feel. |
| `pencil-write` | 1.6s | – | Hand-drawn underline sweep, scribble highlight, sketchout text reveal. |

### Outro / Payoff (2)

| Slug | Dur | Loop | Use when... |
|---|---|---|---|
| `outro-payoff` | 1.4s | – | End card, conclusion slide, "thanks for watching". |
| `bass-drop-soft` | 1.8s | – | Final takeaway emphasis, big reveal at the end. Tasteful, not EDM. |

## Common Scene-to-SFX Mappings

These are battle-tested pairings, not laws. Mix and match.

| Scene moment | Suggested SFX (and timing) |
|---|---|
| Opening title card | `stinger-opener` (start at t=0, visual fires ~50 ms later) |
| Section transition | `whoosh-fast` (0.8s before next section), optional `swipe-paper` for paper style |
| Big chapter break | `whoosh-deep` + `impact-soft` overlapping by 200 ms |
| Stat reveal (number flies in) | `transition-riser` (riser ends ON the reveal frame) + `impact-cinematic` on the reveal frame |
| Bullet list appearing | `pop-bubble` per bullet, staggered 200-300 ms |
| Checklist completion | `tick-check` per item |
| Aha-moment / insight | `lightbulb-idea` on the line, optional `sparkle-magic` 300 ms later |
| Typewriter text intro | `typewriter-loop` (ducked under voice if any), trim to text duration |
| Hand-drawn underline | `pencil-write` aligned to stroke duration |
| Did-you-know callout box | `notification-chime` on box entry |
| Final takeaway | `bass-drop-soft` 200 ms before the headline lands, `outro-payoff` on the end card |

## Placement & Levels (Hard Rules)

These come from `sound-design.md` — re-stated here so the asset/edit director doesn't have to cross-reference.

- **Pre-roll:** Start every SFX **10-20 ms BEFORE** the visual frame it accents. Audio leads, not trails.
- **Levels:** -18 dB to -12 dB peak. Must sit **at least 6 dB below dialogue**.
- **Ducking:** When dialogue overlaps, drop the SFX another 3-6 dB. `audio_mixer` handles this if `enable_ducking=True`.
- **Density:** Maximum **1 SFX per 2 seconds** of runtime in dialogue-heavy edits. Higher density (1 per second) is acceptable in fast cuts with no narration.
- **Don't stack within 200 ms** of another SFX unless intentionally layering (riser + impact pair).
- **Loops:** Only `typewriter-loop` is seam-tested for looping. For other slugs, do not loop — trim or fade instead.

## Wiring SFX Into a Composition

### Remotion timeline (edit-decisions JSON)

```json
{
  "audio_tracks": [
    {
      "source": "assets/sfx/whoosh-fast.mp3",
      "start_seconds": 4.78,
      "volume_db": -14
    },
    {
      "source": "assets/sfx/impact-cinematic.mp3",
      "start_seconds": 5.20,
      "volume_db": -10
    }
  ]
}
```

Paths in `edit_decisions` are repo-relative. The Remotion composer resolves them at render time. Do not copy SFX into `projects/<name>/assets/audio/` — reference them in place.

### HyperFrames composition

Reference via `<audio>` tags inside the HTML composition with `data-hf-start` for timing. See `skills/core/hyperframes.md` for the timing model. Same level guidance applies.

### FFmpeg-only edits

Mix SFX via `audio_mixer` with explicit `tracks[]` and `start_seconds[]`. The mixer handles dB conversion and ducking.

## Reviewer Checklist

When self-reviewing an edit that uses this library:

- [ ] Every SFX placed inside a 200 ms pre-roll window of its visual?
- [ ] No SFX peaking above -12 dB?
- [ ] No more than 1 SFX every 2s during dialogue?
- [ ] Each scene's emotional weight earns its SFX (don't decorate empty moments)?
- [ ] Loop-only sounds (`typewriter-loop`) trimmed or faded, not hard-cut?
- [ ] Manifest paths in `edit_decisions` actually exist on disk?

A finding like "SFX present but pre-roll missing" is a critical edit-stage issue — fix and re-render.

## Regenerating or Extending the Library

The generator is at `scripts/generate_educational_sfx.py`. It uses `ELEVENLABS_API_KEY` from `.env` (loaded via `lib/env_loader.py`).

```bash
# Regenerate everything (idempotent — overwrites existing MP3s)
python scripts/generate_educational_sfx.py

# Only add missing files (use this after appending to LIBRARY)
python scripts/generate_educational_sfx.py --skip-existing

# Regenerate specific slugs after tweaking their prompts
python scripts/generate_educational_sfx.py --only "impact-soft,impact-cinematic"
```

**To add a new effect:** append a dict to the `LIBRARY` list in the script with `slug`, `category`, `prompt`, `duration_seconds` (≥ 0.5, ≤ 30), `prompt_influence` (0-1), `loop`, and `usage`. Then run with `--skip-existing`. The manifest is regenerated automatically.

**Prompt-writing tips (from `.agents/skills/sound-effects/SKILL.md`):**

- Be specific: "Heavy rain on a tin roof" beats "Rain".
- Combine elements: "Footsteps on gravel with distant traffic".
- Specify style/mood: "Cinematic braam, horror" or "8-bit retro jump sound".
- For UI sounds, add "clean", "modern", "very short" to keep tails tight.
- Use `prompt_influence` 0.6-0.8 for UI/utility sounds (tight adherence), 0.4-0.6 for atmospheric/cinematic textures (allow more interpretation).

## Cost

ElevenLabs SFX billing is credit-based; the full 20-effect library costs roughly $1 in credits to regenerate. Single-effect tweaks cost a few cents. The generator is idempotent — only re-run for slugs you actually changed.

## Related

- [`skills/creative/sound-design.md`](sound-design.md) — full audio levels, ducking, LUFS targets.
- [`skills/creative/music-gen-usage.md`](music-gen-usage.md) — BPM and music prompt guidance (use alongside SFX, not instead of).
- [`.agents/skills/sound-effects/SKILL.md`](../../.agents/skills/sound-effects/SKILL.md) — Layer 3 ElevenLabs SFX API reference.
- [`scripts/generate_educational_sfx.py`](../../scripts/generate_educational_sfx.py) — generator source.
