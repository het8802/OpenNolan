# Remotion polished render pattern for talking-head + screen-proof Reels

Session learning from an OpenAI personal-finance Reel recreation.

## When this applies
Use when the user wants the Clicko/Arshman style specifically rendered as a polished Remotion edit: full-screen creator moments, creator PIP over proof/source cards, kinetic punch captions, and screen/demo-style evidence behind the speaker.

## Durable workflow lesson
If the user asks for the Remotion polished version, do not treat an FFmpeg fallback as the final answer. FFmpeg can be a temporary proof-of-style draft, but the next production pass should return to Remotion and fix the render path.

## Remotion implementation notes
- Prefer `OffthreadVideo` for embedded source/talking-head video layers, especially when the same footage is used both full-screen and as PIP. This can avoid browser decode/render instability from normal `<Video>` during long social renders.
- Keep one original audio track outside the muted video layers, usually with `<Audio src={staticFile(...)} startFrom={...} />` or final muxing, so duplicated PIP/video layers do not double audio.
- Before a full 80s+ render, run a Remotion still-frame sanity check around an early proof-card/PIP moment, e.g. frame ~8s. This catches import/layout issues without waiting for the full render.
- For long vertical renders, run Remotion in background with completion notification if available; then QA the final MP4, not just the component.

## QA pattern
After render, verify:
- `ffprobe` duration, size, 1080x1920, 30fps, H.264, audio stream present.
- `ffmpeg -v error -i final.mp4 -f null -` has no decode errors.
- `blackdetect` finds no unintended black sections.
- Contact sheet across the timeline shows: full-screen hook/CTA, proof/source cards, creator PIP, bold punch captions, and no broken crops or unreadable safe-area text.

## Communication pitfall
If Remotion fails or is extremely slow, surface the blocker and options. Only switch runtime after user approval. If an FFmpeg draft is created to keep momentum, label it clearly as a V1 proof/draft and preserve the Remotion project for the polished pass.