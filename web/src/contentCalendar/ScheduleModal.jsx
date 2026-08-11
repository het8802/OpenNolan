import { useEffect, useState } from 'react'
import * as api from '../api.js'
import { IconX } from '../components/icons.jsx'
import { datetimeLocalValue, defaultDatetimeLocal, entryForProject } from './model.js'

// Opening this dialog READS the project's current slot from `/api/content-calendar` — the same
// aggregate the calendar month view renders — so an entry the agent's `schedule_content` tool
// wrote pre-fills here too. Saving upserts that one entry (server-side, by project id), so a
// correction moves the slot instead of leaving a duplicate behind.
export default function ScheduleModal({ projectId, onClose, onScheduled }) {
  const [scheduledAt, setScheduledAt] = useState(() => defaultDatetimeLocal())
  const [available, setAvailable] = useState([])
  const [selected, setSelected] = useState([])
  const [current, setCurrent] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let alive = true
    api.getContentCalendar()
      .then(data => {
        if (!alive) return
        const channels = data.channels || []
        const entry = entryForProject(data.entries, projectId)
        setAvailable(channels)
        setCurrent(entry)
        setScheduledAt(entry ? datetimeLocalValue(new Date(entry.scheduled_at)) : defaultDatetimeLocal())
        // Filter through the vocabulary so a stale channel in an old entry can't be re-submitted.
        setSelected(entry
          ? channels.filter(channel => (entry.channels || []).includes(channel))
          : channels.slice(0, 1))
      })
      .catch(err => alive && setError(String(err.message || err)))
    return () => { alive = false }
  }, [projectId])

  function toggle(channel) {
    setSelected(chosen => chosen.includes(channel)
      ? chosen.filter(value => value !== channel)
      : [...chosen, channel])
  }

  async function submit(event) {
    event.preventDefault()
    if (!scheduledAt || !selected.length || busy) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.scheduleProject(projectId, {
        scheduled_at: new Date(scheduledAt).toISOString(),
        channels: selected,
      })
      onScheduled(result.entry)
    } catch (err) {
      setError(String(err.message || err))
      setBusy(false)
    }
  }

  const stale = current && new Date(current.scheduled_at) <= new Date()

  return (
    <div className="modal-overlay" onClick={onClose}>
      <form className="modal schedule-modal" onClick={event => event.stopPropagation()} onSubmit={submit}>
        <div className="schedule-modal-head">
          <div>
            <h3>{current ? 'Reschedule final video' : 'Schedule final video'}</h3>
            <p>
              {current
                ? `Already on your plan — set ${current.created_by === 'agent' ? 'by the agent' : 'by you'}. Saving moves this entry.`
                : 'Choose when this completed render should appear on your content plan.'}
            </p>
          </div>
          <button type="button" className="am-close" onClick={onClose} title="Close" aria-label="Close">
            <IconX />
          </button>
        </div>
        <label className="modal-field">
          <span>Date and time</span>
          <input
            type="datetime-local"
            value={scheduledAt}
            min={datetimeLocalValue(new Date())}
            onChange={event => setScheduledAt(event.target.value)}
          />
        </label>
        {stale && <p className="modal-hint warn">That slot has already passed — pick a new date and time.</p>}
        <fieldset className="schedule-channels">
          <legend>Channels</legend>
          {available.map(channel => (
            <label key={channel} className="schedule-channel">
              <input
                type="checkbox"
                checked={selected.includes(channel)}
                onChange={() => toggle(channel)}
              />
              <span>{channel}</span>
            </label>
          ))}
          {!available.length && !error && <span className="modal-hint">Loading channels…</span>}
        </fieldset>
        <p className="modal-hint">This adds a calendar entry only. It does not publish to a social network.</p>
        {error && <div className="modal-err">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="modal-cancel" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={busy || !scheduledAt || !selected.length}>
            {busy ? 'Saving…' : current ? 'Update schedule' : 'Schedule'}
          </button>
        </div>
      </form>
    </div>
  )
}
