# Wiring this repo for AI-agent development — merged plan

**Status:** PLAN — nothing built yet.
**Merged from:** [`../claude/architecture.md`](../claude/architecture.md) · [`../codex/architecture.md`](../codex/architecture.md)
**Date:** 2026-08-01
**Decision owner:** Het

Claude and Codex planned this independently and landed on the same architecture:
sealed worktrees, one shared command surface, cross-provider review, Orca owns
coordination, the human owns merge. This doc keeps the strongest half of each and
says which. Where they disagreed, the winner and the reason are marked `★`.

---

## Who this is for

Two readers, one document.

- **Het**, deciding what to approve and what gets built first.
- **An AI agent** implementing this, or landing in a worktree tomorrow and needing
  to know how the repo expects it to behave.

Every claim points at a real `file:line`. If a number has drifted, trust the
function name and re-grep.

---

## The goal in one picture

```
   HET (manager)
    │  "fix these two issues"
    ▼
 Orca coordination run
    │
    ├──► claude, worktree A ──► builds ──► fast checks ──► submits SHA ──┐
    │                                                                    │
    └──► codex,  worktree B ──► builds ──► fast checks ──► submits SHA ──┤
                                                                         ▼
                          ★ cross-provider review, bound to that exact SHA
                            A's commit → codex     B's commit → claude
                            reviewer works in a clean review/<sha> tree
                                                                         ▼
                                  merge-mode tests (base merged in first)
                                                                         ▼
                                             PR ──► HET approves and merges
```

Four rules carry the whole design. Everything else is plumbing.

1. **A worktree is a sealed box** — own branch, own Python env, own ports, own
   data dir. Nothing writable is shared.
2. **One command surface, many callers** — agents, git hooks, reviewers, and CI
   all run `scripts/dev`. A check that only lives in CI can't be run locally, so
   agents will guess.
3. **The author never reviews itself** — claude writes, codex reviews, and back.
4. **A review belongs to one commit SHA** — a new commit makes the old review
   stale automatically.

---

## Step zero: this repo cannot run its own tests right now

Before any phase below, one thing has to be true that currently isn't.

```
  $ ls .venv                    → does not exist
  $ python3 -m pytest           → No module named pytest
  $ cat requirements-dev.txt    → requirements.txt + pytest + pytest-asyncio
                                  (nothing else — no linter, no formatter)
```

`.claude/settings.local.json` is full of allowlisted `./.venv/bin/python -m pytest …`
commands pointed at a venv that no longer exists. Orca's per-repo setup script is
empty, so a new worktree inherits nothing:

```
  orca repo show --repo id:<OpenMontage> --json
    hookSettings.scripts.setup = ""
```

There are **74 Python test files** (`tests/contracts` 34, `tests/tools` 34,
`tests/qa` 6) and **14 web test files**. Nobody knows if they pass, because
nothing can run them.

> **"Build a comprehensive test suite" starts as "make the existing 88 files
> green and hold them there."** Writing new tests before the old ones run is
> building on sand.

---

## Current state: what's already here

Real foundations — an implementing agent should reuse these, not rebuild them.

| Exists | Where | Note |
|---|---|---|
| Python suite | `Makefile:43` | `pytest tests/ -v` |
| Web suite | `web/package.json:11` | `vitest run` |
| Health endpoint | `server/app.py:261` | `/api/health` — the readiness probe |
| Per-worktree data root | `lib/app_paths.py:41` | `_REPO_ROOT` derives from file location |
| `OPENNOLAN_HOME` override | `lib/app_paths.py:52` | data isolation needs **zero** code |
| Containment check | `scripts/verify_containment.sh` | OPN-10, executable |
| Human merge gate | `.github/CODEOWNERS:2` | `* @het8802` — already enforced |
| Playwright | `desktop/package.json` devDeps | `@playwright/test` ^1.61.1 — **zero specs written** |
| UI session analyzer | `scripts/debug_session.py` | bounded report from a recording |
| Analyzer over HTTP | `GET /api/debug/sessions/{id}/analyze` | same data |
| Backend log tee | `.agents/tools/backend-log` | → `.agents/tools/logs/backend.log` |

And the gaps:

