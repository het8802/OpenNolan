// ConnectClaudeModal — "Sign in with Claude" (OAuth) with an API-key fallback.
//
// OAuth uses the manual paste-code variant of the Claude Code flow (the same one `claude
// setup-token` runs): we ask the backend for the authorize URL, open it in the system browser
// (Electron routes external links out), the user approves and copies back the `code#state` string,
// and the backend exchanges it for a token. The API-key path verifies the key with a live call
// before saving. On success the credential is persisted to the local .env and this modal closes.

import { useState } from 'react'
import * as api from '../api.js'
import { ClaudeLogo, IconX, IconKey } from '../components/icons.jsx'

const KEYS_URL = 'https://console.anthropic.com/settings/keys'

export default function ConnectClaudeModal({ onClose, onConnected, initialStatus }) {
  const [mode, setMode] = useState('claude')      // 'claude' | 'key'
  const [authorizeUrl, setAuthorizeUrl] = useState('')  // set once the OAuth flow has begun
  const [code, setCode] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const reauth = !!initialStatus?.authenticated
  const openExternal = (url) => window.open(url, '_blank', 'noopener,noreferrer')

  async function beginOAuth() {
    setBusy(true); setErr(null)
    try {
      const { authorize_url } = await api.startOAuth()
      setAuthorizeUrl(authorize_url)
      openExternal(authorize_url)
    } catch (e) { setErr(String(e.message || e)) }
    finally { setBusy(false) }
  }

  async function finishOAuth() {
    if (!code.trim()) return
    setBusy(true); setErr(null)
    try {
      const status = await api.finishOAuth(code.trim())
      onConnected?.(status)
    } catch (e) { setErr(String(e.message || e)) }
    finally { setBusy(false) }
  }

  async function connectKey() {
    if (!apiKey.trim()) return
    setBusy(true); setErr(null)
    try {
      const status = await api.connectApiKey(apiKey.trim())
      onConnected?.(status)
    } catch (e) { setErr(String(e.message || e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal auth-modal" onClick={e => e.stopPropagation()}>
        <div className="auth-head">
          <div className="auth-head-text">
            <h3>{reauth ? 'Re-authenticate with Claude' : 'Connect your Anthropic account'}</h3>
            <div className="auth-sub">
              OpenNolan's AI agent uses your own Anthropic account to build and edit your videos.
              Your credentials stay on this Mac.
            </div>
          </div>
          <button className="am-close" onClick={onClose} title="Close" aria-label="Close"><IconX /></button>
        </div>

        <div className="auth-body">
          {mode === 'claude' ? (
            <>
              {!authorizeUrl ? (
                <button className="claude-btn claude-btn-lg" onClick={beginOAuth} disabled={busy}>
                  <ClaudeLogo size={20} /> {busy ? 'Opening…' : 'Sign in with Claude'}
                </button>
              ) : (
                <div className="auth-step">
                  <ol className="auth-steps">
                    <li>Approve access in the browser tab that just opened
                      {' '}(<button type="button" className="linkish" onClick={() => openExternal(authorizeUrl)}>reopen it</button>).</li>
                    <li>Copy the code Claude shows you and paste it below.</li>
                  </ol>
                  <input
                    className="auth-input"
                    type="text"
                    placeholder="Paste the code from Claude"
                    value={code}
                    autoFocus autoComplete="off" spellCheck={false}
                    onChange={e => setCode(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') finishOAuth() }}
                  />
                  <button className="claude-btn" onClick={finishOAuth} disabled={busy || !code.trim()}>
                    <ClaudeLogo size={16} /> {busy ? 'Connecting…' : 'Connect'}
                  </button>
                </div>
              )}
              <button type="button" className="linkish auth-alt" onClick={() => { setErr(null); setMode('key') }}>
                Prefer to use an API key instead?
              </button>
            </>
          ) : (
            <div className="auth-step">
              <label className="auth-label">
                Anthropic API key
                <a className="linkish" href={KEYS_URL} target="_blank" rel="noopener noreferrer">Get a key ↗</a>
              </label>
              <input
                className="auth-input"
                type="password"
                placeholder="sk-ant-…"
                value={apiKey}
                autoFocus autoComplete="off" spellCheck={false}
                onChange={e => setApiKey(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') connectKey() }}
              />
              <button className="btn-primary" onClick={connectKey} disabled={busy || !apiKey.trim()}>
                <IconKey size={15} /> {busy ? 'Verifying…' : 'Verify & connect'}
              </button>
              <button type="button" className="linkish auth-alt" onClick={() => { setErr(null); setMode('claude') }}>
                ← Back to Sign in with Claude
              </button>
            </div>
          )}

          {err && <div className="auth-err">⚠ {err}</div>}
        </div>
      </div>
    </div>
  )
}
