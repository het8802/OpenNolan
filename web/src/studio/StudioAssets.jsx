// Studio Assets tab — shown in the properties panel when nothing is selected (feat 4).
// Mirrors the agent window's asset kinds (images / video / audio / music). Clicking an asset
// ADDS it to the timeline in the way that fits its kind — this is where images come from now
// that +Image left the project toolbar:
//   image → image overlay   ·   video → appended cut   ·   music → music bed   ·   audio → SFX
// Pure display + click-to-add; all mutation goes through the interp mutators wired in Studio.

import { useState } from 'react'
import * as api from '../api.js'

const KINDS = ['images', 'video', 'audio', 'music']
const HINT = {
  images: 'click an image to add it as an overlay',
  video: 'click a clip to append it to the timeline',
  audio: 'click to drop it as an SFX at the playhead',
  music: 'click to set it as the music bed',
}

export default function StudioAssets({ projectId, assets, onAddImage, onAddClip, onAddSfx, onSetMusic }) {
  const [kind, setKind] = useState('images')
  const files = assets?.kinds?.[kind] || []

  const onPick = (path) => {
    if (kind === 'images') onAddImage(path)
    else if (kind === 'video') onAddClip(path)
    else if (kind === 'music') onSetMusic(path)
    else onAddSfx(path)
  }

  return (
    <aside className="st-inspector st-assets">
      <h3 className="st-insp-head">Assets</h3>
      <div className="st-asset-tabs">
        {KINDS.map(k => (
          <button
            key={k}
            className={`st-asset-tab ${kind === k ? 'on' : ''}`}
            onClick={() => setKind(k)}
          >{k}{assets?.kinds?.[k]?.length ? ` (${assets.kinds[k].length})` : ''}</button>
        ))}
      </div>
      <div className="st-hint">{HINT[kind]}</div>

      {files.length === 0 ? (
        <div className="st-insp-empty">No {kind} yet — add them from the agent window or upload.</div>
      ) : (
        <div className="st-asset-grid">
          {files.map(f => (
            <button
              key={f.path}
              className={`st-asset-item k-${kind}`}
              title={`${f.name} — click or drag onto the timeline`}
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData('application/x-opennolan-asset', JSON.stringify({ kind, path: f.path }))
                e.dataTransfer.effectAllowed = 'copy'
              }}
              onClick={() => onPick(f.path)}
            >
              {kind === 'images' && <img src={api.fileUrl(projectId, f.path, f.mtime)} alt={f.name} loading="lazy" />}
              {kind === 'video' && (
                <span className="st-asset-thumb">
                  <video src={api.fileUrl(projectId, f.path, f.mtime)} preload="metadata" muted playsInline />
                  <span className="st-asset-badge">▶</span>
                </span>
              )}
              {(kind === 'audio' || kind === 'music') && (
                <span className="st-asset-thumb st-asset-audio">{kind === 'music' ? '♫' : '♪'}</span>
              )}
              <span className="st-asset-name">{f.name}</span>
            </button>
          ))}
        </div>
      )}
    </aside>
  )
}
