# Kinetic Whiteboard Captions — Creative Skill

## When to Use

Use this skill for vertical short-form videos where the voiceover, captions, and floating UI/product cards carry the main storytelling: Instagram Reels, TikToks, Shorts, AI-tool explainers, product tips, creator education, and faceless social videos inspired by clean kinetic typography reference reels.

Best reference pattern: InsiderForce Reel `DYxBWLIHFM5` — a matte white/gray canvas, word-synced caption motion, floating mockups, minimal brand props, and 3-part educational structure.

## Prerequisites

| Resource | Required? | Notes |
|---|---:|---|
| Voiceover script | Yes | Must be clause-based, not paragraph-based. Every clause should have one visual action. |
| Word-level or phrase-level timings | Strongly preferred | Use WhisperX/Whisper/faster-whisper after TTS or recording. |
| Style playbook | Preferred | Use `styles/kinetic-whiteboard-captions.yaml` when available. |
| Composition runtime | Preferred | HyperFrames for HTML/CSS/GSAP kinetic text; Remotion for React timeline and word-level captions. |
| UI/product proof assets | Preferred | Screenshots, command cards, mockups, diagrams, ebook/product card, etc. |

## Core Principle

Do not treat captions as subtitles. Treat text as the lead actor. The viewer should feel the voiceover is building an animated set of premium notes in real time.

## Structure Pattern

Use this structure for most AI/tool explainers:

1. **Hook title builds word-by-word**
   - Start with the concrete number/claim.
   - Make the final payoff phrase largest or highlighted.
   - Example: `3 Claude Code Skills` → `that make you look like` → `a DESIGNER OVERNIGHT`.

2. **Numbered section card**
   - `First`, `Second`, `Third` appear briefly.
   - Main term becomes large and black/all-caps.
   - Pair with a floating card, product screenshot, or phone mockup.

3. **Problem micro-list**
   - 2–4 short fragments, each appearing on a separate beat.
   - Use gray text for setup words and black text for the diagnosis.

4. **Solution/payoff phrase**
   - One large phrase lands as the visual reward.
   - Use stacked words or a black pill/highlight.

5. **Repeat 2–4 for each section**
   - Keep the same visual grammar so pace feels intentional.

6. **Keyword CTA ending**
   - Product/ebook/mockup appears.
   - Keyword is large and quoted: `Comment "HUMAN"`.
   - Follow-gate appears last if needed: `Only sends to FOLLOWERS`.

## Text Motion System

### Tokenize by meaning, not by sentence

Break every VO line into visual chunks:

```text
VO: AI interfaces all have the same tell.
Chunks: [AI interfaces] [all have] [the same tell]
```

For each chunk define:

```json
{
  "text": "the same tell",
  "start": 5.9,
  "end": 7.4,
  "emphasis": "strong",
  "motion": "snap-up"
}
```

### Motion vocabulary

| Motion | Use For | Parameters |
|---|---|---|
| `gray-to-black` | Word activation synced to VO | opacity 0.35 → 1, color gray → black |
| `snap-up` | Normal phrase entrance | y 24px → 0, blur 6px → 0, 6–10 frames |
| `scale-pop` | Key nouns/payoffs | scale 0.92 → 1.04 → 1.0, 8–12 frames |
| `black-pill` | Commands, CTA keywords, important labels | rounded black rectangle appears 2–4 frames before text |
| `stacked-payoff` | Final reward phrase | 2–3 words stacked, each staggered 3–5 frames |
| `blur-wipe` | Section transition | canvas blur 8px + scale 1.02, then reset |
| `card-float` | UI/product proof | y drift 6–14px, rotate -1° to 1°, soft shadow |

### Caption hierarchy

1. **Active phrase:** black, bold, 100% opacity.
2. **Inactive/future phrase:** light gray, 30–45% opacity.
3. **Payoff phrase:** black, extra-bold, 1.5–2.5× size.
4. **CTA keyword/command:** white text in black pill or black text with oversized quote marks.

## Visual Design Rules

