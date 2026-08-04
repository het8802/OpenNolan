# Cross-Review of codex — by claude
Status: PLAN

Reviewed: `docs/plans/ui-polish-audit/codex/findings.md` (84 findings + 8 exemptions = 92 items).
Compared against `docs/plans/ui-polish-audit/claude/findings.md` (115 findings).
Rubric: `~/.claude/skills/emil-design-eng/SKILL.md`. Conventions: `RULES.md`. Every DISPUTE below quotes
the cited line. Read-only pass — no application code touched.

## Verdict summary

| Verdict | Count |
| --- | ---: |
| AGREE | 28 |
| AGREE-BUT-SHARPEN | 9 |
| DISPUTE | 2 |
| DUPLICATE-OF-MINE | 53 |
| **Total items reviewed** | **92** |

Genuine conflicts requiring a decision before code: **6**.

**Honest assessment.** This is a strong audit and in several respects a better one than mine. Three things
stand out. First, **codex read the product document against the UI copy and I did not.** RULES.md says in
its own words "The user should NEVER have to hit Render just to see an edit… Render produces the final
exact-bytes MP4; it is not the way you preview an edit" — and codex found that `StudioToolbar.jsx:64` ships
`title="Render preview (render-once)"` and `StudioPreview.jsx:518` ships `'No render yet — hit Render.'`.
The UI actively teaches the behavior the north star forbids. I read the same two lines and filed the second
one as "weak empty state." That is the single most valuable finding in either report, and it is theirs.
Second, **their contrast math is more accurate than mine and more complete.** They have muted at 3.58:1
(I said 3.62), amber at 2.42:1 (I said 2.46), green at 3.51:1 (I said 3.54) — I recomputed all three and
codex is right each time; I had an arithmetic slip in the background luminance. More importantly they caught
**white-on-accent at 3.93:1**, which I missed entirely: every *enabled* primary button in the app fails AA,
not just the disabled ones I measured. Their counts are also more complete than mine (41 font-sizes because
they included the `px` declarations at `styles.css:21` and `:747`, where I counted only `rem` and said 35;
17 shadow recipes to my 10 because they counted rings and insets). Third, **codex correctly scoped the
rubric where I never thought to look.** Their exemption for authored keyframe presets at `model.js:173` —
`fade_out` uses `easing: 'ease-in'` and the presets run 350–500ms — draws the line between application
chrome and exported video content. Those values feed the FFmpeg render path, so "fixing" them would change
exported pixels and break `preview == export`. I never opened that function; had I, I would probably have
flagged it and been wrong.

Their weaker axes: **anchor precision** — they consistently cite the enclosing rule, comment, or function
rather than the offending declaration (`styles.css:255` for a transition on `:263`; `ChatPanel.jsx:35` for
the effect on `:38`; `setup.js:14` for the scroll on `:19`). Nothing is fabricated and every anchor lands in
the right construct, but the merged plan should use declaration-level lines. They also **under-specify some
fixes into non-actionability** ("emphasize identity/timing first", "restrained warm editorial template"),
and two of their proposed fixes are actively wrong (below). Finally, their report **misses the motion class
that hurts this app most**: un-animated *layout reflow*. `Studio.jsx:681` inserting `.st-notice` into normal
flow shoves the preview canvas and the entire timeline on every save; codex has no row for it.

---

## Per-finding verdicts

### Their Top 10

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| 1. No coherent focus system; dialogs are not real dialogs | `styles.css:41`, `App.jsx:493` | DUPLICATE-OF-MINE | My Top 10 #10 + 4 accessibility rows. `styles.css:41` (`input, select, button, textarea {`) is exactly the right insertion point for the global `:focus-visible` — better-chosen anchor than mine. |
| 2. Timeline objects are pointer-only | `StudioTimeline.jsx:327`, `Studio.jsx:691` | DUPLICATE-OF-MINE | My accessibility rows. They add splitters-as-controls (`Studio.jsx:691`), which I only measured as a 6px target and never treated as a keyboard control. Their framing is better. |
| 3. 75 hex, 41 font-sizes, 12 radii, 17 shadows, one radius token | `styles.css:1` | AGREE | Re-measured all four: 75 hex including `:root` (I reported 60 excluding it), 41 font-sizes including `px` (I said 35 — **I undercounted**), 12 radius scalars, 18 shadow declarations of which 17 are real recipes. Their numbers are the ones to use. |
| 4. Muted 3.58:1 and white/accent 3.93:1 both fail AA | `styles.css:2`,`:5`,`:7`,`:45` | AGREE | Verified: `--accent #c8643c` has relative luminance 0.2172, so white-on-accent = 1.05/0.2672 = **3.93:1**. **I missed this entirely** — I only measured the *disabled* primary at 1.41:1. Theirs is the more important number because it is the always-on state of every CTA in the product. |
| 5. Render is the only filled primary despite "edit live, render rarely" | `StudioToolbar.jsx:59`,`:64` | AGREE-BUT-SHARPEN | Best strategic finding in either report. Sharpen: renaming to "Export" alone is incomplete, because RULES.md carves out that Remotion/HyperFrames comp clips genuinely *do* require a re-render. See Conflict 2. |
| 6. Delete and keyframe clear have no confirm or visible undo | `Studio.jsx:373`, `StudioKeyframes.jsx:68` | DUPLICATE-OF-MINE | My Top 10 #8. Identical diagnosis and identical fix (undo toast, not confirm) — independently reached, which is good evidence it is right. |
| 7. No `prefers-reduced-motion` path | `styles.css:301`, `ChatPanel.jsx:38`, `setup.html:35` | DUPLICATE-OF-MINE | My motion row; measured 0 occurrences. |
| 8. Project cards use arbitrary rainbow HSL gradients | `App.jsx:229` | DUPLICATE-OF-MINE | My Top 10 #2. Their fix (real poster frame) beats mine (clamp the hue) — see Conflict 3. |
| 9. Browse failures become "empty folder"; upload has no busy state | `FolderBrowser.jsx:32`, `StudioAssets.jsx:34` | AGREE | **Miss of mine.** Verified `FolderBrowser.jsx:34` — `.catch(() => { if (alive) setEntries([]) })` — a 500 renders as "This folder is empty." I audited the empty state's *typography* and never asked whether it was lying. This is a state-modelling lens I did not apply anywhere. |
| 10. Studio still ships emoji/text-glyph controls | `StudioTimeline.jsx:263`, `StudioInspector.jsx:489` | DUPLICATE-OF-MINE | My Top 10 #3. I add which glyphs are *actually* color emoji on macOS (`🗑` U+1F5D1, `⏳` U+23F3, `▶` U+25B6, `⏸` U+23F8) versus merely wrong-weight text glyphs — that distinction sets the fix priority. |

