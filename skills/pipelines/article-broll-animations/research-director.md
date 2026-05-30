# Research Director — article-broll-animations

## Purpose
This is the **source-verification gate** — the defining stage of this pipeline. Output a
`research_brief` in which **every factual claim** the reel will make is backed by a real source,
an exact quote, a date, and a confidence label. If a claim cannot be verified, it is softened,
removed, or explicitly labeled as reported-but-unverified. Never let an unsupported claim reach the
script.

## Why it matters
This pipeline puts claims about real companies/people on screen with highlighted "proof." If the
proof is fabricated or wrong, the reel spreads misinformation and the highlight feature becomes a
liability. The research stage is what makes the evidence chain honest.

## Process
1. **Split the brief/script into atomic factual claims.** One row per claim (name, number, date,
   causal statement, quote).
2. **Verify each claim** with `WebSearch` + `WebFetch`. Prefer primary/origin sources (the outlet
   that broke it, official blogs, filings, docs) over aggregators. Capture:
   - `url` — the real source
   - `exact_quote` — verbatim supporting text (this becomes the marker-highlight target later)
   - `date` — publication date
   - `confidence` — `verified` | `reported` | `derived` | `unverifiable`
   - `attribution` — who is making the claim (e.g. "an AI consultant told Axios")
   - `proof_type` — `article_card` | `logo` | `product_ui` | `chart` | `quote_card`
   - `broll_need` — concrete real footage that fits the beat (e.g. "server racks", "cash", "power lines")
3. **Label reported-but-unverified claims explicitly.** If a number comes from a single unnamed
   source, mark `reported` and note "company unnamed" / "single-source". The script and on-screen
   copy must carry this ("Reported ≠ verified").
4. **Bound derived numbers as RANGES.** Never assert an exact figure you computed (token math,
   $/unit). State the assumption and give a range with a footnote (e.g. "$500M ≈ 20T–100T+ tokens*
   — rough public-rate math; model/mix dependent").
5. **Capture exact highlight phrases.** For each `article_card`, record the 1–2 word/phrase runs to
   sweep-highlight, copied verbatim from the source so the on-screen highlight matches reality.

## Output: research_brief
- `claims[]` table with the fields above (≥ one row per factual sentence)
- `sources[]` with label, url, claim, date, confidence
- `broll_needs[]` consolidated list for the asset stage
- `risk_notes` — any claim that is shaky, single-source, or needs softening

## Review focus
- Every factual sentence has url + exact_quote + date + confidence + proof_type
- Reported/unverified claims flagged; derived numbers bounded as ranges
- Highlight phrases captured verbatim
- ≥3 sources cited

## Common mistakes
- Trusting the user's draft claims without checking (the draft may be wrong or stale).
- Highlighting a paraphrase instead of the exact source words.
- Asserting a computed number as fact instead of a labeled range.
- Using an aggregator when the origin outlet is the real attribution.
