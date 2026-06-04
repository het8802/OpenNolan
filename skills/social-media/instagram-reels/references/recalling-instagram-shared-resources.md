# Recalling previously shared Instagram resources

Use when Het asks variants of: “what open source projects did I share from Instagram?”, “what tools/links were in that Reel I sent?”, or “find the Instagram projects I shared.”

## Retrieval pattern
1. Search past sessions first, not the web. Good queries:
   - `"instagram.com/reel"` with `role_filter="user"` to find direct Reel shares from Het.
   - `"instagram.com/p"` with `role_filter="user"` to find direct carousel/post shares. Many GitHub/tool lists are posts, not Reels.
   - `"Get the link" "instagram.com/reel"`, `"open source" "instagram.com"`, `"GitHub repos" "instagram.com"`, `"open-source projects"`, or the specific remembered topic (`"Claude Code skills"`, `"50 Best Claude Code Skills"`).
2. If the first result is a skills/tutorial Reel but Het says he meant “many open source projects available on GitHub,” switch away from skills-specific queries. Search for phrasing from viral reposts such as `"10 GitHub Repos That Quietly print money"`, `"open-source Calendly"`, `"self-hosted Vercel"`, or `"GitHub repos" "print money"`.
3. Prefer direct user-shared sessions over cron/marketing research snippets. Cron collector entries can mention Instagram URLs, but they are usually research signals, not necessarily things Het personally shared.
4. If a prior assistant answer already extracted and verified links, scroll that session and reuse the final table rather than re-running video extraction.
5. If the prior extraction looks uncertain or contains umbrella/awesome-list substitutions, label that explicitly instead of presenting every row as a canonical exact repo.
6. Return a concise answer for “some” / “what are some”: lead with the Instagram Reel/post URL/context, then a compact table of the highest-confidence projects. Do not dump all 50 items unless Het asks for the full list.

## Confidence labels
- **Direct shared Reel:** Het pasted the Instagram URL in Slack/chat.
- **Extracted repo:** A prior workflow found a likely GitHub URL from the Reel/frame/OCR/search.
- **Umbrella/list repo:** The best available link was a collection or awesome list, not a single exact project.
- **Research snippet only:** Found by automated marketing research, not necessarily user-shared; include only if the user asks broadly about research history.

## Examples from May 2026

### GitHub projects carousel, not the Claude skills Reel
If Het asks for the Instagram share with “many open source projects available on GitHub,” he likely means post `https://www.instagram.com/p/DYZ5UTiknH2/?img_index=3` (“10 GitHub Repos That Quietly Print Money While You Sleep”), not Reel `DXt6opNiAN1`.

Recovered/verified project list:
1. Cal.com / Cal.diy — scheduling infra / Calendly alternative — `https://github.com/calcom/cal.diy`
2. Plausible Analytics — privacy-first analytics — `https://github.com/plausible/analytics`
3. Ghost — publishing/newsletters — `https://github.com/TryGhost/Ghost`
4. n8n — workflow automation / Zapier alternative — `https://github.com/n8n-io/n8n`
5. Supabase — Firebase/Postgres backend platform — `https://github.com/supabase/supabase`
6. Medusa — ecommerce / Shopify alternative — `https://github.com/medusajs/medusa`
7. AppFlowy — Notion alternative — `https://github.com/AppFlowy-IO/AppFlowy`
8. Coolify — self-hosted Vercel/Heroku/Netlify alternative — `https://github.com/coollabsio/coolify`
9. Listmonk — self-hosted newsletter/mailing list manager — `https://github.com/knadh/listmonk`
10. Penpot — open-source Figma/design collaboration alternative — `https://github.com/penpot/penpot`

Useful framing for Het: the post is clickbait-y on “passive income,” but useful as a business/content pattern: find proven OSS infra and package it into a niche outcome via setup, hosting, managed service, verticalized workflow, or content/business ops system. Most relevant for Het’s systems: n8n, Listmonk, Ghost, Coolify, Supabase.

### Claude Code skills Reel
For Reel `DXt6opNiAN1` (“50 Best Claude Code Skills”), the useful recalled items included Autoresearch, Remotion skills, social-media skills, Humanizer, Anything to NotebookLM, Beautiful Prose, TweetClaw, X Article Publisher, Color Expert, Hand-Drawn Diagrams, Deep Research, Academic Research, Social Media Research, PM Skills, Video Toolkit, Claude AI Music Skills, Superpowers, Repomix, Antfu Skills, Claude SEO, Vexor, Skill Seekers, and Web Scraper. Some rows were umbrella/awesome-list matches, so future answers should say “main open-source items I found” rather than imply perfect canonical extraction for every skill.
