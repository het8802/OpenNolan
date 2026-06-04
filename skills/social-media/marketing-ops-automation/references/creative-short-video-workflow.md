# Creative Short-Video Workflow: Avoid Captioned-Still Outputs

Session learning from Idea 10 / “Trend Court” after user feedback that prior videos were too bland.

## Core correction

Do **not** make social videos by placing captions over one generated image. Treat each video as a short narrative sequence with multiple visual beats.

## Recommended production pattern

1. **Storyboard the script into 5–8 beats**
   - Example: hook/open → evidence/problem → diagnosis/conflict → system/fix → payoff/verdict → CTA.
   - Give each beat a distinct visual purpose, not just a text line.

2. **Generate/source multiple visuals**
   - Use GPT-image-2 for a separate 9:16 image per beat when allowed/available.
   - Prompt for no readable text/logos; add all copy in edit for control.
   - Free B-roll is acceptable when it improves motion/story, but verify licensing.

3. **Edit like a vertical short, not a slideshow**
   - Prefer Remotion for production-quality short-form edits when the user wants richer animation than ffmpeg zoom/pan.
   - Look for reusable Remotion components/templates before building from scratch. Useful free source: `reactvideoeditor/remotion-templates` (MIT; examples: glitch text, whip-pan, particle explosion, progress steps, cinematic title intro). Copy/adapt patterns, do not blindly drop in generic demos.
   - Use Remotion hooks (`useCurrentFrame`, `interpolate`, `spring`) for kinetic typography, whip-pan scene changes, particle/flash impacts, animated process cards, progress bars, UI tags, scanline/noise overlays, and layered audio/SFX.
   - **Freshness rule:** do not use the same template/animation package/cut pattern across videos. Reuse underlying know-how, not the visible edit. Same transitions, same timing, same text treatments, same progress bars, or same scene order makes the content feel rotten/stale.
   - For every script, choose a new creative system: visual metaphor, palette, typography rhythm, transition language, camera/motion grammar, CTA treatment, and pacing should be intentionally different from the last few videos.
   - When adapting Remotion templates, treat them as a pattern library. Combine, modify, or rebuild pieces so the final video does not look like a cloned template.
   - Add motion to each shot: zoom, pan, crop, transitions, or B-roll movement — but vary the motion style per video.
   - Use title cards and selective captions; do not dump the full script on screen.
   - Keep captions in mobile safe zones and avoid bottom UI areas.

4. **Add audio/sound design**
   - Voiceover if appropriate.
   - Music/ambience/SFX can make the piece feel intentional. Use free/local/generated SFX when possible; paid SFX needs approval.

5. **QA before delivery**
   - Verify MP4 exists and is decodable:
     ```bash
     ffprobe -v error -show_entries format=duration,size <video.mp4>
     ffmpeg -v error -i <video.mp4> -f null -
     ```
   - Make a contact sheet and inspect visual variety and text readability:
     ```bash
     ffmpeg -y -loglevel error -i <video.mp4> -vf "fps=1/7,scale=270:480,tile=4x2:padding=8:margin=8" -frames:v 1 contact-sheet.jpg
     ```
   - If text is cramped/cut off, revise before sharing.

## Common pitfalls

- One image + captions = bland and unacceptable for this user’s marketing workflow.
- Too much microcopy on every scene competes with captions and hurts mobile readability.
- AI-generated images should not contain the final text; generated text can be garbled.
- Repeated scenes weaken perceived quality; make payoff/sentence/CTA visually distinct.
- `ffmpeg zoompan` pitfall: if you use `zoompan=d=<frames>` with a looped multi-second input, duration can multiply unexpectedly and render minutes. Feed one still frame (`-loop 1 -framerate 1 -t 1`) and let `zoompan` create the desired frame count.
- Remotion renders may produce an invalid partial MP4 if interrupted during encoding (`moov atom not found`). Always run the decode verification after render before sharing.
- Contact-sheet QA can catch typography problems that frame previews miss: long all-caps headlines may look like missing letters at mobile scale. If a phrase clips or wraps badly, reduce font size or force a deliberate line break with `whiteSpace: "pre-line"`.
- Fixed-interval contact sheets can create false blank-frame alarms from transitions or unused tile slots. If a sheet shows a blank/duplicate tile, extract exact frames and/or build a scene-midpoint sheet from storyboard timestamps before sharing or rerendering.
- Scene-midpoint sheets can also overrepresent a final held CTA if you add extra late timestamps; do not treat repeated final frames as harmless if the user asked for fresh, non-templated creative. Vary the end card/CTA, source frame, and final action state, or sample only one final hold plus earlier beat midpoints.
- If using generated stills or local Pillow/FFmpeg illustrations as B-roll, keep all important typography inside the original 1080x1920 safe frame. Avoid post-render crop/pan/overscale motion that cuts headlines or bottom captions; add motion through internal elements, full-frame zooms with safe margins, or Remotion transforms that are QA-checked.
- A useful local fallback when image/video APIs are unavailable: create 5–8 distinct vector-style 9:16 beat stills with Python/Pillow (portal/map/cards/form/funnel/clock/etc.), animate each as a short FFmpeg segment, concatenate, mix voiceover with a subtle generated bed, then run the normal ledger + decode + blackdetect + contact-sheet QA. This is acceptable only when the visuals are genuinely beat-specific, not a single static background.
- For attention-span edits, cut runtime aggressively (about 35–45s for this format), use scene changes every ~3–5s, and make each scene carry one idea.
