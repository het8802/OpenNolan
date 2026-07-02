// Component tests for the timeline RENDER CONTRACT: one block per cut/overlay/audio item,
// at the DERIVED pixel position (LANE_PAD + time*zoom). jsdom has no layout engine, so the
// pointer-driven interactions (scrub / trim / drag-reorder) are covered by E2E, not here —
// these assert the structure the FFmpeg-faithful placement math produces.

import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import StudioTimeline from './StudioTimeline.jsx'

const LANE_PAD = 12
const ZOOM = 80

const noop = () => {}
const baseProps = {
  zoom: ZOOM, playhead: 2, selection: null, sourceMetas: {}, playing: false,
  onSeek: noop, onSelect: noop, onTrim: noop, onTrimBegin: noop, onReorder: noop, onZoom: noop,
  onTogglePlay: noop, onSplit: noop, onDuplicate: noop, onDelete: noop, onAutoArrange: noop,
  onOverlayMove: noop, onOverlayTrim: noop, onOverlayDragBegin: noop, onOverlayResolve: noop,
  onAudioDragBegin: noop, onMoveSfx: noop, onMoveNarration: noop, onTrimNarration: noop, onSetMusicLevels: noop,
}

function renderTimeline(doc, dur) {
  return render(<StudioTimeline doc={doc} dur={dur} {...baseProps} />)
}

const fullDoc = {
  cuts: [
    { id: 'a', source: 'clips/intro.mp4', in_seconds: 0, out_seconds: 4 },            // 4s @ [0,4)
    { id: 'b', source: 'clips/body.mp4', in_seconds: 0, out_seconds: 2, speed: 2 },   // 1s @ [4,5)
  ],
  overlays: [{ type: 'text', text: 'Hello world this is long', start_seconds: 1, end_seconds: 3 }],
  audio: {
    music: { asset_id: 'music/bed.mp3' },
    narration: { segments: [{ asset_id: 'vo/line1.mp3', start_seconds: 0.5, end_seconds: 2 }] },
    sfx: [{ asset_id: 'sfx/whoosh.mp3', start_seconds: 3 }],
  },
}

describe('clips lane', () => {
  it('renders one clip per cut, labelled by source basename, with a speed suffix', () => {
    const { container } = renderTimeline(fullDoc, 5)
    const clips = container.querySelectorAll('.st-clip')
    expect(clips).toHaveLength(2)
    expect(clips[0].textContent).toContain('intro.mp4')
    expect(clips[1].textContent).toContain('body.mp4')
    expect(clips[1].textContent).toContain('2×')           // non-1 speed is surfaced
  })
  it('places each clip at the DERIVED concat position (LANE_PAD + start*zoom)', () => {
    const { container } = renderTimeline(fullDoc, 5)
    const [a, b] = container.querySelectorAll('.st-clip')
    expect(a.style.left).toBe(`${LANE_PAD}px`)              // first cut at the gutter
    expect(a.style.width).toBe(`${4 * ZOOM}px`)            // 4 project seconds wide
    expect(b.style.left).toBe(`${LANE_PAD + 4 * ZOOM}px`)  // concatenated after cut a
    expect(b.style.width).toBe(`${1 * ZOOM}px`)            // 2s @2x = 1 project second
  })
  it('marks the selected clip', () => {
    const { container } = render(
      <StudioTimeline doc={fullDoc} dur={5} {...baseProps} selection={{ kind: 'cut', id: 'b' }} />)
    const clips = container.querySelectorAll('.st-clip')
    expect(clips[0].className).not.toContain('sel')
    expect(clips[1].className).toContain('sel')
  })
})

