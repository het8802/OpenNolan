# Implementation report — full event coverage, all three tiers

**STATUS: BUILT · claude · against [`plan.md`](plan.md) (APPROVED, rev 7) and
[`architecture.md`](architecture.md)**

The plan was approved for **P0 only**. The human then widened it to everything. This is what
that produced, what it cost, and what I deliberately did not build.

Nothing is committed. The tree holds this on top of the previous build's ~2,100 uncommitted
insertions.

---

## 1. The manifest — every total is a set difference

`scripts/analytics_manifest.py` parses the ratified catalog and diffs it against the live
taxonomy. **No number in this document is hand-carried**, which is D1's rule: rev 1 of the plan
said "50 P0 events" and it was 49, because a row carrying two event names parsed as one phantom.

```
$ python3 scripts/analytics_manifest.py --check

CATALOG   95 EMITTED rows / 97 names / 35 DERIVED rows
  P0  names= 70  declared= 69  outstanding=  0
  P1  names= 23  declared= 23  outstanding=  0
  P2  names=  4  declared=  2  outstanding=  0
DECLARED  96 names   (catalog cap is <=100 EMITTED names)
BUILD     0 outstanding
```

The parser reproduces the catalog's own machine-derived counts exactly (95/97/35), which is the
check that it is reading the same document the reviewers read.

**75 rows / 76 names were outstanding at the start.** The task brief said 75; the extra name is
row 118, which carries `survey_shown` *and* `survey_answered`. The cap is on **names**, not rows
— the catalog says so explicitly — so 76 is the number that matters.

### DROP — 3 names, each with a reason

| Name | Why not |
|---|---|
| `preview_mode_switched` | FOLDED into `editor_session_summary` as `preview_switches_source` / `preview_switches_render`. Six switches is six uploads, and the catalog's own §3 allows only six families to upload per-interaction — this is none of them. "Do users keep flipping to render?" is a counter question, and the counters answer it identically. |
| `survey_shown` | No surface exists. The catalog itself says *"new surface; no anchor invented"* — both phase-1 docs anchored survey rows at unrelated lines. Building a survey UI is a product feature, not instrumentation of one. |
| `survey_answered` | Same surface. |

### RENAME — 1

`auth_connected` → `auth_connect_finished`. The old event was success-only, so setup conversion
had a numerator and no denominator; the failure branches did not exist at all. Renaming rather
than adding keeps the name count under the cap.

### Outside the catalog — 2, kept

