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
