import React, { useCallback, useEffect, useRef, useState } from 'react'
import { marked } from 'marked'
import * as api from './api.js'

// Configure marked for safe, compact output
marked.setOptions({ breaks: true, gfm: true })

const STATUS_LABEL = {
  pending: 'pending',
  in_progress: 'running',
  awaiting_human: 'needs you',
  completed: 'done',
  failed: 'failed',
  error: 'error',
}

const ASSET_KINDS = ['images', 'video', 'audio', 'music']

const TOOL_ICON = {
  Read: '📄',
  Write: '✏️',
  Edit: '✏️',
  MultiEdit: '✏️',
  Bash: '⌨️',
  Glob: '🔍',
  Grep: '🔍',
  WebSearch: '🌐',
  WebFetch: '🌐',
  Skill: '🛠',
  TodoWrite: '📋',
}

// Detect whether text contains option patterns like:
//   A) ...   B) ...   or   1. ...   2. ...
// Returns an array of {label, text} if found, empty array otherwise.
function extractOptions(rawText) {
  if (!rawText) return []
  const lines = rawText.split('\n').map(l => l.trim()).filter(Boolean)
  const letterOpt = /^([A-D])[).]\s+(.+)$/
  const numOpt = /^(\d+)[).]\s+(.+)$/
  let opts = []
  for (const l of lines) {
    const lm = l.match(letterOpt) || l.match(numOpt)
    if (lm) opts.push({ label: lm[1], text: lm[2] })
  }
  return opts.length >= 2 ? opts : []
}

// Detect render-in-progress from a tool_use event
function isRenderCommand(item) {
  if (item.kind !== 'tool_use' || item.name !== 'Bash') return false
  const d = (item.detail || '').toLowerCase()
  return d.includes('npx remotion') || d.includes('ffmpeg') || d.includes('npm run render') || d.includes('hyperframes render')
}

