// Studio timeline — a real pixels-per-second timeline (zoom + scroll). Top to bottom:
// stacked OVERLAY TRACK lanes (highest track on top = highest z, matching the renderer's
// ascending-track compositing), then the MAIN clips lane (cuts: drag-reorder + edge trim),
// then the audio lanes (music / narration / sfx). Every lane carries a STICKY left-hand LABEL
// (pinned during horizontal scroll, like a spreadsheet's frozen first column) with an eye toggle
// that hides that track from the PREVIEW canvas (view-only; the doc/export are untouched).
// Overlays, music regions and narration drag horizontally (move on absolute time) and have
// edge-trim handles; music regions also carry a draggable gain line (↕ volume). Same pure
// pointer-event model as cuts (no HTML5 dnd for in-timeline manipulation; listeners attach on
// pointerdown, tear down on pointerup/cancel/unmount). The ONE sanctioned HTML5-DnD path is a
// cross-panel asset drop from the Assets tab onto a lane. All clamping/structure lives in
// interp.js — this file is pointer math + layout only.

import { useEffect, useRef, useState } from 'react'
import * as interp from '../editor/interp.js'
import { fmtTime, overlayType, groupAudioLanes } from './model.js'

const ASSET_DND = 'application/x-opennolan-asset'

const LABEL_W = 128          // px width of the sticky track-label gutter on the left
const LANE_PAD = LABEL_W + 14 // px gutter before t=0: reserves the label column + a small gap
const DRAG_THRESHOLD = 4     // px before a press becomes a drag
const OV_LANE_H = 28         // px height of one overlay-track lane row (drives dy → track math)
const MUSIC_LANE_H = 44      // px height of the music lane (taller: carries a draggable gain line)

function niceStep(pxPerSec) {
  const target = 90 / pxPerSec // seconds per ~90px
  for (const s of [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]) if (s >= target) return s
  return 600
}

const baseName = (p) => String(p || '').split('/').pop() || p

// Aesthetic inline icons (no emoji per the studio UI convention).
const ICON_EYE = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" /><circle cx="12" cy="12" r="3" />
  </svg>
)
const ICON_EYE_OFF = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
    <line x1="1" y1="1" x2="23" y2="23" />
  </svg>
)

