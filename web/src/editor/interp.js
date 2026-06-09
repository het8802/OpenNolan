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

const round3 = (x) => Math.round(Number(x) * 1000) / 1000

// Schema field whitelists (keep in sync with schemas/artifacts/edit_decisions.schema.json).
const CUT_FIELDS = ['id', 'source', 'in_seconds', 'out_seconds', 'speed', 'layer',
  'transform', 'transition_in', 'transition_out', 'transition_duration', 'reason']
const OVERLAY_FIELDS = ['asset_id', 'start_seconds', 'end_seconds', 'position',
  'animation', 'opacity', 'keyframes']
const POSITION_FIELDS = ['x', 'y', 'width', 'height']
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
  }
  return c
}

/** Sanitize an overlay (incl. nested position + keyframes) to schema-known fields. */
export function sanitizeOverlay(ov) {
  const o = pick(ov, OVERLAY_FIELDS)
  if (o.position && typeof o.position === 'object') o.position = pick(o.position, POSITION_FIELDS)
  if (Array.isArray(o.keyframes)) o.keyframes = o.keyframes.map(k => pick(k, KEYFRAME_FIELDS))
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
  const overlays = (doc.overlays || []).map((o, i) =>
    (i === index ? sanitizeOverlay({ ...o, ...patch }) : o))
  return { ...doc, overlays }
}

export function setOverlayKeyframes(doc, index, keyframes) {
  return updateOverlay(doc, index, { keyframes: keyframes.map(k => pick(k, KEYFRAME_FIELDS)) })
}

export function upsertKeyframe(doc, index, kf) {
  const ov = (doc.overlays || [])[index]
  if (!ov) return doc
  const kfs = [...(ov.keyframes || [])]
  const at = kfs.findIndex(k => Number(k.t) === Number(kf.t))
  const clean = pick(kf, KEYFRAME_FIELDS)
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
