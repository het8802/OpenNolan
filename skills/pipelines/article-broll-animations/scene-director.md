# Scene Director — article-broll-animations

## Purpose
Turn the script into a concrete shot table. Output a `scene_plan` where each scene declares its
background mode, primary proof asset, B-roll slot, highlight phrase, SFX, and motion. Human approval.

## The core rule (from source-backed-reel-evidence-montage)
Every factual sentence maps to **one primary proof asset + one emphasis**:
`claim → source → crop → highlight → motion`. Reveal the proof as the noun/claim is spoken, not
after the sentence ends.

## Per-scene fields
- `id`, `start`, `end` (windows come from the VO manifest once assets exist; estimate here)
- `bg`: `paper` | `paperWarm` | `darkHit` — **dark-drama hits reserved** for the high-tension
  beats only (hook/invoice, token burn, budget burn). Everything else warm.
- `primary_visual`: the animated beat (invoice slam, SaaS→metered morph, racing counter,
  leaderboard bars, split source cards, router diagram, payoff line)
- `proof` / `screenshot`: source-receipt card(s) + the verbatim `highlight_phrase`
- `broll`: real stock slot (ghosted behind dark beats, or texture strip)
- `on_screen`: designed phrase caption(s)
- `sfx`: which cues fire (tie to motion events)
- `motion`: from the Greg motion vocabulary (`receipt-stack-in`, `phrase-collage-build`,
  `keyword-hit`, `connector-draw`, `count-up`, `highlight-sweep`, `proof-board-land`)

## Pacing & taste
- Something meaningful changes every 0.5–1.5s (word group, card, connector, counter, reveal).
- Limit transition vocabulary (≤4 distinct). Motion serves hierarchy, not decoration.
- Keep generous negative space; primary text inside Reels safe zones; nothing critical under the
  bottom UI. Avoid bouncy/glitchy meme motion — premium soft-settle.

## Asset mix target (per 45–60s reel)
2–4 source-receipt cards, 2–4 real B-roll inserts, the animated motion-graphics beats, designed
overlays. Every scene has a concrete visual unless it's an intentional moral-reset text beat.

## Output: scene_plan
Ordered `scenes[]` with the fields above + a `global` block (captions style, music, narration_vol,
safe_zones) and `asset_requirements` (vo, screenshots/cards, broll, sfx, music).

## Review focus
- One proof asset per factual sentence; highlight phrase present
- bg/proof/broll/highlight/sfx/motion declared per scene
- Dark hits reserved; transitions limited; change cadence honored
