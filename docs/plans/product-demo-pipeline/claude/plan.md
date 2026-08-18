# Product-demo pipeline — kinetic UI demos from stills

**Status: BUILT** — full python suite green (2246 passed). Not committed.

Human waived the Codex review gate for this change; recorded here because
CLAUDE.md otherwise requires it.

Three problems came out of the `launch-video` project. They are unrelated in
cause and fixed in different places. Problem 1 is what stopped the QA loop
that would have caught problem 3.

---

## Problem 1 — the agent died mid-QA (FIXED)

### Symptom

Five times in one thread, the turn ended with:

```
Failed to decode JSON: JSON message exceeded maximum buffer size of
1048576 bytes...
```

### What it actually is

Nothing to do with video. It is the pipe between the Claude Code CLI and the
Python SDK.

```
 CLI subprocess                                    SDK (python)
 ─────────────                                     ────────────
 tool_result ──> ONE line of NDJSON on stdout ──>  guard(len(line))
                                                    len > max_buffer_size?
                                                        └─> raise, turn dies
```

The guard lives in the SDK, one message at a time:
`claude_agent_sdk/_internal/transport/subprocess_cli.py:1074`.

The trap is that the CLI writes an image's base64 **twice in that one line**.
Measured directly against the bundled CLI (`claude -p --output-format
stream-json`, reading a 477 KB PNG):

```
line length ......... 1,273,830 chars     <-- over the 1 MB default
  .message.content[0].content[0].source.data ... 636,628   (the API block)
  .tool_use_result.file.base64 ................. 636,628   (CLI sidecar)
```

So the usable ceiling is **half** of `max_buffer_size`. At the SDK's 1 MB
default, any image whose base64 passes ~512 KB kills the turn — that is any
PNG over roughly **390 KB**.

Which is precisely a 1920x1080 QA frame pulled out of a render with ffmpeg.
Every one of the five deaths came directly after a `Read` of one:

| thread msg | file read                  | on disk | b64 x2   | outcome |
| ---------- | -------------------------- | ------- | -------- | ------- |
| 24         | `/tmp/lv3/qa/f_8.5.png`    | 489 KB  | 1.30 MB  | died    |
| 27         | `/tmp/lv3/qa2/f_8.5.png`   | 494 KB  | 1.32 MB  | died    |
| 30         | `/tmp/lv3/qa3/s4_18.5.png` | 678 KB  | 1.81 MB  | died    |
| 33, 36     | `/tmp/lv3/qa4/s4.png`      | 477 KB  | 1.27 MB  | died    |

The frames that survived earlier in the same thread were either small enough
to pass through (`f_3.png`, 289 KB -> 771 KB doubled) or big enough that the
CLI re-encoded them down to JPEG first (`exec-*.png`, 1.4 MB -> 382 KB). The
lethal window is the middle: roughly **390 KB to ~600 KB**, and that is where
UI screenshots live. Hence "many times" rather than once.

### Fix

`server/agent_runner.py` built `ClaudeAgentOptions` without
`max_buffer_size`, so it took the 1 MB default.

- `server/agent_runner.py:64` — `MAX_SDK_MESSAGE_BYTES = 24 * 1024 * 1024`
- `server/agent_runner.py:1051` — passed to `ClaudeAgentOptions`

24 MB = the API's own ~5 MB-base64 image ceiling, doubled for the duplication,
plus headroom for a large text result. Still bounded, so a runaway producer
cannot exhaust memory.

Test: `tests/contracts/test_agent_runner.py::
test_build_agent_options_sizes_the_stdout_buffer_for_frame_reads` — asserts
the ceiling clears a worst-case frame **on the doubled accounting**, so it
fails if anyone reverts to the default. 88/88 pass.

### Not doing

- Not capping frame size at extraction. Downscaling QA frames would hide the
  bug and cost the agent the pixel detail it needs to spot a 2 px collision.
- Not unbounding the buffer. A ceiling that catches a genuinely broken
  producer is worth keeping.

### Does the model see the image twice?

No. The duplication is transport-only. `.tool_use_result` is a **sibling of**
`.message` in the NDJSON envelope, and `.message` is what becomes the API
request — the sidecar is out-of-band metadata for stream consumers. Measured
at 960x540 (below any resize cap, so tokens ~= w*h/750 is exact):

```
  image cost in context ... 706 tokens
  one copy would be ....... 691   <-- matches
  two copies would be ..... 1382
```

