# OPN-39 and OPN-27 design

Status: PLAN · Rev 1

Baseline inspected: `ae57d47`

## Decisions at a glance

- OPN-39: make a genuinely new editor document explicitly 9:16 at
  1080x1920@30, preserve the 1920x1080@30 fallback for legacy documents, and
  derive every displayed frame from the effective document canvas.
- OPN-27: autocomplete every user asset, agent clip, and top-level export, send
  a structured project-relative reference beside the visible text, and let the
  server validate it and give the agent an absolute readable path.

These decisions keep the already-ratified Phase 5b rule: the UI does not
hardcode one ratio for all projects. The product is vertical-first, not
vertical-only.

## Defects being fixed

1. A fresh editor document omits `compose_target`, so it inherits the shared
   legacy 16:9 fallback instead of expressing the product's vertical default
   (`web/src/editor/interp.js:193`).
2. The final-render player presents every project in a fixed 16:9 box even when
   the document and exported pixels use another canvas
   (`web/src/styles.css:480`).
3. Chat has a plain textarea and sends only a string, so there is no way to
   discover a project asset or bind visible text to an exact readable file
   (`web/src/chat/ChatPanel.jsx:132`, `web/src/api.js:226`).

## OPN-39: vertical-first editor canvas

### What the source actually does

`canvasOf` currently reads `metadata.compose_target` and otherwise returns
1920x1080@30 (`web/src/editor/interp.js:573`). The Python renderer really has
the same default: `_resolve_canvas` starts at 1920x1080@30, then applies a
profile, then lets `metadata.compose_target` win
(`tools/video/video_compose.py:1132`,
`tools/video/video_compose.py:1141`,
`tools/video/video_compose.py:1149`). The frontend comment is therefore true.

The editor is already capable of multiple ratios. Its presets put 9:16 first
but also offer 1:1, 16:9, and 4:5 (`web/src/studio/model.js:28`). The safe frame
contain-fits whatever canvas it receives (`web/src/studio/StudioPreview.jsx:108`).
The remaining unconditional presentation bug is the final-render player CSS,
which fixes every video at 16:9 (`web/src/styles.css:480`).

The fresh-document path is distinct from the legacy fallback. When the API has
no `edit_decisions.json`, Studio calls `scaffoldEditDecisions`
(`web/src/studio/Studio.jsx:131`, `web/src/studio/Studio.jsx:134`). That pure
factory currently emits no canvas (`web/src/editor/interp.js:193`). This is the
safe place to express a new-project default.

### Decision

Add two named concepts in `interp.js`:

- `NEW_PROJECT_CANVAS = {width: 1080, height: 1920, fps: 30}`.
- `LEGACY_CANVAS = {width: 1920, height: 1080, fps: 30}`.

`scaffoldEditDecisions` writes `NEW_PROJECT_CANVAS` into
`metadata.compose_target`. `canvasOf` continues to use `LEGACY_CANVAS` only
when a saved document omits the field. Both remain pure, and the scaffold stays
schema-valid because `compose_target` is an allowed metadata field
(`schemas/artifacts/edit_decisions.schema.json:294`).

This is a deliberate interpretation of OPN-39: "make the editor 9:16" means
the default for new work, not rewriting every saved project. It does not
contradict the ratified plan at
`docs/plans/ui-polish-audit/agreed-ui-polish-plan.md:286`; it implements that
plan's distinction between a vertical-first default and ratio-derived display.

The final-render player in `App.jsx` will fetch the live edit document alongside
its existing asset poll (`web/src/App.jsx:1288`) and set the video wrapper's
`aspect-ratio` from `canvasOf(content)`. If content is absent, it uses
`canvasOf(scaffoldEditDecisions())`, which is the new-project 9:16 default.
Remove the fixed ratio from `.render-item video`; retain `object-fit: contain`
so stale or earlier output with a different intrinsic ratio is not cropped.

The result is one consistent decision chain:

```text
fresh, no saved doc
  -> scaffold writes 1080x1920
  -> canvasOf reads 1080x1920
  -> preview frame and render card use 9:16
  -> renderer reads 1080x1920

legacy doc, no compose_target
  -> canvasOf fallback is 1920x1080
  -> preview frame and render card use 16:9
  -> renderer fallback is 1920x1080

any explicit compose_target
  -> every surface and renderer uses that exact target
```

### Compatibility and migration

There is no bulk or silent migration.

- A saved project with explicit 1920x1080 remains 1920x1080.
- A saved project with another explicit ratio remains at that ratio.
- A saved project with no `compose_target` remains effectively 1920x1080.
  Its overlay and cut positions keep the coordinate system in which they were
  authored.
- A project with no edit document has no saved canvas-space positions to
  preserve, so its first scaffold can safely be explicit 1080x1920.
