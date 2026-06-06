# Asset Director — animation-talking-head-50-50 Pipeline

## When to Use

You have an approved scene plan. Your job is to prepare three things:
1. **Talking head video segments**: Crop-only FFmpeg extracts of the source footage for each scene, with NO color-space conversion.
2. **External visual assets**: Real-world images, article screenshots, and downloaded video clips sourced from the web based on each scene's `asset_type` classification.
3. **HyperFrames animated overlay**: `index.html` authored from the scene_plan's hyperframes_spec entries, incorporating downloaded assets as needed.

Then produce the `asset_manifest`.

## Visual Asset Sourcing (NEW)

The scene plan marks each scene's external visual need with an `asset_type` field. Pick the right tool for each type:

| asset_type | Tool | When |
|---|---|---|
| `web_photo` | `web_image_search` (selector) | Real-world event photos, keynote stages, product launches, people at events. DDG by default (free); auto-upgrades to Firecrawl for HD. |
| `logo` | `web_image_search` with `type_image: "transparent"` | Company logos, brand marks. Always add "transparent PNG" or "logo SVG" to the query. |
| `article_screenshot` | `webpage_screenshot` | Capture the actual article page as a visual proof card. Pass the article URL directly. |
| `stock_photo` | `image_selector` (pexels/pixabay) | Generic lifestyle, office, nature, people — when you don't need a specific real event. |
| `ai_generated` | `image_selector` (flux/grok/imagen) | Custom diagrams, stylized illustrations, conceptual art. |
| `video_clip` | `video_downloader` → `scene_detect` + `transcriber` → `video_trimmer` | Actual footage from YouTube/TikTok/Instagram to use as B-roll or full-screen evidence. |
| `animated_panel` | HyperFrames HTML/GSAP | Greg animated diagrams, phrase collages, receipt cards — authored in the composition, no external fetch. |

### web_photo workflow
```python
from tools.tool_registry import registry
registry.discover()
sel = registry._tools["web_image_search"]

result = sel.execute({
    "query": "Google IO 2024 keynote stage photo",
    "max_results": 5,
    "size": "Large",
    "output_dir": f"projects/{name}/assets/images/",
    "download_top_n": 2,
})
# result.data["downloaded_paths"] → list of local PNG/JPG paths
# Pick the best one — read the images and choose the most visually suitable
```

### logo workflow
```python
result = sel.execute({
    "query": "Google logo transparent PNG",
    "max_results": 5,
    "type_image": "transparent",   # critical for logos
    "output_dir": f"projects/{name}/assets/images/",
    "download_top_n": 1,
})
```

### article_screenshot workflow
```python
sc = registry._tools["webpage_screenshot"]
result = sc.execute({
    "url": "https://techcrunch.com/2024/05/14/google-io-gemini-announcement/",
    "output_dir": f"projects/{name}/assets/images/",
    "full_page": False,    # viewport crop — better for article headers
    "also_return_markdown": True,   # optionally get the article text too
})
# result.data["local_path"] → PNG path
# result.data["title"] → article title for the Greg card
# result.data["markdown"] → article text (use to verify the claim)
```

### video_clip workflow
```python
dl = registry._tools["video_downloader"]
dl_result = dl.execute({
    "url": "https://www.youtube.com/watch?v=XEzRZ35urlk",  # Google IO keynote
    "output_dir": f"projects/{name}/assets/video/downloaded/",
    "format": "video",
    "max_resolution": "1080p",
})
video_path = dl_result.data["video_path"]

# Transcribe to find the right timestamp
tr = registry._tools["transcriber"]
transcript = tr.execute({"source": video_path, "word_timestamps": True})

# Scene detect to find clean cut points
sd = registry._tools["scene_detect"]
scenes = sd.execute({"source": video_path})

# Trim the specific clip you need
trimmer = registry._tools["video_trimmer"]
clip = trimmer.execute({
    "source": video_path,
    "start_seconds": 142.5,    # from transcript analysis
    "end_seconds": 158.0,
    "output_path": f"projects/{name}/assets/video/google_io_gemini_clip.mp4",
})
```

