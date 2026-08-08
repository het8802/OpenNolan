# P0 implementation report

STATUS: BUILT · uncommitted on `analytics-polishing` · 2026-08-06
Implements plan.md §8 P0 items 1-7 plus the §12 addendum items 12A / 12C / 12G / 12I.

Everything below was verified by running it, including one live round-trip into the PostHog
dev project (544720). Nothing is committed — the human commits.

---

## What proves it works

```
python3 scripts/dev test full     # ruff + 843 pytest + 394 vitest + web build   PASSED
python3 scripts/dev smoke         # Playwright against a real backend            PASSED
```

Plus a live end-to-end round-trip against the dev PostHog project, with the dev backend
running on this worktree's ports (21137/21138 — **not** 20905/20906, which belong to a
different worktree):

```bash
curl -X POST http://127.0.0.1:21137/api/telemetry/events \
  -H 'Content-Type: application/json' -H 'X-ON-Session: live-check-0806' \
  -d '{"events":[{"event":"editor_session_summary",
       "properties":{"commits":7,"action_digest":["editor.split","editor.undo","editor.crop"],
                     "prompt_len":412}}]}'
curl -X POST http://127.0.0.1:21137/api/projects \
  -H 'Content-Type: application/json' -H 'X-ON-Session: live-check-0806' \
  -d '{"name":"analytics live check"}'
```

What came back out of PostHog ~30s later:

| event | session_id | commits | action_digest | telemetry_dropped_props |
|---|---|---|---|---|
| `project_created` | `live-check-0806` | — | — | — |
| `editor_session_summary` | `live-check-0806` | 7 | `["editor.split","editor.undo","editor.crop"]` | **1** |
| `app_opened` (boot) | `null` | — | — | — |

Read it as five separate confirmations:

- The header reaches an **ordinary route** (`project_created`) with zero per-route plumbing.
- The batch route works and preserves array/object properties.
- `prompt_len` was **dropped and counted** — the exact defect §12/item 8 exists for.
- A boot event correctly has a null session (no request context), not a fabricated one.
- Envelope + geo, from the same query: `install_id=dev-<32 hex>`, `schema_version=1`,
  unique `event_id`, `env=dev`, `internal=True`, **`$geoip_country_code=US`** — geo was
  collecting nothing at all before 12G.

Electron main was booted for real (`ELECTRON_DEV=1 npx electron .`) and printed:

```
[analytics/main] key=phc_s9P9JiTb… host=https://us.i.posthog.com default_key=true
                 env=dev internal=true session=<8 hex>
```

---

## P0 items

| # | Item | State |
|---|---|---|
| 1 | Taxonomy + `validate_event` + batch endpoint | done |
| 2 | Envelope / join keys | done |
| 3 | Export + render lifecycle, all 4 writers | done |
| 4 | Agent turn + tool correlation | done |
| 5 | Session start/end + fatal crash-free inputs | done |
| 6 | Editor `action_id` + session summary | done |
| 7 | Wall dashboard query definitions | done (`docs/analytics-dashboard.md`) |
| 12A | `install_id` moved + `OPENNOLAN_INSTALL_ID` override | done (both, as required) |
| 12C 0.8 | dotenv in Electron main before the `POSTHOG_KEY` read | done |
| 12C 0.9 | Boot destination log from both reporters | done |
| 12C 0.10 | `orca.yaml` seeds `.env` | done — **and corrected, see below** |
| 12G | GeoIP on | done, confirmed live |
| 12I | `cache/` → `appcache/` | done |

The three §7 contract tests live in `tests/contracts/test_analytics_taxonomy.py` (128
assertions, mostly the parametrized scrub round-trip over every declared property).

---

## Things found by running the plan that reading it would not have shown

**1. The 12C/0.10 seed snippet, as written, does not reach the dev backend.**
`scripts/dev setup` pins `OPENNOLAN_HOME=<worktree>/.local` in `.env.worktree`, so
`app_paths.env_path()` is `<worktree>/.local/.env` — while the plan's snippet copies to
`<worktree>/.env`. Seeding only the root leaves the dev **backend** on the production key
while pytest, a bare `uvicorn`, and the Electron shell are on the dev project. This was
observed directly: before the fix this worktree's dev backend logged
`key=phc_s9P9JiTb… default_key=True`, after it logged `key=phc_tTqiU7Ls… default_key=False`.
The hook now seeds **both** paths and runs *after* `scripts/dev setup` (so `.local/` exists).
0.9's log line is what made this visible at all, which is a fair argument for 0.9 on its own.

