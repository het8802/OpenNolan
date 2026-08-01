import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import DebugReportModal from './DebugReportModal.jsx'
import * as api from '../api.js'
import dbg from '../debug/recorder.js'

vi.mock('../api.js', () => ({
  sendFeedback: vi.fn(() => Promise.resolve({ stored: true, emailed: true })),
  discardDebugSession: vi.fn(() => Promise.resolve({ ok: true, removed: true })),
}))
vi.mock('../debug/recorder.js', () => ({ default: { flushNow: vi.fn(() => Promise.resolve()) } }))

afterEach(() => { cleanup(); vi.clearAllMocks() })

const SESSION = '2026-07-09-abc'

describe('DebugReportModal', () => {
  it('sends the report with the debug session attached, after draining the recorder', async () => {
    render(<DebugReportModal session={SESSION} onClose={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Describe the bug/i), { target: { value: 'canvas froze on scrub' } })
    fireEvent.click(screen.getByText('Send report'))

    await waitFor(() => expect(api.sendFeedback).toHaveBeenCalled())
    expect(dbg.flushNow).toHaveBeenCalled()                 // drained BEFORE sending
    const arg = api.sendFeedback.mock.calls[0][0]
    expect(arg.debug_session).toBe(SESSION)
    expect(arg.message).toBe('canvas froze on scrub')
    await screen.findByText(/were sent/i)                   // success state
  })

  it('discard is a two-step confirm — first click only prompts, second deletes', async () => {
    render(<DebugReportModal session={SESSION} onClose={() => {}} />)
    fireEvent.click(screen.getByText('Discard'))
    expect(api.discardDebugSession).not.toHaveBeenCalled()  // first click: prompt only, no delete
    screen.getByText(/will be deleted/i)                    // confirm prompt shown

    fireEvent.click(screen.getByText('Discard logs'))
    await waitFor(() => expect(api.discardDebugSession).toHaveBeenCalledWith(SESSION))
    await screen.findByText(/discarded/i)
    expect(api.sendFeedback).not.toHaveBeenCalled()         // discard never sends
  })

  it('an implicit form submit during the discard-confirm step does NOT send (Enter-in-email guard)', () => {
    const { container } = render(<DebugReportModal session={SESSION} onClose={() => {}} />)
    fireEvent.click(screen.getByText('Discard'))            // enter the confirm sub-state
    fireEvent.submit(container.querySelector('form'))       // == Enter in the lone email input
    expect(api.sendFeedback).not.toHaveBeenCalled()         // guarded: send() early-returns
    expect(dbg.flushNow).not.toHaveBeenCalled()
  })
})
