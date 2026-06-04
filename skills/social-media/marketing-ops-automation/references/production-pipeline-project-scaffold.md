# Production-grade AI video pipeline project scaffold

Session learning: when the user proposes an AI-news/Instagram video system, treat it as a separate class of project: a typed production pipeline, not a single better render/video tool.

## Created scaffold

Initial project path:

```bash
/home/ubuntu/ai-video-pipeline
```

Verification run:

```bash
cd /home/ubuntu/ai-video-pipeline
npm run typecheck
npm test
npm run tool:list
```

Observed successful state:

- TypeScript typecheck passed.
- Vitest passed: 1 test file, 2 tests.
- Tool registry listed 25 tools.

## Architecture pattern to reuse

Core rule: never generate a final video directly from a topic.

Required artifact chain:

```text
Topic / query
  -> ResearchCard
  -> Script
  -> FactCheck
  -> Storyboard
  -> AssetLedger
  -> Voiceover + word timings
  -> Timeline JSON / edit decision list
  -> Render
  -> QAReport
  -> ExportPackage
  -> Optional human-approved publishing
```

This is especially important for Harness/Hermes-style agents because tools should have explicit typed inputs and outputs; do not rely on vague browser access or ambient permissions.

## Files created in scaffold

Docs:

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/IMPLEMENTATION_PLAN.md`

Schemas:

- `schemas/research-card.schema.json`
- `schemas/storyboard.schema.json`
- `schemas/asset.schema.json`
- `schemas/timeline.schema.json`
- `schemas/qa-report.schema.json`

Tool registry:

- `src/tools/registry.ts`
- `src/tools/list.ts`

Node/test setup:

- `package.json`
- `tsconfig.json`
- `vitest.config.ts`
- `tests/tool-registry.test.ts`

## Tool surface captured

Research:

- `search_news`
- `scrape_source`
- `extract_structured_facts`
- `lookup_company`
- `scan_social`
- `score_story`
- `create_research_card`

Planning:

- `generate_angles`
- `write_script`
- `fact_check_script`
- `create_storyboard`

Assets:

- `search_stock_video`
- `generate_image`
- `generate_broll_video`
- `capture_source_screenshot`
- `store_asset`

Audio:

- `generate_voiceover_with_timestamps`
- `force_align_audio_to_script`
- `generate_sfx`
- `mix_audio`

Editing/rendering/QA/export:

- `create_timeline`
- `render_video`
- `render_thumbnail`
- `qa_video`
- `export_package`

## Implementation guidance

Build the developer-native path first:

1. TypeScript + Zod/JSON Schema contracts.
2. JSONL/local persistence before Postgres.
3. Research card + fact checking before scripts become renderable.
4. Asset ledger before any render.
5. Timeline JSON as deterministic edit-decision list.
6. Remotion + FFmpeg renderer.
7. QA gate that blocks unsupported claims, unlicensed assets, bad aspect ratio, bad decode, poor caption timing, missing thumbnail, and unsafe YouTube-sourced assets.

Only integrate paid/credentialed services after exact pricing/ROI/user approval:

- Tavily/Exa/OpenAI web search
- Firecrawl
- Crunchbase/PitchBook/Similarweb/Semrush
- ElevenLabs TTS/forced alignment
- Runway/Luma
- Plainly/After Effects
- Shotstack/Creatomate/JSON2Video
- Meta Instagram publishing

## Pitfalls

- Do not treat Remotion as a full production editor by itself. It needs branded templates, a motion system, and timeline schema.
- Do not use downloaded YouTube clips as default b-roll. Use YouTube for discovery/references/transcripts unless ownership/license/permission is explicit.
- Do not guess caption timing. Require TTS timestamps or forced alignment.
- Do not render until every scene has timing, caption text, an asset, and claim/source references where applicable.
- Do not auto-post; publishing stays approval-gated.
