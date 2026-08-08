# Reading the OpenNolan analytics dashboard

**Status: BUILT** — live in PostHog as of 2026-08-06.

This is the reader's guide to the **live dashboard**. The formulas it implements, and the
arguments behind them, are in [`analytics-dashboard.md`](analytics-dashboard.md). Read that one
to know *why* a number is defined the way it is; read this one to know *what you are looking at*
and *when to distrust it*.

| | |
|---|---|
| Dashboard | `OpenNolan — Dev / Internal` |
| URL | https://us.posthog.com/project/544720/dashboard/1964915 |
| PostHog project | `OpenNolan - Dev`, id **544720**, token prefix `phc_tTqiU…` |
| Production project | `OpenNolan`, id **478214**, token prefix `phc_s9P9…` |

---

## First: why the dashboard you were looking at was empty

Nothing was broken. Analytics has been configured correctly the whole time.

When you create a PostHog project it auto-generates **"Your starter dashboard"** with eight
tiles: Active users, Sessions, Pageviews, DAUs, WAUs, Retention, Top referrers, and a
visit-to-interaction funnel. Every one of those is a **website** metric. They ask about
`$pageview`, `$autocapture`, web sessions and referrers.

OpenNolan is a desktop app. It emits `app_opened`, `project_created`, `render_queued`,
`export_completed` and so on — and **none** of the events the starter dashboard asks about. So
that board reads zero forever, no matter how good the instrumentation is. It is asking a question
the app never answers.

```
   what the app sends              what the starter board asks for
   ──────────────────              ───────────────────────────────
   app_opened                      $pageview          <- never sent
   session_started                 $autocapture       <- never sent
   project_created                 web sessions       <- never sent
   render_queued                   referrers          <- never sent
   export_completed
   editor_session_summary          => every tile reads 0, correctly
   $exception
```

The fix is not a code change. It is having tiles that ask about the events the app actually
sends — which is what this dashboard is.

---

## The one line you will want to change

Every tile is a **SQL insight**, and each one carries this line:

```sql
-- internal board; flip to != 'true' for external users
AND toString(properties.internal) = 'true'
```

That is deliberate, and it is worth understanding before you touch it.

**Why the filter exists.** Events carry an `internal` flag so your own usage can be separated
from real users'. Right now **100% of events in this project are `internal: true`**, because the
sentinel file `~/.opennolan-internal` exists on the dev machine. So `= 'true'` is the only filter
that shows anything at all pre-beta. When real users arrive, duplicate the dashboard and flip
this line to `!= 'true'` for a board that describes them instead of you.

**Why it is baked into every query rather than set once on the dashboard.** PostHog dashboard-level
filters **do not apply to SQL insights**. Setting a dashboard filter would look like it worked
and silently do nothing. One line per tile is uglier and honest.

A trap worth knowing: `OPENNOLAN_INTERNAL=0` does **not** turn the flag off. A falsy env var falls
through to the sentinel-file check (`server/analytics.py`), so the file wins. Delete
`~/.opennolan-internal` if you want your own runs to look external.

---

## Tile 0 — Is anything arriving? *(read this first)*

Everything below is meaningless if the pipe is dry, so start here. It lists every event received,
how many installs and sessions it covers, when it last arrived, and telemetry health.

As of 2026-08-06, 14 event kinds and 53 events from 1 install:

```
app_opened             10     $exception               2
app_launch_started      8     backend_ready            2
session_started         8     export_completed         2
project_created         4     render_finished          2
session_ended           4     app_first_run            1
editor_session_summary  3     render_superseded        1
render_queued           3
render_started          3
```

**`minutes_ago`** is your liveness check. A large number means nobody has run the app, not that
telemetry is broken.

**`dropped_props`** is the one to actually watch. It counts properties the taxonomy gate threw
away because they were not declared. Non-zero means the code tried to send something the schema
does not know about, and it vanished. Today: `export_completed` 2, `editor_session_summary` 1,
`render_superseded` 1. That is the counter doing its job, but it also means some intended data is
not arriving. Chase it by diffing the emit site's properties against
`schemas/analytics/*.json`.

Health counters ride out on the **next event that successfully sends**, because a dead sink
cannot report its own death.

---

## What a rate actually is, and how Wall 5 gets it wrong

Three of the five walls are rates. A rate is a fraction, and the vocabulary matters for reading
them honestly:

