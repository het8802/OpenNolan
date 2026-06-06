# Compose Director — animation-talking-head-50-50 Pipeline

## When to Use

You have approved edit_decisions and asset_manifest. Your job is the two-pass render:
1. HyperFrames renders the animated overlay track.
2. FFmpeg assembles the final video with the original talking head.

Then verify output quality (including color fidelity of the talking head).

## Pre-compose Checklist (mandatory before any render)

Before running HyperFrames or FFmpeg:

- [ ] `npx hyperframes lint {workspace}/` → 0 errors
- [ ] `npx hyperframes validate {workspace}/` → 0 errors (contrast deferred with `--no-contrast` if needed)
- [ ] Confirm no `<video>` or `<audio>` elements in `index.html` — this is what prevents HDR detection
- [ ] Confirm all video segment files are real files (not symlinks) in `hyperframes/assets/video/`
- [ ] Confirm `color_transfer` on each talking head segment matches source (no SDR conversion)
- [ ] Confirm `color_protection_verified: true` in edit_decisions

## Pass 1: HyperFrames Render

```bash
npx hyperframes render {workspace}/ \
  --output projects/{name}/renders/overlay_raw.mp4 \
  --workers 1
```

**Expected render log line**: `[INFO] [Render] No HDR sources detected — rendering SDR`

If you see `[INFO] [Render] HDR auto-detected from source(s)` instead, STOP. This means a video element or HDR-tagged media was inadvertently included in the composition. Check `index.html` for `<video>` elements, check audio files for HDR metadata. Do NOT proceed with a layered composite render — the output will not composite video correctly.

Monitor progress: the render processes ~2426 frames for an 80s composition at 30fps. At `--workers 1`, expect ~5-15 minutes depending on hardware.

After render, verify:
```bash
ffprobe -show_format overlay_raw.mp4 | grep duration
ffprobe -show_streams -select_streams v overlay_raw.mp4 | grep -E "codec_name|width|height"
```
Expected: codec_name=h264, width=1080, height=1920, duration≈source duration.

## Pass 2: FFmpeg Assembly

The FFmpeg assembly composites the original talking head footage into the overlay output.

**CRITICAL: DO NOT apply color-space conversion to talking head clips in this step either.**

### Step 2a: Process split_screen_greg scenes

For each `split_screen_greg` scene, overlay the talking head segment onto the bottom 45% of the HyperFrames overlay:

```bash
# The HyperFrames overlay has solid bg in the bottom panel (y=1058 to y=1920)
# We overlay the talking head segment at y=1058 to replace that solid area
ffmpeg -y \
  -ss {scene_start} -to {scene_end} -i projects/{name}/renders/overlay_raw.mp4 \
  -i projects/{name}/assets/video/seg-{id}-bot.mp4 \
  -filter_complex "[0:v][1:v]overlay=0:1058:shortest=1" \
  -c:v libx264 -preset fast -crf 20 \
  -an tmp_sc{id}.mp4
```

### Step 2b: Process hero_talking_head scenes

The talking head is the primary (full-frame). HyperFrames overlay elements (sticker pills, comparison boards, CTA) appear on top.

**Option A** (if HyperFrames background matches talking head background closely):
```bash
ffmpeg -y \
  -ss {scene_start} -to {scene_end} -i projects/{name}/assets/video/seg-{id}-full.mp4 \
  -ss {scene_start} -to {scene_end} -i projects/{name}/renders/overlay_raw.mp4 \
  -filter_complex "[0:v]scale=1080:1920[th]; [1:v]scale=1080:1920[ov]; [th][ov]overlay=0:0:shortest=1" \
  -c:v libx264 -preset fast -crf 20 \
  -an tmp_sc{id}.mp4
```

