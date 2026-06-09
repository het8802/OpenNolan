# Hourly Marketing Cron Reference

This reference captures a concrete Marketing OS cron setup that can be adapted for future users.

## Job shape

Use four independent cron jobs:

1. **Hourly trend scout**
   - Schedule: `0 * * * *`
   - Delivery: `local`
   - Toolsets: `web`, `browser`, `terminal`, `file`, `code_execution`, `delegation`, `session_search`
   - Purpose: gather trend evidence without spamming the user.
   - Output: `~/marketing-os/trends/YYYY-MM-DD.jsonl`

2. **Daily ideas report**
   - Schedule: `0 17 * * *` (or user timezone equivalent)
   - Delivery: `origin`
   - Purpose: present exactly 10 approval-ready ideas.
   - Include approval instruction: `Reply with the numbers you approve, e.g. approve 1, 4, 7.`

3. **Approved scripts**
   - Schedule: `0 19 * * *`
   - Delivery: `origin`
   - Purpose: find explicit approvals in recent session history, deepen research, write scripts.
   - Output: `~/marketing-os/scripts/YYYY-MM-DD/idea-N.md`

4. **Production**
   - Schedule: `0 21 * * *`
   - Delivery: `origin`
   - Toolsets add: `image_gen`, `vision`, `video`
   - Purpose: generate images/assets and Remotion/ffmpeg video deliverables when possible.
   - Output: `~/marketing-os/productions/YYYY-MM-DD/idea-N/`

## Critical prompt clauses

### Trend scout

- Do not post to social platforms.
- Do not ask the user questions.
- Do not deliver a long human-facing report.
- Save each JSONL record with: `timestamp_utc`, `platform`, `trend_name`, `evidence_url`, `evidence_summary`, `why_it_matters`, `suggested_angle`, `confidence_1_to_5`, `source_type`, and `tool_gap_or_recommended_integration`.
- Avoid duplicates already present in today's file.
- Maintain `~/marketing-os/tool-wishlist.md` for exact tool/API/Actor recommendations discovered during research.
- Tool recommendations must prefer free options, name exact Apify Actors/apps instead of saying “Apify”, include pricing for paid tools, compare free alternatives, state ROI, and check startup credits/discounts.
- Verify JSONL exists and is valid before finalizing.

### Ideas report

- Read today's trend log first; if sparse, run fresh research.
- Output exactly 10 numbered ideas.
- For each idea include title/angle, platform fit, source links, hook, format, why it works, complexity, and next step.
- Do not claim guaranteed virality.
- Prefer ideas that can be scripted by the next deadline and produced by the production deadline.

### Script job

- Use `session_search` for approvals: `approve`, `approved`, `approving`, `Daily Marketing Ideas`.
- Treat approvals as explicit only when the user names idea numbers or otherwise clearly indicates selections.
- If no clear approvals exist, send a concise reminder.
- For each approved idea include title, objective, audience assumption, 3 hook options, final hook, timestamped script, on-screen text, voiceover, visual direction, image prompts, CTA, caption, hashtags, and Remotion notes.

### Production job

- Read today's script files.
- Check session history for script approvals/rejections/changes.
- If no scripts or unclear approvals exist, explain what is missing.
- Create a production folder per idea.
- Use `image_gen` for visuals when appropriate.
- Generate Remotion component/project or a render plan for 1080x1920 vertical shorts.
- Use ffmpeg where useful.
- If rendering is blocked, produce ready-to-run code and exact commands.
- Never claim a rendered video exists unless verified.
- Include `MEDIA:/absolute/path/to/file` only for files that exist.

## Timezone handling

Always run `date '+%Y-%m-%d %H:%M:%S %Z %z'` or equivalent before schedule creation. If the user did not specify timezone, use the server timezone and state the assumption in the final response. Offer to update schedules if they meant a different timezone.

## Approval gates

Default gates:

1. Ideas: user approves numbers.
2. Scripts: if user requests script changes, production pauses for those items.
3. Posting: never post automatically unless the user has explicitly authorized autoposting and scope.

## Useful external integrations

- Apify for trend and competitor scraping.
- Buffer/Metricool/Publer for cross-platform scheduling.
- X via `xurl` after user configures credentials locally.
- YouTube Data API for uploads/analytics.
- Meta Graph API for Instagram professional accounts.
- TikTok Content Posting API; expect app review for direct posting.
