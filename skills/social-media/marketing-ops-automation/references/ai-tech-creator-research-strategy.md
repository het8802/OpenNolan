# AI Tech + Creator Research Strategy

Use this reference for Content OS research. Despite the name, Content OS is an AI/tech content engine, not a generic marketing-advice system. Research should focus on AI tools, companies, funding, startup incubators/applications, accelerator programs, founder credits/perks, events, devtools, agents, infrastructure, model releases, enterprise AI, and consumer AI.

## Research cadence

- Hourly research jobs are source collection only and should deliver local-only.
- Daily 5 PM (your local timezone) jobs should synthesize the last 24 hours of research into one strong video concept and a ready-to-film script.
- Store hourly evidence as JSONL under `~/content-os/research/YYYY-MM-DD/hour-HH.jsonl` and maintain a rolling strategy note under `~/content-os/strategy/research-playbook.md`.

## Required source mix

Every hourly research pass should look across at least these buckets when tools/credentials allow:

1. Breaking AI/tech news: OpenAI, Anthropic, Google DeepMind, Meta AI, Microsoft, Nvidia, xAI, Perplexity, Mistral, Hugging Face, major model releases, product launches, policy/regulation, benchmark shifts.
2. Funding/startups: funding rounds, acquisitions, YC/accelerator launches, AI infrastructure, agents, devtools, vertical AI, enterprise workflow automation.
3. Startup opportunities: incubators, accelerators, fellowships, grant programs, cloud/API credits, founder perks, hackathons, demo days, application deadlines, eligibility rules, and events useful to technical founders.
4. Builder/community signal: Hacker News, Reddit, GitHub trending, Product Hunt, arXiv, Hugging Face trending, developer Twitter/X search where available.
5. Creator-pattern signal: top Instagram/Reels/Shorts/TikTok creators in AI/startups/devtools; track what topics, hooks, visual formats, comments, and recurring framing they use.
6. Market/contrarian angles: backlash, failures, pricing changes, adoption data, regulatory risk, open-source vs closed-source tension, jobs/workflow impact.

## Creator research requirements

For creator research, do not merely list creator names. Capture:

- creator/account handle and platform
- post URL when available
- topic being covered
- opening hook pattern
- visual format: talking head, screen recording, green screen, news card, demo, skit, carousel-style video, benchmark comparison, reaction, teardown
- why it is getting attention: controversy, utility, novelty, status, fear, money, workflow improvement, founder/operator relevance
- comments/questions that reveal audience demand
- reusable pattern for your content

Prefer approved/free routes first. Apify TikTok scraping is approved only for the named low-volume actor already approved; other paid tools require approval. Instagram scraping should use free/public/manual browser observation unless an exact paid actor/tool is approved.

## Daily 5 PM synthesis standard

The daily script job must not summarize everything. It should choose one best video concept using:

- freshness: why this matters today
- evidence strength: at least 3 credible sources or 2 sources + strong creator signal
- audience fit: useful to AI/startup/software-engineer/founder/operator audience
- visual potential: can become engaging video, not just abstract news
- differentiation: avoids generic “AI is changing everything” framing
- actionability: viewer learns what to watch, do, or think differently

Daily output should include:

1. Chosen video concept title
2. Why this concept won today
3. Source/evidence bullets with links
4. For tools/companies/funding/programs: what it is, who it is for, why it matters now, eligibility/deadlines/credits/links when applicable
5. Creator-pattern insight with at least one observed format/hook pattern when available
6. 45–75 sec short-form script with timestamps
7. Visual storyboard with motion/B-roll ideas for each beat
8. Caption + 3 alternate hooks
9. Follow-up research questions for tomorrow's hourly jobs

## Skill updating loop

When research reveals a repeatable winning pattern, creator format, source that consistently performs, or a failure mode in script/video quality, update `marketing-ops-automation` or this reference. The update should be procedural and reusable, not a one-off log entry.

## Anti-patterns

- Do not write scripts from one generic web search.
- Do not use only company blog posts; add independent/market/community signal.
- Do not treat Instagram creator research as vanity inspiration; extract hook, pacing, format, and audience-demand signals.
- Do not choose concepts that cannot be visualized dynamically.
- Do not produce image-slideshow video plans; require B-roll, screen recordings, demos, kinetic UI, transitions, and sound-design notes.