**2. Moving `install_id` breaks the Electron reporter unless it moves too.**
`desktop/main.js` read `device_id` out of `settings.json`. Once 12A moves the id to
`~/.opennolan/install_id`, that read silently falls back to `'desktop-unknown'` and one Mac
becomes two installs. `main.js` now reads the same file and honors the same
`OPENNOLAN_INSTALL_ID` override.

**3. `before-quit` deferral vs. the auto-updater.** The awaited flush needs
`event.preventDefault()`, which would also intercept the quit that
`autoUpdater.quitAndInstall()` issues — an untestable regression on a path that only runs
against a signed build. The flush is skipped (event still sent, quit not deferred) when the
user chose "Restart & update", and `exit_kind` records `update`.

**4. Two `pagehide` handlers, and order matters.** `track.js` registers its flush at app
start, so it runs *before* Studio's summary handler and would drain an empty queue. Studio
flushes explicitly after enqueuing; if it didn't, the editor summary would die with the
document — silently, and only in the real browser.

**5. `app_opened` fires twice when `server.app` is imported and `create_app()` is then
called again** (module-level `app = create_app()` at the bottom of the file). Pre-existing,
not introduced here, and harmless under uvicorn — noted so nobody chases it.

---

## Deliberately built smaller than the plan specifies

Each of these satisfies the stated success condition; none is a silent cut.

- **Taxonomy scope: 24 events, not 97.** `schemas/analytics_events.json` declares exactly
  what has a live emitter today. Contract test 1b fails on any declared event with no call
  site, so the file cannot quietly grow aspirational rows. The remaining catalog is P1/P2.
- **No `wrong_type` / `high_cardinality` validation.** `validate_event` implements unknown
  event → drop, unknown property → drop + count. The reserved-substring rule is enforced by
  *contract test 3* rather than at runtime: a reserved name can never be declared, so it can
  only ever arrive as an unknown property, which is already dropped and counted. A runtime
  re-check would be dead code.
- **Telemetry counters are in-memory, not durable.** They ride out on the next event that
  sends. A restart-spanning counter needs a file under `home()`; add it when a restart-
  spanning number is actually wanted.
- **`ordinal` / `first_export` are NOT emitted on `export_completed`.** The only counter
  available at that hook is the in-memory job dict, which empties on every backend restart —
  it would report `first_export=true` repeatedly and inflate activation and time-to-value.
  Both are exact at query time from `min(timestamp)` per `install_id`, which is how walls #1
  and #2 compute them anyway. This is a deliberate deviation from row 93.
- **`feature_id` is a hand-written frozen list**, not derived at build time from
  `PROPERTY_TITLES` + drag modes. The derivation only pays for itself alongside the
  eligibility table, which is explicitly P1.
- **`features_eligible` is not computed**, so `feature_adoption_rate` is not yet computable.
  Same reason: it is the other half of the P1 eligibility work.
- **Drags count as commits only where the call site knows its feature** (trim, overlay
  timeline, audio, canvas position). `onScrubBegin` is passed straight to the inspector as a
  prop and cannot name its field without the propertySchema plumbing — P1.
- **Per-tool percentiles use nearest-rank**, not interpolation. Exact for the handful of
  calls a turn makes, and it needs no dependency.
- **`dotenv` added as a real `desktop` dependency.** It was already physically present but
  only as an `electron-builder` transitive dev dep, so it would **not** have been bundled into
  the packaged app — which is precisely where 0.8 has to work. Declared and lock updated.

---

## Not built (out of scope, flagged)

- Everything in plan §8 P1/P2 and the §9 kill list, as instructed.
- `agent_interrupted` (row 42). `asyncio.CancelledError` is a `BaseException`, so a Stop does
  not reach the `except`; the `finally` still emits `agent_turn_completed` with
  `stop_reason=None`. Consistent, but a Stop is currently indistinguishable from a quiet
  finish. Row 42 is where that gets fixed.
