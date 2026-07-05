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
import { isFfmpeg, newTextOverlay, newImageOverlay, newVideoOverlay, summarizeDocChange } from './model.js'
import StudioToolbar from './StudioToolbar.jsx'
import StudioTimeline from './StudioTimeline.jsx'
import StudioInspector from './StudioInspector.jsx'
import StudioPreview from './StudioPreview.jsx'
import ChatPanel from '../chat/ChatPanel.jsx'
import dbg from '../debug/recorder.js'

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

export default function Studio({ projectId, state, onClose, chat, auth, onReconnect }) {
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

  const [assets, setAssets] = useState({ kinds: { images: [], video: [], audio: [], music: [] }, renders: [], agent_renders: [] })
  const [sourceMetas, setSourceMetas] = useState({}) // ref -> {duration,width,height}
  const [zoom, setZoom] = useState(80) // px per second

  // ── per-track PREVIEW hide (view state, NOT the doc) — a Set of track keys the preview canvas
  // skips: `main`, `ov:<track>`, `aud:music|narration|sfx`. It's an editing aid only (the eye
  // toggle in each timeline lane label); the saved doc and the exported/rendered MP4 are untouched,
  // so preview==export holds for the render. Persisted to localStorage like the panel layout. ──
  const HIDDEN_KEY = 'st.hidden.v1'
  const [hiddenTracks, setHiddenTracks] = useState(() => {
    try { return new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) || '[]')) } catch { return new Set() }
  })
  useEffect(() => { try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...hiddenTracks])) } catch { /* ignore */ } }, [hiddenTracks])
  const onToggleHidden = useCallback((key) => {
    setHiddenTracks(prev => { const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next })
  }, [])

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
    dbg.event('ui.togglePlay', { toPlaying: !playing, playhead })
    setPlaying(!playing)
  }, [playing, playhead, doc])
  const seekFromUser = useCallback((t) => { dbg.event('ui.seek', { t: Math.round(t * 1000) / 1000 }); setPlaying(false); setPlayhead(t) }, [])
  const changePreviewMode = useCallback((m) => { dbg.event('ui.previewMode', { mode: m }); setPlaying(false); setPreviewMode(m) }, [])

  // Dev observability: re-arm the session recorder if a session was left running before a reload
  // (survives an accidental ⌘R). The toggle lives in the toolbar; see web/src/debug/recorder.js.
  const recording = dbg.useRecording()
  useEffect(() => { dbg.resumeIfActive({ projectId }) }, [projectId])
  const onToggleRecord = useCallback(() => {
    const session = dbg.toggle({ projectId, canvas: `${canvas.width}×${canvas.height}` })
    if (dbg.isRecording()) flash('ok', `Debug recording on → .agents/tools/logs/ui-sessions/${session}.ndjson`)
    else flash('ok', 'Debug recording stopped — session saved')
  }, [projectId, canvas, flash])

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

  // Live update, NO history push (per-frame during a coalesced drag). Emits a throttled
  // `edit.live` to the debug recorder with a diff summary (which cut/overlay/field the drag touched).
  const live = useCallback((next) => {
    const prev = docRef.current
    const nd = typeof next === 'function' ? next(prev) : next
    if (nd === prev) return
    dbg.event('edit.live', summarizeDocChange(prev, nd))
    setDocBoth(nd); setDirty(true)
  }, [setDocBoth])

  // Discrete edit = snapshot + apply (one undo step). Referential no-op = no history. Emits
  // `edit.commit` with a diff summary — the semantic record of EVERY discrete edit (trim/split/
  // delete/reorder/add/overlay/text/settings/keyframes/canvas/audio) for the debug session trace.
  const commit = useCallback((next) => {
    const prev = docRef.current
    const nd = typeof next === 'function' ? next(prev) : next
    if (nd === prev) return
    dbg.event('edit.commit', summarizeDocChange(prev, nd))
    setPast(p => [...p, prev].slice(-100))
    setFuture([])
    setDocBoth(nd); setDirty(true)
  }, [setDocBoth])

  const undo = useCallback(() => {
    if (!past.length) return
    const prev = past[past.length - 1]
    dbg.event('edit.undo', summarizeDocChange(docRef.current, prev))
    setPast(past.slice(0, -1))
    setFuture([docRef.current, ...future])
    setDocBoth(prev); setDirty(true)
  }, [past, future, setDocBoth])

  const redo = useCallback(() => {
    if (!future.length) return
    const nxt = future[0]
    dbg.event('edit.redo', summarizeDocChange(docRef.current, nxt))
    setFuture(future.slice(1))
    setPast([...past, docRef.current])
    setDocBoth(nxt); setDirty(true)
  }, [past, future, setDocBoth])

  // ── save / autosave / render ──────────────────────────────────────────────
  // One persist path (reads docRef so it always writes the LATEST doc, never a stale closure).
  // `silent` = autosave (no toasts). Refuses while the agent is mid-turn so we never clobber its
  // write; the editor's edits land the instant its turn ends (autosave resumes).
  const persist = useCallback(async ({ silent } = {}) => {
    const d = docRef.current
    if (!d) return null
    if (agentBusyRef.current || reconcilingRef.current) {
      dbg.event('ui.save', { silent: !!silent, result: 'blocked-agent' })
      if (!silent) flash('warn', 'Agent is editing — your changes will save the moment its turn ends')
      return null
    }
    try {
      await api.saveEditDecisions(projectId, d)
      savedRef.current = JSON.stringify(d)
      setDirty(false)
      dbg.event('ui.save', { silent: !!silent, result: 'ok' })
      if (!silent) flash('ok', 'Saved')
      return d
    } catch (e) {
      dbg.event('ui.save', { silent: !!silent, result: 'rejected', error: String(e.message || e).slice(0, 200) })
      if (!silent) flash('err', `Save rejected: ${String(e.message || e)}`)
      return null
    }
  }, [projectId, flash])

  const save = useCallback(() => persist({ silent: false }), [persist])
  // Flush a pending autosave NOW (used before handing a turn to the agent so it reads our latest).
  const flushAutosave = useCallback(async () => { if (dirtyRef.current) await persist({ silent: true }) }, [persist])

  // Autosave (debounced): the on-disk edit_decisions.json is the single source of truth shared with
  // the agent, so we keep it current instead of waiting for a manual Save. Suspended during/around
  // an agent turn (the reconcile effect owns the disk then).
  useEffect(() => {
    if (!dirty || chatBusy || agentBusyRef.current || reconcilingRef.current) return
    const id = setTimeout(() => { persist({ silent: true }) }, 700)
    return () => clearTimeout(id)
  }, [dirty, doc, chatBusy, persist])

  // Hand the agent our LATEST edits: flush a pending autosave before its turn starts, so it reads the
  // timeline we actually see (not a stale disk copy). Wrap only `send`; everything else passes through.
  const chatForPanel = useMemo(() => (
    chat ? { ...chat, send: async (text) => { await flushAutosave(); return chat.send(text) } } : chat
  ), [chat, flushAutosave])

  const render = useCallback(async () => {
    if (rendering) return
    dbg.event('ui.render', { phase: 'start' })
    const saved = await save()
    if (!saved) { dbg.event('ui.render', { phase: 'abort', reason: 'save-failed' }); return }
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
          dbg.event('ui.render', { phase: 'done', warnings: st.warnings || [] })
          flash('ok', w ? `Rendered — ${w}` : 'Rendered')
          return
        }
        if (st.status === 'failed') { dbg.event('ui.render', { phase: 'failed', error: st.error || 'unknown' }); flash('err', `Render failed: ${st.error || 'unknown'}`); return }
      }
      dbg.event('ui.render', { phase: 'timeout' })
      flash('err', 'Render timed out')
    } catch (e) {
      dbg.event('ui.render', { phase: 'error', error: String(e.message || e).slice(0, 200) })
      flash('err', `Render error: ${String(e.message || e)}`)
    } finally {
      if (jobRef.current === myJob) setRendering(false)
    }
  }, [rendering, save, projectId, flash])

  // When the in-editor agent finishes a turn (busy true→false), it may have rewritten
  // edit_decisions.json on disk. Adopt its new timeline LIVE into the editor — no "reopen". The user
  // and the agent share ONE source of truth (the on-disk doc); autosave keeps our edits there before
  // the turn, so by turn-end the disk is "our edits + the agent's on top" and adopting loses nothing.
  // If the user DID edit during the turn (autosave is suspended then), we still adopt the agent's
  // result but push the user's mid-turn doc onto the undo stack, so ⌘Z restores their version.
  // `agentBusyRef` is the previous-busy tracker + save-guard mirror; `reconcilingRef` holds the guard
  // open across the async re-fetch so an autosave/Save can't race it.
  useEffect(() => {
    const was = agentBusyRef.current
    agentBusyRef.current = chatBusy
    if (!(was && !chatBusy) || !projectId) return // only act on a turn that just ended
    let alive = true
    reconcilingRef.current = true
    // The agent may have rendered new HyperFrames clips into hf/renders this turn — refresh the
    // Assets tabs (incl. the Renders tab) so they show up without reopening the project.
    api.listAssets(projectId).then(a => { if (alive) setAssets(a) }).catch(() => {})
    api.getEditDecisions(projectId)
      .then(({ content }) => {
        if (!alive || !content) return
        const incoming = JSON.stringify(content)
        if (incoming === savedRef.current) return // disk matches our last save — nothing changed
        const hadLocal = dirtyRef.current
        dbg.event('agent.adopt', { hadLocalEdits: hadLocal, ...summarizeDocChange(docRef.current, content) })
        if (hadLocal) { setPast(p => [...p, docRef.current].slice(-100)); setFuture([]) } // keep ⌘Z to user's version
        docRef.current = content; setDoc(content); savedRef.current = incoming; setDirty(false)
        flash('ok', hadLocal ? 'Timeline updated by the agent — ⌘Z to restore your version' : 'Timeline updated by the agent')
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
    : selAudio.audioKind === 'music' ? (interp.musicRegions(doc)[selAudio.index] || null)
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

  // Split acts on WHATEVER is selected at the playhead — the selected overlay / music region /
  // narration segment, else the main cut under the playhead. Each pure mutator returns the same
  // doc ref when the playhead isn't strictly inside that item, which we surface as a hint.
  const onSplit = useCallback(() => {
    const before = docRef.current
    const sel = selection
    let nd = before
    let hint = 'Move the playhead inside a clip to split it'
    if (sel?.kind === 'overlay') {
      nd = interp.splitOverlay(before, sel.index, playhead)
      hint = 'Move the playhead inside the selected overlay to split it'
    } else if (sel?.kind === 'audio') {
      if (sel.audioKind === 'music') {
        nd = interp.splitMusic(before, sel.index, playhead)
        hint = 'Move the playhead inside the music region to split it'
      } else if (sel.audioKind === 'narration') {
        nd = interp.splitNarration(before, sel.index, playhead)
        hint = 'Move the playhead inside the narration segment to split it'
      } else {
        flash('warn', 'A sound effect is a single cue — there’s nothing to split'); return
      }
    } else {
      nd = interp.splitCutAtPlayhead(before, playhead)
    }
    if (nd === before) { flash('warn', hint); return }
    commit(nd)
  }, [selection, playhead, commit, flash])

  const onDelete = useCallback(() => {
    if (selCut) {
      if ((doc?.cuts || []).length <= 1) { flash('warn', 'Can’t delete the last clip'); return }
      commit(d => interp.removeCut(d, selCut.id))
      setSelection(null)
    } else if (selOverlayIndex >= 0) {
      commit(d => interp.removeOverlay(d, selOverlayIndex))
      setSelection(null)
    } else if (selAudio) {
      if (selAudio.audioKind === 'music') commit(d => interp.removeMusic(d, selAudio.index))
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
  // Drop a music asset onto the timeline: the FIRST bed spans the whole timeline (background music);
  // a subsequent drop adds a bounded region at the drop time (its length from the asset duration).
  const onAddMusic = useCallback((path, atTime) => {
    if (interp.musicRegions(docRef.current).length === 0) { commit(d => interp.setMusic(d, path)); flash('ok', 'Music set'); return }
    const start = atTime != null ? Math.max(0, atTime) : playhead
    const meta = sourceMetas[path]
    const end = start + (meta?.duration ? Math.min(meta.duration, 30) : 12)
    commit(d => interp.addMusic(d, path, { start, end })); flash('ok', 'Music region added')
  }, [commit, flash, playhead, sourceMetas])
  const onAddSfx = useCallback((path, atTime) => { commit(d => interp.addSfx(d, path, atTime != null ? atTime : playhead)) }, [commit, playhead])

  // Edit the selected audio item (music bed / narration segment / sfx) from the properties panel.
  const onUpdateAudio = useCallback((patch) => {
    if (!selAudio) return
    if (selAudio.audioKind === 'music') commit(d => interp.updateMusic(d, selAudio.index, patch))
    else if (selAudio.audioKind === 'narration') commit(d => interp.updateNarration(d, selAudio.index, patch))
    else commit(d => interp.updateSfx(d, selAudio.index, patch))
  }, [selAudio, commit])
  // Per-frame variant used while DRAGGING a scrub field (no history; onScrubBegin snapshotted once).
  const onLiveUpdateAudio = useCallback((patch) => {
    if (!selAudio) return
    if (selAudio.audioKind === 'music') live(d => interp.updateMusic(d, selAudio.index, patch))
    else if (selAudio.audioKind === 'narration') live(d => interp.updateNarration(d, selAudio.index, patch))
    else live(d => interp.updateSfx(d, selAudio.index, patch))
  }, [selAudio, live])

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
    if (kind === 'music') { onAddMusic(path, t); return } // first drop = full bed, else a region at t
    if (kind === 'audio') { onAddSfx(path, t); return }
    if (lane === 'cuts') { onAddClip(path, t); return } // image or video → main-timeline clip
    if (kind === 'video') onAddVideoOverlay(path, t, track)
    else onAddImage(path, t, track) // images → image overlay at the dropped track
  }, [onAddImage, onAddVideoOverlay, onAddClip, onAddMusic, onAddSfx])

  // Overlay timeline drag (feat 3): move on absolute time + change track, or edge-trim. Uses the
  // live/snapshot pattern — onOverlayDragBegin snapshots once at pointerdown so a whole drag is one
  // undo step; the per-frame move/trim go through `live` (no history).
  const onOverlayMove = useCallback((index, patch) => live(d => interp.moveOverlay(d, index, patch)), [live])
  const onOverlayTrim = useCallback((index, patch) => live(d => interp.trimOverlay(d, index, patch)), [live])
  const onOverlayDragBegin = useCallback(() => snapshot(), [snapshot])
  // On drag-end: float the just-moved/trimmed overlay off any new same-track overlap. `live` (no
  // new history) folds it into the one undo step the drag's start-of-move snapshot already opened.
  const onOverlayResolve = useCallback((index) => live(d => interp.resolveOverlayOverlap(d, index)), [live])

  // Audio drag on the timeline (feat: full NLE audio editing) — same snapshot-once/live pattern.
  // onAudioDragBegin snapshots once at pointerdown so the whole drag is ONE undo step; per-frame
  // moves/trims/level-changes go through `live` (no history). These target an explicit index (the
  // block being dragged), unlike the properties-panel onUpdateAudio which edits the selection.
  const onAudioDragBegin = useCallback(() => snapshot(), [snapshot])
  const onMoveSfx = useCallback((index, start) => live(d => interp.updateSfx(d, index, { start_seconds: start })), [live])
  const onMoveNarration = useCallback((index, start) => live(d => interp.moveNarration(d, index, start)), [live])
  const onTrimNarration = useCallback((index, patch) => live(d => interp.updateNarration(d, index, patch)), [live])
  // Music regions dragged directly on the lane: gain line (volume), edge-trim, body-move — each
  // targets the region by index (there can be several after a split).
  const onSetMusicLive = useCallback((index, patch) => live(d => interp.updateMusic(d, index, patch)), [live])
  const onTrimMusic = useCallback((index, patch) => live(d => interp.trimMusic(d, index, patch)), [live])
  const onMoveMusic = useCallback((index, start) => live(d => interp.moveMusic(d, index, start)), [live])

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

  // Canvas drag-to-move a MAIN clip: merge {x,y} (canvas px) into transform.position, preserving
  // scale/crop. Live (per-frame, no history); onClipDragBegin snapshotted once at pointerdown.
  const onClipPosition = useCallback((cutId, xy) => live(d => {
    const cut = d?.cuts?.find(c => c.id === cutId)
    if (!cut) return d
    const transform = { ...(cut.transform || {}), position: { x: Math.round(xy.x), y: Math.round(xy.y) } }
    return interp.updateCut(d, cutId, { transform })
  }), [live])

  // Project background (metadata.background) — one commit per change.
  const onSetBackground = useCallback((bg) => commit(d => interp.setBackground(d, bg)), [commit])

  // Upload an asset from the editor's Assets panel (same as the pipeline page) → re-list so it shows.
  const refreshAssets = useCallback(() => {
    api.listAssets(projectId).then(a => setAssets(a)).catch(() => {})
  }, [projectId])
  const onUploadAsset = useCallback(async (kind, file) => {
    if (!file) return
    try { await api.uploadAsset(projectId, kind, file); refreshAssets(); dbg.event('ui.uploadAsset', { kind, name: file.name, bytes: file.size }); flash('ok', `Uploaded ${file.name}`) }
    catch (e) { dbg.event('ui.uploadAsset', { kind, name: file?.name, result: 'failed', error: String(e.message || e).slice(0, 200) }); flash('err', `Upload failed: ${String(e.message || e)}`) }
  }, [projectId, refreshAssets, flash])

  // Quietly self-heal an agent-authored image/video overlay whose position is a string anchor
  // (the renderer rejects that for non-text). Routed through `live` — NOT `commit` — so merely
  // SELECTING such an overlay never pushes a phantom undo step or wipes the redo stack.
  const onNormalizeOverlay = useCallback((index, patch) => live(d => interp.updateOverlay(d, index, patch)), [live])

  const onCanvas = useCallback(({ width, height }) => {
    commit(d => interp.setCanvas(d, { width, height }))
  }, [commit])

  const onUpdateCut = useCallback((cutId, patch) => commit(d => interp.updateCut(d, cutId, patch)), [commit])
  const onUpdateOverlay = useCallback((index, patch) => commit(d => interp.updateOverlay(d, index, patch)), [commit])
  // Live (no-history) variants for scrub-field DRAGS — `onScrubBegin` (= snapshot) opens ONE undo
  // step at the start of the drag, then each frame applies through `live`. Typing / arrow keys still
  // go through the commit handlers above (one undo step each).
  const onLiveUpdateCut = useCallback((cutId, patch) => live(d => interp.updateCut(d, cutId, patch)), [live])
  const onLiveUpdateOverlay = useCallback((index, patch) => live(d => interp.updateOverlay(d, index, patch)), [live])
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
      const exists = selection.audioKind === 'music' ? !!interp.musicRegions(doc)[selection.index]
        : selection.audioKind === 'narration' ? !!(a.narration?.segments?.[selection.index])
          : !!(a.sfx?.[selection.index])
      if (!exists) setSelection(null)
    }
  }, [doc, selection])

  // Debug recorder: log every selection change (from any source — timeline, canvas, keyboard, add)
  // so the trace shows what the user had selected when an edit happened.
  useEffect(() => {
    dbg.event('ui.select', selection
      ? { kind: selection.kind, id: selection.id, index: selection.index, audioKind: selection.audioKind }
      : { kind: null })
  }, [selection])

  // ── keyboard shortcuts ──────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target
      // Don't fire global shortcuts while typing in a field OR while a scrub bar (role=slider) is
      // focused — it owns Space/Escape/arrows/Delete so they don't also toggle play / clear selection.
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable
        || (t.getAttribute && t.getAttribute('role') === 'slider'))) return
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
          recording={recording} onToggleRecord={onToggleRecord}
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
                <ChatPanel chat={chatForPanel} disabled={!projectId} className="st-agent"
                  auth={auth} onReconnect={onReconnect} />
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
            playing={playing} selection={selection} sourceMetas={sourceMetas} hidden={hiddenTracks}
            onScrub={setPlayhead} onPlayingChange={setPlaying}
            onSelectOverlay={onSelectOverlay} onOverlayPosition={onOverlayPosition} onOverlayDragBegin={onOverlayDragBegin}
            onClipPosition={onClipPosition} onClipDragBegin={snapshot}
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
                  onLiveUpdateCut={onLiveUpdateCut} onLiveUpdateOverlay={onLiveUpdateOverlay} onLiveUpdateAudio={onLiveUpdateAudio} onScrubBegin={snapshot}
                  onSetKeyframes={onSetKeyframes} onUpsertKeyframe={onUpsertKeyframe} onRemoveKeyframe={onRemoveKeyframe}
                  onAddImage={onAddImage} onAddClip={onAddClip} onAddSfx={onAddSfx} onSetMusic={onSetMusic}
                  onSetBackground={onSetBackground} onUploadAsset={onUploadAsset}
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
                onOverlayResolve={onOverlayResolve}
                onAudioDragBegin={onAudioDragBegin} onMoveSfx={onMoveSfx} onMoveNarration={onMoveNarration}
                onTrimNarration={onTrimNarration} onSetMusicLevels={onSetMusicLive}
                onTrimMusic={onTrimMusic} onMoveMusic={onMoveMusic}
                hidden={hiddenTracks} onToggleHidden={onToggleHidden}
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