- Choosing another toolbar preset remains an ordinary undoable edit through
  `setCanvas` (`web/src/editor/interp.js:562`).

No Python default changes. Changing only the JavaScript fallback would violate
preview == export; changing both fallbacks would silently reinterpret old
coordinates. An explicit new document avoids both failures. The packaged reel
pipeline independently already requires an explicit 1080x1920 target
(`pipeline_defs/instagram-fast-reel.yaml:192`), so agent-produced reels follow
the same rule rather than depending on a fallback.

### Files that change

- `web/src/editor/interp.js`
  - name the new-project and legacy canvases;
  - make `scaffoldEditDecisions` write the new-project target;
  - keep `canvasOf` on the legacy fallback.
- `web/src/editor/interp.test.js`
  - pin both semantics separately.
- `web/src/App.jsx`
  - obtain the current edit document with the asset poll;
  - derive each final-render card ratio from the effective canvas.
- `web/src/styles.css`
  - remove the unconditional `16 / 9` from `.render-item video`.

Audited, deliberately unchanged:

- `tools/video/video_compose.py`: renderer fallback remains legacy landscape.
- `schemas/artifacts/edit_decisions.schema.json`: its precedence description
  remains correct (`schemas/artifacts/edit_decisions.schema.json:299`).
- `web/src/studio/model.js`: all six defensive 1920/1080 fallbacks stay aligned
  with legacy behavior (`web/src/studio/model.js:126`,
  `web/src/studio/model.js:247`, `web/src/studio/model.js:265`,
  `web/src/studio/model.js:302`, `web/src/studio/model.js:315`,
  `web/src/studio/model.js:322`). Studio supplies the effective canvas to these
  helpers; changing their no-argument behavior would reinterpret legacy calls.
- `web/src/studio/StudioPreview.jsx`: it already derives the safe-frame ratio.
- `web/src/studio/StudioToolbar.jsx`: it already exposes all four presets and
  writes the selected dimensions (`web/src/studio/StudioToolbar.jsx:14`).

### Rejected alternatives

1. Change every fallback to 1080x1920. Rejected because old documents without
   metadata would move and resize coordinate-based edits.
2. Change only `canvasOf`. Rejected because preview would be vertical while
   Python exports landscape.
3. Hardcode 9:16 in CSS. Rejected because it repeats the current bug with the
   opposite ratio and directly contradicts the ratified plan.
4. Add canvas selection to project creation. Rejected for this ticket because
   the toolbar already supplies the choice and the mission gives a clear
   default. Reconsider if user research shows the default surprises a material
   landscape or square cohort.

### Tests and success condition

Unit tests in `interp.test.js` must prove:

- `canvasOf(scaffoldEditDecisions())` is 1080x1920@30;
- `canvasOf({})` remains 1920x1080@30;
- explicit landscape and non-landscape targets win unchanged;
- the scaffold validates through the existing editor save contract.

The final-render surface needs a render-contract test or extracted pure helper
test proving 9:16, 16:9, and 1:1 docs produce the matching CSS ratio. Existing
Python tests already prove an explicit vertical target exports 1080x1920
(`tests/tools/test_compose_transitions.py:215`); retain that coverage and add no
redundant FFmpeg integration test.

Success means a never-edited fresh project opens at 9:16 and exports 9:16, an
old metadata-free project stays 16:9 in both preview and export, explicit presets
remain honored, and no render card uses an unconditional ratio.

## OPN-27: mention project assets in agent chat

### User contract

Typing `@` at a token boundary opens a searchable list of files from all three
existing asset buckets returned by `GET /assets`
(`server/app.py:421`):

- `kinds`: uploaded/source images, video, audio, and music;
- `agent_renders`: intermediate clips under `hf/renders/`;
- `renders`: top-level final and earlier deliverables under `renders/`.

All three are mentionable. A final render is useful in requests such as
"shorten this version," while an agent render is a normal timeline building
block. Engine internals and proxy files remain excluded because the existing
endpoint intentionally excludes them (`server/app.py:458`). Text companions
are not in this ticket because they are exposed by `/browse`, not by the three
buckets the ticket names.

The menu shows basename as the primary label and project-relative path plus a
bucket label as secondary text. Results match basename and path,
case-insensitively. The inserted visible token is the unambiguous
project-relative form, for example:

```text
Use @assets/images/product-shot.png for the opening frame
```

Spaces in names are allowed: selection, not reparsing visible prose, supplies
the authoritative reference.

### What the agent receives

The visible textarea remains plain text (`web/src/chat/ChatPanel.jsx:132`), but
the chat request gains a structured sidecar:

```text
message:  "Use @assets/images/product shot.png for the opening frame"
mentions:
  - token: "@assets/images/product shot.png"
    path:  "assets/images/product shot.png"
```

The server does not trust the client path. For each mention it:

