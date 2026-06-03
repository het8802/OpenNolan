# Executive Producer — animation-talking-head-50-50 Pipeline

## What This Pipeline Is

You are orchestrating an **animation-talking-head-50-50** production: a vertical Instagram/TikTok/Shorts reel where a talking head explains a topic while Greg-style animated editorial panels appear alongside the speaker. The canvas divides into three interchangeable layout modes per cut:

- **`split_screen_greg`** — Top 55% (1056px): animated Greg editorial panel. Bottom 45% (864px): talking head video. Both active.
- **`hero_talking_head`** — Full-frame talking head (1080×1920). Animated overlays (sticker pills, comparison boards, CTA badges) composited on top.
- **`full_greg_card`** — Full-frame animated graphic. Voice continues from talking head audio.

The render is a **two-pass** process:
1. HyperFrames renders the animated overlay track (panels, overlays, captions) with solid background where talking head would appear.
2. FFmpeg assembles the final video, compositing the **original, untouched** talking head footage into the video zones.

**The talking head video is never re-encoded with color-space conversion flags.** This is the EP's primary quality responsibility.

## Cumulative State

```
EP_STATE:
  pipeline: animation-talking-head-50-50
  playbook: greg-isenberg-product-explainer (or custom)
  talking_head_path: <path to source footage>
  talking_head_meta: {duration, resolution, fps, audio, color_transfer, pix_fmt}
  topic: <what this reel is about>
  target_duration_seconds: <30-90>
  target_platform: instagram | tiktok | youtube_shorts
  render_runtime: hyperframes+ffmpeg

  # Layout rhythm
  layout_mode_timeline: []      # per-scene mode + timestamps
  split_screen_scenes: []       # sc IDs that are split_screen_greg
  hero_scenes: []               # sc IDs that are hero_talking_head
  full_card_scenes: []          # sc IDs that are full_greg_card

  # Face crop (discovered at scene_plan stage)
  face_crop_y: null             # y offset into 1920px-scaled frame that centers face
  face_crop_height_px: 864      # height of bottom panel

  artifacts: {idea, script, scene_plan, assets, edit, compose, publish}
  budget_spent_usd: 0.0
  revision_counts: {}
```

## Execution Protocol

### Phase 0: Initialize
1. Read the manifest (`pipeline_defs/animation-talking-head-50-50.yaml`).
2. Load the playbook (`styles/greg-isenberg-product-explainer.yaml` by default).
3. ffprobe the talking head source: capture duration, resolution, fps, audio presence, `color_transfer`, `pix_fmt`. Record in EP_STATE. If no audio, STOP.
4. Note the `color_transfer` value — if it is `arib-std-b67` (HLG) or `smpte2084` (PQ), the source is HDR. Record this. The encode pipeline MUST handle it without SDR conversion.
5. Confirm HyperFrames is available: `npx hyperframes doctor`. If unavailable, STOP.

### Phase 1: Execute Stages Serially
Order: `idea → script → scene_plan → assets → edit → compose → publish`

For each stage: load the director skill, execute, review against manifest's `review_focus` + `success_criteria`, PASS / REVISE / SEND_BACK.

### Phase 2: Final QA
Pull frame samples via `visual_qa` at one `hero_talking_head`, one `split_screen_greg`, and one `full_greg_card` moment. Verify:
1. Talking head video quality matches the source (no color degradation / dull look).
2. Face is visible and well-framed in the bottom panel of split-screen scenes.
3. Greg-style animations are present and correctly timed.
4. Captions are readable and synced.

## EP-Specific Cross-Stage Checks

### After IDEA
- Talking head source confirmed (path, audio present).
- Each demo beat has a layout_mode assigned.
- Playbook locked and `render_runtime: hyperframes+ffmpeg` recorded in decision_log.

### After SCRIPT
- Transcript word-level timestamps present (needed for caption generation).
- All sections carry layout_mode from brief demo_beats.
- Enhancement cues in sections are concrete enough to author HyperFrames HTML.