### Their Motion section

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| `.q-option` uses `transition: all` | `styles.css:115` | DUPLICATE-OF-MINE | Declaration is on `:119`. Same fix. |
| `.option-btn` uses `transition: all` | `styles.css:194` | DUPLICATE-OF-MINE | Declaration is on `:199` (`:194` is the section comment). Same fix. |
| `.step .bullet` uses `transition: all 0.25s` | `styles.css:282` | DUPLICATE-OF-MINE | Declaration is on `:287`. I add the aggravating factor: `App.jsx:606` polls artifacts every 2s, so `all` re-fires on unrelated re-renders. |
| Render progress animates `width` for 400ms | `styles.css:255` | DUPLICATE-OF-MINE | Declaration on `:263`. Their `transform-origin: left` + scaleX fix matches mine exactly. |
| Capability progress animates `width` for 300ms | `styles.css:1336` | DUPLICATE-OF-MINE | Declaration on `:1338`. |
| First-run progress animates `width` for 350ms | `setup.html:28` | DUPLICATE-OF-MINE | Declaration on `:30`. |
| First-run indeterminate animates `margin-left` | `setup.html:38` | DUPLICATE-OF-MINE | `:38` applies the animation; `margin-left` is on `:40`. Both of us rank this near the top and we should — it runs layout every frame, forever, while the installer saturates the CPU. |
| No reduced-motion query while pulse/recorder/rise/sheen/slide run | `styles.css:301`,`:920`, `setup.html:35` | DUPLICATE-OF-MINE | Same three infinite animations identified. |
| Chat auto-scroll always uses `behavior: 'smooth'` | `ChatPanel.jsx:35` | DUPLICATE-OF-MINE | Effect is on `:38`. Their fix adds a "jump to latest" affordance, which is a genuine improvement on my bare `behavior: busy ? 'instant' : 'smooth'`. Take theirs. |
| Tile hover lift is not capability-gated | `styles.css:459` | DUPLICATE-OF-MINE | |
| Trim affordances appear only through ungated hover | `styles.css:1140`,`:1150` | DUPLICATE-OF-MINE | Completeness note: there are four such rules, not two — `:1142` (`.st-clip:hover`), `:1150` (`.st-ov:hover`), `:1162` and `:1163` (narration/music). |
| Global button hover uses a brightness filter with no hover query | `styles.css:45` | AGREE-BUT-SHARPEN | The sticky-hover-on-touch point is additive to my finding and correct. But gating the filter is the wrong fix — the filter should be **deleted**, not conditioned. `filter: brightness(1.06)` on `styles.css:47` is why 23 `filter: none` overrides exist across the file, and it promotes every element to its own compositing layer. Replace with semantic `--accent-hover` fills; then no media query is needed for a color change. |
| Buttons have hover but no shared `:active` | `styles.css:41`,`:901` | DUPLICATE-OF-MINE | My Top 10 #4. Their addition — *suppress the press scale on drag handles* — is a real refinement I did not state: `scale(0.97)` on `.st-clip` would fight the drag. Adopt it. |
| Built-in `ease` used for card and progress motion | `styles.css:463`,`:1338` | DUPLICATE-OF-MINE | Identical `--ease-out: cubic-bezier(.23,1,.32,1)` proposal. |
| Asset previews autoplay after arrow-key navigation | `AssetModal.jsx:31`,`:59` | **DISPUTE** (the fix) | The defect is real; the proposed axis is wrong. `AssetModal.jsx:57` and `:70` are the prev/next **buttons**, calling the same `prev()`/`next()` that the arrow keys call — so gating on "navigation came from the keyboard" fixes half the bug and leaves clicking `›` behaving differently from pressing `→` for one action. It also removes behavior the ICP wants: autoplay-on-step *is* how you flip through takes. The actual defect is at `AssetModal.jsx:59`, `<div className="al-media" key={item.url}>` — the `key` remounts the `<video autoPlay>` on every step, so a held arrow fires a burst of unmuted playback starts. Fix: debounce the mount ~150ms and start `muted` with a sticky unmute, for both input paths. |

