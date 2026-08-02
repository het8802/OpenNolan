# Scheduled automation — designed, deferred

**Status:** DEFERRED — designed, not being built.
**Parent:** [`architecture.md`](architecture.md) (Decision #4)
**Date:** 2026-08-01
**Decision owner:** Het

Everything in the parent plan runs on demand: an agent runs a tier, a reviewer is
dispatched, CI fires on a PR. Three jobs don't fit that shape — they need to run
on a clock, whether or not anyone asked.

Het's call: **not now.** This file exists so the design isn't re-derived later,
and so nobody wires a cron job that quietly spends money.

---

## What would run on a schedule, and why it can't run on demand

```
  ON DEMAND (built / planned)              ON A CLOCK (this file)
  ───────────────────────────              ──────────────────────
   FAST   pre-commit                        PROVIDER tier
   FULL   before review, PR CI                └ paid, slow, flaky — can't gate
   APP    before review, PR CI                  a PR on it, but it still has to
   MEDIA  PR CI when render code                run sometime or it rots
          changed
                                            WORKTREE REAPING
   review dispatched on worker_done           └ nothing "finishes" to hang it on;
                                                 the trigger is elapsed time
   reap   manual: scripts/dev reap
                                            PACKAGING CHECK
                                              └ a DMG build nobody watches until
                                                release day
```

Each one shares the same property: **no event marks the right moment**, so it
either runs on a timer or it never runs.

---

## The three jobs

### 1. PROVIDER tier

Real calls to Replicate, ElevenLabs, Anthropic. Never on a PR — cost and
flakiness. But if it only runs manually, it runs never, and a provider API change
is discovered by a user.

```
  nightly (or weekly)
    ├── explicit provider list — not "everything installed"
    ├── a hard spend cap; abort at the cap, report what was skipped
    ├── separate test credentials, never the dev .env
    ├── record provider + model version with each result
    └── verdict per provider: skipped | passed | failed
```

**The failure mode to design against:** a retry loop against a paid API at 3am.
The cap must be enforced by the runner, not by the prompt.

### 2. Worktree reaping

`scripts/dev reap` with the parent doc's predicate — merged + clean + idle
removes, merged + clean + running skips and reports, everything else is reported
only. On a schedule it becomes maintenance instead of a chore.

```
  daily
    └── scripts/dev reap --json
          ├── removed: [...]
          ├── skipped (running): [...]   → after N skips, flag
          └── ignored (unmerged/dirty): [...]  → report at 14 days idle
```

Lowest risk of the three, and the one with a measured cost of not doing it:
568 MB of stale trees today.

### 3. Packaging check

`npm run dist:dir` plus the containment checker. Catches a broken build weeks
before a release tag, instead of on release day.

Weekly is enough. Notarization stays manual — it needs credentials that should
not sit in an unattended runner.

---

## How it would be wired

Orca automations, not cron — the runner already exists and reports into the same
place as everything else.

```
orca automations create \
    --name "Nightly provider + reap" \
    --trigger daily --time 02:00 \
    --provider codex \
    --repo id:<OpenMontage> \
    --prompt "<the job>" \
    --disabled                 ← always start disabled, verify by hand first

orca automations run <id>      manual trigger while testing
orca automations runs --id <id>  history
```

`--repo` gives each run a fresh worktree; `--workspace` reuses an existing one.
For the reaper, use `--workspace` — a reaper that creates a worktree in order to
delete worktrees is its own joke.

---

## Why it's deferred

Three reasons, in order:

1. **Nothing to schedule yet.** `scripts/dev` does not exist. There is no
   PROVIDER tier, no `reap`, no CI. A schedule pointed at missing commands is
   noise.
2. **Unmeasured spend.** Nobody has costed one cross-review pass yet, let alone a
   nightly provider sweep. Turning on recurring spend before the one-off number is
   known is how a bill surprises you.
3. **Unattended agents are the highest-risk mode.** Everything else in the parent
   plan has a human or a coordinator watching. These don't. They should be the
   last thing enabled, not an early convenience.

---

## What would change the decision

| Enable | When |
|---|---|
| Reaping | Phase 2 ships `scripts/dev reap` and it has run clean manually a few times |
| Packaging check | a broken build is discovered at release time even once |
| PROVIDER tier | Phase 4's cost instrumentation has produced real numbers **and** a spend cap is enforced by the runner |

Enable them one at a time, each `--disabled` first, each verified with
`automations run` by hand before the schedule is allowed to fire.
