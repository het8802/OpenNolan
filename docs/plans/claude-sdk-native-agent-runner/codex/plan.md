# Claude SDK-native agent runner

**Status:** PLAN
**Revision:** rev 4
**Author:** Codex
**Date:** 2026-08-14

## Objective

Reduce `server/agent_runner.py` to the OpenNolan behavior the application must
own, and delegate generic agent execution, Bash containment, tool availability,
model switching, and structured result handling to native Claude Agent SDK
features where those features are genuinely equivalent. Retain the local
boundaries for which the pinned SDK has no equivalent.

This is an analysis and architecture task. No implementation is authorized.

## Evidence baseline

- `requirements-ui.txt:14` pins `claude-agent-sdk==0.2.133`.
- The pinned wheel contains Claude Code 2.1.225, verified with the bundled
  executable's `--version` output on 2026-08-14.
- `server/agent_runner.py` is 2,873 lines and was read in full for this audit.
- The installed `ClaudeAgentOptions` exposes `tools`, `allowed_tools`,
  `disallowed_tools`, `strict_mcp_config`, `sandbox`, `hooks`,
  `include_partial_messages`, `fallback_model`, `output_format`,
  `enable_file_checkpointing`, `session_store`, `max_turns`, and other native
  controls.
- The installed `ClaudeSDKClient` exposes `set_model`, `set_permission_mode`,
  `get_context_usage`, `get_mcp_status`, `stop_task`, and the existing
  `interrupt` and session APIs.

Official references used for current behavior:

