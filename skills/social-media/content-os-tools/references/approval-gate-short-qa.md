# Approval-gate short: reusable production notes

Use when producing Content OS shorts about AI agents, workflow control layers, approval gates, audit trails, or vertical AI product patterns.

## Content pattern that worked

- Frame the launch as a product-pattern lesson, not a news recap: "not the model — approval gates."
- Show the vertical-AI stack visually: `System of record → Packaged workflow → Human approval → Audit trail`.
- For SMB/ops agent stories, use concrete consequential verbs in visuals: `SEND`, `POST`, `PAY`, `SIGN`.
- Product-spec final frame: three columns works well for founder audiences: `Connect / Automate / Approve`.

## Procedural motion-graphics QA lessons

- Low-alpha red stamps can look blank in contact sheets. Use high-contrast fill plus white text for important stamps such as `APPROVAL GATE`.
- If a final CTA card sits above a lower-third caption, move the caption or CTA; do not let the caption cover the CTA. Keep a second pass specifically for final-frame safe area.
- White/frosted CTA cards can wash out in 3x3 contact sheets and mobile compression. Use dark cards with light text, or bright buttons with dark text.
- Fine print near the lower third often becomes illegible once captions and progress bars are added. Either remove it, move it up, or convert it into a bold two-line takeaway.
- Contact-sheet QA should explicitly ask about previous blockers after a fix: blank stamps/buttons, unreadable final cards, clipped CTA text, lower-third overlap, and black frames.

## Verification loop used

```bash
ffprobe -v error -show_entries format=duration,size -of json final.mp4 > qa/ffprobe.json
ffmpeg -v error -i final.mp4 -f null -
ffmpeg -hide_banner -nostats -i final.mp4 -vf "blackdetect=d=0.25:pic_th=0.98" -an -f null - 2> qa/blackdetect.log
ffmpeg -y -loglevel error -i final.mp4 -vf "fps=1/7,scale=270:480,tile=3x3:padding=8:margin=8" -frames:v 1 qa/contact-sheet.jpg
```

Then run visual QA on the contact sheet before sharing the MP4. If issues are found, patch the renderer and re-run the full decode/blackdetect/contact-sheet loop.
