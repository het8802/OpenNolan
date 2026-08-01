# Shared SFX Library — Reels / TikTok / Shorts

A **project-agnostic** sound-effects library for short-form vertical video. Any project in this
repo references these by relative path — there is no per-project copy to maintain.

- **70 effects**, MP3 44.1 kHz / 128 kbps, generated with **ElevenLabs** `eleven_text_to_sound_v2`.
- Machine-readable index: [`manifest.json`](manifest.json) (slug, category, prompt, duration,
  `prompt_influence`, `loop`, `usage`, `bytes`, `gen_seconds`).
- Reference a clip as **`assets/sfx/<slug>.mp3`** (e.g. `assets/sfx/vine-boom.mp3`).

## Why these sounds (short-form retention logic)

Instagram and TikTok weight **audio-on watch time** in distribution — content people keep
watching *with sound* gets pushed harder. The proven short-form "sonic grammar" is:

| Moment in the edit | Sound to place | Slugs |
| --- | --- | --- |
| **The cut / scene change** | whoosh / swipe on the transition frame | `whoosh-*`, `swipe-*`, `swoosh-fabric`, `transition-*` |
| **Before a reveal** | riser that *peaks on* the reveal frame (trains anticipation) | `riser-*`, `transition-riser` |
| **The reveal / punchline** | an impact or boom that lands *exactly on the cut* | `boom-deep`, `impact-*`, `slam-title`, `punch-thud`, `vine-boom` |
| **The payoff** | reveal chime / drumroll / cha-ching | `reveal-*`, `drumroll-hit`, `cash-register-reveal`, `magic-reveal-shimmer` |
| **Key word / caption** | short emphasis accent | `sparkle-*`, `shimmer-highlight`, `glitch-emphasis`, `zap-electric`, `laser-zip` |
| **Bullets / checklist / UI** | satisfying micro-tick | `click-*`, `pop-*`, `tick-check`, `coin-collect`, `level-up`, `success-chime-3note` |
| **Pattern interrupt** | notification ping (feels like a real alert) | `notify-*`, `dm-slide-in`, `like-ping-heart`, `airhorn-hype` |
| **Comedy / rewatch bait** | meme stabs (replays boost retention) | `record-scratch`, `sad-trombone`, `boing-cartoon`, `bruh-thud`, `suspense-sting-dramatic`, `crickets-awkward` |
| **Celebration / social proof** | crowd energy | `crowd-cheer`, `applause-quick` |
| **End card** | resolved outro hit | `outro-payoff`, `bass-drop-soft` |

Rule of thumb: **place the transient on the frame it punctuates** — the whoosh ends on the cut,
the impact hits on the reveal, the riser *peaks* (not starts) at the payoff. Keep them low in the
mix (roughly −12 to −18 dB under a voiceover) so they accent rather than fight the narration.

## Catalog
<!-- CATALOG:START (regenerated from manifest.json) -->

### transition  (11)
- **swipe-fast** (0.5s) — Photo swipes, before/after reveals.
- **swipe-paper** (0.7s) — Card flips, lower-third reveals, callout entries.
- **swoosh-fabric** (0.6s) — Outfit change, quick swipe cuts.
- **transition-3d-swoosh** (1.0s) — Dramatic chapter transitions.
- **transition-glitch** (0.7s) — Glitchy scene cuts, tech reels.
- **transition-riser** (1.6s) — Build-up before a key point or reveal.
- **whoosh-deep** (1.4s) — Big chapter transitions and section breaks.
- **whoosh-double** (0.9s) — Rapid back-to-back cuts.
- **whoosh-fast** (0.8s) — Quick cuts between scenes, text reveals.
- **whoosh-reverse** (0.7s) — Rewind reveals, snap-back cuts.
- **whoosh-swirl-up** (0.8s) — Swipe-up transitions, scene changes.

### riser  (4)
- **riser-buildup-snare** (2.5s) — Beat-drop buildups before the payoff.
- **riser-long-tension** (3.0s) — Suspense build before a big reveal.
- **riser-uplifter** (1.8s) — Feel-good reveal buildups.
- **riser-whoosh-hit** (1.5s) — Lead-in to a punchline or stat.

### impact  (9)
- **bass-drop-hard** (1.2s) — Beat-synced drops, hype reveals.
- **boom-deep** (1.4s) — Big reveals, dramatic word emphasis.
- **impact-cinematic** (1.6s) — Hero shots, big number reveals, title cards.
- **impact-glass-break** (1.2s) — Shock reveals, myth-busting moments.
- **impact-metal-hit** (1.3s) — Hard-hitting stat or title slam.
- **impact-soft** (1.0s) — Punctuate a callout or stat reveal.
- **punch-thud** (0.6s) — Punchline emphasis, text slam.
- **slam-title** (1.1s) — Title cards slamming onto screen.
- **stinger-opener** (1.5s) — Opening logo or title sting.

### reveal  (5)
- **cash-register-reveal** (1.0s) — Price drops, revenue reveals, sales.
- **drumroll-hit** (2.0s) — Winner announcements, big reveals.
- **magic-reveal-shimmer** (1.4s) — Transformation reveals, glow-ups.
- **reveal-chime-big** (1.6s) — Big product or number reveals.
- **reveal-pop-shine** (1.0s) — Badge reveals, feature pop-ins.

