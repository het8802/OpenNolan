# Instagram AI/Tech Script Engine notes

Use this when the user wants the old Marketing OS reduced to an Instagram-focused AI/tech content engine.

## Operating shape

- Keep the research + script loop; do not assume the user wants full Marketing OS production/posting.
- User mostly records talking-head Instagram videos and uses supporting images/videos as edit inserts.
- Research should cover AI tools/products, AI companies, funding, startup programs, credits/perks, events, devtools, agents, infrastructure, and creator-pattern signal.
- Legacy storage paths may still be `~/.hermes/marketing-os/...`; treat those paths as historical storage, not as a sign the content should be generic marketing advice.

## Cron pattern

Recommended active jobs:

1. **4-hour research collector**
   - Schedule: `every 240m` unless the user requests a different cadence.
   - Delivery: `local`.
   - Writes JSONL evidence and rolling summaries under `~/.hermes/marketing-os/research/`.

2. **Daily concept + talking script**
   - Delivery: valid named messaging target, e.g. `slack:marketing-os`, not stale raw channel IDs.
   - Output should be a creator brief for talking-head filming, not only word-for-word VO.
   - Include concept, why it won, source URLs, hook rationale, flexible talking flow, optional VO, on-screen text, CTA/caption/hashtags, and an `Asset/B-roll brief for follow-up cron` section.

3. **Daily asset + B-roll pack**
   - Schedule: 15–60 minutes after the script job.
   - Skills: `marketing-ops-automation`, `marketing-os-tools`, `codex`.
   - Creates `~/.hermes/marketing-os/assets/YYYY-MM-DD/daily-ai-tech-video/` with:
     - `README.md`
     - `asset-ledger.jsonl`
     - `broll-suggestions.md`
     - 5–10 generated SVG/PNG assets when possible
   - Use Codex from a git repo such as `/home/ubuntu/instagram-ai-tech-assets` to generate SVG/HTML/Python asset code, then copy finished outputs into the asset package.
   - If Codex fails, fall back to simple SVG assets and explicitly report the fallback.

## Asset pack expectations

- Assets support a talking-head edit; they are not a finished produced video.
- Favor diagrams, headline cards, workflow maps, comparison cards, checklists, mock UI screens, source-backed quote cards, and abstract tech/editorial cutaways.
- Use 1080x1920 or editor-friendly overlays/cards.
- Suggest licensed B-roll via Pexels/Pixabay search URLs or source-page screenshot/screen-recording ideas.
- Do not recommend random YouTube footage unless reuse permission/license is clear.
- Avoid copyrighted logos as standalone creative assets; source-page screenshots may be suggested as editorial references with URLs.

## Common pitfalls

- Leaving delivery as a stale raw Slack channel ID can cause `channel_not_found`; prefer named targets from `send_message action=list`.
- A job named “hourly” may have an interval like `every 360m`; audit the live cron schedule before describing cadence.
- Do not delete paused legacy production jobs unless the user explicitly asks; leaving them paused is a safe cleanup boundary.
