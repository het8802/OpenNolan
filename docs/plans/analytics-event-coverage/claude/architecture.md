# Architecture — full event coverage, agent-readable PostHog, taxonomy-driven E2E

**STATUS: BUILT · claude · companion to [`plan.md`](plan.md) (APPROVED, rev 7)**

Built for **all three tiers**, not the approved P0 scope: the human widened it after approval.
What that produced, what was built smaller and why, and the recomputed volume budget are in
[`implementation-report.md`](implementation-report.md).

Reasoning, defects and review history live in `plan.md`. This document is the shape:
what the paths look like today, what they look like after, and the order to build in.

---

## 1. Today — four sources, one gate, and nothing coming back

```text
  ELECTRON MAIN (node)            RENDERER (chromium)       BACKEND (python)
  desktop/main.js                 web/src/analytics/        server/
  ────────────────                ──────────────────        ────────────────
  :842 session_started            :25 resolveSessionId()    capture()
  :833 app_launch_started            ├─ window.openNolan?     analytics.py:436
  :289 launch_failure                  │    .sessionId
  :660 backend_ready                 │      present → reuse
  :902 process_gone                  └─ absent → MINT :32
  :939 session_ended                      sessionStorage
       desktop_error                            │
        │                                       │ X-ON-Session
        │ raw JSON POST                         ▼
        │                            POST /api/telemetry/events
        │                                  server/app.py:1068
        │                                       │
        │                                       ▼
        │                            validate_event()  analytics.py:130
        │                               :141 taxonomy empty → DROP ALL
        │                               :151 unknown event  → DROP
        │                                    unknown prop   → strip
        │                                       │
        │                                       ▼  _scrub → posthog SDK
        └───────────────────────────────────────┴────────► us.i.posthog.com
                  ▲                                          WRITE ONLY
                  │  never validated                              │
                  └──────────────────────────────────────────┐    │
                                                             │    ▼
                                                    ╔════════╧═════════╗
                                                    ║  no way back     ║
                                                    ║  posthog-python  ║
                                                    ║  cannot read     ║
                                                    ╚══════════════════╝
```

### ★ DIVERGENCE 1 — who registers a session

```text
  desktop/main.js:842   track('session_started', …)   ← the ONLY emitter
  web/.../track.js:32   id = crypto.randomUUID()      ← mints, never announces

  Electron present  → main announces        → session on the register    ✓
  Electron absent   → renderer invents id   → NOTHING registers it       ✗
                      every event labelled with it

  measured: 6 session ids carry events with no session_started, incl.
  4f3044c6 — the only fatal=true $exception in the project.
```

### ★ DIVERGENCE 2 — who passes the taxonomy gate

```text
  renderer + backend  ──► validate_event()  analytics.py:130   GATED
  electron main       ──► postToPostHog()   raw JSON           UNGATED

  desktop_error declares [app_version, arch, fatal, os, packaged, source]
  desktop/main.js:233-234 sends  message (500ch) + stack (8000ch)
                                 ▲
                                 └─ undeclared, unvalidated, on the wire today
```

### ★ DIVERGENCE 3 — who owns the install id

```text
  server/settings.py:124        desktop/main.js:157
    open(path, "x")               writeFileSync(…, {flag:'wx'})
    └─ EEXIST →                   └─ throws →
         read().strip() or did          read().trim() || minted
                          ▲                              ▲
                          └──────── falls back to ITS OWN id ─┘

  inode is created BEFORE the bytes are written, so the loser of that
  window reads empty and invents a second id for one launch.
```

---

## 2. The publish race, as a timeline

Ordering *is* the design here, so it gets a timeline rather than prose.

```text
  TODAY — create-then-write, two readers

  t0   A: open("x")  ── inode exists, 0 bytes ──┐
  t1   B: open("x")  → EEXIST                   │
  t2   B: read()     → ""  → returns B's own id │  ← two ids, one launch
  t3   A: write(id)  ─────────────────────────  ┘
  t4   A: returns A's id

  and if A dies at t2.5, the file is 0 bytes FOREVER
     → rev 2's "retry until non-empty" would spin here on every boot
     → installId() is synchronous during boot ⇒ the app never starts


  AFTER — write-then-publish, atomically

  t0   A: write tmp_A + fsync        (invisible: not at the final name)
  t1   B: write tmp_B + fsync        (invisible)
  t2   A: link(tmp_A, install_id)    → OK      ── the file APPEARS complete
  t3   B: link(tmp_B, install_id)    → EEXIST
  t4   B: read(install_id)           → A's id  ── guaranteed whole
  t5   both unlink their tmp in `finally`

  link() and NOT rename(): rename REPLACES, so both would "win" and the
  later write would silently overwrite the id.
```

---

## 3. After — one gate, one register, one publisher, and a way back

