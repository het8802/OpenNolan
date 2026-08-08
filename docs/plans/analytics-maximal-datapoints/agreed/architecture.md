# Analytics architecture — the shape of the change

STATUS: PLAN · rev 1
Companion to [`plan.md`](plan.md) (STATUS: AGREED, approved by `codex` after three
code-backed review rounds). The plan holds the reasoning, the 14-item decision
record and the 130-row catalog. This document is the shape: what exists, what it
becomes, and in what order to build it.

Reasoning is **not** restated here. If you want to know *why* `export_completed`
is defined by a receipt rather than by a file existing, read `plan.md` §1 item 3.

---

## 1. Today — four capture systems, zero joins

The instrumentation is not missing. It is **fragmented into four independent
sinks that share no identity**, so nothing can be joined to anything.

```text
                      WHAT EXISTS TODAY

 ELECTRON MAIN                RENDERER
 ─────────────                ────────
 reportDesktopError           dbg.event(type, data)
 desktop/main.js:65           recorder.js:136
        │                            │
        │                            ▼
        │                     push(type, rec)
        │                     recorder.js:113
        │                            │
        │            ★ DIVERGENCE 1  │
        │            recorder.js:114 │  if (!state.on) return
        │            22 semantic call sites in Studio.jsx and
        │            StudioPreview.jsx are NO-OPS unless the
        │            user manually turned recording on.
        │                            │
        │                            ▼
        │                     .ndjson on disk — never uploaded
        │
        │  ★ DIVERGENCE 2   desktop/main.js:98
        │  https.request POST /capture/ straight to PostHog.
        │  Bypasses _scrub (:145), _before_send (:108) and any
        │  future validate_event. Carries device_id only (:90).
        │
        └──────────────────────────────┐
                                       │
 BACKEND                               │
 ───────                               │
 analytics.capture(event, props)       │
 analytics.py:166                      │
        │                              │
        ▼                              │
 _scrub(props)  :145                   │
 + {env, internal}  :68                │
        │                              │
 ★ DIVERGENCE 3   analytics.py:174     │
 distinct_id = settings.device_id()    │
 and NOTHING else. No session_id, no   │
 project_id, no turn_id, no job_id.    │
        │                              │
        ▼                              ▼
 ┌─────────────────────────────────────────────────────────┐
 │                        PostHog                          │
 │  5 product events. export_completed does not exist in   │
 │  app code at all — only in a test at                    │
 │  tests/contracts/test_analytics.py:99                   │
 └─────────────────────────────────────────────────────────┘

 AGENT LOOP — a fourth path, entirely local
 ──────────────────────────────────────────
 run_turn  agent_runner.py:1924
    │
    ├── tool_use branch  :1969
    │      └─► record_tool_use(name, detail)   activity.py:170
    │             └─► .mc/activity.jsonl  (local, never sent)
    │
    └── tool_result blocks  :782-791
           ★ DIVERGENCE 4   agent_runner.py:791
           block.is_error is READ here and never forwarded.
           run_turn's loop has no tool_result branch at all,
           so the outcome of every tool call is discarded.
```

Two more divergences live inside the render path and are drawn in §4:
`render_jobs.py:176` (supersede written around `_set`, deliberately) and
`render_jobs.py:591` (cache counts flattened into a warning string).

---

## 2. After — one envelope, one validator, one sink

Every source funnels through a single validated envelope. That funnel **is** the
claim: it is what makes `session_id`, `turn_id` and `job_id` exist on the same
events, and therefore what makes six previously-uncomputable metrics computable.

