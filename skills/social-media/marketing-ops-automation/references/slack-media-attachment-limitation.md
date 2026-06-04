# Slack media attachment limitation for Marketing OS videos

Session learning from a Tech/AI Content OS test draft sent to `slack:hermes-home`.

## What happened

A verified MP4 existed locally and was included in a Slack `send_message` body as:

```text
MEDIA:/home/ubuntu/.hermes/marketing-os/productions/.../openai-campus-network-short.mp4
```

The Slack send succeeded, but the tool response warned:

```text
MEDIA attachments were omitted for slack; native send_message media delivery is currently only supported for telegram, discord, matrix, weixin, signal, yuanbao and feishu
```

So the Slack channel received the text and local path, not a native uploaded video attachment.

## Future workflow

1. For Slack video delivery, include the verified local path or a shareable link in the message.
2. After calling `send_message`, read the tool response before claiming the media was sent.
3. If Slack media was omitted, immediately post or report the limitation clearly: Slack got text/path only, not an attached MP4.
4. Do not use `MEDIA:` tags in CLI-facing final answers; they render as literal text. Plain absolute paths are enough for terminal users.

## Related QA still required before sharing a path

```bash
ffprobe -v error -show_entries format=duration,size -of default=nw=1 video.mp4
ffmpeg -v error -i video.mp4 -f null -
ffmpeg -hide_banner -nostats -i video.mp4 -vf "blackdetect=d=0.25:pic_th=0.98" -an -f null -
```

Build and inspect a contact sheet before delivery; if main captions clip at the right edge, wrap or shrink text and rerender before sending.
