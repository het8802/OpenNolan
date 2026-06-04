# OpenMontage + FFmpeg Draft Render Fallback

Use this reference when producing a fast vertical social-video draft with a cloned OpenMontage repo but the preferred provider path is blocked or too slow.

## Trigger conditions

- OpenMontage is installed and usable, but API-gated TTS fails (example: OpenAI TTS returns `invalid_api_key`).
- Remotion can render technically but ETA is too long for the host/task.
- The user needs a reviewable first draft now, not a fully polished final.

## Practical workflow

1. Keep the work inside the OpenMontage project tree, e.g. `projects/<slug>/` with `assets/`, `qa/`, and `renders/`.
2. Use the existing script and asset/B-roll pack if available. Do not invent a new topic or switch scripts unless asked.
3. Generate voice locally when cloud TTS fails:
   - Prefer Piper if already installed by OpenMontage setup.
   - Otherwise use the local fallback voice path documented in `local-motion-graphics-fallback.md`.
4. Render a lightweight 9:16 motion-graphics draft with OpenMontage conventions plus FFmpeg/Python/Pillow as needed:
   - `1080x1920`
   - H.264 MP4
   - readable scene cards/captions
   - simple but purposeful movement, transitions, and SFX/bed if available
5. Mux voice and video with FFmpeg; verify with `ffprobe` rather than trusting the render command.
6. Generate a contact sheet before delivery. Fix obvious issues and re-render before pinging the user.

## QA checks that mattered

Run an independent stream probe:

```bash
ffprobe -v error -show_streams -select_streams v:0 renders/draft.mp4
ffprobe -v error -show_streams -select_streams a:0 renders/draft.mp4
```

Check audio loudness enough to catch silent/peaking output:

```bash
ffmpeg -hide_banner -i renders/draft.mp4 -af volumedetect -f null - 2>&1 | grep -E 'mean_volume|max_volume'
```

Generate/contact-sheet inspect. If tiles show unsupported emoji/glyph boxes, awkward title wraps, right-edge clipping, blank frames, or CTA overlap, edit the scene text/layout and rerender.

## Reporting language

Be explicit that this is a **first draft** if using fallback rendering. Include:

- `MEDIA:/absolute/path/to/render.mp4` when responding in Slack.
- Absolute path, format, resolution, duration, audio presence, and size.
- QA performed: stream probe, audio-level check, contact-sheet review.
- Caveats: provider-key failure, local voice fallback, Remotion/runtime slowness, and suggested next improvements.

Avoid implying the fallback draft is equivalent to a polished Remotion/provider-native render.