// Thin client for the Mission Control read/write API.

async function json(resp) {
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    throw new Error(body.detail || `${resp.status} ${resp.statusText}`)
  }
  return resp.json()
}

export const getPipelines = () => fetch('/api/pipelines').then(json)
// Visual style playbooks (built-in + user-created) for the New Project picker.
export const getStyles = () => fetch('/api/styles').then(json)
export const getProjects = () => fetch('/api/projects').then(json)

// BYOK: read the local .env (curated variable menu + current values) and save edits back.
export const getEnv = () => fetch('/api/env').then(json)
export const saveEnv = (vars) =>
  fetch('/api/env', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ vars }),
  }).then(json)
// Anthropic account auth ("Sign in with Claude" OAuth + API-key fallback).
export const getAuthStatus = () => fetch('/api/auth/status').then(json)
export const startOAuth = () => fetch('/api/auth/oauth/start', { method: 'POST' }).then(json)
export const finishOAuth = (code) =>
  fetch('/api/auth/oauth/finish', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  }).then(json)
export const connectApiKey = (api_key) =>
  fetch('/api/auth/api-key', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key }),
  }).then(json)
export const disconnectAuth = () => fetch('/api/auth/disconnect', { method: 'POST' }).then(json)

// In-app feedback (bug/feature/other). Stored locally + PostHog event + best-effort email.
// Pass { debug_session } to attach a recorded editor session's analysis to the report.
export const sendFeedback = (body) =>
  fetch('/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(json)

// Discard a recorded debug session's logs (user chose not to send the debug report). Idempotent.
export const discardDebugSession = (session) =>
  fetch(`/api/debug/sessions/${encodeURIComponent(session)}`, { method: 'DELETE' }).then(json)

// Product-analytics opt-out state (+ anonymous device id) and the toggle to flip it.
export const getAnalytics = () => fetch('/api/settings/analytics').then(json)
export const setAnalytics = (disabled) =>
  fetch('/api/settings/analytics', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ disabled }),
  }).then(json)

// Fire-and-forget crash reporter. Called from window.onerror / the React ErrorBoundary, so it must
// NEVER throw (that would loop) and never use `json()` (which throws on !ok). Deduped + capped so a
// tight render-error loop can't flood the backend.
const _reported = new Set()
export function reportClientError(source, message, stack, context) {
  try {
    const sig = `${source}:${String(message).slice(0, 200)}`
    if (_reported.has(sig) || _reported.size > 25) return
    _reported.add(sig)
    fetch('/api/telemetry/error', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, message: String(message).slice(0, 1000), stack: stack ? String(stack).slice(0, 8000) : null, context }),
    }).catch(() => {})
  } catch { /* reporting must never throw */ }
}

export const getState = (id) => fetch(`/api/projects/${id}/state`).then(json)
export const getCapabilities = () => fetch('/api/capabilities').then(json)

// First-run / capability provisioning status: core/ffmpeg/composition + per-pack installed flags
// and metadata (label, size_mb). Drives the Capabilities settings panel.
export const getDoctor = () => fetch('/api/doctor').then(json)

// Install a lazy capability pack (transcription/vision/bg-removal/beat-sync/tts). Streams NDJSON
// frames {type:'log'|'done'|'error', ...} while pip runs — plain newline-delimited JSON (NOT the
// SSE `data:` framing that chatStream uses), so parse line-by-line.
export async function* provisionStream(pack, signal) {
  const resp = await fetch(`/api/provision/${encodeURIComponent(pack)}`, { method: 'POST', signal })
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    throw new Error(body.detail || `install failed (${resp.status})`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let nl
    while ((nl = buffer.indexOf('\n')) >= 0) {
      const raw = buffer.slice(0, nl).trim()
      buffer = buffer.slice(nl + 1)
      if (!raw) continue
      try { yield JSON.parse(raw) } catch { /* ignore malformed line */ }
    }
  }
  const tail = buffer.trim()
  if (tail) { try { yield JSON.parse(tail) } catch { /* ignore */ } }
}

// Answer an agent `request_capability` prompt: unblock the waiting tool after the UI installed
// (installed=true → agent retries) or the user declined (installed=false).
export const provideCapability = (id, cap_request_id, installed) =>
  fetch(`/api/projects/${id}/agent/provide-capability`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cap_request_id, installed }),
  }).then(json)
