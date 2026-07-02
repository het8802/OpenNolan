// Studio-editor UI helpers — pure, dependency-free. Constants + factories + small math.
// The schema-level doc mutations live in ../editor/interp.js (tested contract core);
// this file only adds UI-facing vocab/presets/factories that emit schema-valid fragments.

// ── Transition vocabulary (ONLY names the FFmpeg xfade path renders — see _XFADE_MAP) ──
// Anything not here would degrade to 'fade' with a warning, so we don't offer it.
export const TRANSITIONS = [
  { value: '', label: 'Hard cut' },
  { value: 'fade', label: 'Fade' },
  { value: 'fadeblack', label: 'Fade through black' },
  { value: 'fadewhite', label: 'Fade through white' },
  { value: 'dissolve', label: 'Dissolve' },
  { value: 'wipeleft', label: 'Wipe ←' },
  { value: 'wiperight', label: 'Wipe →' },
  { value: 'wipeup', label: 'Wipe ↑' },
  { value: 'wipedown', label: 'Wipe ↓' },
  { value: 'slideleft', label: 'Slide ←' },
  { value: 'slideright', label: 'Slide →' },
  { value: 'circleopen', label: 'Circle open' },
  { value: 'circleclose', label: 'Circle close' },
  { value: 'zoom', label: 'Zoom' },
]

// Quick speed buttons. FFmpeg setpts/atempo render any positive factor; these are presets.
export const SPEED_PRESETS = [0.5, 1, 1.5, 2, 4]

// Output-canvas presets (write metadata.compose_target). Even dims (yuv420p requirement).
export const CANVAS_PRESETS = [
  { label: '9:16 · 1080×1920', width: 1080, height: 1920 },
  { label: '1:1 · 1080×1080', width: 1080, height: 1080 },
  { label: '16:9 · 1920×1080', width: 1920, height: 1080 },
  { label: '4:5 · 1080×1350', width: 1080, height: 1350 },
]

// Named anchors the FFmpeg drawtext path accepts for TEXT overlays.
export const TEXT_ANCHORS = [
  'top-left', 'top-center', 'top-right',
  'center-left', 'center', 'center-right',
  'bottom-left', 'bottom-center', 'bottom-right',
]

export const EASINGS = ['linear', 'ease-in', 'ease-out', 'ease-in-out', 'spring', 'step']

// Keyframe dims the FFmpeg path actually renders, BY overlay kind. Rotation is never
// rendered (dropped + warned). Text overlays render only x/y/opacity (scale warned+ignored).
export const KF_DIMS_IMAGE = ['x', 'y', 'scale', 'opacity']
export const KF_DIMS_TEXT = ['x', 'y', 'opacity']

/** 'text' | 'image' — an overlay with explicit type wins; else text iff it has `text`.
 * (Keyframe dims treat video like image, so this stays a two-way split for `kfDimsFor`.) */
export function overlayKind(ov) {
  if (ov?.type === 'text') return 'text'
  if (ov?.type === 'image' || ov?.type === 'video') return 'image'
  return ov?.text != null && ov?.asset_id == null ? 'text' : 'image'
}

/** 'text' | 'image' | 'video' — the finer three-way split the inspector/preview need. */
export function overlayType(ov) {
  if (ov?.type === 'text') return 'text'
  if (ov?.type === 'video') return 'video'
  if (ov?.type === 'image') return 'image'
  return ov?.text != null && ov?.asset_id == null ? 'text' : 'image'
}

export function kfDimsFor(ov) {
  return overlayKind(ov) === 'text' ? KF_DIMS_TEXT : KF_DIMS_IMAGE
}

// Still-image source extensions — kept in sync with video_compose.py `_IMAGE_EXTENSIONS`
// (note .gif is intentionally absent: GIFs are video-like on both paths).
export const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.webp']

/** True if a cut source path is a still image (→ image_main on the main timeline). */
export function isImageSource(path) {
  const p = String(path || '').toLowerCase()
  return IMAGE_EXTENSIONS.some(ext => p.endsWith(ext))
}

