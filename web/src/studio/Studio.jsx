// Studio — the from-scratch editing-software UI for OpenNolan (M3).
//
// Owns the single source-of-truth `doc` (edit_decisions), an undo/redo history, the
// selection + playhead, and the save→render-once→poll loop. Every edit goes through a
// schema-safe mutator in ../editor/interp.js and flips `dirty`; Save PUTs the validated
// doc; Render runs the render-once proxy loop (only changed scenes re-render) and the
// preview plays the exact bytes the export produces (preview == export).
//
// Reuses ONLY pure, tested logic (interp.js) — no old-editor UI.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as api from '../api.js'
import * as interp from '../editor/interp.js'
import { isFfmpeg, newTextOverlay, newImageOverlay } from './model.js'
import StudioToolbar from './StudioToolbar.jsx'
import StudioTimeline from './StudioTimeline.jsx'
import StudioInspector from './StudioInspector.jsx'
import StudioPreview from './StudioPreview.jsx'

const POLL_MS = 500
const POLL_MAX = 600 // ~5 min ceiling

export default function Studio({ projectId, state, onClose }) {
  const [doc, setDoc] = useState(null)
  const docRef = useRef(null) // mirror of `doc` so handlers read the latest synchronously
  const [past, setPast] = useState([])
  const [future, setFuture] = useState([])
  const [dirty, setDirty] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadErr, setLoadErr] = useState('')
  const [notice, setNotice] = useState(null) // {kind, msg}

  const [selection, setSelection] = useState(null) // {kind:'cut',id} | {kind:'overlay',index}
  const [playhead, setPlayhead] = useState(0)      // project seconds
  const [previewMode, setPreviewMode] = useState('source') // 'source' | 'render'
  const [playing, setPlaying] = useState(false)            // transport play/pause

  const [assets, setAssets] = useState({ kinds: { images: [], video: [], audio: [], music: [] }, renders: [] })
  const [sourceMetas, setSourceMetas] = useState({}) // ref -> {duration,width,height}
  const [zoom, setZoom] = useState(80) // px per second

  const [rendering, setRendering] = useState(false)
  const [renderPath, setRenderPath] = useState(null)
  const [renderVersion, setRenderVersion] = useState(0)
  const jobRef = useRef(0) // supersede in-flight poll loops

  const savedRef = useRef('') // JSON of last-saved doc (dirty compare)
  const canvas = useMemo(() => interp.canvasOf(doc), [doc])
  const ffmpeg = isFfmpeg(doc)

  const flash = useCallback((kind, msg) => {
    setNotice({ kind, msg })
    if (kind === 'ok') setTimeout(() => setNotice(n => (n && n.msg === msg ? null : n)), 2600)
  }, [])

  // Transport. Playback advances the playhead (via Preview's onScrub); a USER scrub or a
  // preview-mode switch pauses, so manual seeking never fights playback.
  const togglePlay = useCallback(() => {
    const atEnd = playhead >= interp.timelineDuration(doc) - 1e-3
    if (!playing && atEnd) setPlayhead(0) // hitting play at the end restarts from the top
    setPlaying(!playing)
  }, [playing, playhead, doc])
  const seekFromUser = useCallback((t) => { setPlaying(false); setPlayhead(t) }, [])
  const changePreviewMode = useCallback((m) => { setPlaying(false); setPreviewMode(m) }, [])

  // ── load ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    let alive = true
    setLoading(true); setLoadErr('')
    api.getEditDecisions(projectId)
      .then(({ content }) => {
        if (!alive) return
        const d = content || interp.scaffoldEditDecisions({})
        docRef.current = d; setDoc(d); savedRef.current = JSON.stringify(d); setDirty(!content)
        const first = (d.cuts || [])[0]
        if (first) setSelection({ kind: 'cut', id: first.id })
      })
      .catch(e => { if (alive) setLoadErr(String(e.message || e)) })
      .finally(() => { if (alive) setLoading(false) })
    api.listAssets(projectId).then(a => { if (alive) setAssets(a) }).catch(() => {})
    return () => { alive = false }
  }, [projectId])

  // Probe source metadata (duration/dims) for each distinct cut source — trim bounds + scrub.
  useEffect(() => {
    const refs = [...new Set((doc?.cuts || []).map(c => c.source).filter(Boolean))]
    let alive = true
    refs.forEach(ref => {
      if (sourceMetas[ref] !== undefined) return
      api.getSourceMeta(projectId, ref)
        .then(m => { if (alive) setSourceMetas(s => ({ ...s, [ref]: m })) })
        .catch(() => { if (alive) setSourceMetas(s => ({ ...s, [ref]: null })) })
    })
    return () => { alive = false }
  }, [doc, projectId, sourceMetas])

  // ── history / mutation ──────────────────────────────────────────────────────
  // INVARIANT: setState is NEVER nested inside another setState updater — that breaks
  // under StrictMode (updaters are double-invoked in dev, so a nested setter would run
  // twice and corrupt the stacks). docRef mirrors `doc` for synchronous reads; past/future
  // stay state so the undo/redo buttons re-render. undo/redo read the latest past/future
  // from their closures (event handlers always see the last committed state).
  const setDocBoth = useCallback((nd) => { docRef.current = nd; setDoc(nd) }, [])

  // Push the current doc onto the undo stack WITHOUT changing it — called once at the start
  // of a coalesced drag so an entire trim drag collapses to a single undo step.
  const snapshot = useCallback(() => {
    setPast(p => [...p, docRef.current].slice(-100))
    setFuture([])
  }, [])

  // Live update, NO history push (per-frame during a coalesced drag).
  const live = useCallback((next) => {
    const prev = docRef.current
    const nd = typeof next === 'function' ? next(prev) : next
    if (nd === prev) return
    setDocBoth(nd); setDirty(true)
  }, [setDocBoth])

  // Discrete edit = snapshot + apply (one undo step). Referential no-op = no history.
  const commit = useCallback((next) => {
    const prev = docRef.current
    const nd = typeof next === 'function' ? next(prev) : next
    if (nd === prev) return
    setPast(p => [...p, prev].slice(-100))
    setFuture([])
    setDocBoth(nd); setDirty(true)
  }, [setDocBoth])

  const undo = useCallback(() => {
    if (!past.length) return
    const prev = past[past.length - 1]
    setPast(past.slice(0, -1))
    setFuture([docRef.current, ...future])
    setDocBoth(prev); setDirty(true)
  }, [past, future, setDocBoth])

  const redo = useCallback(() => {
    if (!future.length) return
    const nxt = future[0]
    setFuture(future.slice(1))
    setPast([...past, docRef.current])
    setDocBoth(nxt); setDirty(true)
  }, [past, future, setDocBoth])

  // ── save / render ─────────────────────────────────────────────────────────
  const save = useCallback(async () => {
    if (!doc) return null
    try {
      await api.saveEditDecisions(projectId, doc)
      savedRef.current = JSON.stringify(doc)
      setDirty(false)
      flash('ok', 'Saved')
      return doc
    } catch (e) {
      flash('err', `Save rejected: ${String(e.message || e)}`)
      return null
    }
  }, [doc, projectId, flash])

  const render = useCallback(async () => {
    if (rendering) return
    const saved = await save()
    if (!saved) return
    setRendering(true)
    flash('warn', 'Rendering… (only changed scenes re-render)')
    const myJob = ++jobRef.current
    try {
      const { job_id } = await api.startRender(projectId)
      for (let i = 0; i < POLL_MAX; i++) {
        if (jobRef.current !== myJob) return // superseded by a newer render
        await new Promise(r => setTimeout(r, POLL_MS))
        const st = await api.getRenderStatus(projectId, job_id)
        if (st.status === 'done') {
          setRenderPath(st.output_path)
          setRenderVersion(v => v + 1)
          setPreviewMode('render')
          const w = (st.warnings || []).join(' · ')
          flash('ok', w ? `Rendered — ${w}` : 'Rendered')
          return
        }
        if (st.status === 'failed') { flash('err', `Render failed: ${st.error || 'unknown'}`); return }
      }
      flash('err', 'Render timed out')
    } catch (e) {
      flash('err', `Render error: ${String(e.message || e)}`)
    } finally {
      if (jobRef.current === myJob) setRendering(false)
    }
  }, [rendering, save, projectId, flash])

  // ── selection-aware edit handlers ──────────────────────────────────────────
  const selCut = selection?.kind === 'cut' ? (doc?.cuts || []).find(c => c.id === selection.id) : null
  const selOverlayIndex = selection?.kind === 'overlay' ? selection.index : -1

  // Trim runs per pointermove → use `live` (no history); onTrimBegin snapshots once at
  // pointerdown so a whole drag is one undo step (not one per frame).
  const onTrim = useCallback((cutId, patch) => {
    const src = docRef.current?.cuts?.find(c => c.id === cutId)?.source
    const meta = src ? sourceMetas[src] : null
    live(d => interp.trimCut(d, cutId, patch, { sourceDuration: meta?.duration ?? undefined }))
  }, [sourceMetas, live])
  const onTrimBegin = useCallback(() => snapshot(), [snapshot])

  const onSplit = useCallback(() => {
    const nd = interp.splitCutAtPlayhead(docRef.current, playhead)
    if (nd === docRef.current) { flash('warn', 'Move the playhead inside a clip to split'); return }
    commit(nd)
  }, [playhead, commit, flash])

  const onDelete = useCallback(() => {
    if (selCut) {
      if ((doc?.cuts || []).length <= 1) { flash('warn', 'Can’t delete the last clip'); return }
      commit(d => interp.removeCut(d, selCut.id))
      setSelection(null)
    } else if (selOverlayIndex >= 0) {
      commit(d => interp.removeOverlay(d, selOverlayIndex))
      setSelection(null)
    }
  }, [selCut, selOverlayIndex, doc, commit, flash])

  const onDuplicate = useCallback(() => {
    if (!selCut) return
    commit(d => interp.duplicateCut(d, selCut.id))
  }, [selCut, commit])

  const onSpeed = useCallback((speed) => {
    if (!selCut) return
    commit(d => interp.updateCut(d, selCut.id, { speed }))
  }, [selCut, commit])

  const onReorder = useCallback((from, to) => {
    commit(d => interp.reorderCut(d, from, to))
  }, [commit])

  const onAddText = useCallback(() => {
    const start = Math.min(playhead, Math.max(0, interp.timelineDuration(doc) - 1))
    const at = (doc?.overlays || []).length
    commit(d => interp.addOverlay(d, newTextOverlay({ start, end: start + 2.5 })))
    setSelection({ kind: 'overlay', index: at })
  }, [playhead, doc, commit])

  const onAddImage = useCallback((assetId) => {
    const start = Math.min(playhead, Math.max(0, interp.timelineDuration(doc) - 1))
    const at = (doc?.overlays || []).length
    commit(d => interp.addOverlay(d, newImageOverlay({ assetId, start, end: start + 3, canvas: interp.canvasOf(d) })))
    setSelection({ kind: 'overlay', index: at })
  }, [playhead, doc, commit])

  const onCanvas = useCallback(({ width, height }) => {
    commit(d => interp.setCanvas(d, { width, height }))
  }, [commit])

  const onUpdateCut = useCallback((cutId, patch) => commit(d => interp.updateCut(d, cutId, patch)), [commit])
  const onUpdateOverlay = useCallback((index, patch) => commit(d => interp.updateOverlay(d, index, patch)), [commit])
  const onSetKeyframes = useCallback((index, kfs) => commit(d => interp.setOverlayKeyframes(d, index, kfs)), [commit])
  const onUpsertKeyframe = useCallback((index, kf) => commit(d => interp.upsertKeyframe(d, index, kf)), [commit])
  const onRemoveKeyframe = useCallback((index, ki) => commit(d => interp.removeKeyframe(d, index, ki)), [commit])

  // Clear an overlay selection that went out of range (e.g. after undo/redo/delete) so the
  // inspector doesn't edit a phantom index (which would no-op-but-dirty the doc).
  useEffect(() => {
    if (selection?.kind === 'overlay' && selection.index >= (doc?.overlays || []).length) {
      setSelection(null)
    }
  }, [doc, selection])

  // ── keyboard shortcuts ──────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
      if (e.key === ' ' || e.code === 'Space') { e.preventDefault(); setPlaying(p => !p); return } // space = play/pause
      const mod = e.metaKey || e.ctrlKey
      if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return }
      if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return }
      if (!mod && (e.key === 's' || e.key === 'S')) { e.preventDefault(); onSplit(); return }
      if (!mod && (e.key === 'Delete' || e.key === 'Backspace')) { e.preventDefault(); onDelete(); return }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [undo, redo, save, onSplit, onDelete])

  // ── render ──────────────────────────────────────────────────────────────────
  if (loading) return <div className="st"><div className="st-loading">Loading timeline…</div></div>
  if (loadErr) {
    return (
      <div className="st">
        <div className="st-bar"><button className="st-ghost" onClick={onClose}>← Back</button></div>
        <div className="st-loading st-err-text">Couldn’t load: {loadErr}</div>
      </div>
    )
  }

  const dur = interp.timelineDuration(doc)

  return (
    <div className="st">
      <div className="st-bar">
        <div className="st-bar-left">
          <button className="st-ghost" onClick={onClose} title="Back to project">←</button>
          <span className="st-title">{state?.name || projectId}</span>
          {dirty && <span className="st-dirty" title="Unsaved changes">● unsaved</span>}
        </div>
        <StudioToolbar
          doc={doc} canvas={canvas} ffmpeg={ffmpeg}
          selCut={selCut} selOverlayIndex={selOverlayIndex}
          canUndo={past.length > 0} canRedo={future.length > 0}
          dirty={dirty} rendering={rendering} hasRender={!!renderPath} previewMode={previewMode}
          playing={playing} assets={assets}
          onUndo={undo} onRedo={redo} onSave={save} onRender={render}
          onTogglePlay={togglePlay} onPreviewMode={changePreviewMode}
          onSplit={onSplit} onDuplicate={onDuplicate} onDelete={onDelete} onSpeed={onSpeed}
          onAddText={onAddText} onAddImage={onAddImage} onCanvas={onCanvas}
        />
      </div>

      {!ffmpeg && (
        <div className="st-banner">
          This timeline’s runtime is <b>{doc.render_runtime}</b>. The studio editor targets the
          FFmpeg render path — keyframe/overlay previews reflect FFmpeg behavior.
        </div>
      )}
      {notice && <div className={`st-notice ${notice.kind}`}>{notice.msg}</div>}

      <div className="st-body">
        <StudioPreview
          projectId={projectId} doc={doc} playhead={playhead}
          previewMode={previewMode} renderPath={renderPath} renderVersion={renderVersion}
          playing={playing} onScrub={setPlayhead} onPlayingChange={setPlaying}
        />
        <StudioInspector
          projectId={projectId} doc={doc} canvas={canvas} ffmpeg={ffmpeg}
          selCut={selCut} selOverlayIndex={selOverlayIndex} playhead={playhead}
          assets={assets} sourceMetas={sourceMetas}
          onUpdateCut={onUpdateCut} onUpdateOverlay={onUpdateOverlay}
          onSetKeyframes={onSetKeyframes} onUpsertKeyframe={onUpsertKeyframe} onRemoveKeyframe={onRemoveKeyframe}
        />
        <StudioTimeline
          projectId={projectId} doc={doc} dur={dur} zoom={zoom} playhead={playhead}
          selection={selection} sourceMetas={sourceMetas}
          onSeek={seekFromUser} onSelect={setSelection} onTrim={onTrim} onTrimBegin={onTrimBegin}
          onReorder={onReorder} onZoom={setZoom}
        />
      </div>
    </div>
  )
}