### Their Visual system section

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| `:root` has colors and one radius but no spacing/type/shadow/easing scale | `styles.css:1` | DUPLICATE-OF-MINE | My "design system 2/10" row. Same token list proposed. |
| 75 unique hex literals; near-neutrals indistinguishable | `styles.css:1`,`:1341` | AGREE | Verified 75 including `:root`, 60 excluding. Their examples (`#f0ece4`, `#f6f2ec`, `#efe9df`, `#fbf7f0`) are the right ones to collapse. |
| 41 distinct `font-size` values, `0.58rem`–`3rem` | `styles.css:59`,`:718` | AGREE | **They are right and I was wrong at 35** — I counted only `rem` and missed `body { font-size: 14px }` (`:21`) and `.lc-axis { font-size: 9px }` (`:747`, `:748`). See "Missed by both" for why those `px` values matter more than a count. |
| ≥12 radius scalars plus one-off corner recipes | `styles.css:56`,`:362`,`:1330` | DUPLICATE-OF-MINE | I said 13 counting `50%` and `0`; their "at least 12" is correctly hedged. |
| 17 shadow recipes incl. three near-identical `0 2px 10px` tints | `styles.css:106`,`:219`,`:347` | AGREE | I reported 10 by counting only elevation shadows and excluding rings/insets; 18 declarations exist, 17 real recipes. Their number is the better governance figure. They also independently flagged `:219` — the `#c56a4914` shadow that is a near-miss of `--accent`. |
| Muted `#8a8178` is 3.58:1 / 3.76:1 | `styles.css:2`,`:3`,`:5` | AGREE | Recomputed: L(muted)=0.2246, L(`--bg`)=0.9326 → **3.58:1**. My 3.62 was wrong. Use theirs. |
| White-on-accent and accent-on-white are both 3.93:1 | `styles.css:7`,`:45` | AGREE | Verified 3.93:1. **My largest single miss.** Blast radius: every bare `<button>` (`styles.css:45`), `.st-primary`, `.al-add`, `.cap-btn`, `.q-custom-send`, `.ak-save`, `.update-toast-btn`, plus accent-on-cream text at `.linkish`, `.env-getkey`, `.art-icon`, `.sc-id` (3.72:1 on `--bg`). |
| Amber on amber-soft is 2.42:1 | `styles.css:11`,`:12`,`:959` | AGREE | Recomputed 2.419:1. My 2.46 was slightly off. Use theirs. |
| Green on green-soft is 3.51:1 | `styles.css:13`,`:14`,`:957` | AGREE | Recomputed 3.507:1. My 3.54 was slightly off. Use theirs. |
| Inputs and panels hard-code `#fff` instead of surface tokens | `styles.css:41`,`:314`,`:1024` | AGREE-BUT-SHARPEN | Tokenizing is right. But the sharper point is that the *value* does no work: `#fff` on `--panel #fffdf9` is a **1.007:1** difference, so the intended "raised field" lift is imperceptible — and with a 1.24:1 border the field has no visible boundary at all. Tokenize **and** re-value. See "Missed by both" row 1. |
| Focus styled only for asset tiles and scrub bars | `styles.css:349`,`:1037` | DUPLICATE-OF-MINE | Same two sites found. |
| Several fields remove outlines on `:focus` | `styles.css:125`,`:229`,`:1287` | DUPLICATE-OF-MINE | Declarations on `:129`, `:234`, `:1289`. Their fix — move the visual-only border change to `:focus-visible` and keep the outline — is cleaner than my "keep the outline or add an inset ring." Take theirs. |
| Disabled states are mostly opacity-only | `styles.css:46`,`:908` | DUPLICATE-OF-MINE | I add the measurement that makes it urgent: `.st-btn:disabled` (opacity `.4`) composed with `.st-primary:disabled` (`:911`, white on accent) yields a **1.41:1** label — and the Render button is disabled exactly while it reads "Rendering…". |
| No declared color-scheme; setup forces light | `setup.html:12`, `styles.css:1` | AGREE | New to me and correct. I exempted dark mode as a product decision and stopped there; I never considered that `color-scheme: light` on the web app would fix native scrollbar/form rendering, nor `forced-colors` support for macOS Increase Contrast. Both are real and cheap. |

### Their Hierarchy & layout section

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| Render is the only filled toolbar CTA | `StudioToolbar.jsx:59` | AGREE-BUT-SHARPEN | Verified `StudioToolbar.jsx:64`: `className="st-btn st-primary"` with `title="Render preview (render-once)"`. The tooltip literally calls Render a preview mechanism, which RULES.md forbids. Demote it. Sharpen the rename — see Conflict 2. |
| Source/Render looks segmented but has no group label or selected semantics | `StudioToolbar.jsx:59` | DUPLICATE-OF-MINE | My accessibility row on `.st-seg` and missing `aria-pressed`. Their addition — *make Source's live status explicit* ("Live") — is a product-copy improvement I did not have. Adopt it. |
| The project toolbar and every group can wrap | `styles.css:889`,`:895` | AGREE | New to me. Verified `.st-bar { flex-wrap: wrap }` (`:890`) and `.st-tools { flex: 1; flex-wrap: wrap }` (`:895`). Compounds badly with their `.st-title` finding below: `.st-bar-left` is `flex: none`, so an untruncated long project name cannot shrink and forces the whole toolbar to a second row. |
| Assets panel puts "Canvas background" before its own "Assets" heading | `StudioAssets.jsx:40` | AGREE | New to me and verified: `<aside>` at `:40`, `<BackgroundControl>` at `:41-44`, `<h3>Assets</h3>` at `:45`. The panel's first control belongs to project settings. |
| Inspector sections share one treatment; fields commit immediately | `StudioInspector.jsx:415` | AGREE-BUT-SHARPEN | The flat-hierarchy half is right — 7 schema types render through one `.st-sec` treatment with no progressive disclosure. But "show a subtle saved/live indicator" risks fighting the architecture: RULES.md specifies debounced ~700ms **autosave** as the shared source of truth, and a global indicator already exists at `Studio.jsx:663`. Do not add per-section save affordances; fix the one indicator (it is at 2.68:1 and sits three panels from the edit) and collapse advanced sections. |
| Preview's no-render empty state says "hit Render" | `StudioPreview.jsx:517` | AGREE | Copy is on `:518`: `'No render yet — hit Render.'` **This is the best catch in their report.** It teaches the exact behavior RULES.md prohibits, on the surface where the north star lives. I filed this line as a weak empty state and missed what it was actually saying. |
| Asset browse errors collapse to an empty array | `FolderBrowser.jsx:32` | AGREE | Verified `:34`. Loading/empty/error must be three states with retry. Miss of mine. |
| Upload has no local busy/progress tile | `StudioAssets.jsx:34` | AGREE | Verified: `handleFiles` calls `onUploadAsset` and nothing renders until the parent re-lists. For an ICP dropping multi-hundred-MB screen recordings, silence after drop reads as failure. Their optimistic-tile fix is right. Miss of mine. |
| Render and capability progress display fabricated percentages | `ChatPanel.jsx:150`, `CapabilityInstall.jsx:33` | DUPLICATE-OF-MINE | Same finding; **their fix is better than mine.** See Conflict 1 — I proposed wiring real progress and the backend does not have it. |
| Dashboard identity is an initial over random HSL | `App.jsx:228` | DUPLICATE-OF-MINE | Their poster-frame fix beats my hue clamp. See Conflict 3. |
| Four similarly quiet account/settings actions in one header row | `App.jsx:185` | AGREE | **They counted correctly and I did not** — I said three, omitting the conditional re-auth button at `App.jsx:186-191`. When connected there are four. Their settings-menu fix is right. |

