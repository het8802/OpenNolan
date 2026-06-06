# Scene Director — animation-talking-head-50-50 Pipeline

## When to Use

You have an approved script. Your job is to produce a detailed `scene_plan` that specifies exactly how each scene will be composed — the HyperFrames HTML spec for every animated element, the face crop geometry, and the caption config. The compose director reads this to author `index.html` and the FFmpeg assembly commands.

## Prerequisites

| Layer | Resource |
|-------|----------|
| Script | `artifacts/script.json` — sections with layout_mode and enhancement_cues |
| Brief | `artifacts/brief.json` — playbook, canvas size, color palette |
| Playbook | `styles/greg-isenberg-product-explainer.yaml` — palette, typography, motion rules |
| Tools | `frame_sampler` (face position), `face_tracker` (optional) |

## The Face Crop Problem (SOLVE THIS FIRST)

iPhone talking head footage is typically 1440×2560 portrait, shot at a low angle. When scaled to 1080×1920 for the composition, the face often sits in the lower half of the frame. For the bottom panel (1080×864px), you need to crop a 864px window that shows the face prominently.

**Protocol:**
1. Run `frame_sampler` on the source video, sampling at 5+ timestamps spread across the duration.
2. For each sample: scale the frame to 1080×1920, then identify the y-coordinate of the eyes.
3. Choose `face_crop_y` such that `face_crop_y + 200 ≈ eye_y` (eyes appear 200px from the top of the 864px bottom panel). This gives a tight, engaging face frame.
4. Verify by extracting a test crop: `ffmpeg -i source.mp4 -vf "scale=1080:1920,crop=1080:864:0:{face_crop_y}" -frames:v 1 test.jpg`. Read the frame and confirm the face is well-framed.
5. Record `face_crop_y` in `scene_plan.metadata.face_crop_y`.

**Typical values:** `face_crop_y` between 500-800 for a typical desk-height iPhone shot. The v4 project used `y=650` for the stop-building-ai-agents footage.

**Critical:** Do NOT use `scale=1080:1920,crop=1080:864:0:0` (y=0) or a low y value — this shows the ceiling, not the face.

## Canvas Geometry

```
1080px wide × 1920px tall

┌─────────────────────────────────┐  ← y=0
│                                 │
│   Top Panel (animated)          │  → 1080 × 1056px (55%)
│   layout: split_screen_greg /   │
│           full_greg_card        │
│                                 │
├─────────────────────────────────┤  ← y=1056  (2px divider: #E0D8CE)
│                                 │
│   Bottom Panel (talking head)   │  → 1080 × 862px (45%)
│   layout: split_screen_greg     │
│                                 │
└─────────────────────────────────┘  ← y=1920
```

For `hero_talking_head`: full 1080×1920 is the talking head; overlays composited on top.
For `full_greg_card`: full 1080×1920 is the animated card; no talking head.

## HyperFrames Composition Architecture

The HyperFrames `index.html` renders the **animated overlay track only** — no `<video>` elements for the talking head. This is the critical architectural decision that keeps the talking head untouched.

For each scene, the HyperFrames spec defines:
- `layout_mode`
- For `split_screen_greg`: top panel animated content (SVG, phrase divs, receipt cards, pill stacks)
- For `full_greg_card`: full-canvas animated content
- For `hero_talking_head`: overlay elements (positioned absolutely, composited by FFmpeg)
- In all split-screen and full-card scenes: BOTTOM PANEL is solid playbook background color (NOT a video element)
- GSAP timeline entries with absolute composition times for every animated element

The HyperFrames composition has:
- `background: <playbook_bg>` (e.g., `#F5EFE6`) — fills video zones with solid color
- All animations via a single master GSAP timeline registered as `window.__timelines["<project_id>"]`
- Caption overlay spanning full duration
- NO `<video>` or `<audio>` elements

## Visual Asset Type Classification (NEW)

Before writing the `hyperframes_spec` for each scene, classify what kind of external visual asset (if any) the beat needs. This drives tool selection in the asset stage. Add an `asset_type` field to each scene:

