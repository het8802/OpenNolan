// Component tests for ChatPanel — the shared agent view rendered by both the pipeline window
// and the editor. Presentational only: it pulls all state + handlers from a `chat` bundle, so
// these assert render contracts (what shows for a given bundle) + handler wiring (submit/new).

import { describe, it, expect, vi } from 'vitest'
import { useState } from 'react'
import { render, fireEvent, createEvent, act } from '@testing-library/react'
import ChatPanel from './ChatPanel.jsx'

// A minimal chat bundle matching useAgentChat's return shape, with spies for the handlers.
function mockChat(overrides = {}) {
  return {
    messages: [],
    input: '',
    setInput: vi.fn(),
    busy: false,
    pendingConfirm: null,
    pendingQuestion: null,
    pendingKeyRequest: null,
    renderingStage: null,
    toolResults: {},
    threads: [],
    activeThread: null,
    model: 'claude-opus-4-8',
    setModel: vi.fn(),
    send: vi.fn(),
    stop: vi.fn(),
    newChat: vi.fn(),
    loadThread: vi.fn(),
    resolveConfirm: vi.fn(),
    answerQuestion: vi.fn(),
    provideKey: vi.fn(),
    skipKeyRequest: vi.fn(),
    ...overrides,
  }
}

describe('ChatPanel render contract', () => {
  it('shows the agent header and the empty prompt when there are no messages', () => {
    const { getByText, getByRole } = render(<ChatPanel chat={mockChat()} disabled={false} />)
    expect(getByRole('heading', { name: 'Agent' })).toBeInTheDocument()
    expect(getByText('Tell the agent what to make.')).toBeInTheDocument()
  })

  it('shows the disabled prompt when no project is selected', () => {
    const { getByText } = render(<ChatPanel chat={mockChat()} disabled={true} />)
    expect(getByText('Select or create a project to start.')).toBeInTheDocument()
  })

  it('renders user + assistant messages in order', () => {
    const chat = mockChat({
      messages: [
        { role: 'user', text: 'make me a reel' },
        { role: 'assistant', items: [{ kind: 'text', text: 'On it.' }] },
      ],
    })
    const { getByText } = render(<ChatPanel chat={chat} disabled={false} />)
    expect(getByText('make me a reel')).toBeInTheDocument()
    expect(getByText('On it.')).toBeInTheDocument()
  })

  it('applies the optional className alongside the base panel classes', () => {
    const { container } = render(<ChatPanel chat={mockChat()} disabled={false} className="st-agent" />)
    const section = container.querySelector('section.chat')
    expect(section).toHaveClass('panel', 'chat', 'st-agent')
  })

  it('submitting the composer calls chat.send', () => {
    const chat = mockChat({ input: 'hello' })
    const { container } = render(<ChatPanel chat={chat} disabled={false} />)
    fireEvent.submit(container.querySelector('form.composer'))
    expect(chat.send).toHaveBeenCalledTimes(1)
  })

  // ⚠ THE INVARIANT. Enter-to-send is the most-used interaction in the app, and OPN-27
  // adds an autocomplete that intercepts Enter while its menu is open. This pins the
  // closed-menu case: with no `@` query active, Enter must still send and Shift+Enter must
  // still newline. If this ever goes red, the menu is swallowing Enter — fix that, not this.
  it('with no mention menu open, Enter sends and Shift+Enter does not', () => {
    const chat = mockChat({ input: 'hello' })
    const { container } = render(<ChatPanel chat={chat} disabled={false} />)
    const ta = container.querySelector('textarea.composer-input')

    fireEvent.keyDown(ta, { key: 'Enter' })
    expect(chat.send).toHaveBeenCalledTimes(1)

    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: true })
    expect(chat.send).toHaveBeenCalledTimes(1)   // newline, not a second send
  })

  it('the + button starts a new chat', () => {
    const chat = mockChat()
    const { getByTitle } = render(<ChatPanel chat={chat} disabled={false} />)
    fireEvent.click(getByTitle('New chat'))
    expect(chat.newChat).toHaveBeenCalledTimes(1)
  })

  it('shows a Stop button while busy and wires it to chat.stop', () => {
    const chat = mockChat({ busy: true })
    const { getByTitle } = render(<ChatPanel chat={chat} disabled={false} />)
    const stopBtn = getByTitle('Stop the agent')
    fireEvent.click(stopBtn)
    expect(chat.stop).toHaveBeenCalledTimes(1)
  })

  it('renders the thread history selector when threads exist', () => {
    const chat = mockChat({ threads: [{ thread_id: 't1', title: 'First chat' }], activeThread: 't1' })
    const { getByText } = render(<ChatPanel chat={chat} disabled={false} />)
    expect(getByText('First chat')).toBeInTheDocument()
  })

  it('renders the model selector with Opus marked recommended and selects the chat model', () => {
    const chat = mockChat({ model: 'claude-sonnet-5' })
    const { getByTitle, getByText } = render(<ChatPanel chat={chat} disabled={false} />)
    const select = getByTitle('Agent model (applies to your next message)')
    expect(select.value).toBe('claude-sonnet-5')
    expect(getByText('Opus 4.8 · Recommended')).toBeInTheDocument()
  })

  it('changing the model selector calls chat.setModel', () => {
    const chat = mockChat()
    const { getByTitle } = render(<ChatPanel chat={chat} disabled={false} />)
    fireEvent.change(getByTitle('Agent model (applies to your next message)'), { target: { value: 'claude-haiku-4-5-20251001' } })
    expect(chat.setModel).toHaveBeenCalledWith('claude-haiku-4-5-20251001')
  })

  it('renders the API-key card and wires Save & continue to provideKey', () => {
    const chat = mockChat({
      pendingKeyRequest: {
        key_request_id: 'p:k1', env_var: 'GOOGLE_API_KEY',
        provider: 'Google (Gemini / Veo)', label: 'Google (Gemini / Veo)',
        reason: 'to generate the video',
      },
    })
    const { getByText, container } = render(<ChatPanel chat={chat} disabled={false} />)
    // header + reason surface the friendly provider name and the env-var scoping note
    expect(getByText(/Google \(Gemini \/ Veo\) key needed/)).toBeInTheDocument()
    expect(getByText(/never sent anywhere but Google/)).toBeInTheDocument()
    const input = container.querySelector('.ak-input')
    expect(input.getAttribute('type')).toBe('password')   // masked by default
    fireEvent.change(input, { target: { value: 'sk-goog-123' } })
    fireEvent.click(getByText('Save & continue'))
    expect(chat.provideKey).toHaveBeenCalledWith('sk-goog-123')
  })

  it('the API-key card Continue-without calls skipKeyRequest', () => {
    const chat = mockChat({
      pendingKeyRequest: { key_request_id: 'p:k1', env_var: 'FAL_KEY', provider: 'fal.ai' },
    })
    const { getByText } = render(<ChatPanel chat={chat} disabled={false} />)
    fireEvent.click(getByText('Continue without'))
    expect(chat.skipKeyRequest).toHaveBeenCalledTimes(1)
  })

  it('shows the per-turn cost only when the user is billed per-token (BYOK api_key)', () => {
    const chat = mockChat({ messages: [{ role: 'result', total_cost_usd: 0.123, num_turns: 3 }] })
    const { container } = render(
      <ChatPanel chat={chat} disabled={false} auth={{ authenticated: true, method: 'api_key' }} />
    )
    const cost = container.querySelector('.cost')
    expect(cost).toBeInTheDocument()
    expect(cost.textContent).toContain('$0.123')
  })

  it('hides the per-turn cost on a Claude subscription (oauth) — no per-token billing', () => {
    const chat = mockChat({ messages: [{ role: 'result', total_cost_usd: 0.123, num_turns: 3 }] })
    const { container, getByText } = render(
      <ChatPanel chat={chat} disabled={false} auth={{ authenticated: true, method: 'oauth' }} />
    )
    expect(container.querySelector('.cost')).not.toBeInTheDocument()
    expect(getByText('Turn complete.')).toBeInTheDocument()   // the rest of the result line still renders
  })

  it('hides the per-turn cost when the auth method is unknown (status not yet loaded)', () => {
    const chat = mockChat({ messages: [{ role: 'result', total_cost_usd: 0.123, num_turns: 3 }] })
    const { container } = render(<ChatPanel chat={chat} disabled={false} />)
    expect(container.querySelector('.cost')).not.toBeInTheDocument()
  })

  it('Save & continue is disabled until a key is typed', () => {
    const chat = mockChat({
      pendingKeyRequest: { key_request_id: 'p:k1', env_var: 'FAL_KEY', provider: 'fal.ai' },
    })
    const { getByText, container } = render(<ChatPanel chat={chat} disabled={false} />)
    const save = getByText('Save & continue')
    expect(save).toBeDisabled()
    fireEvent.change(container.querySelector('.ak-input'), { target: { value: 'sk-fal' } })
    expect(save).not.toBeDisabled()
  })
})

