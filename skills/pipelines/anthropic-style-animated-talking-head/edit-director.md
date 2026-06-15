# Edit Director — anthropic-style-animated-talking-head

## When to Use
You have a `scene_plan` (shot modes + timings) and an `asset_manifest`. Produce `edit_decisions`:
the **contiguous segment cut plan** the compose director will assemble.

## Prerequisites
| Layer | Resource |
|-------|----------|
| Scene plan | beats with shot_mode, start/end, specs, face_crop_y |
| Asset manifest | built cutaways (mp4), overlays (mov), split panels, receipts, durations |

## The cut plan (contiguous segments)
The talking head is the base; the timeline is a list of segments that **tile the entire TH
duration with no gaps and no overlaps**, so the untouched audio stays in sync. Convert the
scene_plan beats into ordered segments:

```
segments[]: {
  index, kind, start, end,          # end of segment N == start of segment N+1
  source,                           # TH | cutaway path | overlay mov | split top mp4 | receipt
  fit,                              # for cutaways/panels: 'trim' (clip>=window) | 'hold_last' (window>clip)
  face_crop_y?,                     # for split
  overlay_path?,                    # for th_overlay / claim_proof overlay
  marker_sweep?                     # {trigger_word, abs_time} for claim highlight
}
kind ∈ th | th_overlay | split | animation_full | claim_proof
```

### Rules
1. **Contiguous & summing.** `segments[0].start = 0`, `segments[-1].end = TH_duration`, each
   segment abuts the next. The sum of segment lengths == TH duration (so audio never drifts).
2. **Fit without changing VO speed.** If a cutaway/panel clip is **longer** than its window →
   `trim`. If **shorter** → `hold_last` (freeze the last frame; `tpad=stop_mode=clone`). NEVER
   slow/speed the VO or the TH to fit.
3. **Kill micro-cuts.** Remove any `th` face segment shorter than ~0.6s by extending the
   neighboring cutaway/overlay to cover it (avoids face "blinks").
4. **Overlay/split windows** align to the scene_plan beat; the overlay/panel's internal reveals
   already land on trigger words (authored in assets).
4b. **Claim beats: SEQUENCE, don't double-stack.** When a claim beat has a companion animation
   (`sequenced_after_animation`), emit TWO contiguous `clip` segments — the animation (trimmed to its
   hero reveal, lands on the first part of the claim) then the full-frame article card (lands on the
   proof phrase). Both are full-frame on the shared ivory bg, so the cut is seamless. Do NOT overlay
   the article on top of the animation (the user rejected that — it double-stacks graphics).
5. **Audio.** Explicitly: `audio: "copy original TH audio (-c:a copy)"`. VO untouched.
6. **render_runtime** carried unchanged (hyperframes assets + ffmpeg assembly). No silent swap.

### Worked shape (from the validated reel)
```
th_overlay  0.00–7.55   hook: face + Claude logo pop/rotate + "Mythos"
th_overlay  7.55–19.18  capabilities: boxes build one-by-one over face
split       19.18–24.30 1M-context counter (top) / face (bottom, face_crop_y centered)
th_overlay  24.30–33.55 capabilities continue
th          33.55–35.45 clean face (transition)
clip        35.45–40.00 Stripe ANIMATION (the 50,000,000 stat)         ┐ one claim beat,
clip        40.00–44.82 Stripe ARTICLE (anthropic.com card + highlight) ┘ SEQUENCED on ivory
... etc, ending on a th face CTA ...
```

## Output: `edit_decisions`
- segments[] (as above), total_duration_s == TH duration
- audio: copy original TH audio
- render_runtime: hyperframes+ffmpeg

## Self-evaluate
- Segments tile [0, TH_duration] exactly; no gaps/overlaps; sum == duration.
- Each segment references a valid asset; fit rule set; no micro face-cuts < 0.6s.
- Audio = copy original TH; render_runtime carried.
