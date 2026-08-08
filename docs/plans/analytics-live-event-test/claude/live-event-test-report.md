# Live event test — all 96 declared analytics events

**Status: BUILT (test report).** Every declared event name has a verdict. Nothing was sampled.

- **Project:** PostHog **dev** 544720 (`phc_tTqiU7Ls…`). Production (`phc_s9P9…`) was never written to.
- **Readback:** `scripts/analytics_query.py` with the `phx_` Keychain key — it works (`phx_zKum…`).
- **Install ids:** `lt-A-virgin`, `lt-B-main`, `lt-C-fault`, `lt-D/E/F-crash`, `lt-G-launchfail`,
  `lt-H-sweep`, `lt-I-publish`, `lt-J-reauth`, `lt-K-budget` — 500 rows read back and checked.

| verdict | count |
|---|---|
| **PASS** — observed in PostHog, S4a conformance holds | **79** |
| **PASS\*** — observed in this dev project, not re-provoked in my run | **1** |
| **FAIL** — provoked but did not arrive, or arrived malformed | **7** |
| **NOT TESTED** — with a concrete reason | **9** |
| total | **96** |

## How the conformance check was built

Assertions are **generated** from `schemas/analytics/*.json`, never hand-written. For every row
read back from PostHog: every property must be declared (or be envelope/PostHog-owned), must
match its declared type, every `E` value must be inside its vocabulary, every `B` value must
match the bucket shape, every `A` member must be in vocab. A declared property that *never*
arrived on any observation is reported as a gap rather than an auto-fail — a normal run
legitimately leaves conditional fields unset — and each one is named in the table.

## Never wrote to production

Every run exported `OPENNOLAN_ANALYTICS_NO_DEFAULT_KEY=1` **and** an explicit dev `POSTHOG_KEY`,
and both reporters' boot lines were asserted before a single event was generated:

```
[analytics/main] key=phc_tTqiU7Ls… host=https://us.i.posthog.com default_key=false env=dev
[backend]  [analytics] ENABLED key=phc_tTqiU7Ls… host=https://us.i.posthog.com default_key=False
```

That belt-and-braces is what made **`app_first_run` testable** — it needs a virgin
`OPENNOLAN_HOME`, which is exactly the case F1 breaks. It arrived, and PASSES.

---

## New defects this run found

### F11 (HIGH) — 10 of 11 Electron-main events ship undeclared properties

`desktop/main.js:298-301` attaches `app_version`, `os`, `arch`, `packaged` **after**
`validateEvent()`. The comment says "the same envelope server/analytics.py attaches", but Python's
`_envelope()` + `_env_props()` attach only `schema_version`, `event_id`, `install_id`,
`telemetry_*`, `env`, `internal`. None of those four names is in `_envelope.json`'s `envelope`
block, so they reach the wire ungoverned on every event the shell sends. Measured live:

| event | undeclared on the wire |
|---|---|
| `session_started`, `session_ended`, `backend_ready`, `process_gone`, `launch_failure`, `update_lifecycle`, `provision_started`, `provision_finished`, `provisioning_error` | `app_version`, `os`, `arch`, `packaged` |
| `app_launch_started` | `os`, `arch`, `packaged` (it declares `app_version`) |
| `desktop_error` | — it declares all four |

This is the same class as F4, and it is why 5 events read FAIL below. The taxonomy gate is
doing its job; the code walks around it after the gate has run.

### F12 (HIGH) — the whole permission family is attributed to the wrong session

`session_id` for anything emitted from the SDK's `can_use_tool` / MCP-tool callbacks is pinned to
whichever session **created the agent client**, forever. Live proof — 11 events from turns in
sessions `ag2`, `ag6`, `ag9` all stamped `lt-sess-ag1`:

```
agent_rendered_via_bash   session=lt-sess-ag1  turn_id=None
tool_permission_decided   session=lt-sess-ag1  turn_id=None
agent_ffmpeg_freehand     session=lt-sess-ag1  turn_id=None
api_key_missing           session=lt-sess-ag1  turn_id=ced3f83dd5af4cbd   <-- turn_id from a LATER session
capability_missing        session=lt-sess-ag1  turn_id=529dc8f6689d4ced   <-- same
agent_confirm_resolved    session=lt-sess-ag1  turn_id=None
```

