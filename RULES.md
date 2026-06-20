# GOAL

The main purpose of this desktop app is to create vertical format content, which creates scroll stopping content.

The current focus for this app is the following:
1. Create a desktop app, which is a hybrid of AI agents editing and human editing. The AI agent would start the project, create composition, edits, etc. and then the human reviewer can edit the clips as they like.
2. The goal for creating this app is to create a mac app, which has all the features from instagram edits app (for editing timelines, etc.), but add an ai agent, which can help with the editing part like placing clips, adding music, sound effects, etc.

# RULES — Desktop Editing App

Coding contract for the from-scratch editing UI (`web/src/studio/`) on top of the pure,
tested core (`web/src/editor/interp.js`). `AGENT_GUIDE.md` governs the **video-production
pipeline**; this file is **only** the desktop editor / Mission Control UI conventions. Keep it
short — add a pointer, not an essay.

## Glossary

| Term | What it is | Lives in |
|------|-----------|----------|
| **Timeline** | The area where all clips are arranged | `StudioTimeline.jsx` → `.st-timeline` |
| **Properties panel** | Right-side panel: selection editor, or the Assets tab when nothing is selected | `StudioInspector.jsx` → `.st-inspector` (+ `StudioAssets.jsx`) |
| **Project toolbar** | Top-bar actions: undo, redo, +Text, canvas, preview toggle, Save, Render | `StudioToolbar.jsx` → `.st-tools` (the title sits beside it in `.st-bar-left`) |
| **Canvas setting** | Output-size selector in the top toolbar | `.st-canvas` (in `StudioToolbar.jsx`) |
| **Preview canvas** | Central area where the video plays | `StudioPreview.jsx` → `.st-stage` / `.st-video` |
| **Preview playback controls** | Play / pause transport (lives in the timeline toolbar) | `.st-play` (in `StudioTimeline.jsx` → `.st-tl-ops`) |
| **Playhead** | The red vertical scrub pointer | `.st-playhead` (in `StudioTimeline.jsx`) |
| **Timeline clips** | Clip blocks: cuts (blue) + overlays (green) + audio (warm) | `.st-clip` / `.st-ov` / `.st-aud` |
| **Timeline toolbar** | Bar above the lanes: play/pause, split, delete, duplicate (left) + zoom (right) | `.st-tl-head` (in `StudioTimeline.jsx`) |

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
- **Preview == export.** The preview must reflect what FFmpeg renders. Only expose tools the
  FFmpeg path renders (`TRANSITIONS`, `KF_DIMS_*`); interpolation is linear, so preview linearly.
  The audio lane is selectable/editable (asset, timing, levels) but not yet drag-positioned, and
  FFmpeg still owns mixing.
- **Pointer events for in-timeline manipulation.** Scrub/trim/reorder/resize share one
  `pointerdown → window move/up/cancel` model; always tear down window listeners on pointerup AND
  on unmount. (Exception: cross-panel **asset drag from the Assets tab onto the timeline** uses
  HTML5 DnD — `dataTransfer` type `application/x-opennolan-asset`, dropped via `xToTime`.)
- **CSS namespace `st-`.** Keep new studio styles prefixed and co-located in `styles.css`.

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
  video / audio / music). Deselect = click the timeline background (a single handler on
  `.st-tl-scroll` that ignores `.st-clip/.st-ov/.st-aud/.st-ruler`) or press Escape → Assets.
  Adding an image comes from Assets, so +Image leaves the project toolbar.
- **Clip ops home (feat 5):** split / duplicate / delete live in the timeline toolbar and act
  on the current selection / playhead via the `interp` mutators.
- **Smooth playhead (feat 6):** drive playback + scrub with `requestAnimationFrame`. The red
  line reads time → px every frame; it never stores px. A user scrub pauses playback.

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
