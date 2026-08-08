# Full analytics event coverage, agent-readable PostHog, and a taxonomy-driven E2E

**STATUS: APPROVED · rev 7 · claude**
**Reviewer: gpt-5.6-terra (high), 7 rounds, opposite-provider. VERDICT: APPROVE.**
**rev 1→2 REJECT (9) · 2→3 REJECT (6) · 3→4 REJECT (5) · 4→5 REJECT (3) · 5→6 REJECT (2).
**6→7 REJECT (3, no blocker).** 28 findings verified against code, **all 28 accepted** —
my one attempted pushback was itself refuted, correctly. Twelve were my errors. §6.**

Every `file:line` was grepped fresh against the working tree. The tree holds the
uncommitted analytics build (~2,100 insertions, 25 files) that closed
`docs/plans/analytics-maximal-datapoints/agreed/`.

Companion: [`architecture.md`](architecture.md) — written only after APPROVE.

---

## 1. What is actually broken

### D1 — 49 P0 emitted events do not exist

Derived, not asserted. Splitting the catalog rows that carry two event names and
diffing against `schemas/analytics_events.json`:

```
P0 emitted names (combined rows split)   70
   of those already declared             21
   UNDECLARED                            49
```

rev 1 said 50. That was wrong: the parser turned the row carrying
`render_queued` + `render_started` into one phantom name. **The build must generate a
name-level BUILD / FOLD / DROP manifest and derive every count from its set
difference** — no hand-carried totals, in this plan or any successor.

Some of the 49 are renames or property-folds rather than absences (`render_failed` →
`render_finished{status}`; `preview_health` → the rollup the catalog itself specifies).
The manifest classifies each.

Whole families are absent: auth (5), asset ingest (6), editor lifecycle (6), and the
failure surface — `http_error`, `user_visible_failure`, `publish_partial`,
`data_quality_violation`. Those describe users who never file a bug report, which is
the stated reason this instrumentation exists.

### D2 — the ratified plan contradicts itself about P0

`agreed/plan.md` §3 marks 88 rows P0; §8 defines *"P0 — 3 days"* as seven work items.
Both say `P0`. The implementer built §8, correctly. Both reviewer briefs
(coordinator-authored) scoped review to §8 and said "P1/P2 absence is not a finding" —
so 49 P0-marked rows went unbuilt with no objection. The defect is in the source
document and in my briefs.

### D3 — catalog anchors predate the implementation and have drifted

**Independently verified by the reviewer.** Sampled 5:

| Catalog says | Actually there now | Real location |
|---|---|---|
| `app.py:1029` `provision.doctor()` | `def auth_disconnect()` | **`:1088`** |
| `app.py:961` `auth_mod.start_oauth()` | an `HTTPException` | **`:1006`** |
| `agent_runner.py:2021` `client.interrupt()` | `status = job.get(...)` | **`:2369`** |
| `Studio.jsx:320` `dbg.event('agent.adopt')` | `setDirty(false)` | **`:413`** |
| `agent_runner.py:402` Bash branch | correct | `:402` |

4 of 5 wrong, drift to +348 lines. §3's "7 banned hook classes" makes a misplaced call
a correctness bug, not a cosmetic one.

### D4 — no agent can verify that an event reached PostHog

`posthog-python` is write-only. Reading needs `POST /api/projects/544720/query/` with a
personal key (`phx_…`, `query:read`) on `us.posthog.com` — a different host from the
ingest host `us.i.posthog.com`, and an account-level secret. Verification currently
routes through the coordinator's MCP session: serialised on a supervised human,
impossible in CI, and it already produced one false report (QA graded 6 of 7 P0 rows
NOT TESTED because its `ask` disconnected).

### D5 — `scripts/dev smoke` cannot exercise analytics

`is_enabled()` (`server/analytics.py:293`) gates: `_under_pytest()` `:295`;
`analytics_disabled` `:297`; no-explicit-key + `NO_DEFAULT_KEY` `:299`; key present
`:303`. Smoke fails two deliberately — `scripts/dev:680` writes
`{"analytics_disabled": true}` and `:674` sets `OPENNOLAN_ANALYTICS_NO_DEFAULT_KEY=1`.
So §12D's "Playwright smoke asserts against the dev project" is unimplementable as
written, which the implementer correctly marked DEFERRED.

### D6 — nothing verifies property or enum conformance

Contract test 1b proves declared events have live **emitters in code** — static. It
says nothing about what a delivered event carries. Measured: `entry` declares
`{dashboard, editor, setup}`, only `dashboard` is emitted (`desktop/main.js:842`, sole
site); `launch_kind` declares `activate`, never emitted;
`agent_turn_started.entrypoint` declared, never emitted.