/**
 * The clip-type key for the current selection — drives which declarative property schema the
 * inspector renders. One of: video_main | image_main (cuts) · video_overlay | image_overlay |
 * text (overlays) · music | sfx | narration (audio). Null if nothing/no match is selected.
 */
export function clipType(selection, doc) {
  if (!selection) return null
  if (selection.kind === 'cut') {
    const cut = (doc?.cuts || []).find(c => c.id === selection.id)
    if (!cut) return null
    return isImageSource(cut.source) ? 'image_main' : 'video_main'
  }
  if (selection.kind === 'overlay') {
    const ov = (doc?.overlays || [])[selection.index]
    if (!ov) return null
    const t = overlayType(ov)
    return t === 'text' ? 'text' : t === 'video' ? 'video_overlay' : 'image_overlay'
  }
  if (selection.kind === 'audio') {
    return selection.audioKind === 'music' ? 'music'
      : selection.audioKind === 'sfx' ? 'sfx' : 'narration'
  }
  return null
}

/** A schema-valid text overlay over [start,end], CENTERED by default (feat 4): a new overlay
 * lands in the middle of the canvas, then the user drags it. 'center' is a valid anchor on the
 * drawtext path; it becomes an {x,y} object on the first canvas drag (drawtext accepts both). */
export function newTextOverlay({ start = 0, end = 3, track = 0 } = {}) {
  return {
    type: 'text',
    text: 'Your text',
    font_size: 64,
    color: 'white',
    box: { color: 'black', opacity: 0.5, padding: 12 },
    start_seconds: round3(start),
    end_seconds: round3(end),
    position: 'center',
    opacity: 1,
    track,
  }
}

/** Center an asset-overlay box of `widthFrac` of the canvas width. Width-only (height stays
 * auto so the renderer keeps aspect); y is estimated assuming a square box, so the box lands
 * near the middle — the user fine-tunes by dragging on the canvas. */
function centeredAssetPosition(canvas, widthFrac) {
  const w = canvas?.width || 1920
  const h = canvas?.height || 1080
  const ow = Math.round(w * widthFrac)
  return { x: Math.round((w - ow) / 2), y: Math.max(0, Math.round((h - ow) / 2)), width: ow }
}

/** A schema-valid image overlay (asset_id + OBJECT position; named anchors are text-only),
 * centered by default. */
export function newImageOverlay({ assetId, start = 0, end = 3, canvas, track = 0 } = {}) {
  return {
    type: 'image',
    asset_id: assetId,
    start_seconds: round3(start),
    end_seconds: round3(end),
    position: centeredAssetPosition(canvas, 0.3),
    opacity: 1,
    track,
  }
}

/** A schema-valid VIDEO overlay (PiP) — type='video', centered by default, a little larger
 * than an image overlay. Audio off by default (toggle via audio_mix in the inspector). */
export function newVideoOverlay({ assetId, start = 0, end = 3, canvas, track = 0 } = {}) {
  return {
    type: 'video',
    asset_id: assetId,
    start_seconds: round3(start),
    end_seconds: round3(end),
    position: centeredAssetPosition(canvas, 0.4),
    opacity: 1,
    track,
  }
}

/**
 * Keyframe presets over an overlay's window. opacity presets work for any kind; motion
 * presets need an OBJECT position (image overlays). Returns a keyframes[] array or null
 * if the preset doesn't apply to this overlay kind.
 */
