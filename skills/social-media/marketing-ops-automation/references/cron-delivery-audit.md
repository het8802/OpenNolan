# Cron delivery audit for Content OS

Use this reference when a user asks where scheduled Content OS results go, whether the delivery channel gets them, or which channel/thread receives them.

## Audit workflow

1. List cron jobs with the cron tool/CLI first.
   - Note job id, schedule, enabled state, last status, and delivery mode.
2. Inspect live config at `~/cron/jobs.json`.
   - For each job, record `deliver` and `origin`.
   - `deliver: "origin"` means send back to the platform/chat/thread where the job was created.
   - `deliver: "local"` means save local output only; do not tell the user it posted to any channel.
3. Resolve destination IDs through `~/channel_directory.json`.
   - Pay attention to `type`: some IDs map to DMs; others map to channels.
   - A destination can be a thread/topic inside a DM, not a public channel.
4. Check `~/cron/output/<job-id>/` for the latest run artifact.
   - Verify whether the job produced human-facing output.
   - Check last status / delivery error fields before claiming delivery succeeded.
5. Answer in concrete terms: job name, id, delivery mode, platform, chat/thread, and whether it is a DM or channel.

## Pitfall from a Content OS delivery-channel setup

A Content OS setup had these jobs:

- Hourly trend scout: `deliver=local`; it wrote trend records and tool wishlist files only.
- 5 PM ideas, 7 PM approved scripts, and 9 PM production: `deliver=origin`; origin was the delivery channel.

The destination in `channel_directory.json` resolved to a DM thread/topic, not a public channel:

- a DM-type entry with a thread/topic id (`type: dm`, plus a `thread_id`)

A separate public channel also existed in the directory (`type: channel`).

The correct answer was that user-facing jobs go to the original DM thread, not the public channel. Avoid saying "channel" unless the directory type is actually `channel`; say "DM thread/topic" when type is `dm`.

## Changing destination to a named channel

When the user asks to move Content OS output to a named channel:

1. Use `send_message(action="list")` to refresh visible messaging targets.
2. If the channel is not listed but the user says the app was added, try a direct target for a low-impact test message when explicitly requested.
3. Record the returned channel ID.
4. Update the three user-facing cron jobs to `deliver: "<channel-id>"`; do not leave them as `origin` if the destination should be the named channel.
5. Patch stale prompt text that says "origin thread" or references an old destination, so the job's self-description matches the actual delivery mode.
6. Re-list cron jobs and messaging targets to verify.

## Timezone scheduling pitfall

Treat stated cron times as your local timezone unless the user says otherwise. The scheduler stores 5-field cron expressions in the scheduler/server timezone, so convert local times before saving. For example, when the local zone is UTC-7:

- 5 PM local -> `0 0 * * *` UTC, next local run 5:00 PM.
- 7 PM local -> `0 2 * * *` UTC, next local run 7:00 PM.
- 9 PM local -> `0 4 * * *` UTC, next local run 9:00 PM.

Always verify live offset with `TZ=<your timezone> date` or Python `zoneinfo`; daylight saving changes mean these UTC hours can differ across the year.

## Safe wording

"The user-facing jobs use `deliver=origin`, so they go to the original DM thread where the workflow was created: `<chat_id>` / thread `<thread_id>`. The hourly scout is `deliver=local`, so it does not post to any channel. I also found `<channel-name>` exists, but these jobs are not pointed there."

After moving to a named channel: "The user-facing jobs now use `deliver=<channel-id>`, so they post to the named channel. The hourly trend scout remains `local` and does not post hourly updates. The schedules are converted from your local timezone to the scheduler timezone; next runs are `<UTC>` = `<local>`."
