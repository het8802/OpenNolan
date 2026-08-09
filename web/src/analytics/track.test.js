// S5 — the session announcement.
//
// `desktop/main.js` is the ONLY emitter of `session_started`, so `npm run dev`, Playwright and
// QA invent a session id, label every event with it, and never register the session. Measured
// in the dev project: 6 session ids carrying product events with no `session_started` —
// including the one carrying the project's only fatal exception, which made the crash-free
// wall read 0 against a real fatal crash. Every reviewer who tested
// `$exception where fatal = true` in isolation saw 1; nobody ran the wall against its own
// denominator.
//
// NOTE ON SCOPE: every case here mocks `fetch`, so all of them can pass while the published
// Wall 5 query still divides one distinct fatal session by two `session_started` rows and
// reads 50%. A renderer unit test cannot reach the metric. That assertion belongs to the
// readback (scripts/analytics_query.py), which executes the DOCUMENTED `starts` CTE verbatim.

import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

const SESSION_KEY = 'on.session.v1'
const PENDING_KEY = 'on.session.pending.v1'

async function freshModule() {
  vi.resetModules()
  return import('./track.js')
}

function mockFetch(response) {
  const spy = vi.fn(() => Promise.resolve(response))
  globalThis.fetch = spy
  return spy
}

const ok = (body) => ({ ok: true, json: () => Promise.resolve(body) })

beforeEach(() => {
  sessionStorage.clear()
  delete globalThis.window.openNolan
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('announceSession', () => {
  it('sends nothing when the shell already minted the id — main.js announced it', async () => {
    globalThis.window.openNolan = { sessionId: 'from-shell' }
    const spy = mockFetch(ok({ accepted: 1 }))
    const { announceSession } = await freshModule()
    await announceSession()
    expect(spy).not.toHaveBeenCalled()
  })

  it('sends nothing for a sessionStorage hit with no pending marker', async () => {
    sessionStorage.setItem(SESSION_KEY, 'already-registered')
    const spy = mockFetch(ok({ accepted: 1 }))
    const { announceSession } = await freshModule()
    await announceSession()
    expect(spy).not.toHaveBeenCalled()
  })

  it('announces exactly once on a fresh mint, and clears the marker on acceptance', async () => {
    const spy = mockFetch(ok({ accepted: 1 }))
    const { announceSession } = await freshModule()
    await announceSession()
    expect(spy).toHaveBeenCalledTimes(1)
    const body = JSON.parse(spy.mock.calls[0][1].body)
    expect(body.events).toHaveLength(1)
    expect(body.events[0].event).toBe('session_started')
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull()
  })

  it('is its OWN request — never batched with product events', async () => {
    // A batch `accepted` count cannot acknowledge one event inside it: a batch carrying
    // session_started plus one other, where the START is rejected and the other accepted,
    // returns accepted=1 — and the marker would clear with no session registered.
    const spy = mockFetch(ok({ accepted: 1 }))
    const { announceSession, track } = await freshModule()
    track('project_opened', { entrypoint: 'dashboard' })
    await announceSession()
    const body = JSON.parse(spy.mock.calls[0][1].body)
    expect(body.events.map((e) => e.event)).toEqual(['session_started'])
  })

  it('RETAINS the marker on a 200 with accepted=0, and re-announces next load', async () => {
    // The endpoint is documented "always 200", and `received` used to count events SUBMITTED
    // to capture() — which may still drop them for opt-out, taxonomy rejection or a silent SDK
    // failure. Acknowledgement is backend ACCEPTANCE of this exact event, nothing weaker.
    mockFetch(ok({ received: 1, accepted: 0 }))
    const first = await freshModule()
    await first.announceSession()
    expect(sessionStorage.getItem(PENDING_KEY)).toBe('1')

    const spy = mockFetch(ok({ accepted: 1 }))
    const second = await freshModule()
    await second.announceSession()
    expect(spy).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem(PENDING_KEY)).toBeNull()
  })

  it('retains the marker when the request rejects outright', async () => {
    globalThis.fetch = vi.fn(() => Promise.reject(new Error('offline')))
    const { announceSession } = await freshModule()
    await announceSession()
    expect(sessionStorage.getItem(PENDING_KEY)).toBe('1')
  })

  it('re-announces after acceptance if the RESPONSE was lost — at-least-once, by design', async () => {
    // Exactly-once is not achievable over this transport. The metric is what tolerates it:
    // Wall 5 counts DISTINCT session_id, so a duplicate start cannot inflate the denominator.
    // That is asserted by the readback, not here — see the note at the top of this file.
    globalThis.fetch = vi.fn(() => Promise.reject(new Error('connection lost after accept')))
    const first = await freshModule()
    const id = first.sessionId
    await first.announceSession()

    globalThis.fetch = vi.fn(() => Promise.resolve(ok({ accepted: 1 })))
    const second = await freshModule()
    await second.announceSession()
    expect(second.sessionId).toBe(id)          // one session...
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)  // ...announced a second time
  })
})
