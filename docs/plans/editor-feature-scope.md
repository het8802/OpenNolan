# OpenNolan Editor — Feature Scope Plan (M3)

**Status:** Draft for review
**Date:** 2026-06-28
**Owner:** Het
**Context:** This plan defines the short-form feature roadmap for the OpenNolan desktop editor, derived from PM research into the most-used editing features for vertical short-form (Reels/TikTok/Shorts), benchmarked against Instagram **Edits** and **CapCut**.

**Base reality (not from scratch):** The editor already exists as `web/src/studio/` — a working, tested NLE (~4,228 LOC) with timeline, clips, overlays (auto-overlap-resolve), inspector, keyframes, undo/redo, color picker, scrub bar, and **live agent↔editor sync** (autosave + adopt-on-turn-end). It runs on the render-once / edit-cheap pipeline (prompt → each composition renders to a cached clip → FFmpeg-assemble). The older `web/src/editor/` surface is superseded and slated for retirement. This plan is about **evolving Studio toward the short-form feature set**, not rebuilding it.

---

## 1. Goal & positioning

Build a **fast, local-first, watermark-free desktop editor** for vertical short-form content, where AI-assist features are first-class (BYOK) and the render-once model makes re-edits cheap (only changed scenes re-render).

**The seam we're attacking:** Edits is **mobile-only**; CapCut's desktop app is heavy and adds watermarks/paywalls on assets. A snappy *desktop* editor that is watermark-free, local-first, and AI-native is an open gap.

**Non-goals (for M3):** mobile app, multi-user collaboration/cloud, a template marketplace, long-form (>10 min) editing.

---

## 2. The usage-weighted feature loop (what 90% of edits touch)

Research shows a real Reel edit repeatedly touches ~8–10 features. These define the MVP "must feel great" set:

1. Cut / trim / split on a timeline
2. Auto-captions (burned-in, styled, word-by-word)
3. Music + sync cuts to beat
4. Text overlays / hook titles
5. Speed control (ramp, slow-mo, freeze frame)
6. Transitions
7. 9:16 canvas + auto-reframe horizontal → vertical
8. Background removal / green screen / subject cutout
9. Voiceover / TTS + audio cleanup (remove silence, denoise)
10. B-roll / overlay / PiP layers

---

## 3. Tiered scope

### Tier 0 — Table stakes (MVP; not credible without these)
Status legend: ✅ exists in Studio · 🟡 partial · ❌ gap (build it)

| Feature | Status | Notes / pipeline fit |
|---|---|---|
| Multi-track timeline: trim / cut / split / ripple-delete | ✅ | `StudioTimeline` |
| Overlay / PiP / B-roll layers | ✅ | overlap auto-resolve shipped |
| Click-a-clip inspector / clip props | ✅ | `StudioInspector`, `propertySchema` |
| Text / title overlays | ✅ 🟡 | exists; **animations** are the gap |
| Preview (scrub) == export (render-once consistency) | ✅ | `StudioPreview` — keystone invariant |
| Undo/redo, color picker | ✅ | shipped |
| 9:16 canvas + ratio presets (9:16, 1:1, 4:5, 16:9) | 🟡 | verify quick canvas switch exists |
| Per-clip volume + **ducking** | 🟡 | confirm audio track state |
| **Auto-captions** w/ styling + word highlight | ❌ | BYOK speech-to-text; the single most-used feature |
| Music import + **beat markers** to snap cuts | ❌ | audio waveform on timeline |
| Speed control + **freeze frame** + reverse | ❌ | cheap energy |
| Transitions (cut, crossfade, recurring "style" set) | ❌ | FFmpeg-assemble step |
| **Watermark-free 4K export** | ❌ 🟡 | confirm export path; major adoption driver |

The real MVP work is the ❌ rows: **captions, beat sync, speed/freeze, transitions, watermark-free export.** Everything ✅ is leverage, not rebuild.

### Tier 1 — High-leverage AI differentiators (BYOK strength)
| Feature | Notes |
|---|---|
| **Auto-reframe** horizontal → 9:16 (subject-aware) | Repurposing is a constant chore |
| **Background removal / cutout + tracking** | Explainer/reaction format |
| **Auto-clip from long-form** ("find best moments") | Long→short pipeline |
| **TTS** + voice cleanup (denoise, voice enhance) | Narration content |
| **Auto-remove silences** | Talking-head/podcast clips |
| Adjustment-layer color grading + LUTs | Grade all clips at once |
| Teleprompter (capture + voiceover) | Increasingly table-stakes |

