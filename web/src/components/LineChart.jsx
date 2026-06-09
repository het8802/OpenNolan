import React, { useEffect, useRef, useState } from 'react'

// Reusable, dependency-free SVG line chart.
// series: [{ key, label, color, points:[{x,y}], bold, hidden }]
// x is the data domain (seconds here), y is 0..yMax.
// Extracted from App.jsx so the manual editor's keyframe-curve editor can reuse it.
export function LineChart({ series, xMax, yMax = 100, yTicks = [0, 25, 50, 75, 100], height = 240, xLabel = 'time (s)', yLabel = 'score', onSvgRef }) {
  const wrapRef = useRef(null)
  const [width, setWidth] = useState(560)
  const [hoverPx, setHoverPx] = useState(null)

  // Track the container width so the chart fills its parent responsively.
  useEffect(() => {
    const el = wrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect?.width
      if (w) setWidth(Math.max(280, Math.floor(w)))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const pad = { top: 14, right: 14, bottom: 30, left: 32 }
  const innerW = Math.max(1, width - pad.left - pad.right)
  const innerH = Math.max(1, height - pad.top - pad.bottom)
  const sx = x => pad.left + (xMax > 0 ? (x / xMax) * innerW : 0)
  const sy = y => pad.top + innerH - (Math.max(0, Math.min(yMax, y)) / yMax) * innerH

  const visible = series.filter(s => !s.hidden && s.points && s.points.length)
  const nearest = (pts, x) => pts.reduce((a, b) => (Math.abs(b.x - x) < Math.abs(a.x - x) ? b : a))

  const tickN = 6
  const xTicks = []
  for (let i = 0; i <= tickN; i++) xTicks.push(Math.round((xMax / tickN) * i))

  const primary = visible.find(s => s.bold) || visible[0]
  let focus = null
  if (hoverPx != null && primary) {
    const dataX = ((hoverPx - pad.left) / innerW) * xMax
    focus = nearest(primary.points, dataX)
  }

  function onMove(e) {
    const rect = e.currentTarget.getBoundingClientRect()
    setHoverPx(((e.clientX - rect.left) / rect.width) * width)
  }

  return (
    <div className="lc-wrap" ref={wrapRef}>
      <svg className="lc-svg" viewBox={`0 0 ${width} ${height}`} width="100%" height={height}
        ref={onSvgRef}
        onMouseMove={onMove} onMouseLeave={() => setHoverPx(null)}
        role="img" aria-label={`${yLabel} versus ${xLabel}`}>
        {yTicks.map(t => (
          <g key={`y${t}`}>
            <line className="lc-grid" x1={pad.left} y1={sy(t)} x2={width - pad.right} y2={sy(t)} />
            <text className="lc-axis" x={pad.left - 6} y={sy(t)} textAnchor="end" dominantBaseline="middle">{t}</text>
          </g>
        ))}
        {xTicks.map((t, i) => (
          <text key={`x${i}`} className="lc-axis" x={sx(t)} y={height - pad.bottom + 15} textAnchor="middle">{t}</text>
        ))}
        <text className="lc-axis-title" x={pad.left + innerW / 2} y={height - 2} textAnchor="middle">{xLabel}</text>

        {visible.map(s => (
          <polyline key={s.key} className={`lc-line ${s.bold ? 'bold' : ''}`} fill="none" stroke={s.color}
            points={s.points.map(p => `${sx(p.x)},${sy(p.y)}`).join(' ')} />
        ))}

        {focus && (
          <g>
            <line className="lc-focus" x1={sx(focus.x)} y1={pad.top} x2={sx(focus.x)} y2={pad.top + innerH} />
            {visible.map(s => {
              const pt = nearest(s.points, focus.x)
              return <circle key={s.key} className="lc-dot" cx={sx(pt.x)} cy={sy(pt.y)} r={s.bold ? 3.5 : 2.5} fill={s.color} />
            })}
          </g>
        )}
      </svg>
      {focus && (
        <div className="lc-tip">
          <span className="lc-tip-x">{focus.x.toFixed(1)}s</span>
          {visible.map(s => (
            <span key={s.key} className="lc-tip-row">
              <span className="lc-tip-dot" style={{ background: s.color }} />
              {s.label}<b>{Math.round(nearest(s.points, focus.x).y)}</b>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
