import { describe, it, expect } from 'vitest'
import {
  interpolateAt, sanitizeCut, sanitizeOverlay, updateCut, updateOverlay,
  upsertKeyframe, removeKeyframe, scaffoldEditDecisions, timelineDuration,
  cutDuration, cutStarts, cutAtTime, trimCut, splitCutAtPlayhead, MIN_SOURCE_SPAN,
  removeCut, duplicateCut, reorderCut, addCut, addOverlay, removeOverlay,
  setCanvas, canvasOf, audioClips,
  setMusic, updateMusic, removeMusic, addSfx, updateSfx, removeSfx, updateNarration, removeNarration,
  overlayTracks, moveOverlay, trimOverlay, MIN_OVERLAY_SPAN,
  placeOverlayTrack, autoArrangeOverlays,
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

describe('sanitizeOverlay keeps text-overlay + audio_mix + box fields (studio editor)', () => {
  it('preserves text-overlay fields and whitelists nested box', () => {
    const o = sanitizeOverlay({
      type: 'text', text: 'HELLO', font_size: 64, color: 'white', font_path: 'x.ttf',
      box: { color: 'black', opacity: 0.5, padding: 12, bogus: 1 },
      start_seconds: 0, end_seconds: 2, position: 'bottom-center', opacity: 0.9,
      INVENTED: 'nope',
    })
    expect(o).toEqual({
      type: 'text', text: 'HELLO', font_size: 64, color: 'white', font_path: 'x.ttf',
      box: { color: 'black', opacity: 0.5, padding: 12 },
      start_seconds: 0, end_seconds: 2, position: 'bottom-center', opacity: 0.9,
    })
    expect(o.INVENTED).toBeUndefined()
    expect(o.box.bogus).toBeUndefined()
  })
  it('keeps a STRING position (named anchor) intact, whitelists an OBJECT position', () => {
    expect(sanitizeOverlay({ type: 'text', text: 'a', position: 'top-right', start_seconds: 0, end_seconds: 1 }).position)
      .toBe('top-right')
    expect(sanitizeOverlay({ asset_id: 'logo', position: { x: 1, y: 2, width: 3, height: 4, z: 9 }, start_seconds: 0, end_seconds: 1 }).position)
      .toEqual({ x: 1, y: 2, width: 3, height: 4 })
  })
  it('whitelists audio_mix to {enabled, volume}', () => {
    expect(sanitizeOverlay({ asset_id: 'v', start_seconds: 0, end_seconds: 1, audio_mix: { enabled: true, volume: 1.5, junk: 1 } }).audio_mix)
      .toEqual({ enabled: true, volume: 1.5 })
  })
})

describe('sanitizeOverlay coerces schema-typed values (save can never 422)', () => {
  it('rounds font_size and box.padding to integers', () => {
    const o = sanitizeOverlay({ type: 'text', text: 'a', start_seconds: 0, end_seconds: 1,
      font_size: 48.7, box: { color: 'black', opacity: 0.5, padding: 11.4 } })
    expect(o.font_size).toBe(49)
    expect(o.box.padding).toBe(11)
  })
  it('clamps keyframe t>=0, scale>=0, opacity in [0,1]', () => {
    const o = sanitizeOverlay({ asset_id: 'x', start_seconds: 0, end_seconds: 2, keyframes: [
      { t: -3, opacity: 1.8, scale: -0.5 },
      { t: 1, opacity: -0.2, x: 50 },
    ] })
    expect(o.keyframes[0]).toEqual({ t: 0, opacity: 1, scale: 0 })
    expect(o.keyframes[1]).toEqual({ t: 1, opacity: 0, x: 50 })
  })
})

describe('updateOverlay / removeOverlay no-op on out-of-range index (no stale-dirty)', () => {
  const doc = () => ({ version: '1.0', render_runtime: 'ffmpeg', cuts: [], overlays: [{ type: 'text', text: 'a', start_seconds: 0, end_seconds: 1 }] })
  it('updateOverlay returns the SAME doc when index is out of range', () => {
    const d = doc()
    expect(updateOverlay(d, 5, { opacity: 0.5 })).toBe(d)
    expect(updateOverlay(d, -1, { opacity: 0.5 })).toBe(d)
  })
  it('removeOverlay returns the SAME doc when index is out of range', () => {
    const d = doc()
    expect(removeOverlay(d, 9)).toBe(d)
  })
})

describe('sanitizeCut whitelists transform.crop', () => {
  it('drops stray keys inside transform.crop', () => {
    const c = sanitizeCut({ id: 'c1', source: 's', in_seconds: 0, out_seconds: 1,
      transform: { scale: 1, crop: { x: 0, y: 200, width: 1080, height: 1920, junk: 9 }, junk2: 1 } })
    expect(c.transform.crop).toEqual({ x: 0, y: 200, width: 1080, height: 1920 })
    expect(c.transform.junk2).toBeUndefined()
  })
})

describe('structural mutators (studio editor)', () => {
  const doc = () => ({
    version: '1.0', render_runtime: 'ffmpeg', renderer_family: 'social-reel',
    cuts: [
      { id: 'a', source: 's1', in_seconds: 0, out_seconds: 2 },
      { id: 'b', source: 's2', in_seconds: 0, out_seconds: 3 },
      { id: 'c', source: 's3', in_seconds: 0, out_seconds: 1 },
    ],
  })
  it('removeCut drops by id; no-op (referential) when id missing', () => {
    const d = doc()
    expect(removeCut(d, 'b').cuts.map(c => c.id)).toEqual(['a', 'c'])
    expect(removeCut(d, 'zzz')).toBe(d)
  })
  it('duplicateCut inserts a unique-id copy right after the original', () => {
    const out = duplicateCut(doc(), 'a')
    expect(out.cuts.map(c => c.id)).toEqual(['a', 'a-2', 'b', 'c'])
    expect(out.cuts[1].source).toBe('s1')
  })
  it('reorderCut moves a clip; clamps out-of-range dest', () => {
    expect(reorderCut(doc(), 0, 2).cuts.map(c => c.id)).toEqual(['b', 'c', 'a'])
    expect(reorderCut(doc(), 2, 0).cuts.map(c => c.id)).toEqual(['c', 'a', 'b'])
    expect(reorderCut(doc(), 0, 99).cuts.map(c => c.id)).toEqual(['b', 'c', 'a'])
    expect(reorderCut(doc(), 9, 0)).toEqual(doc())  // out-of-range source = no structural change
  })
  it('addCut forces a unique id and inserts at index (default end)', () => {
    const out = addCut(doc(), { id: 'a', source: 'sX', in_seconds: 0, out_seconds: 1 }, 1)
    expect(out.cuts.map(c => c.id)).toEqual(['a', 'a-2', 'b', 'c'])  // collided 'a' -> 'a-2'
    expect(addCut(doc(), { id: 'z', source: 'sZ', in_seconds: 0, out_seconds: 1 }).cuts.map(c => c.id))
      .toEqual(['a', 'b', 'c', 'z'])
  })
  it('addOverlay appends a sanitized overlay; removeOverlay drops by index', () => {
    const withOv = addOverlay(doc(), { type: 'text', text: 'hi', start_seconds: 0, end_seconds: 1, INVALID: 1 })
    expect(withOv.overlays).toHaveLength(1)
    expect(withOv.overlays[0].INVALID).toBeUndefined()
    expect(removeOverlay(withOv, 0).overlays).toHaveLength(0)
  })
  it('setCanvas merges compose_target; canvasOf falls back to 1920x1080@30', () => {
    expect(canvasOf({})).toEqual({ width: 1920, height: 1080, fps: 30 })
    const d = setCanvas(doc(), { width: 1080, height: 1920 })
    expect(d.metadata.compose_target).toEqual({ width: 1080, height: 1920 })
    expect(canvasOf(setCanvas(d, { fps: 24 }))).toEqual({ width: 1080, height: 1920, fps: 24 })
  })
})

describe('audioClips (timeline audio lane projection)', () => {
  it('returns [] for a doc with no audio', () => {
    expect(audioClips({ cuts: [] })).toEqual([])
    expect(audioClips({})).toEqual([])
    expect(audioClips(null)).toEqual([])
  })
  it('draws music as a single bed spanning [0, timelineDuration]', () => {
    const doc = {
      cuts: [{ in_seconds: 0, out_seconds: 4 }, { in_seconds: 0, out_seconds: 2 }], // concat => 6s
      audio: { music: { asset_id: 'song.mp3', volume: 0.6 } },
    }
    expect(audioClips(doc)).toEqual([
      { kind: 'music', index: null, asset_id: 'song.mp3', start_seconds: 0, end_seconds: 6, point: false },
    ])
  })
  it('prefers audio.music over the legacy top-level music', () => {
    const doc = {
      cuts: [{ in_seconds: 0, out_seconds: 3 }],
      audio: { music: { asset_id: 'new.mp3' } },
      music: { asset_id: 'legacy.mp3' },
    }
    expect(audioClips(doc)[0].asset_id).toBe('new.mp3')
  })
  it('falls back to legacy top-level music when audio.music is absent', () => {
    const doc = { cuts: [{ in_seconds: 0, out_seconds: 3 }], music: { asset_id: 'legacy.mp3' } }
    expect(audioClips(doc)).toEqual([
      { kind: 'music', index: null, asset_id: 'legacy.mp3', start_seconds: 0, end_seconds: 3, point: false },
    ])
  })
  it('skips music with no asset_id (nothing to draw)', () => {
    expect(audioClips({ cuts: [{ in_seconds: 0, out_seconds: 3 }], audio: { music: { volume: 0.5 } } })).toEqual([])
  })
  it('maps narration segments to start→end blocks and sfx to point markers', () => {
    const doc = {
      cuts: [{ in_seconds: 0, out_seconds: 10 }],
      audio: {
        narration: { segments: [{ asset_id: 'vo1.mp3', start_seconds: 1, end_seconds: 4 }] },
        sfx: [{ asset_id: 'whoosh.mp3', start_seconds: 6, volume: 0.8 }],
      },
    }
    const clips = audioClips(doc)
    const vo = clips.find(c => c.kind === 'narration')
    const fx = clips.find(c => c.kind === 'sfx')
    expect(vo).toEqual({ kind: 'narration', index: 0, asset_id: 'vo1.mp3', start_seconds: 1, end_seconds: 4, point: false })
    expect(fx).toEqual({ kind: 'sfx', index: 0, asset_id: 'whoosh.mp3', start_seconds: 6, end_seconds: 6, point: true })
  })
  it('defaults a narration segment with no end to a zero-length block, and skips entries with no asset_id', () => {
    const doc = {
      cuts: [{ in_seconds: 0, out_seconds: 10 }],
      audio: {
        narration: { segments: [{ asset_id: 'vo.mp3', start_seconds: 2 }, { start_seconds: 3 }] },
        sfx: [{ start_seconds: 5 }], // no asset_id
      },
    }
    const clips = audioClips(doc)
    expect(clips).toHaveLength(1)
    expect(clips[0]).toEqual({ kind: 'narration', index: 0, asset_id: 'vo.mp3', start_seconds: 2, end_seconds: 2, point: false })
  })
  it('clamps an inverted narration segment (end < start) to a zero-length block, not negative', () => {
    const doc = {
      cuts: [{ in_seconds: 0, out_seconds: 10 }],
      audio: { narration: { segments: [{ asset_id: 'vo.mp3', start_seconds: 4, end_seconds: 1 }] } },
    }
    const seg = audioClips(doc)[0]
    expect(seg.start_seconds).toBe(4)
    expect(seg.end_seconds).toBe(4)      // Math.max(start, end) → never draws a negative width
  })
  it('returns items sorted by start time across kinds', () => {
    const doc = {
      cuts: [{ in_seconds: 0, out_seconds: 10 }],
      audio: {
        music: { asset_id: 'm.mp3' },               // start 0
        narration: { segments: [{ asset_id: 'v.mp3', start_seconds: 7, end_seconds: 9 }] },
        sfx: [{ asset_id: 's.mp3', start_seconds: 3 }],
      },
    }
    expect(audioClips(doc).map(c => c.start_seconds)).toEqual([0, 3, 7])
  })
})

describe('audio mutators (music bed / narration / sfx)', () => {
  it('setMusic sets the bed; updateMusic merges + clamps volume; removeMusic clears it', () => {
    let d = setMusic({}, 'song.mp3')
    expect(d.audio.music).toEqual({ asset_id: 'song.mp3' })
    d = updateMusic(d, { volume: 5, fade_in_seconds: 1 })   // volume clamps to [0,1]
    expect(d.audio.music).toEqual({ asset_id: 'song.mp3', volume: 1, fade_in_seconds: 1 })
    expect(removeMusic(d).audio.music).toBeUndefined()
    expect(removeMusic({})).toEqual({})                      // no-op when there is no music
  })
  it('removeMusic also clears a legacy top-level music', () => {
    expect(removeMusic({ music: { asset_id: 'legacy.mp3' } }).music).toBeUndefined()
  })
  it('addSfx appends a clamped point; updateSfx merges by index; removeSfx drops by index', () => {
    let d = addSfx({}, 'a.mp3', -3)                          // start clamps to >= 0
    d = addSfx(d, 'b.mp3', 4)
    expect(d.audio.sfx).toEqual([{ asset_id: 'a.mp3', start_seconds: 0 }, { asset_id: 'b.mp3', start_seconds: 4 }])
    d = updateSfx(d, 1, { volume: 9 })                       // volume clamps to [0,1]
    expect(d.audio.sfx[1]).toEqual({ asset_id: 'b.mp3', start_seconds: 4, volume: 1 })
    expect(updateSfx(d, 9, { volume: 0.5 })).toBe(d)         // out-of-range no-op (same ref)
    expect(removeSfx(d, 0).audio.sfx).toEqual([{ asset_id: 'b.mp3', start_seconds: 4, volume: 1 }])
  })
  it('updateNarration/removeNarration edit segments by index, immutably', () => {
    const d0 = { audio: { narration: { segments: [
      { asset_id: 'v1.mp3', start_seconds: 0, end_seconds: 2 },
      { asset_id: 'v2.mp3', start_seconds: 2, end_seconds: 4 },
    ] } } }
    const d1 = updateNarration(d0, 1, { end_seconds: 5 })
    expect(d1.audio.narration.segments[1]).toEqual({ asset_id: 'v2.mp3', start_seconds: 2, end_seconds: 5 })
    expect(d0.audio.narration.segments[1].end_seconds).toBe(4)  // original untouched
    expect(updateNarration(d0, 9, { end_seconds: 5 })).toBe(d0) // out-of-range no-op
    expect(removeNarration(d0, 0).audio.narration.segments.map(s => s.asset_id)).toEqual(['v2.mp3'])
  })
  it('audio mutators drop fields the schema does not allow (whitelist)', () => {
    const d = updateSfx({ audio: { sfx: [{ asset_id: 'a.mp3', start_seconds: 1 }] } }, 0, { hacker: true, volume: 0.5 })
    expect(d.audio.sfx[0]).toEqual({ asset_id: 'a.mp3', start_seconds: 1, volume: 0.5 })
  })
})

describe('interpolateAt + cutAtTime edge cases (placement seams)', () => {
  it('sorts unsorted keyframes before interpolating', () => {
    const unsorted = [{ t: 2, x: 100 }, { t: 0, x: 0 }, { t: 1, x: 50 }]
    expect(interpolateAt(unsorted, 'x', 1)).toBe(50)
    expect(interpolateAt(unsorted, 'x', 0.5)).toBe(25)
  })
  it('maps a time exactly on a cut seam to the LATER cut (half-open intervals)', () => {
    const doc = { cuts: [
      { id: 'a', source: 'A', in_seconds: 0, out_seconds: 3 }, // project [0,3)
      { id: 'b', source: 'B', in_seconds: 0, out_seconds: 4 }, // project [3,7)
    ] }
    expect(cutAtTime(doc, 3).index).toBe(1)      // seam belongs to cut b
    expect(cutAtTime(doc, 2.999).index).toBe(0)
  })
})

describe('overlay track (z-layer) + sanitize', () => {
  it('sanitizeOverlay coerces track to a non-negative int', () => {
    expect(sanitizeOverlay({ type: 'text', text: 'a', start_seconds: 0, end_seconds: 1, track: 2.7 }).track).toBe(3)
    expect(sanitizeOverlay({ type: 'text', text: 'a', start_seconds: 0, end_seconds: 1, track: -5 }).track).toBe(0)
    expect(sanitizeOverlay({ type: 'text', text: 'a', start_seconds: 0, end_seconds: 1 }).track).toBeUndefined()
  })
  it('overlayTracks reports the max track + lane count', () => {
    expect(overlayTracks({ overlays: [] })).toEqual({ max: 0, count: 1 })
    expect(overlayTracks({ overlays: [{ track: 0 }, { track: 3 }, { track: 1 }] })).toEqual({ max: 3, count: 4 })
    expect(overlayTracks({ overlays: [{}, {}] })).toEqual({ max: 0, count: 1 }) // missing track → 0
  })
})

describe('moveOverlay (absolute time + track)', () => {
  const doc = { overlays: [{ type: 'image', asset_id: 'a', start_seconds: 2, end_seconds: 5, track: 0 }] }
  it('shifts start + end together (preserves duration), clamps start >= 0', () => {
    const nd = moveOverlay(doc, 0, { start: 4 })
    expect(nd.overlays[0]).toMatchObject({ start_seconds: 4, end_seconds: 7 }) // duration 3 preserved
    const clamped = moveOverlay(doc, 0, { start: -3 })
    expect(clamped.overlays[0]).toMatchObject({ start_seconds: 0, end_seconds: 3 })
  })
  it('sets the track (clamped non-negative int)', () => {
    expect(moveOverlay(doc, 0, { track: 2 }).overlays[0].track).toBe(2)
    expect(moveOverlay(doc, 0, { track: -1 }).overlays[0].track).toBe(0)
  })
  it('moves time AND track at once', () => {
    const nd = moveOverlay(doc, 0, { start: 1, track: 3 })
    expect(nd.overlays[0]).toMatchObject({ start_seconds: 1, end_seconds: 4, track: 3 })
  })
  it('is a same-ref no-op out of range or with no change', () => {
    expect(moveOverlay(doc, 9, { start: 1 })).toBe(doc)
    expect(moveOverlay(doc, 0, {})).toBe(doc)
  })
  it('a value-equal move (drag back to origin) is a same-ref no-op (honors the dirty/history contract)', () => {
    expect(moveOverlay(doc, 0, { start: 2, track: 0 })).toBe(doc) // start/track unchanged
  })
  it('shifts keyframe `t` by the SAME delta as the window (keyframes travel with the clip)', () => {
    const kfDoc = { overlays: [{
      type: 'image', asset_id: 'a', start_seconds: 2, end_seconds: 5, track: 0,
      keyframes: [{ t: 2, opacity: 0 }, { t: 2.5, opacity: 1 }, { t: 5, opacity: 0 }],
    }] }
    const moved = moveOverlay(kfDoc, 0, { start: 7 }) // +5
    expect(moved.overlays[0]).toMatchObject({ start_seconds: 7, end_seconds: 10 })
    expect(moved.overlays[0].keyframes.map(k => k.t)).toEqual([7, 7.5, 10])
    // clamped move toward 0 shifts keyframes by the CLAMPED delta (start can't go below 0)
    const back = moveOverlay(kfDoc, 0, { start: -1 }) // clamps to 0, delta = -2
    expect(back.overlays[0].start_seconds).toBe(0)
    expect(back.overlays[0].keyframes.map(k => k.t)).toEqual([0, 0.5, 3])
  })
})

describe('placeOverlayTrack (auto-create a track on add when overlapping)', () => {
  const doc = { overlays: [
    { type: 'text', text: 'a', start_seconds: 0, end_seconds: 3, track: 0 },
    { type: 'text', text: 'b', start_seconds: 0, end_seconds: 3, track: 1 },
  ] }
  it('empty timeline → track 0', () => {
    expect(placeOverlayTrack({ overlays: [] }, 0, 3, 0)).toBe(0)
  })
  it('overlapping both existing tracks → a NEW track on top', () => {
    expect(placeOverlayTrack(doc, 1, 2, 0)).toBe(2) // overlaps track 0 AND track 1 → track 2
  })
  it('reuses the lowest track with a free gap (no overlap)', () => {
    expect(placeOverlayTrack(doc, 3, 5, 0)).toBe(0) // starts at 3 = both existing end → no overlap → track 0
  })
  it('honors the preferred (dropped) track as the floor', () => {
    // dropping on track 1 where [4,6] doesn't overlap → stays on track 1
    expect(placeOverlayTrack(doc, 4, 6, 1)).toBe(1)
    // dropping on track 0 where [1,2] overlaps → bumps to the first free >= 0 → track 2
    expect(placeOverlayTrack(doc, 1, 2, 0)).toBe(2)
  })
})

describe('autoArrangeOverlays (greedy interval partitioning / lane assignment)', () => {
  it('separates two overlapping overlays piled on one track into two lanes', () => {
    const doc = { overlays: [
      { type: 'text', text: 'a', start_seconds: 0, end_seconds: 4, track: 0 },
      { type: 'image', asset_id: 'x', start_seconds: 2, end_seconds: 6, position: { x: 0, y: 0 }, track: 0 },
    ] }
    const nd = autoArrangeOverlays(doc)
    expect(nd.overlays.map(o => o.track)).toEqual([0, 1]) // earlier-start lane 0, overlapping → lane 1
  })
  it('packs non-overlapping overlays onto a single track (minimum lanes)', () => {
    const doc = { overlays: [
      { type: 'text', text: 'a', start_seconds: 0, end_seconds: 2, track: 3 },
      { type: 'text', text: 'b', start_seconds: 2, end_seconds: 4, track: 1 },
    ] }
    expect(autoArrangeOverlays(doc).overlays.map(o => o.track)).toEqual([0, 0]) // touching, no overlap → both lane 0
  })
  it('three mutually-overlapping overlays → three lanes', () => {
    const doc = { overlays: [
      { type: 'text', text: 'a', start_seconds: 0, end_seconds: 5, track: 0 },
      { type: 'text', text: 'b', start_seconds: 1, end_seconds: 6, track: 0 },
      { type: 'text', text: 'c', start_seconds: 2, end_seconds: 7, track: 0 },
    ] }
    expect(autoArrangeOverlays(doc).overlays.map(o => o.track)).toEqual([0, 1, 2])
  })
  it('is a same-ref no-op when already arranged / < 2 overlays', () => {
    const arranged = { overlays: [
      { type: 'text', text: 'a', start_seconds: 0, end_seconds: 4, track: 0 },
      { type: 'text', text: 'b', start_seconds: 2, end_seconds: 6, track: 1 },
    ] }
    expect(autoArrangeOverlays(arranged)).toBe(arranged)
    const one = { overlays: [{ type: 'text', text: 'a', start_seconds: 0, end_seconds: 2, track: 0 }] }
    expect(autoArrangeOverlays(one)).toBe(one)
  })
})

describe('music bed: editing a legacy top-level music doc keeps its asset_id', () => {
  it('updateMusic seeds from legacy doc.music and collapses it into audio.music', () => {
    const legacy = { music: { asset_id: 'bed.mp3', volume: 1 } } // legacy top-level shape
    const nd = updateMusic(legacy, { volume: 0.4 })
    expect(nd.audio.music).toMatchObject({ asset_id: 'bed.mp3', volume: 0.4 }) // asset_id preserved
    expect(nd.music).toBeUndefined() // legacy field collapsed → one source of truth
    expect(audioClips(nd).find(a => a.kind === 'music')?.asset_id).toBe('bed.mp3')
  })
  it('setMusic on a legacy doc replaces the asset_id and drops the legacy field', () => {
    const nd = setMusic({ music: { asset_id: 'old.mp3', volume: 0.5 } }, 'new.mp3')
    expect(nd.audio.music).toMatchObject({ asset_id: 'new.mp3', volume: 0.5 })
    expect(nd.music).toBeUndefined()
  })
})

describe('trimOverlay (edge handles, absolute time)', () => {
  const doc = { overlays: [{ type: 'image', asset_id: 'a', start_seconds: 2, end_seconds: 6 }] }
  it('moves the in-edge but never crosses the out within MIN_OVERLAY_SPAN', () => {
    expect(trimOverlay(doc, 0, { start_seconds: 4 }).overlays[0].start_seconds).toBe(4)
    const crossed = trimOverlay(doc, 0, { start_seconds: 10 }) // past end
    expect(crossed.overlays[0].start_seconds).toBeCloseTo(6 - MIN_OVERLAY_SPAN, 5)
  })
  it('moves the out-edge, clamps start >= 0', () => {
    expect(trimOverlay(doc, 0, { end_seconds: 9 }).overlays[0].end_seconds).toBe(9)
    expect(trimOverlay(doc, 0, { start_seconds: -2 }).overlays[0].start_seconds).toBe(0)
  })
})