### Their Interaction feel section

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| Delete commits instantly and clears selection | `Studio.jsx:373` | DUPLICATE-OF-MINE | |
| "clear" removes every keyframe in one click | `StudioKeyframes.jsx:68` | DUPLICATE-OF-MINE | Their "confirm only when more than one keyframe will be removed" is a nice graduation; I proposed a flat undo toast. Either works; theirs is more precise. |
| Trim handles invisible until hover, 9px wide | `styles.css:1140` | DUPLICATE-OF-MINE | Their "enlarge the hit area to ≥24px while keeping the visible grip narrow" is the correct formulation and better than my "widen to 11px." Take theirs. |
| SFX points are 12px and otherwise blank | `StudioTimeline.jsx:443` | AGREE-BUT-SHARPEN | Real: `StudioTimeline.jsx:394` sets `w = 12` and `:444-448` renders a childless `<div>`. Sharpen against RULES.md, which mandates the blankness: *"Timeline clip blocks carry no icon/emoji — … SFX point markers = a bare dot."* So the fix must not put a filename or icon **on** the marker. Legal: selection halo, larger invisible hit box, keyboard nudge, and identity in the properties panel. |
| Music gain is a 3px line with a 9px knob | `styles.css:1169` | DUPLICATE-OF-MINE | Their "visible dB/percent during drag" is the right addition — the value currently lives only in a native `title`. |
| Asset drag-to-timeline is explained only by a small hint | `StudioAssets.jsx:49` | AGREE | New to me. `:50` is a 0.68rem `--muted` hint at 3.58:1 — the discovery text for the app's most powerful gesture is near-illegible. |
| Dropzones are clickable `<div>` elements | `StudioAssets.jsx:86`, `App.jsx:1356` | AGREE | **Real miss of mine.** Verified `StudioAssets.jsx:87-95`: a `<div onClick>` with no role or tabIndex, wrapping `<input type="file" hidden>`. Sharpen the fix: use `<label>` with a **visually-hidden but focusable** input, not `hidden` — `hidden` removes it from the a11y tree, so the current markup has no focusable element at all. That makes upload natively keyboard-operable with zero JS. |
| Splitters are bare pointer-only `<div>`s | `Studio.jsx:691`,`:708`,`:732` | AGREE | I measured them as 6px targets but never treated them as controls. `role="separator"` + `aria-valuenow` + arrow-key resize + a collapse shortcut is right, and it also fixes the 18px reopen-tab dead end I did flag. |
| Backdrop click closes modal forms immediately | `App.jsx:298`,`:410`, `ConnectClaudeModal.jsx:57` | AGREE | Correct and important — a slight miss outside the card discards a pasted OAuth code. Interacts with my Escape requirement; see Conflict 4. |
| First-run log forces scroll to bottom every line | `setup.js:14` | AGREE | Scroll is on `:19`. Correct, and it partially corrects me — I cited this line as a *positive* exemplar. See Conflict 5. |

