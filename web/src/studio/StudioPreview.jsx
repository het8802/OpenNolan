// Studio preview — two modes, same as the render contract.
//  • SOURCE: a native <video> of the clip under the playhead, seeked to the mapped SOURCE
//    time (interp.cutAtTime). Zero-cost scrubbing with no server round-trips; remounts when
//    the underlying clip changes. This is a rough pre-render scrub (no overlays/transitions).
//  • RENDER: plays the composed MP4 (preview == export). Playback drives the playhead;
//    scrubbing the timeline while paused seeks the video (guarded to avoid feedback loops).

import { useEffect, useRef } from 'react'
import * as api from '../api.js'
import * as interp from '../editor/interp.js'
import { fmtTime } from './model.js'

export default function StudioPreview({ projectId, doc, playhead, previewMode, renderPath, renderVersion, onScrub }) {
  const srcRef = useRef(null)
  const renRef = useRef(null)

  const hit = interp.cutAtTime(doc, playhead)
  const sourceRef = hit?.cut?.source || null
  const sourceTime = hit?.sourceTime || 0

  // SOURCE mode — drive video.currentTime from the playhead.
  useEffect(() => {
    if (previewMode !== 'source') return
    const v = srcRef.current
    if (!v || sourceRef == null) return
    const seek = () => { if (Math.abs(v.currentTime - sourceTime) > 0.05) { try { v.currentTime = sourceTime } catch {} } }
    if (v.readyState >= 1) seek()
    else v.addEventListener('loadedmetadata', seek, { once: true })
  }, [previewMode, sourceRef, sourceTime])

  // RENDER mode — seek the composed video when the timeline is scrubbed (paused + drifted).
  useEffect(() => {
    if (previewMode !== 'render') return
    const v = renRef.current
    if (!v) return
    if (v.paused && Math.abs(v.currentTime - playhead) > 0.25) { try { v.currentTime = playhead } catch {} }
  }, [previewMode, playhead])

  return (
    <div className="st-stage">
      {previewMode === 'source' ? (
        sourceRef != null ? (
          <video
            ref={srcRef}
            key={sourceRef}
            className="st-video"
            src={api.sourceUrl(projectId, sourceRef)}
            preload="metadata"
            playsInline
          />
        ) : (
          <div className="st-stage-empty">No clip under the playhead.</div>
        )
      ) : renderPath ? (
        <video
          ref={renRef}
          className="st-video"
          src={api.fileUrl(projectId, renderPath, renderVersion)}
          controls
          playsInline
          onTimeUpdate={(e) => { if (!e.target.paused) onScrub(e.target.currentTime) }}
        />
      ) : (
        <div className="st-stage-empty">No render yet — hit ▶ Render.</div>
      )}

      <div className="st-stage-tc">
        {previewMode === 'source'
          ? <>src {fmtTime(sourceTime)} · timeline {fmtTime(playhead)}</>
          : <>timeline {fmtTime(playhead)}</>}
      </div>
    </div>
  )
}
