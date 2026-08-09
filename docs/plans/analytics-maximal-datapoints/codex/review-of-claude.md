# Adversarial review of Claude's maximal analytics catalog

STATUS: REVIEW

## 1. Verdict

Claude's catalog is not safe to build as-is.
Its worst defect is the missing event envelope: project, session, turn, job, and
tool-invocation joins are assumed by the formulas but absent from most rows.
Its best idea that my catalog lacks is the aggressive session/turn rollup model;
shipping about 30 decision events per session is better than my 135-event plan.
The feature ledger, second-project retention, and freehand-FFmpeg classifier also
belong in the merged document.
The hook research is broad, but several load-bearing hooks do not carry the data
claimed, and the crash-free formula excludes the crashes it is meant to count.

## 2. CONFIRMED ERRORS

### Audit method and coverage

I read the document in full, then checked source rather than merely checking that
a cited line exists.

- In sections 1 and 2 I checked all 108 cited line pointers, including contextual
  shorthand such as `:980`. Of those, 103 landed on the named construct and five
  were wrong or too early to be the claimed hook: **95.4% line-hit rate**.
- In the catalog I sampled 50 rows, stratified across every family: 3a (4), 3b
  (3), 3c (3), 3d (3), 3e (6), 3f (7), 3g (3), 3h (7), 3i (3), 3j (5), 3k
  (3), and 3l (3). Forty-two were viable hook families and eight were materially
  wrong: **84.0% row-hook hit rate**.
- Combined: **158 checks, 145 hits, 13 wrong/insufficient hooks, 91.8%**. A hit
  means the source location can observe the claimed transition, not merely that
  the file and line exist.
- Targeted passes separately covered pure editor modules, pre-UI startup paths,
  tool-result correlation, every formula in section 4, and all volume arithmetic.

The 50 catalog rows were #1, #3, #8, #9, #15, #17, #22, #24, #28, #32,
#34, #37, #44, #47, #48, #57, #67, #71, #78, #80, #82, #86, #91, #98,
#107, #109, #115, #117, #121, #122, #123, #125, #126, #127, #130, #135,
#136, #138, #140, #143, #144, #147, #149, #151, #153, #155, #156, #159,
#160, and #162.

### Errors in Claude's document

