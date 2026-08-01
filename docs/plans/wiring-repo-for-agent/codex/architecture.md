# Wiring OpenNolan for agent development

**Status:** PLAN — no implementation has started.
**Author:** Codex
**Date:** 2026-08-01
**Decision owner:** Het

## Purpose

OpenNolan should support several coding agents working at the same time without
sharing files, ports, runtime data, or responsibility.

Before a pull request reaches Het, the change should have:

- been implemented in its own worktree;
- been run and tested by the author;
- been reviewed by a different agent provider;
- been tested again against the branch it will merge into; and
- produced a short record of what changed, why, and what remains risky.

The system must help the agents do this work. It must not rely on agents
remembering a long checklist.

## Outcome in one picture

```text
                           HET — MANAGER
                                │
                     approves important decisions
                                │
                                ▼
                      Orca coordination run
                                │
                  plan ──► build ──► review ──► QA
                                │          │       │
                                │          │       │
                                ▼          ▼       ▼
                         author tree  review tree  test tree
                                │          │       │
                                └────────┴────────┘
                                           │
                                  PR checks on merge result
                                           │
                                           ▼
                                  HET reviews and merges
```

For a small change, review and QA may be one task. For a risky change, they are
separate tasks so one agent reviews the code while another proves the app works.

## Current state

Useful foundations already exist:

- `AGENT_GUIDE.md` and `PROJECT_CONTEXT.md` explain the product architecture.
- `Makefile:42` runs the Python suite.
- `web/package.json:10` runs the Vitest suite.
- `server/app.py:261` exposes `/api/health` for app readiness checks.
- `lib/app_paths.py:52` supports an `OPENNOLAN_HOME` override.
- `.github/CODEOWNERS:2` requires Het to review all files.
- The repository has 74 Python test files and 14 web test files.

The main gaps are:

- `.github/workflows/release-mac.yml:17` is release-only. Pull requests do not
  run the normal test suite.
- `run-dev:18`, `run-dev:19`, and `web/vite.config.js:9` use fixed ports. Two
  worktrees cannot safely run the app at the same time.
- Playwright is installed in `desktop/package.json`, but there are no Playwright
  tests proving that the complete app starts and handles a basic user flow.
- There is no shared worktree setup command.
- There is no repository-owned hook setup.
- There is no durable link between an agent review and the exact commit it
  reviewed.

## Design rules

### 1. One concern, one worktree

An independent issue starts in a top-level worktree based on the target branch.
A child worktree is used only when its change depends on another unfinished
change.

```text
origin/main
  ├── feature/123-caption-fix       author worktree
  ├── review/123-8f27a1        exact commit; read-only review
  └── merge-test/123-8f27a1         feature merged into current target
```

The review and merge-test worktrees are temporary. They must never become a
second place where the feature is edited.

### 2. The author does not review itself

The default reviewer map is:

```text
Claude wrote the change  ──► Codex reviews it
Codex wrote the change   ──► Claude reviews it
Another agent wrote it   ──► a different provider reviews it
```

If the opposite reviewer is unavailable, the review is blocked. The author does
not silently become its own reviewer.

### 3. A review belongs to one commit

Every review record includes the full commit SHA. A later commit makes the old
review stale automatically.

This prevents a common failure:

```text
commit A reviewed and passed
        │
        └── commit B changes the code
                         │
                         └── old review must NOT count for B
```

### 4. Scripts perform checks; Orca coordinates work

Repository scripts may set up a worktree, run the app, run tests, and collect
debug evidence. They do not decide which agent should work next.

Orca owns agent tasks, dependencies, questions, completion messages, and manager
decision gates. GitHub owns the final merge checks.

This respects OpenNolan's instruction-driven architecture. We are not adding a
second Python product orchestrator.

## Worktree setup

Create one idempotent command for both agents and humans:

```text
scripts/dev setup
  │
  ├── check Python, Node, npm, FFmpeg, and required versions
  ├── create or update the local Python environment
  ├── install locked Node dependencies
  ├── prepare an ignored worktree runtime directory
  ├── assign a stable backend and frontend port pair
  ├── configure safe access to local environment variables
  ├── install repository hooks
  └── print a human summary and a JSON result
```

Orca's repository setup hook should run this command for every new worktree.
`orca orchestration worker-start --setup run` can then create a worker that is
ready to test its own change.

Setup must be safe to run more than once. Re-running it should repair missing
pieces without deleting user data.

### Worktree isolation

Each worktree needs its own:

- Python virtual environment;
- backend and frontend ports;
- `OPENNOLAN_HOME`;
- projects and generated media;
- logs, process IDs, test reports, and screenshots; and
- browser session when login state affects a test.

Large download caches may be shared when they are content-addressed and safe for
concurrent use. Writable project state must not be shared.

### Port allocation

Replace fixed ports with environment-controlled defaults:

```text
OPENNOLAN_BACKEND_PORT=<assigned port>
OPENNOLAN_FRONTEND_PORT=<assigned port>
```