// ── `@` asset mention menu (OPN-27) ────────────────────────────────────────────────────
// ChatPanel is controlled (input/setInput live in the chat bundle), so these drive a small
// stateful harness — otherwise typing would never change what the component renders.

const ASSETS = {
  kinds: {
    images: [{ path: 'assets/images/logo.png', name: 'logo.png' }],
    video: [
      { path: 'assets/video/hook.mp4', name: 'hook.mp4' },
      { path: 'assets/video/b-roll.mp4', name: 'b-roll.mp4' },
    ],
    audio: [], music: [],
  },
  agent_renders: [{ path: 'hf/renders/scene2.mp4', name: 'scene2.mp4' }],
  renders: [{ path: 'renders/final.mp4', name: 'final.mp4' }],
}

vi.mock('../api.js', () => ({
  listAssets: vi.fn(() => Promise.resolve(ASSETS)),
  fileUrl: (id, p) => `/file/${p}`,
  frameUrl: (id, p) => `/frame/${p}`,
}))

function Harness({ chat, ...props }) {
  const [input, setInput] = useState(chat.input || '')
  return <ChatPanel chat={{ ...chat, input, setInput }} disabled={false} {...props} />
}

/** Render the harness and wait for the (mocked) asset fetch to settle. */
async function openComposer(chat = mockChat({ projectId: 'p1' })) {
  const utils = render(<Harness chat={chat} />)
  await act(async () => {})
  const ta = utils.container.querySelector('textarea.composer-input')
  return { ...utils, ta, chat }
}