### emphasis  (8)
- **glitch-emphasis** (0.6s) — Word glitch highlights, tech captions.
- **laser-zip** (0.5s) — Fast callouts, arrow pointer reveals.
- **lightbulb-idea** (1.0s) — Key insight, lightbulb icon animations.
- **riser-short** (1.2s) — Buildup before a stat or punchline.
- **shimmer-highlight** (0.9s) — Highlight sweeps over key words.
- **sparkle-magic** (1.3s) — Aha moment, highlight burst, wow reveal.
- **sparkle-twinkle** (0.8s) — Star ratings, cute highlights.
- **zap-electric** (0.6s) — Idea sparks, energy accents.

### camera  (1)
- **camera-shutter** (0.5s) — Photo snaps, freeze-frame captures, before/after.

### ui  (12)
- **click-mech** (0.5s) — Toggle taps, list ticks.
- **click-soft** (0.5s) — Step counters, list items, micro-interactions.
- **coin-collect** (0.6s) — Points earned, reward moments.
- **ding-positive** (0.6s) — Correct answer, positive callout, tip box.
- **error-buzz** (0.6s) — Wrong option, myth crossed out.
- **level-up** (1.2s) — Progress milestones, upgrades.
- **notification-chime** (0.9s) — Did-you-know callouts, info boxes.
- **pop-bubble** (0.5s) — Bullet points appearing, badge pop-ins.
- **pop-up-in** (0.5s) — Elements popping into frame.
- **success-chime-3note** (0.9s) — Task complete, success callouts.
- **swipe-ui-tick** (0.5s) — Slider reveals, toggle swipes.
- **tick-check** (0.5s) — Checklist items, completed steps.

### texture  (4)
- **keyboard-type-burst** (1.2s) — Fast text typing on screen.
- **paper-flip** (0.7s) — Chapter card flips, slide transitions.
- **pencil-write** (1.6s) — Hand-drawn highlight sweeps, underline animations.
- **typewriter-loop** (4.0s ·loop) — Text reveal sequences, intro letters typing on screen.

### notification  (5)
- **airhorn-hype** (1.0s) — Hype moments, big wins.
- **dm-slide-in** (0.7s) — Chat bubble entries.
- **like-ping-heart** (0.5s) — Like/follow prompts, heart reactions.
- **notify-imessage** (0.8s) — DM/text overlay pop-ups, callouts.
- **notify-ping-soft** (0.6s) — Comment/like pop-ins, tips.

### meme  (6)
- **boing-cartoon** (0.6s) — Funny pop-ins, comedic bounces.
- **bruh-thud** (0.6s) — Facepalm moments, meme reactions.
- **record-scratch** (0.7s) — Wait-what freeze frames, record-scratch moments.
- **sad-trombone** (1.6s) — Fails, disappointing reveals.
- **suspense-sting-dramatic** (1.4s) — Dramatic reveals, comedic tension.
- **vine-boom** (1.0s) — Punchlines, dramatic pauses, plot twists.

### ambience  (3)
- **applause-quick** (1.5s) — Approval beats, achievement claps.
- **crickets-awkward** (3.0s ·loop) — Awkward beats, no-response jokes.
- **crowd-cheer** (2.5s) — Wins, celebrations, hype payoffs.

### outro  (2)
- **bass-drop-soft** (1.8s) — Final reveal, big takeaway emphasis.
- **outro-payoff** (1.4s) — End card, conclusion slide.
<!-- CATALOG:END -->

## Using in a project

**FFmpeg** (mix an SFX onto a clip at a given start time, ducked under the main audio):

```bash
ffmpeg -i clip.mp4 -i ../../assets/sfx/vine-boom.mp3 -filter_complex \
  "[1:a]adelay=2400|2400,volume=0.4[sfx];[0:a][sfx]amix=inputs=2:duration=first" \
  -c:v copy out.mp4
```

**Studio / edit_decisions.json** — SFX are point markers on the audio lane (`audio.sfx[]`),
placed on absolute `start_seconds`; the FFmpeg assemble path owns mixing.

**Programmatic pick** — read `manifest.json` and filter by `category` / `usage`.

## Regenerating & extending

Generated from text prompts (no third-party audio) via the ElevenLabs Sound Effects API
(`text_to_sound_effects.convert`, model `eleven_text_to_sound_v2`, `output_format=mp3_44100_128`).
The generator lives here: [`generate.py`](generate.py). It reads `ELEVENLABS_API_KEY` from the
repo `.env` (never hardcoded) and is **idempotent** — an existing non-empty `<slug>.mp3` is
skipped, so re-runs only fill gaps and never re-bill. Run it with the repo venv:

```bash
.venv/bin/python assets/sfx/generate.py
```

**Constraints:** `duration_seconds` must be **0.5–30s** (the API 422s below 0.5);
`prompt_influence` is 0–1 (higher = more literal). Set `loop: true` for seamless ambiences.

To add effects: append `(slug, category, prompt, duration, prompt_influence, loop, usage)` tuples
to the `SPECS` list in [`generate.py`](generate.py), keep slugs unique, and re-run — it merges new
entries into `manifest.json` and leaves existing files untouched. Keep this catalog block in sync
with the manifest when you do.
