// Studio Assets browser — shown in the properties panel when nothing is selected (feat 4).
// It is a FOLDER browser over the real project tree (it replaced four flat kind tabs), so the
// user finds their uploads AND the agent's created assets where they actually live:
//   assets/{images,video,audio,music}/ · hf/renders/ (agent clips) · renders/ (final export)
// The backend's /browse endpoint decides what's worth navigating: sub-folders + media files
// only — no dot-folders (`.mc/` = the agent's chat history), no JSON/HTML, no proxy cache.
// Clicking a file OPENS it in the shared dialog (play it, read it, see it full size); the dialog's
// primary button then adds it to the timeline in the way that fits its kind — this is where images
// come from now that +Image left the project toolbar:
//   image → image overlay   ·   video → appended cut   ·   music → music bed   ·   audio → SFX
// Dragging a file onto the timeline stays the direct path, no dialog.
// Pure display; all mutation goes through the interp mutators wired in Studio.

import { useRef, useState } from 'react'
import * as api from '../api.js'
import { IconPlay, IconMusic, IconFileText } from '../components/icons.jsx'
import { FolderNav, useFolderBrowse, uploadKindFor, uploadDirLabel } from '../components/FolderBrowser.jsx'
import AssetModal from '../components/AssetModal.jsx'

export default function StudioAssets({ projectId, assets, background, onAddImage, onAddClip, onAddSfx, onSetMusic, onSetBackground, onUploadAsset }) {
  const [dragging, setDragging] = useState(false)
  const [viewer, setViewer] = useState(null)   // index into `files` — the open dialog
  const inputRef = useRef(null)
  // `assets` is the parent's asset listing — it re-lists after an upload and at the end of an
  // agent turn, so keying off it re-lists this folder too (new clips show without reopening).
  const { cwd, setCwd, entries, dirs, files } = useFolderBrowse(projectId, assets)

  const onAdd = (f) => {
    if (f.kind === 'images') onAddImage(f.path)
    else if (f.kind === 'video') onAddClip(f.path)
    else if (f.kind === 'music') onSetMusic(f.path)
    else if (f.kind === 'audio') onAddSfx(f.path)
  }
  const handleFiles = (fl) => {
    const file = fl && fl[0]
    if (onUploadAsset && file) onUploadAsset(uploadKindFor(cwd, file), file)
  }

  return (
    <aside className="st-inspector st-assets">
      {onSetBackground && (
        <BackgroundControl projectId={projectId} images={assets?.kinds?.images || []}
          background={background} onSetBackground={onSetBackground} />
      )}
      <h3 className="st-insp-head">Assets</h3>

      <FolderNav cwd={cwd} dirs={dirs} onNavigate={setCwd} />

      {files.length > 0 && (
        <div className="st-hint">click a file to open it · or drag it onto the timeline</div>
      )}

      <div className="asset-grid">
        {entries.length === 0 && <p className="empty">This folder is empty.</p>}
        {files.map((f, i) => (
          <div
            key={f.path}
            className="asset-item clickable"
            title={`${f.name} — click to open, or drag onto the timeline`}
            role="button" tabIndex={0} draggable
            onDragStart={(e) => {
              e.dataTransfer.setData('application/x-opennolan-asset', JSON.stringify({ kind: f.kind, path: f.path }))
              e.dataTransfer.effectAllowed = 'copy'
            }}
            onClick={() => setViewer(i)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setViewer(i) } }}
          >
            {f.kind === 'images' && <img src={api.fileUrl(projectId, f.path, f.mtime)} alt={f.name} loading="lazy" />}
            {f.kind === 'video' && (
              <div className="asset-thumb video">
                <video src={api.fileUrl(projectId, f.path, f.mtime)} preload="metadata" muted playsInline />
                <span className="asset-play"><IconPlay /></span>
              </div>
            )}
            {(f.kind === 'audio' || f.kind === 'music') && (
              <div className="asset-thumb audio"><span className="asset-audio-icon"><IconMusic size={26} /></span></div>
            )}
            {f.kind === 'text' && (
              <div className="asset-thumb text"><span className="asset-audio-icon"><IconFileText size={26} /></span></div>
            )}
            <div className="asset-name">{f.name}</div>
          </div>
        ))}
      </div>

      {onUploadAsset && (
        <div
          className={`dropzone ${dragging ? 'drag' : ''}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); handleFiles(e.dataTransfer.files) }}
        >
          Drop a file here, or click to choose — saves to <strong>{uploadDirLabel(cwd)}</strong>
          <input ref={inputRef} type="file" hidden onChange={(e) => handleFiles(e.target.files)} />
        </div>
      )}

      {viewer !== null && (
        <AssetModal
          items={files.map(f => ({ ...f, url: api.fileUrl(projectId, f.path, f.mtime) }))}
          index={viewer} onClose={() => setViewer(null)} onAdd={onAdd}
        />
      )}
    </aside>
  )
}

// Project-wide background behind ALL main clips (shows where a moved/scaled clip doesn't cover the
// canvas). Black by default; pick a solid color (native swatch) or any project image. Writes
// metadata.background via onSetBackground (null = back to black).
function BackgroundControl({ projectId, images, background, onSetBackground }) {
  const isBlack = !background
  const isColor = background?.type === 'color'
  const isImage = background?.type === 'image'
  const colorVal = isColor && /^#[0-9a-fA-F]{6}$/.test(background.color || '') ? background.color : '#000000'
  return (
    <section className="st-sec st-bg-ctrl">
      <div className="st-sec-h">Canvas background</div>
      <div className="st-bg-row">
        <button className={`st-chip ${isBlack ? 'on' : ''}`} onClick={() => onSetBackground(null)}>Black</button>
        <label className={`st-bg-swatch ${isColor ? 'on' : ''}`} title="Solid color background">
          <input type="color" value={colorVal}
            onChange={(e) => onSetBackground({ type: 'color', color: e.target.value })} />
        </label>
        <span className="st-hint" style={{ margin: 0 }}>solid color</span>
      </div>
      {images.length > 0 && (
        <>
          <div className="st-hint">or use an image (covers the canvas):</div>
          <div className="st-bg-images">
            {images.map(img => (
              <button key={img.path}
                className={`st-bg-img ${isImage && background.asset_id === img.path ? 'on' : ''}`}
                title={img.name} onClick={() => onSetBackground({ type: 'image', asset_id: img.path })}>
                <img src={api.fileUrl(projectId, img.path, img.mtime)} alt={img.name} loading="lazy" />
              </button>
            ))}
          </div>
        </>
      )}
    </section>
  )
}