/** Type `value`, with the caret at its end unless `caretAt` says otherwise. */
const type = (ta, value, caretAt) =>
  fireEvent.change(ta, { target: { value, selectionStart: caretAt ?? value.length } })

describe('ChatPanel @ mention menu', () => {
  it('typing @ opens a listbox of the project assets, grouped and wired for a11y', async () => {
    const { ta, container, getByRole } = await openComposer()
    await act(async () => { type(ta, '@') })

    const box = getByRole('listbox')
    expect(box).toBeInTheDocument()
    expect(ta).toHaveAttribute('aria-expanded', 'true')
    expect(ta).toHaveAttribute('aria-controls', box.id)
    expect(ta).toHaveAttribute('aria-activedescendant', 'mention-opt-0')

    const opts = container.querySelectorAll('[role="option"]')
    expect(opts).toHaveLength(5)                       // every candidate, no cap
    expect(opts[0]).toHaveAttribute('aria-selected', 'true')
    // Group headings label provenance; icons, never emoji.
    expect([...container.querySelectorAll('.mention-group')].map(e => e.textContent))
      .toEqual(['Project assets', 'Agent clips', 'Renders'])
    expect(container.querySelector('.mention-item svg')).toBeInTheDocument()
  })

  it('does not open inside an email address', async () => {
    const { ta, queryByRole } = await openComposer()
    await act(async () => { type(ta, 'mail someone@example.com') })
    expect(queryByRole('listbox')).toBeNull()
  })

  it('Enter with the menu open inserts the token and does NOT send', async () => {
    const { ta, chat, queryByRole } = await openComposer()
    await act(async () => { type(ta, 'use @hoo') })
    expect(queryByRole('listbox')).toBeInTheDocument()

    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })
    expect(chat.send).not.toHaveBeenCalled()
    expect(ta.value).toBe('use @assets/video/hook.mp4 ')
    expect(queryByRole('listbox')).toBeNull()          // closed after insertion
  })

  it('Tab selects too, and the caret lands after the inserted token', async () => {
    const { ta } = await openComposer()
    await act(async () => { type(ta, 'use @hoo for the opener', 8) })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Tab' }) })
    expect(ta.value).toBe('use @assets/video/hook.mp4  for the opener')
    expect(ta.selectionStart).toBe('use @assets/video/hook.mp4 '.length)
  })

  it('after inserting, the next Enter sends', async () => {
    const { ta, chat } = await openComposer()
    await act(async () => { type(ta, '@hoo') })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })
    expect(chat.send).not.toHaveBeenCalled()
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })
    expect(chat.send).toHaveBeenCalledTimes(1)
  })

  it('Shift+Enter never selects a mention, even with the menu open', async () => {
    const { ta, chat, queryByRole } = await openComposer()
    await act(async () => { type(ta, '@hoo') })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter', shiftKey: true }) })
    expect(chat.send).not.toHaveBeenCalled()           // newline, not a send
    expect(ta.value).toBe('@hoo')                      // and not an insertion either
    expect(queryByRole('listbox')).toBeInTheDocument()
  })

  it('arrow keys move the active option and wrap in both directions', async () => {
    const { ta, container } = await openComposer()
    await act(async () => { type(ta, '@') })
    const activeIdx = () =>
      [...container.querySelectorAll('[role="option"]')].findIndex(o => o.getAttribute('aria-selected') === 'true')

    expect(activeIdx()).toBe(0)
    await act(async () => { fireEvent.keyDown(ta, { key: 'ArrowDown' }) })
    expect(activeIdx()).toBe(1)
    await act(async () => { fireEvent.keyDown(ta, { key: 'ArrowUp' }) })
    await act(async () => { fireEvent.keyDown(ta, { key: 'ArrowUp' }) })
    expect(activeIdx()).toBe(4)                        // wrapped past the top
    await act(async () => { fireEvent.keyDown(ta, { key: 'ArrowDown' }) })
    expect(activeIdx()).toBe(0)                        // and back past the bottom
  })

  it('Escape closes the menu, keeps the draft, and the next Enter sends', async () => {
    const { ta, chat, queryByRole } = await openComposer()
    await act(async () => { type(ta, '@hoo') })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Escape' }) })
    expect(queryByRole('listbox')).toBeNull()
    expect(ta.value).toBe('@hoo')
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })
    expect(chat.send).toHaveBeenCalledTimes(1)
  })

  it('a zero-result query renders no listbox and Enter sends', async () => {
    const { ta, chat, queryByRole } = await openComposer()
    await act(async () => { type(ta, '@zzzznope') })
    expect(queryByRole('listbox')).toBeNull()
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })
    expect(chat.send).toHaveBeenCalledTimes(1)
  })

  it('pointer-down selects without blurring the textarea first', async () => {
    const { ta, container } = await openComposer()
    await act(async () => { type(ta, '@') })
    const opts = container.querySelectorAll('[role="option"]')
    // preventDefault on mousedown is what stops the blur; assert the handler asked for it.
    const ev = createEvent.mouseDown(opts[1])
    await act(async () => { fireEvent(opts[1], ev) })
    expect(ev.defaultPrevented).toBe(true)
    expect(ta.value).toBe('@assets/video/hook.mp4 ')
  })

  it('sends only the references whose token is still in the draft', async () => {
    const { ta, chat } = await openComposer()
    await act(async () => { type(ta, '@hoo') })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })   // insert hook.mp4
    await act(async () => { type(ta, `${ta.value}and @fin`) })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })   // insert final.mp4
    expect(ta.value).toContain('@assets/video/hook.mp4')
    expect(ta.value).toContain('@renders/final.mp4')

    // The user deletes the first mention before sending.
    await act(async () => { type(ta, ta.value.replace('@assets/video/hook.mp4 ', '')) })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })

    expect(chat.send).toHaveBeenCalledTimes(1)
    expect(chat.send.mock.calls[0][1]).toEqual([
      { token: '@renders/final.mp4', path: 'renders/final.mp4' },
    ])
  })

  it('the menu never opens while the agent is busy', async () => {
    const { ta, queryByRole } = await openComposer(mockChat({ projectId: 'p1', busy: true }))
    await act(async () => { type(ta, '@') })
    expect(queryByRole('listbox')).toBeNull()
  })
})

