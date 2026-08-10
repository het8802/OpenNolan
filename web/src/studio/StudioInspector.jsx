// Studio inspector — properties for the current selection, rendered from the DECLARATIVE
// propertySchema (feat 2). The selection resolves to one of 7 clip types
// (video_main/image_main/video_overlay/image_overlay/text/music/sfx, + narration); the schema
// for that type drives which sections/fields appear. Plain inputs (number/text/color/select)
// bind to a dotted path via getAtPath/buildPatch; SPECIAL controls (speed presets, crop,
// audio-mix, keyframes, text position) render bespoke sub-UI. Numeric fields are scrub bars —
// DRAG to adjust (live, one undo step per drag) or CLICK to type an exact value (one commit);
// text/select fields commit on blur/Enter. All writes flow through the schema-safe interp mutators
// passed from Studio, so a Save can never 422. Nothing selected → the panel falls to the Assets tab.

import { useEffect, useRef, useState } from 'react'
import {
  TRANSITIONS, TEXT_ANCHORS, SPEED_PRESETS, anchorToXY, overlayKind, overlayType, isImageSource,
  scrubValue, roundTo, fmtScrub, clipPositionXY, isScaleObject, scaleAxes,
} from './model.js'
import { PROPERTY_SCHEMA, PROPERTY_TITLES, getAtPath, buildPatch } from './propertySchema.js'
import StudioKeyframes from './StudioKeyframes.jsx'
import StudioAssets from './StudioAssets.jsx'

const baseName = (p) => String(p || '').split('/').pop() || p

const SCRUB_THRESHOLD = 3 // px of horizontal movement before a press becomes a drag (vs a click-to-type)

