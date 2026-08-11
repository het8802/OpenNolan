import { useEffect, useState } from 'react'
import * as api from '../api.js'
import { IconX } from '../components/icons.jsx'
import { datetimeLocalValue, defaultDatetimeLocal } from './model.js'

export default function ScheduleModal({ projectId, onClose, onScheduled }) {
  const [scheduledAt, setScheduledAt] = useState(() => defaultDatetimeLocal())
  const [available, setAvailable] = useState([])
  const [selected, setSelected] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let alive = true
    api.getContentCalendar()
      .then(data => {
        if (!alive) return
        const channels = data.channels || []
        setAvailable(channels)
        setSelected(channels.slice(0, 1))
      })
      .catch(err => alive && setError(String(err.message || err)))
    return () => { alive = false }
  }, [])

  function toggle(channel) {
    setSelected(current => current.includes(channel)
      ? current.filter(value => value !== channel)
      : [...current, channel])
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

  return (
    <div className="modal-overlay" onClick={onClose}>
      <form className="modal schedule-modal" onClick={event => event.stopPropagation()} onSubmit={submit}>
        <div className="schedule-modal-head">
          <div>
            <h3>Schedule final video</h3>
            <p>Choose when this completed render should appear on your content plan.</p>
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
            {busy ? 'Scheduling…' : 'Schedule'}
          </button>
        </div>
      </form>
    </div>
  )
}