1. requires a project-relative path in `assets/`, `hf/renders/`, or the
   top-level `renders/` directory;
2. resolves it under the selected project and rejects traversal, hidden files,
   directories, missing files, and files outside the endpoint's media kinds;
3. de-duplicates repeated paths;
4. appends a small JSON-serialized reference map to the message passed to
   `AgentRunner.run_turn`, containing token, kind, project-relative path, and
   absolute path.

```text
textarea + selected refs
          |
          v
POST /chat {message, mentions}
          |
          v
containment + existence + kind validation
          |
          v
user prose + canonical absolute-path reference map
          |
          v
agent Read/tool access inside the existing project sandbox
```

This is why the feature is not merely decorative. The agent's working directory
is the code root, while project storage is a separately added workspace
(`server/agent_runner.py:602`, `server/agent_runner.py:684`). An absolute,
server-resolved path removes cwd ambiguity. The project context already tells
the agent to work in the selected absolute project (`server/agent_runner.py:1243`).
The original user-visible message remains what chat history displays; the
resolution block is execution context, not UI prose.

Requests with no `mentions` remain byte-for-byte compatible. If a selected file
vanishes before send, the server returns a clear 422 and does not start the
agent. It must never silently drop a reference or let the agent guess another
same-named file.

### Composer behavior

The existing contract is Enter to send and Shift+Enter for a newline
(`web/src/chat/ChatPanel.jsx:140`). The mention menu changes it only while the
menu has an active result:

- Up/Down changes the active result.
- Enter or Tab inserts the active result, adds a trailing space, restores the
  caret, and does not send.
- Shift+Enter always inserts a newline; it never chooses a mention.
- Escape closes the menu without changing the draft.
- If the query has no result, Enter keeps its old meaning and sends.
- After insertion the menu is closed, so the next Enter sends normally.
- Mouse selection happens on pointer-down without blurring the textarea first.

The popup uses `role=listbox`/`role=option`; the textarea exposes
`aria-expanded`, `aria-controls`, and `aria-activedescendant`. It is anchored to
the composer rather than attempting per-character textarea geometry. This is a
small autocomplete, not a rich-text editor.

The candidate list is fetched when the project changes, refreshed when `@`
opens, and can show the cached list while the refresh is in flight. This covers
uploads and assets created during an agent turn without polling from the
composer. Busy or disabled chat neither opens nor sends the menu.

### Pure logic and component ownership

Add `web/src/chat/mentions.js` for three pure operations:

- flatten and label the three API buckets;
- find the active `@query` and replacement range at the caret;
- replace that range with a chosen token and return the new caret position and
  structured mention.

`ChatPanel` owns only menu/open/highlight state and selected references. On
ordinary text edits it prunes any selected reference whose exact inserted token
no longer exists. `useAgentChat.send(text, mentions)` owns request transport,
and `api.chatStream` serializes the sidecar. The Studio autosave wrapper must
forward both arguments while still flushing before the turn; it currently wraps
`send` at `web/src/studio/Studio.jsx:255`.

This is chat behavior, not timeline editing behavior, so it does not add a
mutator to `editor/interp.js`.

### Files that change

- `web/src/chat/mentions.js` (new): pure candidate/query/insertion helpers.
- `web/src/chat/mentions.test.js` (new): helper coverage.
- `web/src/chat/ChatPanel.jsx`: menu UI, keyboard behavior, asset refresh, and
  structured reference collection.
- `web/src/chat/ChatPanel.test.jsx`: accessible render and keyboard wiring.
- `web/src/chat/useAgentChat.js`: accept and forward mention sidecars without
  changing visible thread messages.
- `web/src/api.js`: include `mentions` in the `/chat` JSON body
  (`web/src/api.js:226`).
- `web/src/studio/Studio.jsx`: preserve mention arguments through the pre-agent
  autosave wrapper.
- `web/src/App.jsx`: pass the selected project id to the shared ChatPanel
  (`web/src/App.jsx:143`).
- `web/src/studio/Studio.jsx`: pass `projectId` to its ChatPanel instance
  (`web/src/studio/Studio.jsx:698`).
- `web/src/styles.css`: autocomplete popup styles using the existing chat
  namespace; no emoji or new studio-global class.
- `server/app.py`: request model, validation/resolution helper, and agent prompt
  enrichment before `run_turn` (`server/app.py:912`).
- `tests/contracts/test_server_agent_api.py`: server boundary and runner-message
  contract tests.

### Rejected alternatives

1. Send only `@filename`. Rejected because names collide and the code-root cwd
   cannot resolve a project-relative filename reliably.
2. Send only a raw relative path and hope the agent interprets it. Rejected
   because it provides neither server containment nor a canonical absolute
   location.
