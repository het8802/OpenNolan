# Reaction GIF Finder for Short-Form Humor Inserts

> Find no/low-text animated reaction GIFs/MP4s from a script, build a contact sheet, and shortlist editor-ready inserts for Reels/Shorts/TikToks.

## When to Use

Use this when a short-form script needs quick humor, reaction cutaways, meme GIFs, or visual pattern interrupts. This is for **animated reaction inserts**, not static long-caption memes.

Do not use this when the user asks for source-backed evidence, B-roll, or UI proof. Use `broll-planning.md` / source-receipt workflows for that.

## Core Principle

Search for the **emotion/action beat**, not the literal business concept.

Bad query:

```text
AI chatbot invoice approval workflow SaaS
```

Good queries:

```text
facepalm reaction
panic reaction
confused computer reaction
running chase funny reaction
victory dance reaction
```

GIFs should be readable in **0.5-1.5 seconds**. If the joke requires reading a caption, reject it.

## Workflow

### 1. Extract reaction beats from the script

Create 5-8 beats. Each beat should map to a simple reaction intent.

| Script moment | GIF intent | Example query |
|---|---|---|
| A generic chatbot fails | disappointed / facepalm | `facepalm reaction` |
| Someone is chasing a process | running / chase | `running chase funny reaction` |
| Approval bottleneck appears | panic | `panic attack reaction` |
| Too many tools/apps | confused computer | `confused computer reaction` |
| Workflow becomes messy | chaos / disaster | `disaster chaos reaction` |
| Narrow workflow works | celebration | `victory dance reaction` |

### 2. Search animated GIF providers

Preferred source for public reaction GIF discovery: **Tenor API**.

Use an official Tenor API key when available:

```bash
export TENOR_API_KEY="..."
```

Request animated/edit-friendly formats:

```bash
curl -sG "https://tenor.googleapis.com/v2/search" \
  --data-urlencode "q=panic attack reaction" \
  --data-urlencode "limit=20" \
  --data-urlencode "media_filter=gif,tinygif,mp4,tinymp4" \
  --data-urlencode "contentfilter=medium" \
  --data-urlencode "key=${TENOR_API_KEY}"
```

Prefer `mp4` / `tinymp4` in the edit timeline because they compress and compose better than raw GIFs. Keep `gif` only as preview/backup.

### 3. Filter aggressively

Reject candidates with:

- long readable captions/subtitles
- static meme images
- jokes that only work if the viewer reads text
- duration above ~5s unless trimming is obvious
- weak/ambiguous emotion
- visual clutter that will not read on phone screens
- watermarks or quality issues that dominate the frame

Keep candidates with:

- clear facial/body reaction
- no or minimal text
- obvious emotion/action
- short loopable motion
- works as a 0.5-1.5s cutaway

### 4. Download and record provenance

For each candidate, save:

```json
{
  "beat": "Approval gate panic",
  "query": "panic attack reaction",
  "title": "...",
  "tenor_url": "https://tenor.com/view/...",
  "mp4_path": "assets/reaction-gifs/...mp4",
  "gif_path": "assets/reaction-gifs/...gif",
  "duration": 2.4,
  "dims": [498, 498]
}
```

Put project-specific files under:

```text
projects/<project-name>/assets/reaction-gifs/
projects/<project-name>/artifacts/reaction-gif-index.json
```

### 5. Build a contact sheet

Never return raw GIF links as the main deliverable. Build a contact sheet so the user/editor can compare quickly.

Implementation pattern:

```bash
# Extract one representative frame per candidate.
ffmpeg -y -ss 0.35 -i candidate.mp4 -frames:v 1 \
  -vf "scale=240:180:force_original_aspect_ratio=decrease,pad=240:180:(ow-iw)/2:(oh-ih)/2:color=black" \
  frames/candidate.jpg
```

Then tile frames with labels:

- beat name
- query/rank
- title/duration

Save contact sheet to:

```text
projects/<project-name>/artifacts/reaction-gif-contact.jpg
```

### 6. Shortlist

After visual QA, keep 1-3 candidates per beat.

Shortlist output:

```text
projects/<project-name>/assets/reaction-gifs/shortlist/
projects/<project-name>/artifacts/reaction-gif-shortlist.json
```

## OpenNolan Integration

Use reaction GIFs as **brief pattern interrupts**:

```text
spoken line -> 0.5-1.5s reaction GIF -> return to talking head / proof visual
```

Recommended placements:

- after a punchline
- after a failed/wrong-way claim
- during a “panic / chaos / approval bottleneck” beat
- before a payoff beat, as contrast

Do not stack too many GIFs. For a 30-60s Reel, usually 2-5 GIF inserts is enough.

When composing:

- Convert GIFs to MP4 if needed.
- Trim to the strongest 0.5-1.5s.
- Crop/pad to fit the scene slot.
- Duck voice/music only if the GIF has useful audio; otherwise discard GIF audio.
- Add SFX from `sfx-library.md` / `second-hook-sfx.md` for the comedy beat.

## Common Mistakes

- Searching the entire script instead of short reaction intents.
- Using static meme-search engines for fast Reel humor.
- Keeping text-heavy meme GIFs that viewers cannot read.
- Returning links instead of a contact sheet.
- Using too many GIFs, making the video feel like a meme dump.
- Forgetting source/provenance URLs in the asset manifest.

## Software Used in the Evaluation

The successful GIF finder used **Tenor search/API** for animated GIF/MP4 results.

The earlier open-source/public meme-search tests were useful for static meme discovery, but not good for this Reel use case because they returned mostly long-text image memes rather than fast animated reaction GIFs.
