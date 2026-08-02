// Component tests for the Assets FOLDER browser — navigation (breadcrumb + folder rows) and
// the click/drag contract per file kind. The backend's /browse endpoint is mocked; it is what
// hides `.mc/` & friends (covered in tests/contracts/test_server_read_api.py), so here we only
// assert the panel renders what it is given and routes each kind to the right timeline action.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import * as api from '../api.js'
import StudioAssets from './StudioAssets.jsx'

vi.mock('../api.js', () => ({
  browseProject: vi.fn(),
  fileUrl: (id, path) => `mock:${path}`,
}))

// One entry list per folder path, so navigating actually changes what's listed.
const TREE = {
  '': [
    { name: 'assets', path: 'assets', is_dir: true, count: 2 },
    { name: 'hf', path: 'hf', is_dir: true, count: 1 },
  ],
  assets: [
    { name: 'images', path: 'assets/images', is_dir: true, count: 0 },
    { name: 'music', path: 'assets/music', is_dir: true, count: 1 },
    { name: 'subtitles.srt', path: 'assets/subtitles.srt', is_dir: false, kind: 'text', mtime: 4 },
  ],
  'assets/images': [],
  'assets/music': [
    { name: 'bed.mp3', path: 'assets/music/bed.mp3', is_dir: false, kind: 'music', mtime: 3 },
  ],
  hf: [{ name: 'renders', path: 'hf/renders', is_dir: true, count: 2 }],
  'hf/renders': [
    { name: 'anim_intro.mp4', path: 'hf/renders/anim_intro.mp4', is_dir: false, kind: 'video', mtime: 1 },
    { name: 'ov_caption.mov', path: 'hf/renders/ov_caption.mov', is_dir: false, kind: 'video', mtime: 2 },
  ],
}

function setup(overrides = {}) {
  api.browseProject.mockImplementation((_id, path) =>
    Promise.resolve({ path, entries: TREE[path] || [] }))
  const props = {
    projectId: 'p1',
    assets: { kinds: { images: [], video: [], audio: [], music: [] } },
    onAddImage: vi.fn(), onAddClip: vi.fn(), onAddSfx: vi.fn(), onSetMusic: vi.fn(),
    onUploadAsset: vi.fn(),
    ...overrides,
  }
  return { ...render(<StudioAssets {...props} />), props }
}

// Walk the folder rows down a path, e.g. goTo(h, ['hf', 'hf/renders']).
async function goTo(h, paths) {
  for (const p of paths) fireEvent.click(await h.findByTitle(`Open ${p}`))
  await waitFor(() => expect(api.browseProject).toHaveBeenLastCalledWith('p1', paths[paths.length - 1]))
}

beforeEach(() => {
  vi.clearAllMocks()
  // The dialog reads text files over the network to show them.
  global.fetch = vi.fn(() => Promise.resolve({ text: () => Promise.resolve('1\n00:00 --> 00:01\nhi\n') }))
})

describe('folder navigation', () => {
  it('lists the project root as folder rows with their counts', async () => {
    const h = setup()
    expect(await h.findByTitle('Open assets')).toHaveTextContent('2')
    expect(h.getByTitle('Open hf')).toHaveTextContent('1')
  })

  it('clicking a folder browses into it, and the breadcrumb walks back out', async () => {
    const h = setup()
    await goTo(h, ['hf', 'hf/renders'])
    await waitFor(() => expect(h.container.querySelectorAll('.asset-grid .asset-item')).toHaveLength(2))
    // Breadcrumb: Project / hf / renders — clicking a crumb jumps straight to it.
    fireEvent.click(h.getByRole('button', { name: 'hf' }))
    await waitFor(() => expect(api.browseProject).toHaveBeenLastCalledWith('p1', 'hf'))
    fireEvent.click(h.getByRole('button', { name: 'Project' }))
    await waitFor(() => expect(api.browseProject).toHaveBeenLastCalledWith('p1', ''))
  })

  it('shows an empty state for a folder with nothing to show', async () => {
    const h = setup()
    await goTo(h, ['assets', 'assets/images'])
    expect(await h.findByText('This folder is empty.')).toBeInTheDocument()
  })
})