export const listAssets = (id) => fetch(`/api/projects/${id}/assets`).then(json)
// `v` is an optional cache-bust token (e.g. a file's mtime). Including it makes the
// URL change when the file's contents change, so a <video>/<img> re-fetches the new
// bytes instead of serving a stale (or mid-write, unplayable) copy from cache.
export const fileUrl = (id, path, v) =>
  `/api/projects/${id}/file?path=${encodeURIComponent(path)}` + (v != null ? `&v=${encodeURIComponent(v)}` : '')

// Artifacts: stage-grouped manifest + a single artifact's parsed content.
export const getArtifacts = (id) => fetch(`/api/projects/${id}/artifacts`).then(json)
export const getArtifact = (id, key) =>
  fetch(`/api/projects/${id}/artifacts/${encodeURIComponent(key)}`).then(json)

// Manual editor: read/save the edit_decisions timeline + drive renders.
export const getEditDecisions = (id) => fetch(`/api/projects/${id}/edit_decisions`).then(json)
export const saveEditDecisions = (id, doc) =>
  fetch(`/api/projects/${id}/edit_decisions`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(doc),
  }).then(json)
export const startRender = (id) =>
  fetch(`/api/projects/${id}/render`, { method: 'POST' }).then(json)
export const getRenderStatus = (id, jobId) =>
  fetch(`/api/projects/${id}/render/${jobId}`).then(json)
// Single still at time t (cheap scrub preview); returns an <img>-able URL.
export const frameUrl = (id, path, t) =>
  `/api/projects/${id}/frame?path=${encodeURIComponent(path)}&t=${encodeURIComponent(t)}`
// A cut's SOURCE clip (Range-served) for live <video> scrubbing before any render.
export const sourceUrl = (id, ref) =>
  `/api/projects/${id}/source?ref=${encodeURIComponent(ref)}`
// Source duration/dimensions for trim bounds + scrub math (duration may be null sans ffprobe).
export const getSourceMeta = (id, ref) =>
  fetch(`/api/projects/${id}/source_meta?ref=${encodeURIComponent(ref)}`).then(json)

// Activity log (files touched / skills / tools) + synthesized summary.
export const getActivity = (id, { limit, since } = {}) => {
  const q = new URLSearchParams()
  if (limit) q.set('limit', limit)
  if (since) q.set('since', since)
  const qs = q.toString()
  return fetch(`/api/projects/${id}/activity${qs ? `?${qs}` : ''}`).then(json)
}

export const createProject = (name, pipeline_type, style) =>
  fetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, pipeline_type, style }),
  }).then(json)

export const uploadAsset = (id, kind, file) => {
  const fd = new FormData()
  fd.append('kind', kind)
  fd.append('file', file)
  return fetch(`/api/projects/${id}/assets`, { method: 'POST', body: fd }).then(json)
}

export const confirmTool = (id, confirm_id, approved) =>
  fetch(`/api/projects/${id}/agent/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm_id, approved }),
  }).then(json)

export const answerQuestion = (id, question_id, answer) =>
  fetch(`/api/projects/${id}/agent/answer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question_id, answer }),
  }).then(json)

// Answer an agent `request_api_key` prompt: save the key to BYOK (skipped=false) or decline it.
export const provideKey = (id, body) =>
  fetch(`/api/projects/${id}/agent/provide-key`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(json)

export const stopAgent = (id) =>
  fetch(`/api/projects/${id}/agent/stop`, { method: 'POST' }).then(json)

// Chat threads (history + revival)
export const listThreads = (id) => fetch(`/api/projects/${id}/threads`).then(json)
export const getThread = (id, tid) => fetch(`/api/projects/${id}/threads/${tid}`).then(json)
export const createThread = (id, title) =>
  fetch(`/api/projects/${id}/threads`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  }).then(json)
export const saveThread = (id, tid, body) =>
  fetch(`/api/projects/${id}/threads/${tid}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(json)

// Stream an agent turn. /chat is POST, so we read the SSE body off fetch
// (EventSource can't POST). Yields one parsed event object per `data:` line.
export async function* chatStream(id, message, thread_id, signal, model) {
  const resp = await fetch(`/api/projects/${id}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, thread_id, model }),
    signal,
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    throw new Error(body.detail || `chat failed (${resp.status})`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const line = frame.split('\n').find((l) => l.startsWith('data:'))
      if (line) {
        try {
          yield JSON.parse(line.slice(5).trim())
        } catch {
          /* ignore malformed frame */
        }
      }
    }
  }
}
