import { describe, expect, it } from 'vitest'
import {
  channelLabel,
  clockParts,
  dateKey,
  datetimeLabel,
  defaultDatetimeLocal,
  entriesByDay,
  entryForProject,
  hour24,
  monthDays,
  nearestEntryMonth,
  withDate,
  withTime,
} from './model.js'

// The vocabulary itself is the backend's (server/content_calendar.CHANNELS); this is the order
// the API returns, which is what the UI renders.
const CHANNELS = ['tiktok', 'instagram', 'youtube']

describe('content calendar model', () => {
  it('builds a six-week Sunday-first month grid', () => {
    const days = monthDays(new Date(2026, 7, 1))

    expect(days).toHaveLength(42)
    expect(days[0].getDay()).toBe(0)
    expect(days.at(-1).getDay()).toBe(6)
    expect(days.some(day => day.getMonth() === 7)).toBe(true)
  })

  it('groups scheduled entries by the viewer local date', () => {
    const entry = { id: 'one', scheduled_at: new Date(2026, 7, 10, 9, 30).toISOString() }

    expect(entriesByDay([entry]).get(dateKey(entry.scheduled_at))).toEqual([entry])
  })

  it('defaults to local noon tomorrow', () => {
    const value = defaultDatetimeLocal(new Date(2026, 7, 10, 16, 45))

    expect(value).toBe('2026-08-11T12:00')
  })

  it('points an empty month at the nearest month that has an entry', () => {
    const entries = [
      { scheduled_at: new Date(2026, 2, 4, 9).toISOString() },
      { scheduled_at: new Date(2027, 0, 9, 9).toISOString() },
    ]

    // Forward first — a plan is about what is next.
    expect(nearestEntryMonth(entries, new Date(2026, 7, 1))).toEqual(new Date(2027, 0, 1))
    // Nothing ahead: fall back to the latest month that does have one.
    expect(nearestEntryMonth(entries, new Date(2028, 4, 1))).toEqual(new Date(2027, 0, 1))
    // This month already has entries, or there are none at all: no cue.
    expect(nearestEntryMonth(entries, new Date(2026, 2, 20))).toBeNull()
    expect(nearestEntryMonth([], new Date(2026, 7, 1))).toBeNull()
  })

  it('renders channel ids with their real brand casing', () => {
    expect([...CHANNELS].map(channelLabel)).toEqual(['TikTok', 'Instagram', 'YouTube'])
    expect(channelLabel('threads')).toBe('threads')   // backend owns the vocabulary
  })

  it('swaps one half of a datetime-local value at a time', () => {
    const value = '2026-08-12T13:00'

    expect(withDate(value, new Date(2026, 11, 3))).toBe('2026-12-03T13:00')  // clock survives
    expect(withTime(value, 9, 30)).toBe('2026-08-12T09:30')                  // day survives
    expect(clockParts(value)).toEqual({ hour12: 1, minute: 0, meridiem: 'PM' })
    expect(clockParts('2026-08-12T00:05')).toEqual({ hour12: 12, minute: 5, meridiem: 'AM' })
    expect([hour24(12, 'AM'), hour24(12, 'PM'), hour24(1, 'PM')]).toEqual([0, 12, 13])
    expect(datetimeLabel('nonsense')).toBe('Pick a date and time')
    expect(datetimeLabel(value)).toContain('2026')
  })

  it('finds a project current slot in the shared calendar aggregate', () => {
    const mine = { id: 'mine', project_id: 'launch', scheduled_at: '2026-08-12T19:00:00Z' }
    const entries = [
      { id: 'other', project_id: 'teaser', scheduled_at: '2026-08-11T19:00:00Z' },
      { id: 'stale', project_id: 'launch', scheduled_at: '2026-08-20T19:00:00Z' },
      mine,
    ]

    expect(entryForProject(entries, 'launch')).toBe(mine)
    expect(entryForProject(entries, 'nobody')).toBeNull()
    expect(entryForProject(undefined, 'launch')).toBeNull()
  })
})
