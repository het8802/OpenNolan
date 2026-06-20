// Studio preview — two modes, with a real transport (play/pause).
//  • SOURCE: a rough pre-render preview (no overlays/transitions). The PLAYHEAD is the master
//    clock: while playing it advances by wall-clock time, and a SINGLE persistent <video> is
//    SLAVED to it (its src follows the cut under the playhead; its currentTime is corrected only
//    when it drifts, so within a cut it plays smoothly and at boundaries it re-seeks). One element
//    is reused for every cut — we never mount a per-source element, so no detached <video> is left
//    playing audio after a cut change, and pause stops everything.
//  • RENDER: plays the composed MP4 (preview == export); playback drives the playhead.

import { useEffect, useMemo, useRef } from 'react'
import * as api from '../api.js'
import * as interp from '../editor/interp.js'
import { fmtTime, previewAudioTracks } from './model.js'

// Drive the music/narration/sfx <audio> elements to project time `t`. Each track is audible
// for t inside its window (music = whole timeline; narration = [start,end]; sfx = start →
// asset end). `local` is the offset into the asset. Mirrors the FFmpeg mix enough to preview:
// the clip's own audio comes from the <video>, these add the bed + voice + effects on top.
function syncAudioEls(els, tracks, t, active) {
  for (const tr of tracks) {
    const el = els.get(tr.key)
    if (!el) continue
    const local = t - tr.start
    const assetDur = Number.isFinite(el.duration) ? el.duration : Infinity
    const windowEnd = tr.kind === 'narration' ? Math.min(tr.end - tr.start, assetDur) : assetDur
    const audible = active && local >= -0.05 && local < windowEnd
    if (audible) {
      el.volume = Math.max(0, Math.min(1, tr.volume))
      const target = Math.max(0, local)
      if (Math.abs(el.currentTime - target) > 0.3) { try { el.currentTime = target } catch { /* not ready */ } }
      if (el.paused) el.play().catch(() => {})
    } else if (!el.paused) {
      try { el.pause() } catch { /* noop */ }
    }
  }
}

// Pause + rewind every hidden audio track (used on stop / mode switch).
function pauseAllAudio(els) {
  for (const el of els.values()) { try { el.pause() } catch { /* noop */ } }
}

export default function StudioPreview({ projectId, doc, playhead, previewMode, renderPath, renderVersion, playing, onScrub, onPlayingChange }) {
  const srcRef = useRef(null)
  const renRef = useRef(null)

  const hit = interp.cutAtTime(doc, playhead)
  const sourceRef = hit?.cut?.source || null
  const sourceTime = hit?.sourceTime || 0
  const dur = interp.timelineDuration(doc)

  // Audio to play alongside the source clip: music bed + narration + SFX, each a hidden <audio>.
  const audioTracks = useMemo(() => previewAudioTracks(doc), [doc])
  const audioEls = useRef(new Map())

  // Mirror the playhead so the rAF clock (master) can read+advance it without re-subscribing.
  const playheadRef = useRef(playhead)
  playheadRef.current = playhead

  // Latest values for the rAF clock to read without re-subscribing every frame.
  const liveRef = useRef(null)
  liveRef.current = { doc, previewMode, dur, onScrub, onPlayingChange, audioTracks }

  // SOURCE: while paused (or scrubbing), the playhead drives the frame — keep the <video> paused
  // and seeked to the playhead's source time. The loadedmetadata listener is cleaned up so a
  // pause that lands mid-load can never leave a stale handler that resumes playback.
  useEffect(() => {
    if (previewMode !== 'source' || playing) return
    const v = srcRef.current
    if (!v) return
    try { v.pause() } catch { /* noop */ }
    if (sourceRef == null) return
    const seek = () => { if (Math.abs(v.currentTime - sourceTime) > 0.05) { try { v.currentTime = sourceTime } catch { /* not ready */ } } }
    if (v.readyState >= 1) { seek(); return }
    v.addEventListener('loadedmetadata', seek, { once: true })
    return () => v.removeEventListener('loadedmetadata', seek)
  }, [previewMode, sourceRef, sourceTime, playing])

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
    if (v.paused && Math.abs(v.currentTime - playhead) > 0.25) { try { v.currentTime = playhead } catch { /* noop */ } }
  }, [previewMode, playhead])

  // MASTER CLOCK (feat 6): while playing, advance the PLAYHEAD by real wall-clock time and slave
  // the media to it. The playhead — not the <video> — is authoritative, so a cut boundary (even a
  // jump-cut or a source swap) just re-seeks/loads the one video element rather than letting it run
  // away or reset. Reads liveRef/playheadRef so it never re-subscribes per frame (dep = `playing`).
  useEffect(() => {
    if (!playing) return
    let raf = 0
    let last = 0
    const tick = (ts) => {
      const st = liveRef.current
      if (st.previewMode === 'render') {
        const v = renRef.current
        if (v && !v.paused) st.onScrub(v.currentTime)
        raf = requestAnimationFrame(tick)
        return
      }
      if (!last) { last = ts; raf = requestAnimationFrame(tick); return } // first frame: just seed the clock
      const dt = Math.min(0.05, (ts - last) / 1000) // seconds; clamp tab-switch / GC gaps
      last = ts

      const t = playheadRef.current + dt
      if (t >= st.dur) { // reached the end — stop cleanly
        playheadRef.current = st.dur
        st.onScrub(st.dur); st.onPlayingChange(false)
        const v = srcRef.current; if (v) { try { v.pause() } catch { /* noop */ } }
        pauseAllAudio(audioEls.current)
        return // no next frame
      }
      playheadRef.current = t
      st.onScrub(t)

      // Slave the single <video> to the playhead: match speed, correct large drift (boundaries),
      // and keep it rolling. Within a cut, video time and source time advance together so no
      // re-seek happens and playback stays smooth.
      const h = interp.cutAtTime(st.doc, t)
      const v = srcRef.current
      if (v && h) {
        const speed = Number(h.cut.speed) || 1
        if (v.playbackRate !== speed) v.playbackRate = speed
        if (v.readyState >= 1 && !v.seeking && Math.abs(v.currentTime - h.sourceTime) > 0.34) {
          try { v.currentTime = h.sourceTime } catch { /* not ready */ }
        }
        if (v.paused && v.readyState >= 2) v.play().catch(() => {})
      }
      syncAudioEls(audioEls.current, st.audioTracks, t, true) // music / narration / sfx

      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing])

  // Pause every audio track whenever we're not actively playing the source preview — when
  // paused/scrubbing, or in render mode (the composed MP4 carries its own mixed audio).
  useEffect(() => {
    if (playing && previewMode === 'source') return
    pauseAllAudio(audioEls.current)
  }, [playing, previewMode])

  return (
    <div className="st-stage">
      {/* hidden audio tracks (music / narration / sfx) — synced to the playhead by the rAF clock */}
      {audioTracks.map(tr => (
        <audio
          key={tr.key}
          src={api.sourceUrl(projectId, tr.src)}
          preload="auto"
          ref={(el) => { const m = audioEls.current; if (el) m.set(tr.key, el); else m.delete(tr.key) }}
        />
      ))}
      {previewMode === 'source' ? (
        sourceRef != null ? (
          <>
            {/* ONE persistent element for every cut — src follows the playhead. No `key`, so React
                reuses it across cuts instead of mounting a fresh (and detached, still-audible) one. */}
            <video
              ref={srcRef}
              className="st-video"
              src={api.sourceUrl(projectId, sourceRef)}
              preload="auto"
              playsInline
              onClick={() => onPlayingChange(!playing)}
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