| Missing | Evidence |
|---|---|
| PR CI | `.github/workflows/` has only `release-mac.yml` (tag-triggered) |
| Real lint | `Makefile:73-77` is four `py_compile` calls on four files |
| Any linter/formatter | not in `requirements-dev.txt`; no `pyproject.toml`/`ruff.toml` |
| Worktree setup | no script, and Orca's setup hook is `""` |
| Repo-owned git hooks | no `.githooks/`, `core.hooksPath` unset |
| App smoke test | Playwright installed, no `*.spec.js` anywhere |
| Review ↔ commit link | nothing records which SHA was reviewed |

---

## The one bug that breaks parallel agents

Data isolation already works. **The port is the only genuinely global thing.**

```
┌─ worktree A — claude ──────────┐  ┌─ worktree B — codex ──────────┐
│ ./run-desktop --dev            │  │ ./run-desktop --dev           │
└───────────────┬────────────────┘  └───────────────┬───────────────┘
                └────────────────┬──────────────────┘
                                 ▼
   vite dev server                          web/vite.config.js:9
     port: 5173        ← hard-coded    →  B: EADDRINUSE, dev server dies
     proxy /api → :8000                     vite.config.js:12
     (run-dev:19-20 hard-codes the same pair)
                                 ▼
   Electron boot()                          desktop/main.js:623
     :627  backendPort = 8000               ← DEV branch, hard-coded
     :628  probeHealth(8000) → true         (A's uvicorn answers)
     :629  if (!alreadyUp)   → false        so B spawns NO backend
                                 ▼
        ╔══════════════════════════════════════════════════════╗
        ║  B's window is served by A's Python process.         ║
        ║  Codex "tests its own change" and is looking at      ║
        ║  Claude's code. No error is printed anywhere.        ║
        ╚══════════════════════════════════════════════════════╝
```

`:628-629`'s reuse-if-healthy check is **correct once the port is per-worktree** —
one worktree, `run-dev` already up, Electron reuses it instead of double-spawning.
Today it's the bug; after the fix it's the feature. Do not delete it.

---

## Design decisions

### D1 — One worktree per concern ★ Codex

An independent issue gets a **top-level** worktree based on the target branch. A
child worktree only when the change depends on another unfinished change.

```
origin/<base>
  ├── feat/123-caption-fix        author worktree     (long-lived)
  ├── review/123-8f27a1           exact SHA, read-only (temporary)
  └── merge-test/123-8f27a1       feature merged into base (temporary)
```

Review and merge-test trees are disposable. They must never become a second place
the feature gets edited.

**Why the reviewer gets its own tree** (Claude proposed reusing the author's):
a fresh `review/<sha>` checkout is the only way to guarantee the reviewer sees
exactly what was committed. Reusing the author's tree lets uncommitted files fool
the review — and once reviews are SHA-bound (D3), reusing it is simply
inconsistent. Costs one extra `scripts/dev setup` run; worth it.

### D2 — The author never reviews itself ★ both agreed

```
claude wrote it  ──► codex reviews
codex wrote it   ──► claude reviews
anything else    ──► a different provider reviews
```

If the opposite provider is unavailable, the review is **blocked**. The author
never silently becomes its own reviewer.

### D3 — A review binds to a commit SHA ★ Codex

Every review record names the full SHA. A later commit invalidates it.

```
commit A reviewed → pass
        │
        └── commit B lands
                     │
                     └── the old review must NOT count for B
```

Enforced twice: a `pre-push` hook locally, and a required `review-current` CI
check. Without this, a reviewer approves, the author pushes two more commits, and
the PR still carries a green review for code nobody read.

### D4 — Scripts check, Orca coordinates ★ both agreed

Repo scripts set up worktrees, run the app, run tests, and collect evidence. They
never decide which agent works next.

Orca already owns Runs, Tasks, Dispatches, `worker_done`, `escalation`,
`question`, `merge_ready`, and decision gates. **Do not build a second
orchestrator.** GitHub owns the final merge checks.

---

## Piece 1 — `scripts/dev`, one command surface ★ Codex

One entry point, called by agents, hooks, reviewers, and CI. Bash or Python —
implementer's call.

```
scripts/dev doctor          what is ready / missing, and how to fix it
scripts/dev setup           prepare this worktree (idempotent)
scripts/dev run             start the app on this worktree's ports
scripts/dev stop            stop only this worktree's processes
scripts/dev test <tier>     see Piece 3
scripts/dev smoke           Playwright: prove the app starts and works
```

Every command supports: readable human output, `--json` for agents, honest exit
codes, **no paid provider calls unless asked**, and redaction of keys, tokens,
user paths, and private project content (this is a public repo).

Trimmed from Codex's nine: `debug-bundle` (the analyzer already exists — see
Piece 5) and `review status` (belongs in Phase 4, not the first surface).

