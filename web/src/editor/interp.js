// Pure timeline core for the manual editor. NO React, NO DOM — unit-tested in isolation.
//
// `interpolateAt` mirrors the FFmpeg renderer's `_piecewise_linear_expr`
// (tools/video/video_compose.py): LINEAR interpolation between keyframes, values held
// constant outside the first/last keyframe. The `easing` field is stored but the FFmpeg
// path renders linearly (same documented limitation as scale/rotation), so the preview
// curve we draw is linear too — preview must match export.
//
// The mutators return NEW documents (never mutate in place) and only ever emit fields the
// edit_decisions schema allows (additionalProperties:false on cuts/overlays/keyframes), so
// a Save can never be rejected for an unknown key the UI invented.

export const EASINGS = ['linear', 'ease-in', 'ease-out', 'ease-in-out', 'spring', 'step']
export const KEYFRAME_DIMS = ['x', 'y', 'scale', 'rotation', 'opacity']

// Shortest allowed clip / split fragment, in SOURCE seconds. Keeps trims and splits from
// producing zero/negative-length cuts that the schema (out > in) or renderer would reject.
export const MIN_SOURCE_SPAN = 0.1

// Shortest allowed overlay span, in PROJECT seconds. Keeps an overlay drag/trim from
// collapsing to zero/negative length (which the schema would still accept but is useless).
export const MIN_OVERLAY_SPAN = 0.1

const round3 = (x) => Math.round(Number(x) * 1000) / 1000

// Schema field whitelists (keep in sync with schemas/artifacts/edit_decisions.schema.json).
const CUT_FIELDS = ['id', 'source', 'in_seconds', 'out_seconds', 'speed', 'layer',
  'transform', 'transition_in', 'transition_out', 'transition_duration', 'reason']
// Overlays carry BOTH the asset-backed fields (image/video) and the text-overlay fields
// (type=text → text/font/color/box) plus per-overlay audio_mix. The old editor whitelisted
// only the asset fields, which silently dropped text/box/audio_mix on save; the studio editor
// edits those, so they must survive sanitization.
const OVERLAY_FIELDS = ['type', 'asset_id', 'text', 'font_path', 'font_size', 'color', 'box',
  'start_seconds', 'end_seconds', 'position', 'animation', 'opacity', 'track', 'audio_mix', 'keyframes']
const POSITION_FIELDS = ['x', 'y', 'width', 'height']
const BOX_FIELDS = ['color', 'opacity', 'padding']
const AUDIO_MIX_FIELDS = ['enabled', 'volume']
const CROP_FIELDS = ['x', 'y', 'width', 'height']
const KEYFRAME_FIELDS = ['t', 'x', 'y', 'scale', 'rotation', 'opacity', 'easing']

function pick(obj, allowed) {
  const out = {}
  for (const k of allowed) if (obj[k] !== undefined) out[k] = obj[k]
  return out
}

/** Sanitize a cut to only schema-known fields (position-style nesting handled for transform). */
export function sanitizeCut(cut) {
  const c = pick(cut, CUT_FIELDS)
  if (c.transform && typeof c.transform === 'object') {
    c.transform = pick(c.transform, ['scale', 'position', 'animation', 'crop'])
    if (c.transform.crop && typeof c.transform.crop === 'object') {
      c.transform.crop = pick(c.transform.crop, CROP_FIELDS)
    }
  }
  return c
}

/** Pick + value-coerce a single keyframe so no UI path can emit a schema-invalid value
 * (t>=0, scale>=0, opacity in [0,1]; x/y/rotation unbounded). The schema is strict, so
 * clamping HERE — at the one chokepoint every keyframe flows through — keeps the
 * "a Save can never be rejected" invariant honest for VALUES, not just keys. */
function cleanKeyframe(k) {
  const o = pick(k, KEYFRAME_FIELDS)
  if (o.t != null) o.t = Math.max(0, Number(o.t))
  if (o.scale != null) o.scale = Math.max(0, Number(o.scale))
  if (o.opacity != null) o.opacity = Math.max(0, Math.min(1, Number(o.opacity)))
  return o
}

