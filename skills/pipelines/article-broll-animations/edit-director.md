# Edit Director — article-broll-animations

## Purpose
Translate the VO manifest + scene_plan + asset_manifest into the **composition props**
(`edit_decisions`) that drive the Remotion render: scene windows, SFX placements, and audio levels.

## Scene windows (CONTIGUOUS — important)
Derive windows from `vo_manifest.json`. Make each scene run **to the next scene's start** (not just
to its own VO end), so the ~0.18s VO breathing gaps never flash an empty/black frame. The last scene
ends at `total + ~0.4s` pad.

```
scene[i].start = vo_scene[i].start
scene[i].dur   = vo_scene[i+1].start - vo_scene[i].start   (last: total + 0.4 - start)
durationInFrames = ceil(last_end * fps)
```

## SFX placements
- Place each cue at its motion event time (absolute seconds), not on every caption word.
- Levels keep SFX under the VO: typically `0.45–0.7`. Stagger stacked hits (e.g. impact at 0.18s,
  cash-chime at 0.34s) so they read as distinct.

## Audio levels
- narration `~1.0`
- music `~0.09` with fade in (~1.5s) / out (~2.5s)
- sfx `0.45–0.7`

## Runtime
Carry `render_runtime` unchanged from the proposal (Remotion). No silent swaps.

## Output: edit_decisions
`{ render_runtime, scenes:[{id,comp,start,dur}], sfx:[{src,at,vol}], audio:{narration,music} }` —
this maps directly to the custom composition's `defaultProps` (see `Claude500MReel` /
`Root.tsx claude500MDefault`).

## Review focus
- Windows contiguous (no gap flashes); timeline within 5% of VO total
- SFX tied to motion events; levels under VO; runtime unchanged