| # | Their claim (quote + their line) | What the code actually says (`file:line`) | Severity | Fix |
|---|---|---|---|---|
| 1 | The architecture and formulas assume events can be joined by project/session/turn/job; only `error_reported` explicitly carries `session_id` (plan lines 470, 503-640, 1060-1159). | The common sender adds only `env` and `internal`, while identity is only the install-level PostHog `distinct_id` (`server/analytics.py:68`, `server/analytics.py:166`). Render jobs have `job_id` and `project_id` locally (`server/render_jobs.py:64`), but the proposed common schema never requires them. | **Critical** | Require `schema_version`, `event_id`, `install_id`, `session_id`, and nullable `project_id`, `turn_id`, `job_id`, `tool_invocation_id`, plus origin and timestamps, on the applicable event families. |
| 2 | “Crash-free session rate — `1 − sessions_with_any #143 / sessions (#14)`” (plan line 629). | `#14` is emitted on `pagehide`; a hard renderer/process crash may never execute it (`web/src/debug/recorder.js:186`, `web/src/debug/recorder.js:198`). `#143` also includes nonfatal errors, while its own `fatal` flag is ignored. The formula therefore drops crashed sessions from the denominator and counts handled errors as crashes. | **Critical** | Denominator = session starts, with a synthesized timeout end. Numerator = distinct sessions with a fatal/process-gone signal. Report nonfatal error-free sessions separately. |
| 3 | “Thread `is_error` through” from tool results into the call recorded at `run_turn:1973` (plan lines 41-44, 283, 550-551). | A tool-use block carries `id`, name, and detail (`server/agent_runner.py:770`); the later tool-result block carries only `tool_use_id` and `is_error` (`server/agent_runner.py:782`). `run_turn` records the start using only name/detail and ignores all result blocks (`server/agent_runner.py:1964`). This is not a field-threading change; it needs a pending-call map keyed by SDK invocation ID. | **High** | Emit or accumulate `{tool_invocation_id, tool_name, start}` on `tool_use`, resolve it on `tool_result.tool_use_id`, and classify/finish once. Preserve orphan-start and orphan-result counters. |
| 4 | Agent usefulness fields are “already computed in-process and thrown away (`TurnResult`...)” (plan lines 25-27), and row #48 reads them from `TurnResult` (line 269). | `TurnResult` has only `text`, `is_error`, `num_turns`, and `total_cost_usd` (`server/agent_runner.py:832`). It has no `stop_reason`, tool counts, doc change, artifact count, or render flag. `run_turn` copies only error, turns, and cost (`server/agent_runner.py:1979`). | **High** | Say these fields require new snapshots/counters. Compute before/after artifact/doc hashes and tool/render counters around the turn; do not present them as existing. |
| 5 | `_set` is “ONE choke point” through which every render status flows (plan lines 28-30, 135, 379, 1060-1100). | Queued jobs are inserted directly (`server/render_jobs.py:64`, `server/render_jobs.py:81`, `server/render_jobs.py:103`). More importantly, superseded is deliberately written directly because `_set` cannot perform that update (`server/render_jobs.py:168`). | **High** | Use explicit `_create_job` and `_transition` helpers, or instrument all three start methods plus `_set` plus `_mark_superseded_locked`. Never claim `_set` alone is exhaustive. |
| 6 | Row #123 says `_set` can emit cache counts and output bytes from the renderer payload (plan line 382). | `video_compose` computes `n_scenes`, `n_cached`, and `n_rendered` (`tools/video/video_compose.py:1559`), but `RenderJobStore` converts them into a warning string and sends only output path, final-review status, and warnings to `_set` (`server/render_jobs.py:587`). The numeric payload is discarded. | **High** | Propagate a typed render summary into the terminal transition before capture. Do not parse the warning string later. |
| 7 | Row #32 hooks `pipeline_stage_reached` at `server/app.py:743` “`/state` transitions” (plan line 233). | That line is a GET endpoint which reads and returns current state; it does not perform a transition (`server/app.py:743`). Repeated polling would duplicate “reached” events. Actual state writes flow through the state source/update path, including `server/state.py:91`. | **High** | Instrument the mutation boundary once, with `from`, `to`, and duration. Do not capture from the polling GET. |
| 8 | `prompt_len` and `message_len` are safe shape fields preserved by `_scrub` (plan lines 268, 485, 1220-1227). | `_scrub` matches free-text hints by substring in the **property key**. A numeric `prompt_len` or `message_len` matches and becomes `prompt_len_len=None` or `message_len_len=None` because the value is numeric (`server/analytics.py:150`). | **High** | Use neutral allowlisted names such as `input_chars` and `feedback_chars`, or make the taxonomy scrubber type-aware and test the serialized result. |
| 9 | Row #138 proposes `export_opened_externally` at `shell.openPath` in `desktop/main.js` (plan line 408). | Electron `shell` is imported (`desktop/main.js:14`) but `shell.openPath` is never called anywhere in the repository. The UI opens render media internally; no external-open product path exists. | **High** | Delete the row until the product has an explicit Reveal/Open/Share action, then instrument that action rather than inventing a hook. |
| 10 | A shared `swallowed_error` event can be emitted from every reporter's own swallowed exception (plan lines 162-163, row #149 at 476). | The analytics sender itself swallows failures at `server/analytics.py:175`, and `_before_send` swallows at `server/analytics.py:116`. Sending an analytics event from either failed analytics path is recursive and cannot prove delivery. Similar local reporters intentionally cannot raise (`server/activity.py:181`). | **High** | Persist a local delivery-health counter/outbox, attach it to the next independently successful flush, and expose local diagnostics. Do not promise a live remote event from the failed sink itself. |
| 11 | `export_completed` is anchored at `server/render_jobs.py:566` “when `published['published']`” (plan line 406). | Line 566 is only the pre-call branch; the publisher is called at `server/render_jobs.py:567`, and its success is checked at `server/render_jobs.py:576`. The receipt is written inside the publisher at `lib/project.py:614`. | **Medium** | Capture after `publish_final_render` returns `published=true` and after the receipt exists. This app-layer point has origin/job context and excludes refused superseded publishes. |
| 12 | Undo rate is `count(undo op=F) / count(commit op=F)` (plan lines 596-605). | History stores only document snapshots (`web/src/studio/Studio.jsx:168`), and the same action can be undone, redone, then undone again (`web/src/studio/Studio.jsx:196`). No action ID joins an undo to one originating action, so the numerator can double-count and exceed the intended population. | **Medium** | Give each committed history entry an `action_id` and feature ID; count each action once, with a redo-adjusted regret outcome. |
| 13 | Time-in-app/rendering is `sum(render wall_s) / sum(session duration_s)` (plan lines 633-641). | Per-project locks serialize only one project's jobs (`server/render_jobs.py:482`); different projects can overlap, and render intervals can overlap background/unfocused time. Summing durations double-counts concurrent intervals and is not a share of user time. | **Medium** | Compute the union of render-running intervals intersected with foreground intervals, keyed by session. |
| 14 | Row #67 treats `ACTION_CONFIRM "unrecognized tool"` as proof “the agent reached for a tool that does not exist” (plan line 293). | Unknown tools are conservatively routed to confirmation (`server/agent_runner.py:429`). That includes valid but unclassified MCP/SDK tools; it does not prove registry absence or execution failure. | **Medium** | Name it `unrecognized_tool_requested`; separately verify registry/SDK availability before classifying `tool_not_found`. |
| 15 | Row #78 hooks mention-search misses in pure `mentions.js`, and #91 points at pure `interp.placeOverlayTrack` (plan lines 309, 333). | `mentions.js` explicitly has “No React, no DOM, no fetch” (`web/src/chat/mentions.js:1`), while editor mutations must remain pure under `RULES.md:41`. The actual auto-track calls are in `web/src/studio/Studio.jsx:489`, `web/src/studio/Studio.jsx:497`, and `web/src/studio/Studio.jsx:507`; plan line `web/src/studio/Studio.jsx:525` is the asset-drop dispatcher. | **Medium** | Keep pure functions pure. Capture intent/outcome in `ChatPanel` and thin Studio callbacks, or feed the existing debug-event bridge with pre/post summaries. |
| 16 | Row #18/#22 style properties and several formulas rely on a curated enum being redacted by `_SECRET_HINT` (plan line 218). | `_SECRET_HINT` tests the property name, not its value (`server/analytics.py:150`). A property named `var_name` containing `ANTHROPIC_API_KEY` is not redacted. | **Medium** | Enforce a closed provider-family enum before capture; do not cite the current secret scrubber as protection for values. |
| 17 | Row #86 says there are seven `PROPERTY_TITLES` (plan line 328). | The map has eight: video main, image main, video overlay, image overlay, text, music, SFX, and narration (`web/src/studio/propertySchema.js:17`). | **Low** | Use eight types or derive the enum from the schema at build time. |
| 18 | Row #44 anchors `sanitize_filename` at `server/app.py:573` (plan line 255). | Sanitization runs at `server/app.py:567`; line 573 is the post-resolution traversal check. | **Low** | Anchor at line 567 and keep traversal rejection as a separate failure class at line 573. |
| 19 | The map marks desktop errors `□` local-only (plan lines 65 and 160). | `reportDesktopError` directly POSTs a `desktop_error` to PostHog (`desktop/main.js:65`, `desktop/main.js:87`, `desktop/main.js:98`). | **Low** | Mark it observable today; the gap is session correlation and delivery acknowledgement, not capture. |
| 20 | “Denominator is editor sessions, not installs” follows a formula whose denominator is installs with an editor session (plan lines 563-570). | The formula and prose describe different units. The formula's install-level eligible cohort is the defensible one. | **Low** | Say “eligible installs that opened the editor,” and keep all numerator/denominator units at install level. |
| 21 | The rolled design totals 27 events/session, and the post-beta per-interaction estimate is presented as about $2,026/month (plan lines 963-1014). | Its own Tier-B list adds to 8, not 7: `#58 x3` + #82 + #115 + #106 + `#123 x2`; the total is 28/session. The 1,730/session volume math is otherwise correct. Current official progressive bands ($0.0000500, $0.0000343, $0.0000295) make 41.52M events about **$1,278.24/month**, not $2,026; $2,026 is only the explicitly stated flat-rate upper bound. | **Medium** | Correct 27→28 and show both exact tiered cost and conservative ceiling. Source: https://posthog.com/pricing, checked 2026-08-05. |