### What `setup` does

```
scripts/dev setup
  ├── doctor first — python, node, npm, ffmpeg, versions
  ├── uv venv --seed .venv          (uv is already installed at /opt/homebrew/bin/uv)
  ├── .venv/bin/pip install -r requirements-dev.txt
  ├── npm ci --prefix web
  ├── npm ci --prefix desktop
  ├── derive a stable port pair from the worktree path
  │      BASE = 20000 + (crc32(pwd) % 10000)
  │      verify both are free before claiming them
  ├── write .env.worktree  (gitignored):
  │      OPENNOLAN_BACKEND_PORT=…
  │      OPENNOLAN_FRONTEND_PORT=…
  │      OPENNOLAN_HOME=$PWD/.local
  ├── git config core.hooksPath .githooks
  └── print a human summary + a --json result
```

Idempotent: re-running repairs what's missing and never deletes user data.

Then paste `scripts/dev setup` into Orca's repo setup field, so
`orca orchestration worker-start --setup run` runs it for every new worktree.
People doing `git worktree add` by hand run the same command. One code path.

### Isolation contract

Each worktree owns its own: Python venv · port pair · `OPENNOLAN_HOME` ·
projects and generated media · logs, PIDs, test reports, screenshots · browser
session when login state matters.

Shared only if content-addressed and concurrency-safe (large download caches).
**Writable project state is never shared.**

`OPENNOLAN_HOME=$PWD/.local` needs no code change — `lib/app_paths.py:52` already
reads it, and `projects_dir()` (`:57`) hangs off it.

---

## Piece 2 — per-worktree ports (the only real code change)

Three files stop hard-coding, start reading env, keep today's values as defaults
so nothing changes outside a managed worktree.

| File | Line | Change |
|---|---|---|
| `run-dev` | 19-20 | `BACKEND_PORT="${OPENNOLAN_BACKEND_PORT:-8000}"` / `FRONTEND_PORT="${OPENNOLAN_FRONTEND_PORT:-5173}"` |
| `web/vite.config.js` | 9, 12 | `port` and proxy `target` read the same two vars |
| `desktop/main.js` | 627 | `backendPort = +(process.env.OPENNOLAN_BACKEND_PORT \|\| 8000)` |

All three must read the **same** variables. If Electron and Vite disagree, a
window silently connects to another worktree's backend — the exact bug in the
diagram above.

**Verify:** run `./run-desktop --dev` in two worktrees at once. Both windows
open. Change a UI string in A; only A's window shows it.

---

## Piece 3 — test tiers ★ Codex, plus Claude's merge mode

Grouped by cost and purpose, so an agent can pick the cheapest one that answers
its question.

```
FAST      format, lint, schemas, changed-area unit tests      ~seconds
   │      → runs pre-commit
   ▼
FULL      all 74 python + 14 web tests, web build,            ~minutes
   │      containment check, ffmpeg tests on local fixtures
   │      → runs before review and in PR CI
   ▼
APP       backend + frontend + Playwright smoke flows         ~minutes
   │      → runs before review and in PR CI
   ▼
MEDIA     ffmpeg / render checks, synthetic fixtures only     slower
   │      → PR CI when render code changed; nightly otherwise
   ▼
PROVIDER  real Replicate / ElevenLabs / Anthropic calls       paid + flaky
          → NEVER on a PR. Manual or nightly, with a budget.
```

The MEDIA and PROVIDER tiers are why five beats three: this is a video tool that
calls paid, non-deterministic APIs. Without a home, those tests either creep into
FULL (expensive, flaky CI) or never run at all. `tests/eval/` already holds
`bench_runner.py` plus `golden_scenarios/` and `golden_outputs/` — that's the
MEDIA/PROVIDER material, currently orphaned.

