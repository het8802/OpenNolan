import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import * as api from './api.js'
import { reportClientError } from './api.js'
import ErrorBoundary from './ErrorBoundary.jsx'
import { initAnalytics } from './analytics/track.js'
import './styles.css'

// Before anything can fetch: installs the X-ON-Session header on every /api call and the
// pagehide flush. Idempotent, and a no-op if there is no session id to send.
initAnalytics(api)

// Global handlers for errors outside React's render tree (event handlers, async, promises).
window.addEventListener('error', (e) => {
  reportClientError('window.onerror', e?.message || 'error', e?.error?.stack, {
    fatal: false, handled: false,   // the app kept running — NOT part of the crash-free numerator
    filename: e?.filename, line: e?.lineno, col: e?.colno,
  })
})
window.addEventListener('unhandledrejection', (e) => {
  const r = e?.reason
  reportClientError('unhandledrejection', r?.message || String(r), r?.stack, { fatal: false, handled: false })
})

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)
