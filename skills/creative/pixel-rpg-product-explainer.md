# Pixel RPG Product Explainer — Creative Skill

## When to Use

Use this skill for vertical short-form AI/tool tutorials that should feel like a playable mini-world: pixel-art avatar, quest-map metaphors, floating product UI, kinetic serif captions, and creator-native narration. Best for Reels/TikToks/Shorts that teach a workflow while making it feel like leveling up through a game.

Reference pattern: Instagram Reel `DY5SPASumP8` — a workflow explainer using a pixel character, quest-object motifs, floating browser/app cards, top-down map logic, prompt/result loops, artifact transformations, and phrase-synced editorial text. Treat Obsidian/Claude, purple crystals, and violet portal motifs as reference-specific details, not defaults.

## Prerequisites

| Resource | Required? | Notes |
|---|---:|---|
| Voiceover script | Yes | Write as short clauses. Each clause should trigger one motion event. |
| Product/workflow proof | Yes | Screenshots, UI mockups, prompt bars, checklist cards, search/result cards, notes/posts. |
| Visual metaphor | Yes | Define the “quest object” and “guide/sidekick” before scene planning. |
| Word/phrase timings | Strongly preferred | Use generated VO + faster-whisper/WhisperX for phrase sync. |
| Style playbook | Preferred | Use `styles/pixel-rpg-product-explainer.yaml`. |
| Runtime | Preferred | HyperFrames for GSAP/HTML kinetic layouts; Remotion for deterministic React scenes. Present both if available. |


## Asset Sourcing Protocol — Required for Production

Do not build this format as only a pixel map plus text. The reference works because it mixes the playful world with real proof, UI receipts, and tangible outputs.

1. **Extract screenshots from relevant links**
   - For every cited article, product page, report, repo, or social proof link, capture a screenshot/crop and convert it into a layered proof card.
   - Screenshots are proof texture; overlay the key readable phrase in OpenNolan text.
   - Store under `projects/<project>/assets/images/source-receipts/` and record the source URL in the manifest.

2. **Source or extract relevant footage**
   - Use commercial-safe stock/project-corpus/public-domain footage for real-world beats: layoffs/news context, developer workflow, typing/coding, dashboards, social media, interviews, offices, data centers.
   - YouTube footage may be analyzed for reference, but only use it directly when licensing/permission is clear. Otherwise recreate with stock, screenshots, or generated visuals.
   - Trim to 1–3s and place as windows/cards within the map or UI scenes rather than letting footage overpower the style.

3. **Generate supporting images**
   - Generate a hero visual system image plus scene-specific supporting images: quest map background, tool shrine/object, workflow board, agent sidekick, UI card backdrops, checklist/profile/post-grid artifacts.
   - Use the active playbook prompt prefix and negative prompt; keep final text out of generated images and add it in OpenNolan.

4. **Asset mix target**
   - For a 45–60s reel, aim for at least 2–4 screenshot receipts, 2–4 b-roll/extracted footage inserts, 3–6 generated/stylized images, and reusable UI/card overlays.
   - Intentional text-only beats are allowed only for moral resets or chapter emphasis.

## Core Principle

Make the viewer feel the workflow is a game loop: **threat → sidekick/tool → 3 levels → quest map → prompt/action → generated artifact → public proof/payoff**. Motion should clarify where the viewer is in the quest, not decorate random frames.

## Reference Breakdown to Preserve