export default function StudioTimeline({
  doc, dur, zoom, playhead, selection, sourceMetas, playing,
  onSeek, onSelect, onTrim, onTrimBegin, onReorder, onZoom, onAssetDrop,
  onOverlayMove, onOverlayTrim, onOverlayDragBegin, onOverlayResolve, onAutoArrange,
  onAudioDragBegin, onMoveSfx, onMoveNarration, onTrimNarration,
  onSetMusicLevels, onTrimMusic, onMoveMusic,
  hidden, onToggleHidden,
  onTogglePlay, onSplit, onDuplicate, onDelete,
}) {
  const hasSel = !!selection
  const scrollRef = useRef(null)
  const dragCleanup = useRef(null) // teardown for an in-flight drag (also runs on unmount)
  const [dropping, setDropping] = useState(false) // asset drag hovering the timeline

  // Never leak window listeners if Studio unmounts (or the timeline remounts) mid-drag.
  useEffect(() => () => { if (dragCleanup.current) dragCleanup.current() }, [])

  const cuts = doc?.cuts || []
  const overlays = doc?.overlays || []
  const audio = interp.audioClips(doc)
  const audioLanes = groupAudioLanes(audio) // one row per kind (music / narration / sfx)
  const starts = interp.cutStarts(doc)
  const { max: maxTrack } = interp.overlayTracks(doc)
  // Lanes drawn for overlays: tracks max..0 PLUS one empty lane on top (track max+1) that
  // invites a NEW track (drop an asset / drag an overlay up). Highest track sits at the top.
  const topTrack = maxTrack + 1
  const trackRows = []
  for (let t = topTrack; t >= 0; t--) trackRows.push(t)

  const isHidden = (key) => !!(hidden && hidden.has && hidden.has(key))

  const contentW = Math.max(dur * zoom, 600) + LANE_PAD * 2
  const step = niceStep(zoom)
  const ticks = []
  for (let t = 0; t <= dur + 1e-6; t += step) ticks.push(t)

  const xToTime = (clientX) => {
    const el = scrollRef.current
    if (!el) return 0
    const rect = el.getBoundingClientRect()
    const px = clientX - rect.left + el.scrollLeft - LANE_PAD
    return Math.max(0, Math.min(dur, px / zoom))
  }

  // A sticky left-hand label for a lane (frozen during horizontal scroll). `hideKey` (when given)
  // wires the eye toggle → hide/show this track in the PREVIEW (view state, not the doc).
  const laneLabel = (name, hideKey, extraClass = '') => {
    const off = hideKey ? isHidden(hideKey) : false
    return (
      <div className={`st-lane-label${off ? ' off' : ''} ${extraClass}`} onPointerDown={(e) => e.stopPropagation()}>
        <span className="st-lane-name">{name}</span>
        {hideKey && (
          <button className="st-lane-eye" aria-pressed={off}
            title={off ? 'Show this track in the preview' : 'Hide this track from the preview'}
            aria-label={off ? `Show ${name}` : `Hide ${name}`}
            onClick={() => onToggleHidden?.(hideKey)}>
            {off ? ICON_EYE_OFF : ICON_EYE}
          </button>
        )}
      </div>
    )
  }

  // One synchronous drag setup. Cut modes: scrub | trim-in | trim-out | press(→reorder).
  // Overlay modes (spec.kind==='overlay'): ov-press(→ov-move) | ov-trim-in | ov-trim-out.
  // Audio modes: aud-press(→sfx/narr/music move) | aud-narr-trim-* | aud-music-trim-* | aud-music-gain.
  // Snapshots everything it needs at pointerdown, so mid-drag re-renders don't disturb it.
  const beginDrag = (e, spec) => {
    e.preventDefault()
    const startX = e.clientX
    const startY = e.clientY
    const ghost = spec.ghost
    let mode = spec.mode
    const cut = spec.cut
    const ov = spec.ov
    const aud = spec.aud
    const index = spec.index
    let didSnap = false // for body moves: snapshot lazily on the first real move

    const teardown = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
      if (ghost) { ghost.style.transform = ''; ghost.style.zIndex = '' }
      dragCleanup.current = null
    }
    // Snapshot LAZILY on the first real move (not at pointerdown) so a bare CLICK on a trim
    // handle / block is a pure select and adds no undo entry (and never wipes the redo stack).
    const snapOnce = () => { if (!didSnap) { spec.onBegin?.(); didSnap = true } }
    const onMove = (ev) => {
      const dx = ev.clientX - startX
      const dy = ev.clientY - startY
      // ── cut modes ──
      if (mode === 'scrub') { onSeek(xToTime(ev.clientX)); return }
      if (mode === 'trim-in') {
        snapOnce()
        const speed = Number(cut.speed) || 1
        onTrim(cut.id, { in_seconds: (Number(cut.in_seconds) || 0) + (dx / zoom) * speed }); return
      }
      if (mode === 'trim-out') {
        snapOnce()
        const speed = Number(cut.speed) || 1
        onTrim(cut.id, { out_seconds: (Number(cut.out_seconds) || 0) + (dx / zoom) * speed }); return
      }
      if (mode === 'press' && Math.abs(dx) > DRAG_THRESHOLD) mode = 'reorder'
      if (mode === 'reorder' && ghost) { ghost.style.transform = `translateX(${dx}px)`; ghost.style.zIndex = 5 }
      // ── overlay modes ──
      if (mode === 'ov-trim-in') {
        snapOnce()
        onOverlayTrim(index, { start_seconds: (Number(ov.start_seconds) || 0) + dx / zoom }); return
      }
      if (mode === 'ov-trim-out') {
        snapOnce()
        onOverlayTrim(index, { end_seconds: (Number(ov.end_seconds) || 0) + dx / zoom }); return
      }
      if (mode === 'ov-press' && (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD)) {
        mode = 'ov-move'
        snapOnce() // one undo step per drag
      }
      if (mode === 'ov-move') {
        const ns = Math.max(0, (Number(ov.start_seconds) || 0) + dx / zoom)
        const dTrack = Math.round(-dy / OV_LANE_H) // drag UP (dy<0) → higher track
        const nt = Math.max(0, Math.min(topTrack, (Math.round(Number(ov.track) || 0)) + dTrack))
        onOverlayMove(index, { start: ns, track: nt }); return
      }
      // ── audio modes ── (sfx/narration/music move + narration/music edge-trim + music gain)
      if (mode === 'aud-press' && spec.moveMode && (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD)) {
        mode = spec.moveMode
        snapOnce()
      }
      if (mode === 'aud-sfx-move') {
        onMoveSfx(index, Math.max(0, (Number(aud.start_seconds) || 0) + dx / zoom)); return
      }
      if (mode === 'aud-narr-move') {
        onMoveNarration(index, Math.max(0, (Number(aud.start_seconds) || 0) + dx / zoom)); return
      }
      if (mode === 'aud-narr-trim-in') {
        snapOnce()
        onTrimNarration(index, { start_seconds: Math.max(0, (Number(aud.start_seconds) || 0) + dx / zoom) }); return
      }
      if (mode === 'aud-narr-trim-out') {
        snapOnce()
        onTrimNarration(index, { end_seconds: Math.max(0, (Number(aud.end_seconds) || 0) + dx / zoom) }); return
      }
      if (mode === 'aud-music-move') {
        onMoveMusic(index, Math.max(0, (Number(aud.start_seconds) || 0) + dx / zoom)); return
      }
      if (mode === 'aud-music-trim-in') {
        snapOnce()
        onTrimMusic(index, { start_seconds: Math.max(0, (Number(aud.start_seconds) || 0) + dx / zoom) }); return
      }
      if (mode === 'aud-music-trim-out') {
        snapOnce()
        onTrimMusic(index, { end_seconds: Math.max(0, (Number(aud.end_seconds) || 0) + dx / zoom) }); return
      }
      if (mode === 'aud-music-gain') {
        snapOnce()
        // drag UP (dy<0) → louder. Map the drag over the gain track height to [0,1].
        onSetMusicLevels(index, { volume: Math.max(0, Math.min(1, spec.origVol - dy / spec.gainH)) }); return
      }
    }
    const onUp = (ev) => {
      const finalMode = mode
      const dx = ev.clientX - startX
      teardown()
      if (finalMode === 'press') { onSelect({ kind: 'cut', id: cut.id }); return } // tap = select
      if (finalMode === 'ov-press') { onSelect({ kind: 'overlay', index }); return } // tap = select
      if (finalMode === 'aud-press') { onSelect({ kind: 'audio', audioKind: spec.audioKind, index }); return } // tap = select
      if (finalMode === 'reorder') {
        const newCenter = starts[index] + interp.cutDuration(cut) / 2 + dx / zoom
        let target = 0
        cuts.forEach((c, i) => {
          if (i === index) return
          if (starts[i] + interp.cutDuration(c) / 2 < newCenter) target++
        })
        if (target !== index) onReorder(index, target)
      }
      // An overlay move/trim that actually moved → auto-float it off any new same-track overlap
      // (folded into the same undo step the start-of-drag snapshot opened, via `live`).
      if (didSnap && (finalMode === 'ov-move' || finalMode === 'ov-trim-in' || finalMode === 'ov-trim-out')) {
        onOverlayResolve?.(index)
      }
      // move / trim / gain already live-applied; the start-of-drag snapshot owns undo.
    }
    const onCancel = () => teardown() // pointercancel: abandon the drag, keep last live value
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
    dragCleanup.current = teardown
  }

  // Cross-panel asset drop onto a specific lane (HTML5 DnD). The lane decides MAIN vs OVERLAY:
  // dropping on the cuts lane adds a main clip; on an overlay track lane adds an overlay at that
  // track; on the audio lane adds sfx/music. `kind` (from the payload) picks the asset type.
  const onLaneDrop = (e, lane, track) => {
    setDropping(false)
    const data = e.dataTransfer.getData(ASSET_DND)
    if (!data || !onAssetDrop) return
    e.preventDefault()
    e.stopPropagation()
    try {
      const { kind, path } = JSON.parse(data)
      onAssetDrop(kind, path, xToTime(e.clientX), { lane, track })
    } catch { /* ignore bad payload */ }
  }
  const dragOver = (e) => {
    if (!onAssetDrop || !e.dataTransfer.types.includes(ASSET_DND)) return
    e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; if (!dropping) setDropping(true)
  }

  return (
    <div className="st-timeline">
      <div className="st-tl-head">
        <div className="st-tl-ops">
          <button className="st-btn" onClick={onSplit} disabled={!hasSel && cuts.length === 0}
            title="Split the selected clip / overlay / music / narration at the playhead (S)">✂ Split</button>
          <button className="st-btn" onClick={onDuplicate} disabled={selection?.kind !== 'cut'} title="Duplicate clip">⧉ Duplicate</button>
          <button className="st-btn st-danger" onClick={onDelete} disabled={!hasSel} title="Delete selection (⌫)">🗑 Delete</button>
          <button className="st-btn" onClick={onAutoArrange} disabled={overlays.length < 2}
            title="Auto-arrange overlapping overlays into separate tracks">⇅ Arrange</button>
        </div>
        <div className="st-tl-center">
          <button className="st-play" onClick={onTogglePlay} title="Play / pause (Space)" aria-label={playing ? 'Pause' : 'Play'}>
            {playing ? '⏸' : '▶'}
          </button>
        </div>
        <div className="st-tl-right">
          <span className="st-tl-dur">{fmtTime(dur)} total</span>
          <span className="st-zoom">
            <button className="st-ico" onClick={() => onZoom(z => Math.max(20, z - 20))} title="Zoom out">－</button>
            <input type="range" min="20" max="240" value={zoom} onChange={(e) => onZoom(Number(e.target.value))} />
            <button className="st-ico" onClick={() => onZoom(z => Math.min(240, z + 20))} title="Zoom in">＋</button>
          </span>
        </div>
      </div>

      {/* Click anywhere in the timeline that ISN'T a clip/overlay/audio block/label (or the ruler,
          which scrubs) → deselect, so the properties panel falls through to the Assets tab. */}
      <div className={`st-tl-scroll${dropping ? ' drop' : ''}`} ref={scrollRef}
        onPointerDown={(e) => { if (!e.target.closest('.st-clip, .st-ov, .st-aud, .st-ruler, .st-lane-label')) onSelect(null) }}
        onDragOver={dragOver}
        onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDropping(false) }}>
        <div className="st-tl-content" style={{ width: contentW, '--st-label-w': `${LABEL_W}px` }}>
          {/* ruler — click/drag to scrub. Its sticky corner freezes the top-left over the label gutter. */}
          <div className="st-ruler" onPointerDown={(e) => { if (e.target.closest('.st-lane-label')) return; onSeek(xToTime(e.clientX)); beginDrag(e, { mode: 'scrub' }) }}>
            <div className="st-lane-label st-ruler-corner" onPointerDown={(e) => e.stopPropagation()} />
            {ticks.map((t, i) => (
              <span key={i} className="st-tick" style={{ left: LANE_PAD + t * zoom }}>{fmtTime(t)}</span>
            ))}
          </div>

          {/* overlay TRACK lanes — highest track on top (= highest z). One empty lane on top
              invites a new track (drop / drag up). Each overlay sits in its own track's lane. */}
          {trackRows.map((track) => {
            const inTrack = overlays
              .map((o, i) => ({ o, i }))
              .filter(({ o }) => (Math.round(Number(o.track) || 0)) === track)
            const isNewLane = track === topTrack
            const hideKey = `ov:${track}`
            const laneOff = !isNewLane && isHidden(hideKey)
            return (
              <div
                key={`ovlane-${track}`}
                className={`st-lane st-lane-ov${isNewLane ? ' st-lane-ov-new' : ''}${laneOff ? ' st-lane-off' : ''}`}
                style={{ height: OV_LANE_H }}
                onDragOver={dragOver}
                onDrop={(e) => onLaneDrop(e, 'overlay', track)}
              >
                {isNewLane ? laneLabel('New track', null, 'muted') : laneLabel(`Overlay ${track + 1}`, hideKey)}
                {inTrack.map(({ o, i }) => {
                  const s = Number(o.start_seconds) || 0
                  const e2 = Number(o.end_seconds) || s
                  const left = LANE_PAD + s * zoom
                  const w = Math.max(10, (e2 - s) * zoom)
                  const sel = selection?.kind === 'overlay' && selection.index === i
                  const ot = overlayType(o)
                  const label = ot === 'text' ? `“${(o.text || 'text').slice(0, 18)}”` : baseName(o.asset_id || ot)
                  return (
                    <div
                      key={i}
                      className={`st-ov ${sel ? 'sel' : ''} ${(o.keyframes || []).length ? 'kf' : ''}`}
                      style={{ left, width: w }}
                      title={label}
                      onPointerDown={(ev) => {
                        if (ev.target.classList.contains('st-trim')) return
                        beginDrag(ev, { kind: 'overlay', mode: 'ov-press', ov: o, index: i, ghost: ev.currentTarget, onBegin: onOverlayDragBegin })
                      }}
                    >
                      <span className="st-trim st-trim-l"
                        onPointerDown={(ev) => { ev.stopPropagation(); beginDrag(ev, { kind: 'overlay', mode: 'ov-trim-in', ov: o, index: i, onBegin: onOverlayDragBegin }) }} />
                      <span className="st-ov-label">{label}</span>
                      <span className="st-trim st-trim-r"
                        onPointerDown={(ev) => { ev.stopPropagation(); beginDrag(ev, { kind: 'overlay', mode: 'ov-trim-out', ov: o, index: i, onBegin: onOverlayDragBegin }) }} />
                    </div>
                  )
                })}
                {isNewLane && <span className="st-lane-empty" style={{ left: LANE_PAD }}>+ drop here (or drag an overlay up) for a new track</span>}
              </div>
            )
          })}

          {/* MAIN clips lane (the base video track) */}
          <div className={`st-lane st-lane-cuts${isHidden('main') ? ' st-lane-off' : ''}`} onDragOver={dragOver} onDrop={(e) => onLaneDrop(e, 'cuts')}>
            {laneLabel('Video', 'main')}
            {cuts.map((c, i) => {
              const left = LANE_PAD + starts[i] * zoom
              const w = Math.max(8, interp.cutDuration(c) * zoom)
              const sel = selection?.kind === 'cut' && selection.id === c.id
              const speed = Number(c.speed) || 1
              return (
                <div
                  key={c.id}
                  className={`st-clip ${sel ? 'sel' : ''}`}
                  style={{ left, width: w }}
                  onPointerDown={(e) => {
                    if (e.target.classList.contains('st-trim')) return
                    beginDrag(e, { mode: 'press', cut: c, index: i, ghost: e.currentTarget })
                  }}
                >
                  <span className="st-trim st-trim-l"
                    onPointerDown={(e) => { e.stopPropagation(); beginDrag(e, { mode: 'trim-in', cut: c, onBegin: onTrimBegin }) }} />
                  <span className="st-clip-label">{baseName(c.source)}{speed !== 1 ? ` · ${speed}×` : ''}</span>
                  <span className="st-clip-time">{fmtTime(interp.cutDuration(c))}</span>
                  <span className="st-trim st-trim-r"
                    onPointerDown={(e) => { e.stopPropagation(); beginDrag(e, { mode: 'trim-out', cut: c, onBegin: onTrimBegin }) }} />
                </div>
              )
            })}
          </div>

          {/* audio lanes — one row PER KIND (music / narration / sfx). Music regions are trimmable
              (edge handles) + moveable (drag the body) + split-able, and carry a draggable gain line
              (↕ volume) with a non-interactive fade-shape preview (fades edited in the properties
              panel). Narration blocks move + edge-trim; SFX are draggable point markers. A bare tap
              selects → properties panel. */}
          {audioLanes.length ? audioLanes.map((row) => {
            const hideKey = `aud:${row.kind}`
            const laneName = row.kind === 'music' ? 'Music' : row.kind === 'narration' ? 'Narration' : 'SFX'
            return (
            <div key={`aud-${row.kind}`} className={`st-lane st-lane-audio st-lane-audio-${row.kind}${isHidden(hideKey) ? ' st-lane-off' : ''}`}
              style={row.kind === 'music' ? { height: MUSIC_LANE_H } : undefined}
              onDragOver={dragOver} onDrop={(e) => onLaneDrop(e, 'audio')}>
              {laneLabel(laneName, hideKey)}
              {row.items.map((a) => {
                const left = LANE_PAD + a.start_seconds * zoom
                const w = a.point ? 12 : Math.max(8, (a.end_seconds - a.start_seconds) * zoom)
                const sel = selection?.kind === 'audio' && selection.audioKind === a.kind && selection.index === a.index

                if (a.kind === 'music') {
                  const vol = a.volume != null ? Math.max(0, Math.min(1, Number(a.volume))) : 1
                  const fadeIn = Math.max(0, Number(a.fade_in_seconds) || 0)
                  const fadeOut = Math.max(0, Number(a.fade_out_seconds) || 0)
                  const bedTop = 6, bedH = MUSIC_LANE_H - 12, gainTravel = bedH - 6
                  const gainY = bedTop + (1 - vol) * gainTravel
                  return (
                    <div key={`m-${a.index}`} className={`st-aud st-aud-music${sel ? ' sel' : ''}`}
                      style={{ left, width: w, top: bedTop, height: bedH }}
                      title={`music · ${baseName(a.asset_id)} · vol ${Math.round(vol * 100)}% — drag to move, edges to trim`}
                      onPointerDown={(ev) => {
                        if (ev.target.classList.contains('st-trim') || ev.target.classList.contains('st-aud-gain')) return
                        beginDrag(ev, { mode: 'aud-press', moveMode: 'aud-music-move', audioKind: 'music', aud: a, index: a.index, onBegin: onAudioDragBegin })
                      }}>
                      {fadeIn > 0 && <span className="st-aud-fade st-aud-fade-in" style={{ width: Math.max(4, fadeIn * zoom) }} />}
                      {fadeOut > 0 && <span className="st-aud-fade st-aud-fade-out" style={{ width: Math.max(4, fadeOut * zoom) }} />}
                      <span className="st-aud-name">{baseName(a.asset_id)}</span>
                      <span className="st-aud-gain" style={{ top: gainY }}
                        title={`volume ${Math.round(vol * 100)}% — drag ↕`}
                        onPointerDown={(ev) => { ev.stopPropagation(); beginDrag(ev, { mode: 'aud-music-gain', origVol: vol, gainH: gainTravel, index: a.index, onBegin: onAudioDragBegin }) }} />
                      <span className="st-trim st-trim-l"
                        onPointerDown={(ev) => { ev.stopPropagation(); beginDrag(ev, { mode: 'aud-music-trim-in', aud: a, index: a.index, onBegin: onAudioDragBegin }) }} />
                      <span className="st-trim st-trim-r"
                        onPointerDown={(ev) => { ev.stopPropagation(); beginDrag(ev, { mode: 'aud-music-trim-out', aud: a, index: a.index, onBegin: onAudioDragBegin }) }} />
                    </div>
                  )
                }

                if (a.kind === 'narration') {
                  return (
                    <div key={`n-${a.index}`} className={`st-aud st-aud-narration${sel ? ' sel' : ''}`}
                      style={{ left, width: w }}
                      title={`narration · ${baseName(a.asset_id)}`}
                      onPointerDown={(ev) => {
                        if (ev.target.classList.contains('st-trim')) return
                        beginDrag(ev, { mode: 'aud-press', moveMode: 'aud-narr-move', audioKind: 'narration', aud: a, index: a.index, onBegin: onAudioDragBegin })
                      }}>
                      <span className="st-trim st-trim-l"
                        onPointerDown={(ev) => { ev.stopPropagation(); beginDrag(ev, { mode: 'aud-narr-trim-in', aud: a, index: a.index, onBegin: onAudioDragBegin }) }} />
                      <span className="st-aud-name">{baseName(a.asset_id)}</span>
                      <span className="st-trim st-trim-r"
                        onPointerDown={(ev) => { ev.stopPropagation(); beginDrag(ev, { mode: 'aud-narr-trim-out', aud: a, index: a.index, onBegin: onAudioDragBegin }) }} />
                    </div>
                  )
                }

                // SFX — a draggable point marker (drag to move in time).
                return (
                  <div key={`s-${a.index}`} className={`st-aud st-aud-sfx pt${sel ? ' sel' : ''}`}
                    style={{ left, width: w }}
                    title={`sfx · ${baseName(a.asset_id)} — drag to move`}
                    onPointerDown={(ev) => beginDrag(ev, { mode: 'aud-press', moveMode: 'aud-sfx-move', audioKind: 'sfx', aud: a, index: a.index, onBegin: onAudioDragBegin })} />
                )
              })}
            </div>
            )
          }) : (
            <div className="st-lane st-lane-audio" onDragOver={dragOver} onDrop={(e) => onLaneDrop(e, 'audio')}>
              {laneLabel('Audio', null, 'muted')}
              <span className="st-lane-empty" style={{ left: LANE_PAD }}>no audio — music / narration / SFX appear here</span>
            </div>
          )}

          {/* Playhead. Driven by `transform`, NOT `left`: this moves every animation frame
              during playback and scrub, and `left` forces layout each time. transform is
              composited, which also removes the sub-pixel shimmer on the 2px line. */}
          <div className="st-playhead"
            style={{ left: LANE_PAD, transform: `translateX(${playhead * zoom}px)` }} />
        </div>
      </div>
    </div>
  )
}
