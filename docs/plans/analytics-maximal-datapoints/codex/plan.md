# Maximal useful product intelligence

STATUS: PLAN

## 1. Verdict

The five wall numbers are: activation within 7 days, median time to first
watermark-free export, export success rate, agent cost per successful export,
and crash-free sessions. Each must exclude internal/developer machines.
The biggest blind spot is the North Star: the Export button ultimately publishes
a receipted final.mp4, but no export event is emitted at that commit point.
If I had three days, I would wire the publish receipt, agent TurnResult plus tool
outcomes, render status choke point, semantic editor commits/undo, and session
start/end. That makes value, cost, failure, and human-versus-agent contribution
measurable in one path. Maximal useful data is not maximal raw data: this plan
catalogs 154 decision-bearing points while rejecting frames, prompts, filenames,
and pointer chatter that do not earn their cost or trust.

Assumption: keep PostHog for product analytics and use the already-chosen error
inbox for exceptions; this plan does not revisit that tool decision.

## 2. Instrumentation map of this app

Legend: OBS = externally observable today; LOCAL = recorded only when the user
turns on the local debug recorder; BLIND = no durable product signal.

    macOS launch
    desktop/main.js:624 boot
      |
      +-- provision doctor/core/composition
      |   desktop/main.js:356, lib/provision.py:375,655       BLIND
      |
      +-- backend spawn / health / window ready
      |   desktop/main.js:518,465,620                         errors only
      |
      +-- backend boot
          server/app.py:440                                  OBS app_opened
          |
          +-- auth
          |   server/app.py:958,963,973,983                  success only
          |
          +-- project create/open
          |   server/app.py:523; web/src/App.jsx:85      create OBS/open BLIND
          |
          +-- asset ingest / probe / browse
          |   server/app.py:551,856,677                      BLIND
          |
          +-- agent turn
          |   server/app.py:1080 -> server/agent_runner.py:1924
          |       |
          |       +-- each SDK tool use/result
          |       |   server/agent_runner.py:1960             LOCAL activity,
          |       |                                           outcome BLIND
          |       +-- TurnResult cost/error/turns
          |           server/agent_runner.py:1979              BLIND
          |
          +-- shared live edit_decisions doc
          |   read/write server/app.py:776,784
          |       |
          |       +-- editor actions, undo/redo, save
          |       |   web/src/studio/Studio.jsx:173            LOCAL
          |       +-- agent doc adoption
          |           web/src/studio/Studio.jsx:297            LOCAL
          |
          +-- preview/playback
          |   web/src/studio/StudioPreview.jsx:154             LOCAL
          |
          +-- render/proxy/assemble/export
          |   server/render_jobs.py:58,309
          |       +-- scene cache tools/video/video_compose.py:1492
          |       +-- assemble tools/video/video_compose.py:1589
          |       +-- receipt lib/project.py:614               BLIND
          |
          +-- final artifact inspect/download
          |   web/src/App.jsx:1324                             BLIND
          |
          +-- feedback
              server/feedback.py:153                           metadata OBS

    Failure coverage
    Electron main/renderer/GPU  desktop/main.js:65,671-674      OBS exceptions
    React/global JS             web/src/main.jsx:17,37          OBS exceptions
    FastAPI unhandled           server/app.py:421               OBS exceptions
    agent SSE exception         server/app.py:1150              OBS exceptions
    expected 4xx/tool/render    routes and RenderJobStore       mostly BLIND

The existing editor recorder is a source adapter, not the analytics destination:
push returns immediately unless recording is on at
web/src/debug/recorder.js:113. The implementation should retain the private local
recorder and add a separate, allowlisted roll-up sink.

## 3. The data-point catalog

### Property notation and shared envelope

Every property below has a declared shape:

- E{a|b}: bounded enum, used for grouping without cardinality drift.
- B{edges}: ordered bucket, used when exact values add no decision value.
- N(unit): raw number, retained only when sums, percentiles, or rates need it.
- I: random opaque correlation ID, used to join lifecycle events.

Every uploaded product event also carries schema_version N(integer), app_version
E{released versions}, release_channel E{dev|beta|stable}, os_major E{supported
majors}, arch E{arm64|x64}, internal E{true|false}, install_id I, session_id I,
and event_id I. Project-scoped events carry project_key I (per-install opaque,
not the name); agent and render events additionally carry turn_id I and job_id I
when applicable. These are omitted from cells below to keep the table readable.
Raw values are used for time, bytes, counts, rates, and money because aggregation
requires arithmetic. Buckets are used for cohort slicing. Enums are allowlisted.
No event or property name is constructed from user input.

Priority means P0 = required to measure value/reliability, P1 = next decision
cycle, P2 = diagnostic depth. Cost/volume is per relevant unit before sampling.

### 3a. Install / provisioning / launch health

| # | Event / metric | Properties | Hook point (file:line) | Question it answers | Decision it drives | Priority | Cost/volume | SENSITIVE? |
|---:|---|---|---|---|---|---|---|---|
| 1 | install_detected | install_kind E{fresh|upgrade|repair}; previous_version E{release|none} | desktop/main.js:356 | Is this a real first setup or repair? | Separate acquisition from recovery | P0 | 1/install | No |
| 2 | provision_started | tier E{core|composition|capability}; reason E{missing|stale|repair|agent_request}; expected_mb B{0,100,500,1000,5000+} | desktop/main.js:392 | Which setup work blocks entry? | Shrink or defer expensive tiers | P0 | 1/tier run | No |
| 3 | provision_stage_finished | tier E; stage E{venv|python_deps|ffmpeg|ffprobe|remotion|hyperframes|browser|pack}; outcome E{success|failed|skipped|cancelled}; duration_ms N(ms); attempt N(integer) | desktop/main.js:383 | Where does setup time/failure occur? | Fix the slow/failing installer stage | P0 | 5-12/install | No |
| 4 | provision_finished | tier E; outcome E{success|partial|failed|cancelled}; duration_ms N(ms); retry_count N(integer) | desktop/main.js:407 | Can a new user finish setup? | Gate beta release on setup success | P0 | 1/tier run | No |
| 5 | runtime_doctor_snapshot | core_ok E{true|false}; ffmpeg_ok E; ffprobe_ok E; composition_ok E; installed_pack_count N(integer) | server/app.py:1023 | What capability state do real installs have? | Decide bundling and degraded-mode UX | P1 | 1/session, changed only | No |
| 6 | app_launch_started | launch_kind E{cold|activate|reload|post_update}; previous_exit E{clean|crash|kill|unknown} | desktop/main.js:676 | How often do launches start and recover from bad exits? | Prioritize recovery and startup defects | P0 | 1/session | No |
| 7 | backend_spawned | reused E{true|false}; environment E{packaged|dev} | desktop/main.js:518 | Does backend ownership/reuse affect reliability? | Simplify boot ownership if it fails | P1 | 1/session | No |
| 8 | backend_healthy | startup_ms N(ms); probe_count N(integer); cold_provisioned E{true|false} | desktop/main.js:465 | How long until the API is usable? | Set startup budget and optimize imports | P0 | 1/session | No |
| 9 | ui_ready | launch_to_ui_ms N(ms); setup_window_used E{true|false} | desktop/main.js:620 | How long until the user sees the product? | Optimize perceived startup | P0 | 1/session | No |
| 10 | app_session_ended | duration_ms N(ms); foreground_ms N(ms); rendering_overlap_ms N(ms); exit_kind E{clean|window_close|update|crash|backend_fatal|unknown} | desktop/main.js:684 | Are sessions ending normally? | Calculate crash-free sessions, focused time, and recovery | P0 | 1/session | No |
| 11 | update_lifecycle | phase E{available|downloaded|install_clicked|installed|failed}; target_version E{release}; duration_ms N(ms, when known) | desktop/main.js:207 | Are users receiving fixes? | Fix updater or delay deprecation | P1 | 0-4/update | No |
| 12 | launch_failure | phase E{provision|port|backend_spawn|health|ui_load|csp}; failure_class E{missing_binary|download|hash|import|timeout|exit|load|unknown}; retryable E{true|false} | desktop/main.js:652 | Why can a user not enter the app? | Page on beta launch regressions | P0 | only on failure | No; no stderr/path |

### 3b. Auth and setup funnel

| # | Event / metric | Properties | Hook point (file:line) | Question it answers | Decision it drives | Priority | Cost/volume | SENSITIVE? |
|---:|---|---|---|---|---|---|---|---|
| 13 | auth_status_observed | state E{unconnected|connected|needs_reauth}; method E{oauth|api_key|cli|none}; expired_clock E{true|false} | server/auth.py:189 | What share can use the agent? | Improve the dominant blocked state | P0 | 1/session + change | No |
| 14 | auth_connect_opened | entrypoint E{dashboard|project_bar|chat_block|settings}; state E | web/src/App.jsx:90 | Where does sign-in intent originate? | Put guidance at the highest-intent surface | P1 | 0-1/attempt | No |
| 15 | oauth_started | entrypoint E; pending_flow_count N(integer) | server/auth.py:228 | Do users begin OAuth? | Diagnose CTA-to-start drop-off | P0 | 1/attempt | No |
| 16 | oauth_finished | outcome E{success|expired_link|exchange_rejected|network|storage_error}; duration_ms N(ms) | server/app.py:963 | Does OAuth complete and why not? | Repair OAuth copy/exchange/storage | P0 | 1/attempt | No; never code/token |
| 17 | api_key_connect_finished | outcome E{success|invalid|network|storage_error}; duration_ms N(ms) | server/app.py:973 | Is the fallback viable? | Keep, fix, or demote API-key setup | P0 | 1/attempt | No; never key |
| 18 | auth_connected | method E{oauth|api_key|cli}; time_from_first_run_ms N(ms) | server/app.py:970 | How quickly can users unlock the agent? | Simplify onboarding | P0 | 1/connect | No |
| 19 | auth_disconnected | prior_method E; active_project_count B{0|1|2-5|6+} | server/app.py:983 | Is disconnect deliberate or churn-related? | Improve account controls/recovery | P1 | 1/disconnect | No |
| 20 | live_auth_failure | method E; class E{expired|revoked|invalid|credit|billing|unknown}; phase E{turn_start|mid_turn|refresh} | server/auth.py:158 | What breaks already-connected users? | Reauth UX versus billing guidance | P0 | only on failure | No; classified locally |
| 21 | reauth_prompt_outcome | entrypoint E; action E{opened|dismissed|reconnected}; time_to_action_ms N(ms) | web/src/chat/ChatPanel.jsx:202 | Does the recovery prompt work? | Revise or relocate recovery | P1 | 0-2/failure | No |
| 22 | byok_settings_saved | changed_var_count N(integer); provider_family E{anthropic|generation|media|voice|stock|other}; outcome E{success|validation_failed|write_failed} | server/app.py:938 | Which provider setup blocks capabilities? | Improve provider-specific setup | P1 | 1/save | No; names family only |
| 23 | auth_to_first_turn_ms | value N(ms); method E | server/app.py:1080 | After connecting, can users reach agent value? | Shorten post-auth confusion | P1 | derived once/install | No |

### 3c. Project lifecycle

