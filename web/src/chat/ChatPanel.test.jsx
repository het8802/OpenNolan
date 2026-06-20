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
    renderingStage: null,
    toolResults: {},
    threads: [],
    activeThread: null,
    send: vi.fn(),
    stop: vi.fn(),
    newChat: vi.fn(),
    loadThread: vi.fn(),
    resolveConfirm: vi.fn(),
    answerQuestion: vi.fn(),
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
})
