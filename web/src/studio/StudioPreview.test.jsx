// Component tests for the WYSIWYG preview (feat 4): the canvas safe-frame, overlays composited
// on it (z-ordered by track), an <img> frame for an image cut, and select-on-pointerdown.
// jsdom has no layout engine, so we stub ResizeObserver + getBoundingClientRect to give the frame
// a real width (the only geometry the overlay layer needs); placement px math is E2E, not here.

import { describe, it, expect, vi, beforeAll } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import StudioPreview from './StudioPreview.jsx'

beforeAll(() => {
  // ResizeObserver fires once so the frame measures itself; getBoundingClientRect gives a width.
  global.ResizeObserver = class {
    constructor(cb) { this.cb = cb }
    observe() { this.cb([]) }
    disconnect() {}
  }
  Element.prototype.getBoundingClientRect = function () {
    return { width: 1080, height: 1920, left: 0, top: 0, right: 1080, bottom: 1920, x: 0, y: 0 }
  }
  window.HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve())
  window.HTMLMediaElement.prototype.pause = vi.fn()
})

const base = {
  projectId: 'p1', canvas: { width: 1080, height: 1920 }, playhead: 1,
  previewMode: 'source', renderPath: null, renderVersion: 0, playing: false, selection: null,
  onScrub: vi.fn(), onPlayingChange: vi.fn(),
  onSelectOverlay: vi.fn(), onOverlayPosition: vi.fn(), onOverlayDragBegin: vi.fn(),
}

const videoCut = { id: 'c1', source: 'clips/a.mp4', in_seconds: 0, out_seconds: 5 }

describe('WYSIWYG canvas', () => {
  it('renders a canvas safe-frame with the video as the frame media', () => {
    const { container } = render(<StudioPreview {...base} doc={{ cuts: [videoCut] }} />)
    expect(container.querySelector('.st-safe-frame')).toBeInTheDocument()
    expect(container.querySelector('video.st-frame-media')).toBeInTheDocument()
  })

  it('renders an <img> frame (not <video>) when the cut under the playhead is a still image', () => {
    const { container } = render(<StudioPreview {...base} doc={{ cuts: [{ id: 'c', source: 'p.png', in_seconds: 0, out_seconds: 5 }] }} />)
    expect(container.querySelector('img.st-frame-media')).toBeInTheDocument()
    expect(container.querySelector('video.st-frame-media')).toBeNull()
  })

  it('composites overlays visible at the playhead, sorted ascending by track (higher track last/on top)', () => {
    const doc = {
      cuts: [videoCut],
      overlays: [
        { type: 'image', asset_id: 'top.png', start_seconds: 0, end_seconds: 3, position: { x: 0, y: 0, width: 100 }, track: 2 },
        { type: 'text', text: 'hi', start_seconds: 0, end_seconds: 3, position: 'center', track: 0 },
        { type: 'image', asset_id: 'hidden.png', start_seconds: 8, end_seconds: 9, position: { x: 0, y: 0 }, track: 0 }, // not visible at t=1
      ],
    }
    const { container } = render(<StudioPreview {...base} doc={doc} />)
    const items = container.querySelectorAll('.st-ov-canvas')
    expect(items).toHaveLength(2) // the t=8 overlay is filtered out
    // ascending track in DOM order: track 0 (text) first, track 2 (image) last/on top
    expect(items[0].querySelector('.st-ov-text')).toBeInTheDocument()
    expect(items[1].tagName.toLowerCase()).toBe('img')
  })

  it('renders a video overlay as a playable <video> (preload=auto; muted unless audio_mix is on)', () => {
    const mk = (audioMix) => ({
      cuts: [videoCut],
      overlays: [{ type: 'video', asset_id: 'pip.mp4', start_seconds: 0, end_seconds: 3, position: { x: 0, y: 0, width: 200 }, track: 0, ...(audioMix ? { audio_mix: { enabled: true, volume: 1 } } : {}) }],
    })
    const { container } = render(<StudioPreview {...base} doc={mk(false)} />)
    const vid = container.querySelector('.st-ov-layer video.st-ov-canvas')
    expect(vid).toBeInTheDocument()
    expect(vid).toHaveAttribute('preload', 'auto')
    expect(vid.muted).toBe(true) // no audio_mix → muted in preview (matches export)

    const { container: c2 } = render(<StudioPreview {...base} doc={mk(true)} />)
    expect(c2.querySelector('.st-ov-layer video.st-ov-canvas').muted).toBe(false) // audio_mix on → audible
  })

  it('pointerdown on a canvas overlay selects it', () => {
    const onSelectOverlay = vi.fn()
    const doc = { cuts: [videoCut], overlays: [{ type: 'text', text: 'x', start_seconds: 0, end_seconds: 3, position: 'center', track: 0 }] }
    const { container } = render(<StudioPreview {...base} doc={doc} onSelectOverlay={onSelectOverlay} />)
    fireEvent.pointerDown(container.querySelector('.st-ov-canvas'))
    expect(onSelectOverlay).toHaveBeenCalledWith(0)
  })
})

describe('main-clip position/scale + project background', () => {
  it('wraps the clip in a positioned + scaled box (move + resize)', () => {
    const doc = { cuts: [{ id: 'c1', source: 'clips/a.mp4', in_seconds: 0, out_seconds: 5, transform: { scale: 0.5 } }] }
    const { container } = render(<StudioPreview {...base} doc={doc} />)
    const box = container.querySelector('.st-clip-box')
    expect(box).toBeInTheDocument()
    expect(box.style.width).toBe('540px')  // 1080 canvas * 0.5 scale * 1 frame-factor
    expect(box.style.left).toBe('270px')   // centered: (1080 - 540) / 2
    expect(container.querySelector('video.st-frame-media')).toBeInTheDocument()
  })
  it('sizes the clip box from a NON-UNIFORM {x,y} scale (canvas-fraction box, preview == export)', () => {
    // split-screen panel: full width, half height of the 1080×1920 canvas (frame factor 1 here).
    const doc = { cuts: [{ id: 'c1', source: 'clips/a.mp4', in_seconds: 0, out_seconds: 5, transform: { scale: { x: 1, y: 0.5 } } }] }
    const { container } = render(<StudioPreview {...base} doc={doc} />)
    const box = container.querySelector('.st-clip-box')
    expect(box.style.width).toBe('1080px')  // canvas.width * 1
    expect(box.style.height).toBe('960px')  // canvas.height * 0.5
    expect(box.style.left).toBe('0px')      // centered: (1080 - 1080) / 2
    expect(box.style.top).toBe('480px')     // centered: (1920 - 960) / 2
  })
  it('paints a color background on the safe frame', () => {
    const doc = { cuts: [videoCut], metadata: { background: { type: 'color', color: '#ff0000' } } }
    const { container } = render(<StudioPreview {...base} doc={doc} />)
    expect(container.querySelector('.st-safe-frame').style.background).toMatch(/#ff0000|rgb\(255, 0, 0\)/)
  })
  it('renders an image background cover layer behind the clip', () => {
    const doc = { cuts: [videoCut], metadata: { background: { type: 'image', asset_id: 'images/bg.png' } } }
    const { container } = render(<StudioPreview {...base} doc={doc} />)
    expect(container.querySelector('img.st-bg-media')).toBeInTheDocument()
  })
})
