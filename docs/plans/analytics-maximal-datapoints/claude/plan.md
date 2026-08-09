# Maximal product intelligence for OpenNolan — the data-point catalog

STATUS: PLAN
Author: claude · Round 2 of the analytics work · 2026-08-05
Supersedes the data sections of `docs/plans/analytics-feedback-loop/claude/plan.md`.

Scope note, once: **no compliance section.** Assume consent exists and is honored
(`server/settings.py:27` opt-out flag, checked at init in `server/analytics.py:80`).
Assume we keep PostHog and do not add Sentry — `capture_exception`
(`server/analytics.py:179`) plus PostHog Error Tracking already gives one crash
inbox, and a second vendor buys nothing at zero users.

Rows that would transmit the user's creative work are marked **SENS** with a
lower-fidelity alternative in the same cell. That is a product-trust call for the
human to make, not a legal one.

---

## 1. Verdict

Five numbers for the wall:

1. **Activation** — % of installs that reach a watermark-free `export_completed`
   within 7 days of `app_first_run`. Unmeasurable today (§3i).
2. **Agent usefulness + price** — % of agent turns that changed an artifact or
   produced a render, and median USD per successful export. Both are already
   computed in-process and thrown away (`TurnResult`, `server/agent_runner.py:832`).
3. **Render success + cache hit rate** — `done/(done+failed)` through
   `render_jobs._set` (`server/render_jobs.py:309`), and `n_cached/n_scenes`
   (`tools/video/video_compose.py:1559`). The whole "edit live, render rarely"
   thesis in `RULES.md:66` lives or dies on the second number and we have never
   seen it.
4. **Crash-free session rate across all four layers** — Electron, backend,
   renderer, agent. Three of the four already report; nothing joins them to a
   session.
5. **Unmet capability count / week** — how many times the agent asked for a key
   or a pack it did not have (`request_api_key`, `server/agent_runner.py:1040`;
   `request_capability`, `:1084`). This is the build-next list, written by the
   agent, and it costs one `capture()` call.

**Biggest blind spot:** per-tool outcome for the agent. `event_of` already
carries `is_error` on every tool result (`server/agent_runner.py:791` and `:810`),
and `run_turn` drops it — it forwards only `name` and `detail` into
`record_tool_use` (`server/agent_runner.py:1973`). So today we cannot answer
"which of OUR tools does the agent fail with," on a product whose core is an
agent using our tools. The North Star being unwired is worse in impact but it is
already known; this one is silent.

**If I had 3 days:** the taxonomy module + its contract test, `export_completed`
and the full render lifecycle at the one `_set` choke point, `agent_turn_*` at
`run_turn`'s `finally`, and a `session_start`/`session_end` pair with a
crash-free flag. Five files. That alone turns four of the five wall numbers on.

---

## 2. The instrumentation map

`■` observable in PostHog today · `□` computed or logged locally but never sent ·
`·` not captured anywhere.

```
LAUNCH & PROVISIONING
 ■ app_opened            server/app.py:443
 ■ app_first_run         server/app.py:449
 □ desktop_error/fatal   desktop/main.js:65,111   (packaged only, :67)
 · setup window steps    desktop/setup.js         (bar advances, nothing sent)
 · doctor snapshot       lib/provision.py:318     (venv/core/ffmpeg/node/packs)
 · pack install outcome  server/app.py:1070 -> lib/provision.py:444
 · composition install   server/app.py:1060 -> lib/provision.py:655

AUTH & SETUP
 ■ auth_connected        server/app.py:970 (oauth) / :980 (api_key)
 · oauth start/abandon   server/app.py:958 / :963
 · needs_reauth flips    server/auth.py:158 mark / :163 clear / :180 classify
 · BYOK key saved        server/app.py:938  (env PUT)

PROJECT LIFECYCLE
 ■ project_created       server/app.py:544  (pipeline_type, style)
 · project opened        web/src/App.jsx:86
 · editor opened         web/src/App.jsx:32 (`editing`)
 · thread new/switch     server/app.py:1253 / :1265 / agent_runner.py:2026
 · project abandoned     no event; derivable from absence

ASSET INGEST  (RULES.md:94 — "the user can drop in any kind of media")
 · upload (HTTP)         server/app.py:551
 □ ui.uploadAsset        web/src/studio/Studio.jsx:587 (ok) / :588 (fail)
 · probe result          server/app.py:856  ffprobe w/h/duration
 · codec/HDR/fps         NOT probed at ingest at all — only w/h/duration
 · browse navigation     server/app.py:677

AGENT TURN                                     ── the product ──
 · turn start/end        server/agent_runner.py:1924 run_turn
 □ TurnResult            server/agent_runner.py:832  is_error/num_turns/cost
 □ tool_use per call     server/activity.py:170 -> .mc/activity.jsonl
 · tool RESULT is_error  server/agent_runner.py:791,:810  <-- DROPPED at :1973
 · decide_tool verdict   server/agent_runner.py:370  allow/confirm/deny
 · heavy-op steer        server/agent_runner.py:128  bash_runs_heavy_media_op
 · render-via-bash steer server/agent_runner.py:108
 · sandbox escape        server/agent_runner.py:275  bash_path_escape_reason
 · confirm asked/answer  server/agent_runner.py:1298 / :1324
 · ask_user              server/agent_runner.py:920
 · request_api_key       server/agent_runner.py:1040 / :1429
 · request_capability    server/agent_runner.py:1084 / :1506
 · run_media_op          server/agent_runner.py:1128 -> render_jobs.py:403
 · interrupt (Stop)      server/agent_runner.py:2009
 · session death/resume  server/agent_runner.py:1994-1999
 · model switch          server/agent_runner.py:2049
 · @-mention sidecar     server/app.py:1095 resolve_mentions
 ■ agent turn crash      server/app.py:1164 capture_exception
 · auth_error on turn    server/app.py:1145

EDITOR                                       ── 22 dbg.event sites, all local ──
 □ edit.commit / .live   web/src/studio/Studio.jsx:190 / :179
 □ edit.undo / .redo     web/src/studio/Studio.jsx:199 / :208
 □ ui.save               web/src/studio/Studio.jsx:222/:230/:234
 □ ui.render phases      web/src/studio/Studio.jsx:264/:281/:285/:287/:290
 □ ui.select             web/src/studio/Studio.jsx:627
 □ ui.togglePlay/seek    web/src/studio/Studio.jsx:106 / :109
 □ ui.previewMode        web/src/studio/Studio.jsx:110
 □ agent.adopt           web/src/studio/Studio.jsx:320
 · which FEATURE fired   commit() takes an opaque fn — no op label anywhere
 · keyboard vs mouse     both funnel into the same handler (Studio.jsx:634)
 · panel layout use      web/src/studio/Studio.jsx:69 localStorage only
 · inspector field edits web/src/studio/StudioInspector.jsx:31 ScrubField
 · timeline drags        web/src/studio/StudioTimeline.jsx:138 onMove / :210 onUp

PREVIEW / PLAYBACK
 □ preview.seekReq       web/src/studio/StudioPreview.jsx:175
 □ preview.video.<8>     web/src/studio/StudioPreview.jsx:200-203
                         seeking seeked stalled waiting error
                         loadedmetadata loadeddata canplay
 · seek completion time  the recorder analyzer computes it; nothing is sent

RENDER / PROXY / ASSEMBLE
 · every status change   server/render_jobs.py:309 _set   <-- ONE choke point
 · 3 origins             editor :58 / agent :71 / agent_op :94
 · superseded            server/render_jobs.py:168
 · cache hit/miss        tools/video/video_compose.py:1559-1566
 · HDR decision          tools/video/video_compose.py:1568 hdr_handling
 · final review          tools/video/video_compose.py:2619 etc.
 · per-stage timing      not measured anywhere
 · deliverable warning   server/render_jobs.py:436

EXPORT / PUBLISH
 · export_completed      NOT WIRED. Only tests/contracts/test_analytics.py:99
 · publish commit        server/render_jobs.py:566 publish_final_render
 · watermark             no watermark concept in code yet

FEEDBACK
 ■ feedback_submitted    server/feedback.py:177 (kind + has_* booleans)
 · modal opened/dropped  web/src/App.jsx:205 -> :275
 · debug report sent     server/feedback.py:165 _debug_attachment

ERROR PATHS
 ■ backend unhandled     server/app.py:427
 ■ capability discovery  server/app.py:927
 ■ client JS error       web/src/main.jsx:38 -> api.js:63 -> app.py:1016
 ■ ErrorBoundary         web/src/main.jsx:9
 ■ unhandledrejection    web/src/main.jsx:42
 □ desktop/main crash    desktop/main.js:65  (dev suppressed at :71)
 · HTTP 4xx (422/404)    raised as HTTPException, never reach :427
 · swallowed exceptions  analytics.py:176, activity.py:194, recorder.js:132,
                         desktop/main.js:108 — every reporter fails silently
```

The shape of the problem: **instrumentation exists, the sink does not.** Three
independent capture systems already run (`analytics.py`, `activity.jsonl`,
`recorder.js`) and only the smallest one reaches a dashboard.

---

## 3. THE DATA-POINT CATALOG

162 rows. Column key:

- **Properties** — `[E]` enum (fixed small set, safe to group by), `[B]` bucket
  (pre-binned number; bucketing keeps cardinality low and stops a raw value from
  identifying a file), `[N]` raw number (needed for percentiles/sums), `[F]` flag.
- **Pri** — P0 = wire in the first 3 days, P1 = first two weeks, P2 = later.
- **Vol** — events per unit. `/inst` = once per install, `/sess` = per session,
  `/turn`, `/render`, `/export`.
- **SENS** — would transmit or let you reconstruct the user's creative content.

Rows marked ★ in the Decision column are the ~34 rows the developer will
actually read weekly. The rest are there so the ★ rows have denominators, or so a
specific failure can be diagnosed once. That distinction is §12's whole point.

### 3a. Install / provisioning / launch health

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|1|`app_first_run`|`os`[E], `app_version`[E], `arch`[E], `mac_major`[E]|`server/app.py:449` (extend)|How many real installs?|★ Denominator for every funnel|P0|1/inst|no|
|2|`app_opened`|`os`[E], `app_version`[E], `cold_start_ms`[B], `backend_boot_ms`[B]|`server/app.py:443` (extend)|Is launch slow enough to hurt?|Optimize boot if p90 > 8s|P0|1/sess|no|
|3|`provisioning_snapshot`|`venv_ok`[F], `core_ok`[F], `ffmpeg_ok`[F], `node_ok`[F], `remotion_ok`[F], `hyperframes_ok`[F], `composition_ok`[F], `packs_installed`[E list], `forced`[F]|new, from `lib/provision.py:318` at first `/api/doctor`|What fraction of installs are half-provisioned?|★ Kill or fix the lazy-pack model|P0|1/sess|no|
|4|`provision_started`|`target`[E] (`venv`\|`core`\|`composition`\|pack name), `size_mb`[N]|`server/app.py:1060`,`:1070`|Does anyone opt into the 2.6 GB transcription pack?|★ Prune `PACKS` (`lib/provision.py:74`)|P0|0–5/inst|no|
|5|`provision_finished`|`target`[E], `ok`[F], `duration_s`[B], `error_class`[E]|`lib/provision.py:444`/`:655` completion|Which install step fails most?|★ Bundle vs lazy-install per pack|P0|0–5/inst|no|
|6|`provision_step`|`target`[E], `step`[E], `duration_s`[B]|`_stream_provision` worker, `server/app.py:1031`|Where inside a 4-min install does it stall?|Reorder/parallelize steps|P2|~8/install|no|
|7|`setup_window_abandoned`|`last_step`[E], `elapsed_s`[B]|`desktop/setup.js` unload|Do users quit during first-run install?|★ Move install off the critical path|P1|≤1/inst|no|
|8|`backend_never_healthy`|`stderr_class`[E], `attempts`[N], `wait_ms`[B]|`desktop/main.js:111` `fatal()`|The worst first-run failure — how common?|★ Ship-blocker gate|P0|rare|**SENS** — stderr tail can hold paths. Send a classified `stderr_class` enum + a hash, not the tail; the tail already rides the local fatal dialog.|
|9|`ffmpeg_capabilities`|`has_zscale`[F], `has_libx265`[F], `has_hevc_videotoolbox`[F], `ffmpeg_major`[E]|first render, from the probes at `tools/video/video_compose.py:1303-1347`|How many users physically cannot preserve HDR?|★ Bundle a fuller ffmpeg or drop the `preserve` policy|P1|1/inst|no|
|10|`disk_headroom`|`free_gb`[B], `home_size_mb`[B]|`/api/doctor` handler|Do proxies fill users' disks?|Add proxy GC|P2|1/sess|no|
|11|`hardware_class`|`cpu_arch`[E], `cores`[B], `ram_gb`[B]|`server/app.py:443`|Is the floor an M1/8 GB?|★ Set the published minimum spec|P1|1/inst|no|
|12|`app_version_upgrade`|`from`[E], `to`[E]|`server/settings.py` compare-and-set on boot|Do users update?|Force-update policy|P1|rare|no|
|13|`session_start`|`session_id`[N uuid], `entry`[E] (`dashboard`\|`editor`\|`setup`)|new `web/src/analytics/track.js`|Session denominator|★ Every per-session rate|P0|1/sess|no|
|14|`session_end`|`duration_s`[B], `surfaces`[E list], `clean_exit`[F], `errors`[N]|`pagehide`, mirroring `recorder.js:198`|Session length; crash-free rate|★ Crash-free session rate|P0|1/sess|no|

