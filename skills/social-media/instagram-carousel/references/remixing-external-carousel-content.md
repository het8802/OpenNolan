# Remixing an external Instagram carousel with Het's style

Use when Het asks to recreate/remix an Instagram carousel with "exactly this content" but in our style.

## Source capture
- Normalize the post shortcode from the URL, including links like `https://www.instagram.com/p/<shortcode>/?img_index=11`.
- Try Instaloader as the fastest source-capture path when available:
  ```bash
  rm -rf /tmp/ig_<shortcode> && mkdir -p /tmp/ig_<shortcode>
  instaloader --dirname-pattern=/tmp/ig_<shortcode> \
    --no-videos --no-captions --no-metadata-json --no-profile-pic \
    --post-filter='shortcode == "<shortcode>"' -- -<shortcode>
  ```
- A transient `graphql/query: 403 Forbidden` retry line can appear while Instaloader still downloads the carousel. Treat the final downloaded files as the verification, not the warning line alone.
- Build a contact sheet immediately and run vision/OCR-style QA on it before drafting replacement slides.

## Exact-content reconstruction
- Preserve slide count and ordering unless Het explicitly asks to compress or expand.
- Extract visible repository names, claims, CTAs, and slide roles from the original.
- For GitHub-repo list carousels, verify repo metadata via GitHub API or web search before using stars/forks/descriptions. Use rounded counts in the design if live counts have drifted from the source image.
- Do not claim exact OCR if small text is unreadable; reconstruct the core visible content and label any live metadata as verified.

## Styling into Het's carousel system
- Keep the source's information architecture, but translate the visual language into Het's warm editorial AI/tech style: ivory/cream paper, charcoal ink, clay/orange accents, muted olive/blue/lavender states, tactile cards, shadows, diagrams, and readable mobile-first typography.
- The source may use dark cyber/neon styling; do not copy that look unless asked. Convert it into premium editorial/product-strategy visuals.
- For repository roundup carousels, a robust deterministic fallback is a PIL-generated editorial template: cover poster, repo cards with description/stats/link, 3-part benefit diagrams, CTA slide, follow slide, then remaining repo slides.

## QA/delivery
- Verify all slides are 1080×1350 PNG unless another format is requested.
- Generate a contact sheet and inspect for title/tagline clipping, spelling, mobile readability, story order, and style fit.
- If long repo names clip, dynamically reduce title font size rather than cropping.
- Deliver individual slides in exact carousel order first, then contact sheet, with zip only as backup.