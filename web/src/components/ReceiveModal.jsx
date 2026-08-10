// "Receive from phone" — the QR half of getting a clip off a phone and into the project.
//
// Not AirDrop, on purpose: macOS gives an app no way to RECEIVE an AirDrop (Finder owns it and
// the files always land in ~/Downloads). So the Mac shows a code, the phone opens it, and the
// phone posts the file straight into the project. Same gesture, works from Android too.
//
// The window it opens is a real socket on the wifi, so this dialog owns its lifetime: it starts
// on mount and closes on every user-facing exit (Done, Esc, backdrop). See the effect below for
// why unmount is deliberately NOT one of those exits.

import { useCallback, useEffect, useRef, useState } from 'react'
import qrcode from 'qrcode-generator'
import * as api from '../api.js'
import { IconCheck, IconCopy } from './icons.jsx'

const POLL_MS = 2000

// 'M' correction leaves the code readable at the size a phone camera sees across a desk, and a
// LAN URL is short enough that it costs no extra modules. 0 = pick the smallest type that fits.
function qrSvg(url) {
  const qr = qrcode(0, 'M')
  qr.addData(url)
  qr.make()
  return qr.createSvgTag({ scalable: true })
}

const mmss = (secs) => `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, '0')}`

export default function ReceiveModal({ projectId, onClose, onReceived }) {
  const [session, setSession] = useState(null)   // { url, expires_at, received[] }
  const [error, setError] = useState(null)
  const [left, setLeft] = useState(null)
  // Fires the parent's asset refresh once per newly landed file, not once per poll.
  const seen = useRef(0)
  // Held in a ref so a parent that passes an inline callback can't restart the poll interval.
  const notify = useRef(onReceived)
  notify.current = onReceived

  // Deliberately NO stop-on-unmount. React StrictMode mounts, unmounts and re-mounts every
  // effect in dev, and the two requests that produces reach the server in either order — so a
  // stop fired from the phantom unmount lands after the remount's start and kills the window
  // the user is looking at (measured: the dialog opened straight into "the window closed").
  // The window is closed by `close` below, and by the server's TTL if we never get there.
  // ponytail: leaving the panel without pressing Done keeps a token-gated socket up until the
  // TTL. Close it on route change if that 15-minute tail ever matters.
  const started = useRef(null)
  useEffect(() => {
    let alive = true
    started.current = api.startReceive(projectId)
      .then(s => { if (alive) setSession(s); return s })
      .catch(e => { if (alive) setError(e.message || String(e)); return null })
    return () => { alive = false }
  }, [projectId])

  // Copy-to-clipboard for the typed-by-hand path. navigator.clipboard needs a secure context,
  // and this page is always served from 127.0.0.1 / localhost, which browsers count as one —
  // so it is available here even though the LINK it copies is plain http.
  const [copied, setCopied] = useState('')
  const copiedTimer = useRef(null)
  const urlRef = useRef(null)
  const copyLink = useCallback(() => {
    if (!session?.url) return
    const say = (label) => {
      setCopied(label)
      clearTimeout(copiedTimer.current)
      copiedTimer.current = setTimeout(() => setCopied(''), 1600)
    }
    const write = navigator.clipboard ? navigator.clipboard.writeText(session.url) : Promise.reject()
    write.then(() => say('Copied')).catch(() => {
      // Permission denied (or no clipboard API at all): select the text so ⌘C still works,
      // rather than leaving a button that visibly does nothing.
      const node = urlRef.current
      if (node) {
        const range = document.createRange()
        range.selectNodeContents(node)
        const sel = window.getSelection()
        sel.removeAllRanges()
        sel.addRange(range)
      }
      say('Press ⌘C')
    })
  }, [session?.url])
  useEffect(() => () => clearTimeout(copiedTimer.current), [])

  // Every user-facing exit routes through here: Done, Esc, backdrop.
  const close = useCallback(() => {
    started.current?.then(s => s && api.stopReceive(projectId, s.id)).catch(() => {})
    onClose()
  }, [projectId, onClose])

  useEffect(() => {
    if (!session) return
    const tick = () => api.getReceive(projectId).then(s => {
      if (!s.active) { setSession(null); setError('The receive window closed. Open it again to keep going.'); return }
      setSession(s)
      if (s.received.length > seen.current) { seen.current = s.received.length; notify.current?.() }
    }).catch(() => {})
    const id = setInterval(tick, POLL_MS)
    return () => clearInterval(id)
    // `session ? 1 : 0`, not `session`: the poll REPLACES session every tick, and depending on
    // the object itself would tear down and rebuild the interval on every one of them.
  }, [session ? 1 : 0, projectId])

  // Countdown, so "why did my phone stop working" has a visible answer before it happens.
  useEffect(() => {
    if (!session?.expires_at) { setLeft(null); return }
    const tick = () => setLeft(Math.max(0, session.expires_at * 1000 - Date.now()) / 1000)
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [session?.expires_at])

  const received = session?.received || []

  useEffect(() => {
    const onKey = (e) => { e.stopPropagation(); if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [close])

  return (
    <div className="modal-overlay" onClick={close}>
      <div className="modal rcv" onClick={e => e.stopPropagation()}>
        <h3>Receive assets from phone</h3>

        {error && <p className="modal-err">{error}</p>}
        {!session && !error && <p className="modal-hint">Opening…</p>}

        {session && (
          <>
            <div className="rcv-qr" dangerouslySetInnerHTML={{ __html: qrSvg(session.url) }} />
            <p className="rcv-steps">Scan with your phone's camera, then pick photos or videos.</p>

            <div className="rcv-link">
              <div className="rcv-link-label">Secure link to this Mac</div>
              <div className="rcv-link-row">
                <code ref={urlRef} className="rcv-url" title="Same address, if you'd rather type it by hand">
                  {session.url}
                </code>
                <button
                  className={`rcv-copy ${copied === 'Copied' ? 'ok' : ''}`}
                  onClick={copyLink}
                  aria-label="Copy the link"
                  title={copied === 'Copied' ? 'Copied' : copied ? 'Selected — press ⌘C' : 'Copy the link'}
                >
                  {copied === 'Copied' ? <IconCheck size={15} /> : <IconCopy size={15} />}
                </button>
              </div>
            </div>

            {/* This opens a port on the wifi, which a user is right to be wary of. Say plainly
                what it does, rather than hoping nobody wonders — the countdown doubles as
                proof the link is not permanent.
                The last line is the network caveat, deliberately phrased as where the feature
                BELONGS rather than as a defect: "unencrypted" under a warning triangle reads as
                danger to a non-technical user and gets the feature avoided, when the action it
                should produce is simply "use it on your own wifi". The reason is still one
                hover away rather than deleted — we must never claim the opposite. */}
            <ul className="rcv-safe">
              <li><IconCheck size={13} /><span>Goes straight from your phone to this Mac. No cloud, no account.</span></li>
              <li><IconCheck size={13} /><span>
                One private link{left !== null && <>, closes by itself in {mmss(left)}</>}.
              </span></li>
              <li><IconCheck size={13} /><span>Only adds files to this project's assets folder.</span></li>
              <li title="The transfer runs over your local network without encryption, so a shared public network is not a good place for it.">
                <IconCheck size={13} />
                <span>Made for your own network — home or office wifi, not shared public wifi.</span>
              </li>
            </ul>

            {/* Not filler: binding for the phone makes macOS ask once whether to accept
                incoming connections, and Deny looks exactly like a bug. */}
            <p className="modal-hint">
              Your phone must be on the same wifi. macOS may ask once to allow incoming
              connections — say yes.
            </p>
          </>
        )}

        <div className="rcv-list">
          <div className="rcv-list-h">
            {received.length ? `Added to this project (${received.length})` : 'Waiting for your phone…'}
          </div>
          {received.map(f => (
            <div key={f.path} className="rcv-file">
              <span className="rcv-name" title={f.name}>{f.name}</span>
              <span className="rcv-kind">assets/{f.kind}</span>
            </div>
          ))}
        </div>

        <div className="modal-actions">
          <button onClick={close}>Done &amp; close link</button>
        </div>
      </div>
    </div>
  )
}