| asset_type | Meaning | Asset tool used |
|---|---|---|
| `animated_panel` | Pure HyperFrames animation — no external asset needed | HyperFrames HTML/GSAP |
| `web_photo` | Real-world photo from the internet (event, keynote, product) | `web_image_search` (DDG → Firecrawl) |
| `logo` | Company/product logo with transparent/clean background | `web_image_search` with `type_image: "transparent"` |
| `article_screenshot` | Screenshot of a specific article or webpage as proof | `webpage_screenshot` |
| `stock_photo` | Generic stock photo (offices, people, lifestyle) | `image_selector` → pexels/pixabay |
| `ai_generated` | AI-generated illustration or concept image | `image_selector` → flux/grok |
| `video_clip` | Actual video clip from YouTube/Instagram/TikTok | `video_downloader` → `video_trimmer` |

Include a `source_hint` with enough detail for the asset director to write the exact query or URL:

```json
{
  "id": "sc-02",
  "layout_mode": "split_screen_greg",
  "asset_type": "web_photo",
  "source_hint": {
    "query": "Google IO 2024 keynote stage Sundar Pichai",
    "required_size": "Large",
    "note": "Use for the top panel background behind the Greg overlay card"
  }
}
```

or for a video clip:
```json
{
  "id": "sc-04",
  "layout_mode": "hero_talking_head",
  "asset_type": "video_clip",
  "source_hint": {
    "url": "https://www.youtube.com/watch?v=XEzRZ35urlk",
    "description": "Google IO 2024 keynote — find the segment where Gemini is announced (~2:22 in)",
    "target_duration_seconds": 12,
    "usage": "full_screen"
  }
}
```

When the top panel is a purely animated HyperFrames scene (connector diagram, phrase collage, etc.) set `"asset_type": "animated_panel"` — no external asset fetch needed.

When a sourced image (web_photo, logo) is used AS the background behind a Greg overlay, the HyperFrames spec should reference its eventual local path as an `<img>` background in the top panel div.

## Writing the hyperframes_spec for Each Scene

For each scene in the plan, produce a `hyperframes_spec` object:

### split_screen_greg scene
```json
{
  "layout_mode": "split_screen_greg",
  "asset_type": "animated_panel",
  "hyperframes_spec": {
    "top_panel": {
      "type": "connector_diagram | phrase_collage | receipt_card | hero_card | pill_stack_checklist | phrase_reframe",
      "bg": "#F5EFE6",
      "content": { ... }
    },
    "bottom_panel": {
      "type": "talking_head_placeholder",
      "bg": "#F5EFE6",
      "note": "Solid background. FFmpeg replaces with actual talking head video during assembly."
    },
    "gsap_entries": [
      { "target": "#element-id", "props": {"opacity": 1, "y": 0}, "duration": 0.30, "ease": "power3.out", "abs_time": 2.82 }
    ]
  }
}
```

### hero_talking_head scene
```json
{
  "layout_mode": "hero_talking_head",
  "hyperframes_spec": {
    "full_canvas_bg": "transparent_or_#F5EFE6",
    "overlays": [
      { "id": "pill-agents", "type": "sticker_pill", "text": "AI AGENTS ✗", "bg": "#D96D5F", "position": {"top_px": 340, "left_px": 80}, "gsap_in": {"delay": 0.3, "duration": 0.32} },
      { "id": "caption-fed-up", "type": "phrase_caption", "text": "FED UP", "position": {"bottom_px": 200, "center_x": true}, "gsap_in": {"delay": 0.9, "duration": 0.28} }
    ],
    "note": "FFmpeg composites these elements on top of full-frame talking head video."
  }
}
```

### full_greg_card scene
```json
{
  "layout_mode": "full_greg_card",
  "hyperframes_spec": {
    "full_canvas_bg": "#F5EFE6",
    "content_type": "phrase_collage | connector_diagram | comparison_board",
    "phrases": [...],
    "note": "Full canvas animated. No talking head. Audio from talking head master track."
  }
}
```

## Content Type Reference

Use the playbook's design system for all content:

