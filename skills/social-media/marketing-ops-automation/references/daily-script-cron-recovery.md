# Daily Script Cron Recovery

Use this when the user asks for "today's script" and the scheduled Instagram AI/Tech daily concept/script job failed or no `scripts/YYYY-MM-DD/daily-ai-tech-video.md` exists.

## Fast recovery pattern

1. Check the target date in your local timezone.
2. Verify whether the expected script file exists under:
   - `~/marketing-os/scripts/YYYY-MM-DD/daily-ai-tech-video.md`
3. If missing, inspect the live cron status/output enough to know whether the scheduled job failed or is still pending.
4. Do **not** stop at "the cron failed" or only trigger the cron and wait. The user asked for the deliverable now.
5. Build the script manually from the current research corpus:
   - `~/marketing-os/research/latest.md`
   - today's `research/YYYY-MM-DD/*.jsonl` if present
   - previous day's synthesis/research if today's collector is empty
   - authoritative web/source extracts for the top claims
6. Choose the strongest source-backed founder/operator angle, then write the normal daily script shape:
   - concept title
   - why this won today
   - source-backed evidence
   - creator hook rationale
   - FigJam hook combo
   - flexible talking script
   - optional word-for-word VO
   - on-screen text ideas
   - caption/CTA/hashtags
   - asset/B-roll brief
7. Save the recovered file to the normal path so downstream carousel/asset jobs can use it:
   - `~/marketing-os/scripts/YYYY-MM-DD/daily-ai-tech-video.md`
8. Verify by reading the saved file before replying.
9. Tell the user concisely that the cron failed and you recovered the script manually; include the path and the usable talking script.

## Pitfalls

- Do not present yesterday's script as today's just because it is the newest existing file.
- Do not wait on a manually triggered cron if it does not produce output quickly; generate the script in-session.
- Do not use unverified research summary claims as final evidence for numbers/dates; verify major claims against source pages before saving.
- Do not over-deliver internal debugging detail to the user; lead with the script and only briefly mention recovery status.
