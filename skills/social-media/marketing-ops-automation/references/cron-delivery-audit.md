# Cron delivery audit for Marketing OS

Use this reference when a user asks where scheduled Marketing OS results go, whether Slack gets them, or which channel/thread receives them.

## Audit workflow

1. List cron jobs with the cron tool/CLI first.
   - Note job id, schedule, enabled state, last status, and delivery mode.
2. Inspect live config at `~/.hermes/cron/jobs.json`.
   - For each job, record `deliver` and `origin`.
   - `deliver: "origin"` means send back to the platform/chat/thread where the job was created.
   - `deliver: "local"` means save local output only; do not tell the user it posted to Slack/Teams/etc.
3. Resolve destination IDs through `~/.hermes/channel_directory.json`.
   - Pay attention to `type`: Slack IDs beginning with `D` are DMs; IDs beginning with `C` are channels.
   - A destination can be a thread/topic inside a DM, not a public `#channel`.
4. Check `~/.hermes/cron/output/<job-id>/` for the latest run artifact.
   - Verify whether the job produced human-facing output.
   - Check last status / delivery error fields before claiming delivery succeeded.
5. Answer in concrete terms: job name, id, delivery mode, platform, chat/thread, and whether it is a DM or channel.

## Pitfall from Marketing OS Slack setup

A Marketing OS setup had these jobs:

- Hourly trend scout: `deliver=local`; it wrote trend records and tool wishlist files only.
- 5 PM ideas, 7 PM approved scripts, and 9 PM production: `deliver=origin`; origin was Slack.

The Slack destination in `channel_directory.json` was:

- `D0B27UMPKC4:1778119452.868099`
- label: `D0B27UMPKC4 / topic 1778119452.868099`
- type: `dm`
- thread_id: `1778119452.868099`

A separate public channel existed:

- `hermes-home`
- id: `C0B2YLFBL72`
- type: `channel`

The correct answer was that user-facing jobs go to the original Slack DM thread, not `#hermes-home`. Avoid saying "channel" unless the directory type is actually `channel`; say "Slack DM thread/topic" when type is `dm`.

## Changing destination to a named Slack channel

When Het asks to move Marketing OS output to a named Slack channel:

1. Use `send_message(action="list")` to refresh visible Slack targets.
2. If the channel is not listed but the user says the app was added, try a direct target such as `slack:#marketing-os` for a low-impact test message when explicitly requested.
3. Record the returned Slack channel ID (Slack channel IDs usually start with `C`).
4. Update the three user-facing cron jobs to `deliver: "slack:<channel-id>"`; do not leave them as `origin` if the destination should be the named channel.
5. Patch stale prompt text that says "origin thread" or references an old destination, so the job's self-description matches the actual delivery mode.
6. Re-list cron jobs and messaging targets to verify.

## Pacific-time scheduling pitfall

Het is in the Bay Area and expects stated cron times to mean `America/Los_Angeles` time unless he says otherwise. The scheduler stores 5-field cron expressions in the scheduler/server timezone, so convert Pacific local times before saving. In May 2026 the live zone is PDT (UTC-7):

- 5 PM Pacific -> `0 0 * * *` UTC, next local run 5:00 PM PDT.
- 7 PM Pacific -> `0 2 * * *` UTC, next local run 7:00 PM PDT.
- 9 PM Pacific -> `0 4 * * *` UTC, next local run 9:00 PM PDT.

Always verify live offset with `TZ=America/Los_Angeles date` or Python `zoneinfo`; daylight saving changes mean these UTC hours can differ in PST vs PDT.

## Safe wording

"The user-facing jobs use `deliver=origin`, so they go to the original Slack DM thread where the workflow was created: `<chat_id>` / thread `<thread_id>`. The hourly scout is `deliver=local`, so it does not post to Slack. I also found `<channel-name>` exists, but these jobs are not pointed there."

After moving to a named channel: "The user-facing jobs now use `deliver=slack:<channel-id>`, so they post to `#marketing-os`. The hourly trend scout remains `local` and does not post hourly updates. The schedules are converted from Pacific time to the scheduler timezone; next runs are `<UTC>` = `<Pacific>`."