| # | Event / metric | Properties | Hook point (file:line) | Question it answers | Decision it drives | Priority | Cost/volume | SENSITIVE? |
|---:|---|---|---|---|---|---|---|---|
| 24 | project_create_opened | project_count B{0|1|2-5|6+}; auth_state E | web/src/App.jsx:233 | Do users show creation intent? | Improve empty state/new-project CTA | P1 | 1/open | No |
| 25 | project_create_submitted | pipeline E{known IDs|agent_pick}; style E{known IDs|agent_pick}; name_length B{0|1-20|21-50|51+} | web/src/App.jsx:109 | What setup choices are requested? | Curate pipeline/style defaults | P0 | 1/attempt | SENSITIVE: never project name; length only |
| 26 | project_created | pipeline E; style E; create_ms N(ms); first_project E{true|false} | server/app.py:523 | Can users establish a workspace? | Fix create funnel and defaults | P0 | 1/project | No |
| 27 | project_create_failed | failure_class E{unknown_pipeline|unknown_style|duplicate|invalid_name|storage|unknown} | server/app.py:533 | Why does creation fail? | Validation or storage fix | P0 | only on failure | No |
| 28 | project_opened | entrypoint E{just_created|dashboard|auto_resume}; age_days N(days); has_assets E; has_timeline E; has_current_export E | web/src/App.jsx:85 | Which projects are revisited? | Improve resume/navigation and retention | P0 | 1/open | No |
| 29 | editor_opened | source E{project_bar|post_agent|post_export}; cut_count N(integer); overlay_count N(integer); audio_item_count N(integer); duration_s N(seconds) | web/src/App.jsx:139 | When is manual refinement needed? | Invest in editor versus agent automation | P0 | 1/editor session | No |
| 30 | project_content_ready | source E{agent|human|mixed}; time_from_create_ms N(ms); playable_duration_s N(seconds) | server/app.py:784 | How quickly does a blank project become editable/playable? | Fix the slow creation path | P0 | once/project/version | No |
| 31 | project_state_transition | stage E{pipeline stage IDs}; from E{pending|in_progress|awaiting_human|completed|failed|error}; to E; duration_ms N(ms) | server/state.py:91 | Where does the production pipeline stop? | Simplify or repair a stage | P1 | 1/transition | No |
| 32 | thread_lifecycle | action E{created|switched|revived}; prior_message_count B{0|1-5|6-20|21+} | web/src/chat/useAgentChat.js:68 | Are multiple/revived conversations useful? | Keep or simplify thread UI | P1 | 0-3/session | No |
| 33 | project_session_ended | duration_ms N(ms); meaningful_actions N(integer); exports N(integer); last_surface E{dashboard|project|editor|agent}; dirty_abandon E{true|false} | web/src/App.jsx:24 | What did a project visit accomplish? | Identify abandoned project paths | P0 | 1/project visit | No |

### 3d. Asset ingest

| # | Event / metric | Properties | Hook point (file:line) | Question it answers | Decision it drives | Priority | Cost/volume | SENSITIVE? |
|---:|---|---|---|---|---|---|---|---|
| 34 | asset_import_started | kind E{images|video|audio|music}; source E{picker|drop|agent_store}; bytes N(bytes); extension E{allowlisted extension|other} | web/src/api.js:176 | What media enters and by which path? | Prioritize ingest surfaces/formats | P0 | 1/asset | SENSITIVE: never filename; extension only |
| 35 | asset_import_finished | kind E; source E; outcome E{success|deduped|failed}; duration_ms N(ms); bytes N(bytes) | server/app.py:577 | Is ingest reliable at each size? | Fix large-file copy/progress | P0 | 1/asset | No |
| 36 | asset_import_failed | kind E; failure_class E{invalid_kind|invalid_name|path|disk_full|permission|copy|unknown}; bytes_bucket B{0-10MB|10-100MB|100MB-1GB|1GB+} | server/app.py:557 | Why can media not enter? | Validation/storage fix | P0 | only on failure | No |
| 37 | media_probe_finished | kind E; container E{mov|mp4|mkv|webm|wav|mp3|m4a|image|other}; video_codec E{h264|hevc|prores|vp9|av1|none|other}; audio_codec E{aac|pcm|mp3|opus|none|other}; width N(px); height N(px); fps N(fps); duration_s N(seconds); bit_depth B{8|10|12|other}; hdr E{sdr|hlg|pq|unknown}; has_audio E{true|false}; rotation E{0|90|180|270|other} | server/app.py:879 | Which real media shapes must work? | Codec/HDR/proxy roadmap and test corpus | P0 | 1/asset | No |
| 38 | media_probe_failed | kind E; extension E; failure_class E{ffprobe_missing|unsupported|corrupt|timeout|permission|unknown} | server/app.py:898 | What files are accepted but uninspectable? | Add fallback or reject clearly | P0 | only on failure | No; no stderr |
| 39 | browser_proxy_finished | source_codec E; target E{vp9_alpha|browser_native}; cache_hit E; duration_ms N(ms); outcome E{success|failed} | server/editor.py:140 | Which assets need costly browser conversion? | Pre-proxy or improve Chromium support UX | P1 | 0-1/source/session | No |
| 40 | asset_browser_opened | surface E{dashboard|editor|agent_panel}; cwd_kind E{root|images|video|audio|music|hf_renders|renders|other}; item_count B{0|1-10|11-50|51+} | web/src/components/FolderBrowser.jsx:25 | Is browse used and where? | Keep folder browser versus simpler picker | P1 | 1/open/folder | No |
| 41 | asset_preview_opened | kind E; source E{browser|render_card}; current_export E{true|false|na} | web/src/components/AssetModal.jsx:22 | Do users inspect assets before using/exporting? | Improve previews/metadata | P1 | 1/open | No; never asset content |
| 42 | asset_added_to_doc | kind E{image_main|video_main|image_overlay|video_overlay|music|sfx}; method E{modal_add|asset_click|timeline_drop|agent}; time_from_import_ms N(ms) | web/src/studio/Studio.jsx:401 | Which media types become actual edits? | Focus supported asset workflows | P0 | 1/add | No |
| 43 | asset_drop_outcome | kind E; target E{cuts|overlay|audio|outside}; outcome E{added|abandoned|invalid}; at_time_s N(seconds, added only) | web/src/studio/StudioTimeline.jsx:240 | Is drag/drop discoverable and successful? | Improve drop affordance/routing | P1 | 1/drop gesture | No |
| 44 | unused_import_rate | imported N(integer); used_in_doc N(integer); exported N(integer) | server/app.py:589 | Are users importing media they cannot use? | Build format/placement support or simplify ingest | P1 | derived/project | No |
| 45 | source_resolution_failure | reference_kind E{manifest_id|project_path|shared_asset}; consumer E{preview|render|agent}; outcome E{missing|outside_project|unreadable} | server/editor.py:68 | Where do asset references break? | Fix manifest/path contracts | P0 | only on failure | SENSITIVE: no path; reference kind only |

### 3e. Agent turns and tool calls

| # | Event / metric | Properties | Hook point (file:line) | Question it answers | Decision it drives | Priority | Cost/volume | SENSITIVE? |
|---:|---|---|---|---|---|---|---|---|
| 46 | agent_turn_started | model E{AGENT_MODELS}; entrypoint E{project|editor}; thread_kind E{new|resumed}; prompt_chars N(chars); mention_count N(integer); preturn_dirty_flush E{none|success|failed} | server/app.py:1080 | What context starts a turn? | Tune model/default and pre-turn handoff | P0 | 1/turn | SENSITIVE: never prompt; length/local intent only |
| 47 | agent_turn_first_response | latency_ms N(ms); response_kind E{text|tool|question|error} | server/agent_runner.py:1960 | Is the agent responsive before doing work? | Model/SDK/startup optimization | P0 | 1/turn | No |
| 48 | agent_turn_completed | duration_ms N(ms); is_error E; sdk_turns N(integer); cost_usd N(USD); stop_reason E{end_turn|max_turns|budget|tool_error|interrupt|other}; tool_count N(integer); failed_tool_count N(integer); artifact_delta_count N(integer); doc_changed E; render_published E | server/agent_runner.py:1979 | Did the turn work, cost, and change the project? | Model/tool/prompt investment | P0 | 1/turn | No |
| 49 | agent_turn_failed | phase E{query|stream|tool|result|session}; class E{auth|budget|transport|sdk|tool_chain|invalid_output|unknown}; duration_ms N(ms); retryable E | server/app.py:1150 | Why do turns fail? | Fix dominant failure or retry policy | P0 | only on failure | No; no raw error |
| 50 | agent_turn_interrupted | delivered E; elapsed_ms N(ms); tool_in_flight E{registered tool IDs|none}; work_published E | server/agent_runner.py:2009 | Why and when do users stop the agent? | Improve latency/control or tool choice | P0 | 0-1/turn | No |
| 51 | agent_session_resumed | reason E{thread_switch|prior_error|user_stop|backend_restart|background_job}; success E | server/agent_runner.py:2026 | Does context survive interruption/restart? | Fix continuity before adding memory | P1 | 0-1/turn | No |
| 52 | agent_tool_started | tool_id E{registered tools}; family E{file|search|web|skill|shell|render|media|question|provision}; attempt N(integer); source E{native_sdk|mc_inprocess|bash} | server/agent_runner.py:1969 | What does the agent reach for? | Improve high-use tools and routing | P0 | about 20/turn | SENSITIVE: no args/target |
| 53 | agent_tool_finished | tool_id E; family E; outcome E{success|returned_error|denied|cancelled|exception|timeout}; duration_ms N(ms); attempt N(integer); failure_class E{input|missing_file|missing_key|missing_capability|permission|provider|quota|network|decode|render|unknown}; produced_asset E; bytes_out B{0|1B-1MB|1-100MB|100MB+} | server/agent_runner.py:1960 | Which tools work and at what cost/latency? | Repair, replace, or promote a tool | P0 | about 20/turn | SENSITIVE: no result/args/path |
| 54 | agent_tool_retry | tool_id E; prior_failure E; backoff_ms N(ms); outcome E{success|failed|abandoned} | server/agent_runner.py:1960 | Are retries recovering or wasting money? | Change retry limits/prompts | P1 | 0-5/turn | No |
| 55 | tool_permission_decided | tool_id E; action E{allow|confirm|deny}; reason E{safe|unknown|destructive|path_escape|render_route|heavy_media_route}; user_decision E{allow|block|na}; wait_ms N(ms) | server/agent_runner.py:370 | What work is blocked or scary? | Add a safe first-class tool or clearer consent | P1 | 0-3/turn | SENSITIVE: no command/path |
| 56 | bash_render_routearound | attempted_op E{video_compose_render|heavy_media}; steered_to E{render|run_media_op}; later_used_steered_tool E | server/agent_runner.py:401 | Is the agent routing around our tools? | Improve tool descriptions/capability | P0 | only on match | No |
| 57 | bash_heavy_media_signal | operation_family E{silence|motion|reframe|cutout|unknown}; denied E; later_success E | server/agent_runner.py:128 | What media capability was sought outside the product surface? | Build/repair a first-class capability | P0 | only on match | SENSITIVE: local classifier; no command |
| 58 | unsolicited_turn_drained | cause E{background_bash|scheduled_wakeup|unknown}; had_text E; related_job_status E{done|failed|superseded|none} | server/agent_runner.py:1932 | Is background work breaking attribution? | Remove detach paths | P1 | rare | No |
| 59 | agent_question_shown | question_kind E{clarification|choice|approval}; option_count N(integer); turn_elapsed_ms N(ms) | server/agent_runner.py:1333 | Where does the agent need human knowledge? | Improve defaults/context gathering | P1 | 0-3/turn | SENSITIVE: no question text; local topic enum |
| 60 | agent_question_resolved | outcome E{option|custom|dismissed|turn_stopped}; wait_ms N(ms) | server/app.py:1190 | Do questions unblock work? | Change question timing/UI | P1 | 0-1/question | SENSITIVE: never custom answer |
| 61 | api_key_requested | provider_family E; reason_class E{generation|voice|stock|other}; already_in_byok E | server/agent_runner.py:1370 | Which cloud capabilities users try but cannot run? | Prioritize bundled/local alternatives or setup | P0 | 0-2/turn | No |
| 62 | api_key_request_resolved | provider_family E; outcome E{provided|declined|save_failed}; wait_ms N(ms); retry_succeeded E{true|false|unknown} | server/app.py:1197 | Does just-in-time key setup recover the task? | Keep or redesign key prompt | P0 | 1/request | No; never key |
| 63 | capability_requested | pack E{known packs}; reason_class E{transcription|vision|background_removal|beat_sync|tts|other}; installed_before E | server/agent_runner.py:1438 | Which local capability is demanded? | Bundle/promote high-demand packs | P0 | 0-2/turn | No |
| 64 | capability_request_resolved | pack E; outcome E{already_installed|installed|declined|install_failed}; wait_ms N(ms); retry_succeeded E{true|false|unknown} | server/app.py:1223 | Does lazy installation preserve the turn? | Bundle or fix install flow | P0 | 1/request | No |
| 65 | asset_mention_query | result_count B{0|1|2-5|6-20|21+}; query_chars B{0|1-3|4-10|11+}; outcome E{selected|dismissed|sent_plain|abandoned}; input_method E{keyboard|mouse} | web/src/chat/ChatPanel.jsx:58 | Can users find the asset they want to tell the agent about? | Improve search/indexing or add missing asset type | P1 | 0-5/turn | SENSITIVE: never query or filenames |
| 66 | asset_mention_resolved | mention_count N(integer); outcome E{all_found|some_missing|shape_rejected}; missing_count N(integer) | server/app.py:1095 | Do structured references reach the agent? | Fix stale/search/sidecar contracts | P0 | 0-1/turn | SENSITIVE: no paths |
| 67 | agent_output_adopted | had_local_edits E; cut_delta N(integer); overlay_delta N(integer); audio_changed E; top_level_fields_changed_count N(integer); adopt_ms N(ms) | web/src/studio/Studio.jsx:297 | How much agent work enters the live human editor? | Improve hybrid handoff | P0 | 0-1/turn in editor | No |
| 68 | agent_human_conflict | local_edits_during_turn N(integer); undo_restored_local E; save_block_count N(integer) | web/src/studio/Studio.jsx:301 | Are simultaneous edits causing friction? | Lock, merge, or communicate better | P1 | 0-1/turn | No |
| 69 | agent_effectiveness_rollup | tool_unique_count N(integer); skills_used N(integer); files_touched B{0|1-5|6-20|21+}; successful_export_within_30m E; human_undo_within_5m E | server/activity.py:220 | Which agent behaviors predict value versus correction? | Refine system prompt/tools | P1 | 1/turn | SENSITIVE: counts only, no targets |