```text
                        WHAT IT BECOMES

 ELECTRON MAIN            RENDERER              BACKEND
 ─────────────            ────────              ───────
 main.js:626              main.jsx (preload     analytics.capture
 MINTS session_id         receives it)          agent_runner
 boot() + launch          dbg.event fan-out     render_jobs
 main.js:685 flush        recorder.js:136       app.py routes
        │                        │                     │
        │                        │  local reducers:    │
        │                        │  rollup.js          │
        │                        │  (per-frame, per-   │
        │                        │   keystroke, seeks  │
        │                        │   NEVER uploaded)   │
        │                        │                     │
        │                        ▼                     │
        │              web/src/analytics/track.js      │
        │              batches + X-ON-Session header   │
        │                        │                     │
        │                        ▼                     │
        │              POST /api/telemetry/events      │
        │              (mirrors the existing           │
        │               /api/telemetry/error :1016)    │
        │                        │                     │
        └────────────────────────┴─────────────────────┘
                                 │
 ══════ ALL FOUR SOURCES CONVERGE HERE — one envelope ══════
                                 │
                                 ▼
                 ┌───────────────────────────────┐
                 │  validate_event()             │
                 │  NEW, analytics.py:172        │
                 │  · unknown event   → drop     │
                 │  · unknown prop    → drop     │
                 │  · reserved substr → reject   │
                 │  · declared numeric→ pass-thru│
                 │    (bypasses the _scrub       │
                 │     substring heuristic)      │
                 └───────────────────────────────┘
                                 │
                                 ▼
                 ┌───────────────────────────────┐
                 │  _scrub  :145 (unchanged)     │
                 │  _before_send :108 (unchanged)│
                 │  + envelope: schema_version,  │
                 │    event_id, install_id,      │
                 │    session_id?, project_id?,  │
                 │    turn_id?, job_id?,         │
                 │    tool_invocation_id?        │
                 └───────────────────────────────┘
                                 │
                                 ▼
                             PostHog

 The private recorder is UNCHANGED and stays local. dbg.event now
 fans out to both: full diagnostic to the recorder only when
 state.on, schema-listed summaries to the reducer always.
```

**Why the funnel is load-bearing, in one line:** `session_id` is minted once in
Electron main and rides the `X-ON-Session` header into `run_turn`, which mints
`turn_id`, which `_build_render_inputs` (`agent_runner.py:1529`) carries into the
job record — so an agent-started render can be attributed to the session that
caused it. Without the funnel each of those is a separate, unjoinable island.

---

## 3. Ordering is the design: the publish commit window

`export_completed` and `publish_partial` are two branches of the same window.
Getting them from the wrong line is how both phase-1 documents got this wrong.

```text
 _publish_final_locked            lib/project.py:530
 ══════════════════════════════════════════════════════════════

 t0  stage src → .final.<uuid>.part.mp4          :594-598
        │
 t1  enter commit_guard (supersede re-check)     :600-601
        │
        ├── may_commit == False  →  return {published: False}   :603
        │   ★ old video AND old receipt untouched. NOT an export.
        │
 t2  unlink the old receipt                      :604
        │
 t3  os.replace(part → final.mp4)                :605
        │
        ╞═══════════ THE CRASH WINDOW OPENS ═══════════╡
        │   new bytes are live; NO receipt describes them.
        │   final_render_status() reads them as STALE, by design
        │   (lib/project.py:561-563).
        │
 t4  persist_doc → edit_decisions.json           :612   (optional)
        │
 t5  atomic_write_json(receipt)                  :616
        ╞═══════════ THE CRASH WINDOW CLOSES ══════════╡
        │
 t6  return {published: True}                    :623
        │
        ▼
 render_jobs.py:576   if not published["published"]:  → superseded
 render_jobs.py:580   rel_out = published["path"]
        │
        └─► emit export_completed  ← THE HOOK GOES HERE

 An exception anywhere in t4..t5 unwinds PAST :580 and lands in
 the outer catches:  render_jobs.py:359  (editor path)
                     render_jobs.py:401  (agent path)
        └─► emit publish_partial  ← THE OTHER HOOK GOES HERE

 HOOK LINE  ≠  DEFINITION LINE
   hook:        render_jobs.py:580   (server layer, has origin+job_id)
   definition:  lib/project.py:616   (the receipt write completing)
   NOT in lib/: lib/project.py:439 states "lib must not depend on
                server", and server/analytics.py imports server.settings.
```

---

## 4. The render job has four writers, not one

`_set` is not a choke point. Three creation sites insert job dicts directly and
the supersede writer deliberately routes *around* `_set`.