export function presetKeyframes(name, ov) {
  const s = Number(ov?.start_seconds) || 0
  const e = Number(ov?.end_seconds) || s + 1
  const pos = ov?.position
  const obj = pos && typeof pos === 'object' ? pos : null
  const x = obj ? Number(obj.x) || 0 : 0
  const y = obj ? Number(obj.y) || 0 : 0
  switch (name) {
    case 'fade_in':
      return [{ t: round3(s), opacity: 0, easing: 'ease-out' }, { t: round3(Math.min(e, s + 0.4)), opacity: 1 }]
    case 'fade_out':
      return [{ t: round3(Math.max(s, e - 0.4)), opacity: 1, easing: 'ease-in' }, { t: round3(e), opacity: 0 }]
    case 'slide_in_left':
      return obj ? [{ t: round3(s), x: x - 240, opacity: 0, easing: 'ease-out' }, { t: round3(Math.min(e, s + 0.5)), x, opacity: 1 }] : null
    case 'slide_in_right':
      return obj ? [{ t: round3(s), x: x + 240, opacity: 0, easing: 'ease-out' }, { t: round3(Math.min(e, s + 0.5)), x, opacity: 1 }] : null
    case 'pop':
      return obj ? [{ t: round3(s), scale: 0.6, opacity: 0, easing: 'ease-out' }, { t: round3(Math.min(e, s + 0.35)), scale: 1, opacity: 1 }] : null
    case 'ken_burns':
      return obj ? [{ t: round3(s), scale: 1, easing: 'linear' }, { t: round3(e), scale: 1.2 }] : null
    default:
      return null
  }
}

export function isFfmpeg(doc) {
  return (doc?.render_runtime || 'ffmpeg') === 'ffmpeg'
}

/**
 * Audio tracks for the SOURCE preview's hidden <audio> elements: the music bed + narration
 * segments + SFX, each with a stable `key`, the `src` asset id, its window [start,end], and
 * volume. Like `audioClips` but carries volume and uses OPEN-ENDED windows (music/sfx stop at
 * their own asset end, which only the loaded element knows). Items with no asset_id are skipped.
 */
export function previewAudioTracks(doc) {
  const a = doc?.audio || {}
  const list = []
  // Music: one <audio> per region (audio.music may be a single object OR an array; legacy
  // top-level doc.music is the fallback). Each carries its [start,end] window so a trimmed/split
  // region only plays inside it (StudioPreview's syncAudioEls honors the end). Unset end ⇒ plays to
  // the asset end (Infinity here; the element's own duration bounds it).
  const rawMusic = a.music != null ? a.music : doc?.music
  const musicList = (rawMusic == null ? [] : Array.isArray(rawMusic) ? rawMusic : [rawMusic]).filter(r => r && typeof r === 'object')
  musicList.forEach((m, i) => {
    if (!m.asset_id) return
    list.push({
      key: `music${i}`, kind: 'music', src: m.asset_id,
      start: Math.max(0, Number(m.start_seconds) || 0),
      end: m.end_seconds != null ? Number(m.end_seconds) : Infinity,
      volume: m.volume ?? 1,
    })
  })
  ;(a.narration?.segments || []).forEach((s, i) => {
    if (s?.asset_id) list.push({ key: `n${i}`, kind: 'narration', src: s.asset_id, start: Math.max(0, Number(s.start_seconds) || 0), end: s.end_seconds != null ? Number(s.end_seconds) : Infinity, volume: 1 })
  })
  ;(a.sfx || []).forEach((s, i) => {
    if (s?.asset_id) list.push({ key: `s${i}`, kind: 'sfx', src: s.asset_id, start: Math.max(0, Number(s.start_seconds) || 0), end: Infinity, volume: s.volume ?? 1 })
  })
  return list
}

// Fixed top→bottom order of the audio sub-lanes on the timeline. Music sits at the top (the
// bed under everything), then narration (voice), then SFX (point cues).
export const AUDIO_LANE_ORDER = ['music', 'narration', 'sfx']

/**
 * Group the flat `interp.audioClips(doc)` list into ONE row per audio KIND, in AUDIO_LANE_ORDER,
 * dropping kinds with no items. The timeline draws each row on its own lane so a full-width music
 * bed and a full-width narration segment (and SFX point markers) no longer stack in a single row
 * and occlude each other. Pure — placement math stays in the component; this is only the split.
 * Returns [{ kind, items }]; empty array when there's no audio at all (caller shows the hint).
 */
export function groupAudioLanes(audioItems = []) {
  return AUDIO_LANE_ORDER
    .map(kind => ({ kind, items: audioItems.filter(a => a.kind === kind) }))
    .filter(row => row.items.length > 0)
}

