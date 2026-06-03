# Second-Hook SFX for Short-Form Reels

Use when building or reviewing Instagram Reels, TikToks, Shorts, talking-head edits, product demos, kinetic-caption videos, or AI/tool explainers where sound effects should act as a **second hook** that keeps attention after the visual/spoken hook.

## Source Lesson

Reference analyzed: Instagram Reel `DYqo3PbBBYN` by Tanishaa Bhansali.

Caption thesis: **"Sound effects are your second hook on reels."** A Reel without SFX behaves like a movie without background music: less interactive, less interesting, and less emotionally legible.

Accessible transcript:

> Use "fahhhh" for epic fails. Use [whoosh] for zoom in, zoom out, or transitions. Use "riser" for building suspense or making a very strong point. Use "pop/click" when you add overlays. Use "crickets" to show awkward silences. You can find these sound effects free to download on myinstants.com.

Do **not** copy the Reel audio into OpenMontage deliverables. Treat the Reel as a pattern source. Use OpenMontage's in-repo `assets/sfx/` library when possible, generate bespoke replacements when needed, or ask the user to supply licensed sounds.

## Core Pattern

For short-form social videos, each important visual/spoken beat gets a lightweight audio cue that clarifies the emotional instruction:

| SFX family | Use when the beat means... | OpenMontage existing asset | If missing, generate/source this |
|---|---|---|---|
| `fahhhh` / falling-fail | Epic fail, mistake reveal, "wrong way", rejected idea, bad result | closest: `impact-soft` at lower volume, or `bass-drop-soft` for bigger fail | Short comedic descending vocalized fail sting, dry, meme-like but not obnoxious, 0.6-1.0s |
| `whoosh` | Zoom-in, zoom-out, swipe, scene transition, text/card travel | `whoosh-fast`, `whoosh-deep`, `swipe-paper` | Clean fast air whoosh, short tail, modern motion graphics, 0.4-0.8s |
| `riser` | Suspense, upcoming reveal, strong point, before a stat/punchline | `riser-short`, `transition-riser` | Rising synth/air swell that ends exactly on reveal; no long tail, 0.8-1.6s |
| `pop/click` | Overlay appears, bullet lands, UI card enters, pointer/cursor taps, label snap | `pop-bubble`, `click-soft`, `tick-check` | Tight UI pop/click, very short, no reverb, 0.15-0.5s |
| `crickets` | Awkward silence, dead room, joke pause, "nobody responded" | none in current library | Sparse cricket chirps with room tone, comedic awkward silence, 1.5-3.0s; duck under VO or use during intentional pause |

## Timing Rules

- **Audio leads the eye:** start SFX 10-20 ms before the visual frame it accents.
- **Risers end on the reveal:** the final swell/peak should land on the exact frame where the answer, stat, or strong point appears.
- **Overlay clicks are micro-cues:** keep them short and quiet; they should make cards feel tactile, not distract from speech.
- **Comedy SFX need space:** `fahhhh` and `crickets` work only when the edit allows a tiny pause or visual hold.
- **Density cap:** in dialogue-heavy videos, use at most one SFX every ~2 seconds unless the scene is deliberately a fast montage.
- **Dialogue priority:** SFX must sit below narration. Peak target is usually -18 to -12 dB, with extra ducking when VO overlaps.

## Reels / OpenMontage Placement Recipes

### Wrong-way / right-way hook
1. Show the bad output, failed attempt, or common mistake.
2. Add `fahhhh` or a low `impact-soft` as the bad version lands.
3. Cut quickly into the better setup with `whoosh-fast` or `pop-bubble`.
4. Use `riser-short` into the corrected result.

### Product-demo zoom or UI transition
1. Start `whoosh-fast` 10-20 ms before digital zoom begins.
2. Land zoom on a highlighted UI detail.
3. Add `click-soft` when the highlight/label appears.
4. Avoid a whoosh on every zoom; use it only for meaningful focus changes.

### Strong founder/operator point
1. Begin `riser-short` under the setup clause.
2. End the riser exactly on the punchline or stat.
3. Add optional `impact-soft` at the reveal if the point is major.
4. Hold 0.2-0.4s so the viewer feels the payoff.

### Overlay-heavy educational Reel
1. Use `pop-bubble` or `click-soft` for the first overlay in a sequence.
2. For subsequent overlays, alternate silence and clicks so it does not sound like a toy UI.
3. Pair the most important overlay with the cleanest SFX; leave decorative overlays silent.

### Awkward-silence joke
1. Cut music/bed down or out for the pause.
2. Drop `crickets` at low volume during the held face/screen.
3. Return with a short `whoosh-fast` or hard cut to resume pacing.
4. Use sparingly; one crickets gag per video is usually enough.

## Edit-Decision Template

```json
{
  "audio_tracks": [
    {
      "source": "assets/sfx/riser-short.mp3",
      "start_seconds": 3.74,
      "volume_db": -18,
      "notes": "Riser ends on the stat reveal at 4.94s"
    },
    {
      "source": "assets/sfx/impact-soft.mp3",
      "start_seconds": 4.94,
      "volume_db": -15,
      "notes": "Soft hit on strong point"
    },
    {
      "source": "assets/sfx/click-soft.mp3",
      "start_seconds": 7.18,
      "volume_db": -20,
      "notes": "Overlay label appears"
    }
  ]
}
```

Paths are repo-relative. Do not copy shared SFX into `projects/<name>/assets/audio/`; reference `assets/sfx/<slug>.mp3` directly. Project-specific bespoke SFX should live in `projects/<project-name>/assets/audio/` with provenance noted in the asset manifest.

## Sound-Sourcing Notes

- The reference Reel points viewers to `myinstants.com` for free downloads, but OpenMontage should still verify license/provenance before using third-party files in client/public deliverables.
- For internal drafts, placeholder SFX are acceptable if clearly labeled in the asset manifest.
- For publishable videos, prefer OpenMontage-owned generated SFX, user-supplied licensed files, or clearly royalty-free sources.
- If creating permanent shared effects for `fahhhh` or `crickets`, extend `scripts/generate_educational_sfx.py`, regenerate the manifest, then update `skills/creative/sfx-library.md`.

## Review Checklist

- [ ] Did every SFX map to an emotional or visual beat, not random decoration?
- [ ] Are whooshes used for actual motion/transition beats only?
- [ ] Does every riser resolve on-frame with the reveal?
- [ ] Are overlay pops/clicks quiet enough under VO?
- [ ] Are comedy sounds (`fahhhh`, `crickets`) given enough pause/space to read?
- [ ] Are third-party/free-download sounds licensed or clearly marked as placeholders?

## Related Skills

- `skills/creative/sfx-library.md` — canonical in-repo SFX files and levels.
- `skills/creative/sound-design.md` — mixing, ducking, LUFS, and dialogue-first audio rules.
- `skills/creative/short-form.md` — short-form video pacing and retention constraints.
- `skills/creative/kinetic-whiteboard-captions.md` — word-synced caption style that benefits from `pop/click` and riser cues.
