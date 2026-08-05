# OPN-39 + OPN-27 — Codex ratification

**Status: RATIFIED WITH AMENDMENTS**

The two ticket decisions are sound: write the new-project canvas in the
scaffold while preserving both legacy fallbacks, and send selected assets as a
structured sidecar that the server resolves to absolute project paths. I do
not contest ruling B: the dashboard render card is existing Phase 5b work, and
the stale-render case makes the project canvas an invalid final authority.

## What I verified

I re-opened every cited source anchor in the agreed design. The corrected
anchors are all current, including `server/agent_runner.py:685` (`cwd` is the
repo root), the fresh-client preamble at `server/agent_runner.py:1946`,
`server/app.py:974` (the runner call),
`schemas/artifacts/edit_decisions.schema.json:297`, and
`web/src/styles.css:480`. The canvas chain, current 1920x1080 Python default,
per-field JS fallback, Studio's one-argument wrapper, asset-bucket listing,
receipt contents, Phase 5b anchor, and test anchors also match the current
tree.

The first-turn claim is accurate: a client is marked fresh at
`server/agent_runner.py:1191-1197`, then the preamble is prepended only when
that flag is consumed at `:1930-1946`. A restarted/recreated client can make a
later conversational turn fresh again, but a normal warm turn does not get the
preamble; server-resolved absolute paths remain the correct contract.

Ruling A is also correct. `list_assets` calls `Path.is_file()` for candidates
at `server/app.py:441-446`, `:463-465`, and `:495-496`, while resolution follows
symlinks, so a post-resolve project-containment check is necessary. The agreed
draft-recovery requirement is implementable as written: retain the already
computed `message`, and on a non-abort failure in the current catch path
(`web/src/chat/useAgentChat.js:182-192`) refill `input` with it while retaining
the intentional optimistic user bubble and error line; its stated test proves
the needed observable result.

My Phase 1 and cross-review contributions are represented accurately: named
canvas constants without a whole-object fallback, the structured sidecar,
project-first resolution, Studio forwarding of every `send` argument,
selection pruning, and the keyboard/accessibility test matrix are all retained
without being softened. The amendments below narrow two unsupported claims;
they do not change either ticket's selected design.

## Required amendments before implementation

| # | Precise amendment | Why it is required |
| --- | --- | --- |
| 1 | Delete section 5.1's claim that build item 11 recovers re-resolution for a persisted thread, delete build item 11, and renumber the following OPN-27 items. Do not claim that reopening and re-sending a historical message re-resolves its sidecar unless a separately specified history-resend interaction is added. | `loadThread` only restores stored messages and session state (`web/src/chat/useAgentChat.js:79-87`); there is no resend action, and a fresh composer send has only its freshly selected references. Persisting unused mention metadata is scope creep and its proposed success condition cannot be verified. |
| 2 | Place the mention validator in `server/app.py` as a small endpoint-adjacent helper; remove `server/editor.py` from build item 12 and replace the “coin-flip” placement paragraph. Alternatively, specify an explicit cycle-free dependency that passes the eligibility configuration into `server/editor.py`. | The exact extension sets are owned by `server/app.py:33-35`, while `server/app.py` already imports `server.editor` at `:101`; having editor import them back creates a circular ownership/dependency the plan currently does not resolve. Keeping the validator with the listing policy is both implementable and less likely to drift. |
| 3 | Tighten section 5.2's SHAPE predicate to mirror menu eligibility by root: `assets/**` permits the media extensions that `_classify` exposes; `renders/<direct-child>` and `hf/renders/**` permit only `VIDEO_EXTS`. Also make the composer flattening exclude any candidate the SHAPE predicate rejects (or change `list_assets` to exclude dot-directory descendants), and add tests for a non-video under `hf/renders/` and a dot-directory descendant. | The current endpoint permits only video in `renders/` and `hf/renders/` (`server/app.py:463-465`, `:495-496`), but the proposed union permits an image/audio path there. Further, `assets` currently filters only a leaf name (`:441-443`), whereas the plan rejects every dot-prefixed segment. Without this amendment, a path that the menu can offer is not always valid, and a tampered path can be mislabeled as a harmless state failure rather than the promised SHAPE 422. |
| 4 | In section 4.4, remove “its only referrer is its sibling `Inspector.jsx`.” Say only that `Editor.jsx` is currently unimported by `App.jsx` and `main.jsx`, if retaining this observation. | The actual relationship is the reverse: `web/src/editor/Editor.jsx:4` imports `Inspector`; no source outside `Editor.jsx` imports `Editor`. This is a factual source-relationship correction, not a line-number drift. |

