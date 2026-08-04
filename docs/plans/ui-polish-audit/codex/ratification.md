# Ratification of the Agreed UI Polish Plan — by codex
Status: PLAN

## Verdict

**RATIFIED WITH AMENDMENTS.**

The merged plan has the right product direction, preserves the hard-won conflict decisions, and is close enough to execution that it does not need another structural rewrite. The 13 amendments below are required before it becomes the build document: they correct concrete contrast math, restore six silently dropped or partial items, remove two sequencing contradictions, make two currently unimplementable rows honest, and replace verification theatre with checks that can catch regressions.

## Findings against the six checks

### 1. Completeness — FAIL

Six distinct issues from the source audits are absent or only partially represented. They are listed in “Dropped items I want restored” below. Most of the merge is impressively complete: every other item in my 74-row audit is either a build row, subsumed by a token/state row, or explicitly deferred. Claude's broader inventory is likewise preserved except for the chart mousemove coalescing and the setup sheen's linear easing.

The most consequential silent drop is my toolbar-wrap row. Phase 2 item 8 fixes one trigger—an unshrinkable title—but `.st-bar` and `.st-tools` still explicitly wrap (`styles.css:889-895`), so narrow widths can still change spatial memory and split the action groups.

### 2. Fidelity on conflicts — FAIL, with two localized regressions

The 14 conflict summaries accurately represent the cross-reviews: conflicts 1, 3, 5, 6, 8–12 follow my argument; 7, 13, and 14 follow Claude's; 2 and 4 are genuine merges. All five disputes from my cross-review are conceded correctly: no center-play crossfade, instant tool disclosure, no `color-mix()` cleanup mandate, fixed chart tooltip, and “Mission Control” left to product.

Two later rows undermine those settlements:

- Conflict 1 removes invented capability percentages in Phase 4 item 5, but Phase 7 item 8 later reserves width for the example `Installing… 12%`. That percentage should no longer exist.
- Conflict 2 says the toolbar rename to Export is settled, but Open question 2 reopens whether to keep Render. The plan must choose one executable vocabulary. The cross-review resolution was: **Export** for final user-facing bytes; Render/Re-render only for comp materialization and internal/backend language.

The claimed tally is defensible only if conflict 2 is counted as merged: nine codex-only wins, three Claude wins, and conflicts 2/4 merged. The individual winner cells themselves are accurate.

### 3. Executability — FAIL

The token foundation is specific, but several values and two feature rows are not yet executable as written.

- Token foundation `--border` (`#a09079`, plan lines 346-350) computes to **3.058:1 on `--panel`**, **2.907:1 on `--bg`**, and **2.761:1 on `--field`**. It therefore does not meet the plan's 3:1 promise wherever controls sit on the app background or use the new field fill. The suggested fallback `#ab9c85` is **2.641:1 on `--panel`**, not “~2.8:1.” `#98886f` is a viable warm replacement: 3.396:1 on panel, 3.229:1 on background, and 3.067:1 on field.
- The status-token comments slightly overstate their math: `#96382a` on `#fbeae6` is **6.220:1**, not 6.29; `#3b6ea5` on `#faf7f2` is **4.963:1**, not 5.02. Both still pass, but a document that says “computed, not estimated” must use the computed values.
- Phase 2 item 3 replaces unreadable disabled labels with `#a09488` on `--wash`, which is still only **2.541:1**. Use `--ink-dim`; it is 4.827:1 on `--wash` and keeps “Exporting…” readable while background/border/cursor communicate disabled state.
- Phase 4 item 3 assumes timeline cuts expose comp runtime, staleness, and a local re-render action. `StudioTimeline.jsx:353-370` currently has only the cut and its source/speed; the plan names no staleness field, receipt lookup, API, or mutator. This repeats the exact class of mistake caught in Conflict 1: prescribing UI for data that has not been shown to exist.
- Phase 5 items 1–2 call the existing `/frame` endpoint as though it were a free thumbnail service. `server/app.py:657-664` creates a new temporary JPEG and synchronously spawns ffmpeg for every request, with no cache, concurrency limit, or cleanup path shown. A repeating filmstrip across visible clips can fan out dozens of ffmpeg processes and temporary files.
- Phase 1 item 2's `:root { font-size: 100% }` is operationally a no-op in the current browser default and the token block then uses px sizes. Keep it only as explicit documentation; do not describe it as the fix for rem drift. The real fix is migrating declarations to the named type roles.

