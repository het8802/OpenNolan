# Anthropic-inspired editorial carousel style

Use when Het asks for a carousel aesthetic inspired by Anthropic or wants the previous AI/tech carousel to look more premium.

## Visual system
- Palette: warm cream/paper backgrounds, charcoal ink, muted clay/orange accents, sand/tan cards, subdued olive/green approval states.
- Mood: editorial product-strategy memo, not SaaS dashboard screenshot or HTML deck.
- Motifs: paper texture, rounded cards, workflow tiles, connector diagrams, simple line icons, approval buttons, subtle grain/shadows.
- Typography: large serif editorial headlines paired with clean sans body text; prioritize mobile readability over dense explanatory copy.

## Layout rules
- Build each slide as a full-bleed poster-like composition with one focal idea.
- Do not use repeated top headers, bottom footers, date bars, brand bars, slide numbers, or template chrome.
- Contact sheets may include small review numbers, but final `slide-XX.png` files must not.
- Use bottom callout bands only when they are part of the slide’s message, not as repeated footer furniture.

## Preferred generation method
- Use **Codex CLI native image generation** for premium carousel imagery when available. Prompt Codex explicitly with `$imagegen` and instruct it not to write Python/SVG/HTML/CSS/Canvas unless a deterministic programmatic fallback is explicitly needed.
- Preferred native prompt language: `Use the native Codex image generation capability, explicitly with $imagegen. Do NOT write Python, SVG, HTML, CSS, Canvas, or any code to create this image.`
- Ask Codex to save each generated image to a named PNG path. For full carousel sets, generate one image per slide in order (`slide-01.png`, `slide-02.png`, …) or generate the cover first, QA it, then continue.
- If native Codex outputs a non-Instagram size, report it honestly and only resize/crop as a post-processing step when needed; keep the original native output.
- Use programmatic Pillow/Codex rendering only as a fallback for deterministic batch layouts, not as the default when Het asks for generated images.

## Anthropic-inspired aesthetic direction
- The default AI/tech carousel aesthetic should be **Anthropic-inspired**: warm ivory/cream paper, charcoal ink typography, clay/orange accent color, sand/tan cards, muted olive/green states, simple original line icons, tactile paper collage, sticky notes, workflow cards, connector diagrams, approval-gate motifs, subtle film grain and shadows.
- It should feel like a premium editorial product-strategy memo / Anthropic-style research note, not a SaaS dashboard screenshot, HTML render, PowerPoint deck, or generic Canva template.
- Use original generic icons only; do not copy Anthropic logos or proprietary brand assets.

## QA lessons from the invoice-chaser trials
- Het specifically clarified that Codex can generate images **natively** via `$imagegen`; do that first for image-generation asks instead of asking Codex to write a Pillow script.
- Vision QA can misread compact acronyms like `GL`; use `Ledger reconciler` when readability matters.
- Re-check individual slides after contact-sheet QA flags possible cropping; contact-sheet scale can hide or exaggerate issues.
- Fix any cropped headline before delivery, even if the overall design looks good.
- Deliver ordered slide images first (`slide-01.png`, `slide-02.png`, …); archives are optional backups only.
