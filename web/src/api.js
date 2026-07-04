// Thin client for the Mission Control read/write API.

async function json(resp) {
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    throw new Error(body.detail || `${resp.status} ${resp.statusText}`)
  }
  return resp.json()
}

export const getPipelines = () => fetch('/api/pipelines').then(json)
export const getProjects = () => fetch('/api/projects').then(json)

// BYOK: read the local .env (curated variable menu + current values) and save edits back.
export const getEnv = () => fetch('/api/env').then(json)
export const saveEnv = (vars) =>
  fetch('/api/env', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ vars }),
  }).then(json)
export const getState = (id) => fetch(`/api/projects/${id}/state`).then(json)
export const getCapabilities = () => fetch('/api/capabilities').then(json)
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

export const createProject = (name, pipeline_type) =>
  fetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, pipeline_type }),
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
