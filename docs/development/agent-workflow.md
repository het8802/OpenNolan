# Agent development workflow

**Status: BUILT**

This is the daily operating guide for humans, Claude, Codex, Orca, Git hooks,
and GitHub Actions. The architecture and reasons behind it live in
`docs/plans/wiring-repo-for-agent/merged/architecture.md`.

## The short version

```text
manager approves the task and any architecture decision
                         |
                         v
author works in one worktree and tests its own change
                         |
                         v
opposite provider reviews the exact clean commit
                         |
              +----------+-----------+
              |                      |
              v                      v
           approved           changes requested
              |                      |
              v                      v
GitHub tests merge result     author fixes and repeats
              |
              v
manager reviews and merges
```

The manager is always the final reviewer and merge authority. Agents never
merge a PR automatically.

## Start work

Create one top-level worktree for one concern. With Orca:

```bash
orca worktree create --name <task-name> --no-parent \
  --agent claude --setup run --prompt "<task brief>" --json
```

Use `--agent codex` when Codex is the author. Orca's repository setup slot must
contain:

```text
scripts/dev setup
```

Set Orca's startup policy to `wait-for-setup`. Review dispatch refuses to start
unless Orca reports setup as `succeeded`; this prevents an agent from testing
while Python or npm packages are still being installed.

Its archive slot must contain:

```text
scripts/dev stop
```

`setup` creates a local virtual environment, installs declared dependencies,
installs the Playwright browser, assigns a stable free port pair, routes writable
app data to `.local/`, and enables the checked-in Git hooks. It does not start the
app.

## Test the change

The author uses the smallest useful gate while working:

```bash
scripts/dev test fast
```

Before review:

```bash
scripts/dev test full
scripts/dev smoke
```

All test tiers use a scratch application home and remove paid-provider
credentials. `provider` is the only paid tier and requires both an explicit
provider and a spend cap. It currently reports `skipped` because no paid replay
harness has been approved.

For an early merge check against `main`:

```bash
scripts/dev test full --merge-base main
```

This creates a disposable merge-test worktree, tests the synthetic merge, and
removes that worktree afterward. GitHub remains authoritative because `main`
may move later.

To run the app for manual testing:

```bash
scripts/dev run --ttl 30m
```

The TTL prevents forgotten servers. From another terminal, stop this worktree
with `scripts/dev stop`, or all managed worktrees with `scripts/dev stop --all`.

## Request the opposite-provider review

The author commits, leaves the tree clean, pushes the SHA, and reports it to the
coordinator. The trusted coordinator—not the author agent—runs:

```bash
OPENNOLAN_REVIEW_COORDINATOR_DIR=<protected-path> \
OPENNOLAN_REVIEW_STATUS_TOKEN=<github-app-token> \
OPENNOLAN_REVIEW_STATUS_APP_ID=<github-app-id> \
scripts/dev review request --sha <full-sha> --author-provider claude
```

Use `--author-provider codex` for a Codex-authored change. The command refuses
storage inside the repository, refuses a missing coordinator credential, and
verifies that branch protection binds `review-current` to the configured App
before it creates or dispatches review work.

The mapping is fixed:

| Author | Reviewer |
|---|---|
| Claude | Codex |
| Codex | Claude |

The command creates an Orca run and task, checks out a clean worktree named
`review-<short-sha>-<provider>` at the exact commit, starts the opposite
provider, and injects a review-only brief. The reviewer checks correctness,
tests, architecture, security, performance, readability, accessibility, and
regression risk. It runs FULL, smoke, and the disposable merge test against
`main`, then rechecks the exact SHA and clean tree before publishing. It must not
edit the author's branch.

The reviewer publishes one of these verdicts:

```bash
mkdir -p .local/reviews/drafts
scripts/dev review publish \
  --sha <full-sha> \
  --author-provider claude \
  --reviewer-provider codex \
  --verdict approved \
  --summary "full and smoke passed; no blocking findings" \
  --report .local/reviews/drafts/<sha>.md \
  --task-id <injected-task-id> \
  --dispatch-id <injected-dispatch-id> \
  --request-nonce <nonce-from-review-brief>
```

Allowed verdicts are `approved`, `changes_requested`, and `blocked`. The command
refuses self-review, secrets, report files outside the local draft directory, and
a dirty or wrong-SHA review tree. It sends `worker_done` through the reviewer’s
active Orca dispatch. It cannot publish to GitHub and has no GitHub status
credential.

Secret scanning deliberately fails closed on high-entropy, base64-shaped text.
Do not paste base64 fixtures into a public review report; describe the fixture or
refer to its repository path instead. There is no publication override.

The coordinator receives that attested Orca result, writes its payload to a
protected coordinator inbox, and runs this from a trusted checkout:

```bash
OPENNOLAN_REVIEW_COORDINATOR_DIR=<protected-path> \
OPENNOLAN_REVIEW_STATUS_TOKEN=<github-app-token> \
OPENNOLAN_REVIEW_STATUS_APP_ID=<github-app-id> \
scripts/dev review finalize --result <protected-path>/inbox/<delivery>.json
```

The same protected directory and credential are used for request and finalize.
The token is injected only into this coordinator process; never put it in
`.env.worktree`, an agent terminal, GitHub Actions, or the repository.
Finalization checks the
coordinator request, nonce, task, completed dispatch, reviewer terminal, clean
review worktree, and exact SHA before it publishes a redacted commit comment and
the `review-current` status. A later commit has no such status and needs a new
review.

When changes are requested, the Orca coordinator sends the findings back to the
original author. The PR stays open. The author fixes, reruns FULL plus smoke,
commits, pushes, and requests a new review. A disputed finding becomes a manager
decision; agents do not settle it by silently changing the architecture.

## What Git and GitHub enforce

```text
pre-commit   identity + FAST                  blocks on failure
post-commit  local SHA + Orca card update     never blocks
pre-push     account + FULL + review warning  review warning does not block
GitHub PR    FULL + smoke on merge checkout   blocks on failure
main         manager approval + resolved discussion
             + merge-ready + review-current
```

GitHub branch protection must bind `review-current` to the coordinator GitHub
App's numeric ID. Requiring the context name alone is self-approvable by any
credential allowed to create commit statuses. `scripts/dev doctor --remote`
fails until the exact App binding is present. The pre-push review message remains
only a warning because GitHub must receive the commit before it can be reviewed.

## Keep the manager informed

Update the Orca card after reproduction, implementation, validation, handoff, or
a blocker. Keep the PR description current with the problem, owner/provider,
architecture choice and why, tests, exact reviewed SHA, risks, rollback, and open
decisions. The checked-in PR template lists those fields.

Pause for manager approval before changing a public API or stored-data shape,
authentication or secret handling, the agent sandbox, a paid provider, a major
dependency, deployment/release/migration behavior, or architecture spanning
several parts of the product.

## Cleanup and evidence

Use `scripts/dev reap --dry-run` to see which old worktrees are safe to remove.
`scripts/dev reap` removes only a worktree that is merged into `main`, clean,
fully pushed, and idle. Anything else is reported and kept.

Failure evidence lives under `.local/test-results/`. Before evidence leaves the
machine, use the bounded analyzer rather than reading raw session logs:

```bash
python scripts/debug_session.py latest --redact
```

The export removes secrets, local paths, and private project text.