describe('overlays lane', () => {
  it('renders a TEXT overlay block at absolute start time, label truncated to 18 chars in curly quotes', () => {
    const { container } = renderTimeline(fullDoc, 5)
    const ovs = container.querySelectorAll('.st-ov')
    expect(ovs).toHaveLength(1)
    expect(ovs[0].querySelector('.st-ov-label').textContent).toContain('“Hello world this i”') // slice(0,18), quoted
    expect(ovs[0].style.left).toBe(`${LANE_PAD + 1 * ZOOM}px`) // start_seconds = 1
    expect(ovs[0].style.width).toBe(`${2 * ZOOM}px`)           // 3 - 1 = 2s
  })
  it('labels an IMAGE overlay by asset basename (no quotes)', () => {
    const doc = { cuts: fullDoc.cuts, overlays: [{ type: 'image', asset_id: 'images/logo.png', start_seconds: 0, end_seconds: 2, position: { x: 0, y: 0, width: 100 } }] }
    const { container } = renderTimeline(doc, 5)
    const label = container.querySelector('.st-ov .st-ov-label')
    expect(label.textContent).toContain('logo.png')
    expect(label.textContent).not.toContain('“')
  })
  it('shows the empty hint when there are no overlays', () => {
    const { container } = renderTimeline({ cuts: fullDoc.cuts }, 5)
    expect(container.querySelectorAll('.st-ov')).toHaveLength(0)
    expect(container.querySelector('.st-lane-ov .st-lane-empty')).toBeInTheDocument()
  })
})

describe('timeline clip labels carry NO icon/emoji (removed)', () => {
  it('an overlay block label is just its text/name — no T/▦/🖼 prefix', () => {
    const doc = {
      cuts: fullDoc.cuts,
      overlays: [
        { type: 'text', text: 'Caption', start_seconds: 0, end_seconds: 2, track: 0 },
        { type: 'image', asset_id: 'images/logo.png', start_seconds: 0, end_seconds: 2, position: { x: 0, y: 0, width: 100 }, track: 1 },
        { type: 'video', asset_id: 'clips/pip.mp4', start_seconds: 0, end_seconds: 2, position: { x: 0, y: 0, width: 100 }, track: 2 },
      ],
    }
    const { container } = renderTimeline(doc, 5)
    const labels = [...container.querySelectorAll('.st-ov-label')].map(l => l.textContent)
    const all = labels.join(' ')
    for (const glyph of ['🖼', '▦']) expect(all).not.toContain(glyph)
    // the image/video labels are exactly the basename (no leading glyph + space)
    expect(labels).toContain('logo.png')
    expect(labels).toContain('pip.mp4')
    // the text label is the quoted text with no 'T ' prefix
    expect(labels.some(l => l.startsWith('“'))).toBe(true)
    expect(labels.some(l => l.startsWith('T '))).toBe(false)
  })
  it('audio blocks show only the name (no ♫ / 🎙 / ♪) and the SFX point marker has no glyph', () => {
    const { container } = renderTimeline(fullDoc, 5)
    expect(container.querySelector('.st-aud-music').textContent).toBe('bed.mp3')
    expect(container.querySelector('.st-aud-narration').textContent).toBe('line1.mp3')
    expect(container.querySelector('.st-aud-sfx').textContent).toBe('') // point marker = empty dot
    const audioText = [...container.querySelectorAll('.st-aud')].map(a => a.textContent).join(' ')
    for (const glyph of ['♫', '🎙', '♪']) expect(audioText).not.toContain(glyph)
  })
})

