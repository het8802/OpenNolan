// Pure helpers behind the composer's `@` asset menu (OPN-27). Everything decidable from a
// string is tested here; the menu's keyboard/DOM contract lives in ChatPanel.test.jsx.

import { describe, it, expect } from 'vitest'
import {
  flattenCandidates, mentionQuery, rankCandidates, applyMention, pruneMentions,
  mentionToken, MENTION_GROUPS,
} from './mentions.js'

const file = (path) => ({ path, name: path.split('/').pop(), size_bytes: 1, mtime: 1 })

const assets = () => ({
  kinds: {
    images: [file('assets/images/logo.png')],
    video: [file('assets/video/hook.mp4'), file('assets/video/b-roll.mp4')],
    audio: [file('assets/audio/vo.wav')],
    music: [file('assets/music/bed.mp3')],
  },
  agent_renders: [file('hf/renders/scene2.mp4')],
  renders: [file('renders/final.mp4')],
})

describe('flattenCandidates', () => {
  it('flattens all three buckets in a fixed order with a group label', () => {
    const out = flattenCandidates(assets())
    expect(out.map(c => c.path)).toEqual([
      'assets/images/logo.png',
      'assets/video/hook.mp4', 'assets/video/b-roll.mp4',
      'assets/audio/vo.wav',
      'assets/music/bed.mp3',
      'hf/renders/scene2.mp4',
      'renders/final.mp4',
    ])
    expect(out.map(c => c.group)).toEqual([
      'assets', 'assets', 'assets', 'assets', 'assets', 'agent', 'renders',
    ])
    // Every group a candidate can carry has a heading to render it under.
    const keys = MENTION_GROUPS.map(g => g.key)
    for (const c of out) expect(keys).toContain(c.group)
  })

  it('drops dot-directory descendants so the menu can never offer a SHAPE-invalid path', () => {
    // The endpoint skips a dot-prefixed LEAF but walks with rglob, so these ARE listed by
    // GET /assets while the server's SHAPE gate 422s them. Filtering here is what keeps a
    // legitimate click from failing.
    const a = assets()
    a.kinds.video.push(file('assets/.tmp/clip.mp4'))
    a.agent_renders.push(file('hf/renders/.stage/a.mp4'))
    a.renders.push(file('renders/.part.mp4'))
    const paths = flattenCandidates(a).map(c => c.path)
    expect(paths).not.toContain('assets/.tmp/clip.mp4')
    expect(paths).not.toContain('hf/renders/.stage/a.mp4')
    expect(paths).not.toContain('renders/.part.mp4')
    expect(paths).toHaveLength(7)  // the clean fixture, unchanged
  })

  it('keeps same-named files from different buckets as distinct candidates', () => {
    const out = flattenCandidates({
      kinds: {}, agent_renders: [file('hf/renders/final.mp4')], renders: [file('renders/final.mp4')],
    })
    expect(out.map(c => c.path)).toEqual(['hf/renders/final.mp4', 'renders/final.mp4'])
    expect(out.every(c => c.name === 'final.mp4')).toBe(true)
  })

  it('tolerates an absent/partial payload', () => {
    expect(flattenCandidates(null)).toEqual([])
    expect(flattenCandidates({})).toEqual([])
    expect(flattenCandidates({ kinds: { video: null } })).toEqual([])
  })
})

