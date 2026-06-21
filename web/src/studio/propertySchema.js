// Declarative property schema (feat 2). ONE source of truth for which fields the properties
// panel shows per clip type, so the inspector renders from data instead of bespoke JSX per type.
//
// Het's split = 7 types: video_main / image_main (main-timeline cuts) · video_overlay /
// image_overlay / text (overlays) · music / sfx (audio). (narration keeps a small panel too.)
//
// Each type → an ordered list of SECTIONS; each section → a list of FIELD descriptors:
//   { key, label, control, path, ... }
//   - control: 'number'|'text'|'textarea'|'color'|'select'  (plain inputs), or a SPECIAL block
//     'speedPresets'|'crop'|'audioMix'|'keyframes'|'textPosition' the inspector renders by hand.
//   - path: dotted path into the selected object (e.g. 'speed', 'transform.crop.x', 'position.x',
//     'box.opacity'). `getAtPath`/`buildPatch` read+write it; every write still flows through the
//     interp sanitizer, so the schema can never emit a key/value that 422s a Save.
//   - select fields carry static `options` OR `optionsFrom` (resolved against live assets at render).
// Field VALUES are never stored here — placement/values live in the doc; this is layout vocab.

export const PROPERTY_TITLES = {
  video_main: 'Video clip',
  image_main: 'Image clip',
  video_overlay: 'Video overlay',
  image_overlay: 'Image overlay',
  text: 'Text overlay',
  music: 'Music bed',
  sfx: 'Sound effect',
  narration: 'Narration',
}

const TIMING_OPACITY = {
  title: 'Timing',
  fields: [
    { key: 'start', label: 'Start', control: 'number', path: 'start_seconds', min: 0, suffix: 's' },
    { key: 'end', label: 'End', control: 'number', path: 'end_seconds', min: 0, suffix: 's' },
    { key: 'opacity', label: 'Opacity', control: 'number', path: 'opacity', step: 0.05, min: 0, max: 1, default: 1 },
  ],
}

const TRACK_FIELD = { key: 'track', label: 'Track (z-layer)', control: 'number', path: 'track', step: 1, min: 0, default: 0 }

const ASSET_POSITION = {
  title: 'Position & size',
  fields: [
    { key: 'x', label: 'X', control: 'number', path: 'position.x', step: 1 },
    { key: 'y', label: 'Y', control: 'number', path: 'position.y', step: 1 },
    { key: 'w', label: 'Width', control: 'number', path: 'position.width', step: 1, min: 1 },
    { key: 'h', label: 'Height', control: 'number', path: 'position.height', step: 1, min: 1, hint: 'leave height empty to keep aspect ratio' },
  ],
}

const TRANSITIONS_SECTION = {
  title: 'Transitions',
  fields: [
    { key: 'tin', label: 'In', control: 'select', path: 'transition_in', optionsFrom: 'transitions' },
    { key: 'tout', label: 'Out', control: 'select', path: 'transition_out', optionsFrom: 'transitions' },
    { key: 'tdur', label: 'Duration', control: 'number', path: 'transition_duration', step: 0.1, min: 0.1, max: 2, default: 0.5, suffix: 's' },
  ],
}

const NOTE_SECTION = { title: '', fields: [{ key: 'note', label: 'Note (optional)', control: 'text', path: 'reason' }] }

