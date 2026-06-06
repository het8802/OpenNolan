# Publish Director — article-broll-animations

## Purpose
Package the finished reel for delivery/posting. Output a `publish_log`. Human approval.

## Caption
- Lead with the hook (the same tension as the cover), not a brand recap.
- **Name the sources** and preserve claim integrity: "reported", "per The Verge", "per Fortune",
  derived numbers as ranges. Do not overclaim in the caption.
- End with a save/comment prompt tied to an artifact (e.g. the framework/checklist), per the
  `instagram-reels` quality bar — not a generic "follow for more".

## Hashtags
Relevant AI/tech/startup tags; avoid spammy stacks. 5–12 focused tags.

## Cover / thumbnail
Concept matches the warm-editorial visual language (the hook frame usually works — e.g. the invoice
slam). Keep it consistent with the saved reel-aesthetic preference.

## Package
- The MP4 (`projects/<name>/renders/<name>.mp4`)
- `metadata`: title, duration, platform, aspect, voice, runtime
- `sources[]`: the verified source list (so claims are auditable post-publish)
- Optional: store one-off scripts in Notion per the `instagram-reels` Notion workflow if requested.

## Review focus
- Caption preserves claim integrity + names sources; hook-led
- Cover matches the visual system; hashtags relevant
- Export package contains MP4 + metadata + source list

## Content signal (optional, advisory)

Before finalizing, you MAY offer a predicted virality signal on the finished reel via the
`content_signal` tool (Meta TRIBE v2 on Replicate):

- **Opt-in only** — ask first ("Want a predicted virality score before publishing?").
- **Announce the paid call** (Replicate, ~$0.40/run and ~7 min — the model is slow) per AGENT_GUIDE before running.
- **Confirm-gated in code** — the tool refuses a fresh paid run unless called with `confirm: true`. Run `dry_run` first (it reports a cache hit → `$0.00` and the exact cost without spending), announce, then pass `confirm: true`. Headless/batch: set `CONTENT_SIGNAL_AUTOCONFIRM=1`. If a run times out client-side the prediction keeps running server-side — re-call with `use_cache: true` (auto-resumes the same prediction) or `resume_prediction_id`, never a plain re-run (avoids paying twice).
- **Short-form only** — auto-skips if the render is >60s; needs `REPLICATE_API_TOKEN`.
- **Advisory only** — the 0-100 headline + `sub_scores` + per-step `timeline` inform the user
  and NEVER block publishing. Surface the score and the weakest timeline moments, then let the
  user decide whether to re-edit or publish as-is.
- Produces a `content_signal_report` artifact (cached by file hash, so re-runs are free).
