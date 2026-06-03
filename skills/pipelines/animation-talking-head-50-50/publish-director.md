# Publish Director — animation-talking-head-50-50 Pipeline

## When to Use

You have a verified `render_report` and `final_review`. Your job is to prepare the publishing package: caption copy, hashtag list, and a `publish_log` artifact. Optionally run `content_signal` for a virality advisory.

## Caption Template

Structure the Instagram caption for maximum reach on this format:

```
{hook_line} (the first line of the reel, verbatim or close variant)

{2-4 key takeaway bullets — what the reel proved}

{CTA line} — matches the in-reel CTA badge
```

Example:
```
I am literally fed up of people building AI agents.

Frontier AI isn't building broad chatbots — they're deploying engineers
INTO companies to build ONE painful workflow.

OpenAI → PayPal, QuickBooks
Claude for Small Business → month-end closing, invoice follow-ups

The winners won't be the broadest chatbots. The narrowest workflows.

Comment 'workflow' and I'll send you the checklist.
```

**Caption rules for this format:**
- First line must hook immediately — repeat or closely mirror the reel's opening line
- Short paragraphs (2-3 lines max per block)
- End with the CTA — matches whatever badge/CTA appears in the reel's payoff beat
- No excessive hashtags in caption body — keep them in the first comment OR at the very end

## Hashtag Strategy

Pick 8-12 hashtags. Mix:
- Topic-specific (e.g., `#aiagents #aistartup #productbuilding`)
- Audience-specific (e.g., `#founders #techstartup #buildinpublic`)
- Discovery (e.g., `#ai #openai #anthropic`)

Avoid generic filler hashtags (`#viral`, `#trending`). Each hashtag should be one someone in the target audience actually follows.

## content_signal (Optional)

If `content_signal` tool is available, run it for an advisory virality score:
```python
from tools.analysis.content_signal import ContentSignal
result = ContentSignal().execute({
    "video_path": "projects/{name}/renders/final.mp4",
    "platform": "{platform}",
    "hook": "{hook_text}",
    "has_captions": true,
    "has_cta": true,
    "duration_seconds": {duration}
})
```
Record the score and any recommendations in `publish_log.metadata.content_signal`. The score is **advisory** — it never blocks publishing.

## Publish Log

```json
{
  "version": "1.0",
  "deliverable": "projects/{name}/renders/final.mp4",
  "platform": "{platform}",
  "caption": "{full caption text}",
  "hook_text": "{first line}",
  "cta": "{CTA text matching in-reel badge}",
  "hashtags": ["{hashtag1}", "{hashtag2}", "..."],
  "specs": {
    "resolution": "1080x1920",
    "duration_seconds": {duration},
    "aspect_ratio": "9:16",
    "format": "H.264 MP4",
    "audio": "AAC stereo 44.1kHz"
  },
  "metadata": {
    "content_signal": {score_object_or_null},
    "pipeline": "animation-talking-head-50-50",
    "playbook": "{playbook_name}",
    "render_runtime": "hyperframes+ffmpeg"
  }
}
```

## Human Review Gate

Present to the user:
1. The draft caption
2. The hashtag list
3. The content_signal advisory (if run)
4. The final video path

Wait for approval before declaring the stage complete. Common revision requests:
- Tweak the CTA wording
- Adjust hashtag mix
- Shorten/lengthen the caption
