# Reel DY5SPASumP8 — Pixel RPG Product Explainer Pattern

Use as session-specific reference when Het asks for OpenMontage/HyperFrames/Remotion videos inspired by the Instagram Reel `https://www.instagram.com/reel/DY5SPASumP8/`.

## Grounded analysis notes

- Runtime/format observed: vertical 1080×1920, ~61s, 24fps.
- Transcript gist: Obsidian + Claude can help you avoid getting left behind by connecting Obsidian to Claude via MCP, using Claude to learn industry trends, adding Smart Connections for context/patterns, turning gaps into a 7-day deep-dive checklist, then using notes to create a 30-day learning-in-public plan.
- Visual grammar: top-down pixel/RPG map + warm beige tile/off-white editorial canvas + floating dark SaaS/product UI cards + kinetic serif captions.

## Time-range breakdown

| Time | What happens | Reusable edit lesson |
|---|---|---|
| 0–3s | Pixel avatar runs toward old computer and purple crystal; cracked/portal effect opens; labels like layoffs/automation/budget cuts radiate outward. | Open with world-state threat as a game encounter, not a static title. |
| 3–7s | Business/news/report windows stack over the portal with highlighted layoffs/KPMG/layoff tracker proof. | Proof cards enter as overlapping desktop windows with soft shadows and highlighted phrases. |
| 7–10s | Avatar, crystal, and Claude/star sidekick return on the map. | Re-anchor tutorial in the metaphor after proof. |
| 10–16s | Kinetic serif chapter text: “There are 3 levels…” then Obsidian → MCP node → Claude connection diagram and vault screenshot. | Use editorial caption cards plus dotted connectors for tool connection. |
| 17–21s | Black circular “Tech” map with mint pill trends such as AI Automation, Vibe Coding, AI Agents, Vertical AI. | Use a dark radial map to make “industry trends” visually concrete. |
| 21–37s | Claude prompt/result cards, Smart Connections brain graphic, search grids, dark UI screenshots, prompt asking for lacking skills. | Prompt bars are hero UI: black rounded pill, green rim glow, typed text, result card above. |
| 37–42s | AI response turns into “7-Day Deep Dive” checklist. | Always transform abstract output into a tangible artifact. |
| 42–47s | White negative-space moral beat: “learning quietly won’t get you hired”; social comments/proof cards appear. | Use a text-only reset before the final stakes/payoff. |
| 47–61s | Prompt asks Claude to analyze last 10 notes and build a 30-day learning-in-public plan; final profile/proof board lands. | End on proof-of-work identity, not generic CTA. |

## Motion vocabulary

- `rpg-walk`: pixel avatar crossing map with 8-bit bob/step cycle.
- `portal-crack`: radial cracks, purple glow, threat labels shooting outward.
- `desktop-window-stack`: proof cards slide from edges, overlap, settle with slight rotation/shadow.
- `phrase-collage-build`: caption phrases appear as meaning chunks, not full subtitles.
- `keyword-color-hit`: key words switch to mint/purple/coral and scale subtly to ~1.03–1.06.
- `dotted-connector-draw`: tool-to-tool line draws before node/card appears.
- `radial-tag-populate`: mint tags populate around a dark circle/map.
- `prompt-bar-type`: black prompt pill slides in, green glow activates, text types.
- `response-to-artifact`: result card morphs/shrinks into checklist/plan/proof grid.
- `proof-grid-land`: final profile/cards scale/y-settle with soft shadow and small push-in.

## OpenMontage codification created during session

A class-level OpenMontage skill/playbook was created in the OpenMontage repo:

- `skills/creative/pixel-rpg-product-explainer.md`
- `styles/pixel-rpg-product-explainer.yaml`
- `skills/INDEX.md` entries for both

Validation commands used from `/home/ubuntu/projects/OpenMontage`:

```bash
python - <<'PY'
import json, yaml
from jsonschema import validate
schema=json.load(open('schemas/styles/playbook.schema.json'))
playbook=yaml.safe_load(open('styles/pixel-rpg-product-explainer.yaml'))
validate(playbook, schema)
print('style schema validation: OK')
PY

python - <<'PY'
from styles.playbook_loader import load_playbook
pb=load_playbook('pixel-rpg-product-explainer')
print('loader validation: OK')
print(pb['identity']['name'])
PY
```

## Production rules to reuse

- Treat the game/RPG layer as a workflow metaphor: each level must map to a real action (`connect`, `prompt`, `retrieve`, `plan`, `publish`).
- Do not make pixel art decorative only; product proof must remain legible and central.
- Primary instructional text should be OpenMontage-rendered overlays, not tiny screenshot text.
- Alternate light canvas/map scenes with dark UI/prompt cards for attention resets.
- Use soft premium ease-out motion; avoid heavy yoyo bounces and random glitch effects.
- Final payoff should be a tangible proof board/profile/checklist/content plan, not a generic “follow for more.”