export const PROPERTY_SCHEMA = {
  // ── main-timeline cuts ──
  video_main: [
    { title: 'Source', fields: [{ key: 'src', label: 'Clip', control: 'select', path: 'source', optionsFrom: 'video', meta: true }] },
    {
      title: 'Trim & speed',
      fields: [
        { key: 'in', label: 'In', control: 'number', path: 'in_seconds', min: 0, suffix: 's' },
        { key: 'out', label: 'Out', control: 'number', path: 'out_seconds', min: 0, suffix: 's' },
        { key: 'speed', label: 'Speed', control: 'number', path: 'speed', step: 0.1, min: 0.1, default: 1, suffix: '×' },
        { key: 'speedp', control: 'speedPresets', path: 'speed' },
      ],
    },
    { title: 'Crop', fields: [{ key: 'crop', control: 'crop', path: 'transform.crop' }] },
    TRANSITIONS_SECTION,
    NOTE_SECTION,
  ],
  image_main: [
    { title: 'Source', fields: [{ key: 'src', label: 'Image', control: 'select', path: 'source', optionsFrom: 'images', meta: true }] },
    {
      title: 'Duration',
      fields: [
        { key: 'dur', label: 'Hold for', control: 'number', path: 'out_seconds', min: 0.1, suffix: 's', hint: 'a still holds for this long (starts at 0)' },
      ],
    },
    { title: 'Crop', fields: [{ key: 'crop', control: 'crop', path: 'transform.crop' }] },
    TRANSITIONS_SECTION,
    NOTE_SECTION,
  ],

  // ── overlays ──
  video_overlay: [
    { title: 'Asset', fields: [{ key: 'asset', label: 'Video', control: 'select', path: 'asset_id', optionsFrom: 'imagesAndVideo' }, TRACK_FIELD] },
    TIMING_OPACITY,
    ASSET_POSITION,
    { title: 'Source audio', fields: [{ key: 'amix', control: 'audioMix', path: 'audio_mix' }] },
    { title: 'Motion', fields: [{ key: 'kf', control: 'keyframes', path: 'keyframes' }] },
  ],
  image_overlay: [
    { title: 'Asset', fields: [{ key: 'asset', label: 'Image', control: 'select', path: 'asset_id', optionsFrom: 'imagesAndVideo' }, TRACK_FIELD] },
    TIMING_OPACITY,
    ASSET_POSITION,
    { title: 'Motion', fields: [{ key: 'kf', control: 'keyframes', path: 'keyframes' }] },
  ],
  text: [
    TIMING_OPACITY,
    {
      title: 'Text',
      fields: [
        { key: 'content', label: 'Content', control: 'textarea', path: 'text', required: true },
        { key: 'fs', label: 'Font size', control: 'number', path: 'font_size', step: 1, min: 1, default: 48, suffix: 'px' },
        { key: 'color', label: 'Color', control: 'color', path: 'color', default: 'white' },
        { key: 'pos', control: 'textPosition', path: 'position' },
      ],
    },
    {
      title: 'Background box',
      fields: [
        { key: 'bo', label: 'Box opacity', control: 'number', path: 'box.opacity', step: 0.05, min: 0, max: 1, default: 0.5 },
        { key: 'bp', label: 'Box padding', control: 'number', path: 'box.padding', step: 1, min: 0, default: 10, suffix: 'px' },
      ],
    },
    { title: 'Layer', fields: [TRACK_FIELD] },
    { title: 'Motion', fields: [{ key: 'kf', control: 'keyframes', path: 'keyframes' }] },
  ],

  // ── audio ──
  music: [
    { title: 'Asset', fields: [{ key: 'asset', label: 'File', control: 'select', path: 'asset_id', optionsFrom: 'music' }] },
    {
      title: 'Levels',
      fields: [
        { key: 'vol', label: 'Volume', control: 'number', path: 'volume', step: 0.05, min: 0, max: 1, default: 1 },
        { key: 'fi', label: 'Fade in', control: 'number', path: 'fade_in_seconds', step: 0.1, min: 0, default: 0, suffix: 's' },
        { key: 'fo', label: 'Fade out', control: 'number', path: 'fade_out_seconds', step: 0.1, min: 0, default: 0, suffix: 's' },
      ],
      hint: 'plays under the whole timeline (trimmed to the video length on render)',
    },
  ],
  sfx: [
    { title: 'Asset', fields: [{ key: 'asset', label: 'File', control: 'select', path: 'asset_id', optionsFrom: 'audio' }] },
    {
      title: 'Timing & level',
      fields: [
        { key: 'start', label: 'Start', control: 'number', path: 'start_seconds', min: 0, suffix: 's' },
        { key: 'vol', label: 'Volume', control: 'number', path: 'volume', step: 0.05, min: 0, max: 1, default: 1 },
      ],
      hint: 'plays once from the start point',
    },
  ],
  narration: [
    { title: 'Asset', fields: [{ key: 'asset', label: 'File', control: 'select', path: 'asset_id', optionsFrom: 'audio' }] },
    {
      title: 'Timing',
      fields: [
        { key: 'start', label: 'Start', control: 'number', path: 'start_seconds', min: 0, suffix: 's' },
        { key: 'end', label: 'End', control: 'number', path: 'end_seconds', min: 0, suffix: 's' },
      ],
    },
  ],
}

/** Read a dotted path out of an object (returns undefined if any hop is missing). */
export function getAtPath(obj, path) {
  return String(path).split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj)
}

/**
 * Build a SHALLOW patch (top-level key only) that sets `value` at a dotted `path`, rebuilding the
 * nested objects from `obj` so a merge-then-sanitize mutator (updateCut/updateOverlay/…) applies it
 * without dropping sibling keys. e.g. buildPatch(cut, 'transform.crop.x', 4) →
 *   { transform: { ...cut.transform, crop: { ...cut.transform.crop, x: 4 } } }
 */
export function buildPatch(obj, path, value) {
  const keys = String(path).split('.')
  if (keys.length === 1) return { [keys[0]]: value }
  const [top, ...rest] = keys
  const cur = obj?.[top]
  const base = cur && typeof cur === 'object' ? { ...cur } : {}
  let node = base
  for (let i = 0; i < rest.length - 1; i++) {
    const k = rest[i]
    node[k] = node[k] && typeof node[k] === 'object' ? { ...node[k] } : {}
    node = node[k]
  }
  node[rest[rest.length - 1]] = value
  return { [top]: base }
}