### 3f. Editor

| # | Event / metric | Properties | Hook point (file:line) | Question it answers | Decision it drives | Priority | Cost/volume | SENSITIVE? |
|---:|---|---|---|---|---|---|---|---|
| 70 | editor_session_started | entrypoint E; doc_origin E{empty|agent|human|mixed}; cut_count N; overlay_count N; audio_count N; duration_s N | web/src/studio/Studio.jsx:127 | What complexity enters editing? | Tune editor defaults/performance | P0 | 1/editor session | No |
| 71 | editor_session_ended | duration_ms N; commit_count N; drag_count N; undo_count N; redo_count N; unique_feature_count N; saves N; exports N; dirty_abandon E | web/src/studio/Studio.jsx:35 | Is an editor visit productive? | Find shallow/abandoned sessions | P0 | 1/editor session | No |
| 72 | editor_feature_discovered | feature_id E{truth-table IDs}; discovery_source E{toolbar|timeline|inspector|canvas|asset_panel|shortcut|agent}; eligible_session_index N(integer) | web/src/studio/Studio.jsx:183 | Can eligible users find each feature? | Relocate or teach hidden features | P1 | once/install/feature | No |
| 73 | editor_action_committed | feature_id E; target_type E{cut|overlay|audio|canvas|project}; method E{button|keyboard|typed|select|asset_click|modal_add}; origin E{human|agent_adopt}; changed_field_count N; target_count N | web/src/studio/Studio.jsx:183 | What meaningful edits are made? | Invest by use and outcome | P0 | about 40/session | No |
| 74 | editor_drag_completed | feature_id E{trim|reorder|move|resize|scrub_field|canvas_position|gain|panel_resize}; target_type E; duration_ms N; distance_bucket B{0-10px|11-100|101-500|500+}; net_change_bucket B{none|small|medium|large}; cancelled E | web/src/studio/StudioTimeline.jsx:210 | Do direct-manipulation controls work? | Improve handles/sensitivity | P1 | about 15/session | No |
| 75 | property_field_committed | clip_type E{8 property types}; field_id E{schema keys}; input_method E{scrub|type|arrow|select|preset|canvas}; value_delta_bucket B{none|small|medium|large} | web/src/studio/propertySchema.js:60 | Which exact controls matter and how are they used? | Keep defaults and input styles | P1 | about 20/session | SENSITIVE: text content and note excluded; lengths only |
| 76 | editor_selection_rollup | selected_cut_count N; selected_overlay_count N; selected_audio_count N; deselect_count N; median_selection_ms N(ms) | web/src/studio/Studio.jsx:625 | What objects users inspect before editing? | Improve inspector/navigation | P2 | 1/session rollup | No |
| 77 | editor_undo | original_feature_id E; latency_ms N; chain_depth N; origin E{human_action|agent_adopt}; followed_by_redo E | web/src/studio/Studio.jsx:196 | Which features create mistakes/regret? | Fix or remove high-undo interactions | P0 | about 5/session | No |
| 78 | editor_redo | feature_id E; latency_from_undo_ms N; chain_depth N | web/src/studio/Studio.jsx:205 | Was undo exploratory or corrective? | Interpret undo rate correctly | P1 | about 1/session | No |
| 79 | editor_shortcut_used | shortcut E{play|undo|redo|save|split|delete|escape}; feature_id E | web/src/studio/Studio.jsx:633 | Do power users prefer keyboard? | Preserve/teach valuable shortcuts | P1 | roll up counts/session | No |
| 80 | editor_operation_blocked | feature_id E; reason E{no_selection|last_cut|invalid_playhead|agent_busy|no_history|no_render|runtime_mismatch}; attempts N(integer) | web/src/studio/Studio.jsx:351 | Where does intent hit a guardrail? | Improve states/copy/capability | P0 | 0-5/session | No |
| 81 | panel_layout_changed | panel E{agent|inspector|timeline}; outcome E{resized|collapsed|opened}; width_or_height_bucket B{small|medium|large} | web/src/studio/Studio.jsx:440 | What workspace layout do users need? | Set defaults/responsive breakpoints | P2 | roll up/session | No |
| 82 | timeline_zoom_changed | method E{button|slider}; from_bucket B{20-60|61-120|121-180|181-240}; to_bucket B | web/src/studio/StudioTimeline.jsx:275 | Is zoom range/default useful? | Tune timeline scale/navigation | P2 | roll up/session | No |
| 83 | preview_track_visibility | track_kind E{main|overlay|music|narration|sfx}; action E{hide|show}; hidden_duration_ms N | web/src/studio/Studio.jsx:54 | Do users isolate tracks to edit? | Build solo/mute controls or remove clutter | P2 | 0-5/session | No |
| 84 | editor_save_finished | kind E{manual|autosave|pre_agent|pre_export}; outcome E{success|rejected|blocked_agent|network}; duration_ms N; dirty_age_ms N | web/src/studio/Studio.jsx:218 | Is the shared doc safely persisted? | Fix schema/races before feature work | P0 | about 8/session | No |
| 85 | autosave_debounce_rollup | scheduled N; coalesced N; blocked_agent N; failed N; p95_dirty_age_ms N | web/src/studio/Studio.jsx:244 | Does 700 ms autosave achieve a current disk doc? | Tune debounce/merge behavior | P1 | 1/session | No |
| 86 | schema_write_rejected | object_kind E{document|cut|overlay|audio}; failure_field E{allowlisted schema paths|unknown}; author E{human|agent|unknown} | server/app.py:784 | What edits create invalid documents? | Repair sanitizer/agent contract | P0 | only on failure | No; classified message only |
| 87 | edit_origin_rollup | human_commits N; human_drag_commits N; agent_doc_changes N; agent_changes_undone N; final_doc_human_share N(percent) | web/src/studio/model.js:501 | Is the hybrid product actually hybrid? | Balance agent and editor investment | P0 | 1/session/export | No |
| 88 | dirty_work_abandoned | dirty_age_ms N; commit_count_since_save N; exit_reason E{back|window_close|crash|project_switch} | web/src/studio/Studio.jsx:214 | Is unsaved work being lost? | Add unload flush/recovery | P0 | only on abandon | No |
| 89 | feature_noop | feature_id E; reason E{same_value|out_of_range|nothing_to_arrange|invalid_target|unknown}; repeated_count N | web/src/studio/Studio.jsx:186 | Are controls clicked but producing no change? | Fix feedback or remove confusing affordance | P1 | roll up/session | No |
| 90 | agent_panel_usage | action E{open|close|resize|send}; editor_elapsed_ms N | web/src/studio/Studio.jsx:670 | Is the agent useful during manual editing? | Improve co-edit layout/handoff | P1 | roll up/session | No |
| 91 | editor_action_to_export | feature_id E; used_in_last_export E; minutes_before_export N(min); later_undone E | lib/project.py:614 | Which edits contribute to shipped output? | Prioritize outcome-linked features | P1 | derived/export | No |

### 3g. Preview / playback performance

| # | Event / metric | Properties | Hook point (file:line) | Question it answers | Decision it drives | Priority | Cost/volume | SENSITIVE? |
|---:|---|---|---|---|---|---|---|---|
| 92 | preview_play_session | mode E{source|render}; duration_ms N; played_media_ms N; reached_end E; pause_reason E{user|scrub|mode_switch|ended|error}; cut_count_seen N | web/src/studio/StudioPreview.jsx:226 | Does the preview support review, not just clicks? | Invest in playback versus render preview | P0 | 1/play interval | No |
| 93 | preview_seek_completed | mode E; requested_to_seeked_ms N; coalesced_request_count N; source_codec E; ready_state_at_request E{0|1|2|3|4}; distance_s N | web/src/studio/StudioPreview.jsx:192 | How responsive is scrubbing? | Proxy/seek-chaser work and SLO | P0 | roll up p50/p95/session | No |
| 94 | preview_seek_abandoned | mode E; reason E{new_seek|source_change|play|unmount|error}; age_ms N | web/src/studio/StudioPreview.jsx:160 | Are seeks never landing? | Fix frozen-frame regressions | P1 | roll up/session | No |
| 95 | preview_stall | mode E; kind E{waiting|stalled}; duration_ms N; source_codec E; at_time_bucket B{start|middle|end}; recovered E | web/src/studio/StudioPreview.jsx:200 | Which playback paths stall? | Generate proxies or change buffering | P0 | 0-5/play | No |
| 96 | preview_media_error | mode E; media_kind E{main_video|overlay_video|music|narration|sfx|render}; browser_code E{1|2|3|4|unknown}; ready_state E | web/src/studio/StudioPreview.jsx:200 | What media cannot be decoded/played? | Format support and fallbacks | P0 | only on error | No; no URL |
| 97 | preview_source_ready | media_kind E{video|image|render}; metadata_ms N; first_frame_ms N; cache_path E{native|browser_proxy|render}; outcome E{ready|failed} | web/src/studio/StudioPreview.jsx:195 | How long to first visible frame? | Preload/proxy/cache strategy | P1 | 1/source change rollup | No |
| 98 | overlay_preview_quality | overlay_kind E{image|video|text}; count_visible N; dropped_or_late_sync_count N; max_drift_ms N | web/src/studio/StudioPreview.jsx:64 | Does WYSIWYG hold with overlays? | Optimize overlay sync/compositing | P1 | 1/play rollup | No |
| 99 | preview_audio_quality | track_kind E{music|narration|sfx|overlay_audio}; play_rejection_count N; max_drift_ms N; active_track_count N | web/src/studio/StudioPreview.jsx:33 | Is source preview audio trustworthy? | Fix sync/autoplay/mix preview | P1 | 1/play rollup | No |
| 100 | preview_frame_cadence | mode E; sampled_frames N; long_frame_count N; p95_frame_ms N; max_frame_ms N; overlay_count_bucket B{0|1-3|4-10|11+} | web/src/studio/StudioPreview.jsx:228 | Does the editor remain smooth under load? | Rendering/canvas optimization | P2 | 1/10 sessions | No |
| 101 | preview_mode_switched | from E{source|render}; to E; has_current_render E; time_in_prior_mode_ms N; trigger E{user|export_complete} | web/src/studio/Studio.jsx:110 | Is render preview needed and when? | Keep, demote, or improve mode switch | P1 | 0-5/session | No |

### 3h. Render / proxy / assemble

