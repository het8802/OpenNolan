# OPN-39 + OPN-27 — agreed design

**Status: PLAN — RATIFIED, rev 2** · joint document by `claude` and `codex` · baseline
`ae57d47` on `ui-polishing`

Ratified by codex in two passes: rev 1 with four amendments (§11) and rev 2's result ranking
with one (§12). **All five applied, none contested. Nothing in this document is unratified.**

This supersedes `claude/design.md`, `codex/design.md`, `claude/cross-review.md`, and
`codex/ratification.md`. Those stay in place as the audit trail; where this document
disagrees with any of them, this one wins.

Every `file:line` below was re-opened and re-verified at ratification. Where an earlier doc
cited a line that had drifted or a relationship that was backwards, §10 records the
correction. No number here is a guess, and every citation carries its full repo-relative
path so an implementer can open it directly.

---

## 1. Decisions in one line each

- **OPN-39** — A new editor document states its own canvas: `scaffoldEditDecisions` emits
  `metadata.compose_target = {1080, 1920, 30}`. Neither the JS fallback nor the Python
  renderer default changes. No migration. An explicit `compose_target` always wins.
- **OPN-27** — The composer gets an `@` autocomplete over the project's three asset
  buckets. The chosen file travels as a **structured `mentions[]` sidecar** on
  `POST /chat`; the server validates it against a shape predicate that mirrors menu
  eligibility by root, and hands the agent verified absolute paths.

Production surface: **3 JS/JSX files + 1 new pure module + 1 CSS block + 1 Python file.**

---

## 2. Conflicts settled

| # | claude proposed | codex proposed | Winner | Why |
| --- | --- | --- | --- | --- |
| 1 | Scaffold-time canvas, fallbacks untouched | Same | **both, independently** | Converged in Phase 1 with no contact. Not reopened. |
| 2 | Inline literals + a cross-reference comment on `canvasOf` | Named `NEW_PROJECT_CANVAS` / `LEGACY_CANVAS` constants | **codex** | Naming the legacy branch at the site the next engineer will edit is a better mitigation for the top risk than a comment alone. We keep the comment too — they cost nothing together. |
| 3 | — | (constraint added on review) | **claude's constraint on codex's item 2** | `canvasOf` defaults **per field**; naming the literals must not become a whole-object swap. See §4.3. |
| 4 | Cited `skills/pipelines/instagram-fast-reel/edit-director.md` | Cited `pipeline_defs/instagram-fast-reel.yaml:192` | **codex** | The pipeline definition *gates* 9:16 (`:192`, `:218`, `:251`, `:260`); the skill prose only recommends it. Stronger evidence, and it is codex's find. |
| 5 | Visible `@[path]` text token, server regex | Structured `mentions[]` sidecar | **codex** | claude withdrew the token on the merits, not on the wrapper bug. See §5.1. |
| 6 | Reuse `resolve_source_path` | Explicit prefix whitelist, project-first | **codex** | And it exposed a real hole in claude's version — `resolve_source_path` tries **repo-root first**. See §5.2. |
| 7 | Vanished file → `NOT FOUND`, turn proceeds | Vanished file → 422, turn refused | **claude** | The draft is destroyed before the request and never restored. See §5.3 (ruling A). |
| 8 | — | Fix the `web/src/studio/Studio.jsx:255` single-arg wrapper | **codex** | codex found and mitigated it independently; claude's Phase 1 prediction that it would be missed was wrong. |
| 9 | Render card: intrinsic ratio | Render card: derive from the doc canvas | **neither — ruled OUT of scope** | Both sources have a real defect and both were half right. See §6 (ruling B). codex did not contest this at ratification. |
| 10 | `projectId` via the hook's return object | `projectId` passed at both JSX call sites | **claude** | The hook already takes it (`web/src/chat/useAgentChat.js:23`); returning it reaches both call sites in one line. |
| 11 | — | Prune a selected ref when its token leaves the draft | **codex** | Real user-visible benefit; claude's token design had no equivalent. |
| 12 | — | Pointer-down select without blur; Shift+Enter never selects; caret restoration; arrow wrapping; "assert the runner is not called on rejection" | **codex** | claude under-specified all five. |
| 13 | — | — | **new, from convergence** | Post-resolve symlink containment re-check (§5.2) and the draft-restore fix (§5.3). Neither original design had either. |
| 14 | Persist `mentions` on the stored thread message | Delete it — nothing re-resolves it | **codex** (ratification amendment 1) | `loadThread` restores messages and session state only (`web/src/chat/useAgentChat.js:79-87`); there is no resend action, so the success condition was unverifiable. §5.1. |
| 15 | Validator in `server/editor.py` ("a coin-flip") | Validator in `server/app.py` | **codex** (ratification amendment 2) | Not a coin-flip: the extension sets live in `server/app.py:33-35` and `server/app.py:101` already imports `server.editor`, so importing back is a cycle. §5.2. |
| 16 | One SHAPE extension union for all roots | Per-root eligibility mirroring the endpoint | **codex** (ratification amendment 3) | The union both 422s a legitimate menu selection and downgrades a tampered path to a harmless state failure. A defect in claude's ruling, in both directions. §5.2. |
| 17 | "`Editor.jsx`'s only referrer is `Inspector.jsx`" | The relationship is the reverse | **codex** (ratification amendment 4) | `web/src/editor/Editor.jsx:4` imports Inspector; nothing imports Editor. §4.4, §10. |

All four ratification amendments were verified against the tree before acceptance. None was
contested.

---

## 3. Shared ground truth

Everything below was re-verified for this document.

### 3.1 The canvas chain

```
        WHERE THE CANVAS COMES FROM (editor Render button, today)

  artifacts/edit_decisions.json
      metadata.compose_target
             |
    +--------+--------+
    |                 |
    v                 v
  PREVIEW           EXPORT
  interp.canvasOf   video_compose._resolve_canvas
  interp.js:574     video_compose.py:1131
    |                 |
   absent?           absent?
    |                 |  profile rung skipped: render_jobs.py:540-546
    |                 |  builds exec_inputs with NO "profile" key
    v                 v
  1920x1080@30      1920x1080@30
  interp.js:577-579 video_compose.py:1141
```

Two independently maintained literals that happen to be equal. That equality **is** the
preview==export guarantee for a canvas-less document, and nothing enforces it.

- `web/src/editor/interp.js:573-581` — `canvasOf`, per-field `|| 1920 / || 1080 / || 30`.
- `web/src/editor/interp.js:562-571` — `setCanvas`, merges so unspecified dims survive.
- `web/src/editor/interp.js:194` — `scaffoldEditDecisions`, emits no `metadata`.
- `web/src/studio/Studio.jsx:134` — the scaffold is what loads when the project has no
  document; `server/editor.py:43-52` returns `None`, a normal state.
- `tools/video/video_compose.py:1131-1174` — `_resolve_canvas`; literal at
  `tools/video/video_compose.py:1141`; `compose_target` override and validation at
  `tools/video/video_compose.py:1149-1173`; the even-dimension gate at
  `tools/video/video_compose.py:1167-1172`.
- `server/render_jobs.py:540-546` — `exec_inputs` for `render_proxies`. No `profile`.

