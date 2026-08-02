# Wiring this repo for AI-agent development — merged plan

**Status:** PLAN, with Phase 0 partly built (see Rollout).
**Merged from:** [`../claude/architecture.md`](../claude/architecture.md) · [`../codex/architecture.md`](../codex/architecture.md)
**Amended by:** [`suggestions-codex.md`](suggestions-codex.md) · [`suggestions-claude.md`](suggestions-claude.md)
**Deferred piece:** [`nightly-automation.md`](nightly-automation.md)
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
                                    push branch, open a DRAFT PR
                                                                         ▼
                          ★ cross-provider review, bound to that exact SHA
                            A's commit → codex     B's commit → claude
                            reviewer works in a clean review/<sha> tree
                                                                         ▼
                          merge-test worktree: base merged in, tiers run
                                                                         ▼
                              mark ready ──► HET approves and merges
```

Four rules carry the whole design. Everything else is plumbing.

1. **A worktree is a sealed box** — own branch, own Python env, own ports, own
   data dir. Nothing writable is shared.
2. **One command surface, many callers** — the author, the git hooks, the
   reviewer, and CI all run `scripts/dev`. A check that lives only in CI can't be
   run locally, so agents invent their own version of it and CI then fails on
   something nobody ran.
3. **The author never reviews itself** — claude writes, codex reviews, and back.
4. **A review belongs to one commit SHA** — a new commit makes the old review
   stale automatically.

---

## Two kinds of check, and only one of them is a command

This distinction runs through the whole document. Conflating them is the most
common way to misread it.

```
  MECHANICAL — a command. Same input → same output. Has an exit code.
  ┌──────────────────────────────────────────────────────────────┐
  │  scripts/dev test fast|full|app|media|provider               │
  │  scripts/dev smoke                                           │
  │                                                              │
  │    ruff · pytest (74 files) · vitest (14 files) · web build  │
  │    verify_containment.sh · Playwright · schema validation    │
  └──────────────────────────────────────────────────────────────┘
        run by: the author · pre-commit · the reviewer · CI
        all four get byte-identical results

  JUDGMENT — reading, not running. No exit code. Not reproducible.
  ┌──────────────────────────────────────────────────────────────┐
  │  right abstraction, or one interface with one implementation?│
  │  did they patch the caller the ticket named and leave five   │
  │    sibling callers broken?                                   │
  │  is editing logic buried in a component instead of interp.js?│
  │    (RULES.md says the latter is wrong)                       │
  │  is the mutator immutable and same-ref on a no-op?           │
  │  readable at 3am? security reasoning? missing test for the   │
  │    branch just added?                                        │
  └──────────────────────────────────────────────────────────────┘
        produced by: the reviewer agent, and Het. Nothing else.
```

**No script produces the second box.** That is the entire reason a second AI
agent exists here — if review were only "run the tests", the answer would be a
CI job, not a reviewer.

The reviewer does both, in order: mechanical first (a red branch is not worth
reading), judgment second.

---

## Step zero: the repo still cannot run its own tests

```
  $ ls .venv                    → does not exist
  $ python3 -m pytest           → No module named pytest
  $ cat requirements-dev.txt    → requirements.txt + pytest + pytest-asyncio
```

`.claude/settings.local.json` is full of allowlisted `./.venv/bin/python -m pytest …`
commands pointed at a venv that no longer exists. Orca's per-repo setup script is
still empty:

```
  orca repo show --repo id:<OpenMontage> --json
    hookSettings.scripts = { setup: "", archive: "" }
                                       ^^^^^^^^^^^^
                            both slots empty — see Piece 1 and Piece 9
