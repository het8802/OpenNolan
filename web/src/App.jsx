import { useEffect, useRef, useState } from 'react'
import * as api from './api.js'
import { LineChart } from './components/LineChart.jsx'
import { IconKey, IconEye, IconEyeOff, IconCheck, IconX, IconAlert, IconMessage, ClaudeLogo, IconMovie, IconMic, IconStar, IconMusic, IconListDetails } from './components/icons.jsx'
import Studio from './studio/Studio.jsx'
import ChatPanel from './chat/ChatPanel.jsx'
import CapabilitiesModal from './CapabilitiesModal.jsx'
import UpdateBanner from './UpdateBanner.jsx'
import { useAgentChat } from './chat/useAgentChat.js'
import { useAuth } from './auth/useAuth.js'
import ConnectClaudeModal from './auth/ConnectClaudeModal.jsx'

const STATUS_LABEL = {
  pending: 'pending',
  in_progress: 'running',
  awaiting_human: 'needs you',
  completed: 'done',
  failed: 'failed',
  error: 'error',
}

// 'renders' is read-only (not uploadable): the agent's HyperFrames clips from hf/renders,
// served by the backend as `agent_renders` (distinct from the "Final render" in `renders`).
const ASSET_KINDS = ['images', 'video', 'audio', 'music', 'renders']

