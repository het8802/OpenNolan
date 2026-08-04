# OpenAI/YC credits Greg-style Remotion reel — session reference

Use this as a concrete reference when the user asks to turn a daily AI/tech script into a clean Greg Isenberg / Hyperagent-style informational Reel without talking head.

## Source and output
- Source script: `~/content-os/scripts/2026-05-21/daily-ai-tech-video.md`
- Project: `~/openai-credits-greg-reel`
- Silent/design MP4: `~/openai-credits-greg-reel/openai-credits-greg-reel.mp4`
- Final MP4 with ElevenLabs VO: `~/openai-credits-greg-reel/openai-credits-greg-reel-with-vo.mp4`
- Contact sheets: `~/openai-credits-greg-reel/contact-sheet.jpg`, `~/openai-credits-greg-reel/contact-sheet-with-vo.jpg`
- Main source: `~/openai-credits-greg-reel/src/Composition.tsx`
- Generated topical asset: `~/openai-credits-greg-reel/public/generated/ai-credit-cap-table.png`
- Voiceover script: `~/openai-credits-greg-reel/voiceover-script.md`
- Raw ElevenLabs VO: `~/openai-credits-greg-reel/public/voiceover/openai-credits-vo-elevenlabs.mp3`
- Timed VO: `~/openai-credits-greg-reel/public/voiceover/openai-credits-vo-timed.m4a`

## Working storyboard pattern
Topic: OpenAI reportedly offered current YC startups $2M in OpenAI tokens in exchange for equity via uncapped SAFE, compared with Google Cloud and Cloudflare startup credits.

27s structure:
1. Hook: `$2M in AI tokens is not free money.`
2. Source receipt: TechCrunch-reported OpenAI/YC token offer.
3. Hidden trade: tokens now → stack choice → equity later.
4. Founder math: four questions before taking credits.
5. Comparison: OpenAI/YC vs Google Cloud vs Cloudflare.
6. Mental model: Treat credits like capital, not coupons.
7. CTA: Comment CREDITS for founder checklist.

## Technique that worked
- Use `~/greg-style-kit` as the design system and copy it into `public/greg-style-kit`.
- Use Codex CLI native `$imagegen` for one topical 9:16 visual, then use Remotion as the deterministic assembler.
- Keep AI-generated image text as decorative context only; overlay the actual readable text in Remotion.
- Render stills at representative frames before full render.
- Create a 1fps contact sheet and run visual QA; do not rely only on successful MP4 render.
- For VO, use ElevenLabs when available, but rewrite the visual script tighter instead of narrating every on-screen word. The winning 27s VO was ~64 words, with emotional performance direction and a creator-native voice (`Liam - Energetic, Social Media Creator`, voice id `TX3LPaxmHKxFdv7VOQHJ`).

## Important Remotion timing pitfall
If scene components already use `useCurrentFrame()` with absolute frame numbers (e.g. `sceneOpacity(f, 350, 500)`), do **not** wrap later scenes in `<Sequence from={...}>` unless you subtract/normalize the local frame. In Remotion, `Sequence` shifts the child timeline, so the child sees local frame 0 and absolute `sceneOpacity` windows will not line up. In this session that caused the checklist still to show only the background. Fix used: render all scene components directly on the root timeline and let each scene's opacity gate itself by absolute frame.

## ElevenLabs voiceover workflow and pitfall

Commands/pattern used after generating `public/voiceover/openai-credits-vo-elevenlabs.mp3`:

```bash
DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 openai-credits-greg-reel.mp4)
VO_DUR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 public/voiceover/openai-credits-vo-elevenlabs.mp3)
ATEMPO=$(python - <<PY
video=float('$DUR'); vo=float('$VO_DUR'); print(vo/video)
PY
)
ffmpeg -y -loglevel error -i public/voiceover/openai-credits-vo-elevenlabs.mp3 \
  -af "atempo=${ATEMPO},loudnorm=I=-16:TP=-1.5:LRA=9,afade=t=in:st=0:d=0.08,afade=t=out:st=26.55:d=0.45" \
  -t "$DUR" public/voiceover/openai-credits-vo-timed.m4a
ffmpeg -y -loglevel error -i openai-credits-greg-reel.mp4 -i public/voiceover/openai-credits-vo-timed.m4a \
  -filter_complex "[0:a]volume=0.14[a0];[1:a]volume=1.15[a1];[a0][a1]amix=inputs=2:duration=longest:dropout_transition=0,loudnorm=I=-16:TP=-1.5:LRA=8,alimiter=limit=0.95[a]" \
  -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -t "$DUR" openai-credits-greg-reel-with-vo.mp4
```

Pitfall: `-shortest` with `amix=duration=first` produced a video whose audio stream ended early (~24.07s) even though the container was 27s. Fix: use `amix=duration=longest` and explicit `-t "$DUR"`, then verify the audio stream duration separately with `ffprobe -select_streams a:0`.

Voiceover QA checklist:
- `ffprobe` final container duration and audio stream duration should match the video duration.
- `ffmpeg -v error -i final.mp4 -f null -` should decode cleanly.
- `ebur128` should land around social loudness (this session: about `-15.7 LUFS`, peak `-3.2 dBFS`).
- `silencedetect` pauses of ~0.5–1.1s can be acceptable if they align with visual beat changes, but verify no long dead-air ending.
- Regenerate contact sheet after muxing; voiceover changes do not alter frames but it catches accidental wrong-file exports.

## Verification commands used
```bash
npm run lint
npx remotion still OpenAICreditsGregReel --frame=25 --scale=0.25 still-hook.png
npx remotion still OpenAICreditsGregReel --frame=385 --scale=0.25 still-math.png
npx remotion render OpenAICreditsGregReel openai-credits-greg-reel.mp4 --codec=h264 --crf=18 --concurrency=2
ffprobe -v error -show_entries format=duration,size -of default=nw=1 openai-credits-greg-reel.mp4
ffmpeg -v error -i openai-credits-greg-reel.mp4 -f null -
ffmpeg -hide_banner -nostats -i openai-credits-greg-reel.mp4 -vf "blackdetect=d=0.25:pic_th=0.98" -an -f null -
ffmpeg -y -loglevel error -i openai-credits-greg-reel.mp4 -vf "fps=1,scale=216:384,tile=6x5:padding=8:margin=8" -frames:v 1 contact-sheet.jpg
```

## QA notes
- Final duration: 27.051s, 5.8MB.
- Contact sheet showed coherent progression and no blank reel frames.
- Main readability issue to watch: tiny footer/source lines are often too small for mobile; make them decorative or enlarge them.