export default function App() {
  const [pipelines, setPipelines] = useState([])
  const [projects, setProjects] = useState([])
  const [selected, setSelected] = useState(null)
  const [state, setState] = useState(null)
  const [messages, setMessages] = useState([])
  const [pendingConfirm, setPendingConfirm] = useState(null)
  const [pendingQuestion, setPendingQuestion] = useState(null)
  const [busy, setBusy] = useState(false)
  const [input, setInput] = useState('')
  const [toast, setToast] = useState(null)
  const [renderingStage, setRenderingStage] = useState(null) // tool_use id of in-flight render
  const [toolResults, setToolResults] = useState({})          // tool_use_id -> result, for expansion
  const [uploadTick, setUploadTick] = useState(0)             // bump to refresh asset listing
  const [threads, setThreads] = useState([])                  // chat threads for the project
  const [activeThread, setActiveThread] = useState(null)      // current thread id
  const messagesRef = useRef([])                              // latest messages, for thread persistence
  const sessionIdRef = useRef(null)                           // latest agent session_id
  const abortRef = useRef(null)                               // aborts the in-flight chat stream (Stop)
  useEffect(() => { messagesRef.current = messages }, [messages])

  useEffect(() => {
    api.getPipelines().then(d => setPipelines(d.pipelines || [])).catch(showError)
    refreshProjects()
    // Poll the project list so externally/agent-created projects appear live.
    const id = setInterval(refreshProjects, 4000)
    return () => clearInterval(id)
  }, [])

  // Poll state
  useEffect(() => {
    if (!selected) { setState(null); return }
    let alive = true
    const tick = () => api.getState(selected).then(s => alive && setState(s)).catch(() => {})
    tick()
    const id = setInterval(tick, 1500)
    return () => { alive = false; clearInterval(id) }
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
    return api.getProjects().then(d => setProjects(d.projects || [])).catch(showError)
  }

  function refreshThreads(projectId) {
    const id = projectId || selected
    if (!id) return Promise.resolve()
    return api.listThreads(id).then(d => setThreads(d.threads || [])).catch(() => {})
  }

  function clearChat() {
    setMessages([])
    setPendingConfirm(null)
    setPendingQuestion(null)
    setRenderingStage(null)
    setToolResults({})
    setInput('')
    sessionIdRef.current = null
  }

  // '+' new chat: start a fresh thread (created lazily on first message).
  function newChat() {
    clearChat()
    setActiveThread(null)
  }

  async function loadThread(tid) {
    if (!selected || !tid) return
    try {
      const rec = await api.getThread(selected, tid)
      clearChat()
      setMessages(rec.messages || [])
      sessionIdRef.current = rec.session_id || null
      setActiveThread(tid)
    } catch (e) { showError(e) }
  }

  function deriveTitle(msgs) {
    const firstUser = (msgs || []).find(m => m.role === 'user')
    const t = (firstUser?.text || 'New chat').trim().replace(/\s+/g, ' ')
    return t.length > 48 ? t.slice(0, 48) + '…' : t
  }

  function persistThread(tid) {
    if (!selected || !tid) return
    api.saveThread(selected, tid, {
      messages: messagesRef.current,
      session_id: sessionIdRef.current,
      title: deriveTitle(messagesRef.current),
    }).then(() => refreshThreads()).catch(() => {})
  }

  const send = useCallback(async (text) => {
    const message = (text || input).trim()
    if (!message || !selected || busy) return
    setInput('')
    setMessages(m => [...m, { role: 'user', text: message }])
    setBusy(true)
    // Ensure a thread exists to persist this conversation into.
    let tid = activeThread
    if (!tid) {
      try {
        const rec = await api.createThread(selected, deriveTitle([{ role: 'user', text: message }]))
        tid = rec.thread_id
        setActiveThread(tid)
      } catch { /* persistence is best-effort; continue the chat regardless */ }
    }
    const controller = new AbortController()
    abortRef.current = controller
    try {
      for await (const evt of api.chatStream(selected, message, tid, controller.signal)) {
        if (evt.type === 'assistant') {
          setMessages(m => {
            const last = m[m.length - 1]
            // If last message is assistant, merge items into it for streaming effect
            if (last?.role === 'assistant_stream') {
              return [...m.slice(0, -1), { ...last, items: [...(last.items || []), ...evt.items] }]
            }
            return [...m, { role: 'assistant_stream', items: evt.items }]
          })
          // Capture render state + pair tool results to their tool_use by id
          for (const it of evt.items || []) {
            if (isRenderCommand(it)) setRenderingStage(it.id)
            if (it.kind === 'tool_result') {
              setToolResults(prev => ({ ...prev, [it.tool_use_id]: it }))
              if (it.tool_use_id === renderingStage) setRenderingStage(null)
            }
          }
        } else if (evt.type === 'result') {
          setRenderingStage(null)
          if (evt.session_id) sessionIdRef.current = evt.session_id
          setMessages(m => {
            // Finalize the last assistant_stream -> assistant (KEEP its text),
            // then append the result line.
            const last = m[m.length - 1]
            if (last?.role === 'assistant_stream') {
              const finalized = { ...last, role: 'assistant' }
              return [...m.slice(0, -1), finalized, { role: 'result', ...evt }]
            }
            return [...m, { role: 'result', ...evt }]
          })
        } else if (evt.type === 'confirm_request') {
          setPendingConfirm(evt)
        } else if (evt.type === 'question') {
          setPendingQuestion(evt)
        } else if (evt.type === 'error') {
          setMessages(m => [...m, { role: 'error', text: evt.detail }])
        }
      }
    } catch (e) {
      if (e.name === 'AbortError' || controller.signal.aborted) {
        setRenderingStage(null)
        setMessages(m => [...m, { role: 'note', text: '■ Stopped. Your next message resumes this session with its context.' }])
      } else {
        setMessages(m => [...m, { role: 'error', text: String(e.message || e) }])
      }
    } finally {
      abortRef.current = null
      setBusy(false)
      // Persist the conversation (messages + session_id) so the thread is revivable.
      if (tid) setTimeout(() => persistThread(tid), 0)
    }
  }, [input, selected, busy, renderingStage, activeThread])

  async function stop() {
    if (!selected) return
    // Interrupt the agent server-side (context preserved), then stop reading.
    try { await api.stopAgent(selected) } catch { /* best effort */ }
    abortRef.current?.abort()
  }

  async function resolveConfirm(approved) {
    if (!pendingConfirm || !selected) return
    try { await api.confirmTool(selected, pendingConfirm.confirm_id, approved) }
    catch (e) { showError(e) }
    finally { setPendingConfirm(null) }
  }

  async function answerQuestion(answer) {
    if (!pendingQuestion || !selected) return
    setMessages(m => [...m, { role: 'user', text: answer }])  // show the choice in the chat
    try { await api.answerQuestion(selected, pendingQuestion.question_id, answer) }
    catch (e) { showError(e) }
    finally { setPendingQuestion(null) }
  }

  if (!selected) {
    return (
      <div className="app">
        <Dashboard
          pipelines={pipelines}
          projects={projects}
          onOpen={id => { setSelected(id); newChat(); refreshThreads(id) }}
          onCreate={async (name, pipeline) => {
            const m = await api.createProject(name, pipeline)
            await refreshProjects()
            setSelected(m.project_id)
            newChat()
            refreshThreads(m.project_id)
            showOk(`Created "${m.name}"`)
          }}
        />
        {toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}
      </div>
    )
  }

  return (
    <div className="app">
      <ProjectBar
        state={state} projects={projects} selected={selected}
        onBack={() => { setSelected(null); clearChat(); setActiveThread(null) }}
      />
      <main className="grid">
        <ChatPanel
          messages={messages} input={input} setInput={setInput}
          onSend={send} onNewChat={newChat} onStop={stop}
          busy={busy} disabled={!selected}
          pendingConfirm={pendingConfirm} onConfirm={resolveConfirm}
          pendingQuestion={pendingQuestion} onAnswer={answerQuestion}
          renderingStage={renderingStage} toolResults={toolResults}
          threads={threads} activeThread={activeThread} onLoadThread={loadThread}
        />
        <Pipeline state={state} selected={selected} />
        <AssetPanel
          selected={selected}
          uploadTick={uploadTick}
          onUpload={async (kind, file) => {
            try {
              const r = await api.uploadAsset(selected, kind, file)
              showOk(`Uploaded ${r.filename} → ${kind}`)
              setUploadTick(t => t + 1)
            } catch (e) { showError(e) }
          }}
        />
      </main>
      {toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}
    </div>
  )
}

// ─── Dashboard (project tiles + create) ────────────────────────────────────────

function hueOf(s) {
  let h = 0
  for (const c of (s || '')) h = (h * 31 + c.charCodeAt(0)) % 360
  return h
}
function fmtDate(s) {
  try {
    const d = new Date(s)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch { return '' }
}

function Dashboard({ pipelines, projects, onOpen, onCreate }) {
  const [creating, setCreating] = useState(false)
  const sorted = [...projects].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
  return (
    <div className="dashboard">
      <header className="dash-header">
        <div className="brand"><span className="dot" /> OpenMontage <span className="muted">· Mission Control</span></div>
        <div className="dash-sub">{projects.length} project{projects.length === 1 ? '' : 's'}</div>
      </header>
      <div className="dash-grid">
        <button className="tile tile-new" onClick={() => setCreating(true)}>
          <span className="tile-plus">＋</span>
          <span className="tile-new-label">New project</span>
        </button>
        {sorted.map(p => (
          <button key={p.project_id} className="tile tile-project" onClick={() => onOpen(p.project_id)}>
            <span className="tile-accent" style={{ background: `hsl(${hueOf(p.pipeline_type)} 52% 60%)` }} />
            <span className="tile-name">{p.name}</span>
            <span className="tile-meta">
              {p.pipeline_type
                ? <span className="tile-type">{p.pipeline_type}</span>
                : <span className="tile-type unknown">unknown type</span>}
              {p.legacy && <span className="tile-legacy">existing</span>}
            </span>
            {p.created_at && <span className="tile-date">{fmtDate(p.created_at)}</span>}
          </button>
        ))}
      </div>
      {creating && (
        <CreateModal pipelines={pipelines} onClose={() => setCreating(false)} onCreate={onCreate} />
      )}
    </div>
  )
}

function CreateModal({ pipelines, onClose, onCreate }) {
  const [name, setName] = useState('')
  const [pipeline, setPipeline] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  useEffect(() => { if (!pipeline && pipelines.length) setPipeline(pipelines[0].name) }, [pipelines])

  async function submit(e) {
    e.preventDefault()
    if (!name.trim() || !pipeline || busy) return
    setBusy(true); setErr(null)
    try { await onCreate(name.trim(), pipeline); onClose() }
    catch (e) { setErr(String(e.message || e)); setBusy(false) }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <form className="modal" onClick={e => e.stopPropagation()} onSubmit={submit}>
        <h3>New project</h3>
        <label className="modal-field">
          <span>Name</span>
          <input autoFocus placeholder="e.g. launch announcement reel"
            value={name} onChange={e => setName(e.target.value)} />
        </label>
        <label className="modal-field">
          <span>Pipeline type</span>
          <select value={pipeline} onChange={e => setPipeline(e.target.value)}>
            {pipelines.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
          </select>
        </label>
        {err && <div className="modal-err">⚠ {err}</div>}
        <div className="modal-actions">
          <button type="button" className="modal-cancel" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={busy || !name.trim()}>{busy ? 'Creating…' : 'Create'}</button>
        </div>
      </form>
    </div>
  )
}

// ─── Project Bar (slim top bar inside a project) ────────────────────────────────

function ProjectBar({ state, projects, selected, onBack }) {
  const p = projects.find(x => x.project_id === selected)
  const name = state?.name || p?.name || selected
  const type = state?.pipeline_type || p?.pipeline_type || ''
  const runtime = state?.runtime || null
  return (
    <header className="header project-bar">
      <button className="back-btn" onClick={onBack} title="Back to all projects">← Projects</button>
      <div className="pb-title">
        <span className="dot" />
        <strong>{name}</strong>
        {type && <span className="pb-type">{type}</span>}
      </div>
      <div className="runtimes">
        {runtime
          ? <span className="chip on runtime-used" title="Composition runtime used by this project">🎬 {runtime}</span>
          : <span className="chip off" title="No render runtime chosen yet">runtime: not set</span>}
      </div>
    </header>
  )
}

// ─── Chat Panel ───────────────────────────────────────────────────────────────

function ChatPanel({ messages, input, setInput, onSend, onNewChat, onStop, busy, disabled, pendingConfirm, onConfirm, pendingQuestion, onAnswer, renderingStage, toolResults, threads, activeThread, onLoadThread }) {
  const endRef = useRef(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, pendingConfirm, pendingQuestion, renderingStage])

  return (
    <section className="panel chat">
      <div className="chat-header">
        <h2>Agent</h2>
        <div className="chat-header-actions">
          {threads && threads.length > 0 && (
            <select
              className="thread-select"
              value={activeThread || ''}
              onChange={e => e.target.value && onLoadThread(e.target.value)}
              disabled={busy}
              title="Chat history"
            >
              <option value="">{activeThread ? 'Switch thread…' : 'History…'}</option>
              {threads.map(t => (
                <option key={t.thread_id} value={t.thread_id}>{t.title}</option>
              ))}
            </select>
          )}
          <button className="new-chat-btn" onClick={onNewChat} title="New chat" disabled={busy}>＋</button>
        </div>
      </div>
      <div className="messages">
        {messages.length === 0 && (
          <p className="empty">{disabled ? 'Select or create a project to start.' : 'Tell the agent what to make.'}</p>
        )}
        {messages.map((m, i) => <Message key={i} m={m} onOptionClick={text => onSend(text)} toolResults={toolResults} />)}
        {renderingStage && <RenderProgress />}
        {pendingConfirm && (
          <div className="confirm-card">
            <div className="confirm-title">⚠ Confirm command</div>
            <div className="confirm-reason">{pendingConfirm.reason}</div>
            <pre className="confirm-cmd">{pendingConfirm.input?.command || JSON.stringify(pendingConfirm.input)}</pre>
            <div className="confirm-actions">
              <button className="approve" onClick={() => onConfirm(true)}>Allow</button>
              <button className="deny" onClick={() => onConfirm(false)}>Block</button>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      {pendingQuestion && <QuestionCard q={pendingQuestion} onAnswer={onAnswer} />}
      <form className="composer" onSubmit={e => { e.preventDefault(); onSend() }}>
        <textarea
          className="composer-input"
          rows={1}
          placeholder={busy ? 'Agent is working…' : 'Message the agent…  (Enter to send, Shift+Enter for newline)'}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              onSend()
            }
          }}
          disabled={disabled || busy}
        />
        {busy
          ? <button type="button" className="stop-btn" onClick={onStop} title="Stop the agent">■ Stop</button>
          : <button type="submit" disabled={disabled || !input.trim()}>Send</button>}
      </form>
    </section>
  )
}

function RenderProgress() {
  const [pct, setPct] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setPct(p => Math.min(p + 1.5, 90)), 400)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="render-progress">
      <div className="rp-label">🎬 Rendering…</div>
      <div className="rp-bar"><div className="rp-fill" style={{ width: `${pct}%` }} /></div>
      <div className="rp-pct">{Math.round(pct)}%</div>
    </div>
  )
}

