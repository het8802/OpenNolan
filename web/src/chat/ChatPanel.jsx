// ChatPanel — the agent conversation view. Presentational: all state + handlers come from a
// `chat` bundle (see useAgentChat). Rendered by BOTH the pipeline window (App) and the editor
// (Studio) so the agent window is identical in both places. `className` lets the editor restyle
// it inside a resizable panel without forking the markup.

import { useEffect, useRef, useState } from 'react'
import { marked } from 'marked'
import { TOOL_ICON, formatToolInput, AGENT_MODELS, DEFAULT_MODEL } from './chatUtils.js'
import { ClaudeLogo, IconAlert, IconKey, IconEye, IconEyeOff, IconBrain, IconTool, IconMovie, IconChevron } from '../components/icons.jsx'
import CapabilityInstall from '../CapabilityInstall.jsx'

// Configure marked for safe, compact output
marked.setOptions({ breaks: true, gfm: true })

export default function ChatPanel({ chat, disabled = false, className = '', auth, onReconnect }) {
  const {
    messages, input, setInput, busy,
    pendingConfirm, pendingQuestion, pendingKeyRequest, pendingCapability, renderingStage, toolResults,
    threads, activeThread, model = DEFAULT_MODEL, setModel,
    send, stop, newChat, loadThread, resolveConfirm, answerQuestion, provideKey, skipKeyRequest,
    resolveCapability,
  } = chat

  const endRef = useRef(null)
  const msgsRef = useRef(null)
  const stickRef = useRef(true)        // auto-scroll only while parked at the bottom
  const taRef = useRef(null)

  // Per-turn cost is the SDK's token spend, computed at API rates. It's only real money the user
  // pays when they're billed per-token — i.e. BYOK (`method: 'api_key'`). Under a Claude
  // subscription ('oauth') or a logged-in CLI ('cli') the agent runs on the user's plan and isn't
  // charged per-token, so the number would be a misleading notional figure — hide it there.
  const showCost = auth?.method === 'api_key'

  const [atBottom, setAtBottom] = useState(true)

  // Auto-scroll to the newest message, but ONLY if the user hasn't scrolled up to read
  // history. Scrolling up parks them there until they return to the bottom.
  //
  // ALWAYS instant, never smooth. A streaming turn fires this dozens of times, and each new
  // smooth scroll cancels the one still in flight — which is what made the transcript judder
  // for the whole turn. Content arriving is not a gesture the user made, so it gets no motion;
  // the only smooth scroll in this panel is the explicit "Jump to latest" below.
  useEffect(() => {
    if (stickRef.current) endRef.current?.scrollIntoView({ behavior: 'instant', block: 'end' })
  }, [messages, pendingConfirm, pendingQuestion, pendingKeyRequest, pendingCapability, renderingStage])

  function onMessagesScroll() {
    const el = msgsRef.current
    if (!el) return
    const parked = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    stickRef.current = parked
    setAtBottom(parked)
  }

  // The one place a smooth scroll is warranted: the user asked to travel. Keyboard activation
  // lands instantly (Enter/Space on a button is a repeated action; motion would only delay it).
  function jumpToLatest(e) {
    stickRef.current = true
    const smooth = e?.detail > 0   // detail === 0 for keyboard-activated clicks
    endRef.current?.scrollIntoView({ behavior: smooth ? 'smooth' : 'instant', block: 'end' })
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
        {messages.map((m, i) => <Message key={i} m={m} toolResults={toolResults} showCost={showCost} />)}
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
      {!atBottom && messages.length > 0 && (
        <button type="button" className="jump-latest" onClick={jumpToLatest}>Jump to latest</button>
      )}
      {pendingQuestion && <QuestionCard q={pendingQuestion} onAnswer={answerQuestion} />}
      {pendingKeyRequest && <ApiKeyCard req={pendingKeyRequest} onProvide={provideKey} onSkip={skipKeyRequest} />}
      {pendingCapability && <CapabilityCard req={pendingCapability} onResolve={resolveCapability} />}
      {auth && (!auth.authenticated || auth.needs_reauth) && onReconnect && (
        <div className="auth-reconnect">
          <span className="auth-reconnect-msg">
            <IconAlert size={14} />
            {auth.authenticated
              ? 'Claude sign-in needs attention — the agent can’t run until you reconnect.'
              : 'Connect your Anthropic account to use the agent.'}
          </span>
          <button type="button" className="claude-btn claude-btn-sm" onClick={onReconnect}>
            <ClaudeLogo size={14} /> {auth.authenticated ? 'Reconnect' : 'Sign in with Claude'}
          </button>
        </div>
      )}
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
          : <button type="submit" className="btn-primary" disabled={disabled || !input.trim()}>Send</button>}
      </form>
      <div className="composer-bar">
        <select
          className="model-select"
          value={model}
          onChange={e => setModel?.(e.target.value)}
          title="Agent model (applies to your next message)"
        >
          {AGENT_MODELS.map(m => (
            <option key={m.id} value={m.id}>
              {m.label}{m.recommended ? ' · Recommended' : ''}
            </option>
          ))}
        </select>
      </div>
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
      <div className="rp-label"><IconMovie size={13} /> Rendering…</div>
      <div className="rp-bar"><div className="rp-fill" style={{ transform: `scaleX(${pct / 100})` }} /></div>
      <div className="rp-pct">{Math.round(pct)}%</div>
    </div>
  )
}

