# OpenMontage social-media mirror

Use this when Het shares Instagram algorithm, user psychology, engagement, hook, carousel, or editing lessons and wants them available to both Hermes and his local OpenMontage-synced AI agent.

## Rule

Store durable Instagram/social-media learning in the relevant Hermes social-media skill and mirror it into OpenMontage repo-local files under `/home/ubuntu/projects/OpenMontage/skills/social-media/`.

Do not use symlinks for this mirror. OpenMontage is synced through GitHub to Het's local machine, so the repository must contain real Markdown/support files.

## Steps

1. Update the class-level Hermes social-media skill first, usually `instagram-reels` or `instagram-carousel`.
2. Put one-post/session detail in `references/`; put durable operating rules in `SKILL.md`.
3. Copy the changed skill directory into `/home/ubuntu/projects/OpenMontage/skills/social-media/<skill>/` as real files.
4. Update mirror metadata (`skills/social-media/INDEX.md` and `mirror-manifest.json`) when hashes or files change.
5. Verify no missing/different/extra mirrored files before reporting success.
6. If the user asks to sync with GitHub, commit and push OpenMontage to Het's repo sync remote, then verify the remote branch SHA matches local HEAD.

## Pitfalls

- Chat memory alone is not enough; Het's local agent cannot read it.
- OpenMontage-only storage is not enough; Hermes may miss the lesson in future sessions.
- Avoid one narrow skill per Reel/post. Keep class-level skills rich and put specific source notes in `references/`.
- Redact credentials and secrets before mirroring or pushing.
