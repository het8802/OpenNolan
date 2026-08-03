// Center lightbox: opens an asset full-screen over the app, mirroring how artifacts open in the
// middle. Esc / backdrop-click closes; ←/→ steps through the current list. Shared by both Assets
// panels — the agent page opens it to LOOK at a file, the editor also passes `onAdd` so the same
// dialog can put the file on the timeline (kind-aware: overlay / cut / music bed / SFX).

import { useEffect, useState } from 'react'
import { IconMusic } from './icons.jsx'

// What the editor's primary button does for each kind. Text files have no timeline home, so
// they open read-only (no button).
const ADD_LABEL = {
  images: 'Add as overlay',
  video: 'Append as clip',
  music: 'Set as music bed',
  audio: 'Drop SFX at playhead',
}

// Guard against a pathological file: we only ever list .srt/.vtt/.txt/.md, but a multi-MB one
// would still lock the renderer up in a <pre>.
const TEXT_LIMIT = 200_000

export default function AssetModal({ items, index, onClose, onAdd }) {
  const [i, setI] = useState(index)
  useEffect(() => { setI(index) }, [index])
  const prev = () => setI(x => (x - 1 + items.length) % items.length)
  const next = () => setI(x => (x + 1) % items.length)

  // Capture-phase so the app's global shortcuts never fire behind an open dialog — Studio binds
  // Space (play/pause), `s` (split) and Delete on window, and previewing a clip must not edit
  // the timeline. stopPropagation only silences JS listeners; native controls still work.
  useEffect(() => {
    const onKey = (e) => {
      e.stopPropagation()
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft') setI(x => (x - 1 + items.length) % items.length)
      else if (e.key === 'ArrowRight') setI(x => (x + 1) % items.length)
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [items.length, onClose])

  const item = items[i]
  if (!item) return null
  const multi = items.length > 1
  const addLabel = onAdd && ADD_LABEL[item.kind]
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="asset-lightbox" onClick={e => e.stopPropagation()}>
        <div className="al-head">
          <span className="al-name" title={item.name}>{item.name}</span>
          <div className="al-actions">
            <a className="al-dl" href={item.url} download={item.name} title="Download">⤓</a>
            <button className="am-close" onClick={onClose} title="Close (Esc)">✕</button>
          </div>
        </div>
        <div className="al-stage">
          {multi && <button className="al-nav prev" onClick={prev} title="Previous (←)">‹</button>}
          {/* key on the URL so navigating remounts the media (fresh load + autoplay) */}
          <div className="al-media" key={item.url}>
            {item.kind === 'images' && <img src={item.url} alt={item.name} />}
            {item.kind === 'video' && <video src={item.url} controls autoPlay />}
            {(item.kind === 'audio' || item.kind === 'music') && (
              <div className="al-audio">
                <div className="al-audio-icon"><IconMusic size={44} /></div>
                <audio src={item.url} controls autoPlay />
              </div>
            )}
            {item.kind === 'text' && <TextPreview url={item.url} />}
          </div>
          {multi && <button className="al-nav next" onClick={next} title="Next (→)">›</button>}
        </div>
        {addLabel && (
          <div className="al-foot">
            <button className="al-add" onClick={() => { onAdd(item); onClose() }}>+ {addLabel}</button>
          </div>
        )}
        {multi && <div className="al-count">{i + 1} / {items.length}</div>}
      </div>
    </div>
  )
}

// Subtitles / notes / markdown — read-only, no timeline action.
function TextPreview({ url }) {
  const [body, setBody] = useState(null)
  useEffect(() => {
    let alive = true
    fetch(url)
      .then(r => r.text())
      .then(t => { if (alive) setBody(t.slice(0, TEXT_LIMIT)) })
      .catch(e => { if (alive) setBody(`Could not read this file: ${e.message || e}`) })
    return () => { alive = false }
  }, [url])
  return <pre className="al-text">{body === null ? 'Loading…' : body}</pre>
}
