// Unit tests for the studio UI helpers (pure: constants, factories, small math). The
// schema-level doc mutators are tested in ../editor/interp.test.js; this file pins the
// vocab/factories/presets that emit schema-valid fragments and the timecode/clamp math.

import { describe, it, expect } from 'vitest'
import {
  TRANSITIONS, SPEED_PRESETS, CANVAS_PRESETS, TEXT_ANCHORS, EASINGS,
  KF_DIMS_IMAGE, KF_DIMS_TEXT,
  overlayKind, kfDimsFor, newTextOverlay, newImageOverlay, presetKeyframes,
  isFfmpeg, anchorToXY, fmtTime, round3, clamp, previewAudioTracks,
} from './model.js'
import { sanitizeOverlay } from '../editor/interp.js'

describe('vocabulary constants stay render-faithful', () => {
  it('TRANSITIONS only lists names the FFmpeg xfade path renders (+ hard cut)', () => {
    const values = TRANSITIONS.map(t => t.value)
    expect(values).toContain('')          // hard cut
    expect(values).toContain('fade')
    expect(values).toContain('circleopen')
    // a name FFmpeg does NOT have must never be offered (it would degrade to fade)
    expect(values).not.toContain('starwipe')
    // every entry is { value, label }
    for (const t of TRANSITIONS) expect(t).toMatchObject({ value: expect.any(String), label: expect.any(String) })
  })
  it('SPEED_PRESETS are positive (FFmpeg setpts/atempo need > 0)', () => {
    expect(SPEED_PRESETS).toContain(1)
    for (const s of SPEED_PRESETS) expect(s).toBeGreaterThan(0)
  })
  it('CANVAS_PRESETS have even integer dimensions (yuv420p)', () => {
    for (const p of CANVAS_PRESETS) {
      expect(Number.isInteger(p.width) && p.width % 2 === 0).toBe(true)
      expect(Number.isInteger(p.height) && p.height % 2 === 0).toBe(true)
      expect(p.label).toBeTruthy()
    }
  })
  it('TEXT_ANCHORS match the drawtext-accepted nine-anchor grid', () => {
    expect(TEXT_ANCHORS).toHaveLength(9)
    expect(TEXT_ANCHORS).toContain('center')
    expect(TEXT_ANCHORS).toContain('bottom-center')
  })
  it('EASINGS mirror the schema enum; rotation is never a rendered text dim', () => {
    expect(EASINGS).toEqual(['linear', 'ease-in', 'ease-out', 'ease-in-out', 'spring', 'step'])
    expect(KF_DIMS_TEXT).not.toContain('scale')   // text path warns+ignores scale
    expect(KF_DIMS_IMAGE).toContain('scale')
    expect(KF_DIMS_IMAGE).not.toContain('rotation')
    expect(KF_DIMS_TEXT).not.toContain('rotation')
  })
})

describe('overlayKind / kfDimsFor', () => {
  it('explicit type wins', () => {
    expect(overlayKind({ type: 'text', asset_id: 'x' })).toBe('text')
    expect(overlayKind({ type: 'image' })).toBe('image')
    expect(overlayKind({ type: 'video' })).toBe('image') // video treated like image for KF dims
  })
  it('falls back to text iff it has text and no asset_id', () => {
    expect(overlayKind({ text: 'hi' })).toBe('text')
    expect(overlayKind({ text: 'hi', asset_id: 'a' })).toBe('image')
    expect(overlayKind({ asset_id: 'a' })).toBe('image')
    expect(overlayKind({})).toBe('image')
  })
  it('kfDimsFor gates dims by kind', () => {
    expect(kfDimsFor({ type: 'text', text: 'a' })).toEqual(KF_DIMS_TEXT)
    expect(kfDimsFor({ asset_id: 'a' })).toEqual(KF_DIMS_IMAGE)
  })
})

describe('factories emit schema-valid fragments (survive sanitizeOverlay unchanged)', () => {
  it('newTextOverlay is a complete text overlay and round-trips through the sanitizer', () => {
    const ov = newTextOverlay({ start: 1, end: 4 })
    expect(ov).toMatchObject({ type: 'text', text: expect.any(String), start_seconds: 1, end_seconds: 4 })
    expect(ov.position).toBe('bottom-center')
    expect(overlayKind(ov)).toBe('text')
    // sanitizer must not strip any field the factory emits
    expect(sanitizeOverlay(ov)).toEqual(ov)
  })
  it('newImageOverlay carries an OBJECT position sized from the canvas (renderer rejects string anchors for images)', () => {
    const ov = newImageOverlay({ assetId: 'logo.png', start: 0, end: 3, canvas: { width: 1000, height: 2000 } })
    expect(ov.type).toBe('image')
    expect(typeof ov.position).toBe('object')
    expect(ov.position).toMatchObject({ x: 50, y: 100, width: 250 }) // 5%/5%/25% of canvas
    expect(overlayKind(ov)).toBe('image')
    expect(sanitizeOverlay(ov)).toEqual(ov)
  })
  it('newImageOverlay defaults to 1920x1080 when no canvas is given', () => {
    const ov = newImageOverlay({ assetId: 'x', start: 0, end: 1 })
    expect(ov.position).toMatchObject({ x: 96, y: 54, width: 480 })
  })
})

