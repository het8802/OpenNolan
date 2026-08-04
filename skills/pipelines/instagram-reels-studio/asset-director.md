# Asset Director — instagram-reels-studio

Generate/collect the assets the scene plan calls for and produce the `asset_manifest` with
provenance for every asset (paths, source_tool, model, cost, scene linkage).

## Per planned move

- **Cutouts** → `object_cutout` (SAM2 via Replicate). Before reading, read
  `.agents/app/skills/sam2-cutouts/SKILL.md`. It's PAID and confirm-gated: announce the cost, pass
  `confirm=true` (or set OBJECT_CUTOUT_AUTOCONFIRM for batch). Provide explicit click points —
  there is no auto mode. Results cache by (video + clicks). If SAM2 is unavailable the tool
  names `bg_remove` as a person-only fallback — surface that to the user, do NOT silently swap.
- **Restyle** → `restyle_video` (≤10s hero clip, Luma modify-video by default). PAID +
  confirm-gated; announce provider + cost. Read `.agents/app/skills/ai-video-gen`.
- **Captions** → `subtitle_gen` (word-by-word or sentence per the template/brief).
- **Music** → check `music_library/` first, else `music_gen`/`freesound_music`. A beat-synced
  reel needs a real track for `beat_cutter`.
- **Audio cleanup** → `audio_enhance` if the source voice is noisy.

## Provenance (required)
Every generated asset gets an `asset_manifest` entry with `source_tool`, `model` (if any),
`cost_usd`, `scene_id`, and `duration_seconds`. Paid generations must record their real cost.

## HDR caveat for generated assets
`object_cutout`, `restyle_video`, image/video generators, and stock all output **SDR**. If the
source footage is HDR (the `hdr_handling` decision from idea stage is "preserve"), these
generated assets will look flat next to it. Flag any SDR generated asset in the manifest when
the reel is HDR, so the edit/compose stages can place it deliberately (or the user can drop it)
rather than discovering the mismatch at render. Note the current Edits tools are 8-bit SDR
(they don't preserve HDR) — see the deferred HDR-tooling follow-up.

## Quality bar
Assets exist for every planned scene/move; paid tools were confirm-gated and cached; manifest
is schema-valid with full provenance; SDR-vs-HDR mismatches flagged. Auto-proceed (no human
gate) unless a paid run needs approval.
