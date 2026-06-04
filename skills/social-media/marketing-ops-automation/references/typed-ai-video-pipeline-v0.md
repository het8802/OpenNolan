# Typed AI Video Production Pipeline v0

Use this reference when the user wants a production-grade short-video system rather than a one-off rendered video.

## Session learning

A useful v0 is an end-to-end local pipeline, not only architecture docs. Build runnable stages that produce artifacts even before paid APIs are connected:

1. research card JSON with source-policy enforcement
2. source-backed script JSON
3. fact-check result that rejects unsupported claims
4. storyboard JSON with scene timing, shot type, visual need, captions
5. asset ledger JSONL with license metadata and YouTube-download guardrails
6. deterministic placeholder voiceover word timings
7. timeline JSON with 1080x1920, >=30fps, scenes, captions, audio refs, claim refs
8. optional local FFmpeg render to MP4
9. ffprobe/ffmpeg decode verification
10. QA report
11. export package with caption, hashtags, sources, alt text, QA

## Concrete project shape used

Project root: `/home/ubuntu/ai-video-pipeline`

Key files:

- `src/workflows/v0-pipeline.ts` — orchestrates local v0
- `src/workflows/run-v0.ts` — CLI wrapper
- `src/research/create-research-card.ts`
- `src/planning/fact-check-script.ts`
- `src/planning/storyboard-validator.ts`
- `src/assets/ledger.ts`
- `src/audio/voiceover.ts`
- `src/audio/mix-audio.ts`
- `src/timeline/create-timeline.ts`
- `src/timeline/validate-timeline.ts`
- `src/renderers/ffmpeg/render.ts`
- `src/qa/ffprobe.ts`
- `src/qa/video-qa.ts`
- `src/export/export-package.ts`

Run dry-run:

```bash
cd /home/ubuntu/ai-video-pipeline
npm run v0:run -- ./runs/dry-run "AI agents need production pipelines"
```

Run actual local MP4 render:

```bash
npm run v0:run -- ./runs/rendered --render "AI agents need production pipelines"
```

Verification commands:

```bash
npm run typecheck
npm test
ffprobe -v error -show_entries stream=width,height,r_frame_rate -show_entries format=duration,size -of default=nw=1 runs/rendered/renders/final.mp4
ffmpeg -v error -i runs/rendered/renders/final.mp4 -f null -
ffmpeg -y -loglevel error -i runs/rendered/renders/final.mp4 -vf "fps=1/3,scale=270:480,tile=2x2:padding=8:margin=8" -frames:v 1 runs/rendered/qa/contact-sheet.jpg
```

## FFmpeg concat pitfall

When writing a concat list inside a work directory, use absolute clip paths or paths relative to the concat file. A broken version wrote entries like `runs/demo-render/renders/.ffmpeg-work/scene-00.mp4` into `runs/demo-render/renders/.ffmpeg-work/concat.txt`; FFmpeg resolved them relative to the concat file and looked for duplicated paths such as:

`runs/demo-render/renders/.ffmpeg-work/runs/demo-render/renders/.ffmpeg-work/scene-00.mp4`

Fix: resolve `outputPath` and `workDir` to absolute paths before creating scene clip paths and concat entries.

## QA status convention

If render is skipped, treat decode as a warning and allow export only when explicitly passing `allowWarnings`. If actual render runs and decode verification passes, QA can be `passed`.

## What v0 is not

This v0 does not replace production integrations. It uses deterministic local placeholders for live research, screenshots, assets, and voiceover. Next adapters to add:

- Tavily/Exa/OpenAI search
- Firecrawl/source extraction
- real screenshot capture
- ElevenLabs timestamped TTS/forced alignment
- Pexels/Pixabay/Storyblocks asset search
- Remotion renderer or Plainly/AE templates
