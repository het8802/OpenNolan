# OpenNolan product intelligence — the agreed catalog

STATUS: AGREED
Agents: `claude` (scribe) and `codex` (approver) · converged 2026-08-05
**APPROVED by `codex` after three code-backed review rounds.** 26 defects were raised against
this merged draft and all 26 are closed; the three items in §10 are genuine judgment splits, not
unfixed instrumentation. Counts below are machine-parsed from the tables, not asserted.
Supersedes `claude/plan.md` and `codex/plan.md`. Reviews that produced it:
`claude/review-of-codex.md`, `codex/review-of-claude.md`.

No compliance section — the human deferred it. Assume consent exists and is honored
(`server/settings.py:27` opt-out, checked at init in `server/analytics.py:80`). Assume PostHog
stays; no second vendor.

> **§12 is a post-convergence addendum** added after both agents signed off, from hands-on
> verification against the running app and the live PostHog project. Where it contradicts a
> row above, §12 wins — the contradicted rows carry an inline ⚠ pointer.

**Two rules govern every row below, and they are what the reviews were for:**

1. **Cite the line the `capture()` call goes on.** Not the comment above it, not the enclosing
   `def`. If the call needs a new function, say so.
2. **No row ships without a hook-viability note.** Between us we found 38 rows whose anchor was
   defensible but where a call there cannot work — per-frame, per-keystroke, per-log-line, inside
   a 1.5s or 4s poll, in a pure module, or in `lib/`. The note is how that stops happening.

---

## 1. The decision record

All 14 open items. "Conceded" names the agent who dropped their position.

