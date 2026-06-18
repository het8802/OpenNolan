// Studio toolbar — the action buttons. Global actions (undo/redo, add, canvas, save,
// render, preview mode) are always present; clip/overlay actions appear when something
// is selected. Only tools the FFmpeg render path actually renders get a button.

import { SPEED_PRESETS, CANVAS_PRESETS } from './model.js'

export default function StudioToolbar({
  doc, canvas, ffmpeg, selCut, selOverlayIndex,
  canUndo, canRedo, dirty, rendering, hasRender, previewMode, playing, assets,
  onUndo, onRedo, onSave, onRender, onTogglePlay, onPreviewMode,
  onSplit, onDuplicate, onDelete, onSpeed, onAddText, onAddImage, onCanvas,
}) {
  const images = assets?.kinds?.images || []
  const curCanvas = `${canvas.width}×${canvas.height}`

  return (
    <div className="st-tools">
      <div className="st-grp">
        <button className="st-ico" onClick={onUndo} disabled={!canUndo} title="Undo (⌘Z)">↶</button>
        <button className="st-ico" onClick={onRedo} disabled={!canRedo} title="Redo (⇧⌘Z)">↷</button>
      </div>

      <div className="st-grp">
        <button className="st-btn" onClick={onAddText} title="Add a text overlay">＋ Text</button>
        <label className="st-btn st-select-btn" title="Add an image overlay">
          ＋ Image
          <select
            value=""
            onChange={(e) => { if (e.target.value) onAddImage(e.target.value) }}
            disabled={!images.length}
          >
            <option value="">{images.length ? 'pick an image…' : 'no images uploaded'}</option>
            {images.map(im => <option key={im.path} value={im.path}>{im.name}</option>)}
          </select>
        </label>
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

      {/* contextual: a clip is selected */}
      {selCut && (
        <div className="st-grp st-grp-ctx">
          <button className="st-btn" onClick={onSplit} title="Split at playhead (S)">✂ Split</button>
          <button className="st-btn" onClick={onDuplicate} title="Duplicate clip">⧉ Duplicate</button>
          <button className="st-btn st-danger" onClick={onDelete} title="Delete clip (⌫)">🗑 Delete</button>
          <span className="st-speed">
            {SPEED_PRESETS.map(s => (
              <button
                key={s}
                className={`st-chip ${(Number(selCut.speed) || 1) === s ? 'on' : ''}`}
                onClick={() => onSpeed(s)}
                title={`${s}× speed`}
              >{s}×</button>
            ))}
          </span>
        </div>
      )}

      {/* contextual: an overlay is selected */}
      {selOverlayIndex >= 0 && (
        <div className="st-grp st-grp-ctx">
          <button className="st-btn st-danger" onClick={onDelete} title="Delete overlay (⌫)">🗑 Delete overlay</button>
        </div>
      )}

      <div className="st-grp st-grp-right">
        <button className="st-play" onClick={onTogglePlay} title="Play / pause (Space)" aria-label={playing ? 'Pause' : 'Play'}>
          {playing ? '⏸' : '▶'}
        </button>
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