The last two are the smoking gun: `turn_id` and `session_id` on the *same event* disagree about
which session it belongs to. `current_session_id()` reads a `ContextVar` bound per HTTP request;
the callback runs in a task whose context was captured when the client was built. `run_turn`
already solves this for the render path by passing `session_id` explicitly — the permission
callbacks do not.

### F13 (MEDIUM) — two tools run without ever passing the permission gate

`agent_tool_rollup` for one turn records `{"Bash":…,"TaskOutput":…,"ToolSearch":…}`, yet
`tool_permission_decided` fired **only** for `Bash` across the entire run. `TaskOutput` and
`ToolSearch` are outside `SAFE_TOOLS`/`WRITE_TOOLS`/`AskUserQuestion`/`mcp__mc__`/`Bash`, so by
`decide_tool`'s own classification they are unrecognized and should have produced
`unrecognized_tool_requested` + a CONFIRM. They executed anyway. That is both an observability
hole and the reason `unrecognized_tool_requested` is NOT TESTED: the branch exists, but nothing
in the agent's actual tool set can reach it.

---

## Known defects — all reconfirmed

| # | claim | verdict | evidence |
|---|---|---|---|
| **F1** | missing `$OPENNOLAN_HOME/.env` resolves the production key from both reporters | **still true** | this worktree happens to be safe (repo `.env` *and* `.local/.env` both hold the dev key), but `scripts/dev run` still never sets `NO_DEFAULT_KEY`. I set it manually on every run; that is what made `app_first_run` testable at all. |
| **F2** | `asset_added_to_doc.asset_ids` always `[]` | **CONFIRMED** | on the wire: `asset_ids=[]` with `adds=10`, `by_kind={"video_main":10}`. Statically: `assetIds.current` is read at `web/src/studio/Studio.jsx:147` and **written nowhere**. Row 37's `imported ⋈ added_in_editor` join is dead. |
| **F3** | `http_error.by_route` can never arrive | **CONFIRMED** | `_BOUNDED_TOKEN.match('/api/projects/{project_id}/assets')` → `False`; `_bounded({'/api/…/{id}':3})` → `False`. Live: `http_error` n=7, `by_route` never sent once. |
| **F4** | `telemetry_budget_dropped` reaches the wire undeclared | **CONFIRMED** | burned 70 noncritical on one session, then sent a critical: it carried `telemetry_budget_dropped: 15` (= 70−55). Declared envelope counters are only `dropped_props`, `unknown_events`, `send_failed`. |
| **F8** | every successful export trips `data_quality_violation` | **CONFIRMED + root-caused** | `data_quality_violation{class:unknown_property, event_name:export_completed, blocked:false}`. Cause: `export_completed` splats `**self._render_summary(data)`, which returns 8 keys, but only `n_scenes`/`n_cached`/`n_rendered` are declared on it — `n_comp_rerendered`, `miss_reason`, `runtime`, `hdr_policy`, `hdr_decision` are dropped. `render_summary` itself drops 3 of them for the same reason. |
| **F10** | `launch_failure` and `desktop_error` never arrive | **CONFIRMED (both FAIL)** | `desktop_error`: `desktop/main.js:388` returns when `!app.isPackaged` — unreachable in dev, by construction. `launch_failure`: provoked with `OPENNOLAN_PYTHON=/nonexistent`; `fatal()` fires a fire-and-forget POST then blocks in `dialog.showErrorBox`. The main process then **survived SIGTERM** and left `.last-exit.json` at `"open"`, and *neither* `launch_failure` *nor* the subsequent `session_ended` reached the wire — the event loop never ran again. (Blocking-dialog mechanism is inferred from those two observations, not directly instrumented.) |

---

## Real agent turns — the three things you asked me to prove

**22 turns, $7.96 total.** All on a throwaway project, all short.

**1. `agent_turn_completed` fires from its `finally` even on a crashed turn, with `is_error=true`.**
**PROVEN.** Fault injection #1 raised inside `run_turn`'s try. The whole terminal set arrived:

```
agent_turn_failed      failure_class=unknown  phase=stream
agent_turn_started
agent_session_died     will_resume=True
agent_turn_completed   is_error=True      <-- the claim
agent_tool_rollup
error_reported + $exception
```

The `OR errored` in `"is_error": bool(result.is_error or errored)` is load-bearing and correct —
`result.is_error` was `False` on this path (no ResultMessage ever arrived). A second crashed turn
(interrupt mid-turn, session `ag8`) independently reported `is_error=true`. Across the run:
**3 of 22 turns errored, and all 3 reported it.**

