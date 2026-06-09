import React from 'react'
import KeyframeEditor from './KeyframeEditor.jsx'

const LAYERS = ['primary', 'overlay', 'background']

// Client-side keyframe presets (mirror tools/video/keyframe_animate.py at a basic level).
// They emit keyframes the FFmpeg renderer understands; scale-based ones are stored but
// only position+opacity render on the FFmpeg path (noted in the editor).
function presetKeyframes(preset, { start = 0, end = 1, x = 0 }) {
  const mid = Math.min(start + 0.5, end)
  switch (preset) {
    case 'fade_in': return [{ t: start, opacity: 0, easing: 'ease-out' }, { t: Math.min(start + 0.4, end), opacity: 1 }]
    case 'fade_out': return [{ t: Math.max(end - 0.4, start), opacity: 1 }, { t: end, opacity: 0 }]
    case 'slide_in_left': return [{ t: start, x: x - 150, opacity: 0, easing: 'ease-out' }, { t: mid, x, opacity: 1 }]
    case 'slide_in_right': return [{ t: start, x: x + 150, opacity: 0, easing: 'ease-out' }, { t: mid, x, opacity: 1 }]
    case 'pop': return [{ t: start, scale: 0.6, opacity: 0 }, { t: start + 0.2, scale: 1.1, opacity: 1 }, { t: start + 0.35, scale: 1.0 }]
    case 'ken_burns': return [{ t: start, scale: 1.0 }, { t: end, scale: 1.15 }]
    default: return []
  }
}
const PRESETS = ['fade_in', 'fade_out', 'slide_in_left', 'slide_in_right', 'pop', 'ken_burns']

function Num({ label, value, step = 1, min, onChange }) {
  return (
    <label className="insp-field">
      <span>{label}</span>
      <input type="number" step={step} min={min} value={value ?? ''}
        onChange={e => onChange(e.target.value === '' ? undefined : Number(e.target.value))} />
    </label>
  )
}

export default function Inspector({ doc, selection, duration, playhead, onUpdateCut, onUpdateOverlay, onSetKeyframes }) {
  if (!selection) return <div className="inspector empty">Select a clip or overlay on the timeline.</div>

  if (selection.type === 'cut') {
    const cut = (doc.cuts || []).find(c => c.id === selection.id)
    if (!cut) return <div className="inspector empty">Clip not found.</div>
    const set = (patch) => onUpdateCut(cut.id, patch)
    return (
      <div className="inspector">
        <div className="insp-head">Clip · <strong>{cut.id}</strong></div>
        <label className="insp-field"><span>source</span>
          <input value={cut.source || ''} onChange={e => set({ source: e.target.value })} /></label>
        <div className="insp-row">
          <Num label="in (s)" value={cut.in_seconds} step={0.1} min={0} onChange={v => set({ in_seconds: v })} />
          <Num label="out (s)" value={cut.out_seconds} step={0.1} min={0} onChange={v => set({ out_seconds: v })} />
        </div>
        <div className="insp-row">
          <Num label="speed" value={cut.speed ?? 1} step={0.1} min={0.1} onChange={v => set({ speed: v })} />
          <label className="insp-field"><span>layer</span>
            <select value={cut.layer || 'primary'} onChange={e => set({ layer: e.target.value })}>
              {LAYERS.map(l => <option key={l} value={l}>{l}</option>)}
            </select></label>
        </div>
        <div className="insp-row">
          <label className="insp-field"><span>transition in</span>
            <input value={cut.transition_in || ''} placeholder="cut / fade / dissolve"
              onChange={e => set({ transition_in: e.target.value || undefined })} /></label>
          <Num label="trans. dur" value={cut.transition_duration} step={0.1} min={0}
            onChange={v => set({ transition_duration: v })} />
        </div>
        <label className="insp-field"><span>reason</span>
          <input value={cut.reason || ''} onChange={e => set({ reason: e.target.value || undefined })} /></label>
      </div>
    )
  }

  // overlay
  const idx = selection.index
  const ov = (doc.overlays || [])[idx]
  if (!ov) return <div className="inspector empty">Overlay not found.</div>
  const pos = ov.position || { x: 0, y: 0 }
  const set = (patch) => onUpdateOverlay(idx, patch)
  const setPos = (patch) => set({ position: { ...pos, ...patch } })

  return (
    <div className="inspector">
      <div className="insp-head">Overlay · <strong>{ov.asset_id}</strong></div>
      <label className="insp-field"><span>asset_id</span>
        <input value={ov.asset_id || ''} onChange={e => set({ asset_id: e.target.value })} /></label>
      <div className="insp-row">
        <Num label="start (s)" value={ov.start_seconds} step={0.1} min={0} onChange={v => set({ start_seconds: v })} />
        <Num label="end (s)" value={ov.end_seconds} step={0.1} min={0} onChange={v => set({ end_seconds: v })} />
      </div>
      <div className="insp-row">
        <Num label="x" value={pos.x} onChange={v => setPos({ x: v })} />
        <Num label="y" value={pos.y} onChange={v => setPos({ y: v })} />
      </div>
      <div className="insp-row">
        <Num label="width" value={pos.width} min={0} onChange={v => setPos({ width: v })} />
        <Num label="height" value={pos.height} min={0} onChange={v => setPos({ height: v })} />
      </div>
      <Num label="opacity" value={ov.opacity} step={0.05} min={0} onChange={v => set({ opacity: v })} />

      <div className="insp-presets">
        <span className="insp-sub">apply preset</span>
        <div className="insp-preset-btns">
          {PRESETS.map(p => (
            <button key={p} className="insp-preset"
              onClick={() => onSetKeyframes(idx, presetKeyframes(p, {
                start: ov.start_seconds || 0, end: ov.end_seconds || duration, x: pos.x || 0,
              }))}>{p}</button>
          ))}
        </div>
      </div>

      <KeyframeEditor overlay={ov} duration={duration} playhead={playhead}
        onChange={(kfs) => onSetKeyframes(idx, kfs)} />
    </div>
  )
}