The agent path is already vertical **and verified**: `pipeline_defs/instagram-fast-reel.yaml:192`
is a `review_focus` gate requiring `compose_target set to 1080x1920 (9:16)`,
`pipeline_defs/instagram-fast-reel.yaml:218` requires the output canvas to be 1080x1920, and
`pipeline_defs/instagram-fast-reel.yaml:251` runs a `visual_qa` probe with
`expected {width:1080, height:1920}`. The gap OPN-39 closes is the **manual, editor-first
project** and nothing else.

### 3.2 The mention chain

- `web/src/chat/ChatPanel.jsx:132-147` — plain `<textarea>`; Enter sends, Shift+Enter
  newlines (`web/src/chat/ChatPanel.jsx:140-145`). Props are
  `{ chat, disabled, className, auth, onReconnect }` (`web/src/chat/ChatPanel.jsx:15`) —
  **no `projectId`**.
- `web/src/chat/useAgentChat.js:23` — `useAgentChat(projectId, {...})`; the hook holds the
  id but omits it from its return object (`web/src/chat/useAgentChat.js:286-293`).
- `web/src/chat/useAgentChat.js:116` — `setInput('')` runs **before** the request.
  `web/src/chat/useAgentChat.js:182-192` — the catch appends an error message and **never
  restores the draft**.
- `web/src/chat/useAgentChat.js:79-87` — `loadThread` calls `clearChat()`, sets the stored
  messages, restores `session_id`, sets the active thread. **There is no resend action**;
  a historical message cannot be re-sent. (Basis for §5.1.)
- `web/src/api.js:226-232` — `chatStream` POSTs `{message, thread_id, model}`.
- `server/app.py:114-118` — `ChatRequest`. `server/app.py:974` —
  `await runner.run_turn(project_id, body.message, on_event=emit)`. (The decorator is at
  `server/app.py:912`; `server/app.py:974` is the line an implementer edits.)
- `server/app.py:101` — `from server import editor as editor_mod`. The dependency runs
  app → editor, one way. (Basis for §5.2's placement.)
- `server/agent_runner.py:685` — `cwd=str(repo_root)`. The project dir is only an extra
  workspace (`server/agent_runner.py:602-617`). The absolute-path instruction is in the
  **first-turn** preamble (`server/agent_runner.py:1242-1252`, composed at
  `server/agent_runner.py:1946`), so a bare relative path is not reliably resolvable on
  later turns. This is why the server must resolve.

**Bucket eligibility — what the menu can actually offer** (`server/app.py:421-507`). This is
the contract the shape predicate must mirror exactly:

| Bucket | Walk | Path shape | Extensions accepted |
| --- | --- | --- | --- |
| `kinds` | `rglob` (`server/app.py:441`) | `assets/**` | `_classify` (`server/app.py:48-56`) ⇒ `IMAGE_EXTS \| VIDEO_EXTS \| AUDIO_EXTS` (`server/app.py:33-35`) |
| `renders` | `glob` — **direct children only** (`server/app.py:463`) | `renders/<name>` | `VIDEO_EXTS` only (`server/app.py:464`) |
| `agent_renders` | `rglob` (`server/app.py:495`) | `hf/renders/**` | `VIDEO_EXTS` only (`server/app.py:496`) |

All three list `str(f.relative_to(proj))`, i.e. project-relative
(`server/app.py:444`, `:477`, `:501`). All three skip a **leaf** name starting with `.`
(`server/app.py:442`, `:464`, `:496`) — but the two `rglob` buckets do **not** skip a
dot-prefixed *directory*, so `assets/.tmp/clip.mp4` and `hf/renders/.stage/a.mp4` are
listable. That gap is what §5.2's composer-side filter closes. Proxy and review internals
under `renders/` are already excluded by the non-recursive walk (`server/app.py:458`).

---

## 4. OPN-39 — the editor canvas

### 4.1 Decision

`scaffoldEditDecisions` writes `metadata.compose_target = {width: 1080, height: 1920,
fps: 30}`. Nothing else in the canvas chain changes.

```
  New manual project -> Studio.jsx:134 -> scaffoldEditDecisions({})
        emits metadata.compose_target {1080, 1920, 30}
             |
      +------+------+
      v             v
   PREVIEW       EXPORT      both READ the document; the fallback
   1080x1920     1080x1920   branch is never entered
                             => preview == export by construction

  Legacy document -> whatever it already stored, in BOTH. Untouched.
```

Schema-safe: `schemas/artifacts/edit_decisions.schema.json:297` defines `compose_target`
with `additionalProperties: false` and **no required subfields**; `width`/`height` are
integers `minimum: 16`, `fps` is `exclusiveMinimum: 0`. 1080 and 1920 are even, satisfying
`tools/video/video_compose.py:1167-1172`. A Save cannot 422.

It reaches disk on the existing path: `web/src/studio/Studio.jsx:135` marks the scaffolded
doc dirty and the debounced autosave at `web/src/studio/Studio.jsx:247-251` persists it
through the validating `PUT` (`server/app.py:616-626` → `server/editor.py:55-65`). No new
write path.

### 4.2 Rejected alternatives

1. **Flip only `canvasOf`'s fallback.** Preview goes 9:16 while Python exports 16:9 for
   every canvas-less document. Direct RULES.md violation.
2. **Flip both fallbacks in lockstep.** Fixes the divergence but silently reinterprets
   every legacy document. Positions are stored in **canvas pixels** — `position.{x,y}`
   from the WYSIWYG drag path and the box math at `web/src/studio/model.js:301-327` — so an
   overlay authored at `x: 1500` on a 1920-wide canvas lands 420px outside a 1080-wide one,
   in the export as well as the preview, with no user action and no undo entry. It also
   changes the default for the agent's `compose` / `render` / PiP paths
   (`tools/video/video_compose.py:1226`, `:1774`, `:3413`), far outside this ticket.
3. **A canvas picker at project creation.** `POST /api/projects` (`server/app.py:355-381`)
   already carries `pipeline_type` and `style`, so it is mechanically easy — but the
   toolbar already offers all four presets one click away with 9:16 first, and the mission
   supplies the default. A creation-time question the ICP answers "9:16" almost every time
   is a tax, not a feature.
4. **Server-side scaffold at `create_project`.** More robust than depending on the editor's
   autosave, but `create_project` writes no `edit_decisions.json` today and the pipelines
   write their own; planting a document the agent must overwrite adds a race for no gain.

### 4.3 Implementation constraint on the named constants (blocking)

codex's `NEW_PROJECT_CANVAS` / `LEGACY_CANVAS` are adopted, module-private (an exported
constant with no external caller is public surface for nothing), with one hard constraint:

**`canvasOf` must keep its per-field structure.** Today it is
`Number(ct.width) || 1920` for each of width, height and fps independently
(`web/src/editor/interp.js:576-580`). `setCanvas` merges
(`web/src/editor/interp.js:562-571`), so a partial `compose_target` such as `{fps: 24}` is
reachable — and the **existing test at `web/src/editor/interp.test.js:322` pins exactly
that**: `canvasOf(setCanvas(d, {fps: 24}))` must keep `d`'s width and height. Reading "use
`LEGACY_CANVAS` when the document omits the field" as *return the object* would break that
test and change behaviour for partial targets. Name the literals; do not restructure the
function. And never return the shared constant by reference — callers would share a mutable
canvas.

### 4.4 Every canvas site, with a verdict