describe('mentionQuery (the trigger)', () => {
  it('opens on `@` at index 0', () => {
    expect(mentionQuery('@', 1)).toEqual({ start: 0, end: 1, query: '' })
    expect(mentionQuery('@ho', 3)).toEqual({ start: 0, end: 3, query: 'ho' })
  })

  it('opens on `@` preceded by whitespace', () => {
    expect(mentionQuery('use @ho', 7)).toEqual({ start: 4, end: 7, query: 'ho' })
    expect(mentionQuery('line\n@x', 7)).toEqual({ start: 5, end: 7, query: 'x' })
  })

  it('does NOT open inside an email address', () => {
    expect(mentionQuery('someone@example.com', 19)).toBeNull()
    expect(mentionQuery('a@b', 3)).toBeNull()
  })

  it('closes once whitespace follows the `@`', () => {
    expect(mentionQuery('@hook ', 6)).toBeNull()
    expect(mentionQuery('@hook and more', 14)).toBeNull()
  })

  it('is caret-relative: a caret before the `@` sees no mention', () => {
    expect(mentionQuery('hello @hook', 3)).toBeNull()
    expect(mentionQuery('hello @hook', 11)).toEqual({ start: 6, end: 11, query: 'hook' })
  })

  it('reads the token the caret is IN, not the last one in the text', () => {
    const text = '@one @two'
    expect(mentionQuery(text, 4)).toEqual({ start: 0, end: 4, query: 'one' })
    expect(mentionQuery(text, 9)).toEqual({ start: 5, end: 9, query: 'two' })
  })

  it('returns null for text with no mention, and tolerates junk input', () => {
    expect(mentionQuery('just a sentence', 15)).toBeNull()
    expect(mentionQuery('', 0)).toBeNull()
    expect(mentionQuery(null, 5)).toBeNull()
  })
})

describe('rankCandidates', () => {
  const c = (path) => ({ path, name: path.split('/').pop(), group: 'assets' })

  it('orders basename prefix > basename substring > path only', () => {
    const list = [
      c('assets/video/b-roll.mp4'),        // tier 3: only the DIRECTORY says "video"
      c('assets/images/videocard.png'),    // tier 1: name starts with "video"
      c('assets/audio/my-video.wav'),      // tier 2: name merely contains "video"
      c('assets/music/video.mp4'),         // tier 1 too — "video.mp4" starts with "video"
    ]
    expect(rankCandidates(list, 'video').map(x => x.path)).toEqual([
      'assets/images/videocard.png',       // tier 1, first in flattened order
      'assets/music/video.mp4',            // tier 1, later in flattened order
      'assets/audio/my-video.wav',         // tier 2
      'assets/video/b-roll.mp4',           // tier 3
    ])
  })

  it('an exact basename outranks a prefix, which outranks a substring', () => {
    const list = [
      c('assets/video/hook.mp4.bak.mp4'),  // tier 1: starts with "hook.mp4"
      c('assets/video/my-hook.mp4'),       // tier 2: contains it
      c('assets/video/hook.mp4'),          // tier 0: IS it
    ]
    expect(rankCandidates(list, 'hook.mp4').map(x => x.path)).toEqual([
      'assets/video/hook.mp4',
      'assets/video/hook.mp4.bak.mp4',
      'assets/video/my-hook.mp4',
    ])
  })

  it('the §5.5 worked example: @video puts a name match above a path-only match', () => {
    const list = [c('assets/video/b-roll.mp4'), c('assets/video/product-video.mp4')]
    expect(rankCandidates(list, 'video').map(x => x.path)).toEqual([
      'assets/video/product-video.mp4',   // name matches
      'assets/video/b-roll.mp4',          // only the directory matches
    ])
  })

  it('is stable: ties keep the flattened order', () => {
    const list = [c('assets/video/a-clip.mp4'), c('assets/video/b-clip.mp4'), c('assets/video/c-clip.mp4')]
    expect(rankCandidates(list, 'clip').map(x => x.name))
      .toEqual(['a-clip.mp4', 'b-clip.mp4', 'c-clip.mp4'])
    expect(rankCandidates([...list].reverse(), 'clip').map(x => x.name))
      .toEqual(['c-clip.mp4', 'b-clip.mp4', 'a-clip.mp4'])
  })

  it('is case-insensitive on both name and path', () => {
    const list = [c('assets/video/HOOK.MP4')]
    expect(rankCandidates(list, 'hook')).toHaveLength(1)
    expect(rankCandidates(list, 'VIDEO')).toHaveLength(1)
  })

  it('matches names containing spaces', () => {
    const list = [c('assets/images/product shot.png')]
    expect(rankCandidates(list, 'product')).toHaveLength(1)
    expect(rankCandidates(list, 'shot')).toHaveLength(1)
  })

  it('an empty query offers every candidate in flattened order', () => {
    const list = flattenCandidates(assets())
    expect(rankCandidates(list, '').map(x => x.path)).toEqual(list.map(x => x.path))
  })

  it('returns a new array, never the input', () => {
    const list = [c('assets/video/a.mp4')]
    expect(rankCandidates(list, '')).not.toBe(list)
    expect(rankCandidates(list, 'a')).not.toBe(list)
  })
})

