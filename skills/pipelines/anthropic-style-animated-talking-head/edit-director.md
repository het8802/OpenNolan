# Edit Director — anthropic-style-animated-talking-head

## When to Use
You have a `scene_plan` (shot modes + timings) and an `asset_manifest`. Produce `edit_decisions`
in the **standard editor schema** (`cuts[]` + `overlays[]` + `metadata` + `audio`) so the reel both
renders through `video_compose` AND opens in the desktop editor for the human to refine. The
composition is rendered **render-once / edit-cheap**: each scene becomes a content-cached proxy and
the timeline is a cheap FFmpeg assemble (see compose-director).

> **Schema change (2026-06):** this pipeline no longer emits a bespoke `segments[]` array or a
> hand-written FFmpeg recipe. It emits the SAME `cuts[]`/`overlays[]` contract the editor reads
> (`schemas/artifacts/edit_decisions.schema.json`). The talking head is HDR-safe: `video_compose`
> preserves HLG/PQ end-to-end and lifts the SDR graphics into the HDR container — you no longer hand
> the TH to a hand-rolled `-c copy` mux.

## Prerequisites
| Layer | Resource |
|-------|----------|
| Scene plan | beats with shot_mode, start/end, specs, face_crop_y, overlay_safe_zones |
| Asset manifest | built cutaways (mp4), overlays (mov, alpha), split panels, receipts, durations |
| Schema | `schemas/artifacts/edit_decisions.schema.json` (cuts/overlays/transform/track/metadata/audio) |

## The model: a primary track that tiles the TH duration + overlays on top

The talking head is the base. The `cuts[]` (primary track) **tile the entire TH duration with no
gaps and no overlaps**, so the continuous VO stays in sync. Graphics that REPLACE the frame are
primary cuts; graphics that sit ON the face are `overlays[]`. The continuous VO is carried as
`audio.path` and muxed over the assembled video (see Audio below) — so a face span and a full-frame
cutaway both run under one unbroken VO, exactly like the old segment-rebuild.

### Canvas + project background
`metadata.compose_target = {width:1080, height:1920, fps:30}`. The shared ivory backdrop is
`metadata.background = {type:"color", color:"#<ivory>"}` so any letterboxed/positioned clip sits on
ivory, not black, and sequenced full-frame cards share one seam-free bg.

### Shot-mode → schema mapping

**`th` (full-frame face)** → one primary cut, the TH window:
```json
{ "id": "th_03", "source": "<TH source>", "in_seconds": 33.55, "out_seconds": 35.45 }
```
`in/out` are the cut's REAL position in the TH timeline (not 0-based) — the renderer trims that
window. The base scale/crop to 1080×1920 is applied automatically.

**`animation_full` (full-frame cutaway)** → one primary cut referencing the rendered mp4:
```json
{ "id": "anim_05", "source": "assets/video/stripe_stat.mp4", "in_seconds": 0, "out_seconds": 4.55 }
```
`fit`: if the asset is LONGER than its window, set `out_seconds` to the window length (trim). If
SHORTER, the asset-director pre-renders the `tpad`-extended (freeze-last-frame) mp4 and you reference
it 1:1 — no renderer magic. Duration must equal the beat span so the primary track keeps tiling.

**`claim_proof`, presentation `sequenced_after_animation`** → TWO consecutive primary cuts on the
shared ivory bg (animation hero reveal, then the full-frame article card). Seamless cut, no overlay
double-stacking (user-rejected). Record the marker-sweep trigger for QA (below).
```json
{ "id": "stripe_anim",    "source": "assets/video/stripe.mp4",         "in_seconds": 0, "out_seconds": 4.55 },
{ "id": "stripe_article", "source": "assets/video/stripe_card.mp4",    "in_seconds": 0, "out_seconds": 4.82 }
```

**`th_overlay` (alpha graphic ON the face)** → a primary TH cut PLUS an `overlays[]` entry. The
overlay is the alpha `.mov`; its `start_seconds`/`end_seconds` are ABSOLUTE project time (= cumulative
primary-cut duration up to this beat), and `position` keeps it in a safe zone (never on the face).
```json
"cuts":     [ { "id": "th_01", "source": "<TH>", "in_seconds": 0, "out_seconds": 7.55 } ],
"overlays": [ { "type": "video", "asset_id": "assets/overlays/logo_pop.mov",
                "start_seconds": 0.0, "end_seconds": 7.55,
                "position": {"x": 300, "y": 120}, "track": 1 } ]
```
The overlay's internal reveals already land on trigger words (authored in assets). Stack multiple
overlays on ascending `track` (higher = on top). Use absolute project time for every overlay window
— accumulate the durations of the primary cuts listed before this beat.

