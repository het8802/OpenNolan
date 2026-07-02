// Unit tests for the studio UI helpers (pure: constants, factories, small math). The
// schema-level doc mutators are tested in ../editor/interp.test.js; this file pins the
// vocab/factories/presets that emit schema-valid fragments and the timecode/clamp math.

import { describe, it, expect } from 'vitest'
import {
  TRANSITIONS, SPEED_PRESETS, CANVAS_PRESETS, TEXT_ANCHORS, EASINGS,
  KF_DIMS_IMAGE, KF_DIMS_TEXT,
  overlayKind, overlayType, kfDimsFor, newTextOverlay, newImageOverlay, newVideoOverlay, presetKeyframes,
  isFfmpeg, anchorToXY, fmtTime, round3, clamp, previewAudioTracks, groupAudioLanes, isImageSource, clipType,
  scrubValue, roundTo, fmtScrub, decimalsOf,
  clipFitSize, clipBox, clipDefaultPosition, clipAnchorXY, clipPositionXY,
  isScaleObject, scaleAxes,
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
  it('newTextOverlay is a complete text overlay, CENTERED, and round-trips through the sanitizer', () => {
    const ov = newTextOverlay({ start: 1, end: 4 })
    expect(ov).toMatchObject({ type: 'text', text: expect.any(String), start_seconds: 1, end_seconds: 4, track: 0 })
    expect(ov.position).toBe('center') // new overlays land in the center (feat 4)
    expect(overlayKind(ov)).toBe('text')
    // sanitizer must not strip any field the factory emits (incl. track)
    expect(sanitizeOverlay(ov)).toEqual(ov)
  })
  it('newImageOverlay carries a CENTERED OBJECT position sized from the canvas', () => {
    const ov = newImageOverlay({ assetId: 'logo.png', start: 0, end: 3, canvas: { width: 1000, height: 2000 } })
    expect(ov.type).toBe('image')
    expect(typeof ov.position).toBe('object')
    // width = 30% of canvas (300); centered: x=(1000-300)/2, y=(2000-300)/2
    expect(ov.position).toMatchObject({ x: 350, y: 850, width: 300 })
    expect(ov.track).toBe(0)
    expect(overlayKind(ov)).toBe('image')
    expect(sanitizeOverlay(ov)).toEqual(ov)
  })
  it('newImageOverlay defaults to 1920x1080 when no canvas is given', () => {
    const ov = newImageOverlay({ assetId: 'x', start: 0, end: 1 })
    // width = round(1920*0.3) = 576; centered on 1920x1080
    expect(ov.position).toMatchObject({ x: 672, y: 252, width: 576 })
  })
  it('newVideoOverlay is a centered video overlay (type=video)', () => {
    const ov = newVideoOverlay({ assetId: 'clip.mp4', start: 0, end: 4, canvas: { width: 1080, height: 1920 }, track: 2 })
    expect(ov.type).toBe('video')
    expect(ov.track).toBe(2)
    // width = round(1080*0.4) = 432; centered horizontally
    expect(ov.position).toMatchObject({ x: 324, width: 432 })
    expect(sanitizeOverlay(ov)).toEqual(ov)
  })
})

describe('type detection (clipType / isImageSource / overlayType)', () => {
  it('isImageSource matches still-image extensions only (not .mp4/.gif)', () => {
    expect(isImageSource('assets/image/logo.PNG')).toBe(true)
    expect(isImageSource('a.jpg')).toBe(true)
    expect(isImageSource('a.webp')).toBe(true)
    expect(isImageSource('clip.mp4')).toBe(false)
    expect(isImageSource('anim.gif')).toBe(false) // gifs are video-like on both paths
    expect(isImageSource('')).toBe(false)
  })
  it('overlayType is a three-way split text|image|video', () => {
    expect(overlayType({ type: 'text', text: 'a' })).toBe('text')
    expect(overlayType({ type: 'video', asset_id: 'a' })).toBe('video')
    expect(overlayType({ type: 'image', asset_id: 'a' })).toBe('image')
    expect(overlayType({ asset_id: 'a' })).toBe('image') // legacy default
    expect(overlayType({ text: 'a' })).toBe('text')      // legacy text
  })
  it('clipType maps a selection + doc to one of the 7 (+narration) keys', () => {
    const doc = {
      cuts: [{ id: 'v', source: 'a.mp4' }, { id: 'i', source: 'p.png' }],
      overlays: [{ type: 'text', text: 'x' }, { type: 'video', asset_id: 'p.mp4' }, { type: 'image', asset_id: 'p.png' }],
    }
    expect(clipType({ kind: 'cut', id: 'v' }, doc)).toBe('video_main')
    expect(clipType({ kind: 'cut', id: 'i' }, doc)).toBe('image_main')
    expect(clipType({ kind: 'overlay', index: 0 }, doc)).toBe('text')
    expect(clipType({ kind: 'overlay', index: 1 }, doc)).toBe('video_overlay')
    expect(clipType({ kind: 'overlay', index: 2 }, doc)).toBe('image_overlay')
    expect(clipType({ kind: 'audio', audioKind: 'music' }, doc)).toBe('music')
    expect(clipType({ kind: 'audio', audioKind: 'sfx', index: 0 }, doc)).toBe('sfx')
    expect(clipType(null, doc)).toBeNull()
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
      { key: 'music0', kind: 'music', src: 'bed.mp3', start: 0, end: Infinity, volume: 0.6 },
      { key: 'n0', kind: 'narration', src: 'vo.mp3', start: 1, end: 4, volume: 1 },
      { key: 's0', kind: 'sfx', src: 'whoosh.mp3', start: 6, end: Infinity, volume: 0.5 },
    ])
  })
  it('emits one windowed track per music region when music is an array (post-split)', () => {
    const doc = { audio: { music: [
      { asset_id: 'a.mp3', start_seconds: 0, end_seconds: 4 },
      { asset_id: 'b.mp3', start_seconds: 4, end_seconds: 9, volume: 0.3 },
    ] } }
    expect(previewAudioTracks(doc)).toEqual([
      { key: 'music0', kind: 'music', src: 'a.mp3', start: 0, end: 4, volume: 1 },
      { key: 'music1', kind: 'music', src: 'b.mp3', start: 4, end: 9, volume: 0.3 },
    ])
  })
  it('defaults volume to 1, uses legacy top-level music, and skips items with no asset_id', () => {
    expect(previewAudioTracks({ music: { asset_id: 'legacy.mp3' } })[0])
      .toEqual({ key: 'music0', kind: 'music', src: 'legacy.mp3', start: 0, end: Infinity, volume: 1 })
    const doc = { audio: { sfx: [{ start_seconds: 2 }, { asset_id: 'x.mp3', start_seconds: 3 }] } }
    expect(previewAudioTracks(doc).map(t => t.src)).toEqual(['x.mp3']) // the no-asset sfx is skipped
  })
})