/** Sanitize an overlay (incl. nested position/box/audio_mix/keyframes) to schema-known
 * fields AND schema-valid values. `position` may be a STRING (named anchor, text overlays)
 * or an OBJECT ({x,y,width,height}). font_size/box.padding are schema integers, so we round. */
export function sanitizeOverlay(ov) {
  const o = pick(ov, OVERLAY_FIELDS)
  if (o.position && typeof o.position === 'object') o.position = pick(o.position, POSITION_FIELDS)
  if (o.box && typeof o.box === 'object') o.box = pick(o.box, BOX_FIELDS)
  if (o.audio_mix && typeof o.audio_mix === 'object') o.audio_mix = pick(o.audio_mix, AUDIO_MIX_FIELDS)
  if (o.font_size != null) o.font_size = Math.round(Number(o.font_size))
  if (o.box && o.box.padding != null) o.box.padding = Math.round(Number(o.box.padding))
  if (o.track != null) o.track = Math.max(0, Math.round(Number(o.track) || 0)) // z-layer (non-negative int)
  if (Array.isArray(o.keyframes)) o.keyframes = o.keyframes.map(cleanKeyframe)
  return o
}

/**
 * Linear-interpolated value of `dim` at project time `t`, or null if the dimension is not
 * animated by any keyframe. Held constant before the first / after the last keyframe.
 */
export function interpolateAt(keyframes, dim, t) {
  if (!Array.isArray(keyframes)) return null
  const pts = keyframes
    .filter(k => k && k.t != null && k[dim] != null)
    .map(k => [Number(k.t), Number(k[dim])])
    .sort((a, b) => a[0] - b[0])
  if (pts.length === 0) return null
  if (pts.length === 1) return pts[0][1]
  if (t <= pts[0][0]) return pts[0][1]
  if (t >= pts[pts.length - 1][0]) return pts[pts.length - 1][1]
  for (let i = 0; i < pts.length - 1; i++) {
    const [t0, v0] = pts[i]
    const [t1, v1] = pts[i + 1]
    if (t >= t0 && t <= t1) {
      if (t1 === t0) return v1
      return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    }
  }
  return pts[pts.length - 1][1]
}

// ── immutable mutators ──────────────────────────────────────────────────────

export function updateCut(doc, cutId, patch) {
  const cuts = (doc.cuts || []).map(c => (c.id === cutId ? sanitizeCut({ ...c, ...patch }) : c))
  return { ...doc, cuts }
}

export function updateOverlay(doc, index, patch) {
  // Out-of-range index (e.g. a selection gone stale after undo/redo) is a no-op — return
  // the SAME doc so commit()'s referential guard suppresses a spurious history/dirty entry.
  if (index < 0 || index >= (doc.overlays || []).length) return doc
  const overlays = (doc.overlays || []).map((o, i) =>
    (i === index ? sanitizeOverlay({ ...o, ...patch }) : o))
  return { ...doc, overlays }
}

export function setOverlayKeyframes(doc, index, keyframes) {
  return updateOverlay(doc, index, { keyframes: keyframes.map(cleanKeyframe) })
}

export function upsertKeyframe(doc, index, kf) {
  const ov = (doc.overlays || [])[index]
  if (!ov) return doc
  const kfs = [...(ov.keyframes || [])]
  const clean = cleanKeyframe(kf)
  const at = kfs.findIndex(k => Number(k.t) === Number(clean.t))
  if (at >= 0) kfs[at] = { ...kfs[at], ...clean }
  else kfs.push(clean)
  kfs.sort((a, b) => Number(a.t) - Number(b.t))
  return setOverlayKeyframes(doc, index, kfs)
}

export function removeKeyframe(doc, index, kfIndex) {
  const ov = (doc.overlays || [])[index]
  if (!ov || !Array.isArray(ov.keyframes)) return doc
  return setOverlayKeyframes(doc, index, ov.keyframes.filter((_, i) => i !== kfIndex))
}

