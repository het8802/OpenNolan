import React, { useRef, useState } from 'react'
import { LineChart } from '../components/LineChart.jsx'
import { EASINGS, KEYFRAME_DIMS, interpolateAt } from './interp.js'

// Per-dimension plot color + a sensible value range for the draggable surface.
const DIM_COLOR = {
  x: 'var(--accent)', y: 'var(--blue)', opacity: 'var(--green)',
  scale: 'var(--amber)', rotation: 'var(--red)',
}
const PAD = { top: 14, right: 14, bottom: 24, left: 34 }
const H = 200

function dimRange(dim, vals) {
  if (dim === 'opacity') return [0, 1]
  if (vals.length === 0) return dim === 'scale' ? [0, 2] : [-100, 100]
  let lo = Math.min(...vals), hi = Math.max(...vals)
  if (dim === 'scale') { lo = Math.min(lo, 0); hi = Math.max(hi, 1.5) }
  if (lo === hi) { lo -= 1; hi += 1 }
  const pad = (hi - lo) * 0.15 || 1
  return [lo - pad, hi + pad]
}

/**
 * Edit one overlay's keyframes.
 *  - Top: read-only LineChart overview of every animated dimension.
 *  - Middle: a draggable single-dimension curve (drag a dot vertically = value).
 *  - Bottom: a precise table (t / value / easing / delete) + "add at playhead".
 * onChange(nextKeyframes) is called with a fresh, schema-clean keyframe array.
 */
