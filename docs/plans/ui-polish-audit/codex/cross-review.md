# Cross-Review of claude — by codex
Status: PLAN

## Verdict summary

| Verdict | Count |
| --- | ---: |
| AGREE | 38 |
| AGREE-BUT-SHARPEN | 18 |
| DISPUTE | 5 |
| DUPLICATE-OF-MINE | 55 |
| **Total detailed findings reviewed** | **116** |

Claude's audit is unusually strong: specific, well-anchored, and much sharper than mine on whole-editor layout shifts, the near-invisible interactive border, loading/empty-state hierarchy, and small bugs such as the phantom project-title gap. Its weakest tendency is to prescribe animation wherever a state changes, even when the Emil frequency rule says a frequent or keyboard-triggered action should remain instant, and to turn some maintainability preferences into user-facing defects. The report says “96 findings,” but its six detailed tables contain 116 issue rows; the verdict total above covers every detailed row once. The duplicated Top 10 entries are covered by their corresponding detailed rows rather than counted a second time.

## Per-finding verdicts

### Motion

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| Question option uses `transition: all` | `styles.css:119` | DUPLICATE-OF-MINE | Mine: Motion — “Question options use `transition: all 0.12s`.” Their exact-property fix is right. |
| Legacy option button uses `transition: all` | `styles.css:199` | DUPLICATE-OF-MINE | Mine: Motion — “Legacy option pills also use `transition: all 0.12s`.” |
| Pipeline bullet uses `transition: all` | `styles.css:287` | DUPLICATE-OF-MINE | Mine: Motion — “Pipeline bullets use `transition: all 0.25s`.” |
| No press feedback anywhere | `styles.css` | DUPLICATE-OF-MINE | Mine: Motion — “Buttons and custom pressables have hover but no shared `:active` feedback.” Apply it per pressable and exclude drag surfaces so an active transform does not overwrite a drag transform. |
| No reduced-motion path | `styles.css:302`, `styles.css:920`, `setup.html:35`, `setup.html:38` | DUPLICATE-OF-MINE | Mine: Motion — “No reduced-motion media query exists.” Their opacity-preserving interpretation matches the rubric. |
| No custom easing tokens | `styles.css:463`, `styles.css:346` | DUPLICATE-OF-MINE | Mine: Motion — “Built-in `ease` is used for card and progress motion.” |
| Setup indeterminate bar animates margin | `setup.html:40` | DUPLICATE-OF-MINE | Mine: Motion — “First-run indeterminate progress animates `margin-left`.” |
| Indeterminate loop uses `ease-in-out` | `setup.html:38` | AGREE | Constant motion should be linear; this is a direct rubric match. |
| Setup sheen should be linear and 1.1s | `setup.html:35` | AGREE-BUT-SHARPEN | Linear is correct for the constant sweep. Keep the existing 1.6s until a visual check supports 1.1s; the faster number is unmeasured, and the sheen should disappear entirely under reduced motion. |
| Render progress animates width for 400ms | `styles.css:263` | DUPLICATE-OF-MINE | Mine: Motion — “Render progress animates `width` for 400ms.” |
| Capability progress animates width | `styles.css:1338` | DUPLICATE-OF-MINE | Mine: Motion — “Capability progress animates `width` for 300ms.” |
| Setup progress retargets a width transition | `setup.html:30`, `setup.js:79` | DUPLICATE-OF-MINE | Mine: Motion — “First-run progress animates `width` for 350ms.” Their observation that the bar lags its text is the sharper explanation. |
| Chat smooth-scrolls every stream update | `ChatPanel.jsx:38` | DUPLICATE-OF-MINE | Mine: Motion — “Chat auto-scroll always uses smooth behavior.” Prefer instant streaming and reserve smooth motion for an explicit Jump to latest action. |
| Center play button should crossfade every pause | `StudioPreview.jsx:501`, `styles.css:940` | DISPUTE | The source actually says `!playing && sourceRef != null && (` followed by `<button className="st-stage-play" ...>▶</button>`. Play/pause is a frequent Space-key action, and Emil says “Never animate keyboard-initiated actions.” Keep DOM/focus stable or remove the duplicate central control, but do not add a scale crossfade to every pause. |
| Notices and banners reflow the editor | `Studio.jsx:675`, `Studio.jsx:681` | AGREE | This is a high-impact miss in my audit. Reserve a status rail or overlay a toast so routine saves do not move the canvas and timeline. |
| Main toast blinks in and out | `styles.css:411`, `App.jsx:150` | AGREE | Toasts are occasional feedback and warrant a short transform/opacity entrance plus a faster exit, with reduced-motion fallback. |
| Update toast has no exit | `styles.css:422`, `UpdateBanner.jsx:21` | AGREE | Retaining it through a 125–150ms exit is the correct asymmetric treatment. |
| Update toast uses a one-shot keyframe | `styles.css:423` | AGREE | A transition is interruptible and also lets both toast systems share one motion contract. |
| Disclosure caret should rotate | `ChatPanel.jsx:342`, `App.jsx:698` | AGREE-BUT-SHARPEN | Replace the font glyph with one SVG chevron, but do not promise animation for keyboard activation. Frequent disclosures can switch orientation instantly; if pointer-only motion is retained, keep it near 125ms and disable transform motion for reduced motion. |
| Tool result expansion should animate grid rows or max-height | `ChatPanel.jsx:348` | DISPUTE | The source actually conditionally mounts `{open && (` then `<div className="tool-expand">`. Animating `max-height` or grid tracks is layout animation, explicitly contrary to the rubric's transform/opacity performance rule, and this disclosure may be used tens of times per day. Keep it instant; solve scroll anchoring separately. |
| Hover transforms are not capability-gated | `styles.css:465`, `styles.css:1008`, `styles.css:1142`, `styles.css:347` | DUPLICATE-OF-MINE | Mine: Motion — project lift and timeline trim hover rules both need fine-pointer gates and persistent focus/selection affordances. |
| Recorder pulse animates box-shadow | `styles.css:921` | AGREE | A pseudo-element ring using transform and opacity avoids continuous repaint; reduced motion should remove the pulse. |
| Auth mode swap changes modal height abruptly | `ConnectClaudeModal.jsx:71` | AGREE | A stable minimum body height is the main fix. Any crossfade should be short, opacity-only, and absent for keyboard/reduced-motion activation. |
| Capability row grows when install starts | `CapabilityInstall.jsx:92` | AGREE | Reserving the progress row prevents adjacent packs from jumping; a restrained opacity reveal is sufficient. |
| Playhead moves with `left` every frame | `StudioTimeline.jsx:461` | DUPLICATE-OF-MINE | Mine: Craft — “Playhead position is written as fractional `left` pixels every frame.” Fix with a translated layer and a stable device-pixel policy. |
| Line chart sets state on every mousemove | `LineChart.jsx:46` | AGREE-BUT-SHARPEN | Coalescing hover work into one rAF is sensible, but profile before adding machinery: current work is a single state update plus a small nearest-point reduction. Cancel the pending frame on leave/unmount. |