### D7 — one `session_started` emitter, so non-Electron contexts are unregistered

Sole emitter: `desktop/main.js:842`. `web/src/analytics/track.js:25`
`resolveSessionId()` mints a fallback at `:32-33` into `sessionStorage` when
`window.openNolan?.sessionId` is absent; `:41` exports it and every event carries it.
So `npm run dev` / Playwright / QA invent an id, label every event with it, and never
register the session.

Measured in dev 544720: **6 session ids carry product events with no `session_started`**,
including `4f3044c6`, which carries the project's only `fatal=true` `$exception`. Wall
5's fatal numerator reads **0 against a real fatal crash**.

Survived three reviewers because all three tested `$exception where fatal = true` in
isolation, where it returns 1. Nobody ran the wall against its own denominator.

### D8 — the taxonomy fails CLOSED, and a partial merge would be silent

**rev 1 had this backwards and it was the more dangerous half of the plan.**

`validate_event` (`server/analytics.py:141-147`) fails **closed**: an unloadable
taxonomy drops *every* event, by design — *"the taxonomy is the gate that stops a
free-text key reaching the wire; if it cannot load, the security contract cannot be
honored."* `tests/contracts/test_analytics.py:59` asserts it.

Three readers: `server/analytics.py:70`, `desktop/main.js:96`,
`tests/contracts/test_analytics_taxonomy.py:29`. The reviewer confirmed these are
**exactly** the runtime readers, and that `skills/pipelines/**` carries no telemetry
contract.

At ~70 events one file is unreviewable in a diff, hence `schemas/analytics/`. The split
introduces a failure mode the single file lacks: a **partial merge**, where valid
events silently become undeclared and are dropped. Fail-closed makes a total merge
failure a loud, total analytics outage — recoverable and obvious. A partial merge is
neither.

### D9 — Electron and Python can mint different install ids for one launch

Both use exclusive create, then **fall back to their own candidate if the file reads
empty**:

```python
except FileExistsError:                       # server/settings.py:124-126
    return path.read_text().strip() or did    #                 ← `or did`
```
```js
return (fs.readFileSync(file,'utf8').trim() || minted);   // desktop/main.js:159
```

`open(path,"x")` creates the inode *before* `fh.write` runs. A process that loses the
race inside that window reads an empty file and returns its own id. Electron spawns the
backend, so both booting together is the normal case — not an edge case.

`install_id` is the PostHog `distinct_id` **and** the join key every readback query in
this plan depends on. This is required, not optional.

### D10 — there is no session-scoped upload cap

The agreed plan's ceiling is ≤40 uploads/session, hard cap 100. The only 100 in the
code is per POST body / queue (`web/src/analytics/track.js`, `server/app.py:1075-1080`)
— **not per session**. Across 5-second flushes a session can upload arbitrarily many
events.

The reviewer's counterexample, built only from proposed P0 rows: 8 asset imports plus
probes, 10 asset adds, 4 project opens, 6 preview switches, normal start events and 2
agent turns — over 40 before a single render. rev 1's R3 claimed families "default to
rollups" as mitigation; that is an intention, not an enforcement.

### D11 — the loader's own docstring contradicts the gate, and the boot line it promises does not exist

Two live inconsistencies in the working tree, both found while fixing rev 1:

- `server/analytics.py:63-64` says a missing/corrupt taxonomy *"disables validation rather
  than silently dropping every event."* `validate_event` at `:141-147` does the exact
  opposite — FAIL CLOSED, nothing may be sent. **This stale docstring is what produced my
  rev-1 error**, and it will mislead the next maintainer identically.
- `:145` claims *"the boot line says so out loud."* It does not. `log_destination(key, host)`
  (`:380`) receives only key and host and prints only those. A total taxonomy failure is
  therefore **silent**: fail-closed means no event can ever ride the `unknown_events` counter
  out, and nothing else reports it.

So today, a corrupt taxonomy stops all analytics with no observable signal anywhere.

---

## 2. The fix

| # | Intent | Named change |
|---|---|---|
| D1 | Counts derive from data, not prose | generated BUILD/FOLD/DROP manifest; every total is a set difference |
| D2 | The ratified doc stops contradicting itself | reconcile §3 `Pri` with §8 phasing, with a note recording it |
| D3 | No capture call lands in the wrong function | re-derive every anchor by symbol grep; manifest records symbol + line + viability |
| D4 | An agent verifies its own events without a human | `scripts/analytics_query.py` — Keychain key, HogQL over the query API |
| D5 | The pre-release harness can assert delivery | explicit analytics-ON smoke mode |
| D6 | A declared property or enum variant that never arrives fails a test | **two** tests: payload conformance + expected-variant matrix |
| D7 | Every context that can crash is on the register | announce on mint, with a **pending marker cleared only on delivery** |
| D8 | Reviewable taxonomy, no new silent failure | `schemas/analytics/*.json` + **all-or-nothing** merge, preserving fail-closed |
| D9 | One launch, one install id | atomic **hard-link** publish (temp → fsync → `link`); **no retry, no `rename`** |
| D10 | The ceiling is enforced, not hoped for | **per-source** budgets with a bounded critical reserve |
| D11 | The code stops lying about its own failure mode | fix the docstring; add a one-time taxonomy-load failure log the boot line actually emits |

