# Asset Director — anthropic-style-animated-talking-head

## When to Use
You have an approved `scene_plan`. Build every asset it references and produce the `asset_manifest`.
Read Layer 3 skills first: **`hyperframes`** (authoring contract), **`editorial-ai-product-design-system`**
(the design language), and `elevenlabs`/`ai-video-gen` only if generating audio/images.

## Prerequisites
| Layer | Resource |
|-------|----------|
| Scene plan | per-beat shot_mode + specs + trigger-word timings |
| Research brief | claims: highlight phrases, screenshots, logos, attribution |
| Playbook | `styles/anthropic-editorial-animated.yaml` |
| Tools | HyperFrames CLI, `web_image_search`, `webpage_screenshot`, `image_selector`, `subtitle_gen`, `music_*`, `audio_mixer` |

## HyperFrames workspace (validated layout)
```
projects/<name>/hf/
  fonts/                 # Fraunces-600/700/900 + Inter-400/600/700/900 (copied .ttf, local)
  logos/  -> ../assets/logos        screenshots/ -> ../assets/screenshots   img/ -> ../assets/images
  <comp>/index.html      # ONE composition per subdir (lint/render operate on a project DIR)
  <comp>/fonts -> ../fonts
```
- `npx hyperframes lint <comp>` and `render <comp>` operate on a directory containing `index.html`.
- GSAP 3.14.2; `window.__timelines["<id>"]=tl`; deterministic (no Date.now/Math.random).
- **Full-frame cutaways** (`animation_full`) → render `.mp4` (1080×1920).
- **Alpha overlays** (`talking_head_overlay`, claim overlay cards) → render `--format mov` (ProRes 4444, transparent bg).
- **split_5050 top panel** → author at `data-width=1080 data-height=960`, render `.mp4`.

## Build rules
1. **Fonts.** Copy Fraunces (600/700/900) + Inter (400/600/700/900) `.ttf` into `hf/fonts`
   (reuse `assets/greg-style-kit-expanded/fonts`). **@font-face** them. Titles/labels = Fraunces;
   eyebrows/sub/pills/numerals = Inter. (Do NOT set box/card titles in heavy Inter.)
2. **Design.** Ivory radial bg; clay/coral + slate; green=public/approved, amber=gated/caution.
   Soft shadows; rounded cards/pills; drawn SVG connectors/sunburst; reveals on trigger words.
3. **Counters / charts** are deterministic (gsap `to({v:N}, onUpdate)` seek-safe); numbers Inter tabular.
4. **claim_proof receipts.** Build a faithful **browser article card**: window chrome (3 dots) + a
   URL pill (`🔒 <domain>/<path>`), the **real article screenshot as a banner** (object-fit cover,
   top), a source/date row, the real headline (Fraunces), and a quote line whose `highlight_phrase`
   is wrapped in a clay marker (a `.marker` bar behind the text, `scaleX 0→1` sweep) plus a
   `✓ VERIFIED · <domain>` eyebrow.
   - For `sequenced_after_animation` / `full_frame_receipt`: author the card **full-frame on opaque
     ivory** (it's its own beat) and render **.mp4** (the card slides up + the marker sweeps on cue).
     Do NOT build it as a transparent overlay meant to sit on top of an animation — claims are
     SEQUENCED after the animation, not stacked on it (user-rejected double-stacking).
   - For `overlay_card` (compact citation only): a small transparent card → **.mov** to sit over the
     TH / negative space.
   Add `attribution` for `reported` claims. **Never fabricate a publication screenshot for an
   `unverified` claim** — use a neutral designed card.
5. **Logos** via `web_image_search` (`type_image: transparent`); QA each (DDG can mis-hit); seat
   wordmark logos on light chips. **Real photos/articles** via `webpage_screenshot` / `image_selector`.
6. **Talking-head segments** are produced in compose, not here — but record `face_crop_y` and the
   crop/scale-only rule (NO color flags) so compose honors it.
7. **Audio.** The VO is the TH's own track (untouched). Only generate music/SFX if the brief asked;
   keep music ≤ 0.08 under the VO; SFX restrained, on reveals only.

## Output: `asset_manifest`
- assets[]: {id, scene_id, role (cutaway|overlay|split_panel|receipt|logo|screenshot|chart|music|sfx),
  path, type (mp4|mov|png), provider, url?, license?, highlight_phrase?, lint_status}
- layer3_skills_read: [...]

## Self-evaluate
- Every scene_plan asset built; all comps `npx hyperframes lint` clean (0 errors).
- Overlays render to mov (alpha verified yuva), cutaways + split panels to mp4 (1080×1920 / 1080×960).
- Fonts correct (Fraunces titles / Inter sub); receipts carry the exact highlight phrase; provenance recorded.
