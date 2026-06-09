import React, { useEffect, useRef } from 'react'
import * as api from '../api.js'
import { cutAtTime } from './interp.js'

// The preview surface has two modes:
//   • source — live, pre-render: an HTML <video> of the cut under the playhead, seeked to the
//     mapped SOURCE time. Browser-native seeking = smooth scrub with no render and no per-frame
//     server round-trips. This is what lets you scrub footage to find trim / split points.
//   • render — the composed MP4 from "Render preview" (preview == export, FFmpeg path).
// The timeline ruler drives `playhead`; this component keeps whichever <video> is showing in
// sync, and (in render mode) reports playback position back up via onScrub.
export default function Preview({ projectId, doc, playhead, renderPath, mode, onModeChange, onScrub }) {
  const srcRef = useRef(null)
  const renderRef = useRef(null)
  const hit = cutAtTime(doc, playhead)
  const sourceId = hit?.cut?.source || null
  const sourceTime = hit?.sourceTime ?? 0

  // Source mode: keep the source <video> paused and seeked to the mapped source time.
  useEffect(() => {
    if (mode !== 'source') return
    const v = srcRef.current
    if (!v) return
    if (!v.paused) v.pause()
    if (Number.isFinite(sourceTime) && Math.abs(v.currentTime - sourceTime) > 0.04) {
      try { v.currentTime = sourceTime } catch { /* not seekable yet — onLoaded* will retry */ }
    }
  }, [mode, sourceId, sourceTime])

  // Once a freshly-swapped source clip can decode, apply the pending seek.
  function seekSource() {
    const v = srcRef.current
    if (v && Number.isFinite(sourceTime)) { try { v.currentTime = sourceTime } catch { /* ignore */ } }
  }

  // Render mode: an external scrub (timeline) drives currentTime while paused; playback drives
  // the playhead back up. The >0.2s guard + paused check stops the two from fighting each other.
  useEffect(() => {
    if (mode !== 'render') return
    const v = renderRef.current
    if (!v) return
    if (v.paused && Number.isFinite(playhead) && Math.abs(v.currentTime - playhead) > 0.2) {
      try { v.currentTime = playhead } catch { /* ignore */ }
    }
  }, [mode, playhead])

  return (
    <>
      <div className="prev-modes">
        <button className={`prev-mode ${mode === 'source' ? 'on' : ''}`}
          onClick={() => onModeChange('source')}>Source</button>
        <button className={`prev-mode ${mode === 'render' ? 'on' : ''}`} disabled={!renderPath}
          title={renderPath ? '' : 'Render a preview first'}
          onClick={() => onModeChange('render')}>Render</button>
      </div>

      {mode === 'source' ? (
        sourceId ? (
          // key on the source id so a clip change remounts the element (fresh load → seekSource)
          <video key={sourceId} ref={srcRef} src={api.sourceUrl(projectId, sourceId)}
            muted playsInline preload="auto"
            onLoadedMetadata={seekSource} onLoadedData={seekSource} />
        ) : (
          <div className="editor-noprev">No clip under the playhead — drag the ruler over a clip.</div>
        )
      ) : (
        renderPath ? (
          <video ref={renderRef} controls src={api.fileUrl(projectId, renderPath)}
            onTimeUpdate={e => onScrub?.(e.currentTarget.currentTime)} />
        ) : (
          <div className="editor-noprev">No preview yet — click <strong>Render preview</strong> to see your edit.</div>
        )
      )}
    </>
  )
}