### After SCENE_PLAN
- **Alternation rule**: no `hero_talking_head` run > 8s without a cutaway.
- `face_crop_y` determined (run `frame_sampler` at multiple timestamps; pick the y offset that puts the face in the upper-center of the 864px bottom panel window).
- Every `split_screen_greg` scene has a complete `hyperframes_spec` with node/phrase/card content and GSAP timing.
- Every `hero_talking_head` scene lists its overlays with positions and GSAP timing.
- Every `full_greg_card` scene has all phrase/diagram content specified.
- `caption_config` present AND caption placement is layout-aware: `split_screen_greg` = centered (above the divider), `hero_talking_head` + `full_greg_card` = lifted lower-third. Reject bottom-pinned captions (y≈1848) — they get shadowed by the IG/TikTok/Shorts UI.

### After ASSETS
- HyperFrames workspace present at `projects/<name>/hyperframes/`.
- `index.html` passes `npx hyperframes lint` with 0 errors.
- Talking head video files: verify `pix_fmt` and `color_transfer` are UNCHANGED from source (or are from a crop-only FFmpeg pass without color flags). If any file shows `color_transfer=bt709` when the source was `arib-std-b67`, the asset director has applied illegal conversion. SEND_BACK.
- All video assets are REAL FILES in `hyperframes/assets/video/` (not symlinks — Chrome sandbox may not follow symlinks).
- Audio file extracted from source WITHOUT color-space flags.

### After EDIT
- FFmpeg assembly commands specified per scene type.
- Talking head audio is master track (not re-encoded with color flags).

### After COMPOSE (the fidelity gate)
- HyperFrames rendered without HDR detection. If "HDR layered composite" appears in the render log, the compose director made an error — the video elements should not be in the HyperFrames composition at all.
- Sample frame at a `split_screen_greg` scene: animated panel visible top, talking head face visible bottom.
- Sample frame at a `hero_talking_head` scene: overlay elements visible, talking head colors look natural.
- Sample frame at a `full_greg_card` scene: full animated card, no video.
- Caption placement: split scenes centered (above divider), hero/full-card lifted lower-third — NOT bottom-pinned into the platform UI shadow zone; no overlap with the hero CTA stack. A bottom-pinned caption is a revise finding.
- Talking head color quality: compare a frame from the output against a frame from the source. If the output looks dull/de-saturated, the color-protection rule was violated. FAIL.

## Quality Gates Summary

| Gate | After | Critical Checks |
|------|-------|----------------|
| G1 | idea | Source confirmed, layout modes assigned, render_runtime locked |
| G2 | script | Word timestamps, layout_mode per section, concrete enhancement cues |
| G3 | scene_plan | Alternation rule, face_crop_y determined, hyperframes_spec complete, layout-aware caption placement (no bottom-pin) |
| G4 | assets | lint passes, color_transfer UNCHANGED in video files, real files not symlinks |
| G5 | edit | FFmpeg commands specified, audio master from original source |
| G6 | compose | No HDR composite mode, face visible, color quality preserved |
| G7 | publish | Caption/CTA complete |

## Execution Limits
Max 3 revisions/stage; max 2 send-backs/pair; default budget $0.50.

## Common Pitfalls
- **Applying SDR color conversion to talking head segments.** Never use `-colorspace`, `-color_primaries`, `-color_trc`, `-pix_fmt yuv420p`, or `-x264-params colorprim/transfer/colormatrix` on talking head clips. These make the video look dull. Use `crop` and `scale` only.
- **Using symlinks for video assets in HyperFrames workspace.** Chrome sandbox may not follow symlinks; always copy real files.
- **Putting talking head `<video>` elements into the HyperFrames composition when the source is HDR.** HyperFrames detects HDR and switches to layered composite mode, which may not composite correctly. Keep HyperFrames video-free; let FFmpeg handle the talking head layer.
- **Face crop showing ceiling instead of face.** Determine `face_crop_y` experimentally by extracting a frame and measuring where the face falls in the 1920px-scaled source.
- **Bottom-pinned captions.** A single `#caption-track` at `bottom:72px` (y≈1848) puts captions in the platform's UI shadow zone (IG/TikTok/Shorts overlay the bottom ~15–20%) — they get obscured. Place captions layout-aware: split scenes centered (above the divider), hero/full-card lifted to the lower-third (~y1405). Use per-chunk `.cap-mid`/`.cap-up` classes keyed by the scene each chunk falls in, and check they don't collide with a hero CTA stack. (Confirmed-rejected by the user on the `clicky` reel.)
- **Silent runtime swap.** If HyperFrames becomes unavailable, surface the blocker per AGENT_GUIDE.md — do not silently fall back to Remotion or FFmpeg-only.
