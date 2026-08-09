// FAULT INJECTION for wall #5's fatal leg.
//
// This is the one crash signal that is hardest to observe in the wild and easiest to leave
// unproven: `$exception where fatal = true` was a query nothing could ever match, because the
// flag existed in code and had never once been emitted. Adding the flag was necessary and not
// sufficient — the property has to actually travel.
//
// So this throws a GENUINE React render error (not a simulated report) and asserts the whole
// payload the boundary sends, including fatal: true.

import { describe, it, expect, vi, beforeEach, afterAll } from 'vitest'
import { render, screen } from '@testing-library/react'
import ErrorBoundary from './ErrorBoundary.jsx'
import { reportClientError } from './api.js'

vi.mock('./api.js', () => ({ reportClientError: vi.fn() }))

function Exploding() {
  throw new Error('injected fatal render error')
}

// React logs a caught boundary error to console.error; silence it so a passing run is quiet.
const realError = console.error
beforeEach(() => { vi.clearAllMocks(); console.error = () => {} })
afterAll(() => { console.error = realError })

describe('ErrorBoundary — the fatal crash signal', () => {
  it('reports a real render crash with fatal: true', () => {
    render(<ErrorBoundary><Exploding /></ErrorBoundary>)

    // The user sees the recoverable panel, not a white screen.
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()

    expect(reportClientError).toHaveBeenCalledTimes(1)
    const [source, message, stack, context] = reportClientError.mock.calls[0]
    expect(source).toBe('react-boundary')
    expect(message).toBe('injected fatal render error')
    expect(stack).toContain('Error')
    // THE assertion. Without this flag wall #5's numerator silently omits every React crash.
    expect(context.fatal).toBe(true)
    expect(context.handled).toBe(true)
    expect(context.componentStack).toContain('Exploding')
  })

  it('reports nothing at all when the tree renders fine', () => {
    render(<ErrorBoundary><p>all good</p></ErrorBoundary>)
    expect(screen.getByText('all good')).toBeInTheDocument()
    expect(reportClientError).not.toHaveBeenCalled()
  })
})
