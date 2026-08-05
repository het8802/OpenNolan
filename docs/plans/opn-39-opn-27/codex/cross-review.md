# Cross-review of Claude's OPN-39 + OPN-27 design

Status: PLAN REVIEW · Codex review of `claude/design.md`

## Verdict

OPN-39 is converged: write 1080x1920@30 into a fresh editor scaffold and do
not alter either legacy fallback. OPN-27 needs a revision: retain the shared
goal of server-resolved absolute paths, but carry selected assets in an explicit
`mentions[]` sidecar and repair the single dropped call boundary in Studio.

I checked 68 `file:line` anchors in Claude's document for existence and line
range. The sole deliberately stale historical-plan reference is
`styles.css:334`; all current source anchors resolve, and two claims attached
to valid anchors need correction, described below.

## OPN-39 decisions

| Claude decision | Verdict | Review |
| --- | --- | --- |
| Fresh scaffold writes `metadata.compose_target = 1080x1920@30` | **Agree** | This is the smallest safe lever. `Studio` creates the scaffold only when the API returns no document (`web/src/studio/Studio.jsx:131`), and the renderer honours the explicit target (`tools/video/video_compose.py:1149`). |
| Preserve `canvasOf` and Python 1920x1080 fallbacks | **Agree** | The literals still match at `web/src/editor/interp.js:577` and `tools/video/video_compose.py:1141`. Changing either fallback would either break preview/export or reinterpret legacy canvas coordinates. |
| Preserve all four canvas presets and existing-project meaning | **Agree** | Explicit 1920x1080 and metadata-free legacy docs remain unchanged; the latter is the critical compatibility case. |
| Add the scaffold and legacy-fallback tests, but no new Python behavior test | **Agree** | The existing explicit vertical compose test at `tests/tools/test_compose_transitions.py:215` already covers Python's unchanged behavior. |
| Defer `.render-item video` aspect work from OPN-39 | **Agree; remove it from the converged OPN-39 diff** | My Phase 1 proposal pulled `web/src/App.jsx` and `web/src/styles.css:480` forward. That is scope creep. The card is the Assets/dashboard surface scheduled as Phase 5b item 4 (`docs/plans/ui-polish-audit/agreed-ui-polish-plan.md:286`), not the editor canvas, and that phase is explicitly gated by frame-cache work. OPN-39 should name the hardcode as an audited inconsistency, not modify it. |
| Amend Phase 5b to rely on intrinsic video ratio | **Needs change** | Claude identifies a real stale-render problem: the live project canvas can differ from an earlier render's pixels. But that is an amendment to an already-ratified Phase 5b choice, not a decision this two-ticket plan should make. Record the observation for Phase 5b; do not silently replace its stated project-canvas rule here. |

Claude found two useful details I had missed: the editor render job never supplies a
profile (`server/render_jobs.py:540`), so the renderer's profile rung is inert on
this path; and the old `web/src/editor/Editor.jsx` also calls the shared scaffold
(`web/src/editor/Editor.jsx:40`) even though it is presently unmounted. Neither
requires a new OPN-39 behavior change.

## OPN-27 decisions

| Claude decision | Verdict | Review |
| --- | --- | --- |
| Mention all of `kinds`, `agent_renders`, and top-level `renders` | **Agree** | All are returned with project-relative paths by `/assets` (`server/app.py:438`, `server/app.py:452`, `server/app.py:492`) and are useful references. Proxies and hidden/internal files remain excluded. |
| Fetch a flat `listAssets` list when the menu opens | **Agree** | It is fresher than threading two parent asset states through shared `ChatPanel`, and it fits autocomplete better than `/browse`. |
| Keep textarea, use pure chat helpers, and preserve Enter/Shift+Enter | **Agree** | `chatUtils.js` is the existing pure chat-helper home; listbox keyboard behavior is the right bounded scope. Add tests for Tab, click, each bucket, duplicate filenames, and stale selection in addition to Claude's listed cases. |
| Visible `@[path]` token is the only wire format | **Disagree — blocking design change** | It introduces a lossy mini-language into free-form user text, rejects valid filenames containing `]`, and makes user-typed tokens an authorization input. A structured sidecar binds the exact selected candidate without parsing prose and supports every filename that the app permits. |
| Reuse `resolve_source_path` unchanged as the server authorization boundary | **Disagree — blocking security/scope change** | Its valid containment condition allows either the selected project **or the shared repository asset root** (`server/editor.py:118`), and it accepts absolute input (`server/editor.py:103`). A manually typed token could therefore be expanded for a non-project shared asset; it can also name project internals rather than one of the three listed asset buckets. Mentions need a narrow resolver/validation wrapper that accepts only listed project-relative media under `assets/`, `hf/renders/`, or top-level `renders/`. Reusing lower-level resolution after that validation is fine. |
| Unknown/traversal token becomes `NOT FOUND` text and still starts the agent | **Needs change** | A selected asset that disappears must be a clear rejected send (422) before `run_turn`, not an ambiguous prompt the agent may improvise around. This is especially important because the agent cannot infer whether a malformed token was a user request or a failed reference. |
| Same-object return and a fixed 20-token expansion cap | **Needs change** | String identity is not a useful Python contract and comes from the JS document-mutator rule, not server helpers. A bounded mention count is reasonable, but it belongs in the typed request validation and should return a clear client error rather than silently omit later selections. |

