# Proposal Director — article-broll-animations

## Purpose
Lock the production decisions and get explicit human approval before any asset spend. Output a
`proposal_packet` + `decision_log`.

## Decisions to present (recommend, then wait for approval)
1. **Composition runtime (HARD RULE).** If both Remotion and HyperFrames are available
   (`video_compose.get_info()["render_runtimes"]`), present BOTH with a one-line strength + one-line
   tradeoff each, then recommend. For this pipeline, **recommend Remotion** — it owns the custom
   vertical scene stack, the source-receipt cards, charts (leaderboard), and word-timed reveals.
   HyperFrames parity for this composition style is tracked in `skills/core/hyperframes.md`.
   Record the choice in `decision_log` as `render_runtime_selection` with both runtimes in
   `options_considered`. Never pick silently — the locked `render_runtime` is what the compose
   stage routes on.
2. **Aesthetic.** Default **warm editorial base + restrained dark-drama HITS** on the
   cost/burn/danger beats (invoice slam, token burn, budget burn). Confirm — do NOT go full
   dark/neon unless the user explicitly asks (see saved reel-aesthetic preference).
3. **Footage approach.** `real-only` (real stock B-roll + real article screenshots/source-receipt
   cards) vs `real + sparing AI texture` for purely abstract beats (money burning, token streams).
   Default real-only for a news reel; AI texture never impersonates a real product/company.
4. **Music plan.** Check `music_library/` first; else generate a low warm editorial pulse
   (`music_gen`). Present the choice.
5. **SFX plan.** A cohesive **restrained premium** set generated from ElevenLabs — NOT the
   cartoonish library defaults. Name the intended palette (impact, cash/coin, swoosh, marker,
   power-morph, riser, data-tick, deflate, click, confirm, boom, resolve, outro).
6. **Cost estimate.** Itemized + honest (typically < $1; render free/local).

## Concept
The structure is largely fixed by the format (hook → reported-disclaimer → reframe → mechanism →
danger → evidence montage → solution diagram → payoff). Offer 1–2 hook/cover variations rather than
re-deriving the whole structure. Use the `instagram-reels` hook families if the user wants options.

## Output: proposal_packet
- `selected_concept` (hook, beat list, animation_mode)
- `render_runtime` (locked) + `aesthetic` + `footage_approach` + `music_plan` + `sfx_plan`
- `cost_estimate` with itemized line_items
- `approval` block

## Review focus
- Both runtimes presented when available; runtime locked + logged
- Aesthetic + footage + music + SFX plans recorded
- Cost itemized and honest
- Approval obtained before advancing
