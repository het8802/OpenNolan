import { useEffect, useState } from 'react'

// Lower-left "update ready" card. Shown only in the packaged Electron shell once electron-updater
// has downloaded a newer build (see desktop/main.js initAutoUpdate). Click "Restart & update" →
// the main process runs quitAndInstall(). In a browser or in dev there is no preload bridge / no
// updater, so window.openNolan.update is absent or its handlers reject → the banner stays hidden.
export default function UpdateBanner() {
  const [version, setVersion] = useState(undefined) // undefined = none/unknown, string = staged (may be '')
  const [installing, setInstalling] = useState(false)
  const [leaving, setLeaving] = useState(false)   // one --dur-exit before unmounting

  useEffect(() => {
    const u = window.openNolan?.update
    if (!u) return
    // Re-hydrate after a renderer reload: an update may already be staged.
    u.getState?.().then(s => { if (s) setVersion(s.version || '') }).catch(() => {})
    // Live push when the download finishes while the window is open. onDownloaded returns its
    // own unsubscribe, which doubles as this effect's cleanup.
    return u.onDownloaded?.((info) => setVersion((info && info.version) || ''))
  }, [])

  // Dismiss plays a 140ms exit before unmounting — enter and exit now share one contract
  // with the main toast (enter 200ms via @starting-style, exit faster at 140ms).
  const dismiss = () => { setLeaving(true); setTimeout(() => setVersion(undefined), 140) }

  if (version === undefined) return null

  const install = async () => {
    setInstalling(true)
    try { await window.openNolan.update.install() } catch { setInstalling(false) }
  }

  return (
    <div className={`update-toast${leaving ? ' leaving' : ''}`} role="status">
      <div className="update-toast-body">
        <div className="update-toast-title">Update ready</div>
        <div className="update-toast-sub">
          {version ? `Version ${version} ` : 'A new version '}will install on restart.
        </div>
      </div>
      <button className="update-toast-btn" onClick={install} disabled={installing}>
        {installing ? 'Restarting…' : 'Restart & update'}
      </button>
      <button className="update-toast-x" onClick={dismiss} aria-label="Dismiss">×</button>
    </div>
  )
}