export default function App() {
  const [pipelines, setPipelines] = useState([])
  const [styles, setStyles] = useState([])                    // available visual style playbooks
  const [projects, setProjects] = useState([])
  const [selected, setSelected] = useState(null)
  const [state, setState] = useState(null)
  const [toast, setToast] = useState(null)
  const [uploadTick, setUploadTick] = useState(0)             // bump to refresh asset listing
  const [editing, setEditing] = useState(false)               // manual editor open (full-screen)
  const [showConnect, setShowConnect] = useState(false)        // "Sign in with Claude" modal open

  // Anthropic account auth — one instance for the whole app (sign-in CTA, top-right re-auth,
  // in-chat reconnect box). The backend is the source of truth; refresh() re-checks on demand.
  const auth = useAuth()

  // All agent-chat state + handlers live in one hook so the pipeline window and the editor
  // share a single conversation (revived from disk whenever `selected` changes). A turn-level auth
  // failure calls onAuthError → re-check auth so the reconnect surfaces immediately.
  const chat = useAgentChat(selected, { onError: showError, onAuthError: () => auth.refresh() })

  useEffect(() => {
    api.getPipelines().then(d => setPipelines(d.pipelines || [])).catch(showError)
    api.getStyles().then(d => setStyles(d.styles || [])).catch(showError)
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

  // Open a project; the chat hook revives its most recent conversation from disk.
  function openProject(id) {
    setSelected(id)
  }

  // One connect/reconnect modal, shared across every view (dashboard, editor, project).
  const connectModal = showConnect && (
    <ConnectClaudeModal
      initialStatus={auth.status}
      onClose={() => setShowConnect(false)}
      onConnected={() => { setShowConnect(false); auth.refresh(); showOk('Connected to Claude') }}
    />
  )

  if (!selected) {
    return (
      <div className="app">
        <Dashboard
          pipelines={pipelines}
          styles={styles}
          projects={projects}
          onOpen={openProject}
          auth={auth.status}
          onConnect={() => setShowConnect(true)}
          onCreate={async (name, pipeline, style) => {
            const m = await api.createProject(name, pipeline, style)
            await refreshProjects()
            setSelected(m.project_id)   // chat hook starts a fresh conversation for the new project
            showOk(`Created "${m.name}"`)
          }}
        />
        {connectModal}
        {toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}
        <UpdateBanner />
      </div>
    )
  }

  if (editing) {
    return (
      <div className="app">
        <Studio projectId={selected} state={state} onClose={() => setEditing(false)} chat={chat}
          auth={auth.status} onReconnect={() => setShowConnect(true)} />
        {connectModal}
        {toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}
        <UpdateBanner />
      </div>
    )
  }

  return (
    <div className="app">
      <ProjectBar
        state={state} projects={projects} selected={selected}
        onEdit={() => setEditing(true)}
        onBack={() => { setSelected(null); setEditing(false) }}
      />
      <main className="grid">
        <ChatPanel chat={chat} disabled={!selected}
          auth={auth.status} onReconnect={() => setShowConnect(true)} />
        <WorkPanel state={state} selected={selected} />
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
      {connectModal}
      {toast && <div className={`toast ${toast.kind}`}>{toast.text}</div>}
      <UpdateBanner />
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

function Dashboard({ pipelines, styles = [], projects, onOpen, onCreate, auth, onConnect }) {
  const [creating, setCreating] = useState(false)
  const [showEnv, setShowEnv] = useState(false)
  const [showCaps, setShowCaps] = useState(false)
  const [showFeedback, setShowFeedback] = useState(false)
  const sorted = [...projects].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
  // auth === null while the first status check is in flight — don't flash the CTA before we know.
  const connected = auth?.authenticated
  const needsReauth = !!auth?.needs_reauth
  return (
    <div className="dashboard">
      <header className="dash-header">
        <div className="brand"><span className="dot" /> OpenNolan <span className="muted">· Mission Control</span></div>
        <div className="dash-sub">{projects.length} project{projects.length === 1 ? '' : 's'}</div>
        {auth && connected && (
          <button
            className={`claude-btn claude-btn-sm reauth-btn${needsReauth ? ' warn' : ''}`}
            onClick={onConnect}
            title={needsReauth ? 'Your Claude connection needs attention' : 'Re-authenticate with Claude'}>
            <ClaudeLogo size={15} /> Re-authenticate with Claude
          </button>
        )}
        <button className="byok-btn" onClick={() => setShowCaps(true)} title="Manage optional on-device capabilities">
          Capabilities
        </button>
        <button className="byok-btn" onClick={() => setShowFeedback(true)} title="Send feedback or report a problem">
          <IconMessage /> Feedback
        </button>
        <button className="byok-btn" onClick={() => setShowEnv(true)} title="Manage your API keys (.env)">
          <IconKey /> BYOK
        </button>
      </header>
      {auth && !connected && (
        <div className="auth-hero">
          <div className="auth-hero-text">
            <div className="auth-hero-title">Sign in with Claude to get started</div>
            <div className="auth-hero-sub">
              OpenNolan's AI agent uses your own Anthropic account to build and edit your videos.
              Connect it once — your credentials stay on this Mac.
            </div>
          </div>
          <button className="claude-btn claude-btn-lg" onClick={onConnect}>
            <ClaudeLogo size={20} /> Authenticate with Claude
          </button>
        </div>
      )}
      {auth && connected && needsReauth && (
        <div className="auth-warn-bar">
          <span><IconAlert size={15} /> Your Claude connection needs attention — the agent may not be able to run.</span>
          <button className="linkish" onClick={onConnect}>Re-authenticate</button>
        </div>
      )}
      <div className="dash-grid">
        <button className="tile tile-new" onClick={() => setCreating(true)}>
          <span className="tile-plus">＋</span>
          <span className="tile-new-label">New project</span>
        </button>
        {sorted.map(p => {
          const h = hueOf(p.pipeline_type || p.project_id)
          const cover = `linear-gradient(135deg, hsl(${h} 58% 63%), hsl(${(h + 42) % 360} 62% 72%))`
          const mono = (p.name || p.project_id || '?').trim().charAt(0).toUpperCase()
          return (
            <button key={p.project_id} className="tile tile-project" onClick={() => onOpen(p.project_id)}>
              <span className="tile-cover" style={{ background: cover }}>
                <span className="tile-mono">{mono}</span>
              </span>
              <span className="tile-body">
                <span className="tile-name">{p.name}</span>
                <span className="tile-meta">
                  {p.pipeline_type
                    ? <span className="tile-type">{p.pipeline_type}</span>
                    : <span className="tile-type unknown">{p.legacy ? 'unknown type' : 'agent picks'}</span>}
                  {p.style && <span className="tile-style" title={`style: ${p.style}`}>{p.style}</span>}
                  {p.legacy && <span className="tile-legacy">existing</span>}
                </span>
                {p.created_at && <span className="tile-date">{fmtDate(p.created_at)}</span>}
              </span>
            </button>
          )
        })}
      </div>
      {creating && (
        <CreateModal pipelines={pipelines} styles={styles} onClose={() => setCreating(false)} onCreate={onCreate} />
      )}
      {showEnv && <EnvModal onClose={() => setShowEnv(false)} />}
      {showCaps && <CapabilitiesModal onClose={() => setShowCaps(false)} />}
      {showFeedback && <FeedbackModal onClose={() => setShowFeedback(false)} />}
    </div>
  )
}

// ─── Feedback + privacy ─────────────────────────────────────────────────────────
// One place for "tell us something" (bug/feature/other → /api/feedback) and the anonymous-usage
// opt-out. Co-located on purpose: where we ask for feedback we also disclose telemetry + let the
// user turn it off. Feedback is always stored locally + emitted to PostHog; email is best-effort.
function FeedbackModal({ onClose }) {
  const [kind, setKind] = useState('bug')
  const [message, setMessage] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [sent, setSent] = useState(false)

  const [analyticsDisabled, setAnalyticsDisabled] = useState(null)  // null = still loading
  useEffect(() => {
    api.getAnalytics().then(a => setAnalyticsDisabled(!!a.disabled)).catch(() => {})
  }, [])

  async function toggleAnalytics() {
    const next = !analyticsDisabled
    setAnalyticsDisabled(next)  // optimistic
    try { await api.setAnalytics(next) }
    catch { setAnalyticsDisabled(!next) }  // revert on failure
  }

  async function submit(e) {
    e.preventDefault()
    if (!message.trim() || busy) return
    setBusy(true); setErr(null)
    try {
      await api.sendFeedback({ kind, message: message.trim(), email: email.trim() || null })
      setSent(true)
    } catch (e) {
      setErr(String(e.message || e)); setBusy(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal feedback-modal" onClick={e => e.stopPropagation()}>
        <div className="env-head">
          <div className="env-head-text">
            <h3>Feedback</h3>
            <div className="env-sub">Found a bug or have an idea? Tell us — it goes straight to the developer.</div>
          </div>
          <button className="am-close" onClick={onClose} title="Close" aria-label="Close"><IconX /></button>
        </div>

        {sent ? (
          <div className="feedback-sent">
            <div className="feedback-sent-msg"><IconCheck /><span>Thanks — your {kind === 'bug' ? 'report' : 'note'} was sent.</span></div>
            <div className="modal-actions"><button onClick={onClose}>Close</button></div>
          </div>
        ) : (
          <form className="feedback-body" onSubmit={submit}>
            {err && <div className="modal-err">⚠ {err}</div>}
            <label className="modal-field">
              Type
              <select value={kind} onChange={e => setKind(e.target.value)}>
                <option value="bug">Bug / something broke</option>
                <option value="feature">Feature idea</option>
                <option value="other">Other</option>
              </select>
            </label>
            <label className="modal-field">
              Message
              <textarea rows={5} value={message} maxLength={5000} autoFocus
                placeholder={kind === 'bug' ? 'What happened? What did you expect?' : 'Tell us more…'}
                onChange={e => setMessage(e.target.value)} />
            </label>
            <label className="modal-field">
              Email <span className="modal-hint" style={{ marginTop: 0 }}>(optional — so we can reply)</span>
              <input type="email" value={email} placeholder="you@example.com"
                onChange={e => setEmail(e.target.value)} />
            </label>
            <div className="modal-actions">
              <button type="button" className="modal-cancel" onClick={onClose}>Cancel</button>
              <button type="submit" disabled={busy || !message.trim()}>{busy ? 'Sending…' : 'Send'}</button>
            </div>
          </form>
        )}

        <div className="feedback-privacy">
          <label className="privacy-row">
            <input type="checkbox"
              checked={analyticsDisabled === false}
              disabled={analyticsDisabled === null}
              onChange={toggleAnalytics} />
            <span>
              Share anonymous usage &amp; crash data
              <span className="modal-hint" style={{ marginTop: 0 }}>
                No account, no file contents — an anonymous device id only. Helps us fix crashes.
              </span>
            </span>
          </label>
        </div>
      </div>
    </div>
  )
}

// ─── BYOK panel (populate the local .env, one variable per row) ──────────────────
// Reads the curated variable menu + current values from /api/env (the repo .env) and writes edits
// back. Secrets render as masked inputs with a reveal toggle; blank = leave unset. The whole draft
// is sent on Save — the backend writes only what actually changed.

function EnvModal({ onClose }) {
  const [vars, setVars] = useState(null)
  const [draft, setDraft] = useState({})
  const [reveal, setReveal] = useState({})   // key -> show plaintext
  const [path, setPath] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(null)

  useEffect(() => {
    let alive = true
    api.getEnv()
      .then(d => {
        if (!alive) return
        setVars(d.vars); setPath(d.path)
        setDraft(Object.fromEntries(d.vars.map(v => [v.key, v.value || ''])))
      })
      .catch(e => alive && setErr(String(e.message || e)))
    return () => { alive = false }
  }, [])

  const setVal = (key, val) => { setDraft(d => ({ ...d, [key]: val })); setSaved(null) }

  async function save() {
    setBusy(true); setErr(null); setSaved(null)
    try {
      const res = await api.saveEnv(draft)
      setVars(res.vars)
      setDraft(Object.fromEntries(res.vars.map(v => [v.key, v.value || ''])))
      const n = (res.changed || []).length
      setSaved(n ? `Saved ${n} variable${n === 1 ? '' : 's'} to .env` : 'No changes')
    } catch (e) { setErr(String(e.message || e)) }
    finally { setBusy(false) }
  }

  // Group, preserving the first-seen order from the API.
  const groups = []
  const byGroup = {}
  for (const v of (vars || [])) {
    if (!byGroup[v.group]) { byGroup[v.group] = []; groups.push(v.group) }
    byGroup[v.group].push(v)
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal env-modal" onClick={e => e.stopPropagation()}>
        <div className="env-head">
          <div className="env-head-text">
            <h3><IconKey /> Bring your own keys</h3>
            <div className="env-sub">
              Stored in your local <code>.env</code>{path ? ` · ${path}` : ''}. Fill in what you have — leave the rest blank.
            </div>
          </div>
          <button className="am-close" onClick={onClose} title="Close" aria-label="Close"><IconX /></button>
        </div>
        <div className="env-body">
          {err && <div className="modal-err">⚠ {err}</div>}
          {!vars && !err && <p className="empty">Loading…</p>}
          {vars && groups.map(g => (
            <div key={g} className="env-group">
              <div className="env-group-head">{g}</div>
              {byGroup[g].map(v => (
                <div key={v.key} className="env-row">
                  <div className="env-row-label">
                    <span className="env-name">{v.label}</span>
                    <code className="env-key">{v.key}</code>
                    {v.description && <span className="env-desc">{v.description}</span>}
                    {v.url && (
                      <a className="env-getkey" href={v.url} target="_blank" rel="noreferrer noopener">
                        Get key ↗
                      </a>
                    )}
                  </div>
                  <div className="env-input">
                    <input
                      type={v.secret && !reveal[v.key] ? 'password' : 'text'}
                      value={draft[v.key] ?? ''}
                      placeholder="not set"
                      autoComplete="off" spellCheck={false}
                      onChange={e => setVal(v.key, e.target.value)} />
                    {v.secret && (
                      <button type="button" className="env-eye"
                        title={reveal[v.key] ? 'Hide' : 'Show'} aria-label={reveal[v.key] ? 'Hide value' : 'Show value'}
                        onClick={() => setReveal(r => ({ ...r, [v.key]: !r[v.key] }))}>
                        {reveal[v.key] ? <IconEyeOff /> : <IconEye />}
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="env-actions">
          {saved && <span className="env-saved"><IconCheck /> {saved}</span>}
          <button className="modal-cancel" onClick={onClose}>Close</button>
          <button onClick={save} disabled={busy || !vars}>{busy ? 'Saving…' : 'Save to .env'}</button>
        </div>
      </div>
    </div>
  )
}

function CreateModal({ pipelines, styles = [], onClose, onCreate }) {
  // Packaged app ships a single pipeline → preselect it (no "agent decides").
  const single = pipelines.length === 1
  const [name, setName] = useState('')
  const [pipeline, setPipeline] = useState(single ? pipelines[0].name : '')   // '' = let the agent decide
  const [style, setStyle] = useState('')                                       // '' = let the agent decide
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  // Group styles so user-created ones (from the Style Studio) sit in their own section.
  const builtinStyles = styles.filter(s => !s.user)
  const userStyles = styles.filter(s => s.user)
  const styleName = s => s.label || s.name

  async function submit(e) {
    e.preventDefault()
    if (!name.trim() || busy) return
    setBusy(true); setErr(null)
    try { await onCreate(name.trim(), pipeline, style); onClose() }
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
          <select value={pipeline} onChange={e => setPipeline(e.target.value)} disabled={single}>
            {!single && <option value="">Let the agent decide</option>}
            {pipelines.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
          </select>
        </label>
        <label className="modal-field">
          <span>Style</span>
          <select value={style} onChange={e => setStyle(e.target.value)}>
            <option value="">Let the agent decide</option>
            {userStyles.length > 0 && (
              <optgroup label="Your styles">
                {userStyles.map(s => <option key={s.name} value={s.name}>{styleName(s)}</option>)}
              </optgroup>
            )}
            <optgroup label="Built-in styles">
              {builtinStyles.map(s => <option key={s.name} value={s.name}>{styleName(s)}</option>)}
            </optgroup>
          </select>
        </label>
        <div className="modal-hint">
          {style ? `Visuals locked to the “${style}” style.`
                 : 'The agent picks a visual style to match your request — or make your own with “Create New Style”.'}
        </div>
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

function ProjectBar({ state, projects, selected, onBack, onEdit }) {
  const p = projects.find(x => x.project_id === selected)
  const name = state?.name || p?.name || selected
  const type = state?.pipeline_type || p?.pipeline_type || ''
  return (
    <header className="header project-bar">
      <button className="back-btn" onClick={onBack} title="Back to all projects">← Projects</button>
      <div className="pb-title">
        <span className="dot" />
        <strong>{name}</strong>
        {type && <span className="pb-type">{type}</span>}
      </div>
      <div className="runtimes">
        {onEdit && <button className="editor-open-btn" onClick={onEdit} title="Hand-edit this project's timeline">✎ Edit</button>}
      </div>
    </header>
  )
}

// ─── Work Panel (Pipeline | Activity tabs) ──────────────────────────────────────

const ACTIVITY_GROUPS = [
  ['project', 'Project files'],
  ['skill', 'Skills'],
  ['pipeline_def', 'Pipeline def'],
  ['tool', 'Tools'],
  ['web', 'Web'],
  ['framework', 'Framework'],
  ['other', 'Other'],
]
const OP_LABEL = { read: 'read', write: 'wrote', edit: 'edited', exec: 'ran', search: 'searched', fetch: 'fetched', skill: 'skill', tool: 'tool', todo: 'todo' }

function prettyKey(k) {
  return String(k).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}
function fmtBytes(n) {
  if (!n) return ''
  if (n < 1024) return n + ' B'
  if (n < 1048576) return (n / 1024).toFixed(0) + ' KB'
  return (n / 1048576).toFixed(1) + ' MB'
}
function fmtDateTime(s) {
  try {
    const d = new Date(s)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch { return '' }
}
function fmtTime(s) {
  try {
    const d = new Date(s)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch { return '' }
}

function WorkPanel({ state, selected }) {
  const [tab, setTab] = useState('pipeline')
  const [artifacts, setArtifacts] = useState(null)
  const [activity, setActivity] = useState(null)
  const [openArtifact, setOpenArtifact] = useState(null)  // artifact key to inspect

  // Artifacts drive the Pipeline tab; poll always so the spine stays live.
  useEffect(() => {
    if (!selected) { setArtifacts(null); return }
    let alive = true
    const tick = () => api.getArtifacts(selected).then(d => alive && setArtifacts(d)).catch(() => {})
    tick()
    const id = setInterval(tick, 2000)
    return () => { alive = false; clearInterval(id) }
  }, [selected])

  // Activity is heavier; only poll it while its tab is showing.
  useEffect(() => {
    if (!selected || tab !== 'activity') return
    let alive = true
    const tick = () => api.getActivity(selected).then(d => alive && setActivity(d)).catch(() => {})
    tick()
    const id = setInterval(tick, 2000)
    return () => { alive = false; clearInterval(id) }
  }, [selected, tab])

  return (
    <section className="panel work">
      <div className="work-tabs">
        <button className={`work-tab ${tab === 'pipeline' ? 'active' : ''}`} onClick={() => setTab('pipeline')}>Pipeline</button>
        <button className={`work-tab ${tab === 'activity' ? 'active' : ''}`} onClick={() => setTab('activity')}>Activity</button>
      </div>
      {!selected && <p className="empty">No project selected.</p>}
      {selected && tab === 'pipeline' && (
        <PipelineTab state={state} artifacts={artifacts} onOpen={setOpenArtifact} />
      )}
      {selected && tab === 'activity' && (
        <ActivityTab activity={activity} artifacts={artifacts} state={state} onOpen={setOpenArtifact} />
      )}
      {openArtifact && (
        <ArtifactModal selected={selected} artKey={openArtifact} onClose={() => setOpenArtifact(null)} />
      )}
    </section>
  )
}

// ─── Pipeline tab (stepper, each stage expands to its artifacts) ────────────────

function PipelineTab({ state, artifacts, onOpen }) {
  // Prefer the artifact manifest's stages (status + produced artifacts + nested
  // data); fall back to /state's stages before the manifest loads.
  const stages = artifacts?.stages || state?.stages || []
  const name = state?.name || artifacts?.project_id
  const ptype = state?.pipeline_type || artifacts?.pipeline_type
  const extras = artifacts?.extra_artifacts || []
  const dlog = artifacts?.decision_log

  return (
    <div className="work-scroll">
      <div className="pl-head">
        <strong>{name}</strong>
        <span className="muted"> · {ptype || 'agent picks'}</span>
      </div>
      <ol className="stepper">
        {stages.map(s => <StageRow key={s.stage} s={s} onOpen={onOpen} />)}
      </ol>
      {stages.length === 0 && <p className="empty">No stages run yet.</p>}

      {(extras.length > 0 || dlog?.present) && (
        <div className="pl-extra">
          <div className="pl-extra-label">Cross-cutting</div>
          {dlog?.present && (
            <button className="art-chip" onClick={() => onOpen('decision_log')}>
              <span className="art-icon"><IconListDetails size={14} /></span>
              <span className="art-name">Decision log</span>
              <span className="art-size">{dlog.decision_count} decision{dlog.decision_count === 1 ? '' : 's'}</span>
            </button>
          )}
          {extras.map(a => (
            <button key={a.key} className="art-chip" onClick={() => onOpen(a.key)}>
              <span className="art-icon">{'{}'}</span>
              <span className="art-name">{prettyKey(a.key)}</span>
              <span className="art-size">{fmtBytes(a.size_bytes)}</span>
            </button>
          ))}
        </div>
      )}
      {state?.next_stage && <div className="next">Next: <strong>{state.next_stage}</strong></div>}
    </div>
  )
}

function StageRow({ s, onOpen }) {
  const [open, setOpen] = useState(false)
  const arts = s.artifacts || []
  const hasDetail = arts.length > 0 || s.review || s.style_playbook || s.timestamp || s.human_approval_required
  return (
    <li className={`step ${s.status} ${open ? 'open' : ''}`}>
      <button className="step-head" onClick={() => hasDetail && setOpen(o => !o)}>
        <span className="bullet" />
        <span className="step-name">{s.stage}</span>
        {arts.length > 0 && <span className="step-count">{arts.length}</span>}
        <span className="step-status">{STATUS_LABEL[s.status] || s.status}</span>
        {s.status === 'in_progress' && <span className="pulse" />}
        {hasDetail && <span className="step-caret">{open ? '▾' : '▸'}</span>}
      </button>
      {open && (
        <div className="step-detail">
          {s.timestamp && <div className="sd-meta">Updated {fmtDateTime(s.timestamp)}</div>}
          {s.human_approval_required && (
            <div className="sd-meta">{s.human_approved ? '✓ approved' : '⏳ awaiting approval'}</div>
          )}
          {s.style_playbook && <div className="sd-meta">Style · <strong>{s.style_playbook}</strong></div>}
          {arts.length > 0 && (
            <div className="sd-arts">
              {arts.map(a => (
                <button key={a.key} className={`art-chip ${a.canonical ? 'canonical' : ''}`} onClick={() => onOpen(a.key)}>
                  <span className="art-icon">{'{}'}</span>
                  <span className="art-name">{prettyKey(a.key)}</span>
                  <span className="art-size">{fmtBytes(a.size_bytes)}</span>
                </button>
              ))}
            </div>
          )}
          {s.review && (
            <div className="sd-review">
              <div className="sd-review-label">review</div>
              <GenericValue value={s.review} />
            </div>
          )}
        </div>
      )}
    </li>
  )
}

// ─── Activity tab (How-it-was-made + Files / Timeline) ──────────────────────────

function ActivityTab({ activity, artifacts, state, onOpen }) {
  const [view, setView] = useState('files')
  const events = activity?.events || []
  const summary = activity?.summary || {}
  return (
    <div className="work-scroll">
      <HowItWasMade summary={summary} artifacts={artifacts} state={state} />
      <div className="act-toggle">
        <button className={view === 'files' ? 'active' : ''} onClick={() => setView('files')}>Files</button>
        <button className={view === 'timeline' ? 'active' : ''} onClick={() => setView('timeline')}>Timeline</button>
      </div>
      {!activity && <p className="empty">Loading activity…</p>}
      {activity && events.length === 0 && (
        <p className="empty">No agent activity recorded yet. Run a turn and the files, skills, and tools the agent touches show up here.</p>
      )}
      {events.length > 0 && (view === 'files'
        ? <FilesList events={events} artifacts={artifacts} onOpen={onOpen} />
        : <TimelineList events={events} />)}
    </div>
  )
}

function HowItWasMade({ summary, artifacts, state }) {
  const pipeline = state?.pipeline_type || artifacts?.pipeline_type
  const runtime = state?.runtime
  const style = (artifacts?.stages || []).map(s => s.style_playbook).find(Boolean)
  const skills = summary?.skills || []
  const tools = summary?.tools || []
  const pdefs = summary?.pipeline_defs || []
  return (
    <div className="hiwm">
      <div className="hiwm-title">How this was made</div>
      <div className="hiwm-rows">
        <HiwmRow label="Pipeline" chips={[pipeline || 'agent picks', ...pdefs.filter(p => p !== pipeline)]} />
        {style && <HiwmRow label="Style" chips={[style]} />}
        {runtime && <HiwmRow label="Runtime" chips={[runtime]} />}
        {skills.length > 0 && <HiwmRow label="Skills" chips={skills} />}
        {tools.length > 0 && <HiwmRow label="Tools" chips={tools} />}
      </div>
    </div>
  )
}
function HiwmRow({ label, chips }) {
  const list = (chips || []).filter(Boolean)
  if (list.length === 0) return null
  return (
    <div className="hiwm-row">
      <span className="hiwm-key">{label}</span>
      <span className="hiwm-chips">{list.map((c, i) => <span key={i} className="hiwm-chip">{c}</span>)}</span>
    </div>
  )
}

function FilesList({ events, artifacts, onOpen }) {
  // Dedup by (category, label); aggregate ops + count + last-touched. The label
  // is the clean server-side display name (tool slug, file basename, skill name).
  const byKey = new Map()
  for (const e of events) {
    const label = e.label || (e.target || '').split('/').pop() || e.target || e.tool
    const key = `${e.category}|${label}`
    const cur = byKey.get(key) || { label, target: e.target, category: e.category, ops: new Set(), count: 0, last: '' }
    cur.ops.add(e.op)
    cur.count += 1
    if ((e.ts || '') > cur.last) cur.last = e.ts || ''
    byKey.set(key, cur)
  }
  // Which targets are inspectable artifacts (open the modal on click).
  const artKeys = new Set()
  ;(artifacts?.stages || []).forEach(s => (s.artifacts || []).forEach(a => artKeys.add(a.key)))
  ;(artifacts?.extra_artifacts || []).forEach(a => artKeys.add(a.key))
  if (artifacts?.decision_log?.present) artKeys.add('decision_log')
  const artKeyFor = (target) => {
    const base = (target || '').split('/').pop().replace(/\.json$/, '')
    return artKeys.has(base) ? base : null
  }

  const grouped = {}
  for (const row of byKey.values()) (grouped[row.category] ||= []).push(row)
  for (const k in grouped) grouped[k].sort((a, b) => (b.last || '').localeCompare(a.last || ''))

  return (
    <div className="files-list">
      {ACTIVITY_GROUPS.map(([cat, label]) => {
        const rows = grouped[cat]
        if (!rows || rows.length === 0) return null
        return (
          <div key={cat} className="files-group">
            <div className="files-group-head">{label} <span className="muted">({rows.length})</span></div>
            {rows.map((r, i) => {
              const ak = artKeyFor(r.target)
              return (
                <div key={i} className={`file-row ${ak ? 'clickable' : ''}`} onClick={ak ? () => onOpen(ak) : undefined} title={r.target}>
                  <span className="file-ops">
                    {[...r.ops].map(op => <span key={op} className={`op-badge ${op}`}>{OP_LABEL[op] || op}</span>)}
                  </span>
                  <span className="file-name">{r.label}</span>
                  {r.count > 1 && <span className="file-count">×{r.count}</span>}
                  {ak && <span className="file-open">view →</span>}
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

function TimelineList({ events }) {
  // Newest first — most recent activity at the top.
  const rows = [...events].reverse()
  return (
    <div className="timeline">
      {rows.map((e, i) => (
        <div key={i} className="tl-row">
          <span className="tl-time">{fmtTime(e.ts)}</span>
          <span className={`op-badge ${e.op}`}>{OP_LABEL[e.op] || e.op}</span>
          <span className="tl-tool">{e.label || e.tool}</span>
          <span className="tl-target" title={e.target}>{e.target}</span>
        </div>
      ))}
    </div>
  )
}

// ─── Artifact detail modal (generic renderer + marquee views + raw toggle) ──────

function ArtifactModal({ selected, artKey, onClose }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [raw, setRaw] = useState(false)

  useEffect(() => {
    let alive = true
    setData(null); setErr(null)
    api.getArtifact(selected, artKey)
      .then(d => alive && setData(d))
      .catch(e => alive && setErr(String(e.message || e)))
    return () => { alive = false }
  }, [selected, artKey])

  const content = data?.content
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal artifact-modal" onClick={e => e.stopPropagation()}>
        <div className="am-head">
          <div className="am-title">
            <h3>{prettyKey(artKey)}</h3>
            {data && (data.stage || data.source) && (
              <span className="am-sub">{data.stage ? `from ${data.stage} · ` : ''}{data.source}</span>
            )}
          </div>
          <div className="am-actions">
            {content != null && (
              <button className="am-raw" onClick={() => setRaw(r => !r)}>{raw ? 'Formatted' : 'Raw JSON'}</button>
            )}
            <button className="am-close" onClick={onClose} title="Close">✕</button>
          </div>
        </div>
        <div className="am-body">
          {err && <div className="modal-err">⚠ {err}</div>}
          {!data && !err && <p className="empty">Loading…</p>}
          {content != null && (raw
            ? <pre className="am-raw-pre">{JSON.stringify(content, null, 2)}</pre>
            : <ArtifactView artKey={artKey} content={content} />)}
        </div>
      </div>
    </div>
  )
}

// Detect a content-signal report by its content SHAPE, not its filename/key, so any
// re-score (content_signal_report_v3.json, or even a hand-written file under any name)
// renders as the interactive chart. The headline_score(number)+sub_scores(object) combo
// is unique to this artifact.
function looksLikeContentSignal(c) {
  return !!c && typeof c === 'object'
    && typeof c.headline_score === 'number'
    && !!c.sub_scores && typeof c.sub_scores === 'object' && !Array.isArray(c.sub_scores)
}

// Return a chart-ready report from either a parsed content_signal_report (scores at
// top level) OR a raw Replicate prediction dump where the scores are nested under
// `output` (e.g. a manual poll saved straight from the API). null if neither.
function contentSignalReport(c) {
  if (looksLikeContentSignal(c)) return c
  if (c && typeof c === 'object' && looksLikeContentSignal(c.output)) {
    return { model: c.model, ...c.output }  // lift model so the chip shows
  }
  return null
}

function ArtifactView({ artKey, content }) {
  if (content == null || typeof content !== 'object') return <GenericValue value={content} />
  if (artKey === 'scene_plan') return <ScenePlanView c={content} />
  if (artKey === 'script') return <ScriptView c={content} />
  if (artKey === 'decision_log') return <DecisionLogView c={content} />
  if (artKey === 'render_report') return <RenderReportView c={content} />
  // Filename prefix is a cheap fast-path; content-shape detection (incl. raw
  // prediction dumps with nested `output`) is the real guard.
  const cs = contentSignalReport(content)
  if (cs || (typeof artKey === 'string' && artKey.startsWith('content_signal_report')))
    return <ContentSignalView c={cs || content} />
  return <GenericValue value={content} />
}

// ── Generic schema-driven renderer (handles any artifact, no raw JSON) ──

function isUrl(s) { return typeof s === 'string' && /^https?:\/\//.test(s) }
function fmtScalar(key, v) {
  if (typeof v === 'number') {
    const k = (key || '').toLowerCase()
    if (/cost|usd|price|amount|spent|budget|reserved/.test(k)) return '$' + v.toFixed(2)
    if (/seconds$/.test(k)) return v + 's'
  }
  return String(v)
}

function GenericValue({ value, k }) {
  if (value === null || value === undefined) return <span className="gv-null">—</span>
  if (typeof value === 'boolean') return <span className={`gv-bool ${value ? 'yes' : 'no'}`}>{value ? 'yes' : 'no'}</span>
  if (typeof value !== 'object') {
    if (isUrl(value)) return <a className="gv-link" href={value} target="_blank" rel="noreferrer">{value}</a>
    return <span className="gv-scalar">{fmtScalar(k, value)}</span>
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="gv-null">none</span>
    const allScalar = value.every(x => x === null || typeof x !== 'object')
    if (allScalar) return <ul className="gv-list">{value.map((x, i) => <li key={i}>{String(x)}</li>)}</ul>
    return (
      <div className="gv-cards">
        {value.map((x, i) => (
          <div key={i} className="gv-card">
            {x && typeof x === 'object' && !Array.isArray(x) ? <GenericObject obj={x} /> : <GenericValue value={x} />}
          </div>
        ))}
      </div>
    )
  }
  return <GenericObject obj={value} />
}

function GenericObject({ obj }) {
  const entries = Object.entries(obj)
  if (entries.length === 0) return <span className="gv-null">empty</span>
  return (
    <div className="gv-obj">
      {entries.map(([k, v]) => {
        const nested = v !== null && typeof v === 'object'
        return (
          <div key={k} className={`gv-row ${nested ? 'nested' : ''}`}>
            <div className="gv-key">{prettyKey(k)}</div>
            <div className="gv-value"><GenericValue value={v} k={k} /></div>
          </div>
        )
      })}
    </div>
  )
}

// ── Marquee views ──

function Chip({ label, val }) {
  return <span className="mv-chip"><span className="mvc-k">{label}</span><span className="mvc-v">{val}</span></span>
}
function timing(s) {
  const a = s.start_seconds, b = s.end_seconds
  if (a == null && b == null) return ''
  return `${a ?? '?'}–${b ?? '?'}s`
}

function ScenePlanView({ c }) {
  const scenes = c.scenes || []
  const g = (c.metadata && c.metadata.global) || {}
  return (
    <div className="mv">
      <div className="mv-summary">
        {c.style_playbook && <Chip label="style" val={c.style_playbook} />}
        {g.render_runtime && <Chip label="runtime" val={g.render_runtime} />}
        {g.dimensions && <Chip label="size" val={g.dimensions} />}
        {g.fps && <Chip label="fps" val={String(g.fps)} />}
        <Chip label="scenes" val={String(scenes.length)} />
      </div>
      <div className="scene-cards">
        {scenes.map((s, i) => (
          <div key={s.id || i} className={`scene-card ${s.hero_moment ? 'hero' : ''}`}>
            <div className="sc-top">
              <span className="sc-id">{s.id || `sc${i + 1}`}</span>
              {s.type && <span className="sc-type">{s.type}</span>}
              {timing(s) && <span className="sc-time">{timing(s)}</span>}
              {s.hero_moment && <span className="sc-hero"><IconStar size={11} /> hero</span>}
            </div>
            {s.description && <div className="sc-desc">{s.description}</div>}
            <div className="sc-attrs">
              {s.framing && <span><b>framing</b> {s.framing}</span>}
              {s.movement && <span><b>movement</b> {s.movement}</span>}
              {s.narrative_role && <span><b>role</b> {prettyKey(s.narrative_role)}</span>}
            </div>
            {Array.isArray(s.required_assets) && s.required_assets.length > 0 && (
              <div className="sc-assets">
                {s.required_assets.map((a, j) => (
                  <span key={j} className="sc-asset">{a.type}{a.source ? ` · ${a.source}` : ''}: {a.description}</span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function ScriptView({ c }) {
  const secs = c.sections || []
  return (
    <div className="mv">
      <div className="mv-summary">
        {c.title && <Chip label="title" val={c.title} />}
        {c.total_duration_seconds != null && <Chip label="duration" val={c.total_duration_seconds + 's'} />}
        <Chip label="sections" val={String(secs.length)} />
      </div>
      <ol className="script-secs">
        {secs.map((s, i) => (
          <li key={s.id || i} className="script-sec">
            <div className="ss-head">
              <span className="ss-id">{s.id || i + 1}</span>
              {s.label && <span className="ss-label">{prettyKey(s.label)}</span>}
              {timing(s) && <span className="ss-time">{timing(s)}</span>}
            </div>
            {s.text && <div className="ss-text">{s.text}</div>}
            {s.speaker_directions && <div className="ss-dir"><IconMic size={12} /> {s.speaker_directions}</div>}
            {Array.isArray(s.enhancement_cues) && s.enhancement_cues.length > 0 && (
              <div className="ss-cues">
                {s.enhancement_cues.map((q, j) => (
                  <span key={j} className="ss-cue">{q.type}{q.timestamp_seconds != null ? ` @${q.timestamp_seconds}s` : ''}: {q.description}</span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

function DecisionLogView({ c }) {
  const ds = c.decisions || []
  return (
    <div className="mv">
      <div className="mv-summary"><Chip label="decisions" val={String(ds.length)} /></div>
      <div className="decision-cards">
        {ds.map((d, i) => (
          <div key={d.decision_id || i} className="decision-card">
            <div className="dc-head">
              {d.category && <span className="dc-cat">{prettyKey(d.category)}</span>}
              {d.confidence != null && <span className="dc-conf">{Math.round(d.confidence * 100)}% conf</span>}
            </div>
            {d.subject && <div className="dc-subject">{d.subject}</div>}
            {Array.isArray(d.options_considered) && d.options_considered.length > 0 && (
              <div className="dc-opts">
                {d.options_considered.map((o, j) => {
                  const chosen = o.option_id === d.selected
                  return (
                    <div key={j} className={`dc-opt ${chosen ? 'chosen' : ''}`}>
                      <span className="dco-label">{chosen ? '✓ ' : ''}{o.label || o.option_id}</span>
                      {o.score != null && <span className="dco-score">{o.score}</span>}
                      <span className="dco-reason">{chosen ? o.reason : (o.rejected_because || o.reason)}</span>
                    </div>
                  )
                })}
              </div>
            )}
            {d.reason && <div className="dc-reason">{d.reason}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

function RenderReportView({ c }) {
  const outs = c.outputs || []
  const ws = c.workspace || {}
  return (
    <div className="mv">
      <div className="mv-summary">
        {c.render_grammar && <Chip label="grammar" val={c.render_grammar} />}
        {ws.runtime && <Chip label="runtime" val={ws.runtime} />}
        {c.render_time_seconds != null && <Chip label="render time" val={c.render_time_seconds + 's'} />}
      </div>
      {outs.map((o, i) => (
        <div key={i} className="render-out">
          <div className="ro-path">{o.path}</div>
          <div className="ro-meta">
            {o.resolution && <span>{o.resolution}</span>}
            {o.fps && <span>{o.fps} fps</span>}
            {o.duration_seconds != null && <span>{o.duration_seconds}s</span>}
            {o.codec && <span>{o.codec}</span>}
            {o.file_size_bytes && <span>{fmtBytes(o.file_size_bytes)}</span>}
            {o.platform_target && <span>{prettyKey(o.platform_target)}</span>}
          </div>
        </div>
      ))}
      {Array.isArray(c.verification_notes) && c.verification_notes.length > 0 && (
        <div className="ro-block"><div className="ro-block-label">Verification</div>
          <ul>{c.verification_notes.map((n, i) => <li key={i}>{n}</li>)}</ul></div>
      )}
      {Array.isArray(c.warnings) && c.warnings.length > 0 && (
        <div className="ro-block warn"><div className="ro-block-label">Warnings</div>
          <ul>{c.warnings.map((n, i) => <li key={i}>{n}</li>)}</ul></div>
      )}
    </div>
  )
}

// ── Content Signal (Meta TRIBE v2) view + reusable line chart ──

const CS_COLORS = {
  attention: '#c8643c',        // accent — the primary signal we plot bold
  emotion: '#b8503f',
  reward: '#4a8a55',
  social_relevance: '#3b6ea5',
  novelty: '#c9952b',
}
const CS_ORDER = ['attention', 'emotion', 'reward', 'social_relevance', 'novelty']

function scoreColor(v) {
  if (v >= 67) return 'var(--green)'
  if (v >= 45) return 'var(--amber)'
  return 'var(--red)'
}

// LineChart moved to ./components/LineChart.jsx (imported at top) so the manual
// editor's keyframe-curve editor can reuse the same dependency-free SVG chart.

function ContentSignalView({ c }) {
  const timeline = Array.isArray(c.timeline) ? c.timeline : []
  const subs = (c.sub_scores && typeof c.sub_scores === 'object') ? c.sub_scores : {}

  // Dimensions present anywhere (sub_scores keys ∪ timeline keys, minus the time axis).
  const dimSet = new Set(Object.keys(subs))
  timeline.forEach(p => Object.keys(p).forEach(k => { if (k !== 't') dimSet.add(k) }))
  const dims = [...CS_ORDER.filter(d => dimSet.has(d)), ...[...dimSet].filter(d => !CS_ORDER.includes(d))]

  // Attention is the line shown by default (the requested signal); others toggle on.
  const [shown, setShown] = useState(() => {
    const def = dimSet.has('attention') ? 'attention' : dims[0]
    return new Set(def ? [def] : [])
  })
  const toggle = d => setShown(prev => {
    const next = new Set(prev)
    next.has(d) ? next.delete(d) : next.add(d)
    return next
  })

  const xOf = (p, i) => (typeof p.t === 'number' ? p.t : i)
  const xMax = c.video_duration_s || (timeline.length ? Math.max(...timeline.map(xOf)) : 0)

  const series = dims.map(d => ({
    key: d,
    label: prettyKey(d),
    color: CS_COLORS[d] || 'var(--muted)',
    bold: d === 'attention',
    hidden: !shown.has(d),
    points: timeline.map((p, i) => ({ x: xOf(p, i), y: Number(p[d]) || 0 })),
  }))

  // Weakest attention moment — the spot worth re-cutting.
  const attn = series.find(s => s.key === 'attention')
  const weak = (attn && attn.points.length) ? attn.points.reduce((a, b) => (b.y < a.y ? b : a)) : null

  return (
    <div className="mv cs">
      <div className="mv-summary">
        {c.model && <Chip label="model" val="Meta TRIBE v2" />}
        {c.scoring_version && <Chip label="scoring" val={`v${c.scoring_version}`} />}
        {c.video_duration_s != null && <Chip label="duration" val={`${c.video_duration_s}s`} />}
        {c.cost_usd != null && <Chip label="cost" val={`$${Number(c.cost_usd).toFixed(2)}`} />}
        {c.cache_hit && <Chip label="cache" val="hit" />}
      </div>

      <div className="cs-scores">
        <div className="cs-headline">
          <div className="cs-headline-val" style={{ color: scoreColor(c.headline_score) }}>
            {Math.round(c.headline_score)}<span className="cs-of">/100</span>
          </div>
          <div className="cs-headline-label">virality signal <span className="cs-advisory">advisory</span></div>
        </div>
        <div className="cs-subs">
          {dims.map(d => (
            <div key={d} className="cs-sub">
              <div className="cs-sub-top">
                <span className="cs-sub-name">{prettyKey(d)}</span>
                <span className="cs-sub-val">{Math.round(subs[d] ?? 0)}</span>
              </div>
              <div className="cs-bar">
                <div className="cs-bar-fill"
                  style={{ width: `${Math.max(0, Math.min(100, subs[d] ?? 0))}%`, background: CS_COLORS[d] || 'var(--muted)' }} />
              </div>
            </div>
          ))}
        </div>
      </div>

      {timeline.length > 0 ? (
        <div className="cs-chart">
          <div className="cs-chart-head">
            <span className="cs-chart-title">Attention over time</span>
            <div className="cs-legend">
              {dims.map(d => (
                <button key={d} className={`cs-leg ${shown.has(d) ? 'on' : ''}`} onClick={() => toggle(d)}
                  style={shown.has(d) ? { borderColor: CS_COLORS[d], color: CS_COLORS[d] } : undefined}>
                  <span className="cs-leg-dot" style={{ background: shown.has(d) ? (CS_COLORS[d] || 'var(--muted)') : 'var(--muted)' }} />
                  {prettyKey(d)}
                </button>
              ))}
            </div>
          </div>
          <LineChart series={series} xMax={xMax} xLabel="time (s)" yLabel="score" />
          {weak && (
            <div className="cs-weak">⚠ Lowest attention at <b>{weak.x.toFixed(1)}s</b> ({Math.round(weak.y)}/100) — the spot to consider re-cutting.</div>
          )}
        </div>
      ) : (
        <p className="empty">No per-step timeline in this report.</p>
      )}

      {c.license_note && (
        <div className="ro-block"><div className="ro-block-label">Note</div><div className="cs-note">{c.license_note}</div></div>
      )}
      {Array.isArray(c.warnings) && c.warnings.length > 0 && (
        <div className="ro-block warn"><div className="ro-block-label">Warnings</div>
          <ul>{c.warnings.map((n, i) => <li key={i}>{n}</li>)}</ul></div>
      )}
    </div>
  )
}

// ─── Assets Panel ─────────────────────────────────────────────────────────────

function AssetPanel({ selected, onUpload, uploadTick }) {
  const [activeKind, setActiveKind] = useState('images')
  const [data, setData] = useState(null)
  const inputRef = useRef(null)
  const [dragging, setDragging] = useState(false)
  const [viewer, setViewer] = useState(null)   // { items:[{kind,name,url,path}], index } — lightbox

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
  // The Renders tab reads agent_renders (hf/renders clips); every other tab reads kinds[].
  const files = (activeKind === 'renders' ? data?.agent_renders : data?.kinds?.[activeKind]) || []

  // Lightbox item lists. The URL carries the file's mtime as a cache-bust token so
  // a freshly finished render (same path, new bytes) reloads without a page refresh.
  // Renders are videos — tag them 'video' so the tile + lightbox use the video player.
  const gridItems = files.map(f => ({
    kind: activeKind === 'renders' ? 'video' : activeKind,
    name: f.name, path: f.path, url: api.fileUrl(selected, f.path, f.mtime),
  }))
  const renderItems = renders.map(r => ({
    kind: 'video', name: r.name, path: r.path, url: api.fileUrl(selected, r.path, r.mtime),
  }))

  return (
    <section className="panel assets">
      <h2>Assets</h2>
      {!selected && <p className="empty">Select a project to see its assets.</p>}
      {selected && (
        <div className="assets-scroll">
          {/* Final render(s) — shown when the reel is done (#1) */}
          {renders.length > 0 && (
            <div className="renders">
              {renders.map((r, i) => (
                <div key={r.path} className="render-item">
                  {/* One file is the deliverable (final.mp4); it is CURRENT only while
                      its receipt matches the live timeline. Everything else under
                      renders/ is an earlier render — labelling them all "Final render"
                      is how a stale 15s cut got handed over for a 13s timeline. */}
                  <div className={`renders-label ${r.current ? '' : 'stale'}`}
                    title={r.reason || undefined}>
                    <IconMovie size={13} />{' '}
                    {r.name !== 'final.mp4' ? 'Earlier render'
                      : r.current ? 'Final render' : 'Final render (stale — re-render)'}
                  </div>
                  {/* key on path+mtime so a re-render (or a poll that first caught the
                      file mid-write) remounts the <video> and re-fetches the final bytes. */}
                  <video key={`${r.path}:${r.mtime}`} controls
                    src={api.fileUrl(selected, r.path, r.mtime)} />
                  <div className="render-actions">
                    <button className="render-expand" onClick={() => setViewer({ items: renderItems, index: i })}>
                      ⛶ Full screen
                    </button>
                    <a className="render-dl" href={api.fileUrl(selected, r.path, r.mtime)} download={r.name}>
                      ⤓ {r.name}
                    </a>
                  </div>
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
                {k}{(k === 'renders' ? data?.agent_renders : data?.kinds?.[k])?.length
                  ? ` (${(k === 'renders' ? data.agent_renders : data.kinds[k]).length})` : ''}
              </button>
            ))}
          </div>

          <div className="asset-grid">
            {files.length === 0 && <p className="empty">No {activeKind} yet.</p>}
            {gridItems.map((it, i) => (
              <AssetItem key={it.path} kind={it.kind} url={it.url} name={it.name}
                onOpen={() => setViewer({ items: gridItems, index: i })} />
            ))}
          </div>

          {activeKind !== 'renders' && (
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
          )}
        </div>
      )}
      {viewer && (
        <AssetModal items={viewer.items} index={viewer.index} onClose={() => setViewer(null)} />
      )}
    </section>
  )
}

// A grid tile. Clicking it opens the asset full-screen in the center lightbox (like an
// artifact). Video/audio thumbnails are non-interactive previews — the real player (with
// controls) lives in the lightbox so a single click always opens it.
function AssetItem({ kind, url, name, onOpen }) {
  return (
    <div className="asset-item clickable" title={name} role="button" tabIndex={0}
      onClick={onOpen}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen() } }}>
      {kind === 'images' && <img src={url} alt={name} loading="lazy" />}
      {kind === 'video' && (
        <div className="asset-thumb video">
          <video src={url} preload="metadata" muted playsInline />
          <span className="asset-play">▶</span>
        </div>
      )}
      {(kind === 'audio' || kind === 'music') && (
        <div className="asset-thumb audio"><span className="asset-audio-icon"><IconMusic size={26} /></span></div>
      )}
      <div className="asset-name">{name}</div>
    </div>
  )
}

// Center lightbox: opens an asset full-screen over the app, mirroring how artifacts
// open in the middle. Esc / backdrop-click closes; ←/→ steps through the current list.
function AssetModal({ items, index, onClose }) {
  const [i, setI] = useState(index)
  useEffect(() => { setI(index) }, [index])
  const prev = () => setI(x => (x - 1 + items.length) % items.length)
  const next = () => setI(x => (x + 1) % items.length)

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft') setI(x => (x - 1 + items.length) % items.length)
      else if (e.key === 'ArrowRight') setI(x => (x + 1) % items.length)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [items.length, onClose])

  const item = items[i]
  if (!item) return null
  const multi = items.length > 1
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="asset-lightbox" onClick={e => e.stopPropagation()}>
        <div className="al-head">
          <span className="al-name" title={item.name}>{item.name}</span>
          <div className="al-actions">
            <a className="al-dl" href={item.url} download={item.name} title="Download">⤓</a>
            <button className="am-close" onClick={onClose} title="Close (Esc)">✕</button>
          </div>
        </div>
        <div className="al-stage">
          {multi && <button className="al-nav prev" onClick={prev} title="Previous (←)">‹</button>}
          {/* key on the URL so navigating remounts the media (fresh load + autoplay) */}
          <div className="al-media" key={item.url}>
            {item.kind === 'images' && <img src={item.url} alt={item.name} />}
            {item.kind === 'video' && <video src={item.url} controls autoPlay />}
            {(item.kind === 'audio' || item.kind === 'music') && (
              <div className="al-audio">
                <div className="al-audio-icon"><IconMusic size={44} /></div>
                <audio src={item.url} controls autoPlay />
              </div>
            )}
          </div>
          {multi && <button className="al-nav next" onClick={next} title="Next (→)">›</button>}
        </div>
        {multi && <div className="al-count">{i + 1} / {items.length}</div>}
      </div>
    </div>
  )
}
