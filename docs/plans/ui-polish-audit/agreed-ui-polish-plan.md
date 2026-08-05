# Agreed UI Polish Plan — OpenNolan desktop
Status: PLAN

Authored jointly after two independent audits and a mutual cross-review.
**claude** = Claude Code on Opus 5, xhigh effort (after Fable 5 was unavailable).
**codex** = gpt-5.6-sol at high effort.
**Ratification:** reviewed by codex (gpt-5.6-sol, high) — verdict **RATIFIED WITH AMENDMENTS**
(`codex/ratification.md`). All 13 required amendments and all 6 restored items applied in round 5;
none contested. Every contrast ratio published below was independently recomputed against the sRGB
WCAG formula before being written down.
Supersedes `claude/findings.md`, `codex/findings.md`, `claude/cross-review.md`, `codex/cross-review.md`.

Raw input: **116 detailed findings (claude) + 74 (codex)** = 190 rows, of which ~55 were the same issue
seen twice → ~135 distinct. Three were dropped on review; ten more were found by neither auditor in
round 1 and only surfaced from reading each other's work. **~142 distinct issues**, sequenced here as
**122 build items** across 8 phases, P0–P7 (P5 splits into 5a/5b, which run at different times; a few
issues are split by surface and some rows bundle instances).
**14 conflicts settled**: codex won 9, claude won 3, 2 merged.

---

## The one-paragraph problem statement

OpenNolan's editor is engineered better than it looks. The hard parts are solid — the seek-chaser, live
video overlays, canvas-coordinate WYSIWYG, derived timeline placement, the scrub fields. What is missing is
a design system: `styles.css` is one 1341-line stylesheet with 16 color tokens and a single radius token
sitting on top of 41 font sizes, 12 radii, 17 shadow recipes, 75 hex literals, six different disabled
opacities, no spacing steps and no motion tokens. The visible consequences all trace back to that. Every
button, input and card boundary is drawn at **1.23:1 contrast**, so controls have no edges and the UI reads
as mushy rather than crisp. Secondary text is at **3.58:1** and every primary button label — white on
terracotta — is at **3.93:1**, so the app fails AA in its two most common text styles. Nothing in 1341
lines responds to a press. Saving a project shoves the preview canvas and the entire timeline down 28
pixels with no transition. The dashboard, the first screen a founder sees, paints every project with a
random-hue rainbow gradient in an app whose stated identity is warm terracotta on cream. Three separate
progress bars invent percentages the system cannot know. And the interface actively teaches the one
behavior the product's own north star forbids: the Render button's tooltip calls itself
`"Render preview (render-once)"` and the empty preview says `"No render yet — hit Render."` when RULES.md
states *"The user should NEVER have to hit Render just to see an edit."* None of this is exotic. It is a
missing foundation plus about forty small betrayals of an aesthetic the code is otherwise reaching for.

---

## Conflicts settled

| # | Conflict | Winner | Reason (one line) |
| --- | --- | --- | --- |
| 1 | Progress bars: wire real progress vs. labeled indeterminate | **codex** | `server/render_jobs.py:66` builds the job as `{job_id, project_id, status, origin}` and `server/app.py:639` documents only `{status, output_path?, error?}` — the stage/total signal claude proposed passing through does not exist. |
| 2 | Is Render mis-named or just mis-weighted? | **merged** | Demote it and rename the user-facing action to **Export** (codex); **Render/Re-render** stays the vocabulary for internal jobs and future comp materialization (claude's carve-out) — but the per-clip comp badge is deferred, because no per-cut comp or staleness field exists (see Not doing). |
| 3 | Dashboard tile identity: real poster vs. clamped hue band | **codex** | A real frame is both on-brand and informative; `api.js:151` already exports an unused `frameUrl` for the endpoint at `server/app.py:646`. It needs no new endpoint — but it does need a cache before it ships (see Phase 5b). |
| 4 | Modal dismissal: backdrop dirty-guard vs. Escape everywhere | **merged** | Both gestures must obey one policy; route backdrop, Escape and the close button through a single `requestClose()` that confirms when the form is dirty. |
| 5 | First-run log auto-follow (`setup.js:19`) | **codex** | claude cited it as an exemplar; it is right about *behavior* (instant) and wrong about *condition* (unconditional), and yanks a reader off the error line they are reading. |
| 6 | Dead `editor/` scope: delete JSX now vs. defer | **codex** | Dead CSS burdens the shipped visual system and goes first; deleting 690 lines of source is repo cleanup with a wider regression surface and no user-visible gain. |
| 7 | `.st-clip-box` selection suppression | **claude** | `styles.css:1003` reads `cursor: grab; touch-action: none` with no `user-select`; codex conceded its exemption had conflated it with `.st-ov-canvas` at `:1006`. |
| 8 | Should the center play button crossfade? | **codex** | claude proposed a crossfade on a Space-key action fired 100+ times a day; the rubric says never animate keyboard-initiated actions. Remove the duplicate control instead. |
| 9 | Should the chat tool disclosure animate open? | **codex** | claude proposed animating `max-height`/grid rows — the exact non-composited layout animation claude criticized elsewhere. Keep it instant; fix scroll anchoring separately. |
| 10 | Is `color-mix()` used twice a coherence defect? | **codex** | Two context-specific derived colors are legitimate; the row was a maintainability preference and is subsumed by the token-sprawl item. Dropped. |
| 11 | Should the chart tooltip follow the pointer? | **codex** | `LineChart.jsx:73-77` already draws a focus line and per-series dots at the hovered x, so the readout is not orphaned; a following tip would occlude a compact plot. Reduced to collision-avoidance only. |
| 12 | Remove "Mission Control" as internal vocabulary? | **codex** | RULES.md names it but never marks it internal; removing a product subtitle is branding, not craft. Left to product — the human then chose to drop it; both uses removed. |
| 13 | AssetModal autoplay: gate on input modality? | **claude** | `AssetModal.jsx:57` and `:70` are buttons calling the same `prev()`/`next()` as the arrow keys, so a modality gate fixes half the bug; the defect is the `key={item.url}` remount at `:59`. |
| 14 | Playhead: round to device pixels, or transform? | **claude** | Rounding keeps the per-frame layout *and* stutters — at `min="20"` px/sec (`StudioTimeline.jsx:279`) one CSS pixel is 50ms, so quantizing holds the playhead still for a frame then jumps. Transform only. |

**Also adopted from codex's sharpenings of claude rows** (corrections that change the fix, not conflicts):
render cards must derive their aspect from `CANVAS_PRESETS` (`model.js:28-33` has 9:16, 1:1, 16:9 *and*
4:5) rather than hardcode 9:16; asset thumbnails must use intrinsic ratio with `object-fit: contain`
rather than a fixed 4:5 crop, because RULES.md says users drop any media; **errors must not auto-dismiss**
(success times out, errors persist with Close/Retry); trim handles get a 24px invisible hit area with a
2px visible grip, not claude's 11px; `scrollbar-gutter` only where shift is measured, not globally; the
intermediate grid breakpoint should be a container query, not a guessed 1200px; **the setup sheen keeps
1.6s but switches to `linear`**; setup's live region announces the stage, **not** the whole log (which
would spam screen readers); and claude's claim that `'Inter'` changes the setup face was overstated —
`-apple-system` resolves first on the target platform, so Inter is never reached there.

---

## Independently converged

Both of us found these separately, without contact. This is the highest-confidence core of the plan — if
anything gets cut, it should not be from this list.

1. `styles.css` is a stylesheet, not a design system — no spacing, type, shadow or motion scale.
2. `--muted` text, amber-on-amber-soft, and green-on-green-soft all fail WCAG AA.
3. Zero `:active` press feedback anywhere in the app.
4. Zero `prefers-reduced-motion`, while three infinite animations run.
5. Three `transition: all` declarations.
6. Three progress bars animate `width`; the first-run bar animates `margin-left`.
7. Chat auto-scrolls `smooth` on every stream chunk.
8. All easings are weak browser built-ins; no custom curve exists.
9. Hover transforms are not gated behind `(hover: hover) and (pointer: fine)`.
10. Eight modal surfaces, zero with dialog semantics, focus trap, or focus restoration.
11. Timeline clips, overlays and audio blocks are non-focusable divs — the core editing surface is pointer-only.
12. Toasts and notices have no live-region semantics.
13. Delete and keyframe-clear are destructive with no confirm and no surfaced undo.
14. Trim handles are invisible until hover and far too small.
15. Music gain is a 3px line with no visible value.
16. Real emoji ship in the UI in violation of RULES.md, while `icons.jsx` exports the replacements.
17. The two asset browsers render the same tile with different icon languages.
18. Dashboard tiles use arbitrary rainbow HSL gradients.
19. Progress percentages are fabricated.
20. Font sizes descend to 0.58rem; badges are visual dust.
21. No scrollbar styling for a dozen nested scroll regions.
22. First-run setup uses a near-miss palette and a second type scale.
23. ~119 lines of dead old-editor CSS still ship.
24. Splitters lack `user-select: none` and a drag-global cursor.
25. The playhead is repositioned via `left` on every animation frame.

---

## The plan

