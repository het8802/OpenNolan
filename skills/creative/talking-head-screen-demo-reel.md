# Talking Head Screen Demo Reel — Creative Skill

## When to Use
Use this for vertical social videos where the creator explains a workflow while actual product/browser footage runs behind or under them. Best for AI tools, SaaS walkthroughs, coding flows, job-search automations, founder ops, and Chrome-extension-like demos.

Reference pattern: Arshman Khalid / Clicko Reel `DY6UXkINLri`. Preserve the grammar: warm talking head + real screen proof + large PIP + tactile keyboard shot + short all-caps phrase captions + keyword CTA. Do not copy the creator's assets, face, room, or exact product.

## Core Principle
The viewer should feel: **I watched the creator use it live.** Talking head gives trust; screen recording gives proof; captions only punch the stressed words.

## Required Inputs
| Input | Required? | Notes |
|---|---:|---|
| Topic/workflow | Yes | One concrete job-to-be-done, not a generic feature tour. |
| Talking-head footage | Strongly preferred | Hook, bridge lines, CTA. If unavailable, use avatar/spokesperson only with explicit approval. |
| Screen proof | Yes | Browser/app/doc/profile/dashboard footage or screenshots showing the actual workflow. |
| Caption chunks | Yes | 1-4 word all-caps phrases aligned to stressed speech. |
| Style playbook | Preferred | Use `styles/talking-head-screen-demo-reel.yaml`. |
| Pipeline | Preferred | Use `pipeline_defs/talking-head-screen-demo-reel.yaml` or `screen-demo`/`hybrid` with this playbook. |

## Script Formula
1. `If you want [outcome], but you're tired of [specific friction], you need [tool/workflow].`
2. `It lets you [verb 1], [verb 2], [verb 3] without [annoying context switch].`
3. `Let's say I want to [specific job-to-be-done].`
4. `I have [real source/page/doc] open, but [starting friction].`
5. `So I [trigger/key/action].`
6. `[Tool] reads/analyzes [source] and gives [structured result].`
7. `When I don't understand / need to improve [term/section], I [highlight/select/ask] and it fixes it right there.`
8. `Now watch this: [exact prompt/command].`
9. `It generates [first output]; then I improve [specific weak part] by asking for [stronger angle/style].`
10. `The whole thing took [short time]. Comment “[KEYWORD]” and I'll send it.`

## Beat Template
| Time | Beat | Visual | Caption Rule |
|---|---|---|---|
| 0-5s | Pain + audience hook | Full-screen warm talking head, hands moving | 1 special hook phrase + 1-2 chunks |
| 5-10s | Tool promise | Product/site/extension page; creator PIP appears | Tool name + verbs |
| 10-17s | Use-case setup | Actual target page/document/profile/dashboard | Caption task/problem only |
| 17-24s | First action | Keyboard/mouse insert + command popup | Caption key/action |
| 24-32s | Proof result #1 | Tool reads/analyzes/generates structured output | Caption result category |
| 32-42s | Friction removed | Highlight confusing text; inline explanation/fix | Caption `HIGHLIGHT`, `RIGHT THERE`, etc. |
| 42-52s | Magic command | Prompt/listening box over real page | Keep command visible; caption command verb |
| 52-63s | Rewrite/improvement | Select weak part; generated replacement appears | Caption before/after action |
| 63-70s | Time saved + CTA | Return to full talking head or final proof | Big keyword CTA |

## OpenNolan Implementation
1. Select `talking-head-screen-demo-reel` pipeline for net-new creator + screen-proof production. If user already has raw talking-head footage, `hybrid` can also work; if only screen capture is needed, use `screen-demo` with this playbook.
2. At idea/proposal, require a proof map: every spoken claim must map to `page`, `click`, `highlight`, `prompt`, `output`, or `replacement`.
3. Present render runtime choice per `AGENT_GUIDE.md`: HyperFrames is best for HTML/CSS/GSAP PIP, captions, prompt boxes, and UI punch-ins; Remotion is best for deterministic React caption timing and reusable component renders. Do not silently pick.
4. At assets stage, collect/record: talking head hook/CTA, browser proof clips, keyboard insert, output screenshots, audio, captions, SFX choices.
5. At edit stage, build a tight cut list: remove loading/dead time; use screen actions as transitions; caption only stressed phrase chunks.
6. At compose stage, verify ffprobe, decode, blackdetect, volumedetect, and contact sheet. Mobile-readability is a hard gate.

## Caption System
- All-caps, 1-4 words, bold condensed sans.
- White fill with black stroke/drop shadow.
- Placement: lower third or over torso, never covering key UI.
- Not subtitles. If a caption can be a full sentence, it is probably wrong.
- Hook may have one larger stylized phrase with orange/red desire/pain emphasis.

## Motion Vocabulary
| Motion | Use For | Parameters |
|---|---|---|
| `proof-cut` | Moving from claim to UI evidence | hard cut or 2-4 frame snap |
| `ui-punch-in` | Output/popup/key panel emphasis | 1.03-1.08 scale/crop, 0.2-0.35s |
| `cursor-follow-reframe` | Following clicks/highlights | crop tracks cursor/selection, no over-smoothing |
| `pip-anchor` | Creator over screen proof | stable lower PIP, no ornate frame |
| `caption-hit` | Stressed phrase | opacity+scale pop over 3-5 frames |
| `keyboard-trigger-cut` | Command/key moment | 0.5-1.5s tactile insert, cut back to result |
| `result-replace` | Before/after rewrite | selected text -> prompt -> replacement, readable hold |

## Asset Rules
- Real UI proof is mandatory. Stock/generated visuals are allowed only for tiny context inserts or unavailable backgrounds.
- Browser zoom 125-150%; dark mode preferred for contrast.
- Use demo data; hide private info.
- PIP must not cover important buttons, prompt boxes, or generated result lines.
- Primary readable text should be OpenNolan overlay text, not embedded in screenshots.

## QA Checklist
- [ ] Audience + pain/outcome in first 5s.
- [ ] Real browser/product footage appears by 8-10s.
- [ ] Creator is present as full-screen or large PIP often enough to preserve trust.
- [ ] Every claim has visible proof.
- [ ] Captions are phrase hits, not full subtitles.
- [ ] UI output is readable at phone size.
- [ ] Visual change every 0.5-2s.
- [ ] CTA keyword is clear and safe-zone aware.
- [ ] No copyrighted/reference frames or copied creator-specific assets.

## Common Pitfalls
- Feature tour instead of one concrete workflow.
- Fake B-roll where proof should be.
- PIP too small to read emotion.
- Overdesigned transitions that distract from cursor/product actions.
- Cutting away before generated output is readable.
- Captions becoming normal subtitles.
- Hooking with the tool name before the viewer pain.