// ── The set-preservation property (agreed-design.md §5.8) ────────────────────────────
// Ranking must REORDER, never FILTER. Proven over a deterministic, enumerated corpus —
// not a sample — and by LENGTH + PATH MULTISET, not a mathematical set (which would erase
// a duplicated output).
describe('rankCandidates preserves the match set exactly', () => {
  // >25 matching fixtures for `clip`, so a silent "top N" cap cannot pass.
  const many = Array.from({ length: 30 }, (_, i) =>
    ({ path: `assets/video/clip-${String(i).padStart(2, '0')}.mp4`, name: `clip-${String(i).padStart(2, '0')}.mp4`, group: 'assets' }))
  const corpusCandidates = [
    ...many,
    { path: 'assets/images/logo.png', name: 'logo.png', group: 'assets' },
    { path: 'assets/audio/whoosh.wav', name: 'whoosh.wav', group: 'assets' },
    { path: 'assets/images/product shot.png', name: 'product shot.png', group: 'assets' },
    { path: 'hf/renders/scene2.mp4', name: 'scene2.mp4', group: 'agent' },
    { path: 'renders/final.mp4', name: 'final.mp4', group: 'renders' },
  ]

  /** Every non-empty substring of every fixture's lowercased name and path, plus the empty
   *  query and a sentinel that matches nothing. Deterministic and fully enumerated. */
  const queries = () => {
    const set = new Set(['', 'zzz-matches-nothing'])
    for (const c of corpusCandidates) {
      for (const s of [c.name.toLowerCase(), c.path.toLowerCase()]) {
        for (let i = 0; i < s.length; i++) {
          for (let j = i + 1; j <= s.length; j++) set.add(s.slice(i, j))
        }
      }
    }
    return [...set]
  }

  const unranked = (q) => corpusCandidates.filter(c =>
    c.name.toLowerCase().includes(q.toLowerCase()) || c.path.toLowerCase().includes(q.toLowerCase()))

  it('has the same length and the same path multiset for every enumerated query', () => {
    const qs = queries()
    expect(qs.length).toBeGreaterThan(500)   // the corpus really is exhaustive
    for (const q of qs) {
      const ranked = rankCandidates(corpusCandidates, q)
      const want = unranked(q)
      expect(ranked, `length for query ${JSON.stringify(q)}`).toHaveLength(want.length)
      expect(ranked.map(c => c.path).sort(), `multiset for query ${JSON.stringify(q)}`)
        .toEqual(want.map(c => c.path).sort())
    }
  })

  it('returns all 30 matching fixtures — there is no result cap', () => {
    const ranked = rankCandidates(corpusCandidates, 'clip')
    expect(ranked).toHaveLength(30)
    expect(new Set(ranked.map(c => c.path)).size).toBe(30)
  })

  it('a query that matches nothing returns nothing, so the menu closes', () => {
    expect(rankCandidates(corpusCandidates, 'zzz-matches-nothing')).toEqual([])
  })
})

describe('applyMention', () => {
  it('replaces the query range with the token plus a trailing space', () => {
    const range = mentionQuery('use @ho', 7)
    const out = applyMention('use @ho', range, 'assets/video/hook.mp4')
    expect(out.text).toBe('use @assets/video/hook.mp4 ')
    expect(out.caret).toBe(out.text.length)
    expect(out.mention).toEqual({ token: '@assets/video/hook.mp4', path: 'assets/video/hook.mp4' })
  })

  it('preserves text after the caret and lands the caret before it', () => {
    const text = 'use @ho for the opener'
    const range = mentionQuery(text, 7)
    const out = applyMention(text, range, 'assets/video/hook.mp4')
    expect(out.text).toBe('use @assets/video/hook.mp4  for the opener')
    expect(out.text.slice(out.caret)).toBe(' for the opener')
  })

  it('handles a bare `@` and a path containing spaces', () => {
    const out = applyMention('@', mentionQuery('@', 1), 'assets/images/product shot.png')
    expect(out.text).toBe('@assets/images/product shot.png ')
    expect(out.mention.path).toBe('assets/images/product shot.png')
  })

  it('mentionToken is the `@`-anchored project-relative path', () => {
    expect(mentionToken('renders/final.mp4')).toBe('@renders/final.mp4')
  })
})