export default function KeyframeEditor({ overlay, duration, playhead = 0, onChange }) {
  const keyframes = overlay.keyframes || []
  const [dim, setDim] = useState('opacity')
  const svgRef = useRef(null)
  const [drag, setDrag] = useState(null)  // index being dragged

  const dur = Math.max(duration || 1, 0.1)
  const pts = keyframes
    .map((k, i) => ({ i, t: Number(k.t), v: k[dim] }))
    .filter(p => p.v != null && !Number.isNaN(p.t))
    .sort((a, b) => a.t - b.t)
  const [lo, hi] = dimRange(dim, pts.map(p => Number(p.v)))

  // overview: one series per animated dimension (read-only context)
  const overview = KEYFRAME_DIMS.map(d => {
    const dpts = keyframes.filter(k => k[d] != null).map(k => ({ x: Number(k.t), y: Number(k[d]) }))
    if (!dpts.length) return null
    // normalize each dim to 0..100 so they share one axis
    const ys = dpts.map(p => p.y); const mn = Math.min(...ys, 0), mx = Math.max(...ys, 1)
    const norm = dpts.map(p => ({ x: p.x, y: mx === mn ? 50 : ((p.y - mn) / (mx - mn)) * 100 }))
    return { key: d, label: d, color: DIM_COLOR[d], points: norm, bold: d === dim }
  }).filter(Boolean)

  function svgXY(clientX, clientY) {
    const r = svgRef.current.getBoundingClientRect()
    const innerW = r.width - PAD.left - PAD.right
    const innerH = r.height - PAD.top - PAD.bottom
    const t = ((clientX - r.left - PAD.left) / innerW) * dur
    const v = lo + (1 - (clientY - r.top - PAD.top) / innerH) * (hi - lo)
    return { t: Math.max(0, Math.min(dur, t)), v }
  }
  const sx = t => PAD.left + (t / dur) * (svgWidth() - PAD.left - PAD.right)
  const sy = v => PAD.top + (1 - (v - lo) / (hi - lo)) * (H - PAD.top - PAD.bottom)
  function svgWidth() { return svgRef.current?.getBoundingClientRect().width || 520 }

  function onDotDown(e, idx) {
    e.preventDefault(); e.stopPropagation()
    setDrag(idx)
  }
  function onMove(e) {
    if (drag == null) return
    const { v } = svgXY(e.clientX, e.clientY)
    const clamped = dim === 'opacity' ? Math.max(0, Math.min(1, v)) : v
    const next = keyframes.map((k, i) => (i === drag ? { ...k, [dim]: round(clamped) } : k))
    onChange(next)
  }
  function onUp() { setDrag(null) }

  function setField(idx, field, value) {
    const next = keyframes.map((k, i) => (i === idx ? { ...k, [field]: value } : k))
    onChange(next)
  }
  function addAtPlayhead() {
    const t = round(Math.max(0, Math.min(dur, playhead)))
    if (keyframes.some(k => Number(k.t) === t)) return
    const seed = { t, [dim]: dim === 'opacity' ? 1 : 0, easing: 'linear' }
    onChange([...keyframes, seed].sort((a, b) => Number(a.t) - Number(b.t)))
  }
  function remove(idx) { onChange(keyframes.filter((_, i) => i !== idx)) }

  return (
    <div className="kfe">
      <div className="kfe-head">
        <span className="kfe-title">Keyframes</span>
        <select className="kfe-dim" value={dim} onChange={e => setDim(e.target.value)}>
          {KEYFRAME_DIMS.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        <button className="kfe-add" onClick={addAtPlayhead} title="Add a keyframe at the playhead">+ at {playhead.toFixed(1)}s</button>
      </div>

      {overview.length > 0 && (
        <div className="kfe-overview">
          <LineChart series={overview} xMax={dur} yMax={100} yTicks={[0, 50, 100]} height={90}
            xLabel="" yLabel="" />
        </div>
      )}

      <svg ref={svgRef} className="kfe-svg" width="100%" height={H}
        onPointerMove={onMove} onPointerUp={onUp} onPointerLeave={onUp}
        role="img" aria-label={`${dim} keyframe curve`}>
        <line className="kfe-grid" x1={PAD.left} y1={sy(lo)} x2="100%" y2={sy(lo)} />
        <line className="kfe-grid" x1={PAD.left} y1={sy(hi)} x2="100%" y2={sy(hi)} />
        {/* playhead marker */}
        <line className="kfe-playhead" x1={sx(playhead)} y1={PAD.top} x2={sx(playhead)} y2={H - PAD.bottom} />
        {pts.length > 1 && (
          <polyline className="kfe-line" fill="none" stroke={DIM_COLOR[dim]}
            points={pts.map(p => `${sx(p.t)},${sy(Number(p.v))}`).join(' ')} />
        )}
        {pts.map(p => (
          <circle key={p.i} className={`kfe-dot ${drag === p.i ? 'drag' : ''}`}
            cx={sx(p.t)} cy={sy(Number(p.v))} r="6" fill={DIM_COLOR[dim]}
            onPointerDown={e => onDotDown(e, p.i)} />
        ))}
        <text className="kfe-axis" x={PAD.left - 5} y={sy(hi)} textAnchor="end" dominantBaseline="middle">{round(hi)}</text>
        <text className="kfe-axis" x={PAD.left - 5} y={sy(lo)} textAnchor="end" dominantBaseline="middle">{round(lo)}</text>
      </svg>

      <table className="kfe-table">
        <thead><tr><th>t (s)</th><th>{dim}</th><th>easing</th><th></th></tr></thead>
        <tbody>
          {keyframes.length === 0 && <tr><td colSpan="4" className="kfe-empty">No keyframes. Add one, or apply a preset in the inspector.</td></tr>}
          {keyframes.map((k, i) => (
            <tr key={i}>
              <td><input type="number" step="0.1" min="0" value={k.t ?? 0}
                onChange={e => setField(i, 't', Number(e.target.value))} /></td>
              <td><input type="number" step={dim === 'opacity' ? '0.05' : '1'} value={k[dim] ?? ''}
                onChange={e => setField(i, dim, e.target.value === '' ? undefined : Number(e.target.value))} /></td>
              <td>
                <select value={k.easing || 'linear'} onChange={e => setField(i, 'easing', e.target.value)}>
                  {EASINGS.map(es => <option key={es} value={es}>{es}</option>)}
                </select>
              </td>
              <td><button className="kfe-del" onClick={() => remove(i)} title="Delete keyframe">✕</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="kfe-note">FFmpeg renders position + opacity linearly; scale/rotation/easing are stored but not yet rendered on this path.</div>
    </div>
  )
}

function round(n) { return Math.round(Number(n) * 100) / 100 }