/** A minimal valid edit_decisions for a fresh manual project (passes the schema's required set). */
export function scaffoldEditDecisions({ runtime = 'ffmpeg', source = 'clip.mp4', duration = 5 } = {}) {
  return {
    version: '1.0',
    render_runtime: runtime,
    // renderer_family is optional in the schema but REQUIRED by video_compose's pre-compose
    // gate — set it so a fresh manual project can render without a cryptic block.
    renderer_family: 'social-reel',
    cuts: [{ id: 'c1', source, in_seconds: 0, out_seconds: duration }],
  }
}

/** A cut's PROJECT-timeline duration: (out - in) / speed. (in/out are SOURCE offsets.) */
export function cutDuration(cut) {
  const span = (Number(cut.out_seconds) || 0) - (Number(cut.in_seconds) || 0)
  const speed = Number(cut.speed) || 1
  return Math.max(0, span) / (speed > 0 ? speed : 1)
}

/**
 * Total project duration. Cuts CONCATENATE (sum of their project durations); overlays sit
 * on absolute project time (end_seconds). The timeline length is the larger of the two.
 */
export function timelineDuration(doc) {
  let cuts = 0
  for (const c of (doc?.cuts || [])) cuts += cutDuration(c)
  let overlays = 0
  for (const o of (doc?.overlays || [])) overlays = Math.max(overlays, Number(o.end_seconds) || 0)
  return Math.max(cuts, overlays)
}

/** Per-cut project start times (cumulative concatenation), parallel to doc.cuts. */
export function cutStarts(doc) {
  const starts = []
  let acc = 0
  for (const c of (doc?.cuts || [])) { starts.push(acc); acc += cutDuration(c) }
  return starts
}

/**
 * Map a PROJECT time `t` to the cut playing then + the SOURCE time inside that cut.
 * Returns `{ cut, index, start, localProject, sourceTime }` or null if `t` is outside the
 * concatenated timeline. The very end of the timeline maps to the last cut (inclusive), so
 * scrubbing to the end still shows a frame.
 *
 *   sourceTime = cut.in_seconds + (t - cutStart) * speed
 *
 * This is the inverse of the concat layout `cutStarts` builds, and the seam the source-scrub
 * preview and the splitter both ride on.
 */
export function cutAtTime(doc, t) {
  const cuts = doc?.cuts || []
  if (cuts.length === 0) return null
  const starts = cutStarts(doc)
  const tt = Math.max(0, Number(t) || 0)
  for (let i = 0; i < cuts.length; i++) {
    const start = starts[i]
    const end = start + cutDuration(cuts[i])
    const last = i === cuts.length - 1
    if (tt >= start && (tt < end || (last && tt <= end + 1e-6))) {
      const speed = Number(cuts[i].speed) || 1
      const localProject = Math.max(0, tt - start)
      const sourceTime = (Number(cuts[i].in_seconds) || 0) + localProject * (speed > 0 ? speed : 1)
      return { cut: cuts[i], index: i, start, localProject, sourceTime }
    }
  }
  return null
}

/**
 * Trim a cut's in/out point (drag-a-handle). `patch` carries the edge being moved
 * (`in_seconds` and/or `out_seconds`); values are clamped so:
 *   - in >= 0
 *   - out <= sourceDuration (when known — can't keep footage that doesn't exist)
 *   - the moved edge never crosses the other within MIN_SOURCE_SPAN.
 * Returns the original doc unchanged if the cut id isn't found.
 */
export function trimCut(doc, cutId, patch, { sourceDuration } = {}) {
  const cut = (doc?.cuts || []).find(c => c.id === cutId)
  if (!cut) return doc
  let inS = patch.in_seconds != null ? Number(patch.in_seconds) : (Number(cut.in_seconds) || 0)
  let outS = patch.out_seconds != null ? Number(patch.out_seconds) : (Number(cut.out_seconds) || 0)
  const hasMax = sourceDuration != null && Number.isFinite(sourceDuration)
  if (hasMax) outS = Math.min(outS, sourceDuration)
  inS = Math.max(0, inS)
  if (patch.in_seconds != null) inS = Math.min(inS, outS - MIN_SOURCE_SPAN)
  if (patch.out_seconds != null) outS = Math.max(outS, inS + MIN_SOURCE_SPAN)
  inS = Math.max(0, inS)
  return updateCut(doc, cutId, { in_seconds: round3(inS), out_seconds: round3(outS) })
}

