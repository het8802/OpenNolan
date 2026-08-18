# Video Editing Tools — What the Agent Has and How It Works

This document inventories every tool the OpenNolan agent can call to **edit** video/audio
(not generate new assets, and not the MCP tools registered separately). For each tool it
lists:

1. **The discovery metadata** — the exact fields the agent sees when it queries the tool
   registry (`tools/tool_registry.py`), i.e. how the agent learns this tool exists and what
   it's for.
2. **What it actually executes** — the real ffmpeg/ffprobe command(s), filter chains, or
   other library calls (PIL/numpy/librosa/mediapipe/rembg/etc.), with `file:line` citations.
3. **A flow diagram** tracing a real example call from the agent's tool-call payload down to
   the terminal subprocess/output artifact, drawn in the `ascii-explain` skill's style
   (box-drawing, real file:line anchors, branch points marked `★ DIVERGENCE`).

No code was changed to produce this document — it's a read-only snapshot of the code as it
exists on `main` today.

## Scope: what counts as "editing"

The `tools/` package holds far more than editing tools — it also has AI **generation**
providers (Kling/Veo/Runway/Seedance/etc. video-gen, ElevenLabs/OpenAI/Google TTS, Flux/
Imagen/Recraft image-gen, Suno/music-gen), stock-footage search (Pexels/Pixabay), web image
search, avatar/talking-head generation, and publishers. Those create **new** assets and are
deliberately **out of scope** here — say the word and I'll do a follow-up pass on them.

"Video editing tools" in this doc = every tool whose `capability` is one of:

| capability | what it means | count |
|---|---|---|
| `video_post` | cutting, composing, timeline effects, masking, transitions | 15 |
| `subtitle` | caption generation + burn-in | 2 |
| `enhancement` / `segmentation` | visual polish, cutout, restoration | 7 |
| `audio_processing` | mixing, ducking, cleanup, voice ops | 3 |
| `analysis` (editing-support subset) | inspection tools the agent uses *while* editing — scene detection, QA, loudness, face tracking, composition validation | 7 |
| `screen_capture` | acquiring footage to edit | 3 |

**37 tools total.**

## How the agent discovers these tools (recap)

