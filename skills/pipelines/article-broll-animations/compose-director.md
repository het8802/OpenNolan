# Compose Director — article-broll-animations

## Purpose
Build the **custom vertical Remotion composition** (1080×1920), render it, and QA it. Output a
`render_report` + `final_review`. This pipeline does NOT use the generic `Explainer` cut-schema
(that's 16:9 and generic) — it authors a dedicated, props-driven vertical composition.

## 0. Runtime routing — check `render_runtime` FIRST
Compose routes by the `render_runtime` locked in `edit_decisions` at the proposal stage. This
pipeline's composition stack (props-driven vertical scenes, source-receipt cards, leaderboard
charts, word-timed reveals) is built on Remotion, so the proposal normally locks
`render_runtime="remotion"`. HyperFrames parity for this composition style is deferred — see
`skills/core/hyperframes.md` for what stays Remotion-only.

- If `edit_decisions.render_runtime` is missing or anything other than `remotion`, STOP. Surface
  the conflict to the user, route the decision back to the proposal stage to re-lock the runtime
  (logged as a `render_runtime_selection` correction in `decision_log`), then resume.
- Never silently rewrite `render_runtime` in `edit_decisions`, and never silently default to
  Remotion without the locked decision — the runtime the user approved is part of the contract.

## 1. Author the composition (copy the reference)
- Start from `remotion-composer/src/Claude500MReel.tsx`. Reuse its design-system primitives:
  `Phrase`, `Highlight`, `Pill`, `ReceiptCard`, `BrollBg`, `Stage`, palette `C`, and the
  Fraunces+Inter font loads. Author topic-specific scene components for this reel's beats.
- Article highlight = animate `Highlight` / `ReceiptCard` marker width 0→100% across the verified
  phrase; show source context first, then push-in to the highlight.
- Use per-scene `<Sequence from durationInFrames>` and read a LOCAL `useCurrentFrame()` inside each
  scene (frame 0 = scene start). Do NOT mix absolute frame gates inside a `<Sequence from>`.
- Audio: continuous `<Audio>` narration from frame 0, music with a volume-curve fade, SFX as
  `<Sequence from>`-wrapped `<Audio>`.

## 2. Register in Root.tsx
Add a `<Composition id="…" width={1080} height={1920} fps={30} component={…}
defaultProps={…} calculateMetadata={…} />`. `calculateMetadata` returns
`{ durationInFrames: ceil(lastEnd*30), width:1080, height:1920, fps:30 }`. Bake `edit_decisions`
(scenes/sfx/audio) into `defaultProps`.

## 3. Stage assets
Remotion only reads via `staticFile()` from `remotion-composer/public/`. Copy narration, music,
sfx, and B-roll into `remotion-composer/public/<project>/…` and reference those paths.

## 4. Render — RUN FROM THE COMPOSER DIR
**The #1 gotcha:** `npx remotion …` MUST run from inside `remotion-composer/`. From the repo root it
fails with `npm error could not determine executable to run` (Remotion is in
`remotion-composer/node_modules`). A background render launched from the wrong cwd silently leaves the
OLD mp4 in place.

```bash
# stills first after ANY layout change (cheap bug catch):
cd remotion-composer && npx remotion still <CompId> ../projects/<name>/qa/s-<n>.png --frame=<n> --scale=0.5
# then the full render:
cd remotion-composer && npx remotion render <CompId> /abs/path/<name>.mp4 --codec=h264 --crf=18
```

Fonts: `@remotion/google-fonts/Fraunces` and `/Inter` are installed. Node ≥22 available.

## 5. QA (MANDATORY)
```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,duration -of json OUT
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,duration -of json OUT
ffmpeg -v error -i OUT -f null -                      # decode check
ffmpeg -hide_banner -i OUT -vf blackdetect=d=0.4:pic_th=0.98 -an -f null -   # no long black
ffmpeg -y -i OUT -vf "fps=1/3.3,scale=216:-1,tile=5x4" -frames:v 1 qa/contact-sheet.jpg
```
Then **visually inspect the contact sheet scene-by-scene**: invoice/hook clean, each receipt card
legible with the highlight on the right phrase, counters/leaderboard/router readable, payoff lands.
Confirm 1080×1920, fps, duration ≈ target, audio present, no black segments.

## Output: render_report + final_review
Output path, encoding, spec, audio summary, QA results, per-scene self-review notes, and a
claim-integrity check (labels/ranges/attribution preserved in the final).

## Common pitfalls
- Running `npx remotion` from the repo root (silent failure / stale mp4).
- Forgetting to stage assets into `public/` (black frames / missing files).
- Wrapping absolute-frame-gated scenes in `<Sequence from>` (blank scenes).
- Non-contiguous scene windows → black flashes in VO gaps (fix in edit stage).
- Skipping the contact-sheet review — a render isn't done until every scene is eyeballed.