| Type | Description | GSAP motion |
|------|-------------|-------------|
| `connector_diagram` | SVG: two or more nodes + connector line(s) + optional badges + dashed boundary. Nodes in coral (negative) or forest (positive). | `stroke-dashoffset` draw for connectors, `opacity + back.out` pop for nodes and badges |
| `phrase_collage` | Stack of large Outfit 900 text phrases, building sequentially. Optional coral strikethroughs and mint underlines. | `opacity + translateY` rise per phrase, `scaleX` for strikethrough/underline draws |
| `receipt_card` | Dark editorial card (bg #111111, text #F5EFE6) that slides in at -2 to -3 deg rotation with `receipt-stack-in` motion. Title + subtitle + body. | `x + opacity + rotation` on entry |
| `hero_card` | Forest green header + white body card. Fades up. Product/company announcement style. | `opacity + translateY` fade-rise |
| `pill_stack_checklist` | Row of sage-colored rounded pills that pop in staggered, followed by checklist rows that slide in from left. | `scale + back.out` for pills, `x + opacity` for checklist rows |
| `phrase_reframe` | Phrase sequence showing contrast: NOT/INSTEAD pattern. Coral strikethrough on the discarded option, forest underline on the favored. | Same as phrase_collage |
| `comparison_board` | Two side-by-side colored cards (coral ✗ / forest ✓). | `opacity + translateY` staggered entry |
| `sticker_pill` | Small pill with text. Used as overlays on hero_talking_head scenes. | `x + opacity` slide-in from off-screen |
| `cta_badge` | Gold rounded pill CTA. | `xPercent + opacity + y` rise with `back.out(1.4)` |

## Caption Config

Include in scene_plan:

```json
"caption_config": {
  "style": "word_sync_greg",
  "font": "Outfit",
  "font_weight": 800,
  "font_size_px": 44,
  "color": "#111111",
  "bg": "rgba(245,239,230,0.92)",
  "border_radius": 12,
  "padding": "8px 20px",
  "placement_by_layout": {
    "split_screen_greg": "centered",
    "hero_talking_head": "lifted_lower_third",
    "full_greg_card": "lifted_lower_third"
  },
  "words_per_chunk": 3,
  "gsap_in": {"type": "fade", "duration": 0.15}
}
```

### Caption placement is LAYOUT-AWARE (do NOT pin everything to the bottom)

A single bottom-pinned caption (e.g. `bottom:72px` → y≈1848, ~94% of frame height) is
**too low**: Instagram/TikTok/Shorts overlay their own UI (caption text, action
buttons, progress bar) across the bottom ~15–20% of the frame, which **shadows/obscures
captions placed there**. Tested and confirmed on the `clicky` reel — the user rejected
bottom-pinned captions for exactly this reason. Place captions per layout mode instead:

| Layout mode | Caption placement | Why |
|-------------|-------------------|-----|
| `split_screen_greg` | **Centered** — vertical center of the frame, sitting in the LOWER strip of the top animation panel just ABOVE the divider (pill top ≈ y955, i.e. center ≈ y990, clear of the divider at y1056). | Reads as the frame's center, never covers the face (bottom panel) and never the animation's hero content (which lives higher in the panel). |
| `hero_talking_head` | **Lifted lower-third** — pill top ≈ y1405 (center ≈ y1440, ~73% height). NOT the very bottom. | Lower-third over the speaker's chest, lifted clear of the platform UI shadow zone (~y1630+). |
| `full_greg_card` | **Lifted lower-third** — same y as hero (≈ y1405). | Below the centered phrase collage, still clear of the UI shadow zone. |

Author this by giving each caption chunk a per-scene CSS class (e.g. `.cap-mid { top:955px }`,
`.cap-up { top:1405px }`) chosen by which scene's time window the chunk falls in — NOT one
fixed `#caption-track` bottom offset. Keep the chunk's own transform as `translateX(-50%)`
only (GSAP animates opacity, never the transform — no conflict).

**Collision check (hero CTA scenes):** when a `hero_talking_head` payoff carries both a CTA
badge stack AND lifted captions, verify they don't overlap. If they do, move the CTA
badge/sub-tag UP so the stack reads cleanly top→bottom (e.g. `@handle → CTA badge →
sub-tag → caption`). On `clicky` the CTA badge moved to y1120 and the sub-tag to y1268 so
the y1405 caption cleared them.

## Narration Sync — HARD RULE (the speaker dictates the animation, never the reverse)

The single most important quality rule for this format: **every animated reveal must land ON the word the speaker says — never before it.** If a diagram finishes drawing, a pill pops, or a GIF appears *before* the speaker reaches that point in the narration, the illusion that the speaker is driving the visuals breaks and the reel feels pre-canned and "off." This was a real, user-reported defect (the `chrome-devtools` reel v1: the harness diagram fully drew out before the speaker described it; the "WRONG" GIF fired on "right?" instead of "wrong"; step cards ran ahead of "screenshot → analyze → click"). Do not repeat it.

**The source of truth for every `abs_time` is `transcript.json` `words[]` — not eyeballed estimates.**

1. **Anchor to the trigger word.** For each animated element, identify the exact word (or first word of the phrase) it illustrates, read that word's `start` from `words[]`, and set the reveal `abs_time` to that value (you may add `+0.0` to `+0.15s` for a natural beat — **never set it earlier**). Record the trigger word + timestamp next to each `gsap_entry` so it is auditable.

   | Element | Trigger word (example) | abs_time |
   |---|---|---|
   | "SEARCH" tool pill | the word "search" | `words[…].start` for "search" |
   | "BANKRUPT" stamp / cash GIF | the word "bankrupt" | `words[…].start` for "bankrupt" |
   | A scene **header** that merely names the beat | first word of the sentence | OK to lead slightly |

2. **Never pre-reveal a specific element.** A scene header/eyebrow may appear on the first word of the sentence to frame the beat. But *specific* items — steps, tool pills, data labels, stamps, the punchline word — appear on their OWN keyword, one per word, in spoken order.

3. **Build multi-part diagrams progressively across the narration window.** A connector map / step strip / "harness" diagram must spread its sub-element reveals across the whole clause that describes it, finishing as the speaker finishes — NOT all-at-once early. If the sub-elements aren't each named (e.g. the diagram's 4 labels while the speaker says one summary sentence), distribute the reveals evenly across that sentence's word span so it keeps building *while* they talk. The canvas should still be assembling when the speaker is mid-sentence.