describe('overlay tracks (multi-lane, feat 1)', () => {
  const doc = {
    cuts: fullDoc.cuts,
    overlays: [
      { type: 'text', text: 'low', start_seconds: 0, end_seconds: 2, track: 0 },
      { type: 'image', asset_id: 'top.png', start_seconds: 0, end_seconds: 2, track: 2 },
    ],
  }
  it('renders a lane per track (0..max) PLUS one empty new-track lane on top', () => {
    const { container } = renderTimeline(doc, 5)
    // tracks 0,1,2 = 3 lanes + 1 empty new-track lane = 4
    expect(container.querySelectorAll('.st-lane-ov')).toHaveLength(4)
    expect(container.querySelector('.st-lane-ov-new')).toBeInTheDocument()
  })
  it('renders each overlay block in its own track lane', () => {
    const { container } = renderTimeline(doc, 5)
    expect(container.querySelectorAll('.st-ov')).toHaveLength(2)
    // the empty new-track lane is the FIRST rendered (highest track on top)
    expect(container.querySelectorAll('.st-lane-ov')[0].className).toContain('st-lane-ov-new')
  })
  it('the Arrange button is enabled with >=2 overlays, disabled below that', () => {
    const { getByRole, unmount } = render(<StudioTimeline doc={doc} dur={5} {...baseProps} />)
    expect(getByRole('button', { name: /arrange/i })).not.toBeDisabled()
    unmount()
    const single = render(<StudioTimeline doc={{ cuts: fullDoc.cuts, overlays: [{ type: 'text', text: 'x', start_seconds: 0, end_seconds: 1 }] }} dur={5} {...baseProps} />)
    expect(single.getByRole('button', { name: /arrange/i })).toBeDisabled()
  })
})

describe('audio lane (feature: SFX / music / narration visible on the timeline)', () => {
  it('renders music as a full-timeline bed, narration as a block, sfx as a point marker', () => {
    // dur prop = 8 but timelineDuration(doc) = 5 (cuts concat to 5). The bed width must follow
    // the DERIVED timelineDuration (5), not the prop (8) — proving it's render-faithful, not a
    // coincidence of the prop. (FFmpeg amix duration=first cuts the bed to the base length.)
    const { container } = renderTimeline(fullDoc, 8)
    const auds = container.querySelectorAll('.st-aud')
    expect(auds).toHaveLength(3)

    const music = container.querySelector('.st-aud-music')
    expect(music.style.left).toBe(`${LANE_PAD}px`)
    expect(music.style.width).toBe(`${5 * ZOOM}px`)            // = timelineDuration(doc), NOT dur=8
    expect(music.textContent).toContain('bed.mp3')

    const vo = container.querySelector('.st-aud-narration')
    expect(vo.style.left).toBe(`${LANE_PAD + 0.5 * ZOOM}px`)
    expect(vo.style.width).toBe(`${1.5 * ZOOM}px`)             // 2 - 0.5
    expect(vo.textContent).toContain('line1.mp3')

    const fx = container.querySelector('.st-aud-sfx')
    expect(fx.className).toContain('pt')                        // point marker
    expect(fx.style.left).toBe(`${LANE_PAD + 3 * ZOOM}px`)
    expect(fx.style.width).toBe('12px')
  })
  it('draws one lane PER kind so a full-width bed + segment do not occlude each other', () => {
    // The reported bug: music (bed, 0→dur) and narration (0.5→2) were crammed in ONE row and the
    // narration block painted over the bed. Each kind must now own a separate .st-lane-audio row.
    const { container } = renderTimeline(fullDoc, 5)
    const lanes = [...container.querySelectorAll('.st-lane-audio')]
    expect(lanes).toHaveLength(3)
    expect(lanes.map(l => l.className.match(/st-lane-audio-(\w+)/)?.[1])).toEqual(['music', 'narration', 'sfx'])
    // music + narration live in DIFFERENT lane elements (the fix).
    const musicLane = container.querySelector('.st-lane-audio-music')
    const voLane = container.querySelector('.st-lane-audio-narration')
    expect(musicLane.querySelector('.st-aud-music')).toBeInTheDocument()
    expect(voLane.querySelector('.st-aud-narration')).toBeInTheDocument()
    expect(musicLane).not.toBe(voLane)
  })
  it('shows the empty hint when there is no audio', () => {
    const { container } = renderTimeline({ cuts: fullDoc.cuts }, 5)
    expect(container.querySelectorAll('.st-aud')).toHaveLength(0)
    expect(container.querySelector('.st-lane-audio .st-lane-empty')).toBeInTheDocument()
  })
  it('the music bed carries a draggable gain line + fade-in/out handles', () => {
    const doc = {
      cuts: fullDoc.cuts,
      audio: { music: { asset_id: 'music/bed.mp3', volume: 0.5, fade_in_seconds: 1, fade_out_seconds: 2 } },
    }
    const { container } = renderTimeline(doc, 5)
    const bed = container.querySelector('.st-aud-music')
    expect(bed.querySelector('.st-aud-gain')).toBeInTheDocument()
    expect(bed.querySelector('.st-aud-fade-in')).toBeInTheDocument()
    expect(bed.querySelector('.st-aud-fade-out')).toBeInTheDocument()
    // fade handle widths follow the durations (fade_in 1s, fade_out 2s at ZOOM px/s)
    expect(bed.querySelector('.st-aud-fade-in').style.width).toBe(`${1 * ZOOM}px`)
    expect(bed.querySelector('.st-aud-fade-out').style.width).toBe(`${2 * ZOOM}px`)
  })
  it('audio blocks are pointer-draggable divs (not buttons)', () => {
    const { container } = renderTimeline(fullDoc, 5)
    for (const sel of ['.st-aud-music', '.st-aud-narration', '.st-aud-sfx']) {
      expect(container.querySelector(sel).tagName).toBe('DIV')
    }
  })
})

