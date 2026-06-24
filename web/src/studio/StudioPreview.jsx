// Studio preview — two modes, with a real transport (play/pause) and WYSIWYG overlay editing.
//  • SOURCE: a rough pre-render preview. A canvas-aspect "safe frame" (the output 1080×1920 etc.)
//    fits inside the stage; the source clip is letterboxed inside it (object-fit:contain, mirroring
//    the renderer's scale+pad), and EVERY overlay visible at the playhead is composited on top in
//    CANVAS coordinates (feat 4) — text, images, video posters — z-ordered by `track`. Overlays are
//    draggable on the canvas to set their position. The PLAYHEAD is the master clock; a single
//    persistent <video> (or <img> for a still cut) is slaved to it.
//  • RENDER: plays the composed MP4 (preview == export; overlays are already baked in).

import { useEffect, useMemo, useRef, useState } from 'react'
import * as api from '../api.js'
import * as interp from '../editor/interp.js'
import { fmtTime, previewAudioTracks, overlayType, isImageSource, clipBox, clipPositionXY } from './model.js'

const OV_DRAG_THRESHOLD = 3 // px before a press on a canvas overlay becomes a position drag

// Screen position (relative to the canvas frame, anchor string) for a text overlay placed by a
// named anchor — a reasonable preview approximation of the FFmpeg drawtext anchor placement.
const TEXT_ANCHOR_CSS = {
  'top-left': { left: '3%', top: '4%', tx: '0', ty: '0' },
  'top-center': { left: '50%', top: '4%', tx: '-50%', ty: '0' },
  'top-right': { left: '97%', top: '4%', tx: '-100%', ty: '0' },
  'center-left': { left: '3%', top: '50%', tx: '0', ty: '-50%' },
  center: { left: '50%', top: '50%', tx: '-50%', ty: '-50%' },
  'center-right': { left: '97%', top: '50%', tx: '-100%', ty: '-50%' },
  'bottom-left': { left: '3%', top: '96%', tx: '0', ty: '-100%' },
  'bottom-center': { left: '50%', top: '96%', tx: '-50%', ty: '-100%' },
  'bottom-right': { left: '97%', top: '96%', tx: '-100%', ty: '-100%' },
}

// Drive the music/narration/sfx <audio> elements to project time `t`. (Unchanged from before.)
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

function pauseAllAudio(els) {
  for (const el of els.values()) { try { el.pause() } catch { /* noop */ } }
}

// Drive the VIDEO-overlay <video> elements to project time `t` so a video overlay PLAYS live in
// the source preview (WYSIWYG) — no re-render needed. Each element is keyed by overlay index; its
// local time is `t - start` (overlays play at 1×, from their own first frame). When `play` is true
// (transport rolling) we seek-on-drift + play; otherwise we seek-to-frame + pause (scrub). Elements
// only exist while the playhead is inside the overlay window (mounted on demand), so the common
// case is "visible".
function syncOverlayVideos(els, overlays, t, { play }) {
  for (const [idx, el] of els) {
    const o = overlays[idx]
    if (!o) { try { el.pause() } catch { /* noop */ } ; continue }
    const s = Number(o.start_seconds) || 0
    const e = Number(o.end_seconds) || s
    const visible = t >= s - 1e-3 && t <= e + 1e-3
    if (!visible) { if (!el.paused) { try { el.pause() } catch { /* noop */ } } ; continue }
    const target = Math.max(0, t - s)
    if (play) {
      if (el.readyState >= 1 && Math.abs(el.currentTime - target) > 0.34) { try { el.currentTime = target } catch { /* not ready */ } }
      if (el.paused && el.readyState >= 2) el.play().catch(() => {})
    } else {
      if (!el.paused) { try { el.pause() } catch { /* noop */ } }
      if (el.readyState >= 1 && Math.abs(el.currentTime - target) > 0.05) { try { el.currentTime = target } catch { /* not ready */ } }
    }
  }
}

