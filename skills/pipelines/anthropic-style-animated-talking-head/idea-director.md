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
5. **Lock the render path (present the runtime choice).** This pipeline AUTHORS its animated assets
   in **hyperframes** (HTML/CSS/GSAP → mp4/mov) but ASSEMBLES the timeline in **ffmpeg** (render-once:
   `video_compose operation=render_proxies` caches each scene's proxy and concats cheaply), so the
   reel opens in the desktop editor for the human to refine. Present both composition runtimes to the
   user before locking — hyperframes vs remotion for the animated assets — with a one-line tradeoff
   each and your recommendation (hyperframes here, for the Anthropic-editorial motion). Then set
   `render_runtime: "ffmpeg"` (the assemble runtime) and log a **`render_runtime_selection`** decision
   in `decision_log` recording BOTH runtimes considered and why. The HDR talking head is preserved by
   the ffmpeg assemble (10-bit + color tags; SDR graphics lifted into the HDR container) — never
   silently tonemapped. Audio (the VO) is carried continuous and untouched.

## Output: `brief`
- topic, hook, audience, platform, duration_s, cta
- source_footage: {path, w, h, fps, duration_s, has_audio, hdr: {is_hdr, kind}}
- playbook: anthropic-editorial-animated
- render_runtime + rationale (in decision_log)
- subjects_to_source: companies/products/logos likely needed (for research + assets)

## Self-evaluate
- Footage probed; duration + continuous audio confirmed; HDR logged (SDR or decided).
- Hook, audience, platform, CTA captured. Playbook + render path locked in decision_log.
