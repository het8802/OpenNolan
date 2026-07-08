// useAgentChat — all agent-chat state + handlers for a single project, in ONE hook.
//
// The pipeline window (App) and the editor (Studio) both render a ChatPanel, but they must
// share a single conversation — not two diverging copies. So App owns one instance of this
// hook and passes the returned bundle down to Studio. Threads are persisted to disk
// (debounced) and revived when the project changes, so disk stays the source of truth.

import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api.js'
import { isRenderCommand, AGENT_MODELS, DEFAULT_MODEL } from './chatUtils.js'

const MODEL_KEY = 'st.agentModel.v1'   // remembered agent-model choice (a global preference)
const VALID_MODEL_IDS = new Set(AGENT_MODELS.map(m => m.id))

function loadModel() {
  try {
    const saved = localStorage.getItem(MODEL_KEY)
    if (saved && VALID_MODEL_IDS.has(saved)) return saved
  } catch { /* localStorage unavailable */ }
  return DEFAULT_MODEL
}

export function useAgentChat(projectId, { onError, onAuthError } = {}) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [pendingConfirm, setPendingConfirm] = useState(null)
  const [pendingQuestion, setPendingQuestion] = useState(null)
  const [pendingKeyRequest, setPendingKeyRequest] = useState(null) // agent asked for a missing API key
  const [pendingCapability, setPendingCapability] = useState(null) // agent asked to install a local pack
  const [renderingStage, setRenderingStage] = useState(null) // tool_use id of in-flight render
  const [toolResults, setToolResults] = useState({})          // tool_use_id -> result, for expansion
  const [threads, setThreads] = useState([])                  // chat threads for the project
  const [activeThread, setActiveThread] = useState(null)      // current thread id
  const [model, setModelState] = useState(loadModel)          // UI-selected agent model

  // Remember the model choice globally (survives reloads + project switches).
  const setModel = useCallback((id) => {
    setModelState(id)
    try { localStorage.setItem(MODEL_KEY, id) } catch { /* best effort */ }
  }, [])

  const messagesRef = useRef([])      // latest messages, for thread persistence
  const sessionIdRef = useRef(null)   // latest agent session_id
  const resolvingCapRef = useRef(false) // guards resolveCapability against a double-fire (install-done + decline click)
  const abortRef = useRef(null)       // aborts the in-flight chat stream (Stop)
  const onErrorRef = useRef(onError)  // decouple from a non-memoized onError so handlers stay stable
  const onAuthErrorRef = useRef(onAuthError)
  useEffect(() => { onErrorRef.current = onError })
  useEffect(() => { onAuthErrorRef.current = onAuthError })
  useEffect(() => { messagesRef.current = messages }, [messages])

  const showError = useCallback((e) => { onErrorRef.current?.(e) }, [])

  const clearChat = useCallback(() => {
    setMessages([])
    setPendingConfirm(null)
    setPendingQuestion(null)
    setPendingKeyRequest(null)
    setPendingCapability(null)
    resolvingCapRef.current = false
    setRenderingStage(null)
    setToolResults({})
    setInput('')
    sessionIdRef.current = null
  }, [])

  // '+' new chat: start a fresh thread (created lazily on first message).
  const newChat = useCallback(() => {
    clearChat()
    setActiveThread(null)
  }, [clearChat])

  const refreshThreads = useCallback(() => {
    if (!projectId) return Promise.resolve()
    return api.listThreads(projectId).then(d => setThreads(d.threads || [])).catch(() => {})
  }, [projectId])

  const loadThread = useCallback(async (tid) => {
    if (!projectId || !tid) return
    try {
      const rec = await api.getThread(projectId, tid)
      clearChat()
      setMessages(rec.messages || [])
      sessionIdRef.current = rec.session_id || null
      setActiveThread(tid)
    } catch (e) { showError(e) }
  }, [projectId, clearChat, showError])

  const deriveTitle = useCallback((msgs) => {
    const firstUser = (msgs || []).find(m => m.role === 'user')
    const t = (firstUser?.text || 'New chat').trim().replace(/\s+/g, ' ')
    return t.length > 48 ? t.slice(0, 48) + '…' : t
  }, [])

  const persistThread = useCallback((tid) => {
    if (!projectId || !tid) return
    api.saveThread(projectId, tid, {
      messages: messagesRef.current,
      session_id: sessionIdRef.current,
      title: deriveTitle(messagesRef.current),
    }).then(() => refreshThreads()).catch(() => {})
  }, [projectId, deriveTitle, refreshThreads])

  // Persist the active thread continuously (debounced), not just at turn end — so reloading
  // mid-turn (a full pipeline can run for minutes) keeps the chat.
  useEffect(() => {
    if (!projectId || !activeThread || messages.length === 0) return
    const t = setTimeout(() => persistThread(activeThread), 700)
    return () => clearTimeout(t)
  }, [messages, projectId, activeThread, persistThread])

  const send = useCallback(async (text) => {
    const message = (text || input).trim()
    if (!message || !projectId || busy) return
    setInput('')
    setMessages(m => [...m, { role: 'user', text: message }])
    setBusy(true)
    // Ensure a thread exists to persist this conversation into.
    let tid = activeThread
    if (!tid) {
      try {
        const rec = await api.createThread(projectId, deriveTitle([{ role: 'user', text: message }]))
        tid = rec.thread_id
        setActiveThread(tid)
      } catch { /* persistence is best-effort; continue the chat regardless */ }
    }
    const controller = new AbortController()
    abortRef.current = controller
    try {
      for await (const evt of api.chatStream(projectId, message, tid, controller.signal, model)) {
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
            // Finalize the last assistant_stream -> assistant (KEEP its text), then append the result line.
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
        } else if (evt.type === 'api_key_request') {
          setPendingKeyRequest(evt)
        } else if (evt.type === 'capability_request') {
          setPendingCapability(evt)
        } else if (evt.type === 'auth_error') {
          // Credential problem (expired/revoked token, rejected key). Surface it AND nudge the
          // app to re-check auth so the reconnect box appears above the composer.
          setMessages(m => [...m, { role: 'error', text: evt.detail || 'Claude authentication failed — reconnect your account.' }])
          onAuthErrorRef.current?.()
        } else if (evt.type === 'error') {
          setMessages(m => [...m, { role: 'error', text: evt.detail }])
        }
      }
    } catch (e) {
      if (e.name === 'AbortError' || controller.signal.aborted) {
        setRenderingStage(null)
        setMessages(m => [...m, { role: 'note', text: '■ Stopped. Your next message resumes this session with its context.' }])
      } else {
        const text = String(e.message || e)
        setMessages(m => [...m, { role: 'error', text }])
        // A 503 "auth not configured" (or any auth-shaped failure) at request start never reaches
        // the SSE stream — re-check auth so the reconnect box surfaces promptly instead of lagging
        // the 20s poll.
        if (/auth|token|api key|unauthor|401|403|credit|billing/i.test(text)) onAuthErrorRef.current?.()
      }
    } finally {
      abortRef.current = null
      setBusy(false)
      // Persist the conversation (messages + session_id) so the thread is revivable.
      if (tid) setTimeout(() => persistThread(tid), 0)
    }
  }, [input, projectId, busy, renderingStage, activeThread, model, deriveTitle, persistThread])

  const stop = useCallback(async () => {
    if (!projectId) return
    // Interrupt the agent server-side (context preserved), then stop reading.
    try { await api.stopAgent(projectId) } catch { /* best effort */ }
    abortRef.current?.abort()
  }, [projectId])

  const resolveConfirm = useCallback(async (approved) => {
    if (!pendingConfirm || !projectId) return
    try { await api.confirmTool(projectId, pendingConfirm.confirm_id, approved) }
    catch (e) { showError(e) }
    finally { setPendingConfirm(null) }
  }, [pendingConfirm, projectId, showError])

  const answerQuestion = useCallback(async (answer) => {
    if (!pendingQuestion || !projectId) return
    setMessages(m => [...m, { role: 'user', text: answer }])  // show the choice in the chat
    try { await api.answerQuestion(projectId, pendingQuestion.question_id, answer) }
    catch (e) { showError(e) }
    finally { setPendingQuestion(null) }
  }, [pendingQuestion, projectId, showError])

  // Provide the API key the agent asked for: save it to BYOK, then unblock the agent's tool.
  // The card stays open (and rethrows) if the save fails, so the user can correct the value.
  const provideKey = useCallback(async (value) => {
    if (!pendingKeyRequest || !projectId) return
    const kr = pendingKeyRequest
    await api.provideKey(projectId, {
      key_request_id: kr.key_request_id, env_var: kr.env_var, value,
    })
    setMessages(m => [...m, { role: 'note', text: `Saved ${kr.label || kr.env_var} — retrying.` }])
    setPendingKeyRequest(null)
  }, [pendingKeyRequest, projectId])

  const skipKeyRequest = useCallback(async () => {
    if (!pendingKeyRequest || !projectId) return
    const kr = pendingKeyRequest
    try { await api.provideKey(projectId, { key_request_id: kr.key_request_id, env_var: kr.env_var, skipped: true }) }
    catch (e) { showError(e) }
    finally {
      setMessages(m => [...m, { role: 'note', text: `Continuing without ${kr.label || kr.env_var}.` }])
      setPendingKeyRequest(null)
    }
  }, [pendingKeyRequest, projectId, showError])

  // Resolve the agent's request_capability prompt. The card already streamed the install (or the
  // user declined); this just unblocks the waiting tool — installed=true → agent retries.
  const resolveCapability = useCallback(async (installed) => {
    if (!pendingCapability || !projectId || resolvingCapRef.current) return
    resolvingCapRef.current = true  // set synchronously so a racing decline-click + install-done resolve only once
    const cr = pendingCapability
    try { await api.provideCapability(projectId, cr.cap_request_id, installed) }
    catch (e) { showError(e) }
    finally {
      setMessages(m => [...m, {
        role: 'note',
        text: installed ? `Installed ${cr.label || cr.pack} — retrying.`
                        : `Continuing without ${cr.label || cr.pack}.`,
      }])
      setPendingCapability(null)
      resolvingCapRef.current = false
    }
  }, [pendingCapability, projectId, showError])

  // Revive the project's most recent conversation when the project changes; reset when none.
  // This consolidates what App's openProject used to do, so both views land in the same chat.
  useEffect(() => {
    if (!projectId) { setThreads([]); clearChat(); setActiveThread(null); return }
    let alive = true
    clearChat(); setActiveThread(null)
    api.listThreads(projectId)
      .then(d => {
        if (!alive) return
        setThreads(d.threads || [])
        const latest = (d.threads || []).find(t => (t.message_count || 0) > 0) // newest-updated first
        if (latest) loadThread(latest.thread_id)
      })
      .catch(() => { if (alive) setThreads([]) })
    return () => { alive = false }
  }, [projectId, clearChat, loadThread])

  // Never leak an in-flight stream on unmount.
  useEffect(() => () => { abortRef.current?.abort() }, [])

  return {
    messages, input, setInput, busy,
    pendingConfirm, pendingQuestion, pendingKeyRequest, pendingCapability, renderingStage, toolResults,
    threads, activeThread, model, setModel,
    send, stop, newChat, loadThread, resolveConfirm, answerQuestion, provideKey, skipKeyRequest,
    resolveCapability,
  }
}