There is no hardcoded tool list. At preflight the agent runs `registry.discover()`
(`tools/tool_registry.py:115`), which walks every module under `tools/` via
`pkgutil.walk_packages` and auto-registers every concrete `BaseTool` subclass it finds
(`register_module()`, `tools/tool_registry.py:74`). The agent then queries
`registry.capability_catalog()` / `registry.provider_catalog()` / `registry.get_by_capability(...)`
to see what's available — grouped exactly as this document groups them. Every field in each
tool's "Discovery metadata" table below is a class attribute on a `BaseTool` subclass
(`tools/base_tool.py:148`), surfaced verbatim through `BaseTool.get_info()`
(`tools/base_tool.py:236`). See [`FFMPEG_TOOLS.md`](FFMPEG_TOOLS.md) for the registry
mechanism in more depth (it covers the ffmpeg-provider subset of what's documented here).

Every tool implements `execute(self, inputs: dict) -> ToolResult` — this is the function
that actually runs when the agent calls the tool. Most video-editing tools shell out to
`ffmpeg`/`ffprobe` via `BaseTool.run_command()` (`tools/base_tool.py:318`), a thin wrapper
around `subprocess.run(cmd, capture_output=True, text=True, check=True)`.

---

## Table of contents

**Core composition**
- [`video_compose`](#video_compose) — the main render orchestrator
- [`video_trimmer`](#video_trimmer) — cut / speed / concat
- [`video_stitch`](#video_stitch) — multi-clip assembly + transitions + spatial layouts
- [`auto_reframe`](#auto_reframe) — aspect-ratio conversion with face-tracked crop

**Timeline effects**
- [`motion_ops`](#motion_ops) — freeze / reverse / speed / pan-zoom / clip fx / flip
- [`mask_ops`](#mask_ops) — blur region / spotlight / image mask / masked reveal
- [`silence_cutter`](#silence_cutter) — auto jump-cuts via silence detection
- [`showcase_card`](#showcase_card) — 9:16 presentation card
- [`keyframe_animate`](#keyframe_animate) — overlay motion keyframe spec emitter
- [`beat_cutter`](#beat_cutter) — beat-synced cut planning (librosa)
- [`template_apply`](#template_apply) — reusable reel template → edit_decisions

**Compositing engines**
- [`green_screen_composite`](#green_screen_composite) — speaker-over-background layouts
- [`green_screen_processor`](#green_screen_processor) — chromakey or rembg keying
- [`hyperframes_compose`](#hyperframes_compose) — HTML/CSS/GSAP render path
- [`fuse_transition`](#fuse_transition) — Seedance AI morph transition

**Captions & enhancement**
- [`subtitle_gen`](#subtitle_gen) — SRT/VTT/JSON caption generation
- [`remotion_caption_burn`](#remotion_caption_burn) — styled caption burn (Remotion + ffmpeg fallback)
- [`color_grade`](#color_grade) — LUTs, presets, curves, auto-correct
- [`face_enhance`](#face_enhance) — skin smoothing / sharpening (pure ffmpeg)
- [`bg_remove`](#bg_remove) — still-image background removal (rembg)
- [`face_restore`](#face_restore) — CodeFormer/GFPGAN face restoration
- [`eye_enhance`](#eye_enhance) — dark circles / brighten / sharpen eyes (mediapipe)
- [`upscale`](#upscale) — Real-ESRGAN frame upscaling
- [`object_cutout`](#object_cutout) — SAM2 segmentation + local effects

**Audio editing**
- [`audio_mixer`](#audio_mixer) — mix / duck / extract / auto-balance
- [`audio_enhance`](#audio_enhance) — noise reduction / EQ / de-ess / AI isolate
- [`voice_ops`](#voice_ops) — record / pitch-effect / insert onto timeline

**Analysis / inspection (editing-support)**
- [`frame_sampler`](#frame_sampler) — extract representative frames
- [`scene_detect`](#scene_detect) — scene/shot boundary detection
- [`visual_qa`](#visual_qa) — resolution/duration/codec checks
- [`audio_energy`](#audio_energy) — loudness profiling (ebur128)
- [`audio_probe`](#audio_probe) — ffprobe duration/format/stream inspection
- [`face_tracker`](#face_tracker) — face bounding-box tracking (mediapipe/OpenCV)
- [`composition_validator`](#composition_validator) — pre-render edit_decisions validation

**Screen capture**
- [`screen_recorder`](#screen_recorder) — cross-platform ffmpeg native capture
- [`cap_recorder`](#cap_recorder) — bridges to the standalone Cap.app
- [`screen_capture_selector`](#screen_capture_selector) — picks ffmpeg vs Cap

---

# Core composition

## `video_compose`

**File:** `tools/video/video_compose.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `ToolTier.CORE` (`video_compose.py:51`) |
| capability | `"video_post"` (`:52`) |
| provider | `"ffmpeg"` (`:53`) |
| runtime | not overridden → `ToolRuntime.LOCAL` default (`base_tool.py:158`) |
| determinism | `Determinism.DETERMINISTIC` (`:56`) |
| dependencies | `["cmd:ffmpeg"]` (`:58`) |
| capabilities | `["compose_cuts", "burn_subtitles", "overlay_assets", "encode_profile", "remotion_render"]` (`:62-68`) |
| best_for | "Final render for explainer/animation pipelines", "Image-to-video with spring animations (Remotion)", "Animated text/stat cards, charts (Remotion)", "Complex transitions between scenes (Remotion)", "Pure video concat, trim, and xfade cross-transitions (FFmpeg)" (`:291-297`) |
| not_good_for | "HDR sources — output is 8-bit SDR; detect with `is_hdr_source()` and handle HDR per AGENT_GUIDE before using this tool" (`:298-301`) |
| key input params | `operation` (enum: `compose,render,render_proxies,remotion_render,burn_subtitles,overlay,encode`, `:74-83`), `edit_decisions` (`.cuts[]`, `.render_runtime`, `.overlays[]`, `.subtitles`, `.metadata.compose_target/.background/.hdr`), `asset_manifest` (id→path resolution), `subtitle_path`/`subtitle_style`, `overlays[]` (`asset_path`/`type`/`text`/`position`/`keyframes`/`audio_mix`, `:157-244`), `audio_path`, `profile`, `options.subtitle_burn`, `codec`/`crf`/`preset`, `hdr_policy` (`:264-278`) |

### What it actually executes
`execute()` (`:653`) dispatches purely on `operation` to one private method (`:658-673`): `compose→_compose`, `render→_render`, `render_proxies→_render_proxies`, `remotion_render→_remotion_render`, `burn_subtitles→_burn_subtitles`, `overlay→_overlay`, `encode→_encode`.

`_render()` (`:2451`) is the pipeline's normal entry point: it resolves `cuts[].source` asset IDs against `asset_manifest` (`:2486-2492`), runs `_pre_compose_validation` (`:2496`), then reads `edit_decisions.render_runtime` and refuses to guess if it's missing (`:2508-2521`) — a governance rule against silent engine swaps. It branches to `_render_via_hyperframes` (`:2523-2531`), `_render_via_ffmpeg` (`:2532-2541`), or the Remotion path (`_needs_remotion` → `_remotion_render`, else `_compose` fallback, `:2552-2600`).

The FFmpeg path (`_render_via_ffmpeg`, `:3036`) is a two-pass pipeline: base concat via `_compose`, then overlays via `_overlay` (docstring diagram at `:3053-3059`). It bridges `edit_decisions.audio.path`→`audio_path` (`:3095-3099`), resolves `subtitles.source` (`:3105-3109`), resolves `overlays[].asset_id`→path (`:3111-3136`), lifts `cuts[].layer=="overlay"` into timed PiP entries via `_build_layer_overlay_entries` (`:3140-3165`, `:3385`), and resolves `metadata.background` into a `composite_background` gate that only `_render_via_ffmpeg` ever sets (`:3176-3202`) — this is what keeps solo-proxy renders from baking a background into their cache key. It calls `_compose` (`:3251`), then `_overlay` on the result if any overlays exist (`:3259-3290`), then `_apply_structured_audio_mix` for stem-based audio (`:3298-3303`), then the mandatory `_run_final_review` (`:3306-3327`).

`_compose()` (`:1725`) is the actual FFmpeg builder: `_resolve_canvas` (`:1132`, precedence `metadata.compose_target` > `profile` > 1920×1080@30) and `_resolve_joins` (`:976`, resolves each A→B transition, B's `transition_in` wins over A's `transition_out`). For each cut it builds a per-segment filter chain via `_segment_base_vf` (`:739`) — crop → HDR vf → `scale=…:force_original_aspect_ratio=decrease` → `pad=…:color=black` → `setsar=1` → `fps=…` — and runs one `ffmpeg -ss <in> -t <dur> -i <src> -filter:v "<chain>" -c:v libx264 -crf 23 -preset medium -pix_fmt yuv420p -r 30 -c:a aac -b:a 192k -ar 48000 -ac 2 seg_NNNN.mp4` per cut (`run_command` at `:2038`; still-image cuts use `-loop 1 -t <seg_seconds>` instead, `:1904-1930`). Segments then join: if no transitions, plain concat-demuxer `-c copy` (`:2057-2070`, byte-identical legacy path); if any join has a transition, `_transitions_concat()` (`:1041`) builds an `xfade`+`acrossfade` `-filter_complex` chain from **probed** (not requested) segment durations, e.g. `[0:v]settb=AVTB[vtb0];[1:v]settb=AVTB[vtb1];[vtb0][vtb1]xfade=transition=fade:duration=0.6:offset=3.4[vx1];[0:a][1:a]acrossfade=d=0.6[ax1]`, run via `self.run_command` (`:1124`). A final pass muxes subtitles (`-vf "subtitles='…':force_style='…'"`) and/or replaces audio (`run_command` at `:2112`).

`_overlay()` (`:4368`) builds one `-filter_complex` covering every overlay: it sorts by `track` (`:4447`), routes `type=="text"` items to `_build_drawtext_filter` (`:4461`, e.g. `[0:v]drawtext=fontfile=…:text=Hook line:expansion=none:fontsize=48:fontcolor=white:x='(w-text_w)/2':y='h*0.95-text_h':enable='gte(t,0)'[v0]`), image/video overlays to static `overlay=X:Y:enable='…'` or, when `keyframes` are present, to `_keyframe_overlay` (`:4636`) which emits time-varying `overlay`/`scale=eval=frame`/alpha expressions. `audio_mix` requests build an `amix` chain (`:4661-4694`). The whole graph runs as one `ffmpeg … -filter_complex "<chain>" -map […] -map […] -c:v … -c:a …` (`run_command` at `:4717`).

`_burn_subtitles` (`:4326`) and `_encode` (`:5349`) are thin single-pass wrappers around `subtitles=…:force_style=…` and a plain re-encode, respectively.

### Flow
```text
agent call: video_compose.execute({
  operation: "render",
  edit_decisions: {render_runtime:"ffmpeg",
    cuts:[{source:"clipA.mp4",in_seconds:0,out_seconds:4,
           transition_out:"fade"},
          {source:"clipB.mp4",in_seconds:2,out_seconds:8,
           transition_in:"fade",transition_duration:0.6}],
    overlays:[{type:"text",text:"Hook line"}],
    subtitles:{enabled:true,source:"subs.srt"}},
  asset_manifest:{...}, output_path:"out.mp4"
})
        │
        ▼
execute() :653   operation=="render" → self._render(inputs)
        │
        ▼
_render() :2451
  resolve cuts[].source via asset_manifest         (2486-2492)
  render_runtime = edit_decisions.render_runtime    (2508)
        │
        ★ DIVERGENCE on render_runtime (:2523/2532/2542)
        ├─ "hyperframes" ──► _render_via_hyperframes    (2524)
        ├─ "remotion"    ──► _needs_remotion? → Remotion
        │                    or _compose fallback       (2552-2600)
        └─ "ffmpeg" (this run) ──► _render_via_ffmpeg    (2534)
                                        │
                                        ▼
_render_via_ffmpeg() :3036
  bridge edit_decisions.audio → audio_path        (3095-3099)
  resolve subtitles.source → subtitle_path        (3105-3109)
  resolve overlays[].asset_id via manifest         (3114-3136)
  compose_target = "out_base.mp4" (overlays exist) (3170-3174)
        │
        ▼
_compose(cuts=base_cuts) :1725
  _resolve_canvas() :1132 → 1920x1080@30
  _resolve_joins(cuts) :976 → join={"fade",0.6}
  per cut → _segment_base_vf() :739 → per-seg ffmpeg:
    ffmpeg -ss 0 -t 4 -i clipA.mp4 -filter:v
     "scale=1920:1080:force_original_aspect_ratio=decrease,
      pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,
      setsar=1,fps=30"
     -c:v libx264 -crf 23 -preset medium -pix_fmt yuv420p
     -r 30 -c:a aac -b:a 192k -ar 48000 -ac 2
     seg_0000.mp4                    (run_command :2038)
    (clipB.mp4 → seg_0001.mp4, same chain)
        │
        ★ DIVERGENCE has_transitions? (:2047)
        ├─ NO  ──► concat demuxer "-c copy"        (2063-2070)
        └─ YES (fade join) ──► _transitions_concat() :1041
             [0:v]settb=AVTB[vtb0];[1:v]settb=AVTB[vtb1];
             [vtb0][vtb1]xfade=transition=fade:duration=0.6:
                          offset=3.4[vx1];
             [0:a][1:a]acrossfade=d=0.6[ax1]
             -map [vx1] -map [ax1] → concat.mp4  (run_command :1124)
        │
        ▼
  subtitles present → -vf "subtitles='subs.srt':
    force_style='...'" → run_command :2112 → out_base.mp4
        │
        ▼ (resolved_overlays non-empty, :3259)
_overlay({input_path:"out_base.mp4", overlays:[text]}) :4368
  sort by track (:4447) → text → _build_drawtext_filter :4461
    [0:v]drawtext=fontfile=...Arial Bold.ttf:text=Hook line:
     expansion=none:fontsize=48:fontcolor=white:
     x='(w-text_w)/2':y='h*0.95-text_h':enable='gte(t,0)'[v0]
  ffmpeg -i out_base.mp4 -filter_complex "<above>"
    -map [v0] -map 0:a? -c:v libx264 -crf 23 -c:a copy
    out.mp4                                  (run_command :4717)
        │
        ▼
_apply_structured_audio_mix (if stems, :3299)
_run_final_review(out.mp4, edit_decisions, ...) (:3306)
        │
        ▼
ToolResult(success=True, artifacts=["out.mp4"])
```

**Notes:**
- `render_runtime` routing is a hard governance gate — an unset or unavailable runtime returns a structured error rather than silently falling back to a different engine (`:2510-2521`, `:2542-2550`).
- The transition-free concat path is byte-identical to the pre-xfade legacy behavior (`:2063-2070`); `xfade`/`acrossfade` offsets are computed from **probed** post-normalization segment durations, not the requested in/out timestamps, to avoid fps-rounding drift (`:1057-1059`).

---

## `video_trimmer`

**File:** `tools/video/video_trimmer.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `ToolTier.CORE` (`:31`) |
| capability | `"video_post"` (`:32`) |
| provider | `"ffmpeg"` (`:33`) |
| runtime | not overridden → `ToolRuntime.LOCAL` default |
| determinism | `Determinism.DETERMINISTIC` (`:36`) |
| dependencies | `["cmd:ffmpeg"]` (`:38`) |
| capabilities | `["cut", "trim", "speed_adjust", "concat"]` (`:47`) |
| best_for | not set — inherits `BaseTool` default `[]` |
| not_good_for | not set — inherits `BaseTool` default `[]` |
| key input params | `operation` (enum: `cut,speed,concat`, `:53-56`), `input_path`/`output_path`, `start_seconds`/`end_seconds` (cut), `speed_factor` (0.1-100, speed), `segments[]` (`input_path`,`start_seconds`,`end_seconds` — concat), `codec` (default `"copy"`, `:73`) |

### What it actually executes
`execute()` (`:86`) dispatches on `operation` to `_cut`/`_speed`/`_concat` (`:91-96`). `_cut()` (`:105`) builds `ffmpeg -y -i <input> -ss <start> [-to <end>] -c copy|<codec>+aac <output>` — note `-ss` is placed **after** `-i` here (an output-level/precise seek), unlike `video_compose`'s per-cut builder which seeks before `-i` for speed — and runs it via `self.run_command(cmd)` (`:130`). `_speed()` (`:144`) builds `setpts=(1/factor)*PTS` for video and an `atempo` chain (`_build_atempo_chain`, `:255-270`, chained because `atempo` only accepts `[0.5, 100.0]`) for audio, re-encoding with `libx264`/`aac` (`:159-169`). `_concat()` (`:182`) optionally pre-trims each segment with its own `ffmpeg -i … -ss … -to … -c copy` call to a temp file when `start_seconds`/`end_seconds` are given (`:203-211`), writes a concat-demuxer list file (`:216-222`), then runs one final `ffmpeg -f concat -safe 0 -i <list> -c copy <output>` (`:224-231`), cleaning up temp files in a `finally` block (`:242-253`).

### Flow
```text
agent call: video_trimmer.execute({
  operation: "concat",
  segments: [
    {input_path:"a.mp4"},
    {input_path:"b.mp4", start_seconds:2.0, end_seconds:9.0}
  ],
  output_path: "combined.mp4"
})
        │
        ▼
execute() :86   operation=="concat"
        │
        ▼
_concat() :182
  for each segment (loop :195-214):
        │
        ★ DIVERGENCE: start/end given? (:203)
        ├─ NO  (a.mp4) ──► use seg_input as-is       (:214)
        └─ YES (b.mp4) ──► trim to temp file:
             ffmpeg -i b.mp4 -ss 2.0 -to 9.0 -c copy
               .concat_tmp/seg_0001.mp4  (run_command :205-211)
        │
        ▼
  write concat_list.txt:
    file 'a.mp4'
    file 'seg_0001.mp4'                        (:217-222)
        │
        ▼
  ffmpeg -f concat -safe 0 -i concat_list.txt
    -c copy combined.mp4          (run_command :224-231)
        │
        ▼
ToolResult(success=True, artifacts=["combined.mp4"])
```

**Notes:**
- `_cut`'s `-ss` placement (after `-i`) is the opposite convention from `video_compose`'s segment builder (`-ss` before `-i`) — worth knowing if debugging seek-accuracy differences between the two tools.
- `_concat` only pre-trims segments that carry `start_seconds`/`end_seconds`; plain full-clip segments pass straight into the concat-demuxer list untouched (`:213-214`).

---

## `video_stitch`

**File:** `tools/video/video_stitch.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `ToolTier.CORE` (`:32`) |
| capability | `"video_post"` (`:33`) |
| provider | `"ffmpeg"` (`:34`) |
| runtime | not overridden → `ToolRuntime.LOCAL` default |
| determinism | `Determinism.DETERMINISTIC` (`:37`) |
| dependencies | `["cmd:ffmpeg", "cmd:ffprobe"]` (`:39`) |
| capabilities | `["validate_clips", "stitch", "crossfade", "fade_through_black", "preview_stitch", "spatial_side_by_side", "spatial_vertical_stack", "spatial_picture_in_picture"]` (`:48-57`) |
| best_for | not set — inherits `BaseTool` default `[]` |
| not_good_for | not set — inherits `BaseTool` default `[]` |
| key input params | `operation` (enum: `validate,stitch,preview_stitch,spatial`, `:63-66`), `clips[]`, `transition` (enum `cut,crossfade,fade`, default `cut`), `transition_duration` (default 0.5), `auto_normalize` (bool), `target_resolution`/`target_fps`, `codec`/`crf`/`preset`, `profile`, `layout` (enum `side_by_side,vertical_stack,picture_in_picture`), `pip_position`/`pip_scale`/`pip_margin`, `dry_run` |

### What it actually executes
`execute()` (`:148`) first checks `dry_run` (returns a preflight probe report via `dry_run()`, `:175-198`, without executing), then dispatches `operation` to `_validate`/`_stitch`/`_preview_stitch`/`_spatial` (`:159-168`). `_validate()` (`:314`) probes every clip with `ffprobe -show_streams -show_format` (`_probe_clip`, `:256-308`, run via `self.run_command`) and diffs width/height/fps/codec/pixel_format/audio fields against clip[0] (`:349-378`). `_stitch()` (`:481`) probes all clips, decides `_needs_normalization` (`:466-475`), and — if clips mismatch, or `auto_normalize` is set, or `transition != "cut"` — re-encodes every clip via `_normalize_clip` (`:441-464`, `ffmpeg -vf "scale=…:force_original_aspect_ratio=decrease,pad=…" -r <fps> -c:v <codec> -crf … -preset … -c:a aac -ar 44100 -ac 2 -pix_fmt yuv420p`). For `crossfade`/`fade` it also force-adds silent audio to clips lacking a stream (`_ensure_audio_for_clips`, `:220-250`, via `anullsrc`), then dispatches to `_stitch_cut` (plain concat-demuxer `-c copy`, `:584-607`), `_stitch_crossfade` (`:609-633`), or `_stitch_fade_through_black` (`:635-657`). Both transition builders special-case exactly 2 clips (a single `xfade`+`acrossfade` filter_complex) versus N>2 clips, which fall to `_chain_xfade` (`:671-735`) — a progressively-chained `xfade`/`acrossfade` filtergraph across all adjacent pairs, offsets computed from `_get_xfade_offset`/cumulative probed durations. `_preview_stitch` (`:741`) just re-delegates to `_stitch` with forced 640×360/24fps/ultrafast settings. `_spatial()` (`:780`) dispatches `layout` to `_spatial_side_by_side` (`hstack`+`amix`, `:849-874`), `_spatial_vertical_stack` (`vstack`+`amix`, `:876-901`), or `_spatial_pip` (`overlay=` at a corner position map, `:903-943`) — all single `ffmpeg -filter_complex` calls run via `self.run_command`.

### Flow
```text
agent call: video_stitch.execute({
  operation: "stitch",
  clips: ["c1.mp4","c2.mp4","c3.mp4"],
  transition: "crossfade", transition_duration: 0.5
})
        │
        ▼
execute() :148   dry_run=False → operation=="stitch"
        │
        ▼
_stitch() :481
  probe each clip via _probe_clip() :256      (508-512)
  needs_norm = _needs_normalization() :466     (514)
        │
        ★ DIVERGENCE: needs_norm or transition!="cut"? (:534)
        └─ YES (crossfade always re-encodes) ──►
             _normalize_clip() per clip :441   (536-540)
             ffmpeg -i c1.mp4 -vf "scale=W:H:
               force_original_aspect_ratio=decrease,
               pad=W:H:(ow-iw)/2:(oh-ih)/2" -r FPS
               -c:v libx264 -crf 23 -preset medium
               -c:a aac -ar 44100 -ac 2 -pix_fmt yuv420p
               norm_0000.mp4    (run_command in _normalize_clip)
        │
        ▼
  transition in (crossfade,fade) → _ensure_audio_for_clips
    :220 (silent track for audio-less clips)   (547-550)
        │
        ▼
  dispatch transition=="crossfade" → _stitch_crossfade :609 (554)
        │
        ★ DIVERGENCE: len(clips)==2? (:617)
        ├─ YES ──► single xfade+acrossfade filter_complex
        └─ NO (3 clips, this run) ──► _chain_xfade() :671
             [0:v][1:v]xfade=transition=fade:duration=0.5:
                        offset=O0[vfade0];
             [vfade0][2:v]xfade=transition=fade:duration=0.5:
                        offset=O1[vout];
             [0:a][1:a]acrossfade=d=0.5[afade0];
             [afade0][2:a]acrossfade=d=0.5[aout]
             -map [vout] -map [aout] → combined.mp4
                                       (run_command :735)
        │
        ▼
ToolResult(success=True,
  data={method:"xfade_crossfade", ...})
```

**Notes:**
- `crossfade`/`fade` transitions ALWAYS trigger normalization (`:534`, `transition != "cut"` is part of the OR), even if clips already match — a hidden re-encode cost the agent should expect.
- `_spatial_side_by_side`/`_spatial_vertical_stack` only ever use `clips[0]`/`clips[1]` — extra clips passed in are silently ignored (`:855`, `:882`).

---

## `auto_reframe`

**File:** `tools/video/auto_reframe.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `ToolTier.CORE` (`:47`) |
| capability | `"video_post"` (`:48`) |
| provider | `"ffmpeg"` (`:49`) |
| runtime | not overridden → `ToolRuntime.LOCAL` default |
| determinism | `Determinism.DETERMINISTIC` (`:52`) |
| dependencies | `["cmd:ffmpeg"]` (`:54`); optional `mediapipe`/`opencv-python` for face tracking, else falls back to center-crop (`:55-58`) |
| capabilities | `["aspect_ratio_conversion", "face_tracked_crop", "smart_reframe", "center_crop"]` (`:62-67`) |
| best_for | not set — inherits `BaseTool` default `[]` |
| not_good_for | not set — inherits `BaseTool` default `[]` |
| key input params | `input_path` (required), `output_path`, `target_aspect` (enum `portrait,square,landscape,cinematic,vertical_4_5`, default `portrait`), `target_width`/`target_height` (override preset), `face_tracking_json` (pre-computed), `smoothing_window` (default 15), `face_padding` (default 0.4), `sample_fps` (default 5, only used if no `face_tracking_json`), `codec`/`crf` (default `libx264`/18) |

### What it actually executes
`execute()` (`:131`) probes source dimensions/fps via `ffprobe` (`_get_video_info`, `:221-240`), computes the crop box in source-pixel space matching the target aspect (`_compute_crop_size`, `:242-269`), and short-circuits if the source already matches (`:147-152`). It then gets face data (`_get_face_data`, `:300-332`) — either a pre-computed `face_tracking_json`, or by importing `tools.analysis.face_tracker.FaceTracker` and running it internally (`:313-322`). `_compute_face_tracked_crop()` (`:334`) computes face-center pixel trajectories and, if the face barely moves (<10% of frame width/height, `:372`), returns a single static `(x, y)` biased toward the upper third (`:373-384`); otherwise it moving-average-smooths the trajectory (`_smooth_positions`, `:403-410`) into per-frame `crop_xs`/`crop_ys` lists. Output resolution is resolved by `_compute_output_resolution` (`:271-298`, standard sizes per aspect preset). Finally, `isinstance(crop_x, list)` (`:186`) branches to `_render_dynamic_crop` (writes an FFmpeg `sendcmd` script and renders `crop=…,scale=…` with per-frame position updates, `:439-525`) or `_render_static_crop` (a single fixed `crop=…,scale=…`, `:412-437`) — both a single `ffmpeg` call via `self.run_command`.

### Flow
```text
agent call: auto_reframe.execute({
  input_path: "talk.mp4", target_aspect: "portrait"
})
        │
        ▼
execute() :131
  _get_video_info() :221 → src 1920x1080 @30
  _compute_crop_size() :242 → crop_w=608, crop_h=1080
  target != src → continue (skip early-return :147-152)
  _get_face_data() :300 → no face_tracking_json →
    tools.analysis.face_tracker.FaceTracker().execute(...)
                                            (313-322) → faces[]
        │
        ★ DIVERGENCE: faces non-empty? (:158)
        ├─ NO ──► center crop, method="center_crop" (166-170)
        └─ YES ──► _compute_face_tracked_crop() :334
             │
             ★ DIVERGENCE: x/y range < 10% of frame? (:372)
             ├─ YES ──► single static (avg_x,avg_y) crop (373-384)
             └─ NO (moving face, this run) ──►
                  _smooth_positions() :403, window=15
                  → crop_xs[], crop_ys[] (dynamic lists) (386-401)
        │
        ▼
  _compute_output_resolution() :271 → out 1080x1920
        │
        ★ DIVERGENCE: isinstance(crop_x, list)? (:186)
        └─ YES (dynamic, this run) ──► _render_dynamic_crop() :439
             write .reframe_tmp/crop_commands.txt:
               "0.000 [enter] crop x CX0;
                0.000 [enter] crop y CY0; ..."   (468-484)
             ffmpeg -i talk.mp4 -vf
               "sendcmd=f='crop_commands.txt':flags=enter,
                crop=608:1080:CX0:CY0,scale=1080:1920"
               -c:v libx264 -crf 18 -preset fast
               -c:a aac -b:a 192k
               talk_portrait.mp4      (run_command :493-500)
             [on exception → fallback to _render_static_crop
              with averaged crop_x/crop_y, :502-515]
        │
        ▼
ToolResult(success=True,
  data={method:"face_tracked", output:"talk_portrait.mp4"})
```

**Notes:**
- `_get_face_data` swallows any `FaceTracker` import/execution failure with a bare `except Exception: pass` (`:329-330`), silently degrading to center-crop — no warning surfaces to the caller.
- `_render_dynamic_crop`'s `sendcmd` render itself falls back to a static average-position crop on any `ffmpeg` exception (`:502-515`), so "face_tracked" dynamic reframing has two independent degrade paths before it ever fails outright.

---

# Timeline effects

## `motion_ops`

**File:** `tools/video/motion_ops.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | CORE (`tools/video/motion_ops.py:66`) |
| capability | `video_post` (`:67`) |
| provider | `ffmpeg` (`:68`) |
| runtime | LOCAL (`:72`) |
| determinism | DETERMINISTIC (`:71`) |
| dependencies | `["cmd:ffmpeg"]` (`:74`) |
| capabilities | `freeze, reverse, speed, segment_volume, volume_boost, pan_zoom, clip_fx, flip` (`:96`, `= list(OPERATIONS)`) |
| best_for | freeze/reverse/speed/segment-volume; punch-in/Ken Burns/pan baked into footage; beat-timed shake/zoom-pulse/strobe/glitch; flip/rotate (`:98-103`) |
| not_good_for | time-varying speed ramps with easing; edit-stage constant speed (use `cuts[].speed`); HDR sources (8-bit SDR only); 3D camera moves/parallax (`:104-109`) |
| key input params | `operation` (enum, required), `input_path`/`output_path`; freeze: `at_seconds`,`duration`; speed: `factor` (0.5-4.0); segment_volume: `segments=[{start,end,volume}]`; volume_boost: `gain` (≤1.5); pan_zoom: `keyframes` XOR `preset`+`preset_params{max_zoom,duration}`; clip_fx: `effect`,`start`,`end`,`intensity`,`freq`,`amount`,`seed`; flip: `direction`; optional `asset_manifest_path`,`scene_id` (`:112-208`) |

### What it actually executes
`execute()` (`tools/video/motion_ops.py:221-292`) checks `ffmpeg` on PATH (`:224`), validates `operation` (`:228`) and that `input_path` exists (`:234`), defaults `output_path` to `{stem}_{op}.mp4` (`:237`), and probes for an audio stream via `_has_audio` (`:731`, runs `ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 <path>`). It then dispatches to one of eight private op-builders (`:243-260`), each of which constructs an ffmpeg argv and runs it through `_run` (`:716-729`, wraps `BaseTool.run_command` with `timeout=900`, catches `CalledProcessError`/`TimeoutExpired` and returns a trimmed stderr string).

For `freeze` (`:336-386`): extracts a still PNG at `at_seconds` (`ffmpeg -y -ss {at} -i {src} -frames:v 1 {still.png}`, `:354`), then concatenates `[0,at)` + the looped still held for `duration` + `(at,end]` via a 3-way `concat` filtergraph, with silence (`anullsrc`) filling the audio hold:
```
ffmpeg -y -i src.mp4 -loop 1 -t 2.0 -i src_freeze_still.png \
  -filter_complex "[0:v]trim=0:5,setpts=PTS-STARTPTS[v1];[1:v]scale=1080:1920,fps=30,setpts=PTS-STARTPTS[vf];[0:v]trim=5,setpts=PTS-STARTPTS[v2];[v1][vf][v2]concat=n=3:v=1:a=0[v];[0:a]atrim=0:5,asetpts=PTS-STARTPTS[a1];anullsrc=channel_layout=stereo:sample_rate=44100,atrim=0:2.0[as];[0:a]atrim=5,asetpts=PTS-STARTPTS[a2];[a1][as][a2]concat=n=3:v=0:a=1[a]" \
  -map "[v]" -map "[a]" out.mp4
```
For `speed` (`:305-334`) it builds `setpts=PTS/{factor}` plus a chained `atempo` (atempo only covers 0.5-2.0 per instance — `_atempo_chain`, `:321-334`, e.g. factor=3 → `atempo=2.0,atempo=1.5`). For `pan_zoom` (`:425-452`) it validates/expands keyframes (or a preset via `_preset_keyframes`, `:454-480`), builds piecewise-linear `if(lt(...))` expressions for zoom/x/y via `_lerp_expr` (`:506-523`), and emits `scale=w='min(iw*4,7680)':h=-2,zoompan=z='<expr>':x='<expr>':y='<expr>':d=1:s=WxH:fps=FPS` (`:448-451`) — the 4x pre-upscale (`_antijitter_upscale`, `:646-650`) exists because zoompan crops on an integer pixel grid. `clip_fx` (`:527-608`) builds one of `crop`+jitter (`shake`), `zoompan` with a raised-cosine `z` expression (`zoom_pulse`), `eq=brightness=...:enable=...` (`strobe`), or seeded `rgbashift`+`noise` bursts (`glitch`, burst schedule computed in Python via `_glitch_bursts`/`random.Random(seed)`, `:610-628`, so it never depends on ffmpeg's own RNG). `flip` (`:632-642`) maps `direction` to `hflip`/`vflip`/`transpose=clock`/`transpose=cclock`. All video-filter ops share `_vf_cmd` (`:652-662`) which forces `-c:v libx264 -pix_fmt yuv420p` (mandatory once `glitch`'s `rgbashift` forces an RGB pipeline) and `-c:a copy`/`-an`.

Back in `execute()`, the output is re-probed via `_probe` (`:744-777`, combines `tools.video._shared.probe_output` for width/height/duration with a second `ffprobe -show_entries stream=r_frame_rate` call for fps) since freeze/speed/rotate change duration or resolution. If `asset_manifest_path` is given, `_register_asset` (`:666-712`) appends a provenance entry and validates against `schemas.artifacts.validate_artifact("asset_manifest", ...)` before an atomic write (`_write_json`, `:779-784`).

### Flow
```text
{"operation":"pan_zoom","input_path":"clip.mp4",
 "preset":"punch_in","preset_params":{"max_zoom":1.4,"duration":1.0}}
                    │
                    ▼
execute() motion_ops.py:221
 ├─ ffmpeg on PATH? (:224)
 ├─ op in OPERATIONS (:228)   src exists (:234)
 ├─ out_path = clip_pan_zoom.mp4 (:237)
 ├─ _has_audio(src) → ffprobe -select_streams a (:731)
 ▼
_pan_zoom() (:425)
 ├─ _probe(src) → w,h,fps,duration (:744, :430)
 ├─ ★ DIVERGENCE: preset given → _preset_keyframes()
 │     "punch_in" (:470) → [{t:0,zoom:1.0},{t:0.25,zoom:1.4}]
 ├─ _validate_keyframes() (:482)
 ├─ _lerp_expr() ×3 for zoom/x/y (:506)
 ├─ vf = antijitter upscale + zoompan (:448-451)
 └─ _vf_cmd() builds argv (:652)
                    │
                    ▼
_run(cmd) (:716) → BaseTool.run_command (subprocess.run,
                     check=True, timeout=900)
                    │
                    ▼
ffmpeg -y -i clip.mp4 -vf "scale=w='min(iw*4,7680)':h=-2,
  zoompan=z='if(lt(it,0.25),(1+1.6*(it-0)/0.25),1.4)':
  x='max(0,min(iw-iw/zoom,0.5*iw-iw/(2*zoom)))':
  y='max(0,min(ih-ih/zoom,0.5*ih-ih/(2*zoom)))':
  d=1:s=1080x1920:fps=30" -c:v libx264 -pix_fmt yuv420p
  -c:a copy clip_pan_zoom.mp4
                    │
                    ▼
back in execute(): _probe(out) re-probe duration/res (:270)
 → optional _register_asset() into asset_manifest (:284)
 → ToolResult(success=True, artifacts=[out_path])
```

**Notes:**
- zoompan's integer-pixel crop grid stair-steps on slow zooms, so `pan_zoom`/`zoom_pulse` always pre-upscale ~4x (capped at 7680px wide) before zooming and scale back to source resolution — a real anti-jitter workaround, not decoration.
- `freeze` can't `-loop` an mp4 directly (`-loop` only accepts an image input), so it always makes a throwaway still PNG first and deletes it afterward; audio is silence-filled (`anullsrc`) for the held span, not looped.

---

## `mask_ops`

**File:** `tools/video/mask_ops.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | CORE (`tools/video/mask_ops.py:68`) |
| capability | `video_post` (`:69`) |
| provider | `ffmpeg` (`:70`) |
| runtime | LOCAL (`:74`) |
| determinism | DETERMINISTIC (`:73`) |
| dependencies | `["cmd:ffmpeg"]` (`:76`) |
| capabilities | `blur_region, dim_outside, image_mask, reveal_wipe` (`:94`) |
| best_for | blur a face/plate/UI region, spotlight a subject, PNG shape masks, one-off masked reveals (`:96-98`) |
| not_good_for | HDR sources (8-bit SDR); feathered/gradient edges with rect/circle (hard edges only); timeline-wide transition sequencing (`reveal_wipe` bakes one pair — use `video_compose` for multi-cut) (`:99-103`) |
| key input params | `operation` (enum, required), `input_path`/`output_path` (`.mov` forced for `image_mask`); `shape` (rect/circle), `region` (NORMALIZED 0..1: rect `{x,y,w,h}` / circle `{cx,cy,r}`); `start`/`end` (optional window, both-or-neither); `strength` (blur_region, 1-64); `dim_factor` (dim_outside, 0≤f<1); `lossless` (bool); `mask_path`/`invert` (image_mask); `second_path`/`direction`/`duration` (reveal_wipe); optional `asset_manifest_path`,`scene_id` (`:106-157`) |

### What it actually executes
`execute()` (`tools/video/mask_ops.py:171-239`) checks `ffmpeg` on PATH, validates `operation`, checks `input_path` exists, and picks `.mov` for `image_mask` vs `.mp4` for the rest (`:187`). It dispatches to one of four op-builders (`:191-201`), then re-probes the output (`:211`, `_probe` at `:521`, same width/height/fps normalization pattern as motion_ops) and optionally registers the derived clip into an `asset_manifest` (`:229-237`, `_register_asset` at `:558-599`).

`_blur_region` (`:243-277`) validates `strength`, builds an optional `:enable='between(t,S,E)'` clause (`_enable_clause`, `:459-468`), parses the normalized region (`_parse_region`, `:401-431` — pure checks, no probe), converts it to even pixel values (`_region_pixels`, `:433-457`), then for a **rect** shape splits the frame, crops+blurs the patch, and overlays it back:
```
ffmpeg -y -i clip.mp4 -filter_complex \
 "[0:v]split=2[base][reg];
  [reg]crop=324:216:108:192,boxblur=luma_radius=15:luma_power=2:chroma_radius=7:chroma_power=2[blur];
  [base][blur]overlay=108:192:enable='between(t,2.0,5.0)'[v]" \
 -map "[v]" -map 0:a? -c:a copy -c:v libx264 -pix_fmt yuv420p out.mp4
```
For **circle** shapes it crops the bounding box, blurs it, then cuts a circular alpha with `format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*lte(hypot(X-{r},Y-{r}),{r})'` before overlaying (`:265-276`). `_dim_outside` (`:279-307`) is the inverse: `lutyuv=y='val*{dim}'` dims the whole (split) frame, then the original region is cropped and overlaid back on top so only the region stays bright. `_image_mask` (`:309-344`) requires a `.mov` output (rejects otherwise, `:313-317`), scales the PNG mask to the source with `scale2ref`, turns its luminance into alpha via `format=gray[,negate]` + `alphamerge`, and encodes lossless RGBA with `qtrle` — deliberately the same encode `object_cutout` uses:
```
ffmpeg -y -i clip.mp4 -loop 1 -t 5.5 -i mask.png \
 -filter_complex "[1:v][0:v]scale2ref=w=iw:h=ih[mask][src];[mask]format=gray[m];[src][m]alphamerge=shortest=1[out]" \
 -map "[out]" -c:v qtrle -map 0:a? -c:a copy out.mov
```
`_reveal_wipe` (`:346-397`) requires same resolution for both clips (`:363-367`) and `duration <= min(dur_a, dur_b)` (`:370-374`), normalizes both legs (`fps`, `format=yuv420p`, `settb=AVTB`, `setsar=1`) then transitions with `xfade`, plus `acrossfade` if both clips have audio:
```
ffmpeg -y -i a.mp4 -i b.mp4 -filter_complex \
 "[0:v]fps=30,format=yuv420p,settb=AVTB,setsar=1[va];[1:v]fps=30,format=yuv420p,settb=AVTB,setsar=1[vb];[va][vb]xfade=transition=wipeleft:duration=1.0:offset=4.0[v];[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[aa];[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[ab];[aa][ab]acrossfade=d=1.0[a]" \
 -map "[v]" -map "[a]" -c:v libx264 -pix_fmt yuv420p out.mp4
```
All single-input filter runs go through `_run_filter` (`:481-487`) → `_run` (`:489-502`), the same `run_command`/`CalledProcessError` pattern as motion_ops.

### Flow
```text
{"operation":"blur_region","input_path":"clip.mp4",
 "shape":"rect","region":{"x":0.1,"y":0.1,"w":0.3,"h":0.2},
 "strength":15,"start":2.0,"end":5.0}
                    │
                    ▼
execute() mask_ops.py:171
 ├─ ffmpeg on PATH?  op valid  src exists (:174-185)
 ├─ ext = .mp4 (op != image_mask) (:187)
 ▼
_blur_region() (:243)
 ├─ strength in (0,64] (:245)
 ├─ _enable_clause() → ":enable='between(t,2.0,5.0)'" (:459)
 ├─ _parse_region() pure normalized checks (:401)
 ├─ _dimensions(src) → _probe() (:504, :521)
 ├─ _region_pixels() normalized → even px (:433)
 ├─ ★ DIVERGENCE shape: rect (:253) vs circle (:265, geq alpha)
 └─ build filter_complex (crop+boxblur+overlay) (:259-264)
                    │
                    ▼
_run_filter() builds argv (:481) → _run() (:489)
   → BaseTool.run_command (subprocess.run, timeout=900)
                    │
                    ▼
ffmpeg -y -i clip.mp4 -filter_complex "[0:v]split=2[base][reg];
  [reg]crop=324:216:108:192,boxblur=luma_radius=15:luma_power=2:
  chroma_radius=7:chroma_power=2[blur];[base][blur]overlay=108:192:
  enable='between(t,2.0,5.0)'[v]" -map "[v]" -map 0:a? -c:a copy
  -c:v libx264 -pix_fmt yuv420p clip_blur_region.mp4
                    │
                    ▼
back in execute(): _probe(out) re-probe (:211)
 → optional _register_asset() into asset_manifest (:231)
 → ToolResult(success=True, artifacts=[out_path])
```

**Notes:**
- Circle regions that don't fit fully inside the frame are **rejected outright**, never silently clamped (`:449-453`) — a deliberate design choice called out in the module docstring.
- `image_mask` forces a `.mov` container encoded `qtrle` to carry alpha, reusing the exact lossless-RGBA format `object_cutout` produces, so every alpha-carrying derived clip in the project shares one compose-path-compatible format.

---

## `silence_cutter`

**File:** `tools/video/silence_cutter.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | CORE (`tools/video/silence_cutter.py:37`) |
| capability | `video_post` (`:38`) |
| provider | `ffmpeg` (`:39`) |
| runtime | not set in class body → defaults to `ToolRuntime.LOCAL` (`tools/base_tool.py:158`) |
| determinism | DETERMINISTIC (`:42`) |
| dependencies | `["cmd:ffmpeg"]` (`:44`) |
| capabilities | `silence_detection, jump_cut, silence_removal, silence_speedup` (`:48-53`) |
| best_for | not set in class body → defaults to `[]` (`tools/base_tool.py:174`) |
| not_good_for | not set → defaults to `[]` (`tools/base_tool.py:175`) |
| key input params | `input_path`/`output_path`; `mode` (enum `remove`/`speed_up`/`mark`, default `remove`); `silence_threshold_db` (default -35); `min_silence_duration` (default 0.5); `padding_seconds` (default 0.08); `silence_speed_factor` (default 6.0, 1.5-100, speed_up only); `codec` (default libx264); `crf` (default 18) (`:55-94`) |

### What it actually executes
`execute()` (`tools/video/silence_cutter.py:111-218`) checks the input exists, then calls `_detect_silence` (`:220-253`), which shells `ffmpeg -i input -af silencedetect=noise=-35dB:d=0.5 -f null -` and regex-parses `silence_start:`/`silence_end:`/`silence_duration:` out of stderr (`:241-243`). If no silence is found it returns the input unchanged as a no-op success (`:126-138`). Otherwise it gets total duration via `_get_duration` (`:255-267`, `ffprobe -show_entries format=duration -of json`) and computes the inverse speech segments with padding via `_compute_speech_segments` (`:269-296`, merges gaps <0.05s, drops fragments <0.01s).

In `mode="mark"` (`:149-175`) nothing is rendered — it just writes `{silences, speech_segments, total_duration, silence_duration, speech_duration}` to a JSON file. In `mode="remove"`, `_render_jump_cut` (`:298-364`) cuts each speech segment to its own temp mp4 with a forced keyframe at the start:
```
ffmpeg -y -i input.mp4 -ss 0.420 -to 3.100 -c:v libx264 -crf 18 \
  -preset fast -c:a aac -b:a 192k -force_key_frames 0.420 seg_0000.mp4
```
then writes a concat list (`file '/abs/path/seg_0000.mp4'` lines) and stitches losslessly:
```
ffmpeg -y -f concat -safe 0 -i concat_list.txt -c copy output_cut.mp4
```
In `mode="speed_up"`, `_render_speed_up` (`:365-464`) interleaves speech segments (speed 1.0) with silence segments (speed `silence_speed_factor`), sorts by start time, and for non-1x segments applies `setpts={1/speed}*PTS` on video plus a chained `atempo` (`_build_atempo_chain`, `:466-478`, atempo range here is 0.5-100.0 per instance) on audio, then concatenates the same way. Both render paths clean up their temp directories in a `finally` block (`:353-363`, `:455-464`).

### Flow
```text
{"input_path":"raw_talk.mp4","mode":"remove",
 "silence_threshold_db":-35,"min_silence_duration":0.5,
 "padding_seconds":0.08}
                    │
                    ▼
execute() silence_cutter.py:111
 ├─ input exists? (:112)
 ├─ _detect_silence() (:124→220)
 │    ffmpeg -af silencedetect=noise=-35dB:d=0.5 -f null -
 │    regex parse silence_start/end/duration (:241-243)
 ▼
 ★ DIVERGENCE: no silences? → return input unchanged (:126-138)
 │
 ├─ _get_duration() ffprobe format=duration (:141→255)
 ├─ _compute_speech_segments() inverse+padding (:144→269)
 ▼
 ★ DIVERGENCE mode: mark (:149) | remove/speed_up (:177)
 │
 ├─ mode=mark → write JSON only, NO ffmpeg render (:161-162)
 │
 └─ mode=remove → _render_jump_cut() (:191→298)
      ├─ per speech segment: ffmpeg -ss/-to cut w/ force_key_frames
      │    (:316-326)
      ├─ write concat_list.txt (:335-339)
      └─ ffmpeg -f concat -c copy (final stitch) (:341-347)
                    │
                    ▼
ToolResult{mode, input_duration, output_duration,
  silence_removed_seconds, time_saved_percent}
```

**Notes:**
- `mark` mode never invokes a render path at all — it is detect-only, useful for manual review before committing to cuts.
- The jump-cut path re-encodes every speech segment individually (per-segment `codec`/`crf`) and only the final stitch is lossless `-c copy` — so segment boundary quality depends on the per-segment encode, not the concat step.

---

## `showcase_card`

**File:** `tools/video/showcase_card.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | CORE (`tools/video/showcase_card.py:29`) |
| capability | `video_post` (`:30`) |
| provider | `ffmpeg` (`:31`) |
| runtime | not set → defaults to `ToolRuntime.LOCAL` (`tools/base_tool.py:158`) |
| determinism | DETERMINISTIC (`:34`) |
| dependencies | `["cmd:ffmpeg", "cmd:ffprobe"]` (`:36`) |
| capabilities | `create_showcase_card` (`:40`) |
| best_for | not set → defaults to `[]` |
| not_good_for | not set → defaults to `[]` (no explicit HDR/limitation callouts, unlike motion_ops/mask_ops) |
| key input params | `input_path`,`output_path`,`title` (required); `subtitle` (default `""`); `output_width`/`output_height` (default 1080x1920); `background_color` (default `0x0A0F1A`); `title_font` (default `segoeuib.ttf`, system font lookup); `title_font_size` (52)/`subtitle_font_size` (28); `title_color` (white); `watermark` (`""`) (`:42-104`) |

### What it actually executes
`execute()` (`tools/video/showcase_card.py:114-227`) reads all params, checks `input_path` exists, then probes the source's width/height via `ffprobe -select_streams v:0 -show_entries stream=width,height -of csv=p=0 input` (`:135-143`). It computes a letterbox: `scale_factor = out_w/src_w`, `scaled_h = int(src_h*scale_factor)` rounded to even, and `pad_y = (out_h - scaled_h)//2` (`:147-151`). It then builds a filter chain (`:154-190`): `scale=out_w:scaled_h`, `pad=out_w:out_h:0:pad_y:color={bg_color}`, a title `drawtext` at `y=60` using the configurable `title_font`/`title_font_size`/`title_color` with a black `borderw=3` outline, an optional subtitle `drawtext` at `y=h-100` (hardcodes `segoeui.ttf`, ignores `title_font`, uses `white@0.85`), and an optional centered watermark `drawtext` at `fontsize=36`/`white@0.3` (also hardcodes `segoeui.ttf`). Final command:
```
ffmpeg -y -i clip.mp4 -vf \
 "scale=1080:1920,pad=1080:1920:0:0:color=0x0A0F1A,\
  drawtext=text='NEW FEATURE':fontfile='segoeuib.ttf':fontsize=52:\
    fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=60,\
  drawtext=text='Ships this week':fontfile='segoeui.ttf':fontsize=28:\
    fontcolor=white@0.85:x=(w-text_w)/2:y=h-100,\
  drawtext=text='OpenNolan':fontfile='segoeui.ttf':fontsize=36:\
    fontcolor=white@0.3:x=(w-text_w)/2:y=(h-text_h)/2" \
 -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p -c:a aac -b:a 192k out.mp4
```
run via `self.run_command(cmd)` (`:205`). It then verifies the output exists (`:209`) and returns `source_resolution`/`output_resolution`/`letterbox_y_offset`.

### Flow
```text
{"input_path":"clip.mp4","output_path":"card.mp4",
 "title":"NEW FEATURE","subtitle":"Ships this week",
 "watermark":"OpenNolan"}
                    │
                    ▼
execute() showcase_card.py:114
 ├─ read params (:115-126)
 ├─ input exists? (:128)   mkdir parent (:131)
 ├─ probe src dims: ffprobe width,height (:135-143)
 ├─ scale_factor/scaled_h/pad_y computed (:147-151)
 ▼
build filters[] (:154)
 ├─ scale + pad (:155-156)
 ├─ title drawtext (:161-168)
 ├─ ★ DIVERGENCE subtitle given → append drawtext (:171-179)
 ├─ ★ DIVERGENCE watermark given → append drawtext (:182-190)
 └─ vf = ",".join(filters) (:192)
                    │
                    ▼
cmd built (:194-202) → self.run_command(cmd) (:205)
                    │
                    ▼
ffmpeg -y -i clip.mp4 -vf "scale=..,pad=..,drawtext=..." \
  -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p \
  -c:a aac -b:a 192k card.mp4
                    │
                    ▼
output exists check (:209) → ToolResult{source_resolution,
  output_resolution, letterbox_y_offset}
```

**Notes:**
- Subtitle and watermark text hardcode the font to `segoeui.ttf` regardless of the `title_font` input — only the title text actually uses the configurable font param.
- Font is passed as a bare filename (`segoeuib.ttf`) relying on system font/fontconfig lookup, not a bundled path — this tool sets no `best_for`/`not_good_for`, so it carries none of motion_ops/mask_ops' explicit HDR caveats even though it shares the same 8-bit `libx264`/`yuv420p` output path.

---

## `keyframe_animate`

**File:** `tools/video/keyframe_animate.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | CORE (`tools/video/keyframe_animate.py:57`) |
| capability | `video_post` (`:58`) |
| provider | `keyframes` (`:59`) |
| runtime | LOCAL (`:63`) |
| determinism | DETERMINISTIC (`:62`) |
| dependencies | `[]` — pure spec emitter, no ffmpeg (`:65`) |
| capabilities | `keyframe_animation, overlay_motion_presets` (`:69`) |
| best_for | animating an overlay (cutout/text/sticker) over time; expanding a high-level motion preset into keyframes (`:71-74`) |
| not_good_for | rendering (only emits the spec); animating the main video track (overlays only); rotation keyframes on the ffmpeg render path — dropped with a warning, use Remotion/HyperFrames (`:75-80`) |
| key input params | `overlay` (object, required — must already have `asset_id`,`start_seconds`,`end_seconds`,`position`); `keyframes` (raw list, XOR `preset`); `preset` (enum: `slide_in_*`,`slide_out_*`,`fade_in`,`fade_out`,`pop`,`pulse`,`ken_burns`); `preset_params` (`duration`,`distance`,`from_scale`,`to_scale`,`easing`); optional `edit_decisions_path` (merge-in), `output_path` (`:86-110`) |

### What it actually executes
`execute()` (`tools/video/keyframe_animate.py:119-168`) validates the `overlay` object has `asset_id`/`start_seconds`/`end_seconds`/`position` (`:120-125`), then requires exactly one of `keyframes` or `preset` (`:127-132`). Raw keyframes go through `_normalize_keyframes` (`:172-195`, validates each `{t,x,y,scale,rotation,opacity,easing}`, sorts by `t`). A `preset` expands via `_expand_preset` (`:197-256`) — e.g. `slide_in_left` with `overlay.start_seconds=2.0`, `position={x:100,y:200}`, `preset_params={duration:0.5,distance:300}` computes `t0=2.0,t1=2.5`, `sx = x - distance = -200`, and returns `[{"t":2.0,"x":-200,"y":200,"opacity":0.0,"easing":"ease-out"},{"t":2.5,"x":100,"y":200,"opacity":1.0}]` (`:216-225`). The animated overlay is validated by embedding it inside a throwaway minimal `edit_decisions` document and calling the real schema validator, `_validate_overlay` (`:146→260-275`, `schemas.artifacts.validate_artifact("edit_decisions", doc)`). If `edit_decisions_path` is given, `_merge_into_edit_decisions` (`:157→277-305`) reads the existing artifact, replaces the overlay with the same `asset_id` (or appends it), re-validates, and writes back atomically (`_write_json`, `:307-312`, tmp-file + `os.replace`). If `output_path` is given it also writes the animated overlay standalone. **No subprocess/ffmpeg call exists anywhere in this file** — it is a pure JSON spec emitter consumed later by `video_compose`'s renderer.

### Flow
```text
{"overlay":{"asset_id":"logo1","start_seconds":2.0,
 "end_seconds":5.0,"position":{"x":100,"y":200}},
 "preset":"slide_in_left",
 "preset_params":{"duration":0.5,"distance":300}}
                    │
                    ▼
execute() keyframe_animate.py:119
 ├─ overlay has asset_id/start/end/position? (:120-125)
 ├─ keyframes XOR preset? (:127-132)
 ▼
 ★ DIVERGENCE: preset given → _expand_preset() (:135→197)
      t0=2.0, t1=2.5, sx = 100-300 = -200
      → [{t:2.0,x:-200,y:200,opacity:0.0,easing:ease-out},
         {t:2.5,x:100,y:200,opacity:1.0}]
 (else: raw keyframes → _normalize_keyframes() :172)
                    │
                    ▼
animated = overlay + keyframes (:142-143)
 ├─ _validate_overlay() (:146→260): embed in throwaway
 │    edit_decisions doc, validate_artifact("edit_decisions")
 ▼
 ★ DIVERGENCE: edit_decisions_path given?
   ├─ yes → _merge_into_edit_decisions() (:157→277):
   │        read JSON, replace/append by asset_id,
   │        re-validate, _write_json() atomic write
   └─ output_path given → _write_json() standalone
                    │
                    ▼
ToolResult{overlay, keyframes, n_keyframes}
   (NO ffmpeg subprocess anywhere in this tool)
```

**Notes:**
- `provider = "keyframes"` is literal — this tool never touches ffmpeg; it only emits the `overlays[].keyframes` spec that `video_compose`'s renderer later interprets.
- Rotation keyframes are happily accepted and emitted here, but the module docstring and `not_good_for` both flag that the FFmpeg render path silently drops rotation (position/scale/opacity render fine) — only Remotion/HyperFrames renders rotation.

---

## `beat_cutter`

**File:** `tools/video/beat_cutter.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | CORE (`tools/video/beat_cutter.py:49`) |
| capability | `video_post` (`:50`) |
| provider | `librosa` (`:51`) — **not** `ffmpeg` |
| runtime | LOCAL (`:55`) |
| determinism | DETERMINISTIC (`:54`) |
| dependencies | `["python:librosa"]` (`:57`, checked via `__import__`) |
| capabilities | `beat_detection, beat_synced_cutting` (`:65`) |
| best_for | music-led montages where every cut lands on the beat; turning an ordered clip list + a track into a rhythm-synced cut plan (`:72-75`) |
| not_good_for | talking-head edits where cuts must follow speech, not music; rendering video — this only plans cuts, `video_compose` renders them (`:76-79`) |
| key input params | `clips` (ordered list of `{source,in_seconds,id}`, required); `audio_path` (or pre-supplied `beat_times`); `beats_per_cut` (default 1); `start_seconds`; `mode` (enum `speech_safe`/`music_led`, default `speech_safe`); `protected_ranges` (`[[start,end],...]`, speech_safe only); `transition{type,duration}`; optional `edit_decisions_path`, `output_path` (`:85-151`) |

### What it actually executes
`execute()` (`tools/video/beat_cutter.py:163-246`) validates `clips` is a non-empty list where each has a `source` (`:164-169`). If `beat_times` isn't pre-supplied it requires `audio_path` and calls `_detect_beats` (`:192→250-265`): `librosa.load(audio_path, mono=True)`, `librosa.beat.beat_track(y=y, sr=sr)`, `librosa.frames_to_time(beat_frames, sr=sr)` — with a workaround for librosa 0.11 + numpy 2.x returning `tempo` as a non-scalar array (`:261-264`). A missing librosa import raises `_LibrosaMissing`, surfaced as a clean install-instructions error (`:193-200`) rather than a stack trace.

`_cut_boundaries` (`:205→269-308`) filters beats `>= start_seconds`, takes every `beats_per_cut`-th one (`:283-284`), and in `speech_safe` mode (the default) drops any boundary that falls inside a `protected_ranges` span with a warning (`:287-294`). It then merges beats closer than `MIN_CUT_SECONDS=0.2s` apart (`_enforce_min_interval`, `:310-318`). If fewer than 2 usable boundaries remain (silent/ambient track), it falls back to even spacing sized by `_audio_duration` (`:402-412`, which reuses `tools.video._shared.probe_output` — the **only** place this tool touches an ffprobe subprocess, and only to size the even-spacing fallback, never to cut/encode). `_build_cuts` (`:214→336-375`) assigns each clip a duration equal to its beat interval, tagging every cut `"reason": "beat-synced"`. Finally it optionally merges the cuts into an existing `edit_decisions` artifact (`_merge_into_edit_decisions`, `:379-398`, validates via `schemas.artifacts.validate_artifact` before writing) or writes a standalone cuts-only JSON. **There is no ffmpeg call anywhere in this file** — confirming `provider="librosa"`: this tool only plans a `cuts[]` timeline; `video_compose` is what actually renders/cuts video.

### Flow
```text
{"clips":[{"source":"a.mp4"},{"source":"b.mp4"},
 {"source":"c.mp4"}],"audio_path":"track.mp3",
 "beats_per_cut":2,"mode":"speech_safe",
 "protected_ranges":[[3.0,5.0]]}
                    │
                    ▼
execute() beat_cutter.py:163
 ├─ clips non-empty, each has source (:164-169)
 ├─ beat_times not given → audio_path exists (:189)
 ▼
_detect_beats() (:192→250)
 ├─ librosa.load(audio_path, mono=True)
 ├─ librosa.beat.beat_track(y, sr) → tempo, beat_frames
 └─ librosa.frames_to_time(beat_frames, sr) → beat_times
                    │
                    ▼
_cut_boundaries() (:205→269)
 ├─ usable = beats >= start_seconds (:282)
 ├─ picked = every 2nd beat (beats_per_cut) (:284)
 ├─ ★ DIVERGENCE mode=speech_safe: drop beats inside
 │    protected_ranges, warn (:287-294)
 ├─ enforce_min_interval (0.2s floor) (:297→310)
 └─ ★ DIVERGENCE <2 boundaries → even-spacing fallback
      via _audio_duration()→probe_output (ffprobe) (:303-308)
                    │
                    ▼
_build_cuts() (:214→336): duration = interval, reason
   = "beat-synced" per clip
                    │
                    ▼
 ★ DIVERGENCE edit_decisions_path given?
   → _merge_into_edit_decisions() validate_artifact + write
   (else) output_path → write cuts-only JSON
                    │
                    ▼
ToolResult{cuts, beat_times, tempo, mode, warnings}
   (NO ffmpeg render/cut call anywhere in this tool)
```

**Notes:**
- `provider="librosa"` is accurate end-to-end: the tool never shells out to ffmpeg to cut anything — the single ffprobe touch (via the shared `probe_output` helper) only sizes the even-spacing fallback's clip durations.
- librosa (heavy numba/llvmlite dependency) is only imported when `beat_times` isn't pre-supplied — passing pre-computed beat timestamps skips the dependency entirely; `speech_safe` is the default mode specifically so a music-beat montage never chops narration mid-word.

---

## `template_apply`

**File:** `tools/video/template_apply.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | CORE (`tools/video/template_apply.py:39`) |
| capability | `video_post` (`:40`) |
| provider | `opennolan` (`:41`) |
| runtime | LOCAL (`:45`) |
| determinism | DETERMINISTIC (`:44`) |
| dependencies | `[]` — pure: loads a template + emits an artifact (`:47`) |
| capabilities | `apply_template, emit_edit_decisions` (`:51`) |
| best_for | turning a reusable reel template + your clips into a ready-to-compose edit (`:53`) |
| not_good_for | rendering — emits `edit_decisions`; `compose` renders it (`:54`) |
| key input params | `template` (name, or `template_path` override); `slot_assets` (object `{slot_id: asset_path}`, required); `music_path`; `subtitle_source`; `output_path`; `project_id` (`:57-72`) |

### What it actually executes
`execute()` (`tools/video/template_apply.py:79-215`) imports `lib.template_loader.{load_template,validate_template}` and either loads a YAML from `template_path` (`yaml.safe_load` + `validate_template`) or by `template` name via `load_template` (`:80-102`). It computes `slot_ids` from the template's `slots` and diffs against the given `slot_assets`, rejecting with the exact `missing`/`extra` slot-id lists if they don't match 1:1 (`:109-121`), then verifies every referenced asset file actually exists (`:122-126`). It builds one `cuts[]` entry per slot — `out_seconds = slot.seconds`, carrying `transform.animation` for image slots and `transition_in`/`transition_duration` passthrough, tagged `"reason": f"template:{name}"` (`:128-146`). It assembles the `edit_decisions` dict with `render_runtime` (template default or `"ffmpeg"`) and optional `renderer_family` (`:148-154`). If the template's `music` config is enabled, it requires `music_path` to exist and sets `audio.music{asset_id,volume,ducking}` — otherwise emits a warning and skips music (`:158-171`); the same enabled/warn pattern applies to `subtitles` (`:173-186`). Before ever writing, it validates the whole document against `schemas.artifacts.validate_artifact("edit_decisions", ...)` (`:193-202`) — a template can never produce a corrupt artifact. Output path resolves to `output_path` → `projects/<project_id>/artifacts/edit_decisions.json` → `./edit_decisions.json` (`_resolve_output_path`, `:217-224`), written atomically via `_write_json` (`:226-231`, tmp file + `os.replace`). **No ffmpeg/subprocess call exists anywhere in this file** — like `keyframe_animate` and (mostly) `beat_cutter`, it is a pure spec-emitter.

### Flow
```text
{"template":"reel-3clip-basic",
 "slot_assets":{"clip_1":"a.mp4","clip_2":"b.mp4",
                "clip_3":"c.mp4"},
 "music_path":"track.mp3","project_id":"proj123"}
                    │
                    ▼
execute() template_apply.py:79
 ├─ load_template("reel-3clip-basic") (:82-98)
 ├─ slots = template["slots"] (:104)
 ├─ ★ DIVERGENCE slot coverage: missing/extra slot_ids
 │    computed and REJECTED if any missing (:109-121)
 ├─ verify each slot asset file exists (:122-126)
 ▼
build cuts[] per slot: duration=seconds,
  transition_in passthrough, reason="template:name" (:129-146)
                    │
                    ▼
assemble edit_decisions{version,cuts,render_runtime} (:148-154)
 ├─ ★ DIVERGENCE music.enabled?
 │    yes+music_path → audio.music{asset_id,volume,ducking}
 │    yes+no music_path → warn, skip (:158-171)
 ├─ ★ DIVERGENCE subtitles.enabled?
 │    yes → subs{style,position,source} (or warn) (:173-186)
 ├─ metadata{template,aspect_ratio} (:188-191)
 ▼
validate_artifact("edit_decisions", doc) (:193-198)
 — reject before ever writing if invalid
                    │
                    ▼
_resolve_output_path() → projects/proj123/artifacts/
   edit_decisions.json (:204→217)
 → _write_json() atomic write (:205→226)
                    │
                    ▼
ToolResult{template, n_cuts, output_path,
  edit_decisions, warnings}
   (NO ffmpeg call anywhere in this tool)
```

**Notes:**
- Slot-count mismatch is a hard reject naming the exact missing/extra `slot_id`s (`:109-121`) — never a silent partial fill.
- The emitted `edit_decisions` is schema-validated immediately before the write (`:193-202`), so a bad template YAML or bad slot mapping can never reach disk as a corrupt artifact.

# Compositing engines

## `green_screen_composite`

**File:** `tools/video/green_screen_composite.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `CORE` (`tools/video/green_screen_composite.py:38`) |
| capability | `video_post` (`:39`) |
| provider | `ffmpeg` (`:40`) |
| runtime | not declared as a class attr in this tool — inherits the `BaseTool` default |
| determinism | `DETERMINISTIC` (`:43`) |
| dependencies | `["cmd:ffmpeg", "python:numpy", "python:PIL"]` (`:45`) |
| capabilities | `["green_screen_composite", "speaker_overlay", "layout_preset", "alpha_composite"]` (`:52-57`) |
| best_for | not declared on this class |
| not_good_for | not declared on this class |
| key input params | `speaker_path`, `background_path`, `output_path` (required); `original_audio_path`; `layout` enum `news_anchor\|full_behind\|pip\|split` (default `news_anchor`); `speaker_scale` (default 0.65); `bg_shift_up` (default 300px); `bg_color_hex` (default `#0E172A`) — `:59-106` |

### What it actually executes
`execute()` (`:124-247`) validates the three paths (`:134-139`), parses `bg_color_hex` to an RGB `np.ndarray` via `_parse_hex_color` (`:145,249-255`), then probes both inputs with `_probe_video` (`:148-149,257-296`), which runs `ffprobe -v quiet -print_format json -show_format -show_streams <path>` (`:259-264`) and parses `r_frame_rate`/duration/dimensions from the JSON. It picks `target_fps = min(speaker_fps, bg_fps)` (`:158`), takes output width/height from the background clip (`:163-164`), and `duration = min(speaker_dur, bg_dur)` (`:167`).

Frames are dumped to a temp dir tree (`speaker/`, `bg/`, `composite/`, `:170-176`) via `_extract_frames` (`:179-180,298-306`), which runs `ffmpeg -y -i <video> -vf fps=<fps> frame_%06d.png` for each input. For every frame pair (`:196-211`), `_composite_frame` (`:308-386`) does the actual keying in PIL/numpy: it computes a per-pixel Euclidean distance from each speaker pixel to `bg_color`, thresholds at 35 and scales by 8 to build an alpha channel (`np.clip((dist-35)*8, 0, 255)`, `:322-325`), pastes that as the speaker layer's alpha, then composites onto a canvas per `layout` preset using PIL `resize`/`paste` (news_anchor shifts the bg up by `bg_shift_up` px and scales the speaker to `speaker_scale`, bottom-centered, `:334-348`; full_behind stretches the speaker full-frame over the bg, `:350-357`; pip scales the speaker to 30% in the bottom-right with a 20px margin, `:359-371`; split renders speaker on the left half / bg on the right half, `:373-383`).

Composited PNGs are re-encoded via `_encode_frames` (`:217,388-401`): `ffmpeg -y -framerate <fps> -i frame_%06d.png -c:v libx264 -crf 18 -preset fast -pix_fmt yuv420p -vf scale=<w>:<h> <output>`. If `original_audio_path` was given, `_mux_audio` (`:220-221,403-418`) runs `ffmpeg -y -i <video> -i <audio_source> -t <duration> -c:v copy -c:a aac -b:a 192k -map 0:v:0 -map 1:a:0 -shortest <output>`. Every ffmpeg/ffprobe call runs as a subprocess via the inherited `run_command`. Temp dirs are removed in a `finally` block (`_cleanup_temp`, `:246-247,420-427`).

### Flow
```text
execute() green_screen_composite.py:124
 payload: {speaker_path, background_path, output_path,
           original_audio_path, layout="news_anchor"}
        │
        ▼
 validate paths exist                                :134-139
        │
        ▼
 _parse_hex_color(bg_color_hex)                       :145,249
        │
        ▼
 _probe_video(speaker) / _probe_video(bg)              :148-149,257
   ffprobe -show_format -show_streams <path>           :260-264
        │
        ▼
 target_fps = min(speaker_fps, bg_fps)                 :158
 out_w,out_h = bg dims   duration = min(durations)      :163-167
        │
        ▼
 _extract_frames(speaker,...) _extract_frames(bg,...)  :179-180,298
   ffmpeg -i <video> -vf fps=<fps> frame_%06d.png
        │
        ▼
 for i in frame_count: _composite_frame(...)           :196-211,308
   alpha = clip((|px-bg_color| - 35) * 8, 0, 255)       :322-325 (numpy)
        │
        ★ DIVERGENCE on `layout`                        :334-384
   ┌───────────┬────────────┬───────────┬───────────┐
   │news_anchor│full_behind │   pip     │   split   │
   │bg shifted │speaker     │speaker 30%│speaker L50│
   │up, speaker│fills frame,│bottom-    │bg R50     │
   │bottom-ctr │no shift    │right,20px │           │
   │scale 0.65 │            │margin     │           │
   └───────────┴────────────┴───────────┴───────────┘
        │  PIL canvas.paste(speaker_rgba, (x,y), speaker_rgba)
        ▼
 save frame_%06d.png to composite/                     :211
        │
        ▼
 _encode_frames(comp_frames_dir, ...)                   :217,388
   ffmpeg -framerate <fps> -i frame_%06d.png
     -c:v libx264 -crf 18 -preset fast -pix_fmt yuv420p
     -vf scale=<w>:<h> <no_audio_path|output_path>
        │
        ▼ (if original_audio_path given)
 _mux_audio(...)                                        :220-221,403
   ffmpeg -i <video> -i <audio_source> -t <duration>
     -c:v copy -c:a aac -b:a 192k
     -map 0:v:0 -map 1:a:0 -shortest <output>
        │
        ▼
 _cleanup_temp(temp_dir)                                :246,420
        │
        ▼
 ToolResult(success=True, artifacts=[output_path])       :228-242
```

**Notes:**
- No `runtime` class attr is set here (unlike `hyperframes_compose`, which explicitly declares `ToolRuntime.LOCAL`) — this tool relies on the `BaseTool` default.
- The "alpha keying" is a flat Euclidean-distance threshold against a single `bg_color`, not real chromakey — it assumes the speaker footage was already keyed onto a solid `bg_color_hex` (typically by `green_screen_processor`), and works frame-by-frame via PNG dumps to a temp dir rather than a single ffmpeg `filter_complex`.

---

## `green_screen_processor`

**File:** `tools/video/green_screen_processor.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `CORE` (`:39`) |
| capability | `video_post` (`:40`) |
| provider | `ffmpeg` (`:41`) |
| runtime | not declared as a class attr — inherits `BaseTool` default |
| determinism | `DETERMINISTIC` (`:44`) |
| dependencies | `["cmd:ffmpeg"]` (`:46`) — note `rembg` is NOT listed here; it's imported lazily inside `_process_rembg` |
| capabilities | `["green_screen_keying", "chromakey", "background_removal", "rembg_segmentation"]` (`:53-58`) |
| best_for | not declared on this class |
| not_good_for | not declared on this class |
| key input params | `input_path`, `output_path` (required); `method` enum `auto\|chromakey\|rembg` (default `auto`); `fps` (default 15); `bg_color` (default `#0E172A`); `max_frames` (default 0 = all) — `:60-94` |

### What it actually executes
`execute()` (`:114-206`) probes the input with `_probe_video` (`:129,208-242`) — `ffprobe -v quiet -show_entries format=duration:stream=width,height,r_frame_rate -select_streams v:0 -of json <input>` (`:210-216`). If `method == "auto"`, `_auto_detect_method` (`:139-140,244-296`) extracts 5 evenly-spaced sample frames (`ffmpeg -ss <ts> -i <input> -frames:v 1 sample.png`, `:262-268`), then `_detect_green_screen_histogram` (`:280,298-364`) runs an ffmpeg `colorkey=color=0x00FF00:similarity=0.4:blend=0.0,alphaextract,blackframe=amount=0:threshold=128` pipeline to `-f null /dev/null` and parses the `pblack:NN` percentage from stderr (`:334-356`) — a majority "green vote" across the 5 samples decides `has_green_screen`. If no green screen is found, it picks `rembg` outright (`:282-284`); otherwise it runs `_test_chromakey_quality` on the middle sample (`:288,366-408`): `ffmpeg -i frame -vf chromakey=color=0x00FF00:similarity=0.3:blend=0.08 out.png`, then measures the transparent-pixel percentage via `alphaextract,blackframe`. Quality > 80 → `chromakey`, else → `rembg` (`:290-293`).

Frames are then extracted at the target `fps` via `_extract_frames` (`:150,410-440`: `ffmpeg -y -i <input> -vf fps=<fps> frame_%06d.png`, with `-frames:v <max_frames>` inserted if capped). Processing branches on the resolved method:
- **chromakey** — `_process_chromakey` (`:162-163,442-512`) per-frame builds a `filter_complex`: `ffmpeg -y -f lavfi -i color=c=0x<hex>:size=1x1 -i <frame> -filter_complex "[0:v]scale=iw:ih[bg];[1:v]chromakey=color=0x00FF00:similarity=0.3:blend=0.08[fg];[bg][fg]overlay=0:0" -frames:v 1 <out>`. On failure it retries with a bare `chromakey` filter alone (no bg compositing, `:494-501`).
- **rembg** — `_process_rembg` (`:166-167,514-572`) lazily imports `rembg`/PIL/numpy (`:526-530`), builds a `u2net_human_seg` session (`:538`), and per-frame calls `rembg.remove(np.array(img), session=session)` to get an RGBA cutout, then PIL-composites it onto a solid `bg_color` canvas (`:556-561`).

Frames are reassembled via `_reconstruct_video` (`:178,574-594`): `ffmpeg -y -framerate <fps> -i frame_%06d.png -vf scale=<w>:<h>:flags=lanczos -c:v libx264 -crf 18 -preset fast -pix_fmt yuv420p <output>`. All ffmpeg calls run as subprocesses via `run_command`. Temp dirs are cleaned up in `finally` (`_cleanup_dir`, `:204-206,596-614`).

### Flow
```text
execute() green_screen_processor.py:114
 payload: {input_path, output_path, method="auto", fps=15,
           bg_color="#0E172A"}
        │
        ▼
 _probe_video(input_path)                              :129,208
   ffprobe -show_entries format=duration:
     stream=width,height,r_frame_rate <path>
        │
        ★ DIVERGENCE on `method`                         :139-140
   method=chromakey/rembg → skip detection, use as-is
   method=auto → _auto_detect_method()                  :244
        ▼
   extract 5 sample frames
     ffmpeg -ss <ts> -i <input> -frames:v 1 sample.png   :262-268
        ▼
   _detect_green_screen_histogram(samples)               :280,298
     ffmpeg -i sample -vf colorkey=0x00FF00,alphaextract,
       blackframe -f null /dev/null
     parse "pblack:NN" from stderr; vote green if >=20    :350-356
        │
        ★ no green screen detected → method="rembg"      :282-284
        ▼ green screen detected
   _test_chromakey_quality(mid_sample)                   :288,366
     ffmpeg -i frame -vf chromakey=0x00FF00... out.png
     alphaextract,blackframe → pblack % transparent        :390-404
        │
        ★ quality>80 → "chromakey"  else → "rembg"        :290-293
        ▼
 _extract_frames(input, frames_dir, fps)                 :150,410
   ffmpeg -i input -vf fps=<fps> frame_%06d.png
        │
        ★ DIVERGENCE on resolved `method`                 :162-169
   ┌───────────────────────┬────────────────────────────┐
   │ chromakey              │ rembg                      │
   │ _process_chromakey()   │ _process_rembg()           │
   │  :442                  │  :514                      │
   │ per-frame filter_      │ session = rembg.new_session │
   │ complex: lavfi color   │  ("u2net_human_seg") :538   │
   │ bg + chromakey fg +    │ rembg.remove(np.array(img), │
   │ overlay  :462-474      │  session) → RGBA cutout     │
   │ fallback: bare          │ PIL paste cutout onto       │
   │ chromakey  :494-501    │  solid bg_color  :556-561   │
   └───────────────────────┴────────────────────────────┘
        │
        ▼
 _reconstruct_video(processed_dir, output, fps, w, h)    :178,574
   ffmpeg -framerate <fps> -i frame_%06d.png
     -vf scale=<w>:<h>:flags=lanczos -c:v libx264
     -crf 18 -preset fast -pix_fmt yuv420p <output>
        │
        ▼
 _cleanup_dir(temp_dir)                                  :205,596
        │
        ▼
 ToolResult(success=True, data={method_used,...})        :187-200
```

**Notes:**
- `rembg` is imported lazily inside `_process_rembg` (`:526-530`) with a bare `try/except ImportError: return False` — it is not listed in `dependencies` (only `cmd:ffmpeg` is), so a missing `rembg` install silently fails processing rather than being surfaced as an unmet dependency.
- The auto-detect step is a heuristic hack, not real CV: green-screen presence is voted via a `colorkey`+`blackframe` pixel-count trick, and chromakey "quality" is just the transparent-pixel percentage from a single test frame.

---

## `hyperframes_compose`

**File:** `tools/video/hyperframes_compose.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `CORE` (`:53`) |
| capability | `video_post` (`:54`) |
| provider | `hyperframes` (`:55`) |
| runtime | `ToolRuntime.LOCAL` (`:59`) |
| determinism | `DETERMINISTIC` (`:58`) |
| dependencies | `["cmd:npx", "cmd:ffmpeg"]` (`:61`) |
| capabilities | `["hyperframes_render", "hyperframes_lint", "hyperframes_validate", "hyperframes_doctor", "scaffold_workspace", "add_block"]` (`:80-87`) |
| best_for | HTML/CSS/GSAP composition (kinetic typography, product promos, launch reels); motion-graphics-heavy briefs; website-to-video; registry-block-driven scenes (`:89-94`) |
| not_good_for | word-level caption burn; avatar/lip-sync presenter; existing React scene stack — those stay on Remotion in Phase 1 (`:95-99`); `fallback_tools = ["video_compose"]` (`:100`) |
| key input params | `operation` enum `render\|lint\|validate\|doctor\|scaffold_workspace\|add_block` (required); `workspace_path`; `output_path`; `edit_decisions`; `asset_manifest`; `playbook`; `profile`; `quality` enum `draft\|standard\|high`; `fps` enum `24\|30\|60`; `strict`; `skip_contrast` — `:102-196` |

### What it actually executes
`execute()` (`:394-417`) dispatches on `operation` to `_doctor`/`_scaffold`/`_lint`/`_validate`/`_render`/`_add_block`. The main path, `_render` (`:639-748`), first calls `_runtime_check` (`:641,313-352`), which checks `node --version` via `_node_major_version` (`:225-240`, requires major ≥ 22, `_NODE_FLOOR_MAJOR`), `ffmpeg`/`npx` on PATH, and resolves the `hyperframes` npm package via `_resolve_npm_package` (`:242-311`) — it prefers a locally-provisioned install detected through `lib.provision.hyperframes_ok()` (reads `node_modules/hyperframes/package.json` offline, `:262-275`), falling back to a live `npm view hyperframes version` with a 5s timeout (`:277-310`). If unavailable, it fails hard with no silent fallback (`:642-652`, explicit governance note in the error string).

`_scaffold` (`:661,458-547`) materializes the HyperFrames workspace: resolves output dims via `lib.media_profiles.get_profile` (`:477,762-772`), resolves asset IDs and copies files into `workspace/assets/` with `shutil.copy2` via `_resolve_and_stage_assets` (`:485,780-810`), stages narration/music via `_resolve_audio_refs` (`:491,812-862`), builds CSS custom properties + `DESIGN.md` via `_style_bridge` (`:498,872-928`, delegating to `lib.hyperframes_style_bridge.style_bridge` with a built-in fallback palette), writes `hyperframes.json` (registry config, `:501-514`) and `DESIGN.md` (`:517-518`), then generates `index.html` via `_generate_index_html` (`:522-532,934-1029`). Each cut is rendered by `_cut_to_html` (`:1031-1108`): text cards get an `<h1>` plus a GSAP fade/lift entrance tween, images get an `<img>` plus a GSAP scale-in tween, videos get a muted `<video playsinline>`, `.html` sources become a `data-composition-src` block, and unknown shapes fall back to a placeholder text card. The HTML pulls GSAP from a CDN: `<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js">` (`:1014`).

`_lint` (`:671,549-569`) and `_validate` (`:683,571-594`) each shell out via `_run_hf(["lint","--json"])` / `_run_hf(["validate","--json", ...])`. The render step (`:700-723`) builds `["render", "--output", <output_path>, "--fps", <fps>, "--quality", <quality>]` and runs it through `_run_hf` (`:712,1114-1163`), which prefers the locally-provisioned CLI at `lib.provision.hyperframes_root()/node_modules/.bin/hyperframes` (`:1128-1138`, offline/deterministic), else falls back to `npx --yes hyperframes <args>` (`:1131`, live npm fetch), resolving the `.cmd` wrapper on Windows (`:1142-1145`), and executes it with `subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=workspace, check=False)` (`:1147-1154`). This Node subprocess is where the HTML/CSS/GSAP scene actually gets captured and where ffmpeg (a listed dependency, `:61`) is invoked internally for muxing/encoding to MP4 — this Python file itself never calls ffmpeg directly. Finally, `_render` checks `output_path.exists()` (`:725-733`) and returns `ToolResult` with a `steps` dict recording each stage's data (`:735-748`).

### Flow
```text
execute() hyperframes_compose.py:394
 payload: {operation="render", workspace_path, output_path,
           edit_decisions, asset_manifest, playbook,
           profile="tiktok_vertical", quality="standard"}
        │
        ▼
 _render(inputs)                                        :639
 _runtime_check()                                        :641,313
   _node_major_version() → `node --version`               :226-240
   _resolve_npm_package()                                  :243
        ★ DIVERGENCE: provisioned vs dev resolve            :262-311
   ┌────────────────────────┬──────────────────────────┐
   │ lib.provision.          │ npm view hyperframes       │
   │ hyperframes_ok()=True   │  version (5s timeout,       │
   │ read local package.json│  live registry check)       │
   │  :266-272               │  :283-310                  │
   └────────────────────────┴──────────────────────────┘
        │ (any unmet floor → return error, NEVER falls back):642-652
        ▼
 _scaffold(inputs)                                       :661,458
   resolve dims via lib.media_profiles.get_profile         :766-769
   _resolve_and_stage_assets: shutil.copy2 into              :485,780
     workspace/assets/
   _resolve_audio_refs: stage narration+music                :491,812
   _style_bridge → lib.hyperframes_style_bridge              :498,872
     .style_bridge(playbook, edit_decisions)
   write hyperframes.json + DESIGN.md                        :501-518
   _generate_index_html(cuts, audio_refs, ...)                :522,934
     per cut → _cut_to_html()                                 :1031
       text_card → <h1> + gsap fade/lift tween
       image → <img> + gsap scale-in tween
       video → <video muted playsinline>
     <script src=".../gsap@3.14.2/dist/gsap.min.js">          :1014
   write workspace/index.html                                 :532
        │
        ▼
 _lint({workspace_path})                                  :671,549
   _run_hf(["lint","--json"])                               :556,1114
        │ (strict & fail → abort; else warn & continue)      :673-680
        ▼
 _validate({workspace_path, skip_contrast})                :683,571
   _run_hf(["validate","--json"])                           :581
        │ (fail → abort, render blocked)                     :690-698
        ▼
 build render args: ["render","--output",out_path,
   "--fps",fps,"--quality",quality]                          :703-711
 _run_hf(args, cwd=workspace, timeout=1800)                :712,1114
        ★ DIVERGENCE: local CLI vs live npx                  :1128-1145
   ┌─────────────────────────┬────────────────────────┐
   │ node_modules/.bin/       │ npx --yes hyperframes    │
   │ hyperframes (local,      │ <args> (fetches from     │
   │ offline, deterministic)  │ npm registry)             │
   │  :1135-1138              │  :1131                   │
   └─────────────────────────┴────────────────────────┘
   subprocess.run(cmd, cwd=workspace, capture_output)       :1147-1154
   (Node process renders HTML/CSS/GSAP via the hyperframes
    CLI and calls ffmpeg internally to mux/encode the MP4 —
    happens INSIDE that subprocess, not this Python code)
        │
        ▼
 verify output_path exists                                 :725-733
        ▼
 ToolResult(success=True, artifacts=[output_path])          :735-748
```

**Notes:**
- This tool never invokes `ffmpeg` directly itself — `ffmpeg` is a listed dependency (`:61`) purely because the spawned `hyperframes` Node CLI uses it internally for muxing/encoding.
- `_render` hard-fails (no silent runtime substitution to Remotion/`video_compose`) if node < 22, ffmpeg, npx, or the `hyperframes` npm package don't resolve — the governance rule is embedded directly in the error text (`:646-652`).

---

## `fuse_transition`

**File:** `tools/video/fuse_transition.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `GENERATE` (`:65`) |
| capability | `video_post` (`:66`) |
| provider | `seedance` (`:67`) |
| runtime | `ToolRuntime.API` (`:71`) |
| determinism | `STOCHASTIC` (`:70`) |
| dependencies | `["cmd:ffmpeg"]` (`:86`) — `FAL_KEY`/`FAL_AI_API_KEY` env is checked manually in `get_status` (`:171`), not declared as an `env:` dependency |
| capabilities | `["fuse_transition", "generative_morph", "transition"]` (`:94`); `supports`: `generative_morph`, `first_last_frame_conditioning`, `seed`, `min_billable_seconds=4` (`:95-100`) |
| best_for | an AI "fuse" morph a crossfade can't sell (Edits parity); seamless scene hand-offs where A transforms into B (`:101-104`) |
| not_good_for | free/offline transitions (this is PAID, min 4 billable seconds — use `video_compose` for crossfades); mismatched-resolution clips; HDR sources (output is 8-bit SDR) (`:105-109`); `fallback_tools = []` (`:110`) |
| key input params | `clip_a`, `clip_b` (required); `prompt` (default `"smooth seamless morph transition"`); `morph_duration` (0.2–15s, default 1.0); `model_variant` enum `fast\|standard`; `seed`; `output_path`; `confirm` (default false); `keep_intermediates`; `asset_manifest_path`; `scene_id` — `:112-151` |

### What it actually executes
`execute()` (`:192-370`) checks `ffmpeg`/`ffprobe` on PATH (`:193`), then `_validate` (`:197,374-409`) checks both clips exist and are supported formats, prompt is non-empty, `morph_duration` is in range, `model_variant` is valid, and `seed` is an int if given. `_get_generator` (`:202,418-422`) looks up the `seedance_video` tool via `tools.tool_registry.registry` — the fal.ai path is required specifically because only it exposes `end_image_url` (first/last-frame conditioning). If the generator is missing or its `get_status()` isn't `AVAILABLE`, the tool fails fast rather than falling back to a crossfade (`:204-220`). Both clips are probed via `_probe` (`:222-223,566-594`, using `tools.video._shared.probe_output` plus an `ffprobe … r_frame_rate` query); mismatched resolutions abort rather than guessing which wins (`:229-237`).

`gen_secs = _gen_seconds(morph_duration)` clamps `ceil(morph_duration)` into Seedance's `[4, 15]` enum range (`:175-176,239`). If not confirmed (`confirm=true` or `FUSE_TRANSITION_AUTOCONFIRM=1`), it returns a dry-run cost estimate (`estimate_cost` uses `_RATES["fast"]=0.2419`/`"standard"=0.3034` per second, `:178-185`) and spends nothing (`:240-256`).

On confirmed execution: `_extract_last_frame` (`:268,440-450`) seeks to `duration-0.5s` and decodes with `-update 1` so the final overwrite is the true last frame (`ffmpeg -y -ss <t> -i clip_a -map 0:v:0 -an -update 1 a_last.png`, falling back to a full decode if that produces nothing); `_extract_first_frame` (`:271,436-438`) runs `ffmpeg -y -i clip_b -map 0:v:0 -an -frames:v 1 b_first.png`. Both PNGs are uploaded via `_upload_frame` → `tools.video._shared.upload_image_fal` (`:278-279,424-427`). A payload is built with `operation="image_to_video"`, `image_url`, `end_image_url`, `duration=str(gen_secs)`, `model_variant`, `resolution` (480p/720p by `min(w,h)`), `aspect_ratio` via `_aspect_for` (nearest standard ratio by log-distance, `:429-432`), `generate_audio=False`, `output_path=morph_raw.mp4` (`:282-296`), and `gen.execute(payload)` fires the actual Seedance 2.0 API call through fal.ai (`:296`).

The three segments are then conformed to clip A's resolution/fps as an intermediate mezzanine via `_conform` (`:452-483`): each builds `ffmpeg -i <src> -map ... -vf scale=W:H:flags=lanczos,setsar=1[,setpts=PTS*ratio],fps=F -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p [-c:a aac -ar 44100 -ac 2 | -an]`. The morph segment additionally gets `setpts=PTS*(morph_duration/gen_dur)` to retime (not trim) it down to the target `morph_duration`, plus `-t <morph_duration>` (`:317-319`); silence is injected via `-f lavfi -i anullsrc=...` when a source segment lacks audio (`:466-472`). The three conformed segments are spliced by `_concat` (`:328,485-494`), which writes a concat-demuxer list file and runs `ffmpeg -f concat -safe 0 -i concat.txt -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p [-c:a aac] out_path`. The work dir is removed unless `keep_intermediates` is set (`:333-335`), and the new asset can optionally be registered into an `asset_manifest.json` via `_register_asset` (`:356,498-537`), validated against `schemas.artifacts.validate_artifact`.

### Flow
```text
execute() fuse_transition.py:192
 payload: {clip_a, clip_b, prompt, morph_duration=1.0,
           model_variant="fast", confirm=true}
        │
        ▼
 check ffmpeg/ffprobe on PATH                            :193
 _validate(inputs) → paths, prompt, morph, variant, seed  :197,374
        │
        ▼
 _get_generator("seedance_video") via tool_registry       :202,418
        ★ DIVERGENCE: generator unavailable → FAIL FAST     :204-220
        │   (never substitutes a free crossfade)
        ▼ generator AVAILABLE
 _probe(clip_a) / _probe(clip_b)                          :222-223,566
   ffprobe stream=width,height + r_frame_rate
        ★ resolution mismatch → fail, refuse to guess       :229-237
        ▼ match
 gen_secs = clamp(ceil(morph_duration), 4, 15)             :175,239
        ★ DIVERGENCE: confirm=false → dry-run estimate       :240-256
        │   (est via _RATES["fast"]=0.2419/s, nothing spent,
        │    returns requires_confirmation)
        ▼ confirm=true (or FUSE_TRANSITION_AUTOCONFIRM=1)
 _extract_last_frame(clip_a)                               :268,440
   ffmpeg -ss <dur-0.5> -i clip_a -map 0:v:0 -an
     -update 1 a_last.png  (fallback: full decode)
 _extract_first_frame(clip_b)                               :271,436
   ffmpeg -i clip_b -map 0:v:0 -an -frames:v 1 b_first.png
        │
        ▼
 _upload_frame(a_last) / _upload_frame(b_first)             :278-279,424
   tools.video._shared.upload_image_fal(...) → image_url
        │
        ▼
 gen.execute({operation="image_to_video",
   image_url, end_image_url, duration=str(gen_secs),
   model_variant="fast", aspect_ratio=_aspect_for(w,h),
   generate_audio=False, output_path=morph_raw.mp4})        :282-296
   → Seedance 2.0 API call via fal.ai (seedance_video tool)
        │
        ▼
 _probe(morph_raw) → gen_dur                                :302-305
        │
        ▼
 _conform(clip_a → seg_a)                                   :313,452
   ffmpeg -i clip_a -map 0:v:0 -map 0:a:0
     -vf scale=W:H:flags=lanczos,setsar=1,fps=F
     -c:v libx264 -crf 18 -c:a aac seg_a.mp4
 _conform(morph_raw → seg_m, retime, trim=morph_duration)   :317
   adds setpts=PTS*(morph_duration/gen_dur), -t morph_dur,
   drop_src_audio → silence via anullsrc
 _conform(clip_b → seg_b)                                    :322
        │
        ▼
 _concat([seg_a,seg_m,seg_b] → out_path)                    :328,485
   write concat.txt; ffmpeg -f concat -safe 0
     -i concat.txt -c:v libx264 -crf 18 -c:a aac out.mp4
        │
        ▼
 cleanup workdir (unless keep_intermediates)                :333-335
        ▼
 optional _register_asset() → asset_manifest.json            :356,498
        ▼
 ToolResult(success=True, cost_usd=gen_result.cost_usd,
   artifacts=[out_path])                                     :363-370
```

**Notes:**
- Confirm-gated PAID generation: unconfirmed calls return a cost estimate and spend nothing (`:240-256`); `FUSE_TRANSITION_AUTOCONFIRM=1` bypasses `confirm=true`.
- Fails fast rather than degrading to a free ffmpeg crossfade when the `seedance_video` generator is unavailable — the only tool exposing `end_image_url` conditioning (`:204-220`).
- The morph is generated at Seedance's 4s billable floor then time-retimed (`setpts`, not trimmed) down to `morph_duration` so both frame endpoints survive for a seamless concat splice (`:317-319`).

---

# Captions & enhancement

## `subtitle_gen`

**File:** `tools/subtitle/subtitle_gen.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | CORE |
| capability | subtitle |
| provider | opennolan |
| runtime | LOCAL (default, not overridden — pure Python) |
| determinism | DETERMINISTIC |
| dependencies | `[]` (`subtitle_gen.py:53` — pure stdlib) |
| capabilities | `generate_srt`, `generate_vtt`, `generate_caption_json`, `censor_transcript` (`:60-62`) |
| best_for | word-timed SRT/VTT/JSON from transcriber segments; ASR-misrecognition `corrections`; transcript censoring emitting `mute_ranges` (`:63-69`) |
| not_good_for | bleep/mute audio rendering (only emits `mute_ranges`); burning subtitles into pixels — use `video_compose` (`:70-74`) |
| key input params | `segments` (required), `format` (srt/vtt/json), `max_chars_per_line`, `max_words_per_cue`, `highlight_style` (none/word_by_word/karaoke), `corrections`, `censor_words` (`:76-118`) |

### What it actually executes
`execute()` (`:127`) validates `segments` is a list (`:129-130`), then applies `corrections` (`:148-149`, `_apply_corrections` `:195-230`, case-insensitive per-token replacement preserving trailing punctuation) **before** censoring. If `censor_words` is set, `_apply_censor` (`:154, 250-314`) masks each blocklisted word to `first-char + asterisks` via `_mask_word` (`:245-248`, e.g. `damn -> d***`) and, for words with timestamps, builds `{start, end, word}` ranges padded ±`MUTE_PAD_SECONDS` (0.05s, `:57`) then merges overlaps via `_merge_ranges` (`:316-330`). `_build_cues` (`:157, 332-391`) groups word timestamps into display cues respecting `max_words_per_cue`/`max_chars_per_line`. Rendering branches on `format`: `_render_srt`/`_render_vtt` (`:393-436, 438-473`) emit one cue per word for `highlight_style=word_by_word`, or bold the active word with `<b>` tags for `karaoke`; `json` just dumps the cue list (`:165-167`). The file is written to `output_path` (`:171-175`). There is **no ffmpeg/subprocess call anywhere** in this tool — burn-in is explicitly out of scope (module docstring `:1-23`).

Example: `segments=[...]`, `format="srt"`, `highlight_style="karaoke"`, `censor_words=["damn"]` → corrections applied → "damn" masked to "d\*\*\*" in cue text, a padded/merged `mute_ranges` entry emitted → cues built → SRT rendered with `<b>` around the active word → written to `subtitles.srt`.

### Flow
```text
execute() subtitle_gen.py:127
{segments, format:"srt", highlight_style:"karaoke",
 censor_words:["damn"]}
        │
        ▼
   validate segments is list                        (:129)
        │
        ▼
   corrections given? ── no ──► skip
        │ yes
        ▼
   _apply_corrections()            (:149, def :195)
        │
        ▼
   censor_words given? ── no ──► mute_ranges = []
        │ yes
        ▼
   _apply_censor()                 (:154, def :250)
     "damn" → "d***"; word timestamps → mute_ranges
     padded ±0.05s, merged via _merge_ranges (:316)
        │
        ▼
   _build_cues()                   (:157, def :332)
     group words by max_words_per_cue / max_chars_per_line
        │
        ▼
   ★ DIVERGENCE: format
 ┌─────────────┬──────────────┬──────────────────┐
 │ srt (:159)  │ vtt (:162)   │ json (:165)        │
 │_render_srt  │_render_vtt   │json.dumps(cues)   │
 │  (:393)     │  (:438)      │                   │
 │karaoke uses │karaoke uses  │                   │
 │<b>word</b>  │<b>word</b>   │                   │
 └─────────────┴──────────────┴──────────────────┘
        │
        ▼
   write output_path               (:174-175)
        │
        ▼
   ToolResult(data={cue_count, output, mute_ranges,
                    censor_summary}, artifacts=[out])
```

**Notes:**
- Pure stdlib — no ffmpeg subprocess anywhere; the docstring is explicit that burn-in and bleep/mute audio are the *agent's* job via `video_compose`/`motion_ops segment_volume`/`audio_mixer`, not this tool's.
- Censoring only text-masks + emits ranges; segments without word-level timestamps get their text masked but no mute range can be derived (tracked in `censor_summary.unmuted_text_matches`).

---

## `remotion_caption_burn`

**File:** `tools/video/remotion_caption_burn.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | CORE |
| capability | subtitle |
| provider | remotion |
| runtime | LOCAL (default, not overridden) |
| determinism | DETERMINISTIC |
| dependencies | `["cmd:ffmpeg", "cmd:ffprobe"]` (`:92`) |
| capabilities | `burn_remotion_captions`, `burn_ffmpeg_captions_fallback`, `caption_style_presets`, `word_emphasis` (`:99-104`) |
| best_for | not set on this tool (no `best_for` attribute defined) |
| not_good_for | styled captions without Remotion — FFmpeg fallback ignores `style_preset`/colors/emphasis; HyperFrames workspaces (TalkingHead comp is Remotion-only) (`:106-113`) |
| key input params | `input_path`, `output_path` (required); `segments` or `srt_path`; `style_preset` (5 presets); `base_color`/`font_family`/`emphasis_words`/`emphasis_color`/`emphasis_scale` (override preset only if present); `overlays`; `force_ffmpeg` (`:171-293`) |

### What it actually executes
`execute()` (`:667`) validates `style_preset` against `STYLE_PRESETS` (`:679-686`), `emphasis_words` (`:687-693`), color/font types (`:694-696`) and `emphasis_scale` range (`:697-709`), then resolves `_build_caption_style` (`:711, 420-442`) — merging the chosen preset dict with explicit overrides only where the key is *present* in inputs (so schema defaults never clobber a preset). Converts transcript to Remotion `WordCaption` entries via `_segments_to_word_captions` (`:724, 335-370`) or `_srt_to_word_captions` (`:726, 372-414`), applying `corrections`. `_apply_emphasis` (`:736, 444-464`) flags matching words `emphasis: true`.

★ DIVERGENCE on `force_ffmpeg` / `_remotion_available()` (`:741, 325-329` — checks `npx` on PATH plus a `remotion-composer/` dir with `package.json` and `node_modules`, found via `_find_remotion_root` `:310-323`):
- **Remotion path** — `_render_remotion` (`:494-580`): probes duration (`:511-519`) and width/height (`:522-532`) via `ffprobe`; copies the source video into `remotion-composer/public/talking-head/` (`:534-539`); writes a props JSON via `_build_props` (`:542, 466-488`) including `captionStyle` only when non-empty (back-compat); runs `npx remotion render TalkingHead --props=... --width=W --height=H --fps=30 --frames=0-N --codec=h264 --crf=18 --output=...` with `cwd=remotion-composer` (`:551-563`).
- **FFmpeg fallback** — `_render_ffmpeg` (`:586-653`): builds a temp SRT grouping captions 4 words/page (`:599-613`), escapes the path for the `subtitles` filter (Windows colon, `:617`), runs `ffmpeg -vf "subtitles='...':force_style='FontName=Segoe UI,FontSize=24,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,Shadow=2,Alignment=2,MarginV=100'" -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p -c:a copy` (`:619-633`), then deletes the temp SRT (`:636-639`). If non-default styling was requested, attaches `data["style_warning"]` (`:751-771`) explaining every ignored style knob.

### Flow
```text
execute() remotion_caption_burn.py:667
{input_path:"talking_head.mp4", output_path:"captioned.mp4",
 segments:[...], style_preset:"black_pill",
 emphasis_words:["never"]}
                    │
   validate style_preset/emphasis_words/colors/scale (:679-709)
                    │
   _build_caption_style()                (:711, def :420)
                    │
   input_path exists? (:713) ── no ──► error
                    │ yes
   segments → _segments_to_word_captions (:724, def :335)
   (or srt_path → _srt_to_word_captions, :726, def :372)
                    │
   _apply_emphasis()                     (:736, def :444)
                    │
   ★ DIVERGENCE: force_ffmpeg / _remotion_available (:741,:325)
   (npx on PATH + remotion-composer/node_modules present?)
┌────────────────────────┐   ┌───────────────────────────┐
│ REMOTION PATH           │   │ FFMPEG FALLBACK            │
│_render_remotion (:494)  │   │_render_ffmpeg (:586)        │
│ffprobe duration/dims    │   │build temp SRT, 4 words/page │
│ (:511,:522)             │   │ (:599-613)                  │
│copy video → public/     │   │ffmpeg -vf subtitles='...':  │
│ talking-head (:534-539) │   │ force_style='FontName=      │
│write props.json incl.   │   │ Segoe UI,FontSize=24,       │
│ captionStyle (:546-549) │   │ Bold=1,...' (:619-633)       │
│npx remotion render      │   │delete temp srt (:636-639)   │
│ TalkingHead --props=... │   │style_warning attached if    │
│ --codec=h264 --crf=18   │   │ non-default preset/emphasis │
│ (:554-563)              │   │ requested (:751-771)        │
└────────────┬─────────────┘   └─────────────┬───────────────┘
             └──────────────┬────────────────┘
                            ▼
         ToolResult(data={method, output, ...},
                    artifacts=[output_path])
```

**Notes:**
- The FFmpeg fallback ALWAYS burns one fixed bold-white bottom style, ignoring `style_preset`/`emphasis_words` entirely — check `data.method == "remotion"` to confirm requested styles actually rendered.
- The Remotion path physically copies the input video into `remotion-composer/public/talking-head/` before rendering (`:534-539`) — a stale copy there could shadow a newer input if names collide.

---

## `color_grade`

**File:** `tools/enhancement/color_grade.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | CORE |
| capability | enhancement |
| provider | ffmpeg |
| runtime | LOCAL (default, not overridden) |
| determinism | DETERMINISTIC |
| dependencies | `["cmd:ffmpeg"]` (`:139`) |
| capabilities | `grade_preset`, `grade_lut`, `grade_custom`, `adjust`, `curves`, `auto_correct`, `saved_looks` (`:167-175`) |
| best_for | preset cinematic grades/.cube LUTs/parametric adjustments; per-channel curves + shadow/mid/highlight wheels; one-shot luma auto-correct; saved looks reusable across clips (`:176-181`) |
| not_good_for | HDR sources — output is 8-bit SDR; auto white balance/color-cast removal (`op=auto` is luma-only) (`:182-185`) |
| key input params | `input_path` (required), `op` (profile/adjust/curves/auto), `profile`, `lut_path`, `intensity`, `custom_vf`, `brightness`/`contrast`/`saturation`/`gamma`/`temperature`/`tint`/`sharpness`/`vignette`, `points`, `wheels`, `sample_frames`, `look`/`save_look`/`look_name` (`:187-280`) |

### What it actually executes
`execute()` (`:298`) resolves the op via `_resolve_op` (`:315, 416-431`), which loads a saved look from `assets/looks/<name>.json` if `look=` is given (`:467-480`). ★ DIVERGENCE on `op`:
- **`profile`** (legacy) — `_build_filter` (`:317, 788-810`): `custom_vf` wins outright (`:789-790`), else a bare `lut_path` produces `lut3d='<path>'` (`:792-795`), else `PROFILES[name]["vf"]` (e.g. `cinematic_warm` = `colorbalance=rs=0.08:...,curves=all='0/0.03 0.25/0.22 ...',eq=contrast=1.05:saturation=1.1` at `:78-81`). A legacy intensity-blend quirk applies at `:807-808` (bare `lut_path`/`custom_vf` bypass blending — documented as pre-existing, kept for compatibility).
- **`adjust`/`curves`/`auto`** — `_validate_intensity` (`:321, 500-503`); for `adjust`, merges saved-look params with explicit inputs (`:323, 434-445`), validates ranges (`:324, 505-525`) and builds the chain via `_build_adjust_vf` (`:325, 527-566`): optional `lut3d` first, then `temperature` → `colortemperature=temperature=<Kelvin>` if the installed ffmpeg has that filter (probed once via `ffmpeg -hide_banner -filters` and cached at the class level, `:568-582`) else a `colorbalance` rm/bm fallback shift (`:543-546`); `tint` → `colorbalance` gm; `eq=brightness:contrast:saturation:gamma`; `sharpness` → `unsharp=5:5:<amt>` (negative blurs); `vignette` → `vignette=angle=...`. `curves` builds `curves=master='x/y ...'` per channel plus `colorbalance=` from shadows/midtones/highlights wheel offsets (`:586-620`, suffix s/m/h at `:653`). `auto` samples frames via `ffmpeg -vf fps=rate,signalstats,metadata=print:file=- -f null -` (`:695-699`), parses YAVG/YMIN/YMAX, and computes brightness/contrast via a documented heuristic (`_auto_corrections`, `:729-741`), then reuses `_build_adjust_vf`. All non-profile ops are wrapped in `_blend_intensity` (`:336, 758-771`) with corrected semantics: `all_opacity = 1 - intensity` (a 0.2.0 bugfix from a previously-inverted blend — profile calls with partial intensity get a `data["intensity_warning"]`, `:373-385`).

Final ffmpeg run: `ffmpeg -y -i <input> -vf <vf> -c:v <codec> -crf <crf> -c:a copy <output>` (`:345-353`, via `_run_ffmpeg`/`run_command`, `:773-784`). `save_look=true` persists resolved params atomically to `assets/looks/<name>.json` (`:401, 482-495`).

Example: `op="adjust"`, `temperature=40`, `sharpness=0.3`, `intensity=0.7`, `save_look=true`, `look_name="warm_pop"` → chain `colortemperature=temperature=5300,unsharp=5:5:0.45` wrapped as `split[original][tograde];[tograde]<chain>[graded];[original][graded]blend=all_mode=normal:all_opacity=0.3`; saves `assets/looks/warm_pop.json = {op:"adjust", params:{temperature:40, sharpness:0.3}}`.

### Flow
```text
execute() color_grade.py:298
{input_path:"clip.mp4", op:"adjust", temperature:40,
 sharpness:0.3, intensity:0.7, save_look:true,
 look_name:"warm_pop"}
                    │
     input_path exists? (:303) ── no ──► error
                    │ yes
     _resolve_op()  (:315, def :416) → op="adjust"
                    │
   ★ DIVERGENCE: op
 ┌────────────────────┐  ┌───────────────────────────────┐
 │ profile (:316)      │  │ adjust / curves / auto          │
 │_build_filter (:788) │  │_validate_intensity (:321)       │
 │ custom_vf > lut_path│  │                                  │
 │ > PROFILES[name]    │  │ adjust: _merge_look_params (:323,│
 │ (legacy intensity   │  │  :434) + _validate_adjust (:324, │
 │ quirk, :807-808)    │  │  :505) + _build_adjust_vf (:325, │
 └──────────┬───────────┘  │  :527): temperature→              │
            │               │  colortemperature (if avail,     │
            │               │  :568-582) else colorbalance      │
            │               │  fallback; sharpness→unsharp      │
            │               └──────────────┬───────────────────┘
            │                              ▼
            │              _blend_intensity()  (:336, :758)
            │              opacity = 1 - intensity = 0.3
            │                              │
            └──────────────┬───────────────┘
                           ▼
     ffmpeg -y -i clip.mp4 -vf "<chain wrapped in
       split/blend>" -c:v libx264 -crf 20 -c:a copy
       clip_graded.mp4                    (:345-353)
                           │
                           ▼
     save_look=true → _save_look() (:401,:482) writes
       assets/looks/warm_pop.json
                           │
                           ▼
     ToolResult(data={op, filter, adjust, look_path},
                artifacts=[output, look_path])
```

**Notes:**
- `op=profile` keeps a documented "quirk": a bare `lut_path`/`custom_vf` bypasses the intensity blend entirely — use `op=adjust` with `lut_path` for an intensity-blended LUT.
- `colortemperature` filter availability is probed once per process via `ffmpeg -filters` and cached class-wide (`_available_filters`) — a stale cache across ffmpeg upgrades in the same long-lived process would miss the real filter.

---

## `face_enhance`

**File:** `tools/enhancement/face_enhance.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | CORE |
| capability | enhancement |
| provider | ffmpeg |
| runtime | LOCAL (default, not overridden) |
| determinism | DETERMINISTIC |
| dependencies | `["cmd:ffmpeg"]` (`:80`) |
| capabilities | `skin_smoothing`, `sharpening`, `lighting_correction`, `color_balance`, `denoise`, `preset_chain` (`:84-91`) |
| best_for | not set on this tool (no `best_for` attribute) |
| not_good_for | not set on this tool (no `not_good_for` attribute) |
| key input params | `input_path` (required), `preset` (9 presets, default `talking_head_standard`), `presets` (array, chained), `custom_vf`, `codec`, `crf` (`:93-116`) |

### What it actually executes
`execute()` (`:126`) resolves the filter via `_build_filter` (`:138, 172-188`): `custom_vf` wins outright (`:173-174`); else if `presets` (array) is given, concatenates each named preset's `vf` string with commas, silently skipping unknown names (`:176-182`); else falls back to the single `preset` (default `talking_head_standard`, itself a combined chain at `:59-66`): `smartblur=lr=1.0:ls=-0.5:lt=-3.0:cr=0.5:cs=-0.5:ct=-3.0` (skin smoothing) `,unsharp=5:5:0.6:5:5:0.0` (re-sharpens edges lost to the blur) `,colorbalance=rs=0.06:gs=0.01:bs=-0.04:rm=0.04:gm=0.01:bm=-0.03` (warms skin tones). Runs `ffmpeg -y -i <input> -vf <vf> -c:v <codec> -crf <crf> -c:a copy <output>` (`:144-151`) via `run_command` (`:154`). No GPU/ML anywhere — pure ffmpeg filter chains per the module docstring (`:1-6`).

Example: `preset="talking_head_standard"` → filter string above, run once.

### Flow
```text
execute() face_enhance.py:126
{input_path:"face.mp4", presets:["soft_skin","sharpen","warm"]}
                │
   input exists? (:128) ── no ──► error
                │ yes
   _build_filter()          (:138, def :172)
     custom_vf? no
     "presets" list given (:176-182):
       chain = PRESETS["soft_skin"]["vf"] + ","
             + PRESETS["sharpen"]["vf"]   + ","
             + PRESETS["warm"]["vf"]
     = "smartblur=lr=1.0:...,unsharp=5:5:1.0:5:5:0.0,
        colorbalance=rs=0.05:..."
                │
                ▼
   ffmpeg -y -i face.mp4 -vf "<chain>"
     -c:v libx264 -crf 20 -c:a copy face_enhanced.mp4 (:144-151)
                │
                ▼
   ToolResult(data={filter, preset}, artifacts=[output])
```

**Notes:**
- Unknown preset names inside a `presets` array are silently dropped (`if name not in PRESETS: continue`, `:179-180`) rather than erroring — a typo quietly shrinks the filter chain.
- `talking_head_standard`'s order (smooth → sharpen → warm) is deliberate: the unsharp pass exists specifically to recover detail the smartblur pass removed.

---

## `bg_remove`

**File:** `tools/enhancement/bg_remove.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | ENHANCE |
| capability | enhancement |
| provider | rembg |
| runtime | HYBRID (`:36`, explicit override) |
| determinism | DETERMINISTIC |
| dependencies | `["python:rembg", "python:PIL"]` (`:38`) — no `cmd:ffmpeg` |
| capabilities | `background_removal`, `alpha_matte`, `batch_processing`, `custom_background` (`:45-50`) |
| best_for | not set on this tool |
| not_good_for | not set on this tool |
| key input params | `input_path` (required, image/frame), `output_path`, `model` (u2net/u2net_human_seg/isnet-general-use), `bg_color` (hex), `alpha_matting` (bool) (`:52-79`) |

### What it actually executes
`get_status()` (`:92-97`) is a pure `import rembg` check — no `shutil.which("ffmpeg")` anywhere in this file. `execute()` (`:99`) checks the input exists (`:100-102`), lazily imports `rembg` and `PIL.Image` with graceful `ImportError` messages (`:111-125`), opens the input with `PIL.Image.open` (`:129`) — this tool operates on a **single still image/frame**, never a video, and there is **no ffmpeg subprocess anywhere in the file**. The core call is `rembg.remove(input_image, session=rembg.new_session(model_name), alpha_matting=alpha_matting)` (`:135-139`) — an in-process ONNX (U2Net-family) inference, no subprocess. A code comment (`:131-134`) documents a real bug found by `scripts/verify_containment.sh`: passing `model_name` directly as a kwarg to `rembg.remove()` collided with `new_session()`'s own `model_name` argument and crashed every call; fixed by pre-building the session object explicitly. If `bg_color` is given, the RGBA result is composited onto a solid color using the alpha channel as a paste mask, then converted to RGB (`:142-147`). Saved via `PIL.Image.save` (`:150`).

Example: `input_path="frame_001.png"`, `model="u2net_human_seg"`, `bg_color="#00FF00"` → rembg produces an RGBA cutout using the human-seg U2Net model → composited onto solid green → saved as RGB PNG.

### Flow
```text
execute() bg_remove.py:99
{input_path:"frame_001.png", model:"u2net_human_seg",
 bg_color:"#00FF00"}
                │
   input exists? (:100) ── no ──► error
                │ yes
   import rembg / PIL (lazy, :111-125)
                │
                ▼
   PIL.Image.open(frame_001.png)          (:129)
                │
                ▼
   rembg.remove(image,
     session=rembg.new_session("u2net_human_seg"),
     alpha_matting=False)                 (:135-139)
   [U2Net-human-seg ONNX inference, in-process — no subprocess]
                │
                ▼
   bg_color given? ── yes ──► composite RGBA onto solid
                │             (0,255,0) via alpha-mask
                │             paste, convert to RGB (:142-147)
                │ no
                ▼
   Image.save(output_path)                (:150)
                │
                ▼
   ToolResult(data={model, alpha_matting, bg_color},
              artifacts=[output])
```

**Notes:**
- No ffmpeg call exists anywhere in this file despite `agent_skills = ["ffmpeg"]` (`:43`) — a caller wanting video background removal must extract/reassemble frames itself (as `upscale.py` does) since this tool only ever accepts one image path per call.
- `runtime = HYBRID` appears to reflect rembg's own optional GPU/`onnxruntime-gpu` execution mode (install_instructions, `:41`) rather than a cloud-API branch — no API code path exists in `execute()`.

---

## `face_restore`

**File:** `tools/enhancement/face_restore.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | ENHANCE |
| capability | enhancement |
| provider | codeformer |
| runtime | LOCAL_GPU (`:36`) |
| determinism | DETERMINISTIC |
| dependencies | `["python:gfpgan", "python:torch"]` (`:38`) |
| capabilities | `face_restoration`, `face_detection`, `quality_enhancement` (`:45-49`) |
| best_for | not set on this tool (`fallback = None` explicit, `:43`) |
| not_good_for | not set on this tool |
| key input params | `input_path` (required), `model` (CodeFormer/GFPGAN), `fidelity` (0=quality↔1=fidelity, CodeFormer only), `upscale` (factor), `bg_upsampler` (bool, Real-ESRGAN) (`:51-87`) |

### What it actually executes
`get_status()` (`:99-104`) checks `import gfpgan`. `execute()` (`:106`) checks the input exists (`:108-109`), lazily imports `cv2` and `GFPGANer` (`:122-129`). If `bg_upsampler=true` (`:134-157`), it builds a Real-ESRGAN background upsampler: `RRDBNet(num_in_ch=3,num_out_ch=3,num_feat=64,num_block=23,num_grow_ch=32,scale=2)` + `RealESRGANer(scale=2, model_path=".../RealESRGAN_x2plus.pth", tile=400, half=True)` — falling back to `None` silently on `ImportError` (`:156-157`). Model selection (`:159-171`): `model="CodeFormer"` → `model_path=".../codeformer.pth"`, `arch="CodeFormer"`; else `model_path=".../GFPGANv1.3.pth"`, `arch="clean"`. Instantiates `GFPGANer(model_path=..., upscale=upscale, arch=arch, bg_upsampler=bg_upsampler)` (`:175-180`) — GFPGANer internally handles weight download, face detection/alignment. Reads the image via `cv2.imread` (`:187`). The real work: `restorer.enhance(input_img, has_aligned=False, only_center_face=False, paste_back=True, weight=fidelity if CodeFormer else None)` (`:194-201`) — detects faces, runs the restoration network (CodeFormer or GFPGAN), pastes restored face(s) back into the full frame (optionally over the Real-ESRGAN-upscaled background). Writes via `cv2.imwrite` (`:212`). No ffmpeg subprocess anywhere — single image only, no video frame loop in this file.

Example: `input_path="face_frame.png"`, `model="CodeFormer"`, `fidelity=0.6`, `upscale=2`, `bg_upsampler=true` → CodeFormer arch with weight=0.6, background upscaled 2x via Real-ESRGAN, restored face pasted back.

### Flow
```text
execute() face_restore.py:106
{input_path:"face_frame.png", model:"CodeFormer",
 fidelity:0.6, upscale:2, bg_upsampler:true}
                │
   input exists? (:108) ── no ──► error
                │ yes
   import cv2, GFPGANer                   (:122-129)
                │
                ▼
   bg_upsampler=true → build RealESRGANer x2plus
     (RRDBNet scale=2, tile=400)           (:134-157)
                │
                ▼
   model="CodeFormer" → model_path=github .../codeformer.pth,
     arch="CodeFormer"                     (:160-165)
                │
                ▼
   GFPGANer(model_path, upscale=2, arch="CodeFormer",
     bg_upsampler=<RealESRGANer>)          (:175-180)
                │
                ▼
   cv2.imread(face_frame.png)              (:187)
                │
                ▼
   restorer.enhance(img, has_aligned=False,
     only_center_face=False, paste_back=True,
     weight=0.6)                           (:194-201)
   [face detect+align → CodeFormer net → paste back
    onto Real-ESRGAN-upscaled background — in-process]
                │
                ▼
   cv2.imwrite(output_path, restored_img)  (:212)
                │
                ▼
   ToolResult(data={model, faces_detected, fidelity},
              artifacts=[output])
```

**Notes:**
- Despite the schema describing `input_path` as "image or video frame" (`:57`), `execute()` only ever `cv2.imread`s one file — video would need to be driven per-frame externally.
- Model weights are hardcoded GitHub release URLs fetched at call time (`:146-149, 162-171`) — `runtime=LOCAL_GPU` still implies network access on first run.

---

## `eye_enhance`

**File:** `tools/enhancement/eye_enhance.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | ENHANCE |
| capability | enhancement |
| provider | mediapipe |
| runtime | LOCAL (default, not overridden) |
| determinism | DETERMINISTIC |
| dependencies | `["cmd:ffmpeg"]` (`:57`) — mediapipe/opencv are NOT declared here, only probed ad hoc |
| capabilities | `under_eye_brightening`, `dark_circle_removal`, `eye_sharpening`, `eye_brightening` (`:65-70`) |
| best_for | not set on this tool |
| not_good_for | not set on this tool |
| key input params | `input_path` (required), `operations` (dark_circles/brighten_eyes/sharpen_eyes), `dark_circle_intensity`, `eye_brighten_intensity`, `sharpen_intensity`, `codec`, `crf` (`:72-111`) |

### What it actually executes
`get_status()` (`:142-147`): `AVAILABLE` if both `mediapipe`+`opencv` importable, `DEGRADED` with opencv only, else `UNAVAILABLE`. `execute()` (`:149`) picks one of three implementations by capability (`:162-167`):
1. **Both present** — `_enhance_mediapipe` (`:176-268`): opens the video with `cv2.VideoCapture` (`:193`), writes to a silent temp mp4 via `cv2.VideoWriter` (`:200-203`), runs `mp.solutions.face_mesh.FaceMesh(refine_landmarks=True, ...)` per frame (`:208-232`, `refine_landmarks` enables iris landmarks 468-477); for each detected face, `_apply_eye_enhancements` (`:270-320`) runs, per eye, `_remove_dark_circles` (`:322-368`: LAB L-channel boost + HSV desaturation inside a Gaussian-blurred polygon below the lower eyelid), `_brighten_eyes` (`:370-396`: LAB L-channel boost inside the eye-contour polygon), `_sharpen_eyes` (`:398-423`: `cv2.addWeighted(frame, 1+intensity, gaussianblur, -intensity)` masked to a dilated eye contour). After the frame loop, ffmpeg remuxes the **original audio** onto the silent OpenCV output: `ffmpeg -y -i <temp> -i <input> -c:v libx264 -crf <crf> -preset fast -c:a aac -b:a 192k -map 0:v:0 -map 1:a:0? -shortest <output>` (`:238-247`) — this is the only subprocess in this path, purely for audio (OpenCV's `VideoWriter` can't carry audio).
2. **Opencv only** — `_enhance_opencv_only` (`:425-536`): Haar cascade face+eye detection (`haarcascade_frontalface_default.xml` / `haarcascade_eye.xml`), applies only a generic dark-circles ellipse LAB boost below each detected eye box (no brighten/sharpen ops here), same ffmpeg audio-mux step (`:506-515`).
3. **Neither** — `_enhance_ffmpeg_fallback` (`:538-574`): pure ffmpeg, not eye-specific: `ffmpeg -vf "eq=brightness=<0.02*intensity>:contrast=<1+0.05*intensity>" -c:v libx264 -crf <crf> -preset fast -c:a copy` on the whole frame (`:546-557`), tagged `method="ffmpeg_global_brightness"`.

Example: `operations=["dark_circles","brighten_eyes","sharpen_eyes"]`, `dark_circle_intensity=0.4` with mediapipe installed → per-frame Face Mesh landmarks drive targeted LAB/HSV pixel edits, then ffmpeg remuxes the original audio track back on.

### Flow
```text
execute() eye_enhance.py:149
{input_path:"talking_head.mp4",
 operations:["dark_circles","brighten_eyes","sharpen_eyes"],
 dark_circle_intensity:0.4}
                │
   input exists? (:150) ── no ──► error
                │ yes
   ★ DIVERGENCE: has_mediapipe()+has_opencv()? (:162-167)
┌───────────────┬────────────────────┬─────────────────────┐
│ BOTH (best)    │ opencv only        │ neither               │
│_enhance_       │_enhance_opencv_    │_enhance_ffmpeg_       │
│ mediapipe(:176)│ only (:425)        │ fallback (:538)        │
│VideoCapture    │Haar cascade face + │ffmpeg -vf "eq=         │
│(:193); per-    │eye detect (:436-  │ brightness=0.008:      │
│frame FaceMesh  │ 472)→ ellipse dark-│ contrast=1.02" whole-  │
│(:208-232); per-│circle LAB boost    │ frame (:546-557) — NOT │
│eye: dark       │only (:482-496)     │ eye-specific            │
│circles(:322),  │                    │                         │
│brighten(:370), │                    │                         │
│sharpen(:398)   │                    │                         │
└───────┬────────┴──────────┬─────────┴──────────┬──────────────┘
        ▼                   ▼                     │
   ffmpeg mux original audio onto the silent        │
   OpenCV-written temp video: "-map 0:v:0            │
   -map 1:a:0? -shortest"        (:238-247, :506-515) │
        └──────────────────┬─────────────────────────┘
                            ▼
       ToolResult(data={method, frames_processed,
                  frames_enhanced}, artifacts=[output])
```

**Notes:**
- ffmpeg here does audio-remux ONLY — the actual enhancement is entirely OpenCV/MediaPipe pixel math, unlike `face_enhance.py`/`color_grade.py` where ffmpeg IS the enhancement engine.
- `mediapipe`/`opencv` aren't declared in `dependencies` (only `cmd:ffmpeg` is), so registry-level dependency checks won't flag a missing mediapipe install; degradation is entirely runtime, via `get_status()` and the three-tier `execute()` branch.

---

## `upscale`

**File:** `tools/enhancement/upscale.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | ENHANCE |
| capability | enhancement |
| provider | realesrgan |
| runtime | LOCAL_GPU (`:56`) |
| determinism | DETERMINISTIC |
| dependencies | `["python:realesrgan", "python:torch", "cmd:ffmpeg"]` (`:58`) |
| capabilities | `image_upscale`, `video_upscale`, `face_aware_upscale` (`:62-66`) |
| best_for | not set on this tool |
| not_good_for | not set on this tool |
| key input params | `input_path` (required), `scale` (2 or 4), `model` (RealESRGAN_x4plus/_anime_6B, RealESRNet_x4plus), `face_enhance` (bool, GFPGAN), `denoise_strength` (`:68-97`) |

### What it actually executes
`get_status()` (`:111-116`) checks `import realesrgan`. `execute()` (`:122`) determines `is_video` by suffix `{.mp4,.mov,.avi}` (`:127`) and dispatches:
- **Image** — `_upscale_image` (`:175-196`): single `cv2.imread` + `upsampler.enhance(img, outscale=scale)` + `cv2.imwrite`, no subprocess at all.
- **Video** — `_upscale_video` (`:202-255`): builds the upsampler via `_build_upsampler` (`:213, 261-314`) — chooses `RRDBNet` architecture per model (fewer blocks for `_anime_6B`), resolves weights to a hardcoded GitHub release URL (`:280-282, 296`), uses half-precision if `torch.cuda.is_available()` (`:284`); if `face_enhance=true`, monkey-patches `upsampler.enhance` with a `GFPGANer`-based `enhance_with_face` (`:294-312`) so GFPGAN's face-aware restoration replaces the raw upsample. Gets source fps via ffprobe (`_get_video_fps`, `:216, 316-339`). Inside a `tempfile.TemporaryDirectory` (`:218`): `ffmpeg -y -i <input> frames/frame_%06d.png` **extracts every frame** (`:225-229`); each PNG is read via `cv2.imread`, run through `upsampler.enhance(img, outscale=scale)`, and written via `cv2.imwrite` into an `upscaled/` dir (`:232-238`) — this is the actual per-frame Real-ESRGAN (or GFPGAN-wrapped) GPU inference; finally `ffmpeg -y -framerate <fps> -i upscaled/frame_%06d.png -i <input> -map 0:v -map 1:a? -c:v libx264 -crf 18 -c:a copy -pix_fmt yuv420p <output>` **reassembles**, pulling video from the upscaled PNG sequence while copying audio straight from the original (`:241-253`).

Example: `input_path="clip.mp4"`, `scale=4`, `model="RealESRGAN_x4plus"`, `face_enhance=true` → extract frames, upscale each with a GFPGAN-wrapped RRDBNet(num_block=23, scale=4) enhancer, reassemble at source fps with original audio copied through.

### Flow
```text
execute() upscale.py:122
{input_path:"clip.mp4", scale:4, model:"RealESRGAN_x4plus",
 face_enhance:true}
                │
   input exists? (:124) ── no ──► error
                │
                ▼
   is_video = suffix in {.mp4,.mov,.avi} (:127) → True
                │
                ▼
   _upscale_video()                       (:142, def :202)
     _build_upsampler() (:213, def :261): RRDBNet(scale=4),
       RealESRGANer(model_path=github RealESRGAN_x4plus.pth,
       half=torch.cuda.is_available())
     face_enhance=true → wrap upsampler.enhance with
       GFPGANer-based enhance_with_face()  (:294-312)
                │
                ▼
     _get_video_fps() via ffprobe r_frame_rate  (:216,:316)
                │
                ▼
     ffmpeg -y -i clip.mp4 frames/frame_%06d.png
       [EXTRACT every frame]                (:225-229)
                │
                ▼
     for each frame_NNNNNN.png:
       cv2.imread → upsampler.enhance(img, outscale=4)
       → cv2.imwrite to upscaled/           (:232-238)
       [GFPGAN-wrapped Real-ESRGAN inference, per frame]
                │
                ▼
     ffmpeg -y -framerate <fps> -i upscaled/frame_%06d.png
       -i clip.mp4 -map 0:v -map 1:a? -c:v libx264 -crf 18
       -c:a copy -pix_fmt yuv420p output.mp4
       [REASSEMBLE + copy original audio]   (:241-253)
                │
                ▼
       ToolResult(data={total_frames, fps, scale, model},
                  artifacts=[output])
```

**Notes:**
- Model weights are fetched from hardcoded GitHub release URLs at call time (`:280-282, 296`) — `runtime=LOCAL_GPU` still needs network access on first run.
- Video path is genuinely extract-to-PNG → per-frame GPU inference → PNG-sequence reassembly, matching the "ffmpeg splits/reassembles frames around it" pattern exactly.

---

## `object_cutout`

**File:** `tools/enhancement/object_cutout.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | ENHANCE |
| capability | segmentation |
| provider | meta-sam2 |
| runtime | API (`:137`) |
| determinism | DETERMINISTIC |
| dependencies | `["env:REPLICATE_API_TOKEN", "cmd:ffmpeg"]` (`:168`) |
| capabilities | `object_segmentation`, `object_tracking`, `alpha_cutout`, `selective_object_effects` (`:177-179`) |
| best_for | isolating a tracked subject into a transparent cutout (Edits-style); `op="effect"` blur/pixelate/glow/outline/bw_background reusing a prior cutout, local + free (`:188-193`) |
| not_good_for | auto subject pick w/ no clicks (SAM2 needs explicit points); stills (use `bg_remove`); tight feedback loops (`op="cutout"` is paid, ~30s+); HDR sources (`:194-201`) |
| key input params | `video_path` (required), `op` (cutout/effect), `points` (x/y/label/frame/object_id), `effect` (blur/pixelate/glow/outline/bw_background), `mask_path`/`cutout_path`, `strength`/`pixel_size`/`color`/`thickness`, `mask_type`, `use_cache`, `confirm`, `resume_prediction_id` (`:205-338`) |

### What it actually executes
`execute()` (`:381`) branches on `op` **before** any Replicate token/network guard (`:385-387`), so `op="effect"` is entirely local and free.

**`op="cutout"` (paid, API):** validates `REPLICATE_API_TOKEN` (`:390-391`, `_validate_token` `:1143-1159` — catches the `.env` inline-comment footgun and a missing `r8_` prefix), checks `requests` importable (`:407-415`) and `ffmpeg` on PATH (`:417-423`), validates `video_path`/format (`:425-438`) and `points` (`:440-443`, `_validate_points` `:904-919` — requires ≥1 positive click, no auto mode). Computes `sha256(video)` + a cache key over `(sha, points, mask_type)` (`:448-449`). ★ resume_prediction_id short-circuits to polling (`:452-463`). ★ a cache hit (`:467`, `_cached_result` `:1200-1218`) returns a free `ToolResult` if the cached file still exists. Otherwise, under a POSIX flock (`:476`, `_key_lock` `:1245-1266`) to prevent double-charging concurrent identical calls: re-checks cache and in-flight markers, then requires `confirm=true` or `OBJECT_CUTOUT_AUTOCONFIRM=1` (`:487-502`) else returns `requires_confirmation` with zero spend. If confirmed: `_maybe_downscale` (`:504, 1108-1136`) shrinks the video to ≤1080px longest side via `ffmpeg -vf scale=...` (Replicate video models stall above 1080p); `_upload` (`:506, 1049-1064`) does `POST /v1/files`; `_create_prediction` (`:510, 955-979`) resolves `latest_version` via `GET /v1/models/meta/sam-2-video` then `POST /v1/predictions` with `{version, input: {input_video, mask_type, click_coordinates, click_labels, click_frames, click_object_ids}}` (serialized by `_serialize_points`, `:922-934`) and header `Prefer: wait`; `_poll_prediction` (`:996-1024`) polls every 3s up to `max_wait_seconds`, raising a **non-terminal** error on timeout (so a client-side timeout keeps the in-flight marker instead of forcing a re-pay). `_finalize` (`:539-618`) downloads the mask (`:556`) then, for `mask_type="binary"`, runs the real ffmpeg composite via `_composite_alpha` (`:564, 1076-1106`): `ffmpeg -i source -i mask -filter_complex "[1:v][0:v]scale2ref=w=iw:h=ih[mask][src];[mask]format=gray[m];[src][m]alphamerge[out]" -map [out] -c:v qtrle -an <out>.mov` — `scale2ref` rescales the (possibly downscaled) mask back to source dims before `alphamerge` turns mask luminance into alpha.

**`op="effect"` (local, free):** `_execute_effect` (`:622-706`) validates via `_validate_effect_inputs` (`:633, 708-788` — needs `mask_path`/`cutout_path` from a *prior* cutout run; segmentation is never re-run), probes source dims/fps via `_probe_video` (`:648, 871-900`), and (for a cutout source) checks the alpha channel is real via `_pix_fmt_has_alpha` (`:657-667, 851-854`). `_build_effect_graph` (`:671, 790-849`) assembles the `-filter_complex`, e.g. `blur`: `[1:v]alphaextract[mraw];[mraw][0:v]scale2ref[m][src];[src]split[base][fxs];[fxs]gblur=sigma=<S>[fx];[fx][m]alphamerge[fxa];[base][fxa]overlay=shortest=1[out]`; `outline` dilates the mask N times and diffs it against itself (`blend=all_mode=difference`) to get a colored ring; `bw_background` is the one effect that inverts the mask (`negate`) to desaturate everywhere *except* the object. Runs `ffmpeg -i video -i mask_src -filter_complex "<graph>" -map [out] -map 0:a? -c:a copy -c:v libx264 -pix_fmt yuv420p <output>` (`:672-684`). `cost_usd` is always `0.0` for `op="effect"` (`:703-705, 375-376`).

### Flow
```text
execute() object_cutout.py:381
 op ∈ {cutout(default), effect}
                    │
   ★ DIVERGENCE: op (branches BEFORE any token/network
   check, :385-387 — op=effect never touches Replicate)
┌───────────────────────────┐ ┌──────────────────────────────┐
│op="cutout" (PAID, API)     │ │op="effect" (LOCAL, FREE)       │
│                            │ │_execute_effect (:622)           │
│validate token (:390,:1143) │ │_validate_effect_inputs (:633,   │
│ .env footgun / r8_ check   │ │  :708): effect∈EFFECTS, needs   │
│validate points (:440,:904) │ │  mask_path/cutout_path from a  │
│ >=1 positive click          │ │  PRIOR cutout run              │
│sha256(video)+cache_key     │ │_probe_video (:648,:871): w,h,fps│
│  (:448-449)                │ │  via ffprobe                    │
│                            │ │                                 │
│★ cache hit? (:467,:1200)   │ │_build_effect_graph (:671,:790)  │
│ → free ToolResult, done    │ │ effect="blur":                  │
│                            │ │  [1:v]alphaextract[mraw];        │
│★ resume_prediction_id?     │ │  [mraw][0:v]scale2ref[m][src];  │
│ (:452-463) → poll only      │ │  [src]split[base][fxs];          │
│                            │ │  [fxs]gblur=sigma=15[fx];        │
│confirm=true? (:487-502)     │ │  [fx][m]alphamerge[fxa];         │
│ no → requires_confirmation,│ │  [base][fxa]overlay=shortest=    │
│ $0 spent; yes ↓             │ │  1[out]                          │
│_maybe_downscale >1080p      │ │                                  │
│ (:504,:1108)                │ │ffmpeg -i interview.mp4 -i        │
│_upload → POST /v1/files     │ │ interview_cutout_mask.mp4         │
│ (:506,:1049)                │ │ -filter_complex "<graph>" -map   │
│_create_prediction: resolve  │ │ [out] -map 0:a? -c:a copy -c:v   │
│ latest_version → POST       │ │ libx264 -pix_fmt yuv420p          │
│ /v1/predictions {click_     │ │ interview_fx_blur.mp4 (:672-684)  │
│ coordinates="[400,300]",    │ │                                  │
│ click_labels="1",...}       │ │ToolResult(cost_usd=0.0,           │
│ (:510,:955)                 │ │ artifacts=[out])                  │
│_poll_prediction every 3s    │ └──────────────────────────────────┘
│ (:522-533,:996)              │
│_finalize (:539): download   │
│ mask.mp4 (:556); ffmpeg -i  │
│ interview.mp4 -i mask.mp4   │
│ -filter_complex "[1:v]      │
│ [0:v]scale2ref[mask][src];  │
│ [mask]format=gray[m];       │
│ [src][m]alphamerge[out]"    │
│ -map [out] -c:v qtrle -an   │
│ interview_cutout.mov        │
│ (:564,:1076-1106); write    │
│ cache + clear in-flight     │
│ marker (:594-596)            │
│                            │
│ToolResult(cost_usd=~0.06,   │
│ artifacts=[cutout,mask])    │
└────────────────────────────┘
```

**Notes:**
- The tool deliberately does NOT auto-fallback to `bg_remove` when SAM2/the token is unavailable — it names `bg_remove` in the `ToolResult` data (`fallback_tools`, `:202, 393-405`) as a person-only, no-tracking alternative the agent must explicitly opt into, per the Decision Communication Contract.
- A client-side poll timeout is treated as **non-terminal** (`_PredictionError(terminal=False)`, `:1005-1010`), keeping the in-flight marker so a follow-up call (via `resume_prediction_id` or just `use_cache`) resumes instead of re-paying for a still-running prediction.

# Audio editing

## `audio_mixer`

**File:** `tools/audio/audio_mixer.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `core` (`tools/audio/audio_mixer.py:48`) |
| capability | `audio_processing` |
| provider | `ffmpeg` |
| runtime | `local` (BaseTool default — not overridden in this file) |
| determinism | `deterministic` |
| dependencies | `["cmd:ffmpeg"]`; pydub mentioned only in the docstring/install text, never actually imported or required — despite the module docstring calling this "FFmpeg + pydub", the `execute()` path is 100% FFmpeg subprocess calls |
| capabilities | `["mix", "duck", "fade", "normalize", "extract_audio", "segmented_music", "auto_balance"]` |
| best_for | `[]` (not set — inherits `BaseTool` default) |
| not_good_for | `["HDR sources — output is 8-bit SDR; detect with is_hdr_source() and handle HDR per AGENT_GUIDE before using this tool"]` |
| key input params | `operation` (mix/duck/extract/full_mix/segmented_music/auto_balance), `tracks[]` (path/role/volume/start_seconds/fade_in_seconds/fade_out_seconds), `primary_audio`/`secondary_audio`/`duck_level` (simple duck API), `input_path`/`codec`/`sample_rate`/`channels`/`stream_index` (extract), `target_lufs_voice`/`music_offset_db`/`sfx_offset_db`/`apply` (auto_balance), `ducking{enabled, music_volume_during_speech, attack_ms, release_ms}`, `normalize`, `video_path`/`music_path`/`music_volume`/`segments`/`fade_duration` (segmented_music) |

### What it actually executes
`execute()` (`audio_mixer.py:280`) times the call and dispatches on `operation` to one of six private methods, catching both a local `_MixInputError` and any generic `Exception` into a `ToolResult(success=False, ...)` (`:299-302`).

`_mix` (`:307`) validates every track path exists (`:318-320`), then for each track builds a per-track FFmpeg filter chain via `_per_track_chain` (`:326-328`, chain builder at `:912`), joins them with `amix=inputs=N:duration=longest:dropout_transition=2` (`:330-334`), and optionally appends `loudnorm=I=-16:LRA=11:TP=-1.5` (`:336-340`) before running `ffmpeg -y -i A -i B ... -filter_complex "..." -map [out]/[mixed] output` through `self._run` (`:349`, wrapper at `:1004`).

`_duck` (`:364`) accepts either the simple `primary_audio`/`secondary_audio`+`duck_level` format or the advanced `tracks[]` role format (`:391-433`); a `duck_level` in dB is converted to a linear ratio (`10**(db/20)`) for `music_volume_during_speech` (`:401-408`). It builds a **sidechaincompress** ducking chain using the speech track as the key signal: `[1:a]sidechaincompress=threshold=0.02:ratio=9:attack=<s>:release=<s>:level_sc=1:mix=0.9[ducked];[ducked]volume=<music_vol*3>[music_out];[0:a][music_out]amix=inputs=2:duration=longest[out]` (`:441-447`), then runs it via **`self.run_command` directly** (`:458`) — not through the `_run` wrapper the other operations use, so a `CalledProcessError` here is *not* trimmed to a 500-char stderr tail; it bubbles up to `execute()`'s generic `except Exception` (`:301`) as the raw exception string.

`_extract` (`:471`) validates `codec`/`sample_rate`/`channels`/`stream_index` combinations, rejecting `sample_rate`/`channels` with `codec=copy` (`:496-504`). With no `codec`, it preserves the legacy transcription-grade default: `pcm_s16le`, 16kHz mono (`:511-515`). `codec=copy` probes the source codec via `ffprobe -select_streams a:N -show_entries stream=codec_name` (`_probe_audio_codec`, `:990-1002`) and stream-copies it. Builds `ffmpeg -y -i input -vn [-map 0:a:N] -acodec <codec> [-ar SR] [-ac CH] output` (`:530-539`), run via `self._run` (`:541`).

`_auto_balance` (`:564`) measures each track's integrated LUFS via `_measure_lufs` (`:969-988`, runs `ffmpeg -af ebur128 -f null -` and regex-parses the `Summary:` block's `I: <x> LUFS` line, `:975-985`), computes `gain_db = target - measured` per role (voice/music/sfx targets built from `target_lufs_voice` + offsets, `:582-587`), caps the gain at ±30dB (`MAX_BALANCE_GAIN_DB`, `:613-616`), and converts to a linear `volume = 10**(gain_db/20)` (`:617`). With `apply=false` it returns the measured/computed report only, no file written (`:643-644`); otherwise it hands the computed per-track volumes to `self._mix(...)` with `normalize` defaulted **off** (`:646-650`) since a post-mix loudnorm would erase the balance just computed.

`_full_mix` (`:657`) is the "preferred for compose-director" one-shot op: it groups tracks into speech/music/sfx (`:690-693`), builds per-track chains (`:707-709`), and if ducking is enabled and both speech and music exist (`:714`), it mixes all speech into `[speech_all]` then `asplit`s it into `[speech_key]` (the sidechain key) and `[speech_out]` (the output copy) — explicitly to avoid consuming the same pad twice, a bug the header docstring says a previous version had (`:721-729`). Music tracks are similarly mixed to `[music_all]` (`:732-740`), then sidechain-ducked with attack/release **in milliseconds, clamped to ffmpeg's 0.01–2000 range** (`:742-756`) — the comment notes a prior `/1000` bug made attack ~1000x too fast. Speech + ducked music (+ any sfx) are combined in a final `amix` into `[premix]` (`:758-767`); without ducking it's a flat `amix` of everything (`:769-774`). Optional `loudnorm` follows (`:776-781`), then the built command runs via `self._run` (`:790`).

`_segmented_music` (`:808`) mixes music into a video only inside `segments[]`, building a piecewise FFmpeg `volume='if(lt(t,s),0,if(...))'` expression per segment with linear fade-in/out ramps (`:853-868`), probing total video duration first via `ffprobe format=duration` (`:845-851`). The filter chain trims/loops the music (`-stream_loop -1` on the music input, `:881`), reformats both streams to `fltp/44100/stereo`, and `amix`es with `duration=first` so output length matches the video (`:870-876`). This op also calls **`self.run_command` directly** (`:891`), not `self._run` — same untrimmed-error inconsistency as `_duck`.

### Flow
```text
Agent call: {operation:"full_mix", tracks:[
  {path:"narr.mp3", role:"speech", start_seconds:0},
  {path:"music.mp3", role:"music", volume:0.3}],
  ducking:{enabled:true}, normalize:true}
        │
        ▼
execute()                              audio_mixer.py:280
        │  dispatch on operation                :285-296
        ▼
_full_mix()                                          :657
        ├─ validate tracks exist               :699-701
        ├─ group by role speech/music/sfx      :690-693
        ├─ per-track chain (_per_track_chain)  :912
        ▼
   ★ DIVERGENCE: duck_enabled and speech+music both present?  :714
        │                              │
       yes                             no
        │                              │
        ▼                              ▼
 [speech]→amix→asplit          simple amix of ALL
   [speech_key][speech_out]      tracks→[premix]     :769-774
        :721-729                        │
        ▼                               │
 [music]→amix→[music_all]  :732-740     │
        ▼                               │
 sidechaincompress                      │
 [music_all][speech_key]→[ducked_music] │
   attack/release ms, clamped   :742-756│
        ▼                               │
 volume=vol*3→[music_out]               │
        ▼                               │
 amix [speech_out][music_out]           │
   (+sfx)→[premix]           :758-767   │
        └──────────────┬─────────────────┘
                        ▼
        normalize? loudnorm I=-16:LRA=11:TP=-1.5  :778
                        ▼
   cmd=["ffmpeg","-y", -i×N, "-filter_complex",…,
        "-map","[out]", output_path]        :785-788
                        ▼
        self._run(cmd)→subprocess         :790, :1004
                        ▼
   ToolResult(output="full_mix_output.wav")
```

**Notes:**
- `_duck` (`:458`) and `_segmented_music` (`:891`) call `self.run_command` directly instead of the `self._run` wrapper the other four ops use — their ffmpeg failures surface as raw, untrimmed exception strings via `execute()`'s outer `except Exception` (`:301`), not the 500-char stderr tail `_run` produces elsewhere.
- `fade_out_seconds` requires a probeable duration (`_audio_duration` via ffprobe, `:954-967`); if the file can't be probed it raises `_MixInputError` (`:940-944`) rather than silently defaulting `st=0` (which the header docstring says was a previously-shipped bug: fading to silence over the first N seconds and staying muted).
- `auto_balance`'s `apply=true` path deliberately defaults `normalize=False` when delegating to `_mix` (`:649`) — running loudnorm after would erase the relative balance just computed.

---

## `audio_enhance`

**File:** `tools/audio/audio_enhance.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `core` |
| capability | `audio_processing` |
| provider | `ffmpeg` |
| runtime | `hybrid` (`ToolRuntime.HYBRID`, `audio_enhance.py:129`) — local ffmpeg modes OR the ElevenLabs API mode |
| determinism | `deterministic` |
| dependencies | `["cmd:ffmpeg"]` — `ELEVENLABS_API_KEY` is **deliberately not listed** (`:131-133`) so the tool isn't marked unavailable for its local modes when the key is missing |
| capabilities | `["noise_reduction", "normalization", "compression", "eq", "speech_cleanup", "de_essing", "voice_isolation"]` |
| best_for | `[]` (not set) |
| not_good_for | `["HDR sources — output is 8-bit SDR ...", "ai_isolate on music/full mixes — it extracts voice and discards everything else", "keeping the video stream through ai_isolate — that mode outputs audio only"]` |
| key input params | `input_path` (required), `output_path`, `mode` (`preset`\|`deess`\|`ai_isolate`, default `preset`), `preset` (one of 6 preset names, default `clean_speech`), `custom_af`, `intensity` (0-1, deess only, default 0.5), `audio_codec` (default `aac`), `audio_bitrate` (default `192k`) |

### What it actually executes
`get_status()` (`:212-218`) implements partial-availability logic: `AVAILABLE` if `shutil.which("ffmpeg")` succeeds **or** `ELEVENLABS_API_KEY` is set in the environment — either provider alone is enough. `estimate_cost()` (`:220-226`) only prices `mode="ai_isolate"`: it probes duration via `_probe_duration` (`ffprobe -show_entries format=duration`, `:360-371`, falling back to 60s if unprobeable) and multiplies minutes by `ISOLATION_COST_PER_MINUTE_USD = 0.30` (`:146`), floored at 1 second's worth.

`execute()` (`:228`) validates `input_path` exists and `mode` is one of `MODES` (`:236-238`), then branches:

- **`mode="ai_isolate"`** → `_ai_isolate()` (`:305`). Requires `ELEVENLABS_API_KEY`, else returns an error naming the env var while noting local modes still work (`:306-315`). Uploads the raw file as multipart form data to `POST https://api.elevenlabs.io/v1/audio-isolation` with header `xi-api-key`, `timeout=600` (`:326-334`), via the `requests` library (imported locally, `:317`) — **not** a subprocess call, unlike every other tool in this batch. The response bytes (mp3, voice-only — no video stream even for video input) are written straight to `output_path` (`:341-342`), and `cost_usd` on the returned `ToolResult` is computed by re-calling `estimate_cost` (`:354`).
- **`mode="deess"`** → validates `intensity` is a number in `[0,1]` (`:244-247`), builds the filter via `_deess_af(intensity)` (`:53-61`): `deesser=i=<intensity>:m=<0.5+intensity/2>:f=0.5` — `i` must always be set explicitly because ffmpeg's `deesser` defaults `i=0` (a no-op). Default output path is `{stem}_deessed`.
- **`mode="preset"` (default)** → uses `custom_af` verbatim if given, else looks up `PRESETS[preset_name]` (`:254-260`, default `clean_speech`). Default output path is `{stem}_enhanced`.

Either non-isolate branch then detects video-vs-audio by extension (`.mp4/.mkv/.avi/.mov/.webm`, `:272`) and runs `ffmpeg -y -i input -af <chain> [-c:v copy] -c:a <codec> -b:a <bitrate> output` via `self.run_command` (`:274-285`), with failures trimmed to the last 500 chars of stderr by `_trim_err` (`:373-378`).

The six `PRESETS` (`:64-117`) are all ffmpeg `-af` chains: `clean_speech` = `highpass=80,lowpass=13000,agate,acompressor,loudnorm(I=-16)`; `noise_reduce` = `afftdn=nf=-25:nt=w,highpass=100,loudnorm`; `normalize_only` = `loudnorm` alone; `podcast` = `highpass=80` + `_deess_af(0.4)` + `acompressor` + `loudnorm(LRA=7)`; `broadcast` = `highpass,lowpass,acompressor,alimiter,loudnorm(I=-24)`; `voice_clarity` = `highpass` + three `equalizer` bands (200Hz cut, 3kHz/5kHz boost) + `acompressor` + `loudnorm`.

### Flow
```text
Agent call: {input_path:"raw_vo.wav", mode:"preset",
             preset:"clean_speech"}
        │
        ▼
execute()                          audio_enhance.py:228
   ├─ validate input_path exists           :232-234
   ├─ validate mode in MODES               :236-238
        ▼
   ★ DIVERGENCE (mode)                          :240-260
        ├─ "ai_isolate" → _ai_isolate()          :305
        │     needs ELEVENLABS_API_KEY    :306-315
        │     requests.post(...isolation) :326-334
        │     write response.content bytes :341
        │     (audio-only mp3, no video stream)
        │
        ├─ "deess" → _deess_af(intensity)  :248,53-61
        │     "deesser=i=<i>:m=<0.5+i/2>:f=0.5"
        │
        └─ "preset" (this path, default)         :253-264
              custom_af, else PRESETS[name]  :254-260
              "highpass=80,lowpass=13000,
               agate=...,acompressor=...,
               loudnorm=I=-16:LRA=11:TP=-1.5"
        ▼
   is_video = suffix in {.mp4,.mkv,.avi,.mov,.webm}  :272
        ▼
   cmd=["ffmpeg","-y","-i",src,"-af",af,
        ("-c:v","copy" if is_video),
        "-c:a",codec,"-b:a",bitrate, out]     :274-282
        ▼
   self.run_command(cmd)                          :285
        ▼
   ToolResult(output="raw_vo_enhanced.wav",
              filter=af)
```

**Notes:**
- `ai_isolate` is the only op in this batch that hits a network API (`requests`, not ffmpeg) and returns audio-only output even for a video input — remuxing back onto the video is left to the caller (per the module docstring, use `audio_mixer`).
- `podcast`'s de-esser is fixed at `intensity=0.4` regardless of the `intensity` input param — that param only drives standalone `mode="deess"`.

---

## `voice_ops`

**File:** `tools/audio/voice_ops.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `core` |
| capability | `audio_processing` |
| provider | `ffmpeg` |
| runtime | `local` (`ToolRuntime.LOCAL`, `voice_ops.py:62`, explicit) |
| determinism | `deterministic` (module docstring notes `record` captures live/non-repeatable audio, but the declared enum value is still `deterministic`) |
| dependencies | `["cmd:ffmpeg"]` |
| capabilities | `["list_devices", "record", "effect", "insert"]` (= `OPERATIONS`) |
| best_for | `["record a voiceover take from the mic, voice-effect it, drop it on the timeline over ducked music"]` |
| not_good_for | `["HDR sources ...", "AI voice conversion / cloning — these are classic DSP effects, not ML voice transfer", "sidechain pumping aesthetics — insert ducking is a flat volume dip, not a compressor"]` |
| key input params | `operation` (required), `output_path`, `device`/`duration_seconds`(≤600)/`sample_rate` (record), `input_path`/`preset`(7 presets)/`pitch_semitones`(-12..12) (effect), `base_path`/`voice_path`/`at_seconds`/`duck_music`/`voice_volume` (insert), `asset_manifest_path`/`scene_id` (optional provenance) |

### What it actually executes
`execute()` (`:141`) validates `operation` and dispatches, catching `_OpInputError` (`:145-154`).

**`list_devices`** (`:158-176`) branches on `platform.system()`. On macOS it runs `ffmpeg -f avfoundation -list_devices true -i ""` via `_capture_stderr` (`:251-261`, which specifically catches `CalledProcessError` because ffmpeg exits non-zero by design when just listing devices) and parses the audio-device section with a `\[(\d+)\]\s+(.+?)\s*$` regex (`_parse_avfoundation_devices`, `:179-195`). On Windows it runs `ffmpeg -f dshow -list_devices true -i dummy` and parses `"name" (audio)` lines or legacy section-header format (`_parse_dshow_devices`, `:197-222`). On Linux it tries `pactl list short sources` (filtering out `.monitor` loopback sources, `:226-236`), falling back to `arecord -l` regex parsing (`:237-248`) if `pactl` is unavailable (`_linux_devices`, `:224-249`).

**`record`** (`:265`) validates `duration_seconds` is `>0` and `≤ RECORD_MAX_SECONDS` (600s, `:266-270`) and `sample_rate` is an int in `[8000, 192000]` (`:271-273`). It builds the mic-capture command via the pure, separately-testable `_record_cmd` classmethod (`:306-341`): macOS → `-f avfoundation -i :0` (bare index/name gets a leading `:` prefix, `:316-322`); Windows → `-f dshow -i audio=<name>` (**requires** an explicit device name from `list_devices`, raises otherwise, `:323-331`); Linux → `-f pulse -i default` or `-f alsa -i hw:...`/`plughw:...` if the device string uses those prefixes (`:332-335`); all platforms append `-t <duration> -ac 1 -ar <sample_rate> <output_path>` (`:336-341`). Runs via `self._run` (`:283`, wrapper at `:584-594`) with `timeout=duration+60`; failures are wrapped with a mic-permission hint (`:285-291`); verifies non-empty output afterward (`:292-293`).

**`effect`** (`:345`) requires exactly one of `preset`/`pitch_semitones` (`:350-352`), checks the input has an audio stream via `_has_audio`→`_has_stream` (ffprobe `-select_streams a`, `:596-613`), detects video presence, and probes sample rate (`_probe_sample_rate`, `:629-641`, defaults 48000 if unprobeable). It builds one filter chain via `_preset_chain` (`:393-415`) or `_pitch_chain` (`:417-422`):
- `helium`: `asetrate=sr*1.35,aresample=sr,<atempo chain for 1/1.35>`
- `deep`: `asetrate=sr*0.8,aresample=sr,<atempo chain for 1/0.8>`
- `robot`: `afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75` (classic ffmpeg-docs phase-zero robot voice)
- `alien`: `_pitch_chain(3.0 semitones)` + `,vibrato=f=6:d=0.5`
- `echo`: `aecho=0.8:0.7:60|120:0.4|0.2`
- `telephone`: `highpass=f=300,lowpass=f=3400,acrusher=bits=10:mode=log:aa=1`
- `whisper`: `afftdn=nf=-25,highpass=f=150,treble=g=4,volume=0.5`
- custom `pitch_semitones`: `factor = 2**(n/12)`, then `asetrate=sr*factor,aresample=sr,<atempo(1/factor)>`

`_atempo_chain` (`:424-436`) exists because ffmpeg's `atempo` filter only accepts a `0.5–2.0` ratio per instance — it chains multiple `atempo=2.0`/`atempo=0.5` stages to cover wider factors. The final cmd is `ffmpeg -y -i src -af <chain> [-c:v copy] out` (`:380-383`), run via `self._run` (`:384`), then finished through `_finish`/`_register_asset` (`:387`, `:514-538`, `:540-580`) which optionally appends the derived file to an `asset_manifest` and validates it against `schemas.artifacts.validate_artifact`.

**`insert`** (`:440`) requires `base_path`+`voice_path`, `at_seconds ≥ 0` and not past the probed base duration (`:462-466`), `voice_volume > 0`. It builds a filter graph where the "bed" is either the base audio ducked to a flat `DUCK_LEVEL=0.3` only during the voice window — `volume=enable='between(t,{at},{duck_end})':volume=0.3` (`:474-479`) — or passed through untouched (`duck_music=false`), or, if the base has no audio at all, a generated `anullsrc` silence bed sized to the base duration (`:483-486`). The voice track is delayed with `adelay={ms}:all=1` and gain-scaled (`:488`), then combined via `amix=inputs=2:duration=first:dropout_transition=0:normalize=0` (`:489`) — `duration=first` means the output is bounded to the **base**'s length, silently truncating any voice tail that runs past it (documented as by-design in the module docstring). Runs via `self._run` (`:503`), then `_finish` (`:506`).

### Flow
```text
Agent call: {operation:"effect", input_path:"take1.wav",
             preset:"helium"}
        │
        ▼
execute()                                voice_ops.py:141
        ▼
   ★ DIVERGENCE (operation)                       :145-152
        ├─ list_devices → _list_devices()            :158
        ├─ record       → _record()                  :265
        ├─ effect       → _effect()  (this path)      :345
        └─ insert       → _insert()                  :440
        ▼
_effect()                                            :345
   ├─ exactly one of preset/pitch_semitones   :350-352
   ├─ _has_audio() ffprobe -select_streams a  :368,602
   ├─ _has_video(), _probe_sample_rate()      :370-371
        ▼
   ★ DIVERGENCE (preset vs pitch_semitones)          :372
        ├─ preset="helium" → _preset_chain()   :394-398
        │    "asetrate=sr*1.35,aresample=sr,
        │     atempo=...(1/1.35, chained)"
        └─ pitch_semitones=n → _pitch_chain()  :417-422
             factor=2^(n/12); asetrate/atempo
        ▼
   cmd=["ffmpeg","-y","-i",src,"-af",chain,
        ("-c:v","copy" if has_video), out]    :380-383
        ▼
   self._run(cmd) → run_command              :384, :584
        ▼
   _finish() → probe duration, optional
     asset_manifest registration        :387, :514, :540
        ▼
   ToolResult(output="take1_voice_helium.wav")
```

**Notes:**
- `record` is explicitly non-testable end-to-end (needs a live mic + OS permission grant); the suite only exercises the pure `_record_cmd` command-builder (`:306-341`), and the agent is required to warn the user before starting a capture (`side_effects`, `:130-131`).
- `insert`'s ducking is a flat volume dip during the voice window, not sidechain compression (unlike `audio_mixer`'s `duck`/`full_mix`) — deliberate, per `not_good_for`, to avoid pumping artifacts at the cost of the pumping aesthetic; and it truncates any voice tail past the base's end because of `amix duration=first` (`:489`).

---

# Analysis / inspection (editing-support)

## `frame_sampler`

**File:** `tools/analysis/frame_sampler.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `core` (`ToolTier.CORE`, `:30`) |
| capability | `analysis` (`:31`) |
| provider | `ffmpeg` (`:32`) |
| runtime | not set on class → defaults to `ToolRuntime.LOCAL` (`base_tool.py:158`) |
| determinism | `deterministic` (`:35`) |
| dependencies | `["cmd:ffmpeg"]` (`:37`) |
| capabilities | `extract_frames_interval`, `extract_frames_count`, `extract_frames_timestamps`, `extract_frames_scene_guided` (`:43-48`) |
| best_for | not set → `[]` (base default) |
| not_good_for | not set → `[]` (base default) |
| key input params | `input_path`, `strategy` (`interval`\|`count`\|`timestamps`\|`scene_guided`), `interval_seconds`, `count`, `timestamps[]`, `scene_boundaries[]`, `max_frames` (default 20), `output_dir`, `format` (`png`\|`jpg`), `quality` (1-31) (`:50-95`) |

### What it actually executes
`execute()` (`frame_sampler.py:102-141`) checks `input_path.exists()` (`:104`), creates `output_dir` (`:110-111`), and dispatches on `strategy` to one of four private methods (`:116-125`), then wraps the returned frame list in a `ToolResult` with `frame_count`, `frames`, `output_dir` (`:131-141`).

- `_extract_interval` (`:143-165`): single `ffmpeg -y -i <input> -vf fps=1/<interval_seconds> [-qscale:v <q>] frame_%04d.<fmt>` call via `self.run_command` (`:163`), then `_collect_frames` globs the pattern back off disk.
- `_extract_count` (`:167-195`): first calls `_get_duration` (`:279-290`, an `ffprobe -show_entries format=duration -of json` call) to compute `interval = duration/count`, then runs the same `fps=1/interval` ffmpeg filter but caps output with `-frames:v <count>` (`:183-193`).
- `_extract_timestamps` (`:197-229`): loops per timestamp and runs one `ffmpeg -y -ss <ts> -i <input> -frames:v 1 [-qscale:v <q>] frame_000i.<fmt>` per entry (`:210-220`) — N separate ffmpeg subprocess invocations, one seek+grab each.
- `_extract_scene_guided` (`:231-277`): if `scene_boundaries` is empty, falls back to `_extract_count(count=min(max_frames,15))` (`:248-252`). Otherwise it computes `start+0.1` (first frame, offset to dodge black frames) plus a midpoint `start+duration/2` for any scene longer than 3s (`:261-266`), dedupes/sorts/caps the list to `max_frames` (`:269-272`), and hands the final timestamp list to `_extract_timestamps` (`:275-277`).

### Flow
```text
agent call
 frame_sampler.execute({input_path:"clip.mp4",
   strategy:"scene_guided", scene_boundaries:[...],
   max_frames:20})                              :102
        │
        ▼
 input_path.exists()? ─ no → ToolResult(False)   :104
        │ yes
        ▼
 dispatch on inputs["strategy"]                  :116-125
        │
        ★ DIVERGENCE (strategy)
        ├─ "interval"    → _extract_interval()   :117,143
        ├─ "count"       → _extract_count()      :119,167
        ├─ "timestamps"  → _extract_timestamps()  :121,197
        └─ "scene_guided"→ _extract_scene_guided():123,231
                 │
                 ▼
     scene_boundaries provided?                  :248
        ★ DIVERGENCE
        ├─ no  → _extract_count(count=min(15,    :250-252
        │        max_frames)) — uniform fallback
        └─ yes ▼
     per scene: ts=start+0.1, +midpoint           :262-266
               if duration > 3.0s
     dedupe/sort/cap to max_frames                :269-272
                 │
                 ▼
     _extract_timestamps(timestamps=[...])        :275
                 │
        per ts: ffmpeg -y -ss {ts} -i clip.mp4    :210-218
                -frames:v 1 -qscale:v {q}
                frame_000i.jpg
                (self.run_command, one subprocess
                 per timestamp)
                 │
                 ▼
     ToolResult(success=True, data={strategy,
       frame_count, frames:[{path, timestamp_
       seconds, index}...], output_dir})          :131-141
```

**Notes:**
- `scene_guided` never runs ffmpeg's scene filter itself — it only geometrically derives timestamps from a caller-supplied `scene_boundaries` list (presumably from `scene_detect`) and reuses the plain per-timestamp extraction path.
- `timestamps`/`scene_guided` strategies cost one ffmpeg process per frame (no batching), while `interval`/`count` do it in a single ffmpeg call — a real perf cliff for large `max_frames`.

---

## `scene_detect`

**File:** `tools/analysis/scene_detect.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `core` (`:29`) |
| capability | `analysis` (`:30`) |
| provider | `ffmpeg` (`:31`) |
| runtime | not set → defaults to `ToolRuntime.LOCAL` |
| determinism | `deterministic` (`:34`) |
| dependencies | `["cmd:ffmpeg"]` (`:36`) — PySceneDetect is optional/undeclared |
| capabilities | `detect_scenes`, `detect_content_changes`, `detect_threshold` (`:43-47`) |
| best_for | not set → `[]` |
| not_good_for | not set → `[]` |
| key input params | `input_path`, `method` (`content`\|`threshold`\|`adaptive`, default `content`), `threshold`, `min_scene_length_seconds` (default 1.0), `output_path` (`:49-70`) |

### What it actually executes
`execute()` (`:86-117`) checks the input exists, then branches on `self._has_pyscenedetect()` (`:79-84`, a bare `import scenedetect` try/except) to pick `_detect_pyscenedetect` or `_detect_ffmpeg` (`:93-96`), writes the scene list to `output_path` (default `<input>.scenes.json`, `:101-105`), and returns scene count/list/method.

- `_detect_pyscenedetect` (`:119-165`): `open_video(input_path)` then a `SceneManager` with `ContentDetector`/`ThresholdDetector`/`AdaptiveDetector` chosen by `method`, defaults `27.0`/`12.0`/`3.0` respectively, `min_scene_len = min_scene_length_seconds * video.frame_rate` frames (`:132-146`). Runs `scene_manager.detect_scenes(video)` then `get_scene_list()` (`:150-152`), converting `FrameTimecode` pairs to `start_seconds`/`end_seconds`/`duration_seconds` (`:154-163`).
- `_detect_ffmpeg` (`:167-216`) — first fallback tier: builds an `ffprobe -show_entries frame=pts_time -of json -f lavfi movie='<path>',select='gt(scene,<threshold>)'` call (with manual `\` and `:` escaping for the lavfi `movie` filter, `:179`) and runs it via `self.run_command(cmd, timeout=120)` (`:183`). Parses `pts_time` entries into change points gated by `min_scene_len` (`:189-193`), gets total duration with a second plain `ffprobe -show_entries format=duration` call (`:196-202`), and turns the change points into scene dicts.
- `_detect_ffmpeg_simple` (`:218-262`) — second fallback tier, used only if the lavfi `ffprobe` call throws (`:186-187`): probes duration the same way, then runs `ffmpeg -i <input> -vf select='gt(scene,<threshold>)',showinfo -f null -` (`:231-236`) and regex-parses `pts_time:(\d+\.?\d*)` out of stderr (`:243-249`) to get change points.

### Flow
```text
agent call
 scene_detect.execute({input_path:"clip.mp4",
   method:"content"})                          :86
        │
        ▼
 input_path.exists()? ─ no → fail               :88-89
        │ yes
        ▼
 self._has_pyscenedetect() = try "import         :79-84
   scenedetect"
        │
        ★ DIVERGENCE (installed?)
        ├─ YES → _detect_pyscenedetect()          :94,119
        │   open_video() + SceneManager +
        │   ContentDetector(threshold=27.0)        :129-151
        │   → get_scene_list()
        │
        └─ NO  → _detect_ffmpeg()                  :96,167
             ffprobe -f lavfi movie='clip.mp4',      :173-183
               select='gt(scene,0.3)' -show_entries
               frame=pts_time  (run_command,120s)
                    │
             ★ DIVERGENCE (lavfi call raises?)
             ├─ no  → parse pts_time, ffprobe          :189-202
             │        format=duration for total dur
             └─ yes → _detect_ffmpeg_simple()           :186-187,218
                      ffmpeg -vf select=...,showinfo     :231-236
                      -f null -  → regex stderr for
                      "pts_time:" (243-249)
                 │
                 ▼
 write {"scenes":[...]} → <input>.scenes.json     :100-105
                 │
                 ▼
 ToolResult(scene_count, scenes:[{index,
   start_seconds, end_seconds,
   duration_seconds}...], method, output)         :107-117
```

**Notes:**
- The declared `dependencies` (`cmd:ffmpeg` only) understates the real capability tiers — PySceneDetect isn't listed as a dependency at all, so `check_dependencies()` never surfaces it as missing; only `install_instructions` mentions `pip install scenedetect[opencv]` (`:37-40`).
- Three-deep fallback chain (PySceneDetect → ffprobe lavfi → ffmpeg showinfo regex) means detection quality/precision silently degrades per environment with no signal returned to the caller other than `"method"` in the output.

---

## `visual_qa`

**File:** `tools/analysis/visual_qa.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `core` (`:32`) |
| capability | `analysis` (`:33`) |
| provider | `ffmpeg` (`:34`) |
| runtime | not set → defaults to `ToolRuntime.LOCAL` |
| determinism | `deterministic` (`:37`) |
| dependencies | `["cmd:ffmpeg", "cmd:ffprobe"]` (`:39`) |
| capabilities | `extract_review_frames`, `probe_video`, `check_audio_levels` (`:43-47`) |
| best_for | not set → `[]` |
| not_good_for | not set → `[]` |
| key input params | `operation` (`review`\|`probe`\|`audio_levels`), `input_path`, `timestamps[]`, `output_dir`, `checks[]`, `expected` (`width`,`height`,`min_duration`,`max_duration`,`pixel_format`,`has_audio`) (`:49-112`) |

### What it actually executes
`execute()` (`:121-143`) validates the input exists then dispatches on `operation` (`:131-138`), stamping `duration_seconds` on whatever `ToolResult` comes back.

- `_review` (`:145-201`): if no `timestamps` given, auto-generates 5 (`1.0s`, 25%, 50%, 75%, `duration-1.0s`) via `_get_duration` (`:151-159`). For each timestamp it runs `ffmpeg -y -ss <ts> -i <input> -frames:v 1 -q:v 2 <frame_path>` (`:170-177`) — one subprocess per frame — and records per-frame success/error.
- `_probe` (`:203-287`) is the resolution/duration/codec check: a single `ffprobe -v error -show_entries format=duration,size:stream=width,height,codec_name,pix_fmt,r_frame_rate,sample_rate,channels,codec_type -of json <input>` (`:209-216`). It picks the first `video`/`audio` stream (`:223-229`), builds an `info` dict (width/height/pixel_format/video_codec/frame_rate/audio_codec/sample_rate/channels/duration/file_size_mb/has_audio, `:231-251`), and validates it against the caller's `expected` dict field-by-field into an `issues` list, setting `validation_passed = len(issues)==0` (`:254-278`).
- `_audio_levels` (`:289-336`): if no timestamps, auto-generates 3 (`1.0s`, 50%, `duration-2.0s`, `:294-296`). For each it runs `ffmpeg -y -ss <ts> -t 3 -i <input> -vn -af volumedetect -f null /dev/null` (or `NUL` on Windows, `:300-307`) and regex-scrapes `mean_volume`/`max_volume` out of stderr (`:313-317`).
- `_get_duration` (`:338-346`) is its own private ffprobe helper (`-of csv=p=0`), separate from `frame_sampler`'s and `audio_probe`'s duration helpers.

### Flow
```text
agent call
 visual_qa.execute({operation:"probe",
  input_path:"clip.mp4",
  expected:{width:1080,height:1920,
            has_audio:true}})                :121
        │
        ▼
 Path(input_path).exists()? ─ no → fail        :125-126
        │ yes
        ▼
 dispatch on operation                          :131-138
        │
        ★ DIVERGENCE (operation)
        ├─ "review" → _review()                  :132,145
        │   per ts: ffmpeg -ss ts -frames:v 1     :170-179
        ├─ "audio_levels" → _audio_levels()       :135,289
        │   per ts: ffmpeg -af volumedetect,       :300-317
        │   parse stderr mean/max_volume
        └─ "probe" → _probe()                     :133,203
                 │
                 ▼
     ffprobe -show_entries format=duration,size:  :209-216
       stream=width,height,codec_name,pix_fmt,
       r_frame_rate,sample_rate,channels,
       codec_type -of json
                 │
                 ▼
     find first video_stream / audio_stream        :223-229
     build info{width,height,pixel_format,
       video_codec,duration,has_audio,...}          :231-251
                 │
                 ▼
     compare info vs expected{width,height,
       min/max_duration,pixel_format,has_audio}      :254-275
     → issues:list[str]
                 │
                 ▼
     ToolResult(data={operation:"probe", ...info,
       validation_issues, validation_passed})         :277-287
```

**Notes:**
- The module docstring (`:6-7`) advertises a "caption occlusion check (brightness in face vs caption zones)" and "transition verification (frame similarity)", but `grep` across the file finds no code implementing either — only `review`/`probe`/`audio_levels` exist. That's aspirational documentation, not shipped behavior.
- `_review` and `_audio_levels` both shell out once per timestamp (no batching), same N-subprocess cost pattern as `frame_sampler._extract_timestamps`.

---

## `audio_energy`

**File:** `tools/analysis/audio_energy.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `core` (`:41`) |
| capability | `analysis` (`:42`) |
| provider | `ffmpeg` (`:43`) |
| runtime | `local` (`ToolRuntime.LOCAL`, explicit, `:47`) |
| determinism | `deterministic` (`:46`) |
| dependencies | `["binary:ffmpeg"]` (`:49`) — non-standard prefix, see notes |
| capabilities | `find_music_offset`, `energy_profile`, `best_window`, `loop_recommendation` (`:57-62`) |
| best_for | "finding where ambient music gets interesting…", "choosing the best offset…", "determining if a music track needs looping…" (`:63-67`) |
| not_good_for | not set → `[]` |
| key input params | `input_path`, `video_duration_seconds` (optional), `energy_threshold_lufs` (default `-40`) (`:69-90`) |

### What it actually executes
`execute()` (`:107-303`) checks the file exists and resolves `ffmpeg`/`ffprobe` via `shutil.which` (`:112-126`) — it does **not** use `self.run_command`; every subprocess call here is a direct `subprocess.run`.

1. **Duration probe** (`:128-139`): `ffprobe -v quiet -print_format json -show_format <input>`, parses `format.duration`.
2. **Loudness analysis** (`:141-156`): `ffmpeg -i <input> -af ebur128 -f null -`, timeout 120s. `ebur128` prints momentary loudness (`M:`) roughly every 100ms.
3. **Parse** (`:158-176`): regex `t:\s*([\d.]+)\s+.*?M:\s*(-?[\d.]+)` over stderr lines into raw `(t, M_lufs)` points; fails the tool if nothing matched.
4. **Downsample to 1s** (`:178-200`): for each integer second, averages all `M` points landing in `[sec, sec+1)` that aren't the `-120` silence marker, then flags `active = avg_lufs > threshold_lufs`. (Note: the module docstring says the profile is "at 100ms intervals" — the returned `energy_profile` is actually 1-second buckets, an average of the underlying 100ms samples.)
5. **Key moments** (`:202-220`): `first_active_sec` = first second where `active` is true; `peak_sec`/`peak_lufs` = the loudest non-silent second.
6. **Best window** (`:222-253`): if `video_duration_seconds` is given and shorter than the track, slides a `window_size`-second window over the per-second loudness array and picks the highest-average start — a straightforward O(n·w) scan, not FFT/DSP.
7. **Loop recommendation** (`:255-276`): if `audio_duration - recommended_offset < video_duration`, sets `needs_loop=True` with a shortfall figure.

### Flow
```text
agent call
 audio_energy.execute({input_path:"track.mp3",
  video_duration_seconds:30,
  energy_threshold_lufs:-40})                :107
        │
        ▼
 input_path.exists()? / which(ffmpeg,ffprobe)  :108-126
        │ ok
        ▼
 subprocess: ffprobe -show_format              :128-139
   → audio_duration
        │
        ▼
 subprocess: ffmpeg -af ebur128 -f null -       :145-156
   (120s timeout) → stderr full of
   "t: 0.0999773 ... M:-120.7 ..." lines
        │
        ▼
 regex "t:\s*(...)\s+.*?M:\s*(-?...)"          :162-170
   → raw_points[(t, M_lufs), ...]
        │
        ▼
 downsample to 1s buckets, avg per sec,         :180-200
   active = avg_lufs > threshold_lufs
        │
        ▼
 first_active_sec, peak_sec/peak_lufs           :205-220
        │
        ▼
 video_duration given & < audio_duration?       :231
        ★ DIVERGENCE
        ├─ no  → recommended_offset =            :225-229
        │        first_active_sec
        └─ yes → slide window_size-sec window,   :232-253
                 pick max-avg start
        │
        ▼
 needs_loop check: audio_duration -              :258-276
   recommended_offset < video_duration?
        │
        ▼
 ToolResult(data={audio_duration_seconds,
   analysis:{threshold_lufs, active_seconds,
   quiet_intro_seconds, peak_loudness_...},
   recommended_offset_seconds, offset_reason,
   needs_loop, loop_info, energy_profile:[...]}) :281-303
```

**Notes:**
- `dependencies = ["binary:ffmpeg"]` uses a `binary:` prefix that `BaseTool.check_dependencies()` doesn't recognize (it only special-cases `cmd:`/`env:`/`python:`, `base_tool.py:221-234`) — so the declared dependency is a silent no-op there; the tool compensates with its own `get_status()` override (`:99-102`) that does the real `shutil.which("ffmpeg")` check.
- The "100ms intervals" claim in the docstring (`:3`) describes the raw `ebur128` sampling rate, not the granularity of the returned `energy_profile`, which is averaged down to 1-second buckets before being handed back to the agent.

---

## `audio_probe`

**File:** `tools/analysis/audio_probe.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `core` (`:62`) |
| capability | `analysis` (`:63`) |
| provider | `ffprobe` (`:64`) |
| runtime | `local` (explicit, `:68`) |
| determinism | `deterministic` (`:67`) |
| dependencies | `["binary:ffprobe"]` (`:70`) — same non-standard prefix as `audio_energy` |
| capabilities | `probe_duration`, `probe_format`, `probe_streams` (`:78`) |
| best_for | "getting audio/video duration before composition", "validating media file format and codec", "pre-render checks on asset files" (`:79-83`) |
| not_good_for | not set → `[]` |
| key input params | `input_path` only (`:85-94`) |

### What it actually executes
This is the lightest of the family — a single `ffprobe` call, no fallback tiers. `execute()` (`:111-178`) checks the file exists, resolves `ffprobe` via `shutil.which` (`:116-118`), and runs (again via direct `subprocess.run`, not `self.run_command`):

```
ffprobe -v quiet -print_format json -show_format -show_streams <input_path>
```
(`:122-135`, 15s timeout). It handles a non-zero return code (`:137-141`), a `TimeoutExpired` (`:144-145`), and a `JSONDecodeError` (`:146-147`) as distinct failure paths. It then finds the **first** stream with `codec_type == "audio"` (`:153`) and builds a flat `probe_data` dict: `duration_seconds`, `format_name`, `format_long_name`, `size_bytes`, `bit_rate`, `stream_count` (`:155-163`), plus a nested `audio` sub-dict (codec/sample_rate/channels/channel_layout/bit_rate) only if an audio stream was found (`:165-172`). Unlike `visual_qa._probe`, it never extracts video-stream fields (width/height/codec) even though its docstring calls it an "audio/video file probe" — video streams only count toward `stream_count`.

The file also exports a standalone module-level helper, `probe_duration(file_path)` (`:31-56`), independent of the `AudioProbe` class — it runs its own minimal `ffprobe -show_format` and returns just a `float` duration or `None` on any exception. This is the exact function `composition_validator.py` imports (`composition_validator.py:21`) to check narration/music duration without going through the full tool `execute()` path.

### Flow
```text
agent call
 audio_probe.execute({input_path:"clip.mp4"})   :111
        │
        ▼
 input_path.exists()? / which("ffprobe")         :112-118
        │ ok
        ▼
 subprocess.run(["ffprobe","-v","quiet",          :122-135
   "-print_format","json","-show_format",
   "-show_streams", input_path], timeout=15)
        │
        ★ DIVERGENCE (result)
        ├─ returncode != 0  → fail "ffprobe failed"  :137-141
        ├─ TimeoutExpired   → fail "timed out (15s)"  :144-145
        ├─ JSONDecodeError  → fail "invalid JSON"      :146-147
        └─ ok ▼
     find first stream where codec_type=="audio"   :153
                 │
                 ▼
     probe_data = {file, duration_seconds,
       format_name, format_long_name, size_bytes,
       bit_rate, stream_count}                       :155-163
     if audio_stream: probe_data["audio"] = {codec,
       sample_rate, channels, channel_layout,
       bit_rate}                                      :165-172
                 │
                 ▼
     ToolResult(success=True, data=probe_data)         :174-178

  (separate helper, no ToolResult, used by other
   tools) probe_duration(path) → ffprobe -show_format
   → float duration | None                              :31-56
```

**Notes:**
- Same `binary:ffprobe` dependency-string quirk as `audio_energy` — bypassed by its own `get_status()` override (`:103-106`) that calls `shutil.which` directly.
- `probe_duration()` is the shared duration-check primitive reused by `composition_validator` — it fails soft (returns `None`) rather than raising, so callers must handle the `None` case explicitly (see `composition_validator.py:219-220`).

---

## `face_tracker`

**File:** `tools/analysis/face_tracker.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `core` (`:32`) |
| capability | `analysis` (`:33`) |
| provider | `mediapipe` (`:34`) |
| runtime | not set → defaults to `ToolRuntime.LOCAL` |
| determinism | `deterministic` (`:37`) |
| dependencies | `["cmd:ffmpeg"]` (`:39`) — misleading, see notes |
| capabilities | `face_detection`, `face_tracking`, `face_bounding_box`, `head_pose_estimation` (`:47-52`) |
| best_for | not set → `[]` |
| not_good_for | not set → `[]` |
| key input params | `input_path`, `output_path`, `sample_fps` (default 5), `min_detection_confidence` (default 0.5) (`:54-75`) |

### What it actually executes
`execute()` (`:138-182`) requires the input to exist and **hard-requires OpenCV** (`_has_opencv()`, `:124-129, 143-147` — fails outright if missing). It then checks `_has_mediapipe()` (`:117-122`, bare `import mediapipe` try/except) to choose `_track_mediapipe` or the degraded `_track_opencv` (`:159-162`), writes the result JSON to `output_path` (default `<input>.faces.json`, `:149-152, 166`), and returns a summary (`video_width`, `video_height`, `fps`, `duration_seconds`, `frames_sampled`, `faces_detected`, `method`, `:168-182`). `get_status()` (`:131-136`) reports `AVAILABLE`/`DEGRADED`/`UNAVAILABLE` based on the real mediapipe+opencv import checks — a more accurate signal than the declared `dependencies` list.

- `_track_mediapipe` (`:184-251`): opens the video with `cv2.VideoCapture(str(input_path))` directly (`:191`) — **no ffmpeg subprocess is ever spawned here**, despite `dependencies = ["cmd:ffmpeg"]`. Reads `fps`/`width`/`height`/`frame_count` off the capture (`:193-197`), computes `sample_interval = max(1, int(video_fps/sample_fps))` (`:200`), then loops `cap.read()` for **every** frame in the file but only runs `mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=confidence).process(rgb)` on frames where `frame_idx % sample_interval == 0` (`:206-239`). For each sampled frame it keeps the single highest-confidence detection and records its normalized (`0..1`) `relative_bounding_box` as `{x, y, width, height}` (`:222-236`).
- `_track_opencv` (`:253-314`): same sampling loop, but loads `cv2.data.haarcascades/haarcascade_frontalface_default.xml` and calls `cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60,60))` (`:280-283`), picking the largest-area box if several are found and hardcoding `confidence=0.0` since Haar doesn't provide one (`:287-293`).

### Flow
```text
agent call
 face_tracker.execute({input_path:"speaker.mp4",
  sample_fps:5, min_detection_confidence:0.5})  :138
        │
        ▼
 input_path.exists()?                            :140-141
        │ yes
        ▼
 _has_opencv()? ─ no → fail (hard requirement)    :143-147
        │ yes
        ▼
 _has_mediapipe()? (try "import mediapipe")       :117-122,159
        │
        ★ DIVERGENCE (installed?)
        ├─ YES → _track_mediapipe()                :160,184
        └─ NO  → _track_opencv() (Haar cascade)     :162,253
                 │
     cv2.VideoCapture(input_path) — NOT ffmpeg      :191
     fps/width/height/frame_count via cap.get()      :193-197
     sample_interval=max(1,int(fps/sample_fps))       :200
                 │
     loop cap.read() over EVERY frame;                :210-239
     process() only on frame_idx % interval==0
       mp_face.FaceDetection(model_selection=1,        :206-209
         min_detection_confidence=0.5)
       → pick max-confidence detection                :222-225
       → bbox = relative_bounding_box (0..1 coords)     :226-236
                 │
                 ▼
     write faces JSON → <input>.faces.json             :166
                 │
                 ▼
     ToolResult(data={video_width,video_height,fps,
       duration_seconds, frames_sampled,
       faces_detected, method:"mediapipe"|
       "opencv_haar"})                                  :168-182
```

**Notes:**
- The task description ("ffmpeg frame extraction feeds it") doesn't match the code: `face_tracker` never shells out to ffmpeg; it decodes the video itself via `cv2.VideoCapture`, and the declared `cmd:ffmpeg` dependency is effectively dead weight (probably copy-pasted from the other analysis tools).
- `head_pose_estimation` is advertised in `capabilities` (`:51`) but neither `_track_mediapipe` nor `_track_opencv` computes any pose/rotation value — output is bounding box + confidence only.
- Every frame is decoded (`cap.read()` runs unconditionally each loop iteration) even though detection only runs on sampled frames — a real perf cost on long videos at low `sample_fps`.

---

## `composition_validator`

**File:** `tools/analysis/composition_validator.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `core` (`:38`) |
| capability | `analysis` (`:39`) |
| provider | `local` (`:40`) |
| runtime | `local` (explicit, `:44`) |
| determinism | `deterministic` (`:43`) |
| dependencies | `["binary:ffprobe"]` (`:46`) — non-standard prefix, and see notes on `get_status` |
| capabilities | `validate_composition`, `pre_render_check` (`:49`) |
| best_for | "catching audio-video duration mismatches before render", "verifying all referenced assets exist", "pre-flight check before expensive render operations" (`:50-54`) |
| not_good_for | not set → `[]` |
| key input params | `composition_path` (required), `assets_root` (optional override), `render_runtime` (`remotion`\|`hyperframes`\|`ffmpeg`) (`:56-82`) |

### What it actually executes
This is the only tool of the seven with `provider = "local"` and no ffmpeg/ffprobe subprocess of its own — it validates a JSON **edit-decisions/composition document**, not a media file directly, delegating the one piece of real media inspection it needs (audio duration) to `audio_probe.probe_duration()` (imported at `:21`).

`execute()` (`:95-243`) reads `composition_path` as JSON (`:102-105`), then resolves `assets_root`: an explicit input wins; otherwise it dispatches on `render_runtime` (`:108-150`) — for `hyperframes` it walks up to 5 parent directories looking for a `hyperframes/assets` folder or a sibling `assets/`+`index.html` pair (`:119-135`); for `ffmpeg` it just uses the composition file's parent directory (`:136-139`); the default (`remotion`) walks up looking for `remotion-composer/public` (`:141-150`).

It then runs seven checks against the parsed JSON:
1. **Cuts exist** (`:159-162`) — hard error + early return if `cuts` is empty.
2. **Video duration** (`:164-170`) = `max(cut.out_seconds for cut in cuts)`.
3. **Cut ordering** (`:173-181`) — sorts cuts by `in_seconds`, flags any cut where `out_seconds <= in_seconds`. (Note: despite the docstring mentioning "overlapping" cuts, there's no check that adjacent cuts' ranges don't overlap — only the single-cut ordering invariant is enforced.)
4. **Asset files exist** (`:183-195`) — checks `cut.source` and `cut.backgroundImage` resolve to real files under `assets_root`.
5. **Narration vs video duration** (`:197-220`) — resolves `audio.narration.src`, checks it exists, calls `probe_duration()` for its length; overshoot `>1.0s` is an error ("audio will be cut off"), `0-1.0s` is a warning.
6. **Music duration** (`:222-237`) — same asset-exists + `probe_duration()` pattern, but only warns if music is shorter than the video (never errors).
7. **No audio at all** (`:239-241`) — warns if neither `narration` nor `music` is configured.

`_result()` (`:245-275`) rolls everything into `ToolResult(success = len(errors)==0, data={valid, errors, warnings, info, error_count, warning_count})`.

### Flow
```text
agent call
 composition_validator.execute({
  composition_path:"edit_decisions.json",
  render_runtime:"hyperframes"})               :95
        │
        ▼
 comp_path.exists()? → json.loads(comp_path)     :96-105
        │
        ▼
 resolve assets_root (explicit wins)              :108-116
        ★ DIVERGENCE (render_runtime)
        ├─ "hyperframes" → walk parents for         :119-135
        │    hyperframes/assets or assets/+
        │    index.html (max 5 levels up)
        ├─ "ffmpeg"      → comp_path.parent          :136-139
        └─ default (remotion) → walk parents for      :141-150
             remotion-composer/public
        │
        ▼
 Check1: cuts non-empty? → no: error, return       :159-162
 Check2: video_duration = max(out_seconds)          :164-170
 Check3: sorted by in_seconds; out<=in → error       :173-181
 Check4: cut.source / backgroundImage exist under     :183-195
   assets_root → missing: error
 Check5: audio.narration.src exists,                   :197-220
   probe_duration(narration_path)                       (audio_probe.py:31)
   overshoot>1.0s → error | 0-1.0s → warning
 Check6: audio.music.src exists,                        :222-237
   probe_duration(music_path)
   music_dur < video_duration → warning only
 Check7: no narration & no music → warning               :239-241
        │
        ▼
 ToolResult(success=(errors==0), data={valid,
   errors, warnings, info, error_count,
   warning_count})                                       :245-275
```

**Notes:**
- `get_status()` unconditionally returns `AVAILABLE` (`:89-90`) even though the tool depends on `ffprobe` via `probe_duration()` — unlike `audio_energy`/`audio_probe`, it doesn't verify `ffprobe` is actually on `PATH` before claiming availability.
- The narration/music duration checks silently skip validation if `probe_duration()` returns `None` (i.e., ffprobe missing or the file is unreadable) — narration only downgrades to a warning ("Could not probe narration duration", `:220`), music duration silently produces no info/warning at all in that case.

---

# Screen capture

## `screen_recorder`

**File:** `tools/capture/screen_recorder.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `ToolTier.SOURCE` (`screen_recorder.py:83`) |
| capability | `"screen_capture"` (`:84`) |
| provider | `"ffmpeg"` (`:85`) |
| runtime | `ToolRuntime.LOCAL` (`:89`) |
| determinism | `Determinism.DETERMINISTIC` (`:88`) |
| dependencies | `["binary:ffmpeg"]` (`:91`) |
| capabilities | `record_screen`, `record_screen_with_audio`, `record_region` (`:99-103`) |
| best_for | quick recording w/o extra software, automated demo-pipeline capture, region capture for tutorials (`:105-109`) |
| not_good_for | webcam PiP overlay, cursor highlight effects, interactive pause/resume UI — "use Cap for that" (`:111-115`) |
| key input params | `output_path` (required), `duration_seconds` (default 60, hard-capped 600), `fps` (default 30), `capture_audio` (default true), `region` (`x`/`y`/`width`/`height`), `screen_index` (default 0) (`:117-156`) |

### What it actually executes
`execute()` (`:176`) resolves `output_path`, clamps `duration_seconds` to 600 (`:178`), creates parent dirs (`:184`), then reads `platform.system()` (`:186`) and dispatches to one of three private command builders via `_build_command` (`:255-279`): `_build_windows_cmd` for `"Windows"`, `_build_mac_cmd` for `"Darwin"`, `_build_linux_cmd` for `"Linux"` (`:267-278`); any other platform string returns `None` and the tool fails cleanly (`:192-197`).

- **Windows** (`:281-312`): builds `ffmpeg -y -f gdigrab -framerate <fps> -t <duration> [-offset_x X -offset_y Y -video_size WxH] -i desktop`, then if `capture_audio`, calls `_detect_audio_device_windows()` (`:35-54`) which runs `ffmpeg -list_devices true -f dshow -i dummy` and scrapes the device name out of stderr, appending `-f dshow -i audio=<name>`. Output side: `-c:v libx264 -preset ultrafast -crf 23 [-c:a aac -b:a 128k] -pix_fmt yuv420p <output_path>`.
- **macOS** (`:314-344`): calls `_detect_audio_device_mac()` (`:57-77`), which runs `ffmpeg -f avfoundation -list_devices true -i ""` and parses the `[N]` index under the "AVFoundation audio devices" stderr section. Builds `ffmpeg -y -f avfoundation -framerate <fps> -t <duration> -i "<screen_index>:<audio_idx or none>"`; if a `region` is given it can't be passed natively to avfoundation, so it's cropped in post via `-vf crop=W:H:X:Y` (`:331-334`). Same libx264/aac/yuv420p output tail.
- **Linux** (`:346-374`): reads `DISPLAY` env (default `:0.0`), builds `ffmpeg -y -f x11grab -framerate <fps> -t <duration> [-video_size WxH] -i <display>[+X,Y]`, optionally appends a second input `-f pulse -i default` for audio, same output tail.

The single `ffmpeg` capture command actually runs via `subprocess.run(cmd, capture_output=True, text=True, timeout=duration+30)` (`:201-204`) — this is the one subprocess that blocks for the whole recording. After it returns (or on `TimeoutExpired`, treated as the expected end-of-recording case at `:233-250`), the tool checks the file exists, computes size, and calls `_probe_resolution()` (`:376-394`) which runs a second subprocess: `ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 <path>`.

### Flow
```text
Agent call:
{ tool: "screen_recorder", output_path: "recordings/demo.mp4",
  duration_seconds: 30, fps: 30, capture_audio: true }
                │
                ▼
execute()                                screen_recorder.py:176
  output_path, duration=min(30,600)              :177-178
  mkdir(parents=True)                             :184
  sys_platform = platform.system()                :186
                │
                ▼
_build_command(sys_platform, ...)                :187,255
     ★ DIVERGENCE — platform branch
     ┌───────────┬────────────┬────────────┐
     ▼           ▼            ▼
 "Windows"    "Darwin"      "Linux"
 _build_      _build_       _build_
 windows_cmd  mac_cmd       linux_cmd
 :281,268     :314,272      :346,276
     │           │            │
     │      capture_audio?    │
     │      _detect_audio_    │
     │      device_mac()      │
     │      :57-77 → runs     │
     │      `ffmpeg -f        │
     │      avfoundation      │
     │      -list_devices     │
     │      true -i ""`       │
     │           │            │
     └───────────┴────────────┘
                │
                ▼
cmd = ["ffmpeg","-y","-f","avfoundation",
       "-framerate","30","-t","30","-i","0:0",
       "-c:v","libx264","-preset","ultrafast",
       "-crf","23","-c:a","aac","-b:a","128k",
       "-pix_fmt","yuv420p","recordings/demo.mp4"]
                │
                ▼
subprocess.run(cmd, timeout=60)          screen_recorder.py:201-204
  ★ blocks here for ~30s — real capture happens
                │
                ▼
output_path.exists()? ── no ──► ToolResult(success=False) :207-211
                │ yes
                ▼
file_size_mb = stat().st_size / 1MB              :213
_probe_resolution(path)                          :216,376
  → subprocess: ffprobe -show_entries
    stream=width,height ...                      :379-388
                │
                ▼
ToolResult(success=True, data={output_path,
  duration_seconds, resolution, has_audio,
  file_size_mb, capture_method:"ffmpeg"},
  artifacts=[output_path])                       :218-231
                │
                ▼
     recordings/demo.mp4  (MP4 artifact on disk)
```

**Notes:**
- `dependencies = ["binary:ffmpeg"]` (`:91`) does not match any prefix `check_dependencies()` recognizes (`cmd:`/`env:`/`python:`) — this tool's ffmpeg presence is effectively never checked by the shared dependency verifier, only implicitly by the subprocess failing at runtime.
- `fallback_tools = ["cap_recorder"]` (`:174`) — the registry can hand off to Cap if ffmpeg capture fails, but the actual failover logic lives in `screen_capture_selector`, not here.

---

## `cap_recorder`

**File:** `tools/capture/cap_recorder.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `ToolTier.SOURCE` (`:166`) |
| capability | `"screen_capture"` (`:167`) |
| provider | `"cap"` (`:168`) |
| runtime | `ToolRuntime.LOCAL` (`:172`) |
| determinism | `Determinism.DETERMINISTIC` (`:171`) |
| dependencies | `[]` — no hard dependency; detection is graceful (`:174`) |
| capabilities | `detect_cap`, `check_status`, `find_recordings`, `setup_guidance` (`:186-191`) |
| best_for | polished recordings w/ webcam overlay, cursor highlight/click effects, UI-driven (not CLI) recording, polished audio (`:193-198`) |
| not_good_for | automated/headless recording, recording w/o user interaction, quick recordings where setup time matters (`:200-204`) |
| key input params | `operation` (required, enum: `detect`/`status`/`find_recordings`/`setup_guide`/`pick_latest`), `output_dir` (for `pick_latest`), `since_minutes` (default 5, for `find_recordings`) (`:206-231`) |

### What it actually executes
`cap_recorder` never launches or controls Cap — Cap (https://cap.so) is a separate, standalone desktop application (its own recording UI, webcam overlay, GPU capture) that the user runs independently. This tool is a filesystem/process bridge: it locates the Cap binary, checks whether it's running, and scrapes Cap's own output directory for finished recordings.

`execute()` (`:257`) dispatches on `operation` to one of five private methods (`:260-277`).

- **`detect`** → `_detect()` (`:279-293`) calls `_find_cap_binary()` (`:44-83`) which checks hardcoded install paths per platform — Windows: `%LOCALAPPDATA%/Cap/Cap.exe` etc. (existence check only, no subprocess); macOS: `/Applications/Cap.app/Contents/MacOS/Cap` (`Path.exists()`); Linux: `shutil.which("cap"/"Cap")` or an AppImage path. If a binary is found, it calls `_is_cap_running()` (`:113-137`), which DOES run a subprocess per platform: Windows `tasklist /FI "IMAGENAME eq Cap.exe" /NH`, macOS `pgrep -x Cap`, Linux `pgrep -x cap`.
- **`find_recordings`** → `_find_recordings()` (`:322-344`) calls `_find_cap_recordings_dir()` (`:86-110`) — e.g. on macOS `~/Library/Application Support/so.cap.desktop` (`:101`) — then `_get_recent_recordings()` (`:140-160`), which iterates that directory's subfolders sorted by mtime, globs `*.mp4`, `output/*.mp4`, `output/result.mp4` inside each (`:150`), and keeps files newer than `since_seconds` cutoff, capped to the 10 most recent.
- **`pick_latest`** → `_pick_latest()` (`:399-440`) reuses the same directory lookup + `_get_recent_recordings(since_seconds=3600)`, takes `recordings[0]`, and if `output_dir` was given, physically copies the file with `shutil.copy2(source, dest)` (`:420`); otherwise it just returns the original path in place.
- **`setup_guide`** returns static per-platform install instructions (brew/winget/AppImage) with no subprocess at all (`:346-397`).

### Flow
```text
Agent call:
{ tool: "cap_recorder", operation: "pick_latest",
  output_dir: "out/" }
                │
                ▼
execute()                                cap_recorder.py:257
  operation == "pick_latest"
                │
                ▼
_pick_latest(output_dir)                 cap_recorder.py:399
                │
                ▼
_find_cap_recordings_dir()               cap_recorder.py:86
  ★ DIVERGENCE — platform branch
  Darwin → ~/Library/Application Support/
           so.cap.desktop            (:101)
  Windows → %APPDATA%/so.cap.desktop (:92)
  Linux   → ~/.local/share/so.cap.desktop (:106)
                │
                ▼
  not found? ──► ToolResult(success=False,
                  "Cap recordings directory
                   not found.")        :401-405
                │ found
                ▼
_get_recent_recordings(dir, since=3600s)  cap_recorder.py:140
  scan subdirs by mtime desc, glob
  *.mp4 / output/*.mp4 / output/result.mp4
                │
  empty? ──► ToolResult(success=False,
             "No recent Cap recordings
              found. Record ... first.") :408-412
                │ non-empty
                ▼
latest = recordings[0]; source = Path(latest["path"])
                │
output_dir given?
     ★ DIVERGENCE
     ┌───────────────┬───────────────┐
     yes             no
     ▼                ▼
dest = out/<name>   return source
mkdir(parents=True)  path as-is
shutil.copy2(source, dest)   :417-420
     ▼
ToolResult(success=True,
  data={output_path: dest, original_path: source,
        size_mb, capture_method:"cap"},
  artifacts=[dest])           cap_recorder.py:421-430
                │
                ▼
     out/<recording>.mp4   (copied MP4 artifact)
```

**Notes:**
- `get_status()` is overridden to always return `AVAILABLE` (`:252-255`) regardless of whether Cap is actually installed — "gracefully handles missing Cap" — so registry availability checks alone can't tell you Cap is usable; callers must inspect the `detect`/`status` payload.
- No ffmpeg or Cap CLI is ever invoked to *record* — the only subprocess calls are OS process inspection (`tasklist`/`pgrep`) and file-existence checks; the actual capture happens entirely inside the separately-running Cap.app, outside this tool's control. `fallback_tools = ["screen_recorder"]` (`:250`) is the escape hatch when Cap isn't installed/running.

---

## `screen_capture_selector`

**File:** `tools/capture/screen_capture_selector.py`

### Discovery metadata (what the agent sees via the tool registry)
| field | value |
|---|---|
| tier | `ToolTier.SOURCE` (`:30`) |
| capability | `"screen_capture"` (`:31`) |
| provider | `"selector"` (`:32`) |
| runtime | `ToolRuntime.HYBRID` (`:36`) |
| determinism | `Determinism.DETERMINISTIC` (`:35`) |
| dependencies | none declared — relies on discovered providers' own deps |
| capabilities | `screen_recording`, `provider_selection`, `cap_setup_guidance` (`:40-44`) |
| best_for | choosing between quick FFmpeg vs polished Cap recording, guiding setup, routing the screen-demo pipeline (`:46-50`) |
| not_good_for | direct screen recording itself — "use the selected provider instead" (`:52-54`) |
| key input params | `operation` (required, enum: `recommend`/`record`/`pick_latest`), `preferred_provider` (enum `auto`/`ffmpeg`/`cap`, default `auto`), `output_path`, `duration_seconds`, `fps`, `capture_audio`, `region`, `since_minutes` (`:56-110`) |

### What it actually executes
This tool never touches ffmpeg or Cap directly — it auto-discovers everything registered under `capability="screen_capture"` via `_providers()` (`:128-133`), which calls `registry.ensure_discovered()` and `registry.get_by_capability("screen_capture")`, then builds a `{provider_name: tool_instance}` dict (excluding itself). In practice this yields `{"ffmpeg": <ScreenRecorder>, "cap": <CapRecorder>}`. `fallback_tools` is a computed property that mirrors whatever `_providers()` currently returns (`:135-137`), and `get_status()` (`:139-143`) reports `AVAILABLE` if any provider is available.

`execute()` (`:145`) dispatches on `operation`:
- **`recommend`** → `_recommend()` (`:160-250`): checks `ffmpeg_tool.get_status()` and calls `cap_tool.execute({"operation": "detect"})` (`:195`) to build a strengths/limitations comparison, then picks a `recommended` provider — honoring `preferred_provider` first, else auto-logic: prefer Cap if it's actively `running`, else FFmpeg if available, else Cap if merely installed, else default to `ffmpeg` (`:226-241`).
- **`record`** → `_record()` (`:276-315`): if `preferred_provider == "cap"`, it calls `cap_tool.execute({"operation": "pick_latest", "output_dir": ...})` — per the code's own comment, "Cap doesn't do the actual recording — it picks up what Cap recorded" (`:285`). For `"ffmpeg"` or `"auto"`, it checks `ffmpeg_tool.get_status() == AVAILABLE` and, if so, calls `tool.execute({output_path, duration_seconds, fps, capture_audio, region})` — i.e. delegates straight into `ScreenRecorder.execute()` (`screen_recorder.py:176`). If ffmpeg isn't available, it falls back to checking if Cap is `running` via a `detect` call and, if so, does a `pick_latest` pickup instead; otherwise it returns a hard error (`:310-313`).
- **`pick_latest`** → `_pick_latest()` (`:317-345`): only ever queries the Cap provider's `find_recordings` op — it does not consult FFmpeg at all (FFmpeg recording is synchronous to a known `output_path`, so there's no "latest file" concept for it).

### Flow
```text
Agent call:
{ tool: "screen_capture_selector", operation: "record",
  preferred_provider: "auto", output_path: "out/demo.mp4",
  duration_seconds: 45 }
                │
                ▼
execute()                          screen_capture_selector.py:145
  operation == "record" → _record(inputs)         :276
                │
                ▼
providers = self._providers()                     :279,128
  registry.ensure_discovered()                     :131
  registry.get_by_capability("screen_capture")      :132
  → {"ffmpeg": ScreenRecorder, "cap": CapRecorder}
                │
                ▼
preferred == "auto" branch                        :289
tool = providers.get("ffmpeg")
                │
   ★ DIVERGENCE — is ffmpeg AVAILABLE?
     ┌────────────────────┬─────────────────────┐
     yes                  no
     ▼                     ▼
tool.execute({          cap_tool = providers.get("cap")
 output_path,           cap_detect = cap_tool.execute(
 duration_seconds,        {"operation":"detect"})   :303
 fps, capture_audio,          │
 region})   :292-298     ★ DIVERGENCE — cap running?
     │                   ┌───────────┬───────────┐
     ▼                   yes          no
delegates to             ▼             ▼
ScreenRecorder.execute() cap_tool.execute(     error:
screen_recorder.py:176    {"operation":       "No screen
(full ffmpeg capture       "pick_latest",      capture
 flow, see above)          "output_dir":       provider
                           output_path})       available"
                           :305-308           :310-313
     │                       │
     └───────────┬───────────┘
                 ▼
     ToolResult bubbled straight back up
     to the agent from whichever provider
     ran (output_path/artifact from
     screen_recorder or cap_recorder)
```

**Notes:**
- The selector is a pure router/orchestrator — all its "execution" is really `providers[...].execute(...)` calls into `screen_recorder` or `cap_recorder`; it has no ffmpeg/Cap logic of its own.
- `_pick_latest()` (`:317-345`) only checks the Cap provider, never FFmpeg — an ffmpeg-only setup makes `operation="pick_latest"` on the selector always fail even right after a successful ffmpeg `record` call; the agent must use the `record` result's own `output_path` in that case.
