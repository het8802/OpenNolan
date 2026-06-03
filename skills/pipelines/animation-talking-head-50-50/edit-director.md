# Edit Director — animation-talking-head-50-50 Pipeline

## When to Use

You have approved scene plan and asset manifest. Your job is to produce the `edit_decisions` artifact — the complete specification for how the compose director will assemble the final video using HyperFrames + FFmpeg.

## What edit_decisions Specifies

The compose director will execute two passes:
1. **HyperFrames render** → `projects/{name}/hyperframes/overlay.mp4` (animated overlay, no talking head)
2. **FFmpeg assembly** → `projects/{name}/renders/final.mp4` (overlay + talking head composited)

`edit_decisions` specifies both passes in concrete detail.

## HyperFrames Pass Parameters

```json
"hyperframes_pass": {
  "workspace": "projects/{name}/hyperframes/",
  "output": "projects/{name}/renders/overlay_raw.mp4",
  "workers": 1,
  "expected_duration_seconds": {total},
  "composition_id": "{project_id}",
  "note": "SDR render expected — no <video> elements in composition means no HDR detection"
}
```

`workers: 1` is required for compositions with many timed elements. Default parallel capture can overwhelm headless Chrome.

## FFmpeg Assembly Pass

The FFmpeg assembly composites the talking head into the HyperFrames overlay output. For each scene type:

### split_screen_greg scenes
The talking head fills the bottom panel (1080×862px) beneath the HyperFrames animated top panel.

```
FFmpeg approach: blend by pixel region
  HyperFrames overlay output: 1080×1920 (top 1056px = animation, bottom 864px = solid playbook bg)
  Talking head segment: 1080×864 (face-cropped, original colors)
  
  overlay the TH segment at y=1058 on the HyperFrames output
  (the solid bg in the bottom panel is fully replaced by the TH video)
```

FFmpeg filter:
```bash
ffmpeg -i overlay_raw.mp4 -i seg-{id}-bot.mp4 \
  -filter_complex "[0:v][1:v]overlay=0:1058" \
  -c:v libx264 -preset slow -crf 20 -pix_fmt yuv420p \
  -c:a copy out_scene.mp4
```

### hero_talking_head scenes
The full-frame talking head is the primary layer; HyperFrames overlay (sticker pills, comparison boards, etc.) sits on top.

```
FFmpeg approach: talking head full-frame, overlay on top
  Talking head segment: 1080×1920 (full frame, face in center)
  HyperFrames overlay: 1080×1920 (transparent-ish solid bg with overlay elements visible)
```

For this to work correctly, the HyperFrames overlay for hero scenes must have a fully transparent/solid playbook-colored background for the regions where no overlay elements appear, and the overlay elements themselves. Since HyperFrames renders to an opaque MP4 (no alpha), use a chroma-key approach OR ensure the playbook background is very close to the talking head background. 

**Recommended approach for hero scenes**: Use the HyperFrames rendered overlay ONLY for scenes where the background doesn't conflict with the talking head. The overlay elements (sticker pills, comparison boards) are composited by FFmpeg using their known pixel positions and sizes — render them as separate image assets and composite with FFmpeg `overlay` at the correct coordinates.

**Alternative**: For simpler hero overlays, render them directly via FFmpeg `drawtext` or static PNG overlays.

Record which approach is used in `edit_decisions.hero_overlay_strategy`.

### full_greg_card scenes
Use the HyperFrames overlay output directly — no talking head for this portion.

```bash
# Extract the full_greg_card time window from the overlay_raw
ffmpeg -i overlay_raw.mp4 -ss {start} -to {end} \
  -c:v copy scene_{id}_card.mp4
```

## Audio Strategy

The master audio track is the original narration:
```bash
ffmpeg -i projects/{name}/assets/audio/narration.m4a -c:a copy audio_master.m4a
```

Mix into the final video:
```bash
ffmpeg -i video_assembled.mp4 -i audio_master.m4a \
  -c:v copy -c:a aac -b:a 192k -shortest final.mp4
```

**Critical**: Do NOT re-extract audio from the talking head video segments. The segments were trimmed without audio (`-an` flag in asset stage). Use the full narration.m4a extracted in the assets stage.

## Edit Decisions Artifact

```json
{
  "version": "1.0",
  "render_runtime": "hyperframes+ffmpeg",
  "canvas": "1080x1920",
  "total_duration_seconds": {total},

  "hyperframes_pass": {
    "workspace": "projects/{name}/hyperframes/",
    "output_path": "projects/{name}/renders/overlay_raw.mp4",
    "workers": 1,
    "composition_id": "{project_id}"
  },

  "ffmpeg_assembly": {
    "output_path": "projects/{name}/renders/final.mp4",
    "audio_source": "projects/{name}/assets/audio/narration.m4a",
    "scenes": [
      {
        "id": "sc-01",
        "layout_mode": "hero_talking_head",
        "th_segment": "projects/{name}/assets/video/seg-01-hook-full.mp4",
        "overlay_source": "overlay_raw.mp4",
        "overlay_time_range": [0.0, 2.52],
        "compose_method": "th_full_frame_with_hf_overlay",
        "hero_overlay_strategy": "hf_top_layer | png_composite"
      },
      {
        "id": "sc-02",
        "layout_mode": "split_screen_greg",
        "th_segment": "projects/{name}/assets/video/seg-02-shift-bot.mp4",
        "overlay_source": "overlay_raw.mp4",
        "overlay_time_range": [2.52, 12.06],
        "compose_method": "hf_top_th_bottom",
        "th_overlay_y_px": 1058
      }
      // ... one entry per scene
    ]
  },

  "caption_config": {
    "embedded_in_hf": true,
    "words_per_chunk": 3,
    "font": "Outfit",
    "font_weight_px": 800,
    "font_size_px": 44
  }
}
```

## Color Protection Check (Pre-Compose Gate)

Before finalizing edit_decisions, verify:
1. Check `asset_manifest.talking_head_segments[*].color_conversion_applied` — all must be `false`.
2. Check `asset_manifest.talking_head_segments[*].color_transfer_output` — all must match the source `color_transfer_source`.
3. If ANY segment has had color conversion applied, SEND_BACK to the asset director.

Record this check in `edit_decisions.metadata.color_protection_verified: true`.
