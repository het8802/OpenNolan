# Analytics, feedback, and crash maturity

STATUS: PLAN

## 1. Verdict up front

PostHog alone is not sufficient. Keep it for product events, funnels, surveys,
and metadata-only agent analytics; add Sentry as the single error inbox for
Electron, the renderer, and Python, including Electron Crashpad minidumps.
Do not send the same exception to both systems. The largest product gap is not
dashboard polish: there is no trustworthy path from an agent turn to a first
successful final export. `export_completed` is not emitted by the application,
so today's proposed North Star cannot be measured. Before any external beta,
ship explicit granular consent, a small versioned event contract, a durable
offline outbox, cross-layer crash capture, and truthful feedback delivery.

## 2. Current-state audit

### What exists

- Python has a thoughtful beginning: it suppresses telemetry in tests, honors
  the analytics setting, avoids constructing the client when disabled, and
  scrubs path-like fields before capture
  (`server/analytics.py:80`, `server/analytics.py:95`,
  `server/analytics.py:121`). It also marks internal machines and supplies
  release/environment properties (`server/analytics.py:51`).
- The backend catches route exceptions and sends them through the analytics
  wrapper (`server/app.py:421`). Renderer errors cross a local endpoint
  (`server/app.py:1005`), and React has boundary, `window.onerror`, and
  unhandled-rejection hooks (`web/src/main.jsx:7`).
- Electron directly posts a capped `desktop_error` event, scrubs path-like
  strings, and handles backend exit, renderer termination, uncaught exceptions,
  and unhandled rejections (`desktop/main.js:47`, `desktop/main.js:518`,
  `desktop/main.js:576`, `desktop/main.js:669`).
- Feedback is first written to local JSONL and then relayed best-effort
  (`server/feedback.py:47`, `server/feedback.py:87`,
  `server/feedback.py:153`). The debug recorder re-buffers after a local flush
  failure (`web/src/debug/recorder.js:200`).
- The editor is not devoid of instrumentation. There are 22, not 23,
  `dbg.event` call sites across `Studio.jsx` and `StudioPreview.jsx`, covering
  play/seek, commits, undo/redo, saves, renders, adoption, uploads, and selection
  (`web/src/studio/Studio.jsx:106`, `web/src/studio/Studio.jsx:179`,
  `web/src/studio/Studio.jsx:264`, `web/src/studio/StudioPreview.jsx:175`).
  They are local debug events, however, because `dbg.event` returns immediately
  unless recording is on (`web/src/debug/recorder.js:113`). There is no product
  analytics sink.
- The agent runner already normalizes tool use/results and result metadata such
  as turn count, cost, stop reason, and session ID
  (`server/agent_runner.py:752`, `server/agent_runner.py:832`). That is enough to
  instrument useful metadata without collecting prompts or tool payloads.

### What does not exist

- Only five product events are wired: `app_opened`, `app_first_run`,
  `project_created`, `auth_connected`, and `feedback_submitted`
  (`server/app.py:440`, `server/app.py:543`, `server/app.py:970`,
  `server/feedback.py:177`). `export_completed` exists in tests and documents,
  not application code (`tests/contracts/test_analytics.py:99`). The advertised
  six-event taxonomy therefore overstates the product by one event.
- `app_opened` currently means the local backend booted, not that a person saw a
  usable app (`server/app.py:440`). There is no stable app-session boundary.
- The render state machine stores failures as job status/error text, but emits no
  analytics and sends no render exception to a dedicated error service
  (`server/render_jobs.py:309`, `server/render_jobs.py:560`). There is no final
  export funnel.
- Agent turns and tool failures are uninstrumented. The route reports only a
  thrown non-auth runner exception (`server/app.py:1128`), while normal tool
  failures and unsuccessful result messages remain invisible.
- There is no Electron `crashReporter`, native minidump upload, source-map
  release pipeline, Python native-crash handoff, durable error queue, retry
  policy, issue grouping, release health, or alert ownership. Vite has no
  production source-map upload configuration (`web/vite.config.js:34`), and no
  Sentry SDK is present (`desktop/package.json:17`, `requirements-ui.txt:8`).
- Analytics shutdown is invoked when the user changes the setting
  (`server/app.py:988`), but not on the ordinary backend lifecycle. Renderer
  error posting is fire-and-forget and memory-capped
  (`web/src/api.js:63`). Electron's direct HTTP reporting also has no durable
  retry (`desktop/main.js:47`). Offline failures can disappear.
- A successful HTTP response makes the UI say feedback was sent, even when the
  server only stored it locally and returned `emailed: false`
  (`web/src/App.jsx:295`, `web/src/App.jsx:318`,
  `server/feedback.py:191`). The user cannot see delivery state or receive an
  acknowledgement.
- The debug attachment reads raw NDJSON (`server/feedback.py:69`). The recorder
  can capture target text and arbitrary console arguments
  (`web/src/debug/recorder.js:77`, `web/src/debug/recorder.js:139`). Although the
  modal asks permission (`web/src/studio/DebugReportModal.jsx:54`), its promise
  that nothing else from the project is included is stronger than the code can
  support. The existing redaction utility is optional at export time
  (`scripts/debug_session.py:35`, `scripts/debug_session.py:111`).