**`split_5050` (panel top / face bottom)** → editable LAYERS (non-uniform clip scale):
- The FACE is a primary cut placed in the BOTTOM half, with `transform.crop` centering the face and
  `transform.scale = {x:1.0, y:0.5}` (a full-width, half-height box) at `position {x:0, y:960}`:
```json
{ "id": "split_07", "source": "<TH>", "in_seconds": 19.18, "out_seconds": 24.30,
  "transform": { "crop": {"x":0, "y":<face_crop_y>, "width":1080, "height":960},
                 "scale": {"x":1.0, "y":0.5}, "position": {"x":0, "y":960} } }
```
  The crop is in SOURCE pixels and pre-shapes the face to the 9:8 panel aspect so it FILLS the box
  (no letterbox). `face_crop_y` comes from the scene plan.
- The PANEL (top 1080×960 animation) is a `video` overlay sized to the top half. NOTE: overlay
  `width`/`height` are NESTED inside `position` (the schema rejects them at the overlay top level):
```json
{ "type": "video", "asset_id": "assets/panels/counter_1M.mp4",
  "start_seconds": 19.18, "end_seconds": 24.30,
  "position": {"x": 0, "y": 0, "width": 1080, "height": 960}, "track": 1 }
```
  Both halves are independently re-editable in the Studio. The panel's reveals are authored on
  trigger words.

**`claim_proof`, presentation `overlay_card`** (compact citation only) → a primary TH cut + an
`overlays[]` receipt `.mov` in a corner / negative space (face visible), same shape as `th_overlay`.

## Audio — the VO is carried continuous, untouched in content/timing
Set `audio.path` to the TH source so the WHOLE original VO is muxed over the assembled video:
```json
"audio": { "path": "<TH source>" }
```
The renderer maps the continuous TH audio over the rebuilt video (the cuts tile the exact TH
duration, so it stays in sync). The VO is NOT sped, cut, or re-timed; it is re-muxed at high-bitrate
AAC (content/duration preserved). Only generate music/SFX if the brief asked; keep music ≤0.08 under
the VO.

## render_runtime
Set `render_runtime: "ffmpeg"`. The animated assets are AUTHORED in HyperFrames at the assets stage
(rendered to mp4/mov there), but the TIMELINE is ASSEMBLED in FFmpeg — `video_compose
operation=render_proxies` renders each scene's proxy once and assembles. (The old
`hyperframes+ffmpeg` label is dropped; the assemble is pure ffmpeg.) `renderer_family` carries
through from the idea stage unchanged.

## HDR
The TH is frequently HDR (HLG/PQ). Do NOT recolor it. `video_compose` auto-detects HDR and PRESERVES
it (10-bit HEVC + color tags), lifting the SDR graphics into the HDR container — pass `hdr_policy`
explicitly only to override (compose-director owns the QA). Record the choice; never silently tonemap.

## Narration Sync — HARD RULE (unchanged)
Every animated reveal lands **ON its trigger word, never before** (source of truth =
`transcript.json words[]`). Record `trigger_word` + the `words[]` timestamp for each reveal/marker-sweep
into `metadata.claim_sweep[]` (and keep it next to each overlay) so the compose director can verify
with before/after-word frame sampling and treat any early reveal as CRITICAL.

## Output: `edit_decisions`
- `cuts[]` (primary track, tiling [0, TH_duration]; graphics-as-cuts for full-frame, transform for split)
- `overlays[]` (alpha graphics ON the face / split panel; absolute project-time windows; track z-order)
- `audio: {path: <TH source>}`, `metadata: {compose_target, background, claim_sweep, face_crop_y}`
- `render_runtime: "ffmpeg"`, `renderer_family` carried

## Self-evaluate
- Primary cuts tile [0, TH_duration] exactly; no gaps/overlaps; sum == TH duration (VO stays in sync).
- Each cut/overlay references a valid asset; `out_seconds > in_seconds`; durations match the beat spans.
- th_overlay/split overlays use ABSOLUTE project time and safe-zone positions (never on the face).
- split face uses crop (center face) + scale {x:1,y:0.5} + bottom-half position; panel overlay tops it.
- claim beats with a companion animation are SEQUENCED (two cuts), not overlaid.
- `audio.path` = TH source; `render_runtime="ffmpeg"`; validates against `edit_decisions.schema.json`.