---

## 3. Deliberately not building

| Not building | Why | What would change my mind |
|---|---|---|
| P1 (34 rows) and P2 (5 rows) | Out of the agreed phasing | A wall the human wants now depends on one |
| **Paid** agent turns to prove the 12 `3e` events | Each spends the human's money | The human budgets a fixed number |
| Reading back from production (478214) | The E2E must never touch production | Never — production readback is a human's job |
| PostHog MCP for the agent CLIs | Needs credentials anyway, per-CLI, may be absent headless | It proves out as a convenience once Path A works |
| A runtime enum re-check inside `capture()` | The deny-by-default gate already drops undeclared values | A value reaches the wire the gate should have dropped |
| Migrating the old `settings.json` install id | Nothing shipped; migration splits history for zero users | A build reaches an external user first |
| Per-event Playwright journeys | 70 journeys is unmaintainable; families share one | A family cannot be provoked in one run |

**Reviewer check:** nothing in this list is required to close the work — *except* that
excluding paid agent turns forces the non-paid `3e` fixture in S4b. Accepted; it is
now a named step, not an assumption.

---

## 4. Steps and verification

**S1 — split the taxonomy, merge all-or-nothing, preserve fail-closed.**
`schemas/analytics/{install,auth,project,asset,agent,editor,preview,render,export,error,feedback}.json`
+ `_envelope.json`. Merge in `server/analytics.py:70`, `desktop/main.js:96`,
`tests/contracts/test_analytics_taxonomy.py:29`.
*Verify:* the 22 existing tests pass; a new test corrupts one family file and asserts
`validate_event(...) is None` — i.e. **fail-closed, nothing sent** — never a partial
dict. **Breaks / must update:** `test_analytics_taxonomy.py:29`, `desktop/main.js:96`,
`tests/contracts/test_analytics.py:59`, plus references to the old filename in
`schemas/analytics_events.json:2`, `docs/analytics-dashboard.md:8-9`,
`docs/analytics-dashboard-guide.md:98-103`.
Also update the two **stale comments that assert the rejected premise** (D11):
`server/analytics.py:46-47` (names the old file) and `:62-64` (claims an empty taxonomy
"disables validation"). Leaving them is how the next maintainer reintroduces rev 1's error.
*Observability:* add a **one-time taxonomy-load-failure log** and assert it. Under
fail-closed no event can send, so the `unknown_events` counter can never ride out on a
later event, and `log_destination(key, host)` (`:380`) carries only key and host. Without
this line the outage is invisible in the smoke and readback results — and `:145` already
promises a boot line that does not exist.
*Electron scope — DECIDED: main.js validates its direct events against the merged
taxonomy, fail-closed, exactly as Python does.* It reads the file already (`:96`) but only
for `schema_version`, so its direct events (`launch_failure` `:289`, `backend_ready` `:660`,
`app_launch_started` `:833`, `session_started` `:842`, `process_gone` `:902`/`:913`,
`session_ended` `:939`, `desktop_error`) currently bypass the gate entirely. Excluding them
would put the hole exactly where the free-text risk is highest — `launch_failure`
*classifies* a local stderr tail into `failure_class` and sends **only that**
(`desktop/main.js:272-293`), while `desktop_error` sends a scrubbed message and stack. An
exclusion would also make D8/R1's fail-closed claim false rather than partial.

**Consequence, decided not deferred — `desktop_error`'s diagnostic contract.** It declares
only `[app_version, arch, fatal, os, packaged, source]`, yet `desktop/main.js:233-234`
sends `message` (500 chars) and `stack` (8000 chars). Because main posts direct, **nothing
validates them today** — that is a live free-text path to PostHog, not a loss introduced by
validating. Validation closes it.

Losing all detail would gut the crash inbox, so adopt the pattern the catalog **already
ratified** for `launch_failure` (row 4: *"`stderr` tail stays in the local dialog; ship a
classified enum + hash only"* — and `desktop/main.js:272` already implements exactly that,
emitting `failure_class` only and never a raw tail): declare `exception_class`E, `top_frame` (basename:line,
path-scrubbed, no whitespace) and `stack_hash`I for grouping. Raw message and stack stay in
the local log and dialog. Same decision, same reasoning, one precedent.