| # | Event / metric | Properties | Hook point (file:line) | Question it answers | Decision it drives | Priority | Cost/volume | SENSITIVE? |
|---:|---|---|---|---|---|---|---|---|
| 102 | render_queued | origin E{editor|agent}; runtime E{ffmpeg|remotion|hyperframes}; scene_count N; duration_s N; publish E; hdr_policy E{auto|preserve|tonemap|sdr}; queue_depth N | server/render_jobs.py:58 | What work enters rendering? | Capacity/runtime defaults | P0 | 1/render | No |
| 103 | render_status_transition | from E{queued|running}; to E{running|done|failed|superseded}; elapsed_ms N; origin E | server/render_jobs.py:309 | Where and how long do jobs spend? | Queue and execution SLOs | P0 | 2/render | No |
| 104 | render_queue_wait | value_ms N; origin E; superseded_before_run E | server/render_jobs.py:482 | Is serialization wasting user time? | Cancellation/coalescing design | P1 | derived/render | No |
| 105 | render_scene_finished | runtime E; cache_hit E; duration_ms N; source_kind E{video|image|composition}; hdr_mode E{sdr|preserve|tonemap}; outcome E{success|failed}; failure_class E{decode|transform|runtime|encode|cache|unknown} | tools/video/video_compose.py:1492 | Which scenes/cache/runtime fail or cost time? | Cache/runtime/format work | P1 | about 6/render | No; no source/hash |
| 106 | render_proxy_summary | runtime E; n_scenes N; n_cached N; n_rendered N; cache_hit_rate N(percent); proxy_ms N; hdr_source E; hdr_decision E{sdr|preserve|tonemap}; warning_count N | tools/video/video_compose.py:1559 | Is render-once actually saving work? | Cache correctness/performance | P0 | 1/render | No |
| 107 | assemble_started | runtime E; scene_count N; overlay_count N; audio_track_count N; transition_count N; target_width N; target_height N; target_fps N | tools/video/video_compose.py:1589 | What final-assembly complexity costs time? | Optimize dominant dimensions/features | P1 | 1/render | No |
| 108 | assemble_finished | outcome E{success|failed}; duration_ms N; output_bytes N; failure_class E{concat|transition|overlay|audio|subtitle|encode|review|unknown}; warning_count N | tools/video/video_compose.py:1602 | Is final assembly the failure bottleneck? | Repair the responsible stage | P0 | 1/render | No |
| 109 | render_finished | outcome E{done|failed|superseded}; total_ms N; queue_ms N; proxy_ms N; assemble_ms N; origin E; runtime E; warning_count N; final_review_status E{pass|fail|unknown} | server/render_jobs.py:587 | Does rendering deliver an artifact? | Release gating/runtime selection | P0 | 1/render | No |
| 110 | render_failed | stage E{input|resolve|proxy|assemble|publish|review}; failure_class E{missing_doc|missing_asset|schema|decode|hdr|runtime|ffmpeg_exit|disk|permission|exception|unknown}; retryable E; elapsed_ms N; exit_code_bucket B{signal|1-127|128-255|other} | server/render_jobs.py:560 | Precisely why did render fail? | Fix top class and guide retry | P0 | only on failure | SENSITIVE: classified stderr only |
| 111 | render_superseded | origin E; stage E{queued|running|publish_guard}; elapsed_ms N; newer_origin E | server/render_jobs.py:168 | Are users/agent issuing redundant jobs? | Debounce/cancel UX | P1 | only when superseded | No |
| 112 | render_warning | warning_code E{default_renderer|hdr_fallback|cache_summary|missing_audio|preview_mismatch|other}; count N | server/render_jobs.py:587 | Which successful renders are degraded? | Turn warning into product fix | P1 | roll up/render | SENSITIVE: code only, no text |
| 113 | media_op_finished | tool_id E{registered media tools}; outcome E; duration_ms N; produced_asset E; deliverable_write_warning E | server/render_jobs.py:403 | Which heavy operations succeed outside full render? | Promote/repair agent media tools | P1 | 0-3/turn | No |
| 114 | render_artifact_current | current E; reason E{no_video|no_receipt|video_replaced|no_doc|doc_changed|current|unreadable}; age_ms N | lib/project.py:445 | Is final.mp4 trustworthy now? | Block stale handoff and fix out-of-band writers | P0 | on open/export inspect, change only | No |
| 115 | render_time_ratio | render_ms N; timeline_duration_s N; ratio N(render seconds/video second); runtime E; cache_hit_rate N(percent) | server/render_jobs.py:503 | How expensive is output for a given video? | Performance budget and runtime choice | P1 | derived/render | No |

### 3i. Export

In current code the toolbar calls the operation Export, while the backend calls
it a render job. The product event is an export only when the canonical final
artifact and its receipt commit successfully. A click is intent, not activation.

| # | Event / metric | Properties | Hook point (file:line) | Question it answers | Decision it drives | Priority | Cost/volume | SENSITIVE? |
|---:|---|---|---|---|---|---|---|---|
| 116 | export_clicked | origin E{editor_toolbar}; first_export_attempt E; dirty_before E; timeline_duration_s N; cut_count N; overlay_count N; audio_count N | web/src/studio/StudioToolbar.jsx:97 | Who intends to finish and with what project? | Funnel/friction segmentation | P0 | 1/click | No |
| 117 | export_aborted_before_job | reason E{save_failed|agent_busy|invalid_doc|duplicate_click}; elapsed_ms N | web/src/studio/Studio.jsx:262 | Why does intent never start computation? | Fix save/race/validation UX | P0 | only on abort | No |
| 118 | export_started | origin E{editor|agent}; first_export_attempt E; runtime E; output E{canonical_final}; app_watermark E{false}; time_from_create_ms N | server/render_jobs.py:58 | How many projects reach real export work? | Measure funnel and runtime | P0 | 1/job | No |
| 119 | export_completed | first_export E; app_watermark_free E{true}; total_ms N; output_bytes N; width N; height N; fps N; video_codec E; audio_codec E; hdr E{sdr|hlg|pq|unknown}; runtime E; final_review_status E; doc_origin E{agent|human|mixed}; agent_cost_to_date_usd N | lib/project.py:614 | Did the user receive the North Star artifact? | Activation, format, cost, and quality decisions | P0 | 1/receipt commit | No |
| 120 | export_failed | stage E; failure_class E; first_export_attempt E; retryable E; elapsed_ms N | server/render_jobs.py:560 | What blocks activation? | Fix highest activation blocker | P0 | only on failure | No |
| 121 | export_timed_out_in_ui | poll_ms N; backend_terminal_later E{done|failed|superseded|unknown}; origin E | web/src/studio/Studio.jsx:287 | Does UI give up while work continues? | Replace polling timeout/progress contract | P0 | only on timeout | No |
| 122 | export_retried | prior_outcome E{failed|superseded|timeout|done}; retry_delay_ms N; retry_outcome E{done|failed|superseded} | server/render_jobs.py:58 | Are failures recoverable by retry? | Automatic retry and messaging | P1 | derived/retry | No |
| 123 | export_artifact_opened | surface E{render_preview|asset_card|fullscreen}; current E; first_export E | web/src/App.jsx:1341 | Do users inspect the finished work? | Improve review/QA handoff | P1 | 0-3/export | No; no frames |
| 124 | export_downloaded | current E; output_bytes N; time_from_complete_ms N; first_export E | web/src/App.jsx:1347 | Did the artifact leave the app for use? | Distinguish production from handoff | P0 | 0-1/export | No |
| 125 | export_became_stale | cause E{human_edit|agent_edit|external_replace|receipt_missing}; time_since_export_ms N | lib/project.py:498 | How often do users edit after exporting? | Add re-export cues/version handling | P1 | 0-1/export | No |

### 3j. Errors, crashes, and failures

Failure classes are outcomes, not duplicate exceptions. Expected product
failures go to analytics as bounded classes; unexpected exceptions go once to
the error inbox with release and layer. Raw stderr, paths, media, tool I/O, and
creative text never become product-event properties.

| # | Event / metric | Properties | Hook point (file:line) | Question it answers | Decision it drives | Priority | Cost/volume | SENSITIVE? |
|---:|---|---|---|---|---|---|---|---|
| 126 | application_exception | layer E{electron_main|renderer|react|python_api|agent_sdk|render_worker}; fatal E; handled E; fingerprint E{registered issue IDs|unknown}; release E | desktop/main.js:671 | Which code defects affect users/releases? | Triage and rollback | P0 | only on exception | SENSITIVE: scrubbed stack in error sink, never product analytics |
| 127 | process_gone | process E{renderer|gpu|utility|backend|provision}; reason E{clean|crashed|killed|oom|launch_failed|unknown}; exit_code_bucket B | desktop/main.js:673 | Which native/process failures escape JS? | Runtime/GPU/backend hardening | P0 | only on exit | No |
| 128 | api_unhandled_500 | route_id E{registered routes}; method E{GET|POST|PUT|DELETE}; layer E{sync|stream|background} | server/app.py:421 | Which API surface crashes? | Fix highest-impact endpoint | P0 | only on 500 | No; route template, not raw path |
| 129 | api_expected_failure | route_id E; status E{400|404|409|422|503}; class E{validation|not_found|conflict|unavailable|path_guard}; user_visible E | web/src/api.js:3 | What recoverable errors hit users? | Improve validation or availability | P1 | roll up counts/session | No |
| 130 | network_operation_failed | operation E{auth|analytics|feedback|asset_api|chat|update|provision}; class E{offline|dns|timeout|tls|reset|http_4xx|http_5xx}; retry_count N; recovered E | web/src/api.js:3 | Where does connectivity break local-first flows? | Offline queue/retry priorities | P0 | only on failure | No |
| 131 | editor_load_failed | phase E{doc|assets|source_meta|render_media}; class E{404|schema|decode|network|unknown}; recovered E | web/src/studio/Studio.jsx:127 | Why can a project not be edited? | Recovery/fallback work | P0 | only on failure | No |
| 132 | preview_failure | class E{source_missing|metadata|decode|seek_timeout|stall|audio_play|overlay_sync}; recovered E; media_kind E | web/src/studio/StudioPreview.jsx:195 | Which playback failures hurt editing? | Proxy/format/preview fix | P0 | failure rollup/session | No |
| 133 | agent_failure | class E{auth|sdk|transport|budget|interrupt|invalid_artifact|tool_chain|background_attribution}; turn_phase E; recovered_next_turn E | server/agent_runner.py:1987 | Where does the AI experience fail? | Model/SDK/tool architecture | P0 | failure rollup/turn | No |
| 134 | tool_failure | tool_id E; class E{permission|missing_input|provider|quota|network|decode|runtime|output_missing|exception}; attempt N; recovered E | server/agent_runner.py:782 | Which capability is unreliable? | Repair/remove tool | P0 | 0-N/tool | SENSITIVE: no result text |
| 135 | render_failure | stage E; class E; runtime E; source_media_profile E{codec-HDR-resolution class}; recovered_on_retry E | server/render_jobs.py:358 | Which media/runtime combination fails output? | Add fixtures/fallbacks | P0 | only on failure | No |
| 136 | feedback_delivery_failure | channel E{relay|resend}; class E{not_configured|timeout|http|network|unknown}; locally_stored E; recovered E | server/feedback.py:120 | Is user feedback reaching the developer? | Add retry/status | P1 | only on failure | No |
| 137 | telemetry_delivery_health | queued N; sent N; dropped N; rejected_schema N; oldest_age_ms N; last_flush E{success|failed|none} | server/analytics.py:166 | Are the numbers themselves complete? | Fix outbox/schema before trusting metrics | P0 | local rollup, 1/session | No |
| 138 | data_quality_violation | class E{unknown_event|unknown_property|wrong_type|high_cardinality|sensitive_key}; event_id E{catalog events}; blocked E | server/analytics.py:145 | Is taxonomy/scrubbing drifting? | Fail CI or block bad event | P0 | only on violation | SENSITIVE: names only, value discarded |
| 139 | crash_free_session_metric | sessions_without_fatal N; eligible_sessions N; rate N(percent); release E | desktop/main.js:684 | Is a release safe enough for beta? | Halt rollout/rollback | P0 | derived daily | No |