## Transport decision: use a typed sidecar

Choose the sidecar:

```text
composer selection
  -> visible text stays readable
  -> POST /chat { message, mentions: [{path, token}] }
  -> server validates each selected project media path
  -> server appends canonical absolute paths to runner prompt
```

Claude is correct about the failure in my original sketch: Studio currently wraps
only one parameter at `web/src/studio/Studio.jsx:255` and drops a second argument
at `:256`. That is a required implementation step, not a reason to choose a
weaker transport. Make the wrapper variadic (or explicitly accept `text,
mentions`) and forward both after `flushAutosave`.

The source search found no other production wrapper that assumes an exactly
one-argument `send`:

- `ChatPanel` calls `send()` today (`web/src/chat/ChatPanel.jsx:132` and `:140`)
  and will call it with the sidecar only when a selection exists.
- `useAgentChat.send` is the current single-parameter implementation
  (`web/src/chat/useAgentChat.js:113`) and becomes the sole transport owner.
- `api.chatStream` is called only from that hook
  (`web/src/chat/useAgentChat.js:131`), so it can accept the optional payload
  without a second UI surface diverging.
- The remaining one-argument mocks are test fixtures, notably
  `web/src/chat/ChatPanel.test.jsx:25` and `web/src/studio/Studio.test.jsx:17`.

The sidecar does add an explicit `ChatRequest` field and test coverage, but that
is clearer and safer than an implicit regex protocol. It preserves the human
transcript exactly as Claude values, while the server, not the model, remains the
authority that maps a selected project asset to an absolute readable path.

## Agent-session correction

Claude's cited lines are real, but the stated lifecycle is partly wrong.

- Correct: options use `cwd=str(repo_root)` at
  `server/agent_runner.py:685`; project data is separately supplied via
  `add_dirs` at `server/agent_runner.py:706`.
- Correct: a warm client sends the raw new message after its initial turn:
  `run_turn` has `prompt = message` and adds the preamble only for `is_fresh`
  (`server/agent_runner.py:1930`, `server/agent_runner.py:1944`).
- Correction: "first-turn only" means first turn of **each freshly created or
  resumed client**, not only turn one of an application's lifetime. `_get_client`
  marks every new client fresh (`server/agent_runner.py:1191`), and a backend
  restart, thread switch, or model switch tears down/rebuilds the client
  (`server/agent_runner.py:2026`, `server/agent_runner.py:2064`). The preamble
  expressly documents restart recovery at `server/agent_runner.py:1940`.

So backend restart and thread switch are not cases where a bare relative reference
loses the new preamble; warm later turns are. In either case, absolute server
resolution remains the right requirement because a mention must not depend on
conversation context or remembered cwd instructions.

## Anchor and scope findings

Except for the historical agreed-plan `styles.css:334` reference explicitly
called out as stale, Claude's current source paths/lines exist. These are
semantic corrections, not line drift:

1. `server/editor.py:118` does not enforce the selected project alone; it also
   permits the shared repository asset root. Claude must not characterize it as
   sufficient mention containment.
2. `server/agent_runner.py:1946` is accurately cited but not evidence that a
   restart or thread switch loses project context; fresh-client reconstruction
   runs it again.
3. The current CSS anchor really is `web/src/styles.css:480`; the agreed plan's
   `styles.css:334` reference has drifted.

Scope creep to remove in the converged plan:

- **Codex:** the App render-card ratio fetch/CSS change from my Phase 1 design;
  defer it with Phase 5b.
- **Claude:** the proposed Phase 5b amendment to intrinsic-ratio layout. Preserve
  it as a handoff observation, not a decision for OPN-39.
- **Claude:** a custom regex grammar, identity contract, and silent `NOT FOUND`
  prompt behavior are extra machinery that sidecar validation removes.

## Required convergence edits

1. Keep Claude's OPN-39 scaffold-only implementation and its legacy tests; make
   no App/CSS change in this ticket pair.
2. Keep Claude's candidate scope, menu placement, keyboard contract, and
   absolute-path requirement.
3. Replace token parsing/expansion with optional typed `mentions[]` from
   `ChatPanel` through `useAgentChat`, `api.chatStream`, `ChatRequest`, Studio's
   forwarding wrapper, and a narrow server validator.
4. Test Studio-side forwarding explicitly, every allowed bucket, paths with
   spaces and `]`, duplicate names, stale files, traversal/absolute/shared paths,
   and the guarantee that invalid references never call `runner.run_turn`.

No blocking disagreement remains after those changes. The only deferred product
question is how Phase 5b should display a stale render whose intrinsic ratio no
longer matches the live canvas.
