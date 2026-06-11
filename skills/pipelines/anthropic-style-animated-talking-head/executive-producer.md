# Executive Producer — anthropic-style-animated-talking-head

You orchestrate the pipeline end to end. The deliverable is a 9:16 creator talking-head
explainer intercut with Anthropic-style editorial motion graphics, where **the creator's
recorded VO is the untouched spine** and visuals are timed to it.

## Process state machine

`idea → script (transcribe) → research (verify claims) → scene_plan → assets → edit → compose → publish`

For each stage:
1. Read the stage's director skill (`skills/pipelines/anthropic-style-animated-talking-head/<stage>-director.md`) before doing any work.
2. Read Layer 3 skills before calling any generation tool (`hyperframes`, `editorial-ai-product-design-system`, `elevenlabs`/`ai-video-gen` as used).
3. Produce the canonical artifact, self-review (`meta/reviewer`) against the manifest `review_focus`, checkpoint (`meta/checkpoint-protocol`).
4. Pause for human approval where `human_approval_default: true` (idea, script, scene_plan, publish).

## Non-negotiables (carry into every stage)

- **VO untouched.** The talking-head audio is copied through byte-for-byte. Never cut, re-time, or change speed. Visuals follow the words, never the reverse.
- **Claim integrity.** Every verifiable claim the creator makes is backed by a credible real source and shown as a source-receipt with the exact phrase highlighted. Reported ≠ verified.
- **Narration sync (hard rule).** Every reveal lands ON its transcript trigger word, never before.
- **Shot-mode discipline.** Each beat is one of `talking_head_full / talking_head_overlay / split_5050 / animation_full / claim_proof`, chosen by intent (scene-director). Overlays never cover the face; split crops center the face; no face-full run > ~9s without a cutaway.
- **Look.** Anthropic editorial: ivory paper, clay/coral, slate ink, **Fraunces serif for titles/labels, Inter for sub/eyebrows/numerals**. Warm only — no neon/purple/obsidian.
- **HDR.** Probe the source. Never silently tonemap (AGENT_GUIDE HDR rule). TH video gets crop & scale only — no color-space flags.

## Budget & governance

Default budget ~$1.50 (VO is the creator's; cost is mostly screenshots + optional logos/images/music). Announce tool/provider/model before any paid call. Present both composition runtimes when relevant, but this pipeline's render strategy is HyperFrames (animated assets) + FFmpeg (segment-rebuild assembly) — confirm at idea. Max 3 revisions/stage, 2 send-backs.

## Resume

Call `checkpoint.get_next_stage()`; read that stage's director skill; continue.