### Visual system

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| Thirty-five font sizes form no scale | `styles.css` | DUPLICATE-OF-MINE | Mine: Visual system — “There are 41 distinct `font-size` values.” The totals use different counting rules, but both establish the same continuum; adopt named sizes with line-height roles. |
| Thirteen radii form no scale | `styles.css:18` | DUPLICATE-OF-MINE | Mine: Visual system — “Radius declarations span at least 12 scalar sizes.” |
| Sixty hardcoded hex colors escape tokens | `styles.css:20-1341` | DUPLICATE-OF-MINE | Mine: Visual system — “The stylesheet contains 75 unique hex literals.” Again the exact filter differs, not the conclusion. |
| API-key shadow uses a near-miss accent | `styles.css:219` | AGREE | This is a precise example of token drift; derive it from the semantic accent or a shadow token. |
| Ten ad-hoc elevations | `styles.css:106`, `styles.css:219`, `styles.css:347`, `styles.css:363`, `styles.css:412`, `styles.css:421`, `styles.css:465`, `styles.css:493`, `styles.css:574`, `styles.css:755` | DUPLICATE-OF-MINE | Mine: Visual system — “There are 17 shadow recipes/variants.” Collapse to functional elevations. |
| `--line` is too faint for interactive boundaries | `styles.css:6` | AGREE | This is the most important issue Claude caught that I missed. Split subtle separators from a roughly 3:1 control-boundary token; do not darken every decorative card line indiscriminately. |
| Muted text fails AA | `styles.css:5` | DUPLICATE-OF-MINE | Mine: Visual system — “Muted `#8a8178` is about 3.6:1.” |
| Warning foreground fails AA | `styles.css:11`, `styles.css:12` | DUPLICATE-OF-MINE | Mine: Visual system — “Amber on amber-soft is only about 2.4:1.” |
| Success foreground fails AA | `styles.css:13`, `styles.css:14` | DUPLICATE-OF-MINE | Mine: Visual system — “Green on green-soft is about 3.5:1.” |
| Unsaved indicator inherits weak amber | `styles.css:893` | DUPLICATE-OF-MINE | Mine: Visual system — warning contrast failure. Claude correctly identifies the highest-cost instance and the type-size compounding effect. |
| Advisory label is 0.58rem with weak amber | `styles.css:722` | DUPLICATE-OF-MINE | Mine: Visual system/Craft — warning contrast plus “Font sizes descend to `0.58rem`.” |
| Disabled primary labels become illegible | `styles.css:46`, `styles.css:908`, `styles.css:911` | DUPLICATE-OF-MINE | Mine: Visual system — “Disabled states are mostly opacity-only.” Explicit state colors are better than compositing the entire control. |
| Six disabled opacities express one state | `styles.css:46`, `styles.css:812`, `styles.css:908`, `styles.css:947`, `styles.css:784`, `styles.css:134`, `styles.css:247`, `styles.css:429` | DUPLICATE-OF-MINE | Mine: Visual system — opacity-only disabled states. Claude's count makes the incoherence clearer. |
| Native controls keep macOS blue | no current rule | AGREE | A root `accent-color` is a low-cost brand-coherence win, provided forced-colors mode remains authoritative. |
| Only two visible focus rules | `styles.css:349`, `styles.css:1037` | DUPLICATE-OF-MINE | Mine: Visual system — “Focus is styled only for asset tiles and scrub bars.” |
| Input focus removes the outline | `styles.css:129`, `styles.css:234`, `styles.css:1289` | DUPLICATE-OF-MINE | Mine: Visual system — “Several text fields explicitly remove outlines.” Add a visible `:focus-visible` ring rather than retaining both UA and custom outlines. |
| Global button default inverts hierarchy | `styles.css:45`, `styles.css:47` | AGREE | Neutral-by-default with an explicit primary class removes the need for 23 hover overrides and makes hierarchy intentional. |
| Setup colors near-miss app tokens | `setup.html:16`, `setup.html:21`, `setup.html:28`, `setup.html:29` | DUPLICATE-OF-MINE | Mine: Craft — “Setup uses a separate set of near-match colors and sizes.” |
| Setup microcopy contrast is too low | `setup.html:44`, `setup.html:48`, `setup.html:56` | AGREE | The measured contrast and very small sizes make this a real first-run accessibility problem. |
| Project covers span all 360 hues | `App.jsx:230` | DUPLICATE-OF-MINE | Mine: Hierarchy — “Dashboard project identity is an initial over random HSL gradients.” Prefer a real poster, then a restrained warm fallback rather than merely hashing into a narrower band. |
| White project initial has unstable contrast | `styles.css:469` | AGREE | A fixed dark foreground on a controlled fallback is simpler and more reliable than runtime luminance logic. |
| `color-mix()` appears only twice | `styles.css:565`, `styles.css:1322` | DISPUTE | The actual rules are `background: color-mix(in srgb, var(--red) 7%, transparent)` for destructive emphasis and `background: color-mix(in srgb, var(--green-soft) 35%, var(--panel))` for completed capabilities. Those are legitimate context-specific derived colors; using a feature twice is not itself incoherent. The real problem is the already-captured hardcoded-color/token sprawl. |