```text
 RenderJobStore                        server/render_jobs.py
 ══════════════════════════════════════════════════════════════

  start()          start_with_inputs()      start_op()
  :58 editor       :71 agent                :94 agent_op
  publish_intent   publish_intent derived   publish_intent
    → true         from _normalize_         → false
                   output_path() :612
       │                  │                      │
       ▼                  ▼                      ▼
  self._jobs[id] = {...}  direct literal insert
  :66               :83                     :105
       │                  │                      │
       └──────────────────┴──────────────────────┘
                          │
              ① emit render_queued  :68 / :91 / :114
                 (after the `with self._lock` block closes)
                          │
                          ▼
                    _set(...)  :309
                          │
              ┌───────────┴────────────┐
              │                        │
        superseded?                  normal
        :323 → :324                  :326  self._jobs.update()
              │                        │
              ▼                        ▼
   _mark_superseded_locked        ② emit transition
   :168 → :176                       copy the record INSIDE
      │                              the lock, emit OUTSIDE:
      │                              :322 holds a mutex and
   ★ DIVERGENCE 5                    capture() can block.
      This writer exists BECAUSE           │
      _set's guard would drop it           │
      (:169-173). It must return           │
      a CHANGED flag, or :324 and          │
      a direct call double-emit.           │
      │                                    │
   ③ emit render_superseded                │
      │                                    │
      └──────────────┬─────────────────────┘
                     │
                     ▼
           ④ emit export_completed  :580

 ★ DIVERGENCE 6   render_jobs.py:589-591
   video_compose returns n_scenes / n_cached / n_rendered as
   NUMBERS at video_compose.py:1559-1566. render_jobs flattens
   them into a warning STRING and _set never sees the numbers:

     warnings.append(f"{n_rendered} scene(s) re-rendered, "
                     f"{n_cached} reused from cache")

   Fix: propagate a typed summary onto the job record. Never
   parse it back out of the warning text.
```

---

## 5. Tool correlation needs a pending map, not a field

The SDK splits one tool call across two blocks with different fields. The join
must happen where `project_id` is in scope — and that is **not** `event_of`.

```text
 event_of(message)                    agent_runner.py:752
 ─────────────────────────────────────────────────────────────
   ToolUseBlock     :770-775  →  {name, id, input, detail}
   ToolResultBlock  :782-791  →  {tool_use_id, is_error}
                                  ↑ no name, no project, no turn

 ★ event_of takes ONE argument: `message`. It has no project_id
   and no turn_id, and _drain_unsolicited calls it too (:1902) —
   the path whose entire job is discarding turns that must NOT be
   attributed to the current message. A capture here would both
   fail to join and double-count discarded work.

 run_turn                             agent_runner.py:1924
 ─────────────────────────────────────────────────────────────
   :1956   turn_id = mint()          ← project_id IS in scope
      │
      ▼
   :1960   async for msg in client.receive_response():
      │
      ├── kind == "tool_use"    :1969
      │      pending[block.id] = {tool_name, t_start}
      │      record_tool_use(...)  :1973  (local log, unchanged)
      │
      ├── kind == "tool_result"  ← NEW BRANCH, does not exist today
      │      hit = pending.pop(tool_use_id, None)
      │        hit  → terminal event, outcome from is_error
      │        miss → orphan_results += 1  (cause: unknown)
      │        seen → duplicate_results += 1
      │
      └── type == "result"       :1979
             is_error / num_turns / total_cost_usd → TurnResult

   :1989-1991  except → _error_occurred = True; raise
      │
   :1992-2005  finally:
      │
   ★ :1993   emit agent_turn_completed + agent_tool_rollup HERE
             pending leftovers → outcome='no_result'
             defaults for result fields unset by the raise
      │
   :2006   result.text = "".join(texts)      ← UNREACHABLE when
   :2007   return result                       the turn raised

 The finally runs exactly once on both paths, so no dedupe is
 needed. An emit at :2006 would silently miss every crashed turn
 while the P0 test asserts the opposite.

 TurnResult  :832  carries ONLY text / is_error / num_turns /
 total_cost_usd. doc_changed, artifacts_delta, tool counts and
 stop_reason are NEW work — a before/after doc snapshot around
 the turn, not fields waiting to be read.
```

---

## 6. Surface table

One row per file that changes. `plan.md` §3 gives the per-row hook line and
viability note for all 130 catalog rows; this is the file-level view.

