// Studio inspector — fine-grained properties for the current selection. Cut: source, trim,
// speed, crop, transitions. Overlay: text/style or image/asset, position, opacity, timing,
// audio mix, and keyframes. Every field commits on blur/Enter (one history step per edit,
// not per keystroke) through the schema-safe interp mutators passed from Studio.

import { useEffect, useState } from 'react'
import { TRANSITIONS, TEXT_ANCHORS, overlayKind, anchorToXY } from './model.js'
import StudioKeyframes from './StudioKeyframes.jsx'

// ── tiny fields (commit on blur / Enter) ────────────────────────────────────
function NumField({ label, value, onCommit, step = 0.1, min, max, suffix }) {
  const [v, setV] = useState(value ?? '')
  useEffect(() => { setV(value ?? '') }, [value])
  const commit = () => {
    if (v === '' || v == null) return
    let n = Number(v)
    if (Number.isNaN(n)) { setV(value ?? ''); return }
    if (min != null) n = Math.max(min, n)
    if (max != null) n = Math.min(max, n)
    onCommit(n)
  }
  return (
    <label className="st-f">
      <span>{label}{suffix ? ` (${suffix})` : ''}</span>
      <input type="number" step={step} min={min} max={max} value={v}
        onChange={(e) => setV(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur() }} />
    </label>
  )
}

function TextField({ label, value, onCommit, area }) {
  const [v, setV] = useState(value ?? '')
  useEffect(() => { setV(value ?? '') }, [value])
  const commit = () => onCommit(v)
  const props = {
    value: v, onChange: (e) => setV(e.target.value), onBlur: commit,
    onKeyDown: (e) => { if (e.key === 'Enter' && !area) e.target.blur() },
  }
  return (
    <label className="st-f">
      <span>{label}</span>
      {area ? <textarea rows={2} {...props} /> : <input type="text" {...props} />}
    </label>
  )
}

function SelectField({ label, value, options, onCommit }) {
  return (
    <label className="st-f">
      <span>{label}</span>
      <select value={value ?? ''} onChange={(e) => onCommit(e.target.value)}>
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  )
}

export default function StudioInspector({
  doc, canvas, ffmpeg, selCut, selOverlayIndex, playhead, assets, sourceMetas,
  onUpdateCut, onUpdateOverlay, onSetKeyframes, onUpsertKeyframe, onRemoveKeyframe,
}) {
  const selOverlay = selOverlayIndex >= 0 ? (doc?.overlays || [])[selOverlayIndex] : null

  if (!selCut && !selOverlay) {
    return <aside className="st-inspector"><div className="st-insp-empty">Select a clip or overlay to edit it.</div></aside>
  }

  if (selCut) return (
    <aside className="st-inspector">
      <CutInspector
        cut={selCut} canvas={canvas} ffmpeg={ffmpeg} assets={assets} meta={sourceMetas[selCut.source]}
        onUpdate={(patch) => onUpdateCut(selCut.id, patch)}
        NumField={NumField} TextField={TextField} SelectField={SelectField}
      />
    </aside>
  )

  return (
    <aside className="st-inspector">
      <OverlayInspector
        ov={selOverlay} index={selOverlayIndex} canvas={canvas} ffmpeg={ffmpeg} assets={assets} playhead={playhead}
        onUpdate={(patch) => onUpdateOverlay(selOverlayIndex, patch)}
        onSetKeyframes={onSetKeyframes} onUpsertKeyframe={onUpsertKeyframe} onRemoveKeyframe={onRemoveKeyframe}
        NumField={NumField} TextField={TextField} SelectField={SelectField}
      />
    </aside>
  )
}