describe('presetKeyframes', () => {
  const text = newTextOverlay({ start: 0, end: 4 })
  const image = newImageOverlay({ assetId: 'a', start: 0, end: 4, canvas: { width: 1920, height: 1080 } })

  it('opacity presets apply to ANY overlay kind', () => {
    const fin = presetKeyframes('fade_in', text)
    expect(fin).toHaveLength(2)
    expect(fin[0]).toMatchObject({ opacity: 0 })
    expect(fin[1]).toMatchObject({ opacity: 1 })
    expect(presetKeyframes('fade_out', image)[1]).toMatchObject({ opacity: 0 })
  })
  it('motion presets require an OBJECT position (image), else null', () => {
    expect(presetKeyframes('slide_in_left', text)).toBeNull()  // text position is a string anchor
    expect(presetKeyframes('pop', text)).toBeNull()
    expect(presetKeyframes('ken_burns', text)).toBeNull()
    const slide = presetKeyframes('slide_in_left', image)
    expect(slide[0].x).toBe(image.position.x - 240)
    expect(slide[1].x).toBe(image.position.x)
    expect(presetKeyframes('ken_burns', image).map(k => k.scale)).toEqual([1, 1.2])
  })
  it('unknown preset name → null', () => {
    expect(presetKeyframes('nope', image)).toBeNull()
  })
  it('preset keyframes are schema-valid (survive sanitizeOverlay unchanged)', () => {
    const ov = { ...image, keyframes: presetKeyframes('pop', image) }
    expect(sanitizeOverlay(ov).keyframes).toEqual(ov.keyframes)
  })
})

describe('isFfmpeg', () => {
  it('treats a missing runtime as ffmpeg (the studio default)', () => {
    expect(isFfmpeg({})).toBe(true)
    expect(isFfmpeg(null)).toBe(true)
    expect(isFfmpeg({ render_runtime: 'ffmpeg' })).toBe(true)
    expect(isFfmpeg({ render_runtime: 'remotion' })).toBe(false)
    expect(isFfmpeg({ render_runtime: 'hyperframes' })).toBe(false)
  })
})

describe('anchorToXY (named anchor → {x,y,width} for image overlays)', () => {
  const canvas = { width: 1000, height: 1000 }
  it('maps the nine anchors into the canvas, with a default width', () => {
    expect(anchorToXY('top-left', canvas)).toEqual({ x: 50, y: 50, width: 250 })
    expect(anchorToXY('bottom-right', canvas)).toEqual({ x: 700, y: 850, width: 250 })
    expect(anchorToXY('center', canvas)).toEqual({ x: 400, y: 450, width: 250 })
  })
  it('falls back to center for an unknown/garbage anchor', () => {
    expect(anchorToXY('sideways', canvas)).toEqual({ x: 400, y: 450, width: 250 })
  })
})

describe('fmtTime (mm:ss.c)', () => {
  it('formats sub-minute and multi-minute times with one decimal', () => {
    expect(fmtTime(0)).toBe('0:00.0')
    expect(fmtTime(5.2)).toBe('0:05.2')
    expect(fmtTime(65)).toBe('1:05.0')
    expect(fmtTime(600)).toBe('10:00.0')
  })
  it('clamps negatives and non-numbers to 0:00.0', () => {
    expect(fmtTime(-3)).toBe('0:00.0')
    expect(fmtTime('x')).toBe('0:00.0')
    expect(fmtTime(undefined)).toBe('0:00.0')
  })
  it('pins the toFixed(1) rounding seam (known: rounds within the minute, no carry to mm)', () => {
    // r = s - m*60, then r.toFixed(1). Rounding can read as 60.0 at a tick boundary rather than
    // rolling to the next minute — pinned so a future ruler change is a deliberate decision.
    expect(fmtTime(9.99)).toBe('0:10.0')
    expect(fmtTime(59.96)).toBe('0:60.0')
  })
})

describe('previewAudioTracks (source-preview <audio> elements)', () => {
  it('returns [] when there is no audio', () => {
    expect(previewAudioTracks({})).toEqual([])
    expect(previewAudioTracks(null)).toEqual([])
  })
  it('builds music / narration / sfx with volume and open-ended windows', () => {
    const doc = { audio: {
      music: { asset_id: 'bed.mp3', volume: 0.6 },
      narration: { segments: [{ asset_id: 'vo.mp3', start_seconds: 1, end_seconds: 4 }] },
      sfx: [{ asset_id: 'whoosh.mp3', start_seconds: 6, volume: 0.5 }],
    } }
    expect(previewAudioTracks(doc)).toEqual([
      { key: 'music', kind: 'music', src: 'bed.mp3', start: 0, end: Infinity, volume: 0.6 },
      { key: 'n0', kind: 'narration', src: 'vo.mp3', start: 1, end: 4, volume: 1 },
      { key: 's0', kind: 'sfx', src: 'whoosh.mp3', start: 6, end: Infinity, volume: 0.5 },
    ])
  })
  it('defaults volume to 1, uses legacy top-level music, and skips items with no asset_id', () => {
    expect(previewAudioTracks({ music: { asset_id: 'legacy.mp3' } })[0])
      .toEqual({ key: 'music', kind: 'music', src: 'legacy.mp3', start: 0, end: Infinity, volume: 1 })
    const doc = { audio: { sfx: [{ start_seconds: 2 }, { asset_id: 'x.mp3', start_seconds: 3 }] } }
    expect(previewAudioTracks(doc).map(t => t.src)).toEqual(['x.mp3']) // the no-asset sfx is skipped
  })
})

describe('round3 / clamp', () => {
  it('round3 keeps 3 decimals', () => {
    expect(round3(1.23456)).toBe(1.235)
    expect(round3(2)).toBe(2)
    expect(round3('0.1')).toBe(0.1)
  })
  it('clamp bounds a value into [lo, hi]', () => {
    expect(clamp(5, 0, 10)).toBe(5)
    expect(clamp(-1, 0, 10)).toBe(0)
    expect(clamp(99, 0, 10)).toBe(10)
  })
})
