# OpenNolan social-media mirror

Use this when the user shares Instagram algorithm, user psychology, engagement, hook, carousel, or editing lessons and wants them available to both their marketing pipeline and their local OpenNolan-synced AI agent.

## Rule

Store durable Instagram/social-media learning in the relevant social-media skill and mirror it into OpenNolan repo-local files under `skills/social-media/`.

Do not use symlinks for this mirror. OpenNolan is synced through GitHub to your local machine, so the repository must contain real Markdown/support files.

## Steps

1. Update the class-level social-media skill first, usually `instagram-reels` or `instagram-carousel`.
2. Put one-post/session detail in `references/`; put durable operating rules in `SKILL.md`.
3. Copy the changed skill directory into `skills/social-media/<skill>/` as real files.
4. Update mirror metadata (`skills/social-media/INDEX.md` and `mirror-manifest.json`) when hashes or files change.
5. Verify no missing/different/extra mirrored files before reporting success.
6. If the user asks to sync with GitHub, commit and push OpenNolan to your repo sync remote, then verify the remote branch SHA matches local HEAD.

## Pitfalls

- Chat memory alone is not enough; your local agent cannot read it.
- OpenNolan-only storage is not enough; the content-OS may miss the lesson in future sessions.
- Avoid one narrow skill per Reel/post. Keep class-level skills rich and put specific source notes in `references/`.
- Redact credentials and secrets before mirroring or pushing.