### Tier 2 — Power-user ceiling (post-MVP)
Keyframe animation (pos/scale/rot/opacity), masking, motion tracking, speed curves, AI restyle, image-to-video, blend modes.

---

## 4. Architecture fit (render-once / edit-cheap)

- Each scene/composition renders once to a **cached proxy clip**; the timeline file assembles them via FFmpeg. Editing a scene re-renders **only that scene** (already wired: `render_jobs` → `render_proxies`).
- Tier-0 ops (trim, speed, transition, overlay, captions, audio) should be **FFmpeg-assemble-layer** operations where possible, so they're "cheap" edits that never trigger a full re-render.
- AI features (Tier 1) are BYOK calls that produce assets (captions JSON, cutout mattes, reframed crops) consumed by the assemble layer.
- **Invariant:** preview == export.

---

## 5. Proposed build sequence (evolving Studio)

- **S0 — Consolidate:** confirm 9:16 canvas switch + watermark-free export path exist; retire `web/src/editor/`; confirm audio-track state for ducking. (De-risk before building.)
- **S1 — The short-form loop (the actual MVP gap):** auto-captions → beat sync → speed/freeze → transitions → text-overlay animations. (Proves "can I make a real Reel" on top of existing Studio.)
- **S2 — AI differentiators (the wedge):** auto-reframe, background removal, auto-remove-silence, TTS. BYOK assets consumed by the assemble layer.
- **S3 — Polish:** color/LUT adjustment layer, teleprompter, overlay/PiP refinement.

## 5b. What already exists (leverage, do not rebuild)

`web/src/studio/` provides: timeline + clips, overlays w/ overlap-resolve, inspector + property schema, keyframes, preview==export, undo/redo, color picker, scrub bar, drag-to-scrub fields, and **live agent↔editor sync** (the differentiator — agent and human edit the same model). All test-covered. Reuse `model.js`, `interp.js`, `propertySchema.js`, and the sync core. The older `web/src/editor/` (~2,074 LOC) is superseded → retire it.

## 5c. NOT in scope
- Rebuilding the NLE from scratch (stale framing from the old `desktop-app-mvp.html` doc).
- Mobile app, cloud collaboration, template marketplace, long-form (>10 min).
- Tier 2 power-user features beyond what Studio already has (masking, motion tracking, speed curves) — deferred.

---

## 6. Decisions (resolved in CEO review 2026-06-28)

1. **Caption rendering → bake only at final export.** Captions stay an editable text track throughout; they are burned into pixels only during the cheap FFmpeg assemble/stitch on export, never into the cached scene proxies. Editing a caption re-runs the stitch, not a scene render. (Protects render-once edit-cheap for the most-edited feature.)
2. **Auto-reframe / bg-removal / cutout → BYOK vision APIs**, not an in-app model. Keeps the app small and matches the project's BYOK + edit-cheap model.
3. **Effects & transitions → lean on HyperFrames comps + the render-once cache.** Do not build a parallel effects system.
4. **auto-clip-from-long-form → deferred to S2/post-MVP.** It's a separate ingestion workflow, not part of the core "make a Reel" loop.

### Still open
- Which specific BYOK providers are the defaults for captions (speech-to-text) and TTS?

---

## 7. Success criteria

A creator can, on a clean Mac, in one sitting: import footage → cut to 9:16 → add beat-synced cuts → auto-caption → add hook text → remove silences → export watermark-free 4K — and a re-edit of one scene re-renders only that scene.

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | mode: HOLD_SCOPE, 0 critical gaps; reframed "from scratch" → "evolve Studio", 4 decisions resolved |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 0 | — | — |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |

- **UNRESOLVED:** 1 (default BYOK providers for captions/TTS)
- **VERDICT:** CEO CLEARED — eng review required before implementation. Key reframe: do NOT rebuild; evolve `web/src/studio`. The MVP is the short-form layer (captions, beat sync, speed/freeze, transitions, watermark-free export) + the AI wedge.