| Time | What happens | Reusable edit lesson |
|---|---|---|
| 0–3s | Pixel avatar runs toward an old computer and quest object on a beige top-down tile grid; crack/ripple transition opens; labels like layoffs/automation/budget cuts radiate out. | Start with a visual threat/world-state, not a static title. Use game-map staging and object collision to open the loop. |
| 3–7s | News/browser windows stack and slide over the portal: Business Insider/KPMG, industry layoffs, layoff tracker. | Proof cards enter as layered desktop windows with slight offsets, shadows, and overlapping depth. |
| 7–10s | Avatar, quest object, and tool/agent sidekick sit on the map, then the canvas resets. | Bring the metaphor characters back after proof so the tutorial still feels like a world. |
| 10–16s | Kinetic serif text: “There are 3 levels…” then app/tool A → connector node → agent/tool B, plus a cropped product proof screenshot or stylized UI. | Use captions as editorial chapter cards; important nouns get colored/italic/larger treatment. |
| 17–21s | A large black circular “Tech” map appears with mint pill tags: AI Automation, Vibe Coding, AI Agents, Vertical AI, DevSecOps, etc. | Use a radial industry map for “trends taking over your niche.” Tags orbit/slide onto a dark focal object. |
| 21–25s | Claude prompt card and black chat/prompt bar appear with green glow. Text says Claude teaches you all about it. | Prompt bars are hero UI objects: black rounded pill, green rim glow, tiny app icons, cursor/text typing. |
| 25–37s | Smart Connections brain graphic, app/search UI, multiple dark screenshots, then prompt: “list of skills I am lacking…” and AI response. | Translate abstract “context” into a plugin brain + search-grid + prompt/result card sequence. |
| 37–42s | AI output becomes a 7-Day Deep Dive checklist with check circles and stacked task rows; caption says saved as checklist to track it. | Convert AI response into a tangible artifact. Show transformation: prompt → response → checklist. |
| 42–47s | Text-only reset: “learning quietly won’t get you hired”; social proof screenshots/comments appear; “Sharing your learning will.” | Use a white negative-space moral beat before the final action. It resets attention and raises stakes. |
| 47–57s | Prompt bar: analyze last 10 notes and build a 30-day learning-in-public plan. Output card + content cards/posts appear. | Final level should generate publishable assets, not more abstract advice. |
| 57–61s | Dark profile/grid/product board lands with caption: “Based on what you’ve actually learned… that spares you the meltdown.” | End on proof-of-work dashboard/profile, not a generic CTA. The payoff is identity/career safety. |

## Structure Pattern

1. **World-state hook / threat portal**
   - Start on a top-down tile map or minimal RPG room.
   - Avatar moves toward a tool/object.
   - Threat labels crash into the scene: `Layoffs`, `Automation`, `Budget Cuts`, `Competition`.
   - Use a cracked/rippled portal, shockwave, or expanding gold/mint glow as the transition trigger. Avoid purple/violet unless the user explicitly asks for that reference-world.

2. **Proof-window storm**
   - Stack 2–4 browser/news/report cards.
   - Animate them as old desktop windows: slide from different edges, overlap, slight rotations, soft shadow.
   - Highlight one phrase per card with yellow/black marker treatment.

3. **Level system announcement**
   - Reset to off-white canvas.
   - Serif text builds in 2–4 phrase chunks: `There are` → `3 levels` → `to this`.
   - Use color for the number or keyword; keep small setup words black/italic.

4. **Tool connection diagram**
   - Show tool A at top, tool B at bottom, dotted vertical connector, MCP/computer node in the middle.
   - Cards/icons should float with parallax and soft shadows.
   - Caption the connector with minimal text, not a full sentence.

5. **Industry/trend map**
   - Dark circular board or globe with one central topic word.
   - Mint pills populate around it, each arriving with small scale/slide.
   - Camera digitally pushes/tilts across the board to imply exploration.

6. **Prompt/result loop**
   - Black rounded prompt bar enters from side or bottom.
   - Type 1–2 lines of the prompt with a green glow on active area.
   - Result card appears above; downstream artifact appears next.

7. **Artifact transformation**
   - Response becomes checklist, plan, post grid, profile board, or content calendar.
   - Animate rows in staggered order; use check circles/fill bars to show utility.

8. **Moral reset + public proof payoff**
   - Use mostly empty white canvas with one strong caption: `learning quietly won’t get you hired`.
   - Then bring in comments/posts/profile/cards as proof that the workflow creates public credibility.

## Motion Vocabulary

