// Component tests for the inspector's selection routing + the commit-on-blur contract.
// With nothing selected the panel shows the Assets tab (feat 4); a selection shows its editor.

import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import StudioInspector from './StudioInspector.jsx'

function setup(overrides = {}) {
  const props = {
    projectId: 'p1', doc: { overlays: [] }, canvas: { width: 1080, height: 1920 }, ffmpeg: true,
    selCut: null, selOverlayIndex: -1, selAudio: null, selAudioObj: null, playhead: 0,
    assets: { kinds: { images: [], video: [], audio: [], music: [] } }, sourceMetas: {},
    onUpdateCut: vi.fn(), onUpdateOverlay: vi.fn(), onUpdateAudio: vi.fn(),
    onSetKeyframes: vi.fn(), onUpsertKeyframe: vi.fn(), onRemoveKeyframe: vi.fn(),
    onAddImage: vi.fn(), onAddClip: vi.fn(), onAddSfx: vi.fn(), onSetMusic: vi.fn(),
    ...overrides,
  }
  return { ...render(<StudioInspector {...props} />), props }
}

describe('selection routing', () => {
  it('shows the Assets tab when nothing is selected (feat 4)', () => {
    const { getByText, getByRole } = setup()
    expect(getByText('Assets')).toBeInTheDocument()
    expect(getByRole('button', { name: /images/i })).toBeInTheDocument()
    expect(getByRole('button', { name: /music/i })).toBeInTheDocument()
  })

  it('shows the clip inspector for a selected cut', () => {
    const selCut = { id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 4, speed: 1 }
    const { getByText } = setup({ selCut })
    expect(getByText('Clip · c1')).toBeInTheDocument()
    expect(getByText('Source')).toBeInTheDocument()
    expect(getByText('Trim & speed')).toBeInTheDocument()
    expect(getByText('Transitions')).toBeInTheDocument()
  })

  it('shows the text-overlay inspector', () => {
    const doc = { overlays: [{ type: 'text', text: 'Hi', start_seconds: 0, end_seconds: 2, position: 'bottom-center', opacity: 1 }] }
    const { getByText } = setup({ doc, selOverlayIndex: 0 })
    expect(getByText('Text overlay')).toBeInTheDocument()
    expect(getByText('Timing')).toBeInTheDocument()
    expect(getByText('Text')).toBeInTheDocument()
  })

  it('shows the image-overlay inspector with a source-audio toggle', () => {
    const doc = { overlays: [{ type: 'image', asset_id: 'logo.png', start_seconds: 0, end_seconds: 3, position: { x: 10, y: 10, width: 200 }, opacity: 1 }] }
    const { getByText } = setup({ doc, selOverlayIndex: 0 })
    expect(getByText('Image overlay')).toBeInTheDocument()
    expect(getByText('Image / video')).toBeInTheDocument()
    expect(getByText('Source audio')).toBeInTheDocument()
  })

  it('shows the audio inspector for a selected SFX, and commits an edit', () => {
    const { getByText, getByDisplayValue, props } = setup({
      selAudio: { audioKind: 'sfx', index: 0 }, selAudioObj: { asset_id: 'whoosh.mp3', start_seconds: 3, volume: 1 },
    })
    expect(getByText('Sound effect')).toBeInTheDocument()
    const start = getByDisplayValue('3')
    fireEvent.change(start, { target: { value: '4' } })
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

describe('image overlay self-heals a string anchor into an object position', () => {
  // The renderer rejects a named-anchor string for image/video overlays; the inspector
  // normalizes it on mount so a saved doc is always renderable.
  it('rewrites a string position to anchorToXY on mount', () => {
    const doc = { overlays: [{ type: 'image', asset_id: 'logo.png', start_seconds: 0, end_seconds: 3, position: 'bottom-center', opacity: 1 }] }
    const { props } = setup({ doc, selOverlayIndex: 0 })
    expect(props.onUpdateOverlay).toHaveBeenCalledWith(0, { position: { x: 432, y: 1632, width: 270 } })
  })
})

describe('NumField commits on blur, not on change', () => {
  it('commits a trimmed out-point through onUpdateCut only on blur', () => {
    const selCut = { id: 'c1', source: 'a.mp4', in_seconds: 0, out_seconds: 4, speed: 1 }
    const { getByDisplayValue, props } = setup({ selCut })
    const out = getByDisplayValue('4')                 // the Out (s) field
    fireEvent.change(out, { target: { value: '5' } })
    expect(props.onUpdateCut).not.toHaveBeenCalled()   // typing does NOT commit
    fireEvent.blur(out)
    expect(props.onUpdateCut).toHaveBeenCalledWith('c1', { out_seconds: 5 })
  })
})
