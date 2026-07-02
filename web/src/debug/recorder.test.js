import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import dbg, { describeTarget, serializeArg, start, stop, event, isRecording } from './recorder.js'

afterEach(() => {
  if (isRecording()) stop()
  try { localStorage.clear() } catch { /* noop */ }
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('describeTarget', () => {
  it('prefers the st-* region class and captures testid/aria/text', () => {
    const el = document.createElement('button')
    el.className = 'foo st-play bar'
    el.id = 'go'
    el.setAttribute('data-testid', 'play-btn')
    el.setAttribute('aria-label', 'Play')
    el.textContent = '  Play   now  '
    const d = describeTarget(el)
    expect(d.sel).toBe('button#go.st-play')
    expect(d.testid).toBe('play-btn')
    expect(d.aria).toBe('Play')
    expect(d.text).toBe('Play now')
  })

  it('falls back to the first class when there is no st-* class', () => {
    const el = document.createElement('div')
    el.className = 'ruler wide'
    expect(describeTarget(el).sel).toBe('div.ruler')
  })

  it('returns null for non-elements', () => {
    expect(describeTarget(null)).toBeNull()
    expect(describeTarget(document.createTextNode('x'))).toBeNull()
  })
})

describe('serializeArg', () => {
  it('passes through primitives and truncates long strings', () => {
    expect(serializeArg(3)).toBe(3)
    expect(serializeArg(true)).toBe(true)
    expect(serializeArg('hi')).toBe('hi')
    expect(serializeArg(undefined)).toBe('(undefined)')
    expect(serializeArg(null)).toBeNull()
    expect(serializeArg('x'.repeat(3000)).endsWith('…')).toBe(true)
  })

  it('handles circular references without throwing', () => {
    const a = { name: 'a' }
    a.self = a
    const out = serializeArg(a)
    expect(out.name).toBe('a')
    expect(out.self).toBe('[circular]')
  })

  it('serializes Errors and DOM nodes compactly', () => {
    const err = serializeArg(new Error('boom'))
    expect(err.error).toBe('Error')
    expect(err.message).toBe('boom')
    const el = document.createElement('div'); el.className = 'st-stage'
    expect(serializeArg(el).sel).toBe('div.st-stage')
  })
})

describe('recording lifecycle', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) })))
  })

  it('toggles state and returns a session id', () => {
    expect(isRecording()).toBe(false)
    const id = start({ projectId: 'p1' })
    expect(isRecording()).toBe(true)
    expect(id).toMatch(/^\d{4}-/) // ISO-ish wall stamp prefix
    stop()
    expect(isRecording()).toBe(false)
  })

  it('is a no-op event() when not recording', () => {
    event('ui.seek', { t: 1 })
    // starting fresh, the first flush after start should only carry the session.start marker
    start()
    event('preview.seekReq', { to: 2 })
    stop()
    const body = JSON.parse(fetch.mock.calls.at(-1)[1].body)
    const types = body.events.map((e) => e.type)
    expect(types).toContain('session.start')
    expect(types).toContain('preview.seekReq')
    expect(types).not.toContain('ui.seek') // emitted before start → dropped
  })

  it('flushes buffered events to /api/debug/log on stop', () => {
    start({ projectId: 'p1' })
    event('ui.seek', { t: 3.14 })
    stop()
    expect(fetch).toHaveBeenCalled()
    const [url, opts] = fetch.mock.calls.at(-1)
    expect(url).toBe('/api/debug/log')
    expect(opts.method).toBe('POST')
    const body = JSON.parse(opts.body)
    expect(body.session).toBeTruthy()
    const seek = body.events.find((e) => e.type === 'ui.seek')
    expect(seek.data.t).toBe(3.14)
    expect(typeof seek.seq).toBe('number')
    expect(typeof seek.wall).toBe('string')
  })

  it('throttles high-frequency per-frame events but not low-volume lifecycle events', () => {
    start()
    // Two ui.seek within the same tick (<66ms): only the leading one survives.
    event('ui.seek', { t: 1 })
    event('ui.seek', { t: 2 })
    // Video lifecycle events are NOT throttled — both must be kept.
    event('preview.video.seeking', { cur: 1 })
    event('preview.video.seeking', { cur: 2 })
    stop()
    const body = JSON.parse(fetch.mock.calls.at(-1)[1].body)
    const seeks = body.events.filter((e) => e.type === 'ui.seek')
    const seekings = body.events.filter((e) => e.type === 'preview.video.seeking')
    expect(seeks).toHaveLength(1)
    expect(seeks[0].data.t).toBe(1) // the leading event
    expect(seekings).toHaveLength(2)
  })

  it('persists the on/session flag so a reload can resume', () => {
    const id = start()
    const saved = JSON.parse(localStorage.getItem('st.debug.rec.v1'))
    expect(saved.on).toBe(true)
    expect(saved.session).toBe(id)
    stop()
    expect(JSON.parse(localStorage.getItem('st.debug.rec.v1')).on).toBe(false)
  })

  it('resumeIfActive re-arms the same session after a reload', () => {
    const id = start()
    stop() // leaves saved.on=false — simulate a crash mid-session instead:
    localStorage.setItem('st.debug.rec.v1', JSON.stringify({ on: true, session: id }))
    dbg.resumeIfActive({ projectId: 'p1' })
    expect(isRecording()).toBe(true)
    expect(dbg.currentSession()).toBe(id)
  })
})
