# Codex aesthetic carousel generation notes

Use this when creating Instagram carousel PNGs from Marketing OS scripts, especially if the first draft risks looking like HTML cards or a basic slide deck.

## Trigger

- User asks for an Instagram carousel or static visual pack from a script/concept.
- User wants "aesthetic images" or explicitly asks to use Codex.
- Existing/generated carousel looks like HTML/PPT/card-grid output.

## Workflow

1. Create a scratch git repo for Codex because Codex requires a git workdir.
2. Ask Codex to generate a deterministic Pillow-based image generator plus outputs:
   - `generate_carousel.py`
   - `slide-01.png` ... `slide-08.png` at `1080x1350`
   - `contact-sheet.png`
   - `caption.md`, `README.md`, `asset-ledger.jsonl`, `approval-status.json`
3. The prompt should specify a premium editorial art direction, not just layout:
   - Anthropic-style warm editorial palette when relevant: ivory/cream, warm tan, charcoal ink, clay orange, muted olive
   - film grain / vignette / soft glows
   - layered paper or sticky-note artifacts
   - tape, hand-drawn arrows, shadows, slight rotations
   - blurred glass/mock UI fragments
   - serif display typography with readable sans/mono details
   - no copyrighted logos; use generic connector chips such as `ACCOUNTING`, `CRM`, `DOCS`, `PAYMENTS`
   - no repeated headers, footers, date bars, slide numbers, brand bars, or template chrome on final slides
4. Run Codex directly in the scratch repo and capture durable logs:
   ```bash
   codex exec --dangerously-bypass-approvals-and-sandbox --json \
     --output-last-message codex-last-message.md \
     - < prompt.md > codex-run.jsonl 2>&1
   ```
5. Verify every slide is exactly `1080x1350` with Pillow before visual QA.
6. QA the contact sheet with vision before delivery.
7. If QA flags readability or polish issues, run a second Codex refinement pass targeted at those issues, then regenerate and re-QA. This worked better than accepting the first Codex pass.
8. Deliver individual slide images in numeric order first. A zip/archive is optional backup only; do not make Het download a zip just to review.

## Prompt pattern

```text
Create an aesthetic, premium Instagram carousel image set from this concept: "...".

Make it feel like premium editorial/product strategy graphics, NOT HTML cards. Avoid flat boxes-on-webpage look.

Deliverables in current directory:
1. Python script generate_carousel.py using Pillow only.
2. 8 PNG slides, 1080x1350, named slide-01.png ... slide-08.png.
3. caption.md, README.md, asset-ledger.jsonl, approval-status.json.
4. contact-sheet.png.

Design requirements:
- warm editorial paper system: ivory/cream background, charcoal ink, tan cards, clay orange accents, muted olive states
- subtle film grain, soft shadows, vignettes, glows
- layered paper/sticky-note/product screenshot fragments, diagonal layouts
- hand-drawn arrows, paper tape, slight rotations, tactile depth
- avoid HTML/CSS component or basic PowerPoint card look
- keep text concise and readable on mobile
- no copyrighted logos; use generic connector chips
- no repeated headers/footers/date bars/slide numbers/template chrome on final slides

Run the script and ensure files exist.
```

Targeted refinement prompt after vision QA:

```text
Refine the existing carousel. Keep the same concept and aesthetic, but fix QA issues:
- increase smallest/body text by 10-20%
- darken low-contrast secondary text
- simplify tiny diagram labels
- apply exact wording fixes from QA
- regenerate slides/contact sheet and verify 1080x1350
```

## QA checklist

- Does it look premium/aesthetic rather than HTML cards?
- Are main headlines readable at contact-sheet size?
- Are secondary details intentionally decorative or readable enough?
- Any low-contrast pills/disclaimers?
- Any accidental crop/overlap?
- Does slide order match the narrative arc?
- Are all slides `1080x1350`?
- Are actual `slide-XX.png` files free of repeated headers, footers, date bars, slide numbers, brand bars, and template chrome?
- Did Slack/channel delivery include ordered individual images first, not only a zip?

## Common refinement fixes

- Increase contrast on light pills or dark-glass labels.
- Enlarge sticky-note body/category text for mobile.
- Remove or enlarge tiny disclaimers; keep `reported` nuance visible for market/funding claims.
- Reposition overlapping notes so comparison labels remain intentionally visible.
- Fix capitalization/style issues discovered in QA (e.g. `pitch book` vs a company/product name).
- If contact-sheet QA says labels are borderline small, do a targeted second Codex pass instead of manually patching around Codex. Ask for 10–20% larger small/body text, darker secondary text, simplified labels, and exact wording fixes.
- For finance/acronym labels, prefer unambiguous words when visual QA may misread compact forms (`Ledger reconciler` instead of `GL reconciler`).
