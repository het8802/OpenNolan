# Publish Director — Talking Head Pipeline

## When to Use

You have a render report with the final video. Your job is to prepare metadata, thumbnails, and an export package for publishing.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/publish_log.schema.json` | Artifact validation |
| Prior artifacts | Render report, Brief | Video file and context |

## Process

### Step 1: Generate Metadata

Create platform-specific metadata:
- **Title**: Based on the brief's title and hook
- **Description**: Summary of the content with relevant keywords
- **Tags**: Derived from brief's key_points
- **Chapters**: From script section timestamps

### Step 2: Thumbnail Concept

Describe or generate a thumbnail:
- Extract a compelling frame from the footage (if frame_sampler available)
- Add text overlay concept (title or key stat)

### Step 3: Package Export

Create the export directory:
- Video file
- Metadata JSON
- Description text file
- Chapter markers
- Thumbnail concept

### Step 4: Build Publish Log

Document the publish event with platform, status (draft), and export path.

### Step 5: Self-Evaluate

| Criterion | Question |
|-----------|----------|
| **Metadata quality** | Is the title compelling and description informative? |
| **Completeness** | Is the export package complete? |

### Step 6: Submit

Validate the publish_log against the schema and persist via checkpoint.

## Content signal (optional, advisory)

Before finalizing, you MAY offer a predicted virality signal on the finished render via the
`content_signal` tool (Meta TRIBE v2 on Replicate):

- **Opt-in only** — ask first ("Want a predicted virality score before publishing?").
- **Announce the paid call** (Replicate, ~$0.40/run and ~7 min — the model is slow) per AGENT_GUIDE before running.
- **Confirm-gated in code** — the tool refuses a fresh paid run unless called with `confirm: true`. Run `dry_run` first (it reports a cache hit → `$0.00` and the exact cost without spending), announce, then pass `confirm: true`. Headless/batch: set `CONTENT_SIGNAL_AUTOCONFIRM=1`. If a run times out client-side the prediction keeps running server-side — re-call with `use_cache: true` (auto-resumes the same prediction) or `resume_prediction_id`, never a plain re-run (avoids paying twice).
- **Short-form only** — auto-skips if the render is >60s (founder reels qualify, long talks do
  not); needs `REPLICATE_API_TOKEN`.
- **Advisory only** — the 0-100 headline + `sub_scores` + per-step `timeline` inform the user and
  NEVER block publishing. Produces a `content_signal_report` artifact (cached by file hash).
