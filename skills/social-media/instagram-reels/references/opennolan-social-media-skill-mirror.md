# OpenNolan social-media skill mirror

Use this when the user shares Instagram/social-media learning and wants it available to their local OpenNolan-synced AI agent.

## Durable preference
You sync your `OpenNolan` checkout to your local machine through GitHub. Instagram/Reels/carousel/editing/engagement lessons should therefore live in both places when relevant:

- Marketing-OS runtime skills: `~/marketing-os/skills/social-media/`
- OpenNolan repo-local mirror: `skills/social-media/`

Do **not** rely on symlinks for the mirror. Real Markdown/files inside the OpenNolan repo survive GitHub/local checkout; absolute symlinks to the runtime paths can break on your machine.

## What to mirror
Mirror class-level social-media skills, including `SKILL.md` and support directories (`references/`, `templates/`, `scripts/`, `assets/`) when present. As of the first mirror pass, the mirrored set was:

- `daily-carousel-remix`
- `daily-tech-carousel`
- `editorial-ai-product-design-system`
- `instagram-carousel`
- `instagram-reels`
- `marketing-ops-automation`
- `marketing-os-tools`
- `source-backed-reel-evidence-montage`
- `talking-head-screen-demo-reel`
- `xurl`

## Workflow after learning a new Instagram/social-media lesson
1. Patch the relevant marketing-OS skill first (`instagram-reels`, `instagram-carousel`, or another social-media umbrella).
2. If the lesson affects Instagram algorithm, user psychology, engagement, hooks, editing, or OpenNolan production, mirror the changed skill files into `skills/social-media/<skill>/` as real files.
3. If the lesson also belongs in an OpenNolan creative production skill, update the OpenNolan-native file too, such as `skills/creative/short-form.md` or a specific style/playbook reference.
4. Update or regenerate `skills/social-media/INDEX.md` and `mirror-manifest.json` if the mirrored set or file hashes change.
5. Verify by comparing file hashes both ways. The expected success shape is: no missing files, no extra files, and no differing files for each mirrored skill.

## Pitfalls
- Do not store only in chat memory; your local agent will not see it.
- Do not store only in OpenNolan; the marketing-OS will not reliably apply it in future sessions.
- Do not create one narrow skill per Reel/post. Put the durable operating rule in the class-level skill and put session-specific detail in `references/`.
- Redact credentials/secrets before mirroring any session notes.