**S2 — readback client.** Key via `security find-generic-password -s
opennolan-posthog-readback -w`; POST HogQL to `us.posthog.com/api/projects/544720/query/`.
*Verify:* a known `install_id` returns a known event; the key never appears in output
past its `phx_` prefix.
**No silent SKIP for required verification.** A missing key makes the analytics-ON E2E
**fail or explicitly block**. Exit-0 SKIP is reserved for ordinary local commands where
readback is incidental — otherwise an agent reports success having verified nothing.

**S3 — analytics-ON smoke mode.** Distinct from the default scratch env
(`scripts/dev:664-681`).
*Verify, three cases:* (a) key absent ⇒ zero events, no production write; (b) key
present and correct ⇒ boot line names `phc_tTqiU…`; (c) **`POSTHOG_KEY` deliberately
set to the production token ⇒ refused.** `NO_DEFAULT_KEY` only guards the *absence* of
a key (`analytics.py:299`, `desktop/main.js:82`), so both processes must additionally
reject the known production key and require the expected host/project.

**S4a — payload conformance.** For every event delivered under the run's `install_id`:
declared properties present, types correct, every `E` value inside its vocabulary, no
undeclared event.

**S4b — expected-event and expected-variant matrix.** Separate from S4a, because a
normal smoke legitimately emits `launch_kind=cold` and `entry=dashboard` and a
payload validator has no reason to demand unused variants. Each journey declares the
events *and enum variants* it must produce; the matrix fails when a declared variant is
never exercised anywhere. Requires a **non-paid agent fixture** so `agent_turn_started`
is delivered at all and its missing `entrypoint` can surface.
*(rev 1 claimed S4 "must fail on first run" against the three dead values. It could
not — refuted and replaced.)*

**S5 — D7 session fix, delivery-aware.** Announce on mint in
`web/src/analytics/track.js` (first-mint block `:31-34`), emitted from
`initAnalytics()` `:117` (called at `web/src/main.jsx:11`) because `sessionId` is a
module const at `:41`. Write a **pending marker** alongside the id, cleared only on
acknowledgement; on load, an id still marked pending re-announces idempotently.
**"Delivered" must be defined, because today it is fiction at three layers:**
`navigator.sendBeacon` returning true means only that the UA queued it
(`web/src/analytics/track.js`); the `fetch` path is `.catch(requeue)`, so a 500 resolves
and is never requeued; and the endpoint is documented **"Always 200"**
(`server/app.py:1073`) returning `received` = events *submitted to* `capture()`
(`:1080`), which may still drop them for opt-out, taxonomy rejection or a silent SDK
failure. Define acknowledgement as **backend acceptance of that exact event**. A batch
`accepted` count is NOT a receipt: a batch carrying `session_started` plus one other event
where the start is rejected and the other accepted returns `accepted=1`, and the marker
clears with no session registered. **So the announcement is sent as its own isolated
request** — it happens once per session, so a dedicated request is cheaper than per-event
ids and result arrays. The marker is retained on rejection, and the doc states plainly that
PostHog transport offers no synchronous receipt — only the S2 readback can confirm that.
**At-least-once is the honest guarantee — and the metric must be made to tolerate it.**
Backend accepts, response lost on reload, next load re-announces ⇒ two starts for one
session. Exactly-once is not achievable over this transport.

**rev 5 claimed the metrics already tolerated this. That was wrong — my one attempted
pushback in six rounds, itself refuted.** I cited a `starts` CTE that groups by
`session_id`, but that CTE lives only in the PostHog *insight I published*, not in
`docs/analytics-dashboard.md:147-151`, which is the authoritative definition and reads
`count(session_started)` — **rows, not distinct sessions.** The non-fatal error-free rate
(`:172-174`) shares that denominator.

The consequence runs in the dangerous direction: one session, response lost, re-announced ⇒
1 distinct fatal session over 2 start rows ⇒ crash-free reads **50% instead of 0%**. Across
a cohort, a duplicate start *hides* failures.

In order:

1. **Fix the definition.** Wall 5 and the non-fatal rate must count **distinct
   `session_id`**, and `docs/analytics-dashboard.md` must carry the `starts` CTE explicitly.
   **`docs/analytics-dashboard-guide.md:261-264` repeats the same row denominator and must be
   corrected with it** — an implementer reading the guide would otherwise recreate the
   inflated metric from a doc that looks authoritative. Three artefacts state this formula
   (dashboard doc, guide, published insight) and today **only the insight is right**; all
   three must agree.
2. A **bounded in-process dedupe** at the endpoint as hygiene against cost and noise.
   *ponytail: bounded LRU; a backend restart forgets. It cannot make the metric correct —
   only (1) does.*