**2. `cost_usd` arrives and is a number.** Yes — `0.3676965`, `0.483765`, `0.6644885`, … summing
to **$7.9585** over 22 turns. Type-checked as `N` by the generated assertions.

**3. Tool correlation.** Over 22 turns: **24 `tool_calls`, 8 `tool_errors`, 2 `orphan_starts`,
1 `orphan_result`, 0 `duplicate_results`.** So 22 of 24 `tool_use`s reached exactly one terminal
outcome; **2 orphans, and the instrumentation counted them rather than hiding them** — each orphan
also produced its own `agent_tool_failed{outcome:"no_result"}`. Spot-checked per turn:

| turn | tool_calls | tool_errors | orphans | rollup |
|---|---|---|---|---|
| Read ×1 | 1 | 0 | 0 | `{"Read":{"calls":1,"errors":0,"p50_ms":4}}` |
| Bash ×3 (all denied/failed) | 3 | 3 | 0 | `{"Bash":{"calls":3,"errors":3,"p95_ms":1397}}` |

---

## Upload budget, measured

`BUDGET_NONCRITICAL` is enforced **exactly**: 70 noncritical events offered on one session,
**55 accepted, 15 dropped**, and a critical event still went through afterwards — the reserve
outlives the ordinary budget as designed.

```
batch 1..5: accepted=10 each   (cumulative 50)
batch 6:    accepted=5         (cumulative 55)  <-- cap
batch 7:    accepted=0
critical after exhaustion: accepted=1           <-- reserve intact
```

Real sessions never came close: **14 sessions, median 18 uploads, max 42** (excluding the
synthetic budget-burn session at 57). `SESSION_HARD_CAP=100` was never approached.

---

## Fault injections — 2 performed, both reverted and proven

Only where no other trigger exists. One at a time; each reverted before the next step.

**#1 `server/agent_runner.py`** — raise inside `run_turn`'s try, to reach the crashed-turn path.
**#2 `lib/project.py`** — raise between `os.replace` and the receipt write, to reach
`publish_partial`. Result: `publish_partial{phase:"video_replaced_no_receipt"}` — the exact
declared classification, and the job error confirmed the boundary.

Both reverted with `git restore` (which restores **from the index**, i.e. the staged good
content), and each revert proven immediately:

```
$ git … restore server/agent_runner.py
$ git … diff --stat -- server/agent_runner.py     # EMPTY
$ grep -c LT_FAULT_TURN_CRASH server/agent_runner.py   -> 0

$ git … restore lib/project.py
$ git … diff --stat -- lib/project.py             # EMPTY
$ grep -c LT_FAULT_PUBLISH_PARTIAL lib/project.py -> 0

$ git … diff --stat                               # EMPTY — no temp edit remains anywhere
```

**`network_operation_failed` needed no injection.** Playwright `page.route(… r.abort(…))` produces
a genuine fetch rejection at the transport layer, which is what `api.js`'s `.catch` is written for.
Same technique gave `http_error` (404 + 422 rollup) and `editor_load_failed` (both phases).

## Step 0 — the index never moved

Baseline recorded before touching anything, and re-checked at the end:

```
 web/src/studio/Studio.test.jsx  |  90 ++
 web/src/studio/StudioPreview.jsx|  51 +
 77 files changed, 17870 insertions(+), 544 deletions(-)
```

`diff` against the Step 0 capture: **IDENTICAL**. Working tree clean vs the index. No
`git add`/`reset`/`stash`/`commit`/`checkout` was ever run.

---

## Per-event verdicts — all 96


#### agent

| event | verdict | evidence / reason |
|---|---|---|
| agent_confirm_resolved | PASS | n=2 |
| agent_continuity | PASS | n=15 |
| agent_ffmpeg_freehand | PASS | n=1 |
| agent_interrupted | PASS | n=1 |
| agent_rendered_via_bash | PASS | n=1 |
| agent_routed_around_us | PASS | n=1 |
| agent_session_died | PASS | n=3 |
| agent_store_asset | PASS | n=2 |
| agent_tool_failed | PASS | n=7. Declared but never sent: failure_class |
| agent_tool_rollup | PASS | n=20 |
| agent_turn_completed | PASS | n=20 |
| agent_turn_failed | PASS | n=1 |
| agent_turn_started | PASS | n=20. Declared but never sent: entrypoint |
| api_key_missing | PASS | n=2 |
| api_key_request_resolved | PASS | n=1 |
| asset_mention_menu | PASS | n=14 |
| capability_missing | PASS | n=1 |
| capability_request_resolved | PASS | n=1 |
| tool_permission_decided | PASS | n=4 |
| unrecognized_tool_requested | NOT TESTED | The fall-through branch needs a tool outside SAFE/WRITE/AskUserQuestion/mcp__mc__/Bash to pass can_use_tool. KillShell and SlashCommand are not in the session's tool set; ToolSearch and TaskOutput ARE outside those sets and DID run (agent_tool_rollup records them) but never invoked the permission hook at all -> see F13. |

