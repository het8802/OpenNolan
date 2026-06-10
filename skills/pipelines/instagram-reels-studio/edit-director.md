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
   Sourcing the overlay pixels:
   - **Styled text** → `text_card_gen` bakes a tight transparent PNG (presets: bold_center,
     lower_third, black_pill_caption, outline_pop, minimal_clean), then animate it via
     `overlays[].keyframes`. Simple captions can instead be `overlays[]` items with
     `type="text"` — rendered via drawtext on the ffmpeg runtime (x/y/opacity keyframes only;
     scale/rotation are ignored for text).
   - **Stickers/GIFs** → `sticker_search` (GIPHY/Tenor). Prefer the rendition the tool picks
     (mp4 for GIFs, guaranteed-alpha gif for stickers) and carry each result's `attribution`
     string into the export metadata — it's an API requirement, not optional.

4. **Apply motion ops + masks as derived clips.** For freeze-on-punchline, reverse loops,
   slow/fast motion, per-segment volume / 150% boost, punch-in/pan/Ken Burns camera moves
   (`pan_zoom`), beat-timed shake/zoom-pulse/strobe/glitch hits (`clip_fx`), or mirroring
   (`flip`), run `motion_ops`. For region blur, spotlight dim, image masks, or a one-off
   masked reveal between two clips, run `mask_ops`. Both output a NEW clip file (re-probed
   duration, registered in the asset_manifest with provenance) which becomes that
   `cuts[].source`. Note: `cuts[].speed` already covers a CONSTANT speed; use motion_ops for
   freeze/reverse and camera moves the scalar can't express. True eased speed RAMPS are NOT
   supported — `motion_ops` speed is a constant factor (documented limitation).

5. **Transitions.** Set `cuts[].transition_in` (B owns its own entrance) — the ffmpeg runtime
   renders fade/dissolve/wipe/slide/circle/zoom natively via xfade, so `dissolve` from
   `beat_cutter`/`template_apply` actually renders. Durations clamp to 0.1–2.0s (default 0.5,
   global override: `metadata.default_transition_duration`). Each crossfade shortens the
   timeline by its duration.

6. **Audio + captions.** Set `audio.music` (with `ducking` — already supported in the schema)
   and `subtitles` (word-by-word or sentence) per the template/brief. For sound effects,
   `sfx_kit` search over the in-repo `assets/sfx/` library is free and local (generate via
   ElevenLabs only when the library misses); see `skills/creative/sfx-library.md` for
   placement/level rules. For voiceover character effects (helium/deep/robot/telephone/... or
   a pitch shift) or dropping a take onto the timeline with music ducking, use `voice_ops`
   (effect / insert) on takes recorded at the assets stage.

For a vertical 9:16 deliverable on the ffmpeg runtime, set
`metadata.compose_target: {width: 1080, height: 1920}` — it takes precedence over the
profile-resolved resolution.

## Validate
The emitted `edit_decisions` must validate against `schemas/artifacts/edit_decisions.schema.json`.
Keyframed overlays validate there; beat-aligned cuts should be present when music-led. Do NOT
change `render_runtime` or `renderer_family` here.

## Quality bar
Schema-valid edit_decisions; cuts on the beat where music-led (speech preserved otherwise);
planned overlay motion present as keyframes; motion-ops/mask-ops derivatives registered;
sticker attribution carried where GIPHY/Tenor assets are used. Auto-proceed.
