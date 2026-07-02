// Dev observability — an in-app SESSION RECORDER for the editor.
//
// Why this exists: the studio renderer runs inside Electron, which the coding agent
// cannot attach to. When something intermittent goes wrong (e.g. the canvas freezing
// on a scrub), there is nothing to inspect after the fact — the console is ephemeral
// and, until now, empty. This recorder turns ONE toggle into a durable, ordered trace:
// while it's ON it captures, with timestamps and a monotonic sequence number,
//   • every console.* call,
//   • uncaught errors + unhandled promise rejections,
//   • user interactions (pointerdown / click / keydown / drag-moves), and
//   • domain events the app emits via `dbg.event(type, data)` (scrub, seek, video state…),
// and batch-flushes them to the backend, which appends NDJSON to
//   .agents/tools/logs/ui-sessions/<session>.ndjson
// — a file the agent can read to see (and reproduce) exactly what happened.
//
// Design notes:
//   • Safe when OFF — no console patching, no listeners, `event()` is a cheap no-op.
//   • Never throws into the app — every capture path is wrapped so a serialization bug
//     can't break the UI it's observing.
//   • Survives a reload — the on/off flag + session id live in localStorage, so an
//     accidental ⌘R (or an error-triggered reload) keeps appending to the same file.
//   • Pure helpers (`describeTarget`, `serializeArg`) are exported for unit tests.

import { useSyncExternalStore } from 'react'

const FLUSH_MS = 1000 // batch cadence: cheap, and fine-grained enough to correlate events
const MAX_BUFFER = 8000 // hard cap so a runaway session can't grow memory unbounded
const MOVE_THROTTLE_MS = 40 // ~25 drag-move samples/sec — enough to correlate with video stalls
const LS_KEY = 'st.debug.rec.v1' // { on, session }
const ENDPOINT = '/api/debug/log'

// Per-frame events fire once per scrub tick and dominate file size (~90% of lines). Throttle
// them to ~15 Hz so a session stays small; the diagnostic signal (video seek COMPLETION RATE)
// is a ratio derived from the low-volume `preview.video.*` lifecycle events, which are NOT
// throttled — so thinning the requests loses nothing that matters. See analyze_session.
const THROTTLE_MS = { 'ui.seek': 66, 'preview.seekReq': 66 }
const MAX_SESSION_EVENTS = 200000 // safety net (~tens of MB) so a forgotten session can't grow unbounded

const state = {
  on: false,
  session: null, // id string, stable across reloads within a session
  load: '', // short id for THIS page load (seq resets per load; disambiguates in the file)
  seq: 0,
  total: 0, // total events pushed this session (across loads) — for the size cap
  capped: false, // true once MAX_SESSION_EVENTS hit; further events are dropped
  buffer: [],
  timer: null,
  startPerf: 0,
  patched: false,
  origConsole: null,
  lastMove: 0,
  throttle: {}, // type -> last-emit perfNow, for THROTTLE_MS
}

const listeners = new Set() // UI subscribers (toggle indicator)
const notify = () => { for (const cb of listeners) { try { cb() } catch { /* noop */ } } }

const perfNow = () => (typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now())
const wallNow = () => new Date().toISOString()
const rid = (n = 4) => Math.random().toString(36).slice(2, 2 + n)

// ── pure helpers (tested) ────────────────────────────────────────────────────

// A compact, human-readable descriptor of a DOM node for an interaction event. Prefers
// the studio region class (`st-*`) since that names the timeline/inspector/etc.
export function describeTarget(el) {
  if (!el || el.nodeType !== 1) return null
  const tag = (el.tagName || '?').toLowerCase()
  const id = el.id ? `#${el.id}` : ''
  let cls = ''
  if (el.classList && el.classList.length) {
    const st = Array.from(el.classList).find((c) => c.startsWith('st-'))
    cls = st ? `.${st}` : `.${el.classList[0]}`
  }
  const attr = (name) => (el.getAttribute ? el.getAttribute(name) : null) || undefined
  const text = ((el.textContent || '').trim().replace(/\s+/g, ' ')).slice(0, 40) || undefined
  return { sel: `${tag}${id}${cls}`, testid: attr('data-testid'), aria: attr('aria-label'), role: attr('role'), text }
}

