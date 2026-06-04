# TTS voice selection and Slack delivery notes

Session learning from a Marketing OS test short:

## Voice selection

- Default ElevenLabs `Adam` (`pNInz6obpgDQGcFmaJgB`) sounded too bland for short-form AI/tech videos.
- For creator-style shorts, prefer a more energetic social-media voice.
- Current preferred voice: `Liam - Energetic, Social Media Creator` (`TX3LPaxmHKxFdv7VOQHJ`).
- Keep provider `elevenlabs` and model `eleven_multilingual_v2` unless there is a reason to test another model.
- Configure with:

```bash
hermes config set tts.elevenlabs.voice_id TX3LPaxmHKxFdv7VOQHJ
hermes config set tts.elevenlabs.model_id eleven_multilingual_v2
```

- Generate a short sample before full video production:

```text
OpenAI just opened a new door for student AI clubs. If your campus has builders, hackers, or startup people, this is the signal to move before it turns into a waitlist.
```

- Verify sample existence/duration with `ffprobe -v error -show_entries format=duration,size`.

## ElevenLabs voice discovery

Use the configured Hermes env reader, not raw `os.environ`, because `.env` may not be shell-exported:

```bash
cd ~/.hermes/hermes-agent
python3 - <<'PY'
import requests
from hermes_cli.config import get_env_value
key = get_env_value('ELEVENLABS_API_KEY')
r = requests.get('https://api.elevenlabs.io/v1/voices', headers={'xi-api-key': key}, timeout=30)
r.raise_for_status()
for v in r.json().get('voices', []):
    labels = v.get('labels') or {}
    print(f"{v.get('name')} | {v.get('voice_id')} | {labels}")
PY
```

Useful available voices seen in this environment:

- `Laura - Enthusiast, Quirky Attitude` — `FGY2WhTYpPnrIDTdsKH5`, social-media, young female, sassy.
- `Liam - Energetic, Social Media Creator` — `TX3LPaxmHKxFdv7VOQHJ`, social-media, young male, confident.
- `Charlie - Deep, Confident, Energetic` — `IKne3meq5aSn9XLyUdCD`, hyped Australian male.
- `Jessica - Playful, Bright, Warm` — `cgSgspJ2msm6clMCkdW9`, conversational, young female, cute.

## Slack delivery pitfall

Hermes `send_message` accepts `MEDIA:/path` in text, but for Slack the media attachment was omitted with a warning: native media delivery is not currently supported for Slack by this connector. When sending Marketing OS videos to Slack:

1. Send the script, source, QA summary, and local file path.
2. Do not imply the MP4 is attached unless the Slack connector explicitly confirms an upload.
3. If the user requires native Slack video upload, use a Slack files API path/tool if available; otherwise report that only the local path was shared.
