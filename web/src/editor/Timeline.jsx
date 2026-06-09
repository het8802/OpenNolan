import React, { useRef } from 'react'
import { cutDuration, cutStarts } from './interp.js'

// Timeline tracks for cuts (concatenated, by project duration) + overlays (absolute project
// time). Click a block to select it; click/drag the ruler to scrub the playhead. Each clip has
// left/right TRIM handles — drag them to move the in/out point (clamped to the source clip).
export default function Timeline({ doc, duration, playhead, selection, sourceMeta, onSelect, onSeek, onTrimCut }) {
  const railRef = useRef(null)
  const laneRef = useRef(null)
  const dur = Math.max(duration || 1, 0.1)
  const pct = (t) => `${Math.max(0, Math.min(100, (t / dur) * 100))}%`

  const cuts = doc.cuts || []
  const starts = cutStarts(doc)
  const overlays = doc.overlays || []

  function seekFromEvent(e) {
    const rail = railRef.current
    if (!rail) return
    const r = rail.getBoundingClientRect()
    const t = ((e.clientX - r.left) / r.width) * dur
    onSeek(Math.max(0, Math.min(dur, t)))
  }

  // Drag a clip edge: convert pointer motion → source-seconds (× speed) and trim that edge.
  // We also scrub the preview to the edge being dragged, so you see the exact in/out frame.
  function beginTrim(e, cut, index, edge) {
    if (!onTrimCut) return
    e.stopPropagation()
    e.preventDefault()
    const lane = laneRef.current
    if (!lane) return
    const laneW = lane.getBoundingClientRect().width || 1
    const pxPerSec = laneW / dur
    const speed = Number(cut.speed) || 1
    const startX = e.clientX
    const startIn = Number(cut.in_seconds) || 0
    const startOut = Number(cut.out_seconds) || 0
    const startProject = starts[index]
    const srcDur = sourceMeta?.[cut.source]?.duration

    const onMove = (ev) => {
      const dSource = ((ev.clientX - startX) / pxPerSec) * (speed > 0 ? speed : 1)
      if (edge === 'left') {
        onTrimCut(cut.id, { in_seconds: startIn + dSource }, { sourceDuration: srcDur })
        onSeek?.(startProject + 0.001)
      } else {
        const newOut = srcDur != null ? Math.min(startOut + dSource, srcDur) : startOut + dSource
        onTrimCut(cut.id, { out_seconds: startOut + dSource }, { sourceDuration: srcDur })
        const newProjDur = Math.max(0, (Math.max(newOut, startIn + 0.1) - startIn) / (speed > 0 ? speed : 1))
        onSeek?.(startProject + newProjDur - 0.001)
      }
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  return (
    <div className="tl">
      {/* Ruler — click/drag to scrub */}
      <div className="tl-ruler" ref={railRef}
        onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); seekFromEvent(e) }}
        onPointerMove={(e) => { if (e.buttons === 1) seekFromEvent(e) }}>
        {ticks(dur).map(t => (
          <span key={t} className="tl-tick" style={{ left: pct(t) }}>{t}s</span>
        ))}
        <div className="tl-playhead" style={{ left: pct(playhead) }} />
      </div>

      <div className="tl-tracks">
        <div className="tl-track">
          <span className="tl-track-label">clips</span>
          <div className="tl-lane" ref={laneRef}>
            {cuts.map((c, i) => {
              const left = pct(starts[i])
              const width = pct(cutDuration(c)).replace('%', '')
              const sel = selection?.type === 'cut' && selection.id === c.id
              return (
                <div key={c.id || i} className={`tl-clip ${sel ? 'sel' : ''}`}
                  style={{ left, width: `${width}%` }}
                  onClick={() => onSelect({ type: 'cut', id: c.id })}
                  title={`${c.id}: ${c.source}  ·  in ${(c.in_seconds ?? 0)}s → out ${(c.out_seconds ?? 0)}s`}>
                  {onTrimCut && <span className="tl-handle l" title="Trim start"
                    onPointerDown={(e) => beginTrim(e, c, i, 'left')} />}
                  <span className="tl-clip-name">{c.id}</span>
                  {onTrimCut && <span className="tl-handle r" title="Trim end"
                    onPointerDown={(e) => beginTrim(e, c, i, 'right')} />}
                </div>
              )
            })}
          </div>
        </div>

        {overlays.length > 0 && (
          <div className="tl-track">
            <span className="tl-track-label">overlays</span>
            <div className="tl-lane">
              {overlays.map((o, i) => {
                const left = pct(o.start_seconds || 0)
                const w = ((Number(o.end_seconds) || 0) - (Number(o.start_seconds) || 0)) / dur * 100
                const sel = selection?.type === 'overlay' && selection.index === i
                return (
                  <button key={i} className={`tl-ov ${sel ? 'sel' : ''} ${o.keyframes?.length ? 'kf' : ''}`}
                    style={{ left, width: `${Math.max(2, w)}%` }}
                    onClick={() => onSelect({ type: 'overlay', index: i })}
                    title={`${o.asset_id}${o.keyframes?.length ? ` · ${o.keyframes.length} keyframes` : ''}`}>
                    <span className="tl-ov-name">{o.asset_id}</span>
                  </button>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function ticks(dur) {
  const step = dur <= 6 ? 1 : dur <= 20 ? 2 : dur <= 60 ? 5 : 10
  const out = []
  for (let t = 0; t <= dur + 0.001; t += step) out.push(Math.round(t))
  return out
}
