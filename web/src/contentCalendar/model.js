// Shared by the month view and the Schedule dialog's day picker, so both grids read the same.
export const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

export function dateKey(value) {
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function monthDays(cursor) {
  const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1)
  const gridStart = new Date(first)
  gridStart.setDate(first.getDate() - first.getDay())
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(gridStart)
    date.setDate(gridStart.getDate() + index)
    return date
  })
}

export function entriesByDay(entries) {
  const grouped = new Map()
  for (const entry of entries || []) {
    const key = dateKey(entry.scheduled_at)
    if (!key) continue
    grouped.set(key, [...(grouped.get(key) || []), entry])
  }
  return grouped
}

// Brand casing, not CSS `text-transform: capitalize` — that renders somebody's product name as
// "Tiktok"/"Youtube". The backend still owns the vocabulary, so an id we don't know shows raw
// rather than disappearing.
const CHANNEL_LABELS = { tiktok: 'TikTok', instagram: 'Instagram', youtube: 'YouTube' }

export function channelLabel(channel) {
  return CHANNEL_LABELS[channel] || channel
}

// Where to send a viewer whose visible month is empty while the plan is not: the nearest month
// that HAS an entry — forward first, since a plan is about what's next — else the latest past one.
// Returns null when this month already has entries or nothing is scheduled at all.
export function nearestEntryMonth(entries, cursor) {
  const monthIndex = date => date.getFullYear() * 12 + date.getMonth()
  const current = monthIndex(cursor)
  const months = [...new Set((entries || [])
    .map(entry => new Date(entry.scheduled_at))
    .filter(date => !Number.isNaN(date.getTime()))
    .map(monthIndex))].sort((a, b) => a - b)
  if (!months.length || months.includes(current)) return null
  const target = months.find(month => month > current) ?? months.at(-1)
  return new Date(Math.floor(target / 12), target % 12, 1)
}

// A project holds ONE slot (the server upserts by project id). Read it out of the same aggregate
// the month view renders so the Schedule dialog and the calendar can never disagree; the earliest
// entry wins if an older file still carries duplicates from before the upsert.
export function entryForProject(entries, projectId) {
  const mine = (entries || [])
    .filter(entry => entry && entry.project_id === projectId && entry.scheduled_at)
    .sort((a, b) => String(a.scheduled_at).localeCompare(String(b.scheduled_at)))
  return mine[0] || null
}

export function defaultDatetimeLocal(now = new Date()) {
  const next = new Date(now)
  next.setDate(next.getDate() + 1)
  next.setHours(12, 0, 0, 0)
  return datetimeLocalValue(next)
}

export function datetimeLocalValue(date) {
  const next = new Date(date)
  const offsetMs = next.getTimezoneOffset() * 60_000
  return new Date(next.getTime() - offsetMs).toISOString().slice(0, 16)
}

// ── `YYYY-MM-DDTHH:MM` arithmetic for the day/time picker ────────────────────────────────────
// The dialog's value stays the datetime-local string the save flow already sends, so the picker
// only ever swaps one half of it. Kept here (pure, tested) rather than in the component.

export function parseDatetimeLocal(value) {
  const parsed = new Date(String(value || ''))
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

/** Same clock time, different day. */
export function withDate(value, date) {
  const current = parseDatetimeLocal(value) || new Date()
  const next = new Date(date)
  next.setHours(current.getHours(), current.getMinutes(), 0, 0)
  return datetimeLocalValue(next)
}

/** Same day, different clock time. `hour` is 0-23. */
export function withTime(value, hour, minute) {
  const next = parseDatetimeLocal(value) || new Date()
  next.setHours(hour, minute, 0, 0)
  return datetimeLocalValue(next)
}

/** 0-23 -> the 12-hour parts a human picks with. */
export function clockParts(value) {
  const date = parseDatetimeLocal(value)
  if (!date) return { hour12: 12, minute: 0, meridiem: 'PM' }
  const hour = date.getHours()
  return {
    hour12: hour % 12 || 12,
    minute: date.getMinutes(),
    meridiem: hour < 12 ? 'AM' : 'PM',
  }
}

export function hour24(hour12, meridiem) {
  return (hour12 % 12) + (meridiem === 'PM' ? 12 : 0)
}

/** What the closed field shows — the app's own formatting, not the browser's locale widget. */
export function datetimeLabel(value) {
  const date = parseDatetimeLocal(value)
  if (!date) return 'Pick a date and time'
  return date.toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit',
  })
}
