import React from 'react'
import { reportClientError } from './api.js'

// Catch anything React can't render so the app shows a recoverable panel instead of a white
// screen, and the crash reaches PostHog. Class component because only class components can be
// error boundaries.
//
// It lives in its own module, apart from main.jsx, for one reason: main.jsx calls createRoot()
// at import time, so importing it in a test boots the whole app. The crash path is the leg of
// wall #5 that is hardest to observe in the wild and easiest to leave unproven, so it has to
// be reachable from a test. See ErrorBoundary.test.jsx.
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { crashed: false }
  }
  static getDerivedStateFromError() {
    return { crashed: true }
  }
  componentDidCatch(error, info) {
    // fatal: the render tree is gone and the user is looking at the crash screen. wall #5
    // counts distinct sessions with fatal=true, so an unflagged crash is an uncounted one.
    reportClientError('react-boundary', error?.message || String(error), error?.stack, {
      fatal: true, handled: true,
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