The current values, `8000` and `5173`, remain the defaults outside a managed
worktree. A setup command assigns a stable pair based on the worktree identity
and verifies that the ports are free before starting the app.

The backend, Vite proxy, and Electron development path must all read the same
values. Otherwise an Electron window can accidentally connect to another
worktree's backend.

## Development command surface

Build one small command surface and reuse it from agents, hooks, and CI.
The examples below use `scripts/dev`; the implementation may be Bash or Python.

```text
scripts/dev doctor              explain what is ready or missing
scripts/dev setup               prepare the current worktree
scripts/dev run                 start the app on its assigned ports
scripts/dev stop                stop only this worktree's processes
scripts/dev test fast           checks used during implementation
scripts/dev test full           complete deterministic local suite
scripts/dev test merge          test the proposed merge result
scripts/dev smoke               prove the app starts and basic UI works
scripts/dev debug-bundle        collect redacted failure evidence
scripts/dev review status       show review SHA and unresolved findings
```

Each command should support:

- readable terminal output for humans;
- `--json` output for agents;
- clear exit codes;
- no paid provider calls unless explicitly requested; and
- redaction of keys, tokens, user paths, and private project content.

Start with this command surface. Add an MCP wrapper only after the commands are
stable. The MCP server should call the same implementation instead of creating a
second set of setup and test rules.

## Test levels

Tests should be grouped by cost and purpose.

```text
FAST       formatting, schemas, lint, focused unit tests
   │
   ▼
FULL       all deterministic Python and web tests
   │
   ▼
APP        backend + frontend + Playwright smoke flows
   │
   ▼
MEDIA      FFmpeg and render checks with synthetic fixtures
   │
   ▼
PROVIDER   real external services, manual or scheduled with a budget
```

### Fast checks

These should finish quickly enough to run before a commit:

- formatting and linting;
- Python import and syntax checks;
- JSON and YAML schema validation;
- tests mapped to the changed area; and
- affected Vitest files.

### Full deterministic checks

These run before review and in pull-request CI:

- all Python unit and contract tests;
- all Vitest tests;
- web production build;
- containment and packaged-path checks; and
- FFmpeg tests that use generated local fixtures.

### App smoke checks

Add a small Playwright suite that:

1. starts the backend and waits for `/api/health`;
2. starts Vite on the assigned port;
3. opens the application;
4. creates or opens a throwaway project;
5. exercises one read path and one write path;
6. fails on unexpected browser console or network errors; and
7. saves screenshots, logs, and a trace when it fails.

Tests must use an isolated temporary `OPENNOLAN_HOME`. They must never consume a
real API key or modify a user's existing projects.

### Provider checks

Tests that call paid or unreliable external services do not run on every pull
request. Run them manually or nightly with:

- explicit provider selection;
- a maximum spend;
- separate test credentials;
- recorded provider/model versions; and
- a clear `skipped`, `passed`, or `failed` result.

## Automated cross-agent review

### Review trigger

Do not launch an AI reviewer for every checkpoint commit. Instead, the author
marks one commit as ready:

```text
author commits ──► fast checks pass ──► author submits for review
                                                │
                                                ▼
                                      opposite provider starts
```

If per-commit review is later required, the queue should keep only the newest
unreviewed commit for a task. This avoids paying reviewers to inspect obsolete
commits.

### Git hooks

Hooks improve the local experience, but they are not the final authority:

- `pre-commit` runs fast deterministic checks.
- `post-commit` records the new SHA and may enqueue a review request.
- `pre-push` checks that the current SHA has a current review and runs the
  medium-cost test level.

The post-commit hook must not wait for an agent. If Orca is unavailable, it
leaves a pending review request that the next coordinator run can process.

Git hooks can be skipped and do not run on GitHub. Pull-request checks enforce
the same rules again.

### Orca review flow

Use Orca's existing Run, Task, Dispatch, question, and decision-gate model:

```text
implementation task reports worker_done
                 │
                 ▼
coordinator reads author provider and commit SHA
                 │
                 ▼
creates review task for the opposite provider
                 │
                 ▼
reviewer checks exact SHA in a clean worktree
                 │
        ┌────────┴─────────┐
        │                  │
  critical findings      pass
        │                  │
        ▼                  ▼
author fixes them     QA / merge test
        │                  │
        └──── new SHA ─────┘
```

The reviewer is read-only. It returns findings to the author. The coordinator
must not turn a review task into an unapproved edit task.

Allow at most two author/reviewer rounds. If critical findings remain, create a
manager decision gate instead of allowing an endless agent loop.

### Review record

The review result should contain:

- task and pull-request identifiers;
- author and reviewer providers;
- full reviewed commit SHA;
- commands run and their results;
- findings with severity, file, line, and explanation;
- checks for correctness, tests, architecture, security, performance,
  readability, and accessibility when relevant;
- unresolved risks; and
- verdict: `pass`, `changes_requested`, or `blocked`.