*Verify:* `web/src/analytics/track.test.js` — shell id ⇒ 0; `sessionStorage` hit with no
pending marker ⇒ 0; fresh mint ⇒ 1; **endpoint returns 200 with accepted=0 ⇒ marker
retained, re-announced next load**; **mixed batch cannot clear the marker** (never batched);
**response lost after acceptance ⇒ the corrected Wall 5 query still reads 0% crash-free
for that fatal session.**

**That last case cannot live in `track.test.js`.** Every case above mocks `fetch`, so all of
them can pass while the published query still divides one distinct fatal session by two
`session_started` rows and reads 50%. **S2 owns it:** a HogQL check that executes the
*documented* `starts` CTE verbatim against a fixed fixture — one fatal session with a
duplicate start — and asserts **0%**. A renderer unit test cannot reach the metric; only the
readback can.

rev 4's "exactly 1 overall" and rev 5's "at most one distinct start" both tested the
transport. rev 6 named the right assertion but left it in the wrong harness.
Live: Wall 5 `fatal_sessions_with_no_start` 1 → 0.

**S6 — install id, one per launch, crash-safe.** **Hard-link publish. Not `rename`, and never a retry.**
`rename()` REPLACES its destination, so two processes could each write a complete temp,
both rename successfully, and each return its own candidate while the later write silently
overwrites the persisted id — recreating D9 exactly. `link()` is the no-replace primitive:
it fails `EEXIST`, which is what produces a winner.

    temp in the SAME directory (link cannot cross filesystems)
    write + fsync temp
    link(temp, install_id)
        success   -> fsync the directory, unlink temp, return candidate
        EEXIST    -> read the winner (guaranteed complete), unlink temp
        any other -> unlink temp, DISABLE analytics. Never invent an id:
                     inventing one is the defect this step exists to remove.

Every other operation — temp write, temp fsync, the winner read after `EEXIST`, the
directory fsync, the temp unlink — gets the same treatment: **cleanup in a `finally`, and
any non-success disables analytics with a local log rather than inventing an id.** A
directory fsync that throws after a successful link must still unlink the temp; a linear
implementation leaks one private temp per boot.

**`EEXIST` does not guarantee a complete winner.** The current buggy implementation can
already have left a zero-byte `install_id` on a real machine, so a read after `EEXIST` that
returns empty is a safe **disabled** state, never an id.

No empty-read fallback (`server/settings.py:126` `or did`, `desktop/main.js:159`
`|| minted`) and no retry loop.
*Why not retry:* rev 2 said "retry until non-empty." If the winner is killed between
inode creation and write, the file is permanently empty and **every subsequent boot spins
forever** — `installId()` runs synchronously during boot, so the app hangs and the backend
never receives a `distinct_id`. That trades an occasional duplicate id for a permanent
startup failure.
*Verify:* a Python-plus-Node contention test using an **isolated temporary HOME** passed
to both spawned processes — never the developer's real `~/.opennolan/`, which either
already holds an id (so the race is never exercised) or risks their data, and which
conflicts with the isolation pattern in `tests/conftest.py`. Cases: simultaneous boot ⇒ one id;
**winner killed mid-publish ⇒ next boot recovers and does not hang**; `EEXIST` path;
**non-`EEXIST` link failure ⇒ analytics disabled, no id invented**;
**pre-existing zero-byte `install_id` ⇒ disabled, not adopted**; **fsync failure after link
⇒ temp still cleaned up**; assert no host-home file was touched.

**S7 — bounded, per-source volume budget.** A single "session limiter" is not
implementable: three sources emit independently and no one of them sees the others —
the renderer batch via `server/app.py:1068`, backend-direct `capture()`
(`server/analytics.py:436`), and Electron main posting raw JSON straight to PostHog
(`desktop/main.js:171-223`), which a backend counter cannot observe at all.
So: **explicit per-source budgets with exact values in the manifest, constrained by an
equation** — "small" and "separately stated" are adjectives, and rev 3 used them in place of
numbers, which only moved the unboundedness rather than removing it:

    backend_noncritical + electron_noncritical + Σ(critical reserves)  ≤ 100
    expected productive session                                        ≤ 40

The backend owns renderer + backend events keyed by `session_id`; Electron owns its own
reserve, because a backend counter cannot see `desktop/main.js:171-223` at all. If the
agreed 100 is instead defined to EXCLUDE criticals, it must be renamed the **non-critical
cap** and the total-hard-cap claim withdrawn — not left ambiguous.
*Verify:* the reviewer's counterexample journey stays under 40; a synthetic flood is
capped and the drop counted, not silent; a critical-event flood is bounded by the reserve
rather than unbounded. **If the budget is not built, the ≤40/100 claim is withdrawn from
the doc** rather than left unenforced.

