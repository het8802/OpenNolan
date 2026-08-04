// Component tests for the inspector's selection routing + the commit-on-blur contract.
// With nothing selected the panel shows the Assets tab (feat 4); a selection shows its editor.

import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import StudioInspector from './StudioInspector.jsx'

// The no-selection branch is the Assets folder browser, which lists the project tree over the
// API. Stub it here; its own suite (StudioAssets.test.jsx) covers navigation and click-to-add.
vi.mock('../api.js', () => ({
  browseProject: vi.fn(() => Promise.resolve({ path: '', entries: [] })),
  fileUrl: (id, path) => `mock:${path}`,
}))

function setup(overrides = {}) {
  const props = {
    projectId: 'p1', doc: { overlays: [] }, canvas: { width: 1080, height: 1920 }, ffmpeg: true,
    selCut: null, selOverlayIndex: -1, selAudio: null, selAudioObj: null, playhead: 0,
    assets: { kinds: { images: [], video: [], audio: [], music: [] } }, sourceMetas: {},
    onUpdateCut: vi.fn(), onUpdateOverlay: vi.fn(), onUpdateAudio: vi.fn(),
    onLiveUpdateCut: vi.fn(), onLiveUpdateOverlay: vi.fn(), onLiveUpdateAudio: vi.fn(), onScrubBegin: vi.fn(),
    onSetKeyframes: vi.fn(), onUpsertKeyframe: vi.fn(), onRemoveKeyframe: vi.fn(),
    onAddImage: vi.fn(), onAddClip: vi.fn(), onAddSfx: vi.fn(), onSetMusic: vi.fn(), onSetBackground: vi.fn(),
    ...overrides,
  }
  return { ...render(<StudioInspector {...props} />), props }
}

// A numeric property is now a scrub bar (role="slider"); CLICKING it (a press with no drag) reveals
// the type-in <input>. This helper performs that click and returns the focused number input.
function openTypeInput(getByRole, name) {
  const bar = getByRole('slider', { name })
  fireEvent.pointerDown(bar, { button: 0, clientX: 10 })
  fireEvent.pointerUp(bar, { clientX: 10 }) // no movement → click → edit mode
  return bar
}

