import { describe, it, expect } from 'vitest'
import {
  interpolateAt, sanitizeCut, sanitizeOverlay, updateCut, updateOverlay,
  upsertKeyframe, removeKeyframe, scaffoldEditDecisions, timelineDuration,
  cutDuration, cutStarts, cutAtTime, trimCut, splitCutAtPlayhead, MIN_SOURCE_SPAN,
} from './interp.js'

describe('interpolateAt (mirrors FFmpeg _piecewise_linear_expr)', () => {
  const kfs = [
    { t: 0, x: 0, opacity: 0 },
    { t: 1, x: 100, opacity: 1 },
    { t: 2, x: 100 },
  ]
  it('returns null for an un-animated dimension', () => {
    expect(interpolateAt(kfs, 'scale', 0.5)).toBeNull()
    expect(interpolateAt([], 'x', 0)).toBeNull()
  })
  it('holds the first value before the first keyframe', () => {
    expect(interpolateAt(kfs, 'x', -1)).toBe(0)
  })
  it('holds the last value after the last keyframe', () => {
    expect(interpolateAt(kfs, 'x', 5)).toBe(100)
  })
  it('linearly interpolates between keyframes', () => {
    expect(interpolateAt(kfs, 'x', 0.5)).toBe(50)
    expect(interpolateAt(kfs, 'opacity', 0.25)).toBeCloseTo(0.25, 6)
  })
  it('ignores keyframes that omit the dimension', () => {
    // opacity is only on t=0 and t=1; at t=2 it holds the last *specified* (t=1 => 1)
    expect(interpolateAt(kfs, 'opacity', 2)).toBe(1)
  })
  it('is constant with a single keyframe', () => {
    expect(interpolateAt([{ t: 3, x: 42 }], 'x', 0)).toBe(42)
    expect(interpolateAt([{ t: 3, x: 42 }], 'x', 99)).toBe(42)
  })
})

describe('sanitizers respect additionalProperties:false', () => {
  it('drops unknown cut fields', () => {
    const c = sanitizeCut({ id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 2, bogus: 9 })
    expect(c).not.toHaveProperty('bogus')
    expect(c).toMatchObject({ id: 'c1', source: 'a.mp4' })
  })
  it('drops unknown overlay + position + keyframe fields', () => {
    const o = sanitizeOverlay({
      asset_id: 'x', start_seconds: 0, end_seconds: 1, hacker: true,
      position: { x: 1, y: 2, evil: 3 },
      keyframes: [{ t: 0, x: 1, nope: 5 }],
    })
    expect(o).not.toHaveProperty('hacker')
    expect(o.position).not.toHaveProperty('evil')
    expect(o.keyframes[0]).not.toHaveProperty('nope')
    expect(o.keyframes[0]).toMatchObject({ t: 0, x: 1 })
  })
})

describe('immutable mutators', () => {
  const doc = {
    version: '1.0', render_runtime: 'ffmpeg',
    cuts: [{ id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 2 }],
    overlays: [{ asset_id: 'o1', start_seconds: 0, end_seconds: 2, position: { x: 0, y: 0 } }],
  }
  it('updateCut returns a new doc and edits the matching cut only', () => {
    const next = updateCut(doc, 'c1', { out_seconds: 3 })
    expect(next).not.toBe(doc)
    expect(next.cuts[0].out_seconds).toBe(3)
    expect(doc.cuts[0].out_seconds).toBe(2)  // original untouched
  })
  it('updateOverlay edits by index and sanitizes', () => {
    const next = updateOverlay(doc, 0, { opacity: 0.5, junk: 1 })
    expect(next.overlays[0].opacity).toBe(0.5)
    expect(next.overlays[0]).not.toHaveProperty('junk')
  })
  it('upsertKeyframe adds, sorts, and updates by t', () => {
    let next = upsertKeyframe(doc, 0, { t: 1, x: 100, opacity: 1 })
    next = upsertKeyframe(next, 0, { t: 0, x: 0, opacity: 0 })
    expect(next.overlays[0].keyframes.map(k => k.t)).toEqual([0, 1])
    next = upsertKeyframe(next, 0, { t: 1, x: 200 })  // update existing
    const kf1 = next.overlays[0].keyframes.find(k => k.t === 1)
    expect(kf1.x).toBe(200)
  })
  it('removeKeyframe drops the indexed keyframe', () => {
    let next = upsertKeyframe(doc, 0, { t: 0, x: 0 })
    next = upsertKeyframe(next, 0, { t: 1, x: 100 })
    next = removeKeyframe(next, 0, 0)
    expect(next.overlays[0].keyframes).toHaveLength(1)
    expect(next.overlays[0].keyframes[0].t).toBe(1)
  })
})

describe('scaffoldEditDecisions', () => {
  it('produces a doc that meets the schema required set (JS mirror)', () => {
    const d = scaffoldEditDecisions({ runtime: 'ffmpeg' })
    expect(d.version).toBe('1.0')
    expect(['remotion', 'hyperframes', 'ffmpeg']).toContain(d.render_runtime)
    expect(d.renderer_family).toBeTruthy()  // required by video_compose's pre-compose gate
    expect(Array.isArray(d.cuts) && d.cuts.length).toBeTruthy()
    for (const c of d.cuts) {
      expect(c).toMatchObject({ id: expect.any(String), source: expect.any(String) })
      expect(typeof c.in_seconds).toBe('number')
      expect(typeof c.out_seconds).toBe('number')
    }
  })
})

