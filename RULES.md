# GOAL

The main purpose of this desktop app is to create vertical format content, which creates scroll stopping content. So basically, we are trying to create an instagram edits app (for mac) with AI agent in it.

The current focus for this app is the following:

1. Create a desktop app, which is a hybrid of AI agents editing and human editing. The AI agent would start the project, create composition, edits, etc. and then the human reviewer can edit the clips as they like.
2. The goal for creating this app is to create a mac app, which has all the features from instagram edits app (for editing timelines, etc.), but add an ai agent, which can help with the editing part like placing clips, adding music, sound effects, etc.

ICP: Our ICP is founder/builders/tech people trying to create videos for their instagram/tiktok to get reach. and hence we want to keep the UI/UX and features focused for them first.

# RULES — Desktop Editing App

Coding contract for the from-scratch editing UI (`web/src/studio/`) on top of the pure,
tested core (`web/src/editor/interp.js`). `AGENT_GUIDE.md` governs the **video-production
pipeline**; this file is **only** the desktop editor / Mission Control UI conventions. Keep it
short — add a pointer, not an essay.

## Glossary


| Term                          | What it is                                                                      | Lives in                                                                       |
| ----------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| **Timeline**                  | The area where all clips are arranged                                           | `StudioTimeline.jsx` → `.st-timeline`                                          |
| **Properties panel**          | Right-side panel: selection editor, or the Assets tab when nothing is selected  | `StudioInspector.jsx` → `.st-inspector` (+ `StudioAssets.jsx`)                 |
| **Project toolbar**           | Top-bar actions: undo, redo, +Text, canvas, preview toggle, Save, Render        | `StudioToolbar.jsx` → `.st-tools` (the title sits beside it in `.st-bar-left`) |
| **Canvas setting**            | Output-size selector in the top toolbar                                         | `.st-canvas` (in `StudioToolbar.jsx`)                                          |
| **Preview canvas**            | Central area where the video plays                                              | `StudioPreview.jsx` → `.st-stage` / `.st-video`                                |
| **Preview playback controls** | Play / pause transport (lives in the timeline toolbar)                          | `.st-play` (in `StudioTimeline.jsx` → `.st-tl-ops`)                            |
| **Playhead**                  | The red vertical scrub pointer                                                  | `.st-playhead` (in `StudioTimeline.jsx`)                                       |
| **Timeline clips**            | Clip blocks: cuts (blue) + overlays (green) + audio (warm)                      | `.st-clip` / `.st-ov` / `.st-aud`                                              |
| **Timeline toolbar**          | Bar above the lanes: play/pause, split, delete, duplicate (left) + zoom (right) | `.st-tl-head` (in `StudioTimeline.jsx`)                                        |


## Architecture

- **Pure core (`editor/interp.js`)** — all doc/timeline logic: mutators, placement math
(`cutStarts`/`cutAtTime`/`timelineDuration`/`audioClips`), sanitizers, `interpolateAt`. No
React, no DOM. Unit-tested in `interp.test.js`.
- **UI vocab (`studio/model.js`)** — constants, factories, presets, small math. Pure; tested
in `model.test.js`.
- **Components (`studio/*.jsx`)** — pointer math + layout only. They call core mutators; they
do not contain editing logic.

## Rules

- **New editing behavior → a pure function in `interp.js` (or a `model.js` helper) + a test.**
Don't bury logic in a component. Components stay thin.
- **Mutators are immutable.** Return a NEW doc; a no-op returns the **same** ref. History and
dirty-detection rely on referential equality — preserve it.
- **Schema-safe writes.** Every field the UI emits must pass `sanitizeCut`/`sanitizeOverlay`
(whitelists; `additionalProperties:false`). Keep whitelists in sync with
`schemas/artifacts/edit_decisions.schema.json`. A Save must never 422.
- **Placement is derived, never stored.** Cuts concatenate via `cutStarts` (order + trim +
speed); overlays and `audio.*` (music/narration/sfx) sit on absolute `start_seconds`. Pixels
= `LANE_PAD + time*zoom`, computed at render. Never store x-positions in the doc.
- **History = `commit` vs `live`.** `commit` = one undo step. `live` = per-frame drag, no
history; call `snapshot()` once at drag start to coalesce a drag into one undo. Never nest a
setState updater inside another (StrictMode double-invokes).
- **Preview == export, and we are working towards a fully WYSIWYG canvas.** The preview must reflect
what FFmpeg renders. Only expose tools the FFmpeg path renders (`TRANSITIONS`, `KF_DIMS_*`);
interpolation is linear, so preview linearly. The audio lane is selectable/editable (asset, timing,
levels) but not yet drag-positioned, and FFmpeg still owns mixing.
  - **NORTH STAR — edit live, render rarely.** Plain FFmpeg editing (cuts, overlays incl. video
  overlays, text, images, audio, position/trim/track/opacity) must be **previewable WITHOUT
  re-rendering** — the source-mode canvas composites and PLAYS it live (base clip + video overlays
  slaved to the playhead). The user should NEVER have to hit Render just to see an edit. **Only
  Remotion/HyperFrames composition clips require a re-render** (their pixels are produced by a
  runtime, not by FFmpeg arrangement) — and even then only the changed comp re-renders
  (render-once, content-cached). Render produces the final exact-bytes MP4; it is not the way you
  preview an edit.