describe('audio lane is selectable + deselect-on-empty (the two reported bugs)', () => {
  it('tapping an audio block (pointerdown+up, no move) selects it with kind + doc index', () => {
    // Audio blocks are pointer-draggable (like cuts/overlays); a bare TAP selects. The drag
    // itself needs layout (xToTime) so it's E2E — a tap is layout-free and testable here.
    const onSelect = vi.fn()
    const { container } = render(<StudioTimeline doc={fullDoc} dur={5} {...baseProps} onSelect={onSelect} />)
    fireEvent.pointerDown(container.querySelector('.st-aud-music'))
    fireEvent.pointerUp(window)
    expect(onSelect).toHaveBeenCalledWith({ kind: 'audio', audioKind: 'music', index: null })
    fireEvent.pointerDown(container.querySelector('.st-aud-sfx'))
    fireEvent.pointerUp(window)
    expect(onSelect).toHaveBeenCalledWith({ kind: 'audio', audioKind: 'sfx', index: 0 })
  })
  it('marks the selected audio item', () => {
    const { container } = render(
      <StudioTimeline doc={fullDoc} dur={5} {...baseProps} selection={{ kind: 'audio', audioKind: 'music', index: null }} />)
    expect(container.querySelector('.st-aud-music').className).toContain('sel')
  })
  it('clicking empty timeline background deselects (→ Assets tab)', () => {
    const onSelect = vi.fn()
    const { container } = render(<StudioTimeline doc={fullDoc} dur={5} {...baseProps} onSelect={onSelect} />)
    fireEvent.pointerDown(container.querySelector('.st-lane-cuts'))   // empty lane area
    fireEvent.pointerDown(container.querySelector('.st-tl-content'))  // content background
    expect(onSelect).toHaveBeenCalledWith(null)
    expect(onSelect).toHaveBeenCalledTimes(2)
  })
  it('clicking a clip or the ruler does NOT deselect (so scrub/select keep working)', () => {
    const onSelect = vi.fn()
    const { container } = render(<StudioTimeline doc={fullDoc} dur={5} {...baseProps} onSelect={onSelect} />)
    fireEvent.pointerDown(container.querySelector('.st-clip'))
    fireEvent.pointerDown(container.querySelector('.st-ruler'))
    expect(onSelect).not.toHaveBeenCalledWith(null)
  })
})

