# Research Director — anthropic-style-animated-talking-head

## When to Use
You have a `script` with a `claims[]` list extracted from the creator's VO. For **every
verifiable claim**, find a credible real source and prepare the proof so the scene/asset
directors can show it on-screen with the exact phrase highlighted. This is the distinctive
gate of this pipeline — *"when the creator states a claim, the agent researches a credible
article and shows it as an overlay/animation with that claim highlighted."*

## Prerequisites
| Layer | Resource |
|-------|----------|
| Script | `artifacts/script.json` — `claims[]` (verbatim spoken phrase + word span) |
| Tools | agent web search/fetch (WebSearch/WebFetch), `webpage_screenshot` (Firecrawl), `web_image_search` (logos) |

## Workflow (per claim)
1. **Search** the web for the claim. Prefer, in order of credibility:
   1. **Primary / official** source (the company's own post, paper, docs, press release).
   2. **Major reputable outlet** (TechCrunch, The Verge, Reuters, Bloomberg, VentureBeat, Tom's Hardware…).
   3. **Reputable secondary** coverage corroborating the above.
   Avoid forums, SEO content farms, and unattributed aggregators as the *primary* citation.
2. **Verify & capture the exact quote.** Fetch the page; find the sentence that supports the
   claim; copy the `exact_quote` verbatim and the **`highlight_phrase`** (the shortest exact
   substring to marker-sweep on screen — must match the source text character-for-character).
3. **Capture the receipt.** For the strongest proof beats, screenshot the source with
   `webpage_screenshot` (viewport ~1280×1000; the official/headline page is best). Save under
   `assets/screenshots/`. Prefer the official announcement + 1–2 outlet articles for variety.
4. **Logos.** Fetch any company/product logos the claim references via `web_image_search`
   (`type_image: "transparent"`), saved under `assets/logos/`.
5. **Label credibility & presentation.**
   - `credibility`: `verified` (primary/major outlet) · `reported` (claimed by a named party,
     not independently confirmed) · `derived` (a number you computed — express as a labeled RANGE) ·
     `unverified` (no credible source found).
   - `proof_presentation`: `sequenced_after_animation` (PREFERRED when the claim also has a
     companion animation — the scene/edit directors play the animation, then cut to a full-frame
     article card; never overlay the article on top of a busy animation) · `full_frame_receipt`
     (article owns the frame, or a stacked set) · `overlay_card` (a COMPACT source card over the
     talking head / in clear negative space — never covering a hero).

## Integrity rules
- **Reported ≠ verified.** If the only support is "company X says," label it `reported` and the
  on-screen card must attribute it ("per Anthropic", "Stripe reported").
- **Never fabricate a screenshot** that impersonates a publication for an `unverified` claim.
  If you can't find a credible source, label it `unverified` and tell the scene director to use
  a neutral designed card (the creator's own editorial graphic), not a fake article.
- **Derived numbers** (e.g. token-cost math) → a labeled range with the stated assumption.
- The `highlight_phrase` must be an **exact substring** of the source so the marker sweep lands
  on real text.

## Output: `research_brief`
```
claims[]: {
  beat_id, spoken_phrase, word_span,
  source_url, source_name, exact_quote, date, credibility,
  highlight_phrase,            # exact substring to marker-sweep
  proof_presentation,          # sequenced_after_animation | full_frame_receipt | overlay_card
  screenshot_path?,            # if captured
  logos?: [paths],
  attribution?                 # required for 'reported'
}
sources[]: deduped {url, name, date}
```

## Self-evaluate
- Every verifiable claim has ≥1 credible source with url + exact_quote + date + credibility.
- highlight_phrase is a verbatim substring of the source; screenshots captured for the headline proofs.
- Reported/derived/unverified claims labeled honestly; no fabricated receipts.