- **`desktop_error` is still gated on `app.isPackaged`.** §12F records that it has never
  arrived once. I left the gate alone — it is a deliberate prior choice and outside this
  scope — but the new shell product events (`app_launch_started`, `session_started`,
  `backend_ready`, `session_ended`, `process_gone`, `launch_failure`) are **not** gated, so
  the direct-to-PostHog transport itself now gets exercised in dev. That covers the transport
  risk; it does not cover the packaged path. **§12F's exit condition — one `env: packaged`
  event observed in PostHog — still stands and still needs a packaged build.**
- Nothing was committed. `.opennolan-dev.env` / `.env` / `.local/.env` are gitignored and the
  diff contains no key other than the production token that was already in `main.js`.

---

## Contract notes for reviewers

- `RenderJobStore.start*` gained keyword-only `session_id` / `turn_id`. Three test fakes were
  updated to match.
- `AgentRunner.run_turn` gained a keyword-only `session_id`. Three test fakes updated.
- `Studio.commit(next)` → `commit(next, featureId)`; `snapshot()` → `snapshot(featureId?)`.
  `pastActions` / `futureActions` refs shadow the `past` / `future` doc stacks and must be
  pushed and popped together — there is a comment at the declaration saying so.
- `cache_dir()` renamed `cache` → `appcache`. Four readers checked; only `app_paths` itself,
  `agent_runner` (via the function, no literal) and two test files referenced it.
- `tests/conftest.py` is new: it pins `OPENNOLAN_INSTALL_ID` for every test so nothing can
  write into the developer's real `~/.opennolan/`.

---

# Round 2 — QA + code review

STATUS: BUILT · still uncommitted · 2026-08-06

## Item 1 — the smoke delivery layer: **option (b), deferred and corrected in the doc**

QA was right that the smoke home resolves the production key prefix, and wrong about the
consequence in both directions. `scripts/dev` writes `{"analytics_disabled": true}` into the
scratch home and `_test_environment` strips every `*_KEY` variable (`POSTHOG_KEY` included),
so smoke has never sent a single event — there was no pollution and never has been. But plan
§12D described that layer as the pre-release check that "asserts against the dev project, over
the real network", and it could not run as written.

**Why (b) and not (a):** the doc has to be corrected either way, because (a) cannot assert
what §12D claims. Reading events back needs a `phx_` personal key with `query:read` that
nobody has, so the strongest automated assertion available is "the batch endpoint returned 2xx
and the boot line named the dev prefix" — a wiring check the contract tests already make
against a fake sink. Building it would mean opening three deliberate gates (the opt-out, the
credential scrubber, the missing key) and putting a network call into the pre-release gate,
which then fails offline. So §12D now marks the layer **deferred**, lists the three gates in
the order they would have to be opened, and records the manual command sequence that replaces
it — the one actually used to verify every claim in this report.

**The trap is closed independently.** `OPENNOLAN_ANALYTICS_NO_DEFAULT_KEY=1` is now set in the
scratch environment: a missing `POSTHOG_KEY` there DISABLES analytics instead of silently
falling back to the hardcoded production token. Covered by
`test_no_default_key_guard_refuses_the_production_fallback`.

## Item 2 — the editor summary: **fixed, and the cause was the hook point, not the flush**

Closing the editor sets `editing=false` in `App.jsx`, which **unmounts Studio while the
document stays loaded**. `pagehide` never fires on that path, so the one event carrying every
`feature_id` only existed for a full page teardown — which is why QA saw zero requests and why
it appeared exactly once, ever, in the whole project history.

It now flushes on unmount as well, once-guarded so a teardown followed by an unmount still
sends exactly one. Reproduced headlessly with QA's own scenario (real browser, real edit,
normal editor close): **0 telemetry POSTs before the exit, 1 after.**

Verifiable window — dev project 544720:

| event | timestamp | session_id | project_id | commits | features_used |
|---|---|---|---|---|---|
| `editor_session_summary` | `2026-08-06T09:06:53Z` | `28d8ff6e-…` | `69c73e85…` | 1 | `["editor.duplicate"]` |

`project_id` there is the random persisted id, **not** the project slug (`editor-exit-check`).

## Item 3 — `app_opened`: **made joinable**

Electron now sets `OPENNOLAN_SESSION_ID` on the backend it spawns, and `current_session_id()`
falls back from the request ContextVar to that process-owner session. Boot-time events have no
request to inherit from, so this is the only thing that can attribute them.

