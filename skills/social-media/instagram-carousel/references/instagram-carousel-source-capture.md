# Instagram carousel source capture for future videos

Use when Het sends an Instagram carousel/post and says to save the repos/tools/ideas for a possible video.

## Proven workflow
1. Capture the source URL and shortcode.
2. If normal browser/web extraction hits Instagram login or unsupported-site blocks, try `instaloader` against the shortcode:
   - `Post.from_shortcode(L.context, "SHORTCODE")`
   - read `owner_username`, `caption`, `typename`, and `mediacount`
   - for carousel posts, iterate `post.get_sidecar_nodes()` and download each `display_url`
3. Save assets under a durable Marketing OS research folder, e.g.:
   - `~/.hermes/marketing-os/research/<topic>-<shortcode>/slide-01.jpg ...`
   - `caption.txt`
   - `contact-sheet.jpg`
4. Make a contact sheet and OCR/analyze it with vision for fast extraction of repo names, categories, and visible benefit text.
5. For GitHub repo lists:
   - normalize OCR mistakes by searching exact suspicious names.
   - verify links via GitHub API or web search when API rate-limits.
   - save both `repos.json` and a grouped `README.md` with source link, categories, repo URLs, and video angle.
6. Save the result into Notion Content Ideas as a reusable source note when available. Include source URL, local archive path, category grouping, and all repo links.

## Pitfalls
- Instagram often redirects browser sessions to login; do not stop there if the public post is accessible via shortcode tooling.
- Search snippets may reveal only fragments; use them to orient, but use the downloaded slides + OCR for the actual list.
- OCR can misread GitHub org casing/hyphens. Correct with web search/GitHub before finalizing. Examples from this session:
  - `OpenDev-Society/openstock` → `Open-Dev-Society/OpenStock`
  - `decodingai/llm-twin-course` → `decodingai-magazine/llm-twin-course`
- GitHub API rate-limits are transient; keep already collected `html_url`, stars, and descriptions rather than marking links invalid solely because later calls hit rate limits.

## Deliverable shape
Return the Notion link plus local archive path. Mention what was archived: slides, contact sheet, structured JSON, Markdown backup. Keep the final response short.