### Quality rules for sourced assets
- For web_photo: sample 3-5 candidates, read each image, choose the one with clearest composition and highest resolution. Skip watermarked or thumbnail-quality images.
- For logo: verify the background is actually transparent (PNG with alpha) or white/solid-color. Reject logos with complex backgrounds.
- For article_screenshot: read the screenshot — confirm the article headline is visible and the page rendered correctly (not a paywall/cookie wall). If it shows a paywall, try `full_page: False` with a narrow viewport or find an alternative URL.
- For video_clip: read the transcript around the target timestamp. The clip should contain the specific claim being made, not adjacent content.

## Critical Rule: Talking Head Video Must Be Untouched

> **NEVER apply color-space conversion flags to talking head video segments.**

Allowed FFmpeg operations on talking head clips:
- `-vf "scale=W:H"` — resize
- `-vf "crop=W:H:X:Y"` — crop
- Combined: `-vf "scale=1080:1920,crop=1080:864:0:{face_crop_y}"`

Forbidden flags (they change the color interpretation and make the video look dull/washed-out):
- `-colorspace bt709` or `-colorspace 1`
- `-color_primaries bt709` or `-color_primaries 1`
- `-color_trc bt709` or `-color_trc 1`
- `-color_transfer bt709` or similar
- `-x264-params colorprim=bt709:transfer=bt709:colormatrix=bt709`
- `-pix_fmt yuv420p` on the talking head (quantizes 10-bit HDR to 8-bit)
- Any `zscale` colorspace conversion (e.g., `zscale=transfer=bt709`)

**Why this matters**: iPhone HDR footage (HEVC, BT.2020 HLG) has rich color data. Applying SDR conversion metadata changes how the display renders the colors, producing a visibly flat, dull image. The original footage is beautiful — don't touch it.

**Consequence of violation**: EP G4 gate will fail if `color_transfer` on a talking head segment differs from the source.