| `file:line` | What changes |
|---|---|
| `schemas/analytics_events.json` | NEW. The one taxonomy: `kind: event\|metric`, property types, priority, sampling class, question, decision, owner |
| `server/analytics.py:172` | NEW `validate_event()` before `_scrub`; allowlist-first so declared numerics survive the substring heuristic |
| `server/analytics.py:174` | Attach the envelope (8 ids) to every capture |
| `server/analytics.py:176` | Increment a durable swallow counter instead of a bare `pass` |
| `server/app.py` (new route) | `POST /api/telemetry/events`, batched, mirroring `/api/telemetry/error` at `:1016` |
| `server/app.py` (new dep) | `X-ON-Session` → `request.state`, available to every route |
| `server/app.py:421` | Add sibling middleware so `HTTPException` 4xx are observable (they bypass this handler today) |
| `server/app.py:580` | Extend the ingest probe; emit `asset_import_finished` + `media_probe_finished` with `asset_id` and `asset_fingerprint` |
| `server/app.py:956` | Change-only `auth_state_observed` |
| `server/app.py:1029` | `provisioning_snapshot` incl. `free_gb`/`proxy_cache_mb` computed here, **not** in `lib/provision.py` |
| `server/app.py:1043` / `:1045` | `pack_install_outcome` at the streaming boundary |
| `server/agent_runner.py:1956` | Mint `turn_id`; before-snapshot of the doc |
| `server/agent_runner.py:1969` | Populate `pending[tool_use_id]` |
| after `:1978` | NEW `tool_result` branch — the join |
| `server/agent_runner.py:1993` | Terminal turn event + tool rollup, in the `finally` |
| `server/agent_runner.py:453` / `:455` | `tool_permission_decided` on **deny and confirm only** |
| `server/agent_runner.py:1529` | Thread `session_id`/`turn_id` into the render inputs |
| `server/render_jobs.py:68`/`:91`/`:114` | `render_queued` with `publish_intent` |
| `server/render_jobs.py:83` | Compute `publish_intent` at creation via `_normalize_output_path` (`:612`) |
| `server/render_jobs.py:176` | Return a changed-flag; emit `render_superseded` once |
| `server/render_jobs.py:322-326` | Copy the transition inside the lock, emit outside it |
| `server/render_jobs.py:359` / `:401` | `publish_partial`, classified against `final_render_status` |
| `server/render_jobs.py:503-605` | Monotonic stage marks for `resolve/proxy/assemble/publish` |
| `server/render_jobs.py:589-593` | Propagate a **typed** render summary; stop flattening numbers into a warning string |
| `server/render_jobs.py:580` | `export_completed` |
| `server/activity.py:194` | Swallow counter |
| `lib/checkpoint.py:273` | Optional module-level observer (no `server` import) |
| `scripts/update_stage.py:73` | Durable local outbox — this is the **primary** stage writer; the agent is told to run it at `agent_runner.py:474-480` |
| `server/editor.py:171`/`:176`/`:191`/`:194` | `browser_proxy_finished` |
| `desktop/main.js:626` | Mint `session_id`; `app_launch_started` |
| `desktop/main.js:685` | Awaited flush on `before-quit` |
| `desktop/main.js:98` | Route through the shared envelope; keep the direct POST only for backend-never-healthy |
| `web/src/analytics/track.js` | NEW. Batcher, session plumbing, `pagehide` beacon |
| `web/src/analytics/rollup.js` | NEW. Pure reducers + `action_digest`. Unit-tested |
| `web/src/studio/model.js` | NEW pure `classifyDocChange(prev, next, hint)` **beside** `summarizeDocChange` (`:501`), never inside it |
| `web/src/studio/Studio.jsx:168`/`:186` | `commit(next, {action_id, feature_id})`; history carries it |
| `web/src/studio/Studio.jsx:97` | One hook in `flash` covers every guardrail and every red toast |
| `web/src/studio/Studio.jsx:313`/`:325` | Replace the bare `.catch(() => {})` — `agent_adopt_failed` |
| `web/src/debug/recorder.js:136` | Fan-out table. `push` (`:113`) and its `state.on` gate are untouched |
| `web/src/api.js:6` | `http_error` from the `!resp.ok` branch; new `fetch` wrapper for network rejects |
| `web/src/App.jsx:1341`/`:1347` | Add the handlers these elements do not have |
| `tests/contracts/test_analytics_taxonomy.py` | NEW. Three contract tests (§7 of the plan) |

**Deliberately untouched.** `recorder.js:113-134` and the NDJSON path — the private
recorder keeps its exact behaviour. `_scrub` (`:145`) and `_before_send` (`:108`) —
still the last gate; `validate_event` runs *before* them, never instead.
`decide_tool` (`:370`), `interp.js`, `mentions.js` and `propertySchema.js` — pure,
and they stay pure; every capture moves to the caller. `lib/project.py` — no
analytics import, ever.