| Site | Verdict |
| --- | --- |
| `web/src/editor/interp.js:194` `scaffoldEditDecisions` | **CHANGE** — emit `metadata.compose_target` |
| `web/src/editor/interp.js:573-581` `canvasOf` | **KEEP** the numbers; name them (§4.3) and add a one-line comment naming `tools/video/video_compose.py:1141` as the twin that must move with it |
| `web/src/editor/interp.js:562-571` `setCanvas` | No change |
| `tools/video/video_compose.py:1141` | **KEEP** — the twin |
| `server/render_jobs.py:540-546` | No change. The missing `profile` key is why the profile rung is inert; documenting it here is enough |
| `web/src/studio/StudioPreview.jsx:108-127` | No change — the safe frame already contain-fits any canvas |
| `web/src/studio/model.js:28-33` `CANVAS_PRESETS` | No change — 9:16 already first |
| `web/src/studio/StudioToolbar.jsx:11-28` `CanvasPicker` | No change — it will now show `9:16 · 1080×1920` selected for new projects with no code edit |
| `web/src/studio/model.js:126, 127, 247, 248, 265, 302, 315, 322` | **KEEP.** Inert in production: every real caller passes `interp.canvasOf(...)` (`web/src/studio/Studio.jsx:93`, `:494`, `:504`), which never returns a falsy dimension. They fire only when `canvas` is omitted entirely, a path exercised solely by `web/src/studio/model.test.js:90`. Both original designs independently agreed to leave them. |
| `web/src/styles.css:480` `.render-item video` | **OUT of scope** — ruling B, §6 |
| `web/src/editor/Editor.jsx:40` (also scaffolds) | **Dead code — no action.** Nothing in `web/src/` imports it; in particular it is unimported by `web/src/App.jsx` and `web/src/main.jsx`. (`web/src/editor/Editor.jsx:4` imports `Inspector.jsx`, not the reverse.) Mentioned, not deleted. |

### 4.5 Compatibility and migration

There is no bulk or silent migration.

| On disk | Before | After | Why |
| --- | --- | --- | --- |
| No `edit_decisions.json` | scaffold, no canvas → 1920×1080 both sides | scaffold **with** canvas → 1080×1920 both sides | The fix. No saved canvas-space positions exist yet, so nothing can move. |
| Document, no `compose_target` | 1920×1080 both sides | unchanged | Reinterpreting it would move stored overlay coordinates |
| Explicit `1920×1080` | unchanged | unchanged | Explicit intent is never overridden |
| Explicit 1:1 / 4:5 / anything | unchanged | unchanged | Same |
| Agent-created | 1080×1920 | unchanged | The pipeline already gates it |

Changing a canvas later remains an ordinary undoable edit through `interp.setCanvas`.

### 4.6 Tests

1. **New**, in `web/src/editor/interp.test.js` beside the existing scaffold block at
   `web/src/editor/interp.test.js:97-110`: `canvasOf(scaffoldEditDecisions())` →
   `{1080, 1920, 30}`.
2. **The existing test at `web/src/editor/interp.test.js:318-323` stays verbatim.**
   `canvasOf({})` → `1920×1080@30` is now a *deliberate legacy-document contract*, not an
   accident. Add a comment above it: this pins the legacy reading and must move in lockstep
   with `tools/video/video_compose.py:1141` or preview and export diverge. Confronted, not
   deleted.
3. **New**, same file: a partial-target regression — `canvasOf(setCanvas(scaffold, {fps: 24}))`
   keeps 1080×1920 and takes fps 24. This is the guard for §4.3.
4. **New**, in `web/src/studio/model.test.js`: `CANVAS_PRESETS[0]` dimensions equal the
   scaffold's canvas, so a drift fails a test instead of silently rendering the picker's
   "custom" option.
5. **Python: none.** No Python behaviour changes.
   `tests/tools/test_compose_transitions.py:215` already proves an explicit vertical target
   exports 1080×1920. Retain it; add no redundant FFmpeg integration test.

### 4.7 Success condition

> Create a new project, open the editor, touch nothing, drop one clip, Render. The toolbar
> picker reads `9:16 · 1080×1920`; the preview safe frame is taller than it is wide;
> `ffprobe renders/final.mp4` reports **1080×1920**. Then open a project created before
> this change: its picker, safe frame and exported dimensions are byte-for-byte what they
> were before.

---

## 5. OPN-27 — @-mention project assets

### 5.1 Transport: the structured sidecar (codex's design, adopted)

```
  user types "@"
      |
      v  menu: flat, filtered, from GET /api/projects/{id}/assets
      |
      v  select (Enter / Tab / pointer-down)
  draft:     Use @assets/video/hook.mp4 for the opener
  selected:  [{token: "@assets/video/hook.mp4",
               path:  "assets/video/hook.mp4"}]
      |
      v  POST /chat {message, thread_id, model, mentions}
      |
      v  server: SHAPE check (string only) -> 422 on violation
      |          STATE check (filesystem)  -> resolve or NOT FOUND
      v
  what run_turn receives (server/app.py:974):

    Use @assets/video/hook.mp4 for the opener

    [MENTIONED PROJECT ASSETS - resolved by the server, do not
     re-derive:
     - assets/video/hook.mp4 (video)
       /Users/x/.../projects/p1/assets/video/hook.mp4]
```

**Why the sidecar beat claude's `@[path]` text token**, on the merits and not on the
`web/src/studio/Studio.jsx:255` wrapper bug (which codex had already found and planned to
fix):

- The token's server regex `@\[([^\]\n]+)\]` **cannot represent a filename containing `]`**.
  That is not exotic — every download manager produces `video[1].mp4`. RULES.md says users
  drop any media; a transport that silently degrades on a real filename class is the
  fragile choice.
- Hand-editing an inserted token silently changes the target with no validation until the
  server reports NOT FOUND. codex's prune-on-edit makes an edited token stop being a
  reference. Tighter.
- The two advantages claude claimed for the token — what the agent's own history shows, and
  behaviour on rename between turns — are washes. Both designs append the same resolution
  block to the runner message, and neither survives a rename after the turn.

**Historical messages do not carry references, and we are not making them.** An earlier
revision of this document claimed the sidecar's one weakness — that a re-sent historical
message would not re-resolve — could be recovered by persisting `mentions` on the stored
thread message, on the grounds that `ThreadSave.messages` is `list[Any]`
(`server/app.py:124-125`). codex was right to strike that: `list[Any]` proves the metadata
is **storable**, not that anything **reads** it. `loadThread`
(`web/src/chat/useAgentChat.js:79-87`) restores messages and session state and stops there;
there is no resend interaction anywhere in the chat surface, so nothing could ever
re-resolve a persisted sidecar and the proposed success condition was unverifiable.
Persisting unread metadata is scope creep. **The accepted behaviour is:** a mention resolves
for exactly the turn in which it was selected; a fresh send carries only its freshly
selected references. Reopening a thread shows the prose, which is what chat history is for.
What would change this: a separately specified history-resend interaction — at which point
persisting the sidecar becomes a requirement of *that* feature, with a testable condition.

### 5.2 Server validation: shape by root, then state (codex's design, adopted and tightened)

**Do not route mentions through `resolve_source_path`.** claude's Phase 1 design proposed
reusing it; reviewing codex's tighter alternative exposed why that is wrong.
`server/editor.py:103-112` tries candidates in this order:

```
  1. Path(projects_dir).parent / raw    <- REPO ROOT first
  2. proj / raw                         <- the project second
  3. Path(projects_dir) / raw
```

and its containment check (`server/editor.py:118`) accepts the project **or** the shared
repo asset library (`shared_root`, `server/editor.py:89`). `<repo>/assets/` exists and
contains `sfx/`, so `assets/sfx/whoosh.wav` would resolve to the **repo** file, not the
project's — silently, because candidate 1 wins. `<repo>/assets/` has no `video/`, `audio/`,
`images/` or `music/` subdirectory today, so the common case cannot collide *yet*; only the
candidate ordering is protecting it. That is a latent shadowing bug and the reason for a
narrow, purpose-built validator.

#### Where the validator lives

**In `server/app.py`, as a small endpoint-adjacent helper.** An earlier revision put it in
`server/editor.py` and called the placement a coin-flip. It is not a coin-flip: the
extension sets it must consult are owned by `server/app.py:33-35` and the eligibility rules
by `server/app.py:48-56` and `:441-496`, while `server/app.py:101` already imports
`server.editor` — so having `editor` import them back is a circular dependency. The
cycle-free alternative (hoisting the extension sets into a third module) is a wider refactor
than either ticket justifies. Keeping the validator beside the listing policy it mirrors is
also the version least likely to drift when that policy changes.

#### The predicate

The validator resolves **only** under `projects_dir / project_id`, and:

```
  SHAPE — decidable from the string alone  ->  422, runner NEVER called
  Mirrors menu eligibility EXACTLY, by root:

    assets/**                      ext in IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS
                                   (_classify, server/app.py:48-56, :33-35)
    hf/renders/**                  ext in VIDEO_EXTS   (server/app.py:496)
    renders/<name>  DIRECT CHILD   ext in VIDEO_EXTS   (server/app.py:464)

  and, for every root:
    - a non-empty string
    - not absolute
    - no ".." segment
    - no dot-prefixed segment, at ANY depth

  Anything else -> 422.

  STATE — needs a filesystem look  ->  degrade, turn proceeds
    - the file is missing
    - it is not a regular file
    - the RESOLVED real path is not inside the project
```

**Per-root, not a union.** An earlier revision used one extension union for all three roots.
codex identified that as a defect in both directions, and it is:

- `renders/` and `hf/renders/` accept **only** `VIDEO_EXTS` at the endpoint
  (`server/app.py:464`, `:496`). A union would have made a tampered `renders/evil.png` pass
  SHAPE, fall through to STATE, and be reported as a harmless `NOT FOUND` — exactly the
  mislabelling the shape/state contract exists to prevent.
- Conversely, the union was *stricter* than the endpoint on dot segments (below), so it
  would have 422'd a path the menu legitimately offered.

Either way the contract inverted. The predicate must mirror the endpoint per root, and the
two must be read side by side whenever `list_assets` changes.

#### The menu must never offer a path SHAPE rejects

`list_assets` guarantees, by construction, every SHAPE rule except one: root prefix,
per-root extension, project-relative and no `..` all follow from how it builds each entry
(§3.2). The exception is the dot-segment rule — the two `rglob` buckets check only the
**leaf** name (`server/app.py:442`, `:496`), so `assets/.tmp/clip.mp4` and
`hf/renders/.stage/a.mp4` are listable today.

**Close it on the composer side:** the flattening helper (build item 6) drops any candidate
with a dot-prefixed segment at any depth. One predicate, one test, and the invariant holds —
*every path the menu can offer is SHAPE-valid, so a legitimate selection can never 422.*

Rejected: changing `list_assets` to exclude dot-directory descendants. It would work, but
that endpoint also feeds the dashboard's 4-second asset poll and the Studio, so hiding files
there changes two surfaces outside these tickets. The client-side filter is the surgical one.

#### Symlinks

**The post-resolve containment re-check is mandatory.** `Path.resolve()` follows symlinks,
and `list_assets` tests candidates with `Path.is_file()` (`server/app.py:442`, `:464`,
`:496`), which also follows — so a symlink inside `assets/` pointing outside the project is
reachable from the menu. Re-check containment on the resolved path and treat an escape as
`NOT FOUND` — never emit the path. The boundary holds without punishing a user who did
nothing wrong.

Duplicate paths de-duplicate, preserving first-appearance order.

### 5.3 Ruling A — vanished-file handling

**Rule: shape → 422. State → degrade to `NOT FOUND`.** Written out above; the reasoning:

A **state** failure is a race we cause ourselves. `hf/renders/*.mp4` are written and
replaced by the agent during its own turns, and the composer's candidate list is a
snapshot. A user who picks an agent clip and then types a sentence is inside that window.
Refusing the turn there costs the user their message, because
`web/src/chat/useAgentChat.js:116` clears `input` **before** the request and the catch at
`web/src/chat/useAgentChat.js:182-192` appends an error line without restoring it. The user
is left looking at their own message in the transcript, an error beneath it, and an empty
composer.

A **shape** failure is impossible from the menu — the sidecar is only ever populated by
selection, never by typing, and §5.2's composer filter guarantees every offered path is
SHAPE-valid — so it means a client bug or a tampered request. Those should fail loudly,
before the runner, not be swallowed as "NOT FOUND". Silently absorbing a mangled path would
let a client regression ship undetected.

**A 422 also destroys the draft, and that is not acceptable as-is — so we fix it.**
Build item 10 restores the draft on a failed send. This is the root-cause fix at the one
shared site: it covers the new 422 *and* repairs the pre-existing case where an auth 503 at
request start already eats the user's text today. codex confirmed at ratification that this
is implementable as written — retain the already-computed `message` and, on a non-abort
failure in the existing catch path, refill `input` with it. The optimistic user bubble and
the error line stay — the transcript reads "you said X, it failed" — and the composer
refills so the user can fix and resend.

With the draft restored, the 422 is fully survivable and the shape/state split stands on
its own merits rather than on UX damage control.

### 5.4 Scope: which buckets

All three, from one `GET /api/projects/{id}/assets` call (`web/src/api.js:123`), grouped in
the menu with a heading and an icon from `web/src/components/icons.jsx` (no emoji, per
RULES.md).

| Bucket | Path shape | Why |
| --- | --- | --- |
| `kinds` (`server/app.py:438-450`) | `assets/video/x.mp4` | Literally what the ticket asks for |
| `agent_renders` (`server/app.py:492-505`) | `hf/renders/scene2.mp4` | Editable timeline building blocks |
| `renders` (`server/app.py:452-487`) | `renders/final.mp4` | "shorten this version" is a real instruction |

**Not mentionable:** `artifacts/*.json` (the agent already knows where they are — the
preamble tells it), `.mc/` (the agent's chat history: noise and a privacy smell), engine
internals under `renders/proxies/` and `renders/.final_review_frames/` (already excluded by
the endpoint, `server/app.py:458`), and the text companions surfaced by `/browse`. "Asset"
in the ticket means media.

We use `list_assets`, not `/browse`: an autocomplete needs one flat filterable list, not a
folder walk.

### 5.5 Composer behaviour

Enter currently sends (`web/src/chat/ChatPanel.jsx:140-145`). The menu is written so that
it can never be open when the user did not mean it to be.

| State | Key | Behaviour |
| --- | --- | --- |
| Menu **closed** | Enter | **Sends. Unchanged. The invariant.** |
| Menu closed | Shift+Enter | Newline. Unchanged. |
| Menu **open** | ↑ / ↓ | Move the active result; wraps |
| Menu open | Enter or Tab | Insert, add a trailing space, restore the caret, close. Does **not** send. |
| Menu open | Shift+Enter | Newline. **Never selects.** |
| Menu open | Escape | Close, keep the draft, keep focus |
| Menu open | anything else | Re-query; **zero results closes the menu** |
| Menu open | pointer | Select on **pointer-down**, without blurring the textarea first |

Opening rules, all inside the pure helper: the `@` must be at index 0 or preceded by
whitespace (so `someone@example.com` never opens a menu); the query is the run of
non-whitespace characters from `@` to the caret; zero results ⇒ closed, so Enter is never
dead. Matching is case-insensitive against both basename and project-relative path.

Filenames with spaces are fine: the sidecar carries the authoritative path, so nothing
reparses visible prose. The query itself cannot span a space — reach such a file by a
prefix or any distinctive substring. Stated ceiling, not engineered around.

#### Result ranking (added post-ratification — see §12)

Matching on the path as well as the basename means `@video` matches every file under
`assets/video/` by path, not only files with "video" in the name. Without an order that is
a long alphabetical list in which the intended file is not near the top. Rank the matches:

```
  q    = query, lowercased, non-empty
  name = basename, lowercased
  rel  = project-relative path, lowercased

  tier 0   name === q              exact basename
  tier 1   name.startsWith(q)      basename prefix
  tier 2   name.includes(q)        basename substring
  tier 3   rel.includes(q)         path only — the name does not match

  first tier that applies wins; ties keep the existing flattened order
```

**The invariant that makes this safe: ranking REORDERS, it never FILTERS.** A candidate is
offered iff it reaches a tier, and `tier0 | tier1 | tier2 | tier3` is exactly
`name.includes(q) || rel.includes(q)` — the pre-existing match rule, unchanged. So the
result *set* is provably identical, "zero results closes the menu" still holds, Enter is
still never dead, and no existing test changes. Any implementation that changes the set has
a bug.

The proof, as codex put it at ratification: tiers 0-2 are *exactly* `name.includes(q)` —
exact ⊆ prefix ⊆ substring, and every substring match lands in 0, 1 or 2 by first-match — and
tier 3 is `rel.includes(q) && !name.includes(q)` by construction. Their union is therefore
the existing predicate. **This does not depend on `rel` containing `name`**; the partition
holds either way. (It happens to be true — `list_assets` derives both from the same file at
`server/app.py:441-450`, `:463-485`, `:495-505` — but nothing here rests on it.)

Ties break on the existing flattened order (`list_assets` sorts within each bucket, and the
helper concatenates buckets in a fixed order), so the sort must be **stable** and the output
fully deterministic — which is what makes it unit-testable.

An empty query (a bare `@`) offers every candidate in flattened order; the tiers do not
apply, because an exact-match test against an empty string is meaningless.

**No result cap.** Ranking makes a "top N" tempting, but a cap silently hides assets the
user owns, and there is no evidence yet about real project sizes. Revisit only with a
project big enough to make the menu unusable, and then show the count that was elided.

Accessibility: `role="listbox"` / `role="option"`; the textarea exposes `aria-expanded`,
`aria-controls`, `aria-activedescendant`. Anchored to the composer, not to per-character
textarea geometry. This is a small autocomplete, not a rich-text editor.

The candidate list is fetched when the project changes and refreshed when `@` opens, showing
the cached list while a refresh is in flight, so assets created during an agent turn appear
without the composer polling. A busy or disabled chat neither opens the menu nor sends.

### 5.6 Rejected alternatives

1. **`@filename` only.** Basenames collide across `assets/`, `hf/renders/` and `renders/`,
   and the code-root cwd cannot resolve a bare name.
2. **A raw relative path, resolved by the agent.** No containment, and unreliable past turn
   one — the absolute-path instruction is first-turn only
   (`server/agent_runner.py:1242-1252`, composed at `server/agent_runner.py:1946`).
3. **claude's `@[path]` text token.** Withdrawn — §5.1.
4. **Contenteditable chips.** Replaces a textarea that owns Enter-to-send, IME composition,
   autosize and `disabled` handling. Large diff, new bug surface, identical string reaches
   the agent.
5. **Manifest `asset_id` instead of a path.** Uploads land on disk directly
   (`server/app.py:383-419`) without a manifest entry, so ids cover a subset of what the
   menu lists.
6. **`/browse` + arbitrary text files.** The three-bucket contract is already the bounded
   media scope the ticket names.
7. **Only `kinds`.** Agent clips are editable building blocks and finals are legitimate
   revision references.
8. **Persisting mention metadata on stored thread messages.** Nothing reads it — §5.1.
9. **A single extension union across all three roots.** Inverts the shape/state contract in
   both directions — §5.2.
10. **Changing `list_assets` to hide dot-directory descendants.** Correct but it moves two
    surfaces outside these tickets — §5.2.

### 5.7 Compatibility

- A request with no `mentions` is byte-for-byte what it is today. The validator returns the
  **same message object** when the list is empty — it must not touch the filesystem on the
  no-mention path, which is every existing turn.
- Chat history displays the user's prose. The resolution block is execution context, never
  UI text, and never lands in a stored thread file — so no absolute home-directory path is
  persisted, which matters for a public repo.
- `ChatRequest` gains an optional field with a default, so an older client is unaffected.

### 5.8 Tests

**Pure (`web/src/chat/mentions.test.js`)** — trigger boundaries (index 0, after whitespace,
`a@b.com` returns nothing, caret before the `@`); caret-in-the-middle replacement; spaces in
names; duplicate basenames across buckets; all three buckets flattened and labelled;
**a dot-directory descendant (`assets/.tmp/clip.mp4`, `hf/renders/.stage/a.mp4`) is dropped
by the flattening helper, so the menu cannot offer it**; deleted-token pruning, **including
the substring case** (`@renders/final.mp4` vs `@hf/renders/final.mp4` — safe only because
the `@` anchors it, so pin it rather than rely on the accident).

**Ranking (§5.5)** — each tier outranks the next: an exact basename beats a prefix, a prefix
beats a substring, and any basename match beats a path-only match (`@video` puts
`assets/video/product-video.mp4` above `assets/video/b-roll.mp4`); ties preserve the
flattened order, so the sort is stable and the output deterministic; an empty query returns
every candidate in flattened order.

**The set-preservation property test** (specified by codex at ratification; an earlier draft
said "for a sample of queries, the ranked result SET equals the unranked match set", which is
too weak on two counts — a mathematical set erases multiplicity, so a duplicated output would
pass, and "a sample" is not a reproducible corpus):

- **Corpus, deterministic and enumerated:** the empty query, a sentinel that matches nothing,
  and *every non-empty substring* of each fixture candidate's lowercased `name` and `path`.
- **Assertion, per query:** the ranked output has the **same length** and the **same path
  multiset** as `candidates.filter(c => name.includes(q) || path.includes(q))`.
- **No-cap coverage:** include **more than a conventional menu cap's worth** of matching
  fixtures (say 25) and assert every one is returned.

That is verifiable without pulling in a property-testing library. The tier-precedence and
stable-tie tests above remain necessary — this one only guards set preservation and the
absence of a cap.

**Component (`web/src/chat/ChatPanel.test.jsx`)** — listbox semantics; pointer selection;
arrow wrapping; Enter/Tab select without sending; Shift+Enter newline; Escape close; exact
caret restoration; **with no menu open, Enter still calls `chat.send`** (the regression
guard for the existing contract, and the single most important test in this ticket); a
zero-result query renders no listbox and Enter sends; submit passes only references whose
tokens remain in the draft.

**Backend (`tests/contracts/test_server_agent_api.py`)** — a valid reference from each of
the three buckets reaches the runner as a canonical absolute path; duplicates de-duplicate;
a no-mention message reaches the runner unchanged; every SHAPE violation returns 422 and
**asserts the runner was not called**, specifically including:

- an absolute path, a `..` segment, and a root outside `assets/` `hf/renders/` `renders/`;
- **a non-video under `hf/renders/`** (e.g. `hf/renders/thumb.png`) — the per-root rule, the
  case a single union would have wrongly downgraded to a state failure;
- a non-direct child of `renders/` (e.g. `renders/proxies/x.mp4`);
- **a dot-directory descendant** (e.g. `assets/.tmp/clip.mp4`).

And, degrading with the turn proceeding and no absolute path emitted: a missing file, a
directory, and a symlink resolving outside the project.

**Draft restore (`web/src/chat/ChatPanel.test.jsx` or a `useAgentChat` test)** — a send that
rejects leaves the typed text back in the composer.

Run `scripts/dev test fast` per increment, then `scripts/dev test full` and
`scripts/dev smoke` before review.

### 5.9 Success condition

> In a project with an uploaded video, an `hf/renders/` clip and a `renders/final.mp4`:
> type `@` — a menu lists all three, grouped. Type two letters, press Enter — the token is
> inserted and **nothing is sent**. Press Enter again — the turn sends, and the agent's next
> tool call reads that exact file without asking where it is. Press Escape on an open menu,
> then Enter: the message sends, proving the pre-existing Enter behaviour survived. Delete a
> file between opening the menu and sending: the turn still runs and the agent says it
> cannot find that file. Send with a tampered `mentions` path: 422, and the agent never ran.
> Trigger any send failure: the typed text is back in the composer.

---

## 6. Ruling B — the render card is OUT of scope

`web/src/styles.css:480` — `.render-item video { width: 100%; aspect-ratio: 16 / 9;
object-fit: contain; ... }`, consumed by `web/src/App.jsx:1328-1342`. It is the only
`aspect-ratio` declaration in the stylesheet. **It is not changed by OPN-39.** codex did not
contest this at ratification and accepted the Phase 5b handoff below.

### Why out

1. **It is `docs/plans/ui-polish-audit/agreed-ui-polish-plan.md:286` — Phase 5b item 4**,
   already ratified, already scheduled, already owned. The coordinator's brief excluded
   Phase 5b explicitly.
2. **It is a different surface.** `.render-item` lives in `AssetPanel` on the agent
   dashboard (`web/src/App.jsx:1280`, `:1328`), not in the editor. OPN-39 says "the
   editor's canvas".
3. **OPN-39 does not create the defect.** Every shipped pipeline already renders 1080×1920
   (`pipeline_defs/instagram-fast-reel.yaml:192`, `:251`), so a 9:16 render already displays
   in a 16:9 card today for the dominant, agent-driven path. OPN-39 extends the mismatch to
   manual projects; it does not introduce it.
4. **Doing it correctly is not one line**, because both candidate sources are wrong in a
   different way and both of us were half right:
   - **Deriving from the project canvas** (codex) is wrong for a **stale** render. The
     `renders` bucket explicitly carries earlier renders and a `current` flag
     (`server/app.py:481-485`), so a 16:9 render made before the user switched to 9:16 gets
     a 9:16 box and is letterboxed. codex's own mitigation sentence — keep `object-fit:
     contain` so a different intrinsic ratio is not cropped — concedes the failure mode.
     Its absent-document branch also *invents* a ratio.
   - **Deleting `aspect-ratio`** (claude) is wrong on layout stability. A `<video>` with no
     ratio and unloaded metadata has an intrinsic size of 300×150, so the card renders at
     2:1 and then jumps to the true ratio — in a ~320px panel, a ~409px jump for a 9:16
     render. That is precisely what Phase 3 of the ratified plan ("stop the layout jumps")
     exists to prevent. claude did not weigh this; codex was right to.
   The honest authority is the render's **own intrinsic dimensions**. Getting them without
   a shift needs a real decision, which is what Phase 5b item 4 is for.

### What the user sees in the meantime, and is that shippable

Stated plainly, because it is not pretty. In a ~320px Assets column, a 1080×1920 render
inside `aspect-ratio: 16/9; object-fit: contain` renders as roughly a 101×180 vertical strip
inside a 320×180 mostly-black box — about 18% of the card's area. For a vertical-first
product, at the "first watermark-free export" moment, that is bad.

**It is nonetheless shippable**, for three reasons: it is exactly what ships today for every
agent-made reel, so OPN-39 does not degrade anyone's current experience; the same card has a
full-screen button (`web/src/App.jsx:1344`) that shows the render correctly; and it is item
4 of a phase that already exists with an owner. **Recommendation: pull Phase 5b item 4 into
the increment immediately after OPN-39.** It should not wait for the rest of 5b.

### Handoff to Phase 5b item 4 — so it is not re-derived

Three corrections and one recommendation, from the analysis above:

- The plan's anchor `styles.css:334` has **drifted**; the rule is at `web/src/styles.css:480`.
- The plan says "derive from the project canvas". That is wrong for stale renders
  (`server/app.py:481-485`).
- A bare deletion costs a 300×150 → intrinsic layout shift.
- **Recommended shape:** the project canvas as the *initial reservation* only, with the
  video's intrinsic dimensions as the *authority* — `onLoadedMetadata` sets
  `style.aspectRatio` from `videoWidth/videoHeight`. Zero shift in the common case (current
  render, current canvas), self-correcting for a stale one. Cost to weigh: the reservation
  needs the document, and `AssetPanel` polls `listAssets` every 4s
  (`web/src/App.jsx:1288-1295`), so a second fetch on that tick is the price. A cheaper
  alternative worth costing first: extend the render **receipt**
  (`lib/project.py:619-621`, currently `{doc_hash, video_size, video_mtime_ns}`) with the
  canvas it was rendered at, and surface it on the `renders` entry. That is free —
  `receipt_doc` is already in hand — but it covers only a receipted `final.mp4`, not
  earlier renders.

---

## 7. Build items, in dependency order

| # | Ticket | Item | File(s) | Verify |
| --- | --- | --- | --- | --- |
| 1 | 39 | Name `NEW_PROJECT_CANVAS` / `LEGACY_CANVAS` module-private; keep `canvasOf` per-field (§4.3); add the `tools/video/video_compose.py:1141` cross-reference comment | `web/src/editor/interp.js` | Existing suite green, incl. `web/src/editor/interp.test.js:318-323` unmodified |
| 2 | 39 | `scaffoldEditDecisions` writes `metadata.compose_target` | `web/src/editor/interp.js` | New test: `canvasOf(scaffoldEditDecisions())` = `{1080,1920,30}` |
| 3 | 39 | Pin both semantics separately + the partial-target regression + the comment on the legacy test | `web/src/editor/interp.test.js` | 3 tests pass; the legacy assertion is unchanged |
| 4 | 39 | Preset-drift guard | `web/src/studio/model.test.js` | `CANVAS_PRESETS[0]` matches the scaffold canvas |
| 5 | 39 | Manual: new project → picker reads 9:16, `ffprobe` says 1080×1920; legacy project unchanged | — | §4.7 |
| 6 | 27 | Pure helpers: flatten+label buckets **and drop any candidate with a dot-prefixed segment** (§5.2); find the `@query` range at the caret; **rank matches into the four tiers, stably (§5.5)**; replace and return caret + structured mention | `web/src/chat/mentions.js` (new) | `web/src/chat/mentions.test.js` matrix (§5.8), incl. the dot-directory case and the ranking set-equality property |
| 7 | 27 | Return `projectId` from the hook | `web/src/chat/useAgentChat.js:286-293` | ChatPanel reads it at both call sites with no JSX edit |
| 8 | 27 | Menu UI, keyboard contract, listbox a11y, candidate fetch/refresh, selected-ref pruning | `web/src/chat/ChatPanel.jsx`, `web/src/styles.css` (`.composer` gets `position: relative`; new `.mention-*` block, chat namespace, no `st-` prefix) | `web/src/chat/ChatPanel.test.jsx` (§5.8) — the closed-menu-Enter-sends guard first |
| 9 | 27 | `send(text, mentions)`; `chatStream` serializes the sidecar; **forward all args through the Studio autosave wrapper** | `web/src/chat/useAgentChat.js`, `web/src/api.js:226-232`, `web/src/studio/Studio.jsx:255-256` | A wrapper test proving mentions survive the pre-agent flush |
| 10 | 27 | Restore the draft on a failed send (§5.3) | `web/src/chat/useAgentChat.js:182-192` | A rejected send leaves the text in the composer |
| 11 | 27 | `ChatRequest.mentions`; the per-root SHAPE/STATE validator as an endpoint-adjacent helper (§5.2/§5.3); enrich the message at `server/app.py:974` | `server/app.py` | `tests/contracts/test_server_agent_api.py` (§5.8), incl. runner-not-called on every SHAPE 422 |
| 12 | 27 | Manual: the §5.9 walkthrough end to end | — | §5.9 |
| 13 | — | `scripts/dev test fast`, then `scripts/dev test full` and `scripts/dev smoke`; `scripts/dev stop` before marking complete | — | A failure in any existing canvas, editor-save, chat or agent-API contract blocks review |

Items 1–5 and 6–12 are independent; they share no file and can run in parallel or in either
order.

**Sequencing against the ratified UI-polish plan.** Item 8 touches `.composer`
(`web/src/styles.css:211`), which Phase 7 item 10 also touches (`align-self` on the composer
action), and Phase 6 item 20 adds an accessible name to the composer textarea. Land OPN-27
first and rebase 6/7 onto it — a one-property change rebases onto a structural one far more
cheaply than the reverse. Phase 5b item 4 is neither blocked by nor blocking either ticket
(§6).

---

## 8. Risk register

| Risk | Mitigation | Proof |
| --- | --- | --- |
| **The two canvas literals drift apart.** `web/src/editor/interp.js:577` and `tools/video/video_compose.py:1141` are hand-maintained copies. OPN-39 points a spotlight at exactly one of them: the next engineer reads "make the app 9:16", edits `canvasOf`, sees `web/src/editor/interp.test.js:318-323` fail, updates the test, and ships a silent preview/export divergence for every canvas-less project. The inert `profile` rung means there is not even an intermediate signal. **This is the single biggest risk in either ticket.** | Named constants (item 1), the cross-reference comment, and the explanatory comment on the legacy test. All three, cheaply. The durable fix — make the renderer the single source and have the frontend fetch it — is a separate ticket. | Items 1 and 3 |
| Naming the constants restructures `canvasOf` into a whole-object fallback | Keep the per-field `||` (§4.3) | Item 3's partial-target test |
| Changing a fallback moves legacy overlay coordinates | Change neither; write 9:16 only in the fresh scaffold | Item 3 pins both branches |
| Enter selects when the user meant to send | Intercept only with an active result; zero results closes the menu | Item 8's closed-menu guard |
| The Studio autosave wrapper drops mention metadata | Forward all arguments after the flush | Item 9's wrapper test |
| A crafted path escapes the project | Per-root SHAPE checks before any filesystem touch; project-only resolution; **post-resolve** containment re-check for symlinks | Item 11's rejection tests assert zero runner calls |
| **The menu offers a path SHAPE rejects, 422-ing a legitimate selection** | The flattening helper drops dot-segment candidates; everything else `list_assets` guarantees by construction (§3.2, §5.2) | Item 6's dot-directory test + item 11's server-side counterpart |
| **A tampered non-video under `renders/` or `hf/renders/` is mislabelled a state failure** | Per-root extension rules, not a union | Item 11's `hf/renders/thumb.png` test |
| The SHAPE predicate and `list_assets` drift apart | Both live in `server/app.py` (`:33-56`, `:441-496`, and the validator) and must be read together | Item 11's per-bucket happy-path tests |
| A mention shadows a same-named repo asset | Purpose-built validator, never `resolve_source_path` (§5.2) | Item 11 |
| An asset disappears between menu load and send | Degrade to NOT FOUND; the turn proceeds | Item 11's vanished-file test |
| A 4xx destroys the user's typed message | Restore the draft on a failed send (§5.3) | Item 10 |
| The validator runs on every turn and adds a filesystem walk | Empty `mentions` returns the same message object and touches no disk | Item 11's no-mention test |
| A visible mention maps to the wrong same-named file | The sidecar carries the exact path; the menu shows path + bucket as secondary text | Item 6's duplicate-basename test |

---

## 9. Not building, and what would change our minds

| Not building | Reason | What would change it |
| --- | --- | --- |
| Any change to either canvas fallback literal | Changing one breaks preview==export; changing both reinterprets saved coordinates | Nothing short of a real migration |
| Forced conversion of legacy canvas-less projects | Their overlay positions were authored in 1920×1080 canvas space; silent reinterpretation is data corruption wearing a feature's clothes | An **explicit**, undoable in-editor prompt ("this project has no canvas set — set it to 9:16?") writing through `interp.setCanvas`. Separate ticket. **This is the one open product question in this document.** |
| A project-creation canvas step | The toolbar already offers all four presets, 9:16 first | A landscape/YouTube pipeline, or research finding a material non-vertical cohort |
| Removing 1:1 / 16:9 / 4:5 | The editor intentionally supports them; the product is vertical-**first**, not vertical-only | A product decision to become vertical-only |
| The render-card ratio | Phase 5b item 4; both candidate sources have a real defect (§6) | Nothing — it is scheduled. Pull it into the next increment with the §6 handoff. |
| **Persisted mention metadata on stored thread messages** | Nothing reads it: `loadThread` (`web/src/chat/useAgentChat.js:79-87`) has no resend path, so the success condition is unverifiable (§5.1) | A separately specified history-resend interaction — persistence then becomes a requirement of that feature, with a testable condition |
| Changing `list_assets` to hide dot-directory descendants | It feeds the dashboard poll and the Studio; the composer-side filter is surgical (§5.2) | A second consumer needing the same exclusion |
| Rich chips, asset-to-chat drag, interactive history pills | Contenteditable/selection/clipboard scope is not needed for resolvable mentions | Measured comprehension or editing failures with plain path tokens |
| `#`-mention for timeline clips or scenes | Plausible next step, not this ticket | A user asking for it |
| Mentioning artifacts, `.mc/`, proxies, or arbitrary files | Engine internals or beyond the project's media boundary | A separate, permissioned artifact-reference feature |
| Automatic timeline mutation from a mention | The surrounding sentence, interpreted by the agent, supplies intent | A separately specified deterministic quick action |
| A cap on the number of mentions | claude's Phase 1 design had one; dedupe covers the realistic case | Evidence of prompt-size damage |
| Re-laying-out the stage for a tall frame | `.st-stage` has `min-width: 360px` (`web/src/styles.css:1101`), so a 9:16 frame is height-bound and leaves horizontal slack on a wide window. Real, but it is Phase 4 layout work | Phase 4 |

**Observed adjacent defect, deliberately not fixed:** merely *opening* the editor on a fresh
project autosaves a document containing the scaffold's placeholder cut
`{id: 'c1', source: 'clip.mp4'}` — a file that does not exist
(`web/src/studio/Studio.jsx:134-135` → `:247-251`). Pre-existing. OPN-39 makes that
persisted scaffold strictly *more* correct (it now also states the canvas), but the fake cut
deserves its own ticket.

---

## 10. Corrections carried forward

Recorded so the drift and the misreadings do not propagate.

**Line-number drift**

| Cited as | Actual | Where it appeared |
| --- | --- | --- |
| `styles.css:334` (render-card ratio) | **`web/src/styles.css:480`** | `docs/plans/ui-polish-audit/agreed-ui-polish-plan.md:286` |
| `server/agent_runner.py:684` (agent cwd) | **`server/agent_runner.py:685`** — `:684` is `return ClaudeAgentOptions(` | `codex/design.md` |
| `schema:294` (compose_target) | **`schemas/artifacts/edit_decisions.schema.json:297`** — `:294` is the `"metadata"` key. `:299` (the precedence description) was cited correctly | `codex/design.md` |
| `server/app.py:912` (enrichment site) | **`server/app.py:974`** — `:912` is the `/chat` decorator; `:974` is `await runner.run_turn(...)`, the line an implementer edits | `codex/design.md` |
| `interp.js:193` | `:193` is the JSDoc line; the signature is `web/src/editor/interp.js:194`. Both fine as anchors | both designs |

**Factual corrections (not line drift)**

| Claimed | Actual | Where it appeared |
| --- | --- | --- |
| "`Editor.jsx`'s only referrer is its sibling `Inspector.jsx`" | The reverse: `web/src/editor/Editor.jsx:4` imports `Inspector.jsx`, and **nothing** in `web/src/` imports `Editor.jsx`. The original claim came from a grep whose pattern also matched `KeyframeEditor.jsx` | `claude/design.md`, and revision 1 of this document (§4.4) |
| "Persisting `mentions` recovers re-resolution for a re-sent thread message" | `list[Any]` proves it is storable, not that anything reads it; `loadThread` has no resend path | revision 1 of this document (§5.1) |
| "Validator placement is a coin-flip between `server/app.py` and `server/editor.py`" | Not a coin-flip: `server/app.py:101` already imports `server.editor`, and the extension sets live in `server/app.py:33-35`, so the `editor` placement is a cycle | revision 1 of this document (§5.2) |
| "SHAPE accepts `IMAGE_EXTS \| VIDEO_EXTS \| AUDIO_EXTS` under every root" | Per-root: `renders/` and `hf/renders/` accept only `VIDEO_EXTS` (`server/app.py:464`, `:496`) | revision 1 of this document (§5.2) |

No fabricated references were found in either design.
`tests/contracts/test_server_agent_api.py` exists.

---

## 11. Ratification record

| Amendment (codex, `docs/plans/opn-39-opn-27/codex/ratification.md:45`) | Disposition | Applied in |
| --- | --- | --- |
| 1 — delete the persisted-mentions claim and build item 11; renumber | **Accepted, not contested.** Verified `web/src/chat/useAgentChat.js:79-87`: `loadThread` restores messages + session state only, and no resend action exists anywhere in the chat surface | §5.1 (rewritten), §5.6 item 8, §7 (item 11 deleted, 12→11, 13→12, 14→13), §9, §10 |
| 2 — move the validator to `server/app.py`; drop the coin-flip framing | **Accepted, not contested.** Verified `server/app.py:101` (`from server import editor as editor_mod`) and the extension sets at `server/app.py:33-35`. The `editor` placement is a genuine cycle, not a preference | §5.2 ("Where the validator lives"), §7 item 11, §1 |
| 3 — per-root SHAPE eligibility; make the menu never offer a SHAPE-rejected path; add two tests | **Accepted, not contested — treated as a defect in claude's ruling.** Verified `server/app.py:464` and `:496` accept only `VIDEO_EXTS`, and `:442`/`:496` filter only the **leaf** name while `:441`/`:495` walk recursively | §3.2 (new eligibility table), §5.2 (predicate rewritten), §5.6 items 9-10, §5.8, §7 items 6 and 11, §8 (two new rows) |
| 4 — fix the `Editor.jsx` / `Inspector.jsx` relationship | **Accepted, not contested.** Verified `web/src/editor/Editor.jsx:4` imports `Inspector.jsx`, and a definitive grep finds no importer of `Editor.jsx` anywhere in `web/src/` | §4.4, §10 |

Nothing in codex's verdict was contested. All 70 distinct `path:line` citations in this
document were range-checked against the tree at ratification, and shorthand basenames were
normalised to full repo-relative paths so every citation is directly openable.

---

## 12. Post-ratification changes

Changes made after codex's ratification. Listed separately so the ratified baseline stays
legible and a reviewer can see exactly what has not been through the loop.

| # | Change | Origin | Ratified? |
| --- | --- | --- | --- |
| 1 | `useAgentChat.js:117` → `:116` for `setInput('')` (two citations). `:117` is `setMessages`; the off-by-one predates ratification and survived the citation sweep because the range check only verified the line exists | coordinator, during the `architecture.md` citation sweep | n/a — factual correction |
| 2 | **Result ranking in §5.5**, its tests in §5.8, and the extension of build item 6 | user request, 2026-08-04 | **YES — RATIFIED WITH AMENDMENTS** by codex (`codex/ratification.md:78-123`); the amendment is applied |

**On change 2.** The plan already said matching was case-insensitive against basename *and*
project-relative path, but said nothing about order. Because the path contains the bucket
directory names (`images/ video/ audio/ music/`), a query like `@video` matches every file
under `assets/video/` on the path alone — so the unordered list buries the file the user
meant. Ranking is additive, confined to the pure helper, changes no interaction and no
server behaviour.

**Codex's rev-2 verdict (`codex/ratification.md:78`): RATIFIED WITH AMENDMENTS.** It
independently reproved the tier partition, confirmed the `:116` correction, and confirmed the
stable-sort, empty-query and no-cap rules as correctly specified. On the two open questions it
was asked to attack:

- **Basename-only matching: rejected, and it argued the case rather than deferring.** Matching
  the path lets `@video`, `@music` or any directory fragment find an asset when the user knows
  the bucket but not the filename. Ranking refines that rule instead of discarding it.
- **Scope: in scope for OPN-27.** A pure-helper-only refinement that fixes a real
  discoverability failure and touches neither the sidecar nor the server.

**Its one amendment, applied in §5.8:** the property test was too weak. "The ranked SET equals
the unranked set" erases multiplicity, so a duplicated result would pass, and "a sample of
queries" is not a reproducible corpus. It now specifies an enumerated corpus, asserts equal
**length and path multiset**, and includes >25 matching fixtures to prove no cap crept in.
