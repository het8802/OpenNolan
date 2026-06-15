# Humor inserts and talking-head background removal evaluation

Use this note when the user asks to add humor/memes/GIFs to Reels from a script, or to compare background-removal options for talking-head footage.

## Class workflow

1. Start from the script, not from generic search terms.
2. Extract 5-8 humor beats as short semantic prompts: e.g. "generic chatbot rejected", "invoice-chasing intern", "approval gate panic", "SaaS inside ChatGPT bloat", "messy workflow integration", "narrow workflow wins".
3. For each retrieval source, save the top candidates and build a contact sheet. Do not judge meme/GIF search from URLs alone; visual comparison matters.
4. For talking-head background removal, run each tool on the same short representative clip first (3-10s), normalize outputs to the same background (green or transparent), then build a comparison video/contact sheet.
5. Deliver: selected outputs, a contact sheet, a short verdict table, and keep/remove recommendation.

## Meme/GIF retrieval lessons

- Public semantic meme indexes can prove the concept, but are usually not production-ready for your AI/dev/founder/operator Reels: many hits are too text-heavy, obscure, or not funny at Reel speed.
- Better durable path: curate a local folder of AI/dev/founder/startup/operator humor memes and GIFs, then index that corpus with a local semantic meme-search tool (for example, `neonwatty/meme-search`). Query it from the script-derived humor beats.
- Keep Tenor/GIPHY as fallback sources after API keys are configured, especially for recognizable reaction GIFs; track source URLs/license/usage in the asset ledger.
- Useful output shape: `beat -> query -> top candidates -> thumbnail/contact sheet -> selected asset -> suggested timestamp/overlay note`.

## Background-removal lessons for talking-head clips

- Robust Video Matting (RVM) is a strong default for normal talking-head clips because it tends to preserve a stable human matte and remove room/background objects better than generic image segmentation.
- `rembg`/video-background-remover-style pipelines are useful fallback CLIs for green-screen style output, but inspect hair/hand edges before production use.
- BackgroundMattingV2-style workflows require a clean empty-background plate from the same camera angle. If the user wants the best possible result in a fixed room, ask them to record 2-3 seconds of empty background before/after the take.
- MODNet-style portrait mattes may keep non-human objects as foreground on some clips; always compare visually before choosing.

## QA commands/patterns

- Clip a short representative sample before benchmarking full footage.
- Normalize all candidates to a common background for visual comparison:
  - green background for quick edge inspection;
  - transparent output only when the downstream editor needs alpha.
- Make a comparison video plus contact sheet before recommending a default.
- Record runtime roughly, but rank by visual matte quality first for Reels.

## Recommendation heuristic

- Default: RVM for talking-head background removal.
- Fallback: rembg/video-background-remover for simple CLI green-screen output.
- High-quality fixed-camera workflow: collect an empty-background plate and retest BackgroundMattingV2-style methods.
- Meme workflow: curated local corpus + semantic index first; web GIF APIs second; random public meme indexes only as discovery/proof-of-concept.