---

## 7. Build order

Dependency-first. Item 2 is first for a reason: six metrics across both phase-1
documents are uncomputable until the ids exist, so building anything that reports
them earlier produces numbers that look real and are not.

```text
 1  taxonomy + validate_event + batch route + track.js
       │        contract test 3 (scrub round-trip) fails today on
       │        prompt_len, prompt_chars, message_len, text_len,
       │        content_len and content_fingerprint. Make it pass.
       ▼
 2  THE ENVELOPE  ← blocker for everything below
       │        session_id (main:626) → header → request.state →
       │        run_turn → _build_render_inputs → job record
       ▼
 3  render lifecycle, all four writers ─┐
       │  queued/started/superseded     │  these two are
       │  typed summary, export at :580 │  independent of
       ▼                                │  each other
 4  agent turn + tool correlation ──────┘
       │  pending map, terminal in the finally
       ▼
 5  session start/end + fatal crash-free
       │  before-quit flush; unclean_timeout is a QUERY, not
       │  an event — the backend is dead by then (main.js:684)
       ▼
 6  editor action_id + session summary + action_digest
       ▼
 7  the five dashboard screens
       ▼
 END-TO-END CHECK
   one synthetic and one manual packaged journey whose counts
   reconcile EXACTLY: install → session → auth → project → turn
   → tool outcomes → human edit → render → receipt-backed export
   → clean close. Then inject one failure per layer and verify
   its bounded class.
```

---

## 8. Three things the diagrams make obvious

**1. The North Star is not unmeasured because it is hard — it is unmeasured
because nobody stood at `render_jobs.py:580`.** §3 shows the receipt commit is a
single, already-serialized, already-guarded line with `origin` and `job_id` in
scope. Every ingredient exists. `export_completed` has lived in
`tests/contracts/test_analytics.py:99` and nowhere else, which is exactly the
failure the taxonomy's "every declared event has a live call site" assertion
exists to catch.

**2. Four of the six divergences are the same mistake: a value is computed and
then discarded one line later.** `is_error` at `agent_runner.py:791`, the cache
counts at `render_jobs.py:591`, the tool outcome that never reaches
`activity.py:170`, and the whole `dbg.event` surface behind `recorder.js:114`.
This is not an instrumentation project so much as a *plumbing* one — the
measurements already happen, they just terminate in a local variable. That is why
P0 is three days and not three weeks.

**3. The join key is the architecture; the events are the easy part.** §2's funnel
exists to make one header reach four processes. Both phase-1 documents wrote
rich catalogs on top of an identity model that did not exist —
`analytics.py:174` sends `distinct_id` and nothing else — and both had to
withdraw metrics in review because of it. If only one thing from this document
gets built, build step 2.

---

## 9. Post-convergence addendum

Added **after** both agents signed off, from hands-on verification against the
running system and the live PostHog project. Everything here was checked by
executing it, not by reading. Lettered to match the plan's §12.

### 9A. `install_id` is per-`OPENNOLAN_HOME`, not per-machine

`device_id()` (`server/settings.py:80-88`) mints `dev-{uuid4}` and persists it in
`settings.json` at `home()` — and `home()` is `OPENNOLAN_HOME`, else the **repo
root**. `desktop/main.js` sets `OPENNOLAN_HOME` only in the packaged branch, so:

```text
 PACKAGED  home = ~/Library/Application Support/opennolan-desktop
           └─ ONE id per Mac. Survives app updates AND a normal reinstall
              (dragging the .app to Trash does not remove Application
              Support). Lost only if that folder is deleted, or if the app
              name / bundle id changes — do any rename BEFORE beta.

 DEV       home = the repo root of whichever checkout is running
           └─ EVERY worktree and every clone mints its own id, so the
              developer's own machine looks like N separate installs.
```

So the instability is **development-only**; production identity is sound. §12A of
the plan files the fix as P1, not P0, for that reason.

Two consequences that are not obvious:

- The per-home behaviour is *accidentally useful*: a test that points
  `OPENNOLAN_HOME` at a temp dir gets a unique `install_id` for free, which is the
  correlation key §9D relies on. Moving the id to a fixed path **must** therefore
  ship with an `OPENNOLAN_INSTALL_ID` override, or it breaks the e2e test.