- [Python SDK reference](https://code.claude.com/docs/en/agent-sdk/python)
- [Sandboxing](https://code.claude.com/docs/en/sandboxing)
- [Permissions](https://code.claude.com/docs/en/agent-sdk/permissions)
- [Approvals and user input](https://code.claude.com/docs/en/agent-sdk/user-input)
- [Hooks](https://code.claude.com/docs/en/agent-sdk/hooks)
- [Sessions](https://code.claude.com/docs/en/agent-sdk/sessions)
- [OpenTelemetry](https://code.claude.com/docs/en/agent-sdk/observability)

## What is actually broken or redundant

### Defect 1: Bash containment is reimplemented with static parsing

`server/agent_runner.py:149-372` defines its own sandbox roots, path resolver,
shell tokenizer, and Bash path detector. `decide_tool` then applies those checks
at `server/agent_runner.py:470-477` and `server/agent_runner.py:510-517`.

The Bash half duplicates the SDK's OS-enforced sandbox and is weaker: shell
syntax can hide paths, while ordinary regex and `sed` syntax can resemble paths
and trigger false approvals. The built-in Read/Write/Edit boundary is different.
The SDK sandbox governs Bash and child processes; it does not replace the
default-deny path check for in-process file tools. SDK Read/Edit rules are
deny-region rules and cannot safely stand in for OpenNolan's root allowlist.

**Intent:** make the SDK sandbox the Bash boundary only if the pinned packaged
runtime can preserve arbitrary outbound HTTPS without prompts and expose a
reliable unavailable signal. Retain the small canonical path allowlist for
built-in file tools. Use native permission rules as defense in depth, not as the
only host-filesystem boundary.

**Named change:** remove `_bash_tokens`, `_looks_like_path`, `_path_candidates`,
and `bash_path_escape_reason`; keep `Sandbox`, `_resolve_under`, `_within`,
`_tool_paths`, and the Read/Write/Edit check. Move that check to an always-run
`PreToolUse` hook so SDK allow rules cannot shadow it. Split read and write roots
only if product policy requires it. Before removing any Bash parser, probe the
network behavior of a sandboxed child and sandbox-startup failure. If arbitrary
HTTPS is denied/prompted or failure cannot be detected, keep the Bash parser;
the native feature is adjacent but not equivalent in SDK 0.2.133.

### Defect 2: tool availability and approval are conflated

`SAFE_TOOLS` and `WRITE_TOOLS` at `server/agent_runner.py:59-73`, plus the
branching policy at `server/agent_runner.py:455-530`, manually decide which
tools are safe, which MCP tools are trusted, and which unknown tools prompt.

The SDK already separates:

- availability through `tools` and bare-name `disallowed_tools`;
- auto-approval through `allowed_tools` and permission allow rules;
- hard denial through bare-name `disallowed_tools` and structured deny rules;
- interactive fallback through `can_use_tool`; and
- MCP source isolation through `strict_mcp_config`.

Because OpenNolan leaves the built-in tool set implicit, tools it does not use
can enter model context and fall through to the generic "unrecognized" prompt.

**Intent:** make available tools an explicit, closed SDK configuration.

**Named change:** use `tools`, `allowed_tools`, structured deny/ask rules, and
`strict_mcp_config=True`; reduce `can_use_tool` to actual user interaction after
the file boundary has moved to `PreToolUse`.

### Defect 3: Bash policy is attached to the wrong SDK layer

`bash_uses_videocompose_render`, `bash_runs_heavy_media_op`, and destructive
command checks run from `can_use_tool` through `decide_tool`
(`server/agent_runner.py:484-524`). Native sandbox auto-approval can resolve a
Bash call before `can_use_tool`, so policy that must inspect every call belongs
in `PreToolUse`, not in the interactive permission fallback.

Simple command families such as `git push` can be declarative ask/deny rules.
Compound OpenNolan routing checks still require custom logic, but the SDK hook
is the correct execution point. The current `dd` rule at
`server/agent_runner.py:86` looks for `if=` while its label says "raw disk
write"; the write target is normally `of=`.

**Intent:** use rules for simple policy and a `PreToolUse` hook for unavoidable
product-specific command routing.

**Named change:** move always-run routing checks to a Bash `PreToolUse` hook and
set `autoAllowBashIfSandboxed=false`. The hook explicitly allows safe Bash,
asks for destructive Bash, and denies product-routing violations. Keep the UI
approval callback only for unresolved asks.

### Candidate 4: clarifying questions may duplicate `AskUserQuestion`

OpenNolan defines an `ask_user` SDK MCP tool at
`server/agent_runner.py:1283-1312`, tracks question futures at
`server/agent_runner.py:1236-1237`, and implements the UI wait/resolve pair at
`server/agent_runner.py:1773-1808`. It then removes the SDK's built-in
`AskUserQuestion` at `server/agent_runner.py:1582-1587`.

Current Anthropic documentation describes `AskUserQuestion` through
`can_use_tool`, and the bundled CLI contains the tool. The pinned Python wheel
does not document this headless contract, however, and the native schema allows
only two to four options plus Skip/free-text behavior. OpenNolan's current tool
accepts a different shape and returns one string.

**Intent:** treat native questions as a compatibility spike, not an authorized
replacement.

**Named change:** keep `ask_user` unless a packaged-runtime probe proves that
`can_use_tool` receives the call, `updated_input` resumes the same query, Skip is
handled, timeouts do not hang the turn, and the two-to-four-option constraint is
acceptable to the product.

### Defect 5: model switching restarts the entire SDK client

`AgentRunner.set_model` disconnects the client and sets resume state at
`server/agent_runner.py:2842-2865`. SDK 0.2.133 exposes
`ClaudeSDKClient.set_model`, specifically for changing the active model during
a streaming session.

**Intent:** switch the model in place without CLI startup and session resume.

**Named change:** call `await client.set_model(model)` when a client exists;
retain `_models` only as the initial-model setting for clients not yet created.

### Constraint 6: CLI runtime discovery and auth preflight are different checks

`claude_cli_available` maintains external/system fallback locations at
`server/agent_runner.py:703-728`. The SDK separately resolves its always-bundled
runtime executable. Counting that bundled file as login evidence would make
`auth_configured()` true on every packaged install, including a new user with no
credential, and suppress the actionable setup response.

**Intent:** let the SDK own runtime executable discovery while OpenNolan keeps a
separate credential or logged-in-system-CLI preflight.

**Named change:** do not add the bundled executable to the auth predicate.
Clarify or rename the current helper as an external CLI login heuristic. If a
runtime-presence predicate is needed elsewhere, make it a separate
bundled-first helper. A live SDK connection remains the definitive auth check.
Correct the comment at `server/agent_runner.py:703-704`, which falsely says the
external fallback list matches the SDK's bundled-first resolver.

### Defect 7: telemetry ignores useful native lifecycle information

`_TurnTools` at `server/agent_runner.py:1045-1135` joins tool-use and tool-result
blocks manually to recover names, failures, and latency. `run_turn` performs the
join at `server/agent_runner.py:2583-2623`, while `_report_turn` computes local
rollups at `server/agent_runner.py:2724-2790`.

The SDK supplies `PreToolUse`, `PostToolUse`, and `PostToolUseFailure` hooks with
tool names, inputs, IDs, results, session IDs, and subagent attribution. Hooks
do not include per-tool duration, so OpenNolan still needs a keyed start/finish
timer for latency and duplicate/orphan diagnostics. `ResultMessage` does carry
turn duration, usage, per-model cost, API status, terminal reason, and permission
denials.

The SDK cannot replace OpenNolan product facts such as document deltas,
project analytics IDs, asset kind, or browser session attribution.

**Intent:** consume native fields where they are richer, but keep the local join,
privacy classifiers, fail-closed analytics schema, and product telemetry.

**Named change:** use `PostToolUseFailure` for reliable tool naming and native
`ResultMessage` fields for turn status. If hooks replace stream observation,
move rather than delete the `tool_use_id` timer. OpenTelemetry is deliberately
out of scope until OpenNolan has an approved collector and analytics contract.

### Constraint 8: the warm-turn drain is costly but not redundant

`run_turn` waits up to 150 ms before each warm turn at
`server/agent_runner.py:2516-2534`, using `_drain_unsolicited` from
`server/agent_runner.py:2434-2497`. The sources named in the comments are
scheduled wakeups and background Bash tasks. ScheduleWakeup is already removed
at `server/agent_runner.py:1584-1587`, while heavy jobs are already routed to
in-process MCP tools.

The remaining generic `run_in_background` path is not denied. More importantly,
the bundled CLI can automatically move a slow foreground Bash command to the
background after its timeout. Tool availability and a `run_in_background` deny
do not prevent that source. Typed task messages and `stop_task` do not establish
turn separation for the current single receive stream.

`n_drained` is always zero because `_pending_unsolicited` is never defined
(`server/agent_runner.py:2522-2524`). `_truthy` at
`server/agent_runner.py:191-192` is also unused.

**Intent:** preserve the off-by-one protection. Reduce the warm-path cost only
through a persistent reader or a pinned CLI control that demonstrably disables
automatic Bash backgrounding.

**Named change:** keep `_drain_unsolicited`; evaluate a persistent reader as a
separate change. Set explicit CLI Bash timeout environment variables only after
measuring legitimate media commands. Delete the dead `n_drained` read and
unused `_truthy` independently.

### Defect 9: native result and status messages are discarded

`event_of` collapses all `SystemMessage` subclasses to only their subtype at
`server/agent_runner.py:999-1000` and ignores `RateLimitEvent`, task lifecycle
messages, `ResultMessage.duration_ms`, `usage`, `model_usage`,
`api_error_status`, `terminal_reason`, `permission_denials`, and `errors`
(`server/agent_runner.py:989-998`). `_classify_turn_error` then guesses auth,
budget, and transport classes from exception text at
`server/agent_runner.py:1169-1183`.

**Intent:** project native structured status into SSE and analytics rather than
re-derive it from strings.

**Named change:** extend event normalization for native result, rate-limit, and
task messages; prefer structured SDK fields and retain exception parsing only
as a fallback for pre-result transport failures.

## Deliberately not building

| Not building | Reason | What would change the decision |
|---|---|---|
| Replacing pipeline checkpoints with SDK file checkpointing | SDK checkpoints rewind Write/Edit file content; OpenNolan checkpoints are semantic stage/artifact contracts and include work performed by Bash and custom tools. | A future SDK checkpoint API stores arbitrary application state and external-tool side effects. |
| Replacing RenderJobStore with SDK background tasks | SDK tasks describe Claude/CLI tasks; OpenNolan must validate, supersede, publish, receipt, and resume media jobs. | SDK tasks gain application-defined durable job storage and OpenNolan publishing transactions. |
| Replacing project/thread selection with `continue_conversation` | OpenNolan supports multiple explicit threads; "most recent in cwd" is ambiguous. | The product becomes single-thread-only. |
| Sharing one SDK client across projects | MCP closures, project context, current session, permissions, and UI emitters are project-specific. | The SDK documents safe multiplexed independent conversations with per-query MCP context. |
| Replacing the custom system prompt with the Claude Code preset | OpenNolan is a video-production agent with a different surface and policy; Anthropic recommends a custom prompt for that case. | The app becomes a coding/IDE agent. |
| Enabling file checkpointing as part of this cleanup | It adds a new undo feature and does not remove current code. | Product requests agent file undo and accepts its Bash/directory limitations. |
| Adding SessionStore for a local single-host app | The SDK already persists local sessions; mirroring adds I/O without replacing the UI thread record. | OpenNolan becomes multi-host or needs externally governed transcript storage. |
| Forcing native structured output for every pipeline artifact | `output_format` validates the final agent response, not every file or custom-tool side effect produced during a long pipeline. | Stage publication is redesigned as one schema-validated SDK/custom-tool result per stage. |
| Enabling tool search explicitly | OpenNolan exposes seven custom MCP tools; Anthropic says loading fewer than roughly ten is usually faster than a search round trip. | The MCP catalog grows past the point where tool definitions materially consume context. |

## Build sequence and verification

No build is part of this task. If approved later, implement in this order:

1. Add an SDK capability contract test for version 0.2.133 and CLI 2.1.225.
   Prove settings permissions still apply with `setting_sources=[]`, and probe
   native `AskUserQuestion` before making it a build item. In the packaged CLI,
   also prove whether sandboxed arbitrary outbound HTTPS succeeds, prompts, or
   fails, and identify a stable sandbox-unavailable signal.
2. Install the file boundary and product routing in `PreToolUse` while the
   existing checks remain active.
   Preserve the live `turn_ctx` getter and serialize shared hook telemetry; the
   current runner still supports only one in-flight turn per project.
3. Enable SDK sandboxing for Bash as defense in depth, retain the built-in file
   allowlist, and keep the static Bash path parser as a rollback backend for one
   packaged release. If native sandboxing becomes unavailable during that
   overlap, route Bash through the legacy parser; after parser deletion, deny
   Bash for the client. Remove it only if the egress and unavailability probes
   prove equivalence. Set `autoAllowBashIfSandboxed=false`; the hook decides
   allow/ask/deny for each Bash call. Keep
   `OPENNOLAN_AGENT_SANDBOX=0` as a full opt-out from both enforcement layers for
   its existing development/testing purpose; never use that mode in packaging.
4. Close built-in availability with `tools`, bare tool names in
   `allowed_tools`/`disallowed_tools`, and path-bearing rules in settings JSON.
   Include `BashOutput`/`KillShell` if automatic backgrounding remains reachable.
5. Switch live models with `client.set_model`.
   Verify in `tests/contracts/test_agent_runner.py` that the client is neither
   disconnected nor resumed and the next request uses the new model.
6. Separate SDK runtime discovery from the external-CLI/auth heuristic; do not
   count the bundled executable as proof of login.
7. Project structured result/rate/task messages; retain the local tool-duration
   join and privacy classifiers. Declare every new event/field in
   `schemas/analytics/` before emission.
8. Keep the drain. If profiling justifies it, separately replace polling with a
   persistent reader and prove two consecutive turns cannot cross streams.
9. Keep the OS-independent path-boundary contract tests. Add one packaged macOS
   containment smoke; Linux CI cannot prove macOS Seatbelt behavior.
10. Run `scripts/dev test full` and `scripts/dev smoke` before implementation is
   considered complete.

## Risk register

| Risk | Mitigation | Proof |
|---|---|---|
| Native sandbox is unavailable on a managed or future macOS host | During overlap, the unavailable state routes Bash through the legacy parser. After parser deletion, it makes `PreToolUse` deny Bash. If neither consequence can be enforced, use `failIfUnavailable=true` or do not migrate. | Packaged smoke simulates both rollout phases and proves Bash is never silently unsandboxed. |
| Native Read/Edit rules are mistaken for a default-deny allowlist | Keep the canonical path boundary; use settings rules only as defense in depth. | Pure contract tests deny secrets, sibling users, `/Volumes`, symlink escapes, and outside writes. |
| Sandbox auto-approval skips OpenNolan routing | Put must-run policy in `PreToolUse`, not `can_use_tool`. | Sandboxed background render is denied by the hook. |
| Enabling the sandbox blocks provider/CDN or arbitrary user URLs | Make migration conditional on a packaged arbitrary-HTTPS probe. Do not use a finite domain list or excluded interpreter as a false equivalent. | Sandboxed Bash reaches multiple dynamic HTTPS hosts without prompts, or the migration remains blocked. |
| Hook migration briefly removes the only enforcement path | Land hooks and tests before narrowing `can_use_tool` or availability. Keep both layers during transition. | Every intermediate commit keeps the boundary suite green. |
| Declarative Bash matching is weaker than regex | Use rules only for simple prefixes; retain a small hook for compound product routing. | Table-driven policy tests cover each command family. |
| Built-in question schema does not fit current UI or pinned runtime | Keep `ask_user`; authorize replacement only after a packaged live probe covers Skip, timeout, option limits, and resume. | Probe passes without an `mcp__mc__ask_user` definition. |
| Native `set_model` fails for an inactive client | Store selection for initial options when no client exists; call native method only when connected. | Fresh and warm model-switch tests both pass. |
| New hook/result analytics bypass fail-closed schemas or leak paths | Keep existing classifiers and declare events before use; do not adopt OTel in this work. | Analytics taxonomy and PII tests pass. |
| Drain optimization revives the off-by-one bug | Keep the drain; any reader redesign ships behind a rollback flag and a two-turn regression fixture. | Two consecutive messages each receive only their own result. |
| SDK/sandbox rollout regresses a packaged host | Stage behind `OPENNOLAN_NATIVE_BASH_SANDBOX`, retain the previous boundary as fallback, and rollback by disabling the new flag. | Both flag states pass packaged restrictions. |
| SDK docs drift from the pinned CLI | Test the pinned wheel/CLI, not only current online docs; guard future version bumps. | Bundle smoke and SDK option contract tests run in packaging CI. |

## Review rounds

| Round | Reviewer finding | Resolution |
|---|---|---|
| 1 | SDK sandbox protects Bash, not built-in Read/Edit, and deny regions are not a default-deny root allowlist. | Accepted. Retain the canonical file-tool boundary; remove only the Bash parser and add native sandboxing as defense in depth. |
| 1 | `AskUserQuestion` is documented but its headless contract is not proven in the pinned Python SDK/CLI pair. | Accepted. Downgraded from replacement to a packaged-runtime spike; keep `ask_user` until it passes. |
| 1 | Automatic CLI backgrounding means the unsolicited-turn drain still prevents a real off-by-one. | Accepted. Keep the drain; isolate any persistent-reader optimization. |
| 1 | Hooks lack per-tool duration and OTel does not satisfy OpenNolan's analytics/privacy contract. | Accepted. Keep keyed timing and classifiers; use failure hooks and result fields only where richer. |
| 1 | Availability, hooks, sandbox, analytics, CI, rollback, and opt-out semantics were underspecified. | Accepted. Reordered hook-first migration, kept the existing boundary during transition, defined the flag, added analytics/rollback requirements, and separated Linux contracts from macOS containment smoke. |
| 1 | Deleting CLI preflight would remove actionable setup errors. | Accepted. Keep the product gate; round 2 further separates runtime presence from login evidence. |
| 2 | Path-bearing tools in `allowed_tools` can shadow `can_use_tool`. | Accepted before round completion. Move the canonical root check to always-run `PreToolUse`; require no shadow warning for guarded tools. |
| 2 | Enabling native sandboxing can mediate outbound HTTPS even without a domain allowlist. | Accepted. Sandbox replacement is now conditional on a packaged arbitrary-egress probe; retain the parser if the SDK cannot preserve product traffic. |
| 2 | A bundled-first file check would make packaged `auth_configured()` always true. | Accepted. Split runtime discovery from the external-CLI/login heuristic and preserve the no-credential setup response. |
| 2 | Rollback flag, sandbox-unavailable consequence, and Bash ask precedence were not executable. | Accepted, then refined in round 3. Name the rollout flag and disable sandbox auto-allow so the hook explicitly decides every Bash call. |
| 3 | Sandbox-unavailable handling ignored the deliberately retained legacy parser. | Accepted. During the rollback window, degrade to the legacy Bash parser; deny Bash only after that parser is deleted. |
| 3 | Requiring zero `CanUseToolShadowedWarning` is impossible because `skills="all"` adds `Skill` to effective allowed tools. | Accepted. The invariant is that no guarded file tool bypasses `PreToolUse`; the SDK warning is advisory. |
| 4 | Final check of the unavailable-state ladder and advisory-warning wording. | APPROVE. No remaining changes requested. |