/** Generate a cut id derived from `base` that doesn't collide with any existing cut id. */
function uniqueCutId(doc, base) {
  const ids = new Set((doc?.cuts || []).map(c => c.id))
  let n = 2
  let id = `${base}-${n}`
  while (ids.has(id)) { n += 1; id = `${base}-${n}` }
  return id
}

/**
 * Split the cut under PROJECT time `t` into two cuts at the mapped source time. The first
 * keeps in→split, the second split→out (new id). A mid-clip transition would double-apply,
 * so the first half drops transition_out and the second drops transition_in.
 * Returns the original doc unchanged when `t` isn't strictly inside a cut (needs MIN_SOURCE_SPAN
 * on both halves) — callers use referential equality to detect the no-op.
 */
export function splitCutAtPlayhead(doc, t) {
  const hit = cutAtTime(doc, t)
  if (!hit) return doc
  const { cut, index, sourceTime } = hit
  const inS = Number(cut.in_seconds) || 0
  const outS = Number(cut.out_seconds) || 0
  if (sourceTime <= inS + MIN_SOURCE_SPAN || sourceTime >= outS - MIN_SOURCE_SPAN) return doc
  const split = round3(sourceTime)
  const newId = uniqueCutId(doc, cut.id || `c${index + 1}`)
  const first = sanitizeCut({ ...cut, out_seconds: split })
  delete first.transition_out
  const second = sanitizeCut({ ...cut, in_seconds: split, id: newId })
  delete second.transition_in
  const cuts = [...(doc.cuts || [])]
  cuts.splice(index, 1, first, second)
  return { ...doc, cuts }
}

// ── clip-level structural mutators (studio editor) ──────────────────────────

/** Remove a cut by id. Returns the doc unchanged if not found. Callers guard the last cut. */
export function removeCut(doc, cutId) {
  const cuts = (doc?.cuts || []).filter(c => c.id !== cutId)
  if (cuts.length === (doc?.cuts || []).length) return doc
  return { ...doc, cuts }
}

/** Duplicate a cut (fresh unique id), inserting the copy directly after the original. */
export function duplicateCut(doc, cutId) {
  const cuts = doc?.cuts || []
  const i = cuts.findIndex(c => c.id === cutId)
  if (i < 0) return doc
  const clone = sanitizeCut({ ...cuts[i], id: uniqueCutId(doc, cuts[i].id || `c${i + 1}`) })
  const next = [...cuts]
  next.splice(i + 1, 0, clone)
  return { ...doc, cuts: next }
}

/** Move the cut at `fromIndex` to `toIndex` (drag-reorder). Clamped; no-op if out of range. */
export function reorderCut(doc, fromIndex, toIndex) {
  const cuts = [...(doc?.cuts || [])]
  if (fromIndex < 0 || fromIndex >= cuts.length) return doc
  const [moved] = cuts.splice(fromIndex, 1)
  const dest = Math.max(0, Math.min(cuts.length, toIndex))
  cuts.splice(dest, 0, moved)
  return { ...doc, cuts }
}

/** Append a cut (id forced unique against existing cuts) at `atIndex` (default end). */
export function addCut(doc, cut, atIndex) {
  const cuts = [...(doc?.cuts || [])]
  const keepId = cut.id && !cuts.some(x => x.id === cut.id)
  const c = sanitizeCut({ ...cut, id: keepId ? cut.id : uniqueCutId(doc, cut.id || 'c') })
  const at = atIndex == null ? cuts.length : Math.max(0, Math.min(cuts.length, atIndex))
  cuts.splice(at, 0, c)
  return { ...doc, cuts }
}