### 3b. Auth & setup funnel

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|15|`auth_prompt_shown`|`reason`[E] (`first_run`\|`chat_503`\|`needs_reauth`)|`web/src/App.jsx:33` `showConnect`|How many see the wall?|★ Auth funnel top|P0|~1/inst|no|
|16|`auth_method_chosen`|`method`[E] (`oauth`\|`api_key`), `cli_available`[F]|`server/app.py:958`/`:973`|Does anyone still paste a key?|Drop the API-key path if <10%|P0|~1/inst|no|
|17|`auth_oauth_abandoned`|`elapsed_s`[B]|`start_oauth` (`server/auth.py:228`) with no matching finish|Does the browser round-trip lose people?|★ #1 funnel-fix candidate|P0|≤1/inst|no|
|18|`auth_connected`|`method`[E], `attempts`[N], `time_to_connect_s`[B]|`server/app.py:970`/`:980` (extend)|Setup conversion|★ Activation numerator step|P0|1/inst|no|
|19|`auth_failed`|`method`[E], `http_status`[E], `error_class`[E]|`server/auth.py:275`, `:369` `_validate_api_key`|Bad keys or a broken flow?|Improve the error copy|P0|rare|no|
|20|`auth_needs_reauth`|`days_since_connect`[B], `trigger`[E]|`server/auth.py:158` `mark_auth_error`|Do tokens silently expire mid-project?|★ Add proactive refresh|P1|rare|no|
|21|`auth_disconnected`|`had_exports`[F]|`server/app.py:983`|Is disconnect a churn signal?|Exit-intent survey|P2|rare|no|
|22|`byok_key_saved`|`var_name`[E] (curated menu only), `count_total`[N]|`server/app.py:938`|Which providers do users actually pay for?|★ Which generation tools to invest in|P1|0–8/inst|no — the menu is a fixed enum; the value is redacted by `_SECRET_HINT` (`analytics.py:37`)|
|23|`capabilities_panel_opened`|`from`[E]|`web/src/App.jsx:202`|Is the pack UI discoverable?|Move or remove it|P2|≤1/sess|no|

### 3c. Project lifecycle

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|24|`project_created`|`pipeline_type`[E], `style`[E], `ordinal`[N], `via`[E] (`dashboard`\|`agent`)|`server/app.py:544` (extend)|Which styles get chosen?|★ Prune `styles/` playbooks|P0|1–5/inst|no — `pipeline_type`/`style` are validated against a closed list at `:533`/`:540`. Project NAME is never sent.|
|25|`project_opened`|`age_days`[B], `has_export`[F], `n_cuts`[B], `n_overlays`[B]|`web/src/App.jsx:86`|Do users return to old projects or always start fresh?|★ Decides whether "projects" is even the right unit|P0|1–8/sess|no|
|26|`second_project_created`|`days_since_first`[B], `first_exported`[F]|derived from #24 `ordinal==2`|The real habit signal (§4)|★ Primary retention number|P0|≤1/inst|no|
|27|`editor_opened`|`from`[E] (`dashboard`\|`chat`), `doc_exists`[F], `scaffolded`[F]|`web/src/App.jsx:32`|Agent-first or editor-first?|★ Where to spend UI budget|P0|1–4/sess|no|
|28|`thread_created`|`ordinal`[N]|`server/app.py:1253`|Do users use multiple chat threads?|Delete threads if p95 == 1|P1|0–3/sess|no|
|29|`thread_switched`|`n_threads`[N], `session_resumed`[F]|`server/app.py:1265` → `agent_runner.py:2026`|Same|Same|P1|rare|no|
|30|`project_deleted`|`had_export`[F], `age_days`[B]|no endpoint today — user deletes on disk|Silent churn|Add an in-app delete so it's observable|P2|rare|no|
|31|`project_stalled`|`last_activity_days`[B], `furthest_stage`[E], `n_turns`[N], `n_commits`[N]|nightly sweep in the backend over `projects/`|**Where do projects die?**|★ The single most actionable abandonment metric|P1|1/inst/day|no|
|32|`pipeline_stage_reached`|`stage`[E], `project_ordinal`[N]|`server/app.py:743` `/state` transitions|Which pipeline stage is the cliff?|★ Rewrite that stage's skill|P1|~6/project|no|

### 3d. Asset ingest

