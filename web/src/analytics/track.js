// Renderer telemetry: the session id, the batcher, and the one header that makes joins work.
//
// The renderer has NO PostHog client of its own, on purpose. Everything goes to
// POST /api/telemetry/events (mirroring the existing /api/telemetry/error) so all four
// sources — Electron main, this renderer, the FastAPI backend and the agent loop — pass
// through ONE taxonomy validator, ONE scrubber and ONE envelope. A second SDK here would be
// a second set of rules to keep in sync, and a second way to leak a prompt body.
//
// What NEVER reaches here: per-frame values (the rAF tick), keystrokes, pointer coordinates
// and raw error text. Those stay in the private session recorder (web/src/debug/recorder.js),
// which is unchanged and still writes only to local disk.

const ENDPOINT = '/api/telemetry/events'
const FLUSH_MS = 5000
const MAX_QUEUE = 100 // matches the backend's per-batch ceiling

let queue = []
let timer = null

// The session id is MINTED IN ELECTRON MAIN and handed here through preload. Minting it in
// the renderer would start a new session on every ⌘R and would leave a launch that failed
// before the UI existed with no id at all. In a plain browser (QA, `npm run dev` without the
// shell) there is no preload, so fall back to a per-tab id — sessionStorage, not localStorage,
// so two tabs are two sessions and a reload is still one.
const SESSION_KEY = 'on.session.v1'
// Cleared ONLY on backend acceptance. Its presence means "this id was minted here and its
// session_started has not been acknowledged", which is what makes the announce idempotent
// across a reload that killed the queue before it flushed.
const PENDING_KEY = 'on.session.pending.v1'

function resolveSessionId() {
  try {
    const fromShell = window.openNolan?.sessionId
    if (fromShell) return fromShell
    let id = sessionStorage.getItem(SESSION_KEY)
    if (!id) {
      id = (crypto.randomUUID?.() || String(Math.random()).slice(2))
      sessionStorage.setItem(SESSION_KEY, id)
      // Mark BEFORE the announce can be attempted: a mint whose announce never lands must
      // re-announce on the next load, or the id labels every event of a session that is on
      // no register at all — which is how a real fatal crash counted as zero.
      sessionStorage.setItem(PENDING_KEY, '1')
    }
    return id
  } catch {
    return null
  }
}

export const sessionId = resolveSessionId()

/**
 * Register this session, exactly when the shell did not.
 *
 * `desktop/main.js` is the only emitter of `session_started`, so `npm run dev`, Playwright and
 * QA all INVENT an id, label every event with it, and never register the session. Measured in
 * the dev project: 6 session ids carrying product events with no `session_started` — including
 * the one carrying the project's only fatal exception, which made the crash-free wall read 0
 * against a real fatal crash.
 *
 * Sent as its OWN ISOLATED REQUEST, never batched. A batch `accepted` count cannot acknowledge
 * one event inside it: a batch carrying `session_started` plus one other, where the start is
 * rejected and the other accepted, returns accepted=1 — and the marker would clear with no
 * session registered.
 *
 * AT-LEAST-ONCE is the honest guarantee. The backend can accept and the response can be lost on
 * reload, giving two starts for one session; exactly-once is not achievable over this transport.
 * The METRIC is what tolerates it: Wall 5 counts DISTINCT session_id, not start rows.
 */
export function announceSession() {
  let pending = null
  try {
    if (window.openNolan?.sessionId) return Promise.resolve()   // the shell already announced
    pending = sessionStorage.getItem(PENDING_KEY)
  } catch { return Promise.resolve() }
  if (!pending || !sessionId) return Promise.resolve()
  return fetch(ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ events: [{ event: 'session_started', properties: { entry: 'editor', session_id: sessionId } }] }),
  })
    .then(r => (r.ok ? r.json() : null))
    .then(body => {
      // "Delivered" is fiction at three layers, so acknowledgement is defined as BACKEND
      // ACCEPTANCE of this exact event: sendBeacon returning true only means the UA queued it,
      // the endpoint is documented "always 200", and `received` counts events SUBMITTED to
      // capture() — which may still drop them for opt-out, taxonomy rejection or a silent SDK
      // failure. PostHog itself offers no synchronous receipt; only the S2 readback can confirm.
      if (body && body.accepted >= 1) sessionStorage.removeItem(PENDING_KEY)
    })
    .catch(() => { /* marker retained: the next load re-announces */ })
}

