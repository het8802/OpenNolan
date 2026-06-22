// Studio Assets tab — shown in the properties panel when nothing is selected (feat 4).
// Mirrors the agent window's asset kinds (images / video / audio / music). Clicking an asset
// ADDS it to the timeline in the way that fits its kind — this is where images come from now
// that +Image left the project toolbar:
//   image → image overlay   ·   video → appended cut   ·   music → music bed   ·   audio → SFX
// Pure display + click-to-add; all mutation goes through the interp mutators wired in Studio.

import { useRef, useState } from 'react'
import * as api from '../api.js'
import { IconPlay, IconMusic } from '../components/icons.jsx'

const KINDS = ['images', 'video', 'audio', 'music']
const HINT = {
  images: 'click to add as an overlay · or drag onto the timeline',
  video: 'click to append a clip · or drag onto the timeline',
  audio: 'click to drop an SFX at the playhead',
  music: 'click to set the music bed',
}

// Editor Assets panel (shown when nothing is selected) — same look + upload flow as the pipeline
// page's Assets panel. Clicking an asset ADDS it to the timeline (image→overlay, video→cut,
// music→bed, audio→SFX); the dropzone uploads a new file for the active kind (via onUploadAsset).
export default function StudioAssets({ projectId, assets, background, onAddImage, onAddClip, onAddSfx, onSetMusic, onSetBackground, onUploadAsset }) {
  const [kind, setKind] = useState('images')
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef(null)
  const files = assets?.kinds?.[kind] || []

  const onPick = (path) => {
    if (kind === 'images') onAddImage(path)
    else if (kind === 'video') onAddClip(path)
    else if (kind === 'music') onSetMusic(path)
    else onAddSfx(path)
  }
  const handleFiles = (fl) => { if (onUploadAsset && fl && fl.length) onUploadAsset(kind, fl[0]) }

  return (
    <aside className="st-inspector st-assets">
      {onSetBackground && (
        <BackgroundControl projectId={projectId} images={assets?.kinds?.images || []}
          background={background} onSetBackground={onSetBackground} />
      )}
      <h3 className="st-insp-head">Assets</h3>
      <div className="asset-tabs">
        {KINDS.map(k => (
          <button key={k} className={`asset-tab ${kind === k ? 'active' : ''}`} onClick={() => setKind(k)}>
            {k}{assets?.kinds?.[k]?.length ? ` (${assets.kinds[k].length})` : ''}
          </button>
        ))}
      </div>
      <div className="st-hint">{HINT[kind]}</div>

      <div className="asset-grid">
        {files.length === 0 && <p className="empty">No {kind} yet.</p>}
        {files.map(f => (
          <div
            key={f.path}
            className="asset-item clickable"
            title={`${f.name} — click to add, or drag onto the timeline`}
            role="button" tabIndex={0} draggable
            onDragStart={(e) => {
              e.dataTransfer.setData('application/x-opennolan-asset', JSON.stringify({ kind, path: f.path }))
              e.dataTransfer.effectAllowed = 'copy'
            }}
            onClick={() => onPick(f.path)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(f.path) } }}
          >
            {kind === 'images' && <img src={api.fileUrl(projectId, f.path, f.mtime)} alt={f.name} loading="lazy" />}
            {kind === 'video' && (
              <div className="asset-thumb video">
                <video src={api.fileUrl(projectId, f.path, f.mtime)} preload="metadata" muted playsInline />
                <span className="asset-play"><IconPlay /></span>
              </div>
            )}
            {(kind === 'audio' || kind === 'music') && (
              <div className="asset-thumb audio"><span className="asset-audio-icon"><IconMusic size={26} /></span></div>
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
          Drop a <strong>{kind}</strong> file here, or click to choose
          <input ref={inputRef} type="file" hidden onChange={(e) => handleFiles(e.target.files)} />
        </div>
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