### Hierarchy & layout

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| Render cards hardcode 16:9 | `styles.css:334`, `styles.css:52` | AGREE-BUT-SHARPEN | The hardcoded landscape box is wrong, but hardcoding 9:16 is also wrong: `CANVAS_PRESETS` includes 9:16, 1:1, 16:9, and 4:5 (`model.js:28`). Derive the card aspect ratio from project/render metadata and prioritize the latest deliverable. |
| Asset thumbnails crop vertical media | `styles.css:350`, `styles.css:1221` | AGREE-BUT-SHARPEN | A fixed 4:5 crop still damages 9:16 and landscape assets, and RULES.md says users may drop any media. Use intrinsic/poster ratio in a bounded frame with `object-fit: contain`, or a portrait-biased frame that never crops content. |
| Empty chat lacks examples | `ChatPanel.jsx:79` | AGREE | Three concise, outcome-oriented starter prompts would teach the AI editing model better than a generic sentence. Keep them secondary to the composer. |
| Loading states are bare strings | `CapabilitiesModal.jsx:47`, `App.jsx:424`, `App.jsx:893`, `AssetModal.jsx:94`, `App.jsx:743`, `Studio.jsx:645` | AGREE-BUT-SHARPEN | Build one accessible spinner and use skeletons only where final geometry is known, such as project tiles. Under reduced motion use a static progress mark/status text rather than a rotating arc. |
| Progress bars fabricate precision | `ChatPanel.jsx:153`, `CapabilityInstall.jsx:36`, `setup.js:79`, `Studio.jsx:272` | DUPLICATE-OF-MINE | Mine: Hierarchy — “Render and capability progress display fabricated percentages,” plus setup fake precision. Do not infer `stage/total` merely because render status is polled; use labelled indeterminate phases until the backend exposes measured progress. |
| Warning/error notices never dismiss | `Studio.jsx:98` | AGREE-BUT-SHARPEN | Do not auto-dismiss errors. Transient success can time out; warnings may time out when resolved; errors should persist with Close and, where possible, Retry while occupying a reserved/overlay status rail. |
| Fatal project load has no retry | `Studio.jsx:645`, `Studio.jsx:650` | AGREE | Add a clear error surface with Retry and Back; preserve the error detail without making raw text the entire UI. |
| Keyframe presets are tiny prose links | `StudioKeyframes.jsx:60`, `StudioKeyframes.jsx:68` | AGREE | Style presets as compact actions, separate destructive Clear, and keep an undo affordance. |
| Artifact chip uses literal `{}` | `App.jsx:674`, `App.jsx:712` | AGREE | Use the existing icon language; this is a visible mismatch inside one list. |
| Raw/Formatted state is ambiguous | `App.jsx:886` | AGREE | A labelled two-state control should expose both options and selected state. |
| Dashboard header pills compete and “BYOK” is jargon | `App.jsx:193`, `App.jsx:201` | DUPLICATE-OF-MINE | Mine: Hierarchy — “The dashboard header has four similarly quiet account/settings actions.” Rename to API keys and group settings. |
| Composer action stretches with textarea | `styles.css:92`, `styles.css:89` | AGREE | Align Send/Stop to the composer bottom and give the action a stable height; a tall terracotta slab destroys the input hierarchy. |
| Dashboard loading and empty look the same | `App.jsx:223` | AGREE | Preserve an explicit loading sentinel, then show a useful first-project empty state only after loading resolves. |
| Pipeline shows two in-progress indicators | `styles.css:293`, `App.jsx:697` | AGREE | One status cue is enough; keep it on the step bullet and remove the redundant dot. Reduced motion should make it static. |
| Render mode introduces native video controls | `StudioPreview.jsx:505` | AGREE-BUT-SHARPEN | RULES.md correctly puts transport in the timeline, but do not hide native controls until the shared transport offers equivalent play/pause, seek, volume, and accessibility for the render. Then drive both modes from the same persistent control. |
| Chart tooltip should follow the pointer | `styles.css:753` | DISPUTE | The actual rule fixes `.lc-tip` at `top: 6px; right: 6px`, while the SVG already draws a focus line and dots at the hovered x (`LineChart.jsx:71-78`). A fixed readout avoids covering the very data being inspected and is predictable in a compact chart. Move it only if usability testing shows users cannot associate the focus line with the readout. |
| Collapsed-panel reopen strip is 18px | `styles.css:976`, `Studio.jsx:696`, `Studio.jsx:726` | AGREE | Expand the invisible target to at least 28px and label the destination; the recovery path must be discoverable without consuming much stage width. |
| Splitter hit area is 6px | `styles.css:971`, `styles.css:972` | DUPLICATE-OF-MINE | Mine: Interaction/Accessibility — “Splitters are bare pointer-only divs” and undersized. A wider pseudo hit target plus focusable separator semantics is right. |
| Grid lacks an intermediate layout mode | `styles.css:52`, `styles.css:761` | AGREE-BUT-SHARPEN | The cramped middle range is real, but prefer a container query or `minmax()` based on the actual tile minimum over a guessed 1200px viewport breakpoint. |

