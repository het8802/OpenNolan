// Smoke test for the editor CONTAINER. The other suites test leaf components in isolation,
// so a mount-time crash in Studio itself (a hook-order slip, or a useCallback whose deps read
// a later const in its temporal dead zone) sails past the build AND the unit tests. This
// renders the whole editor against a mocked API and asserts it mounts + loads.

import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import * as api from '../api.js'
import Studio from './Studio.jsx'

// A minimal chat bundle (see useAgentChat) so we can mount the agent panel in isolation.
function mockChat(overrides = {}) {
  return {
    messages: [], input: '', setInput: vi.fn(), busy: false,
    pendingConfirm: null, pendingQuestion: null, renderingStage: null, toolResults: {},
    threads: [], activeThread: null,
    send: vi.fn(), stop: vi.fn(), newChat: vi.fn(), loadThread: vi.fn(),
    resolveConfirm: vi.fn(), answerQuestion: vi.fn(),
    ...overrides,
  }
}

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

// Panel layout persists to localStorage; clear it so each test starts from the defaults.
beforeEach(() => { try { localStorage.clear() } catch { /* ignore */ } })

describe('Studio container mounts', () => {
  it('renders the editor without crashing and loads the timeline', async () => {
    render(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} />)
    // Reaching the loaded state proves there was no TDZ/hook crash during mount.
    expect(await screen.findByText('demo-project')).toBeInTheDocument()
  })

  it('renders without an agent panel when no chat bundle is passed', async () => {
    render(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} />)
    await screen.findByText('demo-project')
    // The editor mounts fine; the agent header only appears once a chat bundle is provided.
    expect(screen.queryByRole('heading', { name: 'Agent' })).not.toBeInTheDocument()
  })
})

describe('editor agent panel', () => {
  it('mounts the agent panel when a chat bundle is provided (open by default)', async () => {
    render(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} chat={mockChat()} />)
    await screen.findByText('demo-project')
    expect(screen.getByRole('heading', { name: 'Agent' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Message the agent/)).toBeInTheDocument()
  })

  it('shows a re-open tab (not the panel) when collapsed, and reopening restores it', async () => {
    localStorage.setItem('st.panels.v1', JSON.stringify({ agentOpen: false }))
    render(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} chat={mockChat()} />)
    await screen.findByText('demo-project')
    // Collapsed: the panel is gone, the re-open tab is present.
    expect(screen.queryByRole('heading', { name: 'Agent' })).not.toBeInTheDocument()
    const reopen = screen.getByTitle('Show agent panel')
    fireEvent.click(reopen)
    expect(screen.getByRole('heading', { name: 'Agent' })).toBeInTheDocument()
  })
})

// The in-editor agent shares the project's conversation, so it can rewrite edit_decisions.json
// while the editor holds an open-time snapshot. Guard against silently clobbering the agent's work.
describe('editor / agent-edit safety', () => {
  it('refuses to Save while the agent is mid-turn (busy)', async () => {
    api.saveEditDecisions.mockClear()
    render(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} chat={mockChat({ busy: true })} />)
    await screen.findByText('demo-project')
    fireEvent.keyDown(window, { key: 's', metaKey: true }) // Cmd+S
    expect(await screen.findByText(/Agent is editing/)).toBeInTheDocument()
    expect(api.saveEditDecisions).not.toHaveBeenCalled()
  })

  it('re-fetches edit_decisions when an agent turn completes (busy true→false)', async () => {
    const { rerender } = render(
      <Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} chat={mockChat({ busy: true })} />,
    )
    await screen.findByText('demo-project')
    api.getEditDecisions.mockClear() // ignore the initial open-time load
    rerender(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} chat={mockChat({ busy: false })} />)
    await waitFor(() => expect(api.getEditDecisions).toHaveBeenCalledTimes(1))
  })
})
