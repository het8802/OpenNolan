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