```
  ┌──────────────────────────────────────────────────────────┐
  │ P0 PREFLIGHT — delete the dead editor CSS (763-881)      │
  │    BEFORE counting or migrating anything, so the token   │
  │    scales are measured on live code only                 │
  └────────────────────────────┬─────────────────────────────┘
                               │
  ┌────────────────────────────▼─────────────────────────────┐
  │ P1 TOKEN FOUNDATION                                      │
  │    color roles · space · type · radii · elevation ·      │
  │    motion — ONE reviewable commit PER token family       │
  └──┬──────────────┬─────────────────┬─────────────────────┬┘
     │              │                 │                     │
┌────▼─────┐ ┌──────▼──────┐ ┌────────▼────────┐ ┌──────────▼────┐
│ P2       │ │ P3          │ │ P5a ICON SYSTEM │ │ P7 CRAFT      │
│ LEGIBIL- │ │ MOTION      │ │  parallel-safe  │ │ SWEEP         │
│ ITY &    │ │ CONTRACT    │ │  RULES.md       │ │ (any time     │
│ EDGES    │ │ + no jumps  │ │  compliance     │ │  after P1)    │
└────┬─────┘ └──────┬──────┘ └─────────────────┘ └───────────────┘
     └───────┬──────┘
             │                        ┌────────────────────────┐
  ┌──────────▼───────────────┐        │ P6 STATES & KEYBOARD   │
  │ P4 NORTH-STAR ALIGNMENT  │        │  loading/empty/error · │
  │   Export · honest        │        │  dialog shell ·        │
  │   progress · live source │        │  timeline semantics    │
  └──────────┬───────────────┘        │  (needs only P1)       │
             │                        └────────────────────────┘
  ┌──────────▼───────────────┐
  │ P5b VERTICAL-FIRST       │
  │   posters · thumbnails · │
  │   aspect — GATED on the  │
  │   frame cache landing    │
  └──────────────────────────┘
```

P0 goes first so nobody tokenizes dead code. P1 then gates everything, but ships as one commit per token
family rather than one big one. P2, P3, P5a and P7 all depend only on P1 and can run in parallel by
different people — **P5a in particular is trivial, high-value RULES.md compliance and must not be stranded
behind the product work.** P4 needs P2's primary/secondary button distinction to exist before Export can be
demoted. P5b needs P4 (posters must not imply "render first") *and* a bounded frame cache. P6 is the largest
engineering surface and depends on nothing but P1.

### Phase 0 — Preflight

| # | Surface | Change | Anchor | Why it matters |
| --- | --- | --- | --- | --- |
| 1 | Global | Delete the ~119-line dead old-editor CSS block | `styles.css:763-881` | 7 of the 12 radii, several shadows and a chunk of the 41 font sizes live in dead code. Deleting first means every scale in P1 is measured against code that actually ships. Doing it last — as an earlier draft of this plan had it — means tokenizing rules nobody renders. JSX deletion stays deferred (Conflict 6). |
| 2 | Global | Delete the three unused studio selectors after re-confirming zero references | `styles.css:1018`, `:928`, `:899` | `.st-insp-empty` documents a state the inspector no longer has. |

### Phase 1 — Token foundation

**What lands:** the token block (values and measured ratios in the next section) plus a migration of the
declarations each token replaces. **Why here:** every later phase consumes it.

**On risk:** an earlier draft claimed "no visual change except contrast." That was wrong. Collapsing 41 font
sizes to 8, 12 radii to 4, 17 shadows to 3, a spacing continuum to 7 steps and six near-neutrals to two
*necessarily* changes layout and appearance. Land **one token family per commit** — color, then type, then
spacing, then radii, then elevation, then motion — each separately reviewable, so a regression is bisectable
to a family instead of buried in a 600-line diff.

| # | Surface | Change | Anchor | Why it matters |
| --- | --- | --- | --- | --- |
| 1 | Global | Add the full token block, including `color-scheme: light` to declare the intentional light editorial scheme | `styles.css:1-19` | 16 color tokens + one radius is the root cause of nearly every item in this plan. Declaring the scheme also makes native scrollbars and form controls render coherently instead of guessing. `forced-colors` support stays deferred. |
| 2 | Global | Migrate every `font-size` declaration onto the named type roles; add an explicit `:root { font-size: 100% }` purely as documentation | `styles.css:1`, `styles.css:21` | `:root` sets no `font-size` while `body` sets `14px`, so all 41 rem sizes compute against the 16px root — `.md-body { 0.9rem }` renders **larger** than body copy. The explicit 100% is a no-op against the current browser default and must not be described as the fix; **the fix is the migration to named roles.** Found by neither auditor in round 1. |
| 3 | Global | Split `--line` (keep `#ece5db`, decorative dividers only) from new `--border: #98886f` — measured **3.396:1** on `--panel`, **3.229:1** on `--bg`, **3.067:1** on `--field` | `styles.css:6` | Every button, input, select, tab, chip, scrub bar and dropzone edge is currently 1.23:1 against its panel. codex: *"the most important issue Claude caught that I missed."* An earlier draft proposed `#a09079`; that computes to 3.058 / 2.907 / **2.761**, so it fails on both the app background and the new field fill. `#98886f` is the value that actually clears 3:1 on all three control surfaces. |
| 4 | Global | Split `--accent` into decorative fill (`#c8643c`, unchanged) and `--accent-ink: #a44a26` for text and for fills under white labels | `styles.css:7`, `styles.css:45` | White on `#c8643c` is **3.930:1** — every enabled primary button label fails AA. `#a44a26` measures 5.856:1 under white and 5.480:1 as text on cream. |
| 5 | Global | Add `--ink-dim: #6f665c` (**5.267:1** on `--bg`) and migrate the 129 `var(--muted)` uses that carry **text**; alias `--muted` to it as a fail-safe and move the 3 decorative uses to `--muted-decor` (amended at QA increment 1) | `styles.css:5` | 3.578:1 on `--bg`. `--muted` carries timecodes, hints, metadata and navigation at 11–13px — far too much content to treat as decorative. |
| 6 | Global | Add `--amber-ink: #8a6410`, `--green-ink: #34663d`, `--red-ink: #96382a`, `--blue-ink: #33608f`; keep the existing hues as fills | `styles.css:11-16` | Amber-on-amber-soft is 2.419:1 — the "your Claude connection is broken" bar and the agent's approval card are the highest-stakes messages in the app at the weakest contrast. Recomputing for ratification also caught a pair neither audit measured: **`--blue` on `--blue-soft` is 4.269:1**, a marginal AA failure at `.ss-cue` (`styles.css:689`) and `.q-option code` (`:123`); `--blue-ink` measures 5.265:1 there. |
| 7 | Global | Add `--field: #f6f1e8` and replace the 41 `background: #fff` control fills | `styles.css:43`, `:1025`, `:822` | `#fff` on `--panel #fffdf9` is a **1.016:1** difference — the intended "raised field" does nothing, so with a 1.23:1 border an inspector input has *no visible boundary of any kind*. `--field` sits 1.107:1 below the panel, which combined with `--border` makes the field unmistakable. Found by neither auditor in round 1. |
| 8 | Global | Collapse the near-neutral drift into `--wash: #f2ede4` / `--wash-strong: #e9e2d6` / `--pre-bg: #1e1c18` / `--frame: #15120e` | `styles.css:139`, `:152`, `:167`, `:180`, `:477`, `:990` | `#f0ece4`, `#f6f2ec`, `#efe9df`, `#fbf7f0`, `#e8e2d8`, `#ede9e0` are visually indistinguishable and individually ungovernable. |
| 9 | Global | Replace 17 shadow recipes with `--shadow-1/2/3` + `--ring-accent` | `styles.css:106`, `:219`, `:347`, `:363`, `:412`, `:421`, `:465`, `:493`, `:574`, `:755` | Shadows currently encode component identity, not elevation. `:219`'s `#c56a4914` is a hand-eyeballed near-miss of `--accent`. |
| 10 | Global | Collapse 12 radii to `--radius-sm/md/lg/pill`; keep `--radius` as an alias of `--radius-md` | `styles.css:18` | `.st-btn` 8px, `.st-ov` 6px, `.st-clip` 7px — three radii on three adjacent blocks in one timeline. |
| 11 | Global | Add a `.sr-only` utility (none exists) | `styles.css` | Prerequisite for the dropzone `<label>` fix in P6 and for chart/table fallbacks. |

### Phase 2 — Legibility and edges

**What lands:** the token consumption that changes how the app *looks*. **Why here:** the highest visible
quality per line changed in the whole plan, and it is mechanical once P1 exists. **Parallel with P3, P5a, P7.**