- **numerator** — the top number. The thing you are counting.
- **denominator** — the bottom number. The "out of how many" you are counting *against*.

Wall 5 asks what fraction of sessions ended badly:

```
                     sessions that crashed        <- numerator
   crash rate  =  ──────────────────────────
                     sessions that started        <- denominator
```

The denominator is a **register**: the list of every session we know began, built from
`session_started`. Crashes are then counted by checking which names on that register *also*
appear in the crash list.

**The defect.** A genuinely fatal crash was recorded on session `4f3044c6`, and that session
never sent a `session_started`. So it is not on the register — and the arithmetic only looks for
crashes among names that are:

```
   register (denominator)        crash list
   ──────────────────────        ──────────
   69ef8cbf                      4f3044c6   <- crashed, not registered
   09c68309
   e9f428c3                      the rate only searches for crashes AMONG
   ecc0aac1                      registered sessions, so this one contributes
   ec7a2b3f                      nothing and the crash count reads 0
   2fc25cb8
   455d9ef6
   40af6a57
```

It is a school reporting "% of students who passed" from a class register: a student who sat the
exam but was never registered cannot be counted. The paper exists; the arithmetic cannot see it.

**Consequence: the crash-free number reads better than reality.** Wall 5 therefore carries a
`fatal_sessions_with_no_start` column, which reads **1** today. When it is non-zero, the headline
rate is optimistic by at least that many sessions.

**How this got missed.** All three reviewers — including me — tested the fragment
`$exception where fatal = true` **in isolation**, where it correctly returns 1. Nobody ran the
wall end-to-end against its own denominator. A predicate passing on its own is not the same as a
metric being computable.

Six session ids in total carry product events without ever emitting `session_started`. The
orphan-session pattern was fixed for the editor case; it persists elsewhere, chiefly for
sessions that begin outside Electron (a plain browser page has no main process to mint the id).

---

## The five walls

### Wall 1 — Activation, 7 days

> Of the people who installed, how many produced a video in their first week?

```
numerator     installs with export_completed within 7 days of app_first_run
denominator   installs whose 7-day window has ALREADY ELAPSED
```

Good ≥ 40% · Bad < 20% · Joins on `install_id`

**Reads `NULL` today, and that is correct.** The one install is still inside its 7-day window, so
the denominator is empty and the tile refuses to state a rate. `installs_still_in_window` tells
you why.

That refusal is the point. Without the elapsed-window guard, somebody who installed yesterday and
has not exported yet counts as a *failure to activate*, which drags the number down for no reason.
**Expect this tile to read empty for the first week of any beta** — that is the guard working, not
a bug.

`first_export` is computed as `min(timestamp)` at query time, not sent as a property: the only
counter available at the emit site lives in memory and empties on every backend restart, so it
would report a "first" export repeatedly.

### Wall 2 — Time to value

> How long from install to first finished video?

Per install: `first(export_completed) − first(app_first_run)`, reported as P50 and P90.

Good P50 < 1 day · Bad P90 > 7 days · Joins on `install_id`

Today: **P50 45 minutes** over 1 install.

Read `installed_never_exported` alongside it. Someone who never exported contributes nothing to
the percentiles, so a fast P50 over a small number of installs is not good news by itself — it
can just mean the only people counted are the ones who succeeded.

### Wall 3 — Agent value, and its price

> Is the agent useful, and what does using it cost?

```
                 agent_turn_completed where doc_changed
useful-turn      OR artifacts_delta > 0
rate        =  ──────────────────────────────────────────
                        agent_turn_started
```

Good ≥ 70% and P50 < $3 · Bad < 50% or P90 > $10 · Joins `turn_id` → `project_id`

**Empty by design today.** No live agent turn has ever been run, because a real turn spends real
money. A zero here means *not yet measured*, not *the agent is useless*. It fills the first time
anyone runs a turn.

Two definitions worth knowing:

- **The denominator is *delivered* turns, not successful ones.** BYOK means the user pays for an
  errored turn too, so a rate conditioned on success would hide exactly the cost that hurts.
- **`agent_turn_completed` fires even when a turn crashes**, because it is emitted from a
  `finally` block in `server/agent_runner.py`. That is why it is the complete delivered-turn and
  cost source. `agent_turn_failed` is emitted from the `except` block *alongside* it — it explains
  *why* a turn failed and is not a second terminal event. Do not treat them as alternatives or
  you will undercount both turns and spend.

