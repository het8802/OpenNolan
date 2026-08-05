# OPN-39 + OPN-27 architecture — the shape of the change

**Status: PLAN** · companion to [`../agreed-design.md`](../agreed-design.md)
(RATIFIED, joint claude + codex, baseline `ae57d47`)

Read this for the shape. Read `agreed-design.md` for the reasoning, the rejected
options, the risk register and the full test list.

**The two changes, one sentence each:**

- **OPN-39** — a new project will *say* it is 9:16 instead of relying on a default,
  because the preview and the renderer keep that default in two separate files and
  nothing forces them to agree.
- **OPN-27** — typing `@` in chat lists the project's files, and the one you pick
  travels to the server as structured data, which turns it into a verified absolute
  path the agent can actually open.

Total production surface: **7 existing files + 1 new module + 1 CSS block.**

---

## 1. OPN-39 — the editor canvas

### 1.1 Today — two copies of one number, agreeing by luck

```text
  NEW PROJECT — no artifacts/edit_decisions.json yet
                         |
                         v
          scaffoldEditDecisions()               interp.js:194
          builds cuts + overlays, emits NO metadata
                         |
            +------------+------------+
            |                         |
            v                         v
        PREVIEW                    EXPORT
        canvasOf(doc)              _resolve_canvas()
        interp.js:574              video_compose.py:1132
            |                         |
     compose_target absent     compose_target absent
       -> use fallback           -> use fallback
            |                         |
            v                         v
        1920 x 1080                1920 x 1080
        interp.js:577              video_compose.py:1141
            |                         |
            +------------+------------+
                         |
                         v
                  16:9 in a vertical-video app
```

★ **THE TRAP** — those two `1920, 1080` literals are maintained by hand, in
different languages, in different repos-worth of code. They agree today. Nothing
checks that they still will. The whole of OPN-39 is about not making that worse.

### 1.2 After — the document states its own canvas

```text
  NEW PROJECT — no artifacts/edit_decisions.json yet
                         |
                         v
          scaffoldEditDecisions()               interp.js:194
          emits metadata.compose_target = {1080, 1920, 30}   <== THE CHANGE
                         |
                         v
  ======== ONE DECLARED CANVAS — BOTH READERS OBEY IT ========
                         |
            +------------+------------+
            |                         |
            v                         v
        PREVIEW                    EXPORT
        canvasOf(doc)              _resolve_canvas()
        reads 1080 x 1920          reads 1080 x 1920
            |                         |
            +------------+------------+
                         |
                         v
              9:16 — matching by CONSTRUCTION,
              not by two literals happening to be equal
```

The funnel is the point: an explicit `compose_target` is the one value both
readers consult, so the fallbacks stop being load-bearing for new work.

### 1.3 The legacy path — deliberately untouched

```text
  OLD PROJECT — edit_decisions.json exists, no compose_target
                         |
            +------------+------------+
            v                         v
      fallback 1920x1080        fallback 1920x1080
      interp.js:577             video_compose.py:1141
       (NOT CHANGED)             (NOT CHANGED)
                         |
                         v
        16:9, exactly as before. No migration, no rewrite.
```

An overlay saved at `x: 960` was authored against a 1920-wide canvas. Reinterpret
that canvas as 1080-wide and the overlay silently moves. So we do not.

### 1.4 Why not simply change the default

| Option | What breaks |
| --- | --- |
| Flip `interp.js` only | Preview goes 9:16, export stays 16:9. Violates preview==export. |
| Flip both files | Every legacy doc's saved overlay coordinates silently re-scale. |
| Hardcode 9:16 in CSS | Repeats today's bug with the opposite ratio; 4 presets exist. |
| **Write it at scaffold time** | **Nothing. New projects vertical, old ones frozen.** |

---

## 2. OPN-27 — mention a project asset

### 2.1 Today — the agent cannot be told which file you mean

