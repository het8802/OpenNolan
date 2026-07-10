// Shown when the user STOPS a debug recording in the editor. Same shell as the app's Feedback
// modal, but the recorded session rides along: on Send, the backend attaches a compact analysis of
// the session to the feedback so the developer gets the trace with the report.
//
// Discard is a two-step, no-accident action: the first click asks to confirm, and only the confirm
// deletes the session logs (they are otherwise kept on disk). Closing the modal (✕ / overlay) keeps
// the logs — neither sent nor discarded — so a stray click never destroys a repro.

import { useState } from 'react'
import * as api from '../api.js'
import dbg from '../debug/recorder.js'
import { IconX, IconCheck } from '../components/icons.jsx'

export default function DebugReportModal({ session, onClose }) {
  const [message, setMessage] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [sent, setSent] = useState(false)
  const [discarded, setDiscarded] = useState(false)
  const [confirmDiscard, setConfirmDiscard] = useState(false)

  async function send(e) {
    e.preventDefault()
    // confirmDiscard guard: while the discard prompt is showing, the form has no submit button, so
    // an Enter keypress in the email field would implicitly submit — which must NOT send.
    if (busy || sent || confirmDiscard) return
    setBusy(true); setErr(null)
    try {
      await dbg.flushNow()  // drain any tail re-buffered by a failed stop-flush before the backend reads the log
      await api.sendFeedback({
        kind: 'bug',
        message: message.trim() || 'Debug session (no description provided).',
        email: email.trim() || null,
        debug_session: session,
      })
      setSent(true)
    } catch (e) {
      setErr(String(e.message || e)); setBusy(false)
    }
  }

  async function discard() {
    if (busy) return
    setBusy(true); setErr(null)
    try {
      await api.discardDebugSession(session)
      setDiscarded(true)
    } catch (e) {
      setErr(String(e.message || e)); setBusy(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal feedback-modal" onClick={e => e.stopPropagation()}>
        <div className="env-head">
          <div className="env-head-text">
            <h3>Send debug report</h3>
            <div className="env-sub">Your debug recording is attached. Add what went wrong and send it straight to the developer.</div>
          </div>
          <button className="am-close" onClick={onClose} title="Close" aria-label="Close"><IconX /></button>
        </div>

        {sent ? (
          <div className="feedback-sent">
            <div className="feedback-sent-msg"><IconCheck /><span>Thanks — your report and the debug recording were sent.</span></div>
            <div className="modal-actions"><button onClick={onClose}>Close</button></div>
          </div>
        ) : discarded ? (
          <div className="feedback-sent">
            <div className="feedback-sent-msg"><IconCheck /><span>Debug session discarded — nothing was sent.</span></div>
            <div className="modal-actions"><button onClick={onClose}>Close</button></div>
          </div>
        ) : (
          <form className="feedback-body" onSubmit={send}>
            {err && <div className="modal-err">⚠ {err}</div>}
            <div className="dbg-attach">
              Only what you did after you started recording — your clicks, scrubs, and any errors — is attached.
              Nothing else from your project is sent.
            </div>
            <label className="modal-field">
              What happened?
              <textarea rows={5} value={message} maxLength={5000} autoFocus
                placeholder="Describe the bug — what you did, what you expected, and what went wrong."
                onChange={e => setMessage(e.target.value)} />
            </label>
            <label className="modal-field">
              Email <span className="modal-hint" style={{ marginTop: 0 }}>(optional — so we can reply)</span>
              <input type="email" value={email} placeholder="you@example.com"
                onChange={e => setEmail(e.target.value)} />
            </label>

            {confirmDiscard ? (
              <div className="dbg-discard">
                <div className="dbg-discard-msg">
                  If you discard, the debug session logs will be deleted and <b>not</b> sent. This can’t be undone.
                </div>
                <div className="modal-actions">
                  <button type="button" className="modal-cancel" onClick={() => setConfirmDiscard(false)} disabled={busy}>Keep logs</button>
                  <button type="button" className="dbg-discard-go" onClick={discard} disabled={busy}>{busy ? 'Discarding…' : 'Discard logs'}</button>
                </div>
              </div>
            ) : (
              <div className="modal-actions">
                <button type="button" className="modal-cancel" onClick={() => setConfirmDiscard(true)} disabled={busy}>Discard</button>
                <button type="submit" disabled={busy}>{busy ? 'Sending…' : 'Send report'}</button>
              </div>
            )}
          </form>
        )}
      </div>
    </div>
  )
}
