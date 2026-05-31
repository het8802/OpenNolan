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
export const getState = (id) => fetch(`/api/projects/${id}/state`).then(json)
export const getCapabilities = () => fetch('/api/capabilities').then(json)

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

// Stream an agent turn. /chat is POST, so we read the SSE body off fetch
// (EventSource can't POST). Yields one parsed event object per `data:` line.
export async function* chatStream(id, message, signal) {
  const resp = await fetch(`/api/projects/${id}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
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