| # | Surface | Change | Anchor | Why it matters |
| --- | --- | --- | --- | --- |
| 1 | All controls | Apply `--border` to buttons, inputs, selects, tabs, chips, scrub bars, the dropzone | `styles.css:43`, `:903`, `:1025`, `:1034`, `:405` | This single change is the difference between "assembled" and "art-directed". |
| 2 | All buttons | Invert the global default: neutral `<button>`, explicit `.btn-primary` using `--accent-ink` | `styles.css:45`, `styles.css:47` | Because the default button is primary, every secondary button is un-styled back down — which is why **23 `filter: none` overrides** exist. Deleting `filter: brightness(1.06)` removes all 23 and stops promoting every button to its own compositing layer. |
| 3 | All controls | Replace opacity-only disabled with explicit tokens: `background: var(--wash); color: var(--ink-dim); border-color: var(--line)`; no opacity on the control | `styles.css:46`, `:908`, `:911` | `.st-btn:disabled` (opacity .4) composed with `.st-primary:disabled` yields a **1.41:1** label — and Export is disabled exactly while it reads "Exporting…". An earlier draft proposed `#a09488`, which measures only **2.541:1** on `--wash` and would have shipped a second unreadable state; `--ink-dim` measures **4.827:1** there and keeps the label legible while background, border and cursor carry the disabled meaning. Also unifies six different disabled opacities. |
| 4 | Global | One `:where(button, a, input, select, textarea, [tabindex]:not([tabindex="-1"])):focus-visible { outline: 2px solid var(--accent-ink); outline-offset: 1px }` | `styles.css:41` | Two focus rules exist for ~120 interactive selectors. The `[tabindex]` clause is deliberate: it is what makes the `spinbutton`, `separator`, `option` and tab controls introduced in P6 inherit the ring for free instead of needing per-role selectors added later. |
| 5 | Forms | Remove the three `:focus { outline: none }`; move the border-color change to `:focus-visible` | `styles.css:129`, `:234`, `:1289` | Includes the API-key and OAuth-code fields, which currently have no perceptible focus state. |
| 6 | Global | Enforce an 11px type floor; retire `0.58rem`/`0.6rem`/`9px` | `styles.css:722`, `:609`, `:747`, `:748` | `.cs-advisory` is 9.3px amber-on-amber-soft at 2.419:1 — the label whose entire job is to say "this score is advisory". |
| 7 | Global | `:root { accent-color: var(--accent) }` | *(no current rule)* | The timeline zoom slider, the Lock-aspect / audio-mix checkboxes and the privacy toggle all render in **macOS system blue** inside a terracotta product. One line. |
| 8 | Studio bar | `.st-title`: add `min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap` and let `.st-bar-left` shrink | `styles.css:891`, `styles.css:892` | `.st-bar-left` is `flex: none` with no title truncation, so a long project name forces the toolbar to wrap. `.pb-title strong` (`:442`) already does this correctly — same app, two treatments, one broken. |
| 9 | Studio bar | Remove `flex-wrap: wrap` from `.st-bar` and `.st-tools`; hold one line and move the lowest-priority actions into a labeled More overflow at the width where the groups stop fitting | `styles.css:889-890`, `styles.css:895` | **Truncating the title fixes one trigger, not the wrap.** Both rules still wrap explicitly, so a narrow window can still reflow the toolbar and separate Save/Export from the project context — destroying spatial memory for the two actions users reach for most. Verify at the minimum supported Electron window width. |
| 10 | Dashboard | Fix the unstable white initial: fixed dark foreground on a controlled cover | `styles.css:469` | `#ffffffe6` over a random-hue gradient lands anywhere between roughly 1.6:1 and 3.5:1 depending on the project name. |
| 11 | Setup | Replace `#FBF7F0`/`#d97757`/`#7a7266`/`#ece3d6` with the canonical values; fix the three sub-3:1 microcopy colors | `setup.html:16`, `:21`, `:28`, `:29`, `:44`, `:48`, `:56` | First screen a user ever sees, currently handing off to a visibly different orange. `#b3ab9d` at 10.5px is ~2.2:1. |
| 12 | Chat | Widen the speaker distinction: `.msg.user { max-width: 80% }` and a real tint gap | `styles.css:137-139` | In the 340px editor agent panel a 95%-wide user bubble is 5% narrower than the assistant's, and `#f4e3da` vs `#f6f2ec` differ by ~2% luminance. Attribution rests on differences at the edge of perception. Found by neither auditor in round 1. |
| 13 | Assets | `.fb-sep` → `--ink-dim` | `styles.css:1203` | Breadcrumb `/` separators at 1.23:1 make the path read as one run-on string. |

### Phase 3 — Motion contract, and stop the layout jumps

**What lands:** one motion vocabulary, and the removal of un-animated reflow. **Why here:** the reflow is
the most frequently-felt defect in the editor and the motion tokens now exist. **Parallel with P2, P5a, P7.**

| # | Surface | Change | Anchor | Why it matters |
| --- | --- | --- | --- | --- |
| 1 | Studio | Stop `.st-notice`/`.st-banner` reflowing the editor — overlay them on `.st-bar` or reserve their height | `Studio.jsx:675`, `Studio.jsx:681` | Every save, export, warning and agent-adopt shoves the preview canvas **and the whole timeline** ~28px with no transition. codex: *"a high-impact miss in my audit… deserves day-one priority."* |
| 2 | Global | Add `@media (prefers-reduced-motion: reduce)`: drop transforms/pulses, keep opacity and color | `styles.css:302`, `:920`, `setup.html:35`, `setup.html:38` | Three infinite animations run unconditionally, two of them for the entire duration of a pipeline run or a debug recording. |
| 3 | All pressables | `transform: scale(0.97)` on `:active` with `--dur-press`; **exclude drag surfaces** so an active transform cannot overwrite a drag transform | `styles.css:41`, `:901`, `:459`, `:936` | Nothing in 1341 lines confirms a press. The drag exclusion is codex's refinement and prevents a regression on `.st-clip`. |
| 4 | Global | Replace the three `transition: all` with named properties | `styles.css:119`, `:199`, `:287` | `all` animates every future property; `.step .bullet` also re-fires on the unrelated 2s artifacts poll (`App.jsx:606`). |
| 5 | Setup | Convert the **setup** bar's `width` transition to `transform: scaleX()` with `transform-origin: left` | `setup.html:30` | Scoped deliberately to the one bar with real bounded progress. The chat and capability bars are **deleted** in Phase 4 item 4 — polishing their fill motion first would be work thrown away one phase later, so they are not touched here. |
| 6 | Setup | `@keyframes slide` → `transform: translateX()`, and **both** the indeterminate slide and the sheen switch to `linear` (sheen keeps 1.6s pending visual review) | `setup.html:35`, `setup.html:38`, `setup.html:40` | Animating `margin-left` runs layout every frame, forever, on first launch while the installer saturates the CPU. And `ease-in-out` on continuous motion makes both the bar and the sheen hesitate at each end, reading as *stuck* — the exact opposite of the intended "still working" signal. |
| 7 | Chat | Make **every** automatic scroll instant; smooth motion belongs only to an explicit pointer-clicked "Jump to latest" (keyboard activation of it is instant too) | `ChatPanel.jsx:38` | Overlapping smooth scrolls cancel each other, so the transcript judders for an entire agent turn. An earlier draft used `busy ? 'instant' : 'smooth'`, which still lets a keyboard-submitted message or a keyboard-selected thread hit the smooth branch — content arriving is not a "look at this" gesture the user initiated. |
| 8 | Setup | Auto-follow the log only while the reader is near the bottom; copy the `stickRef` pattern | `setup.js:19`, `ChatPanel.jsx:41-45` | Conflict 5. During a failed install the log's purpose is diagnosis and it currently yanks the reader off the line they are on. |
| 9 | Global | Add `--ease-out`/`--ease-in-out` and retire bare `ease` | `styles.css:463`, `:346`, `:1338` | Zero custom curves exist; built-ins are too weak to read as intentional. |
| 10 | Toasts | Give `.toast` a 200ms enter / 140ms exit; give `.update-toast` an exit; convert `update-rise` from a keyframe to a transition | `styles.css:411`, `:422`, `:423`, `UpdateBanner.jsx:21` | One toast system blinks with no motion at all; the other animates in and vanishes instantly. Both should share one contract, exit faster than enter. |
| 11 | Preview | **Remove the duplicate center play button**; do not animate it | `StudioPreview.jsx:500`, `StudioPreview.jsx:501` | Conflict 8. Play/pause is a 100+/day Space action the rubric says never to animate — and clicking the center button unmounts the focused element, dropping focus to the document. RULES.md already names `.st-tl-head` as transport home. |
| 12 | Timeline | Playhead via `transform: translateX()` with `left: LANE_PAD` fixed | `StudioTimeline.jsx:461` | Conflict 14. `left` forces layout on every frame of playback and scrub; transform is composited and also fixes the shimmer. |
| 13 | Studio | Hold the panel-drag cursor globally and suppress selection: mirror `body.st-scrubbing` for splitters | `styles.css:971`, `:972`, `:1063` | Dragging a splitter rubber-band-selects text in both adjacent panels. |
| 14 | Preview | Add `user-select: none` to `.st-clip-box` and `.st-aud-gain` | `styles.css:1003`, `styles.css:1169` | Conflict 7 — codex conceded. Dragging the main clip selects the overlay text drawn on top of it. |
| 15 | Global | `cursor: grabbing` on `:active` for every grab surface | `styles.css:1003`, `:1135`, `:1145`, `:1155`, `:1006` | Five drag surfaces never acknowledge the grab. The dead old editor did it correctly at `:869`. |
| 16 | Global | Gate every hover transform behind `@media (hover: hover) and (pointer: fine)` | `styles.css:465`, `:1008`, `:1142`, `:347` | Trackpad-adjacent environments retain false hover. |
| 17 | Recorder | Convert **only** the recorder ring to a transform/opacity pseudo-element | `styles.css:920-921` | `box-shadow` repaints every frame of an infinite animation. |
| 18 | Pipeline | Delete the redundant `.pulse` dot; keep the ringed bullet | `App.jsx:697`, `styles.css:302`, `styles.css:293` | Two "in progress" indicators 20px apart, one an infinite animation. Deleted here rather than converted — an earlier draft rebuilt `.pulse` as a composited ring in this phase and then deleted it in the craft sweep. Delete once. |
| 19 | Modals/rows | Give `.cap-item` and `.auth-body` a reserved minimum height instead of growing on state change | `CapabilityInstall.jsx:92`, `ConnectClaudeModal.jsx:71` | Pressing Install grows the row ~30px and shoves every pack below it; the auth mode swap jumps the modal ~60px. |
| 20 | Global | Add exact-property color transitions to hard-cut hovers | `styles.css:384`, `:603`, `:1201` | Hover changes across the app are instant jumps. |
| 21 | Chat/pipeline | Replace the `▾`/`▸` glyph swap with one SVG chevron; rotation optional and pointer-only | `ChatPanel.jsx:342`, `App.jsx:698` | Replace the glyph, but do not promise animation for keyboard activation on a frequent disclosure. |
| 22 | Chat | Keep the tool disclosure **instant**; fix the scroll jump with `overflow-anchor` instead | `ChatPanel.jsx:348` | Conflict 9 — animating `max-height`/grid rows is the layout animation this plan bans elsewhere. |