### Their Accessibility section

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| Modals lack `role="dialog"`, `aria-modal`, labelled title | `App.jsx:493`, `CapabilitiesModal.jsx:33`, `ConnectClaudeModal.jsx:57` | DUPLICATE-OF-MINE | Both measured 0 across 8 surfaces. |
| Non-asset modals have no Escape handling | `App.jsx:298`,`:410`, `ConnectClaudeModal.jsx:57` | DUPLICATE-OF-MINE | Both found `AssetModal.jsx:34` as the sole exception. |
| No modal traps or restores focus | `App.jsx:252`,`:493` | DUPLICATE-OF-MINE | |
| AssetModal has shortcuts but no semantics/trap/restore | `AssetModal.jsx:31`,`:47` | DUPLICATE-OF-MINE | Good nuance that shortcut shielding ≠ focus management. |
| Timeline clips and overlays are non-focusable divs | `StudioTimeline.jsx:327`,`:359` | DUPLICATE-OF-MINE | Same anchors, same fix. |
| Audio blocks and handles are non-focusable | `StudioTimeline.jsx:404`,`:427` | DUPLICATE-OF-MINE | Their "expose gain/trim as sliders when the block is selected" is more concrete than my generic listbox proposal. Adopt it. |
| Work-panel/activity segmented controls lack tab roles | `App.jsx:622`,`:739` | DUPLICATE-OF-MINE | |
| Timeline zoom range has no accessible name | `StudioTimeline.jsx:277` | DUPLICATE-OF-MINE | Input is on `:279`. Their `aria-valuetext` in px-per-second matches mine. |
| Icon-only asset modal controls rely on `title`/glyphs | `AssetModal.jsx:51` | DUPLICATE-OF-MINE | I had the glyph half under Craft and the missing `aria-label` only for `App.jsx:888`; they correctly generalize it. |
| Toasts are plain divs with no live-region semantics | `App.jsx:108`, `Studio.jsx:681` | DUPLICATE-OF-MINE | Their "preserve long errors until dismissed" converges with my finding that `Studio.jsx:98` never auto-dismisses `warn`/`err` — arrived at from opposite directions, same answer. |
| LineChart exposes only a generic image label | `LineChart.jsx:49` | DUPLICATE-OF-MINE | `role="img"` is on `:54`. Their screen-reader table + keyboard point-traversal is a better fix than my note that the data is inaccessible. |
| Clickable Activity file rows are divs | `App.jsx:823` | DUPLICATE-OF-MINE | Same anchor. |
| Targets below the 24px WCAG 2.5.8 minimum | `styles.css:1114`,`:1140` | DUPLICATE-OF-MINE | **Their standard citation is better than mine** — I asserted "28×28 minimum" from habit; WCAG 2.5.8 (Target Size Minimum, 24×24) is the actual normative floor. Use their framing. |

### Their Craft details section

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| Toolbar uses emoji/glyphs for split, duplicate, delete, arrange, play, zoom | `StudioTimeline.jsx:263` | DUPLICATE-OF-MINE | Buttons span `:263-280`. I add that `icons.jsx` has no `IconPause`/`IconTrash`/`IconScissors`/`IconCopy` yet, so the fix has a prerequisite. |
| Emoji warning/error text across chat and forms | `ChatPanel.jsx:84`,`:292`, `DebugReportModal.jsx:77` | DUPLICATE-OF-MINE | `⚠` appears at 11 sites; `IconAlert` is already imported in `ChatPanel.jsx:9` and used at `:102`, so the file contradicts itself. |
| AssetPanel uses glyphs while StudioAssets uses SVG | `App.jsx:1334`,`:1387` | DUPLICATE-OF-MINE | Independently found the same `.asset-play` divergence. Strong signal. |
| Playhead written as fractional `left` px every frame | `StudioTimeline.jsx:460` | AGREE-BUT-SHARPEN | Element is on `:461`. Finding is right and their shimmer diagnosis is a real symptom I did not name. But **drop the "round to device pixels" branch** — it keeps the per-frame layout *and* introduces visible stepping: `min="20"` (`:279`) means 1 CSS px = 50ms of timeline, so quantizing to device pixels holds the playhead still for a frame then jumps, stuttering against smooth audio at low zoom. `transform: translateX()` fixes both the layout thrash and the shimmer, because transforms rasterize on the compositor. |
| Splitters omit `user-select:none` and a drag-global cursor | `styles.css:970` | DUPLICATE-OF-MINE | Their addition of the drag-global cursor lock is right, and `body.st-scrubbing` (`styles.css:1063`) is the in-repo pattern to copy. |
| No scrollbar styling for many nested scroll regions | `styles.css:85`,`:1088` | DUPLICATE-OF-MINE | I add `scrollbar-gutter: stable` and the specific dark-pane problem (`.te-pre` `:188`, `.am-raw-pre` `:636` get light UA scrollbars). |
| Font sizes descend to `0.58rem`/`0.6rem` for badges | `styles.css:609`,`:722` | DUPLICATE-OF-MINE | Their "use weight/case/color before shrinking further" is the better prescription. |
| Studio project title has no truncation or min-width protection | `styles.css:891`,`:892` | AGREE | **Good catch, new to me.** Verified: `.st-title` (`:892`) has no `white-space`/`overflow`/`max-width`, and `.st-bar-left` (`:891`) is `flex: none` so it cannot shrink. Meanwhile `.pb-title strong` (`:442-443`) truncates correctly — the same app has two project-title treatments and only one works. |
| ~119 lines of dead old-editor CSS still ship | `styles.css:763`, `App.jsx:5` | DUPLICATE-OF-MINE | Same 119-line span identified. See Conflict 6 on whether the JS goes in the same pass. |
| Setup uses a separate set of near-match colors and sizes | `setup.html:14` | DUPLICATE-OF-MINE | Both found the four near-misses. |
| Setup percentage is an asymptotic timer, not measured work | `setup.js:74` | DUPLICATE-OF-MINE | Their "false precision is a craft defect" framing is sharper than mine. Note that `setup.js:38-54` *does* receive real `{pct, end, label}` step frames — so unlike the render bar, partial real progress exists here. |

### Their "Deliberately not changing" exemptions

