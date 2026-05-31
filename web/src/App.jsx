import React, { useCallback, useEffect, useRef, useState } from 'react'
import * as api from './api.js'

const STATUS_LABEL = {
  pending: 'pending',
  in_progress: 'running',
  awaiting_human: 'needs you',
  completed: 'done',
  failed: 'failed',
  error: 'error',
}

const ASSET_KINDS = ['images', 'video', 'audio', 'music']

export default function App() {
  const [pipelines, setPipelines] = useState([])
  const [projects, setProjects] = useState([])
  const [selected, setSelected] = useState(null)
  const [state, setState] = useState(null)
  const [caps, setCaps] = useState(null)
  const [messages, setMessages] = useState([])
  const [pendingConfirm, setPendingConfirm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [input, setInput] = useState('')
  const [toast, setToast] = useState(null)

  // initial load
  useEffect(() => {
    api.getPipelines().then((d) => setPipelines(d.pipelines || [])).catch(showError)
    refreshProjects()
    api.getCapabilities().then(setCaps).catch(() => {})
  }, [])

  // poll state for the selected project
  useEffect(() => {
    if (!selected) {
      setState(null)
      return
    }
    let alive = true
    const tick = () =>
      api.getState(selected).then((s) => alive && setState(s)).catch(() => {})
    tick()
    const id = setInterval(tick, 1500)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [selected])

  function showError(e) {
    setToast({ kind: 'error', text: String(e.message || e) })
    setTimeout(() => setToast(null), 5000)
  }
  function showOk(text) {
    setToast({ kind: 'ok', text })
    setTimeout(() => setToast(null), 3000)
  }

  function refreshProjects() {
    return api.getProjects().then((d) => setProjects(d.projects || [])).catch(showError)
  }

  const send = useCallback(async () => {
    const message = input.trim()
    if (!message || !selected || busy) return
    setInput('')
    setMessages((m) => [...m, { role: 'user', text: message }])
    setBusy(true)
    try {
      for await (const evt of api.chatStream(selected, message)) {
        if (evt.type === 'assistant') {
          setMessages((m) => [...m, { role: 'assistant', items: evt.items }])
        } else if (evt.type === 'result') {
          setMessages((m) => [...m, { role: 'result', ...evt }])
        } else if (evt.type === 'confirm_request') {
          setPendingConfirm(evt)
        } else if (evt.type === 'error') {
          setMessages((m) => [...m, { role: 'error', text: evt.detail }])
        }
      }
    } catch (e) {
      setMessages((m) => [...m, { role: 'error', text: String(e.message || e) }])
    } finally {
      setBusy(false)
    }
  }, [input, selected, busy])

  async function resolveConfirm(approved) {
    if (!pendingConfirm || !selected) return
    try {
      await api.confirmTool(selected, pendingConfirm.confirm_id, approved)
    } catch (e) {
      showError(e)
    } finally {
      setPendingConfirm(null)
    }
  }

  return (
    <div className="app">
      <Header
        pipelines={pipelines}
        projects={projects}
        selected={selected}
        onSelect={(id) => {
          setSelected(id)
          setMessages([])
          setPendingConfirm(null)
        }}
        onCreate={async (name, pipeline) => {
          try {
            const m = await api.createProject(name, pipeline)
            await refreshProjects()
            setSelected(m.project_id)
            setMessages([])
            showOk(`Created “${m.name}”`)
          } catch (e) {
            showError(e)
          }
        }}
        caps={caps}
      />

      <main className="grid">
        <ChatPanel
          messages={messages}
          input={input}
          setInput={setInput}
          onSend={send}
          busy={busy}
          disabled={!selected}
          pendingConfirm={pendingConfirm}
          onConfirm={resolveConfirm}
        />
        <Pipeline state={state} selected={selected} />
        <AssetPanel
          selected={selected}
          onUpload={async (kind, file) => {
            try {
              const r = await api.uploadAsset(selected, kind, file)
              showOk(`Uploaded ${r.filename} → ${kind}`)
            } catch (e) {
              showError(e)
            }
          }}
        />
      </main>

      {toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}
    </div>
  )
}

function Header({ pipelines, projects, selected, onSelect, onCreate, caps }) {
  const [name, setName] = useState('')
  const [pipeline, setPipeline] = useState('')
  useEffect(() => {
    if (!pipeline && pipelines.length) setPipeline(pipelines[0].name)
  }, [pipelines])

  const runtimes = caps?.composition_runtimes || {}
  return (
    <header className="header">
      <div className="brand">
        <span className="dot" /> OpenMontage <span className="muted">· Mission Control</span>
      </div>
      <div className="controls">
        <select value={selected || ''} onChange={(e) => onSelect(e.target.value || null)}>
          <option value="">Select a project…</option>
          {projects.map((p) => (
            <option key={p.project_id} value={p.project_id}>
              {p.name} ({p.pipeline_type})
            </option>
          ))}
        </select>
        <form
          className="new-project"
          onSubmit={(e) => {
            e.preventDefault()
            if (name.trim() && pipeline) {
              onCreate(name.trim(), pipeline)
              setName('')
            }
          }}
        >
          <input
            placeholder="New project name…"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <select value={pipeline} onChange={(e) => setPipeline(e.target.value)}>
            {pipelines.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
              </option>
            ))}
          </select>
          <button type="submit">Create</button>
        </form>
      </div>
      <div className="runtimes">
        {['remotion', 'hyperframes', 'ffmpeg'].map((r) => (
          <span key={r} className={`chip ${runtimes[r] ? 'on' : 'off'}`}>
            {r}
          </span>
        ))}
      </div>
    </header>
  )
}

