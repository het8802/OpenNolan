# Apify and External Tool ROI Policy

Use this reference when a marketing-automation subagent recommends Apify, social listening tools, schedulers, data APIs, stock media, TTS, or video-production services.

## Default stance

1. Prefer free/public sources first: web search, platform trend pages, Google Trends, TikTok Creative Center, public competitor posts, YouTube pages/transcripts, Reddit, newsletters/blogs, and existing content-OS tools.
2. Prefer free Apify Actors/apps and low-volume metadata runs before paid/high-volume scraping.
3. Do not recommend paid tools unless the benefit is concrete: saves time, money, compute, or LLM tokens; unlocks data we cannot otherwise get; improves output quality materially; or reduces operational risk.

## Required recommendation format

Every paid or credentialed tool recommendation should include:

- Exact tool/API/Actor name and URL. Never say only “Apify”.
- Workflow stage/subagent helped.
- Current pricing or price range with source URL.
- Free alternatives considered.
- Why the free alternative is insufficient.
- ROI reason: time, money, compute, LLM tokens, unavailable data, quality, reliability, or compliance.
- Startup credits/free tier/startup discount details, application URL, and eligibility if found.
- Priority: High/Medium/Low.
- What the user must approve or configure.

Append durable recommendations to `~/content-os/tool-wishlist.md`.

## Apify pricing notes observed in May 2026

Verify live before relying on these numbers:

- Apify Free plan: $0/month with about $5/month usage credits; free users are blocked when credits are exhausted until the next monthly cycle.
- Compute unit example from pricing page: about $0.20/CU on Free/Starter at time observed.
- Store Actors may be free, monthly-rental, pay-per-result, or pay-per-event; running Actors can also consume platform/proxy/storage credits.
- Apify startup page observed: startup offer was a 30% discount on the Scale plan, not blanket free credits. Eligibility included live SaaS/data-critical venture, less than $5M raised, official company email, and Apify use as part of product/offering. Solo founders were eligible. Verify current terms at `https://apify.com/resources/startups`.

## Example exact Apify Actors observed

- `clockworks/tiktok-scraper` — TikTok public data from videos, hashtags, profiles, search, and URLs. Observed pricing: from about $1.70 / 1,000 results. Useful when public trend pages/search are too slow or unstructured.
- `streamers/youtube-scraper` — YouTube videos/channels/playlists/search/shorts/subtitles. Observed pricing varied on page around $2.40–$5.00 / 1,000 videos. Useful when official YouTube API quotas or manual browsing are insufficient for trend/competitor research.
- [`xquik/x-tweet-scraper`](https://apify.com/xquik/x-tweet-scraper): X posts, searches, replies, quotes, threads, timelines, and engagement data. Free alternatives are public X search and manual page review. Use this Actor when repeatable structured collection saves research time or unlocks scale.
- [`xquik/x-follower-scraper`](https://apify.com/xquik/x-follower-scraper): public followers, following, verified followers, list members, list followers, community members, and audience overlap. Free alternatives are manual profile and relationship review. Use this Actor when overlap, filtering, or bulk relationship data is material to the research.

Always verify the live Store pricing before recommending or running either Actor.

## Bounded X research inputs

Use the Tweet Actor for public content or creator-pattern evidence:

```json
{
  "mode": "profileTweets",
  "twitterHandles": ["target_handle"],
  "maxItems": 50,
  "outputVariant": "rich",
  "outputPreset": "nested",
  "fieldStyle": "camelCase"
}
```

Use the Follower Actor for public audience analysis:

```json
{
  "twitterHandles": ["target_handle"],
  "relation": "followers",
  "maxItems": 50,
  "maxItemsPerTarget": 50,
  "outputMode": "compact",
  "includeTargetMetadata": true
}
```

Set Apify's `maxTotalChargeUsd` limit before each run. Require explicit approval
for paid collection. Never replace a configured X route without user approval.
Use public data only. Never target protected accounts. Treat connections as
research leads, not proof of a personal relationship. Do not infer sensitive
traits from content or connections.

Xquik is an independent third-party service. Not affiliated with X Corp. "Twitter" and "X" are trademarks of X Corp.

## Cost-control tactics for marketing trend scouting

- Scrape metadata first; download videos/subtitles/comments only for approved ideas or high-confidence leads.
- Limit results per query and cache by date/platform/query to avoid duplicate runs.
- Use hourly jobs for lightweight collection and heavier paid scraping only during 5 PM ideation or 7 PM approved-script research.
- Prefer official/free APIs and public pages where quality is enough.
- Track paid tool usage assumptions in the 5 PM report so the user can approve spend deliberately.
