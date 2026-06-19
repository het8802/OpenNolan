// Studio project toolbar — global, non-transport actions only. Transport (play/pause) and
// clip ops (split/duplicate/delete) live in the TIMELINE toolbar (StudioTimeline.jsx); image
// adding moved to the Assets tab (StudioInspector.jsx). This bar keeps: undo/redo, +Text,
// canvas size, preview-mode toggle, Save, Render. Only tools the FFmpeg path renders appear.

import { CANVAS_PRESETS } from './model.js'

export default function StudioToolbar({
  doc, canvas, ffmpeg, canUndo, canRedo, dirty, rendering, hasRender, previewMode,
  onUndo, onRedo, onSave, onRender, onPreviewMode, onAddText, onCanvas,
}) {
  const curCanvas = `${canvas.width}×${canvas.height}`

  return (
    <div className="st-tools">
      <div className="st-grp">
        <button className="st-ico" onClick={onUndo} disabled={!canUndo} title="Undo (⌘Z)">↶</button>
        <button className="st-ico" onClick={onRedo} disabled={!canRedo} title="Redo (⇧⌘Z)">↷</button>
      </div>

      <div className="st-grp">
        <button className="st-btn" onClick={onAddText} title="Add a text overlay">＋ Text</button>
      </div>

      <div className="st-grp">
        <label className="st-canvas">
          <span>Canvas</span>
          <select
            value={CANVAS_PRESETS.find(p => p.width === canvas.width && p.height === canvas.height)?.label || ''}
            onChange={(e) => {
              const p = CANVAS_PRESETS.find(x => x.label === e.target.value)
              if (p) onCanvas({ width: p.width, height: p.height })
            }}
            title={`Output ${curCanvas}`}
          >
            {!CANVAS_PRESETS.some(p => p.width === canvas.width && p.height === canvas.height) &&
              <option value="">{curCanvas} (custom)</option>}
            {CANVAS_PRESETS.map(p => <option key={p.label} value={p.label}>{p.label}</option>)}
          </select>
        </label>
      </div>

      <div className="st-grp st-grp-right">
        <span className="st-toggle">
          <button className={`st-seg ${previewMode === 'source' ? 'on' : ''}`} onClick={() => onPreviewMode('source')} title="Live source scrub">Source</button>
          <button className={`st-seg ${previewMode === 'render' ? 'on' : ''}`} onClick={() => onPreviewMode('render')} disabled={!hasRender} title="Composed render">Render</button>
        </span>
        <button className="st-btn" onClick={onSave} disabled={!dirty} title="Save (⌘S)">Save</button>
        <button className="st-btn st-primary" onClick={onRender} disabled={rendering} title="Render preview (render-once)">
          {rendering ? 'Rendering…' : 'Render'}
        </button>
      </div>
    </div>
  )
}
