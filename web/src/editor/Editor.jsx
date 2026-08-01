import React, { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api.js'
import Timeline from './Timeline.jsx'
import Inspector from './Inspector.jsx'
import Preview from './Preview.jsx'
import {
  updateCut, updateOverlay, setOverlayKeyframes, scaffoldEditDecisions, timelineDuration,
  trimCut, splitCutAtPlayhead,
} from './interp.js'
import { IconMovie } from '../components/icons.jsx'

// Full-screen manual editor. Loads a project's edit_decisions, lets the human edit cuts /
// overlays / keyframes, saves through the schema-validated PUT, and renders previews through
// the same video_compose path the agent uses. Preview == export (FFmpeg path).
export default function Editor({ projectId, state, onClose }) {
  const [doc, setDoc] = useState(null)
  const [loading, setLoading] = useState(true)
  const [dirty, setDirty] = useState(false)
  const [selection, setSelection] = useState(null)
  const [playhead, setPlayhead] = useState(0)
  const [renderPath, setRenderPath] = useState(null)
  const [rendering, setRendering] = useState(false)
  const [notice, setNotice] = useState(null)   // {kind:'ok'|'err', text}
  const [previewMode, setPreviewMode] = useState('source')  // 'source' | 'render'
  const [sourceMeta, setSourceMeta] = useState({})          // source ref -> {duration,width,height}
  const fetchedMeta = useRef(new Set())
  const pollRef = useRef({ cancelled: false })

  const runtime = doc?.render_runtime || state?.runtime
  const ffmpegPath = (runtime || '').toLowerCase() === 'ffmpeg'

  const [loadError, setLoadError] = useState(null)

  const load = useCallback(() => {
    let alive = true
    setLoading(true); setLoadError(null)
    api.getEditDecisions(projectId)
      .then(d => {
        if (!alive) return
        setDoc(d.content || scaffoldEditDecisions({ runtime: runtime || 'ffmpeg' }))
        setDirty(!d.content)  // a scaffolded doc is unsaved
      })
      .catch(e => { if (alive) setLoadError(String(e.message || e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [projectId, runtime])

  useEffect(() => {
    const cancel = load()
    return () => { cancel(); pollRef.current.cancelled = true }
  }, [load])

  function flash(kind, text, ms = 4000) {
    setNotice({ kind, text })
    if (ms) setTimeout(() => setNotice(null), ms)
  }
  const mutate = (next) => { setDoc(next); setDirty(true) }

  const save = useCallback(async () => {
    if (!doc) return false
    try {
      await api.saveEditDecisions(projectId, doc)
      setDirty(false)
      flash('ok', 'Saved')
      return true
    } catch (e) {
      flash('err', `Save rejected: ${String(e.message || e)}`, 8000)
      return false
    }
  }, [doc, projectId])

  const render = useCallback(async () => {
    if (rendering) return
    const ok = await save()           // always render the saved doc
    if (!ok) return
    setRendering(true)
    pollRef.current = { cancelled: false }
    try {
      const { job_id } = await api.startRender(projectId)
      for (let i = 0; i < 600 && !pollRef.current.cancelled; i++) {
        const st = await api.getRenderStatus(projectId, job_id)
        if (st.status === 'done') {
          setRenderPath(st.output_path)
          setPreviewMode('render')   // show the freshly composed result
          flash('ok', st.final_review_status ? `Rendered (review: ${st.final_review_status})` : 'Rendered')
          break
        }
        if (st.status === 'failed') { flash('err', `Render failed: ${st.error || 'unknown'}`, 8000); break }
        await sleep(500)
      }
    } catch (e) {
      flash('err', `Render error: ${String(e.message || e)}`, 8000)
    } finally {
      setRendering(false)
    }
  }, [rendering, save, projectId])

  // Scrubbing just moves the playhead; <Preview> keeps whichever video is showing in sync.
  const seek = useCallback((t) => setPlayhead(Number.isFinite(t) ? t : 0), [])

  // Trim a clip edge (from the timeline handles) — clamped + schema-sanitized by trimCut.
  const onTrimCut = useCallback(
    (id, patch, opts) => mutate(trimCut(doc, id, patch, opts)),
    [doc],
  )

  // Split the clip under the playhead in two. No-op (same doc ref) if the playhead isn't
  // strictly inside a clip — tell the user rather than silently doing nothing.
  const splitAtPlayhead = useCallback(() => {
    if (!doc) return
    const next = splitCutAtPlayhead(doc, playhead)
    if (next === doc) { flash('err', 'Move the playhead inside a clip (not at its edge) to split.'); return }
    mutate(next)
    flash('ok', 'Clip split at playhead')
  }, [doc, playhead])

  // Lazily probe each unique source clip's duration (for trim bounds) — once per ref.
  useEffect(() => {
    if (!doc) return
    const refs = [...new Set((doc.cuts || []).map(c => c.source).filter(Boolean))]
    for (const ref of refs) {
      if (fetchedMeta.current.has(ref)) continue
      fetchedMeta.current.add(ref)
      api.getSourceMeta(projectId, ref)
        .then(m => setSourceMeta(prev => ({ ...prev, [ref]: m })))
        .catch(() => fetchedMeta.current.delete(ref))
    }
  }, [doc, projectId])

  // Keyboard: "S" splits at the playhead (ignored while typing in a field).
  useEffect(() => {
    const onKey = (e) => {
      const tag = e.target?.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
      if ((e.key === 's' || e.key === 'S') && !e.metaKey && !e.ctrlKey) { e.preventDefault(); splitAtPlayhead() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [splitAtPlayhead])

  if (loading) return <div className="editor"><div className="editor-loading">Loading editor…</div></div>

  // A failed/empty load must not blank-crash. Show a clear, actionable error.
  if (!doc) {
    return (
      <div className="editor">
        <header className="editor-bar">
          <button className="back-btn" onClick={onClose}>← Close editor</button>
          <div className="editor-title"><strong>{state?.name || projectId}</strong></div>
        </header>
        <div className="editor-noprev">
          <div>Couldn't load this project's timeline.</div>
          {loadError && <div className="editor-notice err" style={{ marginTop: '0.5rem' }}>{loadError}</div>}
          <div style={{ marginTop: '0.8rem', color: 'var(--muted)' }}>
            If the error mentions <strong>404 / Not Found</strong>, the backend is running older code —
            restart it (<code>uvicorn server.app:app --reload</code>) so the editor API routes load, then retry.
          </div>
          <button className="editor-render" style={{ marginTop: '0.8rem' }} onClick={load}>Retry</button>
        </div>
      </div>
    )
  }

  const duration = timelineDuration(doc)

  return (
    <div className="editor">
      <header className="editor-bar">
        <button className="back-btn" onClick={onClose}>← Close editor</button>
        <div className="editor-title">
          <strong>{state?.name || projectId}</strong>
          {runtime && <span className={`chip ${ffmpegPath ? 'on' : 'off'}`}><IconMovie size={12} /> {runtime}</span>}
          {dirty && <span className="editor-dirty">● unsaved</span>}
        </div>
        <div className="editor-actions">
          <button className="editor-split" onClick={splitAtPlayhead}
            title="Split the clip under the playhead (S)">✂ Split</button>
          <button className="editor-save" onClick={save} disabled={!dirty}>Save</button>
          <button className="editor-render" onClick={render} disabled={rendering}>
            {rendering ? 'Rendering…' : 'Render preview'}
          </button>
        </div>
      </header>

      {!ffmpegPath && (
        <div className="editor-banner">
          This project renders via <strong>{runtime || 'an unset runtime'}</strong>. Cuts, timing and audio are
          editable, but keyframe / overlay-motion preview only applies on the <strong>ffmpeg</strong> path.
        </div>
      )}
      {notice && <div className={`editor-notice ${notice.kind}`}>{notice.text}</div>}

      <div className="editor-main">
        <section className="editor-preview">
          <Preview
            projectId={projectId} doc={doc} playhead={playhead} renderPath={renderPath}
            mode={previewMode} onModeChange={setPreviewMode} onScrub={setPlayhead}
          />
          <div className="editor-timecode">{playhead.toFixed(2)}s / {duration.toFixed(2)}s</div>
        </section>

        <aside className="editor-inspector">
          <Inspector
            doc={doc} selection={selection} duration={duration} playhead={playhead}
            onUpdateCut={(id, patch) => mutate(updateCut(doc, id, patch))}
            onUpdateOverlay={(idx, patch) => mutate(updateOverlay(doc, idx, patch))}
            onSetKeyframes={(idx, kfs) => mutate(setOverlayKeyframes(doc, idx, kfs))}
          />
        </aside>

        <footer className="editor-timeline">
          <Timeline
            doc={doc} duration={duration} playhead={playhead} selection={selection}
            sourceMeta={sourceMeta} onSelect={setSelection} onSeek={seek} onTrimCut={onTrimCut}
          />
        </footer>
      </div>
    </div>
  )
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }
