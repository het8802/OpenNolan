# OPN-39 (editor canvas 9:16) + OPN-27 (@-mention project assets) — design

**Status: PLAN** · author: `claude` · verified against `ae57d47` on branch `ui-polishing`

Every `file:line` below was opened and read while writing this. Where the ratified
UI-polish plan cites a line that has since drifted, I say so and give the current one.

---

## Decisions in one line each

- **OPN-39** — Do **not** change any fallback. Make the canvas **explicit in the doc at
  creation**: `scaffoldEditDecisions` emits `metadata.compose_target = {1080, 1920, 30}`.
  Everything downstream already derives from the doc.
- **OPN-27** — The composer inserts a literal `@[project/relative/path]` token; the
  **server** expands it into verified absolute paths appended to the prompt, reusing
  `resolve_source_path`. No new request field, no change to `send()`'s signature.

Combined production diff: **2 JS files, 1 Python file, 1 CSS block, 4 test files.**

---

# Part 1 — OPN-39: the editor canvas

## 1.1 What "the current 16:9" actually is

There is no hardcoded 16:9 canvas anywhere in the editor. The preview safe frame
(`web/src/studio/StudioPreview.jsx:108-127`) contain-fits `canvas.width/canvas.height`
via a `ResizeObserver` and is completely ratio-agnostic. `CANVAS_PRESETS`
(`web/src/studio/model.js:28-33`) already lists **9:16 · 1080×1920 first**.

The 16:9 the ticket is complaining about is a **fallback that fires because nothing ever
writes a canvas**:

- `web/src/editor/interp.js:573-581` — `canvasOf(doc)` returns `1920×1080@30` when
  `metadata.compose_target` is absent.
- `web/src/editor/interp.js:193-203` — `scaffoldEditDecisions()`, the doc a brand-new
  manual project starts from, emits `version`, `render_runtime`, `renderer_family` and
  one placeholder cut. **It never emits `metadata`.**
- `web/src/studio/Studio.jsx:134` — that scaffold is what the editor loads when the
  project has no `edit_decisions.json` yet (`server/editor.py:43-52` returns `None`,
  which is a normal state).

So a fresh manual project opens with no canvas, `canvasOf` fills in 1920×1080, and the
toolbar's `CanvasPicker` (`web/src/studio/StudioToolbar.jsx:11-28`) faithfully renders
`16:9 · 1920×1080` as the selected option. The user reads that as "the app is 16:9".

**The agent path is already vertical.** All three shipped pipelines write the canvas
themselves: `skills/pipelines/instagram-fast-reel/edit-director.md:53`,
`skills/pipelines/anthropic-style-animated-talking-head/edit-director.md:32`,
`skills/pipelines/instagram-reels-studio/edit-director.md:57` — all
`{width: 1080, height: 1920}`. The gap is exactly and only the **manual, editor-first
project**.

## 1.2 The renderer investigation the brief demanded

The `interp.js:573` comment claims it "Mirrors the renderer's fallback (1920x1080@30)."
**I verified this. It is still true, and it is true for a slightly scarier reason than
the comment implies.**

`tools/video/video_compose.py:1131-1174` — `_resolve_canvas(edit_decisions, profile_name)`:

- `video_compose.py:1141` — `target_w, target_h, target_fps = 1920, 1080, 30.0`
- then a **profile** rung (`video_compose.py:1142-1148`) can override it
- then `metadata.compose_target` overrides everything (`video_compose.py:1149-1173`),
  with validation: positive, numeric, **even** dimensions.

The middle rung is dead on the editor path. `server/render_jobs.py:540-546` builds
`exec_inputs` for `operation: "render_proxies"` with `edit_decisions`,
`asset_manifest`, `output_path`, `proxies_dir` — and **no `profile` key**.
`video_compose.py:1767` reads `inputs.get("profile")`, gets `None`, so
`_resolve_canvas` goes straight from nothing to the 1920×1080 literal.

```
        WHERE THE CANVAS COMES FROM (today, editor Render button)

  artifacts/edit_decisions.json
      metadata.compose_target
             |
    +--------+--------+
    |                 |
    v                 v
  PREVIEW           EXPORT
  interp.canvasOf   video_compose._resolve_canvas
  interp.js:577-9   video_compose.py:1149-1173
    |                 |
   absent?           absent?
    |                 |  profile rung: render_jobs.py:540-546
    |                 |  sends no "profile" => skipped
    v                 v
  1920x1080@30      1920x1080@30      <-- the two numbers agree today
  interp.js:577     video_compose.py:1141
```

Two independently maintained literals that happen to be equal. That is the whole
preview==export guarantee for a canvas-less doc, and it is one careless edit from
breaking.

## 1.3 The decision

**Write the canvas into the doc at creation. Leave both fallbacks alone.**

`scaffoldEditDecisions` gains a `metadata.compose_target` of `{width: 1080,
height: 1920, fps: 30}`. That is the entire behavioural change.

```
  CHOSEN: state the canvas, never lean on a fallback

  New manual project -> Studio.jsx:134 -> scaffoldEditDecisions({})
        emits metadata.compose_target {1080, 1920, 30}
             |
      +------+------+
      v             v
   PREVIEW       EXPORT        both READ the doc; the fallback
   1080x1920     1080x1920     branch is never entered
                               => preview == export by construction

  Existing project -> whatever it already stored, in BOTH. Untouched.
```

Why this is the right rung:

1. **It fixes the actual defect.** The defect is "a new project does not declare its
   canvas", not "the fallback number is wrong".
2. **It cannot break preview==export.** It removes reliance on the two-literals
   coincidence for every project created after it lands, instead of adding a third
   place the coincidence has to hold.
3. **It is schema-safe.** `schemas/artifacts/edit_decisions.schema.json` defines
   `metadata.compose_target` with `additionalProperties: false` and **no required
   subfields**; `width`/`height` are integers `minimum: 16`, `fps` is
   `exclusiveMinimum: 0`. 1080×1920@30 passes, and both dims are even, satisfying the
   renderer's yuv420p gate at `video_compose.py:1167-1172`. Save cannot 422.
4. **It reaches disk on the existing path.** `Studio.jsx:135` sets `dirty` when there
   was no content, and the debounced autosave at `Studio.jsx:247-251` persists it ~700ms
   later through the validating `PUT` (`server/app.py:616-627` →
   `server/editor.py:55-65`). No new write path.

### Alternatives rejected

**(a) Change `canvasOf`'s fallback to 1080×1920.** This is what the ticket literally
asks for and it is the one option that **directly violates RULES.md**:

```
  OPTION (a): flip only the frontend fallback

  legacy doc, no compose_target
        |
        +--> PREVIEW  1080x1920   (9:16 safe frame, overlays remapped)
        +--> EXPORT   1920x1080   (16:9 mp4, video_compose.py:1141)
                                   ^ preview != export. Not shippable.
```

Flipping **both** literals in lockstep fixes the divergence but is worse on three counts:
`_resolve_canvas` is shared by the agent's `compose` / `render` / PiP paths
(`video_compose.py:1226`, `:1774`, `:3413`), so it changes the default for the whole
production pipeline, not the editor; it silently reinterprets every legacy doc (see 1.5);
and it is a bigger diff that delivers nothing the scaffold does not already deliver for
new projects. Rejected.

**(b) A canvas picker at project creation.** `POST /api/projects`
(`server/app.py:355-381`) already carries `pipeline_type` and `style`; a third field is
mechanically easy. Rejected as scope creep: the toolbar already has a canvas picker one
click away with 9:16 listed first, and a creation-time question the ICP will answer
"9:16" 99% of the time is a dialog tax, not a feature. Revisit if we ever ship a
landscape/YouTube pipeline.

**(c) Server-side scaffold at `create_project`.** More robust than depending on the
editor's autosave, but `create_project` writes no `edit_decisions.json` today and the
agent pipelines write their own. Planting a doc the agent must then overwrite adds a
race for zero benefit. Rejected.

## 1.4 Does this contradict the ratified plan?

**No — it is the same principle applied one layer earlier.**

`docs/plans/ui-polish-audit/agreed-ui-polish-plan.md:286` (Phase 5b item 4) says the
render card must derive its aspect from the project canvas and that *"hardcoding 9:16 is
equally wrong"* because `CANVAS_PRESETS` offers four ratios. That is a statement about
**never baking a ratio into a display surface**. My design bakes nothing into a display
surface. It writes a *default value into a document*, which the user can change in one
click, and which every surface continues to derive from. A default is not a hardcode.

Two notes on that plan row:

- **Its anchor has drifted.** It cites `styles.css:334`; the P0–P3 commit moved things.
  The rule today is `web/src/styles.css:480` —
  `.render-item video { width: 100%; aspect-ratio: 16 / 9; object-fit: contain; ... }`,
  consumed by `web/src/App.jsx:1328-1342`. It is the only `aspect-ratio` declaration in
  the stylesheet.
- **I recommend amending its fix, and I am deliberately not doing it here.** The element
  is a real `<video>` with a real file (`App.jsx:1341-1342`), so the browser already
  knows the true ratio. Deriving from the *project canvas* would mis-frame a **stale**
  render made at a different canvas — and `App.jsx:1328` lives on the agent page, which
  has no editor doc in scope, so doc-derived would need new plumbing. Deleting
  `aspect-ratio: 16 / 9` and letting the intrinsic ratio win (plus `max-height` and
  `margin-inline: auto`; on a replaced element with `width: 100%` and auto height,
  `max-height` re-solves the width and preserves the ratio) is both smaller and more
  correct. **This stays Phase 5b item 4. OPN-39 does not need it** — a render card is not
  the editor canvas.

## 1.5 Existing projects (the brief's tension #3)

**Nothing changes for them, on purpose.**

| Project state on disk | Before | After | Why |
| --- | --- | --- | --- |
| No `edit_decisions.json` (fresh manual) | scaffold, no canvas → 1920×1080 both sides | scaffold **with** canvas → 1080×1920 both sides | The fix |
| Has a doc, no `compose_target` | 1920×1080 both sides | 1920×1080 both sides | Untouched — see below |
| Has `compose_target: 1920×1080` | 1920×1080 both sides | 1920×1080 both sides | Explicit intent; never override |
| Agent-created (any shipped pipeline) | 1080×1920 both sides | unchanged | Pipelines already set it |

The middle row is the one that matters. Overlay and clip positions are stored in
**canvas pixels** — `position.{x,y}` written by the WYSIWYG drag path, and the
`clipBox`/`clipAnchorXY`/`clipDefaultPosition` math in
`web/src/studio/model.js:301-327`. Reinterpreting a canvas-less legacy doc as 1080×1920
would leave an overlay authored at `x: 1500` **420px outside the frame**, in the export
as well as the preview, with no user action and no undo entry. That is data corruption
wearing a feature's clothes. We do not do it silently.

If the human wants legacy manual projects to become vertical, the correct shape is a
one-time **explicit** prompt in the editor ("This project has no canvas set — set it to
9:16?") that writes `compose_target` through `interp.setCanvas` as a normal, undoable
commit. That is a separate ticket and I am not designing it here. **That is what would
change my mind about the middle row** — an explicit user action, never a fallback flip.

## 1.6 Every site that touches the canvas — named, with a verdict

| Site | Verdict |
| --- | --- |
| `web/src/editor/interp.js:193-203` `scaffoldEditDecisions` | **CHANGE** — emit `metadata.compose_target` |
| `web/src/editor/interp.js:573-581` `canvasOf` fallback | **KEEP** the numbers; add one comment line naming `video_compose.py:_resolve_canvas` as the twin that must move with it |
| `web/src/editor/interp.js:562-571` `setCanvas` | No change — already merges correctly |
| `tools/video/video_compose.py:1141` renderer fallback | **KEEP** — the twin |
| `server/render_jobs.py:540-546` | No change — the missing `profile` key is why the profile rung is inert; documenting it is enough |
| `web/src/studio/StudioPreview.jsx:108-127` safe frame | No change — already ratio-agnostic |
| `web/src/studio/model.js:28-33` `CANVAS_PRESETS` | No change — 9:16 already first |
| `web/src/studio/StudioToolbar.jsx:11-28` `CanvasPicker` | No change — will now show `9:16 · 1080×1920` selected for new projects, with no code edit |
| `web/src/studio/model.js:126-127, 247-248, 265, 302, 315, 322` (`\|\| 1920` / `\|\| 1080`) | **KEEP.** Inert: every production caller passes `interp.canvasOf(...)` (`Studio.jsx:93`, `:494`, `:504`), which never returns a falsy dimension. They only fire when `canvas` is omitted entirely — a path exercised solely by `model.test.js:90`. Editing tested behaviour for no user-visible gain violates karpathy §3. |
| `web/src/styles.css:480` `.render-item video` | **Out of scope** — Phase 5b item 4, amended per 1.4 |
| `web/src/editor/Editor.jsx:40` (also scaffolds) | **Dead code.** Not imported by `App.jsx` or `main.jsx`; its only referrer is its own sibling `web/src/editor/Inspector.jsx`. Mentioned, not deleted (karpathy §3). |

## 1.7 Tests

1. **New** in `interp.test.js`, beside the existing `scaffoldEditDecisions` block at
   `web/src/editor/interp.test.js:97-110`:
   `canvasOf(scaffoldEditDecisions({}))` → `{width: 1080, height: 1920, fps: 30}`.
2. **The existing canvas test at `web/src/editor/interp.test.js:318-323` stays exactly
   as written.** `canvasOf({})` → `1920×1080@30` is now a *deliberate* legacy-doc
   contract, not an accident. Its name is already honest ("falls back to 1920x1080@30").
   I am adding a comment above it that says: this pins the legacy-doc reading and must
   move in lockstep with `video_compose.py:1141`, or preview and export diverge.
   Confronted, not deleted.
3. **New** in `model.test.js`: `CANVAS_PRESETS[0]` dims equal the scaffold's canvas — so
   a drift shows up as a failing test rather than as the picker silently rendering its
   "custom" option.
4. **Python:** none needed. No Python behaviour changes. Existing coverage
   (`tests/tools/test_compose_transitions.py:215` — 9:16 `compose_target` produces
   1080×1920 output) already proves the renderer honours an explicit canvas.

## 1.8 Success condition

> Create a new project, open the editor without touching the canvas picker, drop one
> clip, Render. The picker reads `9:16 · 1080×1920`; the preview safe frame is taller
> than it is wide; `ffprobe renders/final.mp4` reports **1080×1920**. Then open a project
> created before this change: its picker, its safe frame and its exported dimensions are
> byte-for-byte what they were before.

## 1.9 Deliberately not building

- Any change to either fallback literal (1.3).
- A creation-time canvas picker (1.3b).
- A migration for canvas-less legacy docs (1.5) — needs a human decision.
- The render-card aspect fix (1.4) — Phase 5b item 4.
- Re-laying-out the stage for a tall frame. Worth noting for whoever owns Phase 4:
  `.st-stage` has `min-width: 360px` (`web/src/styles.css:1101`), so a 9:16 frame is
  height-bound and leaves real horizontal slack on a wide window. That is layout work,
  not OPN-39.
- **Observed adjacent defect, not fixed:** merely *opening* the editor on a fresh project
  autosaves a doc containing the scaffold's placeholder cut
  `{id: 'c1', source: 'clip.mp4'}` — a file that does not exist — before the agent has
  run (`Studio.jsx:134-135` → `:247-251`). Pre-existing. My change makes that persisted
  scaffold strictly *more* correct (it now also states the canvas), but the fake cut
  deserves its own ticket.

---

# Part 2 — OPN-27: @-mention project assets

## 2.1 The constraint that decides the design

**The agent's working directory is not the project.** `server/agent_runner.py:685` sets
`cwd=str(repo_root)` — the read-only code root. The projects directory is exposed only as
an extra workspace via `agent_add_dirs` (`server/agent_runner.py:602-617`). The system
prompt is explicit about the consequence (`server/agent_runner.py:1242-1252`):

> *"Write artifacts to the ABSOLUTE path {projects_dir}/{project_id}/artifacts/ — your
> working directory is the read-only app code, so a relative 'projects/{project_id}/...'
> path would write to the wrong place."*

And that guidance is **first-turn only**: `server/agent_runner.py:1946` composes
`prompt = f"{self._first_turn_preamble(project_id)}\n\n{message}"` when a fresh client is
created; every later turn sends the raw message.

**Therefore a bare relative path in a chat message is not reliably resolvable.** On turn
one the agent can join it against the preamble's absolute path. On turn nine, after a
backend restart or a thread switch (`server/app.py:941-943`), it is guessing. The brief
is right that a mention the agent cannot resolve is worthless — so the resolution has to
happen somewhere deterministic, and the only place that *knows* `projects_dir` is the
server.

## 2.2 The decision

**A text token the user can see, expanded by the server into verified absolute paths.**

```
  user types "@"
      |
      v  menu: flat, filtered list from GET /api/projects/{id}/assets
      |
      v  accept (Enter / Tab / click)
  textarea now reads:
      tighten @[assets/video/hook.mp4] to 3s
      |
      v  POST /api/projects/{id}/chat  {message, thread_id, model}
      |     ^ UNCHANGED request shape
      v
  server/app.py:974 — expand before run_turn
      editor.expand_asset_mentions(pdir, project_id, body.message)
      regex  @\[([^\]\n]+)\]  ->  editor.resolve_source_path()
      |
      v  what the agent actually receives:
  tighten @[assets/video/hook.mp4] to 3s

  [MENTIONED PROJECT ASSETS — verified absolute paths:
   - assets/video/hook.mp4
     -> /Users/x/Library/.../projects/p1/assets/video/hook.mp4]
```

Three properties that make this the right rung:

1. **`resolve_source_path` already exists and already does the hard part.**
   `server/editor.py:68-120` resolves a manifest id *or* a path, tries repo-root-relative
   then project-relative then projects-dir-relative, and **refuses anything that escapes
   the project directory or the shared repo asset library** (the containment check at
   `server/editor.py:118`). A mention is a user-supplied string crossing into a model
   prompt — a trust boundary — and the guard is already written and already tested. Rung
   2 of the ladder; I am not writing a second path resolver.
2. **The API contract does not change.** No new `ChatRequest` field
   (`server/app.py:114-118`), no new argument to `send()`. That matters more than it
   looks — see 2.6.
3. **The transcript stays human.** `useAgentChat` persists its own client-side message
   list (`web/src/chat/useAgentChat.js:117`, persisted via `persistThread`), so the
   expansion is transport-only. Re-opening a thread shows `@[assets/video/hook.mp4]`, not
   a wall of absolute paths — and never leaks the user's home directory into a stored
   thread file, which matters for a public repo.

### Alternatives rejected

**Send the raw relative path and let the agent join it.** Free, and wrong past turn one
(2.1). Rejected.

**Add `mentions: [path]` to `ChatRequest`.** Structurally cleaner-looking. Rejected for
a concrete reason: it forces `chat.send(text)` to grow a second parameter, and
`web/src/studio/Studio.jsx:255-256` wraps it as
`send: async (text) => { await flushAutosave(); return chat.send(text) }` — a single-arg
wrapper that would **silently drop the mentions inside the Studio editor**, which is
exactly where a user drags assets around and would most want to @ one. A design whose
failure mode is "works on the agent page, silently degrades in the editor" is worse than
a slightly uglier token. Text is the transport that already reaches both.

**A contenteditable composer with rendered pills.** Rejected outright: it replaces a
plain `<textarea>` (`web/src/chat/ChatPanel.jsx:132-147`) that owns Enter-to-send, IME
composition, autosize and `disabled` handling. Enormous diff, new bug surface, zero
functional gain — the agent receives the same string either way.

**Resolve to a manifest `asset_id`.** `resolve_source_path` accepts ids, but not every
asset is in the manifest (uploads land on disk directly, `server/app.py:383-419`), so ids
cover a subset of what the menu lists. Paths cover all of it. Rejected.

## 2.3 What is mentionable (the brief's tension #5)

One `GET /api/projects/{id}/assets` call (`web/src/api.js:123` →
`server/app.py:421-507`) returns all three buckets. **All three are mentionable**,
grouped in the menu with a heading and an icon from `web/src/components/icons.jsx` (no
emoji, per RULES.md):

| Bucket | Path shape | Mentionable | Why |
| --- | --- | --- | --- |
| `kinds` (`server/app.py:438-450`) | `assets/video/x.mp4` | **Yes** | Literally what the ticket asks for |
| `agent_renders` (`server/app.py:492-505`) | `hf/renders/scene2.mp4` | **Yes** | "use `@[hf/renders/scene2.mp4]` instead of scene 3" is a real, frequent instruction |
| `renders` (`server/app.py:452-487`) | `renders/final.mp4` | **Yes** | "the cut at 0:12 in `@[renders/final.mp4]` is wrong" |

All three are project-relative, so the `proj / raw` candidate at `server/editor.py:110`
resolves every one of them. Verified against the response construction: `kinds`
`server/app.py:444`, `renders` `server/app.py:477`, `agent_renders` `server/app.py:501` —
each is `str(f.relative_to(proj))`.

**Not mentionable:** `artifacts/*.json` (the agent's own contract files — it already knows
where they are, and the preamble tells it), `.mc/` (the agent's chat history — noise and
a privacy smell), and the text companions the browse endpoint surfaces (`.srt`/`.md`).
"Asset" in the ticket means media. If subtitle files turn out to be wanted, they are a
one-line addition to the menu's source list.

I use `listAssets`, **not** the `/browse` endpoint (`server/app.py:509+`) that the Assets
panel uses, because an autocomplete needs one flat filterable list, not a folder walk.
`Studio.jsx:141` already holds a `listAssets` result, but `ChatPanel` renders under two
different parents (the agent page and the Studio), so threading it down from both is more
plumbing than one extra directory listing on menu-open is worth. Fetch on open; that also
picks up assets uploaded since the panel mounted.

## 2.4 Keyboard contract (the brief's tension #5)

Enter currently sends (`web/src/chat/ChatPanel.jsx:140-145`). An autocomplete that
swallows Enter is a regression waiting to happen, so the rules are written to make the
menu **impossible to be open when the user did not mean to open it**:

| State | Key | Behaviour |
| --- | --- | --- |
| Menu **closed** | Enter | **Sends. Unchanged. This is the invariant.** |
| Menu closed | Shift+Enter | Newline. Unchanged. |
| Menu **open** | ↑ / ↓ | Move the highlight (wraps) |
| Menu open | Enter or Tab | Insert `@[path] `, close the menu. Does **not** send. |
| Menu open | Escape | Close the menu, keep the typed text, keep focus |
| Menu open | any other key | Re-run the query; **zero matches closes the menu** |

Opening rules, all in the pure `mentionQuery(text, caret)` helper:

- The `@` must be at index 0 or preceded by whitespace — so `someone@example.com` never
  opens a menu.
- The query is the run of non-whitespace characters from `@` to the caret. Whitespace
  ends the token.
- Zero matches ⇒ closed. There is never a state where Enter does nothing.

Filenames containing a space are reachable by typing a prefix up to the space, or any
distinctive substring — matching is case-insensitive against both `name` and `path`.
Known ceiling, stated rather than engineered around.

**Token syntax ceiling:** a filename containing `]` breaks the `@\[([^\]\n]+)\]` regex;
the token degrades to plain text and the agent still sees the visible path. Rare enough
to accept; documented rather than parsed around.

## 2.5 Files that change

**Frontend**

- `web/src/chat/chatUtils.js` — three pure functions (this is the right home: it is the
  chat module's existing pure-helper file, tested by `chatUtils.test.js`; the RULES.md
  "put it in `interp.js`" rule governs *doc/timeline* logic, and a composer caret is
  neither):
  - `mentionQuery(text, caret)` → `{start, query}` or `null`
  - `applyMention(text, caret, path)` → `{text, caret}`
  - `mentionCandidates(assets, query, limit)` → flat `[{path, name, group}]`, ordered
    `kinds` → `agent_renders` → `renders`, capped
- `web/src/chat/ChatPanel.jsx` — menu state, the keydown branch (guarded on menu-open so
  the existing `Enter` path at `:140-145` is untouched when closed), the `listAssets`
  fetch on first open, and the `<ul role="listbox">` with `aria-activedescendant` /
  `aria-expanded` on the textarea.
- `web/src/styles.css` — `position: relative` on `.composer` (currently
  `web/src/styles.css:211`) plus a `.mention-menu` / `.mention-item` block styled from
  the P0–P3 tokens. **CSS namespace note:** RULES.md scopes the `st-` prefix to the
  studio; the chat panel's vocabulary is unprefixed (`.composer`, `.composer-bar`,
  `.confirm-card`), and this is chat. Not a RULES violation.

**Backend**

- `server/editor.py` — new `expand_asset_mentions(projects_dir, project_id, message)`.
  Placed here because this file already owns `resolve_source_path`. Contract:
  - no `@[...]` token ⇒ returns the **same string object** (identity — mirrors the
    "a no-op returns the same reference" discipline the doc mutators follow)
  - dedupes, preserves first-appearance order, caps at 20 expansions (a cheap bound on
    prompt size, not a feature)
  - resolvable ⇒ `- <relpath>\n  -> <abspath>`
  - unresolvable ⇒ `- <relpath> -> NOT FOUND in this project`, so the agent asks instead
    of inventing a path. This is also the path-traversal outcome: `@[../../../etc/passwd]`
    fails the containment check at `server/editor.py:118`, returns `None`, and is
    reported as NOT FOUND. It is never expanded to an absolute path.
- `server/app.py:974` — one line: pass the expanded message to `runner.run_turn` instead
  of `body.message`.

**Not changed:** `web/src/api.js:226-232` (`chatStream`), `ChatRequest`
(`server/app.py:114-118`), `useAgentChat.send` (`web/src/chat/useAgentChat.js:113`),
`Studio.jsx:255-256`. That is the point of choosing a text token.

## 2.6 Tests

**`web/src/chat/chatUtils.test.js`** (pure, cheapest coverage):
`@` at index 0 opens with an empty query; `@` after a space opens; `a@b.com` returns
`null`; a caret placed before the `@` returns `null`; whitespace after the `@` closes it;
`applyMention` produces `@[path] ` with the caret after the trailing space and leaves the
rest of the line intact; `mentionCandidates` matches on both `name` and `path`,
case-insensitively, and respects the cap.

**`web/src/chat/ChatPanel.test.jsx`** (render contract; the existing suite already covers
`submitting the composer calls chat.send`):
1. **Regression guard, most important:** with no menu open, Enter still calls
   `chat.send`.
2. Typing `@` renders the listbox.
3. With the menu open, Enter does **not** call `chat.send` and the textarea value gains
   the `@[...]` token.
4. Escape closes the menu; the next Enter calls `chat.send`.
5. A query with zero matches renders no listbox, and Enter sends.

No geometry assertions — jsdom has no layout engine (RULES.md testing section).

**`tests/contracts/test_editor_api.py`** (the existing home for `server/editor.py`
coverage):
1. A message with no token returns the identical string.
2. A real project-relative asset expands to its absolute path, and that path exists.
3. An unknown path is reported NOT FOUND and is not expanded.
4. `@[../../etc/passwd]` is reported NOT FOUND — the absolute path never appears in the
   output. (Direct test of the trust boundary.)
5. The same path mentioned twice expands once.

## 2.7 Success condition

> In a project with at least one uploaded video, one `hf/renders/` clip and a
> `renders/final.mp4`: type `@` in the chat composer — a menu lists all three, grouped.
> Type two letters, press Enter — the token is inserted and **nothing is sent**. Press
> Enter again — the turn sends. The agent's next tool call reads that exact file without
> asking where it is. Press Escape on an open menu, then Enter: the message sends, proving
> the pre-existing Enter behaviour survived. Reopen the thread: the transcript shows
> `@[assets/video/hook.mp4]`, not an absolute path.

## 2.8 Deliberately not building

- Pills / contenteditable (2.2).
- `#`-mention for timeline clips or scenes. Plausible next step, not this ticket.
- Mentioning artifacts, `.mc/`, or text companions (2.3).
- Drag-an-asset-into-the-composer. `StudioAssets` already has an HTML5 DnD channel
  (`application/x-opennolan-asset`, RULES.md) so it would be cheap, but it is a second
  interaction for one ticket.
- Any change to how the agent *responds* to a mention. It gets a verified absolute path;
  its existing Read/Bash/Glob tools do the rest. No new tool, no prompt engineering.

---

# Sequencing, risk, and what I want a human to decide

## Sequencing against the ratified UI-polish plan

The two tickets are independent of each other and can land in either order or in
parallel — they share no file.

```
  OPN-39  ->  interp.js + 3 tests             no CSS, no overlap
  OPN-27  ->  chatUtils/ChatPanel/styles.css/editor.py/app.py
                     |
                     +-- touches .composer (styles.css:211)
                         Phase 7 item 10 also touches .composer
                         (align-self on the composer action)
                         Phase 6 item 20 adds an accessible name to
                         the composer textarea

  Recommended order: OPN-27 first, then Phase 6/7 rebase onto it.
  OPN-27's diff is structural (a new child element + position:relative);
  6/7's are single-property. Cheaper to rebase a one-liner onto a
  structural change than the reverse.
```