/** Append a sanitized overlay. Returns the new doc; index of the new overlay = overlays.length-1. */
export function addOverlay(doc, overlay) {
  const overlays = [...(doc?.overlays || []), sanitizeOverlay(overlay)]
  return { ...doc, overlays }
}

/** Remove the overlay at `index`. No-op (same doc) if out of range. */
export function removeOverlay(doc, index) {
  if (index < 0 || index >= (doc?.overlays || []).length) return doc
  const overlays = (doc.overlays || []).filter((_, i) => i !== index)
  return { ...doc, overlays }
}

/**
 * Overlay track usage — the z-layers in play. `max` is the highest track index any overlay
 * sits on (0 when there are none); `count` = max+1 lanes to draw. The UI renders one lane per
 * track (highest on top, matching the renderer's ascending-track = on-top z-order) plus an
 * empty lane above for adding. Track is the ONLY stored field; lane pixel rows are derived.
 */
export function overlayTracks(doc) {
  let max = 0
  for (const o of (doc?.overlays || [])) max = Math.max(max, Math.max(0, Math.round(Number(o?.track) || 0)))
  return { max, count: max + 1 }
}

/**
 * Move an overlay in ABSOLUTE project time and/or change its track (z-layer). `start` sets the
 * new start (end follows, preserving duration; start clamped >= 0). `track` sets the z-layer
 * (clamped to a non-negative int). Pass either or both. No-op (same doc ref) if out of range or
 * nothing changes — so a coalesced drag that lands where it began adds no history/dirty entry.
 * Overlays sit on absolute time (unlike cuts, which concatenate), so there's no cutStarts math.
 */
export function moveOverlay(doc, index, { start, track } = {}) {
  const overlays = doc?.overlays || []
  if (index < 0 || index >= overlays.length) return doc
  const ov = overlays[index]
  const patch = {}
  if (start != null) {
    const s = Number(ov.start_seconds) || 0
    const e = Number(ov.end_seconds) || s
    const dur = Math.max(0, e - s)
    const ns = Math.max(0, round3(Number(start)))
    if (ns !== round3(s)) { // value-compare so a wiggle-and-return drag is a true no-op (same ref)
      patch.start_seconds = ns
      patch.end_seconds = round3(ns + dur)
      // Keyframe `t` is ABSOLUTE project time (same axis as start_seconds), so authored
      // motion/fades must travel WITH the clip — shift every keyframe by the SAME (clamped)
      // delta, else a time-move silently desyncs the animation from its new window.
      const delta = ns - s
      if (Array.isArray(ov.keyframes) && ov.keyframes.length) {
        patch.keyframes = ov.keyframes.map(k =>
          (k && k.t != null ? { ...k, t: Math.max(0, round3(Number(k.t) + delta)) } : k))
      }
    }
  }
  if (track != null) {
    const nt = Math.max(0, Math.round(Number(track)))
    if (nt !== Math.max(0, Math.round(Number(ov.track) || 0))) patch.track = nt
  }
  if (patch.start_seconds == null && patch.track == null) return doc
  return updateOverlay(doc, index, patch)
}

/**
 * Trim an overlay's start/end edge (drag-a-handle), on absolute project time. The moved edge is
 * clamped so start >= 0 and the two edges never cross within MIN_OVERLAY_SPAN. No-op if out of
 * range. (Unlike `trimCut`, there's no source window — an overlay can run as long as you like.)
 */
export function trimOverlay(doc, index, patch) {
  const overlays = doc?.overlays || []
  if (index < 0 || index >= overlays.length) return doc
  const ov = overlays[index]
  let s = patch.start_seconds != null ? Number(patch.start_seconds) : (Number(ov.start_seconds) || 0)
  let e = patch.end_seconds != null ? Number(patch.end_seconds) : (Number(ov.end_seconds) || 0)
  s = Math.max(0, s)
  if (patch.start_seconds != null) s = Math.min(s, e - MIN_OVERLAY_SPAN)
  if (patch.end_seconds != null) e = Math.max(e, s + MIN_OVERLAY_SPAN)
  s = Math.max(0, s)
  return updateOverlay(doc, index, { start_seconds: round3(s), end_seconds: round3(e) })
}

