import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import { reportClientError } from './api.js'
import './styles.css'

// Catch anything React can't render so the app shows a recoverable panel instead of a white screen,
// and the crash reaches PostHog. Class component because only class components can be error boundaries.
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { crashed: false }
  }
  static getDerivedStateFromError() {
    return { crashed: true }
  }
  componentDidCatch(error, info) {
    reportClientError('react-boundary', error?.message || String(error), error?.stack, {
      componentStack: (info?.componentStack || '').slice(0, 2000),
    })
  }
  render() {
    if (!this.state.crashed) return this.props.children
    return (
      <div className="crash-screen">
        <div className="crash-card">
          <h2>Something went wrong</h2>
          <p>OpenNolan hit an unexpected error. The problem has been reported. Reloading usually fixes it.</p>
          <button onClick={() => window.location.reload()}>Reload OpenNolan</button>
        </div>
      </div>
    )
  }
}

// Global handlers for errors outside React's render tree (event handlers, async, promises).
window.addEventListener('error', (e) => {
  reportClientError('window.onerror', e?.message || 'error', e?.error?.stack, {
    filename: e?.filename, line: e?.lineno, col: e?.colno,
  })
})
window.addEventListener('unhandledrejection', (e) => {
  const r = e?.reason
  reportClientError('unhandledrejection', r?.message || String(r), r?.stack)
})

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)