describe('pruneMentions', () => {
  const m = (path) => ({ token: `@${path}`, path })

  it('keeps a mention whose token is still in the draft', () => {
    expect(pruneMentions([m('assets/video/hook.mp4')], 'use @assets/video/hook.mp4 now'))
      .toHaveLength(1)
  })

  it('drops a mention the user deleted or edited', () => {
    expect(pruneMentions([m('assets/video/hook.mp4')], 'use nothing')).toEqual([])
    expect(pruneMentions([m('assets/video/hook.mp4')], 'use @assets/video/hook')).toEqual([])
  })

  it('is safe against a PREFIX COLLISION between two tokens (the `@` anchor)', () => {
    // `@renders/final.mp4` must NOT be considered present inside `@hf/renders/final.mp4`.
    expect(pruneMentions([m('renders/final.mp4')], 'use @hf/renders/final.mp4')).toEqual([])
    expect(pruneMentions([m('hf/renders/final.mp4')], 'use @hf/renders/final.mp4')).toHaveLength(1)
  })

  // ⚠ THE DANGEROUS DIRECTION. The `@` anchor above says nothing about text APPENDED to a
  // token: `@assets/video/hook.mp4` is a plain substring of `@assets/video/hook.mp4.bak`, so
  // an unbounded `includes` would keep resolving hook.mp4 while the prose now names a
  // different file — the agent would act on a file the user did not write.
  it('drops a reference when the user APPENDS to its token', () => {
    const ref = [m('assets/video/hook.mp4')]
    expect(pruneMentions(ref, 'use @assets/video/hook.mp4.bak')).toEqual([])
    expect(pruneMentions(ref, 'use @assets/video/hook.mp4-old')).toEqual([])
    expect(pruneMentions(ref, 'use @assets/video/hook.mp42')).toEqual([])
    expect(pruneMentions(ref, 'use @assets/video/hook.mp4/nested.mp4')).toEqual([])
  })

  it('drops a reference when the user PREPENDS to its token (no longer a mention)', () => {
    // `mentionQuery` would not treat this as a mention either — the `@` is not at a
    // whitespace boundary — so the sidecar must not claim it is one.
    expect(pruneMentions([m('assets/video/hook.mp4')], 'email@assets/video/hook.mp4')).toEqual([])
  })

  it('keeps a reference at every legitimate boundary', () => {
    const ref = [m('assets/video/hook.mp4')]
    expect(pruneMentions(ref, '@assets/video/hook.mp4')).toHaveLength(1)           // whole draft
    expect(pruneMentions(ref, '@assets/video/hook.mp4 ')).toHaveLength(1)          // trailing space
    expect(pruneMentions(ref, 'use @assets/video/hook.mp4\nthen cut')).toHaveLength(1)  // newline
    expect(pruneMentions(ref, '  @assets/video/hook.mp4  ')).toHaveLength(1)       // padded
    // Two occurrences, one of them mangled: the intact one still counts.
    expect(pruneMentions(ref, '@assets/video/hook.mp4.bak and @assets/video/hook.mp4')).toHaveLength(1)
  })

  it('drops on trailing punctuation — a deliberate, safe-direction false negative', () => {
    // No character class separates "sentence punctuation" from "more path" (a filename may
    // contain a comma as readily as a dot), so we drop rather than risk a stale reference.
    expect(pruneMentions([m('assets/video/hook.mp4')], 'use @assets/video/hook.mp4, then cut'))
      .toEqual([])
  })

  it('tolerates empty input', () => {
    expect(pruneMentions(null, 'text')).toEqual([])
    expect(pruneMentions([m('a/b.mp4')], '')).toEqual([])
  })
})