/** Set the output canvas (metadata.compose_target). Merges so unspecified dims are kept. */
export function setCanvas(doc, { width, height, fps } = {}) {
  const meta = { ...(doc.metadata || {}) }
  const ct = { ...(meta.compose_target || {}) }
  if (width != null) ct.width = Math.round(width)
  if (height != null) ct.height = Math.round(height)
  if (fps != null) ct.fps = fps
  meta.compose_target = ct
  return { ...doc, metadata: meta }
}

/** Read the effective output canvas. Mirrors the renderer's fallback (1920x1080@30). */
export function canvasOf(doc) {
  const ct = doc?.metadata?.compose_target || {}
  return {
    width: Number(ct.width) || 1920,
    height: Number(ct.height) || 1080,
    fps: Number(ct.fps) || 30,
  }
}

/**
 * Read-only projection of the schema's audio model into items the timeline can DRAW on an
 * audio lane (music / narration / sfx). This is NOT a render contract — the FFmpeg path owns
 * mixing; this only places blocks so the user can SEE what audio sits where. Placement is
 * absolute project time (same axis as overlays), and — like overlay/cut placement — it is
 * DERIVED, never stored. Returns a flat array sorted by start, each:
 *   { kind:'music'|'narration'|'sfx', asset_id, start_seconds, end_seconds, point }
 *  - music: a single bed spanning [0, timelineDuration]. The FFmpeg amix uses duration=first,
 *    so the bed is cut to the base (video) length — we draw it that way too (preview==export).
 *    Reads doc.audio.music first, then legacy top-level doc.music. Skipped without an asset_id.
 *  - narration: doc.audio.narration.segments[] → start→end blocks (end defaults to start).
 *  - sfx: doc.audio.sfx[] → point markers (point:true, end_seconds === start_seconds).
 * Items with no asset_id are skipped — there's nothing to show.
 */
// Audio field whitelists + value coercion (keep in sync with edit_decisions.schema.json audio.*).
const MUSIC_FIELDS = ['asset_id', 'volume', 'fade_in_seconds', 'fade_out_seconds', 'ducking']
const NARRATION_FIELDS = ['asset_id', 'start_seconds', 'end_seconds']
const SFX_FIELDS = ['asset_id', 'start_seconds', 'volume']

function cleanMusic(m) {
  const o = pick(m, MUSIC_FIELDS)
  if (o.volume != null) o.volume = Math.max(0, Math.min(1, Number(o.volume)))
  if (o.fade_in_seconds != null) o.fade_in_seconds = Math.max(0, Number(o.fade_in_seconds))
  if (o.fade_out_seconds != null) o.fade_out_seconds = Math.max(0, Number(o.fade_out_seconds))
  return o
}
function cleanNarration(s) {
  const o = pick(s, NARRATION_FIELDS)
  if (o.start_seconds != null) o.start_seconds = round3(Math.max(0, Number(o.start_seconds)))
  if (o.end_seconds != null) o.end_seconds = round3(Math.max(0, Number(o.end_seconds)))
  return o
}
function cleanSfx(s) {
  const o = pick(s, SFX_FIELDS)
  if (o.start_seconds != null) o.start_seconds = round3(Math.max(0, Number(o.start_seconds)))
  if (o.volume != null) o.volume = Math.max(0, Math.min(1, Number(o.volume)))
  return o
}

// Seed the bed from audio.music, FALLING BACK to the legacy top-level doc.music, so editing a
// legacy-shaped bed doesn't drop its asset_id (audioClips reads either shape). Collapse the legacy
// field afterward so there's one source of truth.
function _withMusic(doc, fields) {
  const audio = { ...(doc?.audio || {}) }
  const base = audio.music || doc?.music || {}
  audio.music = cleanMusic({ ...base, ...fields })
  const next = { ...doc, audio }
  if (next.music !== undefined) delete next.music
  return next
}

