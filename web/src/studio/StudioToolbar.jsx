// Studio project toolbar — global, non-transport actions only. Transport (play/pause) and
// clip ops (split/duplicate/delete) live in the TIMELINE toolbar (StudioTimeline.jsx); image
// adding moved to the Assets tab (StudioInspector.jsx). This bar keeps: undo/redo, +Text,
// canvas size, preview-mode toggle, Save, Export. Only tools the FFmpeg path renders appear.

import { CANVAS_PRESETS } from './model.js'
import { IconUndo, IconRedo } from '../components/icons.jsx'

// The output-size picker. Rendered twice (inline + inside the More menu); `bare` drops the
// label because the More row supplies its own.
function CanvasPicker({ canvas, curCanvas, onCanvas, bare = false }) {
  const sel = (
    <select
      value={CANVAS_PRESETS.find(p => p.width === canvas.width && p.height === canvas.height)?.label || ''}
      onChange={(e) => {
        const p = CANVAS_PRESETS.find(x => x.label === e.target.value)
        if (p) onCanvas({ width: p.width, height: p.height })
      }}
      title={`Output ${curCanvas}`}
      aria-label="Output canvas size"
    >
      {!CANVAS_PRESETS.some(p => p.width === canvas.width && p.height === canvas.height) &&
        <option value="">{curCanvas} (custom)</option>}
      {CANVAS_PRESETS.map(p => <option key={p.label} value={p.label}>{p.label}</option>)}
    </select>
  )
  if (bare) return sel
  return <label className="st-canvas"><span>Canvas</span>{sel}</label>
}

export default function StudioToolbar({
  doc, canvas, ffmpeg, canUndo, canRedo, dirty, rendering, hasRender, previewMode,
  onUndo, onRedo, onSave, onRender, onPreviewMode, onAddText, onCanvas,
  recording, onToggleRecord,
}) {
  const curCanvas = `${canvas.width}×${canvas.height}`

  return (
    <div className="st-tools">
      <div className="st-grp">
        <button className="st-ico" onClick={onUndo} disabled={!canUndo} title="Undo (⌘Z)" aria-label="Undo"><IconUndo /></button>
        <button className="st-ico" onClick={onRedo} disabled={!canRedo} title="Redo (⇧⌘Z)" aria-label="Redo"><IconRedo /></button>
      </div>

      <div className="st-grp">
        <button className="st-btn" onClick={onAddText} title="Add a text overlay">＋ Text</button>
      </div>

      {/* Canvas + the debug recorder are the lowest-priority controls in this bar, so they are
          the ones that move into the More menu when it gets tight. Both are rendered twice —
          once inline, once inside More — and a container query on .st-bar picks which copy is
          visible. That keeps the browser doing the measuring (no ResizeObserver to get wrong)
          while Save and Export never leave the line. */}
      <div className="st-grp st-grp-optional">
        <CanvasPicker canvas={canvas} curCanvas={curCanvas} onCanvas={onCanvas} />
      </div>

      <div className="st-grp st-grp-right">
        {onToggleRecord && (
          <button
            className={`st-ico st-rec st-grp-optional${recording ? ' on' : ''}`}
            onClick={onToggleRecord}
            title={recording
              ? 'Stop debug recording (writes the session log to disk)'
              : 'Record a debug session — console, clicks & scrubs → .agents/tools/logs/ui-sessions'}
            aria-label={recording ? 'Stop debug recording' : 'Record debug session'}
            aria-pressed={!!recording}
          >
            <span className="st-rec-dot" />
          </button>
        )}
        <details className="st-more">
          <summary className="st-btn" title="More toolbar controls" aria-label="More controls">More</summary>
          <div className="st-more-panel">
            <label className="st-more-row">
              <span>Canvas</span>
              <CanvasPicker canvas={canvas} curCanvas={curCanvas} onCanvas={onCanvas} bare />
            </label>
            {onToggleRecord && (
              <button className="st-more-row st-more-btn" onClick={onToggleRecord} aria-pressed={!!recording}>
                <span>{recording ? 'Stop debug recording' : 'Record debug session'}</span>
                <span className={`st-rec-dot${recording ? ' on' : ''}`} />
              </button>
            )}
          </div>
        </details>
        <span className="st-toggle">
          <button className={`st-seg ${previewMode === 'source' ? 'on' : ''}`} onClick={() => onPreviewMode('source')} title="Live source scrub">Source</button>
          <button className={`st-seg ${previewMode === 'render' ? 'on' : ''}`} onClick={() => onPreviewMode('render')} disabled={!hasRender} title="Composed render">Render</button>
        </span>
        <button className="st-btn" onClick={onSave} disabled={!dirty} title="Save (⌘S)">Save</button>
        {/* Called "Export", not "Render". It sat directly beside the Source/Render preview
            segment, so two adjacent controls both read "Render" and looked duplicated. The
            name also taught render-to-preview, which RULES.md forbids: "The user should
            NEVER have to hit Render just to see an edit." Render/Re-render stays the
            vocabulary for the internal job and future comp materialisation. */}
        <button className="st-btn st-primary" onClick={onRender} disabled={rendering}
          title="Export the final MP4 (only changed scenes re-render)">
          {rendering ? 'Exporting…' : 'Export'}
        </button>
      </div>
    </div>
  )
}