- **Agent and user share ONE live doc (no "reopen").** The on-disk `edit_decisions.json` is the
single source of truth that BOTH the user (editor) and the agent edit. The editor **autosaves**
(debounced ~700ms) so the disk is always current; it **flushes that autosave before handing a turn
to the agent** (so the agent reads the user's latest) and **suspends autosave while the agent is
mid-turn** (never clobber its write). When the agent's turn ends, the editor **adopts the new disk
doc LIVE** into React state — and if the user made mid-turn edits, pushes their version onto the
undo stack (⌘Z restores it) rather than warning them to reopen. The agent edits the JSON directly
(the schema is the shared contract); it does NOT use the editor's JS mutators. `agentBusyRef` /
`reconcilingRef` gate Save/autosave so they can't race the agent's write.
- **Pointer events for in-timeline manipulation.** Scrub/trim/reorder/resize share one
`pointerdown → window move/up/cancel` model; always tear down window listeners on pointerup AND
on unmount. (Exception: cross-panel **asset drag from the Assets tab onto the timeline** uses
HTML5 DnD — `dataTransfer` type `application/x-opennolan-asset`, dropped via `xToTime`.)
- **CSS namespace `st-`.** Keep new studio styles prefixed and co-located in `styles.css`.
- Do not commit until the user tells you to commit explicitly.
- If there is any issue in this [RULES.md](http://RULES.md) that contradicts the actual repo or what the user tells you to do, then instead of silently failing, flag it to the user for confirmation to either change the [RULES.md](http://RULES.md) file or rethink on what the user wants.
- We want an aesthetic UI, so don't add unnecessary emojis to the UI. Use aesthetic icons instead.

### Editor feature conventions

- **Resizable panels (feat 1):** drag-splitters resize stage / properties panel / timeline.
Preview canvas has a hard min size; past a threshold the properties panel and timeline
**collapse** (not shrink indefinitely). Sizes are view state — persisted to `localStorage`
(`st.panels.v1`), never written to the doc.
- **Transport home (feat 2):** play/pause belongs in the **timeline toolbar** (`.st-tl-head`).
- **Audio on the timeline (feat 3):** derive blocks from `interp.audioClips(doc)` (carries the
doc `index`) — never re-read the schema in the component. Music spans the whole timeline; sfx
are point markers. Items are selectable (`{kind:'audio', audioKind, index}`) and edited via the
audio mutators (`updateMusic`/`updateNarration`/`updateSfx` + `remove*`). The source preview
plays them too: `model.previewAudioTracks(doc)` → hidden `<audio>` synced to the rAF playhead.
- **Properties panel = selection or assets (feat 4):** show the clip / overlay / **audio** editor
for the current selection, else the **Assets tab** (same kinds as the agent window: images /
video / audio / music — plus a read-only **renders** tab fed by the backend's `agent_renders`
array, i.e. the agent's HyperFrames clips from `{project}/hf/renders`; renders behave like videos
on the timeline and are NOT uploadable, so their tab has no dropzone). Deselect = click the timeline background (a single handler on
`.st-tl-scroll` that ignores `.st-clip/.st-ov/.st-aud/.st-ruler`) or press Escape → Assets.
Adding an image comes from Assets, so +Image leaves the project toolbar.
- **Clip ops home (feat 5):** split / duplicate / delete live in the timeline toolbar and act
on the current selection / playhead via the `interp` mutators.
- **Smooth playhead (feat 6):** drive playback + scrub with `requestAnimationFrame`. The red
line reads time → px every frame; it never stores px. A user scrub pauses playback.

### Overlay tracks · per-type props · canvas editing (later additions)

- **Overlay tracks (z-order):** overlays carry a `track` integer (schema field;
`additionalProperties:false`, default 0 = legacy array-order). The FFmpeg `_overlay` pass
**stable-sorts overlays by `track`** → higher track composites on top. The timeline draws one
lane per track (highest on top) plus an empty top lane for adding; placement is **derived** from
`interp.overlayTracks(doc)`, never stored. Drop target decides main-vs-overlay: cuts lane → a
main clip, overlay track lane → an overlay at that track.
- **Auto-track-stacking (greedy interval partitioning):** every overlay ADD runs
`interp.placeOverlayTrack(doc, start, end, preferredTrack)` — the lowest track ≥ preferred with no
TIME overlap, else a fresh track on top — so a new overlapping overlay auto-lands on its own lane
(both stay visible), NLE-style. The `⇅ Arrange` toolbar button runs `interp.autoArrangeOverlays`
(the calendar/Gantt "lane assignment" algorithm: sort by start, drop each on the first lane whose
last item ended, else a new lane) to re-pack an existing doc into the fewest non-overlapping
tracks. A move/trim drag-END runs `interp.resolveOverlayOverlap(doc, index)` — a CHEAP, TARGETED
resolve that floats ONLY the just-edited overlay up to a free lane if it now overlaps a neighbor on
its own track (not a full re-pack), folded into the drag's single undo step. It's a same-ref no-op
when there's no new same-track overlap, so a deliberate placement (incl. an overlap on a DIFFERENT
track — already visible) is left alone. So overlaps auto-stack on add AND on move/trim; only a
cross-track vertical drag with no resulting overlap, or the explicit Arrange button, is honored verbatim.
- **Draggable overlays:** overlays move on **absolute** project time (`interp.moveOverlay` — start
preserves duration AND shifts keyframe `t` by the same delta; vertical drag sets `track`) and
edge-trim (`interp.trimOverlay`). Same pointer model as cuts; snapshot **lazily on first move**
(via `spec.onBegin`) so a bare click on a handle/overlay is a pure select with no undo entry.
- **Per-type property schema:** the inspector renders from the declarative `studio/propertySchema.js`
(7 types: `video_main`/`image_main`/`video_overlay`/`image_overlay`/`text`/`music`/`sfx`, +
narration). Type is derived from the selected OBJECT (`isImageSource`/`overlayType`), not a doc
re-lookup. Fields bind to a dotted `path` via `getAtPath`/`buildPatch` and commit through the
same `interp` mutators (Save never 422s). Special controls (speed presets / crop / audio-mix /
keyframes / text-position) render bespoke. image_overlay has NO audio-mix; image_main has
duration + no speed.
  - **Numeric fields are scrub bars (`ScrubField`):** DRAG horizontally to adjust (1px ≈ one
  `step`, Shift = fine) or CLICK to type an exact value. A drag is ONE undo step — `onScrubBegin`
  (= `snapshot`) once on the first move, then `onLiveUpdate{Cut,Overlay,Audio}` (→ `live`) per
  frame; typing/arrow-keys `commit` (one step each); a bare click only opens the type-in input (no
  history). Drag→value math is the pure `scrubValue`/`roundTo`/`fmtScrub` in `model.js` (tested).
  Bounded fields (finite min AND max — opacity/volume/box-opacity/transition-duration) show a fill
  bar; the unit `suffix` shows in the VALUE, not the caption. **Timeline clip blocks carry no
  icon/emoji** — overlay/audio blocks show only their text/name (SFX point markers = a bare dot).
- **WYSIWYG canvas (preview == export):** source mode renders a **canvas-aspect safe frame**
(`.st-safe-frame`, `object-fit:contain` mirroring the renderer's scale+pad). Overlays are drawn
in **canvas coordinates** mapped to the frame (`scale = frameW / canvas.width`, measured via
ResizeObserver — overlays gate on `scale > 0`), z-ordered by `track`. Drag-to-position writes
`position.{x,y}` in canvas px (text anchor → object on first drag; drag origin for
object/scale-keyframed overlays comes from canvas x/y, NOT the post-transform bbox). Still images
are now valid **main-timeline cuts** (the FFmpeg path loops them; guarded against 0-duration).
**Video overlays PLAY live** in the source preview: each is a `<video>` (registered in
`ovVideoEls`) slaved to the playhead by the rAF clock (rolling) / a paused-seek effect (scrubbing),
muted unless its `audio_mix.enabled`. No re-render to preview a dropped video overlay.
- **Crop is baked into the proxy, not the assemble.** `transform.crop` is in SOURCE pixels, so it
can only be applied at native source resolution — `render_proxies` bakes it into the (content-keyed)
proxy and `_build_assemble_edl` drops crop from the assemble. Applying a source-px crop to the
canvas-sized proxy goes out of bounds (`crop=1440x2560 on a 1080x1920 proxy → ffmpeg exit 234`).
So a crop edit re-renders that one scene (crop is a content edit); reorder/retime/transition/
position stay cheap.

## Testing

- **Run:** `npm test` in `web/` (vitest). Component tests need jsdom (configured in
`vite.config.js` + `src/test/setup.js`).
- **Unit (pure) — most coverage here.** Placement/edit/sanitize logic in `interp.test.js`;
vocab/factories/presets in `model.test.js`. Add/extend a test with every mutator or helper
you touch.
- **Component (RTL + jsdom).** Render contracts only — assert structure and inline-style
placement (`left/width` = derived px) and handler wiring. jsdom has no layout engine, so do
NOT assert geometry that needs `getBoundingClientRect`.
- **E2E (out of scope here).** Pointer-driven flows — drag-resize/collapse thresholds, scrub
smoothness, drag-trim/reorder, render==preview — belong in a Playwright suite against the
running app (`./run-desktop --dev`), not jsdom.