describe('timeline toolbar (feat 2 + 5: transport + clip ops live here now)', () => {
  function renderTL(extra = {}) {
    const handlers = { onTogglePlay: vi.fn(), onSplit: vi.fn(), onDuplicate: vi.fn(), onDelete: vi.fn() }
    return { ...render(<StudioTimeline doc={fullDoc} dur={5} {...baseProps} {...handlers} {...extra} />), handlers }
  }
  it('shows play/pause and toggles it', () => {
    const { getByRole, handlers } = renderTL({ playing: false })
    const play = getByRole('button', { name: 'Play' })
    fireEvent.click(play)
    expect(handlers.onTogglePlay).toHaveBeenCalledOnce()
  })
  it('reflects the playing state on the transport button', () => {
    const { getByRole } = renderTL({ playing: true })
    expect(getByRole('button', { name: 'Pause' }).textContent).toContain('⏸')
  })
  it('split always fires; duplicate needs a selected cut; delete needs any selection', () => {
    const none = renderTL({ selection: null })
    fireEvent.click(none.getByRole('button', { name: /split/i }))
    expect(none.handlers.onSplit).toHaveBeenCalledOnce()
    expect(none.getByRole('button', { name: /duplicate/i })).toBeDisabled()
    expect(none.getByRole('button', { name: /delete/i })).toBeDisabled()
    none.unmount()

    const sel = renderTL({ selection: { kind: 'cut', id: 'a' } })
    fireEvent.click(sel.getByRole('button', { name: /duplicate/i }))
    fireEvent.click(sel.getByRole('button', { name: /delete/i }))
    expect(sel.handlers.onDuplicate).toHaveBeenCalledOnce()
    expect(sel.handlers.onDelete).toHaveBeenCalledOnce()
  })
})

describe('asset drag-and-drop onto the timeline', () => {
  const DND = 'application/x-opennolan-asset'
  it('dropping an asset on a lane calls onAssetDrop with kind, path, drop time, and the target lane', () => {
    const onAssetDrop = vi.fn()
    const { container } = render(<StudioTimeline doc={fullDoc} dur={5} {...baseProps} onAssetDrop={onAssetDrop} />)
    // drop on the main cuts lane → target.lane === 'cuts' (becomes a main-timeline clip)
    const cutsLane = container.querySelector('.st-lane-cuts')
    fireEvent.drop(cutsLane, { dataTransfer: { getData: () => JSON.stringify({ kind: 'images', path: 'images/logo.png' }), types: [DND] } })
    expect(onAssetDrop).toHaveBeenCalledWith('images', 'images/logo.png', expect.any(Number), expect.objectContaining({ lane: 'cuts' }))
  })
  it('dropping on an overlay track lane targets that track', () => {
    const onAssetDrop = vi.fn()
    const { container } = render(<StudioTimeline doc={fullDoc} dur={5} {...baseProps} onAssetDrop={onAssetDrop} />)
    const ovLane = container.querySelector('.st-lane-ov') // first rendered = the top (new) track
    fireEvent.drop(ovLane, { dataTransfer: { getData: () => JSON.stringify({ kind: 'images', path: 'images/logo.png' }), types: [DND] } })
    expect(onAssetDrop).toHaveBeenCalledWith('images', 'images/logo.png', expect.any(Number), expect.objectContaining({ lane: 'overlay', track: expect.any(Number) }))
  })
  it('shows a drop affordance while an asset is dragged over', () => {
    const { container } = render(<StudioTimeline doc={fullDoc} dur={5} {...baseProps} onAssetDrop={vi.fn()} />)
    const scroll = container.querySelector('.st-tl-scroll')
    fireEvent.dragOver(scroll, { dataTransfer: { types: [DND], dropEffect: '' } })
    expect(scroll.className).toContain('drop')
  })
})

describe('ruler + playhead', () => {
  it('positions the playhead at LANE_PAD + playhead*zoom', () => {
    const { container } = renderTimeline(fullDoc, 5)
    expect(container.querySelector('.st-playhead').style.left).toBe(`${LANE_PAD + 2 * ZOOM}px`)
  })
  it('shows the total duration and a ruler of ticks', () => {
    const { container } = renderTimeline(fullDoc, 5)
    expect(container.querySelector('.st-tl-dur').textContent).toContain('0:05.0')
    expect(container.querySelectorAll('.st-tick').length).toBeGreaterThan(0)
  })
})
