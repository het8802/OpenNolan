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

/** 'text' | 'image' — an overlay with explicit type wins; else text iff it has `text`. */
export function overlayKind(ov) {
  if (ov?.type === 'text') return 'text'
  if (ov?.type === 'image' || ov?.type === 'video') return 'image'
  return ov?.text != null && ov?.asset_id == null ? 'text' : 'image'
}

export function kfDimsFor(ov) {
  return overlayKind(ov) === 'text' ? KF_DIMS_TEXT : KF_DIMS_IMAGE
}

/** A schema-valid text overlay over [start,end]. position default bottom-center (anchor). */
export function newTextOverlay({ start = 0, end = 3 } = {}) {
  return {
    type: 'text',
    text: 'Your text',
    font_size: 64,
    color: 'white',
    box: { color: 'black', opacity: 0.5, padding: 12 },
    start_seconds: round3(start),
    end_seconds: round3(end),
    position: 'bottom-center',
    opacity: 1,
  }
}

/** A schema-valid image overlay (asset_id + OBJECT position; named anchors are text-only). */
export function newImageOverlay({ assetId, start = 0, end = 3, canvas } = {}) {
  const w = canvas?.width || 1920
  const h = canvas?.height || 1080
  return {
    type: 'image',
    asset_id: assetId,
    start_seconds: round3(start),
    end_seconds: round3(end),
    position: { x: Math.round(w * 0.05), y: Math.round(h * 0.05), width: Math.round(w * 0.25) },
    opacity: 1,
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
  const music = a.music || doc?.music
  if (music?.asset_id) list.push({ key: 'music', kind: 'music', src: music.asset_id, start: 0, end: Infinity, volume: music.volume ?? 1 })
  ;(a.narration?.segments || []).forEach((s, i) => {
    if (s?.asset_id) list.push({ key: `n${i}`, kind: 'narration', src: s.asset_id, start: Math.max(0, Number(s.start_seconds) || 0), end: s.end_seconds != null ? Number(s.end_seconds) : Infinity, volume: 1 })
  })
  ;(a.sfx || []).forEach((s, i) => {
    if (s?.asset_id) list.push({ key: `s${i}`, kind: 'sfx', src: s.asset_id, start: Math.max(0, Number(s.start_seconds) || 0), end: Infinity, volume: s.volume ?? 1 })
  })
  return list
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