describe('groupAudioLanes (one timeline row per audio kind)', () => {
  it('returns [] when there are no audio items', () => {
    expect(groupAudioLanes([])).toEqual([])
    expect(groupAudioLanes()).toEqual([])
  })
  it('groups items by kind in music → narration → sfx order, dropping empty kinds', () => {
    const items = [
      { kind: 'music', index: null, asset_id: 'bed.mp3' },
      { kind: 'sfx', index: 0, asset_id: 'a.mp3' },
      { kind: 'sfx', index: 1, asset_id: 'b.mp3' },
    ]
    const rows = groupAudioLanes(items)
    expect(rows.map(r => r.kind)).toEqual(['music', 'sfx']) // narration absent → no row
    expect(rows[0].items).toHaveLength(1)
    expect(rows[1].items.map(i => i.index)).toEqual([0, 1])
  })
  it('keeps a full-width music bed and a full-width narration segment on SEPARATE rows', () => {
    // The occlusion bug: both spanned the whole timeline in ONE row, so narration hid the bed.
    const items = [
      { kind: 'music', index: null, asset_id: 'bed.mp3', start_seconds: 0, end_seconds: 10 },
      { kind: 'narration', index: 0, asset_id: 'vo.mp3', start_seconds: 0, end_seconds: 10 },
    ]
    const rows = groupAudioLanes(items)
    expect(rows.map(r => r.kind)).toEqual(['music', 'narration'])
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

describe('scrub-field math (drag-to-adjust number inputs)', () => {
  it('decimalsOf reads the precision implied by a step', () => {
    expect(decimalsOf(1)).toBe(0)
    expect(decimalsOf(0.1)).toBe(1)
    expect(decimalsOf(0.05)).toBe(2)
    expect(decimalsOf(undefined)).toBe(0)
    expect(decimalsOf(0)).toBe(0)
  })
  it('roundTo snaps to step precision (kills float artifacts)', () => {
    expect(roundTo(4.800000001, 0.1)).toBe(4.8)
    expect(roundTo(3.14159, 0.01)).toBe(3.14)
    expect(roundTo(7.6, 1)).toBe(8)
  })
  it('scrubValue moves ~one step per pixel from the drag-start value', () => {
    expect(scrubValue({ start: 1, dx: 40, step: 0.1 })).toBe(5)      // +40px * 0.1
    expect(scrubValue({ start: 0, dx: 30, step: 1 })).toBe(30)       // +30px * 1
    expect(scrubValue({ start: 10, dx: -4, step: 1 })).toBe(6)       // drag left lowers it
  })
  it('fine mode (Shift) moves 5× slower', () => {
    expect(scrubValue({ start: 1, dx: 40, step: 0.1, fine: true })).toBe(1.8) // 40 * (0.1/5)=0.8
  })
  it('clamps to [min,max] when finite', () => {
    expect(scrubValue({ start: 0.9, dx: 40, step: 0.05, min: 0, max: 1 })).toBe(1)   // capped
    expect(scrubValue({ start: 0.1, dx: -100, step: 0.05, min: 0, max: 1 })).toBe(0) // floored
    expect(scrubValue({ start: 5, dx: -100, step: 1, min: 1 })).toBe(1)              // min only
  })
  it('snaps to the increment grid + tolerates junk input', () => {
    expect(scrubValue({ start: 0, dx: 7, step: 0.5 })).toBe(3.5)     // 7*0.5, on the 0.5 grid
    expect(scrubValue({ start: undefined, dx: undefined })).toBe(0)  // no NaN leaks
    expect(scrubValue({ start: 2, dx: 3, step: 0 })).toBe(5)         // bad step → treated as 1
  })
  it('fmtScrub renders compact numbers and blanks non-numbers (= unset/auto)', () => {
    expect(fmtScrub(4)).toBe('4')
    expect(fmtScrub(1.5)).toBe('1.5')
    expect(fmtScrub(1.07)).toBe('1.07')
    expect(fmtScrub('')).toBe('')
    expect(fmtScrub(null)).toBe('')
    expect(fmtScrub(undefined)).toBe('')
  })
})

describe('main-clip placement helpers (move + resize on the canvas)', () => {
  const canvas = { width: 1080, height: 1920 }
  const src = { width: 1920, height: 1080 } // 16:9 source in a 9:16 canvas
  it('clipFitSize fits a 16:9 source into a 9:16 canvas (contain)', () => {
    expect(clipFitSize(src, canvas)).toEqual({ width: 1080, height: 608 })
  })
  it('clipFitSize falls back to the canvas when dims are unknown', () => {
    expect(clipFitSize(null, canvas)).toEqual({ width: 1080, height: 1920 })
  })
  it('clipBox = fit × scale with even dims', () => {
    expect(clipBox(src, canvas, 0.5)).toEqual({ width: 540, height: 304 })
    expect(clipBox(src, canvas, 1)).toEqual({ width: 1080, height: 608 })
  })
  it('clipDefaultPosition centers the box (matches the legacy centered letterbox)', () => {
    expect(clipDefaultPosition(src, canvas, 1)).toEqual({ x: 0, y: 656 })
  })
  it('clipAnchorXY places the box flush to the named edge (margin 0)', () => {
    expect(clipAnchorXY('top-left', src, canvas, 0.5)).toEqual({ x: 0, y: 0 })
    expect(clipAnchorXY('bottom-right', src, canvas, 0.5)).toEqual({ x: 1080 - 540, y: 1920 - 304 })
  })
  it('clipPositionXY resolves an {x,y} object as-is and a string anchor to numbers', () => {
    expect(clipPositionXY({ transform: { position: { x: 5, y: 9 } } }, src, canvas)).toEqual({ x: 5, y: 9 })
    expect(clipPositionXY({ transform: { position: 'top-left', scale: 0.5 } }, src, canvas)).toEqual({ x: 0, y: 0 })
    expect(clipPositionXY({ transform: {} }, src, canvas)).toEqual({ x: 0, y: 656 }) // default = centered
  })

  // ── non-uniform scale ({x,y} box) — preview must match the renderer's boxw=canvas*sx path ──
  it('isScaleObject distinguishes the per-axis box from a uniform number', () => {
    expect(isScaleObject({ x: 1, y: 0.5 })).toBe(true)
    expect(isScaleObject(1.5)).toBe(false)
    expect(isScaleObject(null)).toBe(false)
    expect(isScaleObject(undefined)).toBe(false)
  })
  it('scaleAxes splits a uniform number into equal axes and an object into its x/y', () => {
    expect(scaleAxes(1.5)).toEqual({ sx: 1.5, sy: 1.5 })
    expect(scaleAxes({ x: 1, y: 0.5 })).toEqual({ sx: 1, sy: 0.5 })
    expect(scaleAxes()).toEqual({ sx: 1, sy: 1 }) // default
  })
  it('scaleAxes floors non-positive / non-finite members at 1 (no zero-collapse)', () => {
    expect(scaleAxes({ x: 0, y: 0.5 })).toEqual({ sx: 1, sy: 0.5 })
    expect(scaleAxes({ x: 'bad', y: 2 })).toEqual({ sx: 1, sy: 2 })
  })
  it('clipBox for an {x,y} object is a CANVAS-fraction box (even dims), NOT fit×scale', () => {
    // split-screen panel: full width, half height of the 1080×1920 canvas.
    expect(clipBox(src, canvas, { x: 1, y: 0.5 })).toEqual({ width: 1080, height: 960 })
    // a non-uniform box ignores the source fit-size — it's the panel, the clip fits inside it.
    expect(clipBox(src, canvas, { x: 0.5, y: 1 })).toEqual({ width: 540, height: 1920 })
  })
  it('clipBox uniform number path is UNCHANGED (still fit × scale)', () => {
    expect(clipBox(src, canvas, 0.5)).toEqual({ width: 540, height: 304 }) // == the legacy expectation
    expect(clipBox(src, canvas, 1)).toEqual({ width: 1080, height: 608 })
  })
  it('clipPositionXY anchors an {x,y}-box clip using the box dims (split panel flush bottom-right)', () => {
    const cut = { transform: { scale: { x: 1, y: 0.5 }, position: 'bottom-right' } }
    // box = 1080×960 → flush bottom-right = (1080-1080, 1920-960) = (0, 960)
    expect(clipPositionXY(cut, src, canvas)).toEqual({ x: 0, y: 960 })
  })
})