| Motion | Use For | Parameters |
|---|---|---|
| `rpg-walk` | Pixel avatar crossing map | 8-bit step cycle, 2–4 px bob, linear path, tiny dust or shadow. |
| `portal-crack` | Threat/hook transition | radial cracks/ripple 0→100%, gold or mint glow scale 0.7→1.2, labels shoot outward. |
| `desktop-window-stack` | News/proof cards | x/y slide 80–180px, opacity 0→1, slight rotate -2°→0, shadow grows. |
| `phrase-collage-build` | Editorial captions | words appear in staggered groups, not full subtitles; y 14–22px→0, blur 3px→0. |
| `keyword-color-hit` | Important nouns | mint/forest/gold/coral color swap + 1.03–1.06 scale; no bouncy yoyo or purple hits. |
| `dotted-connector-draw` | Tool-to-tool flow | stroke dashoffset draw-on over 0.3–0.6s; node pops after line reaches it. |
| `radial-tag-populate` | Trend map | pill tags slide from center/outside; stagger 0.08–0.14s; slight orbit/float after settle. |
| `prompt-bar-type` | User action/prompting | prompt pill slides in; typewriter text; green rim glow intensifies on active typing. |
| `response-to-artifact` | AI output becoming checklist/plan | result card shrinks/moves; checklist rows wipe in one-by-one. |
| `proof-grid-land` | Final payoff/profile | cards scale 0.94→1, y 40→0, shadow settles, final camera push-in 3–5%. |

### Easing and Timing

- Keep normal entrances premium: `power3.out` / cubic ease-out, 0.28–0.45s.
- Reserve hard snaps for threat labels, prompt submit, and chapter resets.
- Avoid meme-style yoyo bounces. If scaling, peak at only 1.03–1.06 and settle in 0.18–0.25s.
- Every 0.5–1.5s must change one meaningful thing: word group, UI card, connector, tag, prompt text, checklist row, or camera crop.
- Use short transitions: 4–8 frames for cuts/blur wipes; 8–14 frames for object-driven slides.

## Camera and Composition

- Canvas: 1080×1920 vertical.
- Default scene camera is a virtual top-down/orthographic editorial camera, not handheld footage.
- Use three camera states:
  1. **Map wide:** avatar/tool/quest-object/world visible; slight push-in.
  2. **UI close:** prompt/result cards fill 60–80% width; edges crop slightly for scale.
  3. **Artifact hero:** checklist/profile/post grid centered with caption below.
- Preserve Instagram safe zones: avoid final keywords under bottom UI and avoid tiny text near top controls.
- Use depth through 2.5D layers: background grid → shadows → icons/cards → captions → foreground UI.

## Typography System

- Use a chunky editorial serif for narration captions: bold black, tight line height, mixed size hierarchy.
- Important verbs/nouns may be italic, mint, forest, gold, or coral. Avoid purple/violet keyword hits for your Greg-aligned vibe.
- Do not use one-line subtitles across the bottom. Build a phrase collage around the visual object.
- Keep setup words small; make payoff words 1.4–2.2× larger.
- Text should feel hand-composed: slight baseline offsets are allowed, but never unreadable.

## Visual Design Rules

- Palette: warm off-white/tile beige background, charcoal/black UI, mint/forest highlights, gold quest-object accents, coral/orange sidekick accents, yellow marker highlights for proof cards. Avoid purple/violet/obsidian fantasy accents unless explicitly requested.
- Reusable motifs: pixel avatar, old computer/connector node, gold/mint quest object, coral/green agent sidekick, dotted connectors, dark rounded prompt bar, mint pill tags.
- Use real product screenshots/mockups only as proof layers; recreate or anonymize where needed. Do not copy protected reference assets directly.
- Proof cards should be readable enough to identify their purpose, but primary message comes from OpenNolan text overlays.
- Mix light canvas and dark UI/cards to create attention resets.

## Audio and SFX

- Narration: fast, conversational, slightly urgent; no long pauses except before the moral reset.
- Music: soft electronic pulse, low volume, light “quest” or productivity energy.
- SFX: tiny footstep/8-bit blips for avatar, quest-object shimmer, soft whoosh for window stacks, click/type sounds for prompt bar, pop for checklist rows.
- Do not put SFX on every caption word; hit section transitions and artifact transformations.

## Runtime Guidance

### Prefer HyperFrames when

