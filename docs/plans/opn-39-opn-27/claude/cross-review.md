# Cross-review of `codex/design.md` — by `claude`

**Status: REVIEW** · reviewing `docs/plans/opn-39-opn-27/codex/design.md` (Rev 1) against
the working tree at `ae57d47`. Every line reference cited by codex was opened and checked.

**Headline: I concede the OPN-27 transport.** Codex's structured `mentions[]` sidecar is
the better design and my stated objection to it does not survive contact with codex's
actual document. I also found a real defect in *my own* Phase 1 design that codex's
approach avoids. I keep two blocking objections, both narrow, and one scope flag.

---

## 1. Verdict on each substantive decision

| # | Codex decision | Verdict |
| --- | --- | --- |
| 1 | OPN-39: scaffold writes 1080×1920; both legacy fallbacks untouched; no Python change | **Agree.** Identical to my independent conclusion. Converged. |
| 2 | Name `NEW_PROJECT_CANVAS` / `LEGACY_CANVAS` in `interp.js` | **Agree with a constraint** — see 4.1. Keep them module-private and keep `canvasOf`'s **per-field** `\|\|`, not a whole-object swap. |
| 3 | No bulk/silent migration of legacy docs | **Agree.** Same reasoning, same conclusion. |
| 4 | Render card derives its ratio from the live edit doc, fetched on the 4s asset poll | **Needs change** — this is Phase 5b item 4, out of scope (5.1), and the doc is the wrong source for that element (4.2). |
| 5 | OPN-27: all three buckets (`kinds`, `agent_renders`, `renders`) mentionable | **Agree.** Same conclusion, same reasoning. |
| 6 | OPN-27: structured `mentions[]` sidecar rather than a parsed text token | **Agree — I withdraw my alternative** (3). |
| 7 | Server validates by explicit prefix whitelist + containment, never trusts the client path | **Agree, and codex is stricter than I was.** My reuse of `resolve_source_path` has a real hole (2.2). |
| 8 | A vanished file 422s and the turn does not start | **Disagree — blocking** (4.3). |
| 9 | Composer keyboard contract (Enter only intercepted with an active result) | **Agree.** Effectively identical to mine, and codex's is more complete: it adds Shift+Enter never selecting, pointer-down-without-blur, and caret restoration. |
| 10 | Pure helpers in a new `web/src/chat/mentions.js` | **Agree**, with one note: `web/src/chat/chatUtils.js` already exists as the chat module's pure-helper file with a test beside it. Either is fine; a new file is cleaner if the helper set is three functions. Not worth arguing. |
| 11 | `projectId` passed to ChatPanel at both call sites | **Agree it is needed; needs change on how** — it is one line in the hook, not two JSX edits (4.4). |
| 12 | Tests + `scripts/dev test fast/full/smoke` ordering | **Agree.** Codex's test matrix is more thorough than mine on the component side. |

---

## 2. What codex found that I missed

Plainly, without hedging.

### 2.1 `pipeline_defs/instagram-fast-reel.yaml:192` — verified, and it is a better citation than mine

I cited the *skill prose* (`skills/pipelines/instagram-fast-reel/edit-director.md:53`).
Codex cited the *pipeline definition*, and it is a stronger artifact:

- `pipeline_defs/instagram-fast-reel.yaml:192` — a `review_focus` gate:
  `"render_runtime carried UNCHANGED from the brief; compose_target set to 1080x1920 (9:16)"`
- and, which neither of us cited, `:218` (`Output canvas is 1080x1920 (9:16) and
  watermark-free`), `:251` (a `visual_qa` probe with `expected {width:1080, height:1920}`),
  and `:260` (`canvas: "1080x1920 (9:16)"`).

That changes the strength of the argument. I wrote "the pipelines already set 9:16", which
is guidance. Codex's anchor shows the packaged pipeline **verifies** it — a render that
comes out 16:9 fails a QA gate. So the agent path is not merely nudged vertical, it is
enforced vertical, which tightens the case that the manual scaffold is the only real gap.
Codex's citation should be the one that survives into the merged plan.

### 2.2 Codex's validation is tighter than mine, and my `resolve_source_path` reuse has a real hole

I proposed reusing `server/editor.py:68-120` verbatim. Codex instead specifies an explicit
prefix whitelist (`assets/`, `hf/renders/`, top-level `renders/`) plus containment. Codex
is right, for a reason I did not see when I wrote my design:

