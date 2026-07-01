# Asset Director — instagram-fast-reel

Build the pixels + audio the `edit` stage will place. Everything here is driven by the
annotation plan in the `script`. Auto-proceeds (no human gate) — but confirm-gate any
paid/generative call and cache results.

## Design theme is FIXED (read these before building any pixels)
Every asset must land in the pipeline's locked look (manifest `metadata.design_theme`):
- **Surface + typography + captions = Anthropic editorial.** Read the playbook
  `styles/anthropic-editorial-animated.yaml`. Ivory paper (#F0EDE6), slate ink (#1A1A18),
  clay/coral (#CC785C) brand+emphasis, forest-green=approved / amber=gated. **Fraunces** (900)
  for titles/labels/phrases, **Inter** (600) for eyebrows/sub-labels/pills/numerals.
- **Animated motion-graphic beats = Greg Isenberg.** Read `styles/greg-isenberg-product-explainer.yaml`
  and the skill `skills/creative/greg-isenberg-product-explainer.md` for the motion vocabulary
  (receipt-stack-in, phrase-collage-build, connector-draw, prompt→artifact). Keep the Anthropic
  palette; borrow Greg's forest/mint/gold only as the semantic accent inside diagram/receipt beats.
- Read the design-system skill `.agents/skills/editorial-ai-product-design-system` for card /
  mascot / workflow-diagram construction.

## Read Layer 3 first
Also read (record all in `layer3_skills_read`): `instagram-reels` (short-form attention / hook
grammar), `elevenlabs`/`sound-effects` (if generating SFX), `music` (if generating music). If
render_runtime is hyperframes, read `hyperframes` for any authored title/caption cards.

## Build, per annotation type

1. **Keyframe-animation text → `text_card_gen`.** For each emphasis word/phrase the script
   flagged, bake a tight transparent PNG in the Anthropic look — **Fraunces** for the phrase,
   clay/coral (#CC785C) or amber/green emphasis on slate ink, ivory/soft-shadow card where a card
   is used (presets: `bold_center`, `outline_pop`, `black_pill_caption`, `minimal_clean`). The
   card is animated later via `overlays[].keyframes` in `edit`. Keep them small and legible on a
   phone. For richer animated diagram/receipt/prompt-loop beats, build them in the **Greg motion**
   style (on hyperframes runtime, author as HF comps in step 6).

2. **Meme GIFs → `sticker_search`** (GIPHY/Tenor). Run the search query from the script; prefer
   the rendition the tool picks (mp4 for GIFs, alpha gif for stickers). **Carry each result's
   `attribution` string into the manifest** — it's a GIPHY/Tenor API requirement, not optional.

3. **Word-level captions → `subtitle_gen`** from the `transcript`. Produce the caption track
   (word-by-word / karaoke if the brief chose it; else sentence chunks). This is the always-on
   motion-graphic caption layer. Style/position is finalized in `edit` (keep clear of the 9:16
   UI shadow zone).

4. **SFX → `sfx_kit`** over the in-repo `assets/sfx/` library (free, local) — whooshes for cut
   transitions, impacts/pops for animation reveals. Read `skills/creative/sfx-library.md` for
   placement/level. Generate via ElevenLabs only on a library miss.

5. **Music.** Use the brief's music decision: a `music_library/` track, `freesound_music` /
   `pixabay_music`, or `music_gen`. Note a ducking intent so it sits under the VO. Optionally
   `audio_enhance` the talking-head VO if it's noisy.

6. **(hyperframes runtime only)** Author any premium title/caption cards as HyperFrames comps in
   `projects/<name>/hf/` (alpha overlays → `--format mov` ProRes 4444); lint clean before
   handing off. Skip entirely on the ffmpeg runtime.

## Output
`asset_manifest` (schema-valid): every asset with provenance (provider, url/path,
license/attribution, and the trigger reference from the script). GIF attribution present for
every GIPHY/Tenor result. `layer3_skills_read` recorded.

## Quality bar
Every planned annotation has its asset built; captions + SFX + music ready; attribution captured;
paid calls cached. Auto-proceed to `edit`.