**PROVIDER runs require:** explicit provider selection · a maximum spend ·
separate test credentials · recorded provider/model versions · an honest
`skipped` / `passed` / `failed` result.

### Merge mode — a modifier, not a tier ★ Claude

Answers "green on my branch" vs "green after merging into what I forked from".

```
scripts/dev test full --merge
  ├── git fetch origin <base>
  ├── git merge origin/<base> --no-commit --no-ff
  │      └── conflict → FAIL loudly, never test a broken tree
  ├── run the requested tier
  └── git merge --abort            (always — leave the tree as found)
```

### ⚠️ Every tier must set a scratch `OPENNOLAN_HOME`

`create_app()` reloads the repo `.env`. Without a scratch home, the auth-gate test
**spawns a real, billable agent turn**. This applies to local runs and CI alike.
Tests must also never touch a user's existing projects.

### Note on FAST: there is no linter to run yet

`Makefile:73-77` is four `py_compile` calls. `requirements-dev.txt` has no ruff,
no black, no flake8, and there's no `pyproject.toml`. Adding one is a **new
dependency decision** — see Open Questions.

---

## Piece 4 — app smoke via Playwright ★ Codex

Claude proposed a bash `smoke.sh`. Wrong: `@playwright/test` ^1.61.1 is already
in `desktop/package.json` devDependencies with zero specs, and `RULES.md` already
says E2E "belongs in a Playwright suite against the running app". Use what's
installed and follow the rule the repo already wrote down.

```
scripts/dev smoke
  1. start the backend on this worktree's port
  2. wait for /api/health                      server/app.py:261
  3. start Vite on this worktree's port
  4. open the app
  5. create or open a throwaway project
  6. exercise one read path and one write path
  7. fail on unexpected console or network errors
  8. on failure: save screenshots, logs, and a trace
```

Isolated temporary `OPENNOLAN_HOME`. Never a real API key.

This is what lets a reviewer say "it runs", not just "the tests pass".

---

## Piece 5 — debugging: reuse, don't rebuild ★ Claude

The repo already has better debug tooling than most. An implementing agent must
use these instead of writing new ones:

| Tool | Use |
|---|---|
| `python scripts/debug_session.py latest` | bounded report from a UI session recording |
| `GET /api/debug/sessions/{id}/analyze` | same, over HTTP |
| `.agents/tools/backend-log` | tees uvicorn → `.agents/tools/logs/backend.log` |
| `browse` / `gstack` skills | headless browser QA |
| Orca's built-in browser | a reviewer can drive the Studio UI itself |

> ⚠️ **Never `Read` or `cat` a raw `.ndjson` session file.** A 2-minute session is
> ~2000 lines / ~85k tokens and will blow your context. It is built to be
> queried. Use the analyzer, then pull only the slice it points at.

The only genuine gap is **redaction**: failure evidence leaving a worktree (CI
artifacts, PR comments) must be scrubbed of keys, tokens, user paths, and private
project content. Add that as a flag on the existing analyzer, not as a new
`debug-bundle` command.

---

## Piece 6 — git hooks ★ Codex's three, with Claude's caveat

**Git** hooks, not Claude Code hooks — codex, cursor, and a human typing
`git commit` all need them. Claude Code hooks only fire for Claude.

Checked into `.githooks/`, enabled by `core.hooksPath` in `scripts/dev setup`, so
they travel with every worktree.

```
pre-commit    scripts/dev test fast
              └─ FAIL → commit is BLOCKED. This is the real gate.

post-commit   record the new SHA; enqueue a review request.
              └─ must NOT wait on an agent. If Orca is down, leave a
                 pending request for the next coordinator sweep.

pre-push      is there a current review for this exact SHA?  (D3)
              plus the medium-cost tier.
              └─ FAIL → push blocked.
```

Claude's original plan had only `post-commit` running the gate and exiting 1 —
which does nothing, because the commit already happened. Enforcement has to sit
at `pre-commit` and `pre-push`.

Hooks can be skipped (`--no-verify`) and don't run on GitHub. **PR CI enforces the
same rules again.** Hooks are for fast feedback, not authority.

---

## Piece 7 — review flow on Orca

