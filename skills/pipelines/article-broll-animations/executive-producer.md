# Executive Producer — article-broll-animations

## What this pipeline makes
Source-backed news/AI explainer **Reels** (9:16, 1080×1920) in the warm editorial
**Greg-Isenberg** motion-graphics style. The signature is a **narration-to-proof evidence chain**:
every factual sentence is verified against a real article first, then shown on screen as a
faithful **source-receipt card** with a **marker-sweep highlight** on the exact verified phrase —
interleaved with **real stock B-roll** and **animated motion-graphics beats** (invoice slams,
racing counters, leaderboards, router diagrams). Rendered as a **deterministic, props-driven
custom vertical Remotion composition** with ElevenLabs VO + warm music + a cohesive, restrained
premium SFX set.

Use it when the brief is: "make a Greg-style animated reel about [news/AI/startup story] with
real B-roll and highlighted articles where the claims are verified."

## Canonical reference implementation
The validated production this pipeline was distilled from:
- Project: `projects/the-500m-claude-bill/` (artifacts, scripts, assets, renders, qa)
- Composition: `remotion-composer/src/Claude500MReel.tsx` (registered in `Root.tsx`, 1080×1920)
- Reusable design primitives live inside that file: `Phrase`, `Highlight`, `Pill`, `ReceiptCard`,
  `BrollBg`, `Stage`, palette `C`, Fraunces+Inter fonts. **Copy these as the starting kit.**
- Asset-gen scripts to copy/adapt: `projects/the-500m-claude-bill/scripts/{gen_vo.py,retempo.py,get_broll.py,gen_sfx_v2.py}`
See `REFERENCE.md` in this folder for the full command log.

## Stage flow
`research → proposal → script → scene_plan → assets → edit → compose → publish`

Creative stages (proposal, script, scene_plan) and publish gate on human approval; the rest
auto-proceed. Read each stage's director skill BEFORE doing that stage's work.

Also read these creative skills up front — they carry the depth this pipeline relies on:
- `skills/creative/greg-isenberg-product-explainer.md` (visual language, motion vocabulary, palette)
- `.agents/app/skills/source-backed-reel-evidence-montage/SKILL.md` (claim→source→crop→highlight chain)
- `.claude/skills/instagram-reels/SKILL.md` (hooks, retention, claim discipline)

## Non-negotiable cross-cutting rules (the hard-won lessons)
1. **Verify before you build.** No factual claim goes on screen without a real source URL, an exact
   quote, and a confidence label. This is the research gate — never skip it. Spreading unverified
   claims about real companies is the failure this pipeline exists to prevent.
2. **Claim integrity in copy.** "Reported ≠ verified." Name the source. Show derived numbers (token
   math, $/anything) as a **labeled range with a footnote**, never as an exact assertion.
3. **Highlight the exact verified phrase**, captured verbatim in research. The marker sweep is
   evidentiary, not decorative — one or two phrases per card, source/date/URL legible at phone size.
4. **Runtime is locked at proposal** (present both Remotion + HyperFrames; recommend Remotion here).
   Never swap silently. This pipeline's scene stack assumes Remotion.
5. **Run `npx remotion …` from INSIDE `remotion-composer/`.** From the repo root it fails with
   `npm error could not determine executable to run` (Remotion lives in `remotion-composer/node_modules`).
   This bit the reference build — always `cd remotion-composer && npx remotion render …`.
6. **VO duration manifest is the single source of truth for timing.** Generate per-scene VO, probe
   each clip, derive scene windows from it. Tempo-adjust (pitch-preserved `atempo`) to hit the target.
7. **Aesthetic:** warm editorial base; dark-drama hits ONLY on the cost/burn/danger beats. Not full
   dark/neon (see the saved reel-aesthetic preference).
8. **SFX:** cohesive, restrained, premium. Avoid cartoonish dings/sparkles/pops. Reserve SFX for
   transitions and reveals, not every caption word.
9. **QA is mandatory** before delivery: ffprobe + `ffmpeg -f null` decode + blackdetect + a contact
   sheet visually inspected scene-by-scene. Render stills first after any layout change.

## Cost shape (typical 60s reel)
Under ~$1: VO ~$0.05, SFX ~$0.10, a few optional FLUX texture stills ~$0.30; B-roll (Pexels/Pixabay)
and rendering are free/local.

## Orchestration
Budget default $2.00. Max 3 revisions/stage, 2 send-backs, ~20 min wall time. Use `meta/reviewer`
after each stage (advisory) and `meta/checkpoint-protocol` for the approval gates. Create the project
workspace under `projects/<kebab-title>/` at init.
