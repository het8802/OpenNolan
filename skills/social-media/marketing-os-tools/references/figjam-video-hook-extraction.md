# FigJam Video Hook Extraction Notes

Session learning: the user may ask for the **actual video hook from FigJam**, meaning a playable/reference clip embedded in the Kallaway FigJam board that they can edit into a Reel—not a text hook taxonomy or a generated PNG/SVG asset.

## Correct interpretation

If the user says "send me the FigJam hook" after discussing hooks, clarify/assume they may mean:

- actual embedded FigJam example video / thumbnail clip
- not just the selected visual+spoken hook labels
- not just a generated asset card or text recommendation

## Workflow

1. Try to open the FigJam board in a real Chromium/Playwright browser with WebGL enabled, not Lightpanda/browser extraction:

```bash
node - <<'NODE'
const { chromium } = require('playwright');
(async()=>{
  const browser=await chromium.launch({headless:true,args:['--enable-webgl','--ignore-gpu-blocklist','--use-gl=swiftshader','--no-sandbox']});
  const page=await browser.newPage({viewport:{width:1400,height:900}});
  await page.goto('FIGJAM_URL',{waitUntil:'load',timeout:60000});
  await page.waitForTimeout(20000);
  await page.screenshot({path:'/tmp/figjam-board.png',timeout:60000});
  console.log(await page.title());
  await browser.close();
})();
NODE
```

2. Inspect screenshots with vision. The board may display tiny video thumbnails with play icons, but screenshot-level visibility is usually not enough to identify source URLs or export the clip.
3. Check page HTML/network for source URLs. In the observed board, the static HTML exposed a thumbnail URL and board metadata, but no clean `.mp4`/embedded-video source URL.
4. If no source URL is available, disclose that FigJam examples are visible but not extractable from the current environment.
5. Do **not** claim an actual FigJam video was sent if you generated a replacement.
6. If useful, create an **original FigJam-style hook insert** based on the selected hook pattern and label it as original/generated, not as the FigJam example clip.
7. Mention copyright/reuse risk for editing other creators' FigJam reference clips directly unless the board/source grants reuse rights.

## Observed limitations

- `browser_navigate` / web extraction may show "Update or switch browsers" or WebGL unsupported for Figma/FigJam.
- Playwright Chromium with `--enable-webgl --use-gl=swiftshader` can render the board enough for screenshots.
- Zooming/panning in FigJam is fragile in headless automation; use screenshots/vision for confirmation, but don't overpromise extraction.
- Public FigJam board HTML can expose `s3-alpha.figma.com/thumbnails/...` image thumbnails; these are static PNGs, not the embedded playable videos.

## User-facing language

Good:

> I can see the FigJam board and small video thumbnails, but I can't extract a clean MP4/source URL from Figma here. I made an original hook insert in the same pattern; it's not the actual FigJam reference clip.

Bad:

> Here's the FigJam hook.

when the file is actually a generated substitute.
