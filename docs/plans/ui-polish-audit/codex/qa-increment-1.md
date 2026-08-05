# QA Report — Increment 1 (Phases 0-3)
Status: QA

## Verdict

**FAIL**

The implementation does not yet satisfy the ratified Phase 0–3 contract. Five acceptance-blocking defects are present in the code: the spacing foundation was defined but never consumed, opacity-based disabled states still defeat the contrast fix, press timing is missing on the exact controls requested for verification, the motion contract still contains a 400ms layout transition and an ungated hover transform, and the project toolbar still explicitly wraps with no labeled overflow.

Live visual certification was also blocked by the execution environment. `python3 scripts/dev run` could bind ports 20905/20906 only in an escalated process; the workspace sandbox could not connect to those ports, and the approval service rejected the browser escalation because it reported a usage-limit failure. A second attempt loaded the current `web/dist` bundle under a fully routed mock origin (script: `.local/ui-qa/increment1-qa.js`), but Chromium itself was denied its macOS Mach rendezvous port before creating a page. I therefore do not claim a screenshot, live-computed-style, geometry, or aesthetic verdict I did not observe.

## Screenshots

No screenshots were produced. The failures occurred before a browser page existed, so there are no paths to list.

| Path | Surface | Viewport | What it shows |
| --- | --- | --- | --- |
| — | Dashboard, create modal, project tile, Studio, assets, chat, notices | 1440×900 and 960×600 | Not captured: localhost was unreachable from the sandbox and headless Chromium was denied its Mach rendezvous port. |

The reusable routed-bundle QA driver is at `.local/ui-qa/increment1-qa.js`; once browser execution is available it captures every requested surface, computed tokens/contrast, pressed states, reduced motion, layout deltas, and 960px toolbar geometry into `.local/ui-qa/`.

## Verification

