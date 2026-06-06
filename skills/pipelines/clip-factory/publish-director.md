# Publish Director - Clip Factory Pipeline

## When To Use

This stage packages the clip batch into a distribution plan. The goal is not just exported files. The goal is a usable content engine.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/publish_log.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["compose"]["render_report"]`, `state.artifacts["idea"]["brief"]`, `state.artifacts["script"]["script"]` | Outputs, rankings, and goals |
| Playbook | Active style playbook | Brand voice |

## Process

### 1. Lead With The Strongest Clip

Do not schedule by chronology. Schedule by ranking.

The first published clip should usually be:

- the strongest hook,
- the cleanest standalone clip,
- the clip most aligned with the batch goal.

### 2. Tailor Copy By Platform

Each platform needs its own tone and packaging:

- TikTok / Reels: direct, fast, hook-led
- Shorts: searchable, keyword-aware
- LinkedIn: insight-led and more professional
- X: short, punchy, opinion-friendly

### 3. Package The Batch Cleanly

Group by platform and include ready-to-paste text assets, not just video files.

### 4. Preserve Batch Truth

Store in `publish_log.metadata`:

- `clip_catalog`
- `posting_order`
- `platform_copy_map`
- `schedule_notes`

### 5. Quality Gate

- strongest clips lead the rollout,
- captions are platform-specific,
- export folders are usable without extra cleanup,
- the batch catalog clearly links ranking, file paths, and publishing intent.

## Common Pitfalls

- Publishing the whole batch on the same day.
- Using one caption everywhere.
- Losing the rank/order logic after rendering is complete.

## Content signal (optional, advisory)

Before finalizing, you MAY offer a predicted virality signal on the finished clips via the
`content_signal` tool (Meta TRIBE v2 on Replicate):

- **Opt-in only** — ask first ("Want a predicted virality score before publishing?").
- **Announce the paid call** (Replicate, ~$0.40/run and ~7 min per clip — the model is slow) per AGENT_GUIDE before running.
- **Confirm-gated in code** — the tool refuses a fresh paid run unless called with `confirm: true`. Run `dry_run` first (it reports a cache hit → `$0.00` and the exact cost without spending), announce, then pass `confirm: true`. Headless/batch: set `CONTENT_SIGNAL_AUTOCONFIRM=1`. If a run times out client-side the prediction keeps running server-side — re-call with `use_cache: true` (auto-resumes the same prediction) or `resume_prediction_id`, never a plain re-run (avoids paying twice).
- **Short-form only** — auto-skips if a render is >60s; needs `REPLICATE_API_TOKEN`.
- **Per clip** — for a clip batch, score each clip individually (each is ≤60s); the headline
  score can inform the posting order, but it NEVER blocks publishing.
- **Advisory only** — the 0-100 headline + `sub_scores` + per-step `timeline` inform the user.
  Produces a `content_signal_report` artifact per clip (cached by file hash, so re-runs are free).
