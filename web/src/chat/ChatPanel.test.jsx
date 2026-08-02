// Component tests for ChatPanel — the shared agent view rendered by both the pipeline window
// and the editor. Presentational only: it pulls all state + handlers from a `chat` bundle, so
// these assert render contracts (what shows for a given bundle) + handler wiring (submit/new).

import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
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