> ### ⚠️ EXCEPTION — HDR source delivered as an SDR reel (the dull-colors trap)
> The "never convert" rule above protects a **pure-HDR delivery**. But Instagram/TikTok reels and this animated composite are delivered **8-bit SDR**. If you leave HLG/BT.2020 footage "untouched" and just scale it into an SDR output, the wide HDR range flattens and clips → **dull, grey, washed-out skin** (the original looked rich on an HDR phone; SDR can't show that range without remapping). This is a well-documented iPhone-HDR issue and it bit the `chrome-devtools` reel: the creator shipped the "untouched" flat look, then asked "why are my colors dull?".
>
> **When the final deliverable is SDR (the normal case here), you SHOULD tonemap HLG→Rec.709 — and you must do it with the CORRECT chain or you wash it out worse.** Mandatory pieces: explicit input transfer (`tin=arib-std-b67`), a **float intermediate** (`format=gbrpf32le`), gamut-map to bt709 **before** tonemap, and **`desat=0`** (the default `desat=2.0` greys highlights — this is the #1 cause of "tonemap looked worse"):
> ```
> zscale=tin=arib-std-b67:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=mobius:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p
> ```
> `tonemap=mobius` reads rich/warm; `hable` is the more natural alternative; `npl=400` reads darker/more graded. libplacebo is higher quality but needs Vulkan (often unavailable on macOS — check `ffmpeg -hwaccels`). **ALWAYS render a side-by-side still (flat vs hable vs mobius vs npl=400) and let the user choose the grade — it's their face — then apply that chain when building the footage segments/plate.** Record the chosen grade in `edit_decisions.metadata`. (Naive scale-only "untouched" is correct ONLY if you are genuinely delivering HDR, which reels are not.)

## Part 1: Talking Head Video Segments

For each scene that includes talking head video:

### Full-frame scenes (hero_talking_head)
Extract a time-trimmed segment from the source:
```bash
ffmpeg -y -ss {start} -to {end} -i {source} \
  -vf "scale=1080:1920,format=yuv420p10le" \
  -an -c:v libx264 -r 30 -g 30 -preset fast -crf 18 \
  -movflags +faststart \
  projects/{name}/assets/video/seg-{id}-full.mp4
```

Note: `format=yuv420p10le` preserves the 10-bit depth without changing color interpretation. Do NOT add `-pix_fmt yuv420p` (would force 8-bit and quantize colors).

Alternatively, if stream copy is possible (source is already the right codec):
```bash
ffmpeg -y -ss {start} -to {end} -i {source} \
  -c:v copy -an -movflags +faststart \
  projects/{name}/assets/video/seg-{id}-full.mp4
```

### Bottom-panel scenes (split_screen_greg)
Apply the scene_plan's `face_crop_y`:
```bash
ffmpeg -y -ss {start} -to {end} -i {source} \
  -vf "scale=1080:1920,crop=1080:864:0:{face_crop_y},format=yuv420p10le" \
  -an -c:v libx264 -r 30 -g 30 -preset fast -crf 18 \
  -movflags +faststart \
  projects/{name}/assets/video/seg-{id}-bot.mp4
```

### Verification (mandatory)
After creating each segment:
```bash
ffprobe -show_streams -select_streams v seg-XX.mp4 | grep -E "color_transfer|pix_fmt|codec_name"
```
Compare `color_transfer` against the source. If the source was `arib-std-b67` (HLG), the segment MUST also show `arib-std-b67` (or unspecified). If it shows `bt709`, the conversion flag was applied — regenerate without the flag.

### HyperFrames HDR detection workaround
When video segments preserve their original color metadata (HLG/PQ), HyperFrames detects them as HDR and switches to "layered composite" mode. **This is why the HyperFrames composition does NOT contain video elements.** The talking head video stays entirely within FFmpeg's domain. HyperFrames only sees the animated overlay content (no video elements → no HDR detection → standard SDR render mode).

## Part 2: HyperFrames Workspace

### 2a. Create workspace
```
projects/{name}/hyperframes/
├── index.html
├── DESIGN.md
├── assets/
│   ├── video/      (talking head segments — but NOT used by HyperFrames directly)
│   ├── audio/      (extracted narration, for reference)
│   └── fonts/      (local Outfit + Inter .ttf files — NO Google Fonts CDN)
```

**Fonts**: Download Outfit (weights 700, 800, 900) and Inter (weights 400, 600, 700) as .ttf files and reference via `@font-face` in the HTML. Do NOT use `<link href="https://fonts.googleapis.com/...">` — this fails in offline/sandboxed renders.

**Assets in workspace**: The HyperFrames `index.html` does NOT reference any `<video>` elements. The `assets/video/` folder exists for FFmpeg's use in the assembly step, not for HyperFrames.

### 2b. Write DESIGN.md
Per the HyperFrames SKILL.md hard gate, write a DESIGN.md before any HTML:

```markdown
# DESIGN — {project_name}

## Style Prompt
{One paragraph from the playbook describing the visual system}

## Colors
- `{bg}` — canvas background
- `{forest}` — positive/CTA accent
- `{coral}` — negative/strike accent
- `{mint}` — underlines, connectors
- `{sage}` — tool pills, secondary positive
- `{gold}` — CTA badge
- `{charcoal}` — primary text

## Typography
- Headlines: {font} {weight}, {tracking}
- Body: Inter {weight}

## Motion Rules
- Entrances: 0.28–0.40s, power2.out or power3.out
- Connector draws: stroke-dashoffset, 0.45–0.50s
- Badge pops: back.out(1.4)
- Pill pops: back.out(1.6)
- No repeat:-1, no async GSAP, no Date.now()

## What NOT to Do
{3-5 anti-patterns from the playbook}
```

### 2c. Write index.html (the animated overlay track)

Structure:
```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  @font-face { font-family:'Outfit'; font-weight:700; src:url('assets/fonts/Outfit-700.ttf'); }
  @font-face { font-family:'Outfit'; font-weight:800; src:url('assets/fonts/Outfit-800.ttf'); }
  @font-face { font-family:'Outfit'; font-weight:900; src:url('assets/fonts/Outfit-900.ttf'); }
  @font-face { font-family:'Inter';  font-weight:400; src:url('assets/fonts/Inter-400.ttf'); }
  @font-face { font-family:'Inter';  font-weight:600; src:url('assets/fonts/Inter-600.ttf'); }
  @font-face { font-family:'Inter';  font-weight:700; src:url('assets/fonts/Inter-700.ttf'); }
  * { margin:0; padding:0; box-sizing:border-box; }
  html, body { width:1080px; height:1920px; overflow:hidden; background:{playbook_bg}; }
  #comp { position:relative; width:1080px; height:1920px; overflow:hidden; background:{playbook_bg}; }
  /* ... all CSS for scenes ... */
</style>
</head>
<body>
<div id="comp" data-composition-id="{project_id}" data-start="0" data-duration="{total_duration}" data-width="1080" data-height="1920">

  <!-- NO <video> or <audio> elements here.
       HyperFrames renders only the animated overlay track.
       Talking head video is composited by FFmpeg in the assembly step. -->

  <!-- SC-01: hero_talking_head overlay (hook) -->
  <div id="sc01-overlay" class="clip overlay" data-start="0" data-duration="2.52" data-track-index="5">
    <div id="pill-agents" class="sticker-pill">AI AGENTS ✗</div>
    ...
  </div>

  <!-- SC-02: split_screen_greg top panel (the shift) -->
  <div id="sc02-top" class="clip top-panel" data-start="2.52" data-duration="9.54" data-track-index="5">
    <svg>...</svg>
  </div>
  <!-- SC-02: bottom panel PLACEHOLDER (solid bg, FFmpeg will overlay the actual video) -->
  <div id="sc02-bot-placeholder" class="clip bottom-placeholder" data-start="2.52" data-duration="9.54" data-track-index="4">
    <!-- Solid {playbook_bg} rectangle. No video element. -->
  </div>
  <div id="sc02-divider" class="clip split-divider" data-start="2.52" data-duration="9.54" data-track-index="6"></div>

  <!-- ... remaining scenes ... -->

  <!-- CAPTION OVERLAY (full duration) -->
  <div id="caption-track" class="clip" data-start="0" data-duration="{total_duration}" data-track-index="8" ...>
    <!-- 81 cap-XX divs, no data-start/data-end on individual divs -->
    <div id="cap-00" class="cap-chunk">I am literally</div>
    ...
  </div>

  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <script>
    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });
    // All GSAP tweens at absolute composition times
    // ...
    // Explicit exit tweens for each scene (REQUIRED — backup for clip visibility):
    // tl.set(['#pill-agents', '#pill-chatbots'], {opacity:0}, 2.52);
    // ...
    window.__timelines["{project_id}"] = tl;
  </script>
</div>
</body>
</html>
```

Key HyperFrames authoring rules (read `skills/core/hyperframes.md` and `.agents/skills/hyperframes/SKILL.md` before writing):
- **NARRATION SYNC (HARD RULE): every GSAP reveal time comes from `transcript.json words[]`, set to the start of the word it illustrates — never earlier.** When writing the `tl.fromTo(..., abs_time)` calls, look up each trigger word's timestamp and use it as the position parameter. Specific items reveal one-per-keyword in spoken order; multi-part diagrams build progressively across the spoken clause (don't finish drawing before the speaker gets there); reaction-GIF/overlay windows run from the trigger word to the scene cut. A reveal that precedes its word is a defect. (See scene-director "Narration Sync — HARD RULE".) Keep a comment next to each scene's tweens noting the trigger word, e.g. `/* tools land ON search/compare/cart */`.
- All timed elements have `class="clip"` plus a descriptive class
- Video elements are FORBIDDEN in this composition (see above)
- All transforms set by GSAP must be absent from CSS (no conflicting CSS `transform`)
- For centered elements: use `left: 50%` in CSS + GSAP `xPercent: -50` (NOT `transform: translateX(-50%)` in CSS)
- Add explicit `tl.set()` exit tweens at each scene boundary as backup to HyperFrames clip visibility
- No `repeat: -1` in any GSAP tween
- All elements with `data-start`/`data-duration` need `class="clip"`

### 2d. Generate caption timing
Using the word-level transcript, group words into 3-word chunks and record {text, start, end} for each chunk.

Write all 81+ caption `<div class="cap-chunk">` elements (NO data-start/data-end on individual chunks — the parent `#caption-track` clip handles visibility). Add GSAP `to(opacity:1, 0.15s)` at each chunk's start time and `to(opacity:0, 0.12s)` at its end time.

Add a hard kill `tl.set('#cap-{n}', {opacity:0}, {boundary_time})` for any caption that ends within 0.15s of a scene boundary.

**Caption placement is layout-aware — do NOT bottom-pin (`bottom:72px` gets shadowed by the
IG/TikTok/Shorts UI; confirmed-rejected on `clicky`).** Make `#caption-track` a full-frame
clip (`top:0; height:1920px`) and give EACH chunk a second class chosen by the layout mode of
the scene its time window falls in:

```css
.cap-mid { top:955px; }   /* split_screen_greg: centered, in the lower strip of the top panel, above the y1056 divider */
.cap-up  { top:1405px; }  /* hero_talking_head + full_greg_card: lifted lower-third, clear of the platform UI shadow */
```

```html
<div id="cap-12" class="cap-chunk cap-mid">your apps</div>   <!-- chunk falls inside a split scene -->
<div id="cap-44" class="cap-chunk cap-up">please hire me</div> <!-- chunk falls inside a hero/card scene -->
```

Keep `.cap-chunk { left:50%; transform:translateX(-50%); }` (GSAP touches only opacity, so no
transform conflict). Match each chunk to its scene by start-time vs the scene cut list in the
scene_plan. See the scene-director "Caption Config" section for the full placement table and the
hero-CTA collision rule.

### 2e. Extract audio
```bash
ffmpeg -y -i {source} \
  -vn -af "loudnorm=I=-14:TP=-1.5:LRA=11" \
  -acodec aac -b:a 192k \
  projects/{name}/assets/audio/narration.m4a
```
Note: `-vn` extracts audio only. No color-space flags needed (audio has no color).

## Part 3: lint the composition

Run before concluding:
```bash
npx hyperframes lint projects/{name}/hyperframes/
```

Must report 0 errors. Warnings acceptable (file too large, track too dense) but fix any `gsap_css_transform_conflict` warnings.

## Asset Manifest

```json
{
  "version": "1.0",
  "talking_head_segments": [
    {
      "id": "seg-01-hook-full",
      "path": "projects/{name}/assets/video/seg-01-hook-full.mp4",
      "source_start_seconds": 0.24,
      "source_end_seconds": 2.52,
      "layout_mode": "hero_talking_head",
      "crop_applied": "scale=1080:1920",
      "color_conversion_applied": false,
      "color_transfer_source": "arib-std-b67",
      "color_transfer_output": "arib-std-b67"
    }
    // ... one entry per scene that has talking head
  ],
  "hyperframes_workspace": "projects/{name}/hyperframes/",
  "hyperframes_index": "projects/{name}/hyperframes/index.html",
  "audio": {
    "narration": "projects/{name}/assets/audio/narration.m4a"
  },
  "caption_chunks": 81,
  "lint_passed": true
}
```
