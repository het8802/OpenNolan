# Idea Director — animation-talking-head-50-50 Pipeline

## When to Use

You are starting an `animation-talking-head-50-50` project. The deliverable is a vertical animated explainer reel where a talking head shares insights while Greg-style animated panels appear alongside them. Your job: produce the **brief** — the topic, hook, demo beats, layout mode assignments, and the creative direction for what each animated panel must show.

Read the active playbook (`styles/greg-isenberg-product-explainer.yaml` by default) before writing the brief. It defines the visual system (palette, typography, motion, diagram style) you are committing to.

## Three Layout Modes (LOCK THE ASSIGNMENT STRATEGY NOW)

Every beat in the reel will be assigned one of these modes. Lock the assignment strategy here — it drives everything downstream.

| Mode | When to Use | Canvas |
|------|-------------|--------|
| `split_screen_greg` | Beat needs BOTH the speaker's credibility AND a visual proof simultaneously. The gold standard for this format. Complex concepts, evidence beats, comparisons. | Top 55% (1056px): animated Greg panel. Bottom 45% (864px): talking head face. |
| `hero_talking_head` | Emotional impact needs the speaker full-frame. Hook (first 2s), strong personal statements, CTA, and any beat where the speaker's body language carries the message. No more than ~8s before a cutaway. | Full 1080×1920. Animated overlays composited on top (sticker pills, comparison boards, CTA badge). |
| `full_greg_card` | Concept is so visual / multi-part that the speaker needs to step back and let the diagram breathe. Moral-reset moments. Phrase collages that need the full canvas. | Full 1080×1920 animated graphic. Speaker voice continues underneath. |

**Assignment heuristics:**
- Hook beat → always `hero_talking_head` (face + sticker pill overlays)
- Evidence beats (companies, data, examples) → `split_screen_greg` (speaker + proof card)
- Complex diagrams (connector maps, workflow trees) → `split_screen_greg` OR `full_greg_card` depending on complexity
- Moral-reset / key insight → `full_greg_card` (phrase collage)
- Payoff / CTA → `hero_talking_head` (speaker + comparison board + CTA badge)
- Default: prefer `split_screen_greg` — it is the most engaging and the signature of this format

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Manifest | `pipeline_defs/animation-talking-head-50-50.yaml` | Stage contract |
| Playbook | `styles/greg-isenberg-product-explainer.yaml` | Visual language |
| Tool | `ffprobe` on the talking head source | Confirm audio, duration, resolution, color_transfer |
| Schema | `schemas/artifacts/brief.schema.json` | Artifact validation |

## Process

### Step 1: ffprobe the talking head source

Run ffprobe on the provided footage and record:
- `duration_seconds`
- `resolution` (WxH)
- `fps`
- `audio_present` (MUST be true — no audio = cannot proceed)
- `pix_fmt` (e.g., `yuv420p10le` → 10-bit, `yuv420p` → 8-bit)
- `color_transfer` (e.g., `arib-std-b67` → HLG HDR, `bt709` → SDR)

Note: if `color_transfer` is HDR (HLG or PQ), mark `source_is_hdr: true` in the brief metadata. This informs the asset and compose directors to use crop-only FFmpeg processing with NO color conversion.

### Step 2: Write the hook

The hook lands in the **first 2 seconds** and must:
- Name the target audience's pain or payoff
- Be a single confrontational or surprising statement

Example: "I am literally fed up of people building AI agents." (direct, confrontational, instantly relatable to the AI builder audience).

### Step 3: Map the demo beats

Sketch 3-7 beats. For each beat:
- `narration_intent`: what the speaker says / the point being made
- `layout_mode`: which of the three modes
- `animated_panel_content`: what the Greg animation must SHOW (specific enough for the scene director to write HTML)
- `overlay_elements`: for `hero_talking_head` modes, what overlays appear (sticker pills, comparison boards, CTA badge)

Be concrete about animation content. Not "show a diagram" — but "show two nodes: 'AI AGENT' in coral on left, connector draws right, 'NARROW WORKFLOW' in forest green on right, dashed boundary around the right node, 'Human Approval Gate' badge pops in above."

### Step 4: Write the brief

Populate:
- `version`, `title`, `hook`
- `key_points`: the 4-6 main arguments (from the beat outline)
- `tone`: voice/persona of the speaker
- `style`: playbook name (e.g., `"greg-isenberg-product-explainer"`)
- `target_platform`: `instagram` / `tiktok` / `youtube_shorts`
- `target_duration_seconds`: typically 45-90 for this format
- `cta`: comment-bait / link / follow ask
- `metadata`:
  - `talking_head_source`: path to footage
  - `talking_head_meta`: {duration, resolution, fps, audio_present, pix_fmt, color_transfer, source_is_hdr}
  - `render_runtime`: `"hyperframes+ffmpeg"`
  - `playbook`: `"greg-isenberg-product-explainer"`
  - `layout_modes`: describe the three modes as they'll be used for THIS topic
  - `demo_beats[]`: each with {beat, narration_intent, layout_mode, animated_panel_content, overlay_elements}
  - `render_runtime_selection`: decision log entry with options considered

### Step 5: Runtime Selection (MANDATORY per AGENT_GUIDE "Present Both Composition Runtimes")

Present to the user:

> "This format uses a **two-pass render**: HyperFrames builds the animated overlay track (panels, diagrams, captions), and FFmpeg composites the original talking head video in. This is the default because it keeps your footage completely untouched — no color conversion, no codec re-encoding.
>
> **Alternative** (Remotion + FFmpeg): Remotion can animate the panels, but the existing Greg editorial animations (connector draws, receipt stacks, phrase collages) are HTML/CSS/GSAP — authoring them in React would require significantly more custom component work than writing them in HyperFrames HTML.
>
> I recommend **HyperFrames + FFmpeg**. OK to lock that?"

Record a `render_runtime_selection` decision in `decision_log` with `options_considered: [{hyperframes+ffmpeg, selected}, {remotion+ffmpeg, rejected: ...}]`.

### Step 6: Self-evaluate

| Criterion | Check |
|-----------|-------|
| Hook | Confrontational / surprising enough to stop the scroll in 2s? |
| Beats | Does each beat have a concrete, showable animated panel? |
| Layout modes | Is `split_screen_greg` the dominant mode (>60% of beats)? |
| Hero beats | None run > ~8s without a cutaway? |
| Source confirmed | ffprobe ran, audio present, HDR status noted? |
| Runtime | decision_log entry with both options recorded? |

### Step 7: Present and get approval

Present:
1. The hook
2. The beat-by-beat outline with layout modes
3. The animated panel content for each beat (what will actually be drawn/built)
4. Runtime decision
5. Estimated cost (transcription ~$0.01, no generation if playbook assets used)

Wait for human approval before proceeding to the script stage.
