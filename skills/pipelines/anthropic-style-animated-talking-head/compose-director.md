# Compose Director — anthropic-style-animated-talking-head

## When to Use
You have `edit_decisions` in the standard `cuts[]`/`overlays[]` schema (from the edit director) and
the `asset_manifest`. Assemble the final reel and QA it. Produce `render_report` + `final_review`.

> **Render-once / NLE model (2026-06):** you no longer hand-write an FFmpeg segment-rebuild. Call
> `video_compose` with `operation="render_proxies"` — it renders each scene SOLO to a content-cached
> proxy, then assembles the timeline with a cheap FFmpeg concat that applies overlays, audio,
> transitions and subtitles. Re-edits (reorder, retime, re-score) reuse the cached proxies; only a
> scene whose content changed re-renders. This is the same path the desktop editor uses, so the reel
> you produce opens in the editor for the human to refine.

## Prerequisites
| Layer | Resource |
|-------|----------|
| Edit decisions | `cuts[]` (TH windows / full-frame graphics / split face), `overlays[]` (alpha graphics, split panel), `audio.path` (TH VO), `metadata.{compose_target,background,claim_sweep}` |
| Assets | cutaways (mp4), overlays (mov, alpha), split panels, receipts, the TH source |
| Tool | the `render` tool (render-once / render_proxies path) |
| Schema | `schemas/artifacts/render_report.schema.json` |

## Step 1 — HDR preflight (MANDATORY, before rendering)
The talking head is frequently HDR (HLG/PQ, 10-bit). NEVER silently tonemap it.

1. Detect: `is_hdr_source(<TH source>)` (`tools/video/_shared.py`). `hdr=True` → HDR (HLG/PQ).
2. Device: `video_compose.get_info()["hdr_encode"]`. `available=False` → no 10-bit HEVC encoder here.
3. Decide + log an `hdr_handling` decision in `decision_log`:
   - HDR source + encoder available → **preserve** (default). `render_proxies` keeps the TH 10-bit
     HEVC + carries its HLG/PQ color tags, and LIFTS the SDR graphics/overlays into the HDR
     (BT.2020) container so the whole timeline shares one color space. Pass `hdr_policy="preserve"`
     (or `"auto"`, which preserves when HDR is present).
   - HDR source + NO encoder (or no zscale) → surface the blocker (`render_proxies` returns a
     structured error / warning); get explicit consent before `hdr_policy="tonemap"`.
   - SDR source → nothing special; `auto` leaves it SDR (byte-identical to before).

## Step 2 — Render (one call: the `render` tool)
Call the in-process **`render` tool** — do NOT run `VideoCompose` via `run_in_background` Bash. The
tool drives the same `render_proxies` path through the shared render job store, BLOCKS until the
render finishes, and returns `{success, output_path, warnings, final_review_status}` so you continue
straight to QA in THIS turn. (Background renders end your turn and break message attribution.)

```jsonc
// render tool input
{
  "edit_decisions": <standard cuts[]/overlays[] from the edit stage, render_runtime + renderer_family locked>,
  "asset_manifest": <asset_manifest>,
  "output_path": "projects/<name>/renders/final.mp4",   // optional; normalized under renders/
  "proxies_dir": "projects/<name>/renders/proxies",      // optional
  "hdr_policy": "auto",                                   // or "preserve"/"tonemap" per Step 1's logged decision
  "proposal_packet": <proposal_packet>                    // so final_review can run (the assemble is NOT a runtime swap)
}
```
- Path resolution is handled for you: the store resolves project-relative `source`/`asset_id` refs to
  absolute before rendering, so the proxy content-hash keys match and files are found.
- The result's `output_path`, `warnings`, and `final_review_status` come straight back from the tool;
  surface the HDR handling + warnings. (Re-render reuses cached proxies; only changed scenes re-render.)
- The assemble is `render_runtime="ffmpeg"` by construction; `final_review` will NOT flag a runtime
  swap (it honors `metadata.assemble_of_proxies`).
- If the user STOPS mid-render, the job keeps running; on your next turn you'll get a `[RENDER UPDATE …]`
  note with the finished output — pick up from QA there, do NOT re-render.

## Hard rules
- **VO untouched in content/timing:** the continuous TH VO rides as `audio.path` and is muxed over
  the rebuilt video; it is never sped/cut/retimed. Verify output audio duration ≈ TH audio duration.
- **TH never recolored:** HDR is preserved (or tonemapped only with logged consent). No silent SDR.
- **No double-stacking claims:** sequenced claim beats are two back-to-back full-frame cuts, not an
  article overlaid on a busy animation (enforced at the edit stage; verify in QA).
- **split centers the face** (crop `face_crop_y`); overlays never cover the face.

## QA (mandatory)
1. **ffprobe** the output: 1080×1920, 30fps, duration ≈ TH duration; audio AAC, duration == TH audio.
2. **HDR check:** if the source was HDR and the decision was `preserve`, assert the output is 10-bit
   with the right tags: `ffprobe … pix_fmt` = `yuv420p10le`, `color_transfer` = `arib-std-b67` (HLG)
   or `smpte2084` (PQ), `color_primaries` = `bt2020`. A `preserve` decision that yields an 8-bit /
   bt709 output is a CRITICAL failure — do not present it.
3. **Decode:** `ffmpeg -v error -i FINAL.mp4 -f null -` (no errors); `blackdetect` (no long black).
4. **Contact sheet:** sample a frame in each beat; confirm the right content, no black seams, face
   framed in splits, overlays clear of the face.
5. **NARRATION-SYNC verification (CRITICAL):** for each reveal/marker-sweep in `metadata.claim_sweep[]`
   (and each overlay), sample a frame just BEFORE and just AFTER its trigger word. The
   element/highlight must be ABSENT before and PRESENT on/after the word. Any early reveal → retime
   the asset in HyperFrames, re-render that asset, then call the `render` tool again (only the changed
   scene re-renders). Confirm the claim highlight lands exactly on the spoken claim phrase.

## Output: `render_report` + `final_review`
- output_path, ffprobe summary (incl. pix_fmt + color tags), QA results, per-beat notes,
  sync-verification result, `audio_unchanged: true`, `hdr_handling` (source_hdr, output pix_fmt,
  policy, decision), `n_rendered`/`n_cached`.

## Self-evaluate
- Output plays clean; duration ≈ TH; audio == original TH content (unchanged timing).
- HDR preserved when chosen (output pix_fmt/color tags verified) — never a silent SDR downgrade.
- Splits center the face; overlays clear of face; sequenced claims are seam-clean on shared ivory.
- Every reveal + claim highlight verified on-word by before/after frame sampling; self_review_completed.
