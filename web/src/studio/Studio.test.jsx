// Smoke test for the editor CONTAINER. The other suites test leaf components in isolation,
// so a mount-time crash in Studio itself (a hook-order slip, or a useCallback whose deps read
// a later const in its temporal dead zone) sails past the build AND the unit tests. This
// renders the whole editor against a mocked API and asserts it mounts + loads.

import { describe, it, expect, vi, beforeAll } from 'vitest'
import { render, screen } from '@testing-library/react'
import Studio from './Studio.jsx'

vi.mock('../api.js', () => ({
  getEditDecisions: vi.fn(() => Promise.resolve({ content: {
    version: '1.0', render_runtime: 'ffmpeg',
    cuts: [{ id: 'c1', source: 'clips/a.mp4', in_seconds: 0, out_seconds: 4 }],
  } })),
  listAssets: vi.fn(() => Promise.resolve({ kinds: { images: [], video: [], audio: [], music: [] }, renders: [] })),
  getSourceMeta: vi.fn(() => Promise.resolve({ duration: 4, width: 1920, height: 1080 })),
  saveEditDecisions: vi.fn(() => Promise.resolve({})),
  startRender: vi.fn(() => Promise.resolve({ job_id: 'j1' })),
  getRenderStatus: vi.fn(() => Promise.resolve({ status: 'done' })),
  sourceUrl: () => 'mock:source',
  fileUrl: () => 'mock:file',
  frameUrl: () => 'mock:frame',
}))

// jsdom doesn't implement media playback; stub so the preview's pause()/play() calls are inert.
beforeAll(() => {
  window.HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve())
  window.HTMLMediaElement.prototype.pause = vi.fn()
})

describe('Studio container mounts', () => {
  it('renders the editor without crashing and loads the timeline', async () => {
    render(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} />)
    // Reaching the loaded state proves there was no TDZ/hook crash during mount.
    expect(await screen.findByText('demo-project')).toBeInTheDocument()
  })
})
