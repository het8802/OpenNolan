import { useEffect, useState } from 'react'

// Lower-left "update ready" card. Shown only in the packaged Electron shell once electron-updater
// has downloaded a newer build (see desktop/main.js initAutoUpdate). Click "Restart & update" →
// the main process runs quitAndInstall(). In a browser or in dev there is no preload bridge / no
// updater, so window.openNolan.update is absent or its handlers reject → the banner stays hidden.
export default function UpdateBanner() {
  const [version, setVersion] = useState(undefined) // undefined = none/unknown, string = staged (may be '')
  const [installing, setInstalling] = useState(false)

  useEffect(() => {
    const u = window.openNolan?.update
    if (!u) return
    // Re-hydrate after a renderer reload: an update may already be staged.
    u.getState?.().then(s => { if (s) setVersion(s.version || '') }).catch(() => {})
    // Live push when the download finishes while the window is open. onDownloaded returns its
    // own unsubscribe, which doubles as this effect's cleanup.
    return u.onDownloaded?.((info) => setVersion((info && info.version) || ''))
  }, [])

  if (version === undefined) return null

  const install = async () => {
    setInstalling(true)
    try { await window.openNolan.update.install() } catch { setInstalling(false) }
  }

  return (
    <div className="update-toast" role="status">
      <div className="update-toast-body">
        <div className="update-toast-title">Update ready</div>
        <div className="update-toast-sub">
          {version ? `Version ${version} ` : 'A new version '}will install on restart.
        </div>
      </div>
      <button className="update-toast-btn" onClick={install} disabled={installing}>
        {installing ? 'Restarting…' : 'Restart & update'}
      </button>
      <button className="update-toast-x" onClick={() => setVersion(undefined)} aria-label="Dismiss">×</button>
    </div>
  )
}