### Errors in my own phase-1 document

| # | My claim | What the code actually says (`file:line`) | Severity | Fix for merge |
|---|---|---|---|---|
| O1 | Rows #91 and #119 anchor contribution/export completion at `lib/project.py:614` (my plan lines 235 and 282). | Line 614 enters the optional receipt block; success is only known after `atomic_write_json` returns at `lib/project.py:616`. More importantly, the app-layer caller checks `published` and retains origin/job context at `server/render_jobs.py:576`. | **High** | Claude beat me on layer choice. Emit app analytics after the successful publish return; leave the lower-level library free of product analytics. |
| O2 | My rows #52/#53 treat `tool_id` as enough to join start/finish (my plan lines 191-192). | Tool name and invocation ID are different fields (`server/agent_runner.py:775`), and the result has only `tool_use_id` (`server/agent_runner.py:789`). I omitted `tool_invocation_id` and the pending map. | **High** | Add invocation ID to both local records, join in-process, and upload only the terminal/turn rollup. |
| O3 | My row #46 uses `prompt_chars` through the existing server sink (my plan line 185). | The substring scrubber treats any key containing `prompt` as free text and replaces a numeric value with `None` (`server/analytics.py:150`). | **High** | Rename to `input_chars` under an allowlisted schema and add a serialization test. |
| O4 | My row #91 promises `used_in_last_export` per feature without specifying action identity (my plan line 235). | Current history and doc diffs have no stable action ID or provenance ledger (`web/src/studio/Studio.jsx:168`, `web/src/studio/model.js:462`). | **Medium** | Drop the row from P1. First build action IDs/origin rollups; derive export contribution only if the provenance question remains decision-relevant. |

