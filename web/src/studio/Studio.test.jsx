// Smoke test for the editor CONTAINER. The other suites test leaf components in isolation,
// so a mount-time crash in Studio itself (a hook-order slip, or a useCallback whose deps read
// a later const in its temporal dead zone) sails past the build AND the unit tests. This
// renders the whole editor against a mocked API and asserts it mounts + loads.

import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { useState } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import * as api from '../api.js'
import Studio from './Studio.jsx'
import { flush } from '../analytics/track.js'

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
  listAssets: vi.fn(() => Promise.resolve({ kinds: { images: [], video: [], audio: [], music: [] }, renders: [], agent_renders: [] })),
  browseProject: vi.fn(() => Promise.resolve({ path: '', entries: [] })),
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

  it('autosaves edits to disk after a short debounce (no manual Save needed)', async () => {
    api.saveEditDecisions.mockClear()
    render(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} />)
    await screen.findByText('demo-project')
    fireEvent.click(screen.getByTitle('Add a text overlay')) // a local edit → dirty
    expect(api.saveEditDecisions).not.toHaveBeenCalled()       // not immediately (debounced)
    await waitFor(() => expect(api.saveEditDecisions).toHaveBeenCalled(), { timeout: 2000 })
  })

  it('adopts the agent timeline LIVE on turn-end (no "reopen" warning) even with local edits', async () => {
    api.getEditDecisions.mockResolvedValue({ content: {
      version: '1.0', render_runtime: 'ffmpeg',
      cuts: [{ id: 'c1', source: 'clips/a.mp4', in_seconds: 0, out_seconds: 4 }],
    } })
    const { rerender } = render(
      <Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} chat={mockChat({ busy: true })} />,
    )
    await screen.findByText('demo-project')
    fireEvent.click(screen.getByTitle('Add a text overlay')) // local mid-turn edit (autosave suspended while busy)
    // agent finishes its turn with a CHANGED disk doc → the next fetch returns the agent's version
    api.getEditDecisions.mockResolvedValueOnce({ content: {
      version: '1.0', render_runtime: 'ffmpeg',
      cuts: [{ id: 'AGENT_CUT', source: 'clips/b.mp4', in_seconds: 0, out_seconds: 2 }],
    } })
    rerender(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} chat={mockChat({ busy: false })} />)
    expect(await screen.findByText(/updated by the agent/)).toBeInTheDocument()
    expect(screen.queryByText(/Reopen the editor/i)).toBeNull() // the old bad-UX warning is gone
  })
})

// OPN-27: the editor wraps `chat.send` to flush a pending autosave first. That wrapper used to
// take ONE parameter, which would have dropped the @-mention sidecar in the editor while it kept
// working on the dashboard — same component, one surface quietly broken. Pin the forwarding.
describe('editor / agent @-mention sidecar', () => {
  it('forwards the mention sidecar through the pre-agent autosave flush', async () => {
    api.listAssets.mockResolvedValue({
      kinds: { images: [], video: [{ path: 'assets/video/hook.mp4', name: 'hook.mp4' }], audio: [], music: [] },
      renders: [], agent_renders: [],
    })
    // ChatPanel is controlled by the bundle, so the harness has to own `input` the way
    // useAgentChat does — a bare spy would never re-render and the menu would never open.
    const send = vi.fn()
    function Harness() {
      const [input, setInput] = useState('')
      return (
        <Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}}
          chat={mockChat({ projectId: 'p1', input, setInput, send })} />
      )
    }
    render(<Harness />)
    await screen.findByText('demo-project')

    const ta = screen.getByPlaceholderText(/Message the agent/)
    fireEvent.change(ta, { target: { value: 'use @hoo', selectionStart: 8 } })
    await screen.findByRole('listbox')
    fireEvent.keyDown(ta, { key: 'Enter' })                 // insert the mention
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())
    fireEvent.keyDown(ta, { key: 'Enter' })                 // send

    await waitFor(() => expect(send).toHaveBeenCalledTimes(1))
    expect(send.mock.calls[0][1]).toEqual([
      { token: '@assets/video/hook.mp4', path: 'assets/video/hook.mp4' },
    ])
  })
})