- Canvas: vertical 9:16, matte off-white / light gray.
- Texture: faint grid, subtle noise, or paper grain; never busy.
- Safe zones: keep key text away from top UI and bottom caption/action zones.
- Use negative space aggressively. The premium feel comes from restraint.
- Floating proof cards should have soft shadows and slight parallax.
- Accent shapes should repeat across scenes: one starburst, one circle badge, one small object motif.
- Avoid generic robot stock art. Prefer UI mockups, command cards, product cards, diagrams, screenshots, or simple 3D props.

## Voiceover Alignment

1. Generate or record the VO first.
2. Transcribe to word timings.
3. Convert word timings into phrase timings.
4. Reveal each phrase 1–3 frames before the spoken beat when it improves readability.
5. Land payoff words exactly on the stressed spoken word.
6. Add a small pop/whoosh/click on section labels, not on every word.

## Runtime Guidance

### Prefer HyperFrames when

- The brief asks for kinetic typography, HTML/CSS layouts, GSAP staggered text, product promo energy, or clean web-style motion.
- You need SplitText-like effects, blur wipes, card transforms, SVG accents, or reusable registry blocks.

### Prefer Remotion when

- The pipeline already uses React scene types or word-level caption burn.
- You need deterministic frame math, existing Remotion components, charts, terminal scenes, or React UI mocks.

If both are available, follow `AGENT_GUIDE.md`: present both options at proposal time and get explicit approval before locking the runtime.

## Example Scene Plan Snippet

```json
{
  "scene_id": "hook",
  "duration": 3.5,
  "background": "off-white-grid",
  "text_beats": [
    {"text": "3 AI workflows", "start": 0.0, "end": 1.1, "motion": "scale-pop"},
    {"text": "that make you look like", "start": 1.1, "end": 2.1, "motion": "gray-to-black"},
    {"text": "a 10-person team", "start": 2.1, "end": 3.5, "motion": "black-pill"}
  ],
  "support_visuals": [
    {"type": "floating-card", "content": "workflow map", "entrance": "slide-right"}
  ]
}
```

## Quality Rubric

| Check | 1 | 3 | 5 |
|---|---|---|---|
| VO sync | Text drifts or generic subtitles | Phrases mostly align | Key phrases land exactly on spoken stress |
| Text hierarchy | Everything same size/weight | Some emphasis | Clear active/inactive/payoff/CTA hierarchy |
| Motion taste | Random effects | Consistent but slow | Restrained, premium, meaningful motion every 0.5–1.5s |
| Background | Cluttered or generic | Clean but plain | Branded, minimal, textured, supports continuity |
| Proof visuals | Decorative B-roll | Some relevant cards | Every card/screenshot proves the spoken point |
| Mobile readability | Hard to read | Mostly readable | Readable at 720p on a phone with safe zones respected |

## Common Pitfalls

- **Subtitle fatigue:** rendering every spoken word equally. Fix by emphasizing only the active/key phrase.
- **Too much motion:** every word bouncing creates chaos. Use small moves; reserve pops for key nouns.
- **No proof layer:** kinetic text alone can feel like a template. Add UI cards, command cards, screenshots, or product mockups.
- **Overfilled frames:** this style works because of empty space. Keep only 1–2 focal elements per beat.
- **Late captions:** if text appears after the VO, it feels sluggish. Reveal slightly early or exactly on beat.
- **Unbranded canvas:** repeat 2–3 small motifs so the video feels like a coherent system.

## Implementation Notes

- Store phrase timing as structured JSON so future scenes can be regenerated.
- For Remotion, implement a `KineticPhrase` component with token-level color, opacity, blur, translateY, and scale states.
- For HyperFrames/GSAP, use timelines with staggered phrase spans, CSS custom properties for colors, and reusable card/CTA components.
- Keep SFX subtle: soft pop, click, short whoosh, and a light riser before CTA.
- Final QA: render a contact sheet every 1s, watch at phone size, and verify the CTA remains readable under Instagram UI overlays.
