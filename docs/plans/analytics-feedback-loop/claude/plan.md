# Analytics, feedback loop, and crash reporting for OpenNolan

**STATUS: PLAN**

Author: claude · Date: 2026-08-05 · Scope: instrumentation + feedback + crash
pipeline design for the pre-beta packaged Mac app. No implementation code in
this doc.

Research: I had web access. Every external claim below carries a URL. Nothing
in this doc is "from training knowledge, unverified."

---

## 1. Verdict up front

1. **PostHog is enough.** Product analytics + Error Tracking + Surveys +
   Feature Flags cover every one of the seven asks. The only real hole is
   **native minidumps** (a hard renderer/GPU crash), and you already catch the
   *fact* of those in `desktop/main.js:673-674` — you just lack the stack.
   That is not worth a second vendor at zero users.
2. **Do not turn on Session Replay.** In a video editor the screen *is* the
   user's unreleased creative work. This is the one PostHog product I would
   hard-disable rather than defer.
3. **Biggest gap is not the editor. It is that `export_completed` does not
   exist.** The brief says it does; it does not. It appears only in a test
   fixture (`tests/contracts/test_analytics.py:99`) and a plan doc
   (`docs/plans/publish-mac-app.md:137`). There is no `capture("export_...")`
   anywhere in `server/`. So the North Star — first watermark-free export —
   is currently **unmeasurable**, and so is the success rate of the two things
   the app exists to do (an agent turn, a render).
4. **Real event count is 5, not 6:** `app_opened`, `app_first_run`,
   `project_created`, `auth_connected`, `feedback_submitted`.
5. **One week before beta, in order:** (a) render/export funnel at the single
   choke point `server/render_jobs.py:309`; (b) agent-turn outcome at
   `server/agent_runner.py:1924`; (c) automatic `agent_tool_failed` with a
   *code-derived* ours-vs-agent fault flag; (d) a durable on-disk error spool
   flushed at next launch (the PostHog Python SDK queue is memory-only —
   §5.4); (e) first-run consent, because today `app_opened` fires at backend
   boot (`server/app.py:443`) before the user has seen a single pixel.
