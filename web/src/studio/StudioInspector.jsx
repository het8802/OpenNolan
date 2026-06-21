// Studio inspector — properties for the current selection, rendered from the DECLARATIVE
// propertySchema (feat 2). The selection resolves to one of 7 clip types
// (video_main/image_main/video_overlay/image_overlay/text/music/sfx, + narration); the schema
// for that type drives which sections/fields appear. Plain inputs (number/text/color/select)
// bind to a dotted path via getAtPath/buildPatch; SPECIAL controls (speed presets, crop,
// audio-mix, keyframes, text position) render bespoke sub-UI. Every field commits on blur/Enter
// (one history step per edit) through the schema-safe interp mutators passed from Studio, so a
// Save can never 422. When nothing is selected the panel falls through to the Assets tab.

import { useEffect, useState } from 'react'
import {
  TRANSITIONS, TEXT_ANCHORS, SPEED_PRESETS, anchorToXY, overlayKind, overlayType, isImageSource,
} from './model.js'
import { PROPERTY_SCHEMA, PROPERTY_TITLES, getAtPath, buildPatch } from './propertySchema.js'
import StudioKeyframes from './StudioKeyframes.jsx'
import StudioAssets from './StudioAssets.jsx'

const baseName = (p) => String(p || '').split('/').pop() || p

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

// ── option resolution for select fields (static or live-asset-backed) ───────
function resolveOptions(field, assets, currentValue) {
  if (field.options) return field.options
  const src = field.optionsFrom
  if (src === 'transitions') return TRANSITIONS
  if (src === 'anchors') return TEXT_ANCHORS.map(a => ({ value: a, label: a }))
  const k = assets?.kinds || {}
  const pool =
    src === 'video' ? (k.video || [])
      : src === 'images' ? (k.images || [])
        : src === 'imagesAndVideo' ? [...(k.images || []), ...(k.video || [])]
          : src === 'music' ? [...(k.music || []), ...(k.audio || [])]
            : src === 'audio' ? [...(k.audio || []), ...(k.music || [])]
              : []
  const opts = pool.map(a => ({ value: a.path, label: a.name }))
  if (currentValue && !pool.some(a => a.path === currentValue)) {
    opts.unshift({ value: currentValue, label: `${baseName(currentValue)} (current)` })
  }
  return opts
}

// ── special controls ─────────────────────────────────────────────────────────
function SpeedPresets({ value, onCommit }) {
  return (
    <div className="st-speed">
      {SPEED_PRESETS.map(s => (
        <button key={s} className={`st-chip ${(Number(value) || 1) === s ? 'on' : ''}`}
          onClick={() => onCommit(s)} title={`${s}× speed`}>{s}×</button>
      ))}
    </div>
  )
}

// Crop is in SOURCE pixels, so seed defaults from the clip's real dims (ffprobe), not the canvas.
function CropControl({ cut, canvas, meta, onUpdate }) {
  const sw = meta?.width || canvas.width
  const sh = meta?.height || canvas.height
  const crop = cut.transform?.crop || null
  const setCrop = (patch) => onUpdate({ transform: { ...(cut.transform || {}), crop: { ...(crop || {}), ...patch } } })
  const clearCrop = () => { const t = { ...(cut.transform || {}) }; delete t.crop; onUpdate({ transform: t }) }
  if (!crop) return <button className="st-link" onClick={() => setCrop({ x: 0, y: 0, width: sw, height: sh })}>+ add crop</button>
  return (
    <>
      <button className="st-link" onClick={clearCrop}>remove crop</button>
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
  )
}

function AudioMixControl({ ov, onUpdate }) {
  return (
    <>
      <label className="st-check">
        <input type="checkbox" checked={!!ov.audio_mix?.enabled}
          onChange={(e) => onUpdate({ audio_mix: { ...(ov.audio_mix || { volume: 1 }), enabled: e.target.checked } })} />
        mix this clip’s audio into the timeline
      </label>
      {ov.audio_mix?.enabled &&
        <NumField label="Volume" value={ov.audio_mix?.volume ?? 1} step={0.1} min={0} max={2}
          onCommit={(v) => onUpdate({ audio_mix: { ...(ov.audio_mix || {}), volume: v } })} />}
    </>
  )
}