// Stamp X-ON-Session on every same-origin /api request by wrapping fetch ONCE, rather than
// touching ~40 call sites in api.js. The backend binds it to a ContextVar for the life of the
// request, so a capture() anywhere in any route joins to this session for free.
// ponytail: a global wrapper is the smallest diff that cannot be forgotten at a new call site.
let patched = false
export function installSessionHeader() {
  if (patched || typeof window === 'undefined' || !window.fetch || !sessionId) return
  patched = true
  const original = window.fetch.bind(window)
  window.fetch = (input, init) => {
    try {
      const url = typeof input === 'string' ? input : input?.url || ''
      if (url.startsWith('/api')) {
        const headers = new Headers((init && init.headers) || (typeof input !== 'string' ? input.headers : undefined))
        headers.set('X-ON-Session', sessionId)
        return original(input, { ...(init || {}), headers })
      }
    } catch {
      /* fall through to an unmodified fetch — telemetry must never break a request */
    }
    return original(input, init)
  }
}

/** Queue one product event. Undeclared names are dropped server-side by validate_event. */
export function track(event, properties) {
  if (!event) return
  if (queue.length >= MAX_QUEUE) queue.shift() // keep the most recent; never grow unbounded
  queue.push({ event, properties: { ...(properties || {}), session_id: sessionId } })
  if (!timer && typeof setTimeout !== 'undefined') timer = setTimeout(() => flush(), FLUSH_MS)
}

/**
 * Send what is queued. `beacon` uses sendBeacon, which is the only transport that survives
 * page teardown — a normal fetch is cancelled when the document goes away, which is exactly
 * when the once-per-session editor summary is emitted.
 */
export function flush(beacon = false) {
  if (timer) { clearTimeout(timer); timer = null }
  if (!queue.length) return Promise.resolve()
  const batch = queue
  queue = []
  const body = JSON.stringify({ events: batch })
  // A failed send puts the batch BACK — a transient blip must not drop events — but putting it
  // back has to respect the same ceiling as track(), and has to re-arm the timer, or the batch
  // sits there until the next track() call happens to come along.
  const requeue = () => {
    queue = batch.concat(queue).slice(-MAX_QUEUE)
    if (!timer && typeof setTimeout !== 'undefined') timer = setTimeout(() => flush(), FLUSH_MS)
  }
  try {
    if (beacon && navigator?.sendBeacon) {
      // sendBeacon RETURNS FALSE when the user agent refuses to queue it (over the payload
      // cap, or shutting down). Treating that as sent is how a summary silently disappears at
      // exactly the moment it is emitted.
      if (navigator.sendBeacon(ENDPOINT, new Blob([body], { type: 'application/json' }))) {
        return Promise.resolve()
      }
      requeue()
      return Promise.resolve()
    }
    return fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: beacon,
    }).catch(requeue)
  } catch {
    requeue()
    return Promise.resolve()
  }
}

/**
 * The per-session http_error rollup, flushed once at teardown.
 *
 * Uploading one event per 4xx would put a broken poll loop straight through the session
 * budget; the question ("how many 422s and 404s does a real session hit, and on which
 * routes") is a counter question.
 */
function flushHttpErrors(api) {
  const roll = api?.httpErrorRollup?.()
  if (!roll || !roll.count) return
  track('http_error', {
    count: roll.count,
    by_status: roll.byStatus,
    by_route: roll.byRoute,
  })
}

/** Call once at app start. Idempotent. */
export function initAnalytics(api) {
  installSessionHeader()
  announceSession()
  if (typeof window !== 'undefined') {
    window.addEventListener('pagehide', () => {
      flushHttpErrors(api)
      flush(true)
    })
  }
}

export default { track, flush, initAnalytics, announceSession, sessionId }