**S8 — the 49 events, one family per round.** Order: install/provisioning → auth →
project → asset ingest → editor → render/export → errors → feedback → agent (last; only
one needing fixtures). Each round: classify, re-derive anchors by symbol, extend the
family schema, emit, extend the journey, run S4a + S4b + S7.

---

## 5. Risk register

| # | Risk | Mitigation | Proven by |
|---|---|---|---|
| R1 | Partial merge silently drops valid events | all-or-nothing ⇒ fail-closed. **Not self-announcing:** under fail-closed no event can carry the counter out, so S1's one-time load-failure log is the only signal | S1 corrupt-file test + log assertion |
| R2 | Personal `phx_` key leaks from a public repo | Keychain; `query:read` only; project-restricted; never logged past prefix | S2 + `git status` |
| R3 | 49 new events blow the session ceiling | per-source budgets + bounded critical reserve, not intention | S7 counterexample + flood + critical flood |
| R4 | A capture call lands in a banned hook class via a stale anchor | anchors re-derived by symbol | S8 per-family review |
| R5 | E2E flakes on ingestion lag | poll to timeout; failure names what never arrived | S2 poll test |
| R6 | Fault injection destabilises the suite | confined to analytics-ON smoke; never `test fast` | S3 + `test fast` unchanged |
| R7 | A production write escapes the harness | reject the known production key and require expected host/project in **both** processes — `NO_DEFAULT_KEY` alone only covers a *missing* key | S3 case (c) |
| R8 | Agent path proves out only under supervision | S2 must run from an agent terminal, not the coordinator's | S2 run by the reviewer |
| R9 | Two install ids for one launch break every readback join | atomic publish (temp + fsync + link) in both languages | S6 contention + winner-death test |
| R10 | A required verification passes having verified nothing | no exit-0 SKIP on the analytics-ON path | S2 |

---

## 6. Review rounds

### Round 1 — gpt-5.6-terra (high), read-only. **VERDICT: REJECT.** 9 findings, all accepted.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 1 | BLOCKER | Taxonomy fails **closed**, not open; rev 1's S1 check would have broken `test_analytics.py:59` | **My error.** D8, S1, R1 rewritten. I described code I read before four rounds inverted it — the exact trap my own D3 names |
| 2 | BLOCKER | `NO_DEFAULT_KEY` only guards a *missing* key; an explicit production token still writes to production from both processes | **My error.** R7 + S3 rewritten; S3 case (c) added |
| 3 | MAJOR | S4 could not fail on the three dead values — payload conformance ≠ enum-variant coverage, and no agent turn is delivered at all | Accepted; split into S4a / S4b, non-paid `3e` fixture promoted to a named step |
| 4 | MAJOR | Announce-on-mint loses the announcement if the queue dies before flush; the id then stays permanently unregistered | Accepted; pending marker cleared only on delivery, reload-before-flush test |
| 5 | MAJOR | No session-scoped cap exists; the 100 is per POST body. Counterexample exceeds 40 from P0 rows alone | Accepted; S7 added, with an explicit "withdraw the claim if not built" |
| 6 | MAJOR | Electron/Python can mint different install ids in the create-before-write window | Accepted as **D9**; required, not optional — it is the readback join key |
| 7 | MAJOR | S2's exit-0 SKIP lets a required verification report success having verified nothing | Accepted; SKIP scoped out of the analytics-ON path |
| 8 | MINOR | Split leaves the authority docs and the schema comment pointing at a file that no longer exists | Accepted; enumerated in S1 |
| 9 | MINOR | D1's 50 is not reproducible — the combined render row created a phantom name | Accepted; **49**, and counts must derive from a generated manifest |

**Confirmed correct by the reviewer:** all plan anchors, including the D3 drift table;
the three taxonomy readers are exactly the runtime readers; `skills/pipelines/**`
carries no telemetry contract; nothing in §3 is required to close the work except the
S4b fixture.

### Round 2 — gpt-5.6-terra (high), read-only. **VERDICT: REJECT.** 6 findings, all accepted.