```

There are **74 Python test files** (`tests/contracts` 34, `tests/tools` 34,
`tests/qa` 6) and **14 web test files**. Nobody knows if they pass, because
nothing can run them.

> **"Build a comprehensive test suite" starts as "make the existing 88 files
> green and hold them there."** Writing new tests before the old ones run is
> building on sand.

---

## Current state

### Already here — reuse, don't rebuild

| Exists | Where | Note |
|---|---|---|
| Python suite | `Makefile:43` | `pytest tests/ -v` |
| Web suite | `web/package.json:11` | `vitest run` |
| Health endpoint | `server/app.py:261` | `/api/health` — the readiness probe |
| Per-worktree data root | `lib/app_paths.py:41` | `_REPO_ROOT` derives from file location |
| `OPENNOLAN_HOME` override | `lib/app_paths.py:52` | data isolation needs **zero** code |
| Containment check | `scripts/verify_containment.sh` | OPN-10, executable |
| Playwright | `desktop/package.json` devDeps | `@playwright/test` ^1.61.1 — **zero specs** |
| UI session analyzer | `scripts/debug_session.py` | bounded report from a recording |
| Analyzer over HTTP | `GET /api/debug/sessions/{id}/analyze` | same data |
| Backend log tee | `.agents/tools/backend-log` | → `.agents/tools/logs/backend.log` |
| Identity guard | `.githooks/pre-commit`, `.githooks/pre-push` | **BUILT** — see Piece 6 |

### Branch protection — the real state ★ corrects both source plans

Both plans got this wrong in opposite directions. Claude said CODEOWNERS already
blocks merges; Codex said CODEOWNERS enforces nothing. Measured:

```
  gh api repos/het8802/OpenNolan/branches/main/protection

  required_pull_request_reviews        1 approval
    require_code_owner_reviews         true   ← IS enforced. Claude accidentally right.
  enforce_admins                       false  ← why Het can bypass it
  dismiss_stale_reviews                false  ← MISSING — the native half of rule 4
  required_conversation_resolution     false  ← MISSING
  required_status_checks               none   ← nothing to require until CI exists
  allow_force_pushes                   true
```

So this is not "add branch protection", it is "tighten three settings".

### Gaps

| Missing | Evidence |
|---|---|
| PR CI | `.github/workflows/` has only `release-mac.yml` (tag-triggered) |
| A linter | not in `requirements-dev.txt`; no `pyproject.toml`. **Decision: add `ruff`** |
| Real lint target | `Makefile:73-77` is four `py_compile` calls on four files |
| `scripts/dev` | does not exist |
| Worktree setup / teardown | Orca's `setup` and `archive` slots are both `""` |
| App smoke test | Playwright installed, no `*.spec.js` anywhere |
| Review ↔ commit link | nothing records which SHA was reviewed |
| Worktree reaping | 3 stale trees, 568 MB, none merged, none cleaned |

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

An independent issue gets a **top-level** worktree based on `main`. A child
worktree only when the change depends on another unfinished change.

```
origin/main
  ├── feat/123-caption-fix        author worktree     (long-lived)
  ├── review/123-8f27a1           exact SHA, read-only (temporary)
  └── merge-test/123-8f27a1       feature merged into main (temporary)