`RULES.md:94` says any media can land here, and today ingest probes only width,
height and duration (`server/app.py:856`). Codec, container, pixel format,
color transfer, fps and audio layout are the exact properties that decide
whether a render succeeds — and we capture none of them.

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|33|`asset_ingested`|`kind`[E], `container`[E], `size_mb`[B]|`server/app.py:551` (post-write)|Volume + mix of ingest|★ Ingest denominator|P0|0–20/sess|no — `kind` and `container` are enums; the FILENAME is not sent (see #46)|
|34|`asset_probe`|`vcodec`[E], `acodec`[E], `pix_fmt`[E], `color_transfer`[E], `is_hdr`[F], `fps`[B], `width`[N], `height`[N], `aspect`[E], `duration_s`[B], `bitrate_mbps`[B], `rotation`[E], `n_audio_streams`[N], `has_alpha`[F], `vfr`[F]|extend `server/app.py:856`, or a new probe at upload|**Which codecs do real users drop on us?**|★ Which decode paths to harden; whether HDR is a real user problem or only Het's|P0|1/asset|no — all fixed-vocabulary ffprobe fields|
|35|`asset_probe_failed`|`reason`[E] (`no_ffprobe`\|`nonzero_exit`\|`parse`), `container`[E]|`server/app.py:882` `returncode != 0`|How often is a source unreadable?|★ Show an ingest error instead of silently un-clamped trims|P0|rare|no|
|36|`asset_unsupported`|`vcodec`[E], `where`[E] (`preview`\|`render`)|`<video>` `error` event, `StudioPreview.jsx:200`|Which codec previews but won't render (or vice versa)?|★ Transcode-on-ingest decision|P1|rare|no|
|37|`asset_ingest_method`|`method`[E] (`drop_zone`\|`file_picker`\|`agent_store_asset`\|`timeline_drop`)|`StudioAssets.jsx:92`, `:61`; `agent_runner.py:1000`|Is drag-drop the real path?|Simplify to one path|P1|1/asset|no|
|38|`asset_reused`|`times_used`[B], `across_projects`[F]|`_resolve_sources`, `render_jobs.py:190`|Do users build an asset library?|★ Decides a global asset library feature|P2|1/render|no|
|39|`hdr_source_detected`|`transfer`[E] (`hlg`\|`pq`), `policy_default`[E]|`tools/video/video_compose.py:1568` `hdr_handling.source_hdr`|Is the HDR work load-bearing for anyone but us?|★ Keep or delete the HDR path (a large surface)|P1|1/render|no|
|40|`asset_browse`|`folder`[E] (fixed set: `images`/`video`/`audio`/`music`/`hf/renders`/`renders`), `depth`[B], `n_items`[B]|`server/app.py:677`|Is the folder browser used or is DnD enough?|Simplify the Assets tab|P2|0–30/sess|no — folder names only, never leaf filenames|
|41|`asset_modal_opened`|`kind`[E], `action_taken`[E] (`add`\|`close`)|`web/src/components/AssetModal.jsx`|Does "click opens, button adds" work?|★ Revert to click-to-add if `close` dominates|P1|0–20/sess|no|
|42|`asset_upload_failed`|`error_class`[E], `http_status`[E]|`Studio.jsx:588`|Broken uploads|Fix|P0|rare|no|
|43|`asset_kind_mismatch`|`declared_kind`[E], `probed_kind`[E]|compare `kind` form field to probe at `server/app.py:551`|Do users put video in `audio/`?|Auto-route by media type|P1|rare|no|
|44|`asset_filename_shape`|`ext`[E], `name_len`[B], `sanitized`[F]|`sanitize_filename` at `server/app.py:573`|Do real filenames break our sanitizer?|Loosen the sanitizer|P1|1/asset|no — shape only, no name|
|45|`asset_giant`|`size_mb`[B], `duration_s`[B]|`server/app.py:551`|Are people dropping 4K 10-minute files into a Reels tool?|★ Proxy-on-ingest decision|P1|rare|no|
|46|~~`asset_name`~~|—|—|—|**Do not collect.** A filename is creative content (`camping-with-sarah.mov`). Ship #44's shape instead.|—|—|**SENS**|

### 3e. Agent turn — per turn and per tool call

This is the largest and most valuable group, and it is the cheapest to wire:
every number already exists in process.

**Per turn**

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|47|`agent_turn_started`|`turn_ordinal`[N], `is_fresh_client`[F], `model`[E], `has_mentions`[F], `n_mentions`[B], `prompt_len`[B], `prompt_shape`[E] (`question`\|`imperative`\|`paste`), `from_surface`[E] (`dashboard`\|`editor`)|`server/agent_runner.py:1924`|Turn denominator; are mentions used?|★ Every agent rate|P0|1–20/sess|no — length + shape, never the text. `_scrub` already forces this (`analytics.py:38`).|
|48|`agent_turn_completed`|`is_error`[F], `num_turns`[N], `cost_usd`[N], `wall_s`[B], `n_tool_calls`[N], `n_tool_errors`[N], `stop_reason`[E], `doc_changed`[F], `artifacts_written`[N], `render_started`[F]|`server/agent_runner.py:1992` (`finally`), reading `TurnResult` (`:832`)|**Did the turn do anything useful, and what did it cost?**|★ Agent success rate; cost per export; model choice|P0|1/turn|no|
|49|`agent_turn_crashed`|`error_class`[E], `auth_related`[F]|`server/app.py:1164`|Turn-level crash rate|★ Ship-blocker gate|P0|rare|no (already partly wired)|
|50|`agent_session_died`|`had_result_error`[F], `will_resume`[F]|`server/agent_runner.py:1994-1999`|How often does the SDK session break?|★ Whether resume-after-death is reliable|P0|rare|no|
|51|`agent_interrupted`|`elapsed_s`[B], `n_tool_calls_so_far`[N], `last_tool`[E]|`server/agent_runner.py:2009`|**What is the agent doing when users give up?**|★ The strongest "agent is failing here" signal|P0|rare|no|
|52|`agent_model_switched`|`from`[E], `to`[E], `mid_thread`[F]|`server/agent_runner.py:2049`|Do users downgrade for cost or upgrade for quality?|★ Default model choice|P1|rare|no|
|53|`agent_unsolicited_turn_drained`|`n_events_drained`[N]|`server/agent_runner.py:1938` `_drain_unsolicited`|Is the off-by-one still happening?|Regression alarm on a hard-won fix|P1|rare|no|
|54|`agent_resume_note_injected`|`kind`[E] (`render`\|`media_op`), `status`[E]|`server/agent_runner.py:1803`|Does the survive-a-Stop path fire?|Keep or delete the mechanism|P1|rare|no|
|55|`agent_budget_ceiling_hit`|`cost_usd`[N], `max_budget_usd`[N]|`TurnResult.is_error` + cost ≥ cap (`agent_runner.py:866`)|Is the cap too low?|★ Raise/lower the default cap|P0|rare|no|
|56|`agent_cost_per_turn` (metric)|p50/p90 of `cost_usd`|from #48|BYOK means this is the USER's money|★ Publish an honest cost estimate on the site|P0|—|no|

**Per tool call**

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|57|`agent_tool_call`|`tool`[E], `op`[E] (from `activity._OP_BY_TOOL`, `activity.py:31`), `category`[E] (`activity.py:72`), `is_error`[F], `wall_ms`[B], `result_bytes`[B]|`server/agent_runner.py:1973` — **thread `is_error` through from `:791`/`:810`**|**Which tools does the agent fail with?**|★ Fix or delete that tool|P0|20–150/turn → roll up (§9)|no — tool NAME and op only. `detail` (a path or a shell command) stays local in `activity.jsonl`.|
|58|`agent_tool_summary`|per-turn map `{tool: {calls, errors, ms_total}}`|end of `run_turn`, aggregating #57|Same, at 1/turn instead of 150/turn|★ Same, affordable|P0|1/turn|no|
|59|`agent_skill_used`|`skill`[E]|`activity.py:263` `_skill_label`|Which of `skills/` is dead weight?|★ Delete unused skills|P0|0–10/turn|no|
|60|`agent_pipeline_def_read`|`pipeline_def`[E]|`activity.py:243`|Same for `pipeline_defs/`|★ Prune|P1|0–5/turn|no|
|61|`agent_media_tool_run`|`slug`[E] (`_bash_tool_slug`, `activity.py:93`), `via`[E] (`bash`\|`run_media_op`)|`activity.py:246`|Which of our ~40 `tools/video/*` does the agent ever reach for?|★ **The tool-deletion list**|P0|0–10/turn|no|
|62|`agent_tool_denied`|`tool`[E], `reason_class`[E] (`path_escape`\|`destructive`\|`unrecognized`)|`server/agent_runner.py:370` `decide_tool` on `ACTION_DENY`|Is the sandbox blocking legitimate work?|★ Loosen a boundary, or keep it|P0|rare|no — classify the reason, never send the path|
|63|`agent_confirm_requested`|`tool`[E], `reason_class`[E]|`server/agent_runner.py:1298`|How often is the user interrupted?|★ Auto-allow patterns that are always approved|P0|0–5/turn|no|
|64|`agent_confirm_resolved`|`approved`[F], `wait_s`[B], `timed_out`[F]|`server/agent_runner.py:1324`|Approval rate per pattern|★ If approval > 95%, stop asking|P0|1/confirm|no|
|65|`agent_ask_user`|`had_options`[F], `answered`[F], `wait_s`[B]|`server/agent_runner.py:920`, `:1361`|Does the agent ask good questions or stall?|Tune the guide's ask policy|P1|0–3/turn|no — question TEXT is content|
|66|`agent_bash_share` (metric)|`bash_calls / total_calls`|from #57|How much is the agent working around our tools?|★ High share = missing first-class tools|P0|—|no|
|67|`agent_tool_not_found`|`attempted`[E]|`decide_tool`'s `ACTION_CONFIRM "unrecognized tool"` (`agent_runner.py:430`)|**The agent reached for a tool that does not exist**|★ Highest-signal build request (§5)|P0|rare|no|
|68|`agent_wrote_edit_decisions`|`n_cuts`[B], `n_overlays`[B], `has_audio`[F], `schema_valid`[F]|`server/app.py:784` with an agent-origin marker|Does the agent produce valid timelines?|★ Schema/prompt fix|P0|0–3/turn|no|
|69|`agent_store_asset`|`kind`[E], `ok`[F]|`server/agent_runner.py:1000`|Does the agent generate assets or only arrange them?|Which generation tools matter|P1|0–5/turn|no|
|70|`agent_turn_shape` (metric)|distribution of `n_tool_calls` per turn|from #48|Is a turn 5 calls or 150?|★ Whether the agent is efficient enough to be affordable|P1|—|no|

**Unmet need — the build list (see also §5)**

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|71|`capability_missing`|`pack`[E], `reason_class`[E]|`server/agent_runner.py:1438` `_request_capability`|Which local pack did the agent need?|★ Bundle it or drop the feature|P0|rare|no|
|72|`capability_install_answered`|`pack`[E], `installed`[F], `wait_s`[B]|`server/agent_runner.py:1506`|Will users install a 2.6 GB pack mid-turn?|★ If refusal > 50%, bundle or cut|P0|rare|no|
|73|`api_key_missing`|`env_var`[E], `provider`[E]|`server/agent_runner.py:1370`|Which paid provider did the agent want?|★ Which integration to make first-class|P0|rare|no|
|74|`api_key_answered`|`env_var`[E], `provided`[F]|`server/agent_runner.py:1429`|Will users pay?|★ Same|P0|rare|no|
|75|`agent_routed_around_us`|`marker`[E] (`silence_cutter`\|`motion_ops`\|`auto_reframe`\|`object_cutout`)|`server/agent_runner.py:128` `bash_runs_heavy_media_op`|The agent hand-rolled what we have a tool for|★ The tool's interface is wrong — fix its docs or signature|P0|rare|no|
|76|`agent_rendered_via_bash`|—|`server/agent_runner.py:108`|Same, for render|★ Same|P0|rare|no|
|77|`agent_ffmpeg_freehand`|`filter_family`[E] (`overlay`\|`scale`\|`concat`\|`atempo`\|…), `had_tool`[F]|classify Bash commands in `activity.py:108` `_segment_tool`|**The agent wrote raw ffmpeg — that is a missing tool, named**|★ Top-3 build channel (§5)|P1|0–10/turn|no — extract the filter family, never the command|
|78|`mention_search_miss`|`n_candidates`[N] `== 0`, `query_len`[B]|`web/src/chat/mentions.js` `rankCandidates`|Users looked for an asset that isn't there|★ Whether asset search needs to be smarter|P1|0–10/sess|no — count + length only; the query is content|
|79|`mention_used`|`group`[E], `rank_chosen`[N]|`web/src/chat/ChatPanel.jsx:103`|Is `@` adopted?|★ Keep or delete OPN-27|P1|0–5/turn|no|

### 3f. Editor — per-feature usage

The blocker here is structural: `commit` (`web/src/studio/Studio.jsx:186`) takes
an opaque `next` function, so nothing downstream knows which feature fired. The
one change that unlocks this entire group is to make every call site pass a
label: `commit(fn, 'overlay.trim')`. 40-odd one-word additions, and then #80
below covers the whole editor.

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|80|`edit_commit`|`op`[E] (new label), `input_method`[E] (`drag`\|`type`\|`key`\|`button`\|`dnd`), `selection_kind`[E], `doc_delta`[E] (from `summarizeDocChange`, `model.js:501`)|`web/src/studio/Studio.jsx:190`|**Which editor features are used at all?**|★ Feature adoption for every row in §7|P0|50–300/sess → roll up|no — `summarizeDocChange` returns changed KEY NAMES, never values|
|81|`edit_undo` / `edit_redo`|`undone_op`[E] (needs `op` stored on the history entry at `Studio.jsx:168`), `depth`[N]|`Studio.jsx:199` / `:208`|**Which feature do users immediately take back?**|★ Undo rate per feature — the strongest "this control is wrong" signal|P0|5–50/sess|no|
|82|`editor_session_summary`|counters keyed by `op`: `{commits, undos, live_frames, selects}` + `features_used`[E list] + `features_eligible`[E list] + `duration_s`[B] + `n_cuts`[B] + `n_overlays`[B] + `n_tracks`[B] + `zoom_final`[B] + `panel_layout`[E]|new, on `pagehide` (mirror `recorder.js:200` `flush`)|The whole editor in one event|★ Adoption + discovery + depth, at 1 event/session|P0|1/sess|no|
|83|`feature_first_use`|`op`[E], `days_since_install`[B], `session_ordinal`[N]|derived server-side from #80|**Time-to-discovery per feature**|★ Features with a long discovery lag need UI work, not more features|P1|1/feature/inst|no|
|84|`feature_second_use`|`op`[E], `gap_days`[B]|derived from #80|**Does anyone use it twice?**|★ The deletion test (§6)|P0|1/feature/inst|no|
|85|`selection_changed`|`kind`[E] (`cut`\|`overlay`\|`audio`), `audio_kind`[E], `source`[E] (`timeline`\|`canvas`\|`keyboard`\|`add`)|`web/src/studio/Studio.jsx:627`|Do users select on the canvas or the timeline?|★ Which surface to invest in|P1|100–400/sess → roll up|no|
|86|`inspector_field_edited`|`type`[E] (7 `PROPERTY_TITLES`, `propertySchema.js:17`), `field_key`[E] (every `key` in `PROPERTY_SCHEMA`, `:60`), `control`[E], `via`[E] (`scrub_drag`\|`type`\|`arrow`)|`StudioInspector.jsx:31` `ScrubField` + the generic field renderer|**Which of the ~45 inspector fields are dead?**|★ Delete dead fields; the schema is a closed enum so this is exhaustive by construction|P0|10–80/sess|no|
|87|`scrub_drag`|`field_key`[E], `frames`[B], `delta_steps`[B], `fine`[F] (shift)|`StudioInspector.jsx:80` (`onScrubBegin` on first move)|Is drag-to-scrub used, or does everyone click-to-type?|★ Keep or simplify `ScrubField` (a bespoke control with real cost)|P1|10–60/sess|no|
|88|`timeline_drag`|`mode`[E] (`scrub`\|`trim-in`\|`trim-out`\|`reorder`\|`ov-move`\|`ov-trim-in`\|`ov-trim-out`\|`aud-move`\|`aud-trim-*`), `duration_ms`[B], `cancelled`[F]|`StudioTimeline.jsx:138` `onMove` / `:210` `onUp` / `:233` `onCancel`|Which timeline gestures exist in practice?|★ Delete unused drag modes|P0|20–150/sess|no|
|89|`timeline_reorder`|`from`[N], `to`[N], `n_cuts`[B]|`StudioTimeline.jsx:218`|Do users reorder, or does the agent's order stand?|★ Whether reorder-by-drag earns its complexity|P1|0–20/sess|no|
|90|`clip_op`|`op`[E] (`split`\|`delete`\|`duplicate`), `target`[E], `via`[E] (`button`\|`key`)|`Studio.jsx:351`/`:376`/`:392`|The three core ops — used?|★ Keyboard-vs-button decides whether shortcuts are discoverable|P0|5–40/sess|no|
|91|`overlay_auto_stacked`|`preferred_track`[N], `landed_track`[N], `bumped`[F]|`interp.placeOverlayTrack` (`interp.js:512`) via `Studio.jsx:525`|Does auto-stacking help or surprise?|★ Correlate with #81 undo rate — high undo means it surprises|P1|0–20/sess|no|
|92|`overlay_overlap_resolved`|`floated`[F], `from_track`[N], `to_track`[N]|`interp.resolveOverlayOverlap` (`interp.js:562`)|Same, on drag-end|★ Same|P1|0–20/sess|no|
|93|`auto_arrange_used`|`n_overlays`[B], `tracks_before`[N], `tracks_after`[N], `undone_within_5s`[F]|`Studio.jsx:513` → `interp.js:532`|Is the ⇅ Arrange button used?|★ Prime deletion candidate if adoption < 5%|P0|0–3/sess|no|
|94|`track_visibility_toggled`|`track`[N], `hidden`[F]|`Studio.jsx:59` `hiddenTracks`|Is preview-only hide used?|Keep or delete|P1|0–10/sess|no|
|95|`zoom_changed`|`from`[B], `to`[B], `via`[E] (`slider`)|`StudioTimeline.jsx:279`|Is 20–240 px/s the right range? Do users want pinch/keyboard zoom?|★ Add keyboard zoom if the slider is hammered|P1|0–30/sess|no|
|96|`canvas_changed`|`from`[E], `to`[E] (`CANVAS_PRESETS`, `model.js:28`)|`Studio.jsx:596`|Does anyone leave 9:16?|★ If nobody does, delete the picker (`RULES.md:3` says vertical is the point)|P0|0–2/sess|no|
|97|`text_overlay_added`|`via`[E] (`+Text`), `then_edited`[F], `len`[B]|`Studio.jsx:485`|Is +Text used?|Keep in the toolbar or move to Assets|P0|0–10/sess|no — length only; the TEXT is creative content (§12)|
|98|`keyframes_edited`|`dim`[E] (`x`\|`y`\|`scale`\|`rotation`\|`opacity`), `n_kf`[B], `easing`[E], `preset`[E]|`Studio.jsx:607-609`; `model.presetKeyframes` (`model.js:165`)|Is manual keyframing used, or only presets?|★ Big surface — delete manual keyframing if presets dominate|P0|0–30/sess|no|
|99|`crop_used`|`axis`[E], `forced_rerender`[F]|`StudioInspector.jsx:299`|Crop forces a re-render (`RULES.md:180`) — worth it?|★ Whether "crop is a content edit" is acceptable UX|P1|0–10/sess|no|
|100|`transition_set`|`kind`[E] (`TRANSITIONS`, `model.js:7`), `where`[E] (`in`\|`out`), `duration_s`[B]|`propertySchema.js:49`|Which transitions do users pick?|★ Delete unused transitions from `TRANSITIONS`|P0|0–20/sess|no|
|101|`speed_changed`|`to`[B], `via`[E] (`preset`\|`scrub`\|`type`)|`propertySchema.js:69-70`|Are presets enough?|Drop the free-form speed field|P1|0–10/sess|no|
|102|`audio_op`|`kind`[E] (`music`\|`narration`\|`sfx`), `op`[E] (`add`\|`move`\|`trim`\|`split`\|`volume`\|`fade`\|`remove`)|`Studio.jsx:416`/`:423`; `interp.js:724-887`|Is the audio lane used?|★ Audio is a large recent surface — justify it|P0|0–40/sess|no|
|103|`audio_mix_toggled`|`enabled`[F], `volume`[B]|`StudioInspector.jsx:323`|Does anyone unmute a video overlay?|Delete the control|P1|0–5/sess|no|
|104|`background_set`|`type`[E] (`color`\|`image`), `cleared`[F]|`Studio.jsx:579` → `interp.js:625`|Used?|Keep/delete|P1|0–3/sess|no|
|105|`save_manual`|`dirty_ms`[B], `via`[E] (`button`\|`cmd_s`)|`Studio.jsx:230`|Does anyone press Save when autosave exists?|★ **Delete the Save button** if manual saves are ~0|P0|0–20/sess|no|
|106|`autosave_fired`|`debounce_ms`[N], `blocked_by`[E] (`none`\|`agent`\|`reconciling`)|`Studio.jsx:247`|Is 700 ms right? How often is autosave blocked?|★ Tune the debounce; detect agent/user contention|P0|20–200/sess → roll up|no|
|107|`save_rejected_422`|`error_class`[E], `origin`[E] (`ui`\|`agent`)|`Studio.jsx:234`; `server/app.py:791`|`RULES.md:55` says a Save must never 422 — does it?|★ Contract-violation alarm|P0|rare|no|
|108|`save_blocked_agent`|—|`Studio.jsx:222`|How often do agent and user collide?|★ Whether the shared-doc model is confusing users|P0|0–20/sess|no|
|109|`agent_adopt`|`had_local_edits`[F], `doc_delta`[E], `undone_within_30s`[F]|`Studio.jsx:320`|**Does the user keep or reject what the agent did?**|★ The single best agent-quality signal in the editor|P0|0–20/sess|no|
|110|`keyboard_shortcut_used`|`key`[E] (`space`\|`esc`\|`cmd_z`\|`cmd_shift_z`\|`cmd_s`\|`s`\|`del`)|`Studio.jsx:634`|Keyboard or mouse user?|★ Which shortcuts to document / add|P1|0–100/sess → roll up|no|
|111|`panel_resized`|`which`[E], `to_pct`[B], `collapsed`[F]|`Studio.jsx:69-75`|Do users collapse the timeline or the inspector?|★ Default layout|P1|0–20/sess|no|
|112|`preview_mode_switched`|`to`[E] (`source`\|`render`), `has_render`[F]|`Studio.jsx:110`|Do users trust the live source preview, or keep checking the render?|★ Direct measurement of the `RULES.md:66` North Star|P0|0–30/sess|no|
|113|`edit_to_export` (metric)|`commits / export_completed`|from #80 + §3i|Are we an editor or a one-shot generator?|★ Where the product actually sits|P0|—|no|
|114|`debug_recorder_used`|`duration_s`[B], `n_events`[N], `report_sent`[F]|`Studio.jsx:118`; `recorder.js:261`/`:268`|Do users record sessions for us?|★ Keep the recorder in the shipped UI or hide it behind a dev flag|P1|rare|no|

### 3g. Preview / playback performance

Everything here already fires into the recorder (`StudioPreview.jsx:200-203`);
it just needs a session-level rollup instead of per-event shipping.

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|115|`preview_health`|`n_seeks`[N], `p50_seek_ms`[B], `p90_seek_ms`[B], `n_incomplete_seeks`[N], `n_stalled`[N], `n_waiting`[N], `n_errors`[N], `mode`[E]|new session rollup over `StudioPreview.jsx:175` + `:203`|**Is scrubbing actually smooth on other people's machines?**|★ The core interaction; a bad p90 outranks any new feature|P0|1/sess|no|
|116|`seek_never_completed`|`readyState`[E], `delta_s`[B], `codec`[E]|`preview.seekReq` with no matching `seeked` (`StudioPreview.jsx:175`, `:200`)|The known scrub-freeze suspect|★ Ship-blocker if > 1% of seeks|P0|rare|no|
|117|`playback_dropped_frames`|`decoded`[N], `dropped`[N], `n_overlay_videos`[N]|`video.getVideoPlaybackQuality()` in `StudioPreview`|How many live video overlays can a real Mac composite?|★ Cap concurrent overlay videos|P1|1/sess|no|
|118|`overlay_video_count`|`n`[B], `total_px`[B]|`StudioPreview` `ovVideoEls`|Same|Same|P1|1/sess|no|
|119|`preview_audio_tracks`|`n`[B], `desynced`[F]|`model.previewAudioTracks` (`model.js:200`)|Does the audio preview stay in sync?|★ Preview==export credibility|P1|1/sess|no|
|120|`render_preview_watched`|`watch_s`[B], `pct_of_duration`[B], `replays`[N]|`StudioPreview` render-mode `<video>`|Do users watch the whole thing before exporting?|Proxy for satisfaction|P2|0–20/sess|no|
|121|`frame_endpoint_latency`|`ms`[B]|`server/app.py:813`|Is the frame endpoint a scrub bottleneck?|Cache frames|P2|0–200/sess → roll up|no|

### 3h. Render / proxy / assemble

One hook at `render_jobs._set` (`server/render_jobs.py:309`) covers every row
below, for all three origins (`editor` `:58`, `agent` `:71`, `agent_op` `:94`),
because every status transition in the file flows through it.

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|122|`render_started`|`origin`[E], `n_cuts`[B], `n_overlays`[B], `n_tracks`[B], `duration_s`[B], `has_audio`[F], `renderer_family`[E], `hdr_policy`[E]|`render_jobs.py:58`/`:71`/`:94`|Render denominator; timeline complexity|★ Render success rate|P0|1–10/sess|no|
|123|`render_finished`|`status`[E] (`done`\|`failed`\|`superseded`), `wall_s`[B], `error_class`[E], `n_scenes`[N], `n_cached`[N], `n_rendered`[N], `cache_hit_rate`[N], `output_mb`[B]|`render_jobs.py:309` `_set` on a terminal status; payload from `video_compose.py:1559-1566`|**Success rate and cache hit rate**|★ Wall number #3; validates render-once|P0|1/render|no|
|124|`render_cache_hit_rate` (metric)|`n_cached / n_scenes`|from #123|Is "edit live, render rarely" real?|★ If it's low, the cache key is wrong|P0|—|no|
|125|`render_superseded`|`age_ms`[B], `stage`[E] (`queued`\|`running`)|`render_jobs.py:168` `_mark_superseded_locked`|Are users spamming Render?|★ Debounce the button; do not count as failure|P0|rare|no|
|126|`render_stage_timing`|`stage`[E] (`resolve_sources`\|`proxies`\|`assemble`\|`audio_mix`\|`publish`\|`final_review`), `ms`[B]|instrument `_render_locked` (`render_jobs.py:503-605`)|**Where does a 90-second render spend its time?**|★ What to optimize; what to parallelize|P0|~6/render|no|
|127|`render_failed_reason`|`error_class`[E] (`no_edit_decisions`\|`cut_source_not_found`\|`ffmpeg_nonzero`\|`crop_oob`\|`missing_encoder`\|`renders_dir_escapes`\|`tool_exception`), `ffmpeg_exit`[E]|`render_jobs.py:563` (`result.error`), `:359`, `:401`, `:434`, `:628`|**The render failure taxonomy**|★ Fix the top reason each week|P0|1/failure|**SENS** — `result.error` embeds absolute paths. `_scrub` (`analytics.py:145`) redacts them, but classify to an enum FIRST and send `error_class`, not the string.|
|128|`render_warnings`|`warning_class`[E list] (`default_renderer_family`\|`hdr_no_zscale`\|`hdr_no_10bit`\|`mixed_hdr_sdr`\|`deliverable_write`)|`render_jobs.py:527`, `:588`, `:452`; `video_compose.py:1307-1347`|Which warnings fire in the wild?|★ Turn a common warning into a fix or a hard error|P0|0–5/render|no|
|129|`hdr_decision`|`policy`[E], `source_hdr`[F], `decision`[E], `encoder`[E], `target`[E]|`video_compose.py:1568` `hdr_handling`|Does the HDR machinery ever engage for a real user?|★ Keep or delete a large code surface|P1|1/render|no|
|130|`final_review_status`|`status`[E]|`render_jobs.py:603`; `video_compose.py:2619`|Does the automated review catch anything?|Keep/delete the review pass|P1|1/render|no|
|131|`proxy_cache_size`|`n_files`[B], `total_mb`[B]|`renders/proxies` scan at `/api/doctor`|Is the cache eating disks?|★ Add GC|P1|1/sess|no|
|132|`render_timeout`|`polls`[N], `elapsed_s`[B]|`Studio.jsx:287`|Is the client giving up before the server finishes?|★ Raise `POLL_MAX` or stream progress|P0|rare|no|
|133|`media_op_run`|`tool`[E], `status`[E], `wall_s`[B]|`render_jobs.py:403` `_run_op`|Which heavy ops run in-process?|★ Which `tools/*` earn their keep|P0|0–5/turn|no|
|134|`deliverable_write_warning`|—|`render_jobs.py:436`|Are tools scribbling into `renders/`?|★ Tighten tool output schemas|P1|rare|no|
|135|`render_concurrency_blocked`|`wait_ms`[B]|`project_lock` acquire in `render_jobs.py:482`|Do renders queue behind each other?|Queue UI|P2|rare|no|

### 3i. Export — the North Star

`export_completed` exists only in `tests/contracts/test_analytics.py:99` and
docs. No app code emits it. The North Star is unmeasurable today.

The publish commit (`render_jobs.py:566` → `lib.project.publish_final_render`) is
the right hook: it is the one place a deliverable becomes real.

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|136|`export_completed`|`ordinal`[N], `watermark_free`[F], `duration_s`[B], `resolution`[E], `fps`[B], `size_mb`[B], `vcodec`[E], `is_hdr`[F], `n_cuts`[B], `n_overlays`[B], `has_music`[F], `has_narration`[F], `has_sfx`[F], `n_transitions`[B], `n_keyframes`[B], `origin`[E] (`editor`\|`agent`), `agent_share`[B] (% of commits by the agent), `time_since_project_created_s`[B], `n_renders_before`[N], `agent_cost_usd`[N]|`render_jobs.py:566` when `published["published"]`|**THE North Star, plus everything about what users actually make**|★ Activation, TTV, cost per export, the shape of a real OpenNolan reel|P0|0–3/sess|no — every field is a shape/enum. Filename, project name and pixels are not sent.|
|137|`first_export`|all of #136 + `days_since_install`[B], `n_sessions_before`[N], `n_turns_before`[N]|derived from #136 `ordinal==1`|**Time to value**|★ Wall number #1|P0|1/inst|no|
|138|`export_opened_externally`|`app`[E] (`finder`\|`quicklook`\|other)|`shell.openPath` in `desktop/main.js`|Did the user actually take the file?|★ Strongest proxy for "shipped it"|P1|0–3/sess|no|
|139|`export_shape` (metric)|distributions of duration, cut count, overlay count|from #136|What does a real OpenNolan reel look like?|★ Tune defaults, styles and the agent's guide to the actual median|P0|—|no|
|140|`export_abandoned`|`n_renders`[N], `n_commits`[N], `last_stage`[E]|projects with renders but no publish, from #31|Who gets to the doorstep and stops?|★ The highest-leverage funnel step|P1|—|no|
|141|`export_reexported`|`gap_s`[B], `changed`[E] (`doc_delta` keys)|#136 with `ordinal > 1` in one project|Is exporting iterative?|Whether to keep export versions|P1|0–3/sess|no|
|142|~~`export_thumbnail`~~|—|—|—|**Do not collect.** A frame is the user's video. Ship #139's shape instead.|—|—|**SENS**|

### 3j. Errors / crashes / failures — full taxonomy

Five layers. Layers 1, 3 and 5 partly report today; 2 and 4 do not report at all.

```
L1 ELECTRON MAIN         desktop/main.js:65 reportDesktopError
   · uncaught main exception          -> desktop_error{source:'main'}
   · renderer process gone            -> desktop_error{source:'renderer'}
   · backend child exited             -> desktop_error{source:'backend-exit'}
   · BACKEND NEVER HEALTHY  :111      -> desktop_error{source:'fatal'} <-- worst
   · GAP: dev is suppressed at :67; flood cap 20 at :68

L2 PROVISIONING          lib/provision.py  — NO reporting at all
   · base python missing / venv build fail / uv fail
   · pip pack fail (5 packs, :74)     · node floor / npm ci fail
   · ffmpeg absent                    · manifest corrupt

L3 BACKEND               server/app.py:427 global handler (reports)
   · unhandled route exception        -> capture_exception  ■
   · capability discovery fail :927   -> capture_exception  ■
   · agent turn exception     :1164   -> capture_exception  ■
   · GAP: every HTTPException bypasses :427 —
       422 schema reject :791 · 404 project · 400 traversal :575
       422 bad mention :1099 · 503 no auth :1101 · 404 pack :1076
   · GAP: RendersDirEscapes           render_jobs.py:628

L4 AGENT                 — NO structured reporting
   · ResultMessage.is_error           agent_runner.py:1980
   · per-tool is_error                :791 / :810   <-- DROPPED
   · sandbox DENY                     :385 / :275
   · destructive-command block        :100
   · confirm timeout                  :867 confirm_timeout_s
   · answer timeout                   :868
   · budget ceiling                   :866
   · session death + resume           :1994
   · user interrupt                   :2009
   · off-by-one drain                 :1938
   · auth error mid-turn              auth.py:180

L5 FRONTEND              web/src/main.jsx (reports)
   · ErrorBoundary            :9      -> /api/telemetry/error  ■
   · window.onerror           :38     -> same                  ■
   · unhandledrejection       :42     -> same                  ■
   · GAP: handled-but-user-visible failures
       save 422 Studio.jsx:234 · render fail :285 · timeout :287
       upload fail :588 · load fail :43 loadErr
   · GAP: <video> error       StudioPreview.jsx:200

L6 SILENT (the worst class) — every reporter swallows its own failure
   analytics.py:176 capture      activity.py:194 record_tool_use
   recorder.js:132 push         desktop/main.js:108 report
   feedback.py:149 relay        analytics.py:117 _before_send
```

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|143|`error_reported`|`layer`[E] (L1–L5), `source`[E], `error_class`[E], `fatal`[F], `session_id`, `surface`[E], `n_prior_this_session`[N]|unify: `analytics.py:179`/`:198`, `desktop/main.js:65`|One crash inbox with a session join|★ Crash-free session rate (wall #4)|P0|0–5/sess|**SENS** — frames carry `/Users/<name>/…`. `_before_send` (`analytics.py:108`) already redacts; keep it and never bypass.|
|144|`http_error`|`route_template`[E], `status`[E], `detail_class`[E]|new middleware in `server/app.py` (HTTPException never reaches `:427`)|**The whole 4xx surface is invisible today**|★ Every 422/404 is a UX bug we cannot see|P0|0–20/sess|no — route TEMPLATE (`/api/projects/{id}/render`), never the concrete path|
|145|`user_visible_failure`|`where`[E] (`save`\|`render`\|`upload`\|`load`\|`preview`), `error_class`[E], `recovered`[F]|`Studio.jsx:234`,`:285`,`:287`,`:588`,`:42`|How often does a user see a red toast?|★ Toasts-per-session is the honest quality metric|P0|0–10/sess|no|
|146|`provisioning_error`|`target`[E], `step`[E], `error_class`[E], `stderr_class`[E]|`lib/provision.py:444`/`:655`|First-run failure taxonomy|★ Ship-blocker gate|P0|rare|**SENS** — classify stderr, do not ship it|
|147|`agent_tool_error`|`tool`[E], `error_class`[E] (`not_found`\|`permission`\|`timeout`\|`nonzero_exit`\|`schema`\|`unknown`)|`agent_runner.py:791`/`:810`, classified|**Which tool fails and why** — the biggest blind spot|★ Fix/replace/delete that tool|P0|0–20/turn|no — class only; the result body can hold content|
|148|`sandbox_denied`|`tool`[E], `reason_class`[E], `root_family`[E] (`home`\|`system`\|`other_user`\|`tmp`)|`agent_runner.py:385`, `:275`|Is the sandbox a false-positive machine? (It was once — see the `shlex` fix.)|★ Regression alarm|P0|rare|no|
|149|`swallowed_error`|`site`[E] (`analytics_capture`\|`activity_write`\|`recorder_push`\|`desktop_report`\|`feedback_relay`\|`before_send`)|a shared counter incremented in each `except` at the L6 sites|**Is the telemetry itself broken?**|★ Without this, every other number can silently be a lie|P0|rare|no|
|150|`error_loop_detected`|`error_class`[E], `count`[N], `window_s`[B]|`errorsSent` cap logic, `desktop/main.js:68`|Is a user stuck in a crash loop?|★ Alert; hotfix|P0|rare|no|
|151|`crash_free_session_rate` (metric)|`1 − sessions_with_error / sessions`|from #14 + #143|Quality in one number|★ Wall number #4|P0|—|no|
|152|`recovery_rate` (metric)|`sessions_with_error_that_still_exported / sessions_with_error`|from #143 + #136|Is a crash fatal to the outcome?|★ Which errors to prioritize: the ones that kill the export|P1|—|no|

### 3k. Feedback & requests

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|153|`feedback_submitted`|`kind`[E] (`bug`\|`idea`\|`other`), `message_len`[B], `has_email`[F], `has_diagnostics`[F], `has_debug_session`[F], `has_attachment`[F], `surface`[E], `n_errors_this_session`[N], `n_exports`[N]|`server/feedback.py:177` (extend with the last three)|Who complains, and had they succeeded first?|★ Weight bug reports by whether the user was winning|P0|rare|no — the BODY stays local + email only; `_scrub` turns `message` into `message_len` (`analytics.py:155`)|
|154|`feedback_modal_opened`|`from`[E], `submitted`[F], `abandoned_after_s`[B]|`web/src/App.jsx:205` → `:275`|Do people open it and give up?|★ Shorten the form|P1|rare|no|
|155|`feedback_relay_result`|`ok`[F], `path`[E] (`relay`\|`resend`\|`none`)|`server/feedback.py:187`|Is feedback reaching us at all?|★ Alarm — silent loss of the only qualitative channel|P0|rare|no|
|156|`micro_survey_shown`|`trigger`[E] (`post_first_export`\|`post_3rd_render_fail`\|`pre_abandon`), `question_id`[E]|new, in the editor|Contextual asks beat a generic form|★ §5 channel|P2|rare|no|
|157|`micro_survey_answered`|`question_id`[E], `choice`[E] (closed set)|same|Same|★ Same|P2|rare|no — closed choices only; free text stays local|
|158|`nps_or_pmf`|`score`[E]|after 3rd export|Sean Ellis PMF check|★ Gate on beta expansion|P2|rare|no|

### 3l. Retention / habit / lifecycle

| # | Event / metric | Properties | Hook point | Question | Decision | Pri | Vol | SENS |
|---|---|---|---|---|---|---|---|---|
|159|`session_ordinal`|`n`[N], `days_since_install`[B], `days_since_prior`[B]|`server/settings.py` counter, emitted with #13|Usage cadence|★ Retention curves|P0|1/sess|no|
|160|`week_active`|`week_n`[N], `had_turn`[F], `had_commit`[F], `had_export`[F]|nightly rollup|W1/W4 retention (§4 argues why weekly, not daily)|★ Wall-adjacent|P1|1/inst/wk|no|
|161|`export_cadence` (metric)|median days between consecutive #136 per install|from #136|Are we in the user's weekly workflow?|★ The real habit test|P1|—|no|
|162|`churned`|`last_seen_days`[B], `n_exports`[N], `furthest_stage`[E], `last_error_class`[E]|nightly sweep|**Did they leave broken or just finished?**|★ Distinguishes a bug from a satisfied one-off|P1|1/inst|no|

---

## 4. Metric definitions

Every formula names its source events by catalog number.

**Activation rate**
```
    installs with >=1 #136 (watermark_free=true) within 7d of #1
AR = ----------------------------------------------------------
                    installs with #1
```
Good ≥ 40% for a tool this deliberate. < 20% means the funnel, not the features,
is the problem. **Triggers:** below 20% → freeze feature work, fix the funnel step
with the largest single drop (§8 screen 1).

**Time-to-value (TTV)** — `median( ts(#137) − ts(#1) )` per install. Report p50 and
p90. Good: p50 < 1 day (one sitting). Bad: p90 > 7 days = the first project
never finishes. **Triggers:** p90 > 7d → ship a template/demo project.

**Render success rate**
```
                  count(#123 status='done')
RSR = ----------------------------------------------  ... superseded is EXCLUDED
       count(#123 status='done') + count(status='failed')
```
`superseded` (`render_jobs.py:168`) is a user re-render, not a failure —
including it would understate reliability. Track it separately as #125. Good ≥
97%. **Triggers:** < 95% → the top `error_class` from #127 becomes the week's work.

**Agent turn success rate** — two numbers, and the second is the real one:
```
technical:  1 − count(#48 is_error) / count(#47)
useful:     count(#48 where doc_changed OR artifacts_written>0 OR render_started)
            ---------------------------------------------------------------
                                count(#47)
```
Report both. Technical success with zero useful output is the failure mode BYOK
makes expensive — the user paid for a turn that did nothing. Good: useful ≥ 70%.
**Triggers:** useful < 50% → the agent guide, not the model, is the problem.

**Agent tool success rate, by tool**
```
                          errors(tool)
TSR(tool) = 1 −  ------------------------------      from #57/#58/#147
                          calls(tool)
```
Rank ascending, weighted by call volume. Good ≥ 95% per tool. **Triggers:** any
tool with ≥ 20 calls and TSR < 80% gets fixed, re-documented, or deleted. Today
this is uncomputable — the fix is threading `is_error` through
`server/agent_runner.py:1973`.

**Cost per successful export**
```
        sum(#48.cost_usd) over the project
CPE = ------------------------------------
        count(#136) in the project
```
Report the per-project distribution, not the global mean — one runaway project
would poison a mean. Good: p50 < $2. **Triggers:** p90 > $10 → the site must say
so, and the agent needs turn-budget guardrails.

**Feature adoption rate**
```
        installs that fired #80/#86 for feature F at least once
AR(F) = ------------------------------------------------------
              installs with >=1 #82 (editor session)
```
Denominator is *editor* sessions, not installs — an agent-only user never had the
chance. **Triggers:** see §6.

**Feature retention (the twice test)**
```
        installs with F used in >= 2 DISTINCT sessions
FR(F) = ---------------------------------------------      from #84
        installs with F used in >= 1 session
```
The sharpest signal in this document. A feature with high adoption and FR < 0.25
was clicked once out of curiosity. Good ≥ 0.5. **Triggers:** FR < 0.25 with
adoption < 20% → deletion review.

**Discovery rate**
```
                installs that used F
DR(F) = -------------------------------------
        installs for whom F was ELIGIBLE (visible + applicable)
```
Eligibility is the hard part and it is derivable from the code: crop is eligible
only when a video cut is selected; `audioMix` only for a `video_overlay`
(`propertySchema.js:97`); ⇅ Arrange only with ≥ 2 overlays
(`StudioTimeline.jsx:267`). #82 carries `features_eligible[]` computed from the
same predicates the UI uses to render. A feature with AR 5% but DR 60% is
*discovered and rejected* — delete it. AR 5% with DR 5% is *hidden* — fix the UI.
This distinction is the whole reason to spend a day on `features_eligible`.

**Undo rate per feature**
```
            count(#81 where undone_op = F)
UR(F) = ----------------------------------------
            count(#80 where op = F)
```
Requires the history entry at `Studio.jsx:168` to store the `op` label alongside
the doc snapshot. Good < 10%. **Triggers:** UR > 30% → the control's units,
direction or default are wrong (prime suspects: `ScrubField` drag sensitivity,
`StudioInspector.jsx:31`; auto-track-stacking, #91).

**Edit-to-export ratio** — `count(#80) / count(#136)`, per project. Tells you what
the product is. ≈ 5 → a generator with a review step. ≈ 500 → a real NLE. The
answer decides whether editor polish or agent quality is the priority, and we
have never measured it.

**Session depth** — per #82: `commits`, distinct `features_used`, `duration_s`.
Report the joint distribution. A long session with few commits means the user is
stuck, not engaged — cross-check against #145 and #116.

**Retention — and what it means here.** D1/D7/D30 is a wrong-unit metric for this
product. A founder ships 1–4 reels a month, so a healthy user is *absent* most
days and D1 will be structurally low. Ranked by usefulness:

1. **Second-project rate** (#26) — installs creating project #2 within 14 days of
   their first export. This is the habit signal. Good ≥ 50%.
2. **W1/W4 project-active retention** (#160) — % of installs with ≥ 1 agent turn or
   ≥ 1 commit in week N after first export. Weekly buckets match the cadence.
3. **Export cadence** (#161) — median days between consecutive exports. > 30 days
   means we are not in the workflow, whatever D7 says.
4. D1/D7/D30 — compute them because they are free and comparable to outside
   benchmarks, but do not steer on them. Report them with the caveau attached.

**Crash-free session rate** — `1 − sessions_with_any #143 / sessions (#14)`. Count a
session as crashed if ANY layer reported, including L1 Electron, joined by
`session_id`. Good ≥ 99%. **Triggers:** < 97% → release blocker.

**Time-in-app vs time-rendering**
```
              sum(#123.wall_s)
TRR = ---------------------------------
              sum(#14.duration_s)
```
The direct test of `RULES.md:66` ("edit live, render rarely"). Good < 10%.
**Triggers:** > 25% → the live preview is not covering enough cases; find which
edits force a render (#99 crop, comp clips) and make them previewable.

---

## 5. How to know what to BUILD

Ranked by signal strength. "Signal strength" here means: how close the observation
is to a user's actual blocked intent, and how little interpretation it needs.

```
STRENGTH   CHANNEL                                       VOLUME TO ACT
=========  ============================================  =============
STRONGEST  1. capability_missing / api_key_missing        3 installs
           (#71,#73 — the AGENT declared the gap)
           2. agent_tool_not_found (#67)                  2 occurrences
           3. agent_routed_around_us (#75,#76)            3 occurrences
           4. agent_ffmpeg_freehand (#77)                 5 occurrences
           5. explicit feedback kind='idea' (#153)        2 requests
           6. repeated manual workaround n-grams          5 installs
           7. abandoned funnel step (#31,#140)            10 installs
           8. mention_search_miss (#78)                   20 misses
           9. micro-survey answers (#157)                 15 answers
WEAKEST   10. feature-request inference from usage        never act alone
```

**1–2. The agent as a requirements engine.** `request_api_key`
(`server/agent_runner.py:1040`) and `request_capability` (`:1084`) already exist
and already make the agent name a missing capability in a closed vocabulary. That
is a structured feature request, written by a system that just tried and failed to
do the user's work. Nothing else in this document comes close. It costs one
`capture()` in each of `_request_api_key` (`:1370`) and `_request_capability`
(`:1438`). Pair each with its answer (#72, #74) — a pack the user *refuses* to
install 3 times running is a bundle-or-cut decision, not a build one.

**3–4. The agent routing around us.** `bash_runs_heavy_media_op`
(`server/agent_runner.py:128`) fires when the agent tries to hand-run
`silence_cutter`/`motion_ops`/`auto_reframe`/`object_cutout` through Bash instead
of `run_media_op`. Today the code *steers* it silently. Each fire means our tool's
interface or docs failed — the agent knew what it wanted and could not find the
door. `bash_uses_videocompose_render` (`:108`) is the same for render.

`agent_ffmpeg_freehand` (#77) generalizes it and is the most valuable new
classifier in this document: when the agent writes raw ffmpeg, extract the FILTER
FAMILY (`overlay`, `scale`, `concat`, `atempo`, `zscale`, `drawtext`, `crop`,
`xfade`) and check whether a registry tool covers it. `activity.py:108`
`_segment_tool` is where this classification already half-lives. A filter family
the agent writes by hand five times is a tool we should have shipped, named
precisely.

**5. Explicit feedback.** `kind='idea'` (`server/feedback.py:153`). Highest
intent, lowest volume, and at ~0 users the only channel that produces sentences.
Do not over-weight it: it comes from the users who bother, which is not the users.

**6. Repeated manual workaround sequences.** From #80's new `op` label, mine
n-grams inside a session. Concretely detectable and each maps to a feature:

| Sequence seen ≥ 3× in a session | The missing feature |
|---|---|
| `split` → `delete` → `split` → `delete` | silence/dead-air removal on the timeline |
| `overlay.add` → `position.x/y` → `scale` repeated | an alignment/snap system, or presets |
| `music.trim` → `music.trim` → `music.trim` | beat snapping |
| `canvas` → `render` → `canvas` → `render` | live canvas preview without a render |
| `keyframe.upsert` ×6 on one overlay | more motion presets (`model.js:165`) |
| `undo` ×4 consecutively | the previous feature is wrong (see §4 undo rate) |

The n-gram miner is a nightly SQL job over #80, not app code. It is the only
channel that finds a feature nobody thought to ask for.

**7. Abandoned funnels.** #31 `project_stalled` with `furthest_stage`, and #140
`export_abandoned`. A stage where 30% of projects die is a build target even with
no complaint attached, because the users who died there never wrote to us.

**8. `@`-mention search misses** (#78): zero candidates for a non-trivial query
means the user believed an asset was reachable and it wasn't. Count and query
length only — the query string is content.

**9. Contextual micro-surveys.** One question, closed choices, fired at a moment
of proven context: after the first export ("what were you about to do that you
couldn't?"), after the third render failure, on a stall. Closed choices keep it
out of the SENS column and make it countable. Cap at one per install per week.

**Volume floor before acting.** At 50 installs, nothing crosses statistical
significance, so the rule is not statistical — it is *distinctness*: act when the
same named gap appears from ≥ 3 distinct `device_id`s, or twice from one install
plus once in feedback. Below that, log it on a "watch list" screen (§8) and do
not build. This is the honest answer for a pre-beta product: you are doing case
research, not A/B testing (see §12 on why experiments are off the table).

---

## 6. How to know what to REMOVE

The neglected half, and the one the code makes unusually easy: two closed
vocabularies already enumerate the whole editor surface —
`PROPERTY_SCHEMA` (`web/src/studio/propertySchema.js:60`, every inspector field)
and the drag `mode` set (`web/src/studio/StudioTimeline.jsx:138-202`). Emit
`field_key` (#86) and `mode` (#88) and any member of those sets that never appears
in 60 days is *provably* dead UI, not "probably."

**The deletion test.** A feature is a delete candidate when ALL of these hold over
a 60-day window with ≥ 30 active installs:

```
 (1) ADOPTION      AR(F) < 5%                                  #80/#86
 (2) TWICE-TEST    FR(F) < 20%                                  #84
 (3) NOT HIDDEN    DR(F) > 30%   (discovered and rejected,      #82
                   not merely invisible — if DR < 10% the
                   bug is discoverability; FIX, don't delete)
 (4) NOT INTERNAL  usage from >=2 non-`internal` device_ids     analytics.py:76
                   is required to KEEP it; internal-only = cut
 (5) NO DEPENDENTS not required by a shipped path (agent writes
                   it, the schema requires it, or a pipeline_def
                   reads it) — a static check, not a metric
 (6) SILENT        zero feedback mentions                       #153
```

Then, before deleting: **hide it for 14 days behind a flag** and watch #145 and
#153. Nobody noticing is the confirmation. This is deliberately harder than the
build test — deletion is irreversible for the user who did rely on it.

**High-undo features are a separate verdict.** UR(F) > 30% with healthy adoption
means *fix*, not delete: people want it and the control is wrong. Suspects:
`ScrubField` drag sensitivity (`StudioInspector.jsx:80`, 1px ≈ one step),
auto-track-stacking surprising the user (#91), `resolveOverlayOverlap` floating an
overlay the user placed deliberately (#92).

**Concrete candidates this system would rule on first,** with my prior in
parentheses (stated so a wrong prior is falsifiable, not hidden):

| Feature | Anchor | Expected verdict |
|---|---|---|
| Manual Save button | `StudioToolbar.jsx:91` | DELETE — autosave at 700 ms (`Studio.jsx:247`) makes it vestigial; keep ⌘S |
| ⇅ Arrange | `Studio.jsx:513` | DELETE — auto-stacking on add (#91) already covers it |
| Canvas picker | `Studio.jsx:596` | DELETE for beta — `RULES.md:3` is vertical-only; keep the doc field |
| Manual keyframe editor | `Studio.jsx:607` | KEEP but shrink to presets if #98 shows presets ≫ manual |
| `rotation` keyframe dim | `interp.js:KEYFRAME_DIMS` | DELETE — not in `KF_DIMS_IMAGE`/`KF_DIMS_TEXT` (`model.js:46-47`), so likely unreachable already |
| `hdr_policy='preserve'` | `video_compose.py:1312` | DELETE if #129 shows `source_hdr` false for all non-internal installs |
| Half the `tools/video/*` set (~40 modules) | #61 | DELETE the ones the agent never calls in 60 days |
| `pipeline_defs` / `styles` never chosen | #24, #60 | DELETE |
| Debug recorder in the shipped toolbar | `StudioToolbar.jsx:61` | MOVE behind a flag if #114 shows only internal use |
| `narration` inspector panel | `propertySchema.js:160` | DELETE if #102 shows zero narration ops |
| The 5 capability packs | `provision.py:74` | Per-pack call: install rate (#4/#5) × refusal rate (#72) |

**Code paths never exercised.** Distinct from unused UI, and cheaper to find:
`agent_runner.decide_tool`'s branches (`:385`–`:430`), `render_jobs`' three origins
(`:58`/`:71`/`:94`), and `_normalize_output_path`'s four input shapes (`:632-651`)
are all enumerable. Emit the branch taken as an enum property (#62, #122) and a
branch with zero hits in 60 days is dead code with a defensible delete — the same
technique as the UI vocabularies, applied to control flow.

---

## 7. Feature-level truth table

Adoption = "used at least once." Success = "did the thing the user wanted."
Abandonment = "started and did not finish, or immediately undone."

**Agent**

| Feature | Anchor | Adoption | Success | Abandonment |
|---|---|---|---|---|
| Chat turn | `agent_runner.py:1924` | #47 per install | #48 `doc_changed`\|`render_started` | #51 interrupt; #48 `is_error` |
| Model picker | `:2049` | #52 fired | turn success after switch ≥ before | switched back within 2 turns |
| Stop / interrupt | `:2009` | #51 | n/a — always a failure upstream | #51 rate itself is the metric |
| Confirm prompt | `:1298` | #63 | #64 `approved` | #64 `timed_out` |
| `ask_user` | `:920` | #65 | #65 `answered` | #65 timeout |
| `request_api_key` | `:1040` | #73 | #74 `provided` | #74 refused |
| `request_capability` | `:1084` | #71 | #72 `installed` | #72 refused |
| `render` tool | `:953` | #122 `origin='agent'` | #123 `done` | #125 superseded; #127 |
| `run_media_op` | `:1128` | #133 | #133 `done` | #133 `failed` |
| `store_asset` | `:1000` | #69 | #69 `ok` | asset never used in a doc (#38) |
| `@`-mention | `ChatPanel.jsx:103` | #79 | mention resolved + turn useful | #78 miss; mention deleted pre-send |
| Threads | `app.py:1253` | #28 | #29 resumes prior context | p95 threads == 1 → delete |
| Activity tab | `app.py:911` | poll count | user opens it after a failure | never opened |
| Sandbox | `:370` | always on | #148 zero false positives | #148 DENY on an in-bounds path |
| Skills (`skills/`) | `activity.py:263` | #59 per skill | turn useful when skill ran | skill never read |
| Pipelines / styles | `app.py:544` | #24 per value | export within 7d of creation | stalls at `furthest_stage` (#31) |

**Editor — timeline & transport**

| Feature | Anchor | Adoption | Success | Abandonment |
|---|---|---|---|---|
| Scrub (ruler) | `StudioTimeline.jsx:293` | #88 `mode='scrub'` | #115 p90 seek < 150 ms | #116 seek never completed |
| Play / pause | `Studio.jsx:106` | #110 `space` or button | watch > 3 s | pause within 1 s (a stall) |
| Split | `Studio.jsx:351` | #90 `split` | not undone in 5 s | #81 `undone_op='split'` |
| Delete | `:376` | #90 `delete` | same | undo |
| Duplicate | `:392` | #90 `duplicate` | same | undo |
| Reorder (drag) | `StudioTimeline.jsx:218` | #89 | new order persists to save | #88 `cancelled`; undo |
| Trim in/out | `:369`/`:373` | #88 `trim-*` | value survives to render | undo; trim-then-untrim |
| Overlay move | `interp.js:403` | #88 `ov-move` | persists | undo; #92 auto-floats it |
| Overlay trim | `interp.js:439` | #88 `ov-trim-*` | persists | undo |
| Vertical track drag | `interp.js:403` (`track`) | #88 with `track` delta | lands where dragged | #92 overrode the drop |
| Auto-stack on add | `interp.js:512` | #91 | `bumped` and NOT undone | `bumped` then undone |
| ⇅ Arrange | `Studio.jsx:513` | #93 | tracks reduced, not undone | `undone_within_5s` |
| Zoom slider | `StudioTimeline.jsx:279` | #95 | settles and edits follow | thrashes without an edit |
| Track hide (eye) | `Studio.jsx:59` | #94 | re-shown before render | left hidden at export |
| Asset DnD → timeline | `StudioAssets.jsx:61` → `Timeline:243` | #37 `timeline_drop` | clip appears and survives | drop rejected; undo |
| Timeline background deselect | `Timeline:288` | #85 `source='timeline'` | Assets tab appears | — |

**Editor — inspector (from `propertySchema.js`)**

| Feature | Anchor | Adoption | Success | Abandonment |
|---|---|---|---|---|
| Source / asset select | `:63`,`:79`,`:94`,`:101`,`:130`,`:150` | #86 `field_key='src'\|'asset'` | render succeeds with it | reverted |
| Trim in/out (typed) | `:67-68` | #86 `in`/`out` | persists | undo |
| Speed + presets | `:69-70` | #101 | persists | reverted to 1× |
| Clip transform (scale/x/y) | `:73` → `Inspector:238` | #86 `clipxf` | persists | undo; high #87 frames per unit change |
| Crop | `:74` → `Inspector:299` | #99 | render succeeds (no ffmpeg 234, `RULES.md:183`) | undo; render fail `crop_oob` |
| Transitions in/out/dur | `:49-55` | #100 per kind | appears in export (#136 `n_transitions`) | set then cleared |
| Note (`reason`) | `:58` | #86 `note` | — | expect ~0 → delete |
| Track (z) numeric | `:37` | #86 `track` | z-order visibly changes | user drags instead → delete the field |
| Timing start/end | `:31-32` | #86 `start`/`end` | persists | undo |
| Opacity | `:33` | #86 `opacity` | in export | reset to 1 |
| Position x/y/w/h | `:42-45` | #86 `x`/`y`/`w`/`h` | persists | user drags on canvas instead |
| Audio mix (overlay) | `:97` → `Inspector:323` | #103 | audible in export | toggled off again |
| Keyframes (motion) | `:98`,`:104`,`:125` | #98 | motion in export (#136 `n_keyframes`) | all kf removed; only presets used |
| Text content | `:111` | #97 `then_edited` | in export | overlay deleted |
| Font size / color | `:112-113` | #86 `fs`/`color` | in export | reverted |
| Text position (anchor) | `:114` | #86 `pos` | persists | dragged on canvas instead |
| Background box opacity/pad | `:120-121` | #86 `bo`/`bp` | in export | reverted |
| Music asset/timing/vol/fades | `:129-148` | #102 `music` | in export `has_music` | region removed |
| SFX asset/start/vol | `:149-159` | #102 `sfx` | in export `has_sfx` | removed |
| Narration | `:160-169` | #102 `narration` | in export `has_narration` | expect ~0 → delete panel |
| `ScrubField` drag | `Inspector:31`,`:80` | #87 | one drag = one undo step | click-to-type dominates → simplify |

**Editor — chrome, canvas, lifecycle**

| Feature | Anchor | Adoption | Success | Abandonment |
|---|---|---|---|---|
| Undo / redo | `Studio.jsx:196`/`:205` | #81 | depth > 1 used | undo-storms (≥ 4 in a row) |
| Save (manual) | `:230` | #105 | 200, not 422 | #107 422; expect ~0 adoption |
| Autosave | `:247` | #106 | zero 422s, not blocked | #108 blocked by agent |
| Render button | `:264` | #122 `origin='editor'` | #123 `done` | #132 timeout; #125 spam |
| Source/Render toggle | `:110` | #112 | stays on `source` | flips to `render` constantly → live preview is failing `RULES.md:66` |
| +Text | `:485` | #97 | text in export | overlay deleted |
| Canvas picker | `:596` | #96 | export at that size | reverted to 9:16 |
| WYSIWYG canvas drag | `StudioPreview` overlay drag | #85 `source='canvas'` | position persists | inspector used instead |
| Panel resize / collapse | `:69-75` | #111 | layout persists across sessions | reset to default |
| Assets folder browse | `app.py:677` | #40 | ends in an add (#41 `add`) | #41 `close` |
| Asset modal | `AssetModal.jsx` | #41 | `add` | `close` |
| Upload (drop / picker) | `StudioAssets.jsx:92`,`:61` | #37 | 201 and then used in a doc | #42 failed; never used |
| Keyboard shortcuts | `:634` | #110 per key | used ≥ 2 sessions | zero → document or delete |
| Debug recorder | `:118` | #114 | `report_sent` | recorded, never sent |
| Feedback modal | `App.jsx:205` | #154 | #153 submitted | #154 abandoned |
| Capabilities modal | `App.jsx:202` | #23 | a pack installs (#5) | opened, nothing installed |
| BYOK env panel | `App.jsx:208` | #22 | key saved and used by a tool | opened, nothing saved |
| Agent-adopt reconcile | `Studio.jsx:305` | #109 | kept (not undone in 30 s) | undone → the agent's edit was wrong |

---

## 8. The developer's operating dashboard

Five screens. Every screen filters `internal != true` by default — the property
already exists on every event (`server/analytics.py:76`). One toggle flips to
internal-only, which is how the "only Het uses this" test in §6 gets answered.

```
SCREEN 1 — NORTH STAR FUNNEL                          read: daily
 app_first_run -> auth_connected -> project_created -> agent_turn
   -> render_started -> render done -> EXPORT (watermark-free)
 · conversion + median dwell at each step, 7d & 28d cohorts
 · biggest single drop highlighted
 DECISION: which ONE funnel step gets this week's work.

SCREEN 2 — AGENT HEALTH                               read: daily
 · turns/day · useful-turn rate · median cost/turn · cost/export
 · TSR table by tool, ascending, weighted by calls   <-- the money table
 · interrupt rate + last_tool at interrupt
 · unmet-need feed: capability_missing, api_key_missing,
   tool_not_found, routed_around_us, ffmpeg_freehand (§5 rows 1-4)
 DECISION: fix/replace/delete a tool; bundle a pack; build a tool.

SCREEN 3 — RENDER & PREVIEW PERFORMANCE               read: weekly
 · render success rate (superseded excluded) · failure reasons ranked
 · cache hit rate distribution · stage-timing waterfall
 · preview p50/p90 seek · incomplete-seek rate · stalls/session
 · time-in-app vs time-rendering
 DECISION: what to optimize; whether "edit live" is actually true.

SCREEN 4 — FEATURE LEDGER                             read: fortnightly
 One row per feature from §7. Columns:
   adoption · twice-test · discovery · undo rate · internal-only?
   · last used (days) · VERDICT chip {KEEP · FIX · HIDE · DELETE}
 Sorted by (low adoption, high undo) — the delete queue on top.
 DECISION: the deletion review; which control to fix.

SCREEN 5 — QUALITY & VOICE                            read: daily
 · crash-free session rate, by layer (L1-L5)
 · error_class leaderboard, new-this-week flagged
 · http_error 4xx leaderboard by route template
 · user-visible failures per session (red toasts)
 · swallowed_error counter          <-- is telemetry itself alive?
 · feedback stream, weighted by whether the user had exported
 DECISION: the release gate.
```

**Automatic alerts** (PostHog insight alerts, all on non-internal traffic):

| Alert | Condition | Why it pages |
|---|---|---|
| Install broken | any `backend_never_healthy` (#8) | the worst first-run failure |
| Provisioning regression | `provision_finished ok=false` > 10% over 24 h | first run is the whole funnel |
| North Star flat | 0 `export_completed` in 48 h with ≥ 1 render started | the product stopped working |
| Render regression | RSR < 95% over 24 h | reliability |
| New crash class | an `error_class` unseen in 14 days | a regression just shipped |
| Crash loop | #150 fires | one user is stuck |
| Telemetry dead | `swallowed_error` > 0, or 0 `session_start` in 24 h with prior traffic | every other number is suspect |
| Agent cost spike | p90 `cost_usd`/turn up > 2× week over week | it is the user's money |
| Feedback silence | `feedback_relay_result ok=false` | the qualitative channel is broken |

**Daily digest** (one message): exports yesterday + 7-day trend · new installs ·
activation of the last cohort · agent useful-turn rate and median cost · render
success rate · crash-free rate · new `error_class`es · every unmet-need row · every
feedback body.

**Weekly digest** adds: feature ledger diff (what moved into DELETE) · funnel
conversion week over week · retention (second-project rate, W1/W4) · export-shape
distribution · the workaround n-gram top 5 · the watch list of gaps not yet at the
3-install threshold.

---

## 9. Volume, cost, sampling

**Baseline assumptions,** stated so they can be corrected: beta = 50 installs,
12 sessions/install/month → **600 sessions/month**. Post-beta = 2,000 installs,
same cadence → **24,000 sessions/month**. A session = one app launch with real
work: ~3 agent turns, ~150 editor commits, ~2 renders, 0.5 exports.

**Per-session event counts at three fidelities**

```
FULL FIDELITY (ship every interaction, incl. per-frame)
  agent tool calls        3 turns x 60          =   180
  edit.commit                                   =   150
  edit.live (per-frame drag, 80 drags x 1.5s
             x 60fps)                           = 7,200   <-- absurd
  ui.select                                     =   200
  preview.video.* (8 types x ~150 seeks, part.) = 1,000
  preview.seekReq                               =   150
  everything else                               =    50
                                          TOTAL ≈ 8,930 / session

PER-INTERACTION (drop per-frame, keep one event per discrete action)
  same minus edit.live                          ≈ 1,730 / session

ROLLED UP (the proposed design)
  Tier A funnel/outcome events                  ≈    20
  Tier B session rollups (#58 x3, #82, #115,
         #106 summary, #123 x2)                 ≈     7
                                          TOTAL ≈    27 / session
```

**Monthly arithmetic against a 1,000,000-event free tier**

| Fidelity | Beta (600 sess/mo) | vs free tier | Post-beta (24,000 sess/mo) | vs free tier |
|---|---|---|---|---|
| Rolled (27/sess) | 16,200 | 1.6% | 648,000 | 65% — still free |
| Per-interaction (1,730/sess) | 1,038,000 | **104% — breaks at 50 users** | 41,520,000 | 41× over |
| Full fidelity (8,930/sess) | 5,358,000 | 5.4× over | 214,320,000 | 214× over |

At PostHog's first paid band (~$0.00005/event above the free 1M — verify at
https://posthog.com/pricing, and note the blended rate *falls* at volume so these
are upper bounds):

```
Beta, per-interaction:   (1.04M − 1M)  x 0.00005 ≈ $2 / mo      (fine)
Beta, full fidelity:     (5.36M − 1M)  x 0.00005 ≈ $218 / mo
Post-beta, per-interaction: (41.5M − 1M) x 0.00005 ≈ $2,026 / mo
Post-beta, full fidelity:  (214M − 1M)  x 0.00005 ≈ $10,666 / mo
```

**The conclusion is the design.** The rolled-up catalog stays inside the free tier
through 2,000 installs. Per-interaction shipping costs ~$2,000/month at that scale
for data nobody reads — the value of `ui.select` #147 of a session is zero, while
"this session had 200 selections, 80% from the timeline" is the entire insight.
The lever is not sampling; it is **counter rollup at the session boundary**, which
is lossless for every question in §4 and §7. Properties are effectively free
(PostHog bills events, not properties), so #82 carrying 80 counters costs one
event.

**PostHog Error Tracking** is a separate allowance (~100k exceptions/month free —
verify). At 99% crash-free × 24,000 sessions you generate ~240 exception events
plus retries, well inside it. The flood cap already exists at
`desktop/main.js:68` (20 per process); mirror it server-side per `error_class` per
hour so one crash loop cannot burn the allowance.

**Rollup boundaries.** Session end (`pagehide`, mirroring `recorder.js:198`), turn
end (`agent_runner.py:1992`), and render terminal (`render_jobs.py:309`). Each is
an existing code path, so rollup adds no new lifecycle machinery. Risk: a hard
crash loses the in-flight rollup. Mitigation: `navigator.sendBeacon` (already used
by `recorder.js:200` with `useBeacon`) plus a 60-second periodic partial flush for
long sessions — and accept that a hard-crashed session's rollup is lost, because
#143 fires immediately and independently.

**Never sampled, at any scale:**
`app_first_run` · `auth_connected` · `export_completed` · `first_export` ·
everything in §3j · `feedback_submitted` · every unmet-need row (#67, #71, #73,
#75–77) · `provision_finished`.

Reason: these are the numerators AND denominators of low-N rate metrics. At 50
installs a 10% sample of activation gives you 5 observations — the sampling error
exceeds the effect you are trying to see. Sampling a crash also destroys
crash-free-session rate, which is a *rate over sessions*, not a count.

**Safe to sample if ever needed** (post-beta only): #85 selection changes, #121
frame latency, #40 browse navigation, #110 shortcut usage — all already rolled up,
so sampling would be a second-order saving on an already-cheap family. Do not
bother until you are over 500k events/month.

**Local-only, never shipped:** the recorder NDJSON
(`.agents/tools/logs/ui-sessions/`, capped at `MAX_SESSION_EVENTS`,
`recorder.js:122`) and `.mc/activity.jsonl` full `target` fields. Both reach the
developer only when a user *chooses* to send a debug report
(`server/feedback.py:165`). That is the correct trust boundary and it already
exists.

---

## 10. Implementation architecture

Fewest moving parts, reusing what is there. Five hook points cover roughly 70% of
the catalog.

```
                        ONE TAXONOMY (the anti-drift spine)
                schemas/analytics_events.json   {name: {props, pri}}
                     ▲                                  ▲
      server/events.py (constants)      web/src/analytics/events.js
                     │                                  │
   ┌─────────────────┴──────────┐        ┌──────────────┴───────────┐
   │  PYTHON: analytics.capture │        │  JS: track(name, props)  │
   │  server/analytics.py:166   │        │  web/src/analytics/      │
   │  (unchanged — already      │        │    track.js  (NEW, ~60   │
   │   scrubs, redacts, gates)  │        │    lines: batch + POST)  │
   └─────────────┬──────────────┘        └──────────────┬───────────┘
                 │                                      │
                 │                    POST /api/telemetry/event (NEW)
                 │                    — mirrors /api/telemetry/error
                 │                      (server/app.py:1016) exactly:
                 │                      always 200, no-op when opted out
                 │                                      │
                 └──────────────────┬───────────────────┘
                                    ▼
                            PostHog (one project)

   FIVE HOOK POINTS (server side)
   1. render_jobs._set            server/render_jobs.py:309
        -> #122 #123 #125 #126 #127 #128 #129 #130 #136 (export!)
        every status transition for all 3 origins flows through here
   2. run_turn  finally           server/agent_runner.py:1992
        -> #47 #48 #50 #55 #58
   3. record_tool_use             server/activity.py:170
        -> #57 #59 #60 #61 #147   (needs is_error threaded from :791)
   4. decide_tool                 server/agent_runner.py:370
        -> #62 #63 #67 #75 #76 #148
   5. MCP tool bodies             agent_runner.py:1370 / :1438 / :1000
        -> #69 #71 #72 #73 #74

   FRONTEND: ONE bridge, not 22 new call sites
   web/src/debug/recorder.js:136  export function event(type, data)
        ── add: if (ANALYTICS_MAP[type]) track(mapped, project(data))
   The 22 existing dbg.event sites become analytics sources for free.
```

**The `dbg.event` bridge is the key move.** `recorder.js:136` is already called
from every interesting editor moment (20 sites in `Studio.jsx`, 2 in
`StudioPreview.jsx`) and is already a no-op when recording is off. Adding a
lookup table there — `'edit.commit' → accumulate into the session rollup`,
`'ui.render' → track` — means the editor half of the catalog costs one file plus
a table, not 40 new call sites. `push` (`recorder.js:113`) stays gated on
`state.on` for the NDJSON path; the analytics branch runs unconditionally, so the
two systems stay independent (recording off ≠ analytics off).

**What must change in existing code** (small, listed so review can check it):

| Change | File | Why |
|---|---|---|
| `commit(fn, op)` — add an op label at ~40 call sites | `web/src/studio/Studio.jsx:186` + callers | Without it, no per-feature anything (§3f) |
| Store `op` on the history entry | `Studio.jsx:168` `snapshot` | So undo can name what it undid (#81) |
| Thread `is_error` into `record_tool_use` | `agent_runner.py:1973` ← `:791`/`:810` | The biggest blind spot (#57, #147) |
| HTTPException middleware | `server/app.py` near `:427` | 4xx never reaches the global handler (#144) |
| `swallowed_error` counter in each `except` | `analytics.py:176`, `activity.py:194`, `recorder.js:132`, `desktop/main.js:108`, `feedback.py:149` | Otherwise telemetry can die silently (#149) |
| Ingest probe extension | `server/app.py:856` | codec/HDR/fps are the render-outcome predictors (#34) |

**Where the taxonomy lives so it cannot drift.** One JSON file,
`schemas/analytics_events.json`, next to the existing schemas. Python reads it
into frozen constants (`server/events.py`); JS imports it directly. Property
allowlists live in the same file, so a property that is not declared is dropped
at send time — the same posture `_scrub` (`analytics.py:145`) already takes
toward secrets.

**The ONE contract test** — `tests/contracts/test_analytics_taxonomy.py`, two
assertions both directions:

```
1. Every event NAME literal in the source appears in the taxonomy.
   Scan server/**.py for  capture("<name>"  and  events.<CONST>
   Scan web/src/**.js{,x} for  track('<name>'
   -> a typo'd or undeclared event fails CI.

2. Every taxonomy entry is referenced by at least one call site.
   -> a deleted feature's event cannot rot in the taxonomy, and a
      planned-but-unwired event (today's export_completed, which lives
      only in tests/contracts/test_analytics.py:99) fails LOUDLY
      instead of quietly reading zero forever.
```

Assertion 2 is the one that would have caught the current bug. It also enforces
the `RULES.md` posture: the taxonomy is data, the test is the contract, and no
component holds a hardcoded event name.

**Respecting `RULES.md`.** Rollup accumulation is a pure reducer in a new
`web/src/analytics/rollup.js` (`accumulate(state, type, data) -> state`) with a
unit test — same shape as `interp.js` and `model.js`, no React, no DOM. The
components keep calling `dbg.event` and know nothing about analytics. No logic in
JSX.

---

## 11. Phased plan

**P0 — 3 days.** Turn on the numbers the code already computes.

| # | Item | Files | Success condition | Test |
|---|---|---|---|---|
|1|Taxonomy + contract test|`schemas/analytics_events.json`, `server/events.py`, `web/src/analytics/events.js`, `tests/contracts/test_analytics_taxonomy.py`|Both assertions pass; an undeclared name fails CI|the contract test itself|
|2|JS sink|`web/src/analytics/track.js`, `web/src/analytics/rollup.js`, `POST /api/telemetry/event` in `server/app.py`|Batched POST, always 200, no-op when opted out|`rollup.test.js` (pure reducer); a route test asserting 200 while opted out|
|3|**`export_completed`** + render lifecycle|`server/render_jobs.py:309` (one hook), `:566`|Every status transition emits once, for all 3 origins; superseded is not a failure|extend `tests/contracts/test_render_jobs_inputs.py`: assert exactly one terminal event per job, with a fake capture|
|4|Agent turn events|`server/agent_runner.py:1992`|`cost_usd`, `is_error`, `n_tool_calls` land on every turn incl. crash paths|`tests/contracts/test_agent_runner.py`: a crashed turn still emits `agent_turn_completed`|
|5|Per-tool `is_error`|`server/agent_runner.py:1973`, `server/activity.py:170`|`agent_tool_call.is_error` is non-null for failures|a fake `ToolResultBlock(is_error=True)` produces an error-flagged event|
|6|Session start/end + crash-free|`web/src/analytics/track.js`, `recorder.js:136` bridge|Crash-free session rate computable|`rollup.test.js` end-of-session shape|
|7|Unmet-need channel|`agent_runner.py:1370`, `:1438`, `:370`|#67, #71, #73, #75, #76 fire|`test_agent_runner.py`: a `bash_runs_heavy_media_op` command emits `agent_routed_around_us`|
|8|Provisioning snapshot|`server/app.py:1023` (doctor), `lib/provision.py:444`/`:655`|#3, #4, #5 fire|`tests/contracts/test_provision.py` extension|

Success condition for P0 as a whole: **the funnel screen shows a non-zero
activation rate and a real cost-per-export from the developer's own machine, with
`internal=true` filtering working.**

**P1 — 2 weeks.** The editor and the taxonomy of failure.

| # | Item | Files | Success condition | Test |
|---|---|---|---|---|
|9|`commit(fn, op)` labels + history `op`|`Studio.jsx:168`,`:186` + ~40 callers|Every commit carries an op from a closed set|`Studio.test.jsx`: each toolbar/timeline action produces its expected op|
|10|`editor_session_summary`|`rollup.js`, `recorder.js:136`|#82 emits with `features_used` and `features_eligible`|`rollup.test.js`|
|11|Inspector field telemetry|`StudioInspector.jsx:31` + generic renderer|Every `PROPERTY_SCHEMA` key is reachable as a `field_key`|`propertySchema.test.js`: every declared key appears in the emitted enum|
|12|Preview health rollup|`StudioPreview.jsx:175`,`:203`|p50/p90 seek + incomplete-seek rate|`StudioPreview.test.jsx` (event wiring only; no geometry — `RULES.md:196`)|
|13|Error taxonomy + classifiers|`analytics.py`, `render_jobs.py:563`, `app.py` middleware|`error_class` is an enum, never a raw string; 4xx visible|a raw ffmpeg error string maps to the right `error_class`|
|14|`swallowed_error`|the 5 L6 sites|Counter increments; alert wired|one test per site forcing the `except`|
|15|Ingest probe extension|`server/app.py:856`|codec/HDR/fps/audio on every asset|`test_editor_api.py` extension with a fixture clip|
|16|Dashboards + alerts|PostHog config (no repo change)|5 screens live, 9 alerts armed|manual: fire a synthetic event, confirm the alert|
|17|`project_stalled` sweep|new `server/lifecycle.py`|#31 nightly with `furthest_stage`|unit test over a fixture projects dir|

**P2 — later.** Judgment layers that need users to be worth building.

Micro-surveys (#156–158) · `features_eligible` predicates for full discovery-rate
(#82) · the workaround n-gram miner (§5 row 6) · provisioning step timing (#6) ·
proxy cache GC + `proxy_cache_size` (#131) · watch-time (#120) · frame-endpoint
latency (#121) · export-opened-externally via `shell.openPath` (#138).

Explicitly NOT on any phase: an experiment/A-B framework. See §12.

---

## 12. What I would deliberately NOT collect

A catalog with no exclusions is a wish list. These are the things that look useful
and are not — or are useful and still not worth the trust cost.

**Creative content, in every form.** Prompt bodies, chat text, project names, asset
filenames, text-overlay copy, `reason`/note fields, feedback bodies, video frames,
thumbnails, audio, waveforms, the raw `activity.jsonl` `target` field (it is full
of the user's file paths). `_scrub`'s `_FREETEXT_KEYS` (`analytics.py:38`) already
blocks most of this; the discipline is to not route around it. Every one of these
has a shape-only substitute in the catalog: `prompt_len` + `prompt_shape` (#47),
`name_len` + `ext` (#44), `n_cuts`/`duration_s`/`n_transitions` (#136),
`message_len` (#153). A shape answers "are prompts getting longer, are exports
getting more complex"; the body answers nothing extra that a chosen debug report
would not answer better.

**Session replay of the editor (rrweb or equivalent).** This is the single most
tempting item on the list and the clearest no: the editor's main pane IS the
user's unreleased video. A replay is a screen recording of content they have not
published. The debug recorder (`recorder.js`) already gives higher-fidelity
diagnostics *with consent*, sent only when the user clicks Send debug report
(`server/feedback.py:165`). Replay would add nothing except a way to leak.

**Per-frame anything.** `edit.live` (`Studio.jsx:179`) at 60 fps, pointer
coordinates (`recorder.js:163-165`), scrub positions. §9 shows this is 80% of full
fidelity by volume and it answers exactly one question — "did a drag feel
janky" — which `frames` + `delta_steps` (#87) and preview p90 (#115) answer at
1/1000th the cost. Keep it in the local NDJSON where it belongs.

**Keystroke-level text input.** No product question needs it and it is a keylogger.

**IP-derived geolocation.** At 50 users, "3 users in Berlin" is a
re-identification vector with no decision attached. Revisit when a localization
or pricing decision actually depends on region — not before.

**Any cross-install identity.** No email-to-device linking, no fingerprint, no
"link my two Macs." `device_id` (`server/settings.py:80`) is a random per-install
UUID and should stay one. The cost: a user with two Macs counts twice, so
install-based rates are slightly pessimistic. That is the right error direction.

**An A/B experiment framework.** Not a values call, an arithmetic one: detecting a
5 pp lift on a 40% activation baseline at 80% power needs roughly
`16 × 0.4 × 0.6 / 0.05²` ≈ **1,500 installs per arm**. At 50 installs total, every
experiment is noise, and shipping a flag system now means maintaining branching
code paths that measure nothing. Do case research (§5) until ~2,000 installs.
Feature *flags* for staged rollout are fine and separate — that is release
mechanics, not measurement.

**Continuous resource sampling.** CPU/GPU/memory every second. High volume, and
the actionable slice is already covered by #117 dropped frames and #126 stage
timing, which are attached to a user-visible outcome. Sample resources only
inside a render, only at stage boundaries.

**A second analytics vendor.** One PostHog project, one crash inbox
(`analytics.py:198` deliberately re-homes JS/Electron errors next to backend
exceptions). Two vendors means two taxonomies and two places a number can be
wrong.

**A generic "user did a thing" catch-all.** Every event in the taxonomy must name
a decision (the Decision column). An event with no decision is a row nobody reads
that still costs ingestion and still has to be reasoned about at review time.

**Exact millisecond timestamps on low-value events.** Bucket them. Precise
timing on 200 selection events per session invites treating the timeline as a
reconstruction of what the user was doing minute by minute, which is both a
trust problem and useless.

**Honest closing note on "maximal."** This catalog has 162 rows. Roughly 34 (the ★
rows) will be read weekly; about 60 more exist to be denominators or to diagnose a
specific failure once; the remaining ~68 are coverage — they earn their keep only
by proving a *negative* ("nobody uses ⇅ Arrange"), which is exactly what §6
needs and what a small catalog can never do. The human asked for every data point
and should get every data point. He should also know that if only §8's five
screens ever get built, he loses very little of the decision-making value and
saves most of the work. The rows that would *not* be missed are the ones with no
★ and a P2 priority: #6, #10, #12, #21, #23, #30, #40, #120, #121, #135, #141.

---

## 13. Open questions for the human

1. **Watermark.** #136's `watermark_free` is the North Star's defining property
   and there is no watermark concept in the code today. Is a watermark actually
   shipping, or is the North Star simply "first export"? The metric changes shape
   either way.
2. **`commit(fn, op)`.** Section 3f depends on adding an op label at ~40 call
   sites in `Studio.jsx`. It is mechanical and low-risk but it touches the file
   most likely to be in flight. Do it now, or wait for a quiet window?
3. **Session boundary.** Electron never really "closes." Should a session end on
   window blur + 15 minutes idle, on `pagehide`, or on backend shutdown? This
   choice sets the denominator of every per-session rate, so it should be decided
   once and written down.
4. **HDR.** The HDR path (`video_compose.py:1266-1347`, `hdr_policy` with four
   modes) is a large surface. Was it built for a real user need or for your own
   footage? #129 will answer it in 60 days — do you want it kept on the delete
   watch list until then?
5. **Deletion appetite.** §6's test will likely mark 8–12 features for deletion in
   the first review. Are you willing to delete features you built, or should the
   ledger's verdict column top out at HIDE?
6. **Cost visibility.** #56 measures real per-turn USD. Do you want that surfaced
   to the *user* in-app (it builds trust and suppresses usage) or only to you?
7. **Debug recorder in the shipped app.** It is in the toolbar today
   (`StudioToolbar.jsx:61`). Is it a shipped user feature or dev tooling that
   should hide behind a flag? #114 assumes the former.
8. **Internal filtering.** Everything depends on `internal` being set correctly
   (`analytics.py:51` — env var or `~/.opennolan-internal`). Is the sentinel file
   present on every machine you use, including any test VM? One un-marked machine
   at 50 installs distorts every rate by 2%.
9. **Beta size.** §9's arithmetic assumes 50 then 2,000 installs. If the real beta
   is 500, the per-interaction fidelity becomes affordable and some rollups could
   be skipped. What is the actual number you are planning for?