4. **Overlay / reaction-GIF windows = trigger word → scene cut.** A reaction GIF or sticker appears ON its trigger word and exits at the scene cut (or when the speaker starts the next idea). It must not appear before the word and must not linger into the next sentence. A short window (even ~0.5–0.7s) is fine for a comedic stab — punch it in fast (0.1–0.2s ease) rather than starting early to pad it.

5. **Exit on time too.** Reveal-then-hold elements exit before the scene boundary (or when the next overlay needs the space), so the next beat starts clean.

When you hand the scene_plan to the asset/compose directors, every `gsap_entry` and every overlay/GIF window MUST carry its `trigger_word` and the `words[]` timestamp it was derived from. The compose director will verify this with before/after-word frame sampling (see compose-director "Narration Sync Verification") and treat any early reveal as a CRITICAL finding.

## Alternation Rule

Check before finalizing: no `hero_talking_head` run should exceed 8 seconds without a cutaway to `split_screen_greg` or `full_greg_card`. If a hero scene runs long (e.g., a 12s payoff that's purely talking head), consider splitting it: first 6s hero, then a full_greg comparison card, then last 6s hero.

## Self-evaluate

| Criterion | Check |
|-----------|-------|
| face_crop_y | Determined by frame sampling, face visible in test crop |
| All scenes | Have hyperframes_spec with enough detail to write HTML |
| All split-screen | Bottom panel marked as solid-background placeholder (no video element) |
| All hero | Overlays listed with positions and GSAP timing |
| All full-card | All text/diagram content explicit (no "show a diagram") |
| **Narration sync** | **Every reveal `abs_time` derived from `transcript.json words[]` and lands ON its trigger word, never before. Multi-part diagrams build progressively across the spoken clause. GIF/overlay windows = trigger word → cut. Each gsap_entry records its trigger_word + timestamp.** |
| Alternation | No hero run > 8s |
| Caption config | Present; placement is LAYOUT-AWARE (split=centered above divider, hero/full-card=lifted lower-third) — never bottom-pinned into the platform UI shadow zone |
| CTA collision | On hero CTA scenes, lifted captions don't overlap the CTA badge stack |