```

Review and merge-test trees are disposable. They must never become a second place
the feature gets edited.

**Why the reviewer gets its own tree** (Claude proposed reusing the author's): a
fresh `review/<sha>` checkout is the only way to guarantee the reviewer sees
exactly what was committed. Reusing the author's tree lets uncommitted files fool
the review — and once reviews are SHA-bound (D3), reusing it is inconsistent.

**Why merge-test is a third tree, not a merge inside the review tree** (Claude
proposed collapsing them): the review tree must still satisfy
`git rev-parse HEAD == reviewed SHA` when the verdict is published. A merge
mutates that. Three cheap disposable trees beat one tree with two identities.
Provisioning cost is real — see Piece 1's cache sharing.

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

Enforced by GitHub, not by a local hook: the `review-current` status check plus
`dismiss_stale_reviews`. `pre-push` only *warns* — see Piece 6 for why a hook
cannot be the authority here.

### D4 — Scripts check, Orca coordinates ★ both agreed

Repo scripts set up worktrees, run the app, run tests, and collect evidence. They
never decide which agent works next.

Orca owns Runs, Tasks, Dispatches, `worker_done`, `escalation`, `question`,
`merge_ready`, and decision gates. **Do not build a second orchestrator.** GitHub
owns the final merge checks.

---

## Piece 1 — `scripts/dev`, one command surface ★ Codex

One entry point, called by the author, hooks, reviewers, and CI. Bash or Python —
implementer's call.

```
scripts/dev doctor          what is ready / missing, and how to fix it
scripts/dev setup           prepare this worktree (idempotent)
scripts/dev run [--ttl 30m] start the app on this worktree's ports
scripts/dev stop [--all]    stop this worktree's processes (or every worktree's)
scripts/dev test <tier>     see Piece 3
scripts/dev smoke           Playwright: prove the app starts and works
scripts/dev reap            remove finished worktrees — see Piece 9
```

Every command supports: readable human output, `--json` for agents, honest exit
codes, **no paid provider calls unless asked**, and redaction of keys, tokens,
user paths, and private project content (this is a public repo).

Trimmed from Codex's original nine: `debug-bundle` (the analyzer already exists —
Piece 5) and `review status` (Phase 4, not the first surface).

> ⚠️ **Naming collision.** Orca's `worker-start --setup run` means *"execute the
> repo's setup hook."* It is unrelated to `scripts/dev run`. Two different `run`s.

### `setup` — prepares, never starts

```
scripts/dev setup
  ├── doctor first — python, node, npm, ffmpeg, versions
  │      resolve every tool from PATH. Never hardcode /opt/homebrew.
  ├── uv venv --seed .venv
  ├── .venv/bin/pip install -r requirements-dev.txt      (now includes ruff)
  ├── npm ci --prefix web
  ├── npm ci --prefix desktop
  ├── derive a stable port pair from the worktree path
  │      BASE = 20000 + (crc32(pwd) % 10000)
  │      verify both are free — a hash is a SUGGESTION, not a reservation
  ├── write .env.worktree  (gitignored):
  │      OPENNOLAN_BACKEND_PORT=…
  │      OPENNOLAN_FRONTEND_PORT=…
  │      OPENNOLAN_HOME=$PWD/.local
  ├── git config core.hooksPath .githooks
  └── print a human summary + a --json result
```

**`setup` deliberately does not start the app.** It runs unattended from Orca's
hook on every worktree creation; if it started the app, creating five worktrees
would spawn five apps. Starting is `scripts/dev run`, always explicit.

Idempotent: re-running repairs what's missing and never deletes user data.

Paste `scripts/dev setup` into Orca's repo `setup` slot so
`orca orchestration worker-start --setup run` provisions every new worktree.
People doing `git worktree add` by hand run the same command. One code path.

### `.env.worktree` must actually be loaded ★ Codex #3

Writing an env file makes nothing read it. Required:

- `.gitignore` gets `.env.worktree` and `/.local/`. (`*.env` does **not** match
  `.env.worktree` — it matches names *ending* in `.env`.)
- Every entry point sources it, not just `scripts/dev`. Two lines at the top of
  `run-dev` and `run-desktop`:

  ```bash
  [ -f "$ROOT/.env.worktree" ] && set -a && . "$ROOT/.env.worktree" && set +a
  ```

  Electron inherits the environment from the shell that launched it, so
  `desktop/main.js` needs nothing extra.
- **Ports and paths only. Never secrets.** Credentials keep coming from the
  existing `.env` / keychain path.

### Isolation contract

Each worktree owns: Python venv · port pair · `OPENNOLAN_HOME` · projects and
generated media · logs, PIDs, test reports, screenshots · browser session when
login state matters.

Shared only if content-addressed and concurrency-safe — the npm cache and the uv
cache, which matter because a full provision is ~400-600 MB and three trees per
review pays for it three times.

**Writable project state is never shared.**

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
window silently connects to another worktree's backend.

`scripts/dev run` rechecks the pair immediately before launch and fails clearly
(or reallocates) if something else took it.

**Verify:** run `./run-desktop --dev` in two worktrees at once. Both windows
open. Change a UI string in A; only A's window shows it.

---

## Piece 3 — test tiers ★ Codex, plus Claude's merge mode

```
FAST      ruff, schemas, changed-area unit tests             ~seconds
   │      → runs pre-commit
   ▼
