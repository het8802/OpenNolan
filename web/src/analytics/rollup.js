// Pure reducers for the editor session summary. No React, no DOM, no fetch — unit-tested in
// rollup.test.js.
//
// Why a reducer and not per-interaction events: a 20-minute editing session is 50-300
// commits. Uploading each one blows the per-session event ceiling on its own and buys nothing
// counters do not already give you — EXCEPT the ORDER they happened in. So the order is kept
// as `action_digest`, one capped array of enum members on the single summary event. Properties
// are free; events are not.

/**
 * The closed feature vocabulary. A commit whose feature is not in here is a bug, not a new
 * feature: an open-ended id would make every per-feature denominator uncountable.
 * ponytail: hand-written. The plan derives it at build time from PROPERTY_TITLES + the drag
 * modes; that is P1 work and buys nothing until the eligibility table exists to go with it.
 */
export const FEATURE_IDS = Object.freeze([
  'editor.cut_trim',
  'editor.cut_reorder',
  'editor.split',
  'editor.duplicate',
  'editor.delete',
  'editor.transition',
  'editor.speed',
  'editor.clip_transform',
  'editor.crop',
  'editor.keyframes',
  'editor.overlay_timeline',
  'editor.audio_ops',
  'editor.add_clip',
  'editor.add_overlay',
  'editor.canvas',
  'editor.background',
  'editor.undo',
  'editor.redo',
])

const FEATURE_SET = new Set(FEATURE_IDS)

export function isFeatureId(id) {
  return FEATURE_SET.has(id)
}

/**
 * Which feature a generic `updateCut` / `updateOverlay` patch belongs to.
 *
 * The inspector funnels every numeric field through one mutator, so the call site alone
 * cannot say whether the user changed speed, crop or a keyframe — but the patch's top-level
 * keys can, exactly and cheaply. Order matters: crop lives under `transform`, so it is tested
 * before the generic transform case.
 */
export function featureForPatch(patch, fallback) {
  const keys = Object.keys(patch || {})
  if (!keys.length) return fallback
  if (keys.some((k) => k === 'speed' || k.startsWith('speed'))) return 'editor.speed'
  if (keys.some((k) => k.startsWith('transition'))) return 'editor.transition'
  if (keys.some((k) => k === 'keyframes')) return 'editor.keyframes'
  if (keys.some((k) => k === 'crop' || k.startsWith('transform.crop'))) return 'editor.crop'
  if (keys.some((k) => k === 'in_seconds' || k === 'out_seconds')) return 'editor.cut_trim'
  if (keys.some((k) => k.startsWith('transform') || k.startsWith('position') || k === 'scale'))
    return 'editor.clip_transform'
  if (keys.some((k) => k.startsWith('audio_mix') || k === 'volume' || k === 'gain_db'))
    return 'editor.audio_ops'
  return fallback
}

// The digest is the ONLY thing that preserves sequence, so it is capped rather than dropped:
// 200 entries covers the vast majority of sessions and the overflow count is reported, so a
// truncated digest can never be mistaken for a short session.
export const DIGEST_MAX = 200

/** A fresh, empty session accumulator. */
export function newSession(now = 0) {
  return {
    startedAt: now,
    features: {},        // feature_id -> {commits, undos, redos}
    digest: [],
    digestTotal: 0,
    commits: 0,
    undos: 0,
    redos: 0,
    saves: 0,
    renders: 0,
    noopCommits: 0,      // a feature id the closed enum does not contain — a contract defect
    actions: {},         // action_id -> feature_id, so an undo is attributed to what it undid
    undone: new Set(),   // action_ids counted as undone ONCE, whatever the undo/redo dance
  }
}

function bump(session, featureId, field) {
  const entry = (session.features[featureId] ||= { commits: 0, undos: 0, redos: 0 })
  entry[field] += 1
}

/**
 * Record one discrete edit. `action_id` is what makes the undo rate correct: without it,
 * undo → redo → undo counts two undos against one action and the rate can exceed 100%.
 */
export function recordCommit(session, { actionId, featureId }) {
  if (!isFeatureId(featureId)) {
    // Do NOT silently re-label it. Quietly folding an unknown id into a real feature corrupts
    // that feature's adoption number and hides the wiring bug that produced it; a counter
    // surfaces the defect and leaves every per-feature rate honest.
    session.noopCommits += 1
    session.commits += 1
    session.digestTotal += 1
    return session
  }
  const id = featureId
  session.commits += 1
  bump(session, id, 'commits')
  if (actionId) session.actions[actionId] = id
  if (session.digest.length < DIGEST_MAX) session.digest.push(id)
  session.digestTotal += 1
  return session
}

/**
 * `undos` is counted ONCE PER action_id, not once per keystroke.
 *
 * That is the whole reason action ids exist here: undo -> redo -> undo is one regretted
 * action, and counting it twice lets the per-feature undo RATE exceed the population it is
 * divided by. The raw keystroke sequence is not lost — it is still in `action_digest`.
 */
export function recordUndo(session, actionId) {
  const id = (actionId && session.actions[actionId]) || null
  if (actionId && !session.undone.has(actionId)) {
    session.undone.add(actionId)
    session.undos += 1
    if (id) bump(session, id, 'undos')
  } else if (!actionId) {
    session.undos += 1 // an unattributable undo (the agent-adopt push) still happened
  }
  if (session.digest.length < DIGEST_MAX) session.digest.push('editor.undo')
  session.digestTotal += 1
  return session
}

export function recordRedo(session, actionId) {
  session.redos += 1
  const id = (actionId && session.actions[actionId]) || null
  // `undone` is deliberately NOT cleared here. An earlier version removed the action on redo
  // so a later undo could count it again — which put undo -> redo -> undo straight back at
  // TWO undos for ONE commit, i.e. a 200% undo rate: the exact defect action ids exist to
  // prevent. `undos` answers "how many distinct actions did the user take back", and that is
  // bounded by the number of commits by construction. `redos` and the ordered digest are
  // where the traversal itself survives.
  if (id) bump(session, id, 'redos')
  if (session.digest.length < DIGEST_MAX) session.digest.push('editor.redo')
  session.digestTotal += 1
  return session
}

export function recordSave(session) { session.saves += 1; return session }
export function recordRender(session) { session.renders += 1; return session }

/** The single `editor_session_summary` payload. Counts and enums only — never a value. */
export function summarize(session, { now = 0, nCuts = 0, nOverlays = 0 } = {}) {
  return {
    features: session.features,
    features_used: Object.keys(session.features).sort(),
    duration_s: Math.max(0, Math.round((now - session.startedAt) / 1000)),
    commits: session.commits,
    undos: session.undos,
    redos: session.redos,
    saves: session.saves,
    renders: session.renders,
    noop_commits: session.noopCommits,
    n_cuts: nCuts,
    n_overlays: nOverlays,
    action_digest: session.digest,
    digest_count_total: session.digestTotal,
    digest_truncated: session.digestTotal > session.digest.length,
  }
}
