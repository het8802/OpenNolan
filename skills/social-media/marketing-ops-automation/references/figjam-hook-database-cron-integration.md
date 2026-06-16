# FigJam Hook Database Cron Integration

Session learning: the daily Instagram AI/Tech Script Engine asset pack should include the relevant hook from Kallaway's FigJam board, not just assets/B-roll.

## Requirement

For daily short-form concept/script and asset-pack jobs, include a concept-specific **FigJam hook combo**:

- **Visual hook brick:** choose from the `content-os-tools` Short Form Lego Bricks taxonomy: subject motion, graphic/text overlay, visual selection, pattern interrupt/visual switching, or effects/transitions.
- **Spoken hook brick:** choose from educational/storytelling formats such as secret reveal, case study, problem, contrarian, warning, list, hypothetical, comparison, question, ranking/rating, authority/proof, or personal experience.
- **Execution detail:** exact first 2–5 second shot plan, exact first spoken line, matching SFX/audio cue, props/assets needed, and why it fits the concept.

## Cron prompt pattern

Script job output should include a section like:

```md
## Recommended FigJam hook combo
- Visual hook brick:
- Spoken hook brick:
- Exact first 2–5 seconds:
- First spoken line:
- Matching audio/SFX:
- Props/assets/overlays needed:
- Why this fits today's concept:
```

The script's `Asset/B-roll brief for follow-up cron` should start with `Recommended FigJam hook combo for asset cron` so the downstream asset job can reuse it.

Asset-pack jobs should create `hook-recommendation.md` in the package directory with:

- primary FigJam hook combo
- 2 backup hook combos
- exact first spoken line and shot plan
- SFX/audio cue
- props/assets needed
- editor notes

## Observed live jobs updated

On 2026-05-18, the following jobs were patched:

- `15fea8cb2d24` — `Instagram AI/Tech Script Engine — daily concept + talking script`
- `14edf0fbfbcd` — `Instagram AI/Tech Script Engine — daily asset + B-roll pack`

Verify future edits by checking `~/cron/jobs.json` for `Recommended FigJam hook combo`, `FigJam hook requirement`, and `hook-recommendation.md`.