FULL      all 74 python + 14 web tests, web build,           ~minutes
   │      containment check, ffmpeg tests on local fixtures
   │      → runs before review and in PR CI
   ▼
APP       backend + frontend + Playwright smoke flows        ~minutes
   │      → runs before review and in PR CI
   ▼
MEDIA     ffmpeg / render checks, synthetic fixtures only    slower
   │      → PR CI when render code changed; otherwise deferred
   ▼
PROVIDER  real Replicate / ElevenLabs / Anthropic calls      paid + flaky
          → NEVER on a PR. Manual only for now.
```

MEDIA and PROVIDER are why five tiers beat three: this is a video tool that calls
paid, non-deterministic APIs. Without a home those tests either creep into FULL
(expensive, flaky CI) or never run. `tests/eval/` already holds `bench_runner.py`
plus `golden_scenarios/` and `golden_outputs/` — that material, currently orphaned.

**PROVIDER runs require:** explicit provider selection · a maximum spend ·
separate test credentials · recorded provider/model versions · an honest
`skipped` / `passed` / `failed` result. Scheduled provider runs are deferred —
see [`nightly-automation.md`](nightly-automation.md).

### `ruff` — the newly added linter

`requirements-dev.txt` currently has pytest and pytest-asyncio and nothing else.
`ruff` covers lint **and** format in one fast tool, so FAST and the CI `policy`
job have something real to run.

The reason it matters here is not style: **style findings are exactly what an AI
reviewer wastes its budget on.** A linter catching them mechanically pushes the
reviewer's tokens into the judgment box where they belong.

### Merge mode — a modifier, not a tier ★ Claude

Answers "green on my branch" vs "green after merging into main". Runs in the
disposable `merge-test/<sha>` worktree, never in the review tree.

```
  merge-test worktree, created from the reviewed SHA
    ├── git merge origin/main --no-commit --no-ff
    │      └── conflict → report it, do not test a broken tree
    ├── scripts/dev test full
    └── teardown: the whole worktree goes away
         (if an in-place merge is ever used as a fallback, guard the abort:
          git rev-parse -q --verify MERGE_HEAD >/dev/null && git merge --abort
          — a bare `git merge --abort` errors when no merge is in progress)
```

**In CI this is redundant and should not be repeated** ★ Codex #5. GitHub's
`pull_request` event checks out `refs/pull/N/merge` — the head already merged into
base, computed at check time. CI tests that checkout directly. The local
merge-test is an *early warning*; CI is authoritative, because main may have moved
between the two. Record both the feature SHA and the tested merge SHA.

### ⚠️ Every tier must set a scratch `OPENNOLAN_HOME`

`create_app()` reloads the repo `.env`. Without a scratch home, the auth-gate test
**spawns a real, billable agent turn.** Applies to local runs and CI alike. Tests
must never touch a user's existing projects.

---

## Piece 4 — app smoke via Playwright ★ Codex

Claude proposed a bash `smoke.sh`. Wrong: `@playwright/test` ^1.61.1 is already in
`desktop/package.json` devDependencies with zero specs, and `RULES.md` already says
E2E "belongs in a Playwright suite against the running app".

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

Isolated temporary `OPENNOLAN_HOME`. Never a real API key. This is what lets a
reviewer say "it runs", not just "the tests pass".

---

## Piece 5 — debugging: reuse, don't rebuild ★ Claude

| Tool | Use |
|---|---|
| `python scripts/debug_session.py latest` | bounded report from a UI session recording |
| `GET /api/debug/sessions/{id}/analyze` | same, over HTTP |
| `.agents/tools/backend-log` | tees uvicorn → `.agents/tools/logs/backend.log` |
| `browse` / `gstack` skills | headless browser QA |
| Orca's built-in browser | a reviewer can drive the Studio UI itself |

> ⚠️ **Never `Read` or `cat` a raw `.ndjson` session file.** A 2-minute session is
> ~2000 lines / ~85k tokens and will blow your context. Use the analyzer, then
> pull only the slice it points at.

The one genuine gap is **redaction**: evidence leaving a worktree (CI artifacts,
PR comments) must be scrubbed of keys, tokens, user paths, and private project
content. Add a flag to the existing analyzer, not a new `debug-bundle` command.

---

## Piece 6 — git hooks ★ Codex's three, with one hard constraint

**Git** hooks, not Claude Code hooks — codex, cursor, and a human typing
`git commit` all need them. Checked into `.githooks/`, enabled by `core.hooksPath`
in `scripts/dev setup`, so they travel with every worktree.

```
pre-commit    identity guard  [BUILT]  + scripts/dev test fast  [Phase 2]
              └─ FAIL → commit BLOCKED. This is the real gate.

