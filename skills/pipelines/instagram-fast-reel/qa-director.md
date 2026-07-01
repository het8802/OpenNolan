# QA Director — instagram-fast-reel (final gate)

Inspect the actual rendered `renders/final.mp4` before it's presented. Do it in this order — the
hook first at a HIGH sample rate, then the whole reel at a lower rate, then the technicals. This
is the last stage; it produces the `final_review` artifact and gates the deliverable. Human
approval required.

## Step 1 — HOOK at 5 fps (do this FIRST)
The hook is the whole reel — if it's weak, nothing else matters, so QA it densely before
spending the full pass.
- Hook window = the first ~3s (use the duration of `cuts[0]` from `edit_decisions`, capped ~3s).
- `frame_sampler`: `strategy="interval"`, `interval_seconds=0.2` (= **5 frames/second**),
  `input_path=renders/final.mp4`, restricted to the hook window (sample from 0 to hook_end).
- Inspect every hook frame: no black/frozen opening frame; the hook caption/first animation is
  legible and lands ON its trigger word (not late, not early); framing is 9:16 with the face/subject
  in-frame; nothing broken.
- **If the hook fails, STOP** — set `status="revise"` (or `fail`), `recommended_action`
  accordingly, and report before running Step 2. Don't burn the full pass on a broken hook.

## Step 2 — FULL REEL at 2 fps
- `frame_sampler`: `strategy="interval"`, `interval_seconds=0.5` (= **2 frames/second**) over the
  entire output. (Cap with `max_frames` for long clips; note the cap if hit.)
- Scan the sampled frames for: black or frozen frames, broken/missing overlays, meme GIFs that
  never appear (or never leave), captions pushed into the platform UI shadow zone or clipped
  off-frame, unreadable text, and any planned animation that's absent.

## Step 3 — TECHNICALS (probe everything)
- `visual_qa` `operation="probe"` with `expected={width:1080, height:1920, has_audio:true}`:
  valid container, correct resolution / fps / codec / pixel_format, and a duration that matches
  the tightened edit (must be SHORTER than the source — that proves the fast cuts landed).
- Decode clean (no decode errors); no long black segments (blackdetect).
- `visual_qa` `operation="audio_levels"` at several timestamps: VO present + intelligible, music
  ducked under it, no clipping, and **no leftover dead air** (it should have been cut with the video).
- Captions: coverage vs the transcript, no timing drift.
- HDR: if the source was HDR, confirm it wasn't silently tonemapped.

## Step 4 — SYNC + advisory
- Using the sampled frames, confirm no animation / GIF / caption appears BEFORE its trigger word.
  An early reveal is a **CRITICAL** finding.
- Optional `content_signal` advisory virality score — informational, **never a gate**.

## Output
`final_review` (schema-valid, `schemas/artifacts/final_review.schema.json`):
- `status`: `pass` / `revise` / `fail`.
- `checks`: `technical_probe`, `visual_spotcheck` (record `frames_sampled` + `frame_paths` from
  BOTH the 5fps hook pass and the 2fps full pass), `audio_spotcheck`, `subtitle_check`,
  `promise_preservation` (runtime that ran == locked runtime, no swap).
- `issues_found` and `recommended_action` (`present_to_user` / `re_render` / `revise_edit` /
  `revise_assets` / `block`).

## Quality bar
Hook passed at 5fps, full reel scanned at 2fps, all technicals green, sync verified. Any CRITICAL
finding (bad hook, early reveal, black frames, wrong resolution, missing audio, leftover dead air)
→ `status` revise/fail with a concrete next action; do NOT present as complete. On `pass`, point
the user to `renders/final.mp4` + the live `edit_decisions.json` to hand-tune in the Studio editor.