// Bounded, circular-safe JSON-ish serialization for console args / event payloads.
export function serializeArg(v, depth = 0, seen = new WeakSet()) {
  if (v === undefined) return '(undefined)'
  if (v === null) return null
  const t = typeof v
  if (t === 'string') return v.length > 2000 ? v.slice(0, 2000) + '…' : v
  if (t === 'number' || t === 'boolean') return v
  if (t === 'bigint') return `${v}n`
  if (t === 'symbol') return String(v)
  if (t === 'function') return `[fn ${v.name || 'anonymous'}]`
  if (v instanceof Error) return { error: v.name, message: v.message, stack: (v.stack || '').split('\n').slice(0, 6).join('\n') }
  if (typeof Element !== 'undefined' && v instanceof Element) return describeTarget(v)
  if (depth >= 4) return '[…]'
  if (typeof v === 'object') {
    if (seen.has(v)) return '[circular]'
    seen.add(v)
    try {
      if (Array.isArray(v)) return v.slice(0, 100).map((x) => serializeArg(x, depth + 1, seen))
      const out = {}
      let n = 0
      for (const k of Object.keys(v)) {
        if (n++ >= 60) { out['…'] = 'truncated'; break }
        out[k] = serializeArg(v[k], depth + 1, seen)
      }
      return out
    } catch { return String(v) }
  }
  return String(v)
}

// ── buffer / recording core ──────────────────────────────────────────────────

function push(type, rec) {
  if (!state.on) return
  try {
    const gap = THROTTLE_MS[type]
    if (gap) {
      const ts = perfNow()
      if (ts - (state.throttle[type] || 0) < gap) return // thin high-frequency per-frame events
      state.throttle[type] = ts
    }
    if (state.total >= MAX_SESSION_EVENTS) {
      if (!state.capped) { // one marker, then silently drop — never grow a forgotten session unbounded
        state.capped = true
        state.buffer.push({ seq: state.seq++, load: state.load, t: 0, wall: wallNow(), type: 'session.capped', data: MAX_SESSION_EVENTS })
      }
      return
    }
    state.total++
    if (state.buffer.length >= MAX_BUFFER) state.buffer.shift() // keep the most recent
    state.buffer.push({ seq: state.seq++, load: state.load, t: Math.round((perfNow() - state.startPerf) * 10) / 10, wall: wallNow(), type, ...rec })
  } catch { /* never let recording break the app */ }
}

// Public domain-event API — call this from anywhere in the app.
export function event(type, data) { push(type, data !== undefined ? { data: serializeArg(data) } : {}) }
export function mark(label) { push('mark', { data: label }) }

const CONSOLE_LEVELS = ['log', 'info', 'warn', 'error', 'debug']
function patchConsole() {
  if (state.patched || typeof console === 'undefined') return
  state.origConsole = {}
  for (const lvl of CONSOLE_LEVELS) {
    state.origConsole[lvl] = console[lvl]
    console[lvl] = (...args) => {
      push('console', { level: lvl, args: args.map((a) => serializeArg(a)) })
      try { state.origConsole[lvl].apply(console, args) } catch { /* noop */ }
    }
  }
  state.patched = true
}
function unpatchConsole() {
  if (!state.patched) return
  for (const lvl of CONSOLE_LEVELS) if (state.origConsole[lvl]) console[lvl] = state.origConsole[lvl]
  state.origConsole = null
  state.patched = false
}

function onError(e) {
  push('error', { message: e.message, source: e.filename, line: e.lineno, col: e.colno, stack: e.error?.stack ? e.error.stack.split('\n').slice(0, 8).join('\n') : undefined })
}
function onRejection(e) { push('unhandledrejection', { reason: serializeArg(e.reason) }) }
function onPointerDown(e) { push('pointerdown', { target: describeTarget(e.target), x: Math.round(e.clientX), y: Math.round(e.clientY), button: e.button }) }
function onClick(e) { push('click', { target: describeTarget(e.target), x: Math.round(e.clientX), y: Math.round(e.clientY) }) }
function onPointerMove(e) {
  if (!e.buttons) return // only while dragging (a scrub) — avoids drowning the log in idle moves
  const ts = perfNow()
  if (ts - state.lastMove < MOVE_THROTTLE_MS) return
  state.lastMove = ts
  push('pointermove', { x: Math.round(e.clientX), y: Math.round(e.clientY), buttons: e.buttons })
}
function onKeyDown(e) {
  const el = e.target
  const inField = !!(el && /^(input|textarea|select)$/i.test(el.tagName || '')) || !!el?.isContentEditable
  push('keydown', { key: e.key, meta: e.metaKey, ctrl: e.ctrlKey, shift: e.shiftKey, alt: e.altKey, inField })
}