```
  orca orchestration run-create --objective "<sprint>"
  orca orchestration task-create --spec "<issue>"                     → task_a
  orca orchestration task-create --spec "review task_a" --deps '["task_a"]'

  orca orchestration worker-start --task task_a \
      --worktree new-top-level --name 123-caption-fix \
      --agent claude --setup run        ← runs scripts/dev setup
                          ▼
       author builds; pre-commit gate runs on each commit;
       author picks ONE commit and submits it:
                          ▼
  orca orchestration send --type worker_done \
      --task-id task_a --dispatch-id <d> --outcome succeeded \
      --files-modified "server/render_proxies.py,tests/…" \
      --body "<what changed, what remains risky, SHA 8f27a1…>"
                          ▼
  coordinator:
  orca orchestration check --wait \
      --types worker_done,escalation,question --timeout-ms 900000
                          ▼
       reads author provider + SHA → picks the OPPOSITE provider
                          ▼
  worker-start --task <review_a> --worktree new-top-level \
      --name review/123-8f27a1 --agent codex --setup run
                          ▼
       reviewer, read-only, on that exact SHA:
         scripts/dev test full --merge
         scripts/dev smoke
         git diff $(git merge-base HEAD origin/<base>)
                          ▼
      ┌───────────────────┴────────────────────┐
  changes_requested                          pass
      ▼                                        ▼
  findings return to the AUTHOR          gh pr create --base <base>
  (max 2 rounds → then a manager         orca orchestration send --type merge_ready
   decision gate, never an                       ▼
   endless agent loop)                    HET reviews the PR.
                                          CODEOWNERS blocks merge without him.
```

**Do not review every commit.** The author marks one commit ready. Reviewing each
checkpoint costs roughly 10× the tokens for the same findings. If per-commit
review is ever wanted, the queue keeps only the newest unreviewed commit per task.

**The reviewer is read-only.** A review `worker_done` reports findings; it does
not authorize the coordinator or the reviewer to edit files. Fixes go back to the
author.

### The review record

Stored as a CI artifact or GitHub check — **not** committed to the feature branch,
unless it contains a lasting design decision, which belongs in normal docs.

```
task + PR id · author provider · reviewer provider
full reviewed SHA
commands run + results
findings: severity, file, line, explanation
dimensions: correctness, tests, architecture, security,
            performance, readability, accessibility (where relevant)
unresolved risks
verdict: pass | changes_requested | blocked
```

---

## Piece 8 — PR CI ★ Codex's shape, Claude's starting size

Codex's seven-job graph is the **target**, not the start. This repo has zero CI
jobs today; going to seven in one step means seven things to debug at once.

**Start with one job** that runs `scripts/dev test full --merge` + `smoke`. Split
a job out the moment it gets slow enough to annoy you, in this order:

```
  start:            one job
                       │
  when slow:      python │ web          (parallel)
                       │
  when render     + media
  work lands:          │
                       ▼
  when reviews    + review-current      ← required by branch protection (D3)
  are automated:       │
                       ▼
  target:  policy ─┐
           python ─┤
           web ────┼──► app-smoke ──► review-current ──► merge-ready
           media ──┤
           security┘
```

`merge-ready` is the single summary check branch protection requires. Cancel
older runs on a new commit (`cancel-in-progress`). If a merge queue is enabled
later, also run on `merge_group`. The existing `release-mac.yml` should eventually
depend on the same deterministic checks before building a tag.

`policy` needs a linter that doesn't exist yet — see Open Questions.

---

## Manager visibility and gates

Het should never have to read an agent transcript to know where things stand.

**Three surfaces, all of which already exist:**

1. **Orca worktree card** — `orca worktree set --workspace-status
   todo|in-progress|in-review|completed --comment "<one line>"`. Updated at every
   meaningful checkpoint: repro, fix, validation, handoff, blocker.
2. **The PR** — problem, owner + provider, architecture choice and why, tests run,
   review verdict + reviewed SHA, risks and rollback, open decisions.
3. **GBrain** — `CLAUDE.md` already mandates a page after any meaningful decision.
   Add one line to the dispatch preamble: *write the GBrain page before sending
   `worker_done`.*

**Agents must stop and open a decision gate before:** ★ Codex