Every one landed in a step **rev 2 added** — the fixes, not the original plan.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 10 | BLOCKER | S6's "retry until non-empty" turns an occasional duplicate id into a **permanent boot hang**: winner killed between inode creation and write ⇒ file permanently empty ⇒ every later boot spins in a synchronous `installId()` and the backend never gets a `distinct_id` | **My error.** S6 rewritten to atomic publish (temp + fsync + link); retry removed entirely; winner-death case added to the test |
| 11 | MAJOR | S7 named no shared enforcement point, and "critical bypass" made the hard cap unbounded. Three sources count independently and Electron's direct reporter is invisible to a backend counter | **My error.** S7 rewritten: per-source budgets, cap defined as non-critical-only plus a bounded critical reserve |
| 12 | MAJOR | S5 cannot clear a pending marker "on delivery" — `sendBeacon` true = queued, `.catch(requeue)` misses non-2xx, and the endpoint is "Always 200" with `received` = *submitted*, not accepted | **My error.** Acknowledgement redefined as backend acceptance with a real accepted count; doc now states PostHog offers no synchronous receipt |
| 13 | MAJOR | R1 called a total taxonomy failure "loud" via counter + boot line. Under fail-closed no event can carry the counter out, and `log_destination` (`:380`) takes only key and host | Accepted; R1 reworded, S1 gains a one-time load-failure log **and** an assertion on it |
| 14 | MINOR | S1 did not list the stale comments that assert the rejected premise | Accepted as **D11** — `analytics.py:46-47` and `:62-64` still claim an empty taxonomy "disables validation", contradicting `:141-147`. **This docstring is what caused my rev-1 error** |
| 15 | MINOR | S6's contention test named the developer's real `~/.opennolan/` | Accepted; isolated temporary HOME for both spawned processes, asserting no host-home access |

**Confirmed correct by the reviewer:** all nine round-1 corrections landed; every source
anchor added in rev 2 is right (`settings.py:124-126`, `main.js:159`, `app.py:1075-1080`,
`track.js:25`); D1's derivation now reproduces (70 / 21 / 49).

### Round 3 — gpt-5.6-terra (high), read-only. **VERDICT: REJECT.** 5 findings, all accepted.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 16 | BLOCKER | "atomic link/**rename**" is wrong: `rename()` REPLACES its destination, so two complete temps both rename and the later silently overwrites the id — D9 recreated. No policy for link failure after fsync | **My error**, a POSIX semantics slip. S6 now specifies `link()` only, with an explicit errno table: EEXIST ⇒ read winner; any other errno ⇒ **disable analytics, never invent an id**; fsync the directory |
| 17 | MAJOR | Per-source budgets still do not compose: "small" and "separately stated" are adjectives, so the sum can exceed 100 while each source stays bounded | **My error** — I relocated the unboundedness instead of removing it, the exact thing I asked round 3 to check. S7 now carries an equation and demands values in the manifest |
| 18 | MAJOR | A batch `accepted` count cannot acknowledge one event: `session_started` rejected + another accepted ⇒ `accepted=1` ⇒ marker cleared with no session registered | Accepted. The announcement is now sent as its **own isolated request** — once per session, so cheaper than per-event ids. Mixed-batch case added to the tests |
| 19 | MAJOR | §2's D9 row still directed the **rejected retry**, contradicting S6 in the same document | **My error**, and it is D2's defect reproduced in my own plan. Row replaced; every retry reference removed |
| 20 | MAJOR | S1 left Electron scope as an open *choice* while D8/R1 claimed total fail-closed | **Decided, not deferred:** Electron validates its direct events against the merged taxonomy. Its direct events include `launch_failure` (classified stderr) and `desktop_error` (message + stack) — excluding them puts the hole where the free-text risk is highest and makes the fail-closed claim false rather than partial |

**Confirmed correct by the reviewer:** all six round-2 corrections landed; S5 correctly
distinguishes queued / HTTP / backend-acceptance / readback; S6's test uses a temporary
HOME with a winner-death case; every new anchor is right (`analytics.py:46-47`, `:62-64`,
`:145`, `:380`, `app.py:1073`, `:1080`, `desktop/main.js:171-223`).

### Round 4 — gpt-5.6-terra (high), read-only. **VERDICT: REJECT.** 3 findings, no blocker.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 21 | MAJOR | An isolated request is still not idempotent: backend accepts, response lost on reload, next load re-announces ⇒ two starts for one session. rev 4's "exactly 1 overall" assertion is false | **Accepted as a race, PARTIALLY REFUTED as a correctness defect.** Exactly-once is unachievable over this transport, so at-least-once is the honest guarantee. The metrics already tolerate it: `docs/analytics-dashboard.md` Wall 5's `starts` CTE is `GROUP BY properties.session_id`, so `count()` is distinct sessions and a duplicate cannot inflate the denominator. Added a bounded in-process dedupe as hygiene, named its restart ceiling, and **corrected the assertion** to "at most one DISTINCT session_id start" |
| 22 | MINOR | Electron validation would drop `desktop_error`'s `message`/`stack`, gutting the crash inbox — but keeping them reopens the free-text hole | Accepted, and **sharper than reported**: those two properties are undeclared *today* and main posts direct, so nothing validates them — a live free-text path, not a loss caused by validating. Decided using the catalog's own ratified `launch_failure` precedent: classified enum + hash on the wire (`exception_class`, `top_frame`, `stack_hash`), raw text stays local |
| 23 | MINOR | S6's errno policy covered `link` but not temp write/fsync, the winner read, the directory fsync, or unlink; and a pre-existing zero-byte `install_id` makes `EEXIST` not a guaranteed winner | Accepted; `finally` cleanup, disable-and-log on every non-success, and two new tests (zero-byte target, fsync failure after link) |

