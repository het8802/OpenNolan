# Wiring this repo for AI-agent development

**Status:** PLAN — nothing here is built yet.
**Author:** Claude (Opus 5) · 2026-08-01
**Reviewer slot:** `docs/plans/wiring-repo-for-agent/codex/` (empty — codex has not reviewed)

---

## Who this is for

Two readers, one document.

- **A human (Het)** deciding whether to approve this and what to build first.
- **An AI agent** that has to implement it, or that lands in a worktree tomorrow
  and needs to know how this repo expects it to behave.

Everything below names a real file and a real line where it exists today. If a
line number has drifted, trust the function name and re-grep.

---

## The goal in one picture

You are the manager. Code should reach you already written, already tested, and
already reviewed by a *different* agent than the one that wrote it.

```
   YOU
    │  "fix these two issues"
    ▼
 coordinator ──┬──► claude in worktree A ──► writes ──► gate ──┐
               │                                              │
               └──► codex  in worktree B ──► writes ──► gate ──┤
                                                               ▼
                          cross-review (writer never reviews itself)
                          A's code → codex     B's code → claude
                                                               ▼
                                            merge gate (base merged in first)
                                                               ▼
                                                    PR ──► YOU approve
```

Three rules make this work and everything else is plumbing:

1. **A worktree is a sealed box.** Own branch, own Python env, own ports, own
   data dir. Nothing shared with another worktree.
2. **One gate script, four callers.** Agents, git hooks, reviewers, and CI all
   run `scripts/gate.sh`. If a check only lives in CI, agents can't run it and
   will guess.
3. **The writer never reviews itself.** Claude writes → codex reviews. Codex
   writes → claude reviews.

---

## Today: three things are broken

### 1. Two worktrees running the app collide, silently

```
┌─ worktree A — claude ──────────┐  ┌─ worktree B — codex ──────────┐
│ ./run-desktop --dev            │  │ ./run-desktop --dev           │
└───────────────┬────────────────┘  └───────────────┬───────────────┘
                └────────────────┬──────────────────┘
                                 ▼
   vite dev server                          web/vite.config.js:9
     port: 5173        ← hard-coded    →  B: EADDRINUSE, dev server dies
     proxy /api → :8000                     vite.config.js:12
     (run-dev:19-20 hard-codes the same two)
                                 ▼
   Electron boot()                          desktop/main.js:623
     :627  backendPort = 8000               ← DEV branch, hard-coded
     :628  probeHealth(8000)  → true        (A's uvicorn already answers)
     :629  if (!alreadyUp)    → false       so B spawns NO backend
                                 ▼
        ╔══════════════════════════════════════════════════════╗
        ║  B's window is served by A's Python process.         ║
        ║  Codex "tests its own change" and is looking at      ║
        ║  Claude's code. No error is printed anywhere.        ║
        ╚══════════════════════════════════════════════════════╝
```

The good news: **data isolation already works.** `lib/app_paths.py:41` derives
`_REPO_ROOT` from the file's own location, so each worktree already has its own
`projects/`. `home()` (`:52`) and `projects_dir()` (`:57`) read env overrides
`OPENNOLAN_HOME` / `OPENNOLAN_PROJECTS_DIR`. Nothing to design — the levers exist.

The only genuinely global thing is the **port**.

### 2. A fresh worktree has no working environment

- There is no `.venv` in the checkout. `python3 -m pytest` → `No module named pytest`.
- `.claude/settings.local.json` is full of `./.venv/bin/python -m pytest …`
  commands pointed at a venv that no longer exists.
- Orca's per-repo setup script is empty:

  ```
  orca repo show --repo id:<OpenMontage> --json
    hookSettings.scripts.setup = ""      ← a new worktree gets nothing
  ```