### Interaction feel

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| Timeline delete hides available undo | `Studio.jsx:373`, `Studio.jsx:638` | DUPLICATE-OF-MINE | Mine: Interaction — “Clip/overlay/audio delete commits instantly.” Surface recovery immediately; an inline Undo action is clearer than text that merely says ⌘Z. |
| Clear removes all keyframes without recovery | `StudioKeyframes.jsx:68` | DUPLICATE-OF-MINE | Mine: Interaction — “Clear removes every keyframe in one click.” Style it as destructive and offer Undo. |
| Debug logs get stronger protection than edits | `DebugReportModal.jsx:94` | DUPLICATE-OF-MINE | Mine: delete/clear findings make the same safety-budget point. Keep confirmation only for truly irreversible log deletion; use undo for document edits. |
| Drag surfaces never show `grabbing` | `styles.css:1003`, `styles.css:1135`, `styles.css:1145`, `styles.css:1155`, `styles.css:1006` | DUPLICATE-OF-MINE | Mine: Craft — splitters omit a drag-global cursor state. Claude correctly broadens it to every active editor drag surface. |
| Trim handles are invisible and 9px | `styles.css:1140`, `StudioTimeline.jsx:355` | DUPLICATE-OF-MINE | Mine: Interaction — “Timeline trim handles are invisible until hover and only 9px wide.” Keep a 2px visible grip but expand the invisible hit target to at least 24px; 11px alone is still too precise. |
| Toast timers race | `App.jsx:63`, `App.jsx:70` | AGREE | Clear/re-arm one timer per channel so an older success timer cannot erase a newer error. |
| Privacy toggle flashes a false unchecked state | `App.jsx:346` | AGREE | Use a loading placeholder or tri-state copy; privacy UI must not briefly assert the wrong setting. |
| Main preview clip can select text while dragging | `styles.css:1003` | AGREE | Claude is right and my deliberate-not-changing note was factually wrong: `.st-clip-box` has `cursor: grab; touch-action: none` but no `user-select: none`. Add it and lock selection during drag. |
| Splitter drag can select adjacent text | `styles.css:971`, `styles.css:972` | DUPLICATE-OF-MINE | Mine: Craft — “Splitters omit `user-select:none`.” |
| Native `title` is the only tooltip system | `StudioTimeline.jsx:264`, `StudioInspector.jsx:131` | AGREE-BUT-SHARPEN | Build an accessible tooltip primitive that opens on focus as well as hover, has no motion for keyboard/reduced-motion activation, and skips delay after the first pointer tooltip. Do not mechanically replace labels that already provide an accessible name. |
| Music gain has no visible value or grip | `StudioTimeline.jsx:415`, `styles.css:1169` | DUPLICATE-OF-MINE | Mine: Interaction — “Music gain is a 3px line with a small 9px knob.” Add a larger hit target, visible value, and keyboard adjustment. |
| Scrub field does not advertise click-to-type | `StudioInspector.jsx:128` | AGREE | A subtle caret/edit glyph on hover and focus is clearer than an underline, which reads as a link. Preserve the strong drag cursor and title hint. |
| Setup failure fills the bar to 100% red | `setup.js:65` | AGREE | Completion and failure need different geometry/state; freeze or switch to an explicit failed phase rather than displaying a full completion bar. |
| Setup completion never reaches 100% | `setup.js:56`, `setup.js:79` | AGREE | Once `onDone` is a truthful completion event, showing 100% before handoff is correct. This does not justify invented intermediate percentages. |
| Several hover state changes are hard cuts | `styles.css:384`, `styles.css:603`, `styles.css:1201` | AGREE-BUT-SHARPEN | Add exact-property color transitions using the shared curve, but gate hover-only rules behind fine-pointer media and preserve instant keyboard feedback. |