// ── scrubbable number field (drag to adjust · click to type) ─────────────────
// A draggable value-bar (After-Effects / CapCut style): drag horizontally to change the number
// (1px ≈ one `step`; hold Shift for fine control), or click it to type an exact value. Bounded
// fields (finite min AND max — opacity, volume, …) show a fill bar for the value's position in
// range. A drag goes through `onLive` (no per-frame history) after one `onScrubBegin` snapshot, so
// the whole drag is a single undo step; typing goes through `onCommit` (one step); a held-arrow run
// coalesces the same way a drag does (snapshot once, live per repeat).
function ScrubField({ label, value, onScrubBegin, onLive, onCommit, step = 0.1, min, max, suffix }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const inputRef = useRef(null)
  const cancelRef = useRef(false)   // Escape cancels the pending typed value
  const cleanupRef = useRef(null)   // tears down an in-flight drag (also on unmount)
  const keyScrubRef = useRef(false) // a held-arrow run = ONE undo step (snapshot once, live per repeat)

  useEffect(() => { if (editing && inputRef.current) { inputRef.current.focus(); inputRef.current.select() } }, [editing])
  useEffect(() => () => { if (cleanupRef.current) cleanupRef.current() }, [])

  const num = Number(value)
  const hasNum = value !== '' && value != null && Number.isFinite(num)
  const bounded = Number.isFinite(min) && Number.isFinite(max) && max > min
  const fill = bounded && hasNum ? Math.max(0, Math.min(1, (num - min) / (max - min))) : null

  const bound = (n) => {
    if (Number.isFinite(min)) n = Math.max(min, n)
    if (Number.isFinite(max)) n = Math.min(max, n)
    return n
  }
  const startFrom = () => (hasNum ? num : (Number.isFinite(min) ? min : 0))
  const beginEdit = () => { cancelRef.current = false; setDraft(hasNum ? fmtScrub(num) : ''); setEditing(true) }
  // Typed entry preserves precision (clamp + 3dp float-cleanup, matching the doc's round3) instead of
  // snapping to the drag `step`, and is a no-op when unchanged — so retyping the same value never pushes
  // a dead undo step or wipes the redo stack.
  const commitTyped = () => {
    if (cancelRef.current) { cancelRef.current = false; setEditing(false); return }
    setEditing(false)
    if (draft === '' || draft == null) return
    const n = Number(draft)
    if (Number.isNaN(n)) return
    const cn = roundTo(bound(n), 0.001)
    if (hasNum && cn === num) return
    onCommit(cn)
  }

  // Drag = scrub; a press that never crosses the threshold = click-to-type. Snapshot lazily on the
  // first real move so a bare click never pushes an undo step. Window listeners (no pointer capture,
  // matching the timeline), torn down on up/cancel/unmount.
  const onDown = (e) => {
    if (e.button != null && e.button !== 0) return
    e.preventDefault()
    const startX = e.clientX
    const startVal = startFrom()
    let moved = false
    const onMove = (ev) => {
      const dx = ev.clientX - startX
      if (!moved && Math.abs(dx) < SCRUB_THRESHOLD) return
      if (!moved) { onScrubBegin?.(); document.body.classList.add('st-scrubbing'); moved = true }
      onLive?.(scrubValue({ start: startVal, dx, step, min, max, fine: ev.shiftKey }))
    }
    const teardown = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', teardown)
      document.body.classList.remove('st-scrubbing')
      cleanupRef.current = null
    }
    const onUp = () => { const wasDrag = moved; teardown(); if (!wasDrag) beginEdit() }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', teardown)
    cleanupRef.current = teardown
  }

  // Arrow keys nudge the value. A HELD key (OS key-repeat) coalesces into ONE undo step the same way a
  // drag does — snapshot once on the first press, then live per repeat; keyup/blur ends the run. A press
  // that can't change the value (already at a bound) is a pure no-op (no snapshot, no history flood).
  // e.stopPropagation keeps Space/Escape/arrows/Delete from also firing the editor's global shortcuts.
  const onKey = (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); beginEdit(); return }
    const dir = (e.key === 'ArrowUp' || e.key === 'ArrowRight') ? 1
      : (e.key === 'ArrowDown' || e.key === 'ArrowLeft') ? -1 : 0
    if (!dir) return
    e.preventDefault(); e.stopPropagation()
    const base = startFrom()
    const nv = scrubValue({ start: base, dx: dir * (e.shiftKey ? 10 : 1), step, min, max })
    if (nv === base) return
    if (!keyScrubRef.current) { onScrubBegin?.(); keyScrubRef.current = true }
    onLive?.(nv)
  }
  const endKeyScrub = () => { keyScrubRef.current = false }

  if (editing) {
    return (
      <label className="st-f st-f-scrub">
        <span>{label}</span>
        <input ref={inputRef} type="number" step={step} min={min} max={max} value={draft}
          onChange={(e) => setDraft(e.target.value)} onBlur={commitTyped}
          onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur(); else if (e.key === 'Escape') { cancelRef.current = true; e.target.blur() } }} />
      </label>
    )
  }
  return (
    <label className="st-f st-f-scrub">
      <span>{label}</span>
      <div className="st-scrub" role="slider" tabIndex={0}
        aria-label={label} aria-valuenow={hasNum ? num : startFrom()} aria-valuemin={min} aria-valuemax={max}
        aria-valuetext={hasNum ? `${fmtScrub(num)}${suffix || ''}` : 'auto'}
        title="Drag to adjust · click to type"
        onPointerDown={onDown} onKeyDown={onKey} onKeyUp={endKeyScrub} onBlur={endKeyScrub} onDoubleClick={beginEdit}>
        {fill != null && <span className="st-scrub-fill" style={{ width: `${fill * 100}%` }} />}
        <span className="st-scrub-val">
          {hasNum ? `${fmtScrub(num)}${suffix || ''}` : <span className="st-scrub-auto">auto</span>}
        </span>
      </div>
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

// Resolve any CSS/ffmpeg color (name or hex) to a #rrggbb the native swatch can show. Returns a
// sensible default for unknown/empty values (or in jsdom, where canvas has no 2d context).
function cssColorToHex(color) {
  const c = String(color || '').trim()
  if (/^#[0-9a-fA-F]{6}$/.test(c)) return c.toLowerCase()
  if (/^#[0-9a-fA-F]{3}$/.test(c)) return '#' + c.slice(1).split('').map(x => x + x).join('').toLowerCase()
  if (typeof document !== 'undefined') {
    try {
      const ctx = document.createElement('canvas').getContext('2d')
      if (ctx) { ctx.fillStyle = '#000000'; ctx.fillStyle = c; if (/^#[0-9a-f]{6}$/i.test(ctx.fillStyle)) return ctx.fillStyle.toLowerCase() }
    } catch { /* no canvas (jsdom) */ }
  }
  return '#ffffff'
}

// Color field: a native swatch picker + a text input (so named ffmpeg colors like "white" still
// work). Dragging in the swatch is coalesced into ONE undo step (snapshot once, live per change),
// like the scrub fields; typing commits on blur. The renderer accepts names and #RRGGBB.
function ColorField({ label, value, onScrubBegin, onLive, onCommit }) {
  const [text, setText] = useState(value ?? '')
  const snapped = useRef(false)
  useEffect(() => { setText(value ?? '') }, [value])
  return (
    <label className="st-f">
      <span>{label}</span>
      <div className="st-color">
        <input type="color" className="st-color-swatch" value={cssColorToHex(value)} aria-label={`${label} swatch`}
          onChange={(e) => {
            if (!snapped.current) { onScrubBegin?.(); snapped.current = true }
            setText(e.target.value); onLive?.(e.target.value)
          }}
          onBlur={() => { snapped.current = false }} />
        <input type="text" className="st-color-text" value={text} spellCheck={false} autoComplete="off"
          placeholder="white, #ff0000…"
          onChange={(e) => setText(e.target.value)}
          onBlur={() => { if (text !== (value ?? '')) onCommit(text) }}
          onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur() }} />
      </div>
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
// Main-clip placement: Scale + X/Y (box top-left in canvas px). Writes transform.{scale,position}.
// Scale is polymorphic (schema oneOf): a UNIFORM number (× the fit-size) when "Lock aspect" is ON,
// or a per-axis {x,y} CANVAS-fraction box (e.g. a split-screen panel {x:1,y:0.5}) when OFF. A string
// anchor ("center") is normalized to {x,y} via clipPositionXY on first edit, so the renderer always
// gets numeric placement. Scrub = live (one undo step), typing = commit. Drag-to-move = canvas.
function ClipTransform({ cut, canvas, meta, onUpdate, live, onScrubBegin }) {
  const t = cut.transform || {}
  const scaleObj = isScaleObject(t.scale)
  // Uniform (locked) value for the single Scale field; per-axis values for the unlocked fields.
  const uniform = scaleObj ? 1 : (Number(t.scale) || 1)
  const { sx, sy } = scaleAxes(t.scale != null ? t.scale : 1)
  const xy = clipPositionXY(cut, meta, canvas)
  const xf = (patch) => ({ transform: { ...(cut.transform || {}), ...patch } })
  const scrub = (buildPatchFn) => ({
    onScrubBegin,
    onLive: (v) => live?.(xf(buildPatchFn(v))),
    onCommit: (v) => onUpdate(xf(buildPatchFn(v))),
  })
  // Toggling the lock is a single committed step: ON collapses {x,y}→a uniform number (sx wins, the
  // width axis); OFF expands the uniform number→{x:n,y:n} so the two fields start where the box is.
  const toggleLock = (e) => {
    const next = e.target.checked ? sx : { x: sx, y: sy }
    onUpdate(xf({ scale: next }))
  }
  return (
    <>
      <label className="st-check">
        <input type="checkbox" checked={!scaleObj} onChange={toggleLock} />
        Lock aspect (uniform scale)
      </label>
      {scaleObj ? (
        <div className="st-row">
          <ScrubField label="Scale X" suffix="×" value={sx} step={0.05} min={0}
            {...scrub((v) => ({ scale: { x: v, y: sy } }))} />
          <ScrubField label="Scale Y" suffix="×" value={sy} step={0.05} min={0}
            {...scrub((v) => ({ scale: { x: sx, y: v } }))} />
        </div>
      ) : (
        <ScrubField label="Scale" suffix="×" value={uniform} step={0.05} min={0.05}
          {...scrub((v) => ({ scale: v }))} />
      )}
      <div className="st-row">
        <ScrubField label="X" suffix="px" value={xy.x} step={1}
          {...scrub((v) => ({ position: { x: v, y: xy.y } }))} />
        <ScrubField label="Y" suffix="px" value={xy.y} step={1}
          {...scrub((v) => ({ position: { x: xy.x, y: v } }))} />
      </div>
      <div className="st-hint">{scaleObj
        ? 'Scale X/Y size the box as a fraction of the canvas (e.g. 1 × 0.5 = split-screen panel); the clip fits inside it'
        : 'drag the clip on the canvas to move it; Scale resizes it over the background'}</div>
    </>
  )
}

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
function CropControl({ cut, canvas, meta, onUpdate, live, onScrubBegin }) {
  const sw = meta?.width || canvas.width
  const sh = meta?.height || canvas.height
  const crop = cut.transform?.crop || null
  const cropPatch = (patch) => ({ transform: { ...(cut.transform || {}), crop: { ...(crop || {}), ...patch } } })
  const clearCrop = () => { const t = { ...(cut.transform || {}) }; delete t.crop; onUpdate({ transform: t }) }
  if (!crop) return <button className="st-link" onClick={() => onUpdate(cropPatch({ x: 0, y: 0, width: sw, height: sh }))}>+ add crop</button>
  const scrub = (key) => ({ onScrubBegin, onLive: (v) => live?.(cropPatch({ [key]: v })), onCommit: (v) => onUpdate(cropPatch({ [key]: v })) })
  return (
    <>
      <button className="st-link" onClick={clearCrop}>remove crop</button>
      <div className="st-row">
        <ScrubField label="X" suffix="px" value={crop.x ?? 0} step={1} min={0} {...scrub('x')} />
        <ScrubField label="Y" suffix="px" value={crop.y ?? 0} step={1} min={0} {...scrub('y')} />
      </div>
      <div className="st-row">
        <ScrubField label="W" suffix="px" value={crop.width ?? sw} step={1} min={1} {...scrub('width')} />
        <ScrubField label="H" suffix="px" value={crop.height ?? sh} step={1} min={1} {...scrub('height')} />
      </div>
      <div className="st-hint">source pixels, applied before scaling to canvas</div>
    </>
  )
}

function AudioMixControl({ ov, onUpdate, live, onScrubBegin }) {
  const mixPatch = (patch) => ({ audio_mix: { ...(ov.audio_mix || {}), ...patch } })
  return (
    <>
      <label className="st-check">
        <input type="checkbox" checked={!!ov.audio_mix?.enabled}
          onChange={(e) => onUpdate({ audio_mix: { ...(ov.audio_mix || { volume: 1 }), enabled: e.target.checked } })} />
        mix this clip’s audio into the timeline
      </label>
      {ov.audio_mix?.enabled &&
        <ScrubField label="Volume" value={ov.audio_mix?.volume ?? 1} step={0.1} min={0} max={2}
          onScrubBegin={onScrubBegin} onLive={(v) => live?.(mixPatch({ volume: v }))} onCommit={(v) => onUpdate(mixPatch({ volume: v }))} />}
    </>
  )
}

// Text position is polymorphic: a named anchor (string) OR a free {x,y} (after a canvas drag).
function TextPositionControl({ ov, canvas, onUpdate, live, onScrubBegin }) {
  const pos = ov.position
  const isAnchor = typeof pos === 'string' || pos == null
  const posPatch = (patch) => ({ position: { ...(typeof pos === 'object' && pos ? pos : {}), ...patch } })
  return (
    <>
      <SelectField label="Position" value={isAnchor ? (pos || 'center') : '__xy__'}
        options={[...TEXT_ANCHORS.map(a => ({ value: a, label: a })), { value: '__xy__', label: 'Custom X/Y' }]}
        onCommit={(v) => onUpdate({ position: v === '__xy__' ? anchorToXY(typeof pos === 'string' ? pos : 'center', canvas) : v })} />
      {!isAnchor && (
        <div className="st-row">
          <ScrubField label="X" suffix="px" value={pos?.x ?? 0} step={1}
            onScrubBegin={onScrubBegin} onLive={(v) => live?.(posPatch({ x: v }))} onCommit={(v) => onUpdate(posPatch({ x: v }))} />
          <ScrubField label="Y" suffix="px" value={pos?.y ?? 0} step={1}
            onScrubBegin={onScrubBegin} onLive={(v) => live?.(posPatch({ y: v }))} onCommit={(v) => onUpdate(posPatch({ y: v }))} />
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
  const live = (v) => ctx.live?.(buildPatch(obj, field.path, v))
  switch (field.control) {
    case 'number':
      return <ScrubField label={field.label} value={value ?? field.default ?? ''} step={field.step} min={field.min} max={field.max} suffix={field.suffix}
        onScrubBegin={ctx.snapshot} onLive={live} onCommit={commit} />
    case 'text':
      return <TextField label={field.label} value={value ?? ''} onCommit={field.required ? (v) => { if (v.trim() !== '') commit(v) } : commit} />
    case 'color':
      return <ColorField label={field.label} value={value ?? field.default ?? ''}
        onScrubBegin={ctx.snapshot} onLive={live} onCommit={commit} />
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
    case 'clipTransform':
      return <ClipTransform cut={obj} canvas={ctx.canvas} meta={ctx.meta} onUpdate={onUpdate} live={ctx.live} onScrubBegin={ctx.snapshot} />
    case 'crop':
      return <CropControl cut={obj} canvas={ctx.canvas} meta={ctx.meta} onUpdate={onUpdate} live={ctx.live} onScrubBegin={ctx.snapshot} />
    case 'audioMix':
      return <AudioMixControl ov={obj} onUpdate={onUpdate} live={ctx.live} onScrubBegin={ctx.snapshot} />
    case 'textPosition':
      return <TextPositionControl ov={obj} canvas={ctx.canvas} onUpdate={onUpdate} live={ctx.live} onScrubBegin={ctx.snapshot} />
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
  onLiveUpdateCut, onLiveUpdateOverlay, onLiveUpdateAudio, onScrubBegin,
  onAddImage, onAddClip, onAddSfx, onSetMusic, onSetBackground, onUploadAsset, onAssetsChanged,
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
          ctx={{
            canvas, ffmpeg, assets, meta: sourceMetas[selCut.source], idLabel: selCut.id,
            live: (patch) => onLiveUpdateCut?.(selCut.id, patch), snapshot: onScrubBegin,
          }} />
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
            live: (patch) => onLiveUpdateOverlay?.(selOverlayIndex, patch), snapshot: onScrubBegin,
          }} />
      </aside>
    )
  }

  if (selAudioObj) {
    const type = selAudio.audioKind === 'music' ? 'music' : selAudio.audioKind === 'narration' ? 'narration' : 'sfx'
    return (
      <aside className="st-inspector">
        <SchemaForm type={type} obj={selAudioObj} onUpdate={onUpdateAudio}
          ctx={{ canvas, assets, live: onLiveUpdateAudio, snapshot: onScrubBegin }} />
        <div className="st-hint">Delete with the 🗑 button in the timeline toolbar (⌫).</div>
      </aside>
    )
  }

  // Nothing selected → the Assets tab (feat 4): clicking outside any clip shows assets, not a blank panel.
  return (
    <StudioAssets
      projectId={projectId} assets={assets} background={doc?.metadata?.background || null}
      onAddImage={onAddImage} onAddClip={onAddClip} onAddSfx={onAddSfx} onSetMusic={onSetMusic}
      onSetBackground={onSetBackground} onUploadAsset={onUploadAsset} onAssetsChanged={onAssetsChanged}
    />
  )
}
