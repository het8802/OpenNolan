# Edit Director — instagram-fast-reel (THE HEART)

Assemble the fast reel: cut the talking head tight, then layer keyframe animations, meme GIFs,
motion-graphic captions, energy ops, SFX, and music onto `edit_decisions`. Carry `render_runtime`
from the brief UNCHANGED. The output must validate against
`schemas/artifacts/edit_decisions.schema.json` (a Save must never 422).

## Build order

1. **Cut the talking head fast.** Turn the script's kept-spans into `cuts[]` — each kept span is
   a cut of the source clip (`source` = the talking head, `[start,end]` = the span). Use
   `silence_cutter` to remove dead air/filler at the brief's pacing-energy threshold, or trim to
   the script's cut spans via `video_trimmer`. **Audio is cut WITH the video** — no dead air left
   in the VO. Prefer hard jump cuts; the hook span is `cuts[0]`.
   - Hide each jump cut with EITHER a fast whoosh SFX, a quick punch-in (`motion_ops` `pan_zoom`
     on the incoming cut), or a short `cuts[].transition_in` (e.g. a 0.15–0.3s dissolve). Don't
     overuse dissolves — hard cuts are what read as "fast".

2. **Beat-align (only if music-led).** If the brief wants cuts on the beat, run `beat_cutter`
   with `mode="speech_safe"` and the kept-span narration as `protected_ranges` so a cut never
   chops a spoken word. For a talking head, speech safety wins over strict beat-snapping.

3. **Keyframe animations (Greg Isenberg motion grammar).** For each emphasis the script planned,
   run `keyframe_animate` on the `text_card_gen` PNG → writes `overlays[].keyframes` (nested; `t`
   is ABSOLUTE project seconds on the CUT timeline). Use the **Greg motion vocabulary** from
   `styles/greg-isenberg-product-explainer.yaml`: rapid **pop + settle** (scale-from-0.8 back-out +
   short slide, 0.28–0.45s), `phrase-collage-build` for multi-word phrases, `connector-draw` for
   diagrams, `receipt-stack-in` for proof cards, a subtle 2–4% camera push-in on holds. Emphasis
   words scale only to 1.03–1.06 with a clay/coral (or amber/green) color hit. NO meme bounces,
   yoyo, or glitch. Convert the trigger-word timestamp to cut-timeline time — the reveal lands
   **ON** the trigger word, never before.

4. **Meme GIFs.** Place each GIF as an overlay with entrance + hold + exit keyframes at its
   reaction moment (scale/opacity in, hold, out). Size it for the frame; keep it clear of the
   face and the caption band. Carry the GIF's `attribution` into `metadata` for export.

5. **Motion-graphic captions (Anthropic editorial look).** Wire the `subtitle_gen` track into
   `subtitles` (word-by-word / karaoke per the brief). Style per the fixed theme: **Inter** for
   the running word-by-word captions, **Fraunces** for punch-word / card captions; clay/coral (or
   amber/green) emphasis hits on slate ink. For extra-punch caption cards, add `text_card_gen`
   PNGs as keyframed overlays (using the Greg motion in step 3). **Never bottom-pin captions into
   the platform UI shadow zone** — anchor in the safe band (roughly the lower third but above the
   IG/TikTok chrome).

6. **Energy ops.** Apply the script's planned ops via `motion_ops` (each outputs a NEW clip,
   re-probed and registered in the asset_manifest, which becomes that `cuts[].source`):
   punch-in/pan on a beat (`pan_zoom`), speed-up on a slow ramble (`cuts[].speed` for a constant
   factor; `motion_ops` for freeze/reverse the scalar can't express), freeze on a punchline.

7. **Audio.** Set `audio.music` with `ducking` so it sits under the VO; seat `sfx_kit` hits on
   the hard cuts and animation reveals.

8. **Canvas.** Set `metadata.compose_target: {width: 1080, height: 1920}` for the 9:16 export.

## Validate
Must validate against `edit_decisions.schema.json`. Keyframed overlays + GIF overlays + captions
all validate; motion-ops derivatives registered. Do NOT change `render_runtime`.

## Quality bar
Pacing is genuinely fast (dead air gone, hard jump cuts, hook first); planned animations/GIFs/
captions all present and synced to their trigger words; SFX on the cuts; music ducked. This
`edit_decisions.json` is also the live doc the user hand-tunes in the Studio editor — keep it
clean and schema-tight. Auto-proceed to `compose`.
