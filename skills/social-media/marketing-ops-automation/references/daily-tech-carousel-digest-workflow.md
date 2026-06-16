# Daily Tech Carousel Digest Workflow

Use this reference for a secondary Instagram AI/Tech carousel workflow: a daily "What happened in tech so far" digest built from research signals that did **not** become the main selected talking-head concept.

## Purpose

Keep the existing selected-idea workflow unchanged:

- 4-hour research collector gathers broad AI/tech/startup/funding/opportunity signals.
- Daily concept/script job selects one strongest idea for the main talking-head script.
- Daily selected-idea carousel job builds a full carousel around that winning idea.

Add a separate draft-only workflow that converts the remaining strong research signals into a multi-topic digest carousel where one slide equals one idea.

## Recommended Architecture

1. **Research source**
   - Read `~/content-os/research/latest.md`.
   - Read current/previous day `~/content-os/research/YYYY-MM-DD/*.jsonl`.
   - Read `daily-5pm-synthesis.md` and the daily script to identify the main selected winner.
   - Exclude or de-emphasize the selected winner so the digest does not duplicate the main workflow.

2. **7AM draft-generation cron**
   - Schedule: `0 7 * * *` in your local timezone.
   - Skills: `marketing-ops-automation`, `content-os-tools`, `instagram-carousel`, `instagram-reels` if needed for hook alignment, and `codex` for deterministic local image generation.
   - Deliver draft to your delivery channel once the user creates/invites the bot.

3. **Output folder**

```text
~/content-os/daily-carousels/YYYY-MM-DD/what-happened-in-tech/
```

Expected files:

```text
README.md
research-selection.md
carousel-copy.md
caption.md
posting-times.md
approval-status.json
asset-ledger.jsonl
contact-sheet.png
slide-01.png
slide-02.png
...
```

4. **Carousel shape**
   - Slide 1: cover, e.g. "What happened in tech so far".
   - Slides 2-N: one source-backed tech/startup signal per slide.
   - Final slide: save-worthy pattern/CTA.
   - Prefer 6-8 topic slides, not an overloaded news dump.

5. **Selection rules**
   - Prefer high-confidence official announcements, funding reports, product launches, startup opportunities, benchmarks, and builder-market signals.
   - Use creator/social observations as supporting texture unless they are directly verified.
   - Each selected topic needs a one-line source-backed claim, why it matters, and a founder/operator implication.

6. **Codex generation pattern**
   - First write `carousel-copy.md` and slide art direction.
   - Generate per-slide prompts for Codex/local asset generation.
   - Use Codex to create 1080x1350 PNG slides in numeric order.
   - Generate `contact-sheet.png` and QA for mobile readability, cropping, spelling, factual source labels, and premium editorial aesthetic.

## Approval Gate

Hard rule: **never post the carousel automatically before explicit user approval.**

Track status in `approval-status.json`:

```json
{
  "status": "pending",
  "approved_at": null,
  "approved_by": null,
  "scheduled_for": null,
  "posted_at": null
}
```

Accepted approval language includes: `approve carousel`, `approve today's carousel`, `approved`, `post this`, `schedule this`.

Revision language includes: `revise slide 3`, `remove topic 5`, `change cover`, `make it more startup-focused`.

## Posting-Time Baseline

Use these defaults (in your local timezone) until your own Instagram Insights provide better data. These windows were derived from a creator's posting-time analysis. Treat them as creator-sourced hypotheses, not account-specific proof.

| Day | Primary target | Same-day fallback | Other saved window |
|---|---:|---:|---:|
| Monday | 12:15 PM | 8:00 PM | 7:30 AM |
| Tuesday | 1:00 PM | 7:30 PM | 8:15 AM |
| Wednesday | 2:00 PM | 9:15 PM | 9:00 AM |
| Thursday | 1:45 PM | 8:30 PM | 8:45 AM |
| Friday | 11:45 AM | 6:15 PM | 7:00 AM |
| Saturday | 3:00 PM | 8:00 PM | 10:30 AM |
| Sunday | 1:30 PM | 7:45 PM | 9:45 AM |

Operational rule: prefer the primary lunch/early-afternoon slot when the carousel is approved in time; use the evening fallback if approval lands late; use morning slots mainly for fully pre-approved/pre-scheduled content.

## Delivery Channel

Use a separate channel for draft review/approval. The content-OS may not be able to create channels directly; the user should create it and invite the bot, then configure the cron delivery to that channel ID.

## Common Pitfalls

- Do not replace the main selected-idea script/carousel workflow with this digest workflow.
- Do not post or schedule posting from the 7AM generation job; it drafts only.
- Do not include weak, unverified Instagram search snippets as factual claims.
- Do not make every slide visually identical; it should feel like a premium editorial digest, not a spreadsheet.
- Do not call the digest "research" only; it must generate draft carousel assets and approval status.