describe('selection routing', () => {
  it('shows the Assets folder browser when nothing is selected (feat 4)', () => {
    const { getByText, getByRole } = setup()
    expect(getByText('Assets')).toBeInTheDocument()
    expect(getByRole('button', { name: 'Project' })).toBeInTheDocument() // breadcrumb root
  })

  it('shows the video-clip inspector for a selected video cut', () => {
    const selCut = { id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 4, speed: 1 }
    const { getByText } = setup({ selCut })
    expect(getByText('Video clip · c1')).toBeInTheDocument()
    expect(getByText('Source')).toBeInTheDocument()
    expect(getByText('Trim & speed')).toBeInTheDocument()
    expect(getByText('Transitions')).toBeInTheDocument()
  })

  it('shows the image-clip inspector (image_main) for a still image cut — duration, no speed', () => {
    const selCut = { id: 'c2', source: 'photo.png', in_seconds: 0, out_seconds: 5 }
    const { getByText, queryByText } = setup({ selCut })
    expect(getByText('Image clip · c2')).toBeInTheDocument()
    // "Duration" is the section header here (a field of the same name lives under Transitions now
    // that the unit moved from the caption into the value), so target the header specifically.
    expect(getByText('Duration', { selector: '.st-sec-h' })).toBeInTheDocument()
    expect(queryByText('Trim & speed')).not.toBeInTheDocument()
  })

  it('shows the text-overlay inspector', () => {
    const doc = { overlays: [{ type: 'text', text: 'Hi', start_seconds: 0, end_seconds: 2, position: 'bottom-center', opacity: 1 }] }
    const { getByText } = setup({ doc, selOverlayIndex: 0 })
    expect(getByText('Text overlay')).toBeInTheDocument()
    expect(getByText('Timing')).toBeInTheDocument()
    expect(getByText('Text')).toBeInTheDocument()
  })

  it('shows the image-overlay inspector (track + position, NO source-audio — images are silent)', () => {
    const doc = { overlays: [{ type: 'image', asset_id: 'logo.png', start_seconds: 0, end_seconds: 3, position: { x: 10, y: 10, width: 200 }, opacity: 1, track: 0 }] }
    const { getByText, queryByText } = setup({ doc, selOverlayIndex: 0 })
    expect(getByText('Image overlay')).toBeInTheDocument()
    expect(getByText('Position & size')).toBeInTheDocument()
    expect(getByText('Track (z-layer)')).toBeInTheDocument()
    expect(queryByText('Source audio')).not.toBeInTheDocument()
  })

  it('shows the video-overlay inspector with a source-audio toggle', () => {
    const doc = { overlays: [{ type: 'video', asset_id: 'pip.mp4', start_seconds: 0, end_seconds: 3, position: { x: 10, y: 10, width: 200 }, opacity: 1, track: 1 }] }
    const { getByText } = setup({ doc, selOverlayIndex: 0 })
    expect(getByText('Video overlay')).toBeInTheDocument()
    expect(getByText('Position & size')).toBeInTheDocument()
    expect(getByText('Source audio')).toBeInTheDocument()
  })

  it('shows the audio inspector for a selected SFX, and commits a typed edit', () => {
    const { getByText, getByRole, getByDisplayValue, props } = setup({
      selAudio: { audioKind: 'sfx', index: 0 }, selAudioObj: { asset_id: 'whoosh.mp3', start_seconds: 3, volume: 1 },
    })
    expect(getByText('Sound effect')).toBeInTheDocument()
    expect(getByRole('slider', { name: 'Start' }).textContent).toContain('3') // value shown in the bar
    openTypeInput(getByRole, 'Start')                 // click the bar → type-in input appears
    const start = getByDisplayValue('3')
    fireEvent.change(start, { target: { value: '4' } })
    expect(props.onUpdateAudio).not.toHaveBeenCalled() // typing does NOT commit
    fireEvent.blur(start)
    expect(props.onUpdateAudio).toHaveBeenCalledWith({ start_seconds: 4 })
  })

  it('shows the music-bed inspector for a selected music track', () => {
    const { getByText } = setup({
      selAudio: { audioKind: 'music', index: null }, selAudioObj: { asset_id: 'bed.mp3', volume: 0.6 },
    })
    expect(getByText('Music bed')).toBeInTheDocument()
    expect(getByText('Levels')).toBeInTheDocument()
  })
})

describe('text color uses a color picker (swatch + text), not a bare text box', () => {
  const textDoc = { overlays: [{ type: 'text', text: 'Hi', start_seconds: 0, end_seconds: 2, position: 'center', opacity: 1, color: 'green' }] }
  it('renders a native swatch picker alongside a text input showing the named color', () => {
    const { container, getByDisplayValue } = setup({ doc: textDoc, selOverlayIndex: 0 })
    expect(container.querySelector('.st-color-swatch')).toBeInTheDocument()
    expect(container.querySelector('.st-color-text')).toBeInTheDocument()
    expect(getByDisplayValue('green')).toBeInTheDocument() // named color still typeable
  })
  it('commits a typed color on blur (named colors preserved)', () => {
    const { getByDisplayValue, props } = setup({ doc: textDoc, selOverlayIndex: 0 })
    const text = getByDisplayValue('green')
    fireEvent.change(text, { target: { value: 'red' } })
    expect(props.onUpdateOverlay).not.toHaveBeenCalled()   // typing does not commit
    fireEvent.blur(text)
    expect(props.onUpdateOverlay).toHaveBeenCalledWith(0, { color: 'red' })
  })
  it('a swatch pick coalesces into ONE undo step (snapshot once + live)', () => {
    const { container, props } = setup({ doc: textDoc, selOverlayIndex: 0 })
    const swatch = container.querySelector('.st-color-swatch')
    fireEvent.input(swatch, { target: { value: '#00ff00' } })
    expect(props.onScrubBegin).toHaveBeenCalledTimes(1)
    expect(props.onLiveUpdateOverlay).toHaveBeenCalledWith(0, { color: '#00ff00' })
    expect(props.onUpdateOverlay).not.toHaveBeenCalled()    // a swatch drag never per-frame commits
  })
})

