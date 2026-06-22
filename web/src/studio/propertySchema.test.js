// Unit tests for the declarative property schema (feat 2) + its path helpers.

import { describe, it, expect } from 'vitest'
import { PROPERTY_SCHEMA, PROPERTY_TITLES, getAtPath, buildPatch } from './propertySchema.js'

const SPECIAL = new Set(['speedPresets', 'crop', 'audioMix', 'keyframes', 'textPosition', 'clipTransform'])
const PLAIN = new Set(['number', 'text', 'textarea', 'color', 'select'])

describe('PROPERTY_SCHEMA shape', () => {
  it('covers all 7 Het-specified types (+ narration) with a title each', () => {
    for (const t of ['video_main', 'image_main', 'video_overlay', 'image_overlay', 'text', 'music', 'sfx', 'narration']) {
      expect(PROPERTY_SCHEMA[t], t).toBeDefined()
      expect(PROPERTY_TITLES[t], t).toBeTruthy()
    }
  })

  it('every field has a known control; plain controls carry a path; selects have options', () => {
    for (const [type, sections] of Object.entries(PROPERTY_SCHEMA)) {
      for (const sec of sections) {
        expect(Array.isArray(sec.fields), `${type} section fields`).toBe(true)
        for (const f of sec.fields) {
          expect(SPECIAL.has(f.control) || PLAIN.has(f.control), `${type}.${f.key} control ${f.control}`).toBe(true)
          if (PLAIN.has(f.control)) expect(typeof f.path, `${type}.${f.key} path`).toBe('string')
          if (f.control === 'select') expect(f.options || f.optionsFrom, `${type}.${f.key} options`).toBeTruthy()
        }
      }
    }
  })

  it('image_overlay has NO audio-mix control (images are silent); video_overlay does', () => {
    const controls = (t) => PROPERTY_SCHEMA[t].flatMap(s => s.fields.map(f => f.control))
    expect(controls('image_overlay')).not.toContain('audioMix')
    expect(controls('video_overlay')).toContain('audioMix')
  })

  it('image_main has a duration field and NO speed (a still has no playback rate)', () => {
    const paths = PROPERTY_SCHEMA.image_main.flatMap(s => s.fields.map(f => f.path))
    expect(paths).toContain('out_seconds')
    expect(paths).not.toContain('speed')
    expect(PROPERTY_SCHEMA.video_main.flatMap(s => s.fields.map(f => f.path))).toContain('speed')
  })
})

describe('getAtPath / buildPatch', () => {
  it('reads nested values', () => {
    const ov = { position: { x: 10, y: 20 }, box: { opacity: 0.5 } }
    expect(getAtPath(ov, 'position.x')).toBe(10)
    expect(getAtPath(ov, 'box.opacity')).toBe(0.5)
    expect(getAtPath(ov, 'missing.deep')).toBeUndefined()
  })
  it('builds a shallow top-level patch that rebuilds nested objects WITHOUT dropping siblings', () => {
    const cut = { transform: { scale: 2, crop: { x: 1, y: 2, width: 100, height: 50 } } }
    const patch = buildPatch(cut, 'transform.crop.x', 9)
    expect(patch).toEqual({ transform: { scale: 2, crop: { x: 9, y: 2, width: 100, height: 50 } } })
    // does not mutate the source
    expect(cut.transform.crop.x).toBe(1)
  })
  it('single-key path → flat patch', () => {
    expect(buildPatch({}, 'speed', 2)).toEqual({ speed: 2 })
  })
})