### Phase 4 — North-star alignment

**What lands:** the product's own stated model, reflected in the UI. **Why here:** it needs P2's
primary/secondary distinction before Export can be demoted, and it deletes the fake progress bars before
Phase 3 would otherwise have polished them.

**Settled vocabulary, applied consistently:** the user-facing terminal action is **Export** (final bytes).
**Render / Re-render** remains the language for the internal job, the backend, and any future comp
materialization. This is not reopened as an open question.

| # | Surface | Change | Anchor | Why it matters |
| --- | --- | --- | --- | --- |
| 1 | Toolbar | ~~Rename to **Export** + retitle~~ **DONE in increment 1** (pulled forward: two adjacent controls both read "Render"). Still to do here: demote it off `.st-primary`. | `StudioToolbar.jsx:64` | RULES.md: *"The user should NEVER have to hit Render just to see an edit… it is not the way you preview an edit."* The tooltip says the opposite. codex's best finding. |
| 2 | Preview | Rewrite `'No render yet — hit Render.'` to explain that Source **is** the live editable preview and Export produces final bytes | `StudioPreview.jsx:518` | Same contradiction, on the surface where the north star lives. |
| 3 | Toolbar | Make the Source/Render pair a real labeled two-state control with `aria-pressed`, and mark Source "Live" | `StudioToolbar.jsx:59-61` | It looks segmented but announces as two ordinary buttons and never says which preview is authoritative. |
| 4 | Chat / Capabilities | Replace both fabricated percentages with labeled indeterminate phases | `ChatPanel.jsx:153`, `CapabilityInstall.jsx:36` | Conflict 1. The chat bar climbs to 90% in 24s then sits there for the rest of a multi-minute export. Neither has a real signal to show. |
| 5 | Setup | Keep real step-boundary progress (`onStep` *does* deliver `{pct, end, label}`) and shimmer between boundaries; reach **100% on `onDone`** | `setup.js:38-54`, `setup.js:56`, `setup.js:79` | The only place real progress exists — and today the last thing a user sees is a bar stuck at ~85%. The asymptotic creep between boundaries goes; the measured boundaries stay. |
| 6 | Setup | On failure, freeze the bar at the failure point instead of filling it 100% red | `setup.js:65` | A completely full bar reads as "finished". |
| 7 | Studio | Surface undo at the moment of risk: `flash('ok', 'Deleted — Undo')` with an inline action | `Studio.jsx:373`, `Studio.jsx:638` | Undo exists and works; nothing says so. The rubric's answer to destructive actions is undo, not a dialog. |
| 8 | Keyframes | Style `clear` as destructive and separate it from the presets; add undo | `StudioKeyframes.jsx:68` | Minutes of keyframe work behind a 0.72rem text link 6px from `ken burns`. |
| 9 | Keyframes | Convert the seven prose links into compact `.st-chip` actions | `StudioKeyframes.jsx:60-68` | Seven ~40×14px identical blue links at 6.4px gaps, where six are presets and one destroys work. |
| 10 | Preview | Keep native `<video controls>` in render mode **until** the shared transport offers seek, volume and keyboard parity | `StudioPreview.jsx:505` | RULES.md puts transport in the timeline, but the native control is currently the *accessible* one. Removing it before the replacement is equivalent is a regression. A deliberate lag. |

### Phase 5a — Icon system (parallel-safe immediately after P1)

**What lands:** RULES.md icon compliance. **Why here:** it depends on nothing but the token block, it is the
cheapest visible craft win in the plan, and an earlier draft stranded it behind the poster/thumbnail work
for no reason. Give it to whoever is free after P1.

| # | Surface | Change | Anchor | Why it matters |
| --- | --- | --- | --- | --- |
| 1 | Icons | Add `IconPause`, `IconTrash`, `IconScissors`, `IconCopy`, `IconArrange`, `IconDownload`, `IconFullscreen` to `icons.jsx` | `icons.jsx:20` | Prerequisite for everything below — the set is currently missing the exact icons the toolbar needs. |
| 2 | Timeline / Inspector / Pipeline | Remove the real emoji: `🗑` and `⏳` | `StudioTimeline.jsx:266`, `StudioInspector.jsx:489`, `App.jsx:704` | Direct RULES.md violation, and `StudioTimeline.jsx:34` claims "no emoji per the studio UI convention" 232 lines above one. |
| 3 | Timeline / Preview / Assets | Replace `▶`/`⏸`, which render as color emoji on macOS | `StudioTimeline.jsx:272`, `StudioPreview.jsx:501`, `App.jsx:1387` | `IconPlay` already exists at `icons.jsx:20`. |
| 4 | Global | Replace the fullwidth CJK `＋`/`－` used as icons | `StudioToolbar.jsx:24`, `StudioTimeline.jsx:278`, `:280`, `ChatPanel.jsx:74`, `App.jsx:225` | U+FF0B/U+FF0D carry CJK advance widths, so they sit optically off-center in a Latin button at every size. |
| 5 | Global | Replace the remaining ~20 semantic glyphs (`⚠ ⛶ ⤓ ■ ✎ ✕ ×`) with icons; leave punctuation arrows in prose | `ChatPanel.jsx:85`, `:129`, `:294`, `App.jsx:551`, `:1335`, `:1338`, `AssetModal.jsx:52`, `:53` | `IconAlert` is imported at `ChatPanel.jsx:9` and used at `:102` — the file contradicts itself. Prose arrows need not become SVGs. |
| 6 | Timeline | Import `IconEye`/`IconEyeOff` instead of the inline 13px/stroke-2 duplicates | `StudioTimeline.jsx:35-47`, `icons.jsx:13-14` | Two copies of one icon at different stroke weights in the same UI; stroke weight is the one thing an icon set must hold constant. |
| 7 | Assets | Share one `<AssetTile>` between the two browsers | `StudioAssets.jsx:72`, `App.jsx:1387` | The same `.asset-play` class renders an SVG in one panel and a color emoji in the other. |
| 8 | Pipeline | Replace the literal `{}` artifact icon with `IconFileText` | `App.jsx:674`, `App.jsx:712` | One list, two icon languages — the decision-log chip nine lines up already uses a real icon. |

### Phase 5b — Vertical-first identity

**What lands:** the app starts looking like a tool for making vertical video. **Why here:** it needs P4 so
posters do not imply "render first", and it is **gated on the frame cache in item 1** — this is the
highest-risk work in the plan.

| # | Surface | Change | Anchor | Why it matters |
| --- | --- | --- | --- | --- |
| 1 | Backend | **Gate:** before any thumbnail ships, give frame extraction lazy loading, a concurrency cap, and a bounded cache keyed by `project / source / source-mtime / time-bucket / size` — with no tempfile left per request | `server/app.py:657-664`, `api.js:151` | `/frame` currently spawns a synchronous ffmpeg **and writes a fresh temporary JPEG for every request**, with no cache, no concurrency limit and no cleanup. A filmstrip across visible clips would fan out dozens of processes and temp files. codex's sign-off names this the plan's highest risk, and it is right. |
| 2 | Dashboard | Replace the hashed rainbow cover with **one cached poster** — first cut's source at t≈1s | `App.jsx:229-230` | Conflict 3. One poster per project, cached, is safe once item 1 lands. t≈1s is the decided default, so this is not blocked on a product answer. |
| 3 | Timeline | **One** cached poster per visible clip as the `.st-clip` background; repeating filmstrips deferred until item 1's cache is proven under load | `styles.css:1134`, `api.js:151` | Clip identity is the single biggest "this is a real NLE" signal for an ICP coming from CapCut, and identifying a clip by basename in a 46px block is the daily friction of using this editor. One frame gets most of that value at a fraction of the fan-out. |
| 4 | Assets | Derive the render card's aspect from the project canvas, not a hardcoded ratio | `styles.css:334`, `model.js:28-33` | Today it is `16/9` in a vertical-video app; but `CANVAS_PRESETS` offers 9:16, 1:1, 16:9 *and* 4:5, so hardcoding 9:16 is equally wrong. |
| 5 | Assets | Thumbnails: bounded frame + intrinsic ratio + `object-fit: contain`, never a fixed crop | `styles.css:350`, `styles.css:1221` | `object-fit: cover` at `height: 80px` reduces a 9:16 frame to an unidentifiable center band — and RULES.md says users drop any media, so a fixed 4:5 would just move the damage. |
| 6 | Assets | Give the deliverable more than the `0.85fr` column, or a dedicated row | `styles.css:52` | The finished reel is previewed in the narrowest of three columns. |
| 7 | Timeline | `.st-aud.pt` → 12×12, not `border-radius: 50%` on a 12×20 box | `styles.css:1160`, `styles.css:1154` | RULES.md asks for "a bare dot"; the app draws a squashed ellipse. Found by neither auditor in round 1. |