// ── cut ──────────────────────────────────────────────────────────────────────
function CutInspector({ cut, canvas, ffmpeg, assets, meta, onUpdate, NumField, TextField, SelectField }) {
  const videos = assets?.kinds?.video || []
  const srcOpts = [
    ...(videos.some(v => v.path === cut.source) ? [] : [{ value: cut.source, label: `${cut.source} (current)` }]),
    ...videos.map(v => ({ value: v.path, label: v.name })),
  ]
  // Crop is applied in SOURCE pixels, so default/seed it from the clip's real dimensions
  // (from ffprobe), not the output canvas — they differ and canvas px would crop wrong.
  const sw = meta?.width || canvas.width
  const sh = meta?.height || canvas.height
  const crop = cut.transform?.crop || null
  const setCrop = (patch) => {
    const next = { ...(crop || {}), ...patch }
    onUpdate({ transform: { ...(cut.transform || {}), crop: next } })
  }
  const clearCrop = () => {
    const t = { ...(cut.transform || {}) }
    delete t.crop
    onUpdate({ transform: t })
  }

  return (
    <>
      <h3 className="st-insp-head">Clip · {cut.id}</h3>

      <section className="st-sec">
        <div className="st-sec-h">Source</div>
        <SelectField label="Clip" value={cut.source} options={srcOpts} onCommit={(v) => onUpdate({ source: v })} />
        {meta?.duration != null && <div className="st-hint">source length {meta.duration.toFixed(2)}s{meta.width ? ` · ${meta.width}×${meta.height}` : ''}</div>}
      </section>

      <section className="st-sec">
        <div className="st-sec-h">Trim & speed</div>
        <div className="st-row">
          <NumField label="In" suffix="s" value={cut.in_seconds} min={0} onCommit={(v) => onUpdate({ in_seconds: v })} />
          <NumField label="Out" suffix="s" value={cut.out_seconds} min={0} onCommit={(v) => onUpdate({ out_seconds: v })} />
        </div>
        <NumField label="Speed" suffix="×" value={cut.speed ?? 1} step={0.1} min={0.1} onCommit={(v) => onUpdate({ speed: v })} />
      </section>

      <section className="st-sec">
        <div className="st-sec-h">Crop {crop && <button className="st-link" onClick={clearCrop}>remove</button>}</div>
        {crop ? (
          <>
            <div className="st-row">
              <NumField label="X" suffix="px" value={crop.x ?? 0} step={1} min={0} onCommit={(v) => setCrop({ x: v })} />
              <NumField label="Y" suffix="px" value={crop.y ?? 0} step={1} min={0} onCommit={(v) => setCrop({ y: v })} />
            </div>
            <div className="st-row">
              <NumField label="W" suffix="px" value={crop.width ?? sw} step={1} min={1} onCommit={(v) => setCrop({ width: v })} />
              <NumField label="H" suffix="px" value={crop.height ?? sh} step={1} min={1} onCommit={(v) => setCrop({ height: v })} />
            </div>
            <div className="st-hint">source pixels, applied before scaling to canvas</div>
          </>
        ) : (
          <button className="st-link" onClick={() => setCrop({ x: 0, y: 0, width: sw, height: sh })}>+ add crop</button>
        )}
      </section>

      <section className="st-sec">
        <div className="st-sec-h">Transitions</div>
        <SelectField label="In" value={cut.transition_in ?? ''} options={TRANSITIONS} onCommit={(v) => onUpdate({ transition_in: v })} />
        <SelectField label="Out" value={cut.transition_out ?? ''} options={TRANSITIONS} onCommit={(v) => onUpdate({ transition_out: v })} />
        <NumField label="Duration" suffix="s" value={cut.transition_duration ?? 0.5} step={0.1} min={0.1} max={2} onCommit={(v) => onUpdate({ transition_duration: v })} />
      </section>

      <section className="st-sec">
        <TextField label="Note (optional)" value={cut.reason ?? ''} onCommit={(v) => onUpdate({ reason: v })} />
      </section>
    </>
  )
}

