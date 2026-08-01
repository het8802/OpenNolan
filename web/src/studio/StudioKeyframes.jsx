// Studio keyframe editor for an overlay. Presets + a per-dimension table + a curve preview.
// Dimensions are gated by what the FFmpeg path renders (text: x/y/opacity; image: +scale;
// rotation never). interpolateAt is the same linear math the renderer uses, so the drawn
// curve matches the export.

import { useState } from 'react'
import { interpolateAt } from '../editor/interp.js'
import { LineChart } from '../components/LineChart.jsx'
import { kfDimsFor, presetKeyframes, EASINGS, round3 } from './model.js'

const SAMPLES = 48

export default function StudioKeyframes({ ov, index, kind, ffmpeg, playhead, onSetKeyframes, onUpsertKeyframe, onRemoveKeyframe }) {
  const dims = kfDimsFor(ov)
  const [dim, setDim] = useState(dims[0])
  const activeDim = dims.includes(dim) ? dim : dims[0]
  const kfs = (ov.keyframes || []).slice().sort((a, b) => Number(a.t) - Number(b.t))

  const start = Number(ov.start_seconds) || 0
  const end = Number(ov.end_seconds) || start + 1

  const setKfs = (next) => onSetKeyframes(index, next)
  const editField = (kfIndex, patch) => {
    const next = kfs.map((k, i) => (i === kfIndex ? { ...k, ...patch } : k))
    if (patch.t != null) next.sort((a, b) => Number(a.t) - Number(b.t))
    setKfs(next)
  }
  const addAtPlayhead = () => {
    const t = round3(Math.max(start, Math.min(end, playhead)))
    const cur = interpolateAt(kfs, activeDim, t)
    const val = cur != null ? cur : (activeDim === 'opacity' ? 1 : activeDim === 'scale' ? 1 : 0)
    onUpsertKeyframe(index, { t, [activeDim]: round3(val), easing: 'linear' })
  }

  const applyPreset = (name) => {
    const next = presetKeyframes(name, ov)
    if (next) setKfs(next)
  }

  // curve preview for the active dim (only if any keyframe defines it)
  const animated = kfs.some(k => k[activeDim] != null)
  let series = null, yMax = 1
  if (animated) {
    const pts = []
    for (let i = 0; i <= SAMPLES; i++) {
      const t = start + (end - start) * (i / SAMPLES)
      const v = interpolateAt(kfs, activeDim, t)
      if (v != null) { pts.push({ x: t, y: v }); yMax = Math.max(yMax, v) }
    }
    series = [{ key: activeDim, label: activeDim, color: 'var(--accent)', points: pts, bold: true }]
  }

  const motionPresetsOk = kind !== 'text'

  return (
    <section className="st-sec st-kf">
      <div className="st-sec-h">Keyframes {kfs.length > 0 && <span className="st-hint">{kfs.length}</span>}</div>

      <div className="st-kf-presets">
        <button className="st-link" onClick={() => applyPreset('fade_in')}>fade in</button>
        <button className="st-link" onClick={() => applyPreset('fade_out')}>fade out</button>
        {motionPresetsOk && <>
          <button className="st-link" onClick={() => applyPreset('slide_in_left')}>slide ←</button>
          <button className="st-link" onClick={() => applyPreset('slide_in_right')}>slide →</button>
          <button className="st-link" onClick={() => applyPreset('pop')}>pop</button>
          <button className="st-link" onClick={() => applyPreset('ken_burns')}>ken burns</button>
        </>}
        {kfs.length > 0 && <button className="st-link st-danger" onClick={() => setKfs([])}>clear</button>}
      </div>

      <div className="st-row st-kf-ctrl">
        <label className="st-f">
          <span>Dimension</span>
          <select value={activeDim} onChange={(e) => setDim(e.target.value)}>
            {dims.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <button className="st-btn" onClick={addAtPlayhead} title="Add/Update a keyframe for this dimension at the playhead">+ at {playhead.toFixed(1)}s</button>
      </div>

      {animated && (
        <div className="st-kf-chart">
          <LineChart series={series} xMax={end} yMax={yMax} yTicks={[0, yMax]} height={120} xLabel="t (s)" yLabel={activeDim} />
        </div>
      )}

      {kfs.length > 0 && (
        <table className="st-kf-table">
          <thead><tr><th>t</th><th>{activeDim}</th><th>easing</th><th /></tr></thead>
          <tbody>
            {kfs.map((k, i) => (
              <tr key={i}>
                <td><input type="number" step={0.1} min={0} value={k.t ?? ''} onChange={(e) => editField(i, { t: round3(Number(e.target.value)) })} /></td>
                <td>
                  <input type="number" step={0.1} value={k[activeDim] ?? ''}
                    placeholder="—"
                    onChange={(e) => editField(i, { [activeDim]: e.target.value === '' ? undefined : round3(Number(e.target.value)) })} />
                </td>
                <td>
                  <select value={k.easing ?? 'linear'} onChange={(e) => editField(i, { easing: e.target.value })}>
                    {EASINGS.map(es => <option key={es} value={es}>{es}</option>)}
                  </select>
                </td>
                <td><button className="st-link st-danger" onClick={() => onRemoveKeyframe(index, i)} title="Delete keyframe">✕</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="st-hint">
        {kind === 'text'
          ? 'FFmpeg renders x / y / opacity for text; scale & rotation are ignored.'
          : 'FFmpeg renders x / y / scale / opacity; rotation is ignored.'}
        {!ffmpeg && ' Preview reflects FFmpeg behavior.'} Easing is approximated linearly in preview.
      </div>
    </section>
  )
}
