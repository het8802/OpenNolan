# Idea Director — instagram-fast-reel

Intake the talking-head footage and lock the brief. This pipeline is **fast-cut-first**: the
talking head is the spine and it WILL be cut (jump cuts + silence removal), so audio is cut with
the video. Keep this stage light — the real work is `edit`.

## Do this

1. **Find the source talking head.** The user drops one or more clips. Locate them (project
   `assets/video/` or the path the user gave). If the user gave a reference reel ("make it like
   this") instead of source footage, that's the reference-input path — analyze it with
   `video_analyzer` and mirror pacing/energy, but the deliverable is still built from the user's
   OWN talking head.

2. **Probe every source clip.** Record resolution, fps, duration, and audio presence. Run the
   HDR check `is_hdr_source(path)` (`tools/video/_shared.py`) and log `hdr` + `kind`. **Never
   silently tonemap HDR** (AGENT_GUIDE HDR rule). If HDR + the tools needed are 8-bit SDR only,
   surface the tradeoff and get a decision; log it in `decision_log`.

3. **Lock the creative brief:**
   - topic / angle and the **hook** (the first ~2s line that stops the scroll),
   - target platform (Reels / Shorts / TikTok), target duration (typically 15–45s),
   - tone, and **pacing energy** — how aggressive the cutting is (chill / punchy / hyper). This
     drives how hard `silence_cutter` trims and how many animation/GIF hits land.
   - **caption style**: word-by-word karaoke, punch-word hits, or clean lower-third.

4. **Pick the render_runtime — present BOTH (HARD RULE).** Check
   `video_compose.get_info()["render_engines"]`.
   - **ffmpeg (default, recommended for this pipeline):** fast cuts, keyframed overlays,
     GIF overlays, drawtext + animated caption cards, per-cut xfade. Lean and previewable.
     Tradeoff: caption motion is limited to keyframe transforms (no per-glyph physics).
   - **hyperframes (optional):** premium animated title/caption cards (kinetic typography) as
     alpha overlays composited over the cut talking head. Tradeoff: heavier — each card is an
     authored HTML comp that must be rendered before assembly.
   Recommend ffmpeg for a first fast reel; offer hyperframes for a hero title card. Record BOTH
   in `render_runtime_selection` under `decision_log` (the unused one as `rejected_because`).

5. **Music plan (mandatory).** Check `music_library/`, then `music_generation` tools. Present the
   options (library track / provide your own / generate / none) and record the choice in the brief.

6. **Design theme is FIXED — state it, don't re-pick.** This pipeline has a locked look (see the
   manifest `metadata.design_theme`). Record it in the brief so every downstream stage inherits it:
   - **Overall surface / typography / captions / motion-graphics = Anthropic editorial**
     (playbook `anthropic-editorial-animated`): warm ivory paper (#F0EDE6), slate ink (#1A1A18),
     clay/coral (#CC785C) as brand + emphasis, forest-green=approved / amber=gated accents;
     **Fraunces** (900) for titles/labels/phrases, **Inter** (600) for eyebrows/sub-labels/pills/
     numerals. No neon/purple/obsidian/cyberpunk.
   - **Animated motion-graphic beats = Greg Isenberg motion grammar** (playbook
     `greg-isenberg-product-explainer`): receipt-stack-in, phrase-collage-build, connector-draw,
     prompt→artifact loops, rapid pop+settle, subtle camera push-ins.
   Set the brief's playbook to `anthropic-editorial-animated` and note the Greg motion grammar for
   the animation beats. `custom_allowed` is false — don't invent a different look.

## Output
- `brief` (schema-valid): source clip(s), hook, platform, duration, tone, pacing energy, caption
  style, render_runtime, music plan.
- `decision_log`: HDR handling, render_runtime_selection (both runtimes), music decision.

## Quality bar
Brief names the real source clip(s) with probed specs; HDR logged; render_runtime chosen with
both options shown; caption style + pacing energy set. Human approval before `script`.