Confirmed-error count: **25 total — 2 critical, 11 high, 8 medium, 4 low**.

### Unverified suspicions

- Row #158's PMF survey threshold may be premature at the stated sample size, but
  that is a product judgment, not a code fact.
- `pagehide` plus `sendBeacon` behavior inside packaged Electron should be tested
  under window close and process crash; source inspection cannot prove delivery.
- The claimed 20-150 agent tools/turn may be realistic, but there is no production
  distribution yet. It must remain an assumption, not a capacity fact.

## 3. MISSING DATA POINTS

### In theirs but not mine

- **FFmpeg capability inventory** (#9): zscale and encoder availability decide
  whether HDR modes can work. My probe profile measures media but not machine
  support. The actual probes feed the HDR branch at
  `tools/video/video_compose.py:1291` and `tools/video/video_compose.py:1298`.
- **Freehand FFmpeg family** (#77): classifying `overlay`, `crop`, `atempo`,
  `drawtext`, and similar families is a sharper build-next signal than my four
  hard-coded heavy-operation markers. The existing parser begins at
  `server/activity.py:108`; it must classify locally and never upload commands.
- **Skill, pipeline-definition, and media-tool reach** (#59-61): one per-turn
  rollup can identify dead agent surface using existing activity categories at
  `server/activity.py:234`.
- **Decoded/dropped frame counts** (#117): browser playback quality is more useful
  than my hand-rolled rAF cadence metric for media playback. Keep it as one
  session rollup.
- **Second-project rate and weekly project retention** (#26/#160): these match the
  product's creator cadence better than making D1 a wall number.
- **Automated final-review outcome** (#130): the renderer already attaches the
  status at `tools/video/video_compose.py:2619`; my catalog buried it inside a
  generic render event rather than treating it as a keep/delete test for review.

### In neither document

- **Tool invocation correlation health.** Neither catalog records the SDK
  `tool_use.id`/`tool_result.tool_use_id` pair, orphan starts, orphan results, or
  duplicate results. This is the most valuable shared miss because both catalogs'
  core tool success/latency metrics are otherwise unbuildable
  (`server/agent_runner.py:775`, `server/agent_runner.py:789`).
- **Proxy cache miss reason.** Both count hits, but neither says *why* a scene
  missed: first render, source-byte hash changed, composition content changed,
  runtime/family changed, canvas changed, crop changed, missing clip, or corrupt
  record. The cache identity explicitly folds these inputs at
  `tools/video/render_cache.py:51`; reason counts are what decide which
  invalidation rule to repair.
- **Partial publish failure.** Video replacement happens before receipt write
  (`lib/project.py:604`, `lib/project.py:614`). A receipt-write failure leaves new
  bytes intentionally stale. Count `publish_partial{phase}` separately from
  render failure so a dangerous commit-path regression is visible.
- **Agent-adopt fetch failure and reconcile duration.** The post-turn doc fetch
  silently catches and drops errors (`web/src/studio/Studio.jsx:314`,
  `web/src/studio/Studio.jsx:325`). Both catalogs measure a successful adopt and
  save blocking, but neither measures a turn that changed disk while the live
  editor failed to adopt it.
- **Exact composition invalidation reason.** `RULES.md:70` says only the changed
  Remotion/HyperFrames composition should re-render. Count changed composition
  scenes and miss reasons separately from ordinary FFmpeg/image/video proxies;
  aggregate cache rate cannot verify this promise.
- **Audio output health.** Both count music/narration/SFX presence and preview
  sync, but neither records local final-mix clipping, silence, channel layout, or
  loudness bucket. A successful MP4 with inaudible or clipped audio is not a
  successful export.
- **Telemetry join/data-quality loss by family.** Both mention delivery health,
  but neither specifies unmatched session/project/job/turn rates. A daily
  `orphan_event_count{family}` and duplicate-ID count would catch schema drift
  before a dashboard silently changes denominator.

## 4. BLOAT — what to cut

### Cut from Claude's catalog

| Rows | Why they should die or collapse |
|---|---|
| #46 and #142, struck-through `asset_name`/`export_thumbnail` | These are exclusions, not events or metrics. Keep them in section 12; counting them inflates the 162-row catalog. |
| #12 `app_version_upgrade` | Version changes derive from consecutive session starts. A separate event adds no decision. |
| #21 `auth_disconnected` | `had_exports` does not distinguish credential hygiene from churn, and an exit survey on disconnect is likely noise. Keep disconnect only as auth-funnel diagnostics. |
| #23 `capabilities_panel_opened` | Modal opens do not decide whether packs are valuable; requests, approvals, install outcomes, and subsequent task success do. |
| #30 `project_deleted` | There is no product action to instrument. Do not build deletion UI merely to observe people deleting folders. |
| #40 per-folder `asset_browse` | Up to 30 events/session is excessive. Keep one session rollup: folders opened, searches, preview/add outcome. |
| #44 `asset_filename_shape` | Name length does not justify sanitizer changes; actual sanitizer rejection classes do. Keep extension on the media probe and count rejections. |
| #53/#54 unsolicited-drain/resume-note events | These are regression/debug counters, not product-intelligence rows. Keep local observability or one agent continuity rollup. |
| #83/#84 `feature_first_use`/`feature_second_use` | They are warehouse queries over feature/session facts, not source events. Remove them from the event catalog. |
| #87/#89/#91/#92 | Scrub, reorder, auto-stack, and overlap-specific events duplicate the proposed labeled edit action plus undo. Preserve the feature IDs in one editor rollup. |
| #120 `render_preview_watched` | Watch percentage is a weak satisfaction proxy and expensive to implement correctly; export/open/feedback and preview failures are stronger. |
| #121 `frame_endpoint_latency` | The current Studio source preview uses persistent video seeks, not the frame endpoint (`web/src/studio/StudioPreview.jsx:154`). Delete unless endpoint traffic proves a live consumer. |
| #135 `render_concurrency_blocked` | Keep queue time on terminal render summary; a separate event buys nothing. |

### Cut from my own catalog

| Rows | Why they should die or collapse |
|---|---|
| #23 `auth_to_first_turn_ms`, #44 `unused_import_rate`, #115 `render_time_ratio`, #139 `crash_free_session_metric`, #153 `cohort_retention_metric` | These are derived metrics, not events. Move them to formulas. |
| #69 `agent_effectiveness_rollup` | Duplicates turn summary, editor adopt, and export outcome. Derive it after joins exist. |
| #76 `editor_selection_rollup`, #82 zoom, #83 track visibility | Low-leverage UI diagnostics. Put compact counters in the editor summary only when a concrete layout investigation starts. |
| #89 `feature_noop` | A generic no-op reason requires invasive labeling and conflates valid same-ref guards with user confusion. Blocked intent and undo are stronger. |
| #100 `preview_frame_cadence` | Replace with Claude's browser decoded/dropped-frame rollup plus seek/stall metrics. |
| #105 `render_scene_finished` | Do not ship one success event per scene. Keep failed scene detail at 100% and one proxy summary per render. |
| #107/#108 assemble start/finish | Collapse into one terminal stage summary with duration/outcome. |
| #137 `telemetry_delivery_health` as a remote event | A failed sink cannot report its own failure. Keep durable local counters and attach them to a later successful flush. |
| #142 feedback topic classifier | At beta scale, read opted-in feedback. A local classifier adds false precision before enough messages exist. |
| #154 dormant-project inventory | Derive from project/open/export facts; do not add a new periodic source family. |

## 5. DISAGREEMENTS OF JUDGMENT

| Question | Claude's position | Mine | What should settle it |
|---|---|---|---|
| Granularity | About 28 uploaded events/session via session, turn, and render rollups. | 135/session, preserving semantic editor/tool terminals. | Claude wins on transport. Preserve action/tool detail in a bounded local reducer, upload terminal outcomes plus one rollup, and keep 100% of failures. |
| Event ceiling | 27 claimed; rolled data remains free through 2,000 installs. | 135 average, 500/session cap. | Set a target of <=40 normal events/session and a hard 100-event upload ceiling, with critical failures/export bypassing the cap. |
| Taxonomy location | One JSON taxonomy imported by Python and JS. | A schema/contract plus adapters, with a common envelope. | Adopt the shared machine-readable taxonomy, but add the missing envelope and allow derived metrics so “every entry has a source call” is not applied to metric-only rows. |
| Activation | First export within 7 days of `app_first_run`. | First export after `ui_ready`, excluding installs that never became ready. | Claude is more honest end-to-end; my denominator hides launch failure. Use first-run installs whose 7-day window elapsed, then split launch-ready conversion as the first funnel step. |
| Retention | Second project, W1/W4 project activity, export cadence; daily return is diagnostic. | D1/D7/D30 plus 30-day meaningful project return. | Merge around second-project and W1/W4 after activation. Keep D7/D30 for comparability, never app-open retention. |
| Wall numbers | Combines agent usefulness+price and render success+cache; unmet capability is wall #5. | Activation, TTV, export success, agent cost/export, crash-free. | Keep five outcome walls; unmet capability is a roadmap feed, not product health. Pair useful-turn rate with cost and render success with repeat-cache rate as drill-downs. |
| Build signal | Agent-declared missing key/pack, route-around, and raw FFmpeg are strongest. | Triangulate requests, blocked intent, workarounds, feedback, and outcomes. | Claude convinced me to rank agent-declared gaps first, but no build decision should ship without distinct-install repetition and post-build export/adoption evidence. |
| Feature usage | Label `commit(fn, op)` and aggregate feature eligibility/undo. | More per-action start/finish and truth-table success signals. | Use one stable `action_id` locally, evaluate success/undo there, and upload per-feature counters in the session summary. Keep separate events only for failures and export. |
| Export hook | App-layer publish return in `render_jobs`; lower-level receipt writer mentioned. | I anchored the receipt block in `lib/project.py`. | Claude is right on layer. Hook after `published=true` in `render_jobs`, then assert receipt current; my lower-level anchor loses origin and fires too early. |

## 6. WHAT TO ADOPT

- Adopt Claude's rolled-up transport model and its distinction between weekly
  decision rows and diagnostic denominator rows.
- Adopt #77 `agent_ffmpeg_freehand`, but classify only a closed filter family
  locally and never upload a command.
- Adopt one per-turn rollup for skill, pipeline definition, media tool, calls,
  errors, and wall time (#58-61), after adding invocation correlation.
- Adopt the full ingest probe vocabulary from #34: container, codecs, pixel
  format, transfer, HDR, fps/VFR, dimensions, rotation, streams, and alpha.
- Adopt #117 decoded/dropped playback frames and remove my rAF cadence row.
- Adopt #125's explicit superseded terminal and exclude it from render
  reliability. The implementation must cover the direct supersede writer.
- Adopt second-project rate, W1/W4 meaningful project activity, and export cadence
  as the retention spine.
- Adopt the feature ledger with adoption, eligibility, twice-use, success,
  redo-adjusted regret, and external-only evidence.
- Adopt the five-screen dashboard shape, especially the agent-health tool table
  and the build-next feed.
- Adopt the shared taxonomy and bidirectional contract test, amended with a
  required common envelope and an explicit `kind: event|metric` distinction.
- Adopt the six-part deletion test as a starting policy, but require enough
  eligible external users and a reversible hide period.
- Adopt the warning that superseded renders are not failures and that daily-open
  retention is the wrong primary unit for a project-based creator tool.

## 7. Convergence proposal

1. Wall: 7-day receipt-backed activation from all elapsed first-run installs.
2. Wall: median and P90 time from first run to first current export.
3. Wall: useful-agent-turn rate, drilled into user/notional cost per first export.
4. Wall: export reliability, drilled into render success and repeat cache hit.
5. Wall: fatal crash-free sessions from session starts, including synthesized ends.
6. Mandatory envelope: schema/event/install/session IDs; nullable project/turn/job/tool IDs.
7. P0: install, launch-ready, auth-connected, project-created, and project-opened.
8. P0: asset ingest/probe outcome with the complete codec/HDR profile.
9. P0: agent turn terminal, correlated tool rollup, missing capability/key, route-around.
10. P0: editor session rollup with action IDs, eligibility, success, undo, and redo.
11. P0: preview seek/stall/error/dropped-frame rollup; never per-frame upload.
12. P0: render queued/terminal, supersede, proxy summary, failure class, publish result.
13. P0: exactly one export event after published=true and current receipt verification.
14. P0: fatal/process/nonfatal error taxonomy plus feedback delivery outcome.
15. Granularity: local per-action state, uploaded terminal failures plus rollups.
16. Ceiling: target <=40 normal events/session; hard 100, critical outcomes bypass.
17. Cost: use progressive PostHog bands; show exact estimate and conservative ceiling.
18. Delete derived metrics and deliberate non-collection items from the event-row count.
