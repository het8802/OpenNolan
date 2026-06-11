# Scene Director — anthropic-style-animated-talking-head

## When to Use
You have an approved `script` (beats + word timestamps) and a `research_brief` (verified claims).
Produce the `scene_plan`: assign every beat a **shot mode**, spec the animated assets, and pin
every reveal to its trigger word. This stage is where the pipeline is "smart" about when to show
the face, when to overlay, when to split, and when to go full animation.

## Prerequisites
| Layer | Resource |
|-------|----------|
| Script + transcript | beats with start/end + words[] |
| Research brief | verified claims with highlight phrases, screenshots, presentation |
| Playbook | `styles/anthropic-editorial-animated.yaml` |
| Tools | `frame_sampler` (face position for crops & overlay-safe zones) |

## Step 1 — Locate the face (do this first)
Run `frame_sampler` at 4–6 timestamps. Scale each to 1080×1920 and note the eye y-coordinate
and the face bounding box. You need two things:
- **`face_crop_y`** for `split_5050`: choose so the face is **centered** in the bottom 1080×960
  panel — `face_crop_y ≈ eye_y − 0.42·960`. Verify with a test crop
  (`crop=1080:960:0:<face_crop_y>`); confirm the face is centered, **not the ceiling**.
  (Validated example: this footage had eyes ≈ y765 → `face_crop_y ≈ 360`.)
- **Overlay-safe zones** for `talking_head_overlay`: the regions NOT covered by the face/hands
  (usually above the head and the lower third). Overlays go there, never on the face.

## Step 2 — Shot-Mode Selection (the core decision)
Walk the beats in order. For each, pick exactly ONE mode using this decision tree:

```
Is the creator stating a verifiable fact/number/quote that research_brief resolved?
  └─ YES → claim_proof   (show the source receipt + marker-sweep the verified phrase on the claim)
  └─ NO ↓
Is the visual the POINT and dense/complex? (multi-node diagram, big stat, comparison,
receipt stack, benchmark) and the face would distract?
  └─ YES → animation_full   (cover the frame; VO continues underneath)
  └─ NO ↓
Is this a SUSTAINED explanation that benefits from ONE supporting animation
(a racing counter, a small chart, a small diagram) while the speaker should stay present?
  └─ YES → split_5050   (animation top 1080×960, face bottom 1080×960 centered)
  └─ NO ↓
Can a graphic ANNOTATE the line without replacing the speaker?
(logo bumper, key-term/stat pill, a checklist building point-by-point, a small source card)
  └─ YES → talking_head_overlay   (graphic in a safe zone, face visible)
  └─ NO → talking_head_full        (personal hook, opinion, transition, "the catch", CTA/outro)
```

**Defaults & guardrails:**
- Open on `talking_head_full` (personal hook) and close on `talking_head_full` (CTA/outro) — connection beats.
- Never run `talking_head_full` longer than **~9s** without a cutaway or overlay.
- Overlays **never cover the face**; split crops **center the face**.
- Alternate face and graphics for energy; every 0.5–1.5s something changes.
- A single sustained topic can mix modes (e.g. a capabilities rundown: boxes overlay the face
  one-by-one, then a `split_5050` for the one stat that deserves a counter, then back).

Record per beat: `{id, start, end, shot_mode, rationale}` plus the mode-specific spec below.

## Step 3 — Spec each mode

### talking_head_full
No graphic. Just `{shot_mode: talking_head_full, start, end}`.

### talking_head_overlay  → alpha .mov authored in HyperFrames, composited on the TH
```
overlay_spec: {
  comp_id, canvas: 1080x1920 (transparent bg),
  elements: [ {id, type: logo|pill|box|source_card, content, safe_zone: above_head|lower_third|corner,
               trigger_word, abs_time, gsap_in} ],
  notes: "positioned clear of the face; reveals land on trigger words; box-stacks build bottom-up, one per spoken point"
}
```
Patterns: **logo pop+rotate bumper** (sunburst scale-0 + rotate-in, optional label beneath — like the
hook), **key-term / stat pill**, **checklist building box-by-box** in the lower third (one box per
spoken point), **small source card** in a corner.

