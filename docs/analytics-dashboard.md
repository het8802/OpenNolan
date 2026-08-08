# The five walls — query definitions

STATUS: BUILT (P0 item 7)

Five numbers, on a wall, that say whether OpenNolan works for the people using it. This file
is the definition of each one: the formula, the events it reads, and the filter that separates
a real user from us. Everything here is computable from the events actually emitted today —
`schemas/analytics/*.json` (merged all-or-nothing) is the authority on what exists, and
`tests/contracts/test_analytics_taxonomy.py` fails if that file and the code disagree.

Reasoning lives in `docs/plans/analytics-maximal-datapoints/agreed/plan.md`. This is the
query sheet.

---

## The two filters, and why there are two boards

```
                    every event carries  env ∈ {dev, packaged}
                                         internal ∈ {true, false}

  PRODUCT board   internal != 'true'     real users only
  INTERNAL board  internal =  'true'     the developer's own machines
```

`env` cannot do this job on its own: the developer running the downloaded `.app` looks
identical to a real user. `internal` is set by `OPENNOLAN_INTERNAL` or the
`~/.opennolan-internal` sentinel file.

**Before beta the INTERNAL board is the only one with anything in it.** 100% of events ever
received are `internal: true`, so the product board is correctly, and permanently, empty until
someone else opens the app. Build both; read the internal one.

One trap, verified: `OPENNOLAN_INTERNAL=0` does **not** turn the marker off — a falsy env var
falls through to the sentinel-file check. Do not filter smoke-test events out on that basis.

---

## The join keys

Every event carries these. Without them nothing joins to anything, which is why the envelope
was built before any of the metrics below.

**`project_id` is NOT the folder name.** The on-disk project id is a slug of the name the user
typed ("Q4 launch teaser" → "q4-launch-teaser"), so it is customer text and never goes on the
wire. What is sent is a random id persisted at `<project>/.analytics_id` and minted on first
use. The renderer receives it from `GET /api/projects/{id}/edit_decisions`; it never sees a
reason to send the slug.

```
  install_id ──────────────────────────────────────────────  one Mac
      │        (~/.opennolan/install_id, outside every worktree; = PostHog distinct_id)
      │
      ├── session_id ──────────────────────────────  one app launch
      │       minted in Electron main, so a ⌘R reload does NOT split it.
      │       Rides to the backend on the X-ON-Session header.
      │         │
      │         ├── turn_id ─────────────  one agent turn
      │         │       │
      │         │       └── tool_invocation_id ─── one tool call
      │         │
      │         └── job_id ──────────────  one render / export / media op
      │                 an AGENT render inherits session_id through turn_id,
      │                 so it is attributable to the launch that caused it.
      └── project_id
```

---

## Wall 1 — Activation, 7 days

> Of the people who installed, how many produced a video in their first week?

```
numerator    distinct install_id with  export_completed
             at ts <= min(app_first_run.ts) + 7 days
denominator  distinct install_id with  app_first_run
             whose 7-day window has already elapsed
```

Good ≥ 40% · Bad < 20% · Join `install_id`

The right-censoring guard in the denominator is load-bearing: without it, someone who
installed yesterday counts as a failure to activate.

`first_export` is **not** an event property — it is `min(timestamp)` per `install_id` here.
The only local counter available at the emit site is the in-memory job dict, which empties on
every backend restart and would report a first export repeatedly.

## Wall 2 — Time to value

> How long from install to first finished video?

```
per install:  first(export_completed).ts − first(app_first_run).ts
report:       P50, P90
```

Good P50 < 1 day · Bad P90 > 7 days · Join `install_id`

## Wall 3 — Agent value, and its price

Two halves. Neither is meaningful alone: a useful agent nobody can afford is not a feature.

```
useful-turn rate =
    agent_turn_completed  where  doc_changed = true
                                 OR artifacts_delta > 0
    ─────────────────────────────────────────────────────
    agent_turn_started

price = median( sum(agent_turn_completed.cost_usd) per project_id
                up to that project's first export_completed )
```

