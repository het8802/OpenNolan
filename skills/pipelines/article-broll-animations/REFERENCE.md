# Reference build — "The $500M Claude Bill"

This pipeline was distilled from a validated production the user approved. Use it as the working
template: copy, swap content, re-render.

## Artifacts
- Project workspace: `projects/the-500m-claude-bill/`
  - `artifacts/{script.json, scene_plan.json, render_report.json}`
  - `scripts/{gen_vo.py, retempo.py, get_broll.py, gen_sfx_v2.py}` ← copy/adapt these
  - `assets/{audio, broll, music, screenshots}`, `renders/the-500m-claude-bill.mp4`, `qa/contact-sheet.jpg`
- Composition: `remotion-composer/src/Claude500MReel.tsx` (registered in `remotion-composer/src/Root.tsx`)
- Staged assets: `remotion-composer/public/the-500m-claude-bill/`

## Reusable design primitives (in Claude500MReel.tsx)
Copy these into a new reel's composition; they ARE the Greg-editorial kit:
- `C` — warm palette (paper, paperWarm, forest, mint, teal, coral, gold, charcoal, darkHit…)
- `Phrase` — designed caption (translateY + fade + blur settle)
- `Highlight` — marker-sweep behind a phrase (animated width 0→100%)
- `ReceiptCard` — faithful source card (browser chrome + masthead + headline + date + URL + quote
  with highlight) ← the article-proof signature
- `Pill`, `Stage`, `BrollBg`, `NodeBox`, `Bar` — supporting components
- Fonts: `@remotion/google-fonts/Fraunces` (serif) + `/Inter` (sans)

## Scene archetypes used (swap per topic)
hook invoice-slam (dark) · reported-disclaimer + article card highlight · reframe morph ·
racing counter (dark) · leaderboard bars · split source cards w/ highlights · router diagram ·
moral-reset payoff with gold sweep.

## Command log (the path that worked)
```bash
# VO (per-scene + manifest), then tempo to target
python projects/<name>/scripts/gen_vo.py
python projects/<name>/scripts/retempo.py            # atempo ~1.18x, rescales manifest

# real portrait B-roll (Pexels) + premium SFX (ElevenLabs REST) + music
python projects/<name>/scripts/get_broll.py
python projects/<name>/scripts/gen_sfx_v2.py         # SFX dur >= 0.5s; eleven_text_to_sound_v2

# stage assets into public/, then RENDER FROM THE COMPOSER DIR
cd remotion-composer
npx remotion still  <CompId> ../projects/<name>/qa/s.png --frame=720 --scale=0.5   # stills first
npx remotion render <CompId> /abs/projects/<name>/renders/<name>.mp4 --codec=h264 --crf=18

# QA
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration -of csv=p=0 OUT
ffmpeg  -v error -i OUT -f null -
ffmpeg  -hide_banner -i OUT -vf blackdetect=d=0.4:pic_th=0.98 -an -f null -
ffmpeg  -y -i OUT -vf "fps=1/3.3,scale=216:-1,tile=5x4" -frames:v 1 ../projects/<name>/qa/contact-sheet.jpg
```

## Gotchas confirmed during the build
- `npx remotion` from repo root → `npm error could not determine executable to run`. Always `cd remotion-composer` first.
- ElevenLabs Python SDK not installed → call the REST API with `requests`.
- ElevenLabs sound-generation `duration_seconds` must be ≥ 0.5.
- Live article screenshots needed the Chrome extension (not connected) → faithful `ReceiptCard`s
  were used instead and looked better at phone size. Offer real screenshots if the extension is connected.
- VO ran long (76s) vs a 60s target → `atempo 1.18x` (pitch-preserved) to ~65s.