| Check | Method | Result | Evidence |
| --- | --- | --- | --- |
| 1. Tokens landed | Stylesheet inspection, exact-value comparison with the plan, and token-use counts via `rg`. Live `getComputedStyle` was attempted but blocked before Chromium opened. | **FAIL.** `--border: #98886f`, `--ink-dim: #6f665c`, `--field: #f6f1e8`, `--accent-ink: #a44a26`, and the type/radius/elevation/motion values are present. However `--muted` is `var(--ink-dim)`, not the required `#8a8178`, and all seven spacing tokens have **zero consumers**. | Token definitions: `web/src/styles.css:1-100`; mismatch at `web/src/styles.css:36` versus plan “Token foundation”; spacing declarations at `web/src/styles.css:61-62`, with zero `var(--space-*)` uses. The plan explicitly says the token block lands “plus a migration of the declarations each token replaces.” |
| 2. Contrast | WCAG sRGB formula applied to the actual CSS values and cascade; live DOM sampling was blocked. | **FAIL.** Planned pairs compute correctly: white/`--accent-ink` 5.856:1; `--ink-dim`/panel 5.540:1; `--border`/field 3.067:1; amber/green/red/blue ink on soft fills 4.835/5.698/6.220/5.265:1. The generic disabled pair is 4.827:1, but later opacity overrides composite disabled labels to about **1.68–2.48:1**, and `.chip.off` similarly fades meaningful status text. | Correct base state at `web/src/styles.css:144-149`; defeating overrides at `web/src/styles.css:127`, `:254`, `:379`, `:574`, `:1000`. On a panel, the 0.4/0.5/0.55/0.6 opacity cases compute to about 1.68/1.94/2.10/2.28:1 against their composited control fills (and at most 1.77/2.07/2.27/2.48:1 directly against the panel). |
| 3. Visible control edges | Source inspection and formula only; screenshot judgment unavailable. | **PARTIAL PASS, NOT VISUALLY CERTIFIED.** Interactive controls are broadly migrated to `--border`, and `#98886f` clears 3:1 on panel/background/field. Whether it reads too muddy or heavy in the warm palette cannot be responsibly judged without a screenshot. | `web/src/styles.css:40-47`, `:129-149`, `:945-965`. Computed formula: 3.396:1 on panel, 3.229:1 on app background, 3.067:1 on field. |
| 4. Press feedback | Selector/cascade inspection; mousedown/computed-style capture was scripted but Chromium could not launch. | **FAIL.** `.st-btn` has the correct `transform var(--dur-press)` transition, but the requested generic button and `.st-play` only inherit an active transform with **no transform transition**. `.tile` transitions transform with `--dur-hover` (140ms), not `--dur-press` (120ms). | Generic press at `web/src/styles.css:137-139`; tile transition/active at `web/src/styles.css:604-615`; Studio button contract at `web/src/styles.css:945-957`; play control at `web/src/styles.css:993-996`. |
| 5. Reduced motion | Source inspection of both media queries and infinite-animation sites; reduced-motion browser context could not launch. | **SOURCE PASS, RUNTIME UNVERIFIED.** Recorder motion is disabled, the deleted pipeline pulse is absent, and setup sheen/slide are stopped. Movement/scale feedback is removed while state colors still change (instantaneously). | Web query at `web/src/styles.css:1421-1448`; recorder at `web/src/styles.css:969-983`; setup query at `desktop/setup.html:57-61`; setup infinite animations at `desktop/setup.html:44-53`. |
| 6. Layout jump | JSX/CSS positioning inspection; `getBoundingClientRect` before/after was scripted but not executed. | **SOURCE PASS, RUNTIME UNVERIFIED.** Notices are now children of the positioned toolbar and `.st-alerts` is absolute at `top:100%`, so it should overlay rather than participate in the Studio flex flow. The required measured delta remains outstanding. | `web/src/studio/Studio.jsx:665-691`; `web/src/styles.css:930-931`, `:1008-1016`. |
| 7. Motion contract | Full grep of `styles.css` and setup CSS. | **FAIL.** No `transition: all` survives and no `ease-in` is used on UI chrome. However `.rp-fill` still transitions layout-bound `width` for **400ms with bare `ease`**, `.cap-bar` still transitions width with bare `ease`, several transitions hardcode 120/150ms instead of tokens, and `.tile-new:hover` applies a transform outside the pointer/hover media query. | `web/src/styles.css:395`, `:481`, `:494`, `:635`, `:971`, `:1416`. The correctly gated base tile lift is at `web/src/styles.css:612-614`, but the later `.tile-new:hover` rule reintroduces the transform for coarse pointers. |
| 8. Toolbar at 960px | CSS/JSX inspection; viewport geometry capture was scripted but Chromium could not launch. | **FAIL BY CONTRACT.** `.st-bar` still says `flex-wrap: wrap`; `.st-tools` hides an overflowing horizontal scroller; no labeled More menu exists. Even if the current short mock title happens to fit, long project names can still move the toolbar to a second line and important right-side actions become discoverable only by invisible horizontal scrolling. | `web/src/styles.css:930-939`; toolbar has only fixed groups at `web/src/studio/StudioToolbar.jsx:17-68`. This is the exact behavior Phase 2 item 9 required removing. |
| 9. Regressions | Source review, `git diff --check`, Vitest, and repository fast suite. Visual inspection unavailable. | **NO RENDER CRASH FOUND, VISUAL REGRESSIONS UNVERIFIED.** The component suite mounts Studio successfully and all tests pass. The five contract defects above remain. | `git diff --check` is clean. `web` reports 14/14 files and 321/321 tests. Fast suite passes Ruff, 68 contract tests, and the same web suite. |
| 10. Test suites | Ran `npm test` in `web/`, then `python3 scripts/dev test fast` after stopping the QA stack. | **PASS.** Claude’s reported 14 files / 321 tests reproduces exactly. Fast passes all three actions. The first fast attempt failed only because the intentionally running QA stack made `test_stop_is_idempotent_when_nothing_is_running` inapplicable; after terminating the run session, the required rerun passed. | Latest passing report: `.local/test-results/fast-20260804T202932Z/report.json` — Ruff passed, 68 Pytest contract tests passed, 321 Vitest tests passed. Direct Vitest duration: 4.69s. |

## Findings for claude

### Blocking

1. **Claude, complete the spacing migration; the spacing scale currently has zero effect.** The seven tokens exist at `web/src/styles.css:61-62`, but there is not one `var(--space-*)` consumer in the stylesheet. I observed this with an exhaustive `rg` token-use count, not a sample. Replace the live spacing declarations that Phase 1 mapped onto the 4/8/12/16/24/32/48 scale; do not leave the scale as documentation. Add a check that each token intended for production has at least one named consumer and that no unmapped near-duplicate gaps/paddings remain.

2. **Remove every opacity override on disabled or meaningful status text.** The base fix at `web/src/styles.css:144-149` is correct, but `.q-custom-send:disabled` (`:254`), `.ak-save:disabled` (`:379`), `.update-toast-btn:disabled` (`:574`), and `.st-seg:disabled` (`:1000`) reapply opacity after it. `.chip.off` at `:127` also fades a runtime/status label to an estimated 2.27:1 against the panel. Delete the opacity declarations and let fill, border, cursor, and `--ink-dim` carry disabled state exactly as the plan specifies.