/** Convert a named anchor (text-overlay form) to an {x,y,width} object — image/video
 * overlays MUST carry an object position (the renderer rejects a string anchor for them). */
export function anchorToXY(anchor, canvas) {
  const w = canvas?.width || 1920
  const h = canvas?.height || 1080
  const cols = { left: Math.round(w * 0.05), center: Math.round(w * 0.4), right: Math.round(w * 0.7) }
  const rows = { top: Math.round(h * 0.05), center: Math.round(h * 0.45), bottom: Math.round(h * 0.85) }
  const parts = String(anchor).split('-')
  const v = parts[0] || 'center'
  const hpart = parts[1] || 'center'
  return { x: cols[hpart] ?? cols.center, y: rows[v] ?? rows.center, width: Math.round(w * 0.25) }
}

// ── Main-clip placement on the canvas (move + resize) ────────────────────────
// A main cut is composited as a box on the project background. At scale=1 the box is the clip
// "fit" size (contain) — identical to the legacy full-frame letterbox. transform.scale multiplies
// the fit size; transform.position is the box top-left in canvas px (or a named anchor string).
// These mirror the renderer's geometry so the preview == the export.

/** The clip "fit" (contain) size in canvas px — the box at scale=1. Unknown dims ⇒ the canvas. */
export function clipFitSize(meta, canvas) {
  const cw = canvas?.width || 1920, ch = canvas?.height || 1080
  const sw = Number(meta?.width) || cw, sh = Number(meta?.height) || ch
  const r = Math.min(cw / sw, ch / sh)
  return { width: Math.round(sw * r), height: Math.round(sh * r) }
}

const evenDim = (n) => Math.max(2, Math.round(Number(n) / 2) * 2)

/**
 * Whether a `transform.scale` value is the per-axis OBJECT form (non-uniform box). Mirrors the
 * schema oneOf: a uniform number scales the fit-size; an {x,y} object is a canvas-fraction box.
 */
export function isScaleObject(scale) {
  return scale != null && typeof scale === 'object'
}

/**
 * Resolve `transform.scale` (a uniform number OR an {x,y} object) to per-axis multipliers
 * {sx, sy}. A uniform number → sx === sy === n; an object → its x/y (each defaulting to 1 and
 * floored at 0). Non-positive / non-finite members fall back to 1 so a half-formed value can't
 * collapse a box to zero. Pure mirror of the renderer's `_pos_float` split in video_compose.py.
 */
export function scaleAxes(scale = 1) {
  const ax = (v) => { const f = Number(v); return Number.isFinite(f) && f > 0 ? f : 1 }
  if (isScaleObject(scale)) return { sx: ax(scale.x ?? 1), sy: ax(scale.y ?? 1) }
  const n = ax(scale)
  return { sx: n, sy: n }
}

/**
 * The clip box in canvas px, even dims ≥ 2 (matches the renderer):
 *  - UNIFORM number → fit-size × scale (the legacy centered-letterbox box; at scale=1 = the fit).
 *  - {x,y} OBJECT → a CANVAS-fraction box (canvas.width·sx × canvas.height·sy), e.g. a split-screen
 *    panel {x:1,y:0.5} = full-width half-height. The clip later fits INSIDE this box aspect-preserved,
 *    so the box is the panel, NOT the clip — this mirrors video_compose's `boxw=target_w*sx` path.
 */
export function clipBox(meta, canvas, scale = 1) {
  const cw = canvas?.width || 1920, ch = canvas?.height || 1080
  if (isScaleObject(scale)) {
    const { sx, sy } = scaleAxes(scale)
    return { width: evenDim(cw * sx), height: evenDim(ch * sy) }
  }
  const fit = clipFitSize(meta, canvas)
  const s = Number(scale) || 1
  return { width: evenDim(fit.width * s), height: evenDim(fit.height * s) }
}