// Analytics P0-6: every discrete edit must carry a feature_id from the CLOSED enum, and no
// upload may happen per interaction — the summary is one event, flushed on pagehide. Both
// halves matter: an untagged commit is invisible in the feature ledger, and a per-commit
// upload breaches the session event ceiling on its own (a 20-minute session is 50-300 commits).
describe('editor telemetry', () => {
  // Capture every telemetry batch POST for the duration of one test.
  function withPostedBatches(fn) {
    const posted = []
    const realFetch = global.fetch
    global.fetch = vi.fn((url, init) => {
      if (String(url).includes('/api/telemetry/events')) posted.push(JSON.parse(init.body))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    // track.js's queue is module state shared by every test in this file, and a flush that
    // fails RE-QUEUES its batch (deliberately — a transient network blip must not drop
    // events). Earlier tests mount and unmount Studio with no fetch mock in place, so their
    // summaries are still sitting in that queue. Drain it before observing anything, or this
    // test reads the previous test's numbers.
    flush()
    posted.length = 0
    return Promise.resolve(fn(posted)).finally(() => { global.fetch = realFetch })
  }

  const summaryIn = (posted) =>
    posted.flatMap((b) => b.events).find((e) => e.event === 'editor_session_summary')

  // THE regression this suite exists for. Live QA did a real speed edit, closed the editor
  // normally, and saw ZERO telemetry requests — because closing the editor sets editing=false
  // in App.jsx, which UNMOUNTS Studio while the document lives on. `pagehide` never fires on
  // that path, so the one event carrying every feature_id never left the app.
  it('uploads the summary when the EDITOR closes, not only when the document unloads', async () =>
    withPostedBatches(async (posted) => {
      const { unmount } = render(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} />)
      await screen.findByText('demo-project')

      fireEvent.click(screen.getByTitle('Duplicate clip'))
      expect(posted).toEqual([]) // ← still no per-interaction upload

      unmount() // = the user clicking Close, or switching project
      await waitFor(() => expect(summaryIn(posted)).toBeTruthy())
      expect(summaryIn(posted).properties.features['editor.duplicate'].commits).toBe(1)
    }))

  it('tags each timeline action with its feature_id and uploads exactly one summary', async () =>
    withPostedBatches(async (posted) => {
      render(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} />)
      await screen.findByText('demo-project')

      fireEvent.click(screen.getByTitle('Duplicate clip'))
      fireEvent.click(screen.getByTitle(/Delete selection/))
      expect(posted).toEqual([])       // ← no per-interaction upload

      window.dispatchEvent(new Event('pagehide'))
      await waitFor(() => expect(posted.length).toBe(1))

      const summary = summaryIn(posted)
      expect(summary).toBeTruthy()
      expect(summary.properties.features['editor.duplicate'].commits).toBe(1)
      expect(summary.properties.features['editor.delete'].commits).toBe(1)
      expect(summary.properties.action_digest).toEqual(['editor.duplicate', 'editor.delete'])
      expect(summary.properties.commits).toBe(2)
    }))

  it('sends EXACTLY ONE summary when a teardown is followed by an unmount', async () =>
    withPostedBatches(async (posted) => {
      const { unmount } = render(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} />)
      await screen.findByText('demo-project')
      fireEvent.click(screen.getByTitle('Duplicate clip'))

      window.dispatchEvent(new Event('pagehide'))
      unmount()
      await waitFor(() => expect(posted.length).toBeGreaterThan(0))
      const summaries = posted.flatMap((b) => b.events).filter((e) => e.event === 'editor_session_summary')
      expect(summaries).toHaveLength(1)
    }))

  it('still reports an OPENED but untouched editor — that is the zero-use denominator', async () =>
    withPostedBatches(async (posted) => {
      // Suppressing this would leave only sessions where somebody did something, which
      // inflates every per-feature adoption rate exactly where the numbers are smallest.
      const { unmount } = render(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} />)
      await screen.findByText('demo-project')
      unmount()
      await waitFor(() => expect(summaryIn(posted)).toBeTruthy())
      expect(summaryIn(posted).properties.commits).toBe(0)
      expect(summaryIn(posted).properties.features_used).toEqual([])
    }))
})

// F2 — asset_added_to_doc.asset_ids was ALWAYS []. `assetIds.current` (the path -> asset_id map
// recordAdd() looks up) was declared and read but assigned nowhere, and GET /assets did not
// return an asset_id to build it from. Both halves are fixed; this asserts the join key actually
// arrives, because an empty array is indistinguishable from "the user added nothing".
describe('asset_added_to_doc carries the join key', () => {
  const withPostedBatches = async (fn) => {
    const posted = []
    const realFetch = global.fetch
    global.fetch = vi.fn((url, init) => {
      if (String(url).includes('/api/telemetry/events')) posted.push(JSON.parse(init.body))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    flush()
    posted.length = 0
    return Promise.resolve(fn(posted)).finally(() => { global.fetch = realFetch })
  }

  it('reports the asset_id the backend persisted at ingest, not an empty array', async () => {
    // listAssets supplies the map; browseProject supplies the clickable panel entry. They agree
    // on `path` — which is exactly what broke server-side: the manifest was keyed by a
    // PROJECTS-DIR-relative path while every reader uses the project-relative one.
    vi.mocked(api.listAssets).mockResolvedValue({
      kinds: { images: [], video: [{ path: 'assets/video/a.mp4', name: 'a.mp4', asset_id: 'aid-123' }], audio: [], music: [] },
      renders: [], agent_renders: [],
    })
    vi.mocked(api.browseProject).mockResolvedValue({
      path: '', entries: [{ name: 'a.mp4', path: 'assets/video/a.mp4', is_dir: false, kind: 'video', mtime: 1 }],
    })
    // No cuts, so Studio auto-selects nothing and the inspector falls to its Assets tab — the
    // only surface that can call recordAdd(). (With a cut present the first one is selected on
    // load and the panel shows properties instead, which is why a browser pass never reaches it.)
    vi.mocked(api.getEditDecisions).mockResolvedValue({
      content: { version: '1.0', render_runtime: 'ffmpeg', cuts: [] },
    })
    await withPostedBatches(async (posted) => {
      const { unmount } = render(<Studio projectId="p1" state={{ name: 'demo-project' }} onClose={() => {}} />)
      await screen.findByText('demo-project')

      fireEvent.click(await screen.findByTitle(/a\.mp4 — click to open/))
      fireEvent.click(await screen.findByText('+ Append as clip'))

      unmount()
      await waitFor(() => {
        const added = posted.flatMap((b) => b.events).find((e) => e.event === 'asset_added_to_doc')
        expect(added).toBeTruthy()
        expect(added.properties.asset_ids).toEqual(['aid-123'])
        expect(added.properties.by_method).toEqual({ asset_click: 1 })
      })
    })
  })
})