function ChatPanel({ messages, input, setInput, onSend, busy, disabled, pendingConfirm, onConfirm }) {
  const endRef = useRef(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, pendingConfirm])

  return (
    <section className="panel chat">
      <h2>Agent</h2>
      <div className="messages">
        {messages.length === 0 && (
          <p className="empty">
            {disabled ? 'Select or create a project to start.' : 'Tell the agent what to make.'}
          </p>
        )}
        {messages.map((m, i) => (
          <Message key={i} m={m} />
        ))}
        {pendingConfirm && (
          <div className="confirm-card">
            <div className="confirm-title">⚠ Confirm tool</div>
            <div className="confirm-reason">{pendingConfirm.reason}</div>
            <pre className="confirm-cmd">
              {pendingConfirm.input?.command || JSON.stringify(pendingConfirm.input)}
            </pre>
            <div className="confirm-actions">
              <button className="approve" onClick={() => onConfirm(true)}>
                Approve
              </button>
              <button className="deny" onClick={() => onConfirm(false)}>
                Deny
              </button>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          onSend()
        }}
      >
        <input
          placeholder={busy ? 'Agent is working…' : 'Message the agent…'}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={disabled || busy}
        />
        <button type="submit" disabled={disabled || busy || !input.trim()}>
          {busy ? '…' : 'Send'}
        </button>
      </form>
    </section>
  )
}

function Message({ m }) {
  if (m.role === 'user') return <div className="msg user">{m.text}</div>
  if (m.role === 'error') return <div className="msg error">⚠ {m.text}</div>
  if (m.role === 'result')
    return (
      <div className="msg result">
        {m.is_error ? 'Turn ended with an error.' : 'Turn complete.'}
        {m.total_cost_usd != null && <span className="cost"> ${m.total_cost_usd.toFixed(3)}</span>}
        {m.num_turns != null && <span className="muted"> · {m.num_turns} steps</span>}
      </div>
    )
  // assistant
  return (
    <div className="msg assistant">
      {(m.items || []).map((it, i) => {
        if (it.kind === 'text') return <span key={i}>{it.text}</span>
        if (it.kind === 'tool_use')
          return (
            <code key={i} className="tool-chip">
              {it.name}
            </code>
          )
        if (it.kind === 'thinking') return <span key={i} className="muted">💭</span>
        return null
      })}
    </div>
  )
}

function Pipeline({ state, selected }) {
  return (
    <section className="panel pipeline">
      <h2>Pipeline</h2>
      {!selected && <p className="empty">No project selected.</p>}
      {selected && state && (
        <>
          <div className="pl-head">
            <strong>{state.name}</strong>
            <span className="muted"> · {state.pipeline_type || 'unknown'}</span>
          </div>
          <ol className="stepper">
            {(state.stages || []).map((s) => (
              <li key={s.stage} className={`step ${s.status}`}>
                <span className="bullet" />
                <span className="step-name">{s.stage}</span>
                <span className="step-status">{STATUS_LABEL[s.status] || s.status}</span>
              </li>
            ))}
          </ol>
          {state.next_stage && (
            <div className="next">
              Next: <strong>{state.next_stage}</strong>
            </div>
          )}
          {(!state.stages || state.stages.length === 0) && (
            <p className="empty">No stages run yet.</p>
          )}
        </>
      )}
    </section>
  )
}

function AssetPanel({ selected, onUpload }) {
  const [kind, setKind] = useState('images')
  const inputRef = useRef(null)
  const [dragging, setDragging] = useState(false)

  function handleFiles(files) {
    if (!selected || !files?.length) return
    onUpload(kind, files[0])
  }

  return (
    <section className="panel assets">
      <h2>Assets</h2>
      {!selected && <p className="empty">Select a project to upload.</p>}
      {selected && (
        <>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {ASSET_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <div
            className={`dropzone ${dragging ? 'drag' : ''}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              handleFiles(e.dataTransfer.files)
            }}
          >
            Drop a file here, or click to choose
            <input
              ref={inputRef}
              type="file"
              hidden
              onChange={(e) => handleFiles(e.target.files)}
            />
          </div>
        </>
      )}
    </section>
  )
}