describe('click opens the file, the dialog adds it', () => {
  it('renders video files as video thumbnails, not <img>', async () => {
    const h = setup()
    await goTo(h, ['hf', 'hf/renders'])
    await waitFor(() => expect(h.container.querySelector('.asset-grid .asset-thumb.video video')).toBeTruthy())
    expect(h.container.querySelector('.asset-grid img')).toBeNull()
  })

  it('clicking a file OPENS the dialog on it and adds nothing yet', async () => {
    const h = setup()
    await goTo(h, ['hf', 'hf/renders'])
    await waitFor(() => expect(h.container.querySelector('.asset-grid .asset-item')).toBeTruthy())
    fireEvent.click(h.container.querySelector('.asset-grid .asset-item'))
    expect(h.getByTitle('Download')).toBeInTheDocument()          // the lightbox is up
    expect(h.container.querySelector('.al-name')).toHaveTextContent('anim_intro.mp4')
    expect(h.props.onAddClip).not.toHaveBeenCalled()              // click alone never edits
  })

  it('the dialog button appends a video (incl. an agent render) as a clip, then closes', async () => {
    const h = setup()
    await goTo(h, ['hf', 'hf/renders'])
    await waitFor(() => expect(h.container.querySelector('.asset-grid .asset-item')).toBeTruthy())
    fireEvent.click(h.container.querySelector('.asset-grid .asset-item'))
    fireEvent.click(h.getByRole('button', { name: /append as clip/i }))
    expect(h.props.onAddClip).toHaveBeenCalledWith('hf/renders/anim_intro.mp4')
    expect(h.props.onAddImage).not.toHaveBeenCalled()
    expect(h.container.querySelector('.asset-lightbox')).toBeNull()
  })

  it('the dialog button sets a music bed for a music file', async () => {
    const h = setup()
    await goTo(h, ['assets', 'assets/music'])
    await waitFor(() => expect(h.container.querySelector('.asset-grid .asset-item')).toBeTruthy())
    fireEvent.click(h.container.querySelector('.asset-grid .asset-item'))
    fireEvent.click(h.getByRole('button', { name: /set as music bed/i }))
    expect(h.props.onSetMusic).toHaveBeenCalledWith('assets/music/bed.mp3')
  })

  it('a text file opens read-only — no add button (nothing to put on a timeline)', async () => {
    const h = setup()
    await goTo(h, ['assets'])
    await waitFor(() => expect(h.container.querySelector('.asset-grid .asset-item')).toBeTruthy())
    fireEvent.click(h.container.querySelector('.asset-grid .asset-item'))
    expect(h.container.querySelector('.al-text')).toBeTruthy()
    expect(h.container.querySelector('.al-add')).toBeNull()
  })

  it('the drag payload carries the file kind + path for the timeline drop handler', async () => {
    const h = setup()
    await goTo(h, ['hf', 'hf/renders'])
    await waitFor(() => expect(h.container.querySelector('.asset-grid .asset-item')).toBeTruthy())
    const setData = vi.fn()
    fireEvent.dragStart(h.container.querySelector('.asset-grid .asset-item'), { dataTransfer: { setData } })
    expect(setData).toHaveBeenCalledWith(
      'application/x-opennolan-asset',
      JSON.stringify({ kind: 'video', path: 'hf/renders/anim_intro.mp4' }),
    )
  })
})

describe('upload destination', () => {
  const upload = (h, file) =>
    fireEvent.change(h.container.querySelector('.dropzone input'), { target: { files: [file] } })

  it('falls back to the file media type outside the four asset folders', async () => {
    const h = setup()
    await h.findByTitle('Open assets')
    const file = new File(['x'], 'shot.png', { type: 'image/png' })
    upload(h, file)
    expect(h.props.onUploadAsset).toHaveBeenCalledWith('images', file)
  })

  it('inside assets/music the folder wins over the media type (an mp3 is music, not sfx)', async () => {
    const h = setup()
    await goTo(h, ['assets', 'assets/music'])
    const file = new File(['x'], 'bed2.mp3', { type: 'audio/mpeg' })
    upload(h, file)
    expect(h.props.onUploadAsset).toHaveBeenCalledWith('music', file)
  })
})