- One opt-out combines “usage & crash data,” is enabled by default, and calls a
  persistent UUID anonymous (`web/src/App.jsx:350`, `server/settings.py:25`,
  `server/settings.py:80`). It is pseudonymous, not anonymous.

### What industry practice actually implies

Desktop norms are not uniform. VS Code publishes granular telemetry levels, a
live telemetry log, and its event classifications; Docker Desktop exposes a
usage-statistics control; Obsidian's desktop app says it collects no telemetry.
That range means “desktop standard” is user visibility and control, not a
universal default-on policy. See [VS Code telemetry documentation][vscode-tel],
[Docker Desktop settings][docker-settings], and [Obsidian's privacy policy][obsidian].

For crash handling, Electron's own API uses Crashpad to capture native main,
renderer, and child-process crashes and explicitly names Sentry as a hosted
collector option. That is a materially different capability from a JavaScript
`desktop_error` event. See [Electron crashReporter][electron-crash] and
[Sentry's Electron SDK][sentry-electron]. Source maps must be uploaded with the
release before errors occur; they cannot symbolicate old events retroactively
([Sentry source-map guidance][sentry-maps]).

Commercial video editors do not publish useful internal event dictionaries.
Claims that there is a canonical NLE taxonomy would be invented. The defensible
creative-tool standard is to measure the work loop—import, edit, preview,
render, export—and its latency and failure points. Mux's public definitions for
startup time, seeking, and smoothness are useful adjacent practice for preview
quality, not proof of what Premiere or Resolve collect
([startup time][mux-startup], [smoothness][mux-smoothness]).

## 3. Gap table vs standard

“Standard” below means mature practice in the cited desktop, crash, agent, and
video-adjacent sources. “Right-sized target” is what a solo-funded, zero-user
product should actually build now.

| Area | Current state | Mature practice | Right-sized target | Priority |
|---|---|---|---|---|
| Consent | One default-on combined opt-out | Clear categories and inspectable controls | Three default-off upload categories | P0 |
| Identity | Persistent UUID called anonymous | Disclose pseudonymous IDs and deletion | Random install ID, session ID, delete/rotate path | P0 |
| Lifecycle | Backend boot called app open | User-visible session and release context | App ready/open/clean close, previous-exit state | P0 |
| Activation | Project creation only | Named activation funnel and time-to-value | First watermark-free export in 24h/7d | P0 |
| Editor | 22 debug events behind recorder gate | Low-volume semantic commits | Adapt commits/undo/render; never live drag/frame data | P0 |
| Agent | No turns or tool outcomes | Agent/tool traces with content off by default | Turn rollups, allowlisted tool outcomes, cost/latency | P0 |
| Render/export | Job status only; no export event | Start/outcome/stage/retry metrics | Separate final export from internal render stages | P0 |
| JS/Python errors | Best-effort PostHog events | Grouped, symbolicated, release-aware issues | One Sentry inbox across all three runtimes | P0 |
| Native crashes | Process-exit inference | Crashpad minidumps and symbols | Electron crashReporter -> Sentry | P0 |
| Offline | Memory queues/direct POST | Durable bounded queue with backoff | Local sanitized outbox; delete on opt-out | P0 |
| Feedback | Local store, best-effort relay, false “sent” state | Durable delivery state and acknowledgement | Pending/sent/failed UI, retry, report ID | P0 |
| Demand | No behavioral inference | Reach, repetition, friction, outcomes | Conservative scored signals, human interpretation | P1 |
| Reporting | No operating digest | Owned alerts plus scheduled trend review | Server-side daily beta digest; no client daemon | P1 |
| Advanced analytics | None | Warehouse, replay, experiments | Deliberately defer | P2/not now |

The comparison that drives the tool choice is:

```text
Need                   PostHog                  Sentry
---------------------  -----------------------  -----------------------
Funnels/cohorts        primary                  not the right tool
Surveys                primary                  not the right tool
Agent metadata trends  good                     errors/traces only
Grouped exceptions     possible, not our sink   primary
JS source maps         not configured here      release-native workflow
Native Electron crash  no Crashpad path here     Electron-supported path
Crash-free releases    not established here     release health
```

Two vendors add consent configuration, DPAs/subprocessor review, release IDs,
source-map/symbol upload, and modest operational cost. That cost earns its place
because losing native crashes or debugging minified frames consumes more solo
founder time than maintaining one focused crash SDK. PostHog remains the product
system of record; Sentry becomes the error system of record. Neither receives
raw creative content.

## 4. Event taxonomy

### Contract and naming

Use lowercase `object_action` names: stable object noun followed by past-tense
action. Use properties for variants and outcomes. Amplitude recommends a
consistent taxonomy and treating properties as the dimensions of an event,
rather than multiplying event names ([event taxonomy][amplitude-taxonomy],
[events and properties][amplitude-properties]).

Every event must answer a named product question, funnel, or alert; have an
owner and a saved query; use an allowlisted schema; and pass a forbidden-field
test. Unknown events/properties fail CI. Increment `schema_version` for a
breaking change. Never build event names from input.

Common properties:

- `schema_version`, `app_version`, `build`, `release_channel`, `os_major`,
  `arch`, `environment`, `internal`
- pseudonymous `install_id`, random-per-launch `session_id`, random `event_id`
- bounded enum/bucket properties declared for that event

Never include project names/paths, filenames, prompts, responses, tool
arguments/results, transcript text, media, timeline contents, email, API keys,
full stack traces, or raw stderr in PostHog. If cross-event project correlation
becomes indispensable, P1 may add a per-install HMAC of the internal project ID;
it remains personal data and must rotate on deletion. Do not send per-frame,
mouse-move, live-drag, or selection churn.

### The activation and value model

The North Star is the number and rate of new consenting installs that complete
their first watermark-free final export within 24 hours and within 7 days. It
measures delivered user value better than total exports, which a single power
user can inflate.

```text
first run -> auth -> project -> playable content -> final export -> success
                \          \-> agent path -----/
                 \------------ manual path ----/
```

Supporting metrics are median time to first export, project-to-export
conversion, render/export success, agent-turn success, and crash-free beta
sessions. “Watermark-free” and “final” must be server-derived facts, not a UI
click property.

### Concrete events to ship

| Event | Phase | Required event-specific properties |
|---|---|---|
| `app_first_run` | P0 | `app_version`, `os_major`, `arch`, `release_channel` |
| `consent_updated` | P0 | `category`, `enabled`, `surface`; record disable locally, do not upload it after the gate closes |
| `app_opened` | P0 | `launch_kind`, `previous_exit`, `startup_ms_bucket` |
| `app_closed` | P1 | `duration_bucket`, `clean` |
| `auth_connected` | P0 | `provider`, `entrypoint` |
| `project_created` | P0 | `pipeline_type`, allowlisted `style_id` |
| `project_opened` | P0 | `age_bucket`, `has_timeline`, `has_assets`, `has_export` |
| `asset_imported` | P0 | `media_kind`, `source`, `bytes_bucket`, `outcome` |
| `project_content_ready` | P0 | `source`, `seconds_from_create_bucket` |
| `agent_turn_started` | P0 | `model_id`, `thread_kind`, `entrypoint` |
| `agent_tool_finished` | P0 | registered `tool_id`, `tool_family`, `outcome`, `duration_bucket`, `failure_class`, `attempt_index` |
| `agent_capability_blocked` | P0 | `capability_id`, `reason`, `impact`, `workaround_used` |
| `agent_turn_completed` | P0 | `duration_bucket`, `cost_usd_bucket`, `sdk_turns`, `tool_count`, `failed_tool_count`, `stop_reason`, `produced_timeline`, `produced_render` |
| `agent_turn_failed` | P0 | `phase`, `failure_class`, `duration_bucket`, `retryable`, optional registered `tool_id` |
| `agent_improvement_signal_decided` | P1 | `signal_type`, `capability_id`, `decision`, `reason`, `confidence` |
| `editor_opened` | P0 | `source`, `clip_count_bucket`, `duration_bucket` |
| `editor_operation_committed` | P0 | `operation`, `target_type`, `source`, `count_bucket` |
| `editor_operation_undone` | P0 | `original_operation`, `latency_bucket`, `chain_depth_bucket` |
| `editor_feature_discovered` | P1 | `feature_id`, `surface`, `first_time` |
| `editor_session_ended` | P1 | `duration_bucket`, `commit_count_bucket`, `undo_count_bucket`, `unique_features_bucket` |
| `preview_quality_sampled` | P1 | `seek_latency_p50_bucket`, `seek_latency_p95_bucket`, `stall_count_bucket`, `preview_mode` |
| `render_started` | P0 | `origin`, `runtime`, `scene_count_bucket`, `cache_state` |
| `render_completed` | P0 | `runtime`, `duration_bucket`, `cached_scene_count_bucket`, `rendered_scene_count_bucket`, `warning_count_bucket` |
| `render_failed` | P0 | `stage`, `failure_class`, `runtime`, `duration_bucket`, `retryable`, `exit_class` |
| `render_superseded` | P0 | `origin`, `runtime`, `elapsed_bucket` |
| `export_started` | P0 | `origin`, `output_kind`, `resolution`, `codec` |
| `export_completed` | P0 | `duration_bucket`, `output_kind`, `resolution`, `codec`, `bytes_bucket`, `first_export`, `watermark_free` |
| `export_failed` | P0 | `stage`, `failure_class`, `duration_bucket`, `retryable` |
| `export_cancelled` | P0 | `stage`, `elapsed_bucket` |
| `feedback_opened` | P1 | `source`, `context` |
| `feedback_submitted` | P0 | `kind`, `diagnostics_included`, `contact_provided`, `locally_stored`, `delivery_state` |
| `feedback_delivery_completed` | P1 | `channel`, `retry_count_bucket`, `delay_bucket` |
| `feedback_delivery_failed` | P1 | `failure_class`, `retry_count_bucket`, `will_retry` |
| `survey_shown/responded/dismissed` | P1 | allowlisted `survey_id`, `trigger`, bounded score; free text goes only to the consented feedback service |

`render_*` describes computation; `export_*` describes the final user artifact.
In today's UI one action may begin both, but they remain separate state
transitions. Emit `export_completed` only after the canonical final file exists
and the receipt confirms it is watermark-free. A click is not completion.

The current debug calls are useful inputs, not a schema to upload wholesale.
Adapt commit, undo, render, and save outcomes through a filtered analytics
adapter. Keep live/preview chatter local and roll it into one session sample.

Exceptions and stacks go only to Sentry. PostHog may receive a low-cardinality
product outcome such as `render_failed`; it must not receive a duplicate generic
`error_observed` with the exception text.

## 5. Crash/error pipeline

### Target flow

```text
Electron native/GPU ----> Crashpad minidump ----\
Electron main JS --------> Sentry Electron ------+
React/global JS ----------> renderer-to-main ----+--> Sentry issue inbox
FastAPI/Python -----------> Sentry Python --------+
Python native exit -------> local fault record ---+
FFmpeg/render child ------> typed exit/stderr ----/
                               |
                               +--> product outcome -> PostHog
```

One `release_id` spans desktop, renderer, and backend. A random `run_id`,
`turn_id`, `render_id`, and `event_id` correlate layers without sending project
identity. Sentry receives layer, release, architecture, handled/fatal status,
typed operation, and scrubbed breadcrumbs. It does not receive prompts,
filenames, media, API payloads, raw tool arguments/results, or raw debug logs.

### Coverage by layer

- **Electron native:** start `crashReporter` before creating BrowserWindows on
  launches where stored crash consent is on. Capture main, renderer, GPU, and
  utility-process minidumps through the Sentry Electron integration. A fresh
  install cannot start upload before the first-run decision; crashes before that
  stay local and are deleted if consent is refused.
- **Electron JavaScript:** replace direct PostHog exception POSTs with Sentry
  capture. Keep process-exit inference as a breadcrumb and recovery trigger, not
  a substitute for a minidump.
- **Renderer:** use the Sentry Electron renderer/main bridge for ErrorBoundary,
  global errors, promise rejections, and render-process termination. Do not
  maintain a second `/telemetry/client-error` error destination.
- **Python:** use the Sentry Python SDK around FastAPI and background render
  workers. Convert expected auth, invalid-input, permission, provider, and quota
  outcomes to typed product failures. Unexpected invariants/exceptions go to
  Sentry once.
- **Python native:** enable `faulthandler` to a bounded local sanitized fault
  record. On the next consented launch, the Electron parent attaches the exit
  class and sanitized tail to a Sentry event. Do not upload an unrestricted
  macOS `.ips` report.
- **FFmpeg/render:** capture return code or signal, stage, runtime, retryability,
  and a scrubbed, categorized stderr fingerprint. User-media decode failures are
  product outcomes; internal crashes/invariants are also Sentry issues. Never
  upload raw stderr, which can contain file paths and metadata.
- **Agent SDK:** instrument every turn/tool deterministically. A returned tool
  failure is a product event; an SDK/internal exception is a Sentry issue. The
  model's self-report is not required for basic error coverage.

### Durable delivery and consent

Sentry's own statistics documentation warns that queued/network-failed events
can be dropped ([Sentry stats][sentry-stats]). Therefore SDK installation alone
does not satisfy the local-first/offline requirement.

```text
event -> scrub -> consent gate -> bounded encrypted-at-rest outbox -> send
                                                    ^                 |
                                                    +-- backoff <-----+
```

- Renderer forwards sanitized envelopes to main; Python writes sanitized JSON
  envelopes to its own atomic spool. Crashpad retains its native reports in its
  private local directory. Never persist raw content and “scrub later.”
- Flush at network recovery, app start, every 60 seconds while open, and clean
  shutdown. Use exponential backoff with jitter and idempotent event IDs.
- Cap ordinary envelopes at seven days and 50 MB total, oldest first. Cap
  Crashpad reports separately. Surface pending count in Privacy settings.
- Turning a category off stops new capture, closes its client, deletes unsent
  envelopes/minidumps, requests server-side deletion, and rotates the applicable
  pseudonymous ID. A disabled client performs no DNS or network call.
- Treat 4xx schema/consent failures as terminal; retry network/429/5xx failures.
  Maintain a local delivery counter without sending telemetry about telemetry
  when disabled.

### Releases, grouping, and validation

Production Vite source maps and native symbols must be generated and uploaded
to Sentry under the exact release before notarized artifacts ship; source maps
must not be served publicly. Fingerprint known FFmpeg/provider families, but let
Sentry group unknown exceptions. Alerts: page/email immediately for a new fatal
in the beta channel or a release regression; digest handled issues daily.
Release health provides crash-free session statistics
([Sentry release health API][sentry-health]).

Required pre-beta fault injections:

1. Throw in renderer, Electron main, FastAPI, render worker, and agent adapter.
2. Crash a packaged renderer/native child and verify a symbolicated issue.
3. Go offline, trigger each error, relaunch online, and verify one delivery.
4. Refuse each consent category and prove zero client construction, spool write,
   DNS, or upload for that category.
5. Put a home path, filename, prompt fragment, email, and API key in every input;
   prove none appears in local envelopes or either vendor.

## 6. Agent-as-reporter

The human's proposed direction is right only if the agent is treated as an
untrusted sensor, not an autonomous narrator. Free-form “tell us what went
wrong” is a PII and prompt-injection channel. Also, asking the model to report
every tool failure is less reliable than instrumenting the tool host directly.

### Surface

Add one P1 tool, `report_product_signal`, whose result is a local candidate. It
does not directly call a vendor.

```json
{
  "signal_type": "tool_failure | missing_capability | workflow_blocker | unmet_need",
  "capability_id": "allowlisted_registry_id",
  "failure_class": "auth | dependency | permission | invalid_input | provider | timeout | runtime | quality | unknown",
  "user_impact": "blocked | degraded | none",
  "evidence_type": "observed_tool_failure | capability_miss | workaround | explicit_request",
  "attempt_count": 1,
  "confidence": "low | medium | high"
}
```

Unknown keys and unknown IDs fail validation; payloads are at most 1 KB. There
is no string field capable of carrying prompt text, user text, filenames, tool
arguments/results, or media metadata. The host derives turn/tool/build IDs and
checks the claimed evidence against its own tool-result ledger.

```text
model candidate
      |
      v
strict schema -> evidence match -> content scan -> dedupe/rate limit
                                                       |
                                                       v
local ledger <- consent gate <- human-visible category -> durable outbox
```

Rate-limit to three accepted candidates per turn and 20 per install per day;
deduplicate by release, capability, failure class, and outcome. A candidate
without matching evidence is dropped or marked `unverified`, never promoted to
a product fact. A prompt injection cannot select an endpoint, add a field, or
smuggle a string because the host owns all three.

### “Our fault” versus agent misuse

The host, not the model, makes the first classification:

| Evidence | Classification |
|---|---|
| Tool returned a contract-valid internal exception | likely product/tool defect |
| Same valid call fails repeatedly or across installs | stronger product defect |
| Unknown tool, malformed schema, invalid argument | agent planning/tool-use error |
| Auth, quota, provider outage, OS permission | environment/provider dependency |
| User asks for a capability absent from registry | unmet-need candidate |
| Model claims a failure absent from the ledger | unverified; do not transmit as fact |

Repeated agent misuse still matters: it may reveal a confusing tool contract.
Report it as `agent_tool_finished(outcome=invalid_input)`, not falsely as a
backend crash. Human review can promote recurrent, verified clusters into a
redacted evaluation case. Never automatically train on a user's prompt.

### Approval and user trust

Do not show a modal for every structured signal; that would destroy the passive
loop and train people to click through. Instead offer a separate default-off
first-run toggle for “agent improvement signals,” a local “What OpenNolan
reported” ledger, one-click disable/delete, and a clear schema description.
Anything containing free text or a debug attachment still requires per-send
preview and explicit approval.

There is no honest way to collect from someone who declines telemetry. “Even if
the user never gives feedback” can mean infer from consented behavior; it cannot
mean bypass refusal.

OpenTelemetry's emerging GenAI conventions model an agent invocation with chat
and tool-execution child spans and keep content capture opt-in. VS Code's agent
monitoring similarly exports tool/model metadata with prompt/tool content off by
default. Use that model for future compatibility, not a proprietary free-text
report stream. See [OpenTelemetry GenAI observability][otel-genai],
[GenAI attribute registry][otel-registry], and
[VS Code agent monitoring][vscode-agent].

## 7. Feature demand without feedback

Behavior reveals friction and adoption more reliably than stated roadmap votes,
but it cannot reveal every unmet need. In particular, a hidden feature that is
never discovered produces no behavioral demand signal.

| Signal | What it supports | Confidence / caveat |
|---|---|---|
| Feature committed, then reused on later days | Actual value/adoption | High; not proof of missing demand |
| Feature discovered but never committed | Relevance or usability issue | Low without context |
| Undo within 5 seconds; repeated undo chain | Wrong result or control friction | Medium; not automatically a feature request |
| Repeated manual operation sequence | Possible missing shortcut/workflow | Medium after recurrence across installs |
| Render retry then abandonment | Reliability blocks value | High |
| Agent `missing_capability` verified against registry | Requested absent capability | High after repeat; category only |
| Unsupported import/provider/format outcome | Support gap | Medium/high when it blocks export |
| Repeated clicks on an allowlisted disabled control | Dead end or unclear affordance | Medium; collect counts, not DOM text |
| Preview stalls/slow seeks followed by exit | Performance friction | Medium/high |
| Search/command miss against allowlisted command IDs | Discoverability or missing function | Medium; never upload query text |

Score candidates as:

```text
demand score = reach x repetition x outcome impact x evidence confidence
```

Use buckets, not false precision. Except for a critical crash/data-loss defect,
require either recurrence across at least three installs or direct user evidence
before adding roadmap scope. With zero external users, telemetry is primarily a
way to find interview targets and inspect funnels; it cannot replace watching
the first beta users work.

Ask contextual questions after evidence-rich moments: a one-question effort
score after first export, or a recovery question after a failed render succeeds.
PostHog's own product guidance recommends asking shortly after a key action and
keeping the question specific ([contextual surveys][posthog-surveys]). Avoid NPS
before product-market fit.

## 8. Nightly/scheduled reporting

A nightly script inside a local-first app is the wrong shape. It creates an OS
background job, phones home while the user believes the app is closed, complicates
consent, and still does not solve immediate crash delivery. Use the durable
in-app outbox above and flush only while OpenNolan is running.

```text
Mac while app runs                 Vendor side
-----------------                 -----------
capture -> local outbox -> API -> PostHog/Sentry
                                  |
                                  v
                           scheduled daily query
                                  |
                                  v
                         founder beta digest/alerts
```

During beta, send a vendor/server-side 09:00 daily digest containing:

- new consenting installs and internal-install exclusion;
- activation funnel and median time to first export;
- render/export success, retries, and top typed failure classes;
- agent turn/tool success, latency/cost buckets, and blocked capabilities;
- crash-free sessions, new/regressed Sentry issues, and affected releases;
- feedback pending delivery/reply and contextual survey counts;
- top verified demand/friction clusters with sample size and confidence.

Fatal/new-release regressions alert immediately. Once traffic is stable, switch
the product digest to weekly; daily zero-heavy email is not insight. Every row
links to a saved bounded query, names an owner, excludes internal machines, and
contains no raw content. PostHog's free product-event allowance is ample at this
stage, but pricing is not the design constraint ([PostHog product/pricing][posthog]).

Close the feedback loop separately. Give each submission an opaque report ID and
truthful state: `saved locally`, `pending network`, `sent`, or `delivery failed`.
Retry from the regular outbox. If the user supplies contact information, send an
acknowledgement and later resolution message. P1 may show status in-app by opaque
ID without requiring an account.

## 9. Privacy/consent/compliance

This is product and engineering guidance, not legal advice. The safest
pre-beta posture is explicit, granular opt-in with all external telemetry off
until a positive action.

### First-run proposal

Show after the app can explain itself but before any external SDK is created:

> **Help improve OpenNolan (optional).** Your videos, prompts, filenames,
> project names, and API keys are never sent. Choose what may leave this Mac:
> [ ] Crash diagnostics — scrubbed stack traces and native minidumps
> [ ] Feature usage — named actions, outcomes, and timing buckets
> [ ] Agent improvement signals — allowlisted failure and need categories,
> never prompt or tool content. Change your choices and delete data anytime.

“Continue with all off” must be equally prominent. Settings keep the three
switches separate. User-initiated feedback is a fourth path with payload preview
and per-send attachment approval. Local operational logs may remain local with a
short disclosed retention period; local does not authorize upload.

Enforce consent independently in renderer, Electron, Python, agent reporter,
outbox, Crashpad upload, and feedback attachment code. The current Python
client-construction gate is a good pattern to generalize
(`server/analytics.py:121`). Do not merely filter at a central network call.

### Data status and legal posture

- A stable device UUID and vendor-visible IP address are pseudonymous personal
  data, not anonymous data. Update the UI and privacy notice accordingly.
- GDPR principles require a lawful basis, purpose limitation, minimization,
  accuracy, retention limits, and security
  ([European Commission GDPR principles][gdpr-principles]). Crash/security
  diagnostics may support a documented legitimate-interest assessment, but
  explicit consent for product and agent analytics is the clearer beta posture,
  especially for a creative app. Consent must be specific, informed, freely
  given, and withdrawable ([GDPR consent definition][gdpr-consent-definition],
  [GDPR consent conditions][gdpr-consent]).
- EU ePrivacy rules are technology-neutral and can cover app storage/access and
  persistent identifiers, not only browser cookies
  ([ICO storage/access guidance][ico-storage],
  [EDPB Article 5(3) guidance][edpb-53]). A cookie banner is not the right UI for
  a native app; a first-run category chooser is. “No cookies” is not a reason to
  skip consent analysis.
- Before EU/UK beta, choose regional PostHog and Sentry projects where available,
  sign DPAs, document subprocessors and transfers, disable vendor IP/geolocation
  enrichment, and verify deletion APIs. The current PostHog host is US
  (`server/analytics.py:33`).
- CCPA thresholds may not be met by a zero-user company, but the product should
  still provide notice, access/delete/correct mechanisms, and state that data is
  neither sold nor shared for cross-context behavioral advertising
  ([California DOJ CCPA][ccpa]).
- Apple's App Tracking Transparency prompt is not required here: this is a
  direct-distribution macOS app, and first-party product analytics without
  cross-company ad/data-broker linkage is not ATT “tracking.” ATT's published
  scope and definition are in [Apple's user privacy guidance][apple-att].
- Notarization checks signing and scans software for malicious content; it is not
  privacy review ([Apple notarization][apple-notarization]). App Store privacy
  labels and App Store Connect validation do not directly govern a notarized,
  non-App-Store download. Apple privacy manifests are still useful as a machine-
  readable data inventory and future-proofing measure. Apple's required-reason
  API list currently names iOS-family platforms, not macOS
  ([privacy manifests][apple-manifest],
  [adding a privacy manifest][apple-add-manifest]).

### Operational controls

- Publish a plain-language data map: field, purpose, destination, retention,
  legal basis, and deletion mechanism. Maintain a subprocessor list and breach
  response owner.
- Suggested retention: product raw events 90 days then aggregate; Sentry errors
  30 days; local pending envelopes seven days/50 MB; local debug sessions seven
  days unless explicitly saved; feedback until resolved plus 30 days, capped at
  12 months absent an active conversation.
- Provide deletion by install ID/deletion token without requiring an account.
  Delete vendor records, purge queues, then rotate local identifiers. Make
  access/export available in machine-readable form.
- Disable session replay. A video editor exposes media, prompts, captions, and
  project structure in the DOM; redaction failure is too costly.
- Sanitize debug data before persistence/attachment. Show the exact sanitized
  preview and byte size. Sentry attachments also have distinct retention and
  size behavior ([Sentry attachments][sentry-attachments]); do not use them for
  routine debug sessions.

## 10. Phased P0/P1/P2 plan

### P0 — before the first external beta

If there is only one engineering week, do P0 items 1–5 in that order and defer
the polished digest: consent and a testable event contract first, the activation
and export funnel second, Sentry/native capture plus durable delivery third, and
truthful sanitized feedback fourth. A dashboard cannot compensate for missing or
unsafe evidence.

1. **Define consent and identity.** Split settings into crash, product, and
   agent-signal opt-ins; default all off; move `app_opened` ownership to Electron
   app-ready; add session/release IDs and deletion/rotation behavior. Touch
   `server/settings.py`, `server/analytics.py`, `server/app.py`,
   `desktop/main.js`, `web/src/App.jsx`, and `web/src/api.js`.
2. **Ship the contract and value funnel.** Add a single versioned registry and
   forbidden-field validator. Wire the P0 lifecycle, project, agent, editor,
   render, export, and feedback events. Adapt the existing debug events through
   a narrow sink; do not upload raw debug traffic. Primary files are
   `server/analytics.py`, `server/app.py`, `server/agent_runner.py`,
   `server/render_jobs.py`, `web/src/studio/Studio.jsx`, and
   `web/src/debug/recorder.js`.
3. **Add the two-destination delivery spine.** PostHog accepts only product
   events; Sentry Electron/Python accepts errors. Implement the bounded local
   outbox and opt-out purge. Start Crashpad early when prior consent permits.
4. **Make releases debuggable.** Generate private renderer source maps, upload
   maps/symbols before distribution, attach exact releases to all layers, enable
   Python fault records, and classify FFmpeg exits.
5. **Repair feedback trust.** Sanitize debug data before storage, persist delivery
   attempts, return/report the real delivery state, show `saved/pending/sent`,
   and provide an opaque report ID. Never say “sent” for local-only storage.
6. **Create the operating view.** Build the activation funnel, export/render
   board, agent-tool board, Sentry release alert, and daily beta digest. Write
   the privacy notice/data map, choose vendor regions, and execute DPAs before
   invitations go out.

P0 success conditions:

- A packaged, notarized test build completes the synthetic first-run-to-export
  journey and emits exactly one `export_completed` only after a real final file.
- Refusing all categories produces no vendor client, DNS call, spool entry, or
  upload; withdrawing purges pending data.
- Forced renderer, main, Python, agent, FFmpeg, and native-child failures arrive
  once, grouped and symbolicated after an offline/relaunch cycle.
- Contract tests reject every unknown event/property and known sensitive fixture.
- With the feedback relay down, the UI says pending; after recovery, the same
  report is sent once and contains no raw debug text.

Extend `tests/contracts/test_analytics.py` and the existing feedback, agent,
render-job, and desktop test suites. Add packaged fault-injection smoke coverage
to `scripts/dev smoke`; run `scripts/dev test full` and `scripts/dev smoke`
before review.

### P1 — during a small private beta

1. Add the gated `report_product_signal` tool, host-side evidence matcher,
   inspectable local ledger, dedupe/rate limits, and human-reviewed clustering.
2. Add preview session rollups, discovery/undo/workaround signals, and the
   conservative demand score. Validate each detector against recordings from
   consenting internal/beta sessions before acting on it.
3. Add contextual effort/recovery surveys and in-app feedback delivery status.
   Establish an acknowledgement/response owner and target.
4. Add a user-facing telemetry log similar to VS Code's, remote deletion status,
   privacy manifest/data inventory, and automated retention/deletion checks.
5. Review the daily digest after two weeks; remove unused events and move to a
   weekly product digest when alerting plus sample size makes daily review noisy.

P1 succeeds when at least one beta cohort can be followed from first run to
export without content collection, every demand cluster exposes its evidence and
sample size, and a user can inspect/disable/delete all uploaded categories.

### P2 — only after signal and volume justify it

- Export metadata-only agent/tool traces in the OpenTelemetry GenAI shape and
  maintain a human-curated regression-evaluation set.
- Add experiment/feature-flag measurement only when there is enough traffic for
  decisions; add a warehouse only when PostHog retention/query limits block a
  named analysis.
- Consider self-hosting or replacing a vendor if residency, deletion, cost, or
  reliability targets fail. Consider richer performance profiling only after
  preview/render telemetry identifies a real bottleneck.

## 11. Deliberately not building / what changes my mind

- **No session replay.** It is disproportionate for a creative editor. Revisit
  only if field-level redaction is independently tested, replay answers a named
  problem, and users separately opt in.
- **No prompt/response/tool-payload capture.** Metadata is enough for the first
  funnel. Revisit only for a specific evaluation that cannot work otherwise,
  with per-session preview/consent and short isolated retention.
- **No LangSmith, Braintrust, Helicone, or third observability vendor.** PostHog
  metadata plus Sentry errors and an OTel-compatible schema are enough. Revisit
  when a human-reviewed eval workflow has requirements neither vendor meets.
- **No client nightly daemon or launchd job.** Revisit only for an explicitly
  user-enabled background product feature, not analytics delivery.
- **No warehouse/CDP, hundreds of events, per-click stream, NPS, or experiments.**
  Revisit each only when a named decision is blocked by current data and the
  sample size can support it.
- **No AI-authored free-text reports or automatic roadmap decisions.** Revisit
  free text only with user preview; never remove human product judgment.
- **No raw debug-session upload and no duplicate PostHog/Sentry exception sink.**
  Revisit attachments only for an explicit support case with preview and
  consent. Keep one owner for each data class.
- **No claim that telemetry replaces interviews.** If the first ten users are
  reachable, watching their workflow is higher leverage than a sophisticated
  demand score.

## 12. Open questions

1. What exactly makes an artifact “final” and “watermark-free”: successful job
   completion, a verified file receipt, or a subsequent save/share action? My
   recommendation is verified file receipt.
2. Will the first beta include EU/UK residents? If unknown, configure regional
   vendors and consent as though it will.
3. Is a second vendor acceptable? My recommendation is yes for Sentry; if no,
   native Crashpad collection, symbolication, grouping, alerting, and retention
   still need an explicitly owned replacement before beta.
4. What deletion and support channel can the founder reliably operate? My
   minimum is an opaque deletion token plus email acknowledgement for feedback.
5. What are the beta release channels and exact build identifiers? They must be
   fixed before source-map/symbol upload and regression alerts are testable.
6. Are cached/background renders distinguishable from a user-requested final
   export in the current product contract? If not, that contract is the first
   implementation decision, not an analytics naming issue.
7. What retention or vendor-budget constraints are non-negotiable? They may
   change retention and sampling, but do not justify collecting content.

[vscode-tel]: https://code.visualstudio.com/docs/configure/telemetry
[docker-settings]: https://docs.docker.com/desktop/settings-and-maintenance/settings/
[obsidian]: https://obsidian.md/privacy
[electron-crash]: https://www.electronjs.org/docs/latest/api/crash-reporter
[sentry-electron]: https://docs.sentry.io/platforms/javascript/guides/electron/
[sentry-maps]: https://docs.sentry.io/platforms/javascript/guides/electron/sourcemaps/uploading/
[sentry-stats]: https://docs.sentry.io/product/stats/
[sentry-health]: https://docs.sentry.io/api/releases/retrieve-release-health-session-statistics/
[sentry-attachments]: https://docs.sentry.io/platforms/javascript/enriching-events/attachments/
[mux-startup]: https://www.mux.com/docs/guides/data-startup-time-metric
[mux-smoothness]: https://www.mux.com/docs/guides/data-smoothness-metric
[amplitude-taxonomy]: https://amplitude.com/explore/data/event-taxonomy
[amplitude-properties]: https://amplitude.com/docs/data/user-properties-and-events
[otel-genai]: https://opentelemetry.io/blog/2026/genai-observability/
[otel-registry]: https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/
[vscode-agent]: https://code.visualstudio.com/docs/agents/guides/monitoring-agents
[posthog-surveys]: https://newsletter.posthog.com/p/how-to-uncover-your-users-real-problems
[posthog]: https://posthog.com/
[gdpr-principles]: https://eur-lex.europa.eu/eli/reg/2016/679/art_5/oj/eng
[gdpr-consent-definition]: https://eur-lex.europa.eu/eli/reg/2016/679/art_4/oj/eng
[gdpr-consent]: https://eur-lex.europa.eu/eli/reg/2016/679/art_7/oj/eng
[ico-storage]: https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guidance-on-the-use-of-storage-and-access-technologies/what-are-storage-and-access-technologies/
[edpb-53]: https://www.edpb.europa.eu/system/files/2024-10/edpb_guidelines_202302_technical_scope_art_53_eprivacydirective_v2_en_0.pdf
[ccpa]: https://oag.ca.gov/privacy/ccpa
[apple-att]: https://developer.apple.com/app-store/user-privacy-and-data-use/
[apple-notarization]: https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
[apple-manifest]: https://developer.apple.com/documentation/bundleresources/privacy-manifest-files
[apple-add-manifest]: https://developer.apple.com/documentation/bundleresources/adding-a-privacy-manifest-to-your-app-or-third-party-sdk