### Accessibility

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| Eight modal surfaces lack dialog semantics | `DebugReportModal.jsx:55`, `CapabilitiesModal.jsx:33`, `App.jsx:299`, `App.jsx:411`, `App.jsx:493`, `App.jsx:875`, `ConnectClaudeModal.jsx:57`, `AssetModal.jsx:47` | DUPLICATE-OF-MINE | Mine: Accessibility — “App modals are generic divs without dialog semantics,” including AssetModal separately. A shared dialog shell is the systemic fix. |
| Seven dialogs ignore Escape | `AssetModal.jsx:34` and other modal anchors | DUPLICATE-OF-MINE | Mine: Accessibility — “Non-asset modals have no Escape handling.” Busy/destructive states may require confirmation, not a dead Escape key. |
| No modal traps or restores focus | modal sources | DUPLICATE-OF-MINE | Mine: Accessibility — “No modal traps focus or restores it.” |
| Timeline objects are pointer-only | `StudioTimeline.jsx:327`, `StudioTimeline.jsx:359`, `StudioTimeline.jsx:404`, `StudioTimeline.jsx:427`, `StudioTimeline.jsx:445` | DUPLICATE-OF-MINE | Mine: Accessibility — separate rows for clips/overlays and audio objects. Add selection semantics plus keyboard move/trim/nudge, not focusability alone. |
| Unbounded scrub fields use invalid slider semantics | `StudioInspector.jsx:128`, `StudioInspector.jsx:275`, `StudioInspector.jsx:278`, `StudioInspector.jsx:311`, `StudioInspector.jsx:316`, `StudioInspector.jsx:351`, `StudioInspector.jsx:354` | AGREE | This is an important miss in mine. Use `spinbutton` for unbounded numeric adjustment and slider only where finite min/max exist. |
| Activity file rows are mouse-only divs | `App.jsx:823` | DUPLICATE-OF-MINE | Mine: Accessibility — “Clickable file rows in Activity are divs with no keyboard behavior.” |
| Toasts/notices are silent to assistive tech | `App.jsx:150`, `Studio.jsx:681` | DUPLICATE-OF-MINE | Mine: Accessibility — “Toasts are plain divs with no live-region semantics.” Status for success; alert for actionable errors. |
| Segments, presets, and tabs lack selected semantics | `StudioToolbar.jsx:60`, `StudioToolbar.jsx:61`, `StudioInspector.jsx:291`, `App.jsx:623`, `App.jsx:740`, `App.jsx:1242` | DUPLICATE-OF-MINE | Mine: Accessibility — work/activity segmented controls, plus Source/Render group semantics. Use the correct pressed, radio, or tab pattern per control. |
| Timeline zoom is unnamed | `StudioTimeline.jsx:279` | DUPLICATE-OF-MINE | Mine: Accessibility — “Timeline zoom range has no accessible name.” |
| Many critical hit targets are below 24px | `styles.css:1114`, `styles.css:949`, `styles.css:78`, `styles.css:1199`, `styles.css:539`, `styles.css:430` | DUPLICATE-OF-MINE | Mine: Accessibility — “Several controls miss even the 24px minimum target.” Claude's inventory is broader. |
| Setup progress has no live semantics | `setup.html:64`, `setup.html:69` | AGREE-BUT-SHARPEN | Announce the concise stage/status, not the entire rapidly changing log: making `#log` polite would spam screen readers with installer output. Keep the log navigable and let users opt into reading it. |
| Artifact modal close lacks an explicit label | `App.jsx:888` | AGREE | Add `aria-label="Close"` and the shared icon; do not rely on `title` plus a glyph. |

