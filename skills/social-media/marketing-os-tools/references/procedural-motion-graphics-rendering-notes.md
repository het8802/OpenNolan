# Procedural motion-graphics rendering notes

Use when producing a Marketing OS short with local Python/Pillow + FFmpeg instead of Remotion, stock B-roll, or generated video APIs.

## Proven local pattern

- Generate VO first (`edge-tts` is acceptable for drafts), then use `ffprobe` duration to set the timeline.
- Draw beat-specific scenes frame-by-frame into an FFmpeg rawvideo pipe. This can produce video-first motion without external B-roll when each beat uses a different metaphor: terminal glitch, GPU/data pipe, before/after meters, workflow cards, Product Hunt/product card, checklist, stack graph, CTA orbit.
- Add a locally generated low-volume bed with FFmpeg lavfi (`anoisesrc` + `sine`) and mix under the VO.
- Keep an asset ledger even when every asset is local/generated: note `edge-tts`, `ffmpeg lavfi`, and procedural animation as generated-local assets.

## Performance tips

- Pillow per-pixel loops are too slow for 60s+ vertical renders. Use NumPy/vectorized arrays for animated gradients/noise backgrounds, then convert with `Image.fromarray`.
- 720×1280 at 15 fps is a practical draft target when layout was designed for 720×1280 coordinates. Upscale only if the user needs a final export.
- When only audio/muxing changes, skip rerendering frames: `ffprobe` the silent render and reuse it if duration is valid.
- Suppress FFmpeg progress spam in frame-pipe runs with `-hide_banner -loglevel error` so logs stay inspectable.

## FFmpeg gotchas

- FFmpeg duration values in filters need leading zeroes on this host. `afade=d=.4` / `.7` can fail with `Unable to parse option value`; use `afade=d=0.4` / `0.7`.
- If a render times out during muxing/encoding, verify before reuse with both:
  ```bash
  ffprobe -v error -show_entries format=duration,size -of json final.mp4
  ffmpeg -v error -i final.mp4 -f null -
  ```

## Contact-sheet QA pitfall

Animated entrances can be sampled mid-transition. A contact-sheet tile may show text clipped even if the card is readable at rest. For essential checklist/headline text, prefer pop/fade/highlight entry or keep the text fully inside the safe frame from the first visible frame. If using slide-in motion, sample both scene midpoint and early-scene frames before shipping.

## QA checklist

```bash
ffprobe -v error -show_entries format=duration,size -of json final.mp4 > qa/ffprobe.json
ffmpeg -v error -i final.mp4 -f null -
ffmpeg -hide_banner -nostats -i final.mp4 -vf "blackdetect=d=0.25:pic_th=0.98" -an -f null - 2> qa/blackdetect.log
ffmpeg -y -loglevel error -i final.mp4 -vf "fps=1/7,scale=270:480,tile=3x3:padding=8:margin=8" -frames:v 1 qa/contact-sheet.jpg
```

Review the contact sheet with vision/manual inspection; if text is clipped, revise and re-render before delivery.