```text
  user types a sentence
            |
            v
  <textarea>                        ChatPanel.jsx:133
            |
            v
  send(text)                        useAgentChat.js:113
            |
            v
  POST /chat {message, thread_id, model}          api.js:226
            |
            v
  runner.run_turn(project_id, body.message)       app.py:974
            |
            v
  agent runs with cwd = <repo root>               agent_runner.py:685
       "use the whoosh sound"  ->  which file, on which path?
```

★ **DIVERGENCE** — the project directory is only an *extra workspace*, and the
"use absolute paths" instruction lives in the **first-turn** preamble
(`agent_runner.py:1242-1252`). By turn 5 a bare relative path is not reliably
resolvable. This is why the server, not the user and not the agent, must resolve.

### 2.2 After — pick from a menu, server verifies, agent gets an absolute path

```text
  user types "@"
            |
            v
  menu  <---- GET /projects/<id>/assets            app.py:421
              assets/**      hf/renders/**      renders/<direct child>
            |
       user picks one
            |
            v
  textarea:  "Use @assets/audio/whoosh.wav for the cut"   (what you see)
  sidecar :  mentions[] = [{ path: "assets/audio/whoosh.wav" }]
            |
            v
  POST /chat {message, thread_id, model, mentions}
            |
            v
  +--------------------------------------------------------------+
  |  VALIDATE — server/app.py, beside the listing policy         |
  |                                                              |
  |  GATE 1 — SHAPE.  Decided from the string alone.             |
  |    absolute? ".." segment? dot-prefixed segment?             |
  |    wrong root? extension not allowed FOR THAT root?          |
  |      -> 422, runner NEVER called                             |
  |                                                              |
  |  GATE 2 — STATE.  Needs a filesystem look.                   |
  |    missing / not a regular file / symlink escapes project    |
  |      -> mark "NOT FOUND", the turn PROCEEDS                  |
  +--------------------------------------------------------------+
            |
            v
  message + a reference map of VERIFIED ABSOLUTE paths
            |
            v
  runner.run_turn(...)   -> agent can Read exactly that file
```

### 2.3 Why two gates and not one

This split is the heart of the design, and it is a user-experience decision as much
as a security one.

```text
  SHAPE failure                      STATE failure
  -------------                      -------------
  The menu can never produce it.     We cause it ourselves: the agent
  So it means a client bug or a      rewrites hf/renders/* mid-turn, so
  tampered request.                  a valid pick can vanish mid-flight.
        |                                  |
        v                                  v
  Fail loudly: 422.                  Degrade: "NOT FOUND", carry on.
  Runner never runs.                 The user's sentence still gets sent.
```

The reason this matters more than it looks:

```text
  useAgentChat.js:116  setInput('')     <-- draft cleared BEFORE the request
  useAgentChat.js:182  catch (e) {...}  <-- error shown, draft NEVER restored
```

So today *any* failed send silently eats what the user typed. A 422 on a
vanished-file race would destroy a sentence over a condition we caused. Hence:
races degrade, tampering 422s — and build item 10 fixes the draft loss at its one
shared site, which also repairs the pre-existing auth-503 case that eats text now.

### 2.4 The rule that keeps the two gates honest

```text
  INVARIANT:  every path the menu can offer is SHAPE-valid.
```

If that ever breaks, a legitimate click 422s. The endpoint's own walk is looser
than the predicate in one spot — it skips a dot-prefixed *leaf* but not a
dot-prefixed *directory*, so `assets/.tmp/clip.mp4` is listable. The composer
drops those candidates client-side (build item 6) rather than changing
`list_assets`, which also feeds the dashboard poll and the Studio.

---

## 3. Surface — every file that changes

