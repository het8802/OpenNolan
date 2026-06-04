# RVM long-clip + reaction GIF workflow notes

Use this when evaluating talking-head background removal plus humorous GIF inserts for Het's short-form Reels.

## RVM long-clip validation pattern

When a 5s test clip looks good, validate on the full talking-head clip before declaring the workflow production-ready:

1. Locate the full talking-head source and inspect duration/resolution.
2. Create a CPU-friendly full-duration proxy first, e.g. 360x640 at 15fps, to test temporal stability across the whole clip.
3. Run Robust Video Matting (RVM) on the full proxy with green, alpha, and foreground outputs.
4. Build a side-by-side QA preview: original/proxy on the left, green-screen matte on the right.
5. Extract contact-sheet frames across the whole duration, not only the first few seconds.
6. Judge stability over time: ceiling/background removal, face/torso preservation, mic/hand preservation, hair/shoulder edges, and whether artifacts drift or flicker.
7. Only then recommend production resolution. If the proxy is stable, run 720p/1080p when GPU/time allows.

Example command shape from the successful session:

```bash
# Normalize full clip for CPU stability test
ffmpeg -y -i "$SRC" \
  -vf "scale=360:640:force_original_aspect_ratio=decrease,pad=360:640:(ow-iw)/2:(oh-ih)/2,fps=15" \
  -an -c:v libx264 -preset veryfast -crf 24 outputs/bg-long/full-360p15.mp4

# RVM full-duration proxy
source .venv-bg/bin/activate
python tools/RobustVideoMatting/inference.py \
  --variant mobilenetv3 \
  --checkpoint tools/RobustVideoMatting/rvm_mobilenetv3.pth \
  --device cpu \
  --input-source outputs/bg-long/full-360p15.mp4 \
  --output-type video \
  --output-composition outputs/bg-long/rvm-full-green-360p15.mp4 \
  --output-alpha outputs/bg-long/rvm-full-alpha-360p15.mp4 \
  --output-foreground outputs/bg-long/rvm-full-foreground-360p15.mp4 \
  --output-video-mbps 2 \
  --seq-chunk 1
```

Observed CPU benchmark from one 85.5s talking-head clip at 360p/15fps: about 9 minutes wall-clock, ~1.2GB peak RSS. Treat this as an order-of-magnitude benchmark only.

## Meme GIFs vs static text memes

For fast Reels, static text-heavy memes are usually the wrong asset type. Viewers will not read long text during a 0.5-1.5s humor insert.

Preferred GIF workflow:

1. Convert script lines into short reaction intents, not full-sentence searches: `facepalm reaction`, `panic reaction`, `confused computer`, `running chase`, `victory dance`, `fire chaos`.
2. Search animated GIF/video variants, preferably MP4 (`media_formats.mp4`/`tinymp4`) because they are smaller and easier to edit than raw GIFs.
3. Filter for no/low-text clips, short duration (roughly <=5s), clear emotion, and readable silhouette at phone size.
4. Build a contact sheet and, when possible, a short animated grid preview.
5. Create a shortlist folder with the chosen MP4/GIF files plus an index mapping each clip to the script beat.
6. Use as 0.5-1.5s reaction inserts; do not pause the Reel to let long meme text be read.

Useful candidate mapping for AI/dev/founder Reels:

- Generic chatbot rejected -> facepalm, embarrassed bear, disappointed reaction.
- Invoice-chasing intern -> running/chasing cartoon loop.
- Approval gate panic -> scared bear, panic reaction, glowing-eyed anxiety.
- SaaS inside ChatGPT bloat -> confused computer/laptop reaction.
- Messy workflow integration -> fire/chaos cartoon, overloaded/disaster reaction.
- Narrow workflow wins -> victory dance, thumbs-up, celebration.

Avoid: long-caption memes, subtitle-heavy movie clips, obscure references that require reading, visually dark/low-contrast clips, and GIFs whose joke depends on a full 5s+ setup.