Note: This overlay method will show the solid HyperFrames background on top of the talking head where no overlay elements appear. This works well when the playbook background (#F5EFE6 ivory) is similar to the talking head's background (white/cream walls). For footage with strongly different backgrounds, use option B.

**Option B** (overlay elements only — crop to element bounds, composite as PNGs):
Run HyperFrames validate to extract screenshots of each overlay element at its active time, then composite as PNGs with known dimensions and positions. More complex but more precise.

### Step 2c: Process full_greg_card scenes

Simply cut the relevant time window from the overlay_raw:
```bash
ffmpeg -y \
  -ss {scene_start} -to {scene_end} -i projects/{name}/renders/overlay_raw.mp4 \
  -c:v copy \
  -an tmp_sc{id}.mp4
```

### Step 2d: Concatenate all scene clips

Create a concat list:
```
file tmp_sc01.mp4
file tmp_sc02.mp4
...
```

Concatenate:
```bash
ffmpeg -y -f concat -safe 0 -i concat_list.txt \
  -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p \
  -an tmp_video_only.mp4
```

### Step 2e: Mix audio

```bash
ffmpeg -y \
  -i tmp_video_only.mp4 \
  -i projects/{name}/assets/audio/narration.m4a \
  -c:v copy -c:a aac -b:a 192k -ar 44100 \
  -movflags +faststart \
  projects/{name}/renders/final.mp4
```

## Post-render Verification

### 1. Technical specs
```bash
ffprobe -show_format -show_streams -print_format json projects/{name}/renders/final.mp4
```
Verify: 1080×1920, H.264, AAC 44.1kHz stereo, duration≈source, movflags faststart.

### 2. Visual QA — frame sampling
Extract frames at representative moments:
```bash
for t in {hook_time} {split_screen_time} {full_card_time} {payoff_time}; do
  ffmpeg -ss $t -i final.mp4 -vframes 1 -q:v 2 /tmp/qa_${t}s.jpg
done
```

Read each frame and check:
- **Hook** (`hero_talking_head`): Sticker pills visible. Talking head face natural color (not dull/washed-out).
- **Split-screen** (`split_screen_greg`): Top panel shows animated Greg content. Bottom panel shows talking head face (not ceiling). Face is well-framed (head and face visible, not just hair-top or forehead).
- **Full card** (`full_greg_card`): Animated content fills canvas. Text readable. Greg palette correct (ivory, forest, coral, mint).
- **Payoff** (`hero_talking_head`): Comparison board visible. CTA badge visible. Talking head color matches source.

### 3. Color fidelity check

Extract a frame from the SOURCE talking head at the same timestamp as a hero scene. Compare the colors visually against the output frame:
```bash
ffmpeg -ss {hero_start} -i {source} -vf "scale=1080:1920" -vframes 1 -q:v 2 /tmp/source_ref.jpg
ffmpeg -ss {hero_start} -i final.mp4 -vframes 1 -q:v 2 /tmp/output_check.jpg
```

Read both frames. If the output looks noticeably more muted, flat, or desaturated than the source, the color protection rule was violated somewhere in the pipeline. FAIL and investigate.

### 3b. Narration Sync Verification (MANDATORY — catches early reveals)

The most common reviewer-caught defect in this format is animations revealing *before* the speaker says the words (it makes the reel feel pre-canned). Verify it explicitly.

For each keyed element / overlay / reaction-GIF, sample TWO frames around its `trigger_word` timestamp `T` (from the scene_plan):

```bash
ffmpeg -ss $(echo "$T - 0.35" | bc) -i final.mp4 -vframes 1 /tmp/before_${T}.jpg   # element must be ABSENT
ffmpeg -ss $(echo "$T + 0.25" | bc) -i final.mp4 -vframes 1 /tmp/on_${T}.jpg        # element must be PRESENT
```

Read both frames:
- **before** (~0.35s before the word): the element must NOT yet be visible. If it's already on screen, it revealed too early → **CRITICAL finding, retime** the GSAP `abs_time` (or the FFmpeg overlay `-itsoffset`/`enable` window) to the word's `start`.
- **on** (~0.25s after the word): the element should be visible.

For a multi-part diagram, also sample a frame mid-clause and confirm it is still *building* (not already complete) — it should finish as the speaker finishes, not before.

Treat any "visible before its trigger word" as a release blocker for that beat. Re-render the overlay (GSAP retime) or re-run FFmpeg (overlay window retime) and re-verify.

### 4. Caption check
Sample one frame from a `split_screen_greg` scene and one from a `hero_talking_head`/`full_greg_card`
scene and confirm captions are LAYOUT-PLACED (not bottom-pinned):
- Split scene: caption is **centered** (vertical middle, above the divider) — not over the face, not over the panel's hero content.
- Hero/full-card scene: caption is in the **lifted lower-third** (~y1405, ~73% height), NOT pinned to the very bottom where the platform UI (IG/TikTok/Shorts) would shadow it.
- Caption is readable (3-word chunks, bold, ivory pill) and, on hero CTA scenes, does NOT overlap the CTA badge stack.

A bottom-pinned caption (y≈1848) is a **revise** finding — it gets obscured by the platform's overlay UI (confirmed-rejected on the `clicky` reel).

### 5. Audio check
```bash
ffprobe -show_streams -select_streams a final.mp4 | grep -E "codec_name|channels|sample_rate"
```
Expected: aac, 2 channels, 44100hz. Audio should be continuous from the original talking head.

## Render Report

```json
{
  "version": "1.0",
  "outputs": [
    {
      "path": "projects/{name}/renders/final.mp4",
      "format": "mp4",
      "codec": "h264",
      "audio_codec": "aac",
      "resolution": "1080x1920",
      "fps": 30,
      "duration_seconds": {actual_duration},
      "file_size_bytes": {size},
      "platform_target": "{platform}"
    }
  ],
  "render_grammar": "animation-talking-head-50-50",
  "render_runtime": "hyperframes+ffmpeg",
  "hyperframes_sdr_confirmed": true,
  "color_protection_confirmed": true,
  "warnings": [],
  "verification_notes": [
    "HyperFrames rendered SDR (no HDR sources in composition)",
    "Talking head color preserved — no color-space conversion applied",
    "Face visible in bottom panel at y=650 crop",
    "All {n} layout modes verified via frame sampling"
  ]
}
```
