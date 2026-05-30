# Greg-style reusable asset kit notes

Use when building reusable Remotion/OpenMontage assets for Het's AI/startup videos, based on public Greg Isenberg / Roberto Nickson visual patterns.

## Target directory

```text
greg-style-kit/
  fonts/Fraunces/
  fonts/Inter/
  palettes/greg-editorial.json
  icons/{robot-agent,document,checklist,map-pin,browser,dollar,cursor}.svg
  shapes/{dashed-container,pill-label,rounded-node,progress-bar}.svg
  backgrounds/{warm-paper,subtle-noise,mint-gradient}.png
  sfx/{soft-pop,whoosh,click,riser}.wav
  templates/*.tsx
  previews/asset-preview.png
  README.md
  asset-ledger.json
```

## Production rules

- Recreate the **design schema**, not Greg's original copyrighted thumbnails/assets.
- Use warm paper backgrounds, forest-green outlines, mint/sage AI accents, coral only for failure/reject, gold for money/value, and sage/warm-gray for product UI borders/shadows. Avoid purple/violet/lavender/mauve accents for Het's Greg-theme output.
- Prefer original deterministic SVG/Pillow assets for icons, shapes, background textures, and previews; use native image generation only for non-deterministic hero illustrations when explicitly requested.
- Fonts: Fraunces for editorial hook/type; Inter for labels, UI, nodes. Google Fonts files may download as TrueType URLs even if saved generically; verify magic bytes and use `.ttf` extension when appropriate.
- Generate an `asset-ledger.json` and a visual contact sheet/preview image before delivery.
- QA with vision. Common issues caught: pill-label text clipping, too-small progress-bar labels, background textures too faint to see in previews.

## SFX fallback

If ElevenLabs SFX credentials (`ELEVENLABS_API_KEY` or `XI_API_KEY`) are missing, do not block the entire asset kit. Generate simple local placeholder WAVs with Python/wave for `soft-pop`, `whoosh`, `click`, and `riser`, but clearly report that ElevenLabs was not used and placeholders should be replaced once credentials are provided.

## Verification

```bash
find /path/to/greg-style-kit -type f | sort
unzip -t /path/to/greg-style-kit.zip
for f in /path/to/greg-style-kit/sfx/*.wav; do ffprobe -v error -show_entries format=duration,size -of default=nw=1 "$f"; done
```

Deliver both the zip and the preview image to Slack so Het can inspect the vibe quickly.