### Phase 6 — States and keyboard

**What lands:** the loading/empty/error contract, one accessible dialog shell, and keyboard reach into the
timeline. **Why here:** largest engineering surface; depends only on P1.

| # | Surface | Change | Anchor | Why it matters |
| --- | --- | --- | --- | --- |
| 1 | All modals | One `<Modal>` shell: `role="dialog"`, `aria-modal`, `aria-labelledby`, focus trap, focus restore, and a single `requestClose()` with a dirty guard | `App.jsx:299`, `:411`, `:493`, `:875`, `CapabilitiesModal.jsx:33`, `ConnectClaudeModal.jsx:57`, `DebugReportModal.jsx:55`, `AssetModal.jsx:47` | Conflict 4. Eight surfaces, zero with dialog semantics; seven ignore Escape; a slight miss outside the card discards a pasted OAuth code. |
| 2 | All modals | Make the background inert while a dialog is open — Studio's window shortcuts currently fire behind every modal except AssetModal | `Studio.jsx:626-631`, `AssetModal.jsx:38` | The guard only exempts `INPUT`/`TEXTAREA`/`SELECT`/`role=slider`, so pressing Space or Delete with focus on a modal *button* toggles playback or deletes a clip behind the dialog. AssetModal's capture-phase handler documents the correct pattern. **A real bug, not a polish item.** |
| 3 | AssetModal | Give Download, Previous, Next and Close explicit `aria-label`s | `AssetModal.jsx:51-53`, `:57`, `:70` | Swapping the glyphs for SVGs in P5a **removes** the only accessible name these controls have — a `title` plus a glyph. The label must land with or before the icon swap, not after. |
| 4 | Assets | Browse must model `loading` / `empty` / `error` separately, with Retry | `FolderBrowser.jsx:34` | `.catch(() => setEntries([]))` renders a backend 500 as "This folder is empty." |
| 5 | Assets | Optimistic uploading tile: filename, indeterminate progress, cancel, error, retry | `StudioAssets.jsx:34`, `App.jsx:140` | For an ICP dropping multi-hundred-MB screen recordings, silence after drop reads as failure. |
| 6 | Global | One accessible spinner + skeletons only where geometry is known (project tiles); static status text under reduced motion | `Studio.jsx:645`, `CapabilitiesModal.jsx:47`, `App.jsx:424`, `:743`, `:893`, `AssetModal.jsx:94` | **Zero spinners and zero skeletons exist.** Every load state in the app is the literal string "Loading…" in 3.578:1 grey. |
| 7 | Studio | Errors persist with Close and Retry; success times out; warnings time out when resolved | `Studio.jsx:98`, `Studio.jsx:650` | Today only `ok` auto-dismisses, so "Export failed" pins forever with no dismiss — but auto-dismissing errors would be worse. A fatal project load also has no retry path at all. |
| 8 | Chat | Three concise starter prompts in the empty state, secondary to the composer. **QA increment 1 finding V6** confirmed this live: one line of copy above ~600px of nothing (`.local/ui-qa/studio-1440x900.png`). Deferred to here deliberately — it is new empty-state composition, not P0–P3 token/motion work. | `ChatPanel.jsx:79` | The highest-leverage onboarding surface for founders who have never used an AI video agent is one line of 3.578:1 grey text. |
| 9 | Dashboard | Distinguish loading from empty; real first-project empty state after load resolves. **QA increment 1 finding V4** confirmed this live: one dashed tile in a 1440×900 void, no headline, no guidance (`.local/ui-qa/dash-empty-1440x900.png`). The *other* half of V4 — the account action out-competing New project for attention — was fixed in increment 1 by making `.reauth-btn` neutral. | `App.jsx:223` | A first-time user sees a header, a CTA, and one dashed `+` tile with no indication what a project is. |
| 10 | Timeline | Give clips, overlays and audio blocks focus, `role="option"` in a lane listbox, `aria-selected`, accessible names, arrow-key nudge and Delete. **The nudge math and mutation go in `web/src/editor/interp.js` as a pure mutator with unit tests** (bounds clamping, immutable same-ref no-op when the value cannot change, exactly one undo step per keypress run); `StudioTimeline.jsx` handles keydown and dispatch only | `StudioTimeline.jsx:327`, `:359`, `:404`, `:427`, `:445`, `editor/interp.js` | The core editing surface is entirely pointer-only. RULES.md is explicit: *"New editing behavior → a pure function in `interp.js` (or a `model.js` helper) + a test. Don't bury logic in a component."* Nudge is new editing behavior, so it must not be written inline in the component — and the same-ref no-op rule is what keeps history and dirty-detection correct. **Scoped deliberately:** nudge, select and delete now; full keyboard trim/move deferred (see Not doing). |
| 11 | Inspector | Unbounded scrub fields → `role="spinbutton"`; keep `slider` only where finite min *and* max exist | `StudioInspector.jsx:128`, `:275`, `:278`, `:311`, `:316`, `:351`, `:354` | `role="slider"` requires `aria-valuemin`/`max`; X, Y, Scale, crop and position pass neither, so they announce as broken. codex: *"an important miss in mine."* |
| 12 | Assets | Dropzone → `<label>` wrapping a `.sr-only` (not `hidden`) file input | `StudioAssets.jsx:87`, `App.jsx:1356` | A `<div onClick>` with no role or tabIndex around `<input type="file" hidden>` — `hidden` removes it from the a11y tree, so **upload has no focusable element at all** in either panel. `<label>` makes it keyboard-operable with zero JS. |
| 13 | Studio | Splitters → focusable `role="separator"` with `aria-valuenow` and arrow-key resize | `Studio.jsx:691`, `:708`, `:732` | Panel resize and reveal are core layout controls that only work with a pointer. |
| 14 | Studio | Widen the collapsed-panel reopen strip to ≥28px and label the destination | `styles.css:976`, `Studio.jsx:696`, `:726` | An 18px unlabeled `‹`/`›` strip is the only way back once a panel is dragged shut. |
| 15 | Timeline | Trim handles: 24px invisible hit area, 2px visible grip, shown on selection and focus not only hover | `styles.css:1140`, `StudioTimeline.jsx:355` | Merged fix — codex's 24px target with claude's persistent grip. On a min-width clip the two 9px handles cover the whole block, making body-drag unreachable. |
| 16 | Timeline | Music gain: larger hit target, visible dB/percent during drag, keyboard arrows | `StudioTimeline.jsx:415`, `styles.css:1169` | The only way to set music volume, with the value living solely in a native `title`. |
| 17 | Timeline | SFX marker: selection halo, larger hit box, keyboard nudge — identity in the properties panel, **not** on the block | `StudioTimeline.jsx:443` | RULES.md mandates the blankness (*"SFX point markers = a bare dot"*), so the discoverability fix must stay off the marker. |
| 18 | Global | `aria-live`: `role="status"` for success, `role="alert"` for errors | `App.jsx:150`, `Studio.jsx:681` | Save results, export failures and upload errors are silent to assistive tech. `UpdateBanner.jsx:29` is the only surface that gets this right. |
| 19 | Chat / Pipeline | `aria-expanded` + `aria-controls` on tool and stage disclosures | `ChatPanel.jsx:341`, `App.jsx:692` | Caret direction is not an accessible state. |
| 20 | Chat | Accessible names for the composer textarea and the thread/model selects | `ChatPanel.jsx:113`, `:61`, `:133` | Placeholder copy is not a durable label and vanishes on first keystroke. |
| 21 | Global | Segments, speed presets and tabs get pressed / radio / tab semantics | `StudioToolbar.jsx:60`, `StudioInspector.jsx:291`, `App.jsx:623`, `:740`, `:1242` | Selected state exists only as a background color. |
| 22 | Timeline | `aria-label="Timeline zoom"` + `aria-valuetext` in px/sec | `StudioTimeline.jsx:279` | An unnamed slider between two unlabeled symbol buttons. |
| 23 | Activity | Clickable file rows → real buttons | `App.jsx:823` | A visible "view →" action that is mouse-only; `App.jsx:1380` in the same file does it correctly. |
| 24 | Global | Raise sub-24px targets to the WCAG 2.5.8 floor | `styles.css:1114`, `:949`, `:78`, `:1199`, `:539`, `:430` | The breadcrumb, at ~14px tall, is the only way back up the asset tree. |
| 25 | Setup | `role="status"` on `#stage` only — **not** on `#log` | `setup.html:64`, `setup.html:69` | Announcing the whole installer log would spam screen readers. Keep the log navigable and opt-in. |
| 26 | Global | Build one tooltip primitive: opens on focus and hover, no motion for keyboard, skip-delay after the first | `StudioTimeline.jsx:264`, `StudioInspector.jsx:131` | Native `title` is the app's only tooltip mechanism at ~40 sites — a ~1.5s unstyleable delay, so every affordance hint is slow and off-brand. |
| 27 | Assets | Keep autoplay-on-step, but debounce the mount ~150ms and start muted with a sticky unmute | `AssetModal.jsx:59`, `:57`, `:70` | Conflict 13. `key={item.url}` remounts `<video autoPlay>` on every step, so a held arrow fires a burst of unmuted playback. Gating on keyboard-vs-click would fix half the bug and break the scan-through-takes workflow the ICP needs. |
| 28 | Chart | Screen-reader summary/table fallback and keyboard point traversal | `LineChart.jsx:54` | `role="img"` makes the artifact's actual content inaccessible. |
| 29 | Artifact modal | `aria-label="Close"` + the shared icon | `App.jsx:888` | The one close button in the app with neither. |

