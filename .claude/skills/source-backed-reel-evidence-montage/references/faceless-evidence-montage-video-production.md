# Faceless evidence-montage video production reference

Session pattern: the user asked to turn an existing daily AI/tech script into a Reel using the source-backed evidence montage style learned from Reel `DYhssDhN-ti`.

## When this reference helps
Use when the user asks for an actual MP4/Reel draft from a source-backed script but no talking-head footage is available yet.

## Proven production shape
1. Load the script and split it into claim beats.
2. Extract/verify source facts from the source URLs before visualizing them.
3. Generate voiceover from the VO/script text if no user audio exists.
4. Build a vertical 1080x1920 faceless evidence-montage draft with:
   - opening valuation/shock card;
   - source receipt cards with publication/date/title visible;
   - highlighted exact phrases matching narration;
   - labeled company-reported metrics;
   - workflow/loop diagrams for mechanism beats;
   - metric cards and chart animation for quantitative proof;
   - founder checklist + comment CTA.
5. Use the resulting draft as a motion/storyboard that can later accept talking-head cuts.

## Claim/evidence mapping example
For the 2026-05-27 script about Devin/Cognition and OpenAI Codex tax agents:
- TechCrunch source card supported: `$1B raised`, `$25B pre-money valuation`, `$492M ARR run-rate`, `50% MoM enterprise usage`.
- OpenAI source card supported: self-improving tax agents, practitioner corrections, structured signals, eval-backed improvements.
- Metric proof cards supported: `7,000 returns`, `~1/3 prep time saved`, `up to 97% accuracy`, `~50% throughput increase`.
- Chart supported: `25% → 86%` at the 75% field-completion threshold within six weeks.

## Deterministic fallback implementation
If Remotion/OpenNolan is unnecessary or unavailable, a PIL + FFmpeg pipeline works for a fast draft:
- Generate scenes as 1080x1920 JPEG frames with PIL.
- Render source cards, highlights, captions, loop diagrams, metric cards, and charts programmatically.
- Encode frames with FFmpeg to H.264 MP4.
- Mux generated voiceover audio with FFmpeg.
- Generate a contact sheet and run ffprobe/ffmpeg decode QA.

## QA checklist
- Contact sheet shows readable source cards and highlighted phrases.
- Every factual number visible on screen has a source label (`TechCrunch says`, `OpenAI says`, `company-reported via TechCrunch`).
- Bottom captions do not cover the source highlight area.
- Output is 1080x1920, has audio, and passes `ffmpeg -v error -i out.mp4 -f null -`.
- If no talking-head footage is used, label the deliverable as a faceless/evidence-montage draft and offer to insert talking-head clips later.

## Pitfalls
- Do not present company-reported startup metrics as independently verified facts.
- Do not use generic AI robot/neural-network filler when the script has sourceable claims.
- Do not skip contact-sheet QA; source screenshots/highlights can look fine full-size but become unreadable on mobile.
