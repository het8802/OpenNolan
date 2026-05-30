# Asset Director — article-broll-animations

## Purpose
Generate every asset and produce an `asset_manifest` with provenance. Read the Layer 3 skills before
generating: `elevenlabs`, `sound-effects`, `music`, `ai-video-gen`. Copy/adapt the reference scripts
in `projects/the-500m-claude-bill/scripts/`.

## 1. Voiceover (the timing source of truth)
- Generate **per-scene** VO via `tts_selector` (`preferred_provider="elevenlabs"`), energetic
  founder settings (e.g. voice Adam `pNInz6obpgDQGcFmaJgB`, `eleven_multilingual_v2`,
  stability ~0.42, similarity ~0.8, style ~0.5).
- Probe each clip with `ffprobe`; build a `vo_manifest.json` with per-scene `start/end/duration`.
  **This manifest is the single source of truth for scene timing.**
- If total runs over the target, apply pitch-preserved `atempo` (≈1.15–1.2×) and rescale the
  manifest (see `retempo.py`). Concatenate clips with ~0.18s breathing gaps into `narration.mp3`.
- Captions are designed per-beat phrases (from scene_plan), NOT a whisper word-track.

## 2. Source-receipt cards / article proof (the signature)
- Preferred: faithful **source-receipt cards** rendered IN the composition (vector text, mobile-
  legible) — real masthead + headline + date + URL bar + the verbatim verified quote, with the
  marker-sweep target marked. The reference `ReceiptCard` component does exactly this.
- Live screenshots only if the browser extension is connected AND the result is mobile-legible
  (top-of-article headline region). Dense paywalled screenshots are a known failure — prefer cards.
- Record the `highlight_phrase` per card so the sweep lands on the exact verified words.

## 3. Real B-roll
- Pull **portrait** stock via `pexels_video` (`orientation: "portrait"`) — real footage only; never
  AI-impersonate a real product/company. Adapt `get_broll.py`. Store with provenance (provider, url).
- Use ghosted behind dark beats or as texture strips graded into the warm palette.

## 4. SFX (cohesive, restrained, premium)
- Generate from ElevenLabs sound-generation REST (`/v1/sound-generation`,
  `model_id=eleven_text_to_sound_v2`, `duration_seconds` ≥ 0.5, tune `prompt_influence`). Adapt
  `gen_sfx_v2.py`. The ElevenLabs SDK may be absent — use `requests` to the REST endpoint.
- **Avoid cartoonish dings/sparkles/pops.** Prompt for clean/modern/subtle/premium. A good base set:
  impact-deep, cash-chime, swoosh, marker, power-morph, riser, data-tick, deflate, click, confirm,
  boom-low, resolve, outro-swell. Reserve SFX for transitions + reveals.
- Reusable cross-project SFX may also live in `assets/sfx/` (library); reel-specific go in the project.

## 5. Music
- `music_library/` track if present; else `music_gen` — low warm editorial pulse, instrumental,
  duration ≈ video length.

## Output: asset_manifest
Per asset: `scene_id, type, source_tool, source_url, license, role` (proof/broll/sfx/music/vo/overlay),
plus `highlight_phrase` on proof cards. Include `vo_manifest` and `layer3_skills_read`.

## Review focus
- VO duration manifest present; cards faithful + highlight phrase recorded; B-roll real + portrait
- SFX cohesive/restrained (not cartoonish); music low warm; provenance on every asset
