// Studio timeline — a real pixels-per-second timeline (zoom + scroll), a clips lane with
// drag-to-reorder + edge trim handles, and an overlays lane on absolute project time.
// Pure pointer-event interactions (no HTML5 dnd) so trim/scrub/reorder share one model;
// listeners are attached synchronously on pointerdown and torn down on pointerup. All
// clamping/structure lives in interp.js — this file is just pointer math + layout.

import { useEffect, useRef, useState } from 'react'
import * as interp from '../editor/interp.js'
import { fmtTime } from './model.js'

const ASSET_DND = 'application/x-opennolan-asset'

const LANE_PAD = 12          // px gutter at the left of the lanes
const DRAG_THRESHOLD = 4     // px before a press becomes a reorder drag

function niceStep(pxPerSec) {
  const target = 90 / pxPerSec // seconds per ~90px
  for (const s of [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]) if (s >= target) return s
  return 600
}

const baseName = (p) => String(p || '').split('/').pop() || p

export default function StudioTimeline({
  doc, dur, zoom, playhead, selection, sourceMetas, playing,
  onSeek, onSelect, onTrim, onTrimBegin, onReorder, onZoom, onAssetDrop,
  onTogglePlay, onSplit, onDuplicate, onDelete,
}) {
  const hasCut = selection?.kind === 'cut'
  const hasSel = !!selection
  const scrollRef = useRef(null)
  const dragCleanup = useRef(null) // teardown for an in-flight drag (also runs on unmount)
  const [dropping, setDropping] = useState(false) // asset drag hovering the timeline

  // Never leak window listeners if Studio unmounts (or the timeline remounts) mid-drag.
  useEffect(() => () => { if (dragCleanup.current) dragCleanup.current() }, [])

  const cuts = doc?.cuts || []
  const overlays = doc?.overlays || []
  const audio = interp.audioClips(doc)
  const starts = interp.cutStarts(doc)
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

  // One synchronous drag setup. `spec.mode` ∈ scrub | trim-in | trim-out | press.
  // Snapshots everything it needs at pointerdown, so mid-drag re-renders don't disturb it.
  const beginDrag = (e, spec) => {
    e.preventDefault()
    const startX = e.clientX
    const ghost = spec.ghost
    let mode = spec.mode
    const cut = spec.cut
    const index = spec.index

    const teardown = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
      if (ghost) { ghost.style.transform = ''; ghost.style.zIndex = '' }
      dragCleanup.current = null
    }
    const onMove = (ev) => {
      const dx = ev.clientX - startX
      if (mode === 'scrub') { onSeek(xToTime(ev.clientX)); return }
      if (mode === 'trim-in') {
        const speed = Number(cut.speed) || 1
        onTrim(cut.id, { in_seconds: (Number(cut.in_seconds) || 0) + (dx / zoom) * speed }); return
      }
      if (mode === 'trim-out') {
        const speed = Number(cut.speed) || 1
        onTrim(cut.id, { out_seconds: (Number(cut.out_seconds) || 0) + (dx / zoom) * speed }); return
      }
      if (mode === 'press' && Math.abs(dx) > DRAG_THRESHOLD) mode = 'reorder'
      if (mode === 'reorder' && ghost) { ghost.style.transform = `translateX(${dx}px)`; ghost.style.zIndex = 5 }
    }
    const onUp = (ev) => {
      const finalMode = mode
      const dx = ev.clientX - startX
      teardown()
      if (finalMode === 'press') { onSelect({ kind: 'cut', id: cut.id }); return } // tap = select
      if (finalMode === 'reorder') {
        const newCenter = starts[index] + interp.cutDuration(cut) / 2 + dx / zoom
        let target = 0
        cuts.forEach((c, i) => {
          if (i === index) return
          if (starts[i] + interp.cutDuration(c) / 2 < newCenter) target++
        })
        if (target !== index) onReorder(index, target)
      }
    }
    const onCancel = () => teardown() // pointercancel: abandon the drag, keep last live value
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
    dragCleanup.current = teardown
  }

  return (
    <div className="st-timeline">
      <div className="st-tl-head">
        <div className="st-tl-ops">
          <button className="st-play" onClick={onTogglePlay} title="Play / pause (Space)" aria-label={playing ? 'Pause' : 'Play'}>
            {playing ? '⏸' : '▶'}
          </button>
          <button className="st-btn" onClick={onSplit} title="Split at playhead (S)">✂ Split</button>
          <button className="st-btn" onClick={onDuplicate} disabled={!hasCut} title="Duplicate clip">⧉ Duplicate</button>
          <button className="st-btn st-danger" onClick={onDelete} disabled={!hasSel} title="Delete selection (⌫)">🗑 Delete</button>
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

      {/* Click anywhere in the timeline that ISN'T a clip/overlay/audio block (or the ruler,
          which scrubs) → deselect, so the properties panel falls through to the Assets tab.
          A single handler on the scroll viewport is robust even when clips fill every lane. */}
      <div className={`st-tl-scroll${dropping ? ' drop' : ''}`} ref={scrollRef}
        onPointerDown={(e) => { if (!e.target.closest('.st-clip, .st-ov, .st-aud, .st-ruler')) onSelect(null) }}
        onDragOver={(e) => {
          if (!onAssetDrop || !e.dataTransfer.types.includes(ASSET_DND)) return
          e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; if (!dropping) setDropping(true)
        }}
        onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDropping(false) }}
        onDrop={(e) => {
          setDropping(false)
          const data = e.dataTransfer.getData(ASSET_DND)
          if (!data || !onAssetDrop) return
          e.preventDefault()
          try { const { kind, path } = JSON.parse(data); onAssetDrop(kind, path, xToTime(e.clientX)) } catch { /* ignore bad payload */ }
        }}>
        <div className="st-tl-content" style={{ width: contentW }}>
          {/* ruler — click/drag to scrub */}
          <div className="st-ruler" onPointerDown={(e) => { onSeek(xToTime(e.clientX)); beginDrag(e, { mode: 'scrub' }) }}>
            {ticks.map((t, i) => (
              <span key={i} className="st-tick" style={{ left: LANE_PAD + t * zoom }}>{fmtTime(t)}</span>
            ))}
          </div>

          {/* clips lane */}
          <div className="st-lane st-lane-cuts">
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
                    onPointerDown={(e) => { e.stopPropagation(); onTrimBegin?.(); beginDrag(e, { mode: 'trim-in', cut: c }) }} />
                  <span className="st-clip-label">{baseName(c.source)}{speed !== 1 ? ` · ${speed}×` : ''}</span>
                  <span className="st-clip-time">{fmtTime(interp.cutDuration(c))}</span>
                  <span className="st-trim st-trim-r"
                    onPointerDown={(e) => { e.stopPropagation(); onTrimBegin?.(); beginDrag(e, { mode: 'trim-out', cut: c }) }} />
                </div>
              )
            })}
          </div>

          {/* overlays lane */}
          <div className="st-lane st-lane-ov">
            {overlays.map((o, i) => {
              const s = Number(o.start_seconds) || 0
              const e = Number(o.end_seconds) || s
              const left = LANE_PAD + s * zoom
              const w = Math.max(8, (e - s) * zoom)
              const sel = selection?.kind === 'overlay' && selection.index === i
              const isText = o.type === 'text' || (o.text != null && o.asset_id == null)
              const label = isText ? `“${(o.text || 'text').slice(0, 18)}”` : baseName(o.asset_id || 'image')
              return (
                <button
                  key={i}
                  className={`st-ov ${sel ? 'sel' : ''} ${(o.keyframes || []).length ? 'kf' : ''}`}
                  style={{ left, width: w }}
                  onClick={() => onSelect({ kind: 'overlay', index: i })}
                  title={label}
                >{label}</button>
              )
            })}
            {!overlays.length && <span className="st-lane-empty">no overlays — add Text/Image above</span>}
          </div>

          {/* audio lane — music / narration / sfx; click to select + edit in the properties panel */}
          <div className="st-lane st-lane-audio">
            {audio.map((a, i) => {
              const left = LANE_PAD + a.start_seconds * zoom
              const w = a.point ? 12 : Math.max(8, (a.end_seconds - a.start_seconds) * zoom)
              const icon = a.kind === 'music' ? '♫' : a.kind === 'narration' ? '🎙' : '♪'
              const sel = selection?.kind === 'audio' && selection.audioKind === a.kind && selection.index === a.index
              return (
                <button
                  key={`${a.kind}-${i}`}
                  className={`st-aud st-aud-${a.kind}${a.point ? ' pt' : ''}${sel ? ' sel' : ''}`}
                  style={{ left, width: w }}
                  title={`${a.kind} · ${baseName(a.asset_id)}`}
                  onClick={() => onSelect({ kind: 'audio', audioKind: a.kind, index: a.index })}
                >{a.point ? icon : `${icon} ${baseName(a.asset_id)}`}</button>
              )
            })}
            {!audio.length && <span className="st-lane-empty">no audio — music / narration / SFX appear here</span>}
          </div>

          {/* playhead */}
          <div className="st-playhead" style={{ left: LANE_PAD + playhead * zoom }} />
        </div>
      </div>
    </div>
  )
}
