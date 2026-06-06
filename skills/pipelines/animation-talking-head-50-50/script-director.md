# Script Director — animation-talking-head-50-50 Pipeline

## When to Use

You have an approved brief. Your job is to transcribe the talking head footage (using WhisperX or equivalent), align the transcript to the beat outline from the brief, and produce a schema-valid `script` artifact with word-level timestamps and per-section enhancement cues that the scene director will convert into HyperFrames HTML specs.

## Prerequisites

| Layer | Resource |
|-------|----------|
| Brief | `artifacts/brief.json` — beat outline, layout_modes, animated_panel_content |
| Tool | `transcriber` (WhisperX `large-v3` preferred for confidence) |
| Schema | `schemas/artifacts/script.schema.json` |

## Process

### Step 1: Transcribe

Run `transcriber` on the talking head source:
- Use `large-v3` model for best word-level confidence
- Request word-level timestamps (required — captions are generated from these)
- Check avg word confidence: if < 0.80, re-run with a smaller model on just the failing segments

Record the full transcript in `artifacts/transcript.json` for reuse.

**Note for repeat runs:** If the same source footage was used in a prior project (e.g., stop-building-ai-agents-3), check if a transcript already exists before re-running. If `artifacts/transcript.json` with word-level timestamps exists from a prior run, copy it — do not bill another transcription run.

### Step 2: Align transcript to beats

The brief has a `demo_beats[]` list. Map each beat to a transcript range by:
1. Reading the speaker's actual words in the transcript
2. Finding where each beat starts and ends (use timestamp of first word in each beat section)
3. Trimming or merging beat boundaries to clean sentence breaks

When the transcript doesn't perfectly match the brief's beat outline (speaker improvised, beat ran longer, etc.), **the transcript wins**. Adjust the beat boundaries to match what was actually said. Record any significant deviations in the script's `metadata.deviations`.

### Step 3: Write script sections

For each section (one per beat):

```json
{
  "id": "s-NN",
  "label": "beat_name",
  "text": "Exact words from transcript",
  "start_seconds": X.XX,
  "end_seconds": Y.YY,
  "layout_mode": "split_screen_greg | hero_talking_head | full_greg_card",
  "enhancement_cues": [
    {
      "type": "animation",
      "description": "<concrete HyperFrames animation spec: nodes, colors, GSAP eases, element text>"
    }
  ]
}
```

**Tie every cue to its trigger word + timestamp.** Each enhancement cue that reveals a specific element must name the word the speaker says when it should appear and that word's `start` from `transcript.json words[]` (use the `timestamp_seconds` field). The scene director will anchor the GSAP `abs_time` to this — reveals land ON the word, never before it (see scene-director "Narration Sync — HARD RULE"). For a multi-element build (steps, pills, diagram nodes), list each sub-element with its own trigger word in spoken order. Example: `"'SEARCH' pill on the word 'search' (~23.76s), 'COMPARE' on 'compare' (~24.40s), 'ADD TO CART' on 'add' (~24.98s)"`.

**Enhancement cues must be concrete enough for the scene director to write HTML directly.** Do NOT write vague cues like "show a workflow diagram." Write:

> "Top panel on ivory #F5EFE6: left node 'AI AGENT' coral #D96D5F rounded rect with ✗. Forest green connector #173D35 draws right 0.5s ease-out. Right node 'NARROW WORKFLOW' forest #173D35 with ✓. Dashed boundary box around right node strokes in. 'Human Approval Gate' charcoal pill badge pops (scale back.out 1.4) above the connector."

For `hero_talking_head` sections, describe the overlays:
> "Sticker pill 'AI AGENTS ✗' coral slides in from left (delay 0.3s). Sticker pill 'CHATBOTS ✗' coral slides from right (delay 0.5s). Phrase caption 'FED UP' rises 18px at bottom."

For `full_greg_card` sections, describe the phrase collage or diagram in full:
> "Full ivory canvas. 'AGENTIC AI' coral 80px Outfit 900 rises (0.3s), then coral strikethrough draws across (0.35s). Pause. 'AI + TOOLS' forest 76px Outfit 900 rises. Mint arrow '→'. 'ONE PAINFUL WORKFLOW' forest 68px rises, then mint underline draws right."

### Step 4: Self-evaluate

| Criterion | Check |
|-----------|-------|
| Coverage | Sections cover the full footage duration with no gaps |
| Timestamps | Monotonically increasing, within source duration bounds |
| Word-level | transcript.json has words[] with per-word start/end |
| Layout modes | Each section carries the correct layout_mode |
| Enhancement cues | Specific enough to write HTML without guessing |
| Total duration | Matches talking head source duration (±1s) |

### Step 5: Present and get approval

Show:
1. Section breakdown: ID, label, timestamp range, layout_mode
2. Enhancement cue summary for each section (2-3 words describing the animation type)
3. Any deviations from the brief's beat outline

Wait for human approval before proceeding to scene planning.
