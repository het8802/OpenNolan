# Publish Director - Podcast Repurpose Pipeline

## When To Use

Package podcast-derived clips and companion assets so that every short-form piece points back to the episode instead of drifting as an isolated fragment.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/publish_log.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["compose"]["render_report"]`, `state.artifacts["idea"]["brief"]`, `state.artifacts["script"]["script"]` | Outputs, source truth, chapters |
| Playbook | Active style playbook | Brand voice |

## Process

### 1. Link Every Clip Back To The Episode

Each short-form asset should reference:

- show name,
- episode title or number,
- guest name where relevant,
- full episode destination.

### 2. Tailor The Copy

- Shorts / Reels / TikTok: hook-led and concise
- LinkedIn: insight-led and more contextual
- YouTube companion: chapter-rich and search-friendly

### 3. Sequence The Release

Recommended order:

1. strongest announcement clip
2. next-best insight clip
3. quote-led or guest-led follow-ups
4. remaining supporting clips

### 4. Store Cross-Linking Truth In Metadata

Recommended metadata keys:

- `episode_reference`
- `guest_tags`
- `posting_schedule`
- `clip_to_episode_map`

### 5. Quality Gate

- every clip points back to the episode,
- guest attribution is correct,
- copy matches the platform,
- the release order reflects actual clip strength.

## Common Pitfalls

- Publishing clips without clear episode references.
- Forgetting to tag or mention the guest when that audience matters.
- Reusing one caption style across every platform.

## Content signal (optional, advisory)

Before finalizing, you MAY offer a predicted virality signal on the finished clips via the
`content_signal` tool (Meta TRIBE v2 on Replicate):

- **Opt-in only** — ask first ("Want a predicted virality score before publishing?").
- **Announce the paid call** (Replicate, ~$0.40/run and ~7 min per clip — the model is slow) per AGENT_GUIDE before running.
- **Short-form only** — auto-skips if a render is >60s; needs `REPLICATE_API_TOKEN`. The full
  episode video is typically too long, so this applies to the short clips, not the full cut.
- **Advisory only** — the 0-100 headline + `sub_scores` + per-step `timeline` inform the user and
  NEVER block publishing. Produces a `content_signal_report` artifact (cached by file hash).
