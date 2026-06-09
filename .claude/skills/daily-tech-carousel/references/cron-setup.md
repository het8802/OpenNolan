# Daily Tech Carousel Hermes Cron Setup

This reference captures the proven Hermes architecture for Het's secondary daily Instagram carousel workflow.

## Purpose

Use this when implementing or auditing the daily `What happened in tech so far` carousel system. It is separate from the main selected-idea workflow:

- 4-hour research collector gathers many AI/tech/startup signals.
- Daily selected concept/script job picks one winner.
- Selected-idea carousel job creates a full carousel for that winner.
- Daily tech carousel job turns the **other strong signals** into a digest carousel.

## Live setup created 2026-05-20

- Draft skill: `daily-tech-carousel`
- Draft delivery Slack target: `slack:daily-carousels`
- Resolved Slack channel ID during setup: `C0B4ZMJETA7`
- Draft cron job: `e3924461ec90`
  - Name: `Instagram AI/Tech — daily What Happened in Tech carousel draft`
  - Schedule: `0 7 * * *` / 7:00 AM America/Los_Angeles
  - Delivery: `slack:daily-carousels`
  - Output: `~/.hermes/marketing-os/daily-carousels/YYYY-MM-DD/what-happened-in-tech/`
- Approval/posting checker job: `b7982a1a1adb`
  - Name: `Instagram AI/Tech — approved daily carousel posting checker`
  - Schedule: `every 30m`
  - Delivery: `local`
  - Only posts after explicit approval.
- Existing selected-idea carousel job remains unchanged: `14edf0fbfbcd` at 6:00 AM PT to `slack:marketing-os`.

## Setup sequence

1. Confirm `daily-tech-carousel`, `instagram-carousel`, `marketing-ops-automation`, `marketing-os-tools`, `codex`, `composio-connected-tools`, and Slack skills are available.
2. Verify Slack target by sending a low-impact test to `slack:daily-carousels`.
3. Create the 7AM draft cron with these requirements:
   - load `daily-tech-carousel` as primary skill;
   - read research files from last 24h;
   - exclude/de-emphasize the selected daily winner;
   - generate PNG slides, contact sheet, copy, caption, Codex prompts, ledger, and `approval-status.json`;
   - deliver to `slack:daily-carousels` with `pending approval` and explicit approval/revision commands;
   - never post.
4. Create a separate posting checker cron:
   - runs every 30 minutes;
   - reads `approval-status.json` and session_search for explicit approval;
   - checks the weekday posting-time baseline;
   - posts only when status is approved and the due window has arrived;
   - falls back to a Slack reminder if Instagram posting fails.
5. Verify Instagram capability with a safe read-only Composio call before relying on auto-posting:
   ```bash
   composio execute INSTAGRAM_GET_USER_INFO -d '{"ig_user_id":"me"}'
   ```

## Approval contract

Accepted approval examples for the digest carousel:

- `approve carousel`
- `approve today's carousel`
- `approved`
- `post this`
- `schedule this`

Revision/rejection examples:

- `revise slide 3: ...`
- `remove topic 5`
- `reject carousel`

Do not treat generation success, Slack delivery, or silence as approval.

## Posting-time baseline

Use America/Los_Angeles unless Het says otherwise. Het shared @digitally_create_ post `instagram.com/p/DYfoT0jEY90/` on 2026-05-28 and asked to save its suggested posting windows.

| Day | Primary target | Same-day fallback | Other saved window |
| --- | --- | --- | --- |
| Monday | 12:15 PM PT | 8:00 PM PT | 7:30 AM PT |
| Tuesday | 1:00 PM PT | 7:30 PM PT | 8:15 AM PT |
| Wednesday | 2:00 PM PT | 9:15 PM PT | 9:00 AM PT |
| Thursday | 1:45 PM PT | 8:30 PM PT | 8:45 AM PT |
| Friday | 11:45 AM PT | 6:15 PM PT | 7:00 AM PT |
| Saturday | 3:00 PM PT | 8:00 PM PT | 10:30 AM PT |
| Sunday | 1:30 PM PT | 7:45 PM PT | 9:45 AM PT |

Treat these as creator-sourced timing hypotheses, not account-specific Instagram Insights. Prefer the primary lunch/early-afternoon slot when a carousel is approved in time; use the evening fallback when approval lands late. Morning windows are mainly useful for fully pre-approved/pre-scheduled content. Replace these once Het's own Instagram Insights provide stronger evidence.

## Composio Instagram note

A safe read-only check on 2026-05-20 verified:

- username: `yoki.tsum`
- ig_user_id: `27343152321974797`
- account_type: `MEDIA_CREATOR`

Future posting runs should still re-verify before posting and use the numeric id returned at runtime.

## Pitfalls

- Do not create one monolithic cron that generates and posts. Keep generation and posting separate so approval gates stay enforceable.
- Do not post when `approval-status.json` is missing, pending, rejected, or ambiguous.
- Do not duplicate the main selected-idea carousel; the digest exists to cover other research signals.
- Do not claim Slack media was uploaded unless the message/send result confirms it; include local paths as backup.
- If Instagram posting fails, update status with the error and send a reminder/package instead of retrying blindly, to avoid duplicates.