### Craft details

| Their finding (short) | Their anchor | Verdict | My response |
| --- | --- | --- | --- |
| Trash emoji appears in timeline and inspector copy | `StudioTimeline.jsx:266`, `StudioInspector.jsx:489` | DUPLICATE-OF-MINE | Mine: Craft — “Studio toolbar controls use emoji/text glyphs,” plus warning-string cleanup. Direct RULES.md violation. |
| Hourglass emoji appears in pipeline status | `App.jsx:704` | DUPLICATE-OF-MINE | Mine: Craft — emoji warning/error strings and glyph controls. Use a semantic status icon. |
| Play/pause glyphs can render as emoji | `StudioTimeline.jsx:272`, `StudioPreview.jsx:501`, `App.jsx:1387` | DUPLICATE-OF-MINE | Mine: Craft — replace text glyph transport with the shared stroke icon set. |
| The two asset surfaces use different play icons | `StudioAssets.jsx:72`, `App.jsx:1387` | DUPLICATE-OF-MINE | Mine: Craft — “AssetPanel still uses glyph controls while StudioAssets uses SVG icons.” |
| Fullwidth plus/minus glyphs have wrong metrics | `StudioToolbar.jsx:24`, `StudioTimeline.jsx:278`, `StudioTimeline.jsx:280`, `ChatPanel.jsx:74`, `App.jsx:225` | DUPLICATE-OF-MINE | Mine: Craft — glyph control inventory. Claude's typographic reason is more precise. |
| Eye icons are redefined with different metrics | `StudioTimeline.jsx:35`, `icons.jsx:13` | AGREE | Import the shared icons so stroke, size, and optical alignment remain coherent. |
| Roughly 25 text glyphs substitute for icons | cited JSX sites | DUPLICATE-OF-MINE | Mine: Craft — icon/emoji rows across studio, chat, forms, and AssetPanel. Replace semantic icons first; punctuation arrows in prose/options need not all become SVGs. |
| Nine numeric readouts lack tabular numerals | `styles.css:264`, `styles.css:387`, `styles.css:1333`, `styles.css:747`, `styles.css:758`, `styles.css:718`, `styles.css:141`, `styles.css:606`, `styles.css:289` | AGREE-BUT-SHARPEN | Add tabular numerals to changing percentages, costs, time/value readouts, and aligned columns. Static single counts do not need it merely because they are numeric; prioritize places where width jitter or column alignment is visible. |
| Keyframe-add label changes width with playhead | `StudioKeyframes.jsx:78` | AGREE | Give the time fragment a stable `ch` width/tabular numerals or move it to adjacent status text so playback does not reflow actions. |
| Capability percentage shifts its row | `CapabilityInstall.jsx:89`, `styles.css:1329` | AGREE | Reserve width for the badge and use tabular numerals; this is exactly the compounding jitter case. |
| No scrollbar styling | no current rule | DUPLICATE-OF-MINE | Mine: Craft — “No scrollbar styling exists for the many nested scroll regions.” Keep platform accessibility and add dark-pane color-scheme where needed. |
| No stable scrollbar gutter | scroll-container selectors | AGREE-BUT-SHARPEN | Apply `scrollbar-gutter: stable` only to containers where classic scrollbars produce measured layout shift. On macOS overlay scrollbars, forcing gutters everywhere can create needless dead space in already narrow panels. |
| Window can flash white before CSS loads | `web/index.html:8` | AGREE | A tiny inline cream background in the head prevents launch/reload flash without changing runtime design. |
| ProjectBar dot is unstyled but consumes gap | `App.jsx:546`, `styles.css:31` | AGREE | This is a concrete implementation bug, not taste: either style a dedicated mark or remove the empty child. |
| Overlay label is truncated twice and mid-word | `StudioTimeline.jsx:325`, `styles.css:1147` | AGREE | Remove the JS slice and let the existing CSS ellipsis preserve the full accessible/title text. |
| Breadcrumb separators are nearly invisible | `styles.css:1203` | AGREE | Use a readable neutral; separators are meaningful path structure, not decoration. |
| Three unused studio selectors remain | `styles.css:1018`, `styles.css:928`, `styles.css:899` | DUPLICATE-OF-MINE | Mine: Craft — old/dead styling inflates the design-system file. Delete selectors only after the zero-reference check remains true. |
| Dead editor JSX and 119 CSS lines should all be deleted | `web/src/editor/`, `styles.css:763`, `App.jsx:117` | AGREE-BUT-SHARPEN | Mine: Craft/Deliberately not changing — remove confirmed dead CSS in the polish pass, but delete the five JSX files in a separate cleanup after all entrypoints, packaging, and tests confirm they are unreachable. Keep the live `interp.js` contract. |
| Live Activity `tl-` classes collide with dead editor prefix | `styles.css:618`, `styles.css:836`, `App.jsx:846` | AGREE-BUT-SHARPEN | Rename to `act-*` only as part of the dead-style cleanup, with a reference check. This is maintainability/collision prevention, not a one-day user-facing polish priority. |
| Setup font stack differs from the app | `setup.html:15`, `styles.css:17` | AGREE-BUT-SHARPEN | Use the same stack for coherence, but the claim that Inter causes a different Mac face is overstated: `-apple-system` resolves first on the target platform, so Inter is never reached there. |
| Setup uses a second px type scale | `setup.html:20`, `setup.html:52` | AGREE-BUT-SHARPEN | Map setup text to the same named size/line-height roles, but `rem` versus `px` is not itself polish. In a fixed first-run window, equivalent computed sizes look identical; the problem is the unrelated values and roles. |
| “Mission Control” should be removed as internal vocabulary | `web/index.html:6`, `App.jsx:183` | DISPUTE | The source actually says `<title>OpenNolan · Mission Control</title>` and `OpenNolan <span className="muted">· Mission Control</span>`. RULES.md calls this the “desktop editor / Mission Control UI” but never marks the name internal or forbidden. Removing a product subtitle is a branding decision unsupported by this rubric; change it only with explicit product direction. |

