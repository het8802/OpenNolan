# Compose Director — instagram-fast-reel

Render the reel and hand off to the `qa` stage. Keep this stage focused on producing the file;
the thorough inspection is the dedicated `qa` stage next.

## Render
Run `video_compose`. It routes by `edit_decisions.render_runtime` — **no silent runtime swap**.
If the locked runtime is unavailable, surface a structured blocker (AGENT_GUIDE "Escalate
Blockers Explicitly") and get approval before any substitute. Output canvas = 1080x1920 (9:16)
from `metadata.compose_target`; export watermark-free to `projects/<name>/renders/final.mp4`.
HyperFrames is a valid runtime only when the brief explicitly locks it and its renderer is
available; otherwise preserve the chosen Remotion or FFmpeg runtime and report the constraint.

- Keyframed text/GIF overlays and captions render as planned.
- Music ducks under the VO; SFX land on the cuts.
- Optional `color_grade` only if the brief called for it.
- If the source was HDR, do not silently tonemap — honor the idea-stage decision.

## Sanity check (not full QA)
Confirm the render actually produced a usable file before advancing: an ffprobe sanity probe
(valid container, decodes, resolution ~1080x1920). Do NOT do the deep frame-by-frame /
technicals / sync pass here — that's the `qa` stage. If the render failed or is obviously broken
(0 bytes, wrong resolution, won't decode), fix and re-render before handing off.

## Output
`render_report` (schema-valid): output path, encoding profile, runtime that ran (must match the
brief's locked runtime), and the ffprobe sanity result.

## Quality bar
A valid, watermark-free 9:16 file exists at `renders/final.mp4` and the runtime matches the lock.
Auto-proceed to `qa`.