### Wall 4 — Export reliability

> When someone asks for a finished video, do they get one?

```
numerator     distinct job_id with export_completed
denominator   distinct job_id with render_queued where publish_intent = true,
              old enough to have reached a terminal state
```

Good ≥ 95% · Bad < 90% · Joins on `job_id`

Today: **100%** — 3 jobs with publish intent, 1 superseded and excluded, 2 exported.

Three details decide whether this number is honest:

- **`render_queued` is the denominator even though it is not a terminal event.** Two real
  publish-intent failures return *before* the render starts. A denominator taken any later
  silently drops them and inflates success.
- **`render_superseded` is excluded from both sides.** A supersede means the user pressed Render
  again. Count it as a failure and an impatient user looks like a bug — the number would read
  66.7% here instead of 100%.
- **`export_completed` cannot exist without a receipt.** It is emitted only after a committed
  receipt describing those exact bytes. A finished render is not an export.

### Wall 5 — Fatal crash-free sessions

> How often does the app die on someone?

```
                    distinct session_id with a FATAL signal
      1  −      ───────────────────────────────────────────
                   distinct session_id with session_started

   fatal signals:  $exception / error_reported / desktop_error where fatal = true
                   process_gone where session_fatal = true
                   unclean_timeout  (see below)
```

**DISTINCT sessions, not start rows.** The renderer's session announcement is at-least-once:
the backend can accept it and the response can be lost on a reload, so one session can produce
two `session_started` rows. Counting rows makes that duplicate *hide* the failure — 1 distinct
fatal session over 2 start rows reads **50% crash-free instead of 0%**. The authoritative query
(with the `starts` CTE) is in `docs/analytics-dashboard.md`.

Good ≥ 99.5% · Bad < 98.5% · Joins on `session_id`

Today: **50%** over 8 sessions — 0 fatal exceptions counted, 4 unclean timeouts. That number
describes dev machines being killed on purpose during testing, not product quality.

**The denominator is STARTS, never ends.** `session_ended` fires on `before-quit` and `pagehide`,
and a hard crash reaches neither — so using ends as the denominator would remove precisely the
sessions that crashed, and the rate would always look perfect.

**`unclean_timeout` is a query, not an event**: a session that started, never ended, and whose
lateness window has passed. Nothing local can emit it — by then the backend is stopped, and if
the main process died there is nothing left to run at all.

`app_launch_started.previous_exit` enriches this for free on the next launch, reading `crash`
rather than `clean`, and `prior_session_id` names the session that died.

**Read `fatal_sessions_with_no_start` before trusting the headline.** See the section above.

---

## Known blind spots, in priority order

1. **A fatal crash on an unregistered session is invisible to Wall 5.** Reads 1 today. Either
   ensure every context that can crash also emits `session_started`, or add the missing sessions
   to the denominator.
2. **The signed packaged path has never delivered a single event.** `desktop_error` has never been
   received in either project's history, and `env: packaged` has only ever arrived from a legacy
   build carrying no envelope. This is the highest residual risk before a first external user:
   the "app won't start" crash path — the worst thing a new user can hit — is entirely unproven.
3. **`dropped_props` is non-zero.** Some properties the code sends are being discarded by the
   taxonomy gate.
4. **Two literal test session ids** (`live-check-0806`, `r4-leak-check`) are in the dev data from
   verification runs. Harmless here, but they inflate session counts slightly.
5. **Two feature-rate walls are not built.** `features_eligible` is P1, so "what fraction of
   people who *could* use feature X did" is not yet computable.

---

## Changing things

- **Editing a tile** — open it from the dashboard, edit the SQL, save. The SQL is commented
  in place, so the reasoning travels with the query rather than living only here.
- **A board for real users** — duplicate the dashboard, flip `= 'true'` to `!= 'true'` in all six
  tiles.
- **Verifying delivery yourself** — `posthog-python` is write-only, so the SDK cannot read events
  back. Use the Query API (`POST /api/projects/544720/query/`) with a **personal** key
  (`phx_…`, scope `query:read`). That is a real secret, unlike the project token.
- **Confirming which project the app is writing to** — the app logs its destination key prefix at
  boot. `phc_tTqiU…` is dev; `phc_s9P9…` is production. A silent fallback to the production key
  is the exact failure that log exists to catch, and it has happened before.