// Text position is polymorphic: a named anchor (string) OR a free {x,y} (after a canvas drag).
function TextPositionControl({ ov, canvas, onUpdate }) {
  const pos = ov.position
  const isAnchor = typeof pos === 'string' || pos == null
  return (
    <>
      <SelectField label="Position" value={isAnchor ? (pos || 'center') : '__xy__'}
        options={[...TEXT_ANCHORS.map(a => ({ value: a, label: a })), { value: '__xy__', label: 'Custom X/Y' }]}
        onCommit={(v) => onUpdate({ position: v === '__xy__' ? anchorToXY(typeof pos === 'string' ? pos : 'center', canvas) : v })} />
      {!isAnchor && (
        <div className="st-row">
          <NumField label="X" suffix="px" value={pos?.x ?? 0} step={1} onCommit={(v) => onUpdate({ position: { ...pos, x: v } })} />
          <NumField label="Y" suffix="px" value={pos?.y ?? 0} step={1} onCommit={(v) => onUpdate({ position: { ...pos, y: v } })} />
        </div>
      )}
      <div className="st-hint">drag the text on the canvas to position it freely</div>
    </>
  )
}

// ── one schema field ─────────────────────────────────────────────────────────
function Field({ field, obj, onUpdate, ctx }) {
  const value = getAtPath(obj, field.path)
  const commit = (v) => onUpdate(buildPatch(obj, field.path, v))
  switch (field.control) {
    case 'number':
      return <NumField label={field.label} value={value ?? field.default ?? ''} step={field.step} min={field.min} max={field.max} suffix={field.suffix} onCommit={commit} />
    case 'text':
      return <TextField label={field.label} value={value ?? ''} onCommit={field.required ? (v) => { if (v.trim() !== '') commit(v) } : commit} />
    case 'color':
      return <TextField label={field.label} value={value ?? field.default ?? ''} onCommit={commit} />
    case 'textarea':
      return (
        <>
          <TextField label={field.label} value={value ?? ''} area onCommit={field.required ? (v) => { if (v.trim() !== '') commit(v) } : commit} />
          {field.required && <div className="st-hint">text can’t be empty</div>}
        </>
      )
    case 'select':
      return (
        <>
          <SelectField label={field.label} value={value ?? ''} options={resolveOptions(field, ctx.assets, value)} onCommit={commit} />
          {field.meta && ctx.meta?.duration != null &&
            <div className="st-hint">source length {ctx.meta.duration.toFixed(2)}s{ctx.meta.width ? ` · ${ctx.meta.width}×${ctx.meta.height}` : ''}</div>}
        </>
      )
    case 'speedPresets':
      return <SpeedPresets value={value} onCommit={commit} />
    case 'crop':
      return <CropControl cut={obj} canvas={ctx.canvas} meta={ctx.meta} onUpdate={onUpdate} />
    case 'audioMix':
      return <AudioMixControl ov={obj} onUpdate={onUpdate} />
    case 'textPosition':
      return <TextPositionControl ov={obj} canvas={ctx.canvas} onUpdate={onUpdate} />
    case 'keyframes':
      return (
        <StudioKeyframes
          ov={obj} index={ctx.overlayIndex} kind={overlayKind(obj)} ffmpeg={ctx.ffmpeg} playhead={ctx.playhead}
          onSetKeyframes={ctx.onSetKeyframes} onUpsertKeyframe={ctx.onUpsertKeyframe} onRemoveKeyframe={ctx.onRemoveKeyframe}
        />
      )
    default:
      return null
  }
}

