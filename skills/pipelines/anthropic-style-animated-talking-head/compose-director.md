# Compose Director — anthropic-style-animated-talking-head

## When to Use
You have `edit_decisions` (the contiguous segment cut plan) and the `asset_manifest`. Assemble
the final reel and QA it. Produce `render_report` + `final_review`.

## Prerequisites
| Layer | Resource |
|-------|----------|
| Edit decisions | segments[] (kind, window, source, fit, face_crop_y, overlay, marker_sweep), audio=copy TH |
| Assets | cutaways (mp4), overlays (mov, alpha), split panels (1080×960), receipts, the TH source |
| Tool | `video_compose` (FFmpeg path) |

## Assembly: FFmpeg segment-rebuild (the validated method)
Build each segment to a file with **identical encode params**, concat with `-c copy`, then mux
the **original TH audio** with `-c:a copy`. Encode preset for all segments:
`-c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -r 30 -video_track_timescale 90000
-x264-params keyint=60:scenecut=0`. Base scale/crop for full-frame:
`scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1`.

Per `kind`:
- **th** — `ffmpeg -ss <a> -i TH -t <dur> -an -vf "<SCALE>,fps=30,format=yuv420p" <ENC> seg.mp4`
  (crop & scale ONLY — **no `-colorspace`/`-color_*`, no `-pix_fmt` on HDR TH**).
- **th_overlay** — two inputs, overlay the alpha mov on the full-frame TH:
  `-ss <a> -i TH -i overlay.mov -t <dur> -filter_complex
  "[0:v]fps=30,<SCALE>[base];[1:v]fps=30,setsar=1[ov];[base][ov]overlay=0:0:format=auto,format=yuv420p[v]"
  -map "[v]" -an <ENC>`. (Overlay input not seeked → its t=0 = segment start, so authored
  reveals land on the right words.)
- **split** — vstack the 1080×960 animation panel over the face-centered TH crop:
  `-i panel.mp4 -ss <a> -i TH -t <dur> -filter_complex
  "[0:v]fps=30,scale=1080:960,setsar=1[top];[1:v]fps=30,crop=1080:960:0:<face_crop_y>,setsar=1[bot];
  [top][bot]vstack=inputs=2,format=yuv420p[v]" -map "[v]" -an <ENC>`.
- **animation_full** — cutaway mp4: `-i cutaway.mp4 -t <dur> -an -vf "fps=30,<SCALE>,format=yuv420p"`
  for `trim`; add `tpad=stop_mode=clone:stop_duration=<dur-clipdur>` before `format` for `hold_last`.
- **claim_proof** — PREFERRED (`sequenced_after_animation`): emit the companion animation and the
  full-frame article card as TWO back-to-back `animation_full`-style segments (the article card is an
  opaque full-frame mp4 on the same ivory bg). The cut between them is seamless (shared bg, card
  slides in) — do NOT composite the article on top of the animation (double-stacking looks cluttered;
  user-rejected). `full_frame_receipt` = a single full-frame article/receipt clip. `overlay_card`
  (compact citation only) = like th_overlay with a small receipt-card mov over the TH / negative space.
  In all cases the marker-sweep is authored to land on the spoken proof phrase; ensure the segment
  window contains the sweep time.

Then: write a concat list, `ffmpeg -f concat -safe 0 -i list.txt -c copy concat.mp4`, and
`ffmpeg -i concat.mp4 -i TH -map 0:v:0 -map 1:a:0 -c:v copy -c:a copy -movflags +faststart FINAL.mp4`.

## Hard rules
- **VO untouched:** final audio is `-c:a copy` from the TH (verify output audio duration == TH audio duration).
- **TH never recolored:** crop & scale only; no color-space flags / no `-pix_fmt` on the TH stream. HDR: never silently tonemap (AGENT_GUIDE rule).
- **split centers the face** (face_crop_y); overlays never cover the face.
- **Identical encode params** across segments so `concat -c copy` is seam-clean.

## QA (mandatory)
1. **ffprobe**: 1080×1920, 30fps, duration ≈ TH duration; audio AAC, duration == TH audio.
2. **Decode**: `ffmpeg -v error -i FINAL.mp4 -f null -` (no errors); `blackdetect` (no long black).
3. **Contact sheet**: sample a frame in each segment; confirm the right content, no black seams,
   face framed in splits, overlays clear of the face.
4. **NARRATION-SYNC verification (CRITICAL):** for each animated reveal and every claim
   marker-sweep, sample a frame just BEFORE and just AFTER its trigger word. The element/highlight
   must be ABSENT before and PRESENT on/after the word. Any early reveal → retime the asset and
   re-render. Confirm the claim highlight lands exactly on the spoken claim phrase.

## Output: `render_report` + `final_review`
- output_path, ffprobe summary, QA results, per-segment notes, sync-verification result,
  audio_unchanged: true, hdr_handling.

## Self-evaluate
- Output plays clean; duration ≈ TH; audio == original TH (unchanged).
- Splits center the face; overlays clear of face; seams clean.
- Every reveal + claim highlight verified on-word by before/after frame sampling; self_review_completed.