Keep this record as a CI artifact or GitHub check result. Do not add generated
review files to the feature branch unless the review contains a lasting design
decision that belongs in normal documentation.

## Pull-request CI

Add a pull-request workflow with separate, readable jobs:

```text
policy ──┐
python ──┤
web ─────┤
media ───┼──► app-smoke
         │          │
security ┘          ▼
             review-current ──► merge-ready
```

Recommended jobs:

1. `policy` — formatting, lint, schemas, generated-file rules, secret scan.
2. `python` — deterministic Python tests.
3. `web` — Vitest and production build.
4. `media` — FFmpeg and render tests with local fixtures.
5. `app-smoke` — backend, frontend, and Playwright.
6. `review-current` — prove that the review matches the current SHA and has no
   unresolved critical findings.
7. `merge-ready` — summary check required by branch protection.

Cancel older runs when a pull request receives a new commit. If GitHub's merge
queue is enabled later, also run the workflow for `merge_group` events.

The existing release workflow should depend on the same deterministic checks
before building a tagged release.

## Manager visibility and decisions

Het should not need to read every agent terminal. Each worktree and pull request
should provide a short management summary:

- what problem is being solved;
- current owner and agent provider;
- current state: `todo`, `in-progress`, `in-review`, or `completed`;
- architecture choices and why they were made;
- tests and app checks completed;
- review verdict and reviewed SHA;
- risks, blockers, and rollback notes; and
- decisions that still require Het.

Use Orca worktree comments for the current one-line status. Use orchestration
questions and decision gates for choices that block work.

Require manager approval before agents make changes involving:

- public API or stored-data shape changes;
- authentication, secrets, or sandbox rules;
- a new paid provider or recurring service;
- a new major dependency;
- deployment, release, or migration behavior; or
- an architecture change that affects several parts of the product.

Small implementation choices do not need a manager gate when they follow an
already approved plan.

Meaningful accepted decisions should be written as a short repository decision
record and saved to GBrain when its MCP server is available.

## Rollout plan

### Phase 1 — Make worktrees runnable

- Add the shared setup and doctor commands.
- Add worktree-specific ports and runtime directories.
- Configure Orca's setup hook.
- Prove two worktrees can run different code at the same time.

**Done when:** two agents can independently start the app, make different UI
changes, and see only their own change.

### Phase 2 — Create reliable test commands

- Define fast, full, app, media, and provider test levels.
- Fix or replace the current narrow lint target.
- Add Playwright smoke tests.
- Add redacted debug bundles.

**Done when:** a fresh worktree can run one command and produce a clear pass or
an actionable failure report.

### Phase 3 — Add pull-request CI

- Run deterministic tests on every pull request.
- Test the proposed merge result.
- Configure required checks and stale-run cancellation.
- Keep Het's CODEOWNER review as the final human gate.

**Done when:** untested code cannot be merged through the normal GitHub path.

### Phase 4 — Add cross-agent review

- Record author provider and commit SHA in Orca task state.
- Dispatch the opposite provider in a clean review worktree.
- Store a structured review result.
- Return fixes to the original author.
- Invalidate reviews when the SHA changes.

**Done when:** Claude-authored and Codex-authored sample changes both complete a
cross-provider review without manual routing.

### Phase 5 — Add local hooks and manager summaries

- Install fast pre-commit checks.
- Queue reviewable commits without blocking Git.
- Verify review freshness before push.
- Update Orca worktree comments and PR summaries automatically.
- Add nightly provider and packaging checks with budgets.

**Done when:** Het can understand the state, evidence, and open decisions of a
change from Orca and the pull request without reading agent transcripts.

## What this plan deliberately does not build

- A custom agent scheduler. Orca already supplies task and worker tracking.
- An MCP server before the underlying commands are stable.
- AI review as a replacement for tests or Het's approval.
- Paid provider tests on every commit.
- A reviewer that edits the author's branch.
- Automatic merge. Het remains the final merge authority.
- One large shared worktree for unrelated agents.

Reconsider an MCP wrapper when agents repeatedly struggle to use the stable CLI
output. Reconsider hosted agent review when local Orca availability becomes the
main cause of blocked pull requests.

## Acceptance checklist

The repository is ready for routine multi-agent development when all of these
are true:

- [ ] A new worktree can be prepared with one command.
- [ ] Two worktrees can run the app at the same time without port or data leaks.
- [ ] Every agent can run fast, full, merge, and smoke checks itself.
- [ ] Failures produce bounded, redacted debug evidence.
- [ ] Pull requests run deterministic tests on the proposed merge result.
- [ ] Claude-authored code is reviewed by Codex and vice versa.
- [ ] Every passing review names the exact current commit SHA.
- [ ] New commits invalidate old reviews.
- [ ] Critical findings return to the author for repair.
- [ ] Important architecture choices pause for Het's decision.
- [ ] Het remains the required final reviewer and merge authority.