Phase 5b item 4 stays where it is, with the amendment in 1.4. **Neither ticket unblocks
or blocks it.**

## The single biggest risk

**Not either ticket's own change — it is that `interp.js:577` and
`video_compose.py:1141` are two hand-maintained copies of the same number, and OPN-39
draws attention to exactly one of them.**

The likely failure is not this PR; it is the next one. Someone reads OPN-39 as "make the
app 9:16", opens `canvasOf`, sees `|| 1920`, changes it, sees the test at
`interp.test.js:318-323` fail, updates the test, and ships. Preview is now 9:16 and every
canvas-less legacy project exports 16:9 — with no error, no warning, and a mis-framed
deliverable. The `profile` rung being inert (`render_jobs.py:540-546`) means there is not
even an intermediate signal.

Mitigation, both cheap and both in this design: the cross-reference comment on
`canvasOf` (1.6) and the explanatory comment on the test (1.7 item 2). The durable fix is
to make the renderer the single source of truth and have the frontend fetch it, which is
a real ticket and not this one.

Runner-up risk: `expand_asset_mentions` runs on **every** chat turn. Its no-token path
must be a same-object return and must not touch the disk. If someone implements it as
"always call `resolve_source_path` on something", every turn gains a filesystem walk plus
a manifest read (`server/editor.py:90`). The identity-return test (2.6 Python item 1) is
what stops that.

## What I expect the other agent to get wrong

Ranked by how likely and how costly:

1. **Taking OPN-39 literally and flipping `canvasOf`'s fallback** — possibly flipping
   `video_compose.py:1141` alongside it and calling that "preview == export preserved".
   It is preserved for *new* renders and silently broken for every legacy doc's stored
   overlay coordinates (1.5). The scaffold is the smaller and safer lever, and it is easy
   to miss because `scaffoldEditDecisions` is 10 lines in a 600-line file.
2. **Missing that `Studio.jsx:255-256` is a single-argument wrapper.** Any OPN-27 design
   that adds a parameter to `send()` or a field to `ChatRequest` has to also edit that
   wrapper, and if it does not, mentions work on the agent page and silently vanish in
   the editor — the worst kind of bug, because both surfaces render the same component
   and the failure is invisible in review.
3. **Assuming the agent's cwd is the project directory.** It is `repo_root`
   (`agent_runner.py:685`), and the corrective guidance is first-turn only
   (`agent_runner.py:1946`). A design that ships bare relative paths will demo perfectly
   on turn one and degrade later — the hardest class of bug to catch in QA.
4. **Writing a new path resolver** instead of reusing `resolve_source_path`
   (`server/editor.py:68-120`), and with it re-deriving — or forgetting — the
   containment check at `server/editor.py:118`.
5. **Treating Phase 5b item 4 as in scope for OPN-39**, and deriving the render card's
   aspect from the project canvas. That is what the ratified plan says, but the element
   is a `<video>` with an intrinsic ratio and it lives on the agent page where no doc is
   in scope (1.4).
6. **Trusting `agreed-ui-polish-plan.md:286`'s `styles.css:334` anchor.** It has drifted;
   the rule is at `web/src/styles.css:480`.

## Open questions for the human (neither blocks implementation)

1. **Legacy canvas-less manual projects** — leave them 16:9 forever (my default), or
   ship the explicit one-time "set this project to 9:16?" prompt from 1.5?
2. **Should `renders/final.mp4` be mentionable?** I said yes (2.3). It is the one bucket
   where I can see an argument for no — the agent generally reasons about the timeline,
   not the deliverable. One line either way.
