import { describe, it, expect } from 'vitest'
import * as rollup from './rollup.js'

describe('feature ids', () => {
  it('is a CLOSED set — an open-ended id makes every per-feature denominator uncountable', () => {
    expect(rollup.isFeatureId('editor.split')).toBe(true)
    expect(rollup.isFeatureId('editor.whatever')).toBe(false)
    expect(rollup.isFeatureId(undefined)).toBe(false)
    expect(rollup.isFeatureId({ nope: 1 })).toBe(false) // props are wired straight to children
  })

  it('classifies the generic inspector patches the call site cannot name', () => {
    // One mutator carries a dozen different fields, so the feature lives in the patch keys.
    expect(rollup.featureForPatch({ speed: 2 })).toBe('editor.speed')
    expect(rollup.featureForPatch({ transition: { type: 'fade' } })).toBe('editor.transition')
    expect(rollup.featureForPatch({ keyframes: [] })).toBe('editor.keyframes')
    expect(rollup.featureForPatch({ in_seconds: 1 })).toBe('editor.cut_trim')
    expect(rollup.featureForPatch({ 'transform.scale': 1.2 })).toBe('editor.clip_transform')
    // crop lives UNDER transform, so it has to be tested before the generic transform case
    expect(rollup.featureForPatch({ 'transform.crop': {} })).toBe('editor.crop')
    expect(rollup.featureForPatch({ audio_mix: { enabled: true } })).toBe('editor.audio_ops')
    expect(rollup.featureForPatch({ mystery: 1 }, 'editor.cut_trim')).toBe('editor.cut_trim')
    expect(rollup.featureForPatch({}, 'editor.cut_trim')).toBe('editor.cut_trim')
  })
})

describe('session rollup', () => {
  it('counts commits per feature and keeps the order in the digest', () => {
    const s = rollup.newSession(0)
    rollup.recordCommit(s, { actionId: 'a1', featureId: 'editor.split' })
    rollup.recordCommit(s, { actionId: 'a2', featureId: 'editor.split' })
    rollup.recordCommit(s, { actionId: 'a3', featureId: 'editor.delete' })
    const out = rollup.summarize(s, { now: 12_000, nCuts: 4, nOverlays: 1 })
    expect(out.commits).toBe(3)
    expect(out.features['editor.split'].commits).toBe(2)
    expect(out.features_used).toEqual(['editor.delete', 'editor.split'])
    expect(out.action_digest).toEqual(['editor.split', 'editor.split', 'editor.delete'])
    expect(out.duration_s).toBe(12)
    expect(out.n_cuts).toBe(4)
    expect(out.digest_truncated).toBe(false)
  })

  it('counts ONE undo per action_id however many times the user traverses it', () => {
    // THE defect this exists to prevent. One commit, undone twice with a redo in between, is
    // one taken-back action — counting the traversals gives undos=2 against commits=1, i.e.
    // an undo RATE of 200%, which is the first number anyone would notice on the board.
    const s = rollup.newSession(0)
    rollup.recordCommit(s, { actionId: 'a1', featureId: 'editor.crop' })
    rollup.recordUndo(s, 'a1')
    rollup.recordRedo(s, 'a1')
    rollup.recordUndo(s, 'a1')
    expect(s.features['editor.crop'].commits).toBe(1)
    expect(s.features['editor.crop'].undos).toBe(1)
    expect(s.features['editor.crop'].undos).toBeLessThanOrEqual(s.features['editor.crop'].commits)
    expect(s.undos).toBeLessThanOrEqual(s.commits) // the rate can never exceed its population
    expect(s.features['editor.crop'].redos).toBe(1) // the traversal itself is still visible
    // ...and the raw sequence is recoverable from the digest, which is the whole point of it.
    expect(s.digest).toEqual(['editor.crop', 'editor.undo', 'editor.redo', 'editor.undo'])
  })

  it('keeps the undo rate <= 100% across many traversals of the same action', () => {
    const s = rollup.newSession(0)
    rollup.recordCommit(s, { actionId: 'a1', featureId: 'editor.split' })
    for (let i = 0; i < 20; i++) { rollup.recordUndo(s, 'a1'); rollup.recordRedo(s, 'a1') }
    expect(s.undos).toBe(1)
    expect(s.commits).toBe(1)
  })

  it('an undo with no known action (the agent adopt push) counts but blames nothing', () => {
    const s = rollup.newSession(0)
    rollup.recordUndo(s, null)
    expect(s.undos).toBe(1)
    expect(s.features).toEqual({})
  })

  it('caps the digest but REPORTS the overflow, so truncation cannot read as a short session', () => {
    const s = rollup.newSession(0)
    for (let i = 0; i < rollup.DIGEST_MAX + 25; i++) {
      rollup.recordCommit(s, { actionId: `a${i}`, featureId: 'editor.cut_trim' })
    }
    const out = rollup.summarize(s, { now: 0 })
    expect(out.action_digest).toHaveLength(rollup.DIGEST_MAX)
    expect(out.digest_count_total).toBe(rollup.DIGEST_MAX + 25)
    expect(out.digest_truncated).toBe(true)
    expect(out.commits).toBe(rollup.DIGEST_MAX + 25) // the COUNT is never capped
  })

  it('saves and renders are counted, never uploaded per event (autosave fires 20-200x)', () => {
    const s = rollup.newSession(0)
    for (let i = 0; i < 50; i++) rollup.recordSave(s)
    rollup.recordRender(s)
    const out = rollup.summarize(s, { now: 0 })
    expect(out.saves).toBe(50)
    expect(out.renders).toBe(1)
  })
})