**Confirmed correct by the reviewer:** all five round-3 corrections landed; hard-link is the
right APFS no-replace primitive when temp and target share a directory; every new anchor
accurate.

### Round 5 — gpt-5.6-terra (high), read-only. **VERDICT: REJECT.** 2 findings, both accepted.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 24 | MAJOR | **My refutation was wrong.** The `starts` CTE I cited exists only in the published PostHog insight, not in `docs/analytics-dashboard.md:147-151`, whose Wall 5 denominator is `count(session_started)` — rows. A duplicate start gives 1 distinct fatal session over 2 start rows ⇒ crash-free reads **50% instead of 0%**, so duplicates *hide* failures | **Refutation withdrawn in full.** The documented Wall 5 and non-fatal rate must count distinct `session_id`, and the doc must carry the CTE. Also records that the insight and the doc currently disagree. Endpoint dedupe demoted to hygiene, explicitly unable to make the metric correct |
| 25 | NIT | S1 said `launch_failure` "carries a classified stderr tail"; `desktop/main.js:272` never ships a raw tail — `failure_class` only | Accepted; wording corrected, and the precedent is now cited as *implemented*, which strengthens it |

**Confirmed correct by the reviewer:** findings 22 and 23 landed; the classified
`exception_class` / `top_frame` / `stack_hash` representation is a reasonable privacy-first
triage tradeoff once the taxonomy declares and tests those three bounded fields.

**On the one pushback I attempted:** I cited a document by name and quoted a CTE that was
not in it — I was recalling SQL I had written myself. The reviewer checked the source I
named. That is the whole value of an opposite-provider review, and it is the reason this
plan's §1 insists every anchor be grepped fresh rather than remembered.

### Round 6 — gpt-5.6-terra (high), read-only. **VERDICT: REJECT.** 3 findings, no blocker.

| # | Sev | Finding | Resolution |
|---|---|---|---|
| 26 | MAJOR | S5 promised an assertion against real Wall 5 semantics but put every case in a renderer unit test. A mocked-`fetch` test passes while the published query still reads 50% | Accepted. **S2 now owns** a HogQL check executing the *documented* `starts` CTE verbatim against a duplicate-start fatal fixture, asserting 0%. rev 6 named the right assertion and left it in the wrong harness |
| 27 | MINOR | The correction updated the dashboard doc but not `docs/analytics-dashboard-guide.md:261-264`, which repeats the same row denominator | Accepted. **Three artefacts state this formula and only the published insight is right today** — dashboard doc, guide and insight must all agree, and both docs are now in the update list |
| 28 | NIT | The *operative* Electron paragraph still said `launch_failure` "carries a classified stderr tail" — I had fixed only the precedent citation | Accepted; it classifies a local tail into `failure_class` and sends only that (`desktop/main.js:272-293`) |

### Round 7 — gpt-5.6-terra (high), read-only. **VERDICT: APPROVE.** No findings.

> *"S2 now correctly owns the executable HogQL assertion for the documented distinct-session
> CTE, S5 names both repository documents plus the insight for synchronization, and the
> corrected `launch_failure` language matches `desktop/main.js:272-293`."*

It also answered the question round 6 was asked to check — **no other wall repeats the
defect**: Walls 1 and 4 already use distinct entity denominators, Wall 2 is a first-event
duration, and Wall 3 deliberately counts delivered turn events.

### Where the findings are landing

| rev | Findings | Concentrated in |
|---|---:|---|
| 1 → 2 | 9 | the original plan |
| 2 → 3 | 6 | the fixes written in rev 1 |
| 3 → 4 | 5 | the fixes written in rev 2 |
| 4 → 5 | 3 | the fixes written in rev 3 — **no blocker** |
| 5 → 6 | 2 | one was my refutation being refuted — **no blocker** |
| 6 → 7 | 3 | incomplete application of round 5's own fix — **no blocker** |
| 7 | **0** | **APPROVE** |

Nine of twenty were my errors, and each round's defects sit in the *newest* material. The
second-generation fix gets less scrutiny than the thing it fixes — which is the argument
for not implementing off rev 1 of anything, and for this review continuing until a round
returns nothing substantive.
