// ChatPanel — the agent conversation view. Presentational: all state + handlers come from a
// `chat` bundle (see useAgentChat). Rendered by BOTH the pipeline window (App) and the editor
// (Studio) so the agent window is identical in both places. `className` lets the editor restyle
// it inside a resizable panel without forking the markup.

import { useEffect, useRef, useState } from 'react'
import { marked } from 'marked'
import { TOOL_ICON, formatToolInput } from './chatUtils.js'

// Configure marked for safe, compact output
marked.setOptions({ breaks: true, gfm: true })

export default function ChatPanel({ chat, disabled = false, className = '' }) {
  const {
    messages, input, setInput, busy,
    pendingConfirm, pendingQuestion, renderingStage, toolResults,
    threads, activeThread,
    send, stop, newChat, loadThread, resolveConfirm, answerQuestion,
  } = chat

  const endRef = useRef(null)
  const msgsRef = useRef(null)
  const stickRef = useRef(true)        // auto-scroll only while parked at the bottom
  const taRef = useRef(null)

  // Auto-scroll to the newest message, but ONLY if the user hasn't scrolled up
  // to read history. Scrolling up parks them there until they return to bottom.
  useEffect(() => {
    if (stickRef.current) endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, pendingConfirm, pendingQuestion, renderingStage])

  function onMessagesScroll() {
    const el = msgsRef.current
    if (!el) return
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  // Grow the composer with its content, up to ~10 lines, then scroll inside it.
  useEffect(() => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 220) + 'px'
  }, [input])

  return (
    <section className={`panel chat ${className}`.trim()}>
      <div className="chat-header">
        <h2>Agent</h2>
        <div className="chat-header-actions">
          {threads && threads.length > 0 && (
            <select
              className="thread-select"
              value={activeThread || ''}
              onChange={e => e.target.value && loadThread(e.target.value)}
              disabled={busy}
              title="Chat history"
            >
              <option value="">{activeThread ? 'Switch thread…' : 'History…'}</option>
              {threads.map(t => (
                <option key={t.thread_id} value={t.thread_id}>{t.title}</option>
              ))}
            </select>
          )}
          <button className="new-chat-btn" onClick={newChat} title="New chat" disabled={busy}>＋</button>
        </div>
      </div>
      <div className="messages" ref={msgsRef} onScroll={onMessagesScroll}>
        {messages.length === 0 && (
          <p className="empty">{disabled ? 'Select or create a project to start.' : 'Tell the agent what to make.'}</p>
        )}
        {messages.map((m, i) => <Message key={i} m={m} toolResults={toolResults} />)}
        {renderingStage && <RenderProgress />}
        {pendingConfirm && (
          <div className="confirm-card">
            <div className="confirm-title">⚠ Confirm command</div>
            <div className="confirm-reason">{pendingConfirm.reason}</div>
            <pre className="confirm-cmd">{pendingConfirm.input?.command || JSON.stringify(pendingConfirm.input)}</pre>
            <div className="confirm-actions">
              <button className="approve" onClick={() => resolveConfirm(true)}>Allow</button>
              <button className="deny" onClick={() => resolveConfirm(false)}>Block</button>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      {pendingQuestion && <QuestionCard q={pendingQuestion} onAnswer={answerQuestion} />}
      <form className="composer" onSubmit={e => { e.preventDefault(); send() }}>
        <textarea
          ref={taRef}
          className="composer-input"
          rows={1}
          placeholder={busy ? 'Agent is working…' : 'Message the agent…  (Enter to send, Shift+Enter for newline)'}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
          disabled={disabled || busy}
        />
        {busy
          ? <button type="button" className="stop-btn" onClick={stop} title="Stop the agent">■ Stop</button>
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

function Message({ m, toolResults }) {
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
  // assistant / assistant_stream — render items in the ORDER they happened so
  // tool calls and text stay interleaved (text → tool → text → …), not all
  // tool calls hoisted above all text. Consecutive text items are merged so a
  // paragraph split across stream chunks renders as one markdown block.
  const items = m.items || []
  const nodes = []
  let buf = []
  const flush = (key) => {
    if (!buf.length) return
    nodes.push(
      <div key={`t-${key}`} className="md-body"
        dangerouslySetInnerHTML={{ __html: marked.parse(buf.join('')) }} />
    )
    buf = []
  }
  items.forEach((it, i) => {
    if (it.kind === 'text') { buf.push(it.text); return }
    if (it.kind === 'tool_use' || it.kind === 'thinking') {
      flush(i)
      nodes.push(<ActivityChip key={`a-${i}`} item={it} result={it.id ? toolResults?.[it.id] : null} />)
    }
  })
  flush('end')

  return <div className="msg assistant">{nodes}</div>
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