3. Replace the textarea with contenteditable chips. Rejected as a much larger
   selection, IME, clipboard, and accessibility project than this ticket needs.
4. Mention only uploaded `kinds`. Rejected because agent clips are editable
   building blocks and final outputs are legitimate revision references.
5. Reuse `/browse` and expose arbitrary text files. Rejected because the ticket
   and current three-bucket asset contract already give a bounded media scope.

### Tests and success condition

Pure frontend tests must cover trigger boundaries, caret-in-middle replacement,
spaces, duplicate basenames, all three buckets, deleted-token pruning, and a
literal `@` inside an email address not opening the menu.

Component tests must prove listbox semantics, mouse selection, arrow wrapping,
Enter/Tab selection without send, Shift+Enter newline, Escape close, no-result
Enter send, exact caret restoration, and that submit passes only references
whose tokens remain in the draft.

Backend contract tests must prove valid references from each bucket reach the
runner as canonical absolute paths; duplicate paths de-duplicate; no-mention
messages are unchanged; and traversal, absolute paths, proxy paths, hidden
files, directories, unsupported types, and vanished files return 422 before a
runner call.

Run `scripts/dev test fast` for the implementation increment, then
`scripts/dev test full` and `scripts/dev smoke` before review.

Success means a keyboard-only user can choose any listed project media file,
send without losing the established newline/send behavior, and the agent is
given the exact readable file selected by the user. No mention may resolve
outside the selected project or degrade into a guessed filename.

## Implementation order and verification

1. Separate new-project and legacy canvas semantics in `interp.js`.
   Verify with the focused `interp.test.js` cases before touching presentation.
2. Derive the final-render card ratio in `App.jsx` and remove the CSS constant.
   Verify 9:16, 16:9, and 1:1 render contracts plus the existing vertical Python
   compose test.
3. Add and test the pure mention candidate/query/insertion helpers.
   Verify the helper edge-case matrix in `mentions.test.js`.
4. Add the ChatPanel listbox and structured send plumbing through Studio,
   `useAgentChat`, and `api.js`.
   Verify keyboard and mouse behavior in `ChatPanel.test.jsx`, including the
   pre-agent autosave wrapper forwarding both arguments.
5. Add server request validation and prompt enrichment.
   Verify every allowed bucket and every rejection edge in
   `test_server_agent_api.py`; assert the runner is not called on rejection.
6. Run `scripts/dev test fast`, then `scripts/dev test full` and
   `scripts/dev smoke`. A failure in any existing canvas, editor-save, chat, or
   agent API contract blocks review.

## Risk register

| Risk | Mitigation | Proof |
| --- | --- | --- |
| A fallback change silently moves old overlays | Do not change either legacy fallback; write 9:16 only in the fresh scaffold | Step 1 tests both branches |
| Preview and Python export use different canvases | Persist an explicit target for new docs and retain the matching legacy defaults | Step 1 JS tests plus Step 2 Python vertical test |
| A render card repeats the hardcode in JSX | Compute from `canvasOf`, including explicit non-vertical targets | Step 2 three-ratio render test |
| Enter selects when the user meant to send | Intercept only with an active result; no-result Enter retains send | Step 4 keyboard tests |
| A visible mention maps to the wrong same-named file | Send the selected project-relative path as structured data; show path in the menu | Steps 3 and 5 duplicate-name tests |
| A crafted path escapes the project | Server containment, root, type, existence, and hidden-path checks precede the runner | Step 5 rejection tests assert zero runner calls |
| An asset disappears between menu load and send | Reject the turn clearly rather than dropping or guessing the reference | Step 5 vanished-file test |
| The Studio autosave wrapper drops mention metadata | Forward all send arguments after the existing flush | Step 4 wrapper test |

## Deliberately not building

| Not building | Reason | What would change this decision |
| --- | --- | --- |
| Forced conversion of existing projects | It changes saved canvas-space meaning | A versioned migration with proven coordinate conversion |
| Removal of square, landscape, or 4:5 | The existing editor intentionally supports them | A product decision to become vertical-only |
| Project-creation canvas step | The toolbar supplies the choice and the mission supplies the default | Research finds a substantial surprised non-vertical cohort |
| Rich chips, asset-to-chat drag, interactive history pills | Contenteditable/selection/clipboard scope is not needed for resolvable mentions | Plain path tokens cause measured comprehension or editing failures |
| Proxy, artifact JSON, hidden, or arbitrary filesystem mentions | They are engine internals or exceed the selected project's media boundary | A separate, permissioned artifact-reference feature |
| Automatic timeline mutation from a mention | The user's surrounding sentence, interpreted by the agent, supplies intent | A separately specified deterministic quick action |

## Review rounds

None yet. The dispatched Phase 2 opposite-provider review will populate this
section during convergence; no implementation begins from this independent
Phase 1 proposal.