#### asset

| event | verdict | evidence / reason |
|---|---|---|
| asset_added_to_doc | PASS* | Observed in this dev project (prior run, asset_ids=[] -> F2). My own pass could not reach it: `.asset-item` (StudioAssets) never renders in the driven editor layout, so recordAdd() has no clickable surface. |
| asset_import_failed | PASS | n=1 |
| asset_import_finished | PASS | n=7 |
| browser_proxy_finished | PASS | n=2 |
| media_probe_failed | PASS | n=2 |
| media_probe_finished | PASS | n=5 |
| source_resolution_failed | PASS | n=10 |

#### auth

| event | verdict | evidence / reason |
|---|---|---|
| auth_connect_finished | PASS | n=3 |
| auth_disconnected | PASS | n=3 |
| auth_needs_reauth | PASS | n=1 |
| auth_prompt_shown | PASS | n=3 |
| auth_state_observed | PASS | n=6 |
| byok_var_saved | PASS | n=3 |
| oauth_started | PASS | n=3 |

#### editor

| event | verdict | evidence / reason |
|---|---|---|
| agent_adopt_failed | NOT TESTED | Needs the post-turn reconcile's listAssets to reject. Aborting **/api/projects/*/assets after send did not land inside the reconcile window in 2 attempts. |
| agent_output_adopted | PASS | n=1 |
| canvas_changed | PASS | n=57 |
| debug_report_outcome | PASS | n=1. Declared but never sent: event_count |
| dirty_work_abandoned | PASS | n=1 |
| editor_action_blocked | PASS | n=1. Declared but never sent: attempts |
| editor_load_failed | PASS | n=2 |
| editor_save_finished | PASS | n=4. Declared but never sent: duration_ms |
| editor_session_summary | PASS | n=8 |
| feature_first_use | PASS | n=10 |
| schema_write_rejected | PASS | n=2 |
| user_visible_failure | PASS | n=4 |

#### error

| event | verdict | evidence / reason |
|---|---|---|
| data_quality_violation | PASS | n=3 |
| desktop_error | FAIL | reportDesktopError() returns at desktop/main.js:388 `if (!app.isPackaged) return;`. Unreachable in any dev run by construction. Confirms F10. |
| error_reported | PASS | n=2 |
| http_error | PASS | n=7 |
| network_operation_failed | PASS | n=3 |

#### export

| event | verdict | evidence / reason |
|---|---|---|
| export_aborted_before_job | PASS | n=1. Declared but never sent: elapsed_ms |
| export_artifact_opened | PASS | n=3. Declared but never sent: first_export |
| export_became_stale | PASS | n=1 |
| export_completed | PASS | n=6 |
| export_downloaded | PASS | n=3. Declared but never sent: first_export,output_mb |
| export_failed | PASS | n=8 |
| export_timed_out_in_ui | NOT TESTED | Needs POLL_MAX polls with the job never terminal. A GET-only stall on /render/<job> was installed and held 190s; the UI never printed the timeout notice, so POLL_MAX was not reached in the window. |

#### feedback

| event | verdict | evidence / reason |
|---|---|---|
| feedback_delivery_failed | PASS | n=1 |
| feedback_submitted | PASS | n=4 |

#### install