### 3k. Feedback and requests

| # | Event / metric | Properties | Hook point (file:line) | Question it answers | Decision it drives | Priority | Cost/volume | SENSITIVE? |
|---:|---|---|---|---|---|---|---|---|
| 140 | feedback_opened | entrypoint E{dashboard|debug_report|error|post_export|survey}; context_feature_id E{truth-table IDs|none}; context_outcome E{success|failure|abandon|none} | web/src/App.jsx:205 | What prompts users to speak? | Put asks at useful moments | P1 | 0-1/session | No |
| 141 | feedback_submitted | kind E{bug|feature|other}; chars N; has_email E; has_diagnostics E; has_debug_session E; local_store E; delivery E{sent|failed|not_configured} | server/feedback.py:153 | What explicit demand/problems arrive? | Triage requests and reliability | P0 | rare | SENSITIVE: full message/email stay only in intended feedback channel; analytics gets metadata |
| 142 | feedback_topic_classified | topic E{editor|agent|asset|preview|render|export|auth|performance|other}; request_type E{new_capability|improvement|remove|bug}; confidence B{low|medium|high}; linked_feature_id E{IDs|none} | server/feedback.py:175 | Which themes repeat across channels? | Merge evidence into roadmap | P1 | 1/feedback | SENSITIVE: local/manual classification; never raw text in analytics |
| 143 | debug_report_outcome | phase E{record_started|record_stopped|send_opened|submitted|discarded}; event_count B{0|1-100|101-1000|1001+}; error_count B{0|1|2-5|6+} | web/src/studio/Studio.jsx:117 | Do bug reports include reproducible evidence? | Improve recorder/report flow | P1 | 0-5/report | SENSITIVE: raw log attachment only with explicit feedback; summary uploaded |
| 144 | survey_shown | survey_id E{registered surveys}; trigger E{post_export|failure|feature_repeat|churn_risk}; eligible_count N; feature_id E{IDs|none} | web/src/studio/Studio.jsx:262 | Was the right context sampled? | Interpret response bias | P1 | at most 1/5 sessions | No |
| 145 | survey_answered | survey_id E; score N(scale value); choice E{registered options}; feature_id E; delay_ms N | web/src/studio/Studio.jsx:262 | How satisfied/needed is the contextual feature? | Validate build/remove evidence | P1 | 0-1/show | SENSITIVE: free text goes to feedback channel, not analytics |
| 146 | survey_dismissed | survey_id E; delay_ms N; repeated_prompt_count N | web/src/studio/Studio.jsx:262 | Are surveys mistimed/noisy? | Reduce or retarget surveys | P2 | 0-1/show | No |
| 147 | request_evidence_rollup | capability_id E{roadmap taxonomy}; distinct_install_count N; explicit_requests N; agent_blocks N; workaround_sequences N; failed_outcomes N; successful_exports_affected N | server/agent_runner.py:128 | How strong is demand for a capability? | Ranked build queue | P0 | derived weekly | No |

### 3l. Retention, habit, and lifecycle

| # | Event / metric | Properties | Hook point (file:line) | Question it answers | Decision it drives | Priority | Cost/volume | SENSITIVE? |
|---:|---|---|---|---|---|---|---|---|
| 148 | meaningful_active_day | action E{export_completed|project_content_ready|editor_commit|successful_agent_turn}; first_of_day E | server/analytics.py:166 | Did the user do real creative work today? | Retention denominator/event | P0 | derived/day | No |
| 149 | lifecycle_state_changed | from E{new|activated|retained|dormant|resurrected}; to E; days_since_meaningful N(days) | server/analytics.py:166 | Where is the lifecycle moving? | Trigger research/re-engagement | P1 | on change | No |
| 150 | project_revisited | age_days N; days_since_last_open N; current_export E; prior_export_count N; reason E{edit|agent|inspect|unknown} | web/src/App.jsx:85 | Do projects create ongoing work? | Project organization/resume features | P1 | 1/revisit | No |
| 151 | repeat_export | project_age_days N; export_index N; time_since_prior_export_ms N; doc_changed E | lib/project.py:614 | Is OpenNolan reused to iterate, not just trialed? | Versioning/re-export workflow | P0 | 1/export after first | No |
| 152 | feature_repeat_use | feature_id E; eligible_session_index N; use_index N; days_since_first_use N; successful_outcome E | web/src/studio/Studio.jsx:183 | Does feature adoption persist? | Retain/improve/remove feature | P1 | derived/feature/session | No |
| 153 | cohort_retention_metric | window E{D1|D7|D30}; cohort E{first_project|first_export}; returned_meaningfully N; eligible_installs N; rate N(percent) | server/analytics.py:166 | Do activated creators return on a project cadence? | PMF/onboarding versus habit work | P0 | derived daily | No |
| 154 | dormant_project_inventory | age_bucket B{0-7d|8-30d|31-90d|91d+}; has_export E; has_assets E; last_stage E; count N | server/app.py:519 | Where do projects die before value? | Research abandoned stage and cleanup UX | P1 | local aggregate weekly | No |

## 4. Metric definitions: solid numbers

### Counting rules

An eligible install is non-internal, has ui_ready, and was able to upload product
events for the measured window. A reinstall does not create a new install_id
unless settings are genuinely new. An eligible project has project_created.
An eligible session has ui_ready and app_session_ended, or a synthetic end at
the last event plus a 30-minute inactivity cap after a crash. A successful
export is only export_completed from the receipt commit. Events are deduplicated
by event_id; jobs, turns, tools, projects, and sessions are counted by their
opaque IDs. Superseded renders and deliberate agent interrupts are reported but
excluded from reliability denominators. Internal machines are always excluded.

The initial good/bad thresholds below are beta guardrails, not industry truth.
Do not make a percentage decision before 30 eligible external installs or 50
eligible attempts; use interviews and event traces before that. Reset thresholds
after the first 100 activated external users.

| Metric | Exact formula and source | Initial good / bad | Decision triggered |
|---|---|---|---|
| Activation rate, 24h and 7d | distinct eligible installs with first export_completed at t <= first ui_ready + window / distinct eligible fresh installs whose full window elapsed | Good: >=40% at 7d; bad: <20% | If bad, fix the largest funnel loss before adding breadth |
| Project-to-export conversion | distinct projects with export_completed / distinct project_created projects old enough for the window | Good: >=60% in 7d; bad: <30% | Separates acquisition/onboarding loss from project execution loss |
| Time to value | for each activated install, first export_completed.ts - first ui_ready.ts; report P50/P75/P90, not the mean | Good: P50 <30 min and P90 <2 h; bad: P50 >2 h or P90 >24 h | Fix the stage contributing most elapsed non-idle time |
| Render success rate | count render_finished where outcome=done / count render_finished where outcome in {done,failed}; slice by runtime/media profile; exclude superseded | Good: >=95%; bad: <90% or any common media cohort <85% | Block release or route failing profile to fallback |
| Export success rate | count export_completed / count export_started with terminal outcome; timeout is pending until backend terminal is known | Good: >=95%; bad: <90% | Highest-priority activation reliability work |
| Agent turn success rate | count agent_turn_completed with is_error=false / delivered turns excluding deliberate agent_turn_interrupted | Good: >=90%; bad: <80% | Change model, SDK, system prompt, or tool chain based on failure slice |
| Agent useful-turn rate | successful turns with doc_changed OR produced asset/render OR explicit question answered / successful turns | Good: >=75%; bad: <50% | A technically successful but inert turn is a product failure; improve routing |
| Agent tool execution success by tool | successful agent_tool_finished / agent_tool_finished with outcome in {success,returned_error,exception,timeout}, grouped by tool_id; denial is a separate permission rate | Good: >=95% for core tools; bad: <85% after 30 calls | Repair, replace, or stop advertising the tool |
| Agent tool retry recovery | retries ending success / agent_tool_retry events | Good: >=60% with <=1 retry; bad: <25% or median attempts >2 | Remove wasteful retry instruction or make failure terminal |
| Agent cost per successful export | for first export: sum total_cost_usd of project turns from project_created through first export_completed / 1; portfolio metric = sum those costs / successful first exports. Repeat export uses cost since prior receipt | Good hypothesis: P50 <=$3 and P90 <=$10 for API-key users; bad: P50 rises >25% release-over-release without activation gain | Change model/tool usage and show cost guardrails; keep subscription users labeled notional |
| Cost of failed work | sum agent turn cost for projects with no export within 7d / those failed-to-export projects | Good: P50 <$1; bad: P50 > successful-export cost | Stop expensive loops earlier and surface blockers |
| Feature adoption rate | distinct eligible installs with editor_action_committed(feature_id) / distinct installs that were eligible and exposed to that feature | Core good: >=30%; bad: <10%. Specialist features are judged against their eligible cohort | Promote, improve, or question the feature; never divide by all users |
| Feature retention | adopters who use feature_id in a later editor session on a different day within 30d / adopters who subsequently had at least one eligible editor session | Good: >=40%; bad: <10% after 30 eligible adopters | One-time novelty becomes a remove/redesign candidate |
| Feature discovery rate | eligible installs whose first use occurs within first 3 eligible editor sessions / eligible installs with 3 sessions or an export | Core good: >=50%; bad: <20% | Improve placement/onboarding before calling the feature unwanted |
| Feature success rate | feature actions that reach their declared truth-table success within the allowed window / committed actions for that feature | Good: >=85% for deterministic edits; bad: <70% | Fix mechanics or misleading affordance |
| Undo rate per feature | unique action_ids undone within 5 min and not redone within 30 sec / editor_action_committed + editor_drag_completed for feature_id | Healthy is contextual, often 2-15%; bad: >30% with low redo, or 2x editor median | Investigate interaction/default; high undo plus low success supports removal |
| Redo-adjusted regret rate | undos not followed by redo within 30 sec / all actions for feature_id | Good: <10%; bad: >20% | Distinguishes exploration from genuine correction |
| Edit-to-export ratio | meaningful human editor actions between prior receipt/project creation and export_completed / exports; report median and P90 | Good working band: P50 5-50; bad: P50 >100 with low completion, or 0 with poor output survey | Too high means friction/agent cleanup; zero plus low quality means under-editing |
| Agent-to-human edit ratio | agent-authored changed objects / (agent-authored + human-authored changed objects) at receipt; derive from edit_origin_rollup | No universal target; healthy evidence is exports across agent-heavy, mixed, and human-heavy cohorts | Decide whether to automate more or strengthen human controls |
| Session depth | per eligible project session: unique meaningful feature_ids, total meaningful actions, and completed outcomes; wall number is median unique features among activated sessions | Good: >=3 unique features and >=1 success; bad: median 1 with no export | Detect shallow trial and guide onboarding |
| D1 retention | activated installs with meaningful_active_day during hours 24-48 after activation / activated installs old enough | Diagnostic only: good >=20%, bad <10% | Look for immediate refinement behavior, not daily-video expectation |
| D7 retention | activated installs with meaningful_active_day on days 6-8 / activated installs old enough | Good hypothesis >=25%; bad <12% | Primary early habit measure for founders publishing weekly |
| D30 retention | activated installs with meaningful_active_day on days 23-37 / activated installs old enough | Good hypothesis >=20%; bad <8% | PMF/repeated-production signal |
| Project-based retention | activated installs that either revisit a project meaningfully or export any project again within 30d / activated installs old enough | This is the canonical retention measure; good >=35%, bad <15% | A creative tool should measure return-to-work, not app opens |
| Crash-free session rate | eligible sessions with no fatal application_exception/process_gone and a clean or synthesized end / eligible sessions | Good >=99.5%; bad <98.5%; any new fatal in beta alerts immediately | Halt rollout, fix, and verify by release |
| Launch success rate | ui_ready / app_launch_started, grouped by release and fresh/upgrade | Good >=99%; bad <97% | Roll back or fix provisioning/startup |
| Preview stall rate | play sessions with zero preview_stall / preview_play_session | Good >=98%; bad <95%, or P95 seek >250 ms | Proxy/cache/playback work |
| Time in app versus rendering | sum union(render running intervals intersect foreground intervals) / sum foreground_ms. Report foreground non-rendering = foreground_ms - intersection | Good: rendering share <20%; bad >40% unless user intentionally backgrounded | Optimize/cache render and stop teaching render-to-preview |
| Render cache hit rate | sum n_cached / sum n_scenes from render_proxy_summary, split first versus repeat render | Good repeat >=70%; bad repeat <40% | Fix cache identity/invalidation or explain why edits invalidate |
| Feedback delivery rate | feedback with delivery=sent / feedback_submitted | Good >=99%; bad <95% | Add retry and truthful delivery state |
| Request conversion | capability_id requests that ship and are then adopted by requesting cohort / requesting installs eligible after release | Good: >=30% adoption and improved export; bad <10% | Validate that roadmap demand translated to value |

