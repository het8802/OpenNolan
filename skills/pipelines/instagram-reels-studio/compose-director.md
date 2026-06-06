# Compose Director — instagram-reels-studio

Render the reel and produce the `render_report`. Vertical 9:16, watermark-free.

## Route by render_runtime (no silent swap)

`video_compose` routes on `edit_decisions.render_runtime`, which was LOCKED at the idea stage.
**Carry it through unchanged.** If the locked runtime is unavailable at compose time, surface a
structured blocker and get approval + a logged `render_runtime_selection` decision — do NOT
silently pick another runtime.

- **ffmpeg** (default): renders the full Edits toolset — `overlays[].keyframes` motion (position
  + fade), beat-aligned `cuts[]`, motion-ops derived clips, template-driven cuts. This is the
  path that consumes `edit_decisions.overlays` correctly.
- **remotion**: `renderer_family: social-reel` routes to the `SocialReel` composition (1080×1920,
  reuses the Explainer renderer). Use when the brief locked remotion.
- **hyperframes**: HTML/CSS/GSAP runtime. If HyperFrames isn't installed (Node ≥ 22 + ffmpeg +
  npx, `hyperframes doctor` clean), that's a blocker for a hyperframes-locked reel — escalate,
  don't fall back to Remotion silently. HyperFrames suits kinetic-typography reels.

## Verify the output
- Keyframed overlays actually animate (scrub the result — a slide/fade should be visible). Note:
  the ffmpeg path renders position + opacity keyframes; scale/rotation keyframes warn and are
  not rendered there (use remotion for those).
- Output is 1080×1920 and passes ffprobe (resolution, duration, has audio if expected).
- No watermark.

## Quality bar
Schema-valid render_report; output file exists, is vertical, watermark-free, and passes ffprobe.
Auto-proceed.
