# Edit Director — instagram-reels-studio (the heart)

This is where the Edits-parity toolset comes together. Produce the `edit_decisions` artifact by
orchestrating the new tools onto it. Carry `render_runtime` and `renderer_family` (`social-reel`)
from the brief UNCHANGED.

## Build order

1. **Start from a template (optional).** If the brief picked a template, run `template_apply`
   with the scene clips mapped to slots — it emits a valid `edit_decisions` you then refine.
   Otherwise build `cuts[]` from the scene plan directly.

2. **Beat-sync the cuts (if music-led).** Run `beat_cutter` with the music track + the ordered
   clips. **Default `mode="speech_safe"`** and pass the narration spans as `protected_ranges`
   so cuts never chop a spoken word. Use `music_led` ONLY when the audio is music, not speech.
   It snaps each cut to a beat and can merge straight into the edit_decisions.

3. **Animate overlays with keyframes.** For each cutout/text/sticker that the scene plan said
   should move, run `keyframe_animate` (raw keyframes or a preset: slide_in_left, pop, fade_in,
   ken_burns, ...). It writes `overlays[].keyframes` (nested in the overlay — `t` is absolute
   project seconds). The reveal must land ON the spoken trigger word, never before.

4. **Apply motion ops as derived clips.** For freeze-on-punchline, reverse loops, slow/fast
   motion, or per-segment volume / 150% boost, run `motion_ops`. It outputs a NEW clip file
   (re-probed duration, registered in the asset_manifest with provenance) which becomes that
   `cuts[].source`. Note: `cuts[].speed` already covers a CONSTANT speed; use motion_ops for
   freeze/reverse/ramps the scalar can't express.

5. **Audio + captions.** Set `audio.music` (with `ducking` — already supported in the schema)
   and `subtitles` (word-by-word or sentence) per the template/brief.

## Validate
The emitted `edit_decisions` must validate against `schemas/artifacts/edit_decisions.schema.json`.
Keyframed overlays validate there; beat-aligned cuts should be present when music-led. Do NOT
change `render_runtime` or `renderer_family` here.

## Quality bar
Schema-valid edit_decisions; cuts on the beat where music-led (speech preserved otherwise);
planned overlay motion present as keyframes; motion-ops derivatives registered. Auto-proceed.
