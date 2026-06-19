// Component tests for the PROJECT toolbar's contract after the feat 2/4/5 relocation:
// transport (play/pause) and clip ops (split/duplicate/delete) now live in the TIMELINE
// toolbar (see StudioTimeline.test.jsx), and +Image became an Assets-tab action. This bar
// keeps only the global, non-transport actions.

import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import StudioToolbar from './StudioToolbar.jsx'

function setup(overrides = {}) {
  const handlers = Object.fromEntries(
    ['onUndo', 'onRedo', 'onSave', 'onRender', 'onPreviewMode', 'onAddText', 'onCanvas'].map(k => [k, vi.fn()]))
  const props = {
    doc: { render_runtime: 'ffmpeg' }, canvas: { width: 1080, height: 1920 }, ffmpeg: true,
    canUndo: true, canRedo: true, dirty: true, rendering: false, hasRender: false, previewMode: 'source',
    ...handlers, ...overrides,
  }
  return { ...render(<StudioToolbar {...props} />), handlers, props }
}

describe('global controls present', () => {
  it('shows undo/redo, +Text, canvas, the preview toggle, Save and Render', () => {
    const { getByTitle, getByRole } = setup()
    expect(getByTitle('Undo (⌘Z)')).toBeInTheDocument()
    expect(getByTitle('Redo (⇧⌘Z)')).toBeInTheDocument()
    expect(getByRole('button', { name: /text/i })).toBeInTheDocument()
    expect(getByTitle(/Output 1080×1920/)).toBeInTheDocument()
    expect(getByRole('button', { name: /save/i })).toBeInTheDocument()
    expect(getByTitle('Render preview (render-once)')).toBeInTheDocument()
  })

  it('no longer hosts transport, clip ops, or +Image (those moved)', () => {
    const { queryByRole, queryByTitle } = setup({ rendering: false })
    expect(queryByRole('button', { name: 'Play' })).toBeNull()
    expect(queryByRole('button', { name: /split/i })).toBeNull()
    expect(queryByRole('button', { name: /duplicate/i })).toBeNull()
    expect(queryByTitle('Add an image overlay')).toBeNull()
  })

  it('disables Save when not dirty and Render while rendering', () => {
    const { getByRole } = setup({ dirty: false, rendering: true })
    expect(getByRole('button', { name: /save/i })).toBeDisabled()
    expect(getByRole('button', { name: /rendering/i })).toBeDisabled()
  })
})

describe('preview-mode toggle', () => {
  // "Composed render" is the segmented toggle; the primary action button is "Render preview…".
  it('disables the Render segment until a render exists, then enables it', () => {
    const off = setup({ hasRender: false })
    expect(off.getByTitle('Composed render')).toBeDisabled()
    expect(off.getByRole('button', { name: 'Source' }).className).toContain('on')
    off.unmount()
    const on = setup({ hasRender: true })
    expect(on.getByTitle('Composed render')).not.toBeDisabled()
  })
  it('switches preview mode on click', () => {
    const { getByTitle, handlers } = setup({ hasRender: true })
    fireEvent.click(getByTitle('Composed render'))
    expect(handlers.onPreviewMode).toHaveBeenCalledWith('render')
  })
})

describe('canvas selector', () => {
  it('reflects the current canvas and emits a preset on change', () => {
    const { getByRole, handlers } = setup()
    const sel = getByRole('combobox')
    fireEvent.change(sel, { target: { value: '16:9 · 1920×1080' } })
    expect(handlers.onCanvas).toHaveBeenCalledWith({ width: 1920, height: 1080 })
  })
})