- The brief depends on GSAP-like kinetic text, prompt bars, floating cards, CSS glows, or web-style UI staging.
- You need easy HTML/CSS layout for desktop windows, prompt bars, and 2.5D cards.

### Prefer Remotion when

- You want deterministic React components for pixel avatar movement, checklist rows, prompt typing, and frame-perfect caption timing.
- The video is part of an existing Remotion/OpenNolan social pipeline.

If both are available, follow `AGENT_GUIDE.md`: present both runtime options before locking the render path.

## Scene Plan Snippet

```json
{
  "scene_id": "level_map_to_prompt",
  "duration": 6.0,
  "background": "warm-offwhite-grid",
  "support_visuals": [
    {"type": "pixel-avatar", "motion": "rpg-walk", "path": [[110, 980], [420, 900]]},
    {"type": "gold-mint-quest-object", "motion": "float-shimmer"},
    {"type": "dark-radial-map", "center_label": "Tech", "tags": ["AI Automation", "AI Agents", "Vibe Coding", "Vertical AI"], "motion": "radial-tag-populate"},
    {"type": "prompt-bar", "text": "List skills I am lacking based on the 5 trends in my niche", "motion": "prompt-bar-type"}
  ],
  "text_beats": [
    {"text": "pick a trend", "start": 0.0, "motion": "phrase-collage-build"},
    {"text": "taking over your industry", "start": 1.2, "motion": "keyword-color-hit", "color": "mint"},
    {"text": "and prompt this", "start": 4.2, "motion": "phrase-collage-build"}
  ]
}
```

## Quality Rubric

| Check | 1 | 3 | 5 |
|---|---|---|---|
| Game metaphor | Decorative pixel art only | Some quest logic | Every tool/action maps to a clear level, object, or artifact transformation |
| Proof clarity | Random screenshots | Relevant but small | Each proof card is legible enough and immediately tied to narration |
| Asset richness | Mostly map/text | Some proof or generated visuals | Balanced mix of screenshot receipts, b-roll/footage cards, generated quest/world images, UI mockups, and workflow artifacts |
| Caption motion | Generic subtitles | Phrase chunks with some emphasis | Kinetic editorial collage with active keywords landing on spoken stress |
| Motion taste | Bouncy/janky | Consistent but plain | Premium soft-settle motion with meaningful changes every 0.5–1.5s |
| Workflow legibility | Viewer sees style but not steps | Steps are mostly clear | Viewer can reproduce the workflow from the visual sequence |
| Mobile safety | Important text under UI | Mostly safe | All keywords, prompts, and CTA/payoff readable under Reels UI |

## Common Pitfalls

- **Copying the reference exactly:** preserve the grammar, not the specific creator’s assets or arrangement.
- **Making it too game-like:** the RPG layer is a metaphor; the tutorial still needs product proof, source screenshots, b-roll/footage cards, generated visuals, and workflow artifacts.
- **Tiny UI text:** use screenshots as proof texture, then overlay readable captions for the actual message.
- **Skipping relevant assets:** extract link screenshots, source commercial-safe footage, and generate supporting images before composing; text-only/map-only outputs feel unfinished.
- **Unsafe YouTube reuse:** use YouTube for reference unless the footage is owned/licensed/permissioned; recreate unclear footage with stock or generated visuals.
- **Random kinetic words:** every text hit should align to VO stress and advance the workflow.
- **Overusing bounces/glitches:** this style is playful but still premium; prefer soft ease-out and restrained scale.
- **Ending on advice:** end on a tangible proof board, checklist, profile, or publishable artifact.

## QA Checklist

- Render a 1fps contact sheet and verify the viewer sees: threat, tool connection, levels, prompt, result artifact, public proof/payoff.
- Verify the asset manifest includes screenshot receipts, b-roll/footage inserts where relevant, generated images/UI mockups, and provenance/license notes.
- Watch at phone size; prompt text and payoff captions must remain readable.
- Check that every dark card has enough contrast against the off-white canvas.
- Confirm no copyrighted/reference video frames or creator-specific assets are embedded in final output.
- Verify audio hits: prompt submit, checklist rows, and final proof board should have subtle SFX support.