function addGlobals() {
  if (typeof window === 'undefined') return
  window.addEventListener('error', onError)
  window.addEventListener('unhandledrejection', onRejection)
  window.addEventListener('pointerdown', onPointerDown, true)
  window.addEventListener('click', onClick, true)
  window.addEventListener('pointermove', onPointerMove, true)
  window.addEventListener('keydown', onKeyDown, true)
  window.addEventListener('pagehide', onPageHide)
}
function removeGlobals() {
  if (typeof window === 'undefined') return
  window.removeEventListener('error', onError)
  window.removeEventListener('unhandledrejection', onRejection)
  window.removeEventListener('pointerdown', onPointerDown, true)
  window.removeEventListener('click', onClick, true)
  window.removeEventListener('pointermove', onPointerMove, true)
  window.removeEventListener('keydown', onKeyDown, true)
  window.removeEventListener('pagehide', onPageHide)
}
function onPageHide() { flush(true) }

async function flush(useBeacon = false) {
  if (!state.session || state.buffer.length === 0) return
  const batch = state.buffer
  state.buffer = []
  const body = JSON.stringify({ session: state.session, events: batch })
  try {
    if (useBeacon && typeof navigator !== 'undefined' && navigator.sendBeacon) {
      navigator.sendBeacon(ENDPOINT, new Blob([body], { type: 'application/json' }))
    } else {
      await fetch(ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: useBeacon })
    }
  } catch {
    // Best-effort: return the batch to the front of the buffer so a transient failure
    // doesn't drop events (bounded by MAX_BUFFER on the next push).
    state.buffer = batch.concat(state.buffer)
  }
}

function persist() {
  try { localStorage.setItem(LS_KEY, JSON.stringify({ on: state.on, session: state.session })) } catch { /* noop */ }
}

function begin(session, resumed, meta) {
  state.on = true
  state.session = session
  state.load = rid()
  state.seq = 0
  state.total = 0
  state.capped = false
  state.throttle = {}
  state.buffer = []
  state.startPerf = perfNow()
  patchConsole()
  addGlobals()
  push(resumed ? 'session.resume' : 'session.start', {
    data: { ...meta, ua: typeof navigator !== 'undefined' ? navigator.userAgent : '', url: typeof location !== 'undefined' ? location.href : '', viewport: typeof window !== 'undefined' ? { w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio } : undefined },
  })
  if (state.timer) clearInterval(state.timer)
  state.timer = setInterval(() => flush(false), FLUSH_MS)
  persist()
  notify()
}

// ── public control API ────────────────────────────────────────────────────────

export function start(meta = {}) {
  if (state.on) return state.session
  const session = wallNow().replace(/[:.]/g, '-') + '-' + rid()
  begin(session, false, meta)
  return session
}

export function stop() {
  if (!state.on) return null
  const session = state.session
  push('session.stop', {})
  if (state.timer) { clearInterval(state.timer); state.timer = null }
  removeGlobals()
  unpatchConsole()
  flush(false)
  state.on = false
  persist()
  notify()
  return session
}

export function toggle(meta = {}) { return state.on ? stop() : start(meta) }
export function isRecording() { return state.on }
export function currentSession() { return state.session }
export function subscribe(cb) { listeners.add(cb); return () => listeners.delete(cb) }

// Re-arm after a page reload if a session was left running. Call once at app start.
export function resumeIfActive(meta = {}) {
  if (state.on) return
  let saved = null
  try { saved = JSON.parse(localStorage.getItem(LS_KEY) || 'null') } catch { /* noop */ }
  if (saved?.on && saved.session) begin(saved.session, true, meta)
}

// React binding for the toolbar toggle indicator.
export function useRecording() { return useSyncExternalStore(subscribe, isRecording, isRecording) }

const dbg = { start, stop, toggle, event, mark, isRecording, currentSession, subscribe, useRecording, resumeIfActive }
export default dbg
