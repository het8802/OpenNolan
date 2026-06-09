# OpenAI Ads Manager Greg-style Remotion Reel

Session reference for turning a source Instagram Reel + drafted script into a finished Greg/editorial AI-product Reel.

## Source signal
- Source: a reference Instagram Reel the user shared.
- Topic: OpenAI beta self-serve Ads Manager / ChatGPT Ads.
- Official source pages used for grounding:
  - `https://ads.openai.com/`
  - `https://openai.com/index/new-ways-to-buy-chatgpt-ads/`
  - `https://help.openai.com/en/articles/20001206-ads-manager-beta-overview`

## Final artifact from session
- Project: `~/openai-ads-greg-reel`
- Rendered MP4: `~/openai-ads-greg-reel/openai-ads-greg-reel.mp4`
- VO file: `~/openai-ads-greg-reel/public/audio/openai-ads-vo.mp3`
- Composition ID: `OpenAIAdsGregReel`
- Format: `1080x1920`, `30fps`, `1470 frames`, about `49s`

## Pattern that worked
Use this structure when the topic is a new AI/product channel and the user asks for “Greg style video out of it”:

1. **Status hook** — warm paper canvas, small pill (`NEW AD CHANNEL`), large serif headline, one tilted product/browser card, small mascot, badge (`BETA`).
2. **Expectation beat** — side-by-side comparison cards: old channel (`Google / keywords`) versus new channel (`ChatGPT / conversations`).
3. **Setup walkthrough** — single large product UI card with numbered steps. Keep text sparse and mobile-readable.
4. **Context examples** — ChatGPT-style prompt bubbles with 2–3 concrete buying-intent prompts.
5. **Official proof cards** — stacked/tilted cards showing official capabilities: self-serve Ads Manager, CPC/CPM, measurement/performance tracking.
6. **System map** — simple connector diagram that turns the idea into a save-worthy framework (`Prompt → Context → Ad → Click`).
7. **CTA board** — qualifying audience checklist + large comment keyword block.

## Short VO used
> OpenAI just opened a new ad platform, and it is not Google Ads with a new logo. On Google, you fight over keywords. In ChatGPT, you show up inside buying conversations. Think: my landing page is not converting. Compare SEO agencies. How do I get more demo calls? That is the shift: search intent becomes conversation intent. Ads Manager is still beta, but it already supports campaigns, budgets, bids, uploads, performance tracking, and CPC bidding. If you are a founder, SaaS operator, or agency, this is the channel to test before everyone learns the playbook. Comment OpenAI and I will send the signup link plus my prompt context checklist.

## Implementation notes
- Reuse `~/greg-style-demo` as the Remotion base when available, then copy into a topic-specific project.
- Symlink or copy `~/greg-style-kit` into `public/greg-style-kit`.
- Use `text_to_speech` for the VO first, then match the composition duration to `ffprobe` audio duration instead of guessing.
- Keep proof/UI cards stylized rather than literal dense screenshots unless source receipts are necessary.
- A 49s Greg-style info Reel can still work if the scenes are sparse, with large type and one idea per beat.

## QA commands used
```bash
npm run lint
npx remotion still OpenAIAdsGregReel still-90.png --frame=90 --scale=0.25
npx remotion still OpenAIAdsGregReel still-520.png --frame=520 --scale=0.25
npx remotion still OpenAIAdsGregReel still-1050.png --frame=1050 --scale=0.25
npx remotion render OpenAIAdsGregReel openai-ads-greg-reel.mp4 --codec=h264 --crf=18
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,duration -of json openai-ads-greg-reel.mp4
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,duration -of json openai-ads-greg-reel.mp4
ffmpeg -v error -i openai-ads-greg-reel.mp4 -f null -
ffmpeg -y -i openai-ads-greg-reel.mp4 -vf "fps=1/4,scale=270:-1,tile=4x4" -frames:v 1 contact-sheet.jpg
ffmpeg -hide_banner -i openai-ads-greg-reel.mp4 -vf blackdetect=d=0.5:pic_th=0.98 -an -f null -
```

## Pitfalls / reminders
- Render 2–3 stills before the full MP4; it catches scene opacity/timing issues cheaply.
- Contact-sheet QA should include early transition frames, not only polished final states. Crossfades can create ghosted overlaps; keep transition windows short.
- For TypeScript/Remotion, lint before rendering. Small syntax issues from hand-written TSX (extra braces, unused local vars) should be fixed immediately rather than discovered after a long render.
- The final delivery to your delivery channel should attach the MP4 directly with `MEDIA:/absolute/path/to/file` and briefly mention QA dimensions/duration.
