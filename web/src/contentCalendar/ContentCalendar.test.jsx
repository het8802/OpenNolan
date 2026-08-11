import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ContentCalendar from './ContentCalendar.jsx'
import ScheduleModal from './ScheduleModal.jsx'
import { datetimeLocalValue } from './model.js'
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
    expect(block).toHaveTextContent('tiktok')
    expect(block).toHaveTextContent('youtube')
    fireEvent.click(block)

    expect(await screen.findByTitle('Close (Esc)')).toBeInTheDocument()
    expect(document.querySelector('video')).toHaveAttribute('src', '/file/launch/renders/final.mp4?v=42')
  })

  it('saves one entry with all selected channels', async () => {
    api.getContentCalendar.mockResolvedValue({ channels: ['tiktok', 'instagram', 'youtube'], entries: [] })
    api.scheduleProject.mockResolvedValue({ entry: { id: 'saved' } })
    const onScheduled = vi.fn()

    render(<ScheduleModal projectId="launch" onClose={() => {}} onScheduled={onScheduled} />)
    await screen.findByLabelText('tiktok')
    fireEvent.click(screen.getByLabelText('instagram'))
    fireEvent.click(screen.getByRole('button', { name: 'Schedule' }))

    await waitFor(() => expect(api.scheduleProject).toHaveBeenCalled())
    expect(api.scheduleProject.mock.calls[0][1].channels).toEqual(['tiktok', 'instagram'])
    expect(onScheduled).toHaveBeenCalledWith({ id: 'saved' })
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

    const when = await screen.findByLabelText('Date and time')
    expect(when).toHaveValue(datetimeLocalValue(agentSlot))
    expect(screen.getByLabelText('instagram')).toBeChecked()
    expect(screen.getByLabelText('youtube')).toBeChecked()
    expect(screen.getByLabelText('tiktok')).not.toBeChecked()

    fireEvent.click(screen.getByRole('button', { name: 'Update schedule' }))

    await waitFor(() => expect(api.scheduleProject).toHaveBeenCalled())
    const [projectId, body] = api.scheduleProject.mock.calls[0]
    expect(projectId).toBe('launch')
    expect(body.channels).toEqual(['instagram', 'youtube'])
    expect(new Date(body.scheduled_at).getTime()).toBe(agentSlot.getTime())
  })
})
