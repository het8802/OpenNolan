# Expanded Greg-style AI/reel asset kit runbook

Use when Het asks to generate reusable assets/icons/backgrounds for informational AI/startup Reels.

## Proven output shape

A useful expanded kit should include:

```text
greg-style-kit-expanded/
  ai-generated/
    backgrounds/*.png        # 1080x1920 text-safe canvases
    hero-scenes/*.png        # agent/funding/proof/security scenes
    ui-cards/*.png           # browser receipts, dashboards, tables, checklists
    icons/*.png              # imagegen sheets for inspiration/cropping
    stickers/*.png
    props/*.png
    patterns/*.png
  icons-v2/*.svg             # deterministic overlay icons for Remotion
  shapes-v2/*.svg            # browser shells, stamps, safe zones, connectors
  templates-v2/*.tsx         # reusable Remotion primitives
  previews/ai-generated-contact-sheet.jpg
  asset-ledger-expanded.json
  README-expanded.md
```

Session baseline: `/home/ubuntu/greg-style-kit-expanded` produced 28 Codex-native `$imagegen` PNGs, 30 deterministic SVG icons, 7 SVG shapes, 2 TSX templates, 129 files total, zipped to `/home/ubuntu/greg-style-kit-expanded.zip`.

## Image-generation prompt rules

For Codex CLI native image generation, be explicit:

- "Use the native Codex image generation capability, explicitly with `$imagegen`."
- "Do NOT write Python, SVG, HTML, CSS, Canvas, or any code to create these images."
- Ask for exact file paths under the current directory.
- Batch by 6-8 images; very large batches can timeout or drop the websocket.
- Global style: vertical 9:16, warm ivory paper, forest green ink, mint/sage, warm gray UI shadows, rare coral, gold value accent, no logos/screenshots/faces/tiny unreadable paragraphs, no purple/violet/lavender glow.
- Primary readable copy should be added later in Remotion; generated micro-text is decorative only.

## Codex CLI imagegen recovery pattern

If `codex exec` times out after image generation, inspect the JSONL log for `thread_id`, then recover files from:

```bash
/home/ubuntu/.codex/generated_images/<thread_id>/*.png
```

Copy in modification-time order to the intended target paths if the log shows generation completed but final copy did not happen.

Known issue seen in-session: websocket reset / stream disconnected before completion after images were generated. Recovery via generated_images folder worked.

## Packaging fallback

`zip` may be missing on this host. Use Python instead:

```python
from pathlib import Path
import zipfile
root = Path('/home/ubuntu/greg-style-kit-expanded')
zp = Path('/home/ubuntu/greg-style-kit-expanded.zip')
with zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED) as z:
    for p in root.rglob('*'):
        if p.is_file() and '.git' not in p.parts:
            z.write(p, p.relative_to(root.parent))
with zipfile.ZipFile(zp) as z:
    assert z.testzip() is None
```

## Verification checklist

```bash
python - <<'PY'
from pathlib import Path
from PIL import Image
import zipfile, json
root=Path('/home/ubuntu/greg-style-kit-expanded')
print('ai_pngs', len(list((root/'ai-generated').rglob('*.png'))))
print('icons_v2', len(list((root/'icons-v2').glob('*.svg'))))
print('shapes_v2', len(list((root/'shapes-v2').glob('*.svg'))))
for p in (root/'ai-generated').rglob('*.png'):
    Image.open(p).verify()
with zipfile.ZipFile('/home/ubuntu/greg-style-kit-expanded.zip') as z:
    print('zip_test', z.testzip())
Image.open(root/'previews'/'ai-generated-contact-sheet.jpg').verify()
PY
```

Also run vision QA on the contact sheet. Acceptable result: cohesive warm editorial AI/startup theme; note dense generated pseudo-UI as a production caveat rather than a hard failure.

## Delivery

Send both:

- `MEDIA:/home/ubuntu/greg-style-kit-expanded.zip`
- `MEDIA:/home/ubuntu/greg-style-kit-expanded/previews/ai-generated-contact-sheet.jpg`

Mention counts and any caveats: generated text is decorative, dense hero assets should get Remotion text overlays, sticker sheets may need cropping/cleanup.