describe('image overlay self-heals a string anchor into an object position', () => {
  // The renderer rejects a named-anchor string for image/video overlays; the inspector
  // normalizes it on mount so a saved doc is always renderable.
  it('rewrites a string position to anchorToXY on mount', () => {
    const doc = { overlays: [{ type: 'image', asset_id: 'logo.png', start_seconds: 0, end_seconds: 3, position: 'bottom-center', opacity: 1 }] }
    const { props } = setup({ doc, selOverlayIndex: 0 })
    expect(props.onUpdateOverlay).toHaveBeenCalledWith(0, { position: { x: 432, y: 1632, width: 270 } })
  })
})

describe('ScrubField: drag to adjust, click to type (manual entry preserved)', () => {
  const selCut = { id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 4, speed: 1 }

  it('renders numeric props as draggable scrub bars showing the value', () => {
    const { getByRole } = setup({ selCut })
    const out = getByRole('slider', { name: 'Out' })
    expect(out).toBeInTheDocument()
    expect(out.textContent).toContain('4')
  })

  it('typing after a click commits through onUpdateCut only on blur', () => {
    const { getByRole, getByDisplayValue, props } = setup({ selCut })
    openTypeInput(getByRole, 'Out')                    // click the bar → type-in input
    const out = getByDisplayValue('4')
    fireEvent.change(out, { target: { value: '5' } })
    expect(props.onUpdateCut).not.toHaveBeenCalled()   // typing does NOT commit
    fireEvent.blur(out)
    expect(props.onUpdateCut).toHaveBeenCalledWith('c1', { out_seconds: 5 })
  })

  it('a drag snapshots ONCE then live-updates (one undo step), never committing per frame', () => {
    const { getByRole, props } = setup({ selCut })
    const speed = getByRole('slider', { name: 'Speed' })  // step 0.1, start value 1
    fireEvent.pointerDown(speed, { button: 0, clientX: 100 })
    fireEvent.pointerMove(document.body, { clientX: 140 }) // +40px → +4.0 at step 0.1
    fireEvent.pointerUp(document.body, { clientX: 140 })
    expect(props.onScrubBegin).toHaveBeenCalledTimes(1)    // exactly one snapshot for the whole drag
    expect(props.onLiveUpdateCut).toHaveBeenCalled()        // per-frame live updates
    expect(props.onLiveUpdateCut).toHaveBeenLastCalledWith('c1', { speed: 5 })
    expect(props.onUpdateCut).not.toHaveBeenCalled()        // a drag never commits per frame
  })

  it('a bare click (no movement) does NOT snapshot or live-update — it just opens the input', () => {
    const { getByRole, getByDisplayValue, props } = setup({ selCut })
    openTypeInput(getByRole, 'Out')
    expect(props.onScrubBegin).not.toHaveBeenCalled()
    expect(props.onLiveUpdateCut).not.toHaveBeenCalled()
    expect(getByDisplayValue('4')).toBeInTheDocument()      // edit mode is open
  })

  it('shows a fill bar for a bounded field (opacity 0..1) but not for an unbounded one (speed)', () => {
    const doc = { overlays: [{ type: 'image', asset_id: 'logo.png', start_seconds: 0, end_seconds: 3, position: { x: 0, y: 0, width: 100 }, opacity: 0.5, track: 0 }] }
    const { getByRole, container } = setup({ doc, selOverlayIndex: 0 })
    const opacity = getByRole('slider', { name: 'Opacity' })
    const fill = opacity.querySelector('.st-scrub-fill')
    expect(fill).toBeInTheDocument()
    expect(fill.style.width).toBe('50%')               // (0.5 - 0) / (1 - 0)
    expect(container.querySelector('.st-f-scrub')).toBeInTheDocument()
  })

  it('typed entry preserves sub-step precision (does NOT quantize to the drag step)', () => {
    const { getByRole, getByDisplayValue, props } = setup({ selCut })
    openTypeInput(getByRole, 'Out')
    const out = getByDisplayValue('4')
    fireEvent.change(out, { target: { value: '4.73' } })
    fireEvent.blur(out)
    expect(props.onUpdateCut).toHaveBeenCalledWith('c1', { out_seconds: 4.73 }) // not 4.7
  })

  it('retyping the SAME value commits nothing (no dead undo step / redo wipe)', () => {
    const { getByRole, getByDisplayValue, props } = setup({ selCut })
    openTypeInput(getByRole, 'Out')
    const out = getByDisplayValue('4')
    fireEvent.blur(out)                                 // unchanged
    expect(props.onUpdateCut).not.toHaveBeenCalled()
  })

  it('arrow keys nudge the value as ONE coalesced undo step (snapshot once, live per repeat)', () => {
    const doc = { overlays: [{ type: 'image', asset_id: 'logo.png', start_seconds: 0, end_seconds: 3, position: { x: 0, y: 0, width: 100 }, opacity: 0.5, track: 0 }] }
    const { getByRole, props } = setup({ doc, selOverlayIndex: 0 })
    const opacity = getByRole('slider', { name: 'Opacity' })
    fireEvent.keyDown(opacity, { key: 'ArrowUp' })      // held-key repeat = two keydowns, no keyup
    fireEvent.keyDown(opacity, { key: 'ArrowUp' })
    expect(props.onScrubBegin).toHaveBeenCalledTimes(1) // exactly one snapshot for the whole run
    expect(props.onLiveUpdateOverlay).toHaveBeenCalledWith(0, { opacity: 0.55 })
    expect(props.onUpdateOverlay).not.toHaveBeenCalled() // arrows never per-frame commit
  })

  it('an arrow at a boundary (opacity already at max) is a pure no-op — no snapshot, no write', () => {
    const doc = { overlays: [{ type: 'image', asset_id: 'logo.png', start_seconds: 0, end_seconds: 3, position: { x: 0, y: 0, width: 100 }, opacity: 1, track: 0 }] }
    const { getByRole, props } = setup({ doc, selOverlayIndex: 0 })
    fireEvent.keyDown(getByRole('slider', { name: 'Opacity' }), { key: 'ArrowUp' })
    expect(props.onScrubBegin).not.toHaveBeenCalled()
    expect(props.onLiveUpdateOverlay).not.toHaveBeenCalled()
    expect(props.onUpdateOverlay).not.toHaveBeenCalled()
  })

  it('an unset numeric field reads "auto" and still carries aria-valuenow (valid slider)', () => {
    const { getByRole } = setup({
      selAudio: { audioKind: 'narration', index: 0 }, selAudioObj: { asset_id: 'vo.mp3', start_seconds: 0 }, // no end_seconds
    })
    const end = getByRole('slider', { name: 'End' })
    expect(end.textContent).toContain('auto')
    expect(end).toHaveAttribute('aria-valuenow')        // ARIA requires a value on role=slider
  })
})