Retention means meaningful project work: an export, a successful content-producing
agent turn, or a human editor commit. An app open is not retention. This tool
serves project-based, often weekly creation, so D7 and 30-day project return are
more honest than forcing a daily habit.

## 5. How to know what to BUILD

Use one capability taxonomy for all evidence, for example caption_editing,
background_removal, stock_search, collaboration, or batch_variants. Classification
happens locally or manually; raw prompts, searches, paths, and feedback text do
not go to product analytics. A weekly request_evidence_rollup joins the signals.

    user/agent intent
          |
          +-- explicit request -------------------------- strong
          +-- capability/tool missing ------------------- strong
          +-- repeated successful-looking workaround ---- medium/strong
          +-- search/funnel miss ------------------------- medium
          +-- survey answer ------------------------------ supporting
          |
          +--> distinct external users + affected exports + recurrence
                         |
                         +--> build / research / watch / reject

| Rank | Signal and real detection point | Minimum before acting | Why / action |
|---:|---|---|---|
| 1 | Repeated manual workaround that ends in success: stable sequences of feature_ids, or Bash heavy-media routing at server/agent_runner.py:128 followed by a produced asset/export | 5 external installs and 10 sequences; 3 installs if it blocks first export | Strongest revealed demand: users paid time and still found a route. Build the shortest first-class operation |
| 2 | Agent capability missing: unknown tool, missing local pack, missing provider, denied route-around from agent_tool_finished and capability_requested | 3 external installs or 5 blocked turns; act immediately on a deterministic first-export blocker | The agent tried to fulfill real intent and could not. Distinguish missing product capability from missing user credential |
| 3 | Explicit feature feedback classified at server/feedback.py:153, linked to current context and outcome | 3 independent external requesters plus either behavioral friction or 5 more interview confirmations | Explicit demand is strong but proposed solutions can be wrong. Interview around the job, then test the smallest solution |
| 4 | Repeated @-mention/search miss: asset_mention_query result_count=0 or repeated sent_plain, from web/src/chat/ChatPanel.jsx:58 | 5 external installs and 10 misses for the same local topic | Build indexing/filtering or support the missing asset class; never collect the query text |
| 5 | Agent uses generic Bash/file sequences instead of registered tools, recorded at server/agent_runner.py:1969 | Same sequence family in 10 successful turns across 5 installs | Productize a proven route; do not productize one-off agent exploration |
| 6 | Abandoned funnel: create/auth/import/turn/export starts without its terminal success | At least 50 eligible attempts and a step loses >25%, or 10 observed sessions | Usually fix the existing path before adding a feature |
| 7 | High editor no-op, undo, or save-block sequence that users route around with another feature | 30 eligible adopters and regret >2x median | Build a safer interaction/default, not necessarily a new capability |
| 8 | Contextual micro-survey after a repeated failure, export, or feature repeat | 20 responses or 10 yes responses from distinct eligible users, backed by behavior | Use as supporting evidence; response bias prevents survey-only roadmap decisions |
| 9 | General idea/request with no behavioral evidence | 10 independent requesters or a design-partner commitment | Research queue, not build queue |

For each candidate, compute:

    demand_score =
      5 * distinct_blocked_export_users
    + 3 * distinct_successful_workaround_users
    + 2 * distinct_explicit_requesters
    + 1 * repeated_search_miss_users

The score ranks research; it does not auto-authorize a build. Before building,
read five bounded traces, interview at least three affected users when available,
name the existing workaround, and define the success metric. A candidate wins
when it is repeated across users, affects activation/export, and the proposed
capability removes measurable time, cost, or failure.

## 6. How to know what to REMOVE

A feature is not dead merely because nobody found it. Separate eligibility,
exposure, first use, repeat use, success, regret, outcome lift, and whether the
agent/system depends on it.

    eligible -> exposed -> found -> used -> succeeded -> repeated -> export
         \          \           \         \          \          \
          no data    placement    value     mechanics   habit      outcome

The deletion test for feature F is:

1. The feature had at least 100 eligible external installs or 300 eligible
   sessions across at least eight weeks and two releases.
2. Adoption is below 2%, or adoption is below 5% and 30-day feature retention is
   below 10%.
3. Its success rate is below 70%, redo-adjusted regret is above 20%, or adopters
   show no export/retention lift after matching on project complexity.
4. No agent tool, renderer path, accessibility path, migration, or core schema
   contract depends on it. Internal/developer-only use is explicitly excluded.
5. Five traces and at least three eligible-user conversations reveal no hidden
   discovery problem or must-have niche.
6. Removal has a migration/fallback, and the feature can first be hidden for one
   release. No support spike, workaround explosion, or export regression appears.

All six must pass to delete. With fewer than 20 external eligible users, telemetry
cannot justify deletion; hide experimental UI, keep the compatible schema path,
and gather evidence. Core safety features, import compatibility, autosave,
undo/redo, and receipt validation are judged by failures prevented, not clicks.

Weekly remove report:

| Candidate | Evidence | Default action |
|---|---|---|
| Never eligible/exposed | Instrumentation or reachability bug | Fix measurement, not delete |
| Exposed but undiscovered | Low discovery, normal success among adopters | Relocate/teach |
| Used once, never again | Adoption present, retention <10% | Interview, simplify, then hide test |
| High undo/no-op | Regret >2x editor median | Redesign defaults/mechanics |
| Developer-only | internal=true dominates and <3 external users | Hide from stable UI unless operational |
| Code path never exercised | zero external and agent calls for 90 days, contract scan says unreferenced | Delete after compatibility review |
| Low use, high export lift | Small specialist cohort succeeds | Keep, possibly tuck away |
| Low use, no lift, support burden | Full six-part test passes | Hide one release, then remove |

## 7. Feature-level truth table

Adoption always divides by eligible, exposed external installs, not every app
open. Success is the product result after the action. Abandonment is intent
without that result or one-time use with no repeat opportunity taken. These IDs
are the bounded feature_id enum used throughout the catalog.

| Feature ID | Existing feature and code evidence | Adoption | Success | Abandonment |
|---|---|---|---|---|
| asset.browser | Folder navigation and shared browser at web/src/components/FolderBrowser.jsx:25 | opened folder browser / projects with assets | asset previewed or added within session | opened, navigated, then no preview/add |
| asset.preview | Shared image/video/audio/text modal at web/src/components/AssetModal.jsx:22 | distinct users opening a media item / browser users | added to doc or intentionally closed after meaningful playback | immediate close under 2 s, repeated on same item |
| asset.upload | Picker/drop upload at web/src/App.jsx:1302 and web/src/studio/Studio.jsx:585 | import starters / projects with no usable asset | asset_import_finished and later used | failed import or unused after 7d |
| asset.add_main | Image/video to cuts lane at web/src/studio/Studio.jsx:403 | users with eligible media who add a main cut | cut survives to export | add undone/deleted before save/export |
| asset.add_overlay | Image/video to overlay lane at web/src/studio/Studio.jsx:493 | eligible users adding an overlay | visible overlay survives to export | add undone/deleted or never visible at playhead |
| editor.cut_trim | Edge trim at web/src/studio/StudioTimeline.jsx:368 | cut editors using trim / cut editors | valid net duration change saved/exported | drag no-op/cancel or undo without redo |
| editor.cut_reorder | Body drag reorder at web/src/studio/StudioTimeline.jsx:217 | multi-cut editors reordering | new order saved and previewed/exported | drag snaps to same index or immediate undo |
| editor.split | Cut/overlay/music/narration split at web/src/studio/Studio.jsx:351 | eligible selections split / eligible users | two resulting items both survive next save | blocked playhead, immediate rejoin/delete/undo |
| editor.duplicate | Selected-cut duplicate at web/src/studio/Studio.jsx:392 | multi-use among cut editors | duplicate materially edited or exported | duplicate immediately deleted/undone |
| editor.delete | Selection-aware delete at web/src/studio/Studio.jsx:376 | users with deletable objects | intended object removed and save succeeds | blocked last-cut or immediate undo |
| editor.transition | 13 rendered transition choices at web/src/studio/model.js:7 | multi-cut users selecting non-hard transition | export succeeds and transition remains | reset to hard cut/undo or render warning/failure |
| editor.speed | Typed/scrubbed speed plus presets at web/src/studio/propertySchema.js:65 | video-cut editors changing speed | changed speed survives export | reverted/undone or audio/render failure |
| editor.clip_transform | Main clip position/scale at web/src/studio/propertySchema.js:73 | eligible clip editors using transform | previewed and exported transform | immediate reset/undo or preview mismatch report |
| editor.crop | Source-pixel crop at web/src/studio/propertySchema.js:74 | eligible visual clips cropped | proxy/export succeeds and crop retained | render failure, reset, or undo |
| editor.canvas | Four output presets at web/src/studio/model.js:28 | users changing default 9:16 | selected size matches export artifact | changed back or export fails |
| editor.background | Color/image project background at web/src/studio/Studio.jsx:578 | eligible editors setting background | visible in preview and export | cleared/replaced immediately |
| editor.text_add | Text overlay add at web/src/studio/Studio.jsx:485 | editor users adding text | non-placeholder text survives export | placeholder left, deletion, or undo |
| editor.text_style | Content, size, color, position, box at web/src/studio/propertySchema.js:106 | text adopters editing each field_id | styled text survives and export succeeds | field reverted/undo or text deleted |
| editor.overlay_timeline | Overlay move, trim, and track at web/src/studio/Studio.jsx:534 | overlay users directly manipulating timeline | net placement survives save/export | drag no-op, auto-float surprise, or undo |
| editor.overlay_canvas | Canvas drag for overlay position at web/src/studio/Studio.jsx:558 | overlay users dragging on canvas | position survives export | no-op/immediate undo/mismatch report |
| editor.auto_arrange | Greedy overlay arrange at web/src/studio/Studio.jsx:511 | projects with 2+ overlays using Arrange | overlap resolved and export succeeds | nothing-to-arrange no-op or immediate undo |
| editor.overlay_audio | Video-overlay audio mix at web/src/studio/propertySchema.js:97 | video-overlay users enabling/changing mix | audible preview and successful export | disabled/reverted or no-audio warning |
| editor.keyframes | Add/update/remove motion keyframes at web/src/studio/Studio.jsx:607 | eligible overlay users with keyframes | keyframed motion previewed/exported | all keyframes removed/undo or ignored dimension |
| editor.music | Music bed/regions, move, trim, gain, fades at web/src/studio/propertySchema.js:129 | projects with music assets using bed | audible retained music in export | removed/muted/undo or audio failure |
| editor.narration | Narration select/move/trim at web/src/editor/interp.js:817 | projects with narration segments edited | timing survives and export succeeds | removed/undo or sync failure |
| editor.sfx | SFX add/move/volume at web/src/editor/interp.js:810 | projects with audio assets using SFX | cue survives and is audible in export | removed/undo or cue outside duration |
| editor.audio_split | Music/narration split at web/src/editor/interp.js:784 | eligible audio-region users splitting | both segments retained/exported | immediate delete/undo |
| editor.property_scrub | Drag-to-adjust versus click-to-type at web/src/studio/StudioInspector.jsx:31 | numeric-field users by input method | committed valid value with one undo step | bare/no-op drag, rejection, immediate undo |
| editor.selection_inspector | Type-derived inspector at web/src/studio/model.js:84 | editors selecting each object type | property edit or preview inspection follows | rapid selection churn with no action |
| preview.source | Live source-mode WYSIWYG at web/src/studio/StudioPreview.jsx:1 | editor sessions with source play/seek | low-stall review followed by edit/export | mode abandoned after stall/error |
| preview.render | Composed final MP4 preview at web/src/studio/StudioToolbar.jsx:87 | sessions with current render opening mode | meaningful playback/full review | immediate return to source/error |
| preview.playback | Timeline transport at web/src/studio/StudioTimeline.jsx:270 | eligible sessions starting playback | at least 3 s or reaches intended end | stop under 1 s due to stall/error |
| preview.scrub | Ruler scrub and seek chaser at web/src/studio/StudioTimeline.jsx:292 | editor sessions with scrub | P95 seek under 250 ms and final seek lands | abandoned/stacked seek or frozen frame |
| editor.timeline_zoom | Buttons/range zoom at web/src/studio/StudioTimeline.jsx:275 | long/complex timeline users changing zoom | edit follows at chosen scale | repeated oscillation/no action |
| preview.track_visibility | Per-track hide/show at web/src/studio/Studio.jsx:54 | multi-track editors isolating a track | edit/review completes then track restored | hidden accidentally through session with no action |
| editor.panels | Resize/collapse agent, inspector, timeline at web/src/studio/Studio.jsx:440 | editor users changing default layout | stable layout for >2 min with meaningful action | repeated resize oscillation/reopen |
| editor.history | Undo/redo stack at web/src/studio/Studio.jsx:196 | editors using undo or redo | correction retained or redo restores exploration | deep undo ending dirty abandon |
| editor.save | Manual, autosave, pre-agent, pre-export save at web/src/studio/Studio.jsx:214 | all dirty editor sessions | latest doc accepted and current on disk | rejected/blocked or dirty exit |
| export.final | Toolbar Export at web/src/studio/StudioToolbar.jsx:97 and receipt commit at lib/project.py:614 | eligible projects with export_clicked | export_completed current receipt | abort/fail/timeout/stale before handoff |
| agent.turn | Streaming Claude Agent SDK turn at server/agent_runner.py:1924 | authenticated projects sending a turn | useful-turn result and/or export | failure, interrupt from frustration, inert success |
| agent.model_choice | Persisted model selector at web/src/chat/useAgentChat.js:37 | agent users selecting non-default model | better success/cost/latency for chooser | immediate switch back or degraded outcome |
| agent.threads | New/switch/revive chat at web/src/chat/useAgentChat.js:68 | projects with 2+ conversation needs | correct session resumes and new turn succeeds | switch then immediate return/context failure |
| agent.mentions | Structured @ asset attachment at web/src/chat/ChatPanel.jsx:46 | turns eligible for asset reference that select one | all references resolved and turn uses asset | zero results, pruned token, 422, or plain-path workaround |
| agent.ask_user | In-process clarification at server/agent_runner.py:1333 | turns showing question / turns needing clarification | answered and turn later succeeds | long wait, stop, dismiss, repeated same topic |
| agent.confirm | Tool permission confirmation at server/agent_runner.py:437 | flagged calls shown / flagged calls | informed allow/block and turn continues safely | long wait, turn stop, repeated prompt |
| agent.api_key | Just-in-time provider key request at server/agent_runner.py:1370 | missing-key turns showing prompt | key saved and retry succeeds | declined/save failure/retry failure |
| agent.capability | Lazy local pack request/install at server/agent_runner.py:1438 | missing-pack turns showing prompt | installed and retry succeeds | decline/install failure/retry failure |
| agent.render_tool | In-process render tool routed through RenderJobStore at server/render_jobs.py:71 | turns needing render that call it | job done and artifact attributed to turn | Bash route-around, fail, supersede, timeout |
| agent.media_op | In-process heavy media operation at server/render_jobs.py:94 | eligible heavy-op intent using it | tool succeeds and output used | Bash route-around, output unused, failure |
| agent.stop_resume | Interrupt while preserving context at server/agent_runner.py:2009 | busy turns stopped | next turn resumes successfully | no active client, lost context, repeat stop |
| agent.live_adopt | Agent edits adopted into open Studio at web/src/studio/Studio.jsx:297 | in-editor turns changing doc | adoption succeeds and changes survive | local conflict, undo, save race |
| agent.activity | Persisted tool/files/skills summary at server/activity.py:220 | project users opening Activity | inspection leads to trust/action/feedback | tab never used or opened without dwell |

