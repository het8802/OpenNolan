// Studio preview — two modes, with a real transport (play/pause).
//  • SOURCE: the clip under the playhead plays at its edited speed; on reaching the cut's
//    out-point the playhead jumps to the next cut (which loads the next source). The playhead
//    is DERIVED from playback while playing, and DRIVES the seek while paused/scrubbing — the
//    two never fight. A rough pre-render preview (no overlays/transitions).
//  • RENDER: plays the composed MP4 (preview == export); playback drives the playhead.

import { useEffect, useRef } from 'react'
import * as api from '../api.js'
import * as interp from '../editor/interp.js'
import { fmtTime } from './model.js'

export default function StudioPreview({ projectId, doc, playhead, previewMode, renderPath, renderVersion, playing, onScrub, onPlayingChange }) {
  const srcRef = useRef(null)
  const renRef = useRef(null)

  const hit = interp.cutAtTime(doc, playhead)
  const sourceRef = hit?.cut?.source || null
  const sourceTime = hit?.sourceTime || 0
  const dur = interp.timelineDuration(doc)

  // Latest render values for the rAF clock to read without re-subscribing every frame.
  const liveRef = useRef(null)
  liveRef.current = { doc, hit, previewMode, dur, onScrub, onPlayingChange }

  // When playing reaches a cut's out-point, jump to the next cut (or stop at the end).
  const advanceSource = (v) => {
    const cut = hit?.cut
    if (!cut) { onPlayingChange(false); return }
    const cuts = doc.cuts || []
    const ni = hit.index + 1
    if (ni < cuts.length) {
      const next = cuts[ni]
      onScrub(hit.start + interp.cutDuration(cut) + 1e-3) // → re-render picks the next cut
      if (next.source === cut.source) { // same element keeps playing — reseek to its in-point
        try { v.currentTime = Number(next.in_seconds) || 0 } catch {}
        v.playbackRate = Number(next.speed) || 1
      }
    } else {
      onScrub(dur); onPlayingChange(false)
      try { v.pause() } catch {}
    }
  }

  // SOURCE: seek from the playhead ONLY when paused (while playing, playback owns the time).
  useEffect(() => {
    if (previewMode !== 'source' || playing) return
    const v = srcRef.current
    if (!v || sourceRef == null) return
    const seek = () => { if (Math.abs(v.currentTime - sourceTime) > 0.05) { try { v.currentTime = sourceTime } catch {} } }
    if (v.readyState >= 1) seek()
    else v.addEventListener('loadedmetadata', seek, { once: true })
  }, [previewMode, sourceRef, sourceTime, playing])

  // SOURCE: play/pause + edited-speed playback. Re-runs when the clip changes (sourceRef) so
  // advancing to a new source resumes playback; NOT on every playhead tick.
  useEffect(() => {
    if (previewMode !== 'source') return
    const v = srcRef.current
    if (!v || sourceRef == null) return
    v.playbackRate = Number(hit?.cut?.speed) || 1
    if (playing) {
      const begin = () => {
        if (Math.abs(v.currentTime - sourceTime) > 0.3) { try { v.currentTime = sourceTime } catch {} }
        v.play().catch(() => {})
      }
      if (v.readyState >= 1) begin()
      else v.addEventListener('loadedmetadata', begin, { once: true })
    } else {
      v.pause()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewMode, playing, sourceRef])

  // RENDER: play/pause the composed video from the transport.
  useEffect(() => {
    if (previewMode !== 'render') return
    const v = renRef.current
    if (!v) return
    if (playing) v.play().catch(() => {}); else v.pause()
  }, [previewMode, playing, renderPath])

  // RENDER: seek the composed video when scrubbing the timeline (paused + drifted).
  useEffect(() => {
    if (previewMode !== 'render') return
    const v = renRef.current
    if (!v) return
    if (v.paused && Math.abs(v.currentTime - playhead) > 0.25) { try { v.currentTime = playhead } catch {} }
  }, [previewMode, playhead])

  // SMOOTH PLAYHEAD (feat 6): while playing, drive the playhead from the video clock every
  // animation frame (~60fps) instead of coarse 'timeupdate' events (~4-15fps). In source mode
  // it also advances to the next cut at the out-point. Reads liveRef so it never re-subscribes
  // per frame; the only effect dep is `playing`.
  useEffect(() => {
    if (!playing) return
    let raf = 0
    const tick = () => {
      const st = liveRef.current
      if (st.previewMode === 'source') {
        const v = srcRef.current
        const cut = st.hit?.cut
        if (v && cut) {
          const inS = Number(cut.in_seconds) || 0
          const outS = Number(cut.out_seconds) || 0
          const speed = Number(cut.speed) || 1
          if (v.currentTime >= outS - 0.02) {
            const cuts = st.doc.cuts || []
            const ni = st.hit.index + 1
            if (ni < cuts.length) {
              const next = cuts[ni]
              st.onScrub(st.hit.start + interp.cutDuration(cut) + 1e-3) // → next cut
              if (next.source === cut.source) { // same element keeps playing — reseek to its in-point
                try { v.currentTime = Number(next.in_seconds) || 0 } catch { /* not ready */ }
                v.playbackRate = Number(next.speed) || 1
              }
            } else {
              st.onScrub(st.dur); st.onPlayingChange(false); try { v.pause() } catch { /* noop */ }
            }
          } else {
            st.onScrub(st.hit.start + (v.currentTime - inS) / speed)
          }
        }
      } else {
        const v = renRef.current
        if (v && !v.paused) st.onScrub(v.currentTime)
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing])

  return (
    <div className="st-stage">
      {previewMode === 'source' ? (
        sourceRef != null ? (
          <>
            <video
              ref={srcRef}
              key={sourceRef}
              className="st-video"
              src={api.sourceUrl(projectId, sourceRef)}
              preload="auto"
              playsInline
              onClick={() => onPlayingChange(!playing)}
              onEnded={(e) => { if (playing) advanceSource(e.target) }}
            />
            {!playing && (
              <button className="st-stage-play" onClick={() => onPlayingChange(true)} aria-label="Play">▶</button>
            )}
          </>
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
          onPlay={() => onPlayingChange(true)}
          onPause={() => onPlayingChange(false)}
          onEnded={() => onPlayingChange(false)}
          onTimeUpdate={(e) => { if (!e.target.paused) onScrub(e.target.currentTime) }}
        />
      ) : (
        <div className="st-stage-empty">No render yet — hit Render.</div>
      )}

      <div className="st-stage-tc">
        {previewMode === 'source'
          ? <>src {fmtTime(sourceTime)} · timeline {fmtTime(playhead)} / {fmtTime(dur)}</>
          : <>timeline {fmtTime(playhead)} / {fmtTime(dur)}</>}
      </div>
    </div>
  )
}