Verified live across two launches, including a SIGKILL between them:

| time | event | session_id | previous_exit | prior_session_id |
|---|---|---|---|---|
| 09:06:12 | `app_opened` | **null** | — | — |
| 09:12:40 | `session_started` `entry=dashboard` | `455d9ef6-…` | — | — |
| 09:12:40 | `app_launch_started` | `455d9ef6-…` | `clean` | — |
| 09:12:41 | **`app_opened`** | **`455d9ef6-…`** | — | — |
| 09:12:59 | `app_launch_started` | `40af6a57-…` | **`crash`** | **`455d9ef6-…`** |
| 09:12:59 | **`app_opened`** | **`40af6a57-…`** | — | — |

The first row is the deliberate exception and it is correct: that backend was started by
`scripts/dev run`, not by any session, so it stays null rather than being attributed to a
session that did not start it. `app_opened` still carries no `project_id` — none exists at
boot. Both facts are now recorded in `docs/analytics-dashboard.md`, along with the warning
that `app_opened` counts backend boots and `session_started` is the session denominator.

## Review findings accepted (24)

Correctness: crashed turns reported `is_error=false` (the `finally` exists for exactly that
path) · orphaned tool uses never emitted `agent_tool_failed{no_result}` · undo→redo→undo
counted two undos for one `action_id`, and the old test had codified the defect the plan
named · `snapshot()` recorded a commit at pointerdown, so a click without a drag inflated
adoption · unknown feature ids were silently relabelled `clip_transform` (now `noop_commits`).

Identity/privacy: `project_id` was the user-typed slug on every wire event · Electron sent
`desktop-unknown` on a true first launch, splitting one launch across two installs ·
`settings.device_id` wrote non-atomically · `project_created.style` leaked user-created
playbook names · the taxonomy failed **open** · nested free-text keys bypassed `_scrub` ·
short-key logging printed the whole value · real install/session prefixes in the docs.

Contract/plumbing: shell events lacked `schema_version`/`event_id` and resolved delivery true
on any HTTP status · the launch marker never persisted a session id or classified a crash ·
no `fatal` flag anywhere, so wall #5's predicate could never match · `publish_intent` attached
to three events that did not declare it · `entry='dev'` outside the closed enum · `track.js`
requeue exceeded the cap, swallowed a refused `sendBeacon` and never re-armed its timer ·
contract 1b counted comment-only literals · `.last-exit.json` and `.analytics_id` unignored ·
the editor summary omitted `project_id` and suppressed untouched sessions · no integrated
journey test and no `_TurnTools` tests.

## Review findings refuted (6)

1. **Raw error message/stack should become fingerprints only.** Pre-existing and *ratified* —
   plan row 105: "`_before_send` redacts. Keep it; never bypass." Replacing the crash inbox
   with classifications is a redesign that deletes the diagnostics the app relies on. What the
   finding was really reaching for — a signal wall #5 can query — is now the `fatal` flag.
