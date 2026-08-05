// Pure helpers for the chat composer's `@` asset mention menu (OPN-27). No React, no DOM,
// no fetch — ChatPanel owns the menu state and the network; everything decidable from a
// string lives here so it can be unit-tested.
//
// The contract with the server (server/app.py): the composer sends a structured
// `mentions[]` sidecar beside the visible text, and the server resolves each project
// -relative path to a verified absolute one. See docs/plans/opn-39-opn-27/agreed-design.md.

/** Bucket key -> the order buckets are concatenated in. Ties in the ranking fall back to
 *  this order, so the menu is fully deterministic. */
export const MENTION_GROUPS = [
  { key: 'assets', label: 'Project assets' },
  { key: 'agent', label: 'Agent clips' },
  { key: 'renders', label: 'Renders' },
]

/** A path segment starting with `.` — the ONE rule `list_assets` does not already enforce. */
const hasDotSegment = (path) => String(path).split('/').some(seg => seg.startsWith('.'))

/**
 * Flatten `GET /projects/{id}/assets` into one ordered, labelled candidate list.
 *
 * Order is fixed — kinds, then agent renders, then renders — because the ranking below
 * breaks ties on it. Within a bucket the endpoint already sorts.
 *
 * DROPS any candidate with a dot-prefixed path segment. That is not cosmetic: it upholds
 * the invariant *every path the menu can offer is SHAPE-valid on the server*. The endpoint
 * skips a dot-prefixed LEAF but walks with rglob, so `assets/.tmp/clip.mp4` is listable
 * while the server's SHAPE gate rejects it — without this filter a legitimate click would
 * 422. The predicate's other rules (root prefix, per-root extension, project-relative, no
 * `..`) hold by construction of the endpoint's own walk, so they are not re-checked here.
 */
export function flattenCandidates(assets) {
  const out = []
  const push = (items, group) => {
    for (const it of items || []) {
      if (!it?.path || hasDotSegment(it.path)) continue
      out.push({ path: it.path, name: it.name || it.path.split('/').pop(), group })
    }
  }
  const kinds = assets?.kinds || {}
  for (const kind of ['images', 'video', 'audio', 'music']) push(kinds[kind], 'assets')
  push(assets?.agent_renders, 'agent')
  push(assets?.renders, 'renders')
  return out
}

/**
 * The active `@query` at the caret, or null when the menu should be closed.
 *
 * Opens only when the `@` is at index 0 or preceded by whitespace (so `a@b.com` never
 * opens one) and nothing between it and the caret is whitespace. `end` is the caret, so
 * text AFTER the caret is untouched by a replacement.
 */
export function mentionQuery(text, caret) {
  const s = String(text ?? '')
  const pos = Math.max(0, Math.min(Number(caret) || 0, s.length))
  for (let i = pos - 1; i >= 0; i--) {
    const ch = s[i]
    if (/\s/.test(ch)) return null           // whitespace ends the token before any `@`
    if (ch !== '@') continue
    if (i > 0 && !/\s/.test(s[i - 1])) return null   // `a@b.com` — not a mention
    return { start: i, end: pos, query: s.slice(i + 1, pos) }
  }
  return null
}

/**
 * Rank candidates for `query`: exact basename, basename prefix, basename substring, then
 * path-only. Stable — ties keep the flattened order — and deterministic.
 *
 * ⚠ RANKING REORDERS, IT NEVER FILTERS. The four tiers partition exactly
 * `name.includes(q) || path.includes(q)`, which is the match rule itself: tiers 0-2 are
 * `name.includes(q)` split by strength, tier 3 is the rest of the path matches. So the
 * result SET is identical to the unranked one — which is what keeps "zero results closes
 * the menu" true and Enter never dead. An implementation that changes the set is a bug.
 *
 * There is deliberately NO cap: a cap silently hides assets the user owns.
 */
export function rankCandidates(candidates, query) {
  const list = candidates || []
  const q = String(query ?? '').toLowerCase()
  if (!q) return [...list]                   // bare `@` — everything, flattened order
  const tiers = [[], [], [], []]
  for (const c of list) {
    const name = String(c.name || '').toLowerCase()
    const path = String(c.path || '').toLowerCase()
    if (name === q) tiers[0].push(c)
    else if (name.startsWith(q)) tiers[1].push(c)
    else if (name.includes(q)) tiers[2].push(c)
    else if (path.includes(q)) tiers[3].push(c)
  }
  return [...tiers[0], ...tiers[1], ...tiers[2], ...tiers[3]]
}

/** The visible token for a candidate. The server parses nothing — the sidecar is
 *  authoritative — so this is purely what the user reads. */
export const mentionToken = (path) => `@${path}`

/**
 * Replace the `@query` range with the chosen path plus a trailing space.
 * Returns the new text, the caret position after the inserted token, and the structured
 * mention to add to the sidecar.
 */
export function applyMention(text, range, path) {
  const s = String(text ?? '')
  const token = mentionToken(path)
  const insert = `${token} `
  return {
    text: s.slice(0, range.start) + insert + s.slice(range.end),
    caret: range.start + insert.length,
    mention: { token, path },
  }
}

/**
 * Is `token` present in `text` as a WHOLE mention — bounded by whitespace or the ends of
 * the string on both sides?
 *
 * A bare `includes` is not enough, and the reason is worth stating because it is easy to
 * get wrong in the other direction. The `@` anchor does protect against one threat — a
 * PREFIX COLLISION between two tokens, e.g. `@renders/final.mp4` cannot occur inside
 * `@hf/renders/final.mp4`, because the char before `renders` there is `/`, not `@`. But it
 * says nothing about a SUFFIX APPEND: `@assets/video/hook.mp4` IS a substring of
 * `@assets/video/hook.mp4.bak`, so `includes` would keep resolving the original file while
 * the prose now names a different one. Same for a prepend (`email@assets/...`), which is
 * not a mention at all. Both sides need a boundary — the same rule `mentionQuery` uses to
 * decide a mention exists in the first place (`@` at a whitespace boundary, then a run of
 * non-whitespace).
 *
 * Deliberately strict: trailing punctuation (`@path,`) also drops the reference, because no
 * character class can separate "sentence punctuation" from "more path" — filenames may
 * contain a comma as readily as a dot. Dropping is the safe direction: an unresolved
 * mention makes the agent ask, whereas a stale one makes it act on a file the user did not
 * name. `mentionQuery`'s own token rule has the same ceiling.
 */
function hasWholeToken(text, token) {
  const bounded = (ch) => ch === undefined || /\s/.test(ch)
  for (let i = text.indexOf(token); i !== -1; i = text.indexOf(token, i + 1)) {
    if (bounded(text[i - 1]) && bounded(text[i + token.length])) return true
  }
  return false
}

/**
 * Drop references whose exact token is no longer a whole, unedited word in the draft, so
 * editing, extending or deleting a mention stops it being sent.
 */
export function pruneMentions(mentions, text) {
  const s = String(text ?? '')
  return (mentions || []).filter(m => m?.token && hasWholeToken(s, m.token))
}