So an agent that lands in a new worktree cannot run tests, cannot run the app,
and (because of #1) will test someone else's code if it tries.

### 3. There is no test gate anywhere

- `.github/workflows/` contains only `release-mac.yml` (tag-triggered build).
- No PR workflow. Nothing runs the 74 pytest files or the 14 vitest files.
- `.github/CODEOWNERS:2` is `* @het8802` — the human gate already exists and
  works. It's the *machine* gate that's missing.

---

## The design

Five pieces. Each one is small. They stack in order.

### Piece 1 — `scripts/worktree-setup.sh` (new, ~40 lines)

The one script that makes a worktree usable. Idempotent — safe to re-run.

```
worktree-setup.sh
  ├─ uv venv --seed .venv                    (uv is already installed)
  ├─ .venv/bin/pip install -r requirements-dev.txt
  ├─ npm ci --prefix web
  ├─ npm ci --prefix desktop
  ├─ derive a stable port pair from the worktree path:
  │     BASE = 20000 + (crc32(pwd) % 10000)
  │     OPENNOLAN_PORT = BASE        VITE_PORT = BASE + 1
  ├─ write .env.worktree  (gitignored):
  │     OPENNOLAN_PORT=…  VITE_PORT=…  OPENNOLAN_HOME=$PWD/.local
  └─ git config core.hooksPath .githooks
```

Then paste the same script path into Orca's repo setup field, so
`orca orchestration worker-start --setup run` runs it automatically for every
new worktree. Manual `git worktree add` users run it by hand — same script,
no second code path.

`OPENNOLAN_HOME=$PWD/.local` keeps each worktree's `projects/`, caches, and
renders separate. It needs no code change: `lib/app_paths.py:52` already reads it.

### Piece 2 — per-worktree ports (the only real code change, ~10 lines)

Three files stop hard-coding, start reading env, keep today's values as defaults:

| File | Line | Change |
|---|---|---|
| `run-dev` | 19-20 | `BACKEND_PORT="${OPENNOLAN_PORT:-8000}"` / `FRONTEND_PORT="${VITE_PORT:-5173}"` |
| `web/vite.config.js` | 9, 12 | `port: +(process.env.VITE_PORT \|\| 5173)`, proxy target uses `OPENNOLAN_PORT` |
| `desktop/main.js` | 627 | `backendPort = +(process.env.OPENNOLAN_PORT \|\| 8000)` |

`desktop/main.js:628-629`'s reuse-if-healthy check stays — it's correct *once
the port is per-worktree*. Today it's the bug; after this it's the feature
(one worktree, `run-dev` already up, Electron reuses it).

**How to verify:** start `./run-desktop --dev` in two worktrees at once. Both
windows open. Change a string in worktree A's UI; only A's window shows it.

### Piece 3 — `scripts/gate.sh` (new wrapper, reuses existing tests)

One entry point, three tiers. Everything else calls this.

```
scripts/gate.sh fast     ~20s   changed-file pytest + vitest related + py_compile
scripts/gate.sh full     ~min   all 74 pytest + 14 vitest + verify_containment.sh
scripts/gate.sh merge    ~min   merge base branch in FIRST, then full
```

`merge` is the one people forget. It answers "green on my branch" vs "green
after merging into the branch I forked from":

```
  gate.sh merge
    ├─ git fetch origin <base>
    ├─ git merge origin/<base> --no-commit --no-ff
    │     └─ conflict → FAIL loudly, do not test a broken tree
    ├─ gate.sh full
    └─ git merge --abort            (always — leave the tree as we found it)
```

Reuses: `pytest` (`tests/`, 74 files), `npm test` in `web/` (vitest, 14 files),
`scripts/verify_containment.sh` (already exists, OPN-10).

⚠️ **Gate scripts must set `OPENNOLAN_HOME` to a scratch dir.** `create_app()`
reloads the repo `.env`, and the auth-gate test will otherwise spawn a real,
billable agent turn.

### Piece 4 — `.githooks/post-commit` (new, ~15 lines)

A **git** hook, not a Claude Code hook — because codex, cursor, and a human
typing `git commit` all need to trigger it. Claude Code hooks only fire for Claude.

```
  git commit
      ▼
  .githooks/post-commit
      ├─ scripts/gate.sh fast
      │     ├─ FAIL → print failures, exit 1
      │     │          (commit stands; the agent sees red and must fix)
      │     └─ PASS ▼
      ├─ orca worktree set --worktree active --workspace-status in-review \
      │                    --comment "<subject>; fast gate green"
      └─ touch .git/ORCA_REVIEW_PENDING
```

The hook **queues** a review; it does not spawn a reviewer. Reviewing every
commit costs roughly 10× the tokens for the same findings. The reviewer fires
once per `worker_done` (Piece 5). If you want per-commit review anyway, it's one
added line in this hook — deliberate choice, not an oversight.

Enabled by `git config core.hooksPath .githooks` in Piece 1, so it's checked
into the repo and travels with every worktree.

### Piece 5 — the review loop, on Orca's orchestration layer

Orca already has task DAGs, dispatch, `worker_done`, `escalation`, `question`,
`merge_ready`, and decision gates. Do not rebuild any of it.

```
  orca orchestration run-create --objective "<sprint>"
  orca orchestration task-create --spec "<issue 1>"                  → task_a
  orca orchestration task-create --spec "review task_a" --deps '["task_a"]'

  orca orchestration worker-start --task task_a \
      --worktree new-child --name crop-fix --agent claude --setup run
                          │
                          │   --setup run executes Piece 1
                          ▼
              writer works, commits (Piece 4 gate runs), then:

  orca orchestration send --type worker_done \
      --task-id task_a --dispatch-id <d> --outcome succeeded \
      --files-modified "server/render_proxies.py,tests/…" \
      --body "<what changed, what remains>"
                          ▼
  coordinator:
  orca orchestration check --wait \
      --types worker_done,escalation,question --timeout-ms 900000
                          ▼
        ★ CROSS-ASSIGN: reviewer agent ≠ writer agent
                          ▼
  orca orchestration worker-start --task <review_a> \
      --worktree <the writer's worktree> --agent codex

    reviewer runs, in the writer's checkout so it can actually reproduce:
      scripts/gate.sh full
      scripts/smoke.sh
      git diff $(git merge-base HEAD origin/<base>)
                          ▼
      ┌───────────────────┴────────────────────┐
   FAIL                                      PASS
      ▼                                        ▼
  re-dispatch to the WRITER              scripts/gate.sh merge
  with findings                                ▼
  (max 2 rounds, then                    gh pr create --base <base>
   escalate to the human)                orca orchestration send --type merge_ready
                                               ▼
                                    YOU review the PR.
                                    CODEOWNERS blocks merge without you.
```

**Review verdicts are advisory, not authority.** A review-only `worker_done`
reports findings; it does not license the coordinator to edit files. Fixes go
back to the writer.

### Piece 6 — `.github/workflows/test.yml` (new, ~25 lines)

PR-triggered, runs `scripts/gate.sh merge`. Same script the agents ran locally,
so a green local gate means a green CI gate. `CODEOWNERS` already requires your
approval on top.

---

## Gate summary

| Gate | Who triggers it | What it runs | Roughly |
|---|---|---|---|
| **fast** | `.githooks/post-commit` | changed-file tests | 20s |
| **full** | reviewer agent | everything | minutes |
| **merge** | reviewer before PR, and CI | base merged in, then everything | minutes |
| **human** | PR opened | you + Orca's diff review UI | — |

---

## Debugging: what agents already have, and the one gap

Already in the repo — an implementing agent should use these, not rebuild them:

| Tool | What it does |
|---|---|
| `scripts/debug_session.py latest` | bounded report from a UI session recording |
| `GET /api/debug/sessions/{id}/analyze` | same, over HTTP |
| `.agents/tools/backend-log` | tees uvicorn → `.agents/tools/logs/backend.log` |
| `browse` / `gstack` skills | headless browser QA |
| Orca's built-in browser | reviewer can drive the Studio UI itself |

⚠️ **Never `cat` a raw `.ndjson` session file.** ~2000 lines / ~85k tokens.
Use the analyzer.

**The one gap: `scripts/smoke.sh`** — headless proof the app actually runs.

```
  smoke.sh
    ├─ boot backend on $OPENNOLAN_PORT with OPENNOLAN_HOME=<scratch>
    ├─ wait for /api/health
    ├─ POST /api/projects            → create a throwaway project
    ├─ POST a tiny render            → assert the output file exists
    └─ tear down, exit 0/1
```

This is what lets a reviewer say "it runs," not just "the tests pass." A bash
script, not an MCP server — MCP is worth it only once a bash script provably
isn't enough.

---

## How you stay in the loop

No new ADR system. Three surfaces that already exist:

1. **GBrain** — `CLAUDE.md` already mandates a page after any meaningful
   decision. Add one line to the dispatch preamble: *write the GBrain page
   before sending `worker_done`.*
2. **Orca card status** — `orca worktree set --workspace-status
   todo|in-progress|in-review|completed` plus a one-line `--comment`. The Orca
   workspace list becomes the board.
3. **The PR** — architecture and why-this-way goes in the PR body, linking the
   GBrain page. This doc's folder (`docs/plans/<topic>/<agent>/`) holds the
   longer-form plan when a change needs one.

---

## Build order

Each step is verifiable on its own. Don't start the next until the check passes.

| # | Build | Check it worked |
|---|---|---|
| 1 | `scripts/worktree-setup.sh` + paste into Orca repo setup | fresh worktree → `.venv/bin/pytest --version` works |
| 2 | per-worktree ports (3 files) | two worktrees run `./run-desktop --dev` at once, independently |
| 3 | `scripts/gate.sh` | `gate.sh full` runs green (or lists exactly what's red) |
| 4 | `.githooks/post-commit` | a commit prints gate output and flips the Orca card to in-review |
| 5 | `scripts/smoke.sh` | exits 0 on a clean tree, 1 with the backend broken on purpose |
| 6 | `.github/workflows/test.yml` | a PR shows a red X when a test is broken on purpose |
| 7 | one real cross-reviewed task end to end | a PR you didn't touch until review time |

**Step 3 will surface that the suite is currently red.** That is expected and is
the real content of "build a comprehensive test suite" — get the existing 74 + 14
files green and hold them there. Writing new tests before the old ones run is
building on sand.

---

## Deliberately not building

| Skipped | Add it when |
|---|---|
| Custom MCP server for debugging | a bash script provably can't express the check |
| Homegrown task queue / message bus | never — Orca orchestration is this, already |
| Review on every commit | you see the reviewer missing things it would catch at commit granularity |
| Per-agent prompt frameworks | `AGENT_GUIDE.md` + `RULES.md` stop being enough |
| Coverage thresholds | the suite is green and stays green for a week |
| Docker / devcontainer isolation | worktree isolation provably leaks |

---

## Open questions for the manager

1. **Reviewer strictness.** Should a reviewer `--outcome failed` block the PR
   entirely, or open it with the findings attached for you to judge?
2. **Round cap.** Two writer↔reviewer rounds then escalate to you — right number?
3. **Base branch.** Worktrees currently fork from `pdd`. Should agent worktrees
   fork from `main` instead, so `gate.sh merge` tests against what actually ships?
4. **Nightly automation.** `orca automations create --trigger daily --provider
   codex` could run `gate.sh full` + triage across all open worktrees. Worth the
   spend?
