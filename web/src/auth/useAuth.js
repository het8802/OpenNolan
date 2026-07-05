// useAuth — Anthropic account auth status for the whole app, in one hook.
//
// App owns a single instance and passes the status down to the dashboard (sign-in CTA / top-right
// re-auth button) and the chat panel (in-chat reconnect box). The backend (/api/auth/status) is the
// single source of truth — it reports authenticated / method / needs_reauth and, for OAuth, attempts
// a silent token refresh before answering. We poll it (so an expiry noticed elsewhere surfaces) and
// expose refresh() for an immediate re-check after a connect or a chat auth failure.

import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from '../api.js'

const POLL_MS = 20000

export function useAuth() {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const aliveRef = useRef(true)

  const refresh = useCallback(() => {
    return api.getAuthStatus()
      .then(s => { if (aliveRef.current) setStatus(s); return s })
      .catch(() => { /* transient; keep the last known status */ })
      .finally(() => { if (aliveRef.current) setLoading(false) })
  }, [])

  useEffect(() => {
    aliveRef.current = true
    refresh()
    const id = setInterval(refresh, POLL_MS)
    // Re-check when the user returns to the app (e.g. after signing in via the browser).
    const onFocus = () => refresh()
    window.addEventListener('focus', onFocus)
    return () => {
      aliveRef.current = false
      clearInterval(id)
      window.removeEventListener('focus', onFocus)
    }
  }, [refresh])

  return { status, loading, refresh }
}