/** Centered box top-left in canvas px (the default placement = legacy centered letterbox). */
export function clipDefaultPosition(meta, canvas, scale = 1) {
  const box = clipBox(meta, canvas, scale)
  const cw = canvas?.width || 1920, ch = canvas?.height || 1080
  return { x: Math.round((cw - box.width) / 2), y: Math.round((ch - box.height) / 2) }
}

/** A named anchor → box top-left in canvas px (flush to edges, margin 0 — matches the renderer). */
export function clipAnchorXY(anchor, meta, canvas, scale = 1) {
  const box = clipBox(meta, canvas, scale)
  const cw = canvas?.width || 1920, ch = canvas?.height || 1080
  const a = String(anchor || 'center')
  const x = a.endsWith('left') ? 0 : a.endsWith('right') ? cw - box.width : Math.round((cw - box.width) / 2)
  const y = a.startsWith('top') ? 0 : a.startsWith('bottom') ? ch - box.height : Math.round((ch - box.height) / 2)
  return { x, y }
}

/** Resolve a cut's placement to {x,y} box top-left (object position as-is, else the named anchor).
 * `scale` is passed THROUGH to clipBox (number OR {x,y}) so the anchored box uses the right dims. */
export function clipPositionXY(cut, meta, canvas) {
  const t = cut?.transform || {}
  const scale = t.scale != null ? t.scale : 1
  const pos = t.position
  if (pos && typeof pos === 'object' && pos.x != null) return { x: Number(pos.x) || 0, y: Number(pos.y) || 0 }
  return clipAnchorXY(typeof pos === 'string' ? pos : 'center', meta, canvas, scale)
}

/** mm:ss.c — compact tabular timecode for rulers/labels. */
export function fmtTime(sec) {
  const s = Math.max(0, Number(sec) || 0)
  const m = Math.floor(s / 60)
  const r = s - m * 60
  return `${m}:${r.toFixed(1).padStart(4, '0')}`
}

export function round3(x) {
  return Math.round(Number(x) * 1000) / 1000
}

export function clamp(x, lo, hi) {
  return Math.max(lo, Math.min(hi, Number(x)))
}

// ── Drag-to-scrub number fields (inspector) ──────────────────────────────────
// The properties panel lets you DRAG a number to change it (After-Effects/CapCut style) AND
// still type it manually. The drag→value math is pure + testable here; the component only does
// pointer plumbing. Each 1px of horizontal drag ≈ one `step`; holding Shift (`fine`) moves 5×
// slower for precision. The result is snapped to the increment grid + clamped to [min,max].

/** Decimal places implied by a step (0.05 → 2, 1 → 0), capped at 6 to avoid float noise. */
export function decimalsOf(step) {
  if (!Number.isFinite(step) || step <= 0) return 0
  const s = String(step)
  const i = s.indexOf('.')
  return i < 0 ? 0 : Math.min(6, s.length - i - 1)
}

/** Round x to the decimal precision implied by `step` (kills 0.1+0.2 float artifacts). */
export function roundTo(x, step) {
  return Number((Number(x) || 0).toFixed(decimalsOf(step)))
}

/**
 * New value for a scrub drag: `start` (value at pointer-down) + `dx` px of drag, at `step` per px
 * (Shift = `fine`, 5× slower). Snapped to the increment grid and clamped to [min,max] when finite.
 */
export function scrubValue({ start, dx, step = 1, min, max, fine = false } = {}) {
  const s = Number.isFinite(step) && step > 0 ? step : 1
  const per = fine ? s / 5 : s
  let v = (Number(start) || 0) + (Number(dx) || 0) * per
  v = Math.round(v / per) * per                       // snap to the (fine-aware) increment grid
  if (Number.isFinite(min)) v = Math.max(min, v)
  if (Number.isFinite(max)) v = Math.min(max, v)
  return roundTo(v, per)
}

/** Compact display for a scrub value: drops trailing zeros, '' for non-numbers (= unset/auto). */
export function fmtScrub(v) {
  if (v === '' || v == null) return ''
  const n = Number(v)
  if (!Number.isFinite(n)) return ''
  return Number.isInteger(n) ? String(n) : String(Number(n.toFixed(3)))
}
