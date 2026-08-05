// CapabilityInstall — one lazy capability pack, with an Install button that streams the pip
// install (progress bar + latest line + log) into the managed runtime. Reused by BOTH the
// Capabilities settings panel and the agent's on-demand install card, so the streaming logic
// lives in one place. `onInstalled(pack)` fires when the install stream completes successfully.

import { useEffect, useRef, useState } from 'react'
import * as api from './api.js'

function fmtSize(mb) {
  if (mb == null) return ''
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`
}

export default function CapabilityInstall({
  pack, label, sizeMb, installed, reason, onInstalled, autostart = false,
}) {
  // idle → installing → done | error. `installed` (from /api/doctor) seeds the done state.
  const [status, setStatus] = useState(installed ? 'done' : 'idle')
  const [pct, setPct] = useState(installed ? 100 : 0)
  const [latest, setLatest] = useState('')
  const [err, setErr] = useState(null)
  const abortRef = useRef(null)
  const startedRef = useRef(false)

  async function install() {
    if (status === 'installing') return
    setStatus('installing'); setErr(null); setPct(3); setLatest('Starting install…')
    const controller = new AbortController()
    abortRef.current = controller
    let sawDone = false, sawError = false
    try {
      for await (const f of api.provisionStream(pack, controller.signal)) {
        if (f.type === 'log') {
          if (f.line) setLatest(f.line)
          // pip gives no clean %, so creep toward 92% on activity; `done` snaps to 100.
          setPct(p => Math.min(p + 1.1, 92))
        } else if (f.type === 'done') {
          sawDone = true; setPct(100); setStatus('done'); setLatest('Installed.')
          onInstalled?.(pack)   // fired ONCE, only on a real done frame
        } else if (f.type === 'error') {
          sawError = true; setErr(f.error || 'Install failed'); setStatus('error')
        }
      }
      // The backend ALWAYS ends a successful install with a `done` frame and a failure with `error`.
      // Reaching end-of-stream with NEITHER means the stream was cut short (backend crash / socket
      // drop mid-install). Treat that as a FAILURE, not success — otherwise we'd mark the pack
      // Installed and tell the agent to retry a dependency that isn't actually there.
      if (!sawDone && !sawError) {
        setErr('Install did not complete — the connection ended before it finished. Try again.')
        setStatus('error')
      }
    } catch (e) {
      if (controller.signal.aborted) { setStatus('idle'); setPct(0); return }
      setErr(String(e.message || e)); setStatus('error')
    } finally {
      abortRef.current = null
    }
  }

  // On-demand card wants the install to begin as soon as the user opts in (autostart handled by
  // the parent); the settings panel starts on click. Fire once if autostart is set.
  useEffect(() => {
    if (autostart && !startedRef.current && status === 'idle') {
      startedRef.current = true
      install()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autostart])

  // Abort an in-flight stream on unmount (never leak the fetch reader).
  useEffect(() => () => abortRef.current?.abort(), [])

  return (
    <div className={`cap-item cap-${status}`}>
      <div className="cap-row">
        <div className="cap-info">
          <span className="cap-label">{label || pack}</span>
          {sizeMb != null && <span className="cap-size">{fmtSize(sizeMb)}</span>}
          {reason && <span className="cap-reason">{reason}</span>}
        </div>
        <div className="cap-action">
          {status === 'done' && <span className="cap-badge done">Installed</span>}
          {status === 'idle' && (
            <button className="cap-btn" onClick={install}>Install</button>
          )}
          {status === 'error' && (
            <button className="cap-btn" onClick={install}>Retry</button>
          )}
          {status === 'installing' && <span className="cap-badge working">Installing… {Math.round(pct)}%</span>}
        </div>
      </div>
      {status === 'installing' && (
        <>
          <div className="cap-bar"><i style={{ transform: `scaleX(${pct / 100})` }} /></div>
          <div className="cap-latest" title={latest}>{latest}</div>
        </>
      )}
      {status === 'error' && <div className="cap-err">⚠ {err}</div>}
    </div>
  )
}