3. **Finish press feedback on the three acceptance selectors.** The generic rule at `web/src/styles.css:139` changes transform but has no transform transition; `.st-play` at `:993-996` has the same problem; `.tile` at `:609-615` uses the 140ms hover duration for its active transform. Add a named `transform var(--dur-press) var(--ease-out)` transition to generic pressables/play, and split tile transform timing so active uses `--dur-press` while hover lift remains `--dur-hover`. Preserve the drag-surface exclusions.

4. **Close the remaining motion-contract holes before calling Phase 3 complete.** `.rp-fill` at `web/src/styles.css:395` still animates width for 400ms with bare `ease`; `.cap-bar > i` at `:1415-1416` also animates width with bare `ease`; `.tile-new:hover` at `:635` escapes the hover/pointer gate. The agreed plan intends the fake progress bars to be removed in Phase 4, but this QA gate explicitly requires no UI duration over 300ms and Phase 3 says bare `ease` is retired. Either bring the Phase 4 deletion forward for these two bars or make the interim state contract-compliant; move the entire `.tile-new:hover` transform rule into the existing fine-pointer media block.

5. **Implement the ratified minimum-width toolbar behavior, not an invisible scroller.** `web/src/styles.css:930-939` retains `flex-wrap: wrap` on `.st-bar` and hides the scrollbar on an overflowing `.st-tools`; `web/src/studio/StudioToolbar.jsx:17-68` has no More overflow. Remove wrapping, keep the project identity on the same line, and move the lowest-priority group into a labeled More menu at the measured fit threshold. Save and the terminal action must remain visibly reachable at 960px without horizontal scrolling.

### Polish

1. **Make the token block match the ratified values exactly.** `--muted` is `var(--ink-dim)` at `web/src/styles.css:36`, while the plan defines `--muted: #8a8178` for decoration. The new `--muted-decor` name is defensible fail-safe engineering, but it silently changes the contract the QA prompt explicitly asked to compare exactly. Either restore the agreed name/value and keep all text migrated to `--ink-dim`, or amend the plan before implementation; do not leave code and plan disagreeing.

2. **Replace remaining hardcoded transition durations with motion tokens.** `web/src/styles.css:481`, `:494`, and `:971` use `0.12s`/`0.15s` and default easing instead of the named curve/durations. Use `--dur-press` or `--dur-hover` plus `--ease-out` as appropriate so one future motion tuning pass actually changes the whole app.

## Aesthetic assessment

I cannot certify that the app genuinely looks good because no screenshot or browser frame was available. The source-level changes point in the right direction—warm surface tokens, stronger secondary ink, coherent radii, and real field boundaries—but a visual judgment about whether `#98886f` looks crisp or muddy would be fiction without seeing it rendered.

The result is also guaranteed not to read as fully premium yet, even if the new foundation looks good: the dashboard still generates arbitrary HSL covers (`web/src/App.jsx:237-244`) and the timeline toolbar still mixes `✂`, `⧉`, `🗑`, `⇅`, `▶`/`⏸`, and fullwidth `＋`/`－` glyphs (`web/src/studio/StudioTimeline.jsx:263-280`). Those are scheduled for later phases rather than defects in this increment, but they are the strongest source-backed candidate for the single ugliest thing still on screen. There is no screenshot path to attach, so this is explicitly a source inference, not the requested visual finding.

The next QA pass must rerun `.local/ui-qa/increment1-qa.js` in an environment that permits Chromium. Until it produces the full screenshot matrix and live measurements, “warm-editorial,” hierarchy, edge weight, optical alignment, and the single-ugliest-element call remain unsigned.

## Test results

| Suite | Claude baseline/report | QA run | Result |
| --- | --- | --- | --- |
| `cd web && npm test` | 14 test files, 321 tests passing | 14 test files, 321 tests passing in 4.69s | Reproduced exactly. The existing jsdom `HTMLCanvasElement.getContext` warnings still print but do not fail tests. |
| `python3 scripts/dev test fast` | Passing: Ruff + contract tests + web tests | Passing on final run: Ruff; 68/68 Pytest contract tests; 321/321 Vitest tests | Reproduced. Report: `.local/test-results/fast-20260804T202932Z/report.json`. |
| First fast attempt while QA stack was intentionally running | Not applicable | 1 contract failure: “stop is idempotent when nothing is running” | Harness state, not an application regression. After the owned run session was terminated, the suite passed cleanly. |

`python3 scripts/dev stop` was invoked as required; the sandbox could not run its internal `ps`, so I also terminated the exact owned `scripts/dev run` session directly. The subsequent idempotent-stop contract test passed, confirming no worktree app process remained.