## Missed by both

| Before | After | Why |
| --- | --- | --- |
| Chat tool disclosures expose state only through a glyph and conditional content (`web/src/chat/ChatPanel.jsx:341`) | Add `aria-expanded={open}` and `aria-controls` pointing to a stable result-panel id. | Screen readers cannot tell that the button expands content or whether it is currently open. |
| Pipeline step disclosures likewise omit expanded state (`web/src/App.jsx:692`) | Add `aria-expanded`/`aria-controls`; disable disclosure semantics when there is no detail. | Visual caret direction is not an accessible state, and the button remains a button even when it has nothing to open. |
| The chat composer textarea has only placeholder text (`web/src/chat/ChatPanel.jsx:113`) | Add an explicit accessible label such as `aria-label="Message the agent"`; keep keyboard instructions as description/help text. | Placeholder copy is not a durable label and disappears as soon as the founder starts typing. |
| Thread and model selects rely on nearby context/title rather than labels (`web/src/chat/ChatPanel.jsx:61`, `web/src/chat/ChatPanel.jsx:133`) | Give each select an accessible name and associate any explanatory text with `aria-describedby`. | A screen reader otherwise announces generic popups without saying whether they switch chat history or the agent model. |
| Studio's window-level shortcuts are active behind every modal except AssetModal (`web/src/studio/Studio.jsx:624`, `web/src/components/AssetModal.jsx:28`) | Make the shared dialog shell inert the background and capture/contain editor shortcuts while any dialog is open. | Pressing Space, S, Delete, or Escape in a non-Asset dialog can affect the hidden editor; AssetModal's comment documents the correct exception pattern. |
| Clicking the central preview Play unmounts the focused button (`web/src/studio/StudioPreview.jsx:500`) | Remove the duplicate central transport or explicitly move focus to the persistent timeline Play button before hiding it. | A pointer or keyboard activation can drop focus into the document, while RULES.md already names the timeline toolbar as transport home. |