// ─── Question Card (agent asked a clarifying question) ──────────────────────────

function QuestionCard({ q, onAnswer }) {
  const [custom, setCustom] = useState('')
  const send = () => { const v = custom.trim(); if (v) onAnswer(v) }
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
      <div className="q-custom-row">
        <input
          className="q-custom-input"
          type="text"
          placeholder="Or type your own answer…"
          value={custom}
          onChange={e => setCustom(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); send() } }}
        />
        <button type="button" className="q-custom-send" onClick={send} disabled={!custom.trim()}>Send</button>
      </div>
    </div>
  )
}

// ─── API-key Card (agent needs a missing BYOK key) ──────────────────────────────
// A secure, in-chat prompt: the user pastes the key, it's saved to their BYOK .env, and the
// agent's blocked tool is unblocked to retry. "Continue without" declines. The key is masked
// by default (reveal toggle) and posted straight to the local backend — never to the model.

function ApiKeyCard({ req, onProvide, onSkip }) {
  const [value, setValue] = useState('')
  const [reveal, setReveal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const provider = req.provider || req.label || req.env_var

  async function save() {
    const v = value.trim()
    if (!v || busy) return
    setBusy(true); setErr(null)
    try { await onProvide(v) }
    catch (e) { setErr(String(e.message || e)); setBusy(false) }  // keep the card open to retry
  }
  async function skip() {
    if (busy) return
    setBusy(true)
    try { await onSkip() } finally { setBusy(false) }
  }

  return (
    <div className="apikey-card">
      <div className="ak-header"><IconKey size={14} /> {provider} key needed</div>
      <div className="ak-reason">
        {req.reason
          ? `The agent needs your ${provider} API key ${req.reason}.`
          : `The agent needs your ${provider} API key to continue.`}
        {req.description && <span className="ak-desc"> {req.description}</span>}
      </div>
      <div className="ak-input-row">
        <input
          className="ak-input"
          type={reveal ? 'text' : 'password'}
          placeholder={req.env_var}
          value={value}
          autoFocus
          spellCheck={false}
          autoComplete="off"
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); save() } }}
          disabled={busy}
        />
        <button type="button" className="ak-reveal" onClick={() => setReveal(r => !r)}
          title={reveal ? 'Hide' : 'Show'} disabled={busy}>
          {reveal ? <IconEyeOff size={15} /> : <IconEye size={15} />}
        </button>
      </div>
      {err && <div className="ak-err"><IconAlert size={12} /> {err}</div>}
      <div className="ak-actions">
        <button className="ak-save" onClick={save} disabled={busy || !value.trim()}>
          {busy ? 'Saving…' : 'Save & continue'}
        </button>
        <button className="ak-skip" onClick={skip} disabled={busy}>Continue without</button>
      </div>
      <div className="ak-note">Stored locally in your BYOK keys ({req.env_var}) — never sent anywhere but {provider}.</div>
    </div>
  )
}

// ─── Capability Card (agent needs a missing LOCAL pack) ─────────────────────────
// The agent hit a tool whose on-device deps aren't installed. Offer a one-time local install
// (streamed via the shared CapabilityInstall) or "Continue without". On a completed install the
// agent's blocked tool is unblocked to retry; declining tells it to skip that capability.

function CapabilityCard({ req, onResolve }) {
  return (
    <div className="apikey-card">
      <div className="ak-header">Install {req.label || req.pack}?</div>
      <div className="ak-reason">
        The agent needs this on-device capability{req.reason ? ` ${req.reason}` : ' to continue'}.
        {' '}It downloads and installs locally, one time.
      </div>
      <CapabilityInstall
        pack={req.pack}
        label={req.label || req.pack}
        sizeMb={req.size_mb}
        onInstalled={() => onResolve(true)}
      />
      <div className="ak-actions">
        <button className="ak-skip" onClick={() => onResolve(false)}>Continue without</button>
      </div>
      <div className="ak-note">Installed into your local runtime — nothing leaves your Mac.</div>
    </div>
  )
}

// ─── Message ─────────────────────────────────────────────────────────────────

function Message({ m, toolResults, showCost }) {
  if (m.role === 'user') return <div className="msg user">{m.text}</div>
  if (m.role === 'error') return <div className="msg error">⚠ {m.text}</div>
  if (m.role === 'note') return <div className="msg note">{m.text}</div>
  if (m.role === 'result') {
    return (
      <div className="msg result">
        {m.is_error ? '⚠ Turn ended — your next message resumes this session with its context.' : 'Turn complete.'}
        {showCost && m.total_cost_usd != null && <span className="cost"> ${m.total_cost_usd.toFixed(3)}</span>}
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
  if (item.kind === 'thinking') return <span className="activity-chip thinking"><IconBrain size={13} /> thinking…</span>

  const Icon = TOOL_ICON[item.name] || IconTool
  const hasResult = result != null
  const resultErr = hasResult && result.is_error
  return (
    <div className={`tool-block ${open ? 'open' : ''}`}>
      <button className={`activity-chip tool clickable ${resultErr ? 'tool-err' : ''}`} onClick={() => setOpen(o => !o)}>
        <span className="tc-caret"><IconChevron size={11} /></span>
        <span className="tc-icon"><Icon size={13} /></span>
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