So it costs stdout bandwidth and buffer headroom, not tokens. Nothing to fix.

---

## Problem 2 — the agent worked in /tmp (FIXED)

The agent filled `/tmp/lv`, `/tmp/lv2` and `/tmp/lv3` with a project's
generator scripts, scene HTML and QA frames. None of it travels with the
project, and none of it survives a reboot.

It was not disobeying: `/tmp` is inside the sandbox (`server/agent_runner.py`
`build_sandbox` — media tools legitimately stage there), and the system prompt
said "write generated files to a scratch path" without ever naming one.
`route_caches()` does redirect `TMPDIR`, but only when packaged, and it cannot
stop a hardcoded `mkdir /tmp/lv3` in a Bash call anyway.

The agent picks paths by instruction, so the fix is an instruction:

- `server/agent_runner.py` `_project_context` — names
  `<project>/.scratch/` and forbids `/tmp` for the agent's own working files.
  Creates the dir so the path is real.
- Dot-prefixed, so the Assets browser hides it (`server/app.py:216`) and it
  deletes/travels with the project.
- `_store_asset` — the project's `.scratch` joins `_temp_roots()` as staging,
  so `store_asset` still MOVES rather than copies. Without this the fix would
  just relocate the litter from `/tmp` into the project.

Tests: `test_store_asset_moves_src_from_the_projects_own_scratch`,
`test_project_context_names_the_scratch_dir_and_bans_tmp`.

### Not doing

- Not removing `/tmp` from the sandbox. Media/generation tools stage there
  legitimately and `store_asset` already relocates from it.
- Not setting `TMPDIR` per project. That plumbing catches library temp files,
  which is not what was complained about; revisit if third-party tools turn
  out to leave things behind.

---

## Problem 3 — the demo came out static and slow

### What the run actually bound

`launch-video`'s `project.json` has `pipeline_type: null`, so the agent chose
at proposal time. It chose:

```
checkpoint_proposal.json    pipeline: animated-explainer
                            playbook: greg-isenberg-product-explainer
checkpoint_scene_plan.json  playbook: anthropic-editorial-animated   <-- drift
checkpoint_compose.json     playbook: anthropic-editorial-animated
```

Two separate faults, and neither is agent whim:

**a. The pipeline is an explainer, not a demo.**
`animated-explainer` runs on `pipelines/explainer/*` directors
(`pipeline_defs/animated-explainer.yaml:46`). An explainer's grammar is
*annotate a static visual to teach a concept*. That is exactly the output
described: "taking those images and just highlighting particular sections."
The pipeline did its job. It was the wrong job.

**b. The playbook prescribes the slowness, in writing.**

```
styles/anthropic-editorial-animated.yaml:45
  "... subtle 2-4% camera push-in on hero holds."
styles/anthropic-editorial-animated.yaml:48
  max_scene_hold_seconds: 6
styles/greg-isenberg-product-explainer.yaml:45
  "Use restrained cubic ease-outs and subtle camera push-ins"
```

A 2-4% push-in held for up to 6 s *is* "the camera movement is too static and
too slow." The agent followed policy.

**c. The one demo pipeline we have forbids motion and needs footage.**

```
pipeline_defs/screen-demo.yaml:188
  review_focus: "Zoom-crop notes are smooth and minimal (no constant motion)"
pipeline_defs/screen-demo.yaml:71
  recommended playbooks: minimalist-diagram, clean-professional
```

Both recommended playbooks are calm by design. And `screen-demo`'s script
stage hard-requires `transcriber` against real capture — its success criterion
is "Brief references the provided screen recording footage." `launch-video`
had **screenshots, not a recording**. So `screen-demo` was not available even
if the agent had wanted it.

### The gap

```
                    input = a real recording   input = static UI stills
                    ─────────────────────────  ────────────────────────
  goal: teach a
  concept                  (n/a)                 animated-explainer
                                                 <- what we got

  goal: demo a
  product UI            screen-demo                    NOTHING
                        (anti-motion)              <- what we needed
```

So: a real missing pipeline, not just a tuning problem. Nothing in the repo
covers *synthesize a kinetic demo from static UI stills*.

### What was built — 3 new files, no new directors

`animated-explainer.yaml` and `talking-head-screen-demo-reel.yaml` are both
pipeline manifests that own **no** skill folder — they point at another
pipeline's directors and supply their own creative direction. Follow that.

