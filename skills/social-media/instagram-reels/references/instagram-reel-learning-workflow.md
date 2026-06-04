# Instagram Reel “Learn this” extraction workflow

Use when Het shares an Instagram Reel and asks to learn the pattern, especially when the normal web/browser path returns an Instagram login shell.

## Proven fallback from Reel `DWUCCNVjCYT`

1. Try browser/web extraction first for title/caption/metadata.
2. If Instagram blocks scraping, use `yt-dlp --write-info-json --write-thumbnail --skip-download` to capture public metadata without downloading media.
3. If a later `yt-dlp` download attempt hits login/rate-limit but `info.json` contains signed format URLs, manually download the chosen video/audio URLs with their `http_headers` from the JSON.
4. Mux video + audio with FFmpeg into a temporary MP4.
5. Create a contact sheet sampled every 1–2 seconds for visual/OCR analysis:
   ```bash
   ffmpeg -y -i reel.mp4 -vf "fps=1/2,scale=270:-1,tile=5x4" -frames:v 1 contact.jpg
   ```
6. Extract mono 16k audio and transcribe locally with `faster_whisper` if available:
   ```bash
   ffmpeg -y -i reel.mp4 -vn -ac 1 -ar 16000 audio.wav
   python - <<'PY'
   from faster_whisper import WhisperModel
   model = WhisperModel('tiny.en', device='cpu', compute_type='int8')
   segments, info = model.transcribe('audio.wav', beam_size=5)
   for s in segments:
       print(f'[{s.start:.1f}-{s.end:.1f}] {s.text}')
   PY
   ```
7. Use the contact sheet + transcript + caption metadata to extract the reusable creative pattern, then patch the appropriate skill.
8. Delete temporary media/artifacts unless Het explicitly asks to keep them.

## Pitfalls

- `video_analyze` may not actually inspect a local path even if it accepts the argument; verify with contact sheets/frames and transcript instead of trusting a generic response.
- Instagram Reels can require login on the second call even when metadata succeeded on the first call. Preserve/use the initial `info.json` if available.
- Treat temporary downloaded media as analysis artifacts only; clean `/tmp/...` after saving the reusable lesson.
