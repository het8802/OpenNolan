# Remotion Short-Video Production Notes

Condensed learning from producing Marketing OS videos after user feedback that earlier outputs were bland.

## Approved creative bar

The user approved the Idea 10 Remotion v3 direction as “much better.” For future short-form Marketing OS videos:

- Start with a storyboard, not a full-script caption pass.
- Generate/source multiple 9:16 visuals per script; avoid one-image videos.
- Use GPT-image-2 freely when available, but keep generated visuals free of readable text/logos.
- Research/adapt free Remotion template patterns per video; never reuse one fixed template, exact animation package, cut rhythm, or scene order across scripts.
- Treat templates as inspiration/pattern libraries, not house style. The visible edit must feel fresh each time: change the metaphor, palette, typography rhythm, transition language, camera motion, SFX rhythm, and CTA treatment.
- If two videos start sharing the same progress bars, floating tags, glitch titles, cuts, or timing, stop and redesign one before rendering. Same animations make the content feel rotten/stale.
- Use kinetic typography, fast scene changes, animated process cards, progress bars, floating UI tags, flashes/particles, and layered audio/SFX only when they fit the specific concept — do not apply the full same checklist mechanically.
- Make each script visually distinct: courtroom, editorial office, real build-in-public desk, proof/HUD dashboard, etc.

## Practical workflow

1. Read the script and extract 5–8 beats: hook, problem/evidence, diagnosis, system/fix, proof/payoff, CTA.
2. Create art direction per idea before generating images; preserve a distinct palette/metaphor. Include a "freshness delta" note: what will be different from the last videos in animation style, cuts, typography, SFX rhythm, and CTA treatment.
3. Generate one visual per beat plus optional impact/CTA visuals.
4. Before coding, pick a concept-specific Remotion motion language (e.g. documentary jump-cuts, mock courtroom, product-demo UI, chaotic collage, cinematic proof vault, news ticker, tutorial whiteboard) and avoid reusing the last video's structure.
5. Build or reuse a Remotion project with one composition per video when batch-rendering several scripts, but keep shared components low-level and restyle/remix them aggressively.
6. Layer generated images with Remotion animations: parallax, zoom/pan, whip transitions, glitch/impact text, tags, and progress meters only where they support that video's metaphor.
7. Add voiceover plus bed/SFX. Locally synthesized SFX are acceptable; paid SFX needs approval.
7. Render at social-safe vertical dimensions; scaled renders are acceptable if verified.
8. Verify before delivery:
   ```bash
   ffprobe -v error -show_entries format=duration,size -of default=nw=1 video.mp4
   ffmpeg -v error -i video.mp4 -f null -
   ffmpeg -hide_banner -nostats -i video.mp4 -vf "blackdetect=d=0.25:pic_th=0.98" -an -f null -
   ffmpeg -y -loglevel error -i video.mp4 -vf "fps=1/6,scale=270:480,tile=3x3:padding=8:margin=8" -frames:v 1 contact-sheet.jpg
   ```
9. Inspect contact sheets and spot-check suspicious timestamps with direct frame extraction before deciding there is a real blank frame. When a fixed-interval contact sheet shows blank/duplicate tiles, build a second scene-midpoint contact sheet from the storyboard timestamps so every tile corresponds to an intended beat; unused contact-sheet slots can look like blank rendered frames.

## Remotion pitfalls discovered

- `useCurrentFrame()` timing depends on where the hook is called. If the component calling it is outside a `<Sequence>`, the value is global and you must subtract `scene.start`; if the hook is inside a component mounted by the sequence, it is sequence-relative. Misreading this can hide overlays or create blank/dark frames.
- When copying an existing Remotion project, copy `eslint.config.mjs` and `tsconfig.json` too; `package.json`/`node_modules` alone can make `npm run lint` fail under ESLint 9 (`couldn't find an eslint.config` file).
- Contact sheets can create false blank-frame alarms when tile sampling catches a fade/transition or unused sheet area. Confirm with `ffmpeg -ss <time> -frames:v 1 frame.png` and/or `blackdetect`.
- `video_analyze` may not be able to inspect local file paths reliably. Use contact sheets, frame extraction, `ffprobe`, decode verification, and `blackdetect` as the grounded QA path.

## Batch production notes

For multiple approved scripts, a single Remotion project can define multiple `<Composition>` entries with shared components and per-video scene data. Use this only for engineering efficiency, not visual sameness. Shared components should be primitives (image bed, caption, audio, transition helper), and each composition should override animation style, cuts, color, typography, scene layout, and CTA so the batch does not look templated.