// A single field can span a full row (its own section), or share a row with a sibling. We keep it
// simple: render fields in order; the `st-f` grid + `st-row` pairing in CSS handles layout.
function SchemaForm({ type, obj, onUpdate, ctx }) {
  const sections = PROPERTY_SCHEMA[type] || []
  return (
    <>
      <h3 className="st-insp-head">{PROPERTY_TITLES[type] || 'Properties'}{ctx.idLabel ? ` · ${ctx.idLabel}` : ''}</h3>
      {sections.map((sec, si) => (
        <section className="st-sec" key={si}>
          {sec.title ? <div className="st-sec-h">{sec.title}</div> : null}
          {sec.fields.map(f => <Field key={f.key} field={f} obj={obj} onUpdate={onUpdate} ctx={ctx} />)}
          {sec.hint && <div className="st-hint">{sec.hint}</div>}
        </section>
      ))}
    </>
  )
}

export default function StudioInspector({
  projectId, doc, canvas, ffmpeg, selCut, selOverlayIndex, selAudio, selAudioObj, playhead, assets, sourceMetas,
  onUpdateCut, onUpdateOverlay, onNormalizeOverlay, onUpdateAudio, onSetKeyframes, onUpsertKeyframe, onRemoveKeyframe,
  onAddImage, onAddClip, onAddSfx, onSetMusic,
}) {
  const selOverlay = selOverlayIndex >= 0 ? (doc?.overlays || [])[selOverlayIndex] : null

  // An IMAGE/VIDEO overlay with a STRING (anchor) position passes the schema but the renderer
  // rejects it (named anchors are text-only). Normalize to an {x,y} object once so the saved doc
  // is always renderable. Routed through onNormalizeOverlay (non-historied) so selecting such an
  // overlay doesn't push an undo step. (Our factory emits an object; this only fires for
  // agent-authored overlays.)
  useEffect(() => {
    if (selOverlay && overlayType(selOverlay) !== 'text' && typeof selOverlay.position === 'string') {
      (onNormalizeOverlay || onUpdateOverlay)(selOverlayIndex, { position: anchorToXY(selOverlay.position, canvas) })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selOverlay, selOverlayIndex])

  if (selCut) {
    // Derive the type from the selected object itself (not a doc re-lookup) so the panel is robust
    // to a doc/selection mismatch: a video source → video_main, a still image → image_main.
    const type = isImageSource(selCut.source) ? 'image_main' : 'video_main'
    return (
      <aside className="st-inspector">
        <SchemaForm type={type} obj={selCut}
          onUpdate={(patch) => onUpdateCut(selCut.id, patch)}
          ctx={{ canvas, ffmpeg, assets, meta: sourceMetas[selCut.source], idLabel: selCut.id }} />
      </aside>
    )
  }

  if (selOverlay) {
    const ot = overlayType(selOverlay)
    const type = ot === 'text' ? 'text' : ot === 'video' ? 'video_overlay' : 'image_overlay'
    return (
      <aside className="st-inspector">
        <SchemaForm type={type} obj={selOverlay}
          onUpdate={(patch) => onUpdateOverlay(selOverlayIndex, patch)}
          ctx={{
            canvas, ffmpeg, assets, playhead, overlayIndex: selOverlayIndex,
            onSetKeyframes, onUpsertKeyframe, onRemoveKeyframe,
          }} />
      </aside>
    )
  }

  if (selAudioObj) {
    const type = selAudio.audioKind === 'music' ? 'music' : selAudio.audioKind === 'narration' ? 'narration' : 'sfx'
    return (
      <aside className="st-inspector">
        <SchemaForm type={type} obj={selAudioObj} onUpdate={onUpdateAudio} ctx={{ canvas, assets }} />
        <div className="st-hint">Delete with the 🗑 button in the timeline toolbar (⌫).</div>
      </aside>
    )
  }

  // Nothing selected → the Assets tab (feat 4): clicking outside any clip shows assets, not a blank panel.
  return (
    <StudioAssets
      projectId={projectId} assets={assets}
      onAddImage={onAddImage} onAddClip={onAddClip} onAddSfx={onAddSfx} onSetMusic={onSetMusic}
    />
  )
}