### Phase 7 — Craft sweep

**What lands:** the compounding details. **Why here:** independent of everything after P1; interleave it
whenever another phase is blocked.

| # | Surface | Change | Anchor | Why it matters |
| --- | --- | --- | --- | --- |
| 1 | Shell | Inline `<style>html,body{background:#faf7f2}</style>` in the head | `web/index.html:8` | The Electron window paints **white** until the module graph resolves, then flashes to cream — on every launch and every ⌘R. One line. |
| 2 | Project bar | Fix the phantom `<span className="dot" />` | `App.jsx:546`, `styles.css:31` | `.dot` is only styled as `.brand .dot`, so this renders a zero-size unstyled span that contributes nothing but an 8px gap. An implementation bug, not taste. |
| 3 | App | One toast timer per channel, cleared and re-armed | `App.jsx:63-70` | An `ok` toast's stale 3s timer erases a newer error toast two seconds early. |
| 4 | Feedback | Never render the privacy toggle in the wrong state while loading | `App.jsx:346` | While `/api/analytics` is in flight the checkbox reads "opted out". A privacy control must not assert a wrong value, even for 200ms. |
| 5 | Numerics | `font-variant-numeric: tabular-nums` where width jitter or column alignment is visible | `styles.css:264`, `:1333`, `:141`, `:387` | Changing percentages, costs and aligned columns — not every static count. |
| 6 | Keyframes | Give the playhead time a stable `ch` width, or move it out of the button label | `StudioKeyframes.jsx:78` | `+ at {playhead.toFixed(1)}s` reflows its own toolbar row during playback. |
| 7 | Capabilities | Reserve width for the **status text** — `Installing…` / `Installed` / `Failed` — with no numeric percentage | `CapabilityInstall.jsx:89`, `styles.css:1329` | The row shifts on every log line. The percentage itself is deleted in Phase 4 item 4, so reserving width for a number would re-introduce the thing that phase removes. |
| 8 | Timeline | Delete the JS `slice(0, 18)` and let the existing CSS ellipsis do the work | `StudioTimeline.jsx:325`, `styles.css:1147` | Double truncation produces `“This is my long te”` — a hard mid-word cut inside smart quotes that reads as corrupt data. |
| 9 | Artifact modal | Make Raw/Formatted a two-state segmented control | `App.jsx:886` | The label names the destination, so "Raw JSON" is ambiguous. The pattern exists at `StudioToolbar.jsx:59`. |
| 10 | Chat | `align-self: flex-end` on the composer action | `styles.css:92`, `styles.css:89` | A ten-line message turns Send into a 220px-tall terracotta slab. |
| 11 | Dashboard | Group the four header actions into a settings menu; rename **BYOK → API keys** | `App.jsx:185-201` | Four equal-weight quiet pills compete with the primary task, and "BYOK" is developer jargon on the first screen a founder sees. |
| 12 | Global | Style scrollbars warm and narrow; add `color-scheme: dark` to the dark `<pre>` panes | `styles.css:85`, `:1088`, `:188`, `:636` | Twelve scroll regions with default chrome, and light UA scrollbars over `#1e1c18` panels. |
| 13 | Global | `scrollbar-gutter: stable` **only** where classic scrollbars produce measured shift | scroll containers | Forcing gutters everywhere wastes width in already narrow panels under macOS overlay scrollbars. |
| 14 | Inspector | Collapse advanced sections; lead with identity and timing | `StudioInspector.jsx:415` | Seven schema types render through one flat `.st-sec` treatment with no scan hierarchy. Do **not** add per-section save indicators — RULES.md's autosave model already owns that; fix the one global indicator instead. |
| 15 | Assets | Put the "Assets" heading before the Canvas-background control | `StudioAssets.jsx:40-45` | The panel's first control belongs to project settings, so its purpose reads late. |
| 16 | Assets | Promote the drag hint out of 11px `--muted`, **and** give tiles a visible focusable drag affordance with `cursor: grab` — while keeping the modal's explicit Add action as an equally obvious alternative | `StudioAssets.jsx:49-50`, `:60`, `styles.css:346` | Darker hint text alone does not make drag discoverable. The tile is already `role="button" tabIndex={0}` for click-to-open, so the grab affordance must read as *additional*, not as a replacement for the Add path — that path is what keyboard users have. |
| 17 | Inspector | Advertise click-to-type on scrub fields with a hover/focus edit glyph | `StudioInspector.jsx:128` | Half of the inspector's signature dual gesture is invisible. A glyph, not an underline, which would read as a link. |
| 18 | Chart | Coalesce `setHoverPx` to at most one update per animation frame and cancel the pending frame on leave/unmount | `LineChart.jsx:46` | A React re-render plus a `nearest()` reduction over every series × every point fires on every mousemove event. **Qualified:** instrument first — if no frame ever receives more than one `mousemove`, log that and defer the change rather than adding machinery for a problem that is not there. Either way, the pending-frame cancel on unmount is worth doing. |
| 19 | Layout | Replace the single 900px breakpoint with a container query or `minmax()` off the real tile minimum | `styles.css:52`, `styles.css:761` | Between 900 and ~1150px the Assets column holds two ~115px tiles. Derive it; do not guess a viewport width. |
| 20 | Setup | Map setup type to the shared size/line-height roles and use the app font stack | `setup.html:15`, `:20`, `:52` | `px` vs `rem` is not itself the defect in a fixed window — the unrelated values and roles are. |

---

## Token foundation

Add to `:root` (`styles.css:1-19`). **Every ratio below was computed against the sRGB relative-luminance
formula, not estimated.** Where a value replaced an earlier candidate, the rejected number is shown so
nobody re-proposes it.

