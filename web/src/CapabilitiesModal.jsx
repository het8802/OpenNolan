// CapabilitiesModal — the "Capabilities" settings panel. Lists the lazy capability packs (heavy,
// on-device features kept out of the base install) with their size + installed state, and lets the
// user install one on demand. Also shows the base runtime health (video engine / ffmpeg) read-only.
// Mirrors EnvModal's structure; installs stream via the shared CapabilityInstall component.

import { useEffect, useState } from 'react'
import * as api from './api.js'
import { IconX, IconCheck, IconAlert } from './components/icons.jsx'
import CapabilityInstall from './CapabilityInstall.jsx'

// Order + one-line descriptions for the packs (label + size come from the backend doctor).
const PACK_ORDER = ['transcription', 'vision', 'bg-removal', 'beat-sync', 'tts']
const PACK_DESC = {
  transcription: 'Speech-to-text, captions & video understanding',
  vision: 'Auto-reframe & face tracking',
  'bg-removal': 'Remove backgrounds from footage & images',
  'beat-sync': 'Detect music beats for beat-synced cuts',
  tts: 'On-device text-to-speech voiceover',
}

export default function CapabilitiesModal({ onClose }) {
  const [doctor, setDoctor] = useState(null)
  const [err, setErr] = useState(null)

  const load = () => api.getDoctor().then(setDoctor).catch(e => setErr(String(e.message || e)))
  useEffect(() => { let alive = true; api.getDoctor().then(d => alive && setDoctor(d)).catch(e => alive && setErr(String(e.message || e))); return () => { alive = false } }, [])

  const packs = doctor?.packs || {}
  const meta = doctor?.pack_meta || {}
  const names = PACK_ORDER.filter(n => n in meta).concat(Object.keys(meta).filter(n => !PACK_ORDER.includes(n)))

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal env-modal" onClick={e => e.stopPropagation()}>
        <div className="env-head">
          <div className="env-head-text">
            <h3>Capabilities</h3>
            <div className="env-sub">
              Optional on-device features. They’re downloaded only when you need them, so first
              launch stays small. Everything installs locally — no account required.
            </div>
          </div>
          <button className="am-close" onClick={onClose} title="Close" aria-label="Close"><IconX /></button>
        </div>
        <div className="env-body">
          {err && <div className="modal-err">⚠ {err}</div>}
          {!doctor && !err && <p className="empty">Loading…</p>}

          {doctor && (
            <>
              {/* Base runtime health — read-only status the packs depend on. */}
              <div className="env-group">
                <div className="env-group-head">Base runtime</div>
                <RuntimeRow label="Video engine (Python)" ok={doctor.core_ok} />
                <RuntimeRow label="FFmpeg" ok={doctor.ffmpeg_ok}
                  hint={!doctor.ffmpeg_ok ? 'Missing — video won’t render. Check your network/VPN and reopen the app.' : null} />
                <RuntimeRow label="Motion-graphics engines (Remotion · HyperFrames)" ok={doctor.composition_ok} />
              </div>

              <div className="env-group">
                <div className="env-group-head">Optional capability packs</div>
                {names.map(name => (
                  <div key={name} className="cap-wrap">
                    <CapabilityInstall
                      pack={name}
                      label={meta[name]?.label || name}
                      sizeMb={meta[name]?.size_mb}
                      installed={!!packs[name]}
                      reason={PACK_DESC[name]}
                      onInstalled={load}
                    />
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
        <div className="env-actions">
          <button className="modal-cancel" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}

function RuntimeRow({ label, ok, hint }) {
  return (
    <div className="rt-row">
      <span className={`rt-dot ${ok ? 'ok' : 'bad'}`} />
      <span className="rt-label">{label}</span>
      <span className={`rt-status ${ok ? 'ok' : 'bad'}`}>
        {ok ? <><IconCheck /> ready</> : <><IconAlert size={13} /> not installed</>}
      </span>
      {hint && <span className="rt-hint">{hint}</span>}
    </div>
  )
}