## 8. The developer's operating dashboard

The dashboard is a decision console, not a gallery of charts. Every screen has
an owner (the developer), a review cadence, and an explicit next action.

| Screen | Contents | Decision it drives |
|---|---|---|
| Wall / product pulse | five wall numbers by release; sample sizes; 7/30-day trend; external-only filter | Is the product delivering reliable value, and which wall number gets this week? |
| Activation funnel | fresh install -> ui ready -> auth -> project -> content ready -> export start -> receipt -> download; median/P90 time between steps | Fix the single largest loss or delay |
| Reliability | launch/export/render/turn/tool success, crash-free sessions, new issue fingerprints, recovery-on-retry | Halt rollout, repair top failure class, or close incident |
| Agent economics | useful-turn rate, tool success/latency/retry by tool, cost by model, cost per export, route-arounds, missing capability | Change model/default/tool description; build or remove capability |
| Editor feature matrix | eligibility, discovery, adoption, success, retention, undo/regret, export lift for every truth-table ID | Promote, redesign, hide-test, or retain a specialist feature |
| Media and render lab | imported codec/container/HDR/resolution matrix, probe/decode failures, preview seek/stall, scene timing, cache hit, assemble timing | Add a real-media fixture/fallback or optimize the slow stage |
| Requests / build queue | request_evidence_rollup ranked by blocked exports and distinct users; linked feedback count; representative trace IDs | Research/build/watch/reject each capability |
| Removal queue | deletion-test gates for low-use features, internal-share warning, agent/system dependency check | Hide for one release or keep; never auto-delete |
| Cohorts / lifecycle | activation by release/auth/model/media; D7/D30 meaningful return; project revisits, repeat exports, dormant stage | Onboarding versus core value versus retention work |
| Data quality | event delivery, unknown/rejected events, missing session ends, cardinality, internal share, denominator coverage | Trust/fix instrumentation before reading product conclusions |

Daily digest, sent only when there was external activity:

- five wall numbers for yesterday and trailing 7 days, with numerator/denominator;
- every new fatal and external launch/export failure, grouped by failure class;
- first exports with time-to-value and cost, plus first-time codec/HDR profiles;
- failed agent turns/tools, route-arounds, missing keys/packs, and recovered retries;
- new feedback/request topics with a pointer to the intended feedback record;
- telemetry rejected/dropped counts and any metric with denominator undercoverage.

Weekly product review:

- activation funnel movement versus prior four weeks and current release;
- top three build signals with distinct-user/blocked-export counts;
- top three removal/redesign candidates with all six deletion gates;
- feature matrix movers: discovery, repeat, regret, and export lift;
- media/render Pareto: top failure profiles, P95 seek/render time, cache rate;
- cohort retention, repeat exports, and dormant-project stage;
- one written decision per reviewed item: build, fix, research, watch, hide, keep,
  or reject, with the metric that would reverse it.

Automatic alerts:

| Condition | Alert |
|---|---|
| Any new external fatal fingerprint in beta/stable | Immediate, with release/layer/session and issue link |
| Launch success below 97% over last 20 launches or 2 consecutive failures | Immediate |
| First-export failure for 2 distinct external installs, or 3 of last 10 attempts | Immediate |
| Crash-free sessions below 98.5% over rolling 50 sessions | Immediate |
| Core agent tool below 85% over 30 calls, or one failure class triples | Daily urgent |
| P95 source seek above 500 ms or stall-free play below 95% over 30 plays | Daily |
| Feedback stored but undelivered for over 1 hour | Immediate operational |
| Rejected-schema, sensitive-key, or dropped critical event count above zero | Immediate data-quality |
| Cost per successful export rises over 25% week-over-week with no activation gain | Weekly |

## 9. Volume, cost, and sampling

### Realistic event math

The following is a productive beta session, not a worst-case debug session:

| Source | Assumption | Events |
|---|---|---:|
| launch/auth/project lifecycle | one launch, ready/end, open, state changes | 8 |
| asset ingest/browse | two assets, one probe each, browse/add | 8 |
| agent | 2 turns x (3 turn events + 10 tools x 2 start/finish) | 46 |
| editor | 25 semantic commits + 10 completed drags + 6 undo/save + rollups | 46 |
| preview | four play intervals plus seek/stall session rollups | 8 |
| render/export | one queue/status/proxy/assemble/export/inspect lifecycle | 14 |
| feedback/errors/data health | normally only session health | 1 |
| Total |  | 131 |

Use 135 events/session as the beta planning average. A heavy production session
with four turns, 20 tools/turn, 60 commits, and two exports is about 280 events.
A browsing-only session is about 15. Raw pointer moves, animation frames, render
polls, and individual seek requests are not in these totals because they are
locally aggregated.

Current PostHog pricing lists 1,000,000 Product Analytics events per month free,
then $0.00005/event for 1-2 million, with decreasing tier rates:
https://posthog.com/pricing (checked 2026-08-05).

| Scale | Arithmetic | Monthly events | Product Analytics cost |
|---|---|---:|---:|
| private beta | 100 MAU x 8 sessions x 135 | 108,000 | $0 |
| growing beta, unsampled | 1,000 MAU x 12 sessions x 170 | 2,040,000 | about $51.37: $50 for event 1M-2M + $1.37 for 40k in next tier |
| post-beta, unsampled | 10,000 MAU x 15 sessions x 180 | 27,000,000 | about $849.90: $50 + $445.90 + $354 |

At 135 events/session the free tier breaks at roughly
1,000,000 / 135 = 7,407 sessions/month. At 12 sessions/MAU, that is about 617
MAU. At the heavy 280-event profile, it breaks at 3,571 sessions/month.

Storage/ingestion cost is not the only limit. High-cardinality raw data makes
queries untrustworthy long before it becomes expensive. Apply these controls:

| Data class | Beta | Post-beta policy | Why |
|---|---|---|---|
| launch, auth, project, export funnel | 100% | 100%, never sample | Denominators and North Star must reconcile |
| failures, fatal/process, schema violations | 100% | 100%, never sample | Rare, actionable, release-critical |
| feedback metadata and delivery | 100% | 100%, never sample | Every user report matters |
| agent turn summary/cost | 100% | 100%, never sample | User-paid economics and turn reliability |
| core tool terminal outcome | 100% | 100%; collapse start into terminal event, upload orphan starts as timeout | Keeps exact success/latency at half the tool volume |
| read/search/file tool successes | 100% during beta | per-turn counts plus 20% deterministic turn sample; failures 100% | Preserve demand/reliability without file-operation noise |
| editor semantic commit/undo | 100% | 100% until 10k MAU; then deterministic 50% sessions plus 100% sessions with export/failure/feedback | Needed for feature decisions and trace linkage |
| live drag frames | never upload | one editor_drag_completed summary | Frames are cost without a product decision |
| selection/shortcut/panel/zoom | session rollup | session rollup, 20% deterministic sessions | Discovery diagnostics, not core outcomes |
| preview seeks/frame cadence | session p50/p95/max rollup; cadence 10% | rollup, deterministic 10% sessions; errors/stalls 100% | Performance distribution without event storms |
| scene render success | 100% beta | per-render aggregate plus deterministic 10% successful scenes; failed scenes 100% | Cache/stage data stays representative |
| media probe profile | 100% | 100% | Low volume and essential for format support |

Deterministic sampling hashes install_id + session_id + schema_version so a
whole session is kept or dropped; it never samples individual events in a way
that breaks sequences. Every event has an event_id for retry dedupe. Cap uploaded
semantic detail at 500 events/session and emit one capped rollup; critical events
bypass the cap. Track sampling rate as N(percent) in the common ingestion
metadata so estimates can be weighted.

## 10. Implementation architecture