```css
:root {
  color-scheme: light;       /* declare the intentional light editorial scheme so native
                                scrollbars and form controls render coherently. forced-colors
                                support is explicitly deferred. */

  /* ── surfaces ── */
  --bg:            #faf7f2;  /* app canvas — unchanged */
  --panel:         #fffdf9;  /* raised panel — unchanged */
  --field:         #f6f1e8;  /* NEW recessed control fill — replaces 41× `background: #fff`
                                (styles.css:43, :1025, :822, :1073, …). #fff on --panel is
                                1.016:1, i.e. the current "lift" is invisible; --field sits
                                1.107:1 below the panel, which reads as recessed. */
  --wash:          #f2ede4;  /* NEW — replaces #f0ece4, #f6f2ec, #efe9df, #fbf7f0 */
  --wash-strong:   #e9e2d6;  /* NEW — replaces #e8e2d8, #ede9e0 */
  --pre-bg:        #1e1c18;  /* NEW — replaces 4× #1e1c18 (:153, :188, :636, :374) */
  --frame:         #15120e;  /* NEW — replaces 2× #15120e (:990, :1222) */
  --on-dark:       #d8d0c4;  /* ADDED in increment 1, documented at QA round 2. The ONLY ink
                                for text on --pre-bg / --frame: 11.129:1 and 12.215:1.
                                INVARIANT — --ink, --ink-dim and the status *-ink tokens are
                                calibrated for LIGHT surfaces and must never land on a dark
                                one. Two violations were found and fixed: `.rp-pct` set
                                --ink-dim on --pre-bg (3.022:1, an AA failure that the
                                migration introduced) and `.al-stage` set no colour at all,
                                so the asset-lightbox music icon inherited --ink at 1.147:1
                                (pre-existing). The second was fixed on the SURFACE rule so
                                future children inherit correctly rather than each needing an
                                override — any new dark surface must declare its ink the same
                                way. */

  /* ── ink ── */
  --ink:           #2b2722;  /* unchanged */
  --ink-dim:       #6f665c;  /* NEW — 5.267:1 on --bg, 5.540:1 on --panel, 4.827:1 on --wash.
                                For the 129 var(--muted) TEXT uses, and for disabled labels. */
  --muted:         var(--ink-dim);  /* AMENDED at QA increment 1 (was #8a8178). Aliased
                                deliberately as a fail-safe: 123 of the 128 uses of the old
                                grey were text, and aliasing means any site the migration
                                missed — and any NEW `color: var(--muted)` written later —
                                is accessible by default instead of silently re-introducing
                                a 3.578:1 failure. codex raised this as Polish 1 (code and
                                plan disagreeing); the split is the better engineering, so
                                the plan follows the code. */
  --muted-decor:   #8a8178;  /* 3.578:1 on --bg. The ONLY non-text uses of the old grey:
                                the dashed chart focus line (styles.css .lc-focus), one
                                hover border (.ak-skip), one idle status dot (.st-rec-dot). */

  /* ── boundaries: the split that fixes "the UI has no edges" ── */
  --line:          #ece5db;  /* unchanged — 1.231:1 on --panel. Dividers, card edges,
                                table rules ONLY. */
  --border:        #98886f;  /* NEW — 3.396:1 on --panel, 3.229:1 on --bg, 3.067:1 on --field.
                                Interactive boundaries ONLY: buttons, inputs, selects, tabs,
                                chips, scrub bars, dropzone (replaces var(--line) at :43,
                                :903, :1025, :1034, :405).
                                REJECTED: #a09079 (3.058 panel / 2.907 bg / 2.761 field —
                                fails on two of the three control surfaces) and #ab9c85
                                (2.641 on panel; an earlier draft mislabelled it "~2.8"). */

  /* ── accent: split so white labels and text links pass AA ── */
  --accent:        #c8643c;  /* unchanged — decorative fills, chips, covers.
                                White on it is 3.930:1, which is why it must not carry labels. */
  --accent-ink:    #a44a26;  /* NEW — 5.856:1 under white, 5.480:1 as text on --bg.
                                Replaces --accent at styles.css:45 and every accent text use. */
  --accent-soft:   #f4e3da;  /* unchanged */

  /* ── status: hue stays as fill, *-ink carries text on the soft fill ── */
  --amber: #c9952b;  --amber-soft: #fdf2dc;  --amber-ink: #8a6410;  /* 4.835:1 on soft */
  --green: #4a8a55;  --green-soft: #e0f0e3;  --green-ink: #34663d;  /* 5.698:1 on soft */
  --red:   #b8503f;  --red-soft:   #fbeae6;  --red-ink:   #96382a;  /* 6.220:1 on soft */
  --blue:  #3b6ea5;  --blue-soft:  #dce8f5;  --blue-ink:  #33608f;  /* 5.265:1 on soft */
  /* --blue is 4.963:1 on --bg and passes as a link color there. But --blue on --blue-soft
     is only 4.269:1 — a marginal AA failure at .ss-cue (:689) and .q-option code (:123)
     that neither audit measured. Use --blue-ink on any soft-blue fill. */

  /* ── spacing: px, to sidestep the rem-base drift entirely ── */
  --space-05: 2px;            /* ADDED at QA increment 1. The smallest real values in the
                                 file were 1.9-3.5px (pill and badge padding); rounding those
                                 up to 4px would have inflated ~15 badges by 2.4px a side. A
                                 2px step is a genuine need, not a near-duplicate. */
  --space-1: 4px;  --space-2: 8px;  --space-3: 12px;  --space-4: 16px;
  --space-5: 24px; --space-6: 32px; --space-7: 48px;
  /* replaces the 0.05rem continuum: 0.12/0.15/0.18/0.2/0.22/0.25/0.28/0.3/0.32/0.35/
     0.38/0.4/0.42/0.45/0.5/0.55/0.6/0.65/0.7/0.75/0.8/0.85/0.9/0.95rem */

  /* ── type: 7 UI steps + 1 display, replacing 41 values. 11px is the hard floor. ── */
  --text-2xs:     11px;  /* uppercase micro-labels only — replaces 0.58/0.6/0.62/0.64rem, 9px */
  --text-xs:      12px;  /* replaces 0.65–0.72rem */
  --text-sm:      13px;  /* replaces 0.74–0.78rem */
  --text-base:    14px;  /* body — replaces 0.8–0.88rem */
  --text-lg:      16px;  /* replaces 0.9–1rem */
  --text-xl:      20px;  /* replaces 1.02–1.15rem */
  --text-2xl:     28px;  /* replaces 1.4–1.6rem */
  --text-display: 40px;  /* <5 uses — replaces 2.1/2.5/2.6/3rem */
  --lh-tight: 1.25;  --lh-base: 1.5;  --lh-relaxed: 1.6;

  /* ── radii: 4, replacing 12 ── */
  --radius-sm:   6px;    /* replaces 4px, 5px, 6px, 7px */
  --radius-md:   10px;   /* replaces 8px, 10px, 12px — same value as today's --radius */
  --radius-lg:   16px;   /* replaces 14px, 16px */
  --radius-pill: 999px;
  --radius:      var(--radius-md);  /* alias so existing uses keep working */

  /* ── elevation: 3 levels + 1 ring, replacing 17 recipes. Tinted with --ink, not
        black, so shadows stay warm. ── */
  --shadow-1: 0 1px 2px #2b272212, 0 2px 8px #2b272210;   /* cards, chips, hover lift */
  --shadow-2: 0 4px 16px #2b27221a;                        /* popovers, toasts */
  --shadow-3: 0 16px 48px #2b272226;                       /* modals, lightbox */
  --ring-accent: 0 0 0 2px var(--accent-soft);             /* selection — replaces 6 uses */

  /* ── motion: none of this exists today ── */
  --ease-out:    cubic-bezier(0.23, 1, 0.32, 1);
  --ease-in-out: cubic-bezier(0.77, 0, 0.175, 1);
  --dur-press: 120ms;  --dur-hover: 140ms;
  --dur-enter: 200ms;  --dur-exit:  140ms;   /* exit deliberately faster than enter */
  --dur-modal: 240ms;
}
```

Two judgment calls worth stating. **`--border` is intentionally heavier than today's hairline.** WCAG 1.4.11
requires 3:1 for control boundaries, and "controls have no edges" is the single most-cited defect in both
audits; `#98886f` is the lightest warm neutral that clears 3:1 on all three surfaces a control can sit on.
Cards and dividers keep the `--line` hairline so the layout stays delicate. There is no lighter fallback:
the two that were proposed both measure under 3:1 and are recorded as rejected above. **Spacing is px, not
rem,** deliberately — it removes any dependence on the root-size question, and nothing here needs spacing to
scale with user font size.

---

## Verification

Cheap checks first, then a manual pass. **Run `scripts/dev test fast` at the end of every phase**; run
`scripts/dev test full` and `scripts/dev smoke` before final review. Every grep below is written so that it
can actually fail — the earlier draft of this plan contained several that could not.

**Phase 0 — preflight.** Confirm the dead block is gone and nothing else referenced it:
```
rg -n '\.(editor|insp|kfe|prev)-|\.tl-(ruler|playhead|clip|lane|handle)' web/src/styles.css
rg -n 'st-insp-empty|st-select-btn|st-grp-ctx' web/src
```
Both must return nothing. A count-only check on `.editor-` alone would pass while `.insp-*`, `.prev-*`,
`.tl-clip`, `.tl-lane` and `.tl-handle` survived, which is why this matches every dead family.

**Phase 1 — tokens.** Measure the scales *after* P0, so dead code is not inflating them:
```
rg -o 'font-size: *[0-9.]+(rem|px)' web/src/styles.css | sort -u | wc -l   # 41 -> <=9
rg -o 'border-radius: *[0-9]+px'    web/src/styles.css | sort -u | wc -l   # 11 -> <=4
rg -o 'box-shadow: *[^;]+'          web/src/styles.css | sort -u | wc -l   # 18 -> <=5
rg -o '#[0-9a-fA-F]{3,8}\b'         web/src/styles.css | sort -u | wc -l   # 75 -> <=30
rg -n 'color-scheme'                web/src/styles.css                     # must match
```
Then re-verify every published pair in a contrast checker: `--ink-dim`/`--bg` ≥ 4.5, white/`--accent-ink`
≥ 4.5, each `*-ink`/`*-soft` ≥ 4.5, `--border` ≥ 3.0 against **`--panel`, `--bg` *and* `--field`**. A token
family whose commit changed layout unexpectedly is a mis-mapped spacing or type role — bisect by family.

**Phase 2 — legibility.**
```
rg -c 'filter: none' web/src/styles.css        # 23 -> 0; any survivor means the global
                                               # button hover was not actually removed
rg -n 'focus-visible' web/src/styles.css       # must include the :where(...) rule
rg -n 'flex-wrap: wrap' web/src/styles.css     # must NOT match .st-bar or .st-tools
```
Then resize the window to the minimum supported Electron width with a 60-character project name open: the
toolbar must stay on one line and overflow into the More menu. Screenshot the dashboard, studio toolbar and
inspector against `main` — the diff should read as "same layout, real controls".