| Their exemption (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| Cream/ivory + terracotta is the right direction; refine, don't replace | `styles.css:2`,`:7` | AGREE | Matches my exemption. |
| The preview stage should stay dark | `styles.css:988` | AGREE | Duplicate of my exemption, same reasoning (neutral media surround). |
| `transform-origin: center` on canvas overlay scale is content geometry | `StudioPreview.jsx:433` | AGREE | Duplicate of my exemption. Both of us correctly declined to apply the popover-origin rule here. |
| Authored keyframe presets (`ease-in` fade-out, 350–500ms) are export content | `model.js:173` | AGREE | **The single best judgment call in their report.** Verified `model.js:175`: `fade_out` → `easing: 'ease-in'`, and `:176-181` run 0.35–0.5s. These feed the FFmpeg path, so rewriting them changes exported pixels and breaks `preview == export`. I never opened this function and would likely have flagged it as a rubric violation. This exemption should be quoted verbatim in the merged plan as a scope boundary. |
| Tabular numerals are already correct on timecodes/ticks/durations | `styles.css:1013`,`:1083`,`:1139` | AGREE | Duplicate of my exemption; I listed 10 correct sites and 9 missing ones. |
| Main drag surfaces already use `user-select:none`, `touch-action:none`, grab cursors | `styles.css:1003`,`:1134`,`:1144` | **DISPUTE** | Their own first anchor contradicts the claim. `styles.css:1003` reads verbatim: `.st-clip-box { position: absolute; background: #000; overflow: hidden; cursor: grab; touch-action: none; }` — **there is no `user-select`**. Same at `styles.css:1169-1170`: `.st-aud-gain { … cursor: ns-resize; touch-action: none; }` — none either. Their other two anchors do have it (`:1136`, `:1146`), as does `.st-ov-canvas` (`:1006`). So 2 of the 4 drag surfaces are exempted as fine when they are not: dragging the main clip on the canvas selects the overlay text on top of it, and dragging the music gain line selects the lane label. Separately, **no** drag surface sets `cursor: grabbing` on `:active`, which their exemption also glosses over — the dead old editor did it correctly at `styles.css:869`. |
| Names/paths already truncate; fix only remaining title/toolbars after overflow tests | `styles.css:359`,`:605`,`:1210` | AGREE-BUT-SHARPEN | The three cited sites do truncate. But this exemption is in tension with their own Craft row on `.st-title`, and no overflow test is needed to know `flex: none` + no `max-width` overflows. Demote to: "asset/file/crumb truncation is correct — extend that pattern to `.st-title`," and drop the "after real overflow tests" gate. |
| Old editor JS is dead; remove CSS now, delete code in a separate reviewed cleanup | `App.jsx:5`, `editor/Editor.jsx:15` | AGREE-BUT-SHARPEN | Right that the CSS is the user-visible part and separable. But the evidence for deletion is already conclusive: a repo-wide grep for importers of `Editor/Timeline/Preview/Inspector/KeyframeEditor` returns **only the four self-references inside the dead cluster itself** — no component, no test, no HTML entry. `App.jsx:117` renders `Studio`. Deferring a 690-line deletion behind a second review is pure process overhead. See Conflict 6. |

---

## Missed by both

| Before | After | Why |
| --- | --- | --- |
| `:root` (styles.css:1-19) sets `font-family` but **no `font-size`**, while `body` sets `font-size: 14px` (styles.css:21) | `:root { font-size: 100% }` and an explicit `--text-*` scale; or move the base size to `:root` and keep `body` inheriting | `rem` resolves against the **root**, not `body` — so all 41 font-size values compute against the browser's 16px default while body copy is 14px. `.st-btn { 0.78rem }` is 12.48px, and `.md-body { 0.9rem }` is **14.4px, larger than body text**. The two type systems are decoupled and off by 14% from what anyone reading the CSS assumes. This is the *root cause* of the symptom we both reported as "41 sizes, no scale": there is no anchor to build a scale against. Fix this before tokenizing type, or the new scale inherits the same drift. |
| `.st-f input, .st-f select, .st-f textarea { background: #fff; border: 1px solid var(--line) }` (styles.css:1024-1025) inside `.st-inspector { background: var(--panel) }` (styles.css:1017) | `--field-bg: #fdfaf4` (recessed, ~1.5% below panel) + `--border: #d6cbba`, or an inset shadow instead of a border | `#fff` against `--panel #fffdf9` is a **1.007:1** difference — imperceptible. Combined with a 1.24:1 border, every text, number, and select field in the properties panel has **no visible boundary of any kind**. The user cannot see where a field starts. Codex flagged `#fff` as an untokenized literal and I flagged `--line` as too light; neither of us noticed that together they erase the field affordance entirely on the app's densest input surface. |
| `api.js:151` exports `frameUrl` for the working ffmpeg still endpoint (`server/app.py:646`) and **zero components call it**; `.st-clip` is a flat `--blue-soft` rectangle showing a filename and a duration (styles.css:1134-1139) | Render a repeating filmstrip of `frameUrl(id, cut.source, t)` stills as the `.st-clip` background; reuse the same endpoint for dashboard tile posters | The app already has end-to-end still extraction — path-traversal-protected, ffmpeg-backed, `<img>`-able — and never uses it. Clip thumbnails are the single biggest "this is a real NLE" signal for an ICP coming from CapCut or Instagram Edits, and identifying a clip by basename in a 46px block is the daily friction of using this editor. It also settles Conflict 3: the poster codex wants for dashboard tiles needs no new backend and no prior render, so it does not violate "render rarely". |
| `.st-aud.pt { border-radius: 50% }` (styles.css:1160) on a block that is `height: 20px` (styles.css:1154) and `width: 12` (StudioTimeline.jsx:394) | `height: 12px; width: 12px` for point markers (or a pin/diamond shape), vertically centered in the lane | `border-radius: 50%` on a 12×20 box is an **ellipse**, not a dot. RULES.md calls for "a bare dot" and the app draws a squashed oval. Codex correctly flagged the SFX marker as undiscoverable and I flagged its blankness; neither of us noticed it is the wrong shape. |
| `.msg.user { max-width: 95%; background: var(--accent-soft) }` vs `.msg.assistant { width: 100%; background: #f6f2ec }` (styles.css:137-139) | Give user messages a real inset (`max-width: 80%`) and widen the tint gap, or drop the assistant bubble entirely and let it sit on the panel | In the editor the agent panel defaults to 340px wide (`Studio.jsx:70`), so a 95%-wide user bubble is 5% narrower than the assistant's. The only other cue is `#f4e3da` vs `#f6f2ec` — two pale warm tints ~2% apart in luminance. Speaker attribution in the ICP's primary conversation surface rests on differences at the edge of perception. |

---

## Conflicts to settle

### 1. Progress bars: wire up real progress, or go indeterminate?

**Their position.** Fabricated percentages must become "labeled indeterminate phases until real byte/stage
progress exists" (`ChatPanel.jsx:150`, `CapabilityInstall.jsx:33`, `setup.js:74`).

**My position (round 1).** "Feed the real signal: Studio already polls `getRenderStatus` (`Studio.jsx:272`);
pass stage/total into `RenderProgress`."

**Who should win and why. Codex wins, and my row was factually wrong.** I checked the backend:
`server/app.py:639` documents the response as `{status: queued|running|done|failed, output_path?, error?}`,
and `server/render_jobs.py:66` constructs the job as
`{"job_id", "project_id", "status": "queued", "origin"}` with `status()` (`:119-122`) returning a plain copy.
**There is no stage, no total, no percentage.** The signal I told them to "just pass through" does not exist,
so my fix was unimplementable as written. Ship codex's fix: indeterminate with a phase label, no invented
number, everywhere. One refinement in my favor: `setup.js:38-54` *does* receive real `{pct, end, label}`
frames, so first-run should show real step-boundary progress plus an indeterminate shimmer between
boundaries — not pure indeterminate. And as a follow-up (not a polish-pass blocker), `render_jobs.py:326`
already exposes `update(**fields)`, so adding `{scene_done, scene_total}` is a small change that would let
the render bar say "scene 3 of 7" — which is precisely the "only changed scenes re-render" promise the north
star makes. Sequence: indeterminate now, real scene counts next.

**What would change my mind.** Evidence that `render_proxies` cannot cheaply know its scene total up front —
then indeterminate is permanent for render and codex's position wins outright with no follow-up.

### 2. Is "Render" the wrong name, or just the wrong weight?

**Their position.** Make Source the default truth, **rename the terminal action to Export**, and style it as
a completion action rather than the editor's constant CTA (`StudioToolbar.jsx:59`, `:64`).

**My position (round 1).** I accepted Render-as-primary and only measured its disabled label at 1.41:1.

**Who should win and why. Codex wins on the diagnosis and on demotion; their rename needs one carve-out.**
The diagnosis is unarguable — `StudioToolbar.jsx:64` ships `title="Render preview (render-once)"` and
`StudioPreview.jsx:518` says `'No render yet — hit Render.'`, both teaching the behavior RULES.md forbids in
so many words. Demote `.st-primary` off that button: agreed. But "Export" cannot be the whole answer,
because RULES.md carves out a second, legitimate meaning: *"Only Remotion/HyperFrames composition clips
require a re-render… and even then only the changed comp re-renders."* One button currently serves both
"give me final bytes" and "materialize pixels FFmpeg cannot arrange," and renaming it Export strands the
second case with no affordance. Settle as: **rename the toolbar action to "Export"** and make it secondary;
**add a per-clip staleness badge** on comp clips in the timeline with a local "Re-render this comp" action;
**rewrite both copy strings** so Source is described as the live editable preview. That keeps the north star
and the render-once architecture both expressible.

**What would change my mind.** If comp clips are already auto-re-rendered on edit without user action, the
carve-out is unnecessary and codex's plain rename wins. I did not verify the comp-staleness path end to end.

### 3. Dashboard tile identity: real poster frame, or a constrained palette?

**Their position.** "Prefer a real frame/poster; fall back to a restrained warm editorial template"
(`App.jsx:228`).

**My position (round 1).** Clamp the hash to a warm band: `hsl(${14 + (h % 40)} 44% 66%)`.

**Who should win and why. Codex wins outright, and the evidence is stronger than they knew.** I argued for
the hue clamp on cost grounds — one line, no backend. That reasoning collapses because
`server/app.py:646` `/api/projects/{id}/frame` **already exists** and `api.js:151` already exports
`frameUrl`, with zero callers. A poster frame therefore needs no new endpoint, and it can come from the first
cut's *source* rather than a render, so it does not violate "render rarely." A real frame is both on-brand
and informative — it tells the founder which reel this is, which a hashed initial never can. My hue clamp
survives only as the fallback for a project with no cuts yet.

**What would change my mind.** If `/frame` is too slow to be called per tile on a dashboard with 30 projects
(it spawns an ffmpeg process per request, uncached — `server/app.py:659-664` writes to a temp file every
time). If measurement shows that, the answer is a cached poster written once at project creation, still
codex's direction, not my hue clamp.

### 4. Backdrop-close protection vs Escape-everywhere

**Their position.** Ignore backdrop close once fields are dirty, or confirm before discarding
(`App.jsx:298`, `:410`, `ConnectClaudeModal.jsx:57`).

**My position (round 1).** Add Escape handling to all 8 modals.

**Who should win and why. Both, but they must be one policy, and that is the decision.** As separately
stated the two recommendations conflict: if Escape always closes, the pasted OAuth code is still lost, and
codex's dirty-guard only covers the pointer. Route **every** dismissal gesture — backdrop, Escape, and the
close button — through a single `requestClose()` in the shared dialog shell. `requestClose()` closes
immediately when the form is pristine; when dirty it confirms (or no-ops for the backdrop specifically,
which is the least intentional of the three). Anything else produces a dialog where two gestures that look
identical to the user behave differently.

**What would change my mind.** Nothing on the architecture. On the tuning: if the dirty-confirm proves
annoying on the low-stakes Feedback modal, scope the guard to modals holding unrecoverable input (OAuth
code, API keys) and let the rest close freely.

### 5. `setup.js` log auto-scroll: exemplar or defect?

**Their position.** The log forces scroll to bottom on every line; auto-follow only near the bottom and
offer "Jump to latest" (`setup.js:14`).

**My position (round 1).** I cited `setup.js:19` as a **positive** example — the correct instant-scroll
counterexample to `ChatPanel.jsx:38`'s smooth scroll.

**Who should win and why. Codex wins; my framing was too generous.** Two different properties of one line.
`log.scrollTop = log.scrollHeight` is right about *behavior* (instant, not smooth) and wrong about
*condition* (unconditional). A user who scrolls up to read the error that just flew past is yanked back on
the next line — during a multi-minute install, which is exactly when they need to read. The stickiness
pattern already exists in-repo at `ChatPanel.jsx:41-45` (`stickRef`, an 80px threshold) and should be copied
verbatim. My "exemplar" status narrows to the behavior choice alone, and I should have caught the missing
guard given I had just read the ChatPanel version.

**What would change my mind.** Nothing.

### 6. Dead old editor: delete the JS now, or defer to a separate review?

**Their position.** Remove the shipping CSS now; delete the JS "only in a separately reviewed cleanup if
repository history/tests confirm no alternate entrypoint" (`App.jsx:5`, `editor/Editor.jsx:15`).

**My position.** Delete all five components (690 lines) and `styles.css:763-881` in the same pass.

**Who should win and why. I win, because the confirmation they ask for is already done.** A repo-wide grep
for importers of `Editor`, `Timeline`, `Preview`, `Inspector`, and `KeyframeEditor` returns exactly four
hits, all self-references inside the dead cluster (`editor/Editor.jsx:3,4,5` and `editor/Inspector.jsx:2`).
No component, **no test**, and no HTML entry references them; `App.jsx:117` renders `Studio`. Their caution
is the right instinct with no evidence, but the evidence exists, and deferring costs a real cleanup that
shrinks the design-system surface everyone is about to tokenize — 7 of the 12 radii and a chunk of the 41
font sizes live in that dead block. Codex is right about one thing: the CSS and JS are separable, and the CSS
is the only part users pay for. So if the change must be split for reviewability, split it that way — CSS
first — but both in the same pass.

**What would change my mind.** A build config, docs page, or Storybook-style harness that loads
`editor/Editor.jsx` outside the `main.jsx` graph. I grepped `*.js`, `*.jsx`, and `*.html` and found none,
but I did not read `vite.config.js` for a second entry point.

---

## My concessions

Plainly, where codex was right and I was not:

1. **White-on-accent 3.93:1 — my biggest miss.** I measured the *disabled* primary at 1.41:1 and never
   checked the enabled state. Every primary button in the app fails AA at rest, which is a far bigger deal
   than the disabled case, and I walked past it.
2. **My contrast numbers were less accurate than theirs, three times.** Recomputed: muted is 3.58:1 (I said
   3.62), amber-on-amber-soft 2.42:1 (I said 2.46), green-on-green-soft 3.51:1 (I said 3.54). I had an
   arithmetic error in the background luminance. Their figures go in the merged plan.
3. **My font-size count (35) was wrong; theirs (41) is right.** I counted only `rem` and missed
   `body { font-size: 14px }` (`styles.css:21`) and the two `9px` SVG axis labels (`:747-748`). Ironically
   those `px` values led me to the deeper rem-base finding above — which I only found because their number
   forced me to recount.
4. **My shadow count (10) was narrower than theirs (17).** I counted only elevation shadows and silently
   excluded rings and insets. For a governance number, theirs is the useful one.
5. **My render-progress fix was unimplementable.** I told them to pass stage/total from
   `getRenderStatus`; `server/render_jobs.py:66` proves no such field exists. Codex's indeterminate fix is
   the correct one for today. This is the concession I'd most want caught before anyone wrote code.
6. **I cited `setup.js:19` as a positive exemplar when it has a real defect.** Instant scroll: right.
   Unconditional scroll: wrong. I had just read `ChatPanel.jsx:41-45`'s stickiness guard in the same session
   and still failed to notice it was missing here.
7. **I said the dashboard header has three secondary actions; there are four.** I omitted the conditional
   re-auth button at `App.jsx:186-191`.
8. **I never scoped authored video content out of the motion rubric.** Codex's `model.js:173` exemption is
   the best judgment call in either report. I did not open `presetKeyframes`, and had I, I would likely have
   flagged `easing: 'ease-in'` and the 350–500ms durations as violations — a "fix" that would have changed
   exported pixels and broken `preview == export`.
9. **I missed four real findings in their report entirely**: keyboard-inaccessible dropzones
   (`StudioAssets.jsx:87`), browse errors masquerading as empty folders (`FolderBrowser.jsx:34`), uploads
   with no busy state (`StudioAssets.jsx:34`), and `.st-title` with no truncation inside a `flex: none`
   group (`styles.css:891-892`). The first three are all the same lens — modelling loading/empty/error as
   distinct states — which I did not apply anywhere in my audit.
10. **I lost the north-star argument.** They found that the UI's own copy contradicts RULES.md's north star
    in two places; I read both lines and filed one as a typography problem. Reading the product document
    against the interface copy is a discipline I skipped.
11. **My target-size floor was invented.** I asserted "28×28 minimum" from habit; codex cites WCAG 2.5.8
    (24×24), which is the actual normative requirement.
12. **Several of their fixes are simply better formulations of findings we shared**: hit-area ≥24px with a
    narrow visible grip (vs my "widen to 11px"); move focus border changes to `:focus-visible` (vs my "keep
    the outline"); suppress `:active` scale on drag handles (which I would have broken); expose gain/trim as
    sliders on selection; and add a "jump to latest" affordance to the chat scroll fix.