| File | What changes |
| --- | --- |
| `web/src/editor/interp.js` | `scaffoldEditDecisions` emits `compose_target`; name the two canvases; cross-reference comment to `video_compose.py:1141` |
| `web/src/editor/interp.test.js` | Pin new + legacy semantics separately; partial-target regression |
| `web/src/studio/model.test.js` | Guard that `CANVAS_PRESETS[0]` still matches the scaffold |
| `web/src/chat/mentions.js` | **NEW** — pure helpers: flatten/label buckets, drop dot-segments, find the `@query` at the caret, rank matches (exact name → prefix → substring → path-only), replace + return the mention |
| `web/src/chat/mentions.test.js` | **NEW** — the helper matrix |
| `web/src/chat/ChatPanel.jsx` | The menu, keyboard contract, listbox a11y, ref pruning |
| `web/src/chat/useAgentChat.js` | Return `projectId`; `send(text, mentions)`; restore the draft on a failed send |
| `web/src/api.js` | Serialize the sidecar |
| `web/src/studio/Studio.jsx` | Forward **all** args through the autosave wrapper |
| `web/src/styles.css` | `.composer { position: relative }` + a `.mention-*` block (chat namespace, not `st-`) |
| `server/app.py` | `ChatRequest.mentions`; the SHAPE/STATE validator; enrich the message at `:974` |

**Deliberately untouched — and each for a reason:**

```text
  tools/video/video_compose.py:1141  renderer fallback stays landscape
  web/src/editor/interp.js:577       JS fallback stays landscape
  server/editor.py                   resolve_source_path is repo-root-first
  web/src/styles.css:480             render card ratio = Phase 5b item 4
  schemas/artifacts/*.schema.json    compose_target already an allowed field
```

---

## 4. Build order

The two tickets share no file, so items 1-5 and 6-12 can run in either order or in
parallel.

```text
  OPN-39
   1  name the canvases + cross-reference comment      interp.js
   2  scaffold writes compose_target                   interp.js
   3  pin new + legacy semantics                       interp.test.js
   4  preset-drift guard                               model.test.js
   5  MANUAL: new project -> ffprobe says 1080x1920

  OPN-27  (dependency-first: pure -> UI -> wire -> server)
   6  pure helpers + tests                             mentions.js  (NEW)
   7  return projectId from the hook                   useAgentChat.js
   8  menu UI, keyboard, a11y, pruning                 ChatPanel.jsx + CSS
   9  send(text, mentions) + forward through wrapper   api.js, Studio.jsx
  10  restore the draft on a failed send               useAgentChat.js
  11  ChatRequest.mentions + validator + enrich        server/app.py
  12  MANUAL: end-to-end walkthrough

  13  scripts/dev test fast -> test full -> smoke -> stop
```

Item 8's closed-menu guard goes in **first**: Enter currently sends, and a menu
that swallows Enter would break the most-used interaction in the app.

---

## 5. Three things the diagrams make obvious

1. **OPN-39 is a preview/export-parity change wearing an aspect-ratio costume.**
   The ticket reads like CSS. The actual risk lives in two hand-maintained numbers
   in two languages, and the fix is chosen specifically so neither has to move.

2. **The OPN-27 gates are drawn where they are because of a bug in the composer,
   not because of security.** Both gates are safe. Splitting them the way §2.3
   does is what stops a race *we* cause from deleting a user's sentence — and
   finding that forced a root-cause fix to a draft-loss bug that already ships.

3. **Neither ticket touches the renderer or the agent.** Every change is in the
   editor's document scaffold or between the composer and `run_turn`. That is why
   the surface is 7 files and why nothing here needs a migration.

---

## 6. What this does not do

- **The final-render card still shows every video in a 16:9 box**
  (`styles.css:480`). A 1080x1920 export appears as a ~101x180 strip in a 320x180
  mostly-black card. That is Phase 5b item 4 of the ratified UI-polish plan, it
  already happens today for every agent-made reel, and OPN-39 does not cause it.
  `agreed-design.md` §6 carries the analysis forward so 5b need not re-derive it.
- **No bulk migration.** Existing projects keep their canvas, explicit or implied.
- **Mentions resolve for the turn they were selected in only.** There is no resend
  action in the chat surface, so nothing would re-resolve a stored mention.

**What would change our minds:** if a material number of users make landscape or
square projects in the editor, the scaffold default becomes a project-creation
choice instead. If the render card's letterboxing is judged worse than the layout
shift of fixing it, 5b item 4 gets pulled into the next increment.