`app_opened` (the backend-boot signal, distinct from the shell's `app_launch_started`) and
`desktop_error` (the crash inbox entry from Electron main, which posts direct because the
backend may never have started).

---

## 2. The recomputed volume budget — the thing the plan did not size for

**S7's budget was computed for 49 events. This build ships 76 names.** Recomputed before
building anything, because a number nobody can afford is worse than a number nobody has.

The reviewer's D10 counterexample, re-run across all three tiers — 8 asset imports plus probes,
10 asset adds, 4 project opens, 6 preview switches, 2 agent turns with tool failures, 2 renders,
1 export, plus normal start events:

```
  verbatim                     92 uploads   (electron 4 / backend 48 / renderer 40)
  after the two rollups        78 uploads   (electron 4 / backend 49 / renderer 25)
  expected productive session  ~34          (the catalog's own §6 all-tier model is ~30)
```

**92 is under the 100 hard cap and 2.3× the ≤40 expected, with zero headroom.** Two families
drove it, and **both violate the catalog's own rule** rather than mine:

- `asset_added_to_doc` ×10 → one per-session rollup carrying the **`asset_ids` array**. Row 37
  (`human_add_rate`) joins `#29 ⋈ #34 on asset_id`, and the array preserves that join exactly.
  Properties are free; events are not — the catalog's own item-2 settlement.
- `preview_mode_switched` ×6 → switch **counters** on the session summary.

**Conclusion: 76 events fit. The ≤40 expected figure survives. The 100 hard cap needed real
enforcement**, because unenforced the pathological session is unbounded — the only 100 in the
code was per POST body and per renderer queue, never per session.

The enforcement is per-source, under one equation asserted by a test:

```
  backend_noncritical(55) + electron_noncritical(8) + Σ(reserves 25 + 12)  =  100
```

Electron's half lives in `desktop/main.js` because a backend counter **cannot observe it at
all** — main POSTs raw JSON straight to PostHog, which is the whole reason that transport
exists. `test_electrons_numbers_match_the_ones_python_publishes` reads the JS constants and
asserts they match the Python ones, so two numbers in two languages cannot drift apart.

`critical` is a flag **in the taxonomy**, not a hardcoded list, so both languages read one
source and the reviewer sees it in the diff that adds the event.

---

## 3. What each step produced

| Step | Built | Verified by |
|---|---|---|
| **S1** taxonomy split | `schemas/analytics/{_envelope,install,auth,project,asset,agent,editor,preview,render,export,error,feedback}.json`; all-or-nothing merge in all three readers; Electron now runs the **same gate** on its direct events | 4 merge tests: corrupt family, missing envelope, duplicate name, intact control |
| **S2** readback | `scripts/analytics_query.py` — Keychain `phx_`, HogQL over the query API, `events`/`sql`/`wall5`/`await` | no-key exits **2**, never 0 |
| **S3** analytics-ON smoke | `scripts/dev smoke --analytics`, opening the three deliberate gates in order | refuses the production token and an unexpected host; refuses to run with no key rather than passing |
| **S4a** payload conformance | per-event round-trip with a fully conforming payload; type checks; reserved-substring sweep | 96 parametrized cases |
| **S4b** variant matrix | every declared enum variant must be reachable from source; every declared property must be written somewhere | **found 66 dead variants and 36 never-written properties** — see §4 |
| **S5** session announce | pending marker cleared only on backend acceptance; **its own isolated request** | 7 renderer tests incl. accepted=0 ⇒ marker retained |
| **S6** install id | hard-link publish (temp → fsync → `link`), errno policy, `finally` cleanup, no retry | 9 tests incl. a **live Python↔Node contention race** |
| **S7** volume budget | per-source budgets + bounded critical reserve | flood, critical flood, per-session isolation, the counterexample fixture |
| **S8** events | 76 names across 9 families | manifest reports 0 outstanding |

### D11 — the code that lied about its own failure mode

- `server/analytics.py`'s docstring claimed a missing taxonomy *"disables validation rather than
  silently dropping every event."* It does the exact opposite. **That docstring is what produced
  the plan's rev-1 error**, and it would have misled the next maintainer identically. Fixed, and
  the module contract now names the gate separately from the sink.
- `:145` promised a boot line that did not exist. Under fail-closed no event can carry the
  `unknown_events` counter out, so a total taxonomy failure was **silent everywhere**. There is
  now a one-time stderr line, asserted by a test.

### The `desktop_error` free-text path — closed

It declared `[app_version, arch, fatal, os, packaged, source]` and **sent `message` (500 chars)
and `stack` (8000 chars)**. Because main posts direct, nothing validated them: a live free-text
path to PostHog, not a loss introduced by validating. Replaced with `exception_class`,
`top_frame` (basename:line, path-stripped) and `stack_hash`, the pattern `launch_failure`
already ships. Raw text stays in the local log and the fatal dialog.

---

## 4. Built smaller than specified, and why

**66 enum variants and 36 properties were declared and then deleted.** I first transcribed the
catalog's full vocabularies, and S4b immediately failed on 35 enums. Every one was real: I had
declared `provisioning_error.stage: [doctor, download, install, verify]` and only ever emit
`install`; `previous_exit` declares `kill` and the classifier deliberately never returns it
("`crash` is the honest superset"); `layer` declares `agent_sdk` and `render_worker`, neither
wired.

A declared-but-unreachable variant is **a dashboard slice that reads zero forever and never says
why** — the same defect class as `export_completed` living only in a test file. So they were
trimmed to what the code can actually produce. They are cheap to add back the moment an emit
site exists, and S4b will now fail if one is added without one.

Other deliberate reductions:

| Specified | Built | Why |
|---|---|---|
| `project_stalled` via a nightly sweep in `server/lifecycle.py` | once per backend start, at most once per 20h | A desktop app has no scheduler and no overnight uptime. Same coverage, none of the machinery. |
| `preview_export_divergence` comparing assemble geometry to canvas values | presence-only comparison of overlay position and crop | Comparing VALUES means uploading the user's composition. Presence answers "did the assemble drop something the canvas showed", which is the contract RULES.md states. |
| `audio_output_health` with `clipped_samples` / `silent_seconds` / `channel_layout` | `peak_dbfs_bucket` + `integrated_lufs_bucket` from one ebur128 pass | The other three need a second analysis pass over the published file. Buckets answer "is the deliverable audible". |
| `agent_interrupted` with `elapsed_s` / `tool_calls_so_far` | `tool_in_flight` only | The interrupt path has no turn clock in scope. `tool_in_flight` is the question the row exists for. |

---

## 5. Verified against live data

The human supplied the `phx_` readback key (stored in the Keychain, never in a file). The loop
is closed, and **running it live found five defects that every offline test passed.**

### D7 reproduced exactly, against real rows

```
$ scripts/analytics_query.py wall5
FAIL: 1 fatal session(s) are on no register — Wall 5 cannot see them.
{ "start_sessions": 26, "start_rows": 30, "duplicate_start_rows": 4,
  "fatal_sessions": 4, "fatal_sessions_with_no_start": 1, "crash_free": 0.846 }
```

The orphan is session `4f3044c6-cd33-40ab-b4e0-22e99ffe8fad` — **the exact id the plan's D7
names**, carrying the project's only fatal `$exception` and registered nowhere. The tool exits
**1**. Note `duplicate_start_rows: 4` on live data: row-counting would read `1 − 4/30 = 86.7%`
against the correct `84.6%`, so the duplicates are already hiding failures today.

### S2's named assertion — PASSES

`scripts/analytics_wall5_check.py` writes one session announced **twice** carrying one fatal
error, through the real `analytics.capture()` path (so the gate, scrubber, envelope and budget
all apply), then executes the documented query against it:

```
{ "start_sessions": 1, "start_rows": 2, "duplicate_start_rows": 1,
  "fatal_sessions": 1, "crash_free": 0.0 }

PASS: a duplicate start does not inflate the denominator. Row-counting would have read 50% here.
```

**0.0%, not 50%.** That is the assertion rounds 5 and 6 of the review argued over, now proven
closed against data that left the machine.

### Five defects found only by running it

| # | Defect | How it was found |
|---|---|---|
| 1 | **`_fingerprint` joined with `@`**, which `_BOUNDED_TOKEN` does not allow — so the crash inbox's grouping key was dropped from **every** `error_reported`. The inbox looked healthy and grouped nothing. | `data_quality_violation{class: wrong_type, event_name: error_reported}` in live data — the event built for exactly this |
| 2 | A synthetic frame's basename is `<string>`; angle brackets fail the same gate. | fixing #1 |
| 3 | **Nine events reached via `RenderJobStore._emit` did not declare `origin`/`publish_intent`**, which `_emit` attaches to all of them — so each lost its editor-vs-agent slice. | `data_quality_violation{class: unknown_property}` on five distinct events in delivered data |
| 4 | **The `wall5` query hit PostHog's max execution time.** Four correlated subqueries plus two `IN (SELECT …)`. A check that times out is a check that does not run. | running it |
| 5 | **`await` polled a query shape that lies.** `WHERE install_id = X AND event = Y` returned 0 for *minutes* against rows `WHERE install_id = X` + `countIf(event = Y)` could already count. The two predicates take different query paths and do not become consistent together. | a timeout on data that was demonstrably present |

Every one is fixed, and #1–#3 have regression tests (`test_8_*`) that assert a value we generate
ourselves survives our own gate — the inverse of every other test here, and the failure mode
that actually shipped. #3's test rescans `render_jobs.py` for `_emit` call sites, and
immediately caught a tenth event I had missed by hand (`preview_export_divergence`).

The optimized query is **one scan grouped by session**, and
`docs/analytics-dashboard.md` now carries that exact text — three artefacts stating one formula
and only one of them being right is finding #24.

**Measured ingestion lag: 5–8 minutes** for a fresh `install_id` in this project. The poll
windows are sized for it; R5's "poll to timeout" was not optional advice.

**One pre-existing test failure**, not a regression:
`tests/contracts/test_server_agent_api.py::test_chat_without_auth_returns_503_with_guidance`
fails under a bare `pytest` because `create_app()` loads the repo `.env`, which holds real auth,
so the "no auth ⇒ 503" test gets a 200. It **passes** under `scripts/dev test full`, which
strips every `*_KEY` from the environment. Both gates below are green.

---

## 6. Gates

```
python3 scripts/dev test fast   → passed (ruff check · ruff format --check · contracts · web test)
python3 scripts/dev test full   → passed (ruff · pytest · web test · web build)
python3 scripts/dev smoke       → passed
python3 scripts/analytics_manifest.py --check  → 0 outstanding
python3 scripts/analytics_wall5_check.py       → PASS (exit 0, live)
```

Run with the app stopped. `stop is idempotent` failed once mid-session on a process left behind
by a smoke run — exactly the trap the brief names, and not a regression: `scripts/dev stop`
followed by a re-run is green.

---

## 7. Public-repo check

Every new property was run through `_scrub` and round-trips unchanged; every type-E property is
either in a closed enum or justified in `open_vocabularies`.

The deny-by-default gate is a **shape** check and cannot close this class on its own —
`q4-launch-teaser` is character-for-character the same shape as `instagram-fast-reel`. That is
stated as an assertion in `test_7_the_shape_gate_admits_a_slug_which_is_exactly_why_emit_sites_collapse`
so nobody re-derives "the validator will catch it". The real defense is the emit site, and each
new one has a test with the leak string as its input:

- an asset filename → `.mp4`, never the name
- a jsonschema message → the declared field name, never the value it rejected
- an externally-authored MCP tool name → a sha256 prefix
- a Bash command → `filter_family` + `root_family`, never the command
- a BYOK variable name → `provider_family` (`_SECRET_HINT` would **not** have saved us: it tests
  the key NAME, so `'ANTHROPIC_API_KEY'` as a VALUE rides through unredacted)
- an unresolved source ref → `reference_kind`, never the path
- a crash → `ExceptionClass@basename:line`, no message, no directory

---

## 8. Open

1. **The `phx_` readback key.** Until it exists, no assertion in this build has been checked
   against data that left the machine — which is the exact class of evidence the plan says let a
   fatal crash count as zero for four rounds.
2. **`store_asset(kind='final_render')` still bypasses the North Star.** `agent_store_asset` now
   flags it (`unreceipted_final_artifact`) rather than leaving it invisible, but the underlying
   question — is that path supposed to produce an export? — is the human's (catalog §11 Q1).
3. **`survey_shown` / `survey_answered`** need a surface before they need instrumentation.
4. **`fatal_sessions_with_no_start` is still 1.** S5 fixes this for NEW sessions — the orphan is
   historical, from before the announcement existed. It should reach 0 and stay there; if a new
   orphan appears, the announcement has regressed.