### split_5050  → animation panel (1080×960) over face crop (1080×960)
```
split_spec: {
  top_comp_id, top_canvas: 1080x960 (opaque ivory),
  top_content: counter | small_chart | small_diagram | phrase,
  face_crop_y,           # from Step 1 — centers the face
  gsap_entries: [...]    # reveals on trigger words (e.g. counter lands on the spoken number)
}
```

### animation_full  → full-frame 1080×1920 cutaway (mp4)
```
anim_spec: {
  comp_id, canvas: 1080x1920,
  content_type: stat_card | comparison | connector_diagram | receipt_stack | hero_phrase | kpi/chart,
  gsap_entries: [...],   # progressive build across the clause, reveals on trigger words
  fit: "trim to window if clip>=window; freeze last frame to hold if window>clip"
}
```

### claim_proof  → source receipt with marker-sweep
```
claim_spec: {
  presentation: sequenced_after_animation | overlay_card | full_frame_receipt,
  companion_animation?,  # comp_id of the animation this claim pairs with (for sequenced)
  split_at?,             # abs_time to cut from animation -> article (for sequenced)
  source_name, domain, headline, date, url, screenshot_path,   # real article banner
  highlight_phrase,      # exact substring from research_brief
  marker_sweep: { trigger_word, abs_time },   # the sweep lands ON the spoken proof phrase
  attribution?           # for 'reported' claims
}
```
The card is a faithful **browser article**: chrome + URL bar + real masthead + date + the real
article screenshot as a banner + a `✓ VERIFIED · <domain>` eyebrow; sweep a clay marker highlight
on the exact verified phrase as the creator says it.

**Presentation — DON'T double-stack graphics (validated user rule):**
- If this claim beat ALSO has a companion animation (a stat, device, calendar, diagram), use
  **`sequenced_after_animation`**: play the animation's hero reveal, then at `split_at` CUT to the
  **full-frame** article card (both share the ivory bg → seamless one-beat cut, "here's the number…
  here's the receipt"). Time the animation to the first part of the claim and the article+highlight
  to the proof phrase. This is the PREFERRED pattern. (The user rejected overlaying an article card
  on top of a busy full-frame animation — it looked cluttered; sequencing was the fix.)
- Use **`overlay_card`** ONLY as a compact citation that does not cover a hero — e.g. a small source
  card over the talking head (face visible) or in clear negative space.
- Use **`full_frame_receipt`** (or a stacked `receipt-stack-in` set) when the claim has no companion
  animation and the article should own the frame.

## Narration Sync — HARD RULE
Every animated reveal lands **ON its trigger word, never before** (source of truth =
`transcript.json words[]`). Box-stacks and multi-part diagrams **build progressively**, one item
per spoken point. Counters land on the spoken number. Marker sweeps land on the spoken claim.
Record `trigger_word` + the `words[]` timestamp next to every reveal — the compose director
verifies this with before/after-word frame sampling and treats any early reveal as CRITICAL.

## Design system (every animated asset)
- Ivory paper canvas + soft radial vignette; clay/coral + slate; green=public/approved, amber=gated/caution.
- **Fonts: Fraunces (serif) for titles, card/box labels, product names, narrative phrases;
  Inter for eyebrows, sub-labels, pills, and numerals/counters.** (Do NOT title boxes in heavy Inter.)
- Soft shadows only; rounded cards/pills; drawn connectors; real logos on light chips.
- Captions (if used) are layout-aware (split=centered above divider; full/overlay=lifted lower-third),
  never bottom-pinned into the platform UI shadow zone.

## Output: `scene_plan`
- beats[]: {id, start, end, shot_mode, rationale, + mode-specific spec}
- metadata.face_crop_y, metadata.overlay_safe_zones
- every reveal carries trigger_word + words[] timestamp

## Self-evaluate
| Check | |
|---|---|
| Shot modes | Each beat has exactly one mode chosen via the decision tree, with a rationale |
| Claims | Every verified claim beat is `claim_proof` with highlight_phrase + marker_sweep on the spoken claim; claims with a companion animation are SEQUENCED (animation → full-frame article), not overlaid on top of it |
| Sync | Every reveal `abs_time` from words[]; builds progressive; lands on the word, never early |
| Face | Overlays in safe zones (never on face); split `face_crop_y` centers the face |
| Alternation | No talking_head_full run > ~9s without a cutaway/overlay |
| Look | Fraunces titles / Inter sub; warm editorial; semantic colors correct |