// ─── Question Card (agent asked a clarifying question) ──────────────────────────

function QuestionCard({ q, onAnswer }) {
  return (
    <div className="question-card">
      {q.header && <div className="q-header">{q.header}</div>}
      <div className="q-body md-body" dangerouslySetInnerHTML={{ __html: marked.parse(q.question || '') }} />
      <div className="q-options">
        {(q.options || []).map((opt, i) => (
          <button
            key={i}
            className="q-option"
            onClick={() => onAnswer(opt)}
            dangerouslySetInnerHTML={{ __html: marked.parseInline(opt) }}
          />
        ))}
      </div>
    </div>
  )
}

// ─── Message ─────────────────────────────────────────────────────────────────

function Message({ m, onOptionClick, toolResults }) {
  if (m.role === 'user') return <div className="msg user">{m.text}</div>
  if (m.role === 'error') return <div className="msg error">⚠ {m.text}</div>
  if (m.role === 'note') return <div className="msg note">{m.text}</div>
  if (m.role === 'result') {
    return (
      <div className="msg result">
        {m.is_error ? '⚠ Turn ended — your next message resumes this session with its context.' : 'Turn complete.'}
        {m.total_cost_usd != null && <span className="cost"> ${m.total_cost_usd.toFixed(3)}</span>}
        {m.num_turns != null && <span className="muted"> · {m.num_turns} steps</span>}
      </div>
    )
  }
  // assistant or assistant_stream
  const textBlocks = (m.items || []).filter(it => it.kind === 'text')
  // tool_use chips are interactive; standalone tool_result items are paired into
  // their tool_use via toolResults, so we don't render them separately.
  const activityBlocks = (m.items || []).filter(it => it.kind === 'tool_use' || it.kind === 'thinking')
  const fullText = textBlocks.map(it => it.text).join('')
  const options = extractOptions(fullText)

  return (
    <div className="msg assistant">
      {activityBlocks.map((it, i) => (
        <ActivityChip key={i} item={it} result={it.id ? toolResults?.[it.id] : null} />
      ))}
      {fullText && (
        <div className="md-body" dangerouslySetInnerHTML={{ __html: marked.parse(fullText) }} />
      )}
      {options.length > 0 && (
        <div className="options">
          {options.map((o, i) => (
            <button key={i} className="option-btn" onClick={() => onOptionClick(`${o.label}) ${o.text}`)}>
              {o.label}) {o.text}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function formatToolInput(item) {
  const inp = item.input || {}
  if (item.name === 'Bash') return inp.command || ''
  if (['Write', 'Edit', 'MultiEdit', 'NotebookEdit'].includes(item.name)) {
    const path = inp.file_path || inp.path || ''
    if (inp.content != null) return `${path}\n\n${inp.content}`
    if (inp.old_string != null || inp.new_string != null) {
      return `${path}\n\n- - - old - - -\n${inp.old_string || ''}\n\n+ + + new + + +\n${inp.new_string || ''}`
    }
    return path
  }
  return JSON.stringify(inp, null, 2)
}

function ActivityChip({ item, result }) {
  const [open, setOpen] = useState(false)
  if (item.kind === 'thinking') return <span className="activity-chip thinking">💭 thinking…</span>

  const icon = TOOL_ICON[item.name] || '🔧'
  const hasResult = result != null
  const resultErr = hasResult && result.is_error
  return (
    <div className={`tool-block ${open ? 'open' : ''}`}>
      <button className={`activity-chip tool clickable ${resultErr ? 'tool-err' : ''}`} onClick={() => setOpen(o => !o)}>
        <span className="tc-caret">{open ? '▾' : '▸'}</span>
        <span className="tc-icon">{icon}</span>
        <span className="tc-name">{item.name}</span>
        {item.detail && <span className="tc-detail">{item.detail}</span>}
        {resultErr && <span className="tc-badge err">error</span>}
      </button>
      {open && (
        <div className="tool-expand">
          <div className="te-label">input</div>
          <pre className="te-pre">{formatToolInput(item)}</pre>
          {hasResult && (
            <>
              <div className={`te-label ${resultErr ? 'err' : ''}`}>{resultErr ? 'error' : 'output'}</div>
              <pre className={`te-pre ${resultErr ? 'err' : ''}`}>
                {typeof result.content === 'string' ? result.content : JSON.stringify(result.content, null, 2)}
              </pre>
            </>
          )}
          {!hasResult && <div className="te-pending">awaiting result…</div>}
        </div>
      )}
    </div>
  )
}

// ─── Pipeline ─────────────────────────────────────────────────────────────────

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
            {(state.stages || []).map(s => (
              <li key={s.stage} className={`step ${s.status}`}>
                <span className="bullet" />
                <span className="step-name">{s.stage}</span>
                <span className="step-status">{STATUS_LABEL[s.status] || s.status}</span>
                {s.status === 'in_progress' && <span className="pulse" />}
              </li>
            ))}
          </ol>
          {state.next_stage && <div className="next">Next: <strong>{state.next_stage}</strong></div>}
          {(!state.stages || state.stages.length === 0) && <p className="empty">No stages run yet.</p>}
        </>
      )}
    </section>
  )
}

// ─── Assets Panel ─────────────────────────────────────────────────────────────

function AssetPanel({ selected, onUpload, uploadTick }) {
  const [activeKind, setActiveKind] = useState('images')
  const [data, setData] = useState(null)
  const inputRef = useRef(null)
  const [dragging, setDragging] = useState(false)

  // Poll the asset listing for the selected project (live as the agent writes files).
  useEffect(() => {
    if (!selected) { setData(null); return }
    let alive = true
    const tick = () => api.listAssets(selected).then(d => alive && setData(d)).catch(() => {})
    tick()
    const id = setInterval(tick, 4000)
    return () => { alive = false; clearInterval(id) }
  }, [selected, uploadTick])

  function handleFiles(files) {
    if (!selected || !files?.length) return
    onUpload(activeKind, files[0])
  }

  const renders = data?.renders || []
  const files = data?.kinds?.[activeKind] || []

  return (
    <section className="panel assets">
      <h2>Assets</h2>
      {!selected && <p className="empty">Select a project to see its assets.</p>}
      {selected && (
        <div className="assets-scroll">
          {/* Final render(s) — shown when the reel is done (#1) */}
          {renders.length > 0 && (
            <div className="renders">
              <div className="renders-label">🎬 Final render</div>
              {renders.map(r => (
                <div key={r.path} className="render-item">
                  <video controls src={api.fileUrl(selected, r.path)} />
                  <a className="render-dl" href={api.fileUrl(selected, r.path)} download>{r.name}</a>
                </div>
              ))}
            </div>
          )}

          <div className="asset-tabs">
            {ASSET_KINDS.map(k => (
              <button
                key={k}
                className={`asset-tab ${activeKind === k ? 'active' : ''}`}
                onClick={() => setActiveKind(k)}
              >
                {k}{data?.kinds?.[k]?.length ? ` (${data.kinds[k].length})` : ''}
              </button>
            ))}
          </div>

          <div className="asset-grid">
            {files.length === 0 && <p className="empty">No {activeKind} yet.</p>}
            {files.map(f => (
              <AssetItem key={f.path} kind={activeKind} url={api.fileUrl(selected, f.path)} name={f.name} />
            ))}
          </div>

          <div
            className={`dropzone ${dragging ? 'drag' : ''}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={e => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={e => { e.preventDefault(); setDragging(false); handleFiles(e.dataTransfer.files) }}
          >
            Drop a <strong>{activeKind}</strong> file here, or click to choose
            <input ref={inputRef} type="file" hidden onChange={e => handleFiles(e.target.files)} />
          </div>
        </div>
      )}
    </section>
  )
}

function AssetItem({ kind, url, name }) {
  return (
    <div className="asset-item" title={name}>
      {kind === 'images' && <img src={url} alt={name} loading="lazy" />}
      {kind === 'video' && <video src={url} controls preload="metadata" />}
      {(kind === 'audio' || kind === 'music') && <audio src={url} controls preload="none" />}
      <div className="asset-name">{name}</div>
    </div>
  )
}