The fewest-moving-parts design reuses the four strong choke points already in
the repository and adds one closed event contract.

    Electron lifecycle ----\
                            \
    React semantic events ---> event contract -> backend batch -> analytics.py
    recorder.js adapter ----/        |                 |
                                    |                 +-> PostHog
    Python routes/agent/render ------+                 +-> delivery health
           |             |
           |             +-- render_jobs._set -> render/export lifecycle
           +-- agent SDK stream -> turn/tool correlation and rollups

### One taxonomy

Add analytics/event_schema.json as the source of truth. Each event declares
properties and E/B/N/I type, required/optional status, priority, sampling class,
owner question, decision, and forbidden sensitive alternatives. Python, React,
and Electron load this same file; they do not keep three enum copies. It is
packaged as application data, not generated at runtime.

Extend server/analytics.py:145 with validate_event and capture_many. Validation
drops unknown properties, rejects wrong types/unknown events, increments a local
data-quality counter, and then uses the existing scrubber and PostHog client.
Add one POST /api/telemetry/events beside the current error endpoint at
server/app.py:1016 so the renderer sends small batches through the same gate.
The renderer never embeds a second product analytics client.

Add web/src/productAnalytics.js as the thin batching/session adapter. It owns
session ID, deterministic sampling, action IDs, timers, pagehide flush, and
session rollups. Components call a small event/finishAction API; feature logic
stays in the pure core. There is no analytics decision logic in JSX.

Modify web/src/debug/recorder.js:136 so semantic dbg.event calls fan out:

- the existing private recorder receives the full local diagnostic only when on;
- productAnalytics receives only schema-listed summaries whether recording is
  on or off;
- console, keystroke, click coordinates, pointer moves, and raw errors remain
  recorder-only and never fan out.

The existing summarizeDocChange at web/src/studio/model.js:501 is too structural
to identify every feature. Add a pure classifyDocChange(prev, next, actionHint)
beside it, tested in model.test.js. Thin handlers supply bounded hints such as
split, trim, property field ID, or asset drop. Drag components open one action
at threshold crossing and finish it on pointerup, so no per-frame upload.

### Agent, render, and export wiring

- At server/agent_runner.py:1924 create a turn_id and timer. At the SDK loop at
  line 1960 correlate ToolUseBlock IDs to ToolResultBlock IDs, recording bounded
  tool_id, attempt, start, finish, outcome, and class. Keep activity.py's raw
  local target log separate; never relay target, input, output, text, or prompt.
- Return/use the existing TurnResult at server/agent_runner.py:1979 and emit one
  terminal turn event with is_error, num_turns, total_cost_usd, tool rollup, and
  before/after project artifact counts.
- At server/agent_runner.py:370 emit permission/route-around outcomes from the
  pure ToolDecision. The heavy-media detector at line 128 becomes the explicit
  unmet-capability signal.
- Extend RenderJobStore records with monotonic timestamps and stage data. The
  single _set at server/render_jobs.py:309 emits transitions exactly once.
  VideoCompose already returns scene cache/HDR summary at
  tools/video/video_compose.py:1559; propagate bounded numbers into the job,
  never proxy paths or hashes.
- After publish_final_render has written the receipt at lib/project.py:614 and
  RenderJobStore confirms published=true, emit export_completed from the server
  with probed artifact metadata and first-export flag. Do not emit from the
  button or the UI poll. Treat application watermark as a server-known false;
  do not claim generated source media contains no visual watermark.

Electron loads the same schema for pre-backend launch/provision/fatal events. It
queues bounded launch records until backend health, then posts them to the batch
endpoint. Only the existing direct fatal path remains for cases where the
backend never becomes reachable.

### The one contract test

Keep one required cross-stack contract:

tests/contracts/test_analytics.py::test_closed_event_contract_and_golden_value_path

It loads analytics/event_schema.json and proves:

1. every registered emitter event/property has a declared type and decision;
2. unknown events/properties, dynamic names, forbidden sensitive keys, raw
   paths/prompts/filenames/tool I/O, NaN, and unbounded strings are rejected;
3. a golden fresh-install -> auth -> project -> agent/tools -> human edit ->
   render -> receipt path emits one deduplicated critical lifecycle, and
   export_completed cannot exist without a valid current receipt;
4. disabled/unavailable analytics changes no product behavior.

Unit tests remain near pure classifiers/rollups, but this is the single contract
that prevents taxonomy drift across Electron, React, Python, and the dashboard.

## 11. Phased plan

### P0: three days — make value, cost, and failure measurable

| Work | Planned files | Test | Verifiable success |
|---|---|---|---|
| Closed schema and batch gate | analytics/event_schema.json; server/analytics.py; server/app.py; web/src/productAnalytics.js | extend tests/contracts/test_analytics.py | Unknown/sensitive data is rejected; a batch reaches the existing sink once |
| Real launch/session | desktop/main.js; web/src/main.jsx | desktop launch fixture plus contract golden path | app_launch_started, ui_ready, app_session_ended reconcile for a packaged test launch |
| North Star and render terminal state | server/render_jobs.py; lib/project.py; tools/video/video_compose.py | receipt/export branch in contract test | One export_completed occurs only after current receipt; failure/supersede never activates |
| Agent turn/tool/cost | server/agent_runner.py; server/activity.py | agent runner message fixtures | Every tool use reaches one bounded terminal outcome; TurnResult cost joins the turn |
| Editor semantic actions | web/src/debug/recorder.js; web/src/studio/model.js; web/src/studio/Studio.jsx | model.test.js action classifier cases | commit/drag/undo/save are visible with feature IDs; zero live-frame events upload |
| Five-number wall | PostHog saved dashboard definitions tracked in docs/analytics-dashboard.md | query numerator/denominator fixtures | Developer can read all five wall numbers with external-only filter |

P0 success is one synthetic and one manual packaged journey whose counts
reconcile exactly: one install, session, auth, project, turn, tool outcomes,
human edit, render, receipt-backed export, and clean close. Inject one failure
per layer and verify its bounded class.

### P1: two weeks — learn feature demand and real media reliability

| Work | Planned files | Test | Verifiable success |
|---|---|---|---|
| Full feature truth table | Studio.jsx; StudioTimeline.jsx; StudioPreview.jsx; StudioInspector.jsx; propertySchema.js; productAnalytics.js | pure feature/action tests plus existing component wiring tests | Eligibility, discovery, success, abandonment, undo, and repeat populate for every Section 7 ID |
| Preview rollups | StudioPreview.jsx; productAnalytics.js | synthetic seek/stall clock tests | P50/P95 seek and stall-free play reconcile without raw seek/frame uploads |
| Media profiles | server/app.py; server/editor.py; tools/video/_shared.py | fixtures for H264, HEVC/HDR, ProRes, image, audio, corrupt | One bounded probe profile or typed failure per import |
| Build/remove evidence | agent_runner.py; ChatPanel.jsx; feedback.py; analytics queries | classifier/sequence fixtures | Weekly build and removal reports name sample size and linked outcome |
| Dashboard/digests/alerts | analytics-dashboard definitions and notification job/config | threshold fixtures | Daily digest and every Section 8 alert can be fault-injected |
| Delivery/data health | analytics.py bounded queue/counters | offline/retry/dedupe test | Critical event survives restart and arrives once; data-quality dashboard reconciles |

### P2: later — only after enough external data

- matched-cohort export lift for features and agent behaviors;
- local/manual request-topic classifier evaluation;
- deterministic successful-scene and editor-session sampling at scale;
- experiment exposure events only for a named decision;
- capability request-to-shipped-feature conversion;
- artifact quality score only if a validated human rating target exists.

P2 is not a warehouse, replay system, raw prompt lake, or universal tracing
platform. Build it only when a P0/P1 question cannot be answered accurately.

## 12. What I deliberately would NOT collect

| Do not collect | Why it looks useful | Why it is not worth collecting | Lower-fidelity answer |
|---|---|---|---|
| Video frames, thumbnails, audio, transcripts | Debug quality and understand content | Transmits the creative work and is costly; aggregate behavior cannot judge taste reliably | codec/HDR/duration/profile, artifact outcome, explicit attached debug report |
| Full prompts, agent responses, thinking, custom question answers | Mine requests and agent failures | Creative/business content, extreme cardinality, and ambiguous intent | local intent/capability enum, character count, tool path, outcome |
| Raw tool arguments/results or Bash commands | Reconstruct what the agent tried | Contains paths, content, secrets, and incidental commands | registered tool/family, route-around classifier, outcome, duration, attempt |
| Filenames, project names, folder paths, URLs, hashes | Correlate assets and projects | Names often reveal customer/content; raw hashes can correlate the same media | opaque per-install project ID, media extension/profile, reference kind |
| Full edit_decisions snapshots or field values | Rebuild every edit | Reconstructs the project and text; high payload and hard to query | feature/action ID, target type, changed fields/counts, before/after structural counts |
| Text overlay content, notes, captions, colors/positions at raw precision | Learn design choices | Content is sensitive and values do not answer roadmap questions at beta scale | field used, delta bucket, preset ID, export/undo outcome |
| Raw FFmpeg/provider stderr | Diagnose failures | Paths/metadata/content can appear and cardinality destroys grouping | locally classify stage/failure/exit bucket; exception sink gets scrubbed diagnostic |
| Session replay, screenshots, DOM snapshots | See confusion directly | A video editor exposes creative media and BYOK surfaces; expensive and unnecessary with semantic traces | bounded semantic event sequence plus explicitly sent debug report |
| Every click, hover, pointer move, drag frame, playhead frame, or seek request | Heatmaps and exact interaction | Massive volume, mostly layout noise, and no stable product decision | one completed action plus duration/distance/net change; session percentiles |
| Every render-status poll and progress tick | Detailed render timeline | Poll frequency measures implementation, not user experience | queued/running/terminal transitions and stage timers at _set |
| Clipboard contents or all keystrokes | Detect shortcuts and pasted setup | Captures secrets and prose | allowlisted shortcut ID and input method only |
| API keys, OAuth codes/tokens, email, IP-derived geography | Segment/setup debugging | Secret or identity data adds no build/remove decision here | auth method/outcome/provider family; has_email boolean in feedback |
| Exact device model, free-disk path inventory, installed-app list | Diagnose compatibility | High-cardinality fingerprinting without an established decision | OS major, arch, relevant binary/capability availability, disk-full failure |
| Generic page views and app-open retention | Easy dashboard activity | Inflates engagement without creative value | meaningful active day, project revisit, successful export |
| Model token counts when only cost is actionable | Agent optimization | Provider accounting fields can drift and invite vanity analysis | SDK-reported total cost, duration, turns, outcome, model ID |
| Raw feature flag/exposure events with no experiment | Future flexibility | Noise with no hypothesis | add only when a named experiment and decision exist |
| Metrics no developer reviews | Completeness | An unread number cannot change the product | every schema entry requires question, decision, owner, and saved query |

The exception is an explicitly submitted feedback/debug attachment: the user is
choosing to send that content through the feedback channel. Product analytics
still receives metadata only.

## 13. Open questions for the human

1. Should activation be receipt-backed export_completed, as proposed, while
   export_downloaded remains a stronger handoff signal, or must activation wait
   for the file download?
2. What beta promise should define a good first result: any current final.mp4,
   a passed final review, or a human quality rating?
3. For API-key users, are the initial $3 median / $10 P90 cost-per-first-export
   guardrails acceptable? What spend should automatically stop a turn?
4. Which three editor capabilities are core for the ICP and therefore require
   >=50% discovery, versus specialist features judged only on eligible cohorts?
5. At what expected beta scale should deterministic tool/editor sampling turn
   on: 1,000 MAU, a fixed monthly event budget, or only after the free tier?
6. May the developer manually classify full feedback into the bounded capability
   taxonomy, keeping the text in the feedback store, or should all classification
   remain user-selected?
7. Is app_watermark_free=true an honest product guarantee for every canonical
   export, while separately refusing to claim that third-party/generated source
   media has no embedded watermark?
8. Which contextual survey is worth the first interruption: post-first-export
   quality, failed-export blocker, or repeated-workaround demand?
