# Claude SDK-native agent runner

**Status:** PLAN
**Revision:** rev 4
**Author:** Codex
**Date:** 2026-08-14

The detailed reasoning, risks, and verification plan are in
[plan.md](plan.md). This document shows the intended boundary in one pass.

## Decision

OpenNolan should stop reimplementing generic agent-runtime behavior already
provided by the pinned Claude Agent SDK. The SDK should own:

- OS-enforced containment for Bash and its child processes, only after packaged
  egress and unavailable-state probes prove product equivalence;
- built-in tool availability and declarative permission rules;
- the always-run tool interception point;
- live model switching;
- structured result, rate-limit, task, usage, and failure data; and
- Claude CLI discovery.

OpenNolan should continue owning:

- project and pipeline context;
- the default-deny path boundary for built-in file tools;
- Mission Control UI/SSE adaptation;
- user approval, key, and capability-install interfaces;
- clarifying questions until the pinned native flow passes a live probe;
- render/media job durability and publishing;
- canonical asset placement and content scheduling;
- product analytics and project document deltas; and
- explicit thread selection and crash recovery.

This is not a recommendation to replace 2,873 lines with one SDK flag. It is a
recommendation to remove generic runtime policy while preserving product
semantics.

## Verified runtime

| Item | Verified value | Evidence |
|---|---|---|
| Python SDK | `claude-agent-sdk==0.2.133` | `requirements-ui.txt:14` |
| Bundled CLI | Claude Code 2.1.225 | Installed SDK `_cli_version.py:3` and executable `--version` |
| SDK sandbox option | Present, coupled to egress | Installed `ClaudeAgentOptions.sandbox` and CLI network mediation |
| Live model switch | Present | Installed `ClaudeSDKClient.set_model` |
| Hooks | Present | Pre/Post/Failure/Permission/Stop hook types |
| Native question flow | Unproven | CLI contains it; pinned Python headless contract is undocumented |
| Native status | Present | Result, rate-limit, and task message types |