Good ≥ 70% and P50 < $3 · Bad < 50% or P90 > $10 · Join `turn_id` → `project_id`

The denominator is **delivered** turns, not successful ones. BYOK means the user pays for an
errored turn too, so a conditional rate would hide exactly the cost that hurts.

`doc_changed` is a real before/after diff of `edit_decisions.json` taken around the turn — not
a route flag. The agent writes that file directly and never goes through the editor's PUT, so
`author='agent'` was never observable at any HTTP boundary.

## Wall 4 — Export reliability

> When someone asks for a finished video, do they get one?

```
numerator    distinct job_id with  export_completed
denominator  distinct job_id with  render_queued  where publish_intent = true
             and old enough to have reached a terminal state
```

Good ≥ 95% · Bad < 90% · Join `job_id`

Three details that decide whether this number is honest:

- **`render_queued` is the denominator, not a terminal event.** Two real publish-intent
  failures return before the render even starts; a denominator taken any later than job
  creation silently drops them and inflates success.
- **`render_superseded` is excluded from both sides.** A supersede means the user pressed
  Render again. Counting it as a failure makes an impatient user look like a bug.
- **`export_completed` cannot exist without a receipt.** It is emitted only after
  `publish_final_render` reports a committed receipt describing those exact bytes. A finished
  render is not an export.

Failure breakdown (not the denominator): `export_failed` by `failure_class`, and
`render_finished{status=failed}` by `failure_class` for the wider render surface.

## Wall 5 — Fatal crash-free sessions

```
                distinct session_id with a FATAL signal
    1  −   ──────────────────────────────────────────────
              distinct session_id with session_started

  fatal signals:
    · $exception     where  properties.fatal = true
    · error_reported where  properties.fatal = true
    · desktop_error  where  properties.fatal = true
    · process_gone   where  properties.session_fatal = true
    · unclean_timeout — see below
```

```sql
--  The denominator is DISTINCT session_id, NOT count(session_started) rows.
--  At-least-once is the honest guarantee for the session announcement: the backend can
--  accept and the response can be lost on reload, so ONE session can produce TWO start
--  rows. Counting rows makes that duplicate HIDE the failure — 1 distinct fatal session
--  over 2 start rows reads 50% crash-free instead of 0%. Verified live against a
--  duplicate-start fatal fixture by scripts/analytics_wall5_check.py.
--
--  This is the EXACT text scripts/analytics_query.py executes. Three artefacts stating one
--  formula and only one of them being right is how this defect survived six review rounds.
WITH per_session AS (
    SELECT
        properties.session_id                          AS session_id,
        countIf(event = 'session_started')             AS start_rows,
        countIf(event = 'session_started') > 0         AS has_start,
        countIf((event = '$exception'     AND properties.fatal = true)
             OR (event = 'error_reported' AND properties.fatal = true)
             OR (event = 'desktop_error'  AND properties.fatal = true)
             OR (event = 'process_gone'   AND properties.session_fatal = true)) > 0 AS has_fatal
    FROM events
    WHERE properties.session_id IS NOT NULL
    GROUP BY session_id
)
SELECT
    countIf(has_start)                     AS start_sessions,
    sum(start_rows)                        AS start_rows,
    countIf(has_start AND has_fatal)       AS fatal_sessions,
    countIf(NOT has_start AND has_fatal)   AS fatal_sessions_with_no_start
FROM per_session
```

`fatal` is emitted, not inferred. It is set where the code KNOWS the answer, because nothing
downstream can recover it later:

| source | fatal | why |
|---|---|---|
| React ErrorBoundary | **true** | the render tree is gone; the user is on the crash screen |
| `window.onerror` / `unhandledrejection` | false | the app kept running |
| backend unhandled route error | false | the request 500s, the app lives |
| `desktop_error{source: fatal / main-uncaught / renderer-gone}` | **true** | the shell is going down |
| `process_gone{process: 'renderer'}` | `session_fatal: true` | the renderer IS the window |
| `process_gone{process: 'gpu' / 'utility'}` | `session_fatal: false` | a child died, the app did not |