export default function StudioPreview({
  projectId, doc, canvas, playhead, previewMode, renderPath, renderVersion, playing, selection, sourceMetas = {},
  onScrub, onPlayingChange, onSelectOverlay, onOverlayPosition, onOverlayDragBegin,
  onClipPosition, onClipDragBegin,
}) {
  const srcRef = useRef(null)
  const renRef = useRef(null)
  const frameRef = useRef(null)
  const stageRef = useRef(null)
  const ovDrag = useRef(null)
  const clipDrag = useRef(null)

  const hit = interp.cutAtTime(doc, playhead)
  const cut = hit?.cut || null
  const sourceRef = hit?.cut?.source || null
  const sourceTime = hit?.sourceTime || 0
  const dur = interp.timelineDuration(doc)
  const isImg = sourceRef != null && isImageSource(sourceRef)
  const overlays = doc?.overlays || []
  const background = interp.getBackground(doc)

  // Canvas-aspect "safe frame" size, computed by CONTAIN-fitting the canvas into the STAGE (the clip
  // + overlays are now absolutely positioned, so the frame has no in-flow content to size itself
  // from — we measure the stage and set the frame's px size explicitly). scale = frameW/canvasW.
  const [frameSize, setFrameSize] = useState({ w: 0, h: 0 })
  useEffect(() => {
    const el = stageRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const measure = () => {
      const r = el.getBoundingClientRect()
      const ar = canvas.width / canvas.height || 1
      let w = r.width, h = r.width / ar
      if (h > r.height) { h = r.height; w = r.height * ar }
      setFrameSize({ w: Math.round(w), h: Math.round(h) })
    }
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    measure()
    return () => ro.disconnect()
  }, [previewMode, canvas.width, canvas.height])
  const scale = frameSize.w > 0 && canvas.width > 0 ? frameSize.w / canvas.width : 0

  // Audio to play alongside the source clip (unchanged).
  const audioTracks = useMemo(() => previewAudioTracks(doc), [doc])
  const audioEls = useRef(new Map())
  const ovVideoEls = useRef(new Map()) // overlay index → <video> (video overlays that play live)

  const playheadRef = useRef(playhead)
  playheadRef.current = playhead
  const liveRef = useRef(null)
  liveRef.current = { doc, previewMode, dur, onScrub, onPlayingChange, audioTracks }

  // SOURCE: while paused (or scrubbing), keep the <video> paused + seeked to the playhead's
  // source time. (Image cuts have no <video> — nothing to seek.)
  useEffect(() => {
    if (previewMode !== 'source' || playing || isImg) return
    const v = srcRef.current
    if (!v) return
    try { v.pause() } catch { /* noop */ }
    if (sourceRef == null) return
    const seek = () => { if (Math.abs(v.currentTime - sourceTime) > 0.05) { try { v.currentTime = sourceTime } catch { /* not ready */ } } }
    if (v.readyState >= 1) { seek(); return }
    v.addEventListener('loadedmetadata', seek, { once: true })
    return () => v.removeEventListener('loadedmetadata', seek)
  }, [previewMode, sourceRef, sourceTime, playing, isImg])

  // RENDER: play/pause + seek the composed video.
  useEffect(() => {
    if (previewMode !== 'render') return
    const v = renRef.current
    if (!v) return
    if (playing) v.play().catch(() => {}); else v.pause()
  }, [previewMode, playing, renderPath])
  useEffect(() => {
    if (previewMode !== 'render') return
    const v = renRef.current
    if (!v) return
    if (v.paused && Math.abs(v.currentTime - playhead) > 0.25) { try { v.currentTime = playhead } catch { /* noop */ } }
  }, [previewMode, playhead])

  // MASTER CLOCK (feat 6): while playing, advance the PLAYHEAD by wall-clock time and slave the
  // media to it. Reads liveRef/playheadRef so it never re-subscribes per frame (dep = `playing`).
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
      if (!last) { last = ts; raf = requestAnimationFrame(tick); return }
      const dt = Math.min(0.05, (ts - last) / 1000)
      last = ts
      const t = playheadRef.current + dt
      if (t >= st.dur) {
        if (playheadRef.current < st.dur) { playheadRef.current = st.dur; st.onScrub(st.dur) }
        st.onPlayingChange(false)
        const v = srcRef.current; if (v) { try { v.pause() } catch { /* noop */ } }
        pauseAllAudio(audioEls.current)
        return
      }
      playheadRef.current = t
      st.onScrub(t)
      const h = interp.cutAtTime(st.doc, t)
      const v = srcRef.current
      if (v && h && !isImageSource(h.cut.source)) {
        const speed = Math.max(0.0625, Number(h.cut.speed) || 1)
        if (Math.abs(v.playbackRate - speed) > 0.01) v.playbackRate = speed
        if (v.readyState >= 1 && !v.seeking && Math.abs(v.currentTime - h.sourceTime) > 0.34) {
          try { v.currentTime = h.sourceTime } catch { /* not ready */ }
        }
        if (v.paused && v.readyState >= 2) v.play().catch(() => {})
      }
      syncAudioEls(audioEls.current, st.audioTracks, t, true)
      syncOverlayVideos(ovVideoEls.current, st.doc.overlays || [], t, { play: true }) // video overlays play live
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing])

  // SOURCE + paused/scrub: seek each visible video overlay to its frame and keep it paused, so a
  // dropped video overlay shows the correct frame under the playhead without a re-render.
  useEffect(() => {
    if (previewMode !== 'source' || playing) return
    syncOverlayVideos(ovVideoEls.current, doc?.overlays || [], playhead, { play: false })
  }, [previewMode, playing, playhead, doc])

  useEffect(() => {
    if (playing && previewMode === 'source') return
    pauseAllAudio(audioEls.current)
    pauseAllAudio(ovVideoEls.current) // render mode / paused: don't leave overlay videos rolling
  }, [playing, previewMode])
  useEffect(() => () => {
    const v = srcRef.current; if (v) { try { v.pause() } catch { /* noop */ } }
    pauseAllAudio(audioEls.current)
    pauseAllAudio(ovVideoEls.current)
    if (ovDrag.current) ovDrag.current()
    if (clipDrag.current) clipDrag.current()
  }, [])

  // Drag an overlay on the canvas → set position.x/y in CANVAS px (feat 4). Captures the overlay's
  // current top-left relative to the frame so there's no jump (works for anchored text too — it
  // converts to {x,y} on the first move). Coalesced into one undo step via onOverlayDragBegin.
  const beginOverlayDrag = (e, index) => {
    e.preventDefault(); e.stopPropagation()
    onSelectOverlay(index)
    const frame = frameRef.current
    if (!frame || scale <= 0) return
    const fr = frame.getBoundingClientRect()
    // Origin in CANVAS px. For object-positioned overlays read the layout x/y directly (NOT the
    // post-transform bounding box — a scale-keyframed overlay's rect is the SCALED box, which would
    // make the drag jump). Anchored text has no numeric x/y, so derive its origin from the rect.
    const o = overlays[index]
    const pos = o?.position
    const isAnchorText = overlayType(o) === 'text' && (typeof pos === 'string' || pos == null)
    let origX, origY
    if (isAnchorText) {
      const er = e.currentTarget.getBoundingClientRect()
      origX = (er.left - fr.left) / scale
      origY = (er.top - fr.top) / scale
    } else {
      const kfx = interp.interpolateAt(o.keyframes, 'x', playhead)
      const kfy = interp.interpolateAt(o.keyframes, 'y', playhead)
      origX = kfx != null ? kfx : (pos?.x ?? 0)
      origY = kfy != null ? kfy : (pos?.y ?? 0)
    }
    const startX = e.clientX, startY = e.clientY
    let didSnap = false
    const teardown = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', teardown)
      ovDrag.current = null
    }
    const onMove = (ev) => {
      if (!didSnap && Math.abs(ev.clientX - startX) < OV_DRAG_THRESHOLD && Math.abs(ev.clientY - startY) < OV_DRAG_THRESHOLD) return
      if (!didSnap) { onOverlayDragBegin?.(); didSnap = true }
      const nx = Math.round(origX + (ev.clientX - startX) / scale)
      const ny = Math.round(origY + (ev.clientY - startY) / scale)
      onOverlayPosition(index, { x: nx, y: ny })
    }
    const onUp = () => teardown()
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', teardown)
    ovDrag.current = teardown
  }

  // Drag the MAIN clip on the canvas → set transform.position {x,y} in CANVAS px (move on the
  // background). Same model as overlay drag: snapshot once on first move (one undo step), live per
  // frame. A press with NO drag toggles play (preserves click-to-play). Resize is via the inspector
  // Scale field. Origin = the clip box's current top-left (resolves a named anchor → {x,y}).
  const beginClipDrag = (e) => {
    if (!cut || !onClipPosition) { onPlayingChange(!playing); return }
    e.preventDefault()
    const frame = frameRef.current
    if (!frame || scale <= 0) { onPlayingChange(!playing); return }
    const meta = sourceMetas[sourceRef]
    const orig = clipPositionXY(cut, meta, canvas)
    const startX = e.clientX, startY = e.clientY
    let didSnap = false
    const teardown = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', teardown)
      clipDrag.current = null
    }
    const onMove = (ev) => {
      if (!didSnap && Math.abs(ev.clientX - startX) < OV_DRAG_THRESHOLD && Math.abs(ev.clientY - startY) < OV_DRAG_THRESHOLD) return
      if (!didSnap) { onClipDragBegin?.(); didSnap = true }
      const nx = Math.round(orig.x + (ev.clientX - startX) / scale)
      const ny = Math.round(orig.y + (ev.clientY - startY) / scale)
      onClipPosition(cut.id, { x: nx, y: ny })
    }
    const onUp = () => { const moved = didSnap; teardown(); if (!moved) onPlayingChange(!playing) }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', teardown)
    clipDrag.current = teardown
  }

  // Clip box on the canvas (move + resize), in FRAME px. Pre-measure (scale<=0) → fill the frame so
  // the persistent <video> never remounts; once measured, position+size from transform + source dims.
  const clipBoxStyle = (() => {
    if (scale <= 0 || !cut) return { inset: 0 }
    const meta = sourceMetas[sourceRef]
    // scale is a uniform number OR a per-axis {x,y} box — pass it THROUGH (don't coerce to a
    // float, which would collapse a non-uniform box) so the preview box == the rendered box.
    const ts = cut.transform?.scale != null ? cut.transform.scale : 1
    const box = clipBox(meta, canvas, ts)
    const p = clipPositionXY(cut, meta, canvas)
    return { left: p.x * scale, top: p.y * scale, width: box.width * scale, height: box.height * scale }
  })()
  const bgColor = background?.type === 'color' && background.color ? background.color : null
  const bgImage = background?.type === 'image' && background.asset_id ? api.sourceUrl(projectId, background.asset_id) : null
  const clipSelected = selection?.kind === 'cut' && cut && selection.id === cut.id

  // Overlays visible at the playhead, sorted ascending by track (lower track first → DOM order
  // puts higher tracks on top, matching the renderer's z-order).
  const visibleOverlays = overlays
    .map((o, i) => ({ o, i }))
    .filter(({ o }) => {
      const s = Number(o.start_seconds) || 0
      const e = Number(o.end_seconds) || s
      return playhead >= s - 1e-6 && playhead <= e + 1e-6
    })
    .sort((a, b) => (Math.round(Number(a.o.track) || 0)) - (Math.round(Number(b.o.track) || 0)))

  const renderOverlay = ({ o, i }) => {
    const sel = selection?.kind === 'overlay' && selection.index === i
    const kfo = interp.interpolateAt(o.keyframes, 'opacity', playhead)
    const opacity = kfo != null ? kfo : (o.opacity != null ? o.opacity : 1)
    const kfs = interp.interpolateAt(o.keyframes, 'scale', playhead)
    const kfx = interp.interpolateAt(o.keyframes, 'x', playhead)
    const kfy = interp.interpolateAt(o.keyframes, 'y', playhead)
    const type = overlayType(o)
    const pos = o.position
    const isAnchorText = type === 'text' && (typeof pos === 'string' || pos == null)
    const common = {
      className: `st-ov-canvas${sel ? ' sel' : ''}`,
      style: { opacity, zIndex: Math.round(Number(o.track) || 0) + 1 },
      onPointerDown: (e) => beginOverlayDrag(e, i),
    }

    if (isAnchorText) {
      const a = TEXT_ANCHOR_CSS[pos || 'center'] || TEXT_ANCHOR_CSS.center
      common.style = {
        ...common.style, position: 'absolute', left: a.left, top: a.top,
        transform: `translate(${a.tx}, ${a.ty})`,
      }
      return <div key={i} {...common}>{renderTextInner(o, scale)}</div>
    }

    // object position (or asset overlay): top-left in canvas px → frame px.
    const x = (kfx != null ? kfx : (pos?.x ?? 0)) * scale
    const y = (kfy != null ? kfy : (pos?.y ?? 0)) * scale
    common.style = {
      ...common.style, position: 'absolute', left: x, top: y,
      width: pos?.width != null ? pos.width * scale : undefined,
      transform: kfs != null && kfs !== 1 ? `scale(${kfs})` : undefined,
      transformOrigin: 'center',
    }
    if (type === 'text') return <div key={i} {...common}>{renderTextInner(o, scale)}</div>
    if (type === 'video') {
      // The element is registered so the rAF clock / paused-seek effect drive it (WYSIWYG: it plays
      // live, no re-render). Muted unless this overlay mixes its audio (matches the export).
      return (
        <video
          key={i} {...common}
          ref={(el) => { const m = ovVideoEls.current; if (el) m.set(i, el); else m.delete(i) }}
          src={api.sourceUrl(projectId, o.asset_id)}
          muted={!o.audio_mix?.enabled}
          playsInline
          preload="auto"
        />
      )
    }
    return <img key={i} {...common} src={api.sourceUrl(projectId, o.asset_id)} alt="" draggable={false} />
  }

  return (
    <div className="st-stage" ref={stageRef}>
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
        <div className="st-safe-frame" ref={frameRef}
          style={{ width: frameSize.w || undefined, height: frameSize.h || undefined, background: bgColor || undefined }}>
          {/* project background (color via the frame bg above; image as a cover layer behind clips) */}
          {bgImage && <img className="st-bg-media" src={bgImage} alt="" draggable={false} />}
          {sourceRef != null ? (
            // The clip BOX (move + resize) sits on the background; drag it to move, Scale to resize.
            // The persistent <video> stays mounted inside so playback wiring is uninterrupted.
            <div className={`st-clip-box${clipSelected ? ' sel' : ''}`} style={clipBoxStyle} onPointerDown={beginClipDrag}>
              {isImg ? (
                <img className="st-frame-media" src={api.sourceUrl(projectId, sourceRef)} alt="" draggable={false} />
              ) : (
                // ONE persistent <video> for every video cut — src follows the playhead (no `key`).
                <video
                  ref={srcRef}
                  className="st-frame-media"
                  src={api.sourceUrl(projectId, sourceRef)}
                  preload="auto"
                  playsInline
                />
              )}
            </div>
          ) : (
            <div className="st-stage-empty">No clip under the playhead.</div>
          )}
          {/* WYSIWYG overlay layer (canvas coordinates → frame px) */}
          {scale > 0 && <div className="st-ov-layer">{visibleOverlays.map(renderOverlay)}</div>}
          {!playing && sourceRef != null && (
            <button className="st-stage-play" onClick={() => onPlayingChange(true)} aria-label="Play">▶</button>
          )}
        </div>
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
          onSeeked={(e) => { if (e.target.paused) onScrub(e.target.currentTime) }}
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

// Inner content of a text overlay, scaled to the frame. Font/color/box approximate the FFmpeg
// drawtext output (the rendered MP4 is ground truth; this is a faithful-enough preview).
function renderTextInner(o, scale) {
  const fontSize = (Number(o.font_size) || 48) * scale
  const box = o.box || {}
  const pad = (box.padding != null ? box.padding : 10) * scale
  const boxOpacity = box.opacity != null ? box.opacity : 0.5
  return (
    <span
      className="st-ov-text"
      style={{
        fontSize,
        color: o.color || 'white',
        padding: `${pad * 0.4}px ${pad}px`,
        background: boxOpacity > 0 ? `rgba(0,0,0,${boxOpacity})` : 'transparent',
      }}
    >{o.text || 'text'}</span>
  )
}