// ── Regressions from the OPN-27 code review ────────────────────────────────────────────
describe('ChatPanel mention sidecar survives a failed send', () => {
  // `send` used to have its references cleared BEFORE the request outcome was known, so a
  // rejected turn restored the visible draft (build item 10) but silently emptied the
  // sidecar. The retry then posted a token the agent could not resolve — half a feature.
  it('a rejected send keeps the sidecar, and the retry carries it', async () => {
    const send = vi.fn()
      .mockResolvedValueOnce(false)   // e.g. auth 503 at request start
      .mockResolvedValueOnce(true)    // reconnected, retry
    const { ta } = await openComposer(mockChat({ projectId: 'p1', send }))

    await act(async () => { type(ta, 'use @hoo') })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })   // insert the mention
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })   // send -> rejected

    expect(send).toHaveBeenCalledTimes(1)
    expect(send.mock.calls[0][1]).toEqual([
      { token: '@assets/video/hook.mp4', path: 'assets/video/hook.mp4' },
    ])

    // The real hook restores the draft; emulate that, then retry on the same text.
    await act(async () => { type(ta, 'use @assets/video/hook.mp4 ') })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })

    expect(send).toHaveBeenCalledTimes(2)
    expect(send.mock.calls[1][1], 'the retry must still carry the resolved reference')
      .toEqual([{ token: '@assets/video/hook.mp4', path: 'assets/video/hook.mp4' }])
  })

  it('a delivered send retires the references it sent', async () => {
    const send = vi.fn().mockResolvedValue(true)
    const { ta } = await openComposer(mockChat({ projectId: 'p1', send }))

    await act(async () => { type(ta, '@hoo') })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })   // delivered
    expect(send.mock.calls[0][1]).toHaveLength(1)

    // Same visible text, sent again: the reference is gone because it already went out.
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })
    expect(send.mock.calls[1][1]).toEqual([])
  })

  // The prune used an unbounded `includes`, so extending a token left the OLD file resolved
  // while the prose named a new one. QA replaced a token but never appended to one.
  it('appending to a token drops its reference — the agent must not act on hook.mp4', async () => {
    const send = vi.fn().mockResolvedValue(true)
    const { ta } = await openComposer(mockChat({ projectId: 'p1', send }))

    await act(async () => { type(ta, '@hoo') })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })
    expect(ta.value).toBe('@assets/video/hook.mp4 ')

    // The user edits the path in place: hook.mp4 -> hook.mp4.bak
    await act(async () => { type(ta, '@assets/video/hook.mp4.bak') })
    await act(async () => { fireEvent.keyDown(ta, { key: 'Enter' }) })

    expect(send).toHaveBeenCalledTimes(1)
    expect(send.mock.calls[0][1], 'a stale reference would resolve a file the prose no longer names')
      .toEqual([])
  })
})