```
  pipeline_defs/product-demo.yaml        <- NEW manifest
        │
        ├─ orchestration.skill ─────>  pipelines/screen-demo/*   (reused)
        │                              7 directors, unchanged
        ├─ required_skills ────────>  creative/product-demo-kinetic  <- NEW
        │                              the motion grammar
        └─ compatible_playbooks ───>  styles/product-demo-kinetic.yaml <- NEW
                                       fast pacing, real camera moves
```

What the manifest changes versus `screen-demo`:

| stage      | screen-demo                        | product-demo                      |
| ---------- | ---------------------------------- | --------------------------------- |
| script     | requires `transcriber` on capture  | stills are the source; no capture |
| scene_plan | zoom-crop regions                  | a camera + cursor **motion plan** |
| edit       | "smooth and minimal, no constant   | continuous camera; cut on action; |
|            |  motion"                           | dead air is a defect              |
| compose    | Remotion TerminalScene preferred   | HyperFrames camera rig over stills|

Playbook direction (`styles/product-demo-kinetic.yaml`), stated as the
inverse of what produced the static cut:

- scene holds ~0.6-1.5 s (not up to 6 s)
- camera scale travel 15-40% per move (not 2-4%)
- whip pans / snap zooms between UI regions, cut on the cursor click
- an animated cursor that moves, clicks and types — the demo is *driven*,
  not narrated over
- click ripples, focus rings and state changes land ON the spoken word
  (the existing hard rule from the narration-sync work)
- speed ramps through waiting states; no scene without motion

### Packaging — decided: it ships

`lib/pipeline_loader.py:22` — `PACKAGED_PIPELINES` is now
`("instagram-fast-reel", "product-demo")`.

That flipped a load-bearing assumption. The packaged app used to pin
`PACKAGED_PIPELINES[0]` because "there is exactly one pipeline"; with two,
that pin would silently force every project onto whichever name sits first —
a product demo rendered as a fast reel, with nothing in the UI to say so.

```
  len(PACKAGED_PIPELINES) == 1  ->  pin it        (no choice to make)
  len(PACKAGED_PIPELINES) >  1  ->  agent chooses FROM THAT LIST ONLY
                                    (never "browse pipeline_defs/" — the
                                     un-shipped manifests ride along in the
                                     bundle as inert data)
```

`server/agent_runner.py` `_project_context` now does exactly that. Covered by
`test_packaged_context_pins_a_lone_pipeline_but_offers_a_choice_from_many`,
which was mutation-checked: reverting the logic to `[0]` fails it.

Also required, because the analytics gate fails closed and would otherwise
silently drop every event for the new pipeline:
`schemas/analytics/project.json` — `pipeline_type` gains `product-demo`,
`style` gains `product-demo-kinetic`.

No electron-builder change needed: `desktop/package.json` copies
`pipeline_defs/`, `skills/` and `styles/` wholesale, unfiltered.

### Open questions for the reviewer

1. **Playbook drift.** The proposal said `greg-isenberg-product-explainer`
   and compose used `anthropic-editorial-animated`. Nothing caught the swap,
   though `render_runtime` has exactly that guard already ("silent swap is a
   CRITICAL governance violation", `screen-demo.yaml` compose review_focus).
   Should `playbook` get the same guard globally? `product-demo`'s edit stage
   now carries the review line, but that only protects this pipeline.
2. **Pre-existing schema breakage.** `screen-demo`, `documentary-montage` and
   `animation-talking-head-50-50` currently fail
   `schemas/pipelines/pipeline_manifest.schema.json`
   (`production_modes`, `layout_modes`/`render_strategy`, and a
   `documentary` category outside the enum). Nothing loads them through
   `load_pipeline()` in anger, so it has gone unnoticed. Out of scope here;
   `product-demo` will validate clean.

### Not building

- **No new director skills.** If the reused `screen-demo` directors turn out
  to fight the motion direction rather than take it from the playbook, that
  changes — a forked `scene-director` would then be justified. Cheap to find
  out: one run.
- **Not touching `screen-demo`.** Its anti-motion rule is correct for its own
  job (a real capture with real cursor motion does not want a second camera
  moving on top of it). Leave it.
- **No new tools.** The HyperFrames camera rig the agent hand-rolled in
  `/tmp/lv3` proves the existing runtime can already do this. The gap is
  direction, not capability.