- Changing the id source orphans the existing `device_id`. Harmless now (zero real
  users); after beta it would split every user's history in half.

### 9B. Which PostHog project an event lands in

Neither doc mentioned `POSTHOG_KEY` before. The two reporters resolve it
**independently**, and only one of them can see a `.env` file.

```text
                     ┌───────────────────────────────┐
  shell environment  │  export POSTHOG_KEY=phc_dev…  │
                     └───────────────┬───────────────┘
                                     │
                     ┌───────────────▼───────────────┐
                     │  ELECTRON MAIN  (process.env) │
                     │  main.js:53                   │
                     │    process.env.POSTHOG_KEY    │
                     │    || 'phc_s9P9…'  ← PROD     │
                     │                               │
                     │  ✗ never loads .env — there   │
                     │    is no dotenv in main.js    │
                     │  ✗ a Finder-launched packaged │
                     │    app inherits NO shell env  │
                     │    at all, so the fallback    │
                     │    ALWAYS wins there          │
                     └───────────────┬───────────────┘
                                     │ spawn(env: {...process.env})
                                     │ main.js:495 / :518
                     ┌───────────────▼───────────────┐
                     │  PYTHON BACKEND (os.environ)  │
                     │  create_app() → load_env()    │
                     │  env_loader.py:30 load_dotenv │
                     │    reads home()/.env  ← HERE  │
                     │  analytics.py:135             │
                     │    os.environ.get(            │
                     │      "POSTHOG_KEY",           │
                     │      _DEFAULT_KEY)  ← PROD    │
                     └───────────────────────────────┘

  Environment flows parent → child ONLY. The backend loading .env into its
  own os.environ can never propagate back up to Electron main.
```

Three traps this creates:

| Trap | Consequence |
|---|---|
| `.env` reaches Python but not Electron | `desktop_error` keeps going to **production** |
| A typo'd var name | Silent fallback to `_DEFAULT_KEY`. No error, no warning |
| `POSTHOG_KEY=phc_x # dev` | `load_dotenv` only strips ` #` after **two** spaces, so the value becomes `phc_x # dev` and PostHog drops the events |

Fixes in plan §12C: load dotenv in `main.js`, and log the destination at boot so
"which project am I writing to" is answerable in one glance.

`.env` also lives at `home()`, so it is **per-worktree** and gitignored
(`.gitignore:50`, `*.env`). The `orca.yaml` setup hook seeds it from the main
worktree, located without a hardcoded path:

```bash
MAIN=$(git worktree list --porcelain | head -1 | sed 's/^worktree //')
```

### 9C. Where the app writes on a user's Mac

Two roots, two variables. `OPENNOLAN_CODE_ROOT` is read-only program code;
`OPENNOLAN_HOME` is everything writable. `is_packaged()` is true **iff**
`OPENNOLAN_CODE_ROOT` is set (`lib/app_paths.py:101`).

```text
 PACKAGED ── desktop/main.js:497-499 sets both
 ═══════════════════════════════════════════════════════════════════
 OPENNOLAN_CODE_ROOT  /Applications/OpenNolan.app/Contents/Resources
    (read-only)       └─ python/js source, bundled node, uv

 OPENNOLAN_HOME       ~/Library/Application Support/opennolan-desktop
    (all writes)      │  = Electron app.getPath('userData')
                      │
   ├─ settings.json ....... device_id, analytics_disabled
   ├─ .env ................ env_path()   BYOK keys, chmod 600
   ├─ feedback.jsonl ...... local feedback (written before any network)
   ├─ user_styles/ ........ user_styles_dir()
   │
   ├─ projects/ ........... projects_dir()  OPENNOLAN_PROJECTS_DIR
   │   └─ <project-id>/
   │       ├─ artifacts/ ......... edit_decisions.json, receipt
   │       ├─ assets/images|video|audio|music/     KIND_DIRS :52-59
   │       ├─ hf/renders/ ........ per-scene clips  (kind=render)
   │       ├─ renders/
   │       │   ├─ final.mp4 ...... THE deliverable (kind=final_render)
   │       │   └─ proxies/ ....... content-keyed render cache
   │       └─ .mc/ .............. agent chat history, activity.jsonl
   │
   ├─ runtime/ ............ runtime_dir()
   │   ├─ bin/ ............. ffmpeg, ffprobe   (prepended to PATH)
   │   ├─ venv/ ............ managed Python venv
   │   ├─ composition/browsers/   Remotion / Puppeteer / Playwright
   │   └─ manifest.json
   │
   └─ cache/ .............. cache_dir()   ★ SEE 9I — NAME COLLISION
       ├─ opennolan/    OPENNOLAN_CACHE_DIR
       ├─ huggingface/  HF_HOME          ├─ npm/   NPM_CONFIG_CACHE
       ├─ torch/        TORCH_HOME       ├─ pip/   PIP_CACHE_DIR
       ├─ u2net/        U2NET_HOME       ├─ xdg/   XDG_CACHE_HOME
       └─ scratch/      TMPDIR  (always overridden: launchd presets
                                TMPDIR in every macOS GUI process)

 DEV ── main.js sets NEITHER var
 ═══════════════════════════════════════════════════════════════════
 OPENNOLAN_CODE_ROOT  unset → is_packaged() FALSE → every event env:"dev"
 OPENNOLAN_HOME       unset → home() = the REPO ROOT of this checkout
                      so settings.json, .env, projects/ and cache/ all
                      land in the worktree — and route_caches() is
                      gated OFF (lib/app_paths.py:151)
```

