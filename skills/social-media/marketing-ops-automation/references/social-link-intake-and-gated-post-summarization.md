# Social link intake and gated-post summarization

Use this reference when a user shares an Instagram Reel/post, Threads post, TikTok, X/Twitter post, or similar social link as research input for Marketing OS, creator-pattern learning, or a direct summary request.

## Core standard

Summarize from the strongest retrievable evidence and clearly label access limits. For gated platforms, combine browser metadata, indexed snippets, previews/thumbnails, mirrored/cross-posted pages, and visual analysis rather than pretending to have watched or read inaccessible content.

## Workflow

1. Open the original link in browser first; capture title, page text, accessibility state, and any login/gating.
2. Inspect page metadata (`og:title`, `og:description`, `twitter:*`, app links, image URLs, keywords) with browser console or page source.
3. Search the exact URL/shortcode/caption/creator handle. Check search snippets and cross-post mirrors such as Threads for Instagram content.
4. If a thumbnail or preview image is available, analyze it visually and transcribe visible text.
5. Synthesize a short answer:
   - what it is: platform, creator, post type, title/caption/date if available
   - what it is about: topic and likely takeaway
   - confidence/access caveat: what was directly verified vs inferred
6. If media was downloaded locally only for analysis/learning, delete it after extracting and storing the reusable lesson. Keep only small notes or skill updates unless the user explicitly asks to preserve raw media.
7. Do not overclaim transcripts, spoken content, or comments unless directly retrieved.

## When Het says “Learn this”

Do not store reusable Instagram strategy lessons primarily in memory. Extract the durable pattern and update the right skill:

- Reels, hooks, talking-head scripts, short-form retention, visual/spoken hook systems → patch `instagram-reels`.
- Carousels, swipe funnels, slide jobs, save-worthy post systems, visual carousel design → patch `instagram-carousel`.
- If the post teaches a distinct reusable domain system that does not belong in either skill, create a new focused skill and cross-reference it.

Keep only a tiny memory pointer if needed; skills are the source of truth for Het's learned Instagram creator knowledge.

## Useful probe

```js
({
  title: document.title,
  text: document.documentElement.innerText.slice(0, 2000),
  meta: [...document.querySelectorAll('meta')]
    .map(m => ({
      p: m.getAttribute('property') || m.getAttribute('name'),
      c: m.getAttribute('content')
    }))
    .filter(x => x.c)
})
```

Search queries that work well:

- `"<shortcode>" Instagram reel`
- `"<exact caption>" "<handle>"`
- `site:threads.com "<handle>" "<caption>"`

## Instagram / Threads public metadata patterns

When unauthenticated Instagram access is gated or empty, the page can still expose useful `<meta>` fields:

- `og:title` / `twitter:title`: often creator + caption/title.
- `og:description` / `description`: likes/comments/date and caption snippet.
- `og:image` / `twitter:image`: CDN thumbnail suitable for visual analysis.
- `og:url`, `al:ios:url`, `al:android:url`: canonical URL and media ID.
- `keywords`: topical tags that can confirm the domain of the post.

Threads cross-posts may expose:

- `og:title`: creator
- `og:description`: caption
- `og:image`: thumbnail
- route props/post IDs, though not necessarily transcript or video URL

If full video/transcript/comments are inaccessible, use a caveat such as:

> I couldn't access the full Reel transcript without login; this is based on public metadata, indexed snippets, and the thumbnail.

Separate verified facts from inferences. Thumbnail text and metadata are verified; spoken content and comment context are not unless explicitly retrieved.

## Common mistakes

- Saying “I watched the Reel” when only metadata/thumbnail was accessible.
- Ignoring `comment_id` parameters; a specific comment may not be publicly accessible.
- Stopping at a login wall instead of checking metadata, snippets, and cross-posts.
- Treating thumbnails as full content.