No render stage/percentage field has sneaked back into the plan. Conflict 1 and Phase 4 item 5 correctly use labeled indeterminate states because `server/render_jobs.py:66` and `server/app.py:639` expose status but no stage/total/pct.

### 4. Sequencing — FAIL

- Phase 7 item 1 deletes dead CSS **after** Phase 1 mechanically tokenizes it. This reverses Conflict 6's rationale that deletion should shrink the surface before token work. Move the dead block deletion to the start of Phase 1.
- Phase 1 says its mechanical migration has no visual change except contrast, but collapsing 41 sizes, 12 radii, 17 shadows, a spacing continuum, and several neutrals necessarily changes layout and appearance. Treat token families as separately reviewable migrations rather than one “safe” commit.
- Phase 3 item 5 converts all fake percentage fills to scaleX, then Phase 4 item 5 replaces those fake progress bars with indeterminate phases. Do not polish an implementation one phase before deleting it; move the honesty change before the progress-motion migration or scope scaleX only to bounded real progress.
- Phase 3 item 17 proposes rebuilding `.pulse` as a composited ring; Phase 7 item 10 then deletes the same redundant pulse. Delete it once and only convert the recorder ring.
- Phase 5 icon rows 6–13 have no dependency on posters, aspect ratios, or Phase 4. They are trivial, high-value RULES.md compliance and should be marked parallel-safe immediately after Phase 1 rather than stranded behind the product work.

### 5. Rubric and RULES.md compliance — FAIL

The plan correctly eliminates `transition: all`, non-composited progress motion, center-play animation, tool expansion animation, weak easings, ungated hover transforms, and over-300ms UI motion. It also protects authored video keyframes from the UI rubric, preserves preview/export parity, removes emoji, and keeps native render controls until parity exists.

Three amendments remain:

- Phase 3 item 7 still uses `busy ? 'instant' : 'smooth'` for automatic scrolling. A keyboard-submitted message or keyboard-selected thread can hit the smooth branch. Automatic message-driven scrolling must always be instant; smooth motion belongs only to an explicit pointer-initiated Jump to latest action, with keyboard activation instant.
- Phase 3 item 6 fixes the indeterminate slide easing but never says to change the setup sheen at `setup.html:35` from `ease-in-out` to `linear`. The merge says the sheen should keep 1.6s, but it silently drops the agreed easing correction.
- Phase 6 item 9 introduces arrow-key nudge editing without stating the repository rule it must follow. Nudge math/mutation belongs in `editor/interp.js` (or an existing pure mutator) with unit tests; `StudioTimeline.jsx` should only dispatch the command.

### 6. Verification quality — FAIL

The manual walkthroughs in Phases 2, 4, 6, and 7 are capable of exposing real failures. Several grep checks are not:

- Phase 1 checks `transition: all` but annotates “expect 0 after P3”; it cannot validate Phase 1 and belongs under Phase 3.
- Phase 3's `grep -c 'prefers-reduced-motion'` can pass with one empty/incomplete block, and `grep -c ':active'` can pass with five irrelevant selectors. Verify the named infinite animations and named pressable families instead.
- Phase 3's `grep -n 'transition: width\|margin-left'` can never reach zero because the app legitimately uses static `margin-left: auto` in many rules (`styles.css:505`, `:673`, `:898`, `:1329`). Search specifically for width transitions and the setup slide keyframe/animation.
- Phase 5 uses `grep -P`, which is not portable to the target macOS/BSD grep, and its Unicode ranges miss several named offenders (`⚠`, `⛶`, `⤓`, `✎`, `✕`) while broad ranges risk flagging legitimate multiplication/dimension punctuation. Use `rg` with the explicit semantic-glyph list and a separate close-button pattern.
- Phase 7's dead-CSS grep can pass while `.insp-*`, `.prev-*`, `.tl-clip`, `.tl-lane`, and `.tl-handle` remain. Match every dead prefix/selector family.
- No phase runs the repository's actual verification entrypoints or adds tests for new editing behavior. Per phase, run `scripts/dev test fast`; before final review run `scripts/dev test full` and `scripts/dev smoke`. Add focused component tests for modal focus/shortcut isolation and timeline keyboard semantics, plus pure-core tests for nudge.

## Required amendments

1. **Correct the audit count in the header.** Change “115 findings (claude)” to “116 detailed findings (claude).” Claude's detailed tables contain 116 issue rows; the old “96” in the source audit and “115” in the merge are counting errors.

2. **Make the token math true.** In Phase 1 item 3 and the token block, replace `--border: #a09079` with `--border: #98886f` and state its measured ratios: 3.396:1 on panel, 3.229:1 on background, 3.067:1 on field. Remove the `#ab9c85` fallback or label its actual 2.641:1 panel contrast. Correct the red/soft ratio to 6.220:1 and blue/background to 4.963:1. In Phase 2 item 3, replace disabled `color: #a09488` with `color: var(--ink-dim)` (4.827:1 on wash).

3. **Fix the foundation sequence and risk statement.** Move current Phase 7 item 1 (delete `styles.css:763-881`) to a Phase 1 preflight before counting or migrating tokens. Rewrite Phase 1's “intended visual change: none” claim to acknowledge that type, spacing, radius, shadow, and neutral migrations are intentional visual changes, and land each token family as a separately reviewable commit. Mark Phase 5 icon rows 6–13 parallel-safe immediately after Phase 1.

4. **Restore native-scheme and future-role focus coverage.** Add `color-scheme: light` to the Phase 1 root token row; keep `forced-colors` explicitly deferred. Replace Phase 2 item 4's selector with `:where(button, a, input, select, textarea, [tabindex]:not([tabindex="-1"])):focus-visible` so the Phase 6 `spinbutton`, `separator`, `option`, and tab controls receive the same ring.

5. **Restore stable toolbar geometry.** Add a Phase 2 row after item 8: remove wrapping from `.st-bar`/`.st-tools`, keep one line, and move lowest-priority actions into a labeled More/settings overflow at the width where groups no longer fit. Verify at the minimum supported Electron window width; title truncation alone is not the wrap fix.

6. **Restore the two partial asset-accessibility fixes.** Expand Phase 7 item 18 to add a visible/focusable drag affordance and `cursor: grab` on asset tiles while retaining the modal's explicit Add action. Add a Phase 6 row giving AssetModal Download, Previous, Next, and Close explicit `aria-label`s; P5's icon replacement does not itself create accessible names.

7. **Make the motion contract internally consistent.** Amend Phase 3 item 6 so both the setup indeterminate slide and sheen use `linear`, while the sheen stays at 1.6s pending visual review. Rewrite Phase 3 item 7 so every automatic scroll is instant and only a pointer-clicked Jump to latest may be smooth. Rewrite Phase 3 item 17 to convert only the recorder pulse to a transform/opacity pseudo-ring and move the redundant pipeline `.pulse` deletion there (then remove Phase 7 item 10).

8. **Restore the dropped chart-performance row.** Add a Phase 7 row at `LineChart.jsx:46`: coalesce `setHoverPx` to at most one update per animation frame and cancel a pending frame on leave/unmount. If the author prefers the profiling qualification from cross-review, state the measured threshold and defer the change explicitly when no multi-update frame is observed; do not silently omit it.

