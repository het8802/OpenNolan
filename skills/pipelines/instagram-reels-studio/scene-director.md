# Scene Director — instagram-reels-studio

Produce the `scene_plan` artifact: ordered scenes with vertical framing and explicit
Edits-style move opportunities per scene.

## Per scene, specify

- **Aspect ratio 9:16** and duration (seconds).
- **Source** — generated, stock, or user clip/image.
- **Framing** — keep the subject in the safe zone (center ~80%); captions live bottom-center.
- **Planned moves** (flag so asset + edit stages act on them):
  - *Cutout?* — does a subject need isolating/tracking (object_cutout) to float over a new bg?
  - *Keyframe?* — should an overlay (cutout/text/sticker) slide/scale/fade in? Name the motion
    (e.g. slide_in_left, pop, ken_burns) and the timing.
  - *Beat-sync?* — is this a music-led montage section where cuts should land on beats?
  - *Motion op?* — freeze on a punchline, reverse for a loop, slow-mo, volume duck?
  - *Restyle?* — is this a ≤10s hero shot to run through restyle_video? (paid)

## Rules
- Don't start/end a scene mid-word.
- Keep cuts short for reel energy (often 1.5–3s).
- Carry `render_runtime` and `renderer_family` (social-reel) from the brief unchanged.

## Quality bar
Ordered scenes, each with 9:16 framing, duration, source, and flagged moves. Checkpoint for
human approval.
