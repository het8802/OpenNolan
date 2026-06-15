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

## HDR-preserving render (when the source is HDR)

If the idea-stage `hdr_handling` decision is "preserve" (HDR source + `hdr_encode.available`):
- Encode **HEVC main10, `-pix_fmt yuv420p10le`**, carrying the source's color metadata:
  `-color_primaries bt2020 -color_trc <arib-std-b67|smpte2084> -colorspace bt2020nc
  -color_range tv -tag:v hvc1`. Encoder: `hevc_videotoolbox` (Mac) or 10-bit `libx265`.
- **Do NOT tonemap.** Do NOT route through the 8-bit SDR tools (motion_ops / the FFmpeg
  overlay path force yuv420p 8-bit and would silently degrade HDR — that's the deferred
  HDR-tooling task). For HDR, do the cut with HDR-aware FFmpeg directly.
- **Minimize re-encodes:** one high-quality pass; stream-copy the video through the music mux
  (`-c:v copy`) so audio work never re-encodes (and re-softens) the picture.
- Verify with `is_hdr_source(output)` → `hdr=True` and the right transfer. A frame extracted to
  JPG for preview must be tonemapped to look right; the file itself stays HDR.
- If `hdr_encode.available` is False, you cannot preserve HDR here — surface the blocker; only
  tonemap with the consent recorded at the idea stage.

## Quality bar
Schema-valid render_report; output file exists, is vertical, watermark-free, passes ffprobe;
and (HDR source) `is_hdr_source(output).hdr` is True with the correct transfer. Auto-proceed.
