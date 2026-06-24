// Component tests for the Assets tab — focused on the read-only **renders** tab (the agent's
// HyperFrames clips from {project}/hf/renders, served as `agent_renders`). Renders behave like
// videos on the timeline: click appends a cut, the drag payload is tagged 'video', and the tab
// has no upload dropzone.

import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import StudioAssets from './StudioAssets.jsx'

function setup(overrides = {}) {
  const props = {
    projectId: 'p1',
    assets: {
      kinds: { images: [], video: [], audio: [], music: [] },
      agent_renders: [
        { path: 'hf/renders/anim_intro.mp4', name: 'anim_intro.mp4', mtime: 1 },
        { path: 'hf/renders/ov_caption.mov', name: 'ov_caption.mov', mtime: 2 },
      ],
    },
    onAddImage: vi.fn(), onAddClip: vi.fn(), onAddSfx: vi.fn(), onSetMusic: vi.fn(),
    onUploadAsset: vi.fn(),
    ...overrides,
  }
  return { ...render(<StudioAssets {...props} />), props }
}

describe('renders tab', () => {
  it('shows a renders tab with a count from agent_renders', () => {
    const { getByRole } = setup()
    expect(getByRole('button', { name: /renders \(2\)/i })).toBeInTheDocument()
  })

  it('lists the agent_renders clips as video thumbnails once selected', () => {
    const { getByRole, container } = setup()
    fireEvent.click(getByRole('button', { name: /renders/i }))
    const items = container.querySelectorAll('.asset-grid .asset-item')
    expect(items).toHaveLength(2)
    // video thumbnail markup (same as the 'video' kind), not an <img>.
    expect(container.querySelector('.asset-grid .asset-thumb.video video')).toBeTruthy()
    expect(container.querySelector('.asset-grid img')).toBeNull()
  })

  it('clicking a render appends it as a clip (onAddClip), like a video', () => {
    const { getByRole, container, props } = setup()
    fireEvent.click(getByRole('button', { name: /renders/i }))
    fireEvent.click(container.querySelector('.asset-grid .asset-item'))
    expect(props.onAddClip).toHaveBeenCalledWith('hf/renders/anim_intro.mp4')
    expect(props.onAddImage).not.toHaveBeenCalled()
  })

  it('drags with kind "video" (not "renders") so the timeline routes it correctly', () => {
    const { getByRole, container } = setup()
    fireEvent.click(getByRole('button', { name: /renders/i }))
    const setData = vi.fn()
    fireEvent.dragStart(container.querySelector('.asset-grid .asset-item'), { dataTransfer: { setData } })
    expect(setData).toHaveBeenCalledWith(
      'application/x-opennolan-asset',
      JSON.stringify({ kind: 'video', path: 'hf/renders/anim_intro.mp4' }),
    )
  })

  it('has no upload dropzone on the renders tab (renders are not uploadable)', () => {
    const { getByRole, container } = setup()
    expect(container.querySelector('.dropzone')).toBeTruthy() // present on the default (images) tab
    fireEvent.click(getByRole('button', { name: /renders/i }))
    expect(container.querySelector('.dropzone')).toBeNull()
  })

  it('empty agent_renders renders an empty-state, no crash', () => {
    const { getByRole, getByText } = setup({
      assets: { kinds: { images: [], video: [], audio: [], music: [] }, agent_renders: [] },
    })
    fireEvent.click(getByRole('button', { name: /^renders$/i }))
    expect(getByText('No renders yet.')).toBeInTheDocument()
  })
})
