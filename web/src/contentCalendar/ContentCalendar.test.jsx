import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ContentCalendar from './ContentCalendar.jsx'
import ScheduleModal from './ScheduleModal.jsx'
import { datetimeLabel, datetimeLocalValue } from './model.js'
import * as api from '../api.js'

vi.mock('../api.js', () => ({
  getContentCalendar: vi.fn(),
  scheduleProject: vi.fn(),
  fileUrl: vi.fn((projectId, path, mtime) => `/file/${projectId}/${path}?v=${mtime}`),
}))
vi.mock('../analytics/track.js', () => ({ track: vi.fn() }))

describe('Content Calendar', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows multi-channel entries and opens the shared video lightbox', async () => {
    const scheduledAt = new Date()
    scheduledAt.setDate(10)
    scheduledAt.setHours(9, 30, 0, 0)
    api.getContentCalendar.mockResolvedValue({
      channels: ['tiktok', 'instagram', 'youtube'],
      entries: [{
        id: 'entry-1', project_id: 'launch', project_name: 'Launch film',
        scheduled_at: scheduledAt.toISOString(), channels: ['tiktok', 'youtube'],
        status: 'scheduled', playback: { path: 'renders/final.mp4', mtime: 42 },
      }],
    })

    render(<ContentCalendar onProjects={() => {}} onError={() => {}} />)

    const block = await screen.findByRole('button', { name: /Launch film/i })
    expect(block).toHaveTextContent('TikTok')   // brand casing, not CSS-capitalized "Tiktok"
    expect(block).toHaveTextContent('YouTube')
    fireEvent.click(block)

    expect(await screen.findByTitle('Close (Esc)')).toBeInTheDocument()
    expect(document.querySelector('video')).toHaveAttribute('src', '/file/launch/renders/final.mp4?v=42')
  })

  it('offers a jump to the month that actually holds the entry', async () => {
    const far = new Date(new Date().getFullYear() + 1, 0, 9, 9, 15)
    api.getContentCalendar.mockResolvedValue({
      channels: ['tiktok'],
      entries: [{
        id: 'entry-1', project_id: 'launch', project_name: 'Launch film',
        scheduled_at: far.toISOString(), channels: ['tiktok'],
        status: 'scheduled', playback: { path: 'renders/final.mp4', mtime: 42 },
      }],
    })

    render(<ContentCalendar onProjects={() => {}} onError={() => {}} />)

    const jump = await screen.findByRole('button', { name: /go to January/i })
    fireEvent.click(jump)

    expect(await screen.findByRole('button', { name: /Launch film/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /go to/i })).toBeNull()
  })

  it('saves one entry with all selected channels', async () => {
    api.getContentCalendar.mockResolvedValue({ channels: ['tiktok', 'instagram', 'youtube'], entries: [] })
    api.scheduleProject.mockResolvedValue({ entry: { id: 'saved' } })
    const onScheduled = vi.fn()

    render(<ScheduleModal projectId="launch" onClose={() => {}} onScheduled={onScheduled} />)
    await screen.findByLabelText('TikTok')
    fireEvent.click(screen.getByLabelText('Instagram'))
    fireEvent.click(screen.getByRole('button', { name: 'Schedule' }))

    await waitFor(() => expect(api.scheduleProject).toHaveBeenCalled())
    expect(api.scheduleProject.mock.calls[0][1].channels).toEqual(['tiktok', 'instagram'])
    expect(onScheduled).toHaveBeenCalledWith({ id: 'saved' })
  })

  // The native datetime-local popup is OS-drawn and unstylable, so the field opens OUR grid.
  it('picks the day and the clock from its own dropdown, not the native popup', async () => {
    api.getContentCalendar.mockResolvedValue({ channels: ['tiktok'], entries: [] })
    api.scheduleProject.mockResolvedValue({ entry: { id: 'saved' } })

    render(<ScheduleModal projectId="launch" onClose={() => {}} onScheduled={() => {}} />)
    await screen.findByLabelText('TikTok')

    // No native control anywhere in the dialog, and the popup is closed until asked for.
    expect(document.querySelector('input[type="datetime-local"]')).toBeNull()
    expect(screen.queryByRole('dialog', { name: 'Pick a date and time' })).toBeNull()

    fireEvent.click(screen.getByLabelText('Date and time'))
    expect(screen.getByRole('dialog', { name: 'Pick a date and time' })).toBeInTheDocument()

    // Default is tomorrow noon; the day it lands on is the one marked selected.
    const tomorrow = new Date()
    tomorrow.setDate(tomorrow.getDate() + 1)
    const selected = screen.getByRole('button', { name: String(tomorrow.getDate()), pressed: true })
    expect(selected).toHaveClass('dtf-day', 'on')

    // Move a month on, take the 20th, then set the clock — both halves of the value, one at a time.
    fireEvent.click(screen.getByLabelText('Next month'))
    fireEvent.click(screen.getAllByRole('button', { name: '20' })[0])
    fireEvent.change(screen.getByLabelText('Hour'), { target: { value: '9' } })
    fireEvent.change(screen.getByLabelText('Minute'), { target: { value: '30' } })
    fireEvent.change(screen.getByLabelText('AM or PM'), { target: { value: 'AM' } })
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))

    expect(screen.queryByRole('dialog', { name: 'Pick a date and time' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Schedule' }))

    await waitFor(() => expect(api.scheduleProject).toHaveBeenCalled())
    const saved = new Date(api.scheduleProject.mock.calls[0][1].scheduled_at)
    const expected = new Date(tomorrow.getFullYear(), tomorrow.getMonth() + 1, 20, 9, 30)
    expect(saved.getTime()).toBe(expected.getTime())
  })

  // The bug this guards: the dialog used to open blank, so a slot the AGENT set was invisible
  // here and re-saving appended a second entry.
  it('pre-fills the slot already on the calendar, including one the agent set', async () => {
    const agentSlot = new Date()
    agentSlot.setFullYear(agentSlot.getFullYear() + 1)
    agentSlot.setMonth(2, 14)
    agentSlot.setHours(18, 45, 0, 0)
    api.getContentCalendar.mockResolvedValue({
      channels: ['tiktok', 'instagram', 'youtube'],
      entries: [
        { id: 'other', project_id: 'teaser', scheduled_at: agentSlot.toISOString(), channels: ['tiktok'] },
        {
          id: 'agent-entry', project_id: 'launch', scheduled_at: agentSlot.toISOString(),
          channels: ['instagram', 'youtube'], created_by: 'agent', status: 'scheduled',
        },
      ],
    })
    api.scheduleProject.mockResolvedValue({ entry: { id: 'agent-entry', replaced: true } })

    render(<ScheduleModal projectId="launch" onClose={() => {}} onScheduled={() => {}} />)

    // The field is our own button now (the native popup was unstylable), so the pre-filled slot
    // shows as the label we render rather than an input value.
    const when = await screen.findByLabelText('Date and time')
    expect(when).toHaveTextContent(datetimeLabel(datetimeLocalValue(agentSlot)))
    expect(screen.getByLabelText('Instagram')).toBeChecked()
    expect(screen.getByLabelText('YouTube')).toBeChecked()
    expect(screen.getByLabelText('TikTok')).not.toBeChecked()

    fireEvent.click(screen.getByRole('button', { name: 'Update schedule' }))

    await waitFor(() => expect(api.scheduleProject).toHaveBeenCalled())
    const [projectId, body] = api.scheduleProject.mock.calls[0]
    expect(projectId).toBe('launch')
    expect(body.channels).toEqual(['instagram', 'youtube'])
    expect(new Date(body.scheduled_at).getTime()).toBe(agentSlot.getTime())
  })
})