## Conflicts to settle

1. Main-preview drag selection suppression

   - **Their position:** `.st-clip-box` lacks `user-select: none`; add it.
   - **My position:** My round-1 “Deliberately not changing” section said the main drag surfaces already had `user-select: none`.
   - **Who should win and why:** Claude. The rule at `web/src/styles.css:1003` contains `cursor: grab; touch-action: none` and no `user-select`; I had conflated it with `.st-ov-canvas` at line 1006. Add selection suppression to the main clip and during the global drag state.
   - **What would change my mind:** Only evidence that the clip box can never contain/select text and browser selection is already prevented by its pointer handler across all supported Electron versions.

2. Dead old-editor deletion scope

   - **Their position:** Delete five unused JSX files and their 119-line CSS block in the polish work; retain only `interp.js` and its tests.
   - **My position:** Remove confirmed shipping CSS now, but delete JSX in a separately reviewed cleanup after checking every build/alternate entrypoint.
   - **Who should win and why:** Mine for the one-day polish plan. Dead CSS directly burdens the shipped visual system; deleting 690 lines of source is repository cleanup with a wider regression surface and no immediate user-visible gain. Name it as follow-up rather than mixing it into UI implementation.
   - **What would change my mind:** A complete import/entrypoint/package search plus fast/full tests proving the JSX is unreachable, and explicit scope that the polish change is also authorized to remove dead code.

3. First-run log auto-follow

   - **Their position:** Claude's setup score treats instant unconditional log scrolling (`setup.js:19`) as a polished behavior worth leaving alone.
   - **My position:** Follow only while the reader is near the bottom; once they scroll up, preserve position and offer Jump to latest.
   - **Who should win and why:** Mine. During a long or failed install, the log's purpose is diagnosis; forcibly moving someone away from the line they are reading violates user control. Sticky-to-bottom behavior can preserve the good default without trapping the user.
   - **What would change my mind:** Evidence that the setup log is intentionally non-interactive, cannot be scrolled during installation, or usage data shows users never inspect earlier output before completion.

## My concessions

- My claim that the main preview clip already had `user-select: none` was wrong; Claude's cited CSS proves it is missing.
- I missed the systemic `--line` contrast failure. At roughly 1.2:1 across controls, it is more consequential than several items in my Top 10 and should be fixed in the first pass.
- I missed the routine `.st-notice`/`.st-banner` reflow that moves the preview and timeline. That deserves day-one priority because it affects frequent save/render feedback.
- Claude's loading-state inventory was broader and more useful than my assets-only treatment; loading, empty, error, and retry need a shared product-wide contract.
- I missed the invalid `slider` semantics on unbounded ScrubFields even though I praised the control's interaction design.
- Claude's exact icon and micro-jitter inventory was stronger: the duplicate eye SVGs, phantom ProjectBar dot, redundant JS truncation, and changing keyframe/capability widths are all concrete craft defects.
- My 24px trim-target recommendation remains safer than Claude's 11px target, but Claude's persistent selected grip is the better visual treatment; the merged fix should combine the two.
- My surface scores were too generous to Pipeline/Activity and the dashboard once Claude's random-cover contrast, duplicate progress, loading ambiguity, and mixed icon-language evidence are considered.
