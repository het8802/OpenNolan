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
  it('shows undo/redo, +Text, canvas, the preview toggle, Save and Export', () => {
    const { getByTitle, getByRole, getAllByTitle } = setup()
    expect(getByTitle('Undo (⌘Z)')).toBeInTheDocument()
    expect(getByTitle('Redo (⇧⌘Z)')).toBeInTheDocument()
    expect(getByRole('button', { name: /text/i })).toBeInTheDocument()
    // The canvas picker is rendered twice — inline, and again inside the More overflow menu.
    // A container query on .st-bar shows exactly one of them, so only one is ever in the
    // accessibility tree; jsdom applies no container queries, hence both are present here.
    expect(getAllByTitle(/Output 1080×1920/)).toHaveLength(2)
    expect(getByRole('button', { name: /save/i })).toBeInTheDocument()
    // The terminal action is "Export", not "Render": it sat beside the Source/Render preview
    // segment and read as a duplicate, and the old name taught render-to-preview.
    expect(getByTitle('Export the final MP4 (only changed scenes re-render)')).toBeInTheDocument()
    expect(getByRole('button', { name: /^export$/i })).toBeInTheDocument()
  })

  it('offers a labelled More overflow so Save and Export never leave the line', () => {
    const { getByTitle, container } = setup()
    // A native <details>/<summary> disclosure: keyboard-operable for free and announced as a
    // disclosure rather than a fake button, so it is queried by title, not by button role.
    expect(getByTitle('More toolbar controls')).toBeInTheDocument()
    // The controls that fold away are marked, and the ones that must not are not.
    expect(container.querySelectorAll('.st-grp-optional').length).toBeGreaterThan(0)
    const right = container.querySelector('.st-grp-right')
    expect(right.querySelector('.st-more')).toBeTruthy()
  })

  it('no longer hosts transport, clip ops, or +Image (those moved)', () => {
    const { queryByRole, queryByTitle } = setup({ rendering: false })
    expect(queryByRole('button', { name: 'Play' })).toBeNull()
    expect(queryByRole('button', { name: /split/i })).toBeNull()
    expect(queryByRole('button', { name: /duplicate/i })).toBeNull()
    expect(queryByTitle('Add an image overlay')).toBeNull()
  })

  it('disables Save when not dirty and Export while exporting', () => {
    const { getByRole } = setup({ dirty: false, rendering: true })
    expect(getByRole('button', { name: /save/i })).toBeDisabled()
    expect(getByRole('button', { name: /exporting/i })).toBeDisabled()
  })
})

describe('preview-mode toggle', () => {
  // "Composed render" is the segmented PREVIEW toggle; the terminal action beside it is
  // "Export". Those are different things, which is exactly why the action was renamed.
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
    const { getAllByRole, handlers } = setup()
    // Two comboboxes exist (inline + More); a container query shows one. Either drives the doc.
    const sel = getAllByRole('combobox')[0]
    fireEvent.change(sel, { target: { value: '16:9 · 1920×1080' } })
    expect(handlers.onCanvas).toHaveBeenCalledWith({ width: 1920, height: 1080 })
  })
})
