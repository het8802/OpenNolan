# QA Sign-off — Increment 1
Status: QA

## Verdict

**PASS WITH FINDINGS**

All four requested closures and the additional `.al-stage` contrast repair verify. The only remaining finding is the coordinator-observed inspector hint clipping, which I classify as Phase 7 craft debt rather than a blocker for the Phase 0–3 foundation.

## Closure verification

| Item | Verdict | My evidence |
| --- | --- | --- |
| 1 — finish B1 spacing migration | **verified** | I reran the same declaration-level PostCSS parse that failed round 2. It now reports only `.sr-only { margin:-1px }` (`web/src/styles.css:109-111`) and the content-relative `em` rules under `.md-body` (`:276-292`); there are zero raw nonzero `px`/`rem`/`em` values in live UI `margin`, `padding`, or `gap` declarations outside those two intentional exceptions. The former 11 sites now consume the scale, including the negative edge allowance as `calc(var(--space-1) * -1)` (`:418-420`), compact 2px uses (`:902`, `:1141`, `:1175`, `:1220`), and timeline spacing (`:1232`, `:1259`, `:1272`, `:1281`). The parser counts 525 spacing-token values across 384 declarations. |
| 2 — repair `.rp-pct` contrast | **verified** | `.render-progress` establishes `color:var(--on-dark)` on `--pre-bg` (`styles.css:394-399`), and `.rp-pct` no longer overrides that color (`:405-407`). My independent WCAG calculation reproduces 11.129:1 for `#d8d0c4` on `#1e1c18`, replacing the former 3.022:1 pair. Inheritance is the safer implementation because the percentage follows the surface contract if the dark palette changes. |
| 3 — align the Studio stack breakpoint | **verified** | The query is now `max-width:871px`, and the adjacent comment records the real equation: 260px agent + 360px stage + 240px inspector + two 6px splitters = 872px (`styles.css:1358-1364`). This closes the former 860–871px overflow band. The coordinator's browser pass also confirms the supported 960×600 layout remains unstacked with real 303px/285px chat and inspector panels. |
| 4 — add meaningful 960px smoke coverage | **verified** | The Playwright test uses a deliberately long project name at exactly 960×600, then independently asserts no bar/tools horizontal overflow, Save and Export visible with bounding boxes inside the viewport, More visible, and all top-level control centers within one 8px band (`desktop/tests/e2e/smoke.spec.js:56-125`). This is capable of failing: the old invisible scroller violates the scrollWidth assertion, clipping violates the bounding-box assertion, a broken container threshold fails More visibility, and wrapping fails the center spread. The coordinator reports both a negative-control failure when the bug was restored and a passing `scripts/dev smoke`; I did not rerun Chromium in the sandbox, per the explicit instruction. |
| Additional defect — `.al-stage` inherited 1.147:1 text | **verified** | `.al-stage` now owns both `background:var(--pre-bg)` and `color:var(--on-dark)` (`styles.css:522-524`), so the music icon inherits the 11.129:1 pair instead of document `--ink` at 1.147:1. The surface-level change is safe: image/video descendants carry no text; `.al-nav` explicitly uses white (`:531-535`); `.al-text` explicitly restores `--ink` on `--panel` (`:539-544`); and the audio icon is the descendant that should inherit. |

I also audited the claimed dark-surface sweep rather than sampling only `.al-stage`. All `--pre-bg` text surfaces set `--on-dark` (`styles.css:285`, `:324`, `:396`, `:523`, `:810`); the `#2a1815` error pre sets `#f0c8bf` (`:327`); the `#3a3830` rule is a textless progress track (`:401`); the `--frame` stage gives its actual text descendants explicit `--on-dark` while overlay text has an inline authored/default color (`:1101-1131`, `StudioPreview.jsx:537-549`); black media boxes hold media rather than inherited text; and dark alpha controls/badges explicitly use white or `--on-dark` (`styles.css:504-506`, `:531-535`, `:1130-1131`, `:1353-1354`). I found no second unowned dark-surface text color.

## Ruling on the inspector hint

**(b) Record it as a Phase 7 craft item.**

The round-2 functionality judgment remains correct: the hint is inside the `.st-inspector` scroll container and is reachable. The coordinator's 1440×900 observation changes the polish judgment, though—a line cut mid-glyph at the panel edge with no visible scroll cue reads as broken, not intentionally scrollable.

It does not block this increment because no control or information is lost and the defect belongs to the already-planned inspector craft work, not the token/motion foundation. Phase 7 should extend its inspector hierarchy item with a concrete acceptance check: at 1440×900 no text line may be bisected by the lower edge; collapsing advanced sections should remove the overflow where possible, and any remaining overflow needs a visible bottom-edge scroll affordance that disappears at the end. A permanent fade over text is not the fix.

## Test results

| Suite | Result | Evidence |
| --- | --- | --- |
| `cd web && npm test` | **Pass — 14 files, 322 tests** | Direct QA run completed in 4.64s. Existing jsdom `HTMLCanvasElement.getContext` warnings remain non-failing. |
| `python3 scripts/dev test fast` | **Pass** | Ruff passed; 68/68 contract tests passed; web repeated at 322/322. Report: `.local/test-results/fast-20260804T212919Z/report.json`. |
| `python3 scripts/dev smoke` | **Pass, coordinator-run** | Real-browser evidence supplied by the coordinator. I did not retry Chromium in this sandbox. |
| `git diff --check` | **Pass** | No whitespace errors. |

No app process was started during this verification; the fast suite's process contracts passed, and no QA-owned app process remains running.

## Sign-off

Increment 1—Phases 0–3 plus the Export rename—is complete and ready for the human. The highest remaining visible risk is the inspector's un-signaled overflow cutting a hint mid-line; it is now explicitly carried as Phase 7 craft work. Build the remaining Phase 4 north-star alignment next: demote Export, make Source visibly and semantically live, rewrite the no-render copy, and replace fabricated percentages with honest indeterminate phases.
