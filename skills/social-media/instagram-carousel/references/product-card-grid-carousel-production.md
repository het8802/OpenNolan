# Product-card grid carousel production pattern

Use when Het asks to create/remix an AI/tech carousel in the @okaashish-style product-card/grid design system.

## Source content pattern
- Best for resource stacks, tool lists, skill marketplaces, agent workflows, and “save these links” posts.
- Preserve the source slide count/order when remixing an external carousel unless Het asks to compress.
- Extract the source into: cover promise, one resource/tool per slide, save-worthy link bank, keyword CTA.

## Visual system
- Canvas: 1080×1350, off-white/very light gray with faint grid-paper lines.
- Palette: near-black text, clay/rust orange for section pills/title bars/starburst, pale blush cards/`Best For` strips, dark navy screenshots/mockups for contrast.
- Components: clay section pill, huge product/resource title, factual star/metric badge or omit, short explanation, rounded screenshot/mockup card with soft shadow, `Best For:` note strip, subtle footer handle + swipe button.
- Cover: stacked clay title bars, floating resource cards, small central starburst, compact subtitle.
- CTA: keyword comment request plus save/follow framing.

## Deterministic generation fallback
When native image generation is likely to create unreadable text or inconsistent slide layouts, use a deterministic PIL/Canvas-style generator instead:
1. Define fixed slide dimensions, palette constants, font paths, and reusable components (`grid`, `pill`, `star_pill`, `footer`, `mockup`, `text_box`, `fit_text`).
2. Generate ordered `slide-01.png` … `slide-N.png`, a contact sheet, and optional zip backup.
3. Track automated layout violations for fitted text and report `VIOLATIONS 0` before delivery.
4. Keep screenshot/mockup text illustrative and readable; do not rely on tiny mockup copy as the only useful information.

## QA lessons from session
- Contact sheets can hide defects. Always spot-check dense or risky individual slides after contact-sheet QA.
- Watch long titles like “Frontend UI Engineering”; use fitted multiline titles rather than allowing right-edge clipping.
- Avoid decorative underlines crossing body copy; they look like strikethroughs and reduce legibility.
- Wrap bottom handwritten/italic notes instead of letting them run into the footer or off-canvas.
- If using fake product screenshots/mockups, ensure core facts live in the main slide body, not only inside the mockup.

## Delivery shape
- Send individual slides first in exact carousel order.
- Include the contact sheet for review.
- Include zip only as a secondary backup, not the primary review path.