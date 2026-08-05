// useAgentChat — the send path's failure handling.
//
// `send` clears the composer BEFORE the request goes out (optimistic), so until OPN-27 any
// failed send silently destroyed what the user typed. That already bit the auth-503-at-request
// -start case; OPN-27 adds a second reachable 4xx (a rejected @-mention). The draft has to come
// back, and only when the composer is still empty.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import * as api from '../api.js'
import { useAgentChat } from './useAgentChat.js'

vi.mock('../api.js', () => ({
  chatStream: vi.fn(),
  createThread: vi.fn(() => Promise.resolve({ thread_id: 't1' })),
  saveThread: vi.fn(() => Promise.resolve({})),
  listThreads: vi.fn(() => Promise.resolve({ threads: [] })),
  getThread: vi.fn(() => Promise.resolve({ messages: [], session_id: null })),
  stopAgent: vi.fn(() => Promise.resolve({})),
  confirmTool: vi.fn(), answerQuestion: vi.fn(), provideKey: vi.fn(), provideCapability: vi.fn(),
}))

/** An async generator that rejects the way a 4xx from /chat does. */
const failingStream = (message) => vi.fn(async function* () { throw new Error(message) })

beforeEach(() => vi.clearAllMocks())

describe('useAgentChat send failures', () => {
  it('returns the draft to the composer when the send is rejected', async () => {
    api.chatStream.mockImplementation(failingStream('mention rejected (422)'))
    const { result } = renderHook(() => useAgentChat('p1'))

    act(() => result.current.setInput('use @renders/final.mp4 please'))
    await act(async () => { await result.current.send() })

    expect(result.current.input).toBe('use @renders/final.mp4 please')
    // The optimistic bubble and the error line both stay: "you said X, it failed".
    expect(result.current.messages.map(m => m.role)).toEqual(['user', 'error'])
  })

  it('does NOT clobber a newer draft typed while the turn was in flight', async () => {
    api.chatStream.mockImplementation(failingStream('boom'))
    const { result } = renderHook(() => useAgentChat('p1'))

    act(() => result.current.setInput('first message'))
    const turn = act(async () => { await result.current.send() })
    act(() => result.current.setInput('something else I started typing'))
    await turn

    expect(result.current.input).toBe('something else I started typing')
  })

  it('leaves the composer empty when the user aborted the turn themselves', async () => {
    api.chatStream.mockImplementation(vi.fn(async function* () {
      const e = new Error('aborted'); e.name = 'AbortError'; throw e
    }))
    const { result } = renderHook(() => useAgentChat('p1'))

    act(() => result.current.setInput('stop this'))
    await act(async () => { await result.current.send() })

    expect(result.current.input).toBe('')            // a deliberate stop is not a failure
    expect(result.current.messages.map(m => m.role)).toEqual(['user', 'note'])
  })

  it('forwards the mention sidecar to the transport', async () => {
    api.chatStream.mockImplementation(vi.fn(async function* () { /* no events */ }))
    const { result } = renderHook(() => useAgentChat('p1'))
    const mentions = [{ token: '@assets/video/hook.mp4', path: 'assets/video/hook.mp4' }]

    await act(async () => { await result.current.send('use it', mentions) })

    expect(api.chatStream).toHaveBeenCalledTimes(1)
    expect(api.chatStream.mock.calls[0][5]).toEqual(mentions)
  })

  it('exposes projectId so ChatPanel can load its own mention candidates', () => {
    const { result } = renderHook(() => useAgentChat('p1'))
    expect(result.current.projectId).toBe('p1')
  })

  // ChatPanel keeps the @-mention sidecar alive on a rejected send, so it needs an honest
  // delivered/not-delivered answer — not just a restored draft.
  describe('send reports whether the turn was delivered', () => {
    it('returns false when the request is rejected', async () => {
      api.chatStream.mockImplementation(failingStream('mention rejected (422)'))
      const { result } = renderHook(() => useAgentChat('p1'))
      let out
      await act(async () => { out = await result.current.send('hello') })
      expect(out).toBe(false)
    })

    it('returns true when the stream completes', async () => {
      api.chatStream.mockImplementation(vi.fn(async function* () { /* no events */ }))
      const { result } = renderHook(() => useAgentChat('p1'))
      let out
      await act(async () => { out = await result.current.send('hello') })
      expect(out).toBe(true)
    })

    it('returns true when the USER stops the turn — it was delivered, then interrupted', async () => {
      api.chatStream.mockImplementation(vi.fn(async function* () {
        const e = new Error('aborted'); e.name = 'AbortError'; throw e
      }))
      const { result } = renderHook(() => useAgentChat('p1'))
      let out
      await act(async () => { out = await result.current.send('hello') })
      expect(out).toBe(true)
    })

    it('returns false without sending when there is nothing to send', async () => {
      const { result } = renderHook(() => useAgentChat('p1'))
      let out
      await act(async () => { out = await result.current.send('   ') })
      expect(out).toBe(false)
      expect(api.chatStream).not.toHaveBeenCalled()
    })
  })
})