/** Set the single music bed (audio.music.asset_id), preserving any other valid music fields. */
export function setMusic(doc, assetId) {
  return _withMusic(doc, { asset_id: assetId })
}

/** Merge a patch into the music bed. */
export function updateMusic(doc, patch) {
  return _withMusic(doc, patch)
}

/** Remove the music bed (both audio.music and the legacy top-level music). No-op if absent. */
export function removeMusic(doc) {
  const hasA = doc?.audio?.music !== undefined
  const hasLegacy = doc?.music !== undefined
  if (!hasA && !hasLegacy) return doc
  const next = { ...doc }
  if (hasA) { next.audio = { ...doc.audio }; delete next.audio.music }
  if (hasLegacy) delete next.music
  return next
}

/** Append a point SFX (audio.sfx[]) at project time `start`. */
export function addSfx(doc, assetId, start = 0) {
  const audio = { ...(doc?.audio || {}) }
  audio.sfx = [...(audio.sfx || []), cleanSfx({ asset_id: assetId, start_seconds: start })]
  return { ...doc, audio }
}

/** Merge a patch into a narration segment by index. No-op (same doc) if out of range. */
export function updateNarration(doc, index, patch) {
  const segs = doc?.audio?.narration?.segments || []
  if (index < 0 || index >= segs.length) return doc
  const segments = segs.map((s, i) => (i === index ? cleanNarration({ ...s, ...patch }) : s))
  return { ...doc, audio: { ...(doc.audio || {}), narration: { ...(doc.audio?.narration || {}), segments } } }
}

/** Remove a narration segment by index. No-op (same doc) if out of range. */
export function removeNarration(doc, index) {
  const segs = doc?.audio?.narration?.segments || []
  if (index < 0 || index >= segs.length) return doc
  const segments = segs.filter((_, i) => i !== index)
  return { ...doc, audio: { ...(doc.audio || {}), narration: { ...(doc.audio?.narration || {}), segments } } }
}

/** Merge a patch into an SFX by index. No-op (same doc) if out of range. */
export function updateSfx(doc, index, patch) {
  const sfx = doc?.audio?.sfx || []
  if (index < 0 || index >= sfx.length) return doc
  const next = sfx.map((s, i) => (i === index ? cleanSfx({ ...s, ...patch }) : s))
  return { ...doc, audio: { ...(doc.audio || {}), sfx: next } }
}

/** Remove an SFX by index. No-op (same doc) if out of range. */
export function removeSfx(doc, index) {
  const sfx = doc?.audio?.sfx || []
  if (index < 0 || index >= sfx.length) return doc
  const next = sfx.filter((_, i) => i !== index)
  return { ...doc, audio: { ...(doc.audio || {}), sfx: next } }
}

export function audioClips(doc) {
  const out = []
  const a = doc?.audio || {}
  const music = a.music || doc?.music // prefer audio.music; fall back to the legacy top-level
  if (music && music.asset_id) {
    out.push({ kind: 'music', index: null, asset_id: music.asset_id, start_seconds: 0, end_seconds: timelineDuration(doc), point: false })
  }
  ;(a.narration?.segments || []).forEach((seg, i) => {
    if (!seg || !seg.asset_id) return
    const s = Math.max(0, Number(seg.start_seconds) || 0)
    const e = seg.end_seconds != null ? Math.max(s, Number(seg.end_seconds)) : s
    out.push({ kind: 'narration', index: i, asset_id: seg.asset_id, start_seconds: round3(s), end_seconds: round3(e), point: false })
  })
  ;(a.sfx || []).forEach((fx, i) => {
    if (!fx || !fx.asset_id) return
    const s = round3(Math.max(0, Number(fx.start_seconds) || 0))
    out.push({ kind: 'sfx', index: i, asset_id: fx.asset_id, start_seconds: s, end_seconds: s, point: true })
  })
  return out.sort((x, y) => x.start_seconds - y.start_seconds)
}
