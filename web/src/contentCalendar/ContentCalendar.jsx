import { useEffect, useMemo, useRef, useState } from 'react'
import * as api from '../api.js'
import { track } from '../analytics/track.js'
import AssetModal from '../components/AssetModal.jsx'
import { IconChevron } from '../components/icons.jsx'
import { WEEKDAYS, channelLabel, dateKey, entriesByDay, monthDays, nearestEntryMonth } from './model.js'

function monthLabel(cursor) {
  return cursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
}

export default function ContentCalendar({ onProjects, onError = () => {} }) {
  const [cursor, setCursor] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1))
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(true)
  const [viewer, setViewer] = useState(null)
  const opened = useRef(false)
  const onErrorRef = useRef(onError)
  onErrorRef.current = onError

  useEffect(() => {
    let alive = true
    const tick = () => api.getContentCalendar()
      .then(data => {
        if (!alive) return
        const next = data.entries || []
        setEntries(next)
        setLoading(false)
        if (!opened.current) {
          opened.current = true
          track('content_calendar_viewed', { action: 'calendar', entry_count: next.length })
        }
      })
      .catch(err => {
        if (!alive) return
        setLoading(false)
        onErrorRef.current(err)
      })
    tick()
    const id = setInterval(tick, 4000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  const days = useMemo(() => monthDays(cursor), [cursor])
  const grouped = useMemo(() => entriesByDay(entries), [entries])
  const jump = useMemo(() => nearestEntryMonth(entries, cursor), [entries, cursor])

  function moveMonth(delta) {
    setCursor(current => new Date(current.getFullYear(), current.getMonth() + delta, 1))
  }

  function openEntry(entry) {
    track('content_calendar_viewed', { action: 'video', channel_count: entry.channels.length })
    setViewer({
      items: [{
        kind: 'video',
        name: entry.project_name || entry.project_id,
        path: entry.playback.path,
        url: api.fileUrl(entry.project_id, entry.playback.path, entry.playback.mtime),
      }],
      index: 0,
    })
  }

  return (
    <div className="calendar-page">
      <header className="calendar-topbar">
        <button className="back-btn" onClick={onProjects}>← Projects</button>
        <div className="brand"><span className="dot" /> OpenNolan</div>
        <div className="calendar-topbar-title">Content Calendar</div>
      </header>
      <main className="calendar-shell">
        <div className="calendar-toolbar">
          <div>
            <p className="calendar-eyebrow">Publishing plan</p>
            <h1>{monthLabel(cursor)}</h1>
          </div>
          <div className="calendar-nav" aria-label="Calendar navigation">
            <button className="calendar-step" onClick={() => moveMonth(-1)} aria-label="Previous month">
              <IconChevron style={{ transform: 'rotate(180deg)' }} />
            </button>
            <button onClick={() => setCursor(new Date())}>Today</button>
            <button className="calendar-step" onClick={() => moveMonth(1)} aria-label="Next month">
              <IconChevron />
            </button>
          </div>
        </div>
        {/* Both cues sit ABOVE the grid: six 126px rows push anything below them off a laptop
            screen, and a first-run user would never scroll to find guidance they can't see. */}
        {!loading && !entries.length && (
          <div className="calendar-empty">Schedule a completed video from its project view to start your plan.</div>
        )}
        {jump && (
          <button className="calendar-jump" onClick={() => setCursor(jump)}>
            Nothing in {monthLabel(cursor)} — go to {monthLabel(jump)}
          </button>
        )}
        <div className="calendar-grid" aria-label={monthLabel(cursor)}>
          {WEEKDAYS.map(day => <div key={day} className="calendar-weekday">{day}</div>)}
          {days.map(day => {
            const key = dateKey(day)
            const dayEntries = grouped.get(key) || []
            const outside = day.getMonth() !== cursor.getMonth()
            const today = key === dateKey(new Date())
            return (
              <section key={key} className={`calendar-day${outside ? ' outside' : ''}${today ? ' today' : ''}`}>
                <span className="calendar-day-number">{day.getDate()}</span>
                <div className="calendar-day-entries">
                  {dayEntries.map(entry => (
                    <button key={entry.id} className="calendar-entry" onClick={() => openEntry(entry)}>
                      <span className="calendar-entry-time">
                        {new Date(entry.scheduled_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}
                      </span>
                      <span className="calendar-entry-name">{entry.project_name || entry.project_id}</span>
                      <span className="calendar-entry-channels">
                        {entry.channels.map(channel => <span key={channel}>{channelLabel(channel)}</span>)}
                      </span>
                    </button>
                  ))}
                </div>
              </section>
            )
          })}
        </div>
      </main>
      {viewer && <AssetModal items={viewer.items} index={viewer.index} onClose={() => setViewer(null)} />}
    </div>
  )
}
