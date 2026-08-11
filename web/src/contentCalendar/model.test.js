import { describe, expect, it } from 'vitest'
import { dateKey, defaultDatetimeLocal, entriesByDay, monthDays } from './model.js'

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
})