6. The **nightly script (idea #6) is the wrong shape.** Keep the *durable
   spool*, drop the *schedule*. Your digest is a PostHog daily email
   subscription, not a cron job you have to maintain.
7. The **agent-as-reporter (ideas #4/#5) is the right shape** but must be
   enum-only on the wire. Prose the agent writes never leaves the machine
   without a human click. §6.

---

## 2. Current-state audit

### 2.1 What exists

```
                       ┌──────────────────────────────┐
 Electron main ───────►│  POST /capture/ direct        │ desktop/main.js:65
 (backend dead path)   │  event: desktop_error         │
                       └──────────────┬───────────────┘
                                      │
 Renderer JS ──► /api/telemetry/error │       ┌─────────────────┐
 main.jsx:9-43   app.py:1016 ─────────┼──────►│    PostHog      │
                                      │       │  proj 478214    │
 Python routes ──► global handler ─────┤       └─────────────────┘
                   app.py:421-428     │
 Agent turn ──────► app.py:1164 ──────┘
 Capability disc ─► app.py:927
 Feedback ────────► feedback.py:177 (metadata only)
```

| Layer | Where | What it sends |
|---|---|---|
| PostHog wrapper | `server/analytics.py:1-243` | all of the below |
| Product events (5) | `app.py:443,449,544,970,980`, `feedback.py:177` | see §1.4 |
| Backend exceptions | `app.py:421-428` | route path + method |
| Agent-turn failures | `app.py:1164` | `where=agent_turn` |
| Capability-discovery fail | `app.py:927` | `where=capability_discovery` |
| Renderer/React crashes | `web/src/main.jsx:9-43` → `api.js:67` | msg + stack + component stack |
| Electron main crashes | `desktop/main.js:671-674` | uncaught, rejection, `render-process-gone`, `child-process-gone` |
| Backend-won't-start | `desktop/main.js:111-125` (`fatal()`) | title + stderr tail |
| Feedback | `server/feedback.py:153-191` | local JSONL → event → relay/Resend |
| Local UI recorder | `web/src/debug/recorder.js` | NDJSON on disk, never phones home |
| Local agent activity | `server/activity.py` → `.mc/activity.jsonl` | every tool call, on disk |

### 2.2 What is genuinely good (do not "improve" this)

This is a better privacy posture than most shipped desktop apps. Specifically:

- **Opt-out honored at init, not at send.** `server/analytics.py:128-130`
  returns `None` and *never constructs a client* when disabled. The contract
  test asserts the constructor is never called
  (`tests/contracts/test_analytics.py:80-89`), not merely that events drop.
  That is the correct invariant and every new path I propose inherits it.
- **`_before_send` closes a real leak.** The SDK builds `$exception_list`
  *after* your properties are scrubbed, so each frame's `abs_path` would ship
  `/Users/<username>/…`. `server/analytics.py:108-118` re-redacts, and the
  test uses the SDK's *real* serializer and the *real* current username
  (`tests/contracts/test_analytics.py:198-223`). That test is the best thing
  in the analytics surface.
- **Free-text is dropped, not truncated.** `_scrub` at
  `server/analytics.py:145-163` converts `prompt`/`message`/`caption`/… into
  `<key>_len`. For a creative app whose properties are full of user prose,
  keeping only a length signal is the right default.
- **`internal` flag on every event.** `server/analytics.py:51-77`. `env`
  cannot distinguish "the developer running the downloaded .app" from a real
  user; a `~/.opennolan-internal` sentinel can. This is a detail most solo
  devs get wrong and then spend a month reading their own data.
- **Hard-off under pytest.** `server/analytics.py:44-45`.
- **Feedback is durable-first.** `server/feedback.py:175` appends the local
  JSONL *before* any network call, so feedback survives an offline machine and
  an undeployed relay.
- **Two reporters agree on redaction.** `desktop/main.js:62` deliberately
  mirrors `_PATH_RE` from `server/analytics.py:36`.

### 2.3 What is missing (confirmed against code)

Confirming the brief, with two corrections.

| Brief's claim | Verdict |
|---|---|
| Editor completely uninstrumented | **Partly wrong — better than stated.** ~22 semantic call sites already exist (`Studio.jsx:106,109,110,179,190,199,208,222,230,234,264-290,320,587,628`; `StudioPreview.jsx:175,203`). They are wired to *one sink* (the local NDJSON recorder) which no-ops unless the user manually starts recording (`recorder.js:113-114`). **The instrumentation exists; only the sink is missing.** This is the highest-leverage finding in this doc. |
| Agent turn uninstrumented as product analytics | **Confirmed.** `run_turn` (`server/agent_runner.py:1924-2007`) already computes everything you need — `TurnResult{is_error,num_turns,total_cost_usd}` at `:832-837`, populated at `:1979-1988` — and emits none of it. Tool calls are persisted locally (`:1973-1978` → `server/activity.py`) and never aggregated. |
| No render/export funnel | **Confirmed and worse than stated.** `export_completed` is not wired at all (§1.3). `server/render_jobs.py` has zero analytics imports. |
| No retention/activation/session concept | Confirmed. No `session_started`/`session_ended`, so no crash-free-session rate. |
| No agent-authored reporting path | Confirmed. The agent has `ask_user` (`agent_runner.py:920`), `request_api_key` (`:1370`), `request_capability` (`:1438`) — the pattern exists, the reporting tool does not. |
| No scheduled/offline flush | Confirmed, and it matters more than the brief says: see §5.4. |

Three additional findings the brief did not name:

- **`app_opened` fires before consent can exist.** `server/app.py:443` runs
  inside `create_app()`, i.e. at backend boot, before any window is shown.
  Any consent design has to move or gate this.
- **The feedback UI claims success it cannot verify.** `submit()` returns
  `{stored, emailed}` (`server/feedback.py:191`); the modal ignores `emailed`
  and always renders "your report was sent" (`web/src/App.jsx:320`). The
  default relay is `https://www.opennolan.com/api/feedback`
  (`server/feedback.py:36`). If that endpoint is not deployed, every reporter
  is told their report was sent and it is sitting in a local JSONL file on
  their own disk. **This is the single worst bug in the feedback loop** — it
  is a broken loop that *looks* closed.
- **`_scrub` is a denylist.** `server/analytics.py:145-163` redacts keys that
  *look* dangerous. A new event with a property named `asset_title` or
  `project_name` sails through. That is fine for 5 hand-audited events; it is
  not fine for the ~35 in §4. Note `ui.uploadAsset` already carries
  `name: file.name` (`Studio.jsx:587`) — harmless while it stays on disk,
  a filename leak the moment that sink also feeds analytics.
- **No `crashReporter`.** `grep crashReporter desktop/` is empty, so no
  minidumps. §5.

---

## 3. Gap table vs industry standard

Severity is *for this product at this stage*, not in the abstract.

| Capability | Industry standard | OpenNolan today | Sev | Effort |
|---|---|---|---|---|
| Activation / North Star event | One named activation event, funnel-defined | none (`export_completed` unwired) | **P0** | S |
| Core-action success rate | started/succeeded/failed triple w/ failure taxonomy | render has none | **P0** | S |
| Agent-run outcome | traces + tool-call success + cost/turn ([OTel GenAI][otel], [PostHog LLM][phllm]) | nothing | **P0** | M |
| Consent before first send | notice or gate before first event (VS Code ships an in-product notice, [VS Code telemetry][vsc]) | `app_opened` pre-consent | **P0** | S |
| Offline/crash-safe queue | disk-persisted queue (PostHog mobile SDKs do this; [React Native][phrn]) — Python SDK does **not** ([posthog-python][phpy]) | none | **P0** | S |
| Editor / feature usage | per-feature usage + discovery | 22 call sites, local-only sink | **P0** | S |
| Error inbox, all layers | one inbox, grouped, symbolicated | 5 of 6 layers reach PostHog | P1 | S |
| Crash-free session rate | standard release-health metric ([Sentry][sentryrh]) | no session events | P1 | S |
| Symbolication / source maps | upload maps per release ([PostHog error tracking][pherr]) | none (renderer is minified) | P1 | S |
| Contextual micro-surveys | event-triggered prompts get 25–40% vs 5–15% for a static widget ([Perspective][persp]) | static modal only | P1 | M |
| Kill switch in a shipped binary | feature flags | none | P1 | M |
| Developer digest | scheduled email/Slack subscription ([PostHog subscriptions][phsub]) | none | P1 | S |
| Agent self-report | emerging; no norm | none | P1 | M |
| Native minidumps | `crashReporter` + minidump upload ([Sentry Electron][sentryelec]) | none | P2 | M |
| Session replay | common — **wrong here** | off | n/a | — |
| Experiments / warehouse | mature-product tooling | none | P2 | — |

---

## 4. The event taxonomy I would ship

### 4.1 Conventions

- **`object_action`, snake_case, past tense.** `render_failed`, not
  `failRender`. This is the convention Amplitude, Mixpanel and PostHog all
  endorse ([Growth Method][oaf], [Mixpanel][mp]).
- **The event is the *what*; properties are the *who/where/why/how*.** One
  `editor_action` with an `action` property beats twenty
  `timeline_split_clicked` events ([Mixpanel][mp]).
- **Anti-bloat rule (hard):** a new event name needs a funnel or a retention
  question it answers *today*. Otherwise it is a property on an existing
  event. Ceiling: **35 event names**. At 35 you delete before you add.
- **Enum-only for anything derived from user content.** Every property whose
  value could be influenced by the user's media, filenames, or prompts must be
  a member of a closed set defined in our code. No exceptions.
- **Allowlist enforced by test, not at runtime.** Do not add allowlist
  machinery to `capture()` — keep the existing denylist `_scrub`
  (`server/analytics.py:145`) and add ONE contract test that walks a
  declared `TAXONOMY` dict and fails if any shipped event carries a property
  not in its declared set. Cheap, matches the repo's contract-test culture,
  and catches the `_scrub`-denylist hole in §2.3 at review time.

### 4.2 North Star and the activation funnel

**North Star: first successful watermark-free export**, per
`docs/plans/publish-mac-app.md:121`. Instrumented as
`export_completed{watermark_free:true}` where `first_export` is true.

```
 install ─► auth ─► project ─► agent builds ─► human edits ─► EXPORT
   │          │        │        a timeline     (optional)        │
   ▼          ▼        ▼            ▼              ▼             ▼
app_first_ auth_    project_  agent_turn_    editor_        export_
   run     connected created  completed      session_       completed
                             {produced_edit} summary     {first_export}

ACTIVATION = app_first_run ──► export_completed{first_export:true}
TIME-TO-VALUE = t(export_completed.first) − t(app_first_run)
```

Report weekly: activation rate, TTV median, and **render success rate**
(`render_succeeded / render_started`). If you only ever look at three
numbers, those are the three.

### 4.3 The events

`P0` = wired before any beta user. `P1` = during beta.

**Install / activation**

| Event | P | Properties |
|---|---|---|
| `app_first_run` | P0 (exists) | `os`, `app_version`, `arch` |
| `app_opened` | P0 (exists, must move post-consent) | `os`, `app_version`, `since_install_days` |
| `session_started` / `session_ended` | P1 | `session_id`, `ended_reason` ∈ {clean, timeout}. Absence of `_ended` = abnormal → crash-free-session rate ([Sentry][sentryrh]) |
| `provision_started` / `_completed` / `_failed` | P0 | `stage` ∈ {venv, core, ffmpeg, node, pack}, `duration_s`, `failure_class` |
| `auth_connected` / `auth_failed` | P0 (half exists) | `method` ∈ {oauth, api_key}, `failure_class` |
| `consent_set` | P0 | `analytics`, `diagnostics` (bool each), `at` ∈ {first_run, settings} |

**Project**

| Event | P | Properties |
|---|---|---|
| `project_created` | P0 (exists) | + `source` ∈ {ui, agent}, `n_projects_before` |
| `asset_added` | P0 | `kind` ∈ {video,image,audio,music}, `via` ∈ {upload, drag, agent, generated}, `bytes_bucket`, `duration_bucket`, `codec`, `hdr` (bool). **No filename.** |
| `project_abandoned` | P1 | derived server-side at next launch: `last_stage` ∈ {created, agent_turn, edited, render_failed}, `age_days` |

**Agent turn** — the highest-value block, and none of it exists.

| Event | P | Properties |
|---|---|---|
| `agent_turn_started` | P0 | `turn_index`, `is_fresh_client`, `model`, `has_mentions` |
| `agent_turn_completed` | P0 | `duration_s`, `num_turns`, `cost_usd`, `n_tool_calls`, `tool_categories[]` (from `server/activity.py` op map), `produced_edit` (bool), `produced_render` (bool), `intent_class` (§7.3), `prompt_len` |
| `agent_turn_failed` | P0 | `failure_class` ∈ {auth, budget_exceeded, transport, tool_error, timeout, interrupted, unknown}, `duration_s`, `n_tool_calls`, `cost_usd` |
| `agent_tool_failed` | P0 | `tool`, `fault` ∈ {ours, agent, environment, user_declined}, `failure_class`, `retry_index` (§6.5) |
| `agent_asked_user` | P1 | `kind` ∈ {question, confirm, api_key, capability}, `answered` (bool), `wait_s` |
| `agent_report_filed` | P1 | the enum payload from §6.2 |

All of `TurnResult` is already computed at `server/agent_runner.py:1979-1988`.
`prompt_len` only — never the prompt. `_scrub` already enforces that for a
property literally named `prompt` (`server/analytics.py:38`).

**Editor** — one event, not twenty.

| Event | P | Properties |
|---|---|---|
| `editor_session_summary` | P0 | emitted once when the studio unmounts or after 10 min idle: `duration_s`, `n_commits`, `n_undo`, `n_redo`, `undo_rate` (= undo/commit), `n_live_drags`, `actions{}` (a bounded counter map keyed by the existing `dbg.event` type strings), `fields_touched[]` (property *names* from `summarizeDocChange`, `web/src/studio/model.js:501-517` — already values-free), `n_saves`, `n_save_rejected`, `preview_mode_switches`, `seek_count`, `used_features[]` |
| `editor_feature_first_used` | P1 | `feature` (closed enum: split, arrange, crop, keyframe, text, audio_mix, track_move, mention, …). Fires once per install; backed by a set in `settings.json`. Discovery vs usage. |

**Why one rollup and not per-action events:** the call sites already exist and
already funnel through one function (`dbg.event`, `recorder.js:136`). A second
sink behind that function costs one new file and zero new call sites, cannot
explode event volume (one event per editing session, not one per drag frame),
and leaves the local NDJSON recorder untouched. It must **whitelist keys**, not
pass through — `ui.uploadAsset` carries a filename today (`Studio.jsx:587`).

**Render / export** — instrument at ONE place: `RenderJobStore._set`
(`server/render_jobs.py:309`) is the single choke point every status
transition already flows through.

| Event | P | Properties |
|---|---|---|
| `render_started` | P0 | `job_id`, `origin` ∈ {editor, agent} (already on the job, `render_jobs.py:66`), `n_cuts`, `n_overlays`, `n_audio`, `has_comp_clips`, `canvas`, `duration_s_timeline` |
| `render_succeeded` | P0 | `job_id`, `wall_s`, `realtime_ratio`, `n_scenes_rerendered`, `output_bytes_bucket`, `warnings[]` |
| `render_failed` | P0 | `job_id`, `wall_s`, `failure_class` (enum below), `ffmpeg_exit_code`, `stage` ∈ {proxy, assemble, overlay, publish} |
| `render_superseded` | P1 | `job_id` — a proxy for "user kept changing their mind mid-render" |
| `export_completed` | **P0 — the North Star** | `first_export` (bool), `watermark_free`, `resolution`, `duration_s`, `fps`, `codec`, `hdr`, `origin`, `time_since_install_h`, `n_renders_before` |

`failure_class` enum (derive from the error string in code; **never send the
string**): `no_edit_decisions`, `missing_source`, `unwritable_output`,
`ffmpeg_nonzero`, `crop_out_of_bounds`, `comp_runtime_missing`,
`comp_render_failed`, `oom`, `timeout`, `disk_full`, `unknown`. Failure sites
to map: `render_jobs.py:342,359,378,401,416,432,434,563`.

The `_execute_render` path already renders to `.part.mp4` and publishes once
(`render_jobs.py:480`), so `export_completed` has a clean, unambiguous hook.

**Error / feedback**

| Event | P | Properties |
|---|---|---|
| `$exception` | P0 (exists) | via `capture_exception` / `capture_client_error` |
| `desktop_error` | P0 (exists) | keep as-is; add `spooled` (bool) after §5.4 |
| `feedback_submitted` | P0 (exists) | + `delivered` (bool — fixes §2.3) |
| `feedback_prompt_shown` / `_dismissed` / `_answered` | P1 | `trigger` ∈ {first_export, render_failed, agent_failed}, `question_id` |

Total: **~32 names**, under the 35 ceiling, with room for two P2 additions.

---

## 5. Crash & error pipeline

### 5.1 Every layer, and where it lands

```
 ┌── LAYER ─────────────┬── CAUGHT BY ─────────────┬── TODAY ──┐
 │ 1 Native crash       │ Crashpad minidump        │  ✗ LOST   │
 │   (renderer/GPU/main │ (needs crashReporter)    │           │
 │    hard crash)       │                          │           │
 │   └─ the FACT of it  │ app.on('render-process-  │  ✓ main.js│
 │                      │  gone'/'child-process-   │    673-674│
 │                      │  gone')                  │           │
 │ 2 Electron main JS   │ process.on(uncaught /    │  ✓ main.js│
 │                      │  unhandledRejection)     │    671-672│
 │ 3 Backend won't boot │ fatal() + stderr tail    │  ✓ main.js│
 │                      │                          │    111-125│
 │ 4 Renderer JS        │ ErrorBoundary +          │  ✓ main   │
 │                      │  window.onerror +        │   .jsx:9- │
 │                      │  unhandledrejection      │    43     │
 │ 5 Python backend     │ FastAPI exc handler      │  ✓ app.py │
 │                      │  + SDK autocapture       │  421-428  │
 │ 6 Agent turn         │ drive() except           │  ✓ app.py │
 │                      │                          │    1164   │
 │ 7 Agent subprocess   │ CLI stderr / transport   │  ~ partial│
 │   (claude CLI dies)  │  error → 6               │  no detail│
 │ 8 Render / FFmpeg    │ status='failed' + error  │  ✗ LOCAL  │
 │                      │  string in job dict      │    ONLY   │
 │ 9 Provisioning       │ NDJSON error frame       │  ✗ LOCAL  │
 │   (venv/ffmpeg/node) │  (app.py:1044)           │    ONLY   │
 └──────────────────────┴──────────────────────────┴───────────┘
```

Route 8 and 9 to PostHog and you go from 5 layers to 8. That is a P0 and it is
small: 8 is `render_failed` from §4.3 (one call site), 9 is
`provision_failed` at the existing error-frame site.

### 5.2 What is genuinely uncatchable, and what closes it

- **A native minidump.** JS handlers cannot see a renderer or GPU process
  crash; that needs Electron's `crashReporter`, which must be started from the
  main process, and a minidump uploader ([Sentry Electron][sentryelec],
  [jviotti][jvi]). PostHog Error Tracking symbolicates source maps and symbol
  sets ([PostHog][pherr]) but has no documented minidump ingestion.
- **What you already get instead:** `render-process-gone` and
  `child-process-gone` fire in main and are already reported
  (`desktop/main.js:673-674`) with a `reason` and `exitCode`. Sentry's own
  docs note renderer/GPU minidumps "only include minimal context" anyway
  ([Sentry][sentryelec]).
- **Decision:** ship without minidumps. Add `session_started`/`session_ended`
  (§4.3) so you can compute crash-free-session rate ([Sentry][sentryrh]). If
  that rate is bad *and* `renderer-gone` events do not explain it, only then
  add `@sentry/electron` for the `ElectronMinidump` integration. Cost of
  adding it later: one dependency, a second dashboard, a second DSN, a second
  privacy disclosure, and a free tier you will exceed later than PostHog's.
- **A hard kernel panic / SIGKILL.** Nothing catches that. The
  missing-`session_ended` signal is your only evidence, and it is why that
  pair is worth its two events.

### 5.3 Symbolication

The packaged renderer bundle is minified by Vite, so today a renderer stack in
PostHog is unreadable. Fix: emit source maps in the production build and upload
them per release, keyed to `app_version` ([PostHog][pherr]). Do **not** ship
the maps inside the `.app` — that both bloats the bundle and hands your source
to anyone who unzips it. Python needs no symbolication. P1, small.

### 5.4 Offline queueing — the real reason idea #6 is half-right

`posthog-python` buffers events in memory and flushes on a background thread;
it does not persist the queue to disk, so a process that dies before flush
loses everything queued ([posthog-python docs][phpy]). PostHog's mobile SDKs
*do* persist to device storage ([React Native][phrn]); Python does not. Your
`shutdown()` (`server/analytics.py:226`) helps on a clean exit and does
nothing on a crash — which is exactly when the event matters.

So: **a durable spool, but not a nightly one.**

```
 crash/offline
      │
      ▼
 try POST ──── ok ──► done
      │
      └─ fail ──► append 1 JSON line ──► <home>/pending-errors.jsonl
                                              │
                       app 'ready' (next launch, Electron main)
                                              │
                    drain oldest-first, cap 200 lines / 2 MB,
                    drop >14 days old, truncate on success
                                              ▼
                                          PostHog
```

Where it lives: **Electron main.** It is the only layer that outlives the
backend, already POSTs directly to PostHog (`desktop/main.js:65-109`), already
reads the opt-out from the same `settings.json` (`:71-76`), and already knows
"app just launched". One writer format, one flusher. Python's
`capture_exception` appends to the same file on failure. Failure of the flush
itself is a silent no-op that leaves the file for next time — never a dialog,
never a retry loop.

Privacy: the spool holds already-scrubbed payloads only (the scrub runs before
the POST, `analytics.py:108`/`main.js:57`), so a stale file on disk contains
nothing the user has not already consented to send. Re-check the opt-out at
flush time, and **delete the spool** when the user opts out — a queued event
must not outlive consent.

---

## 6. The agent-as-reporter design (ideas #4 / #5)

### 6.1 The core position

**Split it in two, and put the load-bearing half in code, not in the LLM.**

```
 ┌─ automatic, always on ──────────────────────────────────────┐
 │ Our OWN tool boundary already knows every failure:          │
 │   _text_result marks is_error       agent_runner.py:849-852 │
 │   decide_tool DENY/CONFIRM          agent_runner.py:370     │
 │   _run_render / _run_media_op       agent_runner.py:1548,   │
 │                                                      1671   │
 │   render job status='failed'        render_jobs.py:309      │
 │ ⇒ emit `agent_tool_failed` from CODE. No LLM judgment.      │
 │   This is 90% of the value of ideas #4/#5.                  │
 └─────────────────────────────────────────────────────────────┘
 ┌─ agent-authored, rate-limited, enum-only ───────────────────┐
 │ The ONE thing code cannot see: what the user was trying to  │
 │ achieve, and whether we have a tool for it at all.          │
 │ ⇒ `report_problem` MCP tool. §6.2                           │
 └─────────────────────────────────────────────────────────────┘
```

If you build only the first half you already answer "the tool I tried is
broken" — more reliably than an LLM would, because it is derived from the exit
code rather than from the model's self-report.

### 6.2 `report_problem` — the tool surface

Follows the existing MCP-tool pattern in `_default_client_factory`
(`server/agent_runner.py:911`), same shape as `ask_user` (`:920-945`).

```json
{
  "name": "report_problem",
  "description": "Report to the OpenNolan developers that one of the app's
    own tools failed, or that the user asked for something the app cannot do.
    Use at most once per turn, and only after you have already tried the
    normal path. Do NOT use it for user mistakes, for your own reasoning
    errors, or to pass along anything the user wrote.",
  "input_schema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "category":  {"enum": ["tool_broken", "tool_missing",
                             "capability_missing", "schema_rejected",
                             "asset_unsupported", "docs_wrong",
                             "quality_unacceptable"]},
      "subject":   {"enum": ["<generated at runtime from our own tool +
                              feature registry — the agent may not invent a
                              value>"]},
      "severity":  {"enum": ["blocked", "degraded", "annoyance"]},
      "blocked_user": {"type": "boolean"},
      "attempts":  {"type": "integer", "minimum": 1, "maximum": 10},
      "note":      {"type": "string", "maxLength": 200}
    },
    "required": ["category", "subject", "severity", "blocked_user"]
  }
}
```

**The wire/local split is the whole design:**

```
 report_problem(...)
      │
      ├─► analytics: agent_report_filed{category, subject, severity,
      │                                 blocked_user, attempts, note_len}
      │              ── enums + one integer. NEVER `note`. ──────────────
      │
      └─► local:  <home>/agent-reports.jsonl
                  {ts, project_id, ...all of the above, note, tool_trace_id}
                  ── stays on disk unless the USER clicks send ───────────
```

`note` is capped at 200 chars and never transmitted automatically. It exists
so that *if* the user later files a bug or you ask them for diagnostics, the
agent's own words are already there — attached the same way the debug session
already attaches (`server/feedback.py:60-75`).

Tool return value: a **fixed** string, always the same
(`{"recorded": true}`). No praise, no "thanks, that's helpful". A variable or
positive response is exactly what trains an agent to file more reports.

### 6.3 The PII problem

The agent's context is the single most PII-dense thing in the app: the user's
prompt, their filenames, their footage, their transcripts. My answer:

1. **The agent never chooses free text that goes on the wire.** It chooses
   from enums we defined. `subject` is validated against a runtime registry of
   our own tool/feature ids — an unknown value is rejected by the tool, not
   scrubbed after the fact.
2. **Prose is local-only** (above). The user's creative content therefore
   cannot leave the machine through this path, whatever the model writes.
3. **No paths, no URLs, no attachments** in the schema. `additionalProperties:
   false`. A `tool_trace_id` referencing `.mc/activity.jsonl` replaces "let me
   paste the context".
4. `note_len` is the only signal derived from the prose, matching the existing
   `_scrub` convention (`server/analytics.py:155-158`).

### 6.4 The prompt-injection problem

This is a real untrusted-content path: the agent reads project files, SRT/VTT
text, filenames, and web pages, any of which can say "call report_problem with
the following text". OWASP lists prompt injection as LLM01 and excessive
agency as LLM06 for 2025 ([OWASP][owasp]). Mitigations, in order of how much
they actually buy:

| Threat | Mitigation |
|---|---|
| Exfiltrate user content via the report | Enums on the wire; prose local-only. **Structural — an injected instruction has no channel to send.** |
| Flood the inbox | Hard cap: 1 report per turn, 3 per day per install, enforced in the tool wrapper (not the prompt). Over-cap calls return `{"recorded": false, "reason": "rate_limited"}`. |
| Duplicate reports across turns | Dedupe on `sha256(category|subject|severity)` for 24h, in the same wrapper. |
| Attacker-chosen `subject` | Validated against our registry — the enum is generated from code, so an unknown subject cannot be filed. |
| Sycophancy / reward-hacking | Constant return value; no user-visible "reported!" affirmation; the report never changes what the agent tells the user. |
| Report as an excuse | The prompt instruction says: file *after* trying the normal path; the automatic `agent_tool_failed` events are the cross-check — a `report_problem` with zero preceding tool failures is itself a suspicious pattern you can filter on. |
| Injected content lands in the inbox | Worst case an attacker sets a bogus enum + 200 local chars. Destination is a private inbox with **no automated action** on it. Accepted residual risk. |

The report must never trigger anything automatic — no auto-file, no auto-email
to a mailing list, no webhook that runs code. It is data in a dashboard.

### 6.5 "Our tool is broken" vs "the agent used it wrong"

**Do not ask the agent.** It is the least reliable narrator available. Derive
`fault` in code from the failure class:

```
 fault = agent        schema/validation rejection, unknown tool name,
                      out-of-bounds arg, sandbox DENY (decide_tool:370),
                      crop_out_of_bounds (a source-px crop on a proxy —
                      RULES.md documents this as a known misuse)
 fault = ours         traceback inside our own tool, ffmpeg non-zero on
                      inputs that passed validation, missing binary,
                      unwritable output, a tool that reported success and
                      produced no file
 fault = environment  missing capability pack, no ffmpeg, Node too old,
                      no network, disk full
 fault = user_declined  confirm/capability/api-key request denied
```

Two corroborating signals make this sharp without any LLM involvement:

- **Retry shape.** Same tool, same project, ≥3 calls in one turn with the same
  failure class ⇒ leaning `ours`. Success on retry N with *different* args ⇒
  `agent`. Track `retry_index` on `agent_tool_failed`.
- **The agent reaching around our tools.** `bash_runs_heavy_media_op`
  (`server/agent_runner.py:128`) already detects the agent driving ffmpeg-ish
  work through Bash. That firing *after* one of our tools failed is the
  strongest "our tool surface has a hole" signal in the codebase, and it costs
  one event to expose. §7.

### 6.6 Does the user see and approve what the agent sends?

**Two tiers, and I will defend the split.**

- **The enum event: no per-report approval.** It is anonymous, closed-vocabulary
  telemetry, materially the same as `render_failed`, and it is covered by the
  analytics consent (§9). Asking permission 30 times a session for
  `{tool_missing, audio_ducking, degraded}` trains users to click through
  dialogs, which is worse for privacy than not asking.
- **Anything with prose: explicit approval, every time.** Never automatic.
  The surfacing is a quiet, non-blocking affordance — the agent's turn already
  streams status to the chat, so add a small "the agent logged an issue"
  chip that opens a review sheet showing *exactly* the JSON line, with
  **Send** / **Discard**. Same trust model the debug-report flow already uses
  (`web/src/studio/DebugReportModal.jsx`).
- **And a global disclosure:** the "what we collect" doc (§9) lists
  `agent_report_filed` and its exact field list. A user who does not want the
  agent reporting anything turns off product analytics and the tool is not
  registered at all — the §9.5 enforcement rule.

Anti-position, stated fairly: you could require approval for every report and
get a cleaner consent story. I reject it because the approval prompt itself
becomes the noise, and because an enum from a closed set carries no more
information about the user than `render_failed` does.

---

## 7. Feature demand from behavior alone

This is the human's core ask and it deserves the honest answer: **behavioral
signals tell you where users get stuck, not what they wish existed.** You can
get most of the way with signals, and the remaining gap needs exactly one
LLM-labelled field (§7.3).

### 7.1 Signal → inference map

| Signal (from §4 events) | Inference | Strength |
|---|---|---|
| `agent_tool_failed{fault=environment, failure_class=missing_capability}` clustering on one pack | that pack must be bundled or provisioned earlier | **strong** |
| `bash_runs_heavy_media_op` fires after one of our tools failed (`agent_runner.py:128`) | **our tool surface has a hole the agent is routing around** | **strong** |
| `agent_report_filed{category=tool_missing}` grouped by `subject` | a named missing capability | **strong** (it is literally the answer, just low-volume) |
| `agent.adopt` immediately followed by `edit.undo` (in `editor_session_summary`) | the agent's edit was rejected — an agent-quality bug, not a feature gap | **strong** |
| `render_failed` → no further `render_started` in that project, ever | the failure ended the project. Highest-priority bug class. | **strong** |
| `project_created` with no `agent_turn_started` | onboarding/auth friction, not a feature gap | **strong** |
| `agent_turn_started` with no `export_completed` in 7 days | activation failure; needs the `last_stage` breakdown from `project_abandoned` to be actionable | medium |
| `undo_rate` high on a specific `fields_touched` value | that control is hard to hit — a UX fix (bigger target, snapping, a preset), not a new feature | medium |
| N identical property edits on the same field in one session | that value wants a preset or a default | medium |
| `editor_feature_first_used` never fires for a feature with high usage among those who find it | **discovery** problem — the feature exists and is hidden. Cheapest wins live here. | medium-strong |
| asset browse / `@`-mention returns 0 results, then an upload follows | the user expected an asset we do not have or cannot find | medium |
| `render_superseded` frequent | the user is iterating faster than render — argues for the RULES.md "edit live, render rarely" north star, i.e. more live-previewable ops | medium |
| Rage-click / repeated no-op click | friction *somewhere* | **weak** — needs a per-target definition first; do not build it at P0 |
| Time-on-panel | almost nothing on a desktop app where the window can be idle behind another | **weak — do not build** |

### 7.2 Being honest about the ceiling

Nothing above tells you that a user wanted auto-captions if they never looked
for auto-captions. Behavioral inference is a map of *attempted* paths.
Unattempted demand is invisible to it. Two honest routes out:

- **The agent's read on intent** (§7.3) — the only in-product source that sees
  the ask before it becomes an attempt.
- **One contextual question at the right moment** (§8.3 / P1) — event-triggered
  prompts get 25–40% response vs 5–15% for a static widget ([Perspective][persp]).
  One question after a first export beats a permanent feedback button.

### 7.3 `intent_class` — feature demand from the prompt, without the prompt

The user's prompt is the highest-signal, most sensitive payload in the app. The
compromise: the agent (which already has the prompt in context) labels it with
**one value from a closed list of ~20**, and only the label ships.

```
 user prompt (never sent) ──► agent labels ──► intent_class enum
                                                     │
   e.g. add_captions | beat_sync_cuts | generate_broll | remove_background
        color_match  | add_music      | resize_platform | speed_ramp
        voiceover    | hook_rewrite   | subtitle_translate | zoom_punch
        clean_audio  | logo_overlay   | thumbnail       | other
                                                     ▼
                              agent_turn_completed{intent_class}
```

What you learn: the top-10 things people ask for, ranked, without reading one
prompt. Cross it with `agent_turn_failed` / `agent_tool_failed` and you get
**"the most-requested thing we are worst at"** — which is the single most
useful number in this whole document.

Weaknesses, stated: LLM labels are noisy; `other` will be the largest bucket
early and its *contents* are exactly what you cannot see; and an injected
prompt can flip a label (harm: one bad row in a histogram — acceptable). Grow
the enum from what shows up in the local `agent-reports.jsonl` on *your own*
machine, not by guessing.

---

## 8. Nightly / scheduled reporting (idea #6)

### 8.1 Verdict: mostly the wrong shape

Idea #6 conflates three different needs. Split them:

| The need | Right mechanism | Nightly? |
|---|---|---|
| Events survive an offline/crashed machine | durable spool + **flush at next launch** (§5.4) | **no** — event-driven |
| Live events get retried | already done by the PostHog SDK's queue+retry | **no** — do not reinvent |
| *You* find out what happened without opening a dashboard | **PostHog subscription** — daily/weekly email or Slack, with an optional AI summary ([PostHog][phsub]) | yes, but PostHog schedules it |
| You hear about a spike immediately | PostHog error-tracking alerts | no — threshold-driven |

A launchd job or an Electron `setInterval` nightly uploader buys nothing the
SDK does not already do, and costs you: a scheduler to debug, a "did it run"
question, a process that wakes a sleeping Mac, and a second code path that can
leak. **Reject the schedule; keep the durability.**

"Next launch" is also strictly better than "nightly" for a desktop app: a
laptop is closed at 3am, and the crash you care about most is the one where
the app never came up — which by definition is followed by a relaunch.

### 8.2 If you want one scheduled thing, this is it

There is exactly one payload a launch-triggered flush cannot carry: a rollup of
data that is **too big or too private to send as events** but useful in
aggregate — local render-log rollups, session-recorder digests. My position:
**do not build it.** The aggregate you would compute locally is the same
aggregate PostHog computes from the events in §4, and the events are auditable
line by line while a local rollup is a black box that ships whatever it
happens to contain. Skipped: local rollup uploader. Add it when a specific
question provably cannot be answered from the taxonomy.

### 8.3 How the developer actually gets a digest

```
 PostHog
   ├─ Insight subscription, daily 08:00 → email (+ optional AI summary)
   │     · activation rate, render success rate, TTV median
   │     · top failure_class, top intent_class, top agent_report subject
   ├─ Error-tracking alert → immediate: new issue, or >10 in 1h
   └─ Dashboard "Beta health" — one screen, opened only when the mail is bad
```

Subscriptions support daily/weekly/monthly with a chosen time, email or Slack,
and an AI-generated change summary ([PostHog][phsub]). That is the digest, it
costs zero code, and it cannot break. Add one Slack channel if email gets
ignored.

Cost: PostHog's free tier is 1M events/month, 100k exceptions, 1.5k survey
responses, 1-year retention ([PricePulse][phprice], [Beton][phbeton]). At the
volumes in §4 — roughly a few hundred events per active user per week — you
will not approach it during beta. This plan has **no recurring bill.**

---

## 9. Privacy, consent & compliance

### 9.1 The legal analysis

**GDPR (if any EU user installs it).** Two gates, and people conflate them.

- *Gate 1 — ePrivacy Art. 5(3).* Storing or accessing information on a user's
  terminal equipment needs consent unless strictly necessary for a service the
  user requested. It is **technology-neutral, not cookie-specific**: the EDPB's
  Guidelines 2/2023 (adopted Oct 2024) explicitly cover locally-generated
  information collected through an API, on-device hashed identifiers, and
  IP addresses originating from the terminal equipment ([EDPB][edpb],
  [Inside Privacy][ip]). Your per-install `device_id` in `settings.json`
  (`server/settings.py:79`) is squarely in scope. Analytics is not "strictly
  necessary." **⇒ EU users need consent.**
- *Gate 2 — GDPR Art. 6 lawful basis.* Legitimate interest is arguable for
  anonymous product analytics, **but it does not substitute for the Art. 5(3)
  consent** ([Secure Privacy][sp], [Cookiebeam][cb]). Clearing gate 2 does not
  clear gate 1.
- *Pre-ticked boxes are not valid consent* under GDPR — consent needs an
  affirmative act. So "opt-out with notice" is not EU-compliant consent, and
  neither is a first-run screen with the boxes already checked.

**CCPA/CPRA.** No sale or sharing of personal information here, and the data
is not linked to an identifiable person. A privacy notice and a deletion route
are the practical obligations. Much lower bar than GDPR.

**Apple, for a notarized non-App-Store download.**
- `PrivacyInfo.xcprivacy` privacy manifests and the declared-reason API rules
  are **App Store submission requirements**, enforced at submission through
  App Store Connect; macOS apps outside the Mac App Store are not in scope
  ([Apple developer news][apple], [Purchasely][purch]).
- **ATT does not apply** — no IDFA, no cross-app tracking, no ad SDK.
- Notarization is a **malware scan**, not a privacy review
  ([Apple Support][applesup]).
- **⇒ Nothing Apple requires blocks this design.** Ship a plain privacy page
  on the website anyway; it costs nothing and you will need it for the Mac App
  Store later.

**The real risk is not the law. It is the payload.** The most sensitive things
in this app are the user's unreleased video and their prompts. The current code
already refuses to send prompt bodies (`server/analytics.py:38,155-158`) and
already redacts paths in exception frames (`:108-118`). Every design here
preserves that: enums on the wire, prose on disk, no replay, no media, no
filenames.

### 9.2 The product decision

**One first-run consent screen, two independent switches.** The setup window
already exists and already blocks for provisioning
(`desktop/main.js` `setupSend`), so this adds a step to a screen the user is
already looking at — no extra friction.

```
 ┌──────────────────────────────────────────────────────────┐
 │  Help improve OpenNolan                                  │
 │                                                          │
 │  OpenNolan is made by one person. Two optional signals    │
 │  make it better. Neither ever includes your video, your   │
 │  audio, your prompts, your file names, or your file paths.│
 │                                                          │
 │  [x] Crash & error reports                               │
 │      When something breaks, send the error and the        │
 │      stack trace. No file contents, no paths.             │
 │                                                          │
 │  [x] Anonymous usage                                     │
 │      Which features get used and whether renders          │
 │      succeed — counts and durations only, tied to a       │
 │      random install ID, never to you.                     │
 │                                                          │
 │  Change either any time in Settings. Read what we         │
 │  collect →                                                │
 │                          [ Continue ]                     │
 └──────────────────────────────────────────────────────────┘
```

**Defaults: both ON, and I will name the tradeoff rather than hide it.**
Pre-ticked is not valid EU consent (§9.1). Unticked-by-default is legally
cleaner and will cost you most of your beta data at the exact moment you need
it most. My recommendation for *today*, with zero users and no EU marketing:
**both pre-ticked, and treat it as a documented risk decision, not a
compliance claim.** Two conditions on that: revisit before any public launch
or EU distribution, and see §9.6 for the cheap change that lowers the exposure
a lot.

**Separable?** Yes — two switches, because the arguments differ. "Send the
crash so I can fix the thing that just ate your work" is an easy yes for
almost everyone; "tell me which buttons you press" is a different ask.
VS Code ships four graduated levels — off / crash / error / all — and it is
the right instinct, but four levels on a first-run screen for a one-person app
is over-engineering. Two switches map onto their `crash` and `all`
([VS Code telemetry][vsc]).

**Is the agent-report path separately consented?** No — it rides on the
"anonymous usage" switch, because the wire payload is an enum event
indistinguishable in kind from `render_failed`. It gets its own line in the
"what we collect" doc. Prose is separately consented *per report* by the send
button (§6.6).

### 9.3 Retention and deletion

- **Retention: 12 months.** Matches PostHog's free-tier retention
  ([PricePulse][phprice]) and is short enough to be a real answer.
- **Deletion:** Settings → "Reset my anonymous ID" mints a new `device_id`
  (breaking all future linkage) and clears the local spool. Historical rows
  need a person-deletion on your side, so publish an email address and honour
  it. Say exactly this in the doc rather than implying a self-serve delete you
  do not have.
- **Local data the user owns outright:** `feedback.jsonl`,
  `agent-reports.jsonl`, `.agents/tools/logs/ui-sessions/*.ndjson`. A
  "Reveal in Finder" button is more honest than any deletion API.

### 9.4 The user-facing "what we collect" doc

One page, in-app link + website. Table with three columns: **event name ·
every property · why**. Auto-generate it from the same `TAXONOMY` dict the
contract test in §4.1 walks, so it can never drift from the code. That single
choice — one declaration, driving the test, the doc, and the allowlist — is
what keeps this honest a year from now.

### 9.5 How the opt-out is enforced technically

`server/analytics.py:128-130` refuses to build a client at all when opted out.
**Every new path must inherit that shape, not merely check a flag.** Concretely:

| New path | Enforcement |
|---|---|
| Editor rollup sink | frontend asks `/api/settings/analytics` once; when disabled the sink is **not installed** — `dbg.event` keeps its existing single-sink behavior |
| `report_problem` tool | when opted out, the tool is **not registered** in `_default_client_factory` (`agent_runner.py:911`) — the agent has no such tool to call, and cannot be talked into it |
| Error spool | opt-out **deletes** the file and skips the flush (§5.4) |
| Render/agent events | go through `analytics.capture` — inherited for free |
| `consent_pending` (new) | `is_enabled()` returns False while consent has not been answered, so `app_opened` at `server/app.py:443` cannot fire pre-consent |

The last row is the fix for the §2.3 finding, and it extends the existing
"never constructs a client" test rather than adding a new mechanism.

### 9.6 Two cheap privacy wins worth taking now

1. **Disable GeoIP / drop the IP.** PostHog resolves the client IP to geo by
   default. An IP taken from the terminal equipment is exactly what the EDPB
   flags under Art. 5(3) ([EDPB][edpb]). You do not need country-level data
   during a beta with hand-counted users. One constructor flag.
2. **Bucket every number that could fingerprint.** `output_bytes_bucket`,
   `duration_bucket` in §4.3 rather than exact bytes and exact seconds. An
   exact file size plus an exact duration plus a timestamp is close to a
   fingerprint of a specific video.

---

## 10. Phased build plan

Effort: S ≤ half a day, M ≈ 1–2 days.

### P0 — before ANY beta user (~1 week)

| # | Change | Files | Success condition | Test |
|---|---|---|---|---|
| 0.1 | Render funnel at the one choke point: `render_started/succeeded/failed` + `failure_class` enum derived from the error string (string never sent) | `server/render_jobs.py` (`_set`:309, failure sites 342/359/378/401/416/432/434/563) | a dev render emits exactly one started + one terminal event with the same `job_id` | `tests/contracts/` — assert failure emits an enum `failure_class` and **no** raw error text, no path; assert `superseded` does not double-count |
| 0.2 | **`export_completed`** at the publish commit (`_execute_render` `.part.mp4` publish, `render_jobs.py:480`) with `first_export`, `watermark_free`, `resolution`, `hdr` | `server/render_jobs.py`, `server/settings.py` (a `first_export_done` flag, same shape as `app_first_run_done` at `app.py:448`) | fires exactly once per publish; `first_export` true exactly once per install | contract test on the once-per-install flag |
| 0.3 | Agent-turn events + `intent_class` | `server/agent_runner.py:1924-2007` (`run_turn`), `server/app.py:1164` | every turn emits started + one terminal event; cost/num_turns match `TurnResult` | assert `text` never appears in properties; assert `failure_class` covers each raise path |
| 0.4 | Automatic `agent_tool_failed` with code-derived `fault` (§6.5) | `server/agent_runner.py` (`_text_result`:849, `decide_tool`:370, `_run_render`:1548, `_run_media_op`:1671) | a forced schema rejection ⇒ `fault=agent`; a forced ffmpeg failure ⇒ `fault=ours` | one test per fault class |
| 0.5 | Durable error spool + flush on `app.ready` (§5.4) | `desktop/main.js:65-109`, `server/analytics.py` | POST failure leaves one JSONL line; relaunch drains and truncates; opt-out deletes it | node/py unit: simulate POST failure → line written; flush drains, caps at 200/2MB, drops >14d |
| 0.6 | First-run consent screen + `consent_pending` gate + `consent_set` event | `desktop/main.js` (setup window), `web/src/…` setup UI, `server/settings.py`, `server/analytics.py:80-92` | with consent unanswered, **no PostHog client is constructed** | extend `tests/contracts/test_analytics.py:80-89` — the existing "never constructs" assertion, new precondition |
| 0.7 | Editor rollup sink behind `dbg.event` + key whitelist | one new file under `web/src/debug/`, `web/src/studio/Studio.jsx` (unmount hook only) | one `editor_session_summary` per studio session; zero new `dbg.event` call sites | vitest: rollup emits counts only; an event carrying `name:"my clip.mov"` (`Studio.jsx:587`) drops the filename |
| 0.8 | `TAXONOMY` declaration + allowlist contract test + generated "what we collect" doc | new `server/taxonomy.py`, `tests/contracts/test_taxonomy.py`, `docs/privacy/what-we-collect.md` | every `capture()` name in the codebase appears in `TAXONOMY`; every property is declared | the test **is** the success condition; grep-based check that no `capture(` uses an undeclared name |
| 0.9 | Fix the feedback lie: surface `emitted`/`delivered`; on failure say "saved locally — I'll get it next time you're online" and retry on next launch | `web/src/App.jsx:300,319-320`, `server/feedback.py:191` | with the relay unreachable, the UI does **not** claim it was sent | component test with a failing relay |
| 0.10 | `disable_geoip` + bucketed numerics (§9.6) | `server/analytics.py:132-139` | constructed client carries the flag | assert the constructor kwargs |
| 0.11 | Route provisioning failures to PostHog (`provision_failed{stage}`) | `server/app.py:1031-1045` (error frame), `lib/provision.py` | a forced venv failure emits one event | contract test |

### P1 — during beta

| # | Change | Success condition |
|---|---|---|
| 1.1 | `session_started`/`session_ended` ⇒ crash-free-session rate | rate computable in PostHog; a `kill -9` shows as abnormal |
| 1.2 | Source maps uploaded per release | a renderer stack in PostHog names a real `.jsx` line |
| 1.3 | `report_problem` MCP tool + local `agent-reports.jsonl` + the review sheet (§6) | rate limit and dedupe hold under a forced loop; prose never on the wire (contract test) |
| 1.4 | One contextual micro-survey after `export_completed` and after `render_failed` — hand-rolled against `/api/feedback`, no `posthog-js` | ≥20% response on the first-export prompt |
| 1.5 | `editor_feature_first_used` + discovery-vs-usage view | every feature in the enum has a first-use rate |
| 1.6 | PostHog daily subscription + error alerts (§8.3) | an email lands with activation, render success, top failure |
| 1.7 | `project_abandoned` derivation at launch | `last_stage` breakdown populated |
| 1.8 | Feature flags — a kill switch for a shipped binary | one flag flips one code path without a release |

### P2 — later

- `@sentry/electron` `ElectronMinidump` **only if** crash-free rate is bad and
  `renderer-gone` reasons do not explain it (§5.2).
- `posthog-js` in the renderer **only if** you need remote-configurable
  surveys or flags without a release. Note it enables replay — leave it off
  explicitly, not by omission.
- PostHog Surveys proper (replaces 1.4's hand-rolled prompt).
- `agent_report_filed` → an eval-set feeder: every confirmed `fault=ours` row
  becomes a regression case. This is the 2025-26 norm for agent products, and
  it is where the whole loop finally closes.

---

## 11. What I am deliberately NOT proposing

| Not building | Why | What would change my mind |
|---|---|---|
| **Session replay** | The screen is the user's unreleased creative work. No masking config makes a video canvas safe. | Nothing. This one is permanent. |
| **PostHog LLM Analytics generation capture** | It works by wrapping the provider SDK and capturing prompts/completions ([PostHog][phllm]). Here the model runs behind the Claude Agent SDK subprocess, it is the *user's* BYOK key and the *user's* money, and the payload is their prompt. §4's `agent_turn_*` events give latency, cost, tool counts and failure class with none of that. | If a *local-only* trace viewer is wanted for debugging, that is `server/activity.py` and the session recorder, not PostHog. |
| **OTel GenAI spans** | Right standard, wrong scale — it is a still-in-development spec ([OTel][otelblog]) aimed at distributed backends. A collector + exporter for a single-process desktop app is pure overhead. | A second surface (a cloud render service) appears. |
| **Sentry now** | It is the better crash tool, but its unique win here is minidumps, and you already catch the *fact* of native crashes (`desktop/main.js:673-674`). A second vendor = second SDK, second DSN, second privacy disclosure, second dashboard, split inbox. | Crash-free rate under ~99% with unexplained `renderer-gone`. |
| **Nightly cron / launchd uploader** | §8.1. | A payload appears that genuinely cannot be an event *and* answers a question the taxonomy cannot. |
| **Prompt or media content transmission, ever** | It is the one thing that would end trust in a tool people make unreleased work in. | Never automatically. An explicit per-report user click only (§6.6). |
| **A schema registry (Avo/Segment Protocols)** | ~32 events, one developer. §4.1's test + `TAXONOMY` is the same guarantee in 40 lines. | A second engineer, or >60 events. |
| **Heatmaps, experiments, A/B, data warehouse** | Need traffic you will not have for a year. | Hundreds of weekly active users. |
| **Per-action editor events** | Would 20× event volume for a question `editor_session_summary` already answers. | A specific interaction needs frame-level timing — and then it belongs in the local recorder, which already does it. |
| **`_scrub` as a runtime allowlist** | A runtime allowlist means a new event silently loses properties in production and you debug it blind. Test-time enforcement fails loudly at review. | A contributor lands an event without a test. |
| **Local rollup uploader** | §8.2 — an unauditable payload where the taxonomy already answers the question. | A named question that provably needs it. |

---

## 12. Open questions for the human

1. **EU exposure.** Are you willing to ship both consent boxes pre-ticked and
   carry the documented ePrivacy risk (§9.2), or do you want the anonymous-usage
   box unticked by default and accept losing most beta data? **This is the only
   decision in this doc I cannot make for you.** My recommendation: pre-ticked
   now, revisit before public launch.
2. **Is `https://www.opennolan.com/api/feedback` deployed?**
   (`server/feedback.py:36`) If not, every beta feedback report is being
   silently kept on the reporter's own disk while the UI says it was sent
   (`web/src/App.jsx:320`). P0.9 fixes the message; only you can fix the relay.
3. **Watermark.** `export_completed{watermark_free}` needs a definition. Is
   there a watermark in the build today, and what flips it?
4. **Agent prose.** Do you want the agent's 200-char `note` to reach you at
   all — via a user-approved send (§6.6) — or should it stay purely local and
   only ever arrive attached to a bug report the user files themselves?
5. **A support email for erasure requests** (§9.3). Needed for the privacy
   page. `feedback@`?
6. **`intent_class` enum contents** (§7.3). I listed 16 guesses. Yours will be
   better — you know what people ask this app for.
7. **Retention.** 12 months, or shorter?
8. **Digest cadence and channel** — daily email, or Slack? Daily is right
   during beta; weekly after.
9. **Do you want `internal` to also gate ingestion?** Today
   (`server/analytics.py:51-65`) it flags but still sends. Cheap either way;
   flagging is more useful because you can compare.

---

## Sources

[vsc]: https://code.visualstudio.com/docs/configure/telemetry
[vscfaq]: https://code.visualstudio.com/docs/supporting/faq
[obs]: https://obsidian.md/privacy
[ray]: https://www.raycast.com/privacy
[oaf]: https://growthmethod.com/object-action-framework/
[mp]: https://docs.mixpanel.com/docs/data-structure/events-and-properties
[amp]: https://amplitude.com/blog/analytics-tracking-practices
[pherr]: https://posthog.com/docs/error-tracking
[phllm]: https://posthog.com/docs/llm-analytics/basics
[phtrace]: https://posthog.com/docs/llm-analytics/traces
[phsub]: https://posthog.com/docs/data/subscriptions
[phpy]: https://posthog.com/docs/libraries/python
[phrn]: https://posthog.com/docs/libraries/react-native
[phprice]: https://www.getpricepulse.com/companies/posthog-pricing.html
[phbeton]: https://www.getbeton.ai/blog/posthog-pricing-teardown/
[phvs]: https://posthog.com/blog/best-error-tracking-tools
[sentryelec]: https://docs.sentry.io/platforms/javascript/guides/electron/features/native-crash-reporting
[sentryrh]: https://docs.sentry.io/product/releases/health/
[jvi]: https://www.jviotti.com/2021/12/08/debugging-electronjs-native-crashes-on-macos.html
[otel]: https://opentelemetry.io/blog/2026/genai-observability/
[otelblog]: https://opentelemetry.io/blog/2026/genai-observability/
[owasp]: https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/
[edpb]: https://www.edpb.europa.eu/system/files/2024-10/edpb_guidelines_202302_technical_scope_art_53_eprivacydirective_v2_en_0.pdf
[ip]: https://www.insideprivacy.com/advertising-marketing/edpb-issues-draft-guidelines-on-technical-scope-of-eprivacy-directive-storage-and-access-rules/
[sp]: https://secureprivacy.ai/blog/legitimate-interest-vs-consent-gdpr
[cb]: https://cookiebeam.com/guides/legitimate-interest-vs-consent-cookies-2026
[apple]: https://developer.apple.com/news/?id=pvszzano
[purch]: https://docs.purchasely.com/docs/apples-privacy-manifest-requirement
[applesup]: https://support.apple.com/en-us/102445
[persp]: https://getperspective.ai/blog/in-app-feedback-tools-in-2026-9-options-compared
[refiner]: https://refiner.io/blog/in-app-feedback-best-practices/
[figma]: https://www.figma.com/blog/keeping-figma-fast/

**Desktop / Electron telemetry norms**
- VS Code telemetry — four levels (off / crash / error / all), crash reports
  separable from error and usage data; in-product opt-out notice for GDPR:
  [docs][vsc], [FAQ][vscfaq]
- Obsidian — collects no telemetry at all; third-party plugins are *forbidden*
  from client-side telemetry: [privacy policy][obs]
- Raycast — local-first; on-device stats explicitly never sent as telemetry:
  [privacy policy][ray]

**Event taxonomy discipline**
- Object-action naming, past tense, endorsed by Amplitude/Mixpanel/PostHog:
  [Growth Method][oaf]
- Event = what, property = who/where/why/how — the anti-bloat rule:
  [Mixpanel][mp]
- Duplicate-event failure mode and periodic audits: [Amplitude][amp]

**Creative-tool analytics.** Weakest research area — I found no published
telemetry taxonomy from a video/timeline editor. Figma's engineering writing
covers rendering performance and a fixed test-hardware fleet
([Keeping Figma Fast][figma]) but not product instrumentation. §4.3's
render/export funnel and §7's undo-rate-as-friction map are therefore
**reasoned from first principles plus general product-analytics practice, not
copied from a published creative-tool taxonomy.** Flagging that honestly.

**Crash reporting**
- Native crashes need `crashReporter` + minidumps; JS handlers cannot see a
  renderer/GPU crash; renderer/GPU minidumps carry minimal context anyway:
  [Sentry Electron][sentryelec], [jviotti][jvi]
- PostHog Error Tracking: grouping, source maps and symbol sets; no minidump
  ingestion documented: [PostHog][pherr]
- Crash-free session rate definition and release health: [Sentry][sentryrh]
- PostHog vs Sentry positioning ("PostHog focuses on the user, Sentry on the
  code"): [PostHog's own comparison][phvs]
- Python SDK buffers in memory; events lost if the process dies before flush.
  Mobile SDKs persist to device storage; Python does not:
  [Python][phpy], [React Native][phrn]

**AI-agent observability**
- OTel GenAI conventions: tool calls as child spans, `gen_ai.*` attributes,
  spec still in development: [OpenTelemetry][otel]
- PostHog LLM Analytics: traces/spans/generations, tool-call extraction, cost
  from model + token counts — works by wrapping the provider SDK:
  [basics][phllm], [traces][phtrace]
- OWASP Top 10 for LLM Applications 2025 — LLM01 prompt injection, LLM05
  improper output handling, LLM06 excessive agency: [OWASP][owasp]

**In-app feedback**
- Event-triggered in-app prompts 25–40% response vs 5–15% for a static
  widget: [Perspective AI][persp]
- Ask right after the experience; contextual beats generic:
  [Refiner][refiner]

**Privacy law and platform rules**
- EDPB Guidelines 2/2023 on the technical scope of Art. 5(3) ePrivacy —
  technology-neutral; covers locally-generated info via an API, on-device
  hashed identifiers, and IPs from terminal equipment:
  [EDPB PDF][edpb], [summary][ip]
- Legitimate interest does not override the Art. 5(3) consent requirement:
  [Secure Privacy][sp], [Cookiebeam][cb]
- Privacy manifests are an App Store submission requirement; macOS outside the
  Mac App Store is not in scope: [Apple developer news][apple],
  [Purchasely][purch]
- Notarization is a malware scan, not a privacy review: [Apple][applesup]

**Cost**
- PostHog free tier: 1M events/mo, 100k exceptions, 1.5k survey responses,
  1-year retention, then usage-based: [PricePulse][phprice], [Beton][phbeton]
</content>
</invoke>