After amendments 1–3, the build order is implementable: pure mention helpers and UI precede
sidecar forwarding; the wrapper forwards all arguments; draft restoration handles rejected
requests; and the endpoint helper then defines the contract and contract tests. Item 4 is
documentation-only. Each retained row has a test or manual success condition; the removed
thread-resend row is the only one whose condition did not correspond to a product path.

## Rules and scope check

The plan respects the repository rules after these amendments. OPN-39 keeps preview and
export aligned for legacy documents, names but does not share/mutate fallback objects,
writes a schema-valid scaffold, and tests both new and legacy behaviour. OPN-27 isolates its
new interaction logic in a pure tested helper, uses non-`st-` chat CSS appropriately,
specifies accessible icon-based UI rather than emoji, and does not add an editor-document
write path.

The render-card change remains out of scope. The Phase 5b handoff is good enough to preserve
the analysis: use a document canvas only as an initial reservation, treat video metadata as
authority, and consider putting the rendered canvas into the existing receipt at
`lib/project.py:619-621`; it correctly acknowledges that this receipt cannot describe
earlier unreceipted renders. The only scope creep I found is persisted historical mention
metadata without a historical resend feature (amendment 1).

---

## Rev 2 — post-ratification result ranking

**Verdict: RATIFIED WITH AMENDMENTS**

I do **not** prefer basename-only matching. The existing, ratified rule intentionally
matches both basename and project-relative path; retaining it lets `@video`, `@music`, or
a directory fragment find an asset when the user knows its bucket rather than its filename.
The four-tier ranking is a small, pure-helper-only refinement of that rule, directly fixes a
real discoverability failure, changes neither the sidecar nor server behaviour, and is in
scope for OPN-27.

### Invariant verification

For a non-empty lowercased query `q`, tiers 0–2 are exactly `name.includes(q)`: exact basename
is a subset of prefix, prefix is a subset of substring, and every substring is in tier 0, 1,
or 2 according to the first matching condition. Tier 3 is
`rel.includes(q) && !name.includes(q)`. Their union is therefore
`name.includes(q) || rel.includes(q)`, exactly the existing match predicate. An empty query is
handled explicitly as every flattened candidate, which is also what JavaScript `includes('')`
would produce; ranking cannot turn a non-empty old result into zero results.

The claim does **not** actually depend on `rel` containing `name`; the partition proof holds
regardless. That containment nevertheless holds for every real flattened candidate:
`list_assets` constructs both `path` from `f.relative_to(proj)` and `name` from `f.name` in
the `assets` bucket (`server/app.py:441-450`), direct `renders/` bucket (`:463-485`), and
`hf/renders/` bucket (`:495-505`). The stated line correction is also correct:
`web/src/chat/useAgentChat.js:116` is `setInput('')`, while `:117` is `setMessages`.

Stable sorting, the empty-query rule, and no result cap are correctly specified. Stable ties
preserve the endpoint/helper's deterministic flattened order; returning all candidates for a
bare `@` retains the existing menu behaviour; and a cap would silently make an owned asset
unmentionable without evidence that it is needed.

### Required amendment before implementation

Strengthen the §5.8 ranking property test. “For a sample of queries, the ranked result SET
equals the unranked match set” can miss a duplicate output because a mathematical set erases
multiplicity, and “sample” is not a reproducible test corpus. Specify a deterministic corpus
of the empty query, a non-match sentinel, and every non-empty substring of each fixture
candidate's lowercased `name` and `path`; for each query, assert that ranked output has the
same length and the same path multiset as
`candidates.filter(name.includes(q) || path.includes(q))`. Include more than a conventional
menu cap's worth of matching fixture candidates (for example, 25) and assert all are returned.
The existing tier-precedence and stable-tie tests remain necessary; this amendment makes the
set-preservation and no-cap claims genuinely verifiable without adding a property-testing
library.
