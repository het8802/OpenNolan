# Provenance — HyperFrames Layer 3 Skills

These skills are **vendored** from the upstream HyperFrames monorepo. Do not
edit them expecting the changes to survive a re-sync — changes need to go
upstream, or be recorded in the local-edit log below.

## Source

- Repo: https://github.com/heygen-com/hyperframes
- Local clone: `~/hyperframes`
- Vendored commit: `d291358`
- Vendored date: `2026-04-17`

## Mirrored directories

| OpenNolan path                               | Upstream path                             |
| ---------------------------------------------- | ----------------------------------------- |
| `.agents/app/skills/hyperframes/`                  | `skills/hyperframes/`                     |
| `.agents/app/skills/hyperframes-cli/`              | `skills/hyperframes-cli/`                 |
| `.agents/app/skills/hyperframes-registry/`         | `skills/hyperframes-registry/`            |
| `.agents/app/skills/website-to-hyperframes/`       | `skills/website-to-hyperframes/`          |

The `gsap` upstream skill is NOT re-vendored — OpenNolan already ships its
own GSAP skill family under `.agents/app/skills/gsap*/`.

## Local edits

Any divergence from upstream is noted at the top of the edited file as an
HTML comment starting with `OpenNolan-local`. Current edits:

- `hyperframes-cli/SKILL.md` — added `validate` to the command list and a
  dedicated Validation section. Upstream omits it, but the CLI ships it and
  OpenNolan's HyperFrames runtime path relies on `hyperframes validate` as
  a real browser-based contract check before render.

## Re-sync procedure

```bash
# From the hyperframes clone
cd ~/hyperframes
git pull

# From OpenNolan
cd ~/OpenNolan
rm -rf .agents/app/skills/hyperframes .agents/app/skills/hyperframes-cli \
       .agents/app/skills/hyperframes-registry .agents/app/skills/website-to-hyperframes
cp -r ~/hyperframes/skills/hyperframes          .agents/app/skills/
cp -r ~/hyperframes/skills/hyperframes-cli      .agents/app/skills/
cp -r ~/hyperframes/skills/hyperframes-registry .agents/app/skills/
cp -r ~/hyperframes/skills/website-to-hyperframes .agents/app/skills/
# Then re-apply the local edits listed above and bump the vendored commit SHA.
```

## Why we vendor instead of referencing the upstream clone directly

1. OpenNolan contributors may not have the HyperFrames monorepo on disk.
2. Skills must be readable from the OpenNolan tree for agent discovery.
3. We want deterministic knowledge — upstream moves; we control when we pick
   up changes.
