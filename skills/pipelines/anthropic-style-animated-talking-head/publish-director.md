# Publish Director — anthropic-style-animated-talking-head

## When to Use
The final reel is rendered and QA'd. Produce the `publish_log`.

## Prerequisites
| Layer | Resource |
|-------|----------|
| Render | `artifacts/render_report.json` + the final MP4 |
| Research | `artifacts/research_brief.json` (for the source list) |

## Do this
1. **Caption/hook.** Lead with the strongest line. Where the reel makes claims, the caption
   should be honest and may name sources (claim integrity carries into copy — reported ≠ verified).
2. **Hashtags.** Relevant AI/tech/startup tags; no overclaiming.
3. **Cover concept.** A frame or designed cover in the warm editorial look (ivory + clay, Fraunces).
4. **Source list.** List every on-screen claim with its source URL (from research_brief) so the
   creator can answer questions / add to the caption.
5. **Optional:** run `content_signal` (advisory virality score; never blocks publish — short-form only).

## Output: `publish_log`
- caption, hashtags[], cover_concept, sources[] (claim → url), content_signal? (advisory)

## Self-evaluate
- Caption + hashtags + cover present; every on-screen claim has a listed source.