post-commit   record the new SHA; update the Orca card comment.
              NO review request — worker_done triggers review, not a hook.
              Must never wait on an agent.

pre-push      account guard   [BUILT]  + the medium tier        [Phase 2]
              WARNS if this SHA has no current review. Does not block on it.
```

### Why `pre-push` cannot enforce the review ★ Codex #2, sharpened

Codex framed it as a deadlock: the reviewer needs the commit, the commit needs the
review. The tighter reason is that **a GitHub check cannot attach to a commit
GitHub has never seen** — so the push has to happen before the verdict can exist.
Blocking the push on a review makes the review impossible.

It also blocks pushing a draft branch for backup. `review-current` plus
`dismiss_stale_reviews` are the authority; the hook is a courtesy warning.

### The identity guard — built, and what it taught us

`pre-commit` blocks a commit made with the wrong `user.email`; `pre-push` switches
the gh account to `het8802` and then stops.

It stops on purpose:

```
  git push
    ├─ ref discovery ──► AUTHENTICATES HERE          ← before any hook
    ├─ pre-push hook runs                            ← too late to change it
    └─ transfer
```

Observed while landing `pdd`: a push as `htikawala_eGain` 403'd during ref
discovery and the hook never ran at all. A hook can *block* a push; it cannot
*fix* the credential for the push in flight. So it switches and exits 1 with "run
it again" — self-healing on retry, and it can never let a push through under the
wrong account.

The structural fix for the email is not a hook at all — it is git's conditional
include in `~/.gitconfig`:

```gitconfig
[includeIf "gitdir:~/Documents/Het-personal/"]
    path = ~/.gitconfig-personal
```

Hooks can be skipped (`--no-verify`) and don't run on GitHub. **PR CI enforces the
same rules again.** Hooks are for fast feedback, not authority.

---

## Piece 7 — the review flow

```
  orca orchestration run-create --objective "<sprint>"
  orca orchestration task-create --spec "<issue>"                     → task_a
  orca orchestration task-create --spec "review task_a" --deps '["task_a"]'

  orca orchestration worker-start --task task_a \
      --worktree new-top-level --name 123-caption-fix \
      --agent claude --setup run        ← runs scripts/dev setup
                          ▼
       author builds; pre-commit gate runs on each commit;
       author picks ONE commit, pushes, opens a DRAFT PR
                          ▼
  orca orchestration send --type worker_done \
      --task-id task_a --dispatch-id <d> --outcome succeeded \
      --files-modified "server/render_proxies.py,tests/…" \
      --body "<what changed, what remains risky, SHA 8f27a1…>"
                          ▼
  coordinator: check --wait --types worker_done,escalation,question
       reads author provider + SHA → picks the OPPOSITE provider
                          ▼
  review worktree, created from the exact SHA, then ASSERTED:
       git rev-parse HEAD == <submitted SHA>     else FAIL
       git status --porcelain is empty            else FAIL
       (re-run both immediately before sending the verdict)
                          ▼
       1. scripts/dev test full + smoke     ← mechanical
             RED → stop, report. Nothing to judge yet.
       2. git diff $(git merge-base HEAD origin/main)   ← judgment
                          ▼
  merge-test worktree: base merged in, tiers run
                          ▼
      ┌───────────────────┴────────────────────┐
  changes_requested                          pass
      ▼                                        ▼
  reviewer sends worker_done               reviewer sends worker_done
  through its Orca dispatch                through its Orca dispatch
      ▼                                        ▼
  coordinator publishes findings           coordinator publishes status
  and creates a REMEDIATION                with its GitHub App credential
  task for the AUTHOR                         ▼
      ▼                                     HET approves and merges
      ▼
  author fixes → new SHA → new review task (old review auto-stale)
  max 2 rounds → then a decision gate to Het
