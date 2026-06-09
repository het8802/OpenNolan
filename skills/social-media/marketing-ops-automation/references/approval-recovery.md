# Marketing OS approval recovery and late approvals

Session-derived pitfalls for approval-gated cron workflows.

## Problem observed

A user approved idea numbers after the scheduled 7 PM script job had already run. The job output said it could not find clear approvals, so no scripts existed under `~/marketing-os/scripts/YYYY-MM-DD/`. A later user asked for the scripts and expected them to exist.

## Recovery pattern

1. Check live time and relevant run outputs:
   - `date`
   - `~/cron/output/<script-job-id>/...md`
   - `~/marketing-os/scripts/YYYY-MM-DD/`
2. If scripts are missing, do not claim the cron job created them.
3. Record the approval in `~/marketing-os/approvals/YYYY-MM-DD.md` with approved numbers and any approved tools/spend caps.
4. If using `cronjob(action="run")`, remember it triggers the job on the scheduler's next tick; it does not provide the generated content synchronously in the tool result.
5. If the user asks for the deliverable now, produce it now and save the expected files, instead of only saying the scheduled job will do it.
6. After script approval, record a separate production approval file if needed and trigger production only after scripts exist.

## Cron prompt hardening

Cron prompts should read approval files as well as session history:

- `~/marketing-os/approvals/YYYY-MM-DD.md`
- `~/marketing-os/approvals/YYYY-MM-DD-scripts-approved.md`
- recent session history via `session_search`

This avoids missed approvals when session search cannot see the exact thread context or when approval came after the scheduled run.

## User-facing wording

Use precise status language:

- Good: "I recorded the approval and triggered the next job; I will verify generated files before saying they exist."
- Bad: "The job can process it now" when no files have been created yet.

If the user asks "did you already create it?", inspect files and cron output first, then answer plainly.