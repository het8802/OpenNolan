# Idea Director — anthropic-style-animated-talking-head

## When to Use
The creator has provided (or will provide) a recorded talking-head video and wants it turned
into an Anthropic-style animated explainer. Produce the `brief`.

## Prerequisites
| Layer | Resource |
|-------|----------|
| Footage | The creator's talking-head file (e.g. `projects/<name>/assets/videos/*.mp4`) |
| Playbook | `styles/anthropic-editorial-animated.yaml` |
| Tool | `video_analyzer` / `ffprobe` for source probe |

## Do this
1. **Locate & probe the footage.** Record codec, resolution, fps, **duration**, and audio stream.
   The video is the timeline length; the audio is the untouched VO spine. Confirm continuous audio.
2. **HDR check (HARD RULE).** Detect color primaries/transfer (`is_hdr_source`). If HDR, decide
   handling WITH the user and log it — never silently tonemap. SDR (bt709) → proceed normally.
   The TH will get crop & scale only (no color flags) regardless.
3. **Capture intent.** Topic, the hook (first 2–3s), target audience, platform (IG/TikTok/Shorts),
   rough duration (= footage duration), and the CTA/close.
4. **Lock the look.** Playbook = `anthropic-editorial-animated` (ivory/clay/slate, Fraunces + Inter).
   Note any brand specifics (e.g. a product this reel is about) so logos/screenshots can be sourced.
5. **Lock render path.** `render_runtime`: HyperFrames for animated assets + FFmpeg segment-rebuild
   assembly; audio copied through. Record in `decision_log` (note both runtimes considered).

## Output: `brief`
- topic, hook, audience, platform, duration_s, cta
- source_footage: {path, w, h, fps, duration_s, has_audio, hdr: {is_hdr, kind}}
- playbook: anthropic-editorial-animated
- render_runtime + rationale (in decision_log)
- subjects_to_source: companies/products/logos likely needed (for research + assets)

## Self-evaluate
- Footage probed; duration + continuous audio confirmed; HDR logged (SDR or decided).
- Hook, audience, platform, CTA captured. Playbook + render path locked in decision_log.