Good ≥ 99.5% · Bad < 98.5% · Join `session_id`

**The denominator is STARTS, never ends.** `session_ended` fires on `before-quit` and on
`pagehide`, and a hard crash reaches neither — so using ends as the denominator removes
precisely the sessions that crashed.

`unclean_timeout` is a **query, not an event**: a `session_started` with no matching
`session_ended`, no fatal `$exception` and no session-fatal `process_gone`, once a fixed
lateness window has passed. Nothing local can emit it — the backend is already stopped by
then, and if main died there is nothing left to run at all.

The next launch enriches it directly. `app_launch_started` carries:

- `previous_exit` — `clean` when the last session shut down through `before-quit`, **`crash`**
  when its marker was still `open`, `unknown` when there was no previous launch at all. A
  SIGKILL and a segfault leave the marker in exactly the same state, so `crash` is reported as
  the honest superset rather than guessing between `crash` and `kill`.
- `prior_session_id` — the session that died. Wall #5's numerator counts DISTINCT session ids,
  so without this an unclean shutdown is a death that cannot be attributed to a session.

Report the **non-fatal** error-free rate separately —
`1 − distinct(session_id with a non-fatal $exception) / distinct(session_id with session_started)`.
It shares Wall 5's denominator, so it takes the same correction for the same reason: at
at-least-once announcement, counting start ROWS lets a duplicate hide a failure. Mixing a
handled toast into the crash rate is what made the original formula unusable.

---

## Alongside, labelled: the build feed

Not product health. A rising number here is demand, not a defect, and mixing the two makes
both ambiguous.

```
weekly count of distinct install_id behind:
    capability_missing · api_key_missing · unrecognized_tool_requested
    agent_routed_around_us · agent_ffmpeg_freehand
```

None of these are emitted yet — they are P1. The row is here so the board has a place for
them rather than growing a sixth wall later.

---

## Reading the numbers safely

- **Check telemetry health first.** Every event can carry `telemetry_dropped_props`,
  `telemetry_unknown_events` and `telemetry_send_failed`. They ride out on the next event that
  successfully sends, because a dead sink cannot report its own death. If any of them is
  non-zero, fix the sink before trusting anything above it.
- **A denominator that changes silently is worse than a missing metric.** If
  `session_started` or `render_queued` volume moves without a release, suspect the pipeline,
  not the users.
- **Geo is on.** `$geoip_country_code` and timezone are collected, deliberately (the SDK
  defaults them off). Numeric shapes stay bucketed — city plus an exact timestamp plus an
  exact file size is a fingerprint; buckets are what keep geo cheap to carry.
- **`app_opened` is joinable only when the shell owns the backend.** It fires inside
  `create_app()`, where there is no HTTP request to inherit a session from, so it reads
  `OPENNOLAN_SESSION_ID` — which Electron sets on the backend it SPAWNS. In the packaged app
  that is always the session that started it. In dev, `scripts/dev run` starts a backend the
  shell then reuses, and that backend genuinely was not started by any session: `session_id`
  stays null rather than being attributed to one that did not start it. It carries no
  `project_id` either, and cannot — no project exists at boot. Use `session_started` as the
  session denominator; `app_opened` counts backend boots, which is not the same thing.
- **The real-network delivery check is DEFERRED, not passing.** Plan §12D originally listed a
  Playwright layer asserting against the dev project over the real network; it could not run
  as written and is now marked deferred there, with the manual command sequence that replaces
  it. Automated coverage of delivery is a fake sink only.
- **The packaged reporting path is still unproven.** `env: packaged` has never been observed
  once in the project's whole history. Until one packaged event lands, treat every packaged
  number as untested plumbing rather than as a measurement.
