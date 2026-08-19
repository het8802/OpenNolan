# QA Director — instagram-fast-reel (final gate)

Inspect the actual rendered `renders/final.mp4` before it's presented. Do it in this order — the
hook first at a HIGH sample rate, then the whole reel at a lower rate, then the technicals. This
is the last stage; it produces the `final_review` artifact and gates the deliverable. Human
approval required.

## Step 0 — is `final.mp4` even the video of the current timeline? (do this FIRST)

You are about to hand the user `renders/final.mp4` **and** the live `edit_decisions.json`.
Those are exactly the two things that can silently disagree, so prove they don't before
you QA a single frame:

```bash
python -c "import json;from lib import app_paths;from lib.project import final_render_status;\
print(json.dumps(final_render_status(app_paths.projects_dir(),'<project_id>')))"
```

`app_paths.projects_dir()`, not the literal `projects` — in the packaged app (and whenever
`OPENNOLAN_HOME` / `OPENNOLAN_PROJECTS_DIR` is set) the project does not live under the repo,
and a hardcoded root would report "no final.mp4" for a perfectly current render and block
delivery. Bare `python` for the same reason AGENT_GUIDE.md gives: your PATH already points at
the interpreter that has OpenNolan's dependencies.

`current: false` → **stop**. Report `status` revise with `recommended_action: re_render` and
the returned `reason` verbatim. Do NOT `pass`: the frames you'd sample would not be the
frames of the timeline you're handing over. This is the same callable the editor's asset
listing uses to label the render, so the gate and the UI can't drift apart.

## Step 1 — MEASURE the whole reel (do this before looking at anything)

A still frame cannot show motion. Sampling stills and eyeballing them is how a frozen
hook, a pan that never panned and a dead tail all pass review — so measure first, then
look only where the numbers point.

```
visual_qa operation="motion" input_path="renders/final.mp4"
```

~1s, no images, a few hundred tokens. Read, in this order:

0. **`verdict`** — read this FIRST and treat it as the outcome of the step. It compares
   the measurement against this format's pacing norms (`lib/qa_norms.py`) and returns
   `pass` or `fail`. Every failure names the metric, what was measured, the norm, and
   what it does to the viewer.

   **`verdict.status == "fail"` means you may not report the video as good.** Not "note
   it and pass" — the schema rejects a `final_review` whose `status` is `pass` while
   `motion_check.norms_verdict` is `fail`. Your options are: fix the edit and re-measure,
   or state the specific reason a norm does not apply to this video. "Looks fine to me"
   is not that reason; you cannot see motion, which is why this measurement exists.

   Pass `norms_profile="long_form"` only for genuinely long-form or cinematic work where
   holds are the point. A reel judged as long-form is a reel that failed nothing.

   Note what a passing verdict does and does not say: it says the edit is not broken and
   not slow. It says nothing about whether the composition, easing, or type is any good.
   Those still need your eye on the sheet and the strips.

1. **`frozen_runs` / `findings`** — a FROZEN stretch is consecutive identical frames.
   Any freeze the edit did not deliberately ask for is a defect. A freeze **inside the
   hook window** is CRITICAL: stop and report.
2. **`static_fraction`** — the share of runtime that isn't moving. This is the *measured*
   slideshow number, not the self-reported `slideshow_risk_score`. High and rising means
   the reel is a slide deck with music.
3. **`dark_runs`** — black or near-black stretches.
4. **`cut_count` + `cut_times`** — detected hard cuts. Far fewer than the edit planned
   means the fast cutting didn't land. A cut the viewer cannot see is not a cut, so what
   matters is `duration / (cut_count + 1)` — the shot length actually experienced, which
   the verdict reports as `perceived_shot_seconds`. An edit written at 2.2s per shot that
   measures 4.7s is playing at half the speed it was cut for.
5. **`detail_dropouts`** — frames where the picture briefly empties out and comes back,
   usually a transition rendering a blank. These pass every other check: luma is normal
   so no black-frame test fires, and at 1-3 frames long no contact sheet will ever land
   on one. `strip` over the window to see it.
6. **`table`** — per-second motion. Scan it for flat rows; the first few seconds matter
   most.

**If the hook window (first ~3s, the duration of `cuts[0]`) is static or frozen, STOP**
— set `status="revise"` and report. The hook is the whole reel; don't spend the rest of
the pass on a reel that's already dead.

## Step 2 — LOOK, with one image instead of sixty

```
visual_qa operation="sheet" input_path="renders/final.mp4"
```

One contact sheet of the whole reel, each tile stamped with its own timestamp — quote
that timestamp in any finding. Read the `sheet_path` image and scan for: broken or
missing overlays, meme GIFs that never appear (or never leave), captions in the platform
UI shadow zone or clipped off-frame, unreadable text, wrong framing.

- **One tile per scene** (better than fixed intervals — a fixed-interval sheet can land
  tiles on transition frames that look like blanks): pass `timestamps` set to each cut's
  midpoint.
- `empty_cells` in the result is grid padding, **not** black frames in the video.
- Check `notes` — if the sampling rate was reduced, it says so.