2. **`features_eligible` missing.** Explicitly P1 in the plan ("Full `feature_id` truth table
   with eligibility"). The other half of that finding *was* accepted: an opened-but-untouched
   editor now emits its summary as the zero-use denominator.
3. **Durable telemetry counters.** Documented as a deliberate simplification. The sharper
   version of the point — they clear on SDK *enqueue*, not on delivery — is correct and is now
   a caveat in the dashboard doc rather than a fix.
4. **Taxonomy priority/sampling/owner/forbidden metadata.** Enforces nothing today; `question`
   is already per-event. Governance prose, not a gate.
5. **Electron direct-transport tests.** `main.js` has no test harness in this repo at all;
   adding one is new infrastructure. That path was verified by booting the real shell twice and
   reading the events back out of PostHog (the table in item 3).
6. **The shell bypasses the validator.** By design and documented: main must be able to report
   when the backend is dead or never started. The envelope gap inside that bypass was real and
   is fixed.

## Still not done, for the record

§12F's exit condition — one `env: packaged` event observed in PostHog — needs a real packaged
build and has not been met. `desktop_error` has never been received in the project's entire
history; it remains gated on `app.isPackaged`, which is a deliberate prior choice I left alone.
The new shell product events are not gated, so the direct-to-PostHog transport itself is now
exercised (and confirmed) in dev — but the packaged path is still unproven.

---

# Round 3 — the closing four

STATUS: BUILT · still uncommitted · 2026-08-06 · `test full` + `smoke` green with the app stopped

## 1. Enum values were not enforced — **fixed**

A property the taxonomy typed `E` accepted any string, so a field that *looked* constrained was
an unlabelled free-text field. Same class as the `project_id` slug, and the one that matters
most, because "free text cannot reach the wire" is the entire safety argument here.

Two tiers, because not every `E` has a knowable closed set:

- **Declared vocabulary → membership required.** `schemas/analytics_events.json` now has an
  `enums` block: 21 closed lists, keyed `<event>.<property>` (wins) or bare `<property>`
  (shared). A value outside its list is **dropped and counted**, exactly like an unknown
  property.
- **No declared vocabulary → bounded token required.** `model` comes from the SDK, `style` from
  the user's playbook directory, `os` from the platform, `tool_id` from the tool registry.
  Inventing lists for those would silently drop real data, so they instead must match
  `[A-Za-z0-9_.:/+-]{1,64}` — no whitespace, which is what makes prose fail.

Only vocabularies **defined by our own code** are declared. Before enabling it I swept every
value the code actually emits for a gated field against the new gate: **none would be dropped.**
One real emit site had to change — Electron's `child-process-gone` type is display-cased and
can contain spaces (`Sandbox helper`), so `processName()` normalizes it rather than letting the
validator eat a legitimate value.

Proof: `test_4_a_declared_enum_rejects_an_undeclared_value` feeds `render_finished.status` a
sentence and asserts it does not survive while the rest of the event does, with
`telemetry_dropped_props` incremented. `test_4_an_undeclared_enum_still_cannot_carry_prose`
covers the shape tier. `test_4_every_declared_enum_belongs_to_a_declared_property` stops a
renamed property leaving its vocabulary behind and the new one ungated.

## 2. undo → redo → undo still yielded 200% — **fixed, and I had it wrong**

My round-2 fix added an `undone` set, and then `recordRedo` **deleted from it** — with a
comment explaining why. That reintroduced the exact double count, and the test asserted
`undos: 2`, so the suite went green over the defect. The reviewer was right and my reasoning
was wrong: `undos` answers "how many distinct actions did the user take back", which is bounded
by `commits` by construction. `redos` and the ordered digest are where the traversal survives.

Proof: the test now asserts `undos == 1` for commit → undo → redo → undo, plus
`undos <= commits` as an explicit invariant, plus a 20-traversal loop that still yields 1.

## 3. Docstrings and block comments satisfied contract 1b — **fixed**

Python is now parsed with `ast` and matched on the **call expression** — a name inside a
docstring, a `def`, or a commented-out line is not a call site by construction. JS has no
parser available here, so line *and* `/* block */* comments are stripped before matching.

While writing the stripper I applied `re.S` globally, which let `//.*$` span newlines: one line
comment swallowed 46,210 characters down to 15 and **every** JS emitter vanished. 1b failing
loudly is what caught it — which is the argument for this item in miniature. DOTALL is now
scoped to the block-comment branch, and `test_1c_stripping_comments_does_not_eat_the_file`
pins it.

Proof: three tests pin 1b's own failure mode — a docstring mention, a block-comment mention and
a trailing-comment mention each yield only the genuinely emitted name.

## 4. Wall 5's fatal leg was never exercised — **fixed, and observable in dev 544720**

Correct on both counts: the flag existed in code and had never been emitted, so the property did
not exist in the project's taxonomy and the query matched nothing.

`ErrorBoundary` moved out of `main.jsx` into its own module — `main.jsx` calls `createRoot()` at
import, so the crash path could not be reached from a test at all. Then a **genuine** fault
injection: a temporary `throw` in `App.jsx`'s render, loaded in a real headless browser,
reverted immediately (`App.jsx` is not in the changed-file list).

What arrived in **dev 544720**:

| timestamp | event | source | fatal | handled | session_id |
|---|---|---|---|---|---|
| `2026-08-06T09:44:47.412Z` | `$exception` | `react-boundary` | **`true`** | `true` | `4f3044c6-…` |
| `2026-08-06T09:44:47.410Z` | `$exception` | `window.onerror` | `false` | `false` | `4f3044c6-…` |

The second row is the more interesting one: the *same* crash also surfaces as an uncaught
window error, and it is correctly excluded from the numerator. The flag discriminates, it does
not merely exist. Wall 5's predicate run verbatim —
`count(distinct session_id) where $exception and fatal = true` — returns **1**, where it
previously could not match at all.

Permanent coverage: `web/src/ErrorBoundary.test.jsx` throws a real render error and asserts the
full payload including `fatal: true`;
`test_client_error_carries_the_fatal_flag_all_the_way_to_the_sink` proves the flag survives
`_scrub` and reaches the sink as a real boolean, and that a non-fatal error stays `false`.

## Also confirmed: the guard covers BOTH reporters

`OPENNOLAN_ANALYTICS_NO_DEFAULT_KEY` was Python-only. `desktop/main.js` now honors it too, and
that is the reporter it matters most for — main.js is what writes when the backend never starts,
which is the entire reason it exists. With the flag set and no explicit key, `postToPostHog`
returns before building a request and the boot line says `DISABLED (no explicit key; production
fallback refused)`.

---

## Still open — the honest residual list

1. **§12F is still unmet.** No `env: packaged` event has ever been received, and
   `desktop_error` has never arrived in the project's entire history. It remains gated on
   `app.isPackaged` — a deliberate prior choice I left alone. Everything the shell reports is
   now proven in dev over the real transport, but the packaged path needs a real signed build.
2. **`features_eligible` is not computed**, so `feature_adoption_rate` and
   `feature_discovery_rate` are still not computable. Ratified P1.
3. **The real-network delivery layer is deferred**, per the correction in plan §12D. Automated
   delivery coverage is fake-sink only; the manual sequence in that section is what actually
   verified every live claim in this report.
4. **Telemetry health counters clear on SDK enqueue, not on delivery.** An async rejection after
   enqueue erases the signal. Documented in the dashboard caveats, not fixed.
5. **`previous_exit` cannot distinguish `crash` from `kill`.** A SIGKILL and a segfault leave an
   identical marker, so `crash` is reported as the honest superset. The taxonomy still declares
   `kill` as a legal value; nothing emits it.
6. **Enum vocabularies are declared only where our own code defines them.** `os`, `app_version`,
   `model`, `style`, `pipeline_type`, `stop_reason`, `final_review_status`,
   `process_gone.reason`, `tool_id` and `entrypoint` are shape-bounded, not membership-bounded.
   If any of those ever needs a true closed set, it needs a source of truth first.
7. **`agent_turn_started.entrypoint` is declared and never emitted.** A declared *property* with
   no emitter is not caught by 1b, which only covers events.

---

# Round 4 — the last two

STATUS: BUILT · still uncommitted · 2026-08-06 · `test full` + `smoke` green with the app stopped

## 1. The enum fallback leaked private text — **fixed, and the leak was LIVE**

I nearly filed this as already-handled: round 2 collapsed a user style to `"user"` at the emit
site. It never fired. `list_playbooks(packaged=False)` appends `user_styles/` **regardless of
that flag** — the flag only trims the *shipped* catalogue — so the check
`st in list_playbooks(packaged=False)` classified every user style as built-in and sent the name
verbatim. Executed against the running app before touching anything:

```
list_playbooks includes the user style: True
builtin-only list includes it        : True     ← the bug
EMITTED style = 'q4-launch-teaser'              ← somebody's unreleased campaign, on the wire
```

Two independent layers now stop it.

**Emit site.** New `playbook_loader.builtin_playbooks()` reads the shipped `styles/*.yaml` only.
A user style collapses to the bounded classification `"user"`; a built-in keeps its name, which
is ours and is the whole point of the row ("which styles get chosen → prune `styles/`").

**Validator — the door is now closed by default.** `_enum_ok` used to fall back to a bounded
token for any undeclared vocabulary. That fallback is what admitted the slug, and no regex can
ever fix it: `q4-launch-teaser` is character-for-character the same shape as
`instagram-fast-reel`. A type-`E` value is now **dropped unless** it is in a declared enum, or
its property is named in a new `open_vocabularies` block with a written reason it cannot be
user-authored.

Every gated field the reviewer named, checked on the same test:

| field | verdict |
|---|---|
| `style` | **was leaking.** Now enum = shipped stems + `user` |
| `pipeline_type` | `pipeline_defs/` only, **not** user-extensible → declared as an enum |
| `model` | `AGENT_MODELS`, a literal dict in our code → declared as an enum |
| `stop_reason` | SDK-supplied, no user input path → open, justified |
| `tool_id` | SDK + our own `mcp__mc__` prefix → open, justified. **Residual:** if a user-registered MCP server ever ships, its tool names become externally authored and this needs revisiting |
| `os` | agreed with you — OS-supplied, no user input path → open, justified |

Buckets (`B`) get their own rule rather than the token fallback: `_bucket()` emits arithmetic
(`"10-50"`, `"500+"`), so they must match a numeric-label pattern. Digits, a dash and a plus
cannot carry a name.

**Live proof, dev 544720** — a style planted as `q4-launch-teaser`, project created through the
running app:

| event | timestamp | style | pipeline_type | telemetry_dropped_props |
|---|---|---|---|---|
| `project_created` | `2026-08-06T10:02:27.561Z` | **`user`** | `instagram-fast-reel` | `null` |

`dropped_props` is null, which is the interesting part: the emit site collapsed it correctly, so
the validator never had to catch it — the layers agree rather than one masking the other. A
sweep of every event payload in the window for `%q4-launch%` returns **0**.

Three tests hold it: the user-style regression across both layers;
`test_5_every_enum_property_is_declared_or_justified`, so a NEW field cannot silently inherit the
permissive path; and a sync test that fails loudly when someone adds a style, pipeline or model
without updating the vocabulary — a stale list silently drops real data, which is how people end
up disabling validation.

## 2. The JS stripper over-deleted and blinded 1a — **replaced**

Strip-then-match is the wrong tool, as you said. Replaced with `js_code_only()`: a single
offset-preserving scan that knows what a string, a line comment, a block comment and a regex
literal are, blanking each to spaces so the call regex can then be matched against the original
source at positions the scan proved are code.

**The first three cases I wrote were worthless — the old regex passed all of them.** So the
broken stripper is now kept in the test file as the adversary, and every case asserts it
*loses* the emitter before asserting the scanner keeps it. A regression test that also passes
against the broken implementation guards nothing:

| case | old | new |
|---|---|---|
| regex literal containing `//` — `/[ //]/` | **LOST** | kept |
| URL inside a string — `'see https://x.io // docs'` | **LOST** | kept |
| `/*` and `*/` held in two different strings, emitter between | **LOST** | kept |

And the 1b hole stays shut: a commented-out or block-commented emitter is still rejected.

**1a's failure mode is now loud.** It was the silent one — a scanner that finds nothing gives 1a
nothing to complain about. `test_1c_1a_cannot_go_blind` puts a floor under it: the scan must
find ≥15 emitters, must find a Python-only name and a JS-only name, must find four specific
events in `desktop/main.js`, and must show the scanner is not simply passing everything through
(offsets preserved, comments measurably blanked).

Where the scanner is deliberately imprecise: a template literal's `${...}` interpolation is
treated as part of the string. That direction only ever *hides* an emitter, which makes 1b fail
loudly — never admits a commented-out one, which is what fails silently.

---

## Residual list after round 4

Unchanged from round 3 except where noted:

1. **§12F still unmet** — no `env: packaged` event has ever been received and `desktop_error`
   has never arrived in the project's history. Needs a real signed build.
2. **`features_eligible` not computed** — `feature_adoption_rate` / `feature_discovery_rate`
   remain uncomputable. Ratified P1.
3. **Real-network delivery layer deferred** per the §12D correction; automated delivery coverage
   is fake-sink only.
4. **Telemetry counters clear on SDK enqueue, not delivery.**
5. **`previous_exit` cannot separate `crash` from `kill`** — reported as the honest superset.
6. **`agent_turn_started.entrypoint`** declared and never emitted. You ruled it P1; it is now
   listed in `open_vocabularies` purely so the deny-by-default sweep is exhaustive.
7. **NEW — `tool_id` is open by justification, not by vocabulary.** Safe today because the only
   MCP server is ours. If user-registered MCP servers ever ship, tool names become externally
   authored and must be hashed or classified.
8. **NEW — declared vocabularies are static lists in the schema.** The sync test makes drift
   loud rather than silent, but adding a style/pipeline/model does require a taxonomy edit.