| # | Item | DECISION | Rationale (evidence) | Conceded |
|---:|---|---|---|---|
| 1 | Join-key envelope | 8 ids: `schema_version`, `event_id`, `install_id` **required**; `session_id`, `project_id`, `turn_id`, `job_id`, `tool_invocation_id` **nullable**. Minting + threading in §7. **`session_id` is minted in Electron main, not the renderer**, and propagates to agent renders through `turn_id`. | `server/analytics.py:172-174` sends only `distinct_id` + `env` + `internal`; `install_id` already exists as `settings.device_id()` (`settings.py:80`). claude first claimed `session_id` was NULL by construction on agent renders; **codex refuted it and was right**: the agent turn arrives via `POST /chat` **from the renderer**, so it carries the header, `turn_id` inherits it, and `_build_render_inputs` (`agent_runner.py:1529`) carries it into the job record. Minting in **main** (not the renderer) also survives a ⌘R reload and is what lets main persist `active_session_id` for item 6. Only detached background work (the nightly sweep) is genuinely session-less. Every §4 metric names its join key. | **both** (claude M4, codex #1); claude additionally conceded minting location, `project_id` form, and its own NULL claim |
| 2 | Granularity & ceiling | Target **≤40 uploads/session**, hard cap **100**, criticals bypass. Measured plan = **~30/session**. Taxonomy capped at **≤100 EMITTED names** (currently 97 across 95 rows — the cap is on names, since two rows carry two each). Per-interaction for 6 families; everything else is a local reducer → one rollup, plus a capped **`action_digest`** that preserves sequence analysis at zero extra events. | codex conceded the rollup transport; claude conceded that a per-turn *map* destroys per-tool latency. Two third designs settled it: **percentiles computed in the local reducer** (per-tool `p50/p95/max` without per-call uploads), and — after codex held out for per-interaction editor commits at ≤60/session, which claude showed is arithmetically impossible when a 20-minute session is 50-300 commits — **`action_digest`**, one capped ordered array of `feature_id`s on the session summary. Sequence analysis is the only thing per-commit upload buys that counters do not, and properties are free while events are not. codex conceded it explicitly. | **both** |
| 3 | Export hook | Call goes on **`server/render_jobs.py:580`**. A successful export is *defined* as the receipt write completing at **`lib/project.py:616`** (`atomic_write_json`). Hook line ≠ definition line. An **unreceipted** final artifact (`store_asset`) is classified `unreceipted_final_artifact`, never `export_completed`. | `lib/project.py:542-559` numbers the receipt "step 5 … the commit marker". But `lib/project.py:439` states "**lib must not depend on server**", so the call cannot live there; `:576` is the `published` guard and `:580` is the first line after it, where `origin`/`job_id` are in scope. | **codex** (O1) |
| 4 | `export_started` population | Persist **`publish_intent` on the job record at creation** (`:66` true, `:105` false, `:83` derived via `_normalize_output_path` at `:612`), upload **`render_queued`**, and use `render_queued{publish_intent=true}` as the export denominator. | `:58` `start()` is editor-only and hardcodes `"origin": "editor"` at `:66`, so `origin='agent'` is unreachable there — while `export_completed` at the receipt catches both paths (`:355` editor, `:396` agent). As originally specified, export success rate **could exceed 100%**. claude then proposed the `queued→running` transition and codex refuted that too: it cannot distinguish a publish-intent job from an intermediate agent render or an `agent_op`. claude's third proposal (gate at `_render_locked` before `execute`, `:554`) was **also** refuted, correctly: **`_run` returns at `:341-343` and `_run_with_inputs` at `:377-379` — both genuine publish-intent export failures — before `_render_locked` is ever reached**, so gating there inflates success. Creation-time `publish_intent` is the only boundary every export attempt passes. | **claude**, twice (codex refuted two successive claude fixes; the third position is codex's) |
| 5 | `_set` is not the only choke point | **4 instrumentation points**: (a) the three start methods `:68`/`:91`/`:114` → **uploaded** `render_queued` (item 4 needs it as a denominator); (b) `_set` `:326` → transitions; (c) `_mark_superseded_locked` `:176` → superseded terminal; (d) `:580` → export. **All emits happen outside `self._lock` from a copied record, deduped by `job_id`+transition.** | `render_jobs.py:169-173` docstring: superseded is "written directly rather than through `_set`, **whose supersede guard would drop exactly this update**". Queued jobs are inserted as literals at `:66`/`:83`/`:105`. "One choke point" was claude's phrasing and it was wrong. Two follow-ons from codex: `_set` **also** calls `_mark_superseded_locked` (at `:324`), so both paths can emit the same supersede — the writer must return a changed-or-not snapshot so only a real transition emits; and a `capture()` at `:326` would hold `self._lock` (`:322`) across a call that can block. | **claude** (codex #5); claude further conceded uploaded-queued and emit-outside-the-lock |
| 6 | Crash-free session rate | `1 − distinct(session_id with fatal=true OR session-fatal process_gone OR **unclean_timeout**) / count(session_started)`. Denominator is **starts**, never ends. `unclean_timeout` = a start with neither a clean end nor an explicit fatal once a fixed lateness window closes. Non-fatal error-free rate separate. Electron end hook = **`desktop/main.js:685` `before-quit`** with an awaited flush. | Both found it broken from opposite ends. `session_ended` on `pagehide` (`recorder.js:198`) cannot fire on a hard crash, so crashed sessions left the denominator; and the numerator counted handled errors while carrying a `fatal` flag the formula ignored. `window-all-closed` (`:684`) cannot observe a crash and calls `app.quit()` with no flush — but **`before-quit` already exists at `:685`**. | **both** (codex #2 critical) |
| 7 | Agent tool correlation | Per-turn `pending[tool_use_id] = {tool_name, t_start}` populated in `run_turn`'s existing tool_use branch (**`agent_runner.py:1978`**), resolved in a **new** tool_result branch in the same loop. Counters: `orphan_starts` (pending entries synthesized as `outcome='no_result'` in the `finally`), `orphan_results` (**cause labelled unknown until measured** — codex refused claude's "almost certainly a drained turn"), `duplicate_results` (via a seen-result-id set). | Not a field-threading change: `tool_use` carries `id`+`name` (`:770-775`), `tool_result` carries only `tool_use_id`+`is_error` (`:782-790`), and `run_turn` ignores result blocks entirely (`:1964-1978`). It cannot live in `event_of` (`:752`) — signature is `event_of(message)`, no `project_id`/`turn_id`, and it is also called from `_drain_unsolicited` (`:1902`), so it would attribute **discarded** turns. | **claude** (codex #3) |
| 8 | `prompt_len` is silently destroyed | **Reserved-substring rule** + rename to `*_chars` from a closed list. Contract test: `_scrub({k: 1}) == {k: 1}` for every declared numeric property. | Executed against real code: `{'prompt_len': 412}` → `{'prompt_len_len': None}`; `{'message_len': 88}` → `{'message_len_len': None}`; **`{'prompt_chars': 412}` → `{'prompt_chars_len': None}`** (codex's own proposed name also dies); `{'text_len': 5}` and `{'content_len': 9}` likewise. `{'input_chars': 412}` and `{'feedback_chars': 88}` survive. Cause: `analytics.py:155-157` substring-matches `_FREETEXT_KEYS` (`:38`) against the **key**, and `len(412)` is not str/bytes/list/dict → `None`. | **both** (codex #8, claude's own #153/#97) |
| 9 | Hook viability as a rule | Mandatory column. **7 banned hook classes** with code proof (§3 preamble). Citation rule adopted. | claude's review found 25 such rows in codex's doc; codex's found 8 of 50 sampled in claude's. Same defect class in both. | n/a |
| 10 | Agent-vs-human authorship | **Detect by diff, not by route.** `run_turn` snapshots `canonical_doc_hash` + cut/overlay/audio counts before and after the turn; the delta is the authorship record, carried on `agent_turn_completed` with `turn_id`. `source`/`author` dropped from every PUT-hooked row. | `server/app.py:784` is the **editor's** PUT. `RULES.md:79-81`: "The agent edits the JSON directly … it does **not** use the editor's JS mutators" — it writes via the `Write` tool or `publish_final_render(persist_doc=…)` (`render_jobs.py:397`), never through PUT. So `author='agent'` was structurally unobservable, and agent-authored invalid docs never 422 at all (which is why `_render_locked` must defend a missing `renderer_family` at `:524`). This also supplies codex #4's `doc_changed`/`artifact_delta` honestly. | n/a (claude E8, codex agreed) |
| 11 | Derived vs emitted | Every row carries `kind = EMITTED\|DERIVED`. DERIVED rows have **no hook point**, only source events. Honest counts: **95 EMITTED rows carrying 97 event NAMES, + 35 DERIVED = 130 catalog rows.** The cap is on **names**, not rows: rows #83 and #118 each carry two names. 97 ≤ 100. Telemetry health is a **durable local counter**, never a remote event. | `analytics.py:175-176` swallows its own failures and `:116-117` `_before_send` swallows too — a failed sink cannot report its own failure. Both docs inflated their headline row count with query-time metrics (claude ~13, codex ~10) and claude additionally counted 2 strikethrough anti-rows. | **both** (claude E14 + M7, codex #10) |
| 12 | Cost | Progressive bands `$0.0000500 / $0.0000343 / $0.0000295`, 1M free. Agreed plan = **~$136/mo at 10,000 MAU**. Arithmetic shown in §6. `27→28` corrected; `$2,026` relabelled a flat-rate **ceiling**, not an estimate. | codex recomputed claude's 41.52M case as **$1,278.24** tiered; claude's independent recompute matched to the cent. claude's Tier-B list summed to 8, not 7. **Neither agent had network access** — bands are from codex's cited read of https://posthog.com/pricing (checked 2026-08-05) and are unconfirmed against live pricing. | **claude** (M1) |
| 13 | Bloat | Merged kill list in §9: **31 rows deleted** (17 claude, 14 codex) plus 23 collapsed into rollups. | Each review named rows in its own doc; those concessions are honored verbatim. | **both** |
| 14 | What both missed | **11 new rows added** (§3), led by `browser_proxy_finished` (`server/editor.py:140`) and `proxy_cache_miss_reason`. | claude's review called `browser_proxy_finished` the single best row it lacked; codex's called tool-invocation correlation the most valuable shared miss. Both are in. | n/a |

**Two refutations that survived**, recorded because they change the build:

| # | Claim | Refutation |
|---:|---|---|
| R1 | codex #7 proposed instrumenting stage transitions at "the state source/update path, including `server/state.py:91`". | `state.py:91` is `_stage_entry`, called from a **list comprehension over every stage** at `:125`, on a route `App.jsx:59` polls every **1500 ms** — and it derives one stage's status from a checkpoint file with **no memory of the prior value**, so `from` and `duration_ms` are not computable there. It is the same read-path error codex correctly charged claude with at `app.py:743`. **The real mutation boundary is `lib/checkpoint.py:196` `write_checkpoint`**, which receives `stage` and `status` and can read the prior checkpoint to get `from`. Both original anchors are banned. |
| R2 | codex #17: `PROPERTY_TITLES` has 8 entries, not claude's 7. | Correct about the map; `propertySchema.js:4-5` says "Het's split = 7 types … (narration keeps a small panel too)", which is where 7 came from. Settled by codex's own fix: **derive the enum from `PROPERTY_TITLES` at build time** so the number can never be wrong again. |

---

## 2. Wall numbers

Five product-health walls, plus one standing roadmap counter. claude wanted unmet-capability as
wall #5; codex argued it is a roadmap feed, not product health, and is right that mixing them
makes a rising number ambiguous. It is reported alongside, labelled.

| # | Number | Formula | Join key | Good / bad |
|---:|---|---|---|---|
| 1 | **Activation, 7d** | `distinct install_id with export_completed at t ≤ first(app_first_run)+7d` / `distinct install_id with app_first_run whose 7d window elapsed` | `install_id` | ≥40% / <20% |
| 2 | **Time to value** | `P50, P90 of ( first export_completed.ts − app_first_run.ts )` per install | `install_id` | P50 <1 day / P90 >7 days |
| 3 | **Agent value & price** | useful-turn rate = `agent_turn_completed{doc_changed OR artifacts_delta>0 OR render_published} / agent_turn_started` (denominator = **delivered** turns, so a paid-for errored turn counts against it) · paired with `median(agent_cost_usd per successful first export)` | `turn_id` → `project_id` | ≥70% / <50%; cost P50 <$3 |
| 4 | **Export reliability** | `distinct receipt-backed export_completed job_ids` / `distinct mature render_queued job_ids where publish_intent=true`. `superseded` excluded from the denominator. #94 is the failure **breakdown**, not the denominator | `job_id` | ≥95% / <90% |
| 5 | **Fatal crash-free sessions** | `1 − distinct(session_id with fatal=true OR session-fatal process_gone OR unclean_timeout) / count(session_started)` | `session_id` | ≥99.5% / <98.5% |
| — | *Build feed (roadmap, not health)* | weekly count of distinct `install_id` behind `capability_missing`, `api_key_missing`, `unrecognized_tool_requested`, `agent_routed_around_us`, `agent_ffmpeg_freehand` | `install_id` | — |

---

## 3. The merged catalog

**95 EMITTED rows / 97 event names / 35 DERIVED = 130 catalog rows** (counted by parsing the
tables, and corrected twice — claude's first two counts were both wrong, which is why the number
is now machine-derived rather than asserted). Combined input was
~314 rows across the two phase-1 docs; 31 were deleted outright, 23 collapsed into rollups, 11
added from the "in neither" lists, and the rest merged. Both phase-1 headline counts were
inflated — claude's "162" included 2 strikethrough anti-rows and ~13 derived metrics; codex's
"154" included ~10. This count excludes both, excludes the 12 `feature_id` enum members, and excludes the 3 folded ids (#56, #60, #110).

### Property types

`E{…}` bounded enum · `B{…}` ordered bucket · `N(unit)` raw number (only where sums,
percentiles or rates need it) · `F` boolean flag · `I` opaque correlation id.

### Reserved-substring rule (item 8)

No property key may contain `prompt`, `message`, `text`, `transcript`, `caption`, `content`,
`body` (destroyed by `analytics.py:155-157`) or `key`, `token`, `secret`, `password`,
`authorization`, `cookie` (replaced with `[redacted]` by `:151-153`). Character counts use the
closed list `{input_chars, feedback_chars, query_chars}` — exactly the three
in use, verified scrub-safe. Adding a fifth requires the §7 round-trip test to pass for it
first. (An earlier revision declared `question_chars`/`note_chars`, which no row used, while
row 57 used `query_chars`, which the list omitted — the drift contract test 1a exists to catch.)

### The 7 banned hook classes (item 9)

| Banned | Proof | Use instead |
|---|---|---|
| Per-frame | `StudioPreview.jsx:263-265` — `syncAudioEls`/`syncOverlayVideos` run inside the rAF `tick` | accumulate in a ref, emit at pause/unmount |
| Per-keystroke | `ChatPanel.jsx:58` — `useMemo` deps `[input, caret]` | emit on menu open/select/dismiss |
| Per-log-line | `desktop/main.js:383-385` — relay's first branch is `frame.type === 'log'` | the awaited `runProvision` call at `:394`/`:399` |
| Inside the 1.5s poll | `App.jsx:59` → `/state` → `state.py:125` list comp over every stage | `lib/checkpoint.py:196` (the write) |
| Inside the 4s poll | `App.jsx:1294` → `listAssets` → `app.py:625` → `lib/project.py:445`, whose docstring says "the UI polls this every 4s" | emit on the consumer's `current` flip |
| Pure modules | `interp.js`/`model.js` per `RULES.md:39-45`; `mentions.js:1-3` "No React, no DOM, no fetch" | the thin caller in `Studio.jsx`/`ChatPanel.jsx` |
| `lib/` | `lib/project.py:439` "lib must not depend on server" | the app-layer caller in `server/` |

### The 6 families allowed to upload per-interaction

Render status transitions · agent turn terminals · per-tool **failures** · export terminals ·
every failure class · unmet-capability. Everything else is a local reducer → one rollup.
Per-tool *successes* upload as a per-turn rollup carrying locally-computed `p50/p95/max` per
tool, which is how latency distributions survive the ceiling.

---

### 3a. Install / provisioning / launch health

| # | Event | Kind | Properties | Hook (call goes on this line) | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|1|`app_first_run`|E|`os_major`E, `arch`E, `app_version`E, `cores`B, `ram_gb`B|`server/app.py:449` (existing capture)|once per install; guarded by `app_first_run_done` at `:450`|install|How many real installs? → every funnel denominator|P0|1/inst|no|
|2|`app_launch_started`|E|`launch_kind`E{cold\|activate\|post_update}, `previous_exit`E{clean\|crash\|kill\|unknown}, `env`E|`desktop/main.js:626` (first stmt in `boot()`'s try)|once per process; `boot` is called once from `:676`|install|Do launches start and recover? → startup defects|P0|1/sess|no|
|3|`backend_ready`|E|`startup_ms`N, `cold_provisioned`F, `probe_count`N|`desktop/main.js:470` (the `resolve(port)`)|fires exactly once per wait; the poll's other branches are `:469`/`:471`|install|Time to a usable API → startup budget|P0|1/sess|no|
|4|`launch_failure`|E|`phase`E{provision\|port\|spawn\|health\|ui_load}, `failure_class`E, `retryable`F, `stderr_class`E|`desktop/main.js:116` (inside `fatal`, next to the existing `reportDesktopError`)|`fatal` is idempotent via `fatalShown` at `:112`|install|Why can't a user enter? → ship blocker|P0|rare|**SENS** — `stderr` tail stays in the local dialog; ship a classified enum + hash only|
|5|`provision_started`|E|`tier`E{core\|composition\|pack}, `reason`E, `expected_mb`B|`desktop/main.js:392` (top of `ensureProvisioned`'s try)|once per tier run|install|What blocks entry? → defer or shrink a tier|P0|0-3/inst|no|
|6|`provision_finished`|E|`tier`E, `outcome`E{success\|partial\|failed\|cancelled}, `duration_s`B, `retry_count`N|`desktop/main.js:394` and `:399` (after each awaited `runProvision`), failure at `:401`|**not `:383`** — that relay fires per NDJSON log line (banned class 3). These are the awaited per-tier resolutions: 1-2/install|install|Can a new user finish setup? → gate beta|P0|1-2/inst|no|
|7|`provisioning_snapshot`|E|`venv_ok`F, `core_ok`F, `ffmpeg_ok`F, `node_ok`F, `remotion_ok`F, `hyperframes_ok`F, `composition_ok`F, `packs`E[], `forced`F, **`free_gb`B**, **`proxy_cache_mb`B**|`server/app.py:1029` (wrap the `return provision.doctor()`)|change-only via a module-level last-hash; `/api/doctor` is called by setup + Settings, not polled. **codex B13**: `free_gb` and `proxy_cache_mb` are **not** in `provision.doctor()` (`lib/provision.py:318-333` returns only the ok-flags and packs) — they are computed in the app-layer handler at `:1029`, which is also the only layer allowed to touch analytics|install|What state are real installs in? → bundle vs lazy|P0|≤1/sess|no|
|8|`pack_install_outcome`|E|`pack`E (5 from `provision.PACKS`, `lib/provision.py:74`), `outcome`E, `duration_s`B, `size_mb`N|`server/app.py:1043` (success frame) and `:1045` (error frame) in `_stream_provision`'s worker|**codex B6**: an earlier draft cited `lib/provision.py:444`, which is both a function *definition* and inside `lib/` — violating this doc's own banned-hook class. The server streaming boundary is the real one, reached from `:1078`|install|Does anyone take the 2.6 GB pack? → prune `PACKS`|P0|0-5/inst|no|
|9|`session_started`|E|`session_id`I, `entry`E{dashboard\|editor\|setup}|**`desktop/main.js:626`** (in `boot()`, before `applyCsp()`) — main **mints and owns** the start record; the renderer only *receives* the id via preload and may enrich `entry`|**codex B3**: an earlier draft had the renderer mint at `main.jsx:46`, which would split a session on every ⌘R reload and leave pre-UI launch failures unjoinable. (`main.jsx:46` is also just the blank line before `createRoot` at `:47`.) Main-minted survives reload and is what lets main persist `active_session_id` for #113a|session, install|Session denominator → every per-session rate|P0|1/sess|no|
|10|`session_ended`|E|`duration_s`B, `foreground_s`B, **`render_foreground_overlap_s`**N, `exit_kind`E, `surfaces`E[], `uploads`N|`desktop/main.js:685` (`before-quit`, with an awaited flush) + renderer `pagehide` as enrichment|**enrichment only, never a denominator** (item 6) — a hard crash reaches neither|session, install|Session shape → focused-time drill-down|P0|1/sess|no|
|11|`update_lifecycle`|E|`phase`E{available\|downloaded\|install_clicked\|failed}, `target_version`E|one call per handler: `desktop/main.js:208`, `:211`, `:221`, `:207`|four registrations, each fires per real updater event|install|Do users get fixes? → force-update policy|P1|0-4/upd|no|
|12|`process_gone`|E|`process`E{renderer\|gpu\|utility\|backend\|provision}, `reason`E, `exit_code_bucket`B|`desktop/main.js:673` and `:674` (existing handlers)|already wired; needs classification + `session_id`|session, install|Native failures escaping JS → hardening|P0|rare|no|
|13|`disk_headroom`|D|`free_gb`B, `proxy_cache_mb`B|— source: `provisioning_snapshot`|—|install|Do proxies fill disks? → add cache GC|P2|—|no|

### 3b. Auth & setup funnel

| # | Event | Kind | Properties | Hook | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|14|`auth_state_observed`|E|`state`E{unconnected\|connected\|needs_reauth}, `method`E, `expired`F|`server/app.py:956` (wrap the `return auth_mod.status()`), **change-only** against a module-level last-value|**codex F1**: an earlier draft made this DERIVED from #17/#18/#20 plus "absence = unconnected". That inference is false — an install may already hold a valid credential when analytics ships, or may never visit auth at all, and would emit none of those transitions. `server/auth.py:189-223` computes the authoritative snapshot; change-only makes the polled route cheap (one event per real change, not per poll)|install|What share can use the agent? → fix the dominant blocked state|P0|≤2/sess|no|
|15|`auth_prompt_shown`|E|`reason`E{first_run\|chat_503\|needs_reauth}, `entrypoint`E|`web/src/App.jsx:91` — on the `showConnect` false→true transition (a `useEffect` on `showConnect`), **not** inside the render expression|user action, once per prompt|session, install|Who sees the wall? → funnel top|P0|≤1/inst|no|
|16|`oauth_started`|E|`entrypoint`E|`server/app.py:961` (wrap the `return auth_mod.start_oauth()`)|one per attempt|install|Do users begin OAuth? → CTA drop-off|P0|≤2/inst|no|
|17|`auth_connect_finished`|E|`method`E{oauth\|api_key}, `outcome`E{success\|expired_link\|exchange_rejected\|invalid\|network\|storage}, `duration_s`B, `attempts`N|`server/app.py:970` (oauth, existing) and `:980` (api_key, existing); failures at `:969`/`:979` (the `raise` in each `except auth_mod.AuthError`)|both success captures already exist; add the failure branches|install|Setup conversion → simplify onboarding|P0|1/inst|no|
|18|`auth_needs_reauth`|E|`class`E{expired\|revoked\|invalid\|credit\|billing\|unknown}, `phase`E{turn_start\|mid_turn\|refresh}, `days_since_connect`B|`server/auth.py:161` (inside `mark_auth_error`)|one per transition into the error state, not per call|install|What breaks connected users? → reauth vs billing copy|P0|rare|no|
|19|`byok_var_saved`|E|`provider_family`E{anthropic\|generation\|media\|voice\|stock\|other}, `changed_count`N, `outcome`E|`server/app.py:947` (after `env_config.reload_env()`, before the return at `:948`)|one per save|install|Which providers do users pay for? → integration priority|P1|0-8/inst|no — **closed enum computed before capture. Do NOT rely on `_SECRET_HINT`: it tests the key name, not the value (`analytics.py:150`), so `var_name='ANTHROPIC_API_KEY'` passes through unredacted (verified).**|
|20|`auth_disconnected`|E|`prior_method`E, `active_projects`B|`server/app.py:986` (wrap the `return auth_mod.disconnect(...)`)|one per action|install|Deliberate or churn? → account controls|P2|rare|no|

### 3c. Project lifecycle

| # | Event | Kind | Properties | Hook | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|21|`project_created`|E|`pipeline`E, `style`E, `ordinal`N, `first_project`F, `create_ms`N|`server/app.py:544` (existing capture)|one per creation; `pipeline`/`style` validated against closed lists at `:533`/`:540`|project, session, install|Which styles get chosen? → prune `styles/`|P0|0.2/sess|no — project NAME never sent; `project_id` is a random uuid4 persisted in the project dir|
|22|`project_create_failed`|E|`failure_class`E{unknown_pipeline\|unknown_style\|duplicate\|invalid_name\|storage}|`server/app.py:534`, `:541`, and the `except` at `:547`|three explicit raise sites|session, install|Why does creation fail? → validation fix|P0|rare|no|
|23|`project_opened`|E|`entrypoint`E{just_created\|dashboard}, `age_days`B, `n_cuts`B, `n_overlays`B, `has_export`F, `prior_exports`N|`web/src/App.jsx:87` (inside `openProject`, after `setSelected`)|user action; **not** the 4s `refreshProjects` poll at `:49`|project, session, install|Do users return to old projects? → is "project" the right unit|P0|1-4/sess|no|
|24|`editor_opened`|E|`source`E{project_bar\|post_agent\|post_export}, `n_cuts`B, `n_overlays`B, `n_audio`B, `duration_s`B, `doc_exists`F|`web/src/App.jsx:139` (`onEdit`)|one per open|project, session, install|Agent-first or editor-first? → where UI budget goes|P0|0.8/sess|no|
|25|`pipeline_stage_transition`|E|`stage`E, `from`E, `to`E, `duration_s`B|`scripts/update_stage.py:73` (after the `write_checkpoint` at `:72`) writing to a **durable local outbox** the backend drains on its next flush, **plus** the same observer for any in-process writer|**R1 + codex B6.** NOT `app.py:743` (a polled GET) and NOT `state.py:91` (a per-stage read inside a 1.5s-polled list comp, with no prior value) — that is the mutation boundary. But a direct `capture()` at `:273` would put analytics in `lib/`, violating this doc's own rule, and there is no single app caller: `scripts/update_stage.py:72` calls `write_checkpoint` too. **codex F3 then showed the observer alone is not enough**: `server/agent_runner.py:474-480` *instructs the agent* to run `python scripts/update_stage.py …`, so the primary product writer is a **separate process** where a module-registered observer does not exist. Claiming "a CLI write correctly emits nothing" would have silently dropped normal agent stage transitions — the majority of them. The script writes to a local outbox instead|project, install|Where does the pipeline stop? → repair that stage|P1|~6/proj|no|
|26|`thread_lifecycle`|E|`action`E{created\|switched}, `n_threads`N, `session_resumed`F|`web/src/chat/useAgentChat.js:69` (`newChat`)|one per action|project, session, install|Are multiple threads used? → keep or delete thread UI|P2|0-3/sess|no|
|27|`project_stalled`|E|`last_activity_days`B, `furthest_stage`E, `n_turns`N, `n_commits`N, `has_assets`F|new nightly sweep in `server/lifecycle.py`|server-side batch, once per project per day|project, install|**Where do projects die?** → the abandonment metric|P1|—|no|
|28|`second_project_created`|D|`days_since_first`B, `first_exported`F|— source: #21 `ordinal==2`|—|install|The habit signal → retention spine|P0|—|no|

### 3d. Asset ingest

`RULES.md:94`: any media can land here. Today ingest probes only width/height/duration
(`app.py:879-908`) — codec, container, pixel format, transfer and fps are exactly the fields
that predict render success, and none are captured. claude's row conflated ingest with the lazy
`/source_meta` probe; codex's split is adopted.

| # | Event | Kind | Properties | Hook | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|29|`asset_import_finished`|E|**`asset_id`I (opaque random uuid4, persisted in `asset_manifest.json`)**, **`asset_fingerprint`I (first 16 hex of `HMAC(install_secret, sha256(bytes))` — install-stable, not cross-install correlatable)**, `kind`E, `source`E{picker\|drop\|agent_store}, `extension`E, `bytes`N, `duration_ms`N, `outcome`E{success\|deduped}|`server/app.py:580` (after the copy at `:578-579`, before the `return` at `:581`; `target.stat()` at `:586` gives final bytes)|**not `:577`** (the `mkdir`, before the copy). One per asset|project, session, install|What media enters, by which path? → ingest priorities|P0|0-8/sess|no — filename never sent; `extension` is a closed enum|
|30|`asset_import_failed`|E|`kind`E, `failure_class`E{invalid_kind\|invalid_name\|traversal\|disk_full\|permission\|copy}, `bytes_bucket`B|`server/app.py:560` (invalid kind), `:569` (invalid name — sanitize is at `:567`), `:575` (traversal), `except` around the copy|four distinct raise sites, each its own class|project, session, install|Why can media not enter? → validation fix|P0|rare|no|
|31|`media_probe_finished`|E|**`asset_id`I**, `container`E, `video_codec`E, `audio_codec`E, `pix_fmt`E, `color_transfer`E, `hdr`E{sdr\|hlg\|pq\|unknown}, `bit_depth`B, `width`N, `height`N, `fps`B, `vfr`F, `rotation`E, `n_audio_streams`N, `has_alpha`F, `duration_s`B, `bitrate_mbps`B|**new probe called at `server/app.py:580`**, once at ingest|**not `:879`** (that is the ffprobe-availability gate on the lazy `/source_meta` route, and it is not an ingest path at all). The existing probe must be extended and called at ingest|project, install|**Which codecs do real users drop on us?** → decode hardening; is HDR a real user problem or only ours|P0|1/asset|no — all fixed-vocabulary ffprobe fields|
|32|`media_probe_failed`|E|`extension`E, `failure_class`E{ffprobe_missing\|nonzero_exit\|parse\|timeout\|permission}|the `returncode != 0` branch (**new** — there is no `else` today; `:898` tests success) and the `except` at `:907`|the two real failure paths|project, install|What is accepted but uninspectable? → reject clearly instead of silently unclamped trims|P0|rare|no|
|33|`browser_proxy_finished`|E|`source_codec`E, `target`E{vp9_alpha\|native_passthrough}, `cache_hit`F, `duration_ms`N, `bytes`N, `outcome`E|`server/editor.py:171` (non-ProRes `.mov` → `native_passthrough`, codec known), `:176` (cache hit), `:191` (failure), `:194` (success)|**codex F6**: an earlier draft listed only `:176`/`:191`/`:194`, none of which can produce a `browser_native` outcome — so the enum member was unreachable. The non-`.mov` early return at `:154-155` is deliberately **not** hooked: no codec is known there and a non-`.mov` needs no proxy, so it is not a decision. Once per source per session; **codex's row otherwise adopted verbatim** — the single best row either doc had that the other lacked|project, session, install|**Chromium cannot decode ProRes, and HyperFrames alpha overlays are ProRes 4444 (`editor.py:143-147`) — so previewing the agent's own output silently transcodes.** → pre-proxy, or change the overlay format|P1|0-3/sess|no|
|34|`asset_added_to_doc`|E|**`asset_id`I**, `kind`E{image_main\|video_main\|image_overlay\|video_overlay\|music\|sfx}, `method`E{modal_add\|asset_click\|timeline_drop}, `time_from_import_s`B|`web/src/studio/Studio.jsx:403` (`onAddClip`), `:493`, `:505`, `:416`, `:423`|thin callbacks, one per add. **codex B11**: `method='agent'` is removed — it is unreachable from these Studio callbacks because `RULES.md:80-81` has the agent edit the JSON directly. Agent-added assets are detected by #39's `cuts_delta`/`overlays_delta`, not here|project, session, install|Which media becomes an actual edit? → focus supported workflows|P0|0-10/sess|no|
|35|`source_resolution_failed`|E|`reference_kind`E{manifest_id\|project_path\|shared_asset}, `consumer`E{preview\|render}, `outcome`E{missing\|outside_project\|unreadable}|the two **callers that know the consumer**: `server/render_jobs.py:208` (`_resolve_sources`, consumer=render) and the `/source` route at `server/app.py:834` (consumer=preview)|**codex F7**: an earlier draft hooked `server/editor.py:120`. That is a single fallthrough after the loop at `:113-119` which conflates missing, outside-project, resolve errors and non-files — and `resolve_source_path` takes no `consumer` argument, so both declared slices were fabricated. Emitting at the callers supplies `consumer`; `outcome` requires the resolver to also return a reason, a small change to `resolve_source_path`. **codex R3**: an earlier draft added a third `consumer=agent` hook at `agent_runner.py:1529`, which is not a caller at all — `_build_render_inputs` only copies supplied keys and never calls `resolve_source_path`. Agent renders resolve later through the same `render_jobs.py:207-208` path, so agent-vs-editor is recovered by joining `job_id` to #83's `origin`, not by a fabricated consumer value|project, install|**"Cut source not found" is this app's signature render failure** (`render_jobs.py:197`) → fix the manifest/path contract|P0|rare|no — `reference_kind` only, never the path|
|36|`asset_giant`|D|`size_mb`B, `duration_s`B, `pixels`B|— source: #29 ⋈ #31 **on `asset_id`**|`asset_id`|4K 10-minute files in a Reels tool? → proxy-on-ingest|P1|—|no|
|37|`human_add_rate`|D|`imported`N, `added_in_editor`N|— source: #29 ⋈ #34 **on `asset_id`**|`asset_id`|**codex F4 + R2**, two corrections. `exported` removed: #93 declares no `asset_id` set, and the receipt being in scope at the hook does not make it queryable. And the metric is **renamed and re-scoped** — as `unused_import_rate` it was wrong, because #34 covers only the Studio callbacks while the agent edits the JSON directly (`RULES.md:80-81`) and #39's deltas carry no `asset_id`, so an asset the *agent* used would have been counted unused. It now honestly measures only what a human added in the editor. Are humans importing media they then cannot place? → format/placement support|P1|—|no|

### 3e. Agent turns and tool calls

| # | Event | Kind | Properties | Hook | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|38|`agent_turn_started`|E|`turn_id`I, `model`E, `entrypoint`E{project\|editor}, `thread_kind`E{new\|resumed}, **`input_chars`**N, `mention_count`N, **`mentions_missing`**N, **`mentions_outcome`**E{all_found\|some_missing\|shape_rejected\|none}, `preturn_flush`E{none\|success\|failed}, `is_fresh_client`F|`server/agent_runner.py:1956` (after `TurnResult` init, before `client.query`)|**mints `turn_id`**; once per turn|turn, project, session?, install|What context starts a turn? → model default, handoff|P0|0-6/sess|no — `input_chars`, never the text. **Named `input_chars` not `prompt_len`: verified `prompt_len`→`prompt_len_len=None`**|
|39|`agent_turn_completed`|E|`is_error`F, `sdk_turns`N, `cost_usd`N, `wall_s`B, `stop_reason`E, `tool_calls`N, `tool_errors`N, `orphan_starts`N, `orphan_results`N, `doc_changed`F, `doc_hash_changed`F, `cuts_delta`N, `overlays_delta`N, `audio_delta`N, `artifacts_delta`N, `render_published`F|`server/agent_runner.py:1993` (**first line of the `finally`**)|**Self-caught during the final pass, and it is the exact class of defect this doc exists to prevent.** claude first cited `:2006`, which is *after* the `finally` block (`:1992-2005`) — and `:1991` re-raises, so `:2006` is **unreachable on a crashed turn** while the row claimed "including error paths" and the P0 test asserts it. `:1993` always runs — and because a `finally` runs exactly once on both paths, no dedupe is needed, but the emit must supply **defaults for the result fields that are unset when the turn raised**. **`TurnResult` (`:832`) has ONLY `text`/`is_error`/`num_turns`/`total_cost_usd`** — `stop_reason` exists in the event dict at `:825` but is not copied, and every other field here requires **new** counters and a before/after doc snapshot (item 10). This is new work, not field-reading|turn, project, install|Did the turn work, cost what, and change what? → wall #3|P0|0-6/sess|no|
|40|`agent_turn_failed`|E|`phase`E{query\|stream\|tool\|result\|session}, `class`E{auth\|budget\|transport\|sdk\|tool_chain\|unknown}, `retryable`F|`server/agent_runner.py:1990` **only** (in the `except`, where `turn_id` is in scope)|**codex R4**: an earlier draft listed `app.py:1164` as a second hook and called them "both real failure paths". They are not — `run_turn` catches at `:1989-1991` and **re-raises**, and `app.py:1155` then catches *the same exception* and reports it at `:1164`. Two sequential observations of one failure would have double-counted the turn-failure rate. `:1164` stays as #105's exception-inbox report, which is a different event answering a different question; SSE errors never reach the global handler at `:421`|turn, project, install|Why do turns fail? → fix the dominant class|P0|rare|no|
|41|`agent_session_died`|E|`had_result_error`F, `will_resume`F|`server/agent_runner.py:1999` (in the `_error_occurred` branch)|once per death|turn, project, install|How often does the SDK session break? → is resume reliable|P0|rare|no|
|42|`agent_interrupted`|E|`elapsed_s`B, `tool_calls_so_far`N, `tool_in_flight`E, `work_published`F|`server/agent_runner.py:2021` (after `client.interrupt()` succeeds)|once per Stop|turn, project, session, install|**What is the agent doing when users give up?** → strongest agent-failure signal|P0|rare|no|
|43|`agent_tool_failed`|E|`tool_invocation_id`I, `tool_id`E, `family`E, `outcome`E{returned_error\|denied\|cancelled\|exception\|timeout\|no_result}, `duration_ms`N, `failure_class`E{input\|missing_file\|missing_key\|missing_capability\|permission\|provider\|quota\|network\|decode\|render\|unknown}|**new `tool_result` branch inside `run_turn`'s loop**, after `agent_runner.py:1978`|item 7. `pending[tool_use_id]` is populated in the existing tool_use branch (`:1969-1978`). **NOT `event_of` (`:752`)**: no `project_id`/`turn_id` in scope, and it is also called from `_drain_unsolicited` (`:1902`), which would attribute discarded turns. 100% of failures upload|tool, turn, project, install|**Which of OUR tools does the agent fail with?** → fix, replace or delete it|P0|0-3/turn|no — `tool_id` + class only; result bodies stay local|
|44|`agent_tool_rollup`|E|per-tool map `{tool_id: {calls, errors, p50_ms, p95_ms, max_ms}}`, `unique_tools`N, `bash_share`N, **`permission_allows`N**, `skills`E[], `pipeline_defs`E[], `media_tools`E[]|`server/agent_runner.py:1993` (same point as #39 — inside the `finally`, so a crashed turn still reports)|**item 2's settlement**: percentiles computed in the local reducer, so per-tool latency survives without per-call uploads. One event per turn|turn, project, install|Tool reach and cost → prune dead tools and skills|P0|1/turn|no|
|45|`tool_permission_decided`|E|`tool_id`E, `action`E{confirm\|deny}, `reason_class`E{unrecognized\|destructive\|path_escape\|render_route\|heavy_media_route}, `root_family`E{home\|system\|other_user\|tmp}|`server/agent_runner.py:453` and `:455` — the **deny** and **confirm** branches only|**codex F5**: an earlier draft hooked `:451`, immediately after `decide_tool`, which fires for **every** tool call including every `SAFE_TOOLS`/`WRITE_TOOLS` allow at `:393-394`. A 4-turn × 20-tool session would add ~80 events that §6 omits and would breach the 100 hard cap. Ordinary allows are now counted inside #44's per-turn rollup; only deny and confirm upload|turn, project, install|Is the sandbox blocking real work? → loosen a boundary or keep it|P0|0-2/turn|no — classified reason, never the path or command|
|46|`agent_confirm_resolved`|E|`tool_id`E, `reason_class`E, `approved`F, `wait_s`B, `timed_out`F|`server/agent_runner.py:1330` (after the wait in `_confirm` returns)|one per confirm|turn, project, install|If approval >95%, stop asking → auto-allow that pattern|P0|0-3/turn|no|
|47|`unrecognized_tool_requested`|E|`attempted`E (hashed if not in the known set)|`server/agent_runner.py:430`|**renamed** from claude's `tool_not_found`: `:430` is a conservative fall-through for anything outside `SAFE_TOOLS`/`WRITE_TOOLS`/`AskUserQuestion`/`mcp__mc__`/`Bash`, so it includes valid-but-unclassified SDK/MCP tools and does **not** prove registry absence|turn, install|Did the agent reach for something we don't classify? → classify it or build it|P1|rare|no|
|48|`capability_missing`|E|`pack`E, `reason_class`E, `installed_before`F|`server/agent_runner.py:1450` (after the unknown-pack guard at `:1448-1449`)|one per request|turn, project, install|**The agent declared a missing capability** → bundle it or cut the feature|P0|rare|no|
|49|`capability_request_resolved`|E|`pack`E, `outcome`E{already\|installed\|declined\|install_failed}, `wait_s`B, `retry_succeeded`F|`server/agent_runner.py:1512` (in `resolve_capability_request`)|one per resolution|turn, project, install|Will users install 2.6 GB mid-turn? → if refusal >50%, bundle or cut|P0|rare|no|
|50|`api_key_missing`|E|`env_var`E, `provider_family`E, `already_in_byok`F|`server/agent_runner.py:1376` (after the empty-`env_var` guard at `:1374-1375`)|one per request|turn, project, install|Which paid provider did the agent want? → integration priority|P0|rare|no|
|51|`api_key_request_resolved`|E|`provider_family`E, `provided`F, `wait_s`B, `retry_succeeded`F|`server/agent_runner.py:1435`|one per resolution|turn, project, install|Will users pay? → same|P0|rare|no|
|52|`agent_routed_around_us`|E|`marker`E{silence_cutter\|motion_ops\|auto_reframe\|object_cutout}, `steered_to`E, `later_used_steered_tool`F|`server/agent_runner.py:410` (inside `if heavy_op:` at `:409`, before the `ToolDecision` return)|one per match; the detector at `:128` is pure — capture at the caller|turn, project, install|The agent hand-rolled what we have a tool for → **our tool's interface or docs failed**|P0|rare|no|
|53|`agent_rendered_via_bash`|E|—|`server/agent_runner.py:402` (inside the `if` at `:401`, before the `ToolDecision` return)|one per match|turn, project, install|Same, for render|P0|rare|no|
|54|`agent_ffmpeg_freehand`|E|`filter_family`E{overlay\|scale\|concat\|atempo\|zscale\|drawtext\|crop\|xfade\|other}, `had_tool`F|`server/agent_runner.py:417` (Bash branch, after a new local filter-family classifier)|classify locally; **never upload the command**. Reuse the parser at `activity.py:108`|turn, project, install|**Raw ffmpeg is a missing tool, named** → top-3 build channel|P1|0-3/turn|no|
|55|`agent_store_asset`|E|`kind`E, `ok`F, `was_final_render`F, `unreceipted_final_artifact`F|`server/agent_runner.py:1031` (wrap the `await self._store_asset(...)`)|one per call. **`kind='final_render'` publishes with `receipt_doc=None`** (`agent_runner.py:1014`; `lib/project.py:558-559` refuses "provenance it has not earned"), so it produces a real `final.mp4` that **never fires `export_completed`** — see §11 Q1|turn, project, install|Does the agent generate or only arrange? → which generation tools matter|P1|0-5/turn|no|
|57|`asset_mention_menu`|E|`result_count`B, `query_chars`B, `outcome`E{selected\|dismissed\|sent_plain}, `input_method`E{keyboard\|mouse}|`web/src/chat/ChatPanel.jsx:104` (select) and the dismiss handler|**NOT `ChatPanel.jsx:58`** — that `useMemo`'s deps are `[input, caret]`, i.e. per keystroke (banned class 2). **Both docs got this wrong.** `mentions.js:1-3` is pure and stays pure|session, install|Users looked for an asset that isn't there → smarter search, or the missing asset class|P1|0-5/turn|no — count + length; never the query|
|58|`agent_output_adopted`|E|`had_local_edits`F, `cuts_delta`N, `overlays_delta`N, `audio_changed`F, `fields_changed`N, `adopt_ms`N, `undone_within_30s`F, **`local_commits_during_turn`**N, **`save_blocks`**N, **`undo_restored_local`**F|`web/src/studio/Studio.jsx:320` (existing `dbg.event('agent.adopt')`)|one per turn-end reconcile|turn, project, session, install|**Does the user keep what the agent did?** → best agent-quality signal in the editor|P0|0-6/sess|no|
|59|`agent_adopt_failed`|E|`phase`E{doc_fetch\|assets_fetch}, `class`E|`web/src/studio/Studio.jsx:325` (replacing the bare `.catch(() => {})`)|**codex's "in neither" row**: today a turn can change disk while the live editor silently fails to adopt it. The `.catch` at `:325` and `:313` swallow everything|turn, project, session, install|A silent divergence between disk and screen → the one corruption path in the shared-doc design|P0|rare|no|
|61|`agent_cost_per_export`|D|P50, P90 USD|— source: #39 `cost_usd` summed per `project_id` ÷ #93|—|project, install|Wall #3's price half|P0|—|no|
|62|`agent_useful_turn_rate`|D|rate|— source: #38, #39|—|turn|Wall #3|P0|—|no|
|63|`agent_tool_success_by_tool`|D|per-tool rate|— source: #43, #44. Denominator = `calls`; `denied` excluded (a policy decision, not a tool failure) and reported as a separate permission rate|—|tool, turn|Which tool to repair or delete|P0|—|no|
|64|`cost_of_failed_work`|D|P50 USD|— source: #39 for projects with no export **whose 7d window has elapsed**|—|project, install|Right-censoring guard added: without it, a project created yesterday counts as failed|P1|—|no|

### 3f. Editor

The blocker both docs hit: `commit` (`Studio.jsx:186`) takes an opaque `next`, so nothing knows
which feature fired, and `snapshot` (`:168-170`) pushes only a doc snapshot — no action identity,
so undo→redo→undo double-counts. Both fixes are adopted: **`commit(next, action)`** where
`action = {action_id, feature_id}`, and the history entry carries it.

| # | Event | Kind | Properties | Hook | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|65|`editor_session_summary`|E|per-feature map `{feature_id: {commits, drags, undos, regret, noops}}`, `features_used`E[], **`features_eligible`E[]**, `duration_s`B, `n_cuts`B, `n_overlays`B, `n_tracks`B, `zoom_final`B, `panel_layout`E, `tracks_hidden_max`N, `shortcuts`{}, `selections`N, `saves`N, `exports`N, `dirty_abandon`F, **`action_digest`** (ordered array of closed `feature_id`/outcome enums, **max 200**), `digest_count_total`N, `digest_truncated`F|renderer flush on `pagehide` only (mirroring `recorder.js:198-200`), **exactly one per session**|the single largest merge: **23 per-interaction rows from the two docs collapse into this one**. **codex B15**: an earlier draft added a 60s partial flush, which would upload ~20 cumulative summaries in a 20-minute session — breaking the ~30/session arithmetic and double-counting every commit unless queries selected a max `flush_seq`. Removed rather than reconciled: the flush existed to survive a hard crash, but a crashed session's *editor summary* is not worth 20 events, and the crash itself is already captured by #105 and #113a, which is what actually matters. `action_digest` is the item-2 settlement: it preserves the ordered sequence the workaround n-gram miner (§5) needs, at **zero extra events**, because properties are free and events are not. No timestamps, values, object ids or text in the digest — enums only. `features_eligible` is computed from the same predicates the UI uses to render (e.g. `audioMix` only for `video_overlay`, `propertySchema.js:97`; Arrange only with ≥2 overlays, `StudioTimeline.jsx:267`) and is what makes adoption and discovery denominators computable at all|session, project, install|**Which editor features are used, by whom, and were they even reachable?** → the feature ledger|P0|1/sess|no — `summarizeDocChange` (`model.js:501`) returns changed KEY NAMES, never values|
|66|`feature_first_use`|E|`feature_id`E, `days_since_install`B, `session_ordinal`N, `discovery_source`E{toolbar\|timeline\|inspector\|canvas\|asset_panel\|shortcut\|agent}|`web/src/studio/Studio.jsx:190` (where `dbg.event('edit.commit')` already is), gated on a persisted per-install seen-set|bounded: at most once per feature per install, then zero forever. This is the one per-commit upload that earns its place|session, project, install|**Time-to-discovery per feature** → a long lag means UI work, not more features|P1|~1/sess|no|
|67|`editor_action_blocked`|E|`feature_id`E, `reason`E{no_selection\|last_cut\|invalid_playhead\|agent_busy\|no_history\|no_render\|runtime_mismatch}, `attempts`N|`web/src/studio/Studio.jsx:97` (inside `flash`, on `kind==='warn'`) — ONE hook covers every guardrail, e.g. the last-clip block at `:378` and the split same-ref hint. Requires adding a `code` argument to `flash`|user intent hitting a guardrail; one per blocked attempt|session, project, install|Where does intent hit a wall? → states and copy|P0|0-5/sess|no|
|68|`editor_save_finished`|E|`kind`E{manual\|autosave\|pre_agent\|pre_export}, `outcome`E{success\|rejected\|blocked_agent\|network}, `duration_ms`N, `dirty_age_ms`N|`web/src/studio/Studio.jsx:230` (ok), `:234` (rejected), `:222` (blocked)|**failures upload at 100%; successes are counted into #65.** Autosave fires 20-200×/session (`:247`) so per-save upload is banned|session, project, install|Is the shared doc safely persisted? → fix races before features|P0|~1/sess|no|
|69|`schema_write_rejected`|E|`object_kind`E{document\|cut\|overlay\|audio}, `failure_field`E, `origin`E{editor}|`server/app.py:793` (inside the `except editor_mod.EditDecisionsInvalid` at `:792`)|`RULES.md:55` says a Save must never 422 — this is the contract alarm. **`origin` can only be `editor`**: the agent writes the file directly (item 10), so agent-authored invalid docs never reach this route|project, install|Does the contract hold? → sanitizer fix|P0|rare|no|
|70|`dirty_work_abandoned`|E|`dirty_age_ms`N, `commits_since_save`N, `exit_reason`E{back\|window_close\|project_switch\|crash}|`web/src/studio/Studio.jsx:670` (the `onClose` button) + an unmount cleanup + the `pagehide` flush|**codex's row**: data loss. Reachable because autosave is suspended around agent turns (`:248`)|session, project, install|Is unsaved work lost? → unload flush / recovery|P0|rare|no|
|71|`editor_load_failed`|E|`phase`E{doc\|assets\|source_meta\|render_media}, `class`E{404\|schema\|decode\|network}, `recovered`F|`web/src/studio/Studio.jsx:139` (the `.catch` that calls `setLoadErr`)|one per failure|session, project, install|Why can a project not be edited? → recovery work|P0|rare|no|
|72|`preview_mode_switched`|E|`to`E{source\|render}, `has_render`F, `time_in_prior_mode_s`B, `trigger`E{user\|export_complete}|`web/src/studio/Studio.jsx:110` (existing `dbg.event('ui.previewMode')`)|one per switch|session, project, install|**Direct measurement of `RULES.md:66`**: if users keep flipping to `render`, the live preview is failing its promise|P0|0-6/sess|no|
|73|`canvas_changed`|E|`from`E, `to`E (`CANVAS_PRESETS`, `model.js:28`)|`web/src/studio/Studio.jsx:597` (the `commit(d => interp.setCanvas(...))`)|one per change|session, project, install|Does anyone leave 9:16? → if not, delete the picker (`RULES.md:3`)|P1|0-2/sess|no|
|74|`undo_rate_by_feature`|D|per-feature rate + redo-adjusted regret|— source: #65's `{undos, regret}` per `feature_id`, counted **once per `action_id`**|—|session|**codex #12**: without `action_id`, undo→redo→undo double-counts and the rate can exceed its population|P0|—|no|
|75|`feature_adoption_rate`|D|per-feature rate|— source: #65 `features_used` ÷ #65 `features_eligible`, both at **install** level|—|install|codex #20: claude's formula and prose used different units; install-level is the defensible one|P0|—|no|
|76|`feature_twice_use_rate`|D|per-feature rate|— source: #65 across ≥2 distinct sessions on different days|—|install|**Does anyone use it twice?** → the deletion test|P0|—|no|
|77|`feature_discovery_rate`|D|per-feature rate|— source: #66 within the first 3 sessions where F appears in #65's `features_eligible`; denominator is installs with ≥3 **F-eligible** sessions|`install_id`|Discovered-and-rejected vs never-found → delete vs fix the UI|P1|—|no|
|78|`edit_to_export_ratio`|D|P50, P90|— source: #65 commits ÷ #93|—|project|Are we an editor or a one-shot generator? → where investment goes|P0|—|no|
|79|`doc_origin_cohort`|D|`cohort`E{agent\|mixed\|human}|— source: #39 `doc_changed` vs #65 `commits`, as a **coarse cohort label only**|—|project, turn|**codex B9**: an earlier draft published this as a percentage. It is not computable as one — one human commit can replace 100 cuts while one agent delta adds one, and neither source attributes the final document's objects. A percentage needs object-level provenance that does not exist. Is the hybrid actually hybrid? → balance the investment|P1|—|no|

### 3g. Preview / playback

| # | Event | Kind | Properties | Hook | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|80|`preview_health`|E|`mode`E, `seeks`N, `p50_seek_ms`N, `p95_seek_ms`N, `incomplete_seeks`N, `stalls`N, `waiting`N, `decoded_frames`N, `dropped_frames`N, `overlay_videos_max`N, `audio_tracks_max`N, `max_drift_ms`N|renderer rollup flushed with #65|**accumulate in a ref inside the rAF `tick`; emit once.** A `capture()` at `StudioPreview.jsx:263`/`:264` (`syncAudioEls`/`syncOverlayVideos`) or `:228` (the clock effect) runs 60×/s — banned class 1, and it would make the thing being measured worse. `dropped_frames` from `getVideoPlaybackQuality()` replaces codex's hand-rolled cadence row|session, project, install|**Is scrubbing smooth on other people's machines?** → the core interaction outranks any feature|P0|1/sess|no|
|81|`preview_failure`|E|`class`E{source_missing\|metadata\|decode\|seek_never_completed\|stall\|audio_play\|overlay_sync}, `media_kind`E, `source_codec`E, `ready_state`E, `recovered`F|`web/src/studio/StudioPreview.jsx:203` (the existing `dbg.event` fan-out, error/stalled/waiting names only)|the name list at `:200` is fixed; only 3 of the 8 are failures. 100% upload|session, project, install|Which playback paths break? → proxy/format work|P0|0-3/sess|no|
|82|`preview_export_divergence`|E|`field`E{overlay_geometry\|crop\|scale\|audio_mix}, `magnitude_bucket`B|new comparison at export time in `server/render_jobs.py:580`|**neither doc had this.** `RULES.md:62` makes "preview == export" a contract and `RULES.md:183` records a real violation (source-px crop on a canvas-sized proxy → `ffmpeg exit 234`). Compare the assemble EDL's geometry to the canvas values|job, project, install|The app's stated north star is currently unverifiable → make it verifiable|P1|rare|no|

### 3h. Render / proxy / assemble

| # | Event | Kind | Properties | Hook | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|83|`render_queued` + `render_started`|E|`publish_intent`F, `origin`E{editor\|agent\|agent_op}, `runtime`E, `queue_ms`N, `n_cuts`B, `n_overlays`B, `n_tracks`B, `duration_s`B, `has_audio`F, `renderer_family`E, `hdr_policy`E|`render_queued`: `:68`, `:91`, `:114` (the line after each `with self._lock` block closes, before the thread start). `render_started`: `:326`'s copied record, emitted after the `with` block|**item 4/5**: `publish_intent` is persisted at creation (`:66` true, `:105` false, `:83` derived from `_normalize_output_path`). `render_queued` **must be uploaded**, not local-only — it is the export denominator, and the two early failures at `:341-343`/`:377-379` never reach `_render_locked`. **Emit outside the lock**: `_set` holds `self._lock` at `:322` and `capture()` can block|job, project, session?, install|Render denominator + timeline complexity → wall #4|P0|1-4/sess|no|
|84|`render_finished`|E|`status`E{done\|failed}, `total_ms`N, `queue_ms`N, `resolve_ms`N, `proxy_ms`N, `assemble_ms`N, `publish_ms`N, `output_mb`B, `final_review_status`E, `warning_codes`E[]|`server/render_jobs.py:326` (in `_set`, on a terminal transition)|same 4-point set as item 5. Stage timings require new monotonic marks in `_render_locked` (`:503-605`); codex's separate assemble start/finish rows collapse here|job, project, install|Does rendering deliver? → wall #4|P0|1-4/sess|no|
|85|`render_summary`|E|`n_scenes`N, `n_cached`N, `n_rendered`N, `cache_hit_rate`N, `hdr_policy`E, `hdr_source`F, `hdr_decision`E, `hdr_encoder`E, `has_zscale`F, `has_10bit_encoder`F|`server/render_jobs.py:326` (terminal), reading a **new typed field** on the job record|**codex #6, verified**: `video_compose` computes `n_scenes`/`n_cached`/`n_rendered` at `:1559-1566`, but `render_jobs.py:589-591` turns them into a **warning string** and `_set` receives only `output_path`, `final_review_status` and `warnings`. **The numbers are discarded today.** They must be propagated as a typed summary — never parsed back out of the warning text|job, project, install|**Is "edit live, render rarely" real?** (`RULES.md:66`) → fix the cache key if it isn't|P0|1-4/sess|no|
|86|`proxy_cache_miss_reason`|E|`reason`E{first_render\|source_hash\|scene_spec\|runtime\|renderer_family\|canvas\|crop\|missing_clip\|corrupt_record}, `count`N, `cache_bytes`B, `evicted_count`N|`server/render_jobs.py:326` (aggregated per render from a new per-scene classifier)|**codex's "in neither" row, and the one claude most regrets missing.** `render_cache.py:53-58` names the identity inputs (`render_runtime`, `renderer_family`, `canvas`, solo-scene spec, source content-hash), so the reason is derivable by comparing components. **Not `video_compose.py:1492`** — that is `with cache.lock(key)`, the *start* of scene handling, inside the per-scene loop|job, project, install|**Counting hits tells you the rate; counting reasons tells you which invalidation rule to repair** — e.g. is crop (`RULES.md:180`) the real cost?|P1|1-4/sess|no — reason enum only, never paths or hashes|
|87|`comp_rerender_triggered`|E|`runtime`E{remotion\|hyperframes}, `n_comps`N, `reason`E|`server/render_jobs.py:326` (from the same per-scene classifier, composition scenes only)|**neither doc had this.** `RULES.md:70-73`: only composition clips require a re-render, and only the changed one. Aggregate cache rate cannot verify that promise|job, project, install|The boundary between cheap FFmpeg arrangement and expensive runtime re-render → the app's central performance claim|P1|0-2/sess|no|
|88|`render_failed`|E|`stage`E{input\|resolve\|proxy\|assemble\|publish\|review}, `failure_class`E{no_edit_decisions\|cut_source_not_found\|ffmpeg_nonzero\|crop_oob\|missing_encoder\|renders_dir_escapes\|disk\|permission\|tool_exception}, `ffmpeg_exit_bucket`B, `retryable`F|`server/render_jobs.py:563` (result failure), `:359`, `:401`, `:434`|four distinct failure writers|job, project, install|**The failure taxonomy** → fix the top class weekly|P0|rare|**SENS** — `result.error` embeds absolute paths. `_scrub` (`:145`) redacts them, but **classify to an enum first** and send `failure_class`, not the string|
|89|`render_superseded`|E|`stage`E{queued\|running\|publish_guard}, `elapsed_ms`N, `newer_origin`E|`server/render_jobs.py:176` — return a **changed** flag and emit outside the lock|**item 5**: this writer exists precisely *because* `_set`'s supersede guard would drop the update (`:169-173`). Instrumenting only `_set` misses every supersede — but `_set` **also** calls it at `:324`, so without a changed-flag both paths double-emit|job, project, install|Users spamming Render? → debounce. **Never counted as a failure in wall #4**|P0|rare|no|
|90|`publish_partial`|E|`phase`E{video_replaced_no_receipt\|persist_failed\|receipt_failed}|`server/render_jobs.py:359` and `:401` (the outer `except` in `_run`/`_run_with_inputs`), classifying by inspecting `final_render_status`|**codex B4, and he is right that `:580` cannot see it**: if `publish_final_render` raises *after* `os.replace` at `lib/project.py:605` — during `persist_doc` at `:612` or the receipt write at `:616` — control never reaches `:580`, it unwinds to the outer catches. `lib/project.py:561-563` says the new bytes are then **deliberately** stale, so this is the dangerous case and the one `:580` would have missed entirely|job, project, install|A commit-path regression that silently orphans the deliverable → alarm|P0|rare|no|
|91|`media_op_finished`|E|`tool_id`E, `status`E, `wall_s`B, `produced_asset`F, `deliverable_write_warning`F|`server/render_jobs.py:326` (terminal, `origin='agent_op'`)|`_run_op` (`:403`) routes through `_set`|job, turn, project, install|Which heavy ops earn their keep? → prune `tools/video/*`|P1|0-3/turn|no|
|92|`render_cache_hit_rate`|D|rate, split first vs repeat render|— source: #85|—|job, project|Wall #4 drill-down|P0|—|no|
|92a|`skill_outcome_join`|D|per-skill useful-turn rate|— source: #44 `skills[]` joined to #39 by `turn_id`|—|turn|**codex's late adopt**: claude had skill *reach* but never joined it to whether the turn succeeded. That join is the only way to find a skill that reliably makes the agent worse → delete or rewrite it|P1|—|no|
|92b|`cross_project_reuse`|D|`assets_reused`N, `styles_reused`N|— source: #29's **`asset_fingerprint`** recurring across `project_id` within one `install_id`, plus #21's `style`|`asset_fingerprint`, `install_id`|**codex F4**: `asset_id` cannot do this — it is a fresh uuid4 per project, so a re-import into a second project gets a different id and reuse is invisible. `asset_fingerprint` is install-stable and content-derived, which is exactly the join needed. **codex R1**: my first name for it was `content_fingerprint`, which the doc's own reserved-substring rule forbids — verified, `_scrub` rewrites it to `content_fingerprint_len: 16`, i.e. the length of the hex string. I wrote the rule in §3 and then broke it two sections later; the scrub round-trip contract test (§7) now covers this property by name. Do users build a library? → decides a global asset/template feature|P2|—|no|

### 3i. Export — the North Star

`export_completed` exists today only in `tests/contracts/test_analytics.py:99`. No app code
emits it. The North Star is unmeasurable.

| # | Event | Kind | Properties | Hook | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|93|`export_completed`|E|`ordinal`N, `first_export`F, `app_watermark_free`F, `duration_s`B, `width`N, `height`N, `fps`B, `output_mb`B, `video_codec`E, `audio_codec`E, `hdr`E, `n_cuts`B, `n_overlays`B, `n_transitions`B, `n_keyframes`B, `has_music`F, `has_narration`F, `has_sfx`F, `doc_origin`E{agent\|human\|mixed}, `agent_cost_to_date_usd`N, `time_from_create_s`B, `renders_before`N, `final_review_status`E|**`server/render_jobs.py:580`**|**item 3.** `:576` is the `if not published["published"]` guard; `:580` is the first line after it, where `origin` and `job_id` are in scope. `publish=True` guarantees `receipt_doc` was non-`None` (`:355` editor, `:396` agent). **Definition** of success = the receipt write completing at `lib/project.py:616`. NOT hooked in `lib/` — `lib/project.py:439` forbids depending on `server`|job, project, session?, install|**THE North Star, plus the shape of what users actually make** → activation, TTV, cost, defaults|P0|0-2/sess|no — every field is a shape or enum|
|94|`export_failed`|E|`stage`E, `failure_class`E, `first_export_attempt`F, `retryable`F, `elapsed_ms`N|`server/render_jobs.py:326` (terminal `failed` where `publish` was intended)|distinguishes an export attempt from an intermediate render|job, project, install|What blocks activation? → the highest-leverage fix|P0|rare|no|
|95|`export_aborted_before_job`|E|`reason`E{save_failed\|agent_busy\|invalid_doc\|duplicate_click}, `elapsed_ms`N|`web/src/studio/Studio.jsx:266` (the `save-failed` abort branch)|intent that never became computation|session, project, install|Why does intent never start? → fix the save/race path|P0|rare|no|
|96|`export_timed_out_in_ui`|E|`polls`N, `elapsed_s`B, `backend_terminal_later`E|`web/src/studio/Studio.jsx:287` (existing `dbg.event('ui.render', {phase:'timeout'})`)|exact line, already there|session, project, job, install|Does the UI give up while work continues? → stream progress instead of polling|P0|rare|no|
|97|`export_became_stale`|E|`cause`E{human_edit\|agent_edit\|external_replace\|receipt_missing}, `time_since_export_s`B|`web/src/App.jsx:1292` (inside the poll's `.then`, on the `current` flag flipping true→false)|**NOT `lib/project.py:445`/`:498`** — `final_render_status` runs inside `list_assets` (`app.py:625`) which `App.jsx:1294` polls **every 4s** (its own docstring says so), and it is in `lib/`. Emit from the consumer on the transition|project, session, install|How often do users edit after exporting? → re-export cues|P1|0-1/export|no|
|98|`export_artifact_opened`|E|`surface`E{render_preview\|asset_card}, `current`F, `first_export`F|`web/src/App.jsx:1341` (a new `onPlay` on the existing `<video>`)|**claude's `export_opened_externally` is DELETED**: `shell.openPath` is never called anywhere (verified — only `shell.openExternal` for http URLs at `desktop/main.js:600`/`:607`). There is no external-open product path to instrument|project, session, install|Do users review the finished work? → QA handoff|P1|0-3/export|no|
|99|`export_downloaded`|E|`output_mb`B, `time_from_complete_s`B, `first_export`F|`web/src/App.jsx:1347` (a new `onClick` on the `<a download>`)|the element has **no handler today** — this row requires adding one|project, session, install|Did the artifact leave the app? → production vs handoff|P1|0-1/export|no|
|100|`activation_rate`|D|rate|— source: #1, #93|—|install|Wall #1|P0|—|no|
|101|`time_to_value`|D|P50, P90|— source: #1, #93|—|install|Wall #2|P0|—|no|
|102|`export_shape`|D|distributions|— source: #93|—|install|What does a real OpenNolan reel look like? → tune defaults, styles, the agent guide|P0|—|no|
|103|`repeat_export`|D|`export_index`, `days_since_prior`|— source: #93 `ordinal>1`|—|project, install|Iteration or one-shot trial? → versioning|P0|—|no|
|104|`export_cadence`|D|median days between exports|— source: #93|—|install|Are we in the weekly workflow? → the real habit test|P1|—|no|

### 3j. Errors, crashes, failures

Expected product failures are bounded classes in product analytics; unexpected exceptions go
once to the error inbox. Raw stderr, paths, media, tool I/O and creative text never become
product-event properties.

| # | Event | Kind | Properties | Hook | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|105|`error_reported`|E|`layer`E{electron_main\|renderer\|react\|python_api\|agent_sdk\|render_worker}, `fatal`F, `handled`F, `fingerprint`E, `release`E, `n_prior_this_session`N|existing: `server/analytics.py:187` / `:221`, `desktop/main.js:91`; add `session_id`|**codex #19, conceded**: claude's map wrongly marked desktop errors local-only. `reportDesktopError` **already POSTs to PostHog** (`desktop/main.js:65-107`). The gap is `session_id` and delivery ack, not capture. **`fatal` must be honored by the crash-free formula** (item 6) — claude's original counted handled errors as crashes|session?, install|One crash inbox → triage and rollback|P0|0-3/sess|**SENS** — frames carry `/Users/<name>/…`; `_before_send` (`:108`) redacts. Keep it; never bypass|
|106|`http_error`|E|`route_template`E, `status`E{400\|404\|409\|422\|503}, `class`E{validation\|not_found\|conflict\|unavailable\|path_guard}, `user_visible`F|`web/src/api.js:6` (inside `json()`'s `!resp.ok` branch at `:4`, before the `throw`) — counted into a per-session rollup|**codex's client-side choke point, adopted**: it catches exactly the 4xx that reach a user. Every `HTTPException` bypasses the global handler at `server/app.py:421`, so this whole surface is invisible today. A backend middleware is P1 for the 4xx the UI never surfaces|session, install|The whole 4xx surface → every 422/404 is a UX bug we cannot see|P0|1/sess rollup|no — route TEMPLATE, never the concrete path|
|107|`network_operation_failed`|E|`operation`E{auth\|analytics\|feedback\|asset_api\|chat\|update\|provision}, `class`E{offline\|dns\|timeout\|tls\|reset}, `retry_count`N, `recovered`F|a new `.catch` in a shared `fetch` wrapper in `web/src/api.js`|**NOT `api.js:3`** — `json(resp)` only runs when the fetch **resolves**; offline/dns/tls/reset reject before a response exists, so 5 of 7 declared classes were unobservable there. Verified: every caller is `fetch(...).then(json)` (`:11`, `:176`)|session, install|Where does connectivity break a local-first app? → offline queue|P1|rare|no|
|108|`user_visible_failure`|E|`where`E{save\|render\|upload\|load\|preview\|agent}, `error_class`E, `recovered`F|`web/src/studio/Studio.jsx:97` (inside `flash`, on `kind==='err'`)|one central place every red toast passes through|session, project, install|**Toasts-per-session is the honest quality metric**|P0|0-3/sess|no|
|109|`provisioning_error`|E|`tier`E, `stage`E, `failure_class`E, `stderr_class`E|`desktop/main.js:401` (composition catch) and the `fatal` path at `:116`|per-tier, not per log line|install|First-run failure taxonomy → ship blocker|P0|rare|**SENS** — classify stderr; never ship it|
|111|`data_quality_violation`|E|`class`E{unknown_event\|unknown_property\|wrong_type\|high_cardinality\|reserved_substring}, `event_name`E, `blocked`F|new `validate_event` in `server/analytics.py`, before `_scrub` at `:172`|drops the offending property, counts the violation, sends the rest|install|Is the taxonomy drifting? → fail CI or block the event|P0|rare|no — names only, value discarded|
|112|`telemetry_delivery_health`|D|`queued`, `sent`, `dropped`, `rejected_schema`, `oldest_age_ms`, `orphan_events{family}`, `duplicate_event_ids`|**durable local counter**, attached as properties to the next **successfully** flushed event|**item 11, codex #10**: this cannot be a remote event from the failed sink. `analytics.py:175-176` swallows its own failures and `:116-117` `_before_send` swallows too. Same for claude's `swallowed_error`: a counter at each of the 5 swallow sites (`analytics.py:176`, `activity.py:194`, `recorder.js:132`, `desktop/main.js:108`, `feedback.py:149`), never an event|install|**Without this every other number can silently be a lie** → fix the sink before trusting a dashboard|P0|—|no|
|113|`crash_free_session_rate`|D|rate|— source: #9 (denominator = **starts**), #105 `fatal=true`, #12 `process_gone` **classified session-fatal**, and **#113a `unclean_timeout`**|—|session|Wall #5. **A hard main-process crash emits none of the explicit signals** — `desktop/main.js:673-674` observes only renderer/child loss and `:685` cannot run after main dies — so without `unclean_timeout` the users who crash hardest are the ones the metric cannot see. Non-fatal error-free rate reported separately|P0|—|no|
|113a|`unclean_timeout`|D|`prior_session_id`I, `lateness_s`N|— a **late-arriving warehouse cohort**: #9 starts with no matching #10, no #105 `fatal=true` and no session-fatal #12, once a fixed lateness window has passed|**codex F2**: an earlier draft marked this EMITTED with a "server-side sweep" hook. There is no such call site — it would fail contract test 1b — and the local backend is *stopped* at `desktop/main.js:684-685`, so nothing local can emit after the process dies. It is a query, not an event. The next launch's `previous_exit` enriches the reason when it arrives but is never required|session, install|The crashes no signal can reach → wall #5's missing numerator term|P0|—|no|
|114|`recovery_rate`|D|rate|— source: #105 + #93|—|session, project|Which errors actually kill the export? → prioritize those|P1|—|no|

### 3k. Feedback and requests

| # | Event | Kind | Properties | Hook | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|115|`feedback_submitted`|E|`kind`E{bug\|idea\|other}, **`feedback_chars`**N, `has_email`F, `has_diagnostics`F, `has_debug_session`F, `delivery`E{sent\|failed\|not_configured}, `surface`E, `errors_this_session`N, `exports_to_date`N|`server/feedback.py:177` (existing capture)|**rename `message_len` → `feedback_chars`**: verified `{'message_len': 88}` → `{'message_len_len': None}`|session?, install|Who complains, and had they succeeded first? → weight reports by whether the user was winning|P0|rare|no — the body stays local + email only|
|116|`feedback_delivery_failed`|E|`channel`E{relay\|resend}, `class`E{not_configured\|timeout\|http\|network}, `locally_stored`F|`server/feedback.py:149` (the `except requests.RequestException`)|**the only qualitative channel — silent loss is unacceptable**|P0|rare|no|
|117|`debug_report_outcome`|E|`phase`E{record_started\|record_stopped\|submitted\|discarded}, `event_count`B, `error_count`B|`web/src/studio/Studio.jsx:118` (existing `onToggleRecord`)|one per phase|session, install|Do bug reports carry evidence? → improve the recorder flow|P1|rare|no|
|118|`survey_shown` / `survey_answered`|E|`survey_id`E, `trigger`E{post_first_export\|repeat_failure\|pre_abandon}, `choice`E (closed set), `delay_ms`N|new surface; **no anchor invented**|both docs anchored survey rows at unrelated lines (codex's three at `Studio.jsx:262`, the render callback). Cap: one per install per week. `survey_dismissed` deleted — it is the absence of an answer|session, install|Contextual asks beat a generic form → build/remove evidence|P2|≤0.2/sess|no — closed choices only; free text goes to feedback|
|119|`request_evidence_rollup`|D|`capability_id`E, `distinct_installs`N, `explicit_requests`N, `agent_blocks`N, `workaround_sequences`N, `blocked_exports`N|— source: #48, #50, #47, #52, #53, #54, #115|—|install|**The ranked build queue** → the roadmap counter in §2|P0|—|no|

### 3l. Retention, habit, lifecycle

| # | Event | Kind | Properties | Hook | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|120|`meaningful_active_day`|D|`action`E{export\|agent_turn_useful\|editor_commit}, `first_of_day`F|— source: #93, #39, #65. **No hook** — claude and codex both anchored rows like this at `analytics.py:166`, the generic sender (item 11)|—|install|Real creative work today? → the retention denominator|P0|—|no|
|121|`project_revisited`|D|`age_days`, `days_since_last_open`, `prior_exports`|— source: #23|—|project, install|Do projects create ongoing work? → resume features|P1|—|no|
|122|`week_active`|D|`week_n`, `had_turn`F, `had_commit`F, `had_export`F|— source: #38, #65, #93|—|install|W1/W4 project-active retention|P0|—|no|
|123|`cohort_retention`|D|`window`E{D7\|D30}, `cohort`E{first_project\|first_export}, rate|— source: #120|—|install|Comparability. **D1 is computed but carries no threshold** (§4)|P1|—|no|
|124|`churned`|D|`last_seen_days`B, `n_exports`N, `furthest_stage`E, `last_error_class`E|— source: #27, #93, #105|—|install|**Did they leave broken or just finished?** → distinguishes a bug from a satisfied one-off|P1|—|no|
|125|`dormant_project_inventory`|D|`age_bucket`B, `has_export`F, `last_stage`E, `count`N|— source: #27|—|install|Where do projects die before value? → cleanup UX|P2|—|no|

**Rows 126-142 are the audio and join-health additions both docs missed**, kept together because
they share one rationale: a technically successful export can still be a failed product.

| # | Event | Kind | Properties | Hook | Viability | Joins | Question → Decision | Pri | Vol | SENS |
|---:|---|---|---|---|---|---|---|---|---|---|
|126|`audio_output_health`|E|`peak_dbfs_bucket`B, `integrated_lufs_bucket`B, `clipped_samples`N, `silent_seconds`N, `channel_layout`E, `n_stems`N|`server/render_jobs.py:580` (from a local ffmpeg loudness pass on the published file)|**codex's "in neither" row.** A successful MP4 with inaudible or clipped audio is not a successful export, and nothing in either doc would notice|job, project, install|Is the deliverable audible? → mix defaults, `_mix_structured_audio` bugs|P1|0-2/sess|no — loudness buckets, never audio|
|127|`audio_stem_shape`|E|`n_music_regions`N, `narration_mode`E{replace\|layer\|none}, `n_sfx`N, `music_split_count`N|`server/render_jobs.py:580` (derived from the receipt doc)|`interp.musicRegions` (`interp.js:693`) and the music `oneOf object\|array` schema make this a recent, expensive, unvalidated surface|job, project, install|Is the audio-stem work used? → justify or shrink it|P1|0-2/sess|no|
|128|`orphan_event_count`|D|`family`E, `unmatched_session`N, `unmatched_project`N, `unmatched_turn`N, `unmatched_job`N|— source: #112|—|install|**Join health.** A silently-changing denominator is worse than a missing metric|P0|—|no|
|129|`agent_continuity`|E|`event`E{unsolicited_drained\|resume_note_injected\|model_switched\|session_resumed}, `n_drained`N, `mid_thread`F|`server/agent_runner.py:1938` (drain), `:1953` (resume note), `:2063` (model switch)|**23 rows across both docs collapse into this one continuity counter.** These are regression alarms on hard-won fixes (the off-by-one), not product intelligence — codex was right to demote them|turn, project, install|Is a fixed bug still fixed? → regression alarm|P1|rare|no|
|130|`time_in_app_vs_rendering`|D|ratio, **session**-scoped|— source: **`render_foreground_overlap_s` on #10** ÷ #10 `foreground_s`|`session_id`|**item 13.** Summing `wall_s` double-counts: `project_lock` (`render_jobs.py:482`) serializes per *project*, so two projects render concurrently and their intervals overlap. The union-of-intervals fix is correct and — after item 1's correction — render jobs do carry `session_id`. **But codex B12 is right that it was still not computable**: #10 carries only aggregate `foreground_s`, and an aggregate cannot be intersected with concurrent render intervals. Fixed by accumulating the intersection **locally in the renderer**, which already knows both sides (it polls render status and owns focus), and shipping one number — `render_foreground_overlap_s` on #10. No interval endpoints are uploaded. **Still demoted out of the wall numbers**, for a different reason than claude first gave: it measures our implementation more than the user's experience, and `RULES.md:66` is better tested by #72 (`preview_mode_switched`)|P1|—|no|

**Folded, not rows.** Three ids are retired into their carriers so no row claims `EMITTED` without an
emitter (codex B7): **#56** `asset_mention_resolved` → `mentions_missing`/`mentions_outcome` on **#38**
(`resolve_mentions` runs at `server/app.py:1095`, before `turn_id` exists at `:1956`, so a standalone
row could never join to its turn); **#60** `agent_human_conflict` → `local_commits_during_turn`/
`save_blocks`/`undo_restored_local` on **#58**; **#110** `sandbox_denied` → **#45** `action='deny'`
with `reason_class` + `root_family`. The ids are not reused.

**Not rows — the `feature_id` vocabulary.** These twelve are enum members for #65's per-feature
map and are deliberately excluded from the 130 count (claude's phase-1 doc made exactly this
mistake and codex caught it): `editor.cut_trim`, `editor.cut_reorder`, `editor.split`,
`editor.duplicate`, `editor.delete`, `editor.transition`, `editor.speed`, `editor.clip_transform`,
`editor.crop`, `editor.keyframes`, `editor.overlay_timeline`, `editor.audio_ops`. The full
enum is **derived at build time from `PROPERTY_TITLES` (`propertySchema.js:17`, 8 entries) plus
the drag `mode` set (`StudioTimeline.jsx:138-202`) plus the toolbar and timeline actions** — R2's
settlement, so the count can never be wrong again.

---

## 4. Metric definitions

Only formulas computable from §3. Every one names its join key. Formulas whose join cannot exist
were reworked or demoted, and that is noted.

| Metric | Formula | Join | Good / bad | Triggers |
|---|---|---|---|---|
| Activation, 7d | §2 wall 1 | `install_id` | ≥40% / <20% | <20% → freeze features, fix the largest funnel loss |
| Project→export conversion | `distinct project_id with #93` / `distinct project_id from #21 old enough for the window` | `project_id` | ≥60% / <30% | separates onboarding loss from execution loss |
| Time to value | `P50, P90(first #93.ts − #1.ts)` | `install_id` | P50 <1d / P90 >7d | P90 >7d → ship a template project |
| Render success rate | `#84{done}` / `(#84{done} + #84{failed})` — **`#89 superseded` excluded from both** | `job_id` | ≥95% / <90% | top `failure_class` from #88 becomes the week's work |
| Export success rate | `distinct #93 job_ids` / `distinct #83 render_queued job_ids with publish_intent=true that have matured` | `job_id` | ≥95% / <90% | **item 4.** A terminal-only denominator (`#93 + #94`) would silently drop attempts that never reach a terminal event — which is the exact hole moving the boundary to creation was meant to close. #94 is the failure breakdown only |
| Agent turn success rate | `1 − #39{is_error}` / `#38` | `turn_id` | ≥90% / <80% | model / guide / tool-chain, by failure slice |
| Agent useful-turn rate | `#39{doc_changed OR artifacts_delta>0 OR render_published}` / **`#38`** | `turn_id` | ≥70% / <50% | denominator is **delivered** turns, not successful ones: BYOK means the user pays for errored turns too, and a conditional rate would hide that |
| Agent tool success by tool | `1 − errors/calls` per `tool_id` from #44; `denied` excluded and reported as a separate permission rate | `tool_invocation_id` → `turn_id` | ≥95% / <85% after 30 calls | repair, replace, or stop advertising the tool |
| Cost per successful export | `sum(#39.cost_usd for the project through first #93)`; portfolio = `sum / count(first exports)` | `turn_id` → `project_id` | P50 <$3 / P90 >$10 | publish an honest cost estimate; add turn guardrails |
| Cost of failed work | `sum(#39.cost_usd)` for projects with no #93 **whose 7d window elapsed** / those projects | `project_id` | P50 <$1 | censoring guard added (codex #20-adjacent) |
| Feature adoption | `#65.features_used` / `#65.features_eligible`, **install level for both** | `install_id` | ≥30% core / <10% | never divide by all users |
| Feature twice-use | adopters using F in ≥2 sessions on different days / adopters with a later eligible session | `install_id` | ≥40% / <10% | <10% → deletion review |
| Feature discovery | first use within the first 3 editor sessions **eligible for F** / installs with ≥3 sessions **eligible for F** | `install_id` | ≥50% core / <20% | **low adoption + high discovery = delete; low both = fix the UI** |
| Undo rate / regret | undone-once-per-`action_id` within 5 min and not redone in 30 s / commits+drags for F | `action_id` → `session_id` | <10% / >30% | **codex #12**: without `action_id`, undo→redo→undo double-counts |
| Edit-to-export ratio | `#65 commits` / `#93`, P50 and P90 | `project_id` | P50 5-50 | ≈5 = generator with review; ≈500 = real NLE. Decides where investment goes |
| Session depth | unique `feature_id` + meaningful actions per #65 | `session_id` | ≥3 features + 1 success | a long session with few commits means stuck, not engaged |
| Crash-free (fatal) | §2 wall 5 — numerator includes `unclean_timeout` (#113a); `process_gone` counts only when classified session-fatal | `session_id` | ≥99.5% / <98.5% | <98.5% → halt rollout |
| Non-fatal error-free | `1 − distinct(session_id with #105 fatal=false)` / `#9` | `session_id` | ≥95% | reported **separately** — item 6 |
| Launch success | `#3 backend_ready` / `#2 app_launch_started` | `install_id` | ≥99% / <97% | roll back or fix provisioning |
| Preview stall-free | play intervals with zero stalls / play intervals, from #80 | `session_id` | ≥98%; P95 seek <250 ms | proxy / cache / playback work |
| Render cache hit rate | `sum(#85.n_cached)` / `sum(#85.n_scenes)`, split first vs repeat | `job_id` | repeat ≥70% / <40% | **#85 requires propagating the typed summary — the numbers are discarded today (codex #6)** |
| Second-project rate | installs creating project 2 within 14 d of first export / activated installs | `install_id` | ≥50% | **the primary retention number** |
| W1/W4 project-active | #122 | `install_id` | — | secondary |
| D7 / D30 | #123 | `install_id` | D7 ≥25% / <12% | comparability. **D1 computed, no threshold**: a tool used 1-4×/month would read red forever |
| Time in app vs rendering | union(render running) ∩ foreground / foreground | `session_id` | <20% / >40% | **demoted from the walls** — item 13. Union, never sum: cross-project renders overlap |
| Feedback delivery | `#115{delivery=sent}` / `#115` | `install_id` | ≥99% | add retry |

**Deleted for want of a join:** nothing, after item 1's correction. claude's phase-1 doc and its
first merge draft both demoted metrics on the belief that backend events could not be
session-scoped; codex refuted it and the demotions were withdrawn. The one remaining scope
limit is the detached nightly sweep (#27), which carries no `session_id` and is therefore
install-scoped by design.

---

## 5. What to BUILD / what to REMOVE

### BUILD — ranked by signal strength, with the volume floor

| Rank | Channel | Detection | Floor before acting |
|---:|---|---|---|
| 1 | Repeated successful workaround | n-gram over #65's per-feature sequences; #52/#53 followed by a produced asset | 5 installs + 10 sequences (3 installs if it blocks a first export) |
| 2 | Agent-declared capability gap | #48, #50, #47 | 3 installs or 5 blocked turns; act immediately on a deterministic first-export blocker |
| 3 | Explicit request | #115 `kind='idea'`, read directly at beta scale | 3 independent requesters + behavioral friction |
| 4 | Freehand ffmpeg | #54's `filter_family` where a registry tool exists | same family 5× across 3 installs |
| 5 | `@`-mention / search miss | #57 `result_count=0` | 5 installs + 10 misses |
| 6 | Abandoned funnel | #27, #94, #95 | 50 attempts and a step losing >25%, or 10 observed sessions |
| 7 | High regret routed around | #74 regret >2× median, then another feature used | 30 adopters |
| 8 | Contextual survey | #118 | 20 responses, as supporting evidence only |

Both agents ranked agent-declared gaps at or near the top independently, which is the most
reliable convergence in this document. codex's weighted `demand_score` is **dropped**: at n=3 the
weights determine the ranking entirely.

### REMOVE — the deletion test

All six gates must pass. Thresholds are codex's structure with claude's lower N, plus codex's
escape hatch.

1. ≥30 eligible external installs **and** ≥8 weeks **and** ≥2 releases.
2. Adoption <5% **and** twice-use <20%.
3. Discovery >30% (**discovered and rejected**). If discovery <10% the bug is the UI — **fix, do
   not delete**.
4. Usage from ≥2 non-`internal` installs is required to KEEP it. Internal-only → cut.
   (`internal` already exists on every event: `analytics.py:76`.)
5. No agent tool, renderer path, accessibility path, migration or schema contract depends on it —
   a static check, not a metric.
6. Removal is reversible: **hide behind a flag for one release** and watch #108 and #115.

**Below 30 external installs, telemetry cannot justify deletion — hide, never delete.**
High-regret features with healthy adoption are **fix**, not delete.

Weekly remove report, by evidence shape: never eligible → fix measurement · exposed but
undiscovered → relocate/teach · used once → interview then hide-test · high regret → redesign
defaults · developer-only → hide · code path never exercised (branch enum with zero hits in 90 d)
→ delete after a compatibility review.

**First candidates this system would rule on:** the manual Save button
(`StudioToolbar.jsx:91`, vestigial against 700 ms autosave at `Studio.jsx:247`) · ⇅ Arrange
(`Studio.jsx:513`, covered by auto-stacking) · the canvas picker (`Studio.jsx:596`, `RULES.md:3`
is vertical-only) · `hdr_policy='preserve'` (`video_compose.py:1312`) if #85 shows no external
HDR source · the ~40 `tools/video/*` modules the agent never calls (#44) · unused transitions
(`model.js:7`) · the `narration` inspector panel (`propertySchema.js:160`) · manual keyframing if
presets dominate.

---

## 6. Volume and cost

**Agreed session model — ~30 uploads for a productive beta session** (target ≤40, hard cap 100,
criticals bypass; taxonomy capped at ≤100 EMITTED names, currently 97 across 95 rows). codex initially held out
for ≤60/session with per-interaction editor commits and **conceded to ~30 once the `action_digest`
removed the reason for them** — a 20-minute editing session is 50-300 commits, so 60 commits alone
would exhaust a 60-event budget before a single render, turn or asset event.

```
  launch / session / doctor                                 3.2
  auth (amortized)                                          0.2
  project open + editor open + create                       2.5
  asset ingest: 2 assets x (import + probe) + browser proxy  4.3
  agent: 2 turns x (started + completed + tool_rollup)       6.0
         + tool failures                                     0.5
  editor: session_summary + first_use + blocked + save_fail  3.0
  preview: health rollup + failures                          1.3
  render: 2 renders x (started + finished + summary)          6.0
          + superseded                                       0.3
  export: completed + failed + aborted                       0.7
  errors: error_reported + http rollup + visible failure      2.0
  feedback                                                    0.05
  ------------------------------------------------------------------
  TOTAL                                                     ~30.0 / session
```

Heavy session (4 turns × 20 tools, 60 commits, 4 renders, 2 exports) ≈ **44**, still under the
hard cap. Browsing-only ≈ **8**.

**Pricing assumption.** Progressive bands `$0.0000500` (1-2M), `$0.0000343` (2-15M),
`$0.0000295` (15-50M), first 1M free. Source: https://posthog.com/pricing as read by codex,
checked 2026-08-05. **Neither agent had network access; these bands are unconfirmed against live
pricing and must be re-checked before anyone commits a budget.**

| Scale | Arithmetic | Events/mo | Cost |
|---|---|---:|---:|
| Private beta | 100 MAU × 8 sess × 30 | 24,000 | **$0** |
| Growing beta | 1,000 × 12 × 30 | 360,000 | **$0** |
| Post-beta | 10,000 × 15 × 30 | 4,500,000 | **$135.75** = $50 (1M @ 5.00e-5) + $85.75 (2.5M @ 3.43e-5) |

**The two reference points the reviews argued over, resolved:**

- claude's rejected per-interaction design, 41.52M events/mo: tiered =
  `$50 + 13M × 3.43e-5 ($445.90) + 26.52M × 2.95e-5 ($782.34)` = **$1,278.24**. Both agents
  recomputed this independently to the cent.
- claude's original `$2,026` = `40.52M × 5.00e-5`, a **flat-rate ceiling**, not an estimate. It
  overstated the real figure by 1.6×. Relabelled.
- claude's Tier-B rollup list summed to **8**, not 7 (`#58×3 + #82 + #115 + #106 + #123×2`), so the
  original per-session claim was 28, not 27. Corrected.

Free-tier headroom at 30/session: `1,000,000 / 30 = 33,333 sessions/month` ≈ **2,777 MAU** at 12
sessions each. For reference, codex's pre-concession 60/session model gives 9M/month at 10,000 MAU
≈ **$290.10** under the same bands — both agents verified that figure too, so if the human ever
wants per-commit upload instead of the digest, that is the price. The binding constraint is not price — it is that high-cardinality raw data makes
queries untrustworthy long before it becomes expensive (codex's framing, adopted).

**Never sampled, at any scale:** #1, #17, #93, #94, #88, #89, #90, everything in 3j, #115, #116,
and every unmet-capability row (#47-#54). These are the numerators *and* denominators of low-N
rates: at 30 installs a 10% sample of activation gives 3 observations.

**Sampling, post-beta only:** deterministic hash of `install_id + session_id` so a whole session
is kept or dropped — never individual events, which would break sequences. #65 and #80 at 50%,
#106 at 20%. Not worth doing below 500k events/month.

**Local-only, never uploaded:** the recorder NDJSON (`recorder.js`, capped at
`MAX_SESSION_EVENTS` `:122`) and `activity.jsonl`'s full `target` field. Both reach the developer
only when the user chooses to send a debug report (`feedback.py:165`). That trust boundary
already exists.

---

## 7. Implementation architecture

### The envelope (item 1)

| Id | Type | Required | Minted | Threaded |
|---|---|---|---|---|
| `schema_version` | N | yes | taxonomy constant | — |
| `event_id` | I | yes | at emit (uuid4) | dedupe key |
| `install_id` | I | yes | **exists today** — `settings.device_id()` (`settings.py:80`). ⚠ Per-`OPENNOLAN_HOME`, not per-machine: stable in packaged, but every dev worktree mints its own — **see §12A** | PostHog `distinct_id` |
| `session_id` | I | **nullable** | **Electron main at boot** (`desktop/main.js:626`), exposed to the renderer via preload | `X-ON-Session` header on every `/api` call → FastAPI dependency → `request.state` → `run_turn` → `_build_render_inputs` → persisted on the job record by `RenderJobStore.start*`. Main-minted so a ⌘R reload does not split the session and so `active_session_id` can be persisted for crash detection |
| `project_id` | I | nullable | a random uuid4 **persisted in the project dir** on first use | claude proposed `HMAC(install_id, dir_name)`; codex refuted it — `install_id` is uploaded, so low-entropy folder names are guessable under that HMAC. Conceded |
| `turn_id` | I | nullable | `agent_runner.py:1956` | passed into `_build_render_inputs` (`:1529`) → job record |
| `job_id` | I | nullable | **exists today** — `render_jobs.py:64`/`:81`/`:103` | just needs attaching |
| `tool_invocation_id` | I | nullable | the SDK `ToolUseBlock.id` (`agent_runner.py:775`) | pending map, item 7 |

**The one genuinely session-less case:** a job with no originating request (the nightly
`project_stalled` sweep) or a job that outlives its renderer. Agent renders and media ops **do**
carry `session_id`, inherited through `turn_id`. Metrics must still declare their scope, but the
blanket "backend events cannot be session-scoped" claim in claude's phase-1 doc was wrong.

```
   Electron (main)                  Renderer                    Backend
   ---------------                  --------                    -------
   boot:626  launch_started +       receives session_id          analytics.py:166
             session_id MINTED      via preload                  capture()
   470       backend_ready               |                            ^
   116       launch_failure              |                            ^
   65        error_reported              | X-ON-Session header        |
   685       before-quit (flush)         v                            |
        |                          POST /api/telemetry/events    validate_event
        |                          (batched, mirrors :1016)      (NEW, before :172)
        +-------------------------------->|                           |
                                          +--------------------------+
                                                                      |
   4 render points: :66/:83/:105 (local record), :309 (transitions),   v
   :176 (superseded), :580 (export)                                PostHog
   agent: :1956 (turn start), :1978 (pending map), :1993 (terminal, in finally)
```

### Taxonomy

One file, `schemas/analytics_events.json`, loaded by Python, React and Electron — no three copies
of an enum. Each entry declares: `kind: event|metric`, property types, required/optional,
`priority`, `sampling_class`, the **question**, the **decision**, the **owner**, and
`forbidden_alternatives`. codex's richer schema is adopted over claude's thinner one because it
makes "every event names a decision" machine-checkable instead of aspirational.

`validate_event` is added to `server/analytics.py` **before** `_scrub` at `:172`: drop unknown
properties, reject unknown events and wrong types, increment #111, then hand off to the existing
scrubber and client. `web/src/analytics/` holds the batcher (`track.js`), the pure session/turn
reducers (`rollup.js`), and the action-id/feature-id classifier
(`classifyDocChange(prev, next, actionHint)`, a **new pure function beside**
`summarizeDocChange` in `model.js`, tested in `model.test.js` — never inside it, which
`RULES.md:42` requires stay pure and which runs per live frame at `Studio.jsx:179`).

The `dbg.event` bridge (`recorder.js:136`) fans out: the private local recorder gets the full
diagnostic only when recording is on; the analytics reducer gets only schema-listed summaries
whether recording is on or off; console, keystrokes, pointer coordinates and raw errors stay
recorder-only. That reuses the 22 existing call sites instead of adding 40 new ones.

### The contract tests

Three, and the third is the one item 8 demands.

```
tests/contracts/test_analytics_taxonomy.py

1. TWO-WAY NAME COVERAGE  (claude)
   a. every event-name literal in server/**.py and web/src/**.js{,x} is declared
   b. every declared entry with kind=event has >=1 live call site
   -> (b) is what would have caught export_completed living only in
      tests/contracts/test_analytics.py:99 while reading zero forever.

2. GOLDEN RECEIPT JOURNEY  (codex)
   fresh install -> auth -> project -> agent turn + tools -> human edit ->
   render -> receipt. Asserts exactly one deduplicated critical lifecycle and
   that export_completed CANNOT exist without a current receipt.

3. SCRUB ROUND-TRIP  (item 8 — the test neither doc had)
   for every property in the taxonomy with type N:
       assert analytics._scrub({name: 1}) == {name: 1}
   for every property with type E:
       assert analytics._scrub({name: "x"}) == {name: "x"}
   This single assertion fails today on prompt_len, prompt_chars, message_len,
   text_len and content_len -- every one of which appeared in one of our two
   phase-1 docs -- and on content_fingerprint, which claude introduced DURING
   this merge, two sections after writing the rule that forbids it. Verified:
   _scrub({'content_fingerprint': '<16 hex>'}) -> {'content_fingerprint_len': 16}.
   Renamed asset_fingerprint, which round-trips clean. It also fails on any key
   containing key/token/secret. Every property name introduced by this document
   has been run through _scrub; the survivors are listed in the taxonomy.
```

### Changes required in existing code

| Change | File:line | Why |
|---|---|---|
| `commit(next, action)` + `action_id` on history | `Studio.jsx:186`, `:168` | no per-feature anything, and no correct undo rate, without it |
| new `tool_result` branch + pending map | `agent_runner.py:1978` | item 7 |
| before/after doc snapshot around a turn | `agent_runner.py:1956`, `:1993` | item 10; supplies `doc_changed`/`artifacts_delta` |
| propagate a **typed** render summary | `render_jobs.py:589-593` | the numbers are turned into a warning string and discarded today |
| instrument the superseded writer | `render_jobs.py:176` | `_set` cannot see it by design |
| extend the ingest probe; call it at ingest | `app.py:586` | codec/HDR/fps are the render-outcome predictors and are never probed at upload |
| `validate_event` + reserved-substring guard | `analytics.py:172` | item 8, item 11 |
| swallow counters at 5 sites | `analytics.py:176`, `activity.py:194`, `recorder.js:132`, `main.js:108`, `feedback.py:149` | otherwise telemetry dies silently |
| `session_id` on backend + Electron events | `analytics.py:174`, `main.js:91` | item 1 |
| awaited flush on `before-quit` | `main.js:685` | item 6 |
| replace two bare `.catch(() => {})` | `Studio.jsx:313`, `:325` | #59 — silent disk/screen divergence |
| add handlers to two JSX elements | `App.jsx:1341`, `:1347` | #98/#99 have no handler today |

---

## 8. Phased plan

### P0 — 3 days: make value, cost and failure measurable

| # | Work | Files | Success condition | Test |
|---:|---|---|---|---|
| 1 | Taxonomy + `validate_event` + batch endpoint | `schemas/analytics_events.json`, `analytics.py`, `app.py`, `web/src/analytics/track.js` | an undeclared or reserved-substring property is dropped and counted; a batch reaches the existing sink once | contract tests 1 + 3 |
| 2 | **Envelope / join keys — do this first** | `main.jsx`, `api.js`, `app.py` dependency, `render_jobs.py`, `agent_runner.py` | a render and a turn both carry the ids §7 says they do; `session_id` reaches the job record on the agent path (inherited via `turn_id`), and is NULL only for the detached sweep | a fixture asserting id presence per family, incl. an agent-render job carrying the originating `session_id` |
| 3 | Export + render lifecycle, all 4 points | `render_jobs.py:66/:83/:105/:176/:309/:580`, typed summary at `:589` | one `export_completed` only after a current receipt; superseded never counts as failure; cache numbers arrive as numbers | extend `test_render_jobs_inputs.py`: exactly one terminal event per job per origin |
| 4 | Agent turn + tool correlation | `agent_runner.py:1956/:1978/:1993` | every `tool_use` reaches exactly one terminal outcome; orphans counted; a crashed turn still emits `agent_turn_completed` | `test_agent_runner.py` with a fake `ToolResultBlock(is_error=True)` and a drained-turn fixture |
| 5 | Session start/end + fatal crash-free | `main.js:626` (mint + start), `main.js:685` (awaited flush), `analytics.py`, the `unclean_timeout` sweep | crash-free rate computable with **starts** as the denominator; a killed process still yields a session via the next launch's `previous_exit` | a launch fixture that kills the renderer |
| 6 | Editor `action_id` + session summary | `Studio.jsx:168/:186/:190`, `web/src/analytics/rollup.js` | every commit carries a `feature_id` from the closed enum; zero per-frame uploads | `rollup.test.js`; `Studio.test.jsx` asserts each toolbar/timeline action emits its expected `feature_id` |
| 7 | Wall dashboard | `docs/analytics-dashboard.md` (query definitions) | all five walls readable with an external-only filter | numerator/denominator fixtures |

**P0 exit condition (codex's, adopted):** one synthetic and one manual packaged journey whose
counts reconcile **exactly** — one install, session, auth, project, turn, tool outcomes, human
edit, render, receipt-backed export, clean close. Then inject one failure per layer and verify
its bounded class.

### P1 — 2 weeks: feature demand and real-media reliability

Full `feature_id` truth table with eligibility (`Studio.jsx`, `StudioTimeline.jsx`,
`StudioInspector.jsx`, `propertySchema.js`) · preview health rollup, never per-frame · ingest
probe + `browser_proxy_finished` + `source_resolution_failed` with fixtures for H.264, HEVC/HDR,
ProRes 4444 alpha, image, audio and a corrupt file · error taxonomy classifiers + the 4xx
middleware + `http_error` · `proxy_cache_miss_reason` + `comp_rerender_triggered` ·
`publish_partial` + `audio_output_health` · `project_stalled` nightly sweep · build/remove
evidence rollups · digests and alerts, each fault-injectable.

### P2 — later

Matched-cohort export lift · contextual surveys · `preview_export_divergence` ·
deterministic sampling · request-to-shipped conversion · install-day time-in-app-vs-rendering.

**Not on any phase:** an A/B experiment framework. Detecting a 5 pp lift on a 40% baseline at 80%
power needs ≈ `16 × 0.4 × 0.6 / 0.05²` ≈ **1,500 installs per arm**. Feature *flags* for staged
rollout are release mechanics and are fine.

---

## 9. Deliberately NOT collecting

### The merged kill list — 31 rows deleted

**From claude's catalog (17):** `#46 asset_name` and `#142 export_thumbnail` (exclusions, not
rows — they inflated the 162 count) · `#12 app_version_upgrade` (subsumed by #11) ·
`#21 auth_disconnected`'s `had_exports` · `#23 capabilities_panel_opened` (modal opens don't
decide pack value) · `#30 project_deleted` (no product action to instrument) ·
`#40 per-folder asset_browse` (30/session) · `#44 asset_filename_shape` (name length doesn't
justify sanitizer changes) · `#53/#54` drain/resume-note events (→ #129) ·
`#83/#84 feature_first/second_use` as separate rows (→ #66 + derived) ·
`#85 selection_changed`, `#110 keyboard_shortcut_used`, `#121 frame_endpoint_latency` (claude's
own §12 admitted these) · `#120 render_preview_watched` · `#135 render_concurrency_blocked` ·
**`#138 export_opened_externally`** — `shell.openPath` is never called anywhere in the repo
(verified; only `shell.openExternal` for http URLs at `main.js:600`/`:607`).

**From codex's catalog (14):** `#23`, `#44`, `#104`, `#115`, `#139`, `#153` (derived metrics
presented as events) · `#54 agent_tool_retry` (nothing implements backoff, so `backoff_ms` is
unknowable) · `#69 agent_effectiveness_rollup` (duplicates #39 + #58 + #93) ·
`#76 median_selection_ms`, `#82 zoom`, `#83 track_visibility` (→ #65's counters) ·
`#89 feature_noop` (requires invasive labeling and conflates valid same-ref guards with
confusion) · `#100 preview_frame_cadence` (→ #80's `dropped_frames`) ·
`#105 render_scene_finished` (one event per successful scene) · `#107/#108` assemble
start/finish (→ #84) · `#146 survey_dismissed` (the absence of an answer) ·
`#142 feedback_topic_classified`'s confidence bucket · `#149 lifecycle_state_changed` (a
five-state machine at zero users) · `#154 dormant_project_inventory` as a source family (→ #125).

### What neither of us will collect, and why

| Not collected | Why it looks useful | Why not | Lower-fidelity answer |
|---|---|---|---|
| Video frames, thumbnails, audio, transcripts | debug quality, understand content | transmits the creative work | codec/HDR/duration profile (#31), export shape (#102), loudness buckets (#126) |
| Full prompts, agent text, thinking, custom answers | mine requests | creative content, unbounded cardinality | `input_chars` (#38), tool path (#44), outcome |
| Raw tool args, results, Bash commands | reconstruct what the agent tried | paths, content, secrets | `tool_id` + `family` + `failure_class` (#43), `filter_family` (#54) |
| Filenames, project names, folder paths, hashes | correlate assets | names reveal customers and content | the persisted random `project_id` uuid4, `extension`, `reference_kind` (#35) |
| Full `edit_decisions` snapshots or field values | rebuild every edit | reconstructs the project and its text | `feature_id`, changed-field counts, structural deltas |
| Text overlay content, notes, captions | learn design choices | content; and raw values answer nothing at beta scale | the `editor.text_add` counter in #65, delta bucket, preset id. **No char count** — the row that carried one was folded into #65, and re-adding it would mean declaring a fourth `*_chars` name for a question the counter already answers |
| Raw ffmpeg / provider stderr | diagnose failures | paths appear and cardinality destroys grouping | classified `stage` + `failure_class` + `exit_code_bucket` (#88) |
| **Session replay, screenshots, DOM snapshots** | see confusion directly | **the editor's main pane IS the user's unreleased video** | the debug recorder, sent only by explicit choice (`feedback.py:165`) |
| Every click, hover, pointer move, drag frame, playhead frame, seek request | heatmaps | 80% of full-fidelity volume; answers one question | one completed action + duration/distance (#65), session percentiles (#80) |
| Every render-status poll and progress tick | detailed timeline | measures our implementation, not the user | `_set` transitions + stage timers (#84) |
| Keystrokes, clipboard | detect shortcuts, pasted setup | a keylogger | allowlisted shortcut id + input method |
| API keys, OAuth tokens, email, IP geography | segmentation | secret or identity data with no build decision here | method/outcome/provider family (#17, #19), `has_email` |
| Exact device model, installed-app list | compatibility | fingerprinting without a decision | `os_major`, `arch`, capability flags (#7) |
| Cross-install identity | "link my two Macs" | re-identification vector | `install_id` stays a random per-install uuid; a two-Mac user counts twice, which is the right error direction |
| Model token counts | agent optimization | provider fields drift; invites vanity analysis | SDK `cost_usd`, duration, turns (#39) |
| Generic page views, app-open retention | easy dashboards | inflates engagement without creative value | `meaningful_active_day` (#120) |
| Metrics no one reviews | completeness | an unread number cannot change the product | every taxonomy entry declares its question, decision and owner |

**Honest note on "maximal."** 130 rows. About **32 will be read weekly**; roughly 45 more exist
to be denominators or to diagnose one failure; the remaining ~53 earn their keep only by proving a
*negative* ("nobody uses ⇅ Arrange"), which is exactly what §5's deletion test needs and what a
small catalog can never do. If only the five wall numbers and the five dashboard screens are ever
built, the human loses little decision value and saves most of the work.

---

## 10. Residual disagreements

Three. Each is stated fairly with what would settle it.

**R-1. Tool calls per turn — the largest unmeasured number in the cost model.**
claude assumed 20-150/turn; codex assumed 10-20. Every volume and cost figure in §6 depends on
it, and **neither figure has any evidence**: there is no `projects/` directory in this worktree
and no `.mc/activity.jsonl` anywhere on this machine, so no production distribution exists.
*What settles it:* ship P0 item 4, then read the real `agent_tool_rollup.calls` distribution after
20 turns. Until then §6's agent line is an assumption, not a capacity fact. Both agents flagged
this independently in their "unverified suspicions."

**R-2. Whether the unmet-capability count belongs in the wall numbers.**
claude: it is the only wall number that answers the human's stated ask ("know the right features
to build"). codex: the walls are product health, and mixing a roadmap feed makes a rising number
ambiguous. **Resolved in codex's favour in §2** (five health walls + one labelled roadmap
counter), but claude does not consider it settled on merit — it is a presentation compromise, and
if the human reads only one number, "what should I build next" may deserve the slot.
*What settles it:* the human saying which question he opens the dashboard to answer.

**Note on what is NOT residual.** Items 1-14 are all settled with a single decision. Three of
claude's positions were refuted outright and replaced by codex's (the `export_started` boundary,
twice; `session_id` minting location; `project_id` form), and two of codex's were replaced by
claude's (the export hook layer; editor granularity via `action_digest`). Neither agent's
position survived intact, which is the outcome the phase-2 reviews were for.

**R-3. Deletion-test threshold N.**
codex: 100 eligible external installs / 8 weeks / 2 releases. claude: 30 installs / 60 days.
§5 ships claude's N with codex's structure and escape hatch. Neither is defensible statistically:
at 30 installs a 5% adoption estimate has a ±8 pp confidence interval, so "adoption <5%" is not
reliably distinguishable from "adoption 12%". codex's 100 is sounder but unreachable for roughly a
year at pre-beta scale, which in practice means never deleting anything.
*What settles it:* accept that below ~100 installs deletion is a judgment call informed by
telemetry rather than decided by it — and that "hide for one release" is the real mechanism at
this scale, with deletion deferred.

---

## 11. Open questions for the human

1. **`store_asset(kind='final_render')` bypasses the North Star.** The agent can publish the
   deliverable through `store_asset` (`agent_runner.py:1014`), and `place_asset` routes that kind
   to `publish_final_render` with **`receipt_doc=None`** — `lib/project.py:558-559` deliberately
   refuses "provenance it has not earned." So that path produces a real `final.mp4` that
   **never fires `export_completed`**, and activation could read 0 for an entire agent code path.
   Is that intended? Neither phase-1 doc caught it.
2. **Watermark.** `app_watermark_free` is the North Star's defining property and there is no
   watermark concept in the code. Is one shipping, or is the North Star simply "first export"?
   Related: is `app_watermark_free=true` an honest claim for every canonical export while
   separately refusing to claim that generated or third-party source media carries no embedded
   watermark? (codex's question, sharper than claude's.)
3. **Session boundary.** Electron never really closes. Session ends on `before-quit`, on window
   blur + 15 min idle, or on `pagehide`? This sets the denominator of every per-session rate.
4. **Cost visibility.** #39 measures real per-turn USD, and it is the *user's* money. Surface it
   in-app (builds trust, suppresses usage) or developer-only?
5. **HDR.** Four `hdr_policy` modes and a large surface (`video_compose.py:1266-1347`). Built for
   a real user need or for your own footage? #85 answers it in 60 days — keep it on the delete
   watch list until then?
6. **Deletion appetite.** §5 will likely mark 8-12 features. Are you willing to delete features
   you built, or should the ledger top out at HIDE? (See R-3.)
7. **Debug recorder.** Shipped user feature (`StudioToolbar.jsx:61`) or dev tooling behind a flag?
   #117 assumes the former.
8. **`internal` hygiene.** Everything depends on `internal` being set on every machine you use
   (`analytics.py:51` — env var or `~/.opennolan-internal`). One unmarked machine at 30 installs
   distorts every rate by ~3%.
9. **Beta size.** §6 assumes 100 → 1,000 → 10,000 MAU. If the real beta is 500, some rollups
   could be relaxed to per-interaction. What are you planning for?
10. **Which three editor capabilities are core for the ICP** — and therefore held to ≥50%
    discovery — versus specialist features judged only against their eligible cohort?

---

## 12. Post-convergence addendum

Added **after** both agents signed off. Every claim here was verified by executing it against
the running app and the live PostHog project, not by reading code. Lettered to match the
architecture doc's §9, which holds the diagrams for B, C, D and I.

### 12A. `install_id` is per-`OPENNOLAN_HOME` — dev-only instability · **P1**

`device_id()` (`server/settings.py:80-88`) persists `dev-{uuid4}` in `settings.json` at
`home()`. Packaged, `home()` is Electron's `userData`, so it is **one stable id per Mac** that
survives app updates and a normal reinstall. In dev, `home()` is the **repo root**, so every
worktree and clone mints its own — the developer's machine reads as N separate installs.

Production identity is therefore sound; this is a developer-experience defect. **P1, not P0.**

| Work | Success condition |
|---|---|
| Move the id to a fixed `~/.opennolan/install_id`, outside every worktree | dev and packaged runs on one machine report one `install_id` |
| Add an `OPENNOLAN_INSTALL_ID` env override | 12D's e2e test can pin a known id |

The override is **not optional**: today's per-home behaviour is what lets a test mint a unique
id by pointing `OPENNOLAN_HOME` at a temp dir, and 12D depends on it. Do this before beta —
afterwards, changing the id source splits every user's history. Rename the app or its bundle id
before beta too; `userData` is derived from them, so a later rename resets every user.

### 12C. Three new P0 items

| # | Work | Files | Success condition | Test |
|---|---|---|---|---|
| 0.8 | Load `.env` in Electron main so it can be pointed at a non-production project | `desktop/main.js` (before `:53`) | with `POSTHOG_KEY` set in `.env`, a packaged `desktop_error` reaches the **dev** project | fault-inject a main-process throw; assert destination |
| 0.9 | Log the analytics destination at boot: key prefix, host, `env`, `internal` | `server/analytics.py`, `desktop/main.js` | one line answers "which project am I writing to" | assert the line names the key prefix, never the whole key |
| 0.10 | `orca.yaml` setup seeds `.env` from the main worktree | `orca.yaml` | a new worktree starts with a working `.env`; a fresh clone skips silently | create a worktree, assert `.env` exists and is not committed |

0.9 exists because the fallback to `_DEFAULT_KEY` (`analytics.py:135`) is **silent** — a typo'd
var name writes to production with no error. See architecture §9B for the two-reporter split and
the `.env` two-space comment footgun.

Setup hook, path derived rather than hardcoded:

```bash
MAIN=$(git worktree list --porcelain | head -1 | sed 's/^worktree //')
SEED="$MAIN/.opennolan-dev.env"
[ -f "$SEED" ] && [ ! -f .env ] && cp "$SEED" .env && chmod 600 .env
```

`.opennolan-dev.env` lives in the main checkout and is already gitignored by `.gitignore:50`
(`*.env`, confirmed with `git check-ignore`). The dev project token is write-only, but it must
**not** go in `orca.yaml` — that file is public, and a published dev key invites strangers to
inject junk into the project used to validate instrumentation.

### 12D. Test strategy — the mechanism §8's exit condition assumed

Two harnesses, and the difference is decisive: **pytest hard-disables analytics**
(`analytics.py:44-45`), **Playwright does not** (`scripts/dev smoke` → `npm --prefix desktop run
test:smoke` spawns a real backend with no pytest in `sys.modules`).

| Layer | Runs | Asserts against |
|---|---|---|
| Contract tests | every commit | a **fake sink** — taxonomy, envelope, join keys, counts. Fast, offline |
| ~~Playwright smoke~~ | ~~pre-release~~ | ~~the **dev project**, over the real network~~ — **DEFERRED, see below** |

> ⚠ **CORRECTION (2026-08-06, from building it).** The second row describes a check that does
> not exist and **could not run as written**. Three independent gates in `scripts/dev` stop the
> smoke harness reaching any PostHog project, and all three are deliberate:
>
> 1. the scratch home is seeded `{"analytics_disabled": true}`, and `is_enabled()` honors it;
> 2. `_test_environment` strips every `*_KEY` env var, `POSTHOG_KEY` included, because tests
>    must not depend on ambient credentials;
> 3. the scratch home has no `.env`, so there is no key to resolve.
>
> So smoke sends nothing today — there is no production pollution and never was. But the
> real-network layer this table promises **does not exist**, and P0 item 1's "a batch reaches
> the existing sink once" is proven only against a fake sink.
>
> **Decision: keep it deferred and say so, rather than build a check that cannot assert what
> the row claims.** Reading events back needs a `phx_` personal key with `query:read` that
> nobody has; without it the strongest available automated assertion is "the batch endpoint
> returned 2xx and the boot line named the dev key prefix", which is a wiring check the
> contract tests already cover against a fake sink. Adding a real network call to the
> pre-release gate would also make it fail offline.
>
> **The trap this leaves is closed.** The scratch home now sets
> `OPENNOLAN_ANALYTICS_NO_DEFAULT_KEY=1`, so if anyone later enables analytics there, a
> missing `POSTHOG_KEY` DISABLES analytics instead of silently falling back to the hardcoded
> **production** key. Building the layer later means opening the three gates above
> deliberately, in that order.
>
> **What replaces it in the meantime is a manual, reproducible check**, run against the dev
> project after each change (this is how items 3/4/5 of the decision record were verified on
> real data):
>
> ```bash
> python3 scripts/dev run                     # dev backend reads .local/.env -> dev project
> grep '\[analytics\]' .local/logs/app.log     # must print default_key=False
> curl -sX POST http://127.0.0.1:$PORT/api/telemetry/events \
>   -H 'Content-Type: application/json' -H 'X-ON-Session: manual-check' \
>   -d '{"events":[{"event":"editor_session_summary","properties":{"commits":1}}]}'
> # then read it back with the PostHog MCP / Query API on install_id + timestamp >= t0
> ```

The SDK cannot verify: **`posthog-python` is write-only.** Reading back needs the Query API
(`POST /api/projects/<id>/query/`) with a **personal** key (`phx_…`, scope `query:read`) — a real
secret, unlike the project token.

Correlating a run to its events with no new product code: set `OPENNOLAN_HOME` to a temp dir,
read the freshly-minted `install_id` from `<tmp>/settings.json`, query on it plus
`timestamp >= t0`, and **poll with a timeout** — ingestion is not instant.

Three traps: `OPENNOLAN_INTERNAL=0` does **not** override the `~/.opennolan-internal` sentinel (a
falsy env var falls through to the file check, `analytics.py:59-65`), so smoke events are
`internal: true` — do not filter them out; skip the assertion cleanly when the personal key is
absent, so a fresh clone does not fail; and a live agent turn spends the user's own money, so
stub it outside the pre-release run.

### 12E. A "Dev / internal" dashboard — the only view with data pre-beta

Open question 8 covers one failure (an *unmarked* machine skewing rates). Production hit the
opposite: **every** insight on `OpenNolan — App Monitoring` filters
`coalesce(toString(properties.internal), '') != 'true'`, and 100% of the project's events are
`internal: true`, so the dashboard is correctly empty.

Add to §8 a parallel internal view filtered `= 'true'`. Pre-beta it is the only view with
anything in it. The production dashboard's description also excludes a **stale** `device_id`
(a `dev-…` id, scrubbed here) that no longer matches any live install — harmless while the `internal` filter
catches everything, but it will mislead whoever reads it next.

### 12F. Two verified gaps to carry as risks

Measured on the production project, 180-day window:

| Event | Count | `internal:true` | `env:packaged` |
|---|---:|---:|---:|
| `app_opened` | 75 | 75 | **0** |
| `project_created` | 19 | 19 | **0** |
| `$exception` | 6 | 6 | **0** |
| `app_first_run` | 2 | 2 | **0** |
| `auth_connected` | 1 | 1 | **0** |
| `desktop_error` | **0 — never received** | — | — |

1. **The packaged reporting path has never delivered a single event.** `env: packaged` is zero
   across all history, and `desktop_error` has never arrived at all — it is gated on
   `!app.isPackaged` (`desktop/main.js`). So the "backend won't start" crash path, the worst
   first-run failure a new user can hit, is entirely unproven. **Add to the §8 P0 exit
   condition: one packaged event observed in PostHog before any external user.**
2. **The 5-event baseline has never been observed from a non-internal user.** Every number in
   this document describes an app that only its author has ever run.

### 12G. GeoIP: ON — a deliberate reversal of the SDK default

`posthog-python` 7.37.6 defaults `disable_geoip=True` (verified by inspecting the installed
`Client.__init__` signature), so today **no** `$geoip_*` property is collected. That default was
inherited, not chosen.

**Decision: enable it.** Add `disable_geoip=False` to the `Posthog(...)` constructor
(`server/analytics.py:134`). Country answers when EU concentration makes the deferred compliance
work urgent rather than theoretical, and timezone sets digest and support timing.

Two mechanical notes:

- **All-or-nothing at the client.** Enrichment happens server-side at ingestion, *after*
  `_before_send` has run — so `_before_send` cannot keep country and drop city. Country-only
  would need a PostHog ingestion transformation.
- **It removes an inconsistency rather than creating one.** `desktop/main.js` POSTs raw JSON
  without `$geoip_disable`, so `desktop_error` was always going to be geo-enriched.

Keep the bucketed numerics §3 already specifies (`output_bytes_bucket`, `duration_bucket`): city
plus an exact timestamp plus an exact file size and duration is the fingerprinting combination,
and bucketing is what keeps geo cheap to carry.

### 12I. `cache/` collides with Electron's `Cache/` — a bug, not an analytics decision

macOS APFS is case-**insensitive** by default (case-preserving, which is why folders look
case-sensitive in Finder). Verified on a stock volume: `mkdir CaseTest` then `mkdir casetest`
fails with *File exists*, and both spellings return the same inode.

`cache_dir()` is `home()/cache` (`lib/app_paths.py:85-90`), and in the packaged app `home()` is
Electron's `userData`, which already holds Chromium's `Cache/`. They are **one directory**. So
`route_caches()` would put HuggingFace, torch, u2net, npm, pip and `TMPDIR` inside a folder
Chromium evicts under quota — multi-GB model downloads could vanish mid-session, and
`session.clearCache()` would delete them outright.

**Latent, not yet triggered**: only Chromium's files are in there today, consistent with
`route_caches()` never having run (gated on `is_packaged()`, and 12F shows zero packaged
sessions). Fix now, while nothing has to migrate: rename to `home()/appcache`, one constant.
Full diagram in architecture §9I.

### Decisions still open from this addendum

1. `~/.opennolan/install_id` — confirmed for P1. Fixed path plus `OPENNOLAN_INSTALL_ID`
   override; both, or neither.
2. `cache/` → `appcache/` rename — confirm the new name before it ships, since after a packaged
   release it becomes a migration.
