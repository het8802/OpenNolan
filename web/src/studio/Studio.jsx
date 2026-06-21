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
import { isFfmpeg, newTextOverlay, newImageOverlay, newVideoOverlay } from './model.js'
import StudioToolbar from './StudioToolbar.jsx'
import StudioTimeline from './StudioTimeline.jsx'
import StudioInspector from './StudioInspector.jsx'
import StudioPreview from './StudioPreview.jsx'
import ChatPanel from '../chat/ChatPanel.jsx'

const POLL_MS = 500
const POLL_MAX = 600 // ~5 min ceiling

// Resizable-panel layout (feat 1). Single threshold per panel: drag below it → collapse.
const PANELS_KEY = 'st.panels.v1'
const INSPECTOR_MIN = 240   // also the collapse threshold: drag narrower than this → hide
const INSPECTOR_MAX = 560
const TIMELINE_MIN = 150     // collapse threshold for the timeline height
const TIMELINE_MAX_FRAC = 0.6 // timeline may take at most 60% of the body (keeps preview visible)
const AGENT_MIN = 260       // collapse threshold for the agent panel: drag narrower than this → hide
const AGENT_MAX = 520

export default function Studio({ projectId, state, onClose, chat }) {
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

  // ── resizable panels (feat 1): inspector width + timeline height, collapse past a threshold,
  // persisted to localStorage. Layout is view state — NEVER written to the doc. ──
  const [panels, setPanels] = useState(() => {
    const base = { inspectorW: 320, timelineH: 280, agentW: 340, inspectorOpen: true, timelineOpen: true, agentOpen: true }
    try { return { ...base, ...JSON.parse(localStorage.getItem(PANELS_KEY) || '{}') } } catch { return base }
  })
  useEffect(() => { try { localStorage.setItem(PANELS_KEY, JSON.stringify(panels)) } catch { /* ignore */ } }, [panels])
  const panelDrag = useRef(null)
  useEffect(() => () => { if (panelDrag.current) panelDrag.current() }, []) // never leak a splitter drag

  const [rendering, setRendering] = useState(false)
  const [renderPath, setRenderPath] = useState(null)
  const [renderVersion, setRenderVersion] = useState(0)
  const jobRef = useRef(0) // supersede in-flight poll loops

  const savedRef = useRef('') // JSON of last-saved doc (dirty compare)
  const dirtyRef = useRef(false) // mirror of `dirty` for the agent-sync effect's closure
  useEffect(() => { dirtyRef.current = dirty }, [dirty])
  // The in-editor agent panel shares the project's conversation, so the agent can rewrite
  // edit_decisions.json on disk while the editor holds an open-time snapshot. `agentBusyRef`
  // tracks whether the agent is mid-turn, and `reconcilingRef` covers the brief window after a
  // turn while we re-fetch the disk doc — Save/Render refuse during either so they can't clobber
  // an in-progress write or race the re-sync.
  const agentBusyRef = useRef(false)
  const reconcilingRef = useRef(false)
  const chatBusy = !!(chat && chat.busy)
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
    if (agentBusyRef.current || reconcilingRef.current) {
      flash('warn', 'Agent is editing — wait for its turn to finish before saving')
      return null
    }
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

  // When the in-editor agent finishes a turn (busy true→false), it may have rewritten
  // edit_decisions.json on disk. Re-sync so the editor isn't holding a stale doc that a later
  // Save would use to clobber the agent's work. If the disk changed and we have NO unsaved local
  // edits, adopt the agent's version; if we DO have local edits, warn (don't silently discard
  // either side). `agentBusyRef` doubles as the previous-busy tracker AND the save-guard mirror;
  // `reconcilingRef` holds the Save guard open across the async re-fetch so a Save can't race it.
  useEffect(() => {
    const was = agentBusyRef.current
    agentBusyRef.current = chatBusy
    if (!(was && !chatBusy) || !projectId) return // only act on a turn that just ended
    let alive = true
    reconcilingRef.current = true
    api.getEditDecisions(projectId)
      .then(({ content }) => {
        if (!alive || !content) return
        const incoming = JSON.stringify(content)
        if (incoming === savedRef.current) return // disk matches our last save — nothing changed
        if (dirtyRef.current) {
          flash('warn', 'Agent changed this timeline on disk — saving will overwrite its edits. Reopen the editor to load them.')
        } else {
          docRef.current = content; setDoc(content); savedRef.current = incoming; setDirty(false)
          flash('ok', 'Timeline updated by the agent')
        }
      })
      .catch(() => {})
      .finally(() => { reconcilingRef.current = false })
    return () => { alive = false }
  }, [chatBusy, projectId, flash])

  // ── selection-aware edit handlers ──────────────────────────────────────────
  const selCut = selection?.kind === 'cut' ? (doc?.cuts || []).find(c => c.id === selection.id) : null
  const selOverlayIndex = selection?.kind === 'overlay' ? selection.index : -1
  const selAudio = selection?.kind === 'audio' ? selection : null // {audioKind:'music'|'narration'|'sfx', index}
  const selAudioObj = !selAudio ? null
    : selAudio.audioKind === 'music' ? (doc?.audio?.music || doc?.music || null)
      : selAudio.audioKind === 'narration' ? (doc?.audio?.narration?.segments?.[selAudio.index] || null)
        : (doc?.audio?.sfx?.[selAudio.index] || null)

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
    } else if (selAudio) {
      if (selAudio.audioKind === 'music') commit(d => interp.removeMusic(d))
      else if (selAudio.audioKind === 'narration') commit(d => interp.removeNarration(d, selAudio.index))
      else commit(d => interp.removeSfx(d, selAudio.index))
      setSelection(null)
    }
  }, [selCut, selOverlayIndex, selAudio, doc, commit, flash])

  const onDuplicate = useCallback(() => {
    if (!selCut) return
    commit(d => interp.duplicateCut(d, selCut.id))
  }, [selCut, commit])

  const onReorder = useCallback((from, to) => {
    commit(d => interp.reorderCut(d, from, to))
  }, [commit])

  // Assets-tab adds (feat 4) + drag-drop (atTime present on a drop): each kind drops in the
  // way that fits it. Click → at the playhead; drag-drop → at the dropped project time.
  const onAddClip = useCallback((path, atTime) => {
    const meta = sourceMetas[path]
    const out = meta?.duration ? Math.min(meta.duration, 8) : 5
    let atIndex
    if (atTime != null) {
      const h = interp.cutAtTime(docRef.current, atTime) // insert before/after the cut under the drop
      if (h) atIndex = atTime >= h.start + interp.cutDuration(h.cut) / 2 ? h.index + 1 : h.index
    }
    commit(d => interp.addCut(d, { source: path, in_seconds: 0, out_seconds: out }, atIndex))
  }, [commit, sourceMetas])
  const onSetMusic = useCallback((path) => { commit(d => interp.setMusic(d, path)); flash('ok', 'Music set') }, [commit, flash])
  const onAddSfx = useCallback((path, atTime) => { commit(d => interp.addSfx(d, path, atTime != null ? atTime : playhead)) }, [commit, playhead])

  // Edit the selected audio item (music bed / narration segment / sfx) from the properties panel.
  const onUpdateAudio = useCallback((patch) => {
    if (!selAudio) return
    if (selAudio.audioKind === 'music') commit(d => interp.updateMusic(d, patch))
    else if (selAudio.audioKind === 'narration') commit(d => interp.updateNarration(d, selAudio.index, patch))
    else commit(d => interp.updateSfx(d, selAudio.index, patch))
  }, [selAudio, commit])

  // Splitter drag (feat 1): 'x' resizes the inspector width, 'y' the timeline height. Below the
  // per-panel threshold → collapse. One pointerdown→window move/up model (same as the timeline).
  const beginPanelDrag = useCallback((e, axis) => {
    e.preventDefault()
    const startX = e.clientX, startY = e.clientY
    const startW = panels.inspectorW, startH = panels.timelineH, startAW = panels.agentW
    const bodyH = e.currentTarget.closest('.st-body')?.clientHeight || 800
    const teardown = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', teardown)
      window.removeEventListener('pointercancel', teardown)
      panelDrag.current = null
    }
    const onMove = (ev) => {
      if (axis === 'agent') {
        const w = startAW + (ev.clientX - startX) // handle sits right of the agent → drag right = wider
        if (w < AGENT_MIN) setPanels(p => ({ ...p, agentOpen: false }))
        else setPanels(p => ({ ...p, agentOpen: true, agentW: Math.min(AGENT_MAX, w) }))
      } else if (axis === 'x') {
        const w = startW + (startX - ev.clientX) // drag the handle left → wider inspector
        if (w < INSPECTOR_MIN) setPanels(p => ({ ...p, inspectorOpen: false }))
        else setPanels(p => ({ ...p, inspectorOpen: true, inspectorW: Math.min(INSPECTOR_MAX, w) }))
      } else {
        const h = startH + (startY - ev.clientY) // drag the handle up → taller timeline
        const max = Math.max(TIMELINE_MIN, bodyH * TIMELINE_MAX_FRAC)
        if (h < TIMELINE_MIN) setPanels(p => ({ ...p, timelineOpen: false }))
        else setPanels(p => ({ ...p, timelineOpen: true, timelineH: Math.min(max, h) }))
      }
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', teardown)
    window.addEventListener('pointercancel', teardown)
    panelDrag.current = teardown
  }, [panels])

  // Each overlay add auto-picks a track via interval-partitioning (interp.placeOverlayTrack): if it
  // would overlap an overlay already on the preferred track, it lands on its own lane (a new track
  // is created when needed) so both stay visible. `track` is the PREFERRED track (0 for toolbar/
  // Assets-click; the dropped lane for a timeline drop).
  const onAddText = useCallback(() => {
    const start = Math.min(playhead, Math.max(0, interp.timelineDuration(doc) - 1))
    const end = start + 2.5
    const at = (doc?.overlays || []).length
    commit(d => interp.addOverlay(d, newTextOverlay({ start, end, track: interp.placeOverlayTrack(d, start, end, 0) })))
    setSelection({ kind: 'overlay', index: at })
  }, [playhead, doc, commit])

  const onAddImage = useCallback((assetId, atTime, track = 0) => {
    const start = atTime != null ? Math.max(0, atTime) : Math.min(playhead, Math.max(0, interp.timelineDuration(doc) - 1))
    const end = start + 3
    const at = (doc?.overlays || []).length
    commit(d => interp.addOverlay(d, newImageOverlay({ assetId, start, end, canvas: interp.canvasOf(d), track: interp.placeOverlayTrack(d, start, end, track) })))
    setSelection({ kind: 'overlay', index: at })
  }, [playhead, doc, commit])

  const onAddVideoOverlay = useCallback((assetId, atTime, track = 0) => {
    const start = atTime != null ? Math.max(0, atTime) : Math.min(playhead, Math.max(0, interp.timelineDuration(doc) - 1))
    const meta = sourceMetas[assetId]
    const len = meta?.duration ? Math.min(meta.duration, 6) : 4
    const end = start + len
    const at = (doc?.overlays || []).length
    commit(d => interp.addOverlay(d, newVideoOverlay({ assetId, start, end, canvas: interp.canvasOf(d), track: interp.placeOverlayTrack(d, start, end, track) })))
    setSelection({ kind: 'overlay', index: at })
  }, [playhead, doc, commit, sourceMetas])

  // Re-pack ALL overlays into the fewest non-overlapping tracks (greedy interval partitioning) —
  // for an existing doc where overlapping overlays are piled on one lane. No-op if already arranged.
  const onAutoArrange = useCallback(() => {
    const nd = interp.autoArrangeOverlays(docRef.current)
    if (nd === docRef.current) { flash('ok', 'Overlays already arranged'); return }
    commit(nd)
  }, [commit, flash])

  // Drop an asset from the Assets tab onto the timeline at project time `t`. The drop TARGET
  // ({lane, track}) decides MAIN vs OVERLAY: a drop on the cuts lane becomes a main-timeline clip
  // (video_main or image_main); a drop on an overlay track lane becomes an overlay at that track;
  // music/audio always route to the bed / SFX regardless of lane. Declared AFTER the add-handlers
  // it depends on so they aren't read in their temporal dead zone (a TDZ ReferenceError here
  // crashes the whole editor on mount, which the build can't catch).
  const onAssetDrop = useCallback((kind, path, t, target = {}) => {
    const { lane, track = 0 } = target
    if (kind === 'music') { onSetMusic(path); return }
    if (kind === 'audio') { onAddSfx(path, t); return }
    if (lane === 'cuts') { onAddClip(path, t); return } // image or video → main-timeline clip
    if (kind === 'video') onAddVideoOverlay(path, t, track)
    else onAddImage(path, t, track) // images → image overlay at the dropped track
  }, [onAddImage, onAddVideoOverlay, onAddClip, onSetMusic, onAddSfx])

  // Overlay timeline drag (feat 3): move on absolute time + change track, or edge-trim. Uses the
  // live/snapshot pattern — onOverlayDragBegin snapshots once at pointerdown so a whole drag is one
  // undo step; the per-frame move/trim go through `live` (no history).
  const onOverlayMove = useCallback((index, patch) => live(d => interp.moveOverlay(d, index, patch)), [live])
  const onOverlayTrim = useCallback((index, patch) => live(d => interp.trimOverlay(d, index, patch)), [live])
  const onOverlayDragBegin = useCallback(() => snapshot(), [snapshot])

  // Canvas drag-to-position (feat 4): merge {x,y} (canvas px) into the overlay's position object,
  // converting a text anchor string to an object on the first drag. Live (no per-frame history) —
  // onOverlayDragBegin already snapshotted once at pointerdown.
  const onOverlayPosition = useCallback((index, xy) => live(d => {
    const ov = d?.overlays?.[index]
    if (!ov) return d
    const cur = ov.position && typeof ov.position === 'object' ? ov.position : {}
    return interp.updateOverlay(d, index, { position: { ...cur, x: Math.round(xy.x), y: Math.round(xy.y) } })
  }), [live])
  const onSelectOverlay = useCallback((index) => setSelection({ kind: 'overlay', index }), [])

  // Quietly self-heal an agent-authored image/video overlay whose position is a string anchor
  // (the renderer rejects that for non-text). Routed through `live` — NOT `commit` — so merely
  // SELECTING such an overlay never pushes a phantom undo step or wipes the redo stack.
  const onNormalizeOverlay = useCallback((index, patch) => live(d => interp.updateOverlay(d, index, patch)), [live])

  const onCanvas = useCallback(({ width, height }) => {
    commit(d => interp.setCanvas(d, { width, height }))
  }, [commit])

  const onUpdateCut = useCallback((cutId, patch) => commit(d => interp.updateCut(d, cutId, patch)), [commit])
  const onUpdateOverlay = useCallback((index, patch) => commit(d => interp.updateOverlay(d, index, patch)), [commit])
  const onSetKeyframes = useCallback((index, kfs) => commit(d => interp.setOverlayKeyframes(d, index, kfs)), [commit])
  const onUpsertKeyframe = useCallback((index, kf) => commit(d => interp.upsertKeyframe(d, index, kf)), [commit])
  const onRemoveKeyframe = useCallback((index, ki) => commit(d => interp.removeKeyframe(d, index, ki)), [commit])

  // Clear a selection that went out of range (e.g. after undo/redo/delete) so the inspector
  // doesn't edit a phantom index (which would no-op-but-dirty the doc).
  useEffect(() => {
    if (selection?.kind === 'overlay' && selection.index >= (doc?.overlays || []).length) {
      setSelection(null)
    } else if (selection?.kind === 'audio') {
      const a = doc?.audio || {}
      const exists = selection.audioKind === 'music' ? !!(a.music?.asset_id || doc?.music?.asset_id)
        : selection.audioKind === 'narration' ? !!(a.narration?.segments?.[selection.index])
          : !!(a.sfx?.[selection.index])
      if (!exists) setSelection(null)
    }
  }, [doc, selection])

  // ── keyboard shortcuts ──────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
      if (e.key === ' ' || e.code === 'Space') { e.preventDefault(); setPlaying(p => !p); return } // space = play/pause
      if (e.key === 'Escape') { setSelection(null); return } // deselect → Assets tab
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
          canUndo={past.length > 0} canRedo={future.length > 0}
          dirty={dirty} rendering={rendering} hasRender={!!renderPath} previewMode={previewMode}
          onUndo={undo} onRedo={redo} onSave={save} onRender={render}
          onPreviewMode={changePreviewMode} onAddText={onAddText} onCanvas={onCanvas}
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
        <div className="st-body-top">
          {chat && (panels.agentOpen ? (
            <>
              <div className="st-agent-wrap" style={{ width: panels.agentW }}>
                <ChatPanel chat={chat} disabled={!projectId} className="st-agent" />
              </div>
              <div className="st-vsplit" onPointerDown={(e) => beginPanelDrag(e, 'agent')}
                title="Drag to resize · drag fully left to hide" />
            </>
          ) : (
            <button className="st-reopen st-reopen-v st-reopen-agent" title="Show agent panel"
              onClick={() => setPanels(p => ({ ...p, agentOpen: true, agentW: Math.max(AGENT_MIN, p.agentW) }))}>›</button>
          ))}
          <StudioPreview
            projectId={projectId} doc={doc} canvas={canvas} playhead={playhead}
            previewMode={previewMode} renderPath={renderPath} renderVersion={renderVersion}
            playing={playing} selection={selection}
            onScrub={setPlayhead} onPlayingChange={setPlaying}
            onSelectOverlay={onSelectOverlay} onOverlayPosition={onOverlayPosition} onOverlayDragBegin={onOverlayDragBegin}
          />
          {panels.inspectorOpen ? (
            <>
              <div className="st-vsplit" onPointerDown={(e) => beginPanelDrag(e, 'x')}
                title="Drag to resize · drag fully right to hide" />
              <div className="st-inspector-wrap" style={{ width: panels.inspectorW }}>
                <StudioInspector
                  projectId={projectId} doc={doc} canvas={canvas} ffmpeg={ffmpeg}
                  selCut={selCut} selOverlayIndex={selOverlayIndex} playhead={playhead}
                  selAudio={selAudio} selAudioObj={selAudioObj}
                  assets={assets} sourceMetas={sourceMetas}
                  onUpdateCut={onUpdateCut} onUpdateOverlay={onUpdateOverlay} onNormalizeOverlay={onNormalizeOverlay} onUpdateAudio={onUpdateAudio}
                  onSetKeyframes={onSetKeyframes} onUpsertKeyframe={onUpsertKeyframe} onRemoveKeyframe={onRemoveKeyframe}
                  onAddImage={onAddImage} onAddClip={onAddClip} onAddSfx={onAddSfx} onSetMusic={onSetMusic}
                />
              </div>
            </>
          ) : (
            <button className="st-reopen st-reopen-v" title="Show properties panel"
              onClick={() => setPanels(p => ({ ...p, inspectorOpen: true, inspectorW: Math.max(INSPECTOR_MIN, p.inspectorW) }))}>‹</button>
          )}
        </div>

        {panels.timelineOpen ? (
          <>
            <div className="st-hsplit" onPointerDown={(e) => beginPanelDrag(e, 'y')}
              title="Drag to resize · drag fully down to hide" />
            <div className="st-timeline-wrap" style={{ height: panels.timelineH }}>
              <StudioTimeline
                projectId={projectId} doc={doc} dur={dur} zoom={zoom} playhead={playhead}
                selection={selection} sourceMetas={sourceMetas} playing={playing}
                onSeek={seekFromUser} onSelect={setSelection} onTrim={onTrim} onTrimBegin={onTrimBegin}
                onReorder={onReorder} onZoom={setZoom} onAssetDrop={onAssetDrop}
                onOverlayMove={onOverlayMove} onOverlayTrim={onOverlayTrim} onOverlayDragBegin={onOverlayDragBegin}
                onTogglePlay={togglePlay} onSplit={onSplit} onDuplicate={onDuplicate} onDelete={onDelete}
                onAutoArrange={onAutoArrange}
              />
            </div>
          </>
        ) : (
          <button className="st-reopen st-reopen-h" title="Show timeline"
            onClick={() => setPanels(p => ({ ...p, timelineOpen: true, timelineH: Math.max(TIMELINE_MIN, p.timelineH) }))}>Timeline ▴</button>
        )}
      </div>
    </div>
  )
}