```

### Verdict handling ★ Het's decision

`changes_requested` **does not close or block the PR.** The PR stays open with the
findings attached so Het can see the exchange, and the coordinator dispatches the
**author** to address the reviewer's comments. Fixes always belong to the author;
the reviewer is read-only and never edits the author's branch.

### When the author disagrees with a finding ★ Claude

A third outcome, so a bad finding doesn't burn both rounds in argument:

```
  author disputes ──► decision gate to Het
                      { the finding · the author's rebuttal · the file }
```

Not a third round. This also makes reviewer quality visible — if disputes are
frequently upheld, the reviewer prompt needs work and Het will see it.

### Task completion ≠ review verdict ★ Codex

A review that finds a real bug is a **successful** review.

```
  send --type worker_done --outcome succeeded
       payload verdict: changes_requested
```

Never `--outcome failed` for a review that merely found something — Orca
circuit-breaks a dispatch after 3 consecutive failures, and that budget is for
execution failures, not findings.

### Do not review every commit

The author marks one commit ready. Reviewing each checkpoint costs roughly 10× the
tokens for the same findings. If per-commit review is ever wanted, the queue keeps
only the newest unreviewed commit per task.

### The review record — one location ★ Codex #1, Option A

**A GitHub commit status on the reviewed SHA.** Not a CI artifact, not a PR
comment, not a file on the branch. The coordinator publishes it after a local Orca
review; the `review-current` check reads it.

The reviewer never owns the GitHub status credential. Its result must arrive as
`worker_done` from the active Orca task and dispatch. A coordinator running a
trusted checkout matches that result to its stored request, nonce, reviewer
terminal, clean worktree, and exact SHA. Only then does a dedicated GitHub App
publish the comment and status. The coordinator inbox and credential live outside
agent-writable worktrees.

```text
reviewer worktree ──worker_done──► Orca ──► trusted coordinator
                                                   │
                                                   ▼
                              GitHub App writes review-current
```

```
task + PR id · author provider · reviewer provider
full reviewed SHA
commands run + results
findings: severity · file · line · explanation
dimensions: correctness · tests · architecture · security
            performance · readability · accessibility
unresolved risks
verdict: pass | changes_requested | blocked
```

**Option B — running the reviewer inside CI — is closed for this repo.** It would
put Anthropic and OpenAI credentials in a public repo's Actions secrets, contradict
the product's BYOK local-first design, and rebuild Orca's routing in YAML. Revisit
only if local Orca availability becomes the main cause of blocked work.

---

## Piece 8 — PR CI ★ Codex's shape, Claude's starting size

Codex's seven-job graph is the target, not the start.

**Start with one job** running `scripts/dev test full` + `smoke` against GitHub's
merge checkout. Split when a job gets slow enough to annoy:

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

`merge-ready` is the single summary check branch protection requires. Cancel older
runs on a new commit. If a merge queue is enabled later, also run on `merge_group`.
`release-mac.yml` should eventually depend on the same deterministic checks.

### Branch protection settings to add ★ Codex #6

Three tighten-ups on the existing rule, not a new one:

- [ ] `dismiss_stale_reviews: true` — the native half of D3
- [ ] `required_conversation_resolution: true`
- [ ] required status checks: `merge-ready` and `review-current`
- [ ] bind `review-current` to the dedicated coordinator GitHub App ID
- [ ] consider `enforce_admins: true` once the loop is trusted
- [ ] consider `allow_force_pushes: false`

`scripts/dev doctor` should **verify** these rather than assume them.

---

## Piece 9 — lifecycle: stopping the app and reaping worktrees

The RAM and disk problem, which nothing currently addresses.

```
  measured today: 3 stale worktrees, 568 MB, none merged, none reaped
  projected per fully-provisioned tree:
     node_modules (web + desktop)  ~144 MB
     .venv                         ~200-400 MB
     checkout                       ~29 MB
                                   ────────────  ~400-600 MB each
```

### Stopping

Nothing makes an agent call `stop`. Orca's `worker-stop` closes the agent terminal
and explicitly leaves other processes alone, so a backgrounded uvicorn survives it.
Four levers, weakest to strongest:

| Lever | Reliability |
|---|---|
| Dispatch preamble says "stop before `worker_done`" | agent discipline — will leak |
| Coordinator runs `scripts/dev stop` on `worker_done` | good, needs a live coordinator |
| Orca `archive` hook → `scripts/dev stop` | native slot, currently `""` |
| **`scripts/dev run --ttl 30m`** — self-terminating | needs no discipline at all |

The TTL is the one that actually solves it: nothing has to remember anything, the
process dies. Pair it with a PID file per worktree (the isolation contract already
promises "logs, process IDs") and `scripts/dev stop --all` for orphans.

### Reaping

```
  scripts/dev reap
    merged + clean + idle     → orca worktree rm --worktree <sel> --force
    merged + clean + running  → skip, report, retry next run
                                after N skips → flag for the human
    anything else             → report only, NEVER auto-delete
```

Predicate detail:

```
  merged   git merge-base --is-ancestor <branch> main
  clean    git status --porcelain empty  AND  rev-list @{u}..HEAD empty
  idle     PID file absent or dead
```

**Why `idle` stays in the predicate even though `archive` → `stop` would make it
idle anyway:** `stop` is *cleanup*; the predicate is *safety*. A running process
is the only cheap signal that a human or agent is still using the tree — git
cannot tell you that. There is also an ordering hazard: `worktree rm --force`
deletes the directory, and the archive hook firing first is a hope, not a
guarantee (the slot is empty today, Orca may not be running, and hook-vs-delete
can race). Lose that race and you get an orphaned uvicorn holding a deleted cwd
and its port. Checking idle first means never depending on the ordering. Archive
hook = belt, predicate = braces.

Reaping runs manually for now (`scripts/dev reap`). Scheduling it is deferred —
see [`nightly-automation.md`](nightly-automation.md).

---

## Manager visibility and gates

Three surfaces, all of which already exist:

1. **Orca worktree card** — `orca worktree set --workspace-status
   todo|in-progress|in-review|completed --comment "<one line>"`, updated at every
   checkpoint: repro, fix, validation, handoff, blocker.
2. **The PR** — problem, owner + provider, architecture choice and why, tests run,
   review verdict + reviewed SHA, risks and rollback, open decisions.
3. **GBrain** — `CLAUDE.md` already mandates a page after any meaningful decision.
   Dispatch preamble adds: *write the GBrain page before sending `worker_done`.*

**Agents must open a decision gate before:** ★ Codex

- a public API or stored-data shape change (incl. `schemas/artifacts/*`);
- anything touching authentication, secrets, or the agent sandbox;
- adding a paid provider or a recurring service;
- adding a major dependency;
- deployment, release, or migration behavior;
- an architecture change spanning several parts of the product.

Small implementation choices inside an approved plan do not need a gate.

**Cost** ★ Claude — one cross-reviewed task is roughly writer turn + reviewer turn
+ often a fix round + re-review, plus three worktree provisions. Instrument the
first real Phase 4 task and report the actual number before the loop is turned on
by default. Reuse the existing chat cost display; do not build metering.

---

## Rollout

### Phase 0 — make the repo runnable at all  ◑ PARTLY DONE

Done 2026-08-01:

- [x] `pdd` squash-landed onto `main` (`fb344ff..cd0ec06`) — future branches fork
      from a current `main`
- [x] identity guard: `.githooks/pre-commit` + `pre-push`, `core.hooksPath` set,
      `includeIf` in `~/.gitconfig`
- [x] `.agent/` and `.context/` gitignored; `.agent/seo-audits/` untracked
- [x] PR workflow adopted; branch protection state measured

Still open:

- [ ] `uv venv --seed` + `requirements-dev.txt` (+ `ruff`), then run the suite and
      **write down what's red**
- [ ] tighten the three branch-protection settings (zero code, five minutes)

**Done when:** `pytest tests/` and `npm test --prefix web` both execute and produce
a known result.

### Phase 1 — make worktrees runnable in parallel

`scripts/dev doctor` + `setup`, per-worktree ports (Piece 2), `.env.worktree`
loading, Orca `setup` and `archive` slots filled.

**Done when:** two agents independently start the app, make different UI changes,
and each sees only their own.

### Phase 2 — reliable test commands

Five tiers + merge mode. `ruff` replaces the four-file `py_compile` lint. First
Playwright smoke spec. Redaction on the existing analyzer. `run --ttl`, `stop
--all`, `reap`.

**Done when:** a fresh worktree runs one command and gets a clear pass or an
actionable failure report with evidence.

### Phase 3 — PR CI

One job running `test full` + `smoke` on GitHub's merge checkout. Required check,
stale-run cancellation.

**Done when:** untested code cannot merge through the normal GitHub path.

### Phase 4 — cross-agent review

Author provider + SHA in Orca task state. Opposite provider dispatched into a
pinned, asserted `review/<sha>` tree. GitHub commit status as the review record.
`review-current` check. Remediation task back to the author on
`changes_requested`. Dispute gate.

**Done when:** one claude-authored and one codex-authored change each complete a
cross-provider review with no manual routing — and the cost is measured.

### Phase 5 — hooks and manager summaries

`test fast` joins `pre-commit`; medium tier joins `pre-push`. Automatic Orca
comment + PR summary updates.

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
| Reviewer running inside CI (Option B) | local Orca availability becomes the main blocker |
| Paid provider tests on PRs | never |
| A reviewer that edits the author's branch | never — findings go back to the author |
| Automatic merge | never — Het is the merge authority |
| Coverage thresholds | the suite is green and stays green for a week |
| Docker / devcontainer isolation | worktree isolation provably leaks |
| Seven CI jobs on day one | a single job gets slow enough to annoy |
| **Nightly / scheduled automation** | designed but deferred — [`nightly-automation.md`](nightly-automation.md) |

---

## Decisions on record

| # | Question | Decision |
|---|---|---|
| 1 | Base branch for agent worktrees | **`main`** — `pdd` landed, PRs adopted |
| 2 | Add a linter | **Yes, `ruff`** — lint + format, one dependency |
| 3 | `changes_requested` behavior | **PR stays open with findings; coordinator dispatches the author to address the reviewer's comments** |
| 4 | Nightly automation | **Not now.** Design kept in `nightly-automation.md` |
| 5 | Review worktree topology | **Three trees** — author, `review/<sha>`, `merge-test/<sha>` |
| 6 | Review record location | **GitHub commit status** on the reviewed SHA (Option A) |
| 7 | Push identity enforcement | Hook switches and blocks; retry succeeds |

---

## Acceptance checklist

- [ ] The existing 74 Python + 14 web test files all execute (Phase 0)
- [ ] A new worktree is prepared with one command
- [ ] Two worktrees run the app at once with no port or data leak
- [ ] Every agent can run fast / full / merge / smoke itself
- [ ] `ruff` is clean and runs in FAST
- [ ] Failures produce bounded, redacted evidence
- [ ] PRs run deterministic tests on GitHub's merge checkout
- [ ] Claude-authored code is reviewed by Codex, and vice versa
- [ ] Every passing review names the exact current SHA
- [ ] A new commit invalidates the old review (`dismiss_stale_reviews`)
- [ ] `changes_requested` sends a remediation task to the author, PR stays open
- [ ] A disputed finding reaches Het as a decision gate
- [ ] No worktree keeps the app running after its work is done
- [ ] Merged, clean, idle worktrees are reaped; anything else is only reported
- [ ] Gated change types pause for Het's decision
- [ ] Het remains the required final reviewer and merge authority