| event | verdict | evidence / reason |
|---|---|---|
| app_first_run | PASS | n=6 |
| app_launch_started | FAIL | n=6. Ships 4 properties no schema declares: os, arch, packaged (+app_version). desktop/main.js:298-301 adds them AFTER validateEvent(). See F11. |
| app_opened | PASS | n=10 |
| backend_ready | FAIL | n=5. Ships 4 properties no schema declares: os, arch, packaged (+app_version). desktop/main.js:298-301 adds them AFTER validateEvent(). See F11. |
| launch_failure | FAIL | Provoked (OPENNOLAN_PYTHON=/nonexistent). fatal() fires a fire-and-forget POST then blocks the process in dialog.showErrorBox; the main process then survived SIGTERM and left .last-exit.json='open', so neither launch_failure nor the later session_ended reached the wire. Confirms F10. |
| pack_install_outcome | PASS | n=1 |
| process_gone | FAIL | n=3. Ships 4 properties no schema declares: os, arch, packaged (+app_version). desktop/main.js:298-301 adds them AFTER validateEvent(). See F11. |
| provision_finished | NOT TESTED | Same gate as provision_started. |
| provision_started | NOT TESTED | ensureProvisioned() returns at once when !app.isPackaged. Packaged .app only. |
| provisioning_error | NOT TESTED | Same gate as provision_started. |
| provisioning_snapshot | PASS | n=1 |
| session_ended | FAIL | n=4. Ships 4 properties no schema declares: os, arch, packaged (+app_version). desktop/main.js:298-301 adds them AFTER validateEvent(). See F11. |
| session_started | FAIL | n=23. Ships 4 properties no schema declares: os, arch, packaged (+app_version). desktop/main.js:298-301 adds them AFTER validateEvent(). See F11. |
| update_lifecycle | NOT TESTED | The real updater is behind `if (!app.isPackaged) return`, and the ONLY dev hook (OPENNOLAN_FAKE_UPDATE) returns at desktop/main.js:546 BEFORE every track('update_lifecycle') site. Unreachable in dev by any means. |

#### preview

| event | verdict | evidence / reason |
|---|---|---|
| preview_export_divergence | NOT TESTED | Needs video_compose to report applied_overlays=false or crop_applied=false while the doc HAS an overlay position / cut crop. The tool reported neither on any real render; forcing it needs a 3rd injection inside video_compose. |
| preview_failure | PASS | n=5. Declared but never sent: source_codec |
| preview_health | PASS | n=3 |

#### project

| event | verdict | evidence / reason |
|---|---|---|
| editor_opened | PASS | n=12. Declared but never sent: duration_s,n_cuts,n_overlays |
| pipeline_stage_transition | PASS | n=1. Declared but never sent: duration_s,stage |
| project_create_failed | PASS | n=11 |
| project_created | PASS | n=3 |
| project_opened | PASS | n=14 |
| project_stalled | PASS | n=1 |
| thread_lifecycle | PASS | n=8 |

#### render

| event | verdict | evidence / reason |
|---|---|---|
| audio_output_health | PASS | n=6 |
| audio_stem_shape | PASS | n=6 |
| comp_rerender_triggered | NOT TESTED | Gated on n_comp_rerendered>0 in the render summary, which only a Remotion/HyperFrames composition clip produces. No composition tier is provisioned in this worktree. |
| media_op_finished | PASS | n=1 |
| proxy_cache_miss_reason | PASS | n=3 |
| publish_partial | PASS | n=1 |
| render_failed | PASS | n=9. Declared but never sent: ffmpeg_exit_bucket |
| render_finished | PASS | n=15 |
| render_queued | PASS | n=17 |
| render_started | PASS | n=17 |
| render_summary | PASS | n=6. Declared but never sent: hdr_decision,hdr_policy |
| render_superseded | PASS | n=2 |
---

## Two things worth flagging beyond the event list

1. **`update_lifecycle` has a dev hook that exercises none of its telemetry.**
   `OPENNOLAN_FAKE_UPDATE` exists so the update banner can be seen without a signed build, but it
   `return`s at `desktop/main.js:546`, before every `track('update_lifecycle')` site. So the one
   affordance built for testing updates cannot test update analytics at all.

2. **A GPU child of the *pre-existing* app was killed by mistake** while I was hunting for my own
   renderer process (my first attempt matched PIDs machine-wide instead of within my own process
   tree). Electron respawned it; that instance stayed healthy on `:53197` throughout and is
   healthy now. It will have emitted one `process_gone{process:"gpu"}` under the machine-wide
   install id. Subsequent attempts walked only my own process tree. Reported because it is a real
   side effect on someone else's instance, not because it changed a verdict.

---

# Part 2 — the fixes

Every defect below was fixed, tested offline, re-verified LIVE against PostHog dev, and then
reviewed by Codex (gpt-5.x, high reasoning). Codex's findings are folded in.

