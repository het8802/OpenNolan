# Codex review of the merged agent-development plan

**Status:** REVIEW
**Reviewed:** `merged/architecture.md`
**Date:** 2026-08-01

## Verdict

I agree with the architecture and rollout order. It combines the strongest
parts of the two source plans without building a second orchestrator.

The following changes are needed before implementation. They do not change the
overall design; they make its isolation and review guarantees enforceable.

## Required changes

### 1. Define where the authoritative review record lives

`architecture.md:491` says the review is stored as a CI artifact or GitHub
check. These are different systems, and a CI job cannot automatically read an
artifact produced by a local Orca run.

Choose one first implementation:

```text
Option A — local Orca review
  reviewer finishes
       │
       ▼
  coordinator publishes a GitHub commit status/check for the reviewed SHA
       │
       ▼
  review-current verifies that exact SHA and verdict

Option B — CI-hosted review
  pull request event
       │
       ▼
  CI starts the opposite provider with dedicated credentials
       │
       ▼
  the same workflow creates review-current
```

Start with Option A if reviews will run through the local Orca setup. Document
the check name, JSON shape, who may publish it, and how a later commit makes it
stale. A PR comment alone is not an authoritative record.

### 2. Do not require AI review before every push

`architecture.md:203` and `architecture.md:424` make a current review a
`pre-push` requirement. This can deadlock the workflow:

```text
remote reviewer needs commit ──► author must push commit
author must push commit ───────► pre-push demands remote review
```

This also blocks pushing a draft branch for backup or opening a draft PR.

Recommended rule:

- `pre-push` blocks only on deterministic local checks.
- It may warn when the current SHA is not reviewed.
- `review-current` blocks readiness or merge after the commit is reachable by
  the reviewer.
- Branch protection, not a local hook, is the final review authority.

### 3. Make `.env.worktree` real, persistent, and ignored

`architecture.md:251` writes `.env.worktree`, but the current `.gitignore` does
not ignore `.env.worktree` or `.local/`. More importantly, writing an env file
does not make later Orca terminals, Vite, Electron, or `run-dev` read it.

The implementation must:

- add explicit ignore rules for the generated env and runtime directories;
- make every `scripts/dev` subcommand load `.env.worktree` itself;
- pass the resolved environment to every child process; and
- either require `scripts/dev run` or configure Orca's workspace environment so
  direct `./run-desktop --dev` launches receive the same variables.

Do not copy secrets into `.env.worktree`. It should contain ports and paths, and
refer to the existing secure developer environment for credentials.

### 4. Pin and verify the review worktree's SHA

`architecture.md:463` starts a `new-top-level` worktree, then
`architecture.md:466` assumes it is on the submitted SHA. The shown command does
not pin the worktree to that SHA; a new top-level worktree normally starts from
the repository base.

Create the worktree from the submitted commit using the version-matched Orca
CLI path that accepts an exact base ref, then dispatch the reviewer into it.
Before review, fail unless both checks pass:

```text
git rev-parse HEAD          == submitted full SHA
git status --porcelain     has no tracked changes
```

The reviewer should repeat these checks before sending its verdict.

### 5. Use the promised merge-test worktree

The topology at `architecture.md:168` creates a disposable merge-test tree, but
the merge procedure at `architecture.md:337` mutates the review worktree and
then runs `git merge --abort` unconditionally.

Use a disposable merge-test worktree instead:

```text
target branch + reviewed SHA
            │
            ▼
temporary merge-test worktree
            │
            ├── merge fails   → report conflict
            └── merge passes  → run requested test tier
```

In GitHub Actions, `pull_request` jobs normally test GitHub's proposed merge
checkout. CI should test that checkout directly rather than merging the base a
second time. Record both the feature SHA and tested merge SHA in the result.

If an in-place merge remains as a fallback, require a clean tree and an exit
trap, and do not call `git merge --abort` when no merge is in progress.

### 6. Verify branch protection; CODEOWNERS alone does not enforce review

`architecture.md:104` and `architecture.md:478` say CODEOWNERS already blocks a
merge. The file only names the owner. GitHub must also have branch protection
or a ruleset that requires code-owner approval.

Add to Phase 3:

- require pull requests on the target branch;
- require Het's code-owner approval;
- require the CI summary and current-review checks;
- dismiss stale approvals after new commits; and
- require review conversations to be resolved.

The setup step should verify these settings instead of assuming they exist.

## Smaller corrections

### Resolve tools from the environment

`architecture.md:244` mentions one machine-specific Homebrew path for `uv`.
`doctor` should resolve `uv` from `PATH`, report its version, and provide a clear
setup action when it is unavailable. Do not make the architecture depend on
`/opt/homebrew`.

### Recheck ports when starting the app

Checking a hashed port during setup does not reserve it. `scripts/dev run`
should check the assigned pair again immediately before launch and fail clearly
or allocate a new pair if another process owns it.

### Separate task completion from review verdict

An Orca review task can complete successfully while its verdict is
`changes_requested`. Send `worker_done --outcome succeeded` for a completed
review and carry `changes_requested` in the structured review result. Create a
new remediation task for the author and a new review task for the new SHA.

Do not mark the review task itself failed merely because it found a real bug;
that would mix execution failure with review outcome and interfere with Orca's
retry behavior.

## What should remain unchanged

- One concern per top-level worktree.
- Cross-provider review.
- Exact-SHA review validity.
- Reviewers report findings and do not edit the author's branch.
- Repository commands perform checks; Orca coordinates agents.
- Deterministic PR checks and budgeted provider checks stay separate.
- Het remains the final merge authority.
- Start CI small, then split jobs when runtime or failure clarity justifies it.