9. **Do not polish fake progress before removing it.** Move Phase 4 item 5 before Phase 3 item 5, or rewrite Phase 3 item 5 to apply only to real bounded setup progress. Rewrite Phase 7 item 8 to reserve width for stable status text (`Installing…`, `Installed`, `Failed`) with no numeric percentage example.

10. **Make the comp-staleness row honest and close the Export decision.** Move Phase 4 item 3 to Not doing/deferred unless the plan names an existing source of truth for comp classification, staleness, and local re-render plus the API/mutator that performs it. Keep the settled user vocabulary: toolbar/final bytes = **Export**; internal job and future comp materialization = **Render/Re-render**. Replace Open question 2 with a non-blocking future terminology audit rather than reopening the settled label.

11. **Bound the frame-extraction design before thumbnails ship.** Amend Phase 5 items 1–2 to require lazy loading, a concurrency cap, and a bounded cache keyed by project/source/source-mtime/time bucket/size before any timeline filmstrip or dashboard fan-out. The cached path must not leave one tempfile per request. If that backend/cache work is out of this polish pass, ship one cached poster per visible clip and defer repeating filmstrips. Make first-cut-at-about-1s the default so Open question 3 is not a build blocker.

12. **Honor the pure-core rule for keyboard editing.** Amend Phase 6 item 9: implement arrow-nudge as a pure mutator/helper in `web/src/editor/interp.js` (or reuse an existing tested mutator), add unit tests for bounds, immutable/same-ref no-ops, and one undo step, and keep `StudioTimeline.jsx` limited to key handling and dispatch.

13. **Replace verification theatre.** Move the `transition: all` check to Phase 3; replace count-only reduced-motion/active checks with assertions over the named selectors; replace the static-`margin-left` grep with `rg -n 'transition:\s*width|@keyframes\s+slide|animation:\s*slide'`; replace the macOS-incompatible emoji grep with explicit `rg` checks for `🗑|⏳|▶|⏸|⚠|⛶|⤓|■|✎|✕|＋|－` plus close-button `×`; and expand the dead-CSS check to `\.(editor|insp|kfe|prev)-|\.tl-(ruler|playhead|clip|lane|handle)`. Add `scripts/dev test fast` per phase and final `scripts/dev test full` plus `scripts/dev smoke`, with focused tests for modal focus/shortcut isolation and timeline nudge.

## Dropped items I want restored

| Dropped or partial issue | Original anchor | Where it belongs |
| --- | --- | --- |
| Explicitly declare the intentional light scheme; the Not doing table says it “should” happen but no build row does it | codex findings, Visual system — `web/src/styles.css:1`; `desktop/setup.html:12` | Phase 1 root token row; `forced-colors` may remain deferred |
| Toolbar groups can still wrap even after title truncation | codex findings, Hierarchy & layout — `web/src/styles.css:889`, `web/src/styles.css:895` | Phase 2, directly after the `.st-title` row |
| Asset drag-to-timeline needs a visible grab affordance and the explicit Add alternative, not only darker hint text | codex findings, Interaction feel — `web/src/studio/StudioAssets.jsx:49` | Phase 7 Assets row, paired with the promoted hint |
| AssetModal Download/Close/Previous/Next need explicit accessible names | codex findings, Accessibility — `web/src/components/AssetModal.jsx:51` | Phase 6 modal/control semantics |
| Raw LineChart mousemove should be coalesced to one update per frame, subject to the agreed profiling qualification | Claude findings, Motion — `web/src/components/LineChart.jsx:46`; codex cross-review Motion | Phase 7 performance/craft or explicit deferred row |
| Setup sheen must change from `ease-in-out` to `linear` while retaining the agreed 1.6s duration | Claude findings, Motion — `desktop/setup.html:35`; codex cross-review Motion | Phase 3 setup motion row |

## Sign-off

With the amendments above, I stand behind this plan as the joint output of both auditors; its highest-risk item is the Phase 5 filmstrip/poster work because the current `/frame` endpoint synchronously spawns uncached ffmpeg work and creates a fresh temporary JPEG for every requested frame.