Current official behavior was checked against Anthropic's
[Python SDK reference](https://code.claude.com/docs/en/agent-sdk/python),
[sandbox documentation](https://code.claude.com/docs/en/sandboxing),
[permission documentation](https://code.claude.com/docs/en/agent-sdk/permissions),
and [user-input documentation](https://code.claude.com/docs/en/agent-sdk/user-input).

The pinned runtime remains the compatibility target. Online documentation alone
is not sufficient evidence for a future SDK upgrade.

## Today

```text
User message
    |
    v
AgentRunner.run_turn
    |
    +--> wait up to 150 ms for an unsolicited turn
    |       `-- source count is always reported as zero
    |
    +--> prepend project/resume/render prose
    |
    +--> ClaudeSDKClient.query
             |
             +--> built-in tools are implicit
             |
             +--> can_use_tool
             |       |
             |       +--> custom path resolver and root list
             |       +--> static shell token/path parser
             |       +--> destructive-command regexes
             |       +--> render/media routing regexes
             |       +--> unknown tool -> user confirmation
             |       `--> custom Mission Control confirmation
             |                ★ DIVERGENCE: runner.py:455
             |
             +--> custom mcp__mc__ask_user
             |       `--> duplicate question future and UI schema
             |                ★ DIVERGENCE: runner.py:1283
             |
             +--> AssistantMessage / UserMessage / ResultMessage
             |       `--> custom event projection and tool-result join
             |                ★ DIVERGENCE: runner.py:924, 1045
             |
             `--> model change -> disconnect -> rebuild -> resume
                      ★ DIVERGENCE: runner.py:2842
```

The important problem is not merely duplication. Security, approval, and
application routing all enter through one callback even though the SDK evaluates
those concerns at different points.

## Target boundary

```text
User message
    |
    v
OpenNolan application plane
    |
    +--> project/thread context
    +--> Mission Control SSE and approval UI
    +--> render/media/store/schedule/key/capability tools
    `--> product analytics and durable project state
    |
    v
Claude SDK execution plane
    |
    +--> tools: explicit built-in availability
    +--> strict_mcp_config: only registered MCP servers
    +--> sandbox: conditional OS-enforced Bash containment
    +--> permissions: allow / ask / deny defense in depth
    +--> PreToolUse: file boundary + must-run OpenNolan routing
    +--> can_use_tool: unresolved user approval
    +--> hooks / result messages: richer lifecycle data
    +--> set_model: live model change
    `--> SDK CLI discovery: bundled CLI -> system fallback
    |
    v
Claude Code 2.1.225 child process

==========================================================================
EXECUTION CONVERGES HERE -- SDK runtime plus OpenNolan's file-tool boundary
==========================================================================
```

The application plane decides what a render means and which roots built-in file
tools may access. The SDK plane contains Bash, decides which generic tools
exist, and supplies the always-run and interactive policy points.

## Replacement findings

### 1. Replace only the static Bash path parser

Current surface:

- `server/agent_runner.py:149-188` defines custom sandbox types and path tools.
- `server/agent_runner.py:195-299` resolves paths and parses Bash tokens.
- `server/agent_runner.py:331-372` builds one root list for both reads/writes.
- `server/agent_runner.py:470-477` checks built-in file tools.
- `server/agent_runner.py:510-517` asks on apparent Bash path escapes.

Correct split:

```text
Bash and child processes
    -> Claude SDK sandbox (Seatbelt on macOS)
    -> removes static shell token/path guessing

Read / Write / Edit / Glob / Grep
    -> keep canonical OpenNolan root check
    -> SDK permission rules add defense in depth

Network
    -> enabling sandboxing may mediate egress even with no domain list
    -> packaged probe must preserve arbitrary outbound HTTPS without prompts
    -> otherwise the native sandbox is not an equivalent replacement
```

The installed `SandboxSettings` explicitly scopes filesystem permission rules
outside the sandbox type. Native sandbox `denyRead`/`allowRead` are deny regions
over a default-readable host; they are not equivalent to today's default-deny
root allowlist. An enumerated denylist would miss volumes, shared locations,
other users, and future sensitive paths. Denying `/` and reopening roots can
also mask system executables needed by ffmpeg and Python.

What the native layer genuinely replaces:

- Seatbelt on macOS applies to the actual process and child processes.
- Shell variables, substitutions, pipes, and quoting do not bypass OS policy.
- Regex and `sed` patterns are not misclassified as file paths.
- The session temp directory is native and writable by default.

What remains custom is small and security-critical: resolve candidate paths,
canonicalize them, and reject built-in file-tool access outside the allowed
roots. Move the check at `server/agent_runner.py:470-477` into `PreToolUse`,
which runs even when an SDK allow rule would shadow `can_use_tool`. Keep the
existing pure contract tests for this boundary.

Do not authorize parser removal until two packaged probes pass. First, a
sandboxed Bash child must reach provider, CDN, and arbitrary test HTTPS hosts
without an approval. A finite domain list and `excludedCommands` are not
equivalent because the product accepts arbitrary URLs and its generation
interpreter still needs containment. Second, the runtime must expose a stable
sandbox-unavailable signal. That signal sets per-client state and emits a
declared event: while the legacy parser is retained it routes Bash through that
parser, and only after parser deletion does `PreToolUse` deny Bash for the rest
of the session. If no stable signal exists, use `failIfUnavailable=true` or
keep the legacy parser; never assume `false` is detectable.

`OPENNOLAN_AGENT_SANDBOX=0` continues to disable both custom and native sandbox
enforcement for development tests only and remains illegal in packaging.

### 2. Replace the generic tool classifier with SDK availability and rules

Current surface:

- `SAFE_TOOLS` and `WRITE_TOOLS`: `server/agent_runner.py:59-73`.
- safe/MCP/unknown branches: `server/agent_runner.py:478-530`.
- only two tools explicitly removed: `server/agent_runner.py:1582-1587`.

Native replacement:

| Concern | SDK control |
|---|---|
| Which built-ins Claude sees | `tools=[...]` |
| Which safe calls skip approval | `allowed_tools=[...]` |
| Which calls can never run | bare `disallowed_tools` names / settings deny rules |
| Which calls always ask | settings `permissions.ask` |
| Which MCP servers exist | `strict_mcp_config=True` |
| Interactive unresolved call | `can_use_tool` |

The explicit built-in list should include only production needs such as Read,
Write/Edit, Glob/Grep, Bash, web tools, and Skill. Include `BashOutput` and
`KillShell` while automatic Bash backgrounding remains reachable. Add
`AskUserQuestion` only if its packaged-runtime probe passes. Exact names must be
proven against the pinned CLI before implementation.

The seven `mcp__mc__*` tools can be auto-approved with an SDK wildcard rather
than a Python prefix branch. Unknown tools should be absent from model context,
not shown and then turned into a surprise user prompt. Reserve
`allowed_tools`/`disallowed_tools` for names. Any path-bearing policy belongs in
structured settings JSON so spaces and commas cannot corrupt CLI tokenization.

This also reduces tool-choice noise. It does not by itself force the model to
use Read instead of `cat`; Bash remains necessary for `ffprobe`, stage scripts,
and registry inspection.

For the existing "use Read, not cat" requirement:

```text
cat used for SKILL.md          -> deny; use native Skill tool
cat used for ordinary text     -> steer to Read when Read can express it
cat in a real shell pipeline   -> allow only if the pipeline needs bytes/stdin
```

That steering belongs in a narrow `PreToolUse` rule. A blanket `Bash(cat *)`
deny would be simple but would also break legitimate pipelines.

### 3. Split declarative command policy from product routing

Current Bash checks at `server/agent_runner.py:81-146` and
`server/agent_runner.py:484-524` mix three policy types:

| Policy | Target SDK layer | Custom code left? |
|---|---|---|
| Bash/child-process containment | Sandbox | No Bash parser |
| Simple `git push`, reset, clean, sudo, pipe-to-shell | ask/deny rules | Usually no |
| VideoCompose/heavy-media routing | `PreToolUse` hook | Yes, small |
| Raw ffmpeg family telemetry | hook/product event | Only if product needs it |
| User decision | `can_use_tool` | UI adapter only |

`PreToolUse` is essential for render/media routing because it runs for every
tool call. `can_use_tool` is not an enforcement hook: allow rules, permission
modes, or sandbox auto-approval may resolve a call before it is consulted.
Install and verify this hook before narrowing the current callback. The hook
must read the live `turn_ctx` getter used at `server/agent_runner.py:537-555`,
not capture the session that happened to create the client. Hook matchers can
run concurrently, so telemetry state must be keyed by tool ID. The existing
one-in-flight-turn-per-project limitation at `server/agent_runner.py:1259-1266`
remains explicit.

Set `autoAllowBashIfSandboxed=false` and do not put Bash in `allowed_tools`.
The hook returns allow for safe Bash, ask for destructive Bash, and deny for
render/media route violations. This removes any precedence assumption between a
destructive ask rule and sandbox auto-approval.

The current raw-disk regex at `server/agent_runner.py:86` checks `dd if=` while
describing a write. Native device blocking closes the primary risk; any retained
rule should cover the actual `of=` destination.

### 4. Probe `AskUserQuestion`; keep `mcp__mc__ask_user` for now

Current surface:

- custom SDK MCP definition: `server/agent_runner.py:1283-1312`;
- pending question state: `server/agent_runner.py:1236-1237`;
- UI wait and resolution: `server/agent_runner.py:1773-1808`;
- built-in tool removal: `server/agent_runner.py:1582-1587`;
- duplicate prompt instructions: `server/agent_runner.py:661-663`.

The current online SDK documentation describes this native flow:

```text
Claude calls AskUserQuestion
    |
    v
can_use_tool receives questions[]
    |
    v
OpenNolan displays its question card
    |
    v
user returns labels, multi-select values, or free text
    |
    v
PermissionResultAllow(updated_input={questions, answers})
    |
    v
Claude continues in the same SDK query
```

That is not yet a verified contract for the pinned wheel/CLI pair. The symbol is
implemented in the bundled CLI but not documented in the pinned Python package.
Its schema requires two to four options and adds Skip/free-text behavior;
OpenNolan currently accepts an arbitrary option count and returns one string.

Keep the current tool and UI future until a packaged live probe proves:

1. headless `can_use_tool` receives `questions[]`;
2. `updated_input` answers resume the same query;
3. Skip, free text, timeout, and disconnect settle the future once;
4. the product accepts the native two-to-four-option constraint; and
5. an answer cannot be mistaken for a permission confirmation.

Only after that proof should the duplicate MCP schema, registration, and prompt
vocabulary be removed. This is a small code saving with a comparatively large
behavioral contract, so it is not on the recommended build path today.

The installed SDK also types a `PreToolUse` decision named `defer` and a
`ResultMessage.deferred_tool_use`. That could later replace long-held approval
futures, but it should be a separate spike: current Python documentation is not
fully consistent about defer support and the UI resume contract would change.

### 5. Replace disconnect/resume model switching

Current surface: `server/agent_runner.py:2842-2865`.

```text
TODAY
select model -> disconnect CLI -> mark resume -> rebuild CLI -> resume history

AFTER
select model -> await client.set_model(model) -> next step uses new model
```

Keep `_models` for projects that do not yet have a client. Do not set
`_resume_next` for a live model change. This removes process startup, plugin/MCP
initialization, and a session-resume failure edge.

### 6. Keep runtime discovery separate from authentication preflight

Current surface: `server/agent_runner.py:703-739`.

The SDK's resolution order is:

```text
bundled wheel executable -> PATH/system fallback -> SDK-native not-found error
```

The SDK already owns runtime executable discovery and should continue doing so.
OpenNolan's helper is consumed by `auth_configured()` as a heuristic that an
external/system Claude CLI may already be logged in. These are different
questions. Adding the always-present bundled file to the auth predicate would
make every packaged installation appear authenticated and suppress the
actionable setup response.

Keep or rename the current helper as `external_claude_cli_available()` and do
not include the bundled executable. If another caller truly needs runtime
presence, add a separate bundled-first predicate. Explicit OAuth/API credentials
or the external login heuristic allow runner creation; a live SDK 401/403 stays
the definitive auth failure.

### 7. Enrich tool telemetry with native lifecycle data

Current surface:

- `_TurnTools`: `server/agent_runner.py:1045-1135`;
- stream join: `server/agent_runner.py:2583-2623`;
- rollup: `server/agent_runner.py:2724-2790`;
- string error classifier: `server/agent_runner.py:1169-1183`.

Native data can improve, but not replace, the local accounting:

| Need | Preferred native source |
|---|---|
| Tool name, ID, input, result/failure | Pre/Post/Failure hooks |
| Reliable failed-tool naming | `PostToolUseFailure` |
| Query cost and token usage | `ResultMessage.usage/model_usage` |
| API failure status | `ResultMessage.api_error_status` |
| Stop/cancel reason | `terminal_reason` and `stop_reason` |
| Rate-limit warning/reset | `RateLimitEvent` |

`PostToolUse` has no duration field. OpenNolan must still pair starts and
finishes by `tool_use_id` and time them locally to preserve latency,
duplicate-result, and orphan-start metrics. Hooks can move that join out of the
message projector and make failures easier to name; they do not delete it.

OpenTelemetry is out of scope. It needs a collector, bypasses the current
fail-closed analytics declarations, and would require a separate content/privacy
review. Every new hook/result event and field must be declared under
`schemas/analytics/` before emission.

Keep these product-specific measurements:

- `_doc_snapshot` and timeline deltas at `server/agent_runner.py:1138-1166`;
- project analytics key at `server/agent_runner.py:2720-2722`;
- browser session and OpenNolan turn IDs;
- permission reason/root families needed for product decisions;
- asset, capability, key, render, and scheduling outcomes.

Keep `_known_or_hashed`, `_root_family`, `_bucket_seconds`, and the permission
reason classifier. Hooks expose raw inputs and absolute paths; these helpers are
the privacy boundary, not redundant lifecycle code.

### 8. Keep the warm-turn drain; optimize it separately

Current surface:

- drain timing fields: `server/agent_runner.py:1222-1224`;
- drain implementation: `server/agent_runner.py:2434-2497`;
- per-warm-turn call: `server/agent_runner.py:2516-2534`.

The warm path waits briefly for an empty queue. Several sources can be reduced,
but one remains native to the bundled CLI:

```text
ScheduleWakeup                 -> unavailable now
Bash run_in_background         -> deny/steer in PreToolUse
slow foreground Bash           -> CLI may auto-background after timeout
heavy render/media process     -> in-process MCP + RenderJobStore
background agent/task tool     -> unavailable unless explicitly needed
remaining SDK task lifecycle   -> typed task messages + stop_task
```

No `tools` option or explicit `run_in_background` deny prevents the CLI from
auto-backgrounding a slow foreground command such as an ad hoc ffmpeg pipeline.
The resulting message can otherwise become the first result of the next user
turn. Typed task events describe the task; they do not restore stream framing.
Therefore the drain is scar-tissue code, not a native-feature duplicate.

If 150 ms matters after measurement, replace polling with the persistent reader
already suggested at `server/agent_runner.py:2447-2449`. Treat that as a
separate concurrency design with a rollback flag and two-consecutive-turn
regression test. Explicit `BASH_DEFAULT_TIMEOUT_MS`/`BASH_MAX_TIMEOUT_MS` may
make the detach point intentional, but do not prove that backgrounding is gone.

The metric at `server/agent_runner.py:2522-2524` reads an undefined
`_pending_unsolicited` field and therefore reports zero. Delete or repair it
independently. `_truthy` at `server/agent_runner.py:191-192` is unused.

### 9. Consume native messages instead of flattening them

`event_of` at `server/agent_runner.py:924-1001` is still necessary because the
web UI needs stable JSON rather than Python dataclasses. It should not discard
native structure.

Add projections for:

- `RateLimitEvent` -> warning/rejected status and reset time;
- task started/progress/completed/updated -> background status;
- Result duration, usage, model usage, API status, terminal reason, errors, and
  permission denials;
- optionally `StreamEvent` with `include_partial_messages=True` for lower
  perceived text latency, behind an explicit UI gate because it otherwise
  falls through as per-token `other` SSE noise.

Keep `_classify_turn_error` only for failures that happen before any structured
result exists. Partial streaming is an enhancement, not a replacement, and must
be checked against the existing SSE renderer before enabling it.
Declare any new analytics field or failure path before the projector emits it;
the current analytics gate intentionally drops undeclared data.

## Whole-file disposition

| Current block | Disposition | Native replacement or reason |
|---|---|---|
| destructive Bash regexes | REDUCE | permission rules + narrow PreToolUse hook |
| render/heavy Bash routing | KEEP LOGIC, MOVE | PreToolUse must see every request |
| static Bash path parser | CONDITIONAL REMOVE | only after egress/unavailable probes |
| built-in file-tool root check | KEEP | native sandbox does not gate Read/Edit |
| permission analytics classifiers | KEEP | PII-safe product boundary |
| `decide_tool` | SHRINK | move file boundary/routing to hook; remove availability |
| `make_can_use_tool` | SHRINK | unresolved approval only |
| custom system prompt | KEEP | OpenNolan is not a coding-agent preset |
| external CLI auth heuristic | KEEP/RENAME | bundled runtime is not login evidence |
| auth UX | KEEP | no SDK file-exists check proves user auth |
| managed venv PATH | KEEP | app-specific provisioned runtime |
| `add_dirs` helper | KEEP | already uses the native SDK feature |
| app-skill plugin wiring | KEEP | already the native SDK plugin mechanism |
| event JSON projection | KEEP/EXTEND | UI contract; consume richer SDK types |
| `_TurnTools` join/rollup | KEEP/EVOLVE | hooks lack per-tool duration |
| document snapshot/deltas | KEEP | OpenNolan product state |
| per-project client map | KEEP | project-specific context/MCP/UI state |
| session ID/resume handling | KEEP | explicit thread and crash recovery |
| project/resume preambles | KEEP | disk/project state is not SDK session state |
| confirmation UI future | KEEP/EVOLVE | app UI; possible later defer spike |
| custom `ask_user` MCP | KEEP/PENDING PROBE | native headless contract unproven |
| API-key request tool | KEEP | OpenNolan secure BYOK flow |
| capability request tool | KEEP | OpenNolan provision packs |
| render tool/job polling | KEEP | publish/supersede/receipt semantics |
| media-op tool/job polling | KEEP | durable app job semantics |
| asset placement tool | KEEP | canonical application writer |
| scheduling tool | KEEP | application calendar state |
| render resume note | KEEP | RenderJobStore is outside SDK task state |
| unsolicited-turn drain | KEEP | CLI can auto-background slow Bash |
| `run_turn` | KEEP/SLIM | product streaming and attribution boundary |
| interrupt | KEEP | already calls native `client.interrupt()` |
| switch thread/session | KEEP | explicit user-selected session |
| model-switch reconnect | REMOVE | native `client.set_model()` |
| close clients | KEEP | application lifecycle ownership |

## Native features considered but not substitutes

| SDK feature | Why it does not replace current OpenNolan behavior |
|---|---|
| SDK sandbox filesystem settings | Enforce Bash/children but not built-in Read/Edit; adoption is also gated by coupled egress behavior and unavailable-state detection. |
| Native `AskUserQuestion` | Promising but the pinned headless contract and two-to-four-option UI constraint are not yet proven compatible. |
| OpenTelemetry | Needs a collector and separate privacy/schema approval; it does not satisfy the current PostHog analytics gate by itself. |
| File checkpointing | Rewinds Write/Edit content only; not semantic stages, Bash, directories, media, or provider side effects. |
| SessionStore | Mirrors transcripts for multi-host durability; does not replace UI thread metadata or project artifacts. |
| `continue_conversation` | Picks the most recent cwd session; unsafe for explicit multiple threads. |
| `output_format` | Validates final response, not all pipeline artifacts and side effects. |
| SDK background tasks | Do not provide RenderJobStore publishing and receipt semantics. |
| Typed task events / `stop_task` | Describe or stop tasks but do not prevent a late CLI result from crossing the next user-turn boundary. |
| Claude Code prompt preset | Wrong product surface; OpenNolan needs a custom production prompt. |
| Tool search override | Seven custom tools are below the range where an extra search is efficient. |

## Option shape after migration

This is architecture pseudocode, not implementation-ready code. Paths and rule
syntax must be resolved and tested against the packaged runtime.

```text
ClaudeAgentOptions(
  cwd=code_root,
  system_prompt=OpenNolanVideoProductionPrompt,
  model=selected_project_model,
  resume=explicit_session_id_or_none,
  setting_sources=[],
  tools=[explicit production built-ins],
  allowed_tools=[safe non-Bash built-ins, "mcp__mc__*"],
  disallowed_tools=[bare unavailable tool names],
  strict_mcp_config=true,
  permission_mode="default",
  sandbox={
    enabled: <only after packaged egress probe passes>,
    failIfUnavailable: <false only while legacy fallback is active>,
    autoAllowBashIfSandboxed: false,
    allowUnsandboxedCommands: false,
    network: <validated arbitrary-HTTPS pass-through configuration>
  },
  hooks={
    PreToolUse: [file boundary, route guard using live turn_ctx],
    PostToolUse: [local keyed tool timer],
    PostToolUseFailure: [local keyed failure adapter]
  },
  can_use_tool=MissionControlApprovalAdapter,
  plugins=[OpenNolan app skills],
  skills="all",
  mcp_servers={mc: OpenNolan product tools},
  add_dirs=[projects_dir],
  env={PATH: managed_runtime_path},
  max_budget_usd=15.0,
  max_turns=<measured safety limit>,
  stderr=MissionControlStderrAdapter
)
```

Path-bearing Read/Edit defense-in-depth rules, if used, belong in the structured
settings JSON rather than comma-joined tool lists. Before relying on them, prove
that SDK-supplied settings are honored with `setting_sources=[]`; that option is
load-bearing because it prevents developer/user Claude settings and coding
skills from leaking into the production video agent.

This block is intentionally conditional. If the pinned CLI has no supported
configuration that preserves arbitrary HTTPS without per-host prompts, leave
the native sandbox disabled and keep the legacy Bash boundary. If no stable
unavailable signal exists, do not combine parser removal with
`failIfUnavailable=false`. The file boundary hook runs regardless, and the
configured options must never let a guarded file tool bypass that hook. A
`CanUseToolShadowedWarning` caused by `skills="all"` adding the non-file `Skill`
tool is advisory and does not violate the boundary.

Do not add `max_turns` by guess. Measure successful pipeline turns first; the
budget is already a native hard ceiling and a too-low turn limit would stop
valid long productions.

## Failure behavior

```text
sandbox unavailable
    -> stable packaged signal marks client sandbox_state=unavailable
    -> emit declared sandbox_unavailable transition event
    -> built-in file root boundary remains active
    -> legacy parser present: route Bash through legacy parser
    -> legacy parser deleted: PreToolUse denies Bash
    -> user receives actionable degraded-capability state

no stable unavailable signal
    -> keep legacy Bash boundary or fail SDK startup
    -> parser removal is blocked

path outside allowed read/write boundary
    -> built-in file tool: canonical OpenNolan boundary denies
    -> Bash child: OS sandbox denies when available
    -> Claude sees the violation
    -> no generic permission prompt for a fake parsed path

destructive in-project command
    -> content-scoped ask rule
    -> Mission Control approval adapter
    -> allow once or deny with reason

render/media command attempted through Bash
    -> PreToolUse denies before execution
    -> Claude is steered to the in-process product tool

rate limit approaches or rejects
    -> RateLimitEvent
    -> UI can warn with native reset metadata

model changes
    -> client.set_model
    -> same process and conversation continue
```

## Rollout and rollback

```text
release N
    -> native Bash sandbox behind OPENNOLAN_NATIVE_BASH_SANDBOX
    -> legacy Bash parser still active
    -> compare denials, prompts, unavailable hosts, and media success

release N+1 after packaged macOS proof
    -> native sandbox is the default Bash boundary
    -> legacy parser remains as rollback backend for one release

rollback
    -> set OPENNOLAN_NATIVE_BASH_SANDBOX=0
    -> restore legacy Bash parser, never unrestricted Bash
    -> built-in file-tool boundary stays active in every state
```

Flag matrix:

```text
AGENT_SANDBOX  NATIVE_BASH  file tools       Bash             packaged
on/default     0            canonical hook   legacy parser    legal rollback
on/default     1            canonical hook   native + legacy  legal rollout
0              0 or 1       unrestricted     unrestricted     ILLEGAL
```

`AGENT_SANDBOX` abbreviates `OPENNOLAN_AGENT_SANDBOX` in the diagram;
`NATIVE_BASH` abbreviates `OPENNOLAN_NATIVE_BASH_SANDBOX`. The first remains a
developer/test escape hatch that disables both boundaries and is never a
production rollback mechanism.
After the rollback window and clean packaged telemetry, delete the legacy Bash
parser and its flag branch. The unavailable-state consequence then changes from
legacy parsing to deny-all-Bash. New events such as `sandbox_unavailable`, hook
denials, and native rate-limit/result failures must be declared before rollout.

## Surface map for an implementer

| File:line | Intended change |
|---|---|
| `server/agent_runner.py:59-73` | Replace safe/write sets with SDK configuration. |
| `server/agent_runner.py:81-146` | Split rules from narrow product routing hook. |
| `server/agent_runner.py:149-372` | Keep root boundary; remove only static Bash parsing. |
| `server/agent_runner.py:455-615` | Move boundary/routing to hook; keep approval. |
| `server/agent_runner.py:703-739` | Keep external login heuristic separate; fix the false SDK-parity comment. |
| `server/agent_runner.py:798-879` | Configure tools, strict MCP, sandbox, rules, hooks. |
| `server/agent_runner.py:924-1001` | Project richer native SDK messages. |
| `server/agent_runner.py:1045-1135` | Keep keyed timing; enrich failure naming via hooks. |
| `server/agent_runner.py:1169-1183` | Use only as pre-result error fallback. |
| `server/agent_runner.py:1230-1267` | Keep question/drain state and live turn context. |
| `server/agent_runner.py:1283-1312` | Keep until native question probe passes. |
| `server/agent_runner.py:1562-1593` | Register product tools; close built-in availability. |
| `server/agent_runner.py:1738-1808` | Keep approval and question UI adaptation. |
| `server/agent_runner.py:2434-2534` | Keep drain; remove dead metric or redesign reader separately. |
| `server/agent_runner.py:2577-2648` | Consume native result/status fields. |
| `server/agent_runner.py:2724-2790` | Keep product metrics; drop generic duplication. |
| `server/agent_runner.py:2842-2865` | Switch model through native client method. |
| `server/auth.py:87-94` | Rename/refocus helper; bundled runtime is not login proof. |
| `server/app.py:818-824` | Keep credential/external-login gate for runner creation. |
| `server/app.py:1696-1707` | Preserve no-credential actionable setup response. |
| `tests/contracts/test_agent_runner.py` | Keep file-boundary tests; remove Bash parser cases only after sandbox smoke. |
| `tests/contracts/test_agent_bundle_paths.py` | Assert packaged SDK option wiring. |
| `tests/contracts/test_packaged_restrictions.py` | Add one macOS packaged containment smoke. |
| `schemas/analytics/` | Declare every new sandbox/hook/result failure path and field. |

Deliberately untouched:

- `lib/checkpoint.py` and pipeline artifact contracts;
- `server/render_jobs.py` publishing and job semantics;
- `lib/project.py::place_asset`;
- provision packs and managed-runtime PATH construction;
- content calendar behavior;
- project-specific render/media resume notes; and
- explicit thread switching and SDK session resume.

## Build order

```text
1. Pin capability contract
   -> prove settings, question, arbitrary HTTPS, and unavailable signal

2. Install file boundary + routing hook while current policy remains
   -> forbidden routes never execute; every commit retains a boundary

3. Add native Bash containment only if both sandbox probes pass
   -> retain parser as one-release rollback; otherwise stop this migration

4. Close explicit tool availability
   -> unknown tools absent; background-output tools are intentional

5. Native set_model
   -> no disconnect or resume on model change

6. Separate SDK discovery from external CLI/login preflight
   -> bundled runtime works; unauthenticated package still gets setup response

7. Native status/usage/failure projection
   -> structured fields improve reporting; local timers/privacy remain

8. Keep drain; optionally design a persistent reader separately
   -> no off-by-one regression

9. Linux contracts + packaged macOS smoke + full verification
   -> scripts/dev test full; scripts/dev smoke
```

## What the diagrams make obvious

1. Bash containment and built-in file-tool containment are adjacent layers, not
   interchangeable features. Removing both would be a security regression.
2. `can_use_tool` currently has too many jobs. Containment, unconditional
   routing, declarative denial, and human approval have different SDK layers.
3. RenderJobStore is not redundant with SDK task support. The former publishes
   product state; the latter reports agent-runtime task state.
4. Automatic CLI backgrounding keeps the drain necessary even when explicit
   background tools are removed.

## Acceptance conditions

- A valid project Read/Write/Bash command does not ask for permission.
- Built-in file tools cannot access paths outside canonical allowed roots.
- No guarded file tool bypasses the `PreToolUse` root check. The SDK's
  `CanUseToolShadowedWarning` is advisory because `skills="all"` adds `Skill` to
  effective allowed tools.
- Sandboxed Bash cannot read host secrets or write outside allowed roots.
- Sandboxed Bash preserves arbitrary provider/CDN/user HTTPS without prompts;
  otherwise native sandbox migration remains blocked.
- A sandbox-unavailable signal selects the legacy parser while it exists and
  makes `PreToolUse` deny Bash after deletion; if no stable signal exists, the
  legacy parser remains or startup fails closed.
- A regex, URL, or `sed` expression is never mistaken for a filesystem escape.
- Destructive in-project operations still ask or deny as configured.
- Render/heavy/background Bash routes are blocked before execution.
- The custom question flow remains unless the packaged native probe passes all
  compatibility cases.
- Model switching preserves the connected client and conversation.
- A packaged install with no credential and no external CLI still returns the
  actionable authentication setup response.
- Native SDK usage/status fields are consumed where available; pre-result
  exception parsing, local tool timing, and privacy classifiers remain.
- Product render, asset, provisioning, calendar, and checkpoint behavior is
  unchanged.
- The unsolicited-turn drain remains; any reader optimization passes the
  off-by-one regression test and has a rollback flag.
- Only plan documents are changed by this architecture task.