**Phase 3 — motion.** Assert on named selectors, not counts — a count passes with one empty block or five
irrelevant rules:
```
rg -n 'prefers-reduced-motion' -A6 web/src/styles.css   # block must name .pulse,
                                                        # .st-rec-dot and the setup animations
rg -n ':active' web/src/styles.css                      # must name .st-btn, .st-ico,
                                                        # .st-play, .tile, button
rg -n 'transition:\s*width|@keyframes\s+slide|animation:\s*slide' web/src desktop
rg -n 'transition: all' web/src/styles.css              # -> 0; this belongs to Phase 3,
                                                        # which is the phase that removes it
```
The third pattern replaces a naive `margin-left` grep that could never reach zero: the app legitimately uses
static `margin-left: auto` in nine rules (`styles.css:505`, `:673`, `:685`, `:694`, `:758`, `:898`, `:1249`,
`:1311`, `:1329`). Then: hit ⌘S in the editor and watch the timeline — **the canvas must not move**. Scrub
during playback with paint-flashing on; only the playhead layer should repaint. Enable Reduce Motion and
confirm the infinite animations stop.

**Phase 4 — north star.** Read the toolbar and the empty preview aloud. If either sentence tells a user to
press a button to see an edit, it is not done. `rg -n 'Render preview|hit Render' web/src` must return
nothing. Start an export and confirm no bar ever shows a number the system cannot know. Delete a clip and
confirm an inline Undo appears.

**Phase 5a — icons.** Explicit list, not a Unicode range — the earlier draft's ranges missed five named
offenders (`⚠ ⛶ ⤓ ✎ ✕`) and so could pass with all of them still shipping:
```
rg -n '🗑|⏳|▶|⏸|⚠|⛶|⤓|■|✎|✕|＋|－' web/src --glob '*.jsx'
rg -n '>×<|>&times;<' web/src --glob '*.jsx'        # close-button glyph
```
Both must return nothing outside comments. (`rg` is used rather than `grep -P` for portability and because
the explicit list is what makes the check correct.)

**Phase 5b — vertical-first.** Verify the cache gate before anything else: request the same frame twice and
confirm the second is served from cache, then confirm no tempfile accumulates and concurrent requests are
capped. Only then: open the dashboard — no tile may be teal, lime or magenta. Open a 9:16 project — the
clip shows a frame, the render card is portrait, no thumbnail is cropped past recognition.

**Phase 6 — states and keyboard.** Add focused tests, then walk it: component tests for **modal focus and
shortcut isolation** (assert that a `keydown` of Space or Delete with focus inside an open dialog does not
reach Studio's window handler), and **pure-core tests for nudge** in `interp.test.js` (bounds clamping, the
same-ref no-op, one undo step per run). Then keyboard-only, no mouse: Tab from the dashboard into a project,
open Feedback, confirm Tab stays inside, press Escape, confirm focus returns to the opening button. With a
modal open press Space and Delete — **nothing may happen to the timeline behind it.** Tab to the dropzone and
press Enter — the picker must open. Tab to a clip, arrow-nudge, Delete, ⌘Z. Stop the backend and open Assets:
it must say "couldn't load" with Retry, not "empty".

**Phase 7 — craft.** Launch and watch the first frame: no white flash. Type a ten-line chat message: Send
stays button-height. Watch a capability install: the row must not shift horizontally and must show no
percentage.

---

## Not doing / deferred

Reconciled from both audits' exemption lists plus the ratification.

| Not doing | Why | What would change it |
| --- | --- | --- |
| Replacing the cream/terracotta palette or adding a dark theme | It is the correct direction; the defects are contrast and sprawl, not hue. Both auditors agreed. Note the light scheme is now *declared* in P1 rather than assumed. | A product brief change. |
| `forced-colors` / high-contrast mode support | Out of scope for a polish pass, but it must stay authoritative over `accent-color` when it does land. | A user need or a compliance requirement. |
| Lightening the dark preview surround (`styles.css:990`) | A neutral dark surround improves edge perception and colour judgment on the frame. Not evidence the app wants dark chrome. | Testing colour judgment against a lighter surround. |
| `transform-origin: center` on canvas overlay scale (`StudioPreview.jsx:438`) | Content-animation geometry that mirrors FFmpeg's center-anchored scale. The popover-origin rule does not apply. | Renderer geometry changing. |
| **Touching the authored keyframe presets** (`model.js:173-186`) — `fade_out`'s `easing: 'ease-in'`, the 350–500ms durations | These are **exported video content**, not application chrome. They feed the FFmpeg path, so "fixing" them to the UI-motion rubric would change exported pixels and break `preview == export`. codex's scope boundary; claude concedes it would likely have made this mistake. | Nothing. This is a hard line — quote it to anyone who runs the motion rubric over `model.js`. |
| **A per-clip comp staleness badge with a local "Re-render this comp" action** | An earlier draft put this in Phase 4. It prescribes UI for data that does not exist: `CUT_FIELDS` (`interp.js:27`) is `['id','source','in_seconds','out_seconds','speed','layer',…]` with no runtime, comp or staleness field, the schema is `additionalProperties: false`, and no receipt lookup, API or mutator is exposed to the timeline. Comp *classification* is inferable from the `hf/renders/` source path (RULES.md:111) and *project-level* render staleness already exists (`r.current`/`r.reason`, `App.jsx:1323-1327`) — but per-clip staleness does not. This is the same error claude caught codex on in Conflict 1, and it would have been ours. | A schema field or a receipts endpoint that exposes per-comp staleness, plus the mutator to trigger one comp's re-render. Then it goes back into Phase 4. |
| Repeating filmstrips across timeline clips | Phase 5b ships **one** cached poster per visible clip. Filmstrips multiply the fan-out against an endpoint that currently spawns an ffmpeg per request and writes a tempfile each time (`server/app.py:657-664`). | The Phase 5b item 1 cache proven under load with a concurrency cap. |
| Deleting the five dead `editor/*.jsx` files (690 lines) | Conflict 6 — codex won. The dead **CSS** ships to users and goes in P0; deleting source is repo cleanup with a wider regression surface and no user-visible gain. Zero importers exist (verified: only four self-references inside the dead cluster, no test, no HTML entry), so this is a scheduling call, not a doubt. | Explicit authorization that the polish pass may remove dead code; then it is a 10-minute follow-up PR. |
| Renaming the live Activity `tl-*` classes to `act-*` (`styles.css:618-622`) | Real collision with the dead editor's `tl-*` prefix, but maintainability rather than user-facing polish. Do it inside the `editor/` JSX cleanup. | Bundling it with the deletion above. |
| Full keyboard trim/move on timeline objects | P6 item 10 ships focus, selection, arrow-nudge and Delete through a tested pure mutator. Full keyboard trimming is a large surface, and for this ICP — founders editing with a mouse and trackpad — it is not the core loop. **Deliberate accessibility/ICP trade, stated openly.** | A user who needs it, or a compliance requirement. Selection + nudge already removes the worst of the exclusion. |
| Hiding the native `<video controls>` in render mode (`StudioPreview.jsx:505`) | RULES.md puts transport in the timeline, but the native control is currently the *accessible* one. Removing it before the shared transport has seek, volume and keyboard parity is a regression. | The shared transport reaching parity — then drive both modes from it. |
| A pointer-following chart tooltip (`styles.css:753`) | Conflict 11 — codex won. `LineChart.jsx:73-77` already draws a focus line and per-series dots at the hovered x, and a following tip would occlude a compact plot. | Usability evidence that users cannot associate the focus line with the corner readout. Collision-avoidance (flip left when the focus is right-of-center) is the cheap middle ground. |
| Making all nine numeric readouts tabular | Only where width jitter or column alignment is actually visible. Static one-off counts do not need it. | Nothing. |
| Global `scrollbar-gutter: stable` | Under macOS overlay scrollbars this wastes width in already-narrow panels. Apply per-container where shift is measured. | Measuring a real shift. |
| Rewriting the setup window inside the app build | It is a standalone `file://` page under a strict CSP by design. Mirror the token *values*; do not couple it to the bundle. | Nothing. |

---

## Open questions for the human

One, genuinely product rather than craft. It does not block the build.

1. **A terminology audit across the agent surface, later.** The UI vocabulary is settled and not reopened:
   **Export** for the user-facing final bytes, **Render/Re-render** for internal jobs and comp
   materialization. `AGENT_GUIDE.md`, the skills, and the backend all still say "render", which is correct
   for them. Worth one non-blocking pass someday to confirm nothing user-visible leaks the internal word.

*Resolved during ratification, previously open:* the poster frame comes from the **first cut's source at
t≈1s, cached** (Phase 5b item 2). That was the sensible default all along and does not need a product answer.

*Resolved by the human, previously open:* **"Mission Control" is dropped.** Both user-visible uses are
removed — `web/index.html:6` is now `<title>OpenNolan</title>` and the dashboard brand at `App.jsx:183` is
`OpenNolan` alone. This also repairs an unnoticed override: `desktop/main.js:582` sets the window
`title: 'OpenNolan'`, but with no `page-title-updated` handler and no custom title bar, the renderer's
`<title>` was silently winning, so the macOS title bar read "OpenNolan · Mission Control" against the
desktop shell's stated intent. `api.js:1` still says "Mission Control" in a code comment — not user-visible,
and covered by the terminology audit above.

---

**What to do Monday morning:** Phase 0, then Phase 1 item 3 — split `--line` from `--border: #98886f` —
then Phase 2 item 1. That is one deletion plus roughly forty lines of CSS, and it is the change that makes
the whole app stop looking mushy.