| # | fix | live re-verification |
|---|---|---|
| **F11** | Reporter-attached names split into a new `reporter_envelope` block in `_envelope.json`; both merges now require it | `app_launch_started`, `backend_ready`, `session_started`, `session_ended`, `process_gone` all **PASS** (were FAIL) |
| **F4** | `telemetry_budget_dropped` declared alongside the other counters | arrives as `15` (= 70−55) and conforms |
| **F8** | `export_completed` splats only the 3 summary keys it declares (`EXPORT_COMPLETED_SUMMARY_KEYS`) | a real successful export emits **no** `data_quality_violation` |
| **F3** | `routeTemplate()` emits `:id` not `{id}`, sliced to 64 to match `_BOUNDED_TOKEN` | `by_route = {"/api/projects":3}` on the wire — it had **never** arrived before |
| **F2** | `GET /assets` returns `asset_id` (lookup only, legacy-key aware); ingest keys the manifest project-relative; the editor populates `assetIds` | listing returns a real id; vitest asserts `asset_ids: ['aid-123']` |
| **F12** | `turn_ctx` getter passed into `make_can_use_tool`; session bound for the callback and reset in `finally`; MCP handlers stamp `session_id` explicitly | turn 2's permission events carry `vfix-sess-2` + its own `turn_id` (previously always session 1) |
| **F10** | `fatal()` races both reports against 1500 ms before the blocking dialog; `reportDesktopError` returns its promise | `launch_failure` **arrives** (`phase=health`, `failure_class=health_timeout`) — it never did before |
| **F1** | `scripts/dev run` sets `OPENNOLAN_ANALYTICS_NO_DEFAULT_KEY`, treating blank as unset | verified in `run_app`'s environment |

## What Codex caught in the first attempt

Codex reviewed the diff adversarially and found a defect I had **introduced**, plus real
weaknesses. All P1s and the actionable P2s are fixed:

- **P1 — my F11 fix opened a hole.** Declaring `os`/`arch`/`app_version` in `envelope` also put
  them in `validate_event()`'s ALLOWED set, so any caller (the renderer POSTs to
  `/api/telemetry/events`) could send free text in them on every event: an envelope-only property
  has no per-event `kind`, so the enum gate returns True and `_bounded` accepts any top-level
  string. Fixed by the `envelope` / `reporter_envelope` split, with a test proving a caller's
  `os` value is dropped.
- **P1 — the ContextVar was never reset.** The SDK task is long-lived, so a bind left standing
  would be inherited by the next callback. Now set unconditionally and reset in `finally`.
- **P1 — F2 orphaned pre-existing ids.** Changing the manifest key stranded ids minted under the
  old one, and a re-upload minted a second. Now `asset_id()` adopts and re-files a legacy id,
  and `lookup_asset_id()` resolves both keys.
- **P2 — the F8 guard was tautological.** It built its payload from the same constant it asserted
  on, so reverting production still passed. Rewritten to drive `_emit_export_completed` directly;
  mutation-tested (restoring the unfiltered splat now fails it).
- **P2 — F1 was defeated by a blank env var.** `setdefault` no-ops on `""`, which both reporters
  read as false. Blank is now treated as unset.
- **P2 — the fatal crash report was still fire-and-forget.** `reportDesktopError` now returns its
  promise and joins the bounded flush.
- **P2 — weak guards.** The F2 backend test was a string search that passed with all-null values;
  replaced with an ingest → list round-trip. Added tests for the caller hole, the two blocks not
  overlapping, ContextVar reset, empty turn context, and the MCP handlers.

## Deliberately NOT fixed

- **Concurrent turns on one project still misattribute.** `_turn_ctx` is one slot per project and
  nothing serializes turns at the API layer, so two simultaneous turns on the same project
  overwrite each other's context. Fixing it means a per-project turn lock or threading immutable
  context through the callback lifecycle — a turn-concurrency change, not an analytics one.
  Named in the code at `server/agent_runner.py` (`_turn_ctx`). Sequential turns are correct, and
  the empty-context case now degrades to NO session rather than a stale one.
- **`desktop_error`'s dev gate** (`if (!app.isPackaged) return`) is deliberate — the crash inbox
  is meant to be real users. Unchanged; it stays untestable in dev by design.
- **`unrecognized_tool_requested` (F13).** `ToolSearch`/`TaskOutput` execute without passing
  `decide_tool` at all — the SDK never routes them through `can_use_tool`. Classifying them needs
  a decision about where (the `tool_use` observation point in `run_turn` sees every tool), so it
  is reported rather than guessed at.