describe('cutAtTime (project time → cut + source time)', () => {
  const doc = {
    cuts: [
      { id: 'a', source: 'A.mp4', in_seconds: 2, out_seconds: 5 },             // project [0,3)
      { id: 'b', source: 'B.mp4', in_seconds: 0, out_seconds: 8, speed: 2 },   // 4s @2x => project [3,7)
    ],
  }
  it('returns null for an empty timeline', () => {
    expect(cutAtTime({ cuts: [] }, 1)).toBeNull()
    expect(cutAtTime({}, 1)).toBeNull()
  })
  it('maps into the first cut and adds the in-point offset', () => {
    const h = cutAtTime(doc, 1)
    expect(h.index).toBe(0)
    expect(h.sourceTime).toBeCloseTo(3, 6)   // in 2 + 1s elapsed
  })
  it('maps into a sped-up second cut (source advances faster)', () => {
    const h = cutAtTime(doc, 4)              // 1s into cut b at 2x
    expect(h.index).toBe(1)
    expect(h.sourceTime).toBeCloseTo(2, 6)   // in 0 + 1s*2
  })
  it('clamps the very end of the timeline to the last cut', () => {
    const h = cutAtTime(doc, 7)
    expect(h.index).toBe(1)
  })
})

describe('trimCut (drag a handle)', () => {
  const doc = { cuts: [{ id: 'c1', source: 'a.mp4', in_seconds: 1, out_seconds: 5 }] }
  it('moves the in-point and never lets in pass out by < MIN_SOURCE_SPAN', () => {
    const next = trimCut(doc, 'c1', { in_seconds: 2.5 })
    expect(next.cuts[0].in_seconds).toBe(2.5)
    const tooFar = trimCut(doc, 'c1', { in_seconds: 99 })
    expect(tooFar.cuts[0].in_seconds).toBeCloseTo(5 - MIN_SOURCE_SPAN, 6)
  })
  it('clamps in-point to >= 0', () => {
    expect(trimCut(doc, 'c1', { in_seconds: -3 }).cuts[0].in_seconds).toBe(0)
  })
  it('clamps the out-point to the source duration', () => {
    const next = trimCut(doc, 'c1', { out_seconds: 99 }, { sourceDuration: 6 })
    expect(next.cuts[0].out_seconds).toBe(6)
  })
  it('returns the same doc for an unknown cut', () => {
    expect(trimCut(doc, 'nope', { in_seconds: 0 })).toBe(doc)
  })
})

describe('splitCutAtPlayhead', () => {
  const doc = {
    version: '1.0', render_runtime: 'ffmpeg',
    cuts: [{ id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 6 }],
  }
  it('splits one cut into two at the mapped source time, with a unique id', () => {
    const next = splitCutAtPlayhead(doc, 2)   // single cut, 1x => source time 2
    expect(next).not.toBe(doc)
    expect(next.cuts).toHaveLength(2)
    expect(next.cuts[0]).toMatchObject({ id: 'c1', in_seconds: 0, out_seconds: 2 })
    expect(next.cuts[1]).toMatchObject({ in_seconds: 2, out_seconds: 6 })
    expect(next.cuts[1].id).not.toBe('c1')
    expect(next.cuts[1].source).toBe('a.mp4')
  })
  it('is a no-op (same ref) at the clip edges or outside any clip', () => {
    expect(splitCutAtPlayhead(doc, 0)).toBe(doc)        // left edge
    expect(splitCutAtPlayhead(doc, 6)).toBe(doc)        // right edge
    expect(splitCutAtPlayhead(doc, 99)).toBe(doc)       // past the end
    expect(splitCutAtPlayhead({ cuts: [] }, 1)).toEqual({ cuts: [] })
  })
  it('splits the correct cut in a multi-cut timeline and preserves order', () => {
    const multi = { cuts: [
      { id: 'a', source: 'A.mp4', in_seconds: 0, out_seconds: 3 },   // project [0,3)
      { id: 'b', source: 'B.mp4', in_seconds: 0, out_seconds: 4 },   // project [3,7)
    ] }
    const next = splitCutAtPlayhead(multi, 5)   // 2s into cut b
    expect(next.cuts.map(c => c.id.replace(/-\d+$/, ''))).toEqual(['a', 'b', 'b'])
    expect(next.cuts[1]).toMatchObject({ id: 'b', in_seconds: 0, out_seconds: 2 })
    expect(next.cuts[2]).toMatchObject({ in_seconds: 2, out_seconds: 4 })
  })
})

describe('project-time math', () => {
  it('cutDuration is (out-in)/speed', () => {
    expect(cutDuration({ in_seconds: 0, out_seconds: 4 })).toBe(4)
    expect(cutDuration({ in_seconds: 1, out_seconds: 5, speed: 2 })).toBe(2)  // 4s @2x = 2s
  })
  it('cutStarts accumulates project durations', () => {
    expect(cutStarts({ cuts: [{ in_seconds: 0, out_seconds: 3 }, { in_seconds: 0, out_seconds: 5 }] }))
      .toEqual([0, 3])
  })
  it('timelineDuration is max(concatenated cuts, overlay end)', () => {
    // cuts concatenate: 3 + 5 = 8 (not max). overlay end 7 < 8 -> 8
    expect(timelineDuration({
      cuts: [{ in_seconds: 0, out_seconds: 3 }, { in_seconds: 0, out_seconds: 5 }],
      overlays: [{ end_seconds: 7 }],
    })).toBe(8)
    // overlay extends beyond the cuts -> overlay wins
    expect(timelineDuration({
      cuts: [{ in_seconds: 0, out_seconds: 3 }],
      overlays: [{ end_seconds: 10 }],
    })).toBe(10)
  })
})
