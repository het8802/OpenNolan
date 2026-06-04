# Idea 4 v2 Remotion QA Notes

Session learning from rebuilding Marketing OS Idea 4 (“Unfortunately, Marketing OS will brainwash you into thinking content can be systematic.”) after existing v1 QA showed a blank sampled frame and repetitive CTA frames.

## What worked

- Existing v1 MP4 was decodable, but contact-sheet QA found a visually blank sampled tile and three near-identical CTA tiles. Treat this as a creative QA failure even if the encode passes.
- Recutting to a tighter 7-beat arc improved pacing:
  1. Content chaos hook
  2. Random inspiration / before state
  3. Trends become inputs
  4. Ideas become choices
  5. Scripts become assets
  6. Posting becomes review
  7. Comment OS CTA
- A shorter local TTS voiceover made the piece land at ~42s instead of ~54–58s.
- Rendering at full vertical dimensions (`1080x1920`, H.264 + AAC) and verifying with both `ffprobe` and `ffmpeg -v error -i <mp4> -f null -` produced a shareable draft.

## Contact-sheet QA technique

Generic `fps=1/N,tile=3x3` contact sheets can include unused tiles or catch transitional/partial frames that look like blanks. For social-video QA, create a second scene-midpoint contact sheet using known midpoint timestamps so every tile corresponds to an intended scene.

Example pattern:

```bash
ffmpeg -y -loglevel error \
  -ss 1.8 -i "$mp4" -ss 6.2 -i "$mp4" -ss 11.6 -i "$mp4" \
  -ss 17.4 -i "$mp4" -ss 24.0 -i "$mp4" -ss 31.0 -i "$mp4" -ss 37.0 -i "$mp4" \
  -filter_complex "[0:v]scale=270:480[v0];[1:v]scale=270:480[v1];[2:v]scale=270:480[v2];[3:v]scale=270:480[v3];[4:v]scale=270:480[v4];[5:v]scale=270:480[v5];[6:v]scale=270:480[v6];[v0][v1][v2][v3][v4][v5][v6]xstack=inputs=7:layout=0_0|278_0|556_0|0_488|278_488|556_488|278_976[out]" \
  -map "[out]" -frames:v 1 contact-sheet-scenes.jpg
```

## Pitfalls caught

- A blank-looking contact-sheet tile is not enough to prove the video has a blank scene. Extract frames at suspicious timestamps and/or build a scene-midpoint sheet before deciding.
- An unused tile in a tiled contact sheet can look like a blank rendered frame. Label or avoid unused slots when sharing QA images.
- If an end card samples as multiple identical frames, decide whether it is intentional hold time or a pacing problem. For Het’s Marketing OS videos, long repeated CTA frames generally feel stale; prefer one clear CTA scene with motion/state changes.
- Small floating UI chips (e.g. SCORE/TRIAGE/SHIP, RELEVANCE/EVIDENCE/FIT) should be decorative unless enlarged. Do not rely on them for essential mobile comprehension.

## Delivery checklist used

- MP4 exists and is decodable.
- `ffprobe` confirms duration, size, codec, and dimensions.
- `ffmpeg -v error -i <mp4> -f null -` exits cleanly.
- `blackdetect` output is reviewed, not blindly trusted.
- Generic contact sheet plus scene-midpoint contact sheet are visually checked.
- Caption/hashtags and posting checklist are written next to the render.
- No posting/scheduling occurs without explicit posting approval.