describe('main-clip Position & size + canvas background', () => {
  it('shows a Position & size control (Scale + X/Y sliders) for a video cut', () => {
    const selCut = { id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 4, speed: 1 }
    const { getByText, getByRole } = setup({ selCut, sourceMetas: { 'a.mp4': { width: 1920, height: 1080 } } })
    expect(getByText('Position & size')).toBeInTheDocument()
    expect(getByRole('slider', { name: 'Scale' })).toBeInTheDocument()
    expect(getByRole('slider', { name: 'X' })).toBeInTheDocument()
    expect(getByRole('slider', { name: 'Y' })).toBeInTheDocument()
  })
  it('editing Scale commits transform.scale through onUpdateCut (preserving the position)', () => {
    const selCut = { id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 4, transform: { position: { x: 5, y: 6 } } }
    const { getByRole, getByDisplayValue, props } = setup({ selCut, sourceMetas: { 'a.mp4': { width: 1920, height: 1080 } } })
    openTypeInput(getByRole, 'Scale')
    const input = getByDisplayValue('1')
    fireEvent.change(input, { target: { value: '0.5' } })
    fireEvent.blur(input)
    expect(props.onUpdateCut).toHaveBeenCalledWith('c1', { transform: { position: { x: 5, y: 6 }, scale: 0.5 } })
  })

  // ── non-uniform (per-axis) scale: Lock-aspect toggle + Scale X / Scale Y ──
  const metaXY = { 'a.mp4': { width: 1920, height: 1080 } }
  it('a uniform-scale cut shows a single Scale field + a checked "Lock aspect" toggle', () => {
    const selCut = { id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 4, transform: { scale: 1 } }
    const { getByRole, getByLabelText, queryByRole } = setup({ selCut, sourceMetas: metaXY })
    expect(getByRole('slider', { name: 'Scale' })).toBeInTheDocument()
    expect(queryByRole('slider', { name: 'Scale X' })).not.toBeInTheDocument()
    const lock = getByLabelText(/Lock aspect/i)
    expect(lock).toBeChecked() // uniform = locked
  })

  it('unchecking "Lock aspect" expands the uniform scale into an {x,y} object', () => {
    const selCut = { id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 4, transform: { scale: 0.5, position: { x: 5, y: 6 } } }
    const { getByLabelText, props } = setup({ selCut, sourceMetas: metaXY })
    fireEvent.click(getByLabelText(/Lock aspect/i)) // toggle OFF
    expect(props.onUpdateCut).toHaveBeenCalledWith('c1', { transform: { position: { x: 5, y: 6 }, scale: { x: 0.5, y: 0.5 } } })
  })

  it('an {x,y}-scale cut shows Scale X / Scale Y fields and an UNchecked lock toggle', () => {
    const selCut = { id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 4, transform: { scale: { x: 1, y: 0.5 } } }
    const { getByRole, getByLabelText, queryByRole } = setup({ selCut, sourceMetas: metaXY })
    expect(getByRole('slider', { name: 'Scale X' }).textContent).toContain('1')
    expect(getByRole('slider', { name: 'Scale Y' }).textContent).toContain('0.5')
    expect(queryByRole('slider', { name: 'Scale' })).not.toBeInTheDocument()
    expect(getByLabelText(/Lock aspect/i)).not.toBeChecked()
  })

  it('editing Scale Y commits a {x,y} object preserving Scale X (Save never 422s)', () => {
    const selCut = { id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 4, transform: { scale: { x: 1, y: 0.5 } } }
    const { getByRole, getByDisplayValue, props } = setup({ selCut, sourceMetas: metaXY })
    openTypeInput(getByRole, 'Scale Y')
    const input = getByDisplayValue('0.5')
    fireEvent.change(input, { target: { value: '0.75' } })
    fireEvent.blur(input)
    expect(props.onUpdateCut).toHaveBeenCalledWith('c1', { transform: { scale: { x: 1, y: 0.75 } } })
  })

  it('re-checking "Lock aspect" collapses the {x,y} object back to a uniform number (X axis wins)', () => {
    const selCut = { id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 4, transform: { scale: { x: 0.8, y: 0.4 } } }
    const { getByLabelText, props } = setup({ selCut, sourceMetas: metaXY })
    fireEvent.click(getByLabelText(/Lock aspect/i)) // toggle ON
    expect(props.onUpdateCut).toHaveBeenCalledWith('c1', { transform: { scale: 0.8 } })
  })

  it('the empty panel exposes a Canvas background control; Black clears it', () => {
    const { getByText, props } = setup({}) // nothing selected → Assets/empty panel
    expect(getByText('Canvas background')).toBeInTheDocument()
    fireEvent.click(getByText('Black'))
    expect(props.onSetBackground).toHaveBeenCalledWith(null)
  })
  it('picking a background image sets metadata.background to that image', () => {
    const assets = { kinds: { images: [{ path: 'images/bg.png', name: 'bg.png' }], video: [], audio: [], music: [] } }
    const { container, props } = setup({ assets })
    const imgBtn = container.querySelector('.st-bg-img')
    expect(imgBtn).toBeInTheDocument()
    fireEvent.click(imgBtn)
    expect(props.onSetBackground).toHaveBeenCalledWith({ type: 'image', asset_id: 'images/bg.png' })
  })
})