The dev row is the mechanism behind 9A: in dev, `home()` *is* the worktree.

### 9D. How the end-to-end check actually runs

§7's exit condition names two journeys but not the mechanism. It splits in two,
because the two harnesses differ in one decisive way:

```text
  pytest                          Playwright  (scripts/dev smoke →
  ──────                          ───────────  npm --prefix desktop
  analytics HARD-DISABLED                      run test:smoke)
  analytics.py:44-45              analytics RUNS NORMALLY
  "pytest" in sys.modules         (separate backend process,
        │                          no pytest in sys.modules)
        ▼                                 │
  assert against a FAKE SINK              ▼
  – taxonomy, envelope, join       real capture → real network
    keys, counts                          │
  – fast, offline, every commit           ▼
                                   PostHog DEV project 544720
                                          │
                                   Query API (NOT the SDK — the
                                   SDK is write-only) with a
                                   PERSONAL key phx_… query:read
                                          │
                                   poll with timeout: ingestion
                                   is not instant
```

Correlating a run to its events without new product code: point
`OPENNOLAN_HOME` at a temp dir, read the freshly-minted `install_id` out of
`<tmp>/settings.json`, and query on it plus `timestamp >= t0`.

Three traps: `OPENNOLAN_INTERNAL=0` does **not** override the
`~/.opennolan-internal` sentinel (a falsy env var falls through to the file check
at `analytics.py:59-65`), so smoke events are `internal: true` — do not filter
them out; the personal API key is a real secret, so skip the assertion cleanly
when it is absent; and a live agent turn spends the developer's own money, so
stub it outside the pre-release run.

### 9I. `cache/` collides with Electron's `Cache/` on a default Mac

**A latent bug, not an analytics decision.** macOS APFS is case-**insensitive**
(case-preserving, which is why folders look case-sensitive in Finder). Verified on
a stock volume: `mkdir CaseTest` then `mkdir casetest` fails with *File exists*,
and both names return the same inode.

`cache_dir()` is `home()/cache` (`lib/app_paths.py:85-90`). In the packaged app
`home()` is Electron's `userData`, which already contains Chromium's `Cache/`:

```text
  ~/Library/Application Support/opennolan-desktop/
      Cache/     ← Chromium HTTP cache, quota-managed, auto-evicted
      cache/     ← cache_dir()          SAME INODE. SAME DIRECTORY.
```

So `route_caches()` would place `huggingface/`, `torch/`, `u2net/`, `npm/`,
`pip/`, `xdg/` and `TMPDIR` **inside a directory Chromium evicts under quota** —
multi-GB model downloads could disappear mid-session, and an Electron
`session.clearCache()` would delete them outright.

Currently **latent**: that folder holds only Chromium's own files, consistent with
`route_caches()` never having run there (gated on `is_packaged()`, and zero
packaged events exist — 9F). Fix while it is still theoretical: rename to
`home()/appcache`, one constant in `lib/app_paths.py:85-90`. Nothing has to
migrate, because nothing was ever written.
