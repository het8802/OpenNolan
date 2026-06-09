# OpenNolan Kinetic Whiteboard Captions

Session-derived implementation note for turning Instagram Reel reference analysis into a reusable OpenNolan style/skill.

## When this applies

Use when the user likes a faceless Reel where the voiceover drives kinetic text, floating UI/product cards, clean off-white background, and a keyword CTA, and asks to replicate that motion/background/flow in OpenNolan.

## OpenNolan artifacts to create

Create or update a class-level Layer 2 creative skill plus a style playbook, not a one-off project note:

- `skills/creative/kinetic-whiteboard-captions.md`
- `styles/kinetic-whiteboard-captions.yaml`
- `skills/INDEX.md` entries for both the creative skill and style playbook

## Skill content to preserve

The creative skill should teach:

- Treat captions as the lead actor, not as ordinary subtitles.
- Use a matte off-white/light-gray canvas with faint texture/grid, black active text, light-gray inactive text, muted red/yellow accents, soft-shadow floating cards.
- Structure: word-built hook → numbered section card → problem micro-list → oversized payoff phrase → repeat → keyword CTA/product mockup/follow gate.
- Motion vocabulary: `gray-to-black`, `snap-up`, `scale-pop`, `black-pill`, `stacked-payoff`, `blur-wipe`, `card-float`.
- Timing workflow: VO first → word/phrase timings → reveal each phrase slightly before or exactly on spoken stress → subtle SFX on section titles/payoffs.
- Runtime guidance: HyperFrames for HTML/CSS/GSAP kinetic typography; Remotion for React timeline, deterministic frame math, and existing word-level caption components. If both runtimes are available, OpenNolan requires presenting both before locking runtime.
- QA: render contact sheet every 1s, watch at phone size, check safe zones and CTA legibility under Instagram UI.

## Style playbook shape

The YAML should validate against OpenNolan `schemas/styles/playbook.schema.json` and include:

- `identity.name: "Kinetic Whiteboard Captions"`
- `identity.category: motion-graphics`
- `identity.pace: rapid`
- `visual_language.color_palette.background: "#F6F6F1"`
- black primary text, muted gray inactive text, muted red/yellow accents
- typography around Inter Tight / condensed bold sans
- motion rules for phrase-level snap-up, gray-to-black activation, scale-pop payoff, fast blur-wipe transitions
- quality rules requiring meaningful visual change every 0.5–1.5s and active/inactive/payoff hierarchy

## Validation commands used in OpenNolan

Run from your OpenNolan checkout after writing the files:

```bash
python - <<'PY'
import json, yaml
schema=json.load(open('schemas/styles/playbook.schema.json'))
playbook=yaml.safe_load(open('styles/kinetic-whiteboard-captions.yaml'))
import jsonschema
jsonschema.validate(playbook, schema)
print('style schema validation: OK')
PY

python - <<'PY'
from styles.playbook_loader import load_playbook
pb=load_playbook('kinetic-whiteboard-captions')
print(pb['identity']['name'])
print(pb['identity']['best_for'])
PY
```

## Production run pattern learned from the 2026-05-28 Proof Loops Reel

When the user asks to create the actual video from a daily script using this style, a workable OpenNolan/HyperFrames path is:

1. Read the daily script/brief, especially the optional word-for-word VO and on-screen text ideas.
2. Present Remotion vs HyperFrames if both are available; lock HyperFrames only after explicit approval because OpenNolan treats runtime choice as a proposal-stage contract.
3. Generate narration first, then transcribe it with word timestamps. Use those timings to drive phrase-level reveals.
4. Scaffold a dedicated project workspace under `projects/<slug>/hyperframes/`; do not stage this in `remotion-composer/public/`.
5. Author a single 1080×1920 root composition with:
   - matte off-white background + faint grid/noise
   - persistent small accent motifs such as red starbursts
   - one timed scene per script beat
   - kinetic phrase spans for key words only, not full subtitles
   - floating UI/proof cards and one bold CTA card
6. Run `npm run check` / `npx hyperframes lint && validate && inspect` before rendering.
7. Render a draft MP4, then verify with `ffprobe`, create a contact sheet, and visually inspect at phone scale before delivery.
8. Copy the final MP4 and contact sheet into your marketing pipeline media cache and send both.

## HyperFrames implementation pitfalls for this style

- **Do not put custom word timing in `data-start` on text spans.** HyperFrames interprets `data-start` as a timed clip attribute and will warn that spans are missing `class="clip"` / stable timeline semantics. Use a custom attribute like `data-kstart` for kinetic word timings and read `el.dataset.kstart` in GSAP.
- **If generating HTML from Python templates, verify brace escaping.** Double braces left in CSS/JS (`{{`, `}}`) produce invalid inline script syntax and missing timeline initialization findings. Run lint immediately after writing.
- **Keep `window.__timelines = window.__timelines || {};` as literal JS before assigning `window.__timelines['main']`.** Lint checks this statically.
- **Avoid CSS transform + GSAP transform conflicts.** If GSAP animates `scale` or `rotation`, do not also set `transform: scale(...) rotate(...)` in CSS for the same element; use size/position CSS plus GSAP properties.
- **Avoid abrupt payoff pops.** The first production draft used a `0.09s` scale yoyo after a `0.18s` word reveal, which made payoff words feel jumpy even though the design vibe was right. For InsiderForce-style kinetic captions, prefer a soft settle: reveal over roughly `0.28–0.36s`, start `0.06–0.10s` before the spoken stress, use smaller initial motion (`translateY` about `12–18px`, blur about `2–3px`), scale payoff text only to about `1.03–1.04`, then settle back to `1.0` over `0.18–0.25s`. Avoid large yoyo/bounce unless the user explicitly asks for punchy meme energy.
- **Do not let ambient drift fight entrances.** Start floating card drift only after cards have settled. Prefer `power3.out`/`sine.inOut` float-in and drift; avoid `back.out` overshoot on proof cards for this premium whiteboard style because it reads abrupt/janky at phone size.
- **Blur/opacity transition overlays can trigger contrast warnings because hidden/inactive scenes are still sampled.** If lint has zero errors and `inspect` has zero layout issues, use contact-sheet/phone-size visual QA to decide whether contrast warnings are a false positive from inactive/blurred words or an actual readability problem.
- **On low-memory hosts, render HyperFrames with one worker.** Use `npx hyperframes render --output <file> --quality draft --workers 1` for a reliable first render, then upgrade quality only if needed.
- **Warnings about dense Track 1 are acceptable for a quick draft**, but reusable production templates should eventually split coherent scenes into sub-compositions under `compositions/`.

## Delivery note

If the user asks to “send it to me,” package the OpenNolan skill and style playbook into a media-cache archive and include it as `MEDIA:/...` in the reply. If the user asks for an actual video, deliver both the MP4 and a QA contact sheet. Do not treat this as a commit/push unless the user explicitly asks to publish repository changes.
