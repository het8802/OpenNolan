# Local motion-graphics fallback for Marketing OS shorts

Use when B-roll/API credentials are unavailable but the draft still must feel video-first rather than like stitched stills.

## Pattern

1. Reuse or generate VO first, then get its duration with `ffprobe`.
2. Beat-map the script by seconds and assign a distinct moving visual system per beat: simulated UI recording, moving node map, scrolling form, particle funnel, countdown/CTA, notification slam-in, animated lower-thirds.
3. Render frame-by-frame with Python/Pillow into an FFmpeg rawvideo pipe, then upscale to 1080x1920 H.264.
4. Add VO plus a generated low-volume bed using FFmpeg lavfi sources; avoid copyrighted music.
5. QA with: decode check, `blackdetect`, explicit-timestamp contact sheet, and visual review. If contact-sheet review finds clipped/crowded text, reduce fonts, move lower-thirds down, split long search/caption text, and re-render.

## Commands and gotchas

Render raw RGB frames to video:

```bash
python3 create_video.py
ffmpeg -v error -i final.mp4 -f null -
ffmpeg -hide_banner -nostats -i final.mp4 -vf blackdetect=d=0.25:pic_th=0.98 -an -f null -
ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,r_frame_rate -show_entries format=duration,size -of json final.mp4
```

If ElevenLabs TTS is unavailable in Hermes, `edge-tts` is a practical fallback. The CLI on this host uses `--file`, not `--text-file`:

```bash
edge-tts --voice en-US-GuyNeural --rate +22% --pitch +3Hz \
  --file script.md \
  --write-media assets/voiceover.mp3
ffprobe -v error -show_entries format=duration,size -of json assets/voiceover.mp3
```

FFmpeg duration values in filters should use leading zeroes on this host. `afade=d=.7` failed with `Unable to parse option value ".7" as duration`; use `afade=d=0.7` and `d=0.9`.

Contact-sheet QA for animated headline cards must sample both scene midpoints and transition moments. If headlines slide in from an edge, slow entry can look fine at rest but be unreadable for too much of the beat. Keep essential text fully inside the safe frame within the first ~20–25% of each scene, or remove decorative rank watermarks that collide with dense headlines. If a contact sheet includes extra late timestamps, it may show duplicate final/CTA frames; choose explicit storyboard midpoints plus at most one outro sample so duplicates are not mistaken for content beats.

## Reusable storyboard shape

Use this as a class-level pattern, not a template to repeat exactly:

- 0-3s: visual pattern interrupt (notification, door opening, glitch card, live UI event)
- 3-8s: network/opportunity metaphor with moving nodes or cards
- 8-15s: audience qualification cards
- 15-25s: benefits or evidence grid with sub-scene cuts
- 25-35s: simulated screen/form/product interaction
- 35-42s: strategic interpretation metaphor/funnel
- 42s-end: urgent CTA with search/apply affordance

Do not reuse identical scene order, palette, lower-third style, or animations across Marketing OS videos; adapt the pattern to the new story.