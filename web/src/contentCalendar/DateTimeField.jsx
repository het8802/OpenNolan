import { useEffect, useMemo, useRef, useState } from 'react'
import { IconCalendar, IconChevron } from '../components/icons.jsx'
import {
  WEEKDAYS,
  clockParts,
  dateKey,
  datetimeLabel,
  hour24,
  monthDays,
  parseDatetimeLocal,
  withDate,
  withTime,
} from './model.js'

// `<input type="datetime-local">` renders an OS popup — blue accent, system font, "Clear/Today"
// links — that browsers deliberately do not expose to CSS. So the field is a button and the popup
// is ours: the SAME month grid the Content Calendar view draws (monthDays + WEEKDAYS + the
// .calendar-nav chevrons), sized for a dropdown, plus three selects for the clock. The value it
// emits is still the `YYYY-MM-DDTHH:MM` string the save flow already expects.

const MINUTES = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]

function monthLabel(cursor) {
  return cursor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
}

export default function DateTimeField({ value, onChange, id, minDate = new Date() }) {
  const [open, setOpen] = useState(false)
  const [cursor, setCursor] = useState(() => monthOf(value))
  const boxRef = useRef(null)

  // Reopening on a value set elsewhere (an agent slot arriving in the pre-fill) must land on that
  // month, not on whatever the last browse left behind.
  useEffect(() => { if (!open) setCursor(monthOf(value)) }, [value, open])

  useEffect(() => {
    if (!open) return
    const onDown = event => { if (!boxRef.current?.contains(event.target)) setOpen(false) }
    const onKey = event => { if (event.key === 'Escape') { event.stopPropagation(); setOpen(false) } }
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const days = useMemo(() => monthDays(cursor), [cursor])
  const { hour12, minute, meridiem } = clockParts(value)
  const selectedKey = dateKey(parseDatetimeLocal(value) || '')
  const floorKey = dateKey(minDate)
  // The minute an agent picked may not be on the 5-minute grid; offer it rather than lose it.
  const minutes = MINUTES.includes(minute) ? MINUTES : [...MINUTES, minute].sort((a, b) => a - b)

  function moveMonth(delta) {
    setCursor(current => new Date(current.getFullYear(), current.getMonth() + delta, 1))
  }

  return (
    <div className="dtf" ref={boxRef}>
      <button
        type="button" id={id} className="dtf-trigger" aria-expanded={open}
        onClick={() => setOpen(current => !current)}>
        <span>{datetimeLabel(value)}</span>
        <IconCalendar />
      </button>
      {open && (
        <div className="dtf-pop" role="dialog" aria-label="Pick a date and time">
          <div className="dtf-head">
            <strong>{monthLabel(cursor)}</strong>
            <div className="calendar-nav">
              <button type="button" className="calendar-step" onClick={() => moveMonth(-1)} aria-label="Previous month">
                <IconChevron style={{ transform: 'rotate(180deg)' }} />
              </button>
              <button type="button" className="calendar-step" onClick={() => moveMonth(1)} aria-label="Next month">
                <IconChevron />
              </button>
            </div>
          </div>
          <div className="dtf-grid" aria-label={monthLabel(cursor)}>
            {WEEKDAYS.map(day => <span key={day} className="dtf-weekday">{day.charAt(0)}</span>)}
            {days.map(day => {
              const key = dateKey(day)
              const classes = ['dtf-day']
              if (day.getMonth() !== cursor.getMonth()) classes.push('outside')
              if (key === dateKey(new Date())) classes.push('today')
              if (key === selectedKey) classes.push('on')
              return (
                <button
                  key={key} type="button" className={classes.join(' ')}
                  // Past days are unreachable, the same as the native picker's `min` greying them
                  // out — the backend rejects a past slot anyway.
                  disabled={key < floorKey}
                  aria-pressed={key === selectedKey}
                  onClick={() => onChange(withDate(value, day))}>
                  {day.getDate()}
                </button>
              )
            })}
          </div>
          <div className="dtf-time">
            <span className="dtf-time-label">Time</span>
            <select
              aria-label="Hour" value={hour12}
              onChange={e => onChange(withTime(value, hour24(+e.target.value, meridiem), minute))}>
              {Array.from({ length: 12 }, (_, index) => index + 1).map(hour => (
                <option key={hour} value={hour}>{hour}</option>
              ))}
            </select>
            <select
              aria-label="Minute" value={minute}
              onChange={e => onChange(withTime(value, hour24(hour12, meridiem), +e.target.value))}>
              {minutes.map(value_ => (
                <option key={value_} value={value_}>{String(value_).padStart(2, '0')}</option>
              ))}
            </select>
            <select
              aria-label="AM or PM" value={meridiem}
              onChange={e => onChange(withTime(value, hour24(hour12, e.target.value), minute))}>
              <option value="AM">AM</option>
              <option value="PM">PM</option>
            </select>
            <button type="button" className="dtf-done" onClick={() => setOpen(false)}>Done</button>
          </div>
        </div>
      )}
    </div>
  )
}

function monthOf(value) {
  const date = parseDatetimeLocal(value) || new Date()
  return new Date(date.getFullYear(), date.getMonth(), 1)
}