`resolve_source_path` tries candidates in this order (`server/editor.py:108-112`):

```
  1. Path(projects_dir).parent / raw     <- REPO ROOT first
  2. proj / raw                          <- the project second
  3. Path(projects_dir) / raw
```

and its containment check (`server/editor.py:118`) accepts the project **or** the shared
repo asset library (`shared_root`, `server/editor.py:89`). `<repo>/assets/` exists and
contains `sfx/`. So a mention of `assets/sfx/whoosh.wav` resolves to the **repo** file,
not the project's — silently, because candidate 1 wins. Today `<repo>/assets/` has no
`video/`, `audio/`, `images/` or `music/` subdirectory, so the common case cannot collide
*yet*; the ordering, not any path-space disjointness, is what is protecting it. That is a
latent shadowing bug in my design and codex's project-first, prefix-whitelisted resolution
does not have it.

**Adopt codex's validation.** For mentions, resolve `(proj / rel).resolve()` and require
`proj` to be a parent — do not route through `resolve_source_path`.

### 2.3 The Studio wrapper — codex did not miss it

My Phase 1 prediction that codex would trip over `Studio.jsx:255` is **falsified twice
over**: codex names it in its files-that-change (`web/src/studio/Studio.jsx:255`), in
its ownership section ("The Studio autosave wrapper must forward both arguments while
still flushing before the turn"), and as a row in its risk register with a test attached.
It found the trap independently and mitigated it. My whole stated reason for preferring a
text token was that trap. See section 3.

### 2.4 Things codex specified that I under-specified

- **Pointer-down selection without blurring the textarea.** A mousedown-blur would collapse
  the menu before the click lands. I did not mention it.
- **Caret restoration after insertion** as an explicit tested behaviour.
- **Shift+Enter never selects a mention.** My table implied it; codex states it.
- **Arrow wrapping** and **duplicate-basename** as named test cases.
- **"assert the runner is not called on rejection"** — the right shape for a boundary test.
- **`tests/contracts/test_server_agent_api.py`** exists (verified) and is the correct home
  for the endpoint-level test. I proposed `test_editor_api.py`, which was right only for
  where *I* was putting the helper.

---

## 3. The transport conflict — I withdraw my token and adopt codex's sidecar

The coordinator asked me to engage codex's rebuttal on the merits rather than on the
wrapper bug. Having done so, codex wins. Here is the honest accounting.

**My objection collapses.** I argued the sidecar was worse because
`web/src/studio/Studio.jsx:255-256` is a single-argument wrapper that would silently drop
a second parameter inside the editor. That fact is true (coordinator-verified), but it is
a one-line fix — `(text, ...rest) => { await flushAutosave(); return chat.send(text, ...rest) }`
— and codex already planned it and put a test on it. An objection whose entire content is
a bug the other design already fixes is not an argument.

**On the merits that remain:**

| Dimension | Token `@[path]` (mine) | Sidecar `mentions[]` (codex) | Winner |
| --- | --- | --- | --- |
| Prose parsing | Server regex `@\[([^\]\n]+)\]` | None — nothing is reparsed | **codex** |
| Filenames with `]` | **Breaks.** Not exotic: any download manager produces `video[1].mp4` | Unaffected | **codex** |
| Filenames with spaces | Works at the server; only the *query* can't span a space | Works | tie |
| Editing the draft after inserting | `@[assets/a.mp4]` hand-edited to `@[assets/b.mp4]` silently changes the target with no validation until the server says NOT FOUND | Pruned by exact-token match; an edited token stops being a reference | **codex** |
| New state to maintain | None | A selected-refs list pruned on every keystroke (~10 lines) | mine, marginally |
| API surface | Unchanged | `ChatRequest` gains a field; `send()` gains a parameter | mine, marginally |
| Round-trip through a saved thread | The token *is* the reference, so re-sending a persisted message re-resolves identically | The sidecar is not part of `messages[].text`, so a re-sent old message is inert prose | mine — **but fixable**, see amendment B |
| What the agent's own history shows | Prose + resolution block | Prose + resolution block | tie |
| Behaviour on rename between turns | Neither survives | Neither survives | tie |

The two rows I thought were mine — "what the agent history shows" and "rename" — are
washes once you look at them. Both designs append a resolution block to the message the
runner receives, so both leave the same trace in the SDK session; and neither can survive
a rename that happens after the turn. I should not have implied otherwise.

That leaves exactly one durable advantage for the token — thread round-tripping — and it
is recoverable inside codex's design (amendment B below). Against it stands a filename
class my regex genuinely cannot represent, in a product whose own RULES.md says *"the user
can drop in any kind of media, so our code should be prepared for that."* A transport that
degrades on `video[1].mp4` is the fragile choice here.

**Verdict: adopt the structured sidecar. My token design is withdrawn.**

Two amendments I want carried with it:

**Amendment A (blocking) — the 422 must go.** See 4.3.

**Amendment B (non-blocking, recovers the one thing the token was better at) — persist the
mentions on the stored message.** `ThreadSave.messages` is `list[Any]`
(`server/app.py:124-125`), i.e. untyped, so attaching `mentions` to the stored user
message costs nothing in schema or validation. Then re-opening a thread and re-sending an
earlier message re-resolves identically instead of degrading to prose. Cheap, and it
closes the only gap in codex's transport.

---

## 4. Blocking and needs-change items

### 4.1 `LEGACY_CANVAS` must not change `canvasOf`'s per-field structure — needs change (small, but it will bite)

I agree with naming the constants; it directly mitigates the risk I ranked #1 in my own
design (the next engineer editing `interp.js:577` without knowing `video_compose.py:1141`
is its twin). Keep them module-private — an exported constant with no external caller is a
public surface for nothing.

The constraint codex's wording leaves open: `canvasOf` today defaults **per field**
(`interp.js:576-580` — `Number(ct.width) || 1920`, and separately for height and fps), not
as a whole object. `setCanvas` merges (`interp.js:562-571`), so a partial
`compose_target` such as `{fps: 24}` is reachable, and the **existing test at
`web/src/editor/interp.test.js:322` exercises exactly that**:
`canvasOf(setCanvas(d, {fps: 24}))` must keep `d`'s width and height. An implementation
that reads "use `LEGACY_CANVAS` when a saved document omits the field" as *return the
object* would break that test and change behaviour for partial targets. Name the literals;
do not restructure the function. Also: never return the shared constant object by
reference — callers would share a mutable canvas.

### 4.2 Deriving the render card's ratio from the edit doc is the wrong source — needs change (and see 5.1: it is out of scope anyway)

Setting scope aside for a moment, the technical objection stands. `.render-item video`
(`web/src/styles.css:480`) is a real `<video>` with a real file
(`web/src/App.jsx:1341-1342`). The browser already knows the true ratio of the bytes on
screen. Deriving the box from the *project canvas* means:

- **A stale render is mis-boxed.** Codex's own sentence concedes the failure mode — *"retain
  `object-fit: contain` so stale or earlier output with a different intrinsic ratio is not
  cropped."* That is: put a 16:9 render in a 9:16 box and letterbox it. The `renders`
  bucket explicitly carries earlier renders and a `current: false` flag
  (`server/app.py:481-485`), so this is the normal case, not the edge case.
- **The absent-doc branch invents a ratio.** *"If content is absent, it uses
  `canvasOf(scaffoldEditDecisions())`"* — so a project that has a render but no
  `edit_decisions.json`, or one where the fetch simply failed, gets a 9:16 box around
  whatever the file actually is. Deriving from the file never needs a guess.
- **It puts a second request on a 4-second poll.** `App.jsx:1288-1295` polls
  `listAssets` every 4000ms for as long as the dashboard is open. Adding
  `getEditDecisions` to that tick doubles it forever, for a cosmetic ratio.
- **It needs plumbing that surface does not have.** `AssetPanel` is on the agent page and
  holds no editor document.

**Where codex is right and I was not:** deleting `aspect-ratio` outright is not free.
A `<video>` with no ratio and unloaded metadata has a default intrinsic size of 300×150,
so the card renders at 2:1 and then jumps — a layout shift, which is exactly what the
ratified plan's Phase 3 ("stop the layout jumps") exists to prevent. I did not weigh that
in Phase 1. Codex's approach has no shift. That is a genuine advantage.

The resolution is not to pick between them here — it is that **neither ticket requires
this element to change** (5.1). When Phase 5b item 4 runs, the option worth costing is
intrinsic-ratio with the shift absorbed (`onLoadedMetadata` → set `style.aspectRatio`, or
a neutral reservation), not a doc fetch on a poll.

### 4.3 A vanished file must not 422 the turn — blocking

Codex: *"If a selected file vanishes before send, the server returns a clear 422 and does
not start the agent. It must never silently drop a reference or let the agent guess
another same-named file."*

I agree completely with the second sentence and disagree with the first.

The failure mode is concrete. `useAgentChat.send` clears the draft and appends the user
message to the transcript **before** the request goes out (`web/src/chat/useAgentChat.js:116-117`),
and the error path appends an error line without restoring `input`
(`web/src/chat/useAgentChat.js:186-188`). So a 422 leaves the user looking at their own
message in the transcript, an error beneath it, an empty composer, and no way back to the
text they typed except retyping it.

And this is a *race we cause*, not user error: `hf/renders/*.mp4` are written and replaced
by the agent during its own turns, and the composer's candidate list is a snapshot. A user
who picks an agent clip and then types a sentence is inside the window.

Hard-failing a whole turn to protect against a stale reference is the wrong trade when a
strictly better outcome exists: resolve what resolves, and hand the agent
`- hf/renders/scene2.mp4 -> NOT FOUND in this project` for the rest. That is not "silently
dropping" and it is not "letting the agent guess" — it is the opposite of guessing; the
agent asks. The turn survives, the user's sentence survives.

**Keep the 422 for exactly one case: a containment or whitelist violation.** A path that
escapes the project is a client bug or an attack, never a race, and it should fail loudly
before the runner. That gives a clean, defensible split:

```
  path escapes project / not in the whitelist  -> 422, runner never called
  path is in-bounds but the file is gone       -> turn proceeds, ref marked NOT FOUND
```

Codex's own test line — *"assert the runner is not called on rejection"* — stays valid for
the violation case, which is where it matters.

### 4.4 `projectId` plumbing is one line, not two — needs change (minor)

Verified: `ChatPanel` takes `{ chat, disabled, className, auth, onReconnect }`
(`web/src/chat/ChatPanel.jsx:15`) — no `projectId`. Codex is right that it is needed, and
its two anchors are exact (`web/src/App.jsx:143`, `web/src/studio/Studio.jsx:698`). I did
not name this gap in my own file list; that was an omission on my part.

But the hook already has the value: `useAgentChat(projectId, ...)`
(`web/src/chat/useAgentChat.js:23`) and its return object omits it
(`web/src/chat/useAgentChat.js:286-293`). Adding `projectId` to that returned object
reaches both call sites with no JSX edits, and Studio's `{ ...chat, send: wrapped }`
spread (`web/src/studio/Studio.jsx:255-256`) carries it through. One line instead of two
edits in two files.

### 4.5 Substring pruning needs a named test (minor)

*"Prunes any selected reference whose exact inserted token no longer exists"* is an
`includes()` check, which is substring-sensitive. It happens to be safe for the obvious
collision — `@renders/final.mp4` is not a substring of `@hf/renders/final.mp4`, because
the `@` is anchored — but that safety is accidental, not structural. Add it as a test case
rather than relying on the accident, and dedupe server-side regardless (codex already
specifies dedupe, which makes the duplicate-token case harmless).

---

## 5. Scope creep — both designs

### 5.1 Codex: the render card is Phase 5b item 4, and the brief excluded it

The shared brief said: *"Do not pull in the remaining UI-polish phases (4, 5a, 5b, 6, 7).
Where OPN-39 genuinely overlaps Phase 5b, say so and propose how they sequence — but do
not expand scope into them."*

Codex's design has the render card in its defects list (#2), in its decision text, in its
files-that-change (`web/src/App.jsx`, `web/src/styles.css`), as step 2 of six in its
implementation order, in its risk register, and in its success condition (*"no render card
uses an unconditional ratio"*). It is load-bearing throughout.

I want to be fair about why: the ticket says "the editor's canvas", and reading the render
card as part of the canvas experience is defensible. And codex is right that
`styles.css:480` is a genuine unconditional-ratio bug in a vertical-first product. But it
is `docs/plans/ui-polish-audit/agreed-ui-polish-plan.md:286` — already ratified, already
scheduled, already owned — and the brief drew the line explicitly.

**Recommendation for the merged plan:** cut the render card from OPN-39 entirely. Carry
codex's finding and my 4.2 objection forward as a one-paragraph amendment to Phase 5b
item 4, noting that (a) the plan's own anchor `styles.css:334` has drifted to
`styles.css:480`, and (b) the two candidate sources (doc canvas vs. intrinsic) trade CLS
against stale-render correctness and that trade should be decided in 5b, not here.

Dropping it also shrinks OPN-39 to `interp.js` + tests — which is the right size for a
ticket whose entire defect is "a new document does not state its canvas."

### 5.2 Mine: two small ones, self-flagged

- The **20-mention cap** I specified is a bound nobody asked for. Codex's dedupe covers the
  realistic case. Drop it.
- I proposed amending Phase 5b item 4 (and explicitly did not build it). Under 5.1 that
  amendment is still the right vehicle, but it should be one paragraph in the merged plan,
  not a section.

### 5.3 Codex: two borderline items, not worth blocking

- *"Can show the cached list while the refresh is in flight"* — stale-while-revalidate on a
  directory listing. Nice, not required. Fine either way.
- *"Mouse selection happens on pointer-down without blurring the textarea first"* — reads
  like polish but is load-bearing (a blur would close the menu before the click). Keep.

---

## 6. Line-reference audit

Every reference codex cited, checked against the tree.

**Exact:** `interp.js:573`, `interp.js:562`, `interp.js:193` (the JSDoc line for
`scaffoldEditDecisions`, whose signature is `:194` — fine as an anchor),
`video_compose.py:1132`, `:1141`, `:1149`, `model.js:28`, `model.js:126/247/265/302/315/322`,
`StudioPreview.jsx:108`, `StudioToolbar.jsx:14`, `Studio.jsx:131`, `:134`, `:255`, `:698`,
`styles.css:480`, `App.jsx:143`, `App.jsx:1288`, `ChatPanel.jsx:132`, `:140`, `api.js:226`,
`server/app.py:421`, `:458`, `agent_runner.py:602`, `:1243`,
`agreed-ui-polish-plan.md:286`, `tests/tools/test_compose_transitions.py:215`,
`schemas/artifacts/edit_decisions.schema.json:299`,
**`pipeline_defs/instagram-fast-reel.yaml:192`**.

`tests/contracts/test_server_agent_api.py` exists. ✓

**Two off-by-a-line, both harmless, both worth correcting in the merged plan:**

- `server/agent_runner.py:684` is `return ClaudeAgentOptions(`; the `cwd=str(repo_root)`
  fact it is cited for is at **`:685`**.
- `schemas/.../edit_decisions.schema.json:294` is the `"metadata"` key; `compose_target`
  itself begins at **`:297`**. Defensible as an anchor for "an allowed metadata field".

**One anchor that points at the right function but not the right statement:**

- `server/app.py:912` is the `@app.post("/api/projects/{project_id}/chat")` decorator. The
  `await runner.run_turn(project_id, body.message, ...)` call that the enrichment must
  precede is at **`:974`**. Cite `:974` in the merged plan — that is the line an
  implementer edits.

**No fabricated references.** Nothing codex cited failed to exist.

---

## 7. What I propose we merge

Short version, so Phase 3 has a starting point.

**OPN-39** — converged, minus the render card.
- `scaffoldEditDecisions` writes `metadata.compose_target = {1080, 1920, 30}`; both legacy
  fallbacks untouched; no Python change.
- Codex's named constants, module-private, with `canvasOf`'s per-field structure preserved
  (4.1).
- Codex's `pipeline_defs/instagram-fast-reel.yaml:192` citation replaces my skill-prose one.
- My cross-reference comment on `canvasOf` naming `video_compose.py:1141` as its twin, plus
  the explanatory comment on `interp.test.js:318-323` — both mitigate the same risk codex's
  constant names mitigate, and they are cheap enough to keep all three.
- Render card: **out**, carried to Phase 5b item 4 as an amendment (5.1).
- Files: `web/src/editor/interp.js`, `web/src/editor/interp.test.js`,
  `web/src/studio/model.test.js` (the preset-drift guard). That is the whole ticket.

**OPN-27** — codex's design, with amendments.
- Structured `mentions[]` sidecar; codex's prefix whitelist + project-first containment,
  **not** `resolve_source_path` (2.2).
- Codex's keyboard contract and test matrix verbatim.
- Amendment A: vanished file degrades to `NOT FOUND` in the reference block; 422 reserved
  for containment/whitelist violations (4.3). **Blocking.**
- Amendment B: persist `mentions` on the stored thread message (3).
- `projectId` via the hook's return object, not two JSX edits (4.4).
- Substring-prune test (4.5). Drop my 20-mention cap (5.2).

**Open question for the human, unchanged from my Phase 1 doc:** whether legacy
canvas-less manual projects should ever get an explicit one-time "set this project to
9:16?" prompt. Both designs deliberately leave them at 16:9; neither of us can decide that
for the product.