## Step 3 — SEE THE MOTION on every window that claims to move

A sheet still shows stills. For anything that is supposed to *move* — a pan/zoom, a
keyframed overlay, a motion-graphic build, a composition clip — take a filmstrip:

```
visual_qa operation="strip" input_path="renders/final.mp4" window={"start": 18.0, "duration": 1.5}
```

Consecutive frames side by side, ~12/second, absolute timestamps burned in. This is the
only way to judge whether motion actually *progresses* rather than jumping, stalling or
never starting. Run it on:

- the **hook** (`window={"start":0,"duration":1.5}`) — always;
- every window `motion` flagged static or frozen — to see *what* is frozen;
- every keyframed overlay window and every composition clip that declares an animation;
- any moment where a caption or GIF is supposed to land on a trigger word.

## Step 4 — DIFF the plan against the render

```
visual_qa operation="vs_plan" input_path="renders/final.mp4"
```

**Advisory** — it reports the delta, you decide. Read `lines` worst-first:

- **`not-rendered`** — a certainty, derived from the renderer's own code: the plan declared
  something the render path provably ignores (e.g. `cuts[].transform.animation` has no
  reader on the ffmpeg path, so a declared ken-burns move renders static and silently).
  These need no confirmation. They are the most common cause of "the animation I planned
  isn't in the video".
- **`no-op-keyframes`** — keyframes that never change value, i.e. a constant dressed up as
  an animation.
- **`flat`** — a declared animation whose window measures as unmoving. Confirm with a
  `strip` over that window before acting: frame energy is averaged over the whole frame,
  so a small moving element can be diluted.
- **`cut-undetected`** — WEAK EVIDENCE by construction; two visually similar shots cut
  together score low on scene change. A hint to look, not proof.
- **`duration-drift` / `plan-inconsistent`** — the timeline math doesn't add up.

Whatever you keep, record it (Output, below). A finding you drop silently is a finding
the human never sees.

## Step 5 — TECHNICALS (probe everything)
- `visual_qa` `operation="probe"` with `expected={width:1080, height:1920, has_audio:true}`:
  valid container, correct resolution / fps / codec / pixel_format, and a duration that matches
  the tightened edit (must be SHORTER than the source — that proves the fast cuts landed).
- Decode clean (no decode errors); no long black segments (blackdetect).
- `visual_qa` `operation="audio_levels"` at several timestamps: VO present + intelligible, music
  ducked under it, no clipping, and **no leftover dead air** (it should have been cut with the video).
- Captions: coverage vs the transcript, no timing drift.
- HDR: if the source was HDR, confirm it wasn't silently tonemapped.

## Step 6 — SYNC + advisory
- Confirm no animation / GIF / caption appears BEFORE its trigger word. Use a `strip` over
  the trigger moment — a still cannot tell you whether a reveal is early, and this is the
  rule that matters most for these reels. An early reveal is a **CRITICAL** finding.
- Optional `content_signal` advisory virality score — informational, **never a gate**.

## Output
`final_review` (schema-valid, `schemas/artifacts/final_review.schema.json`):
- `status`: `pass` / `revise` / `fail`.
- `checks`:
  - `motion_check` — **record this.** `norms_verdict` + `norms_profile` +
    `norms_failures` (one line per failed norm — each also goes in `issues_found`),
    `perceived_shot_seconds`, `detail_dropout_count`, `static_fraction`, `frozen_seconds`,
    `cut_count`, `hook_static_seconds`, `declared_not_rendered` (count of `vs_plan`
    `not-rendered` findings), `sheet_path`, `strip_paths` (every window you inspected),
    `issues`. This is
    the only place the measurement survives; `vs_plan` is advisory, so a finding you keep
    but don't write down is one the human never sees.
  - `technical_probe`, `visual_spotcheck` (`frames_sampled` = the **tile count** of the
    sheet, not the number of files — one sheet is still N frames inspected, and the schema
    requires >= 4 — plus `frame_paths` = the sheet and strips), `audio_spotcheck`,
    `subtitle_check`, `promise_preservation` (runtime that ran == locked runtime, no swap).
- `issues_found` and `recommended_action` (`present_to_user` / `re_render` / `revise_edit` /
  `revise_assets` / `block`).

## Quality bar
Step 0 `current: true`; `motion` measured with no unexplained freeze and a hook that moves;
the sheet scanned; a `strip` taken for the hook and for every window that claims to animate;
`vs_plan` read and anything kept recorded; all technicals green; sync verified. Any CRITICAL
finding (frozen or static hook, early reveal, black frames, wrong resolution, missing audio,
leftover dead air, a declared animation that provably did not render) → `status` revise/fail
with a concrete next action; do NOT present as complete. On `pass`, point the user to
`renders/final.mp4` + the live `edit_decisions.json` to hand-tune in the Studio editor.

## Why this order
Measuring is ~1s and a few hundred tokens; reading sixty frame images is neither. Every
looking step is aimed by the measuring step, so the expensive passes only happen where
something is actually wrong — and motion, which no still can show, gets checked at all.