- a public API or stored-data shape change (incl. `schemas/artifacts/*`);
- anything touching authentication, secrets, or the agent sandbox;
- adding a paid provider or a recurring service;
- adding a major dependency;
- deployment, release, or migration behavior;
- an architecture change spanning several parts of the product.

Small implementation choices inside an approved plan do not need a gate.

---

## Rollout

Each phase is verifiable on its own. Don't start the next until the check passes.

### Phase 0 — make the repo runnable at all

Fix step zero. `uv venv --seed`, install `requirements-dev.txt`, then run the
existing suite and **write down what's red**.

**Done when:** `pytest tests/` and `npm test --prefix web` both execute and
produce a known result. Green is the goal; a written list of failures is an
acceptable Phase 0 exit.

### Phase 1 — make worktrees runnable in parallel

`scripts/dev doctor` + `setup`, per-worktree ports (Piece 2), Orca setup hook.

**Done when:** two agents independently start the app, make different UI changes,
and each sees only their own.

### Phase 2 — reliable test commands

The five tiers + merge mode. Replace the four-file `py_compile` lint. First
Playwright smoke spec. Redaction on the existing analyzer.

**Done when:** a fresh worktree runs one command and gets a clear pass or an
actionable failure report with evidence.

### Phase 3 — PR CI

One job running `full --merge` + `smoke`. Required check, stale-run cancellation.
CODEOWNERS stays the human gate.

**Done when:** untested code cannot merge through the normal GitHub path.

### Phase 4 — cross-agent review

Record author provider + SHA in Orca task state. Dispatch the opposite provider
into a clean `review/<sha>` tree. Structured review record. SHA invalidation.
`review-current` CI check.

**Done when:** one claude-authored and one codex-authored change each complete a
cross-provider review with no manual routing.

### Phase 5 — hooks and manager summaries

`pre-commit` / `post-commit` / `pre-push`. Automatic Orca comment + PR summary
updates. Nightly MEDIA/PROVIDER runs with a budget.

**Done when:** Het can understand state, evidence, and open decisions from Orca
and the PR alone.

---

## Deliberately not building

| Skipped | Reconsider when |
|---|---|
| A custom agent scheduler | never — Orca orchestration is exactly this |
| An MCP server | the CLI is stable **and** agents still misuse its output |
| A `debug-bundle` command | the existing analyzer can't be taught to redact |
| Review on every commit | the reviewer demonstrably misses things at that granularity |
| Paid provider tests on PRs | never — nightly with a budget instead |
| A reviewer that edits the author's branch | never — findings go back to the author |
| Automatic merge | never — Het is the merge authority |
| Coverage thresholds | the suite is green and stays green for a week |
| Docker / devcontainer isolation | worktree isolation provably leaks |
| Seven CI jobs on day one | a single job gets slow enough to annoy |
| One shared worktree for unrelated agents | never |

---

## Open questions for Het

1. **Base branch.** Worktrees currently fork from `pdd`. Should agent worktrees
   fork from `main` instead, so `--merge` tests against what actually ships?
2. **A linter.** FAST and the `policy` job need one, and this repo has none
   (`requirements-dev.txt` is pytest only). Add `ruff` — one dependency, format +
   lint in a single fast tool — or keep FAST to tests-only and skip `policy`?
3. **Reviewer strictness.** Does `changes_requested` block the PR from opening,
   or does the PR open with findings attached for Het to judge?
4. **Nightly automation budget.** `orca automations create --trigger daily
   --provider codex` could run FULL + MEDIA + triage across open worktrees. Worth
   the spend, and what's the cap?

---

## Acceptance checklist

Routine multi-agent development is ready when all of these are true:

- [ ] The existing 74 Python + 14 web test files all execute (Phase 0)
- [ ] A new worktree is prepared with one command
- [ ] Two worktrees run the app at once with no port or data leak
- [ ] Every agent can run fast / full / merge / smoke itself
- [ ] Failures produce bounded, redacted evidence
- [ ] PRs run deterministic tests on the **merged** result
- [ ] Claude-authored code is reviewed by Codex, and vice versa
- [ ] Every passing review names the exact current SHA
- [ ] A new commit invalidates the old review
- [ ] Critical findings return to the author, never to the reviewer's own edits
- [ ] Gated change types pause for Het's decision
- [ ] Het remains the required final reviewer and merge authority