```text
   ELECTRON MAIN            RENDERER               BACKEND
   ─────────────            ────────               ───────
   direct events            mint id                capture()
        │                   ├─ shell id? reuse          │
        │                   └─ else MINT + ANNOUNCE     │
        │                        (isolated request,     │
        │                         pending marker)       │
        │                             │                 │
        ▼                             ▼                 ▼
   ┌─────────────────────────────────────────────────────────┐
   │  merged taxonomy   schemas/analytics/*.json             │
   │    install auth project asset agent editor preview      │
   │    render export error feedback  + _envelope            │
   │                                                         │
   │  ALL-OR-NOTHING merge. any parse failure ⇒ {} ⇒ every    │
   │  event dropped (fail-closed) + a one-time local log,     │
   │  because no event can carry the counter out              │
   └──────────────────────────┬──────────────────────────────┘
                              ▼
   ══════ ALL THREE SOURCES CONVERGE — one validator, one scrubber ══════
                              │
                              ▼
                   per-source budget, S7
                     backend_noncritical
                   + electron_noncritical
                   + Σ(critical reserves)   ≤ 100
                     expected session       ≤ 40
                              │
                              ▼
                     us.i.posthog.com  (write, phc_tTqiU…)

   ─────────────────────── and now the loop closes ───────────────────────

   agent / E2E ──► scripts/analytics_query.py
                     key ← Keychain (phx_…, query:read, project 544720)
                     POST us.posthog.com/api/projects/544720/query/
                       ← different host, different credential
                     poll to timeout; NO exit-0 skip on the ON path
                              │
                              ▼
                     S4a payload conformance   (declared props, types, enums)
                     S4b variant matrix        (declared variants exercised)
                     S2  Wall 5 CTE verbatim   (duplicate start ⇒ still 0%)
```

---

## 4. Surface table

| `file:line` | Change |
|---|---|
| `schemas/analytics_events.json` | split into `schemas/analytics/*.json` + `_envelope.json` |
| `server/analytics.py:70` | merge loader, all-or-nothing |
| `server/analytics.py:46-47`, `:62-64` | stale comments asserting the rejected fail-open premise |
| `server/analytics.py:380` | destination line gains taxonomy-load status |
| `server/settings.py:124-126` | `or did` → hard-link publish + errno policy |
| `desktop/main.js:96` | merge loader; **validate direct events** |
| `desktop/main.js:159` | `\|\| minted` → hard-link publish + errno policy |
| `desktop/main.js:233-234` | raw `message`/`stack` → `exception_class`, `top_frame`, `stack_hash` |
| `web/src/analytics/track.js:31-34`, `:117` | announce on mint, pending marker cleared on acceptance |
| `server/app.py:1068` | isolated announcement path returning a real accepted count |
| `scripts/dev:664-681` | analytics-ON smoke mode; reject the known production key |
| `docs/analytics-dashboard.md:147-151`, `:172-174` | denominators → distinct `session_id` + explicit CTE |
| `docs/analytics-dashboard-guide.md:261-264` | same correction |
| `tests/contracts/test_analytics_taxonomy.py:29` | merged load path |
| **new** `scripts/analytics_query.py` | readback client |
| **new** `web/src/analytics/track.test.js` | session announce cases |

**Deliberately untouched:** `_scrub`, the deny-by-default enum gate, the four render
instrumentation points, `route_caches()`, and every P1/P2 catalog row.

---

## 5. Build order

```text
  S1  taxonomy split + merge + fail-closed log     ← everything reads it
   │
   ├── S2  readback client                     ← every later step
   │    │                                         verifies through this
   ├── S6  install-id hard-link publish             ← S2's join key
   │
  S3  analytics-ON smoke mode                       ← needs S1 + S2
   │
   ├── S4a payload conformance
   ├── S4b variant matrix (+ non-paid agent fixture)
   ├── S5  session announce  ← asserted by S2, not by a unit test
   └── S7  per-source budget
   │
  S8  the 49 events, one family per round
      install → auth → project → asset → editor → render/export
              → errors → feedback → agent (last; only one needing fixtures)
```

---

## 6. Three things the diagrams make obvious

**The whole defect surface is upstream of PostHog.** Every divergence in §1 is a
disagreement between two local processes about who owns something — the session
register, the taxonomy gate, the install id. Nothing here is a PostHog problem, which
is why none of it was visible from the dashboard for four review rounds.

**Electron is the odd source out three separate times**, and always in the same
direction: it registers sessions nobody else can, bypasses the gate everyone else
passes, and mints an id the backend also mints. That is one architectural fact —
*main.js is a second, unsupervised reporter* — showing up as three defects. §3's
convergence bar is the fix for all three at once.

**The readback is not a testing convenience, it is the missing half of the system.**
Every assertion worth making — did the event arrive, does it carry its declared
properties, does the metric still read 0% after a duplicate start — is a question about
data that has already left the machine. Until `scripts/analytics_query.py` exists, the
only available proof is that a function was called, which is exactly the class of
evidence that let a fatal crash count as zero for four rounds.