// ── overlay ────────────────────────────────────────────────────────────────
function OverlayInspector({ ov, index, canvas, ffmpeg, assets, playhead, onUpdate, onSetKeyframes, onUpsertKeyframe, onRemoveKeyframe, NumField, TextField, SelectField }) {
  const kind = overlayKind(ov)
  const images = assets?.kinds?.images || []
  const videos = assets?.kinds?.video || []
  const assetOpts = [...images, ...videos]
  const pos = ov.position
  const anchorMode = typeof pos === 'string'

  // An IMAGE/VIDEO overlay with a STRING (anchor) position passes the schema but the
  // renderer rejects it (named anchors are text-only). Normalize it to an object once so
  // the saved doc is always renderable. (Our factory already emits an object; this only
  // fires for agent/pipeline-authored overlays.)
  useEffect(() => {
    if (kind !== 'text' && typeof ov.position === 'string') {
      onUpdate({ position: anchorToXY(ov.position, canvas) })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, ov.position])

  const setPosObj = (patch) => onUpdate({ position: { ...(anchorMode ? {} : pos || {}), ...patch } })

  return (
    <>
      <h3 className="st-insp-head">{kind === 'text' ? 'Text overlay' : 'Image overlay'}</h3>

      <section className="st-sec">
        <div className="st-sec-h">Timing</div>
        <div className="st-row">
          <NumField label="Start" suffix="s" value={ov.start_seconds} min={0} onCommit={(v) => onUpdate({ start_seconds: v })} />
          <NumField label="End" suffix="s" value={ov.end_seconds} min={0} onCommit={(v) => onUpdate({ end_seconds: v })} />
        </div>
        <NumField label="Opacity" value={ov.opacity ?? 1} step={0.05} min={0} max={1} onCommit={(v) => onUpdate({ opacity: v })} />
      </section>

      {kind === 'text' ? (
        <section className="st-sec">
          <div className="st-sec-h">Text</div>
          <TextField label="Content" value={ov.text ?? ''} area onCommit={(v) => { if (v.trim() !== '') onUpdate({ text: v }) }} />
          <div className="st-hint">text can’t be empty</div>
          <div className="st-row">
            <NumField label="Font size" suffix="px" value={ov.font_size ?? 48} step={1} min={1} onCommit={(v) => onUpdate({ font_size: v })} />
            <TextField label="Color" value={ov.color ?? 'white'} onCommit={(v) => onUpdate({ color: v })} />
          </div>
          <SelectField label="Anchor" value={anchorMode ? pos : 'bottom-center'}
            options={TEXT_ANCHORS.map(a => ({ value: a, label: a }))}
            onCommit={(v) => onUpdate({ position: v })} />
          <div className="st-sec-h" style={{ marginTop: '0.5rem' }}>Background box</div>
          <div className="st-row">
            <NumField label="Box opacity" value={ov.box?.opacity ?? 0.5} step={0.05} min={0} max={1}
              onCommit={(v) => onUpdate({ box: { ...(ov.box || { color: 'black' }), opacity: v } })} />
            <NumField label="Box padding" suffix="px" value={ov.box?.padding ?? 10} step={1} min={0}
              onCommit={(v) => onUpdate({ box: { ...(ov.box || { color: 'black' }), padding: v } })} />
          </div>
        </section>
      ) : (
        <section className="st-sec">
          <div className="st-sec-h">Image / video</div>
          <SelectField label="Asset" value={ov.asset_id ?? ''}
            options={[...(assetOpts.some(a => a.path === ov.asset_id) ? [] : [{ value: ov.asset_id, label: `${ov.asset_id} (current)` }]), ...assetOpts.map(a => ({ value: a.path, label: a.name }))]}
            onCommit={(v) => onUpdate({ asset_id: v })} />
          <div className="st-row">
            <NumField label="X" suffix="px" value={anchorMode ? 0 : (pos?.x ?? 0)} step={1} onCommit={(v) => setPosObj({ x: v })} />
            <NumField label="Y" suffix="px" value={anchorMode ? 0 : (pos?.y ?? 0)} step={1} onCommit={(v) => setPosObj({ y: v })} />
          </div>
          <div className="st-row">
            <NumField label="Width" suffix="px" value={anchorMode ? '' : (pos?.width ?? '')} step={1} min={1} onCommit={(v) => setPosObj({ width: v })} />
            <NumField label="Height" suffix="px" value={anchorMode ? '' : (pos?.height ?? '')} step={1} min={1} onCommit={(v) => setPosObj({ height: v })} />
          </div>
          <div className="st-hint">leave height empty to keep aspect ratio</div>
          <div className="st-sec-h" style={{ marginTop: '0.5rem' }}>Source audio</div>
          <label className="st-check">
            <input type="checkbox" checked={!!ov.audio_mix?.enabled}
              onChange={(e) => onUpdate({ audio_mix: { ...(ov.audio_mix || { volume: 1 }), enabled: e.target.checked } })} />
            mix this clip’s audio into the timeline
          </label>
          {ov.audio_mix?.enabled &&
            <NumField label="Volume" value={ov.audio_mix?.volume ?? 1} step={0.1} min={0} max={2}
              onCommit={(v) => onUpdate({ audio_mix: { ...(ov.audio_mix || {}), volume: v } })} />}
        </section>
      )}

      <StudioKeyframes
        ov={ov} index={index} kind={kind} ffmpeg={ffmpeg} playhead={playhead}
        onSetKeyframes={onSetKeyframes} onUpsertKeyframe={onUpsertKeyframe} onRemoveKeyframe={onRemoveKeyframe}
      />
    </>
  )
}
