# OPN-30 — edit_decisions vs. the video the agent actually made

**Status: BUILT + QA-APPROVED — rev 5** · plan converged over five Codex review rounds (§6);
implementation QA'd over nine more, verdict APPROVE (§8). Built 2026-08-03; deviations from
the plan are in §7.
Ticket: OPN-30 "the edit decisions is not in sync with the edits that the agent made"
Reproduced end-to-end 2026-08-02 (two real agent turns, project `opn30-sync-check`).
Findings comment on the ticket; this doc is the fix.

---

## 1. What is actually broken

### Defect A — a re-render leaves `renders/final.mp4` stale

After the re-render turn the project was left like this:

| what | says |
|---|---|
| `artifacts/edit_decisions.json` | 5 + 5 + 3 = **13s** |
| editor timeline | **13s** (correctly adopted) |
| `renders/agent_render_4b4193bafc61.mp4` | **13.13s** (the real new render) |
| `renders/final.e5c66172.mp4` | **13.13s** (same bytes again) |
| **`renders/final.mp4`** | **15.13s — the stale previous cut** |

The agent honestly reported "re-rendered, 13.13s". The file the UI labels **Final
render** and the `⤓ final.mp4` download both served the old 15s edit.

Causes, stated precisely:

1. `server/render_jobs.py:274-276` — the agent's `render` tool omitted `output_path`, so
   the fallback name `agent_render_<job_id>.mp4` was used. **This is the cause of the
   reproduced failure.** A call that *does* pass `output_path="renders/final.mp4"` resolves
   in-project (`_normalize_output_path`, `:378-404`) and ffmpeg overwrites it
   (`tools/video/video_compose.py:1074` runs `ffmpeg -y`). Replacement was always
   possible; the default the agent falls into never does it. Several pipeline contracts do
   pass it — `skills/pipelines/anthropic-style-animated-talking-head/compose-director.md:47`,
   `skills/pipelines/documentary-montage/compose-director.md:98`,
   `skills/pipelines/animation-talking-head-50-50/edit-director.md:112` — and one uses the
   render tool to produce a genuine *intermediate* under `renders/`
   (`.../edit-director.md:106`, `overlay_raw.mp4`). The parameter is load-bearing.
2. `lib/project.py:330-337` — `place_asset` never clobbers: a name collision with different
   bytes gets a content-hash suffix. So `store_asset(kind="final_render", name="final.mp4")`
   yields `final.<hash>.mp4` and leaves the stale `final.mp4` alone. **This path genuinely
   cannot replace the deliverable.** It also does not force the name, so
   `name="reel.mp4"` puts a second "final" in `renders/`.
3. `web/src/App.jsx:1318-1336` — every top-level file in `renders/` is listed under one
   "Final render" heading with no current marker. The user saw three tiles
   (0:13, 0:13, 0:15) and the obviously-named one was the wrong one.
   `RenderJobStore.start()` (`server/render_jobs.py:43`) adds more from the editor's Render
   button as `editor_preview_<job_id>.mp4`.

### Defect B — nothing ties "what got rendered" to "what is on disk"

```
  agent turn                                            disk
  ─────────                                             ────
  render(edit_decisions={...inline...})  ──▶ ffmpeg ──▶ renders/*.mp4
                                                             ▲
       (no link)                                             │  no reconciliation
                                                             │
  Write(artifacts/edit_decisions.json)   ─────────────▶ artifacts/edit_decisions.json
                                                             │
  editor reads ◀───────────────────────────────────────────── ┘
```

`RenderJobStore._run_with_inputs` (`server/render_jobs.py:262-285`) renders the
caller-supplied object and never persists it. The editor reads
`artifacts/edit_decisions.json`. Agreement between the two is pure agent discipline.

Proof from the repro: in turn 1 the agent *did* write the artifact and the two copies
**still** differed (the `reason` strings in the rendered object were not the ones in the
file). Harmless there because timings matched. But during turn 2 there was a ~90s window
where the artifact said 13s and `final.mp4` said 15s. A Stop in that window makes that
state permanent, silently.

### Defect C — concurrent renders share scratch state (pre-existing)

`_is_superseded` (`server/render_jobs.py:119-120`) only suppresses the *status update*; the
superseded thread keeps rendering. And every renderer scratch path derives from
`output_path.parent`, i.e. `renders/`, which is per-project, not per-job: `.compose_tmp`
(`tools/video/video_compose.py:1835`), `.pip_tmp` (`:3154`), `.remotion_props.json`
(`:3675`), `.final_review_frames` (`:4002`).

So an agent render and an editor Render click overlapping in one project can overwrite each
other's segments and props. Broken today, independent of this ticket — but a plan that
promises an atomic publish has to fix it or the promise is hollow.

### Defect D — preview ≠ export for text overlays

The agent wrote (schema-valid, honored by the renderer):

```json
{ "type": "text", "text": "OpenNolan", "font_size": 130, "color": "#F0EDE6",
  "box": { "color": "#CC785C", "opacity": 0.9, "padding": 28 }, "position": "top-center" }
```

- Export at t=1.0s: cream text on a **terracotta `#CC785C`** pill. Correct.
- Editor preview, same doc, same t: cream text on a **black** box.

`web/src/studio/StudioPreview.jsx:544` hardcodes `rgba(0,0,0,${boxOpacity})` and ignores
`box.color`, while `tools/video/video_compose.py:4929` emits `boxcolor={color}@{opacity}`.
The house palette is clay `#CC785C`, so the agent trips this on essentially every reel.

---

## 2. The fix

**There is exactly one publisher of the deliverable. It publishes the video and (only where
that is safe) the doc that produced it. It writes a receipt last, as the commit marker, and
the receipt binds both the document and the published file's identity — so anyone can tell
whether what they are looking at is current.**

```
  BEFORE                                    AFTER
  ──────                                    ─────
  agent render  ─▶ agent_render_<job>.mp4         agent render ─┐
  editor render ─▶ editor_preview_<job>.mp4      editor render ─┤
  store_asset   ─▶ final.<hash>.mp4                store_asset ─┴─▶ publish_final_render()
  rendered doc  ─▶ (vanishes)                                       1. os.replace → final.mp4
  UI            ─▶ 3 tiles, all "Final"                             2. doc (inline-agent only)
                                                                    3. receipt  ← commit marker
                                                 UI ─▶ 1 tile: current | STALE | earlier
```

### F1 — one publisher, one per-project lock

New in `lib/project.py`:

```
project_lock(projects_dir, project_id) -> threading.RLock
    Guarded registry keyed by (resolved projects_dir, project_id) — two checkouts
    with the same project id must not share a lock, and creation is itself guarded
    by a module lock so two first-callers cannot mint two locks.

canonical_doc_hash(doc) -> str
    sha256 of json.dumps(doc, sort_keys=True, separators=(",",":")).
    ONE helper — three different canonicalisations would make `current` meaningless.

final_render_status(projects_dir, project_id) -> {"current": bool, "reason": str}
    The ONE verification callable. Used by server/app.py for the listing, and
    invoked from the QA director as a single Bash line (see F3) so the gate and
    the UI cannot drift apart.

publish_final_render(projects_dir, project_id, src, *,
                     receipt_doc=None, persist_doc=None, move=False)
    Takes project_lock (RLock, so the render thread that already holds it can call
    this directly) and delegates to _publish_final_locked.

_publish_final_locked(...)
    Caller already holds the lock.
```

Two separate document parameters, because the three routes need three different
combinations (rev 4 had a single `doc` and could not express the editor case):

| route | `receipt_doc` | `persist_doc` | effect |
|---|---|---|---|
| inline agent render | the caller's doc | same doc | video + doc + receipt |
| editor render | the snapshot it rendered | `None` | video + receipt, live doc untouched |
| `store_asset(final_render)` | `None` | `None` | video only, **receipt invalidated** |

`_publish_final_locked` does, in this order:

1. **stage** `src` into `renders/.final.<uuid>.part.mp4` — `shutil.move` when `move=True`,
   else `shutil.copy2`. `shutil.move`, not `os.replace`: a temp root can be on a different
   filesystem (`/tmp` vs. the project), where `os.replace` raises `EXDEV`. Only the later
   part→final replace needs same-filesystem atomicity, and both are inside `renders/`. This
   preserves `_store_asset`'s existing contract — temp-rooted sources are consumed,
   everything else is copied and left alone (`server/agent_runner.py:1569-1576`).
2. **final supersede re-check**, for render callers, holding `RenderJobStore._lock` (see the
   lock order below) across the check *and* step 3 so no job can become active in between.
3. `os.replace(part → renders/final.mp4)` — atomic.
4. `persist_doc`, when given → `atomic_write_json(artifacts/edit_decisions.json)`.
5. **receipt**, **last**: `receipt_doc` given → `atomic_write_json` of
   `renders/.final_receipt.json` = `{doc_hash, video_size, video_mtime_ns}`; `receipt_doc`
   `None` → **unlink** any existing receipt. A missing receipt is not current, which is what
   makes the `store_asset` route honest: leaving the old receipt in place would not reliably
   read as stale, because `copy2` preserves the source mtime and unrelated bytes can happen
   to share a size.

The receipt being last is what makes every interruption safe: crash at any earlier point and
the receipt does not describe the file now on disk, so it reads **stale** rather than being
trusted. The honest limit — once step 3 succeeds the previous good `final.mp4` is gone; what
survives is the *ability to tell*, not the old file. Only a failure *before* step 3 (which is
every render failure) leaves the old file intact.

**Lock order, stated once:** `project_lock` (outer, held for a whole render) then
`RenderJobStore._lock` (inner, held for microseconds around job-state reads/writes and step
3). Never the reverse — nothing may take `project_lock` while holding `_lock`.

`server/render_jobs.py::_execute_render` (`:316-376`) renders to
`renders/.final.<job_id>.part.mp4` and calls the publisher. **The lock is held for the whole
render**, not just the publish — that is what protects the shared `.compose_tmp` /
`.pip_tmp` / `.remotion_props.json` / `.final_review_frames` scratch paths (Defect C).
Immediately after acquiring, the job re-checks supersede and exits without rendering if a
newer job exists, so a queue of stale jobs drains instantly instead of burning CPU. On
supersede or failure the `.part.mp4` is deleted.

Serializing per project is the smaller fix than job-scoping four scratch families inside
`video_compose`, and it makes `_is_superseded` mean something for the first time. Cost: a
second render waits. Acceptable — single-user local app, and waiting beats corrupting.

`server/agent_runner.py::_store_asset` (`:1549`) routes `kind == "final_render"` to the
publisher **via `await asyncio.to_thread(...)`**. It is `async` and the publisher can block
for the length of a render that already holds the lock; calling it directly would stall the
event loop and with it the SSE stream and the whole turn. It passes neither document (see F2) and
the `move` flag it already computes. Every other kind keeps `place_asset` and today's
never-clobber behavior verbatim; the `image` collision test
(`tests/contracts/test_asset_placement.py:86`) stays green as the guard on that.
`test_render_and_final_render_go_to_distinct_trees` (`:59`) asserts `renders/reel.mp4` and
is asserting exactly the behavior being changed — it gets updated to the forced name.

**`output_path` stays**, and selects the route:

| `output_path` | route |
|---|---|
| omitted | publisher → `renders/final.mp4` + doc + receipt |
| resolves to `renders/final.mp4` | publisher, same as omitted |
| another path **under `renders/`** | today's direct write, no receipt — non-current |
| anywhere else in the project | **rejected** (falls back to the canonical route) |

That last row is a tightening: `_normalize_output_path` (`:378-404`) currently only checks
that the path stays inside the project, so `output_path="assets/video/source.mp4"` would let
a render overwrite a source asset or an artifact. Constrain it to descendants of
`<project>/renders`. Its existing traversal test (`tests/contracts/test_render_jobs_inputs.py:132`)
stays green; a cross-subtree rejection test joins it.

One pipeline doc points its deliverable at a non-final name —
`skills/pipelines/explainer/compose-director.md:167` passes
`"output_path": "renders/output.mp4"`, so that pipeline would end with nothing marked
current. Change that one line to omit `output_path`.

### F1b — supersede becomes a real, findable terminal state

`_set` (`:236-240`) silently drops updates for a superseded job, so the job sits at
`queued`/`running` forever. `_render_resume_note` (`:1596-1625`) then reads
`active_job_for` (`:97-104`), sees the *newer* job (origin `editor`), and returns `None` —
the agent never learns its render was superseded, and an in-turn waiter (`:1442-1449`) times
out instead. The per-project lock makes this the common case, so it is in scope:

- on either supersede check, write `status="superseded"` under `RenderJobStore._lock`,
  bypassing `_set`'s guard (which would drop it), and delete the job's `.part.mp4`
- `_run_render`'s wait loop treats `superseded` as terminal, the way it already handles
  `st is None` (`:1444-1447`), **and calls `mark_consumed`** — exactly as its own `done` and
  `failed` branches already do (`:1453`, `:1458`; the resume note does the same at `:1618`,
  `:1623`). Without that, a waiter that
  already reported the supersede in-turn would see `latest_unconsumed_agent_job` report it
  again on the next turn
- `active_job_for` cannot find it, so add `latest_unconsumed_agent_job(project_id)`:
  newest-first by insertion sequence, `origin in ("agent","agent_op")`, `consumed` false,
  status terminal. `_render_resume_note` uses it, and `mark_consumed` keeps it one-shot —
  preserving the "fires exactly once" and "never double-reported" properties its docstring
  claims (`:1596-1602`).

### F2 — validate before; commit the doc only where it cannot destroy newer work

`server/agent_runner.py::_run_render` (`:1414`), when the call supplies an inline
`edit_decisions`:

- **validate** it against `schemas/artifacts/edit_decisions.schema.json` *without writing*.
  Invalid → `success=False` with the validation error, no job started. Strict, not
  warn-and-render: a doc that cannot be persisted is the desync, and the artifact schema is
  already the contract AGENT_GUIDE requires the agent to write.
- pass the **caller's** object (not the `_resolve_sources` copy — that rewrite happens later
  inside `_execute_render` (`:338`) on a render-only duplicate) as **both** `receipt_doc` and
  `persist_doc`, so it is committed **only on render success**, in the same critical section
  as the video.

**Only the inline-agent route sets `persist_doc`.** The editor's Render button
(`RenderJobStore.start()`) renders a snapshot read from disk, and autosave is *not* suspended
during a render — `Studio.jsx:221` gates saves on `agentBusyRef`/`reconcilingRef` only, and
`:260`'s `if (rendering) return` guards re-entering render, not saving. So a user can edit and
autosave doc B while the render of doc A is in flight; writing A back on success would destroy
B. The editor route therefore passes `receipt_doc=<its snapshot>, persist_doc=None`: it gets a
receipt for exactly what it rendered and never touches the live document, so an advanced live
doc simply makes the render read **stale** — the correct answer.

`store_asset(kind="final_render")` passes **neither**. Its `src` may be unrelated to anything
on disk, so hashing the disk doc would falsely certify arbitrary bytes. Publishing with no
`receipt_doc` unlinks the receipt (F1 step 5), so the result is unambiguously not current
until a real render supplies one — rather than depending on metadata happening to change.

RULES.md's autosave suspension covers the agent-turn case, so the inline-agent commit has no
human writer to race.

### F3 — the receipt, and a UI that can say "stale"

`final_render_status` is **two** checks, not one:

```
current  ==  a receipt exists
        AND  receipt.doc_hash       == canonical_doc_hash(live edit_decisions.json)
        AND  receipt.video_size     == stat(final.mp4).st_size
        AND  receipt.video_mtime_ns == stat(final.mp4).st_mtime_ns
```

The document half alone is not enough: after step 3 replaces the video but before step 5
writes the receipt, the *old* receipt still hashes to the still-old live doc, so a doc-only
check would call the new bytes current. The identity half also catches an outside writer
(`run_media_op`, `hyperframes_compose.py:673`, a hand-run ffmpeg) replacing `final.mp4`
behind the publisher's back.

Size + `mtime_ns` is a **practical** identity token, not proof of bytes: a `cp -p` of
same-size content preserves both and would still read current. Deliberate — hashing a
50 MB+ video on a listing the UI polls every 4 s (`web/src/App.jsx:1287`) is the wrong
trade. The publisher's own routes cannot produce that state (an unreceipted publish unlinks
the receipt), so it takes a hand-crafted metadata-preserving copy to fool it.
`ponytail:` size+mtime_ns identity — add a `video_sha256` to the receipt if a real
out-of-band writer ever shows up.

- The receipt lives at `renders/.final_receipt.json`; the assets listing already skips
  dotfiles and non-video suffixes (`server/app.py:420`), so it cannot appear as a
  deliverable.
- `server/app.py:411-425` calls `final_render_status` and returns `current: true|false` per
  render, and switches the cache-bust token from `int(stat.st_mtime)` (`:425`) to
  `stat.st_mtime_ns`.
  Second-granularity mtime is a real regression once the path stops changing: two fast
  cached assemblies inside the same second produce an identical token, so
  `web/src/App.jsx:1300,1325` keeps the same URL and React key and the browser can keep
  showing the old bytes while disk is already correct.
- `web/src/App.jsx:1318-1336`: the `current` tile is labelled **Final render**; a
  `final.mp4` that fails either check is **Final render (stale — re-render)**; any other
  top-level file in `renders/` is **Earlier render**, sorted after it. Projects that already
  carry litter (the user has several) become readable without deleting anyone's files.
- `skills/pipelines/instagram-fast-reel/qa-director.md` — the final gate reads
  `renders/final.mp4` and on pass hands the user that file *plus* the live
  `edit_decisions.json` (`:3`, `:55`), i.e. exactly the pair that can disagree. A markdown
  director cannot import a Python helper, so the doc names the exact call it must make
  first, as its own step:

  ```bash
  python3 -c "import json,sys;from lib.project import final_render_status;\
  print(json.dumps(final_render_status('projects','<project>')))"
  ```

  `current: false` → the gate fails with that `reason`; it must not `pass` on a stale pair.
  Same callable as the listing, so the gate and the UI cannot drift.

### F4 — preview honors `box.color`

`web/src/studio/StudioPreview.jsx`

- new tested pure helper `ffColorToCss(c)` → `{css, alpha}`: handles `#RGB` / `#RRGGBB` /
  `#RRGGBBAA`, `0xRRGGBB[AA]`, bare colour names, and an ffmpeg `@alpha` suffix. The schema
  puts no pattern on `box.color` (`schemas/artifacts/edit_decisions.schema.json:118` is a
  bare string) and `_FF_COLOR_RE` (`video_compose.py:4758`) admits all of these. `alpha`
  multiplies `box.opacity`. A bare name may be ffmpeg-only and invalid in CSS, so the
  result is checked with `CSS.supports('color', css)` (guarded for jsdom) and falls back to
  the renderer's default `black` (`video_compose.py:4739`) rather than silently rendering
  transparent. A naive `color-mix(in srgb, <raw> …)` would keep previewing the wrong colour
  for `0x…` and 8-digit forms, which is the bug being fixed.
- `renderTextInner` (`:532-548`): background from that helper. **Nothing else in this
  function or in `TEXT_ANCHOR_CSS` changes** — see §3.

---

## 3. Deliberately not building

- **Making the publisher the *only* writer under `renders/`.** It cannot be, and pretending
  otherwise is what made revs 1 and 2 wrong. `run_media_op`
  (`server/agent_runner.py:1046-1070`) forwards an arbitrary `input` dict to any registry
  tool; `tools/video/hyperframes_compose.py:673` defaults its own output to
  `renders/final.mp4`; `skills/pipelines/animation-talking-head-50-50/` writes
  `overlay_raw.mp4` there on purpose. Validating output paths across dozens of tool schemas
  is whack-a-mole. So the invariant is about *presentation and provability*, not exclusivity:

  > `renders/final.mp4` is the sole **current** deliverable. It is current only while the
  > receipt beside it matches both the live document and the file's own identity. Everything
  > else under `renders/` is non-current, and the UI says so.

  An outside writer that replaces `final.mp4` changes its size or `mtime_ns`, so its bytes
  read stale (the `cp -p` caveat in F3 applies). One cheap
  addition rather than enforcement: `_run_op` (`server/render_jobs.py:287-314`) appends a
  warning when its output landed at the top level of `renders/`. Changes my mind: a pipeline
  starts *depending* on writing the deliverable directly, at which point it should call the
  publisher.
- **Text-overlay *geometry*.** Three mismatches belong together in a WYSIWYG ticket: the
  `0.4*pad` vertical padding vs. uniform `boxborderw` (`StudioPreview.jsx:543`), the 4%/3%
  anchor margins vs. 5% (`:20-30`), and the `boxborderw` offset — ffmpeg anchors the *text*
  origin and grows the box `pad` px past it while CSS anchors the *padding box*, worth about
  `pad * scale` (~8 screen px at typical zoom) and needing a per-anchor transform delta
  through `:416-423`. Fixing two and deferring the third is the worst split: it churns every
  existing preview and still does not match. A wrong *colour* is a correctness bug and is in
  scope; approximate *placement* was always documented as approximate (`:19`, `:530-531`).
  Changes my mind: a caption lands on a safe-area edge and the export clips it.
- **Job-scoping the four `video_compose` scratch families.** F1's per-project lock removes
  the collision at the chokepoint. Changes my mind: renders ever need to run in parallel for
  one project (they do not, today).
- **Fixing the activity-log gap.** `server/activity.py:49` skips every `mcp__mc__*` tool, so
  `render` and `store_asset` calls never reach `activity.jsonl`. Real, but it is an
  observability ticket, and the receipt covers the diagnosis it would have served.
- **Keeping render history**, **cleaning up existing litter**, and **filesystem watchers for
  outside writes.** Every render replaces `renders/final.mp4`; old files are labelled, not
  deleted; the receipt is checked on read, which is sufficient without watching the tree.

---

## 4. Steps and verification

`scripts/dev test fast` between steps; `scripts/dev test full` plus `scripts/dev smoke`
before review. `scripts/dev stop` before calling it done.

```
1. F1a  project_lock + canonical_doc_hash + publish_final_render (lib/project.py)
   → tests/contracts/test_asset_placement.py
     · publish twice with different bytes -> renders/ holds exactly
       {final.mp4, .final_receipt.json}; bytes are the second write; no
       hash-suffixed sibling
     · name="reel.mp4" -> still lands at renders/final.mp4 (UPDATES the
       assertion at :59, which is the behavior being changed)
     · `image` collision still hash-suffixes (test at :86 unchanged)
     · move=False with a NON-temp src -> src still exists afterwards;
       move=True with a temp src -> src consumed  (the semantics at
       agent_runner.py:1569-1576 must survive)
     · move=True with src on a DIFFERENT filesystem -> succeeds (shutil.move,
       not os.replace, which raises EXDEV). CI has no second device, so mock
       os.replace to raise OSError(EXDEV) and assert the staging still lands
     · receipt_doc set / persist_doc None -> receipt written, live
       edit_decisions.json byte-identical afterwards (the editor route)
     · receipt_doc None -> any existing receipt is UNLINKED, and
       final_render_status reports current:false even when the new bytes were
       copy2'd to the SAME size and mtime_ns as the old receipt recorded
     · re-entrancy: a caller already holding project_lock can call
       publish_final_render on the same thread and it completes (RLock)
     · two threads publishing DISTINCT docs+bytes -> one pair wins whole,
       never a mix, and the receipt matches whichever won
     · receipt-last: simulate failure after the video replace but before the
       receipt write -> current is FALSE (identity mismatch), not true
     · same projects_dir+project_id -> same lock object; different
       projects_dir, same project id -> different locks
     · final_render_status: no receipt -> false; doc edited -> false;
       file replaced -> false; untouched pair -> true

2. F1b  render routes; output_path containment
   → tests/contracts/test_render_jobs_inputs.py
     · no output_path -> "renders/final.mp4", receipt written
     · output_path="renders/final.mp4" -> same publisher route
     · output_path="renders/overlay_raw.mp4" -> direct write, NO receipt,
       final.mp4 and its receipt untouched (the pipeline case at
       animation-talking-head-50-50/edit-director.md:106)
     · output_path="assets/video/source.mp4" and "artifacts/x.mp4" -> REJECTED,
       falls back to the canonical route; the named file is untouched
     · traversal guard test at :132 still passes
     · a pre-existing final.mp4 + receipt survive a FAILED render byte-for-byte
     · no .part.mp4 left on success, failure, or supersede
     · second render of an UNCHANGED timeline reports cached scenes -> the
       stable proxies dir still keys by content (tools/video/render_cache.py:52)

3. F1b  supersede: terminal, late-checked, findable
   → tests/contracts/test_render_jobs_inputs.py + test_agent_render_tool.py
     · a job superseded BEFORE acquiring the lock never invokes the renderer
       (fake VideoCompose records zero calls)
     · a job superseded WHILE in the renderer does not publish: final.mp4 and
       receipt are untouched, status is "superseded"  (the pre-publish recheck)
     · the race itself: make job B become active from another thread between
       A's check and A's replace -> impossible, because both are inside the same
       RenderJobStore._lock section; assert A did not publish
     · status is "superseded", never a permanent "queued"
     · _run_render returns promptly on "superseded" AND marks it consumed, so a
       following _render_resume_note does NOT re-report it
     · latest_unconsumed_agent_job surfaces the superseded AGENT job once when
       nothing reported it in-turn, even though active_job_for returns the
       editor's job; a second call returns nothing (one-shot)

4. F2  validate-before; commit only where safe
   → tests/contracts/test_agent_render_tool.py
     · schema-invalid inline doc -> success=False with the validation error,
       no job started, edit_decisions.json untouched
     · valid inline doc + success -> read_edit_decisions() == that doc (NOT the
       source-resolved copy) and the receipt hashes to it
     · valid inline doc + FAILURE -> edit_decisions.json unchanged
     · EDITOR render of doc A while doc B is autosaved mid-render -> B survives
       on disk, and the listing reports current:false for the A render
     · store_asset(final_render) with bytes unrelated to the disk doc ->
       final.mp4 replaced, receipt unlinked, current is FALSE (no forged
       provenance) even with size+mtime_ns matching the old receipt
     · the QA gate's one-liner returns current:false for a stale pair and the
       director's documented step fails rather than passing
     · _store_asset does not block the event loop while a render holds the lock
       (asyncio.to_thread; assert the loop stays responsive)
   → the four existing _run_render tests (:119, :134, :146, :160) pass
     {"cuts": []}, which does not validate, against a repo-root projects dir.
     Convert them to valid docs in tmp_path project dirs FIRST — otherwise they
     fail and also write into the repo.

5. F3 + F4  listing, labelling, preview colour
   → unit test for ffColorToCss: "#CC785C", "0xCC785C", "0xCC785C80", "#CCC",
     "red", "red@0.3", an ffmpeg-only name, "bogus", "" -> expected {css, alpha},
     with black fallback where CSS cannot represent it
   → web/src/studio/StudioPreview.test.jsx: box.color "#CC785C" appears in the
     rendered background and "rgba(0,0,0" does not
   → tests/contracts/test_server_read_api.py: listing returns exactly
     ["final.mp4"], carries current:true, reports current:false after the live
     doc is edited without a re-render, and yields DIFFERENT cache-bust tokens
     for two replacements inside the same second (st_mtime_ns)

6. End-to-end (the actual ticket)
   → Re-run the OPN-30 repro: fresh project, "make a 15s reel + one text
     overlay, render it", then from the editor "trim cut3 to 3s and re-render".
     Assert:
       · the receipt satisfies BOTH checks against the live doc and the file
         (this, not duration, is the invariant)
       · ffprobe(renders/final.mp4) matches the renderer's own timeline math —
         sum(cut durations) MINUS transition overlaps (video_compose.py:1112
         does `cum = cum + durs[i] - d`) — within one frame. Plain sum(cuts)
         equality is wrong for any doc with a transition or a speed change.
       · the UI shows one tile labelled Final render, not three
       · preview frame and exported frame at t=1.0s both show a terracotta pill
```

Success condition, in one sentence: **after any agent turn or editor render, the receipt
beside `renders/final.mp4` matches both the live `artifacts/edit_decisions.json` and the
file's own identity, the editor shows that file as the current Final render, and if they
ever disagree the UI and the QA gate both say STALE instead of staying silent.**

## 5. Risk register

| risk | mitigation |
|---|---|
| Publisher deadlocks against the render that calls it | `project_lock` is an `RLock`; step 1 has a same-thread re-entrancy test |
| A failed render destroys a good `final.mp4` | render to `.part.mp4`, replace only on success. Honest limit: once the replace lands, the old file is gone — what survives is the ability to tell (receipt), not the file |
| Concurrent agent + editor renders corrupt shared scratch | one per-project lock held across the whole render (F1); step 3 asserts a superseded job never invokes the renderer |
| A render that started first publishes over a newer one | the final supersede check and the replace are inside one `RenderJobStore._lock` section, so nothing can become active between them (F1 step 2); step 3 tests the race |
| Lock inversion between the two locks | one stated order: `project_lock` then `_lock`, never the reverse (F1) |
| Editor render writes its snapshot over newer autosaved edits | only the inline-agent route sets `persist_doc` (F2). Verified: `Studio.jsx:221` gates autosave on `agentBusyRef`/`reconcilingRef` only, so autosave *does* run during a render |
| Crash between the video replace and the receipt | receipt is written last and binds file identity, so the window reads STALE rather than falsely current |
| `store_asset` certifies unrelated bytes as current | that route passes neither document, which *unlinks* the receipt — not merely leaves it, since `copy2` preserves mtime and sizes can collide |
| `store_asset` starts consuming files it used to copy | publisher stages via `copy2` for non-temp sources; step 1 asserts both semantics |
| `move=True` staging fails across filesystems | `shutil.move`, not `os.replace`, for the staging hop (`/tmp` is often a different device); step 1 asserts it |
| A superseded result gets reported twice | the in-turn waiter marks it consumed, matching the `done`/`failed` branches; step 3 asserts no re-report |
| The QA gate and the UI drift apart | both call `final_render_status`; the QA director names the exact Bash one-liner (F3) |
| `current` is not proof of bytes | acknowledged in F3: size + `mtime_ns` is a practical token; a metadata-preserving hand copy could fool it. `video_sha256` is the named upgrade if that ever happens — rejected now because the UI polls the listing every 4s |
| Blocking publisher stalls the event loop and the SSE stream | `_store_asset` goes through `asyncio.to_thread`; step 4 asserts loop responsiveness |
| A superseded job hangs forever / is never reported | explicit `superseded` status + `latest_unconsumed_agent_job` with one-shot consumption (F1b), covered by step 3 |
| Canonical replacement serves stale bytes to the browser | `int(st_mtime)` is second-granularity; switch to `st_mtime_ns` and assert differing tokens in step 5 |
| A render overwrites a source asset via `output_path` | direct outputs are constrained to the `renders/` subtree; step 2 asserts cross-subtree rejection |
| Strict validation breaks a working pipeline | four existing `_run_render` tests are known to break and are converted in step 4; `scripts/dev smoke` is the canary for the rest |
| `final_render` loses the never-clobber invariant | scoped to that one kind, which `lib/project.py:54` already calls "the one assembled deliverable"; the `image` collision test stays green |
| A pipeline that passes `output_path` breaks | it is kept, not deleted. Four pipeline docs pass it and one needs a non-final path. Step 2 covers all four routes |
| The QA gate passes a stale `final.mp4` | the gate runs both receipt checks (F3); this is the one skill-doc change the fix requires |
| Three components disagree on how to hash a doc | one shared `canonical_doc_hash` helper used by publisher, listing and QA |
| Two checkouts sharing a project id share a lock | registry keyed by resolved `projects_dir` + `project_id`, creation guarded; step 1 asserts it |
| Render latency when a render is already running | accepted: single-user local app, waiting beats corrupting |

---

## 6. Review rounds

Reviewer: Codex gpt-5.6-sol (high), read-only, four rounds. Every claim was re-verified
against the code before acceptance; every file:line it cited resolved. Confirmed by me:
`ffmpeg -y` at `video_compose.py:1074`; scratch paths from `output_path.parent` at
`video_compose.py:1835/3154/3675/4002`; `int(stat.st_mtime)` at `server/app.py:425`;
`cum = cum + durs[i] - d` at `video_compose.py:1112`; the `active_job_for`/origin gate at
`agent_runner.py:1606-1612`; `box.color` a bare string at
`schemas/artifacts/edit_decisions.schema.json:118`; autosave gated only on
`agentBusyRef`/`reconcilingRef` at `Studio.jsx:221`; `move` true only for temp roots at
`agent_runner.py:1569-1576`; `test_asset_placement.py:59` expecting `renders/reel.mp4`.

### Round 1 — REJECT (9 findings)

| # | finding | resolution |
|---|---|---|
| 1 | Superseded jobs keep executing and share `.compose_tmp` / `.pip_tmp` / `.remotion_props.json` / `.final_review_frames`; an atomic publish alone does not isolate them | Accepted → Defect C + F1's per-project lock across the whole render. Correction: pre-existing, not introduced by rev 1. Chose serialization over job-scoping four families |
| 2 | The `place_asset` change neither forced `final.mp4` nor took a lock | Accepted → F1 forces the name and routes `store_asset(final_render)` through the one publisher and lock. Kept the kind rather than removing it: other producers can legitimately make the assembled file |
| 3 | Persist-before-render recreates the bug (doc B written, ffmpeg fails, video still A) | Accepted → F2 is validate-before / commit-on-success. Strict validation kept, which the reviewer also endorsed |
| 4 | A constant filename is not provenance | Accepted → F3's receipt; "provenance" removed from §3 |
| 5 | `run_media_op` takes an arbitrary output path, so a one-writer claim is defeated | Accepted as a narrowing, not enforcement → the presentational invariant in §3 |
| 6 | "No code path can replace `final.mp4`" was false — `ffmpeg -y` overwrites | Accepted, §1 diagnosis corrected. Rev 2 responded by deleting `output_path`; **round 2 reversed that** and rev 3+ keeps the parameter as a route selector |
| 7 | `color-mix` alone is wrong for schema-valid `0xRRGGBBAA` | Accepted → F4's `ffColorToCss` |
| 8 | Four `_run_render` tests break under strict validation; `duration == sum(cuts)` is invalid with transitions | Accepted → steps 4 and 6 |
| 9 | Proxy cache keys are content-based, so a stable path is safe | Accepted, recorded in step 2 |

### Round 2 — REJECT (14 findings; new material only)

| # | finding | resolution |
|---|---|---|
| 6 | Deleting `output_path` breaks live pipeline contracts, one of which renders a real intermediate to `renders/overlay_raw.mp4`; rev 2's risk register wrongly claimed no skill passes it | Accepted — rev 2 reversed. My grep had covered only `.agents/skills/*/SKILL.md`, not `skills/pipelines/**`. `output_path` stays and selects the route; the plan got smaller |
| 7 | The QA gate reads `final.mp4` and hands over that file plus the live doc, so it can bless stale bytes | Accepted → the QA-gate receipt check in F3 |
| 9 | Supersede is not terminal: the job sits at `queued`, `_render_resume_note` ignores it, an in-turn waiter times out | Accepted → F1b. The lock makes this common, so it is caused by this change |
| 10 | Second-granularity mtime lets two fast replacements share a cache-bust token | Accepted → `st_mtime_ns`. A regression *introduced* by a stable filename |
| 12 | Also breaks `test_asset_placement.py:59` | Accepted → step 1 |
| 14 | Fixing padding and margins while deferring the `boxborderw` offset is the worst split | Accepted → F4 is colour only; all three geometry items move together (§3) |

### Round 3 — APPROVE WITH CHANGES (17 items). All applied in rev 4

| # | finding | applied |
|---|---|---|
| 1 | BLOCKER — self-deadlock: the render path holds a non-reentrant `Lock` and the publisher acquires the same one | `project_lock` is an `RLock`, plus `_publish_final_locked` for already-locked callers; re-entrancy test in step 1 |
| 2 | BLOCKER — the generic publisher writes snapshot A over newer autosaved doc B, because autosave is not suspended during a render | F2: only the inline-agent route commits a doc; editor and `store_asset` routes publish video (+receipt) only. Test in step 4 |
| 3 | MAJOR — a doc-only `current` check calls new bytes current under an old receipt | F3: receipt written last as the commit marker, and `current` also binds `video_size` + `st_mtime_ns`. The "any failure leaves the prior file intact" claim is softened in F1 |
| 4 | MAJOR — `store_asset` could falsely certify unrelated bytes | F2: that route writes no receipt, so its output reads stale |
| 5 | MAJOR — `os.replace` would consume non-temp sources `place_asset` currently copies | F1 step 1 stages via `copy2` unless `move=True`; both semantics tested |
| 6 | MAJOR — one post-acquire supersede check is too early; A can publish after B becomes active | F1 step 2 re-checks immediately before the replace; in-flight test in step 3 |
| 7 | MAJOR — a synchronous publisher blocks the async loop and the SSE stream for a whole render | `_store_asset` uses `await asyncio.to_thread`; asserted in step 4 |
| 8 | MAJOR — the route table permits destructive writes anywhere in the project | direct outputs constrained to the `renders/` subtree; rejection test in step 2 |
| 9 | MAJOR — F1b names an outcome with no way to find it (`active_job_for` returns the editor job) | `latest_unconsumed_agent_job` with deterministic ordering and one-shot consumption |
| 10 | MINOR — two anchors drifted | corrected: schema `:118`, timeline `:1112` |
| 11 | MINOR — §6 still said the `output_path` deletion stands, and cited stale step numbers | corrected in both places above |
| 12 | MINOR — hash canonicalisation and lock identity under-specified | one `canonical_doc_hash` helper; lock registry keyed by resolved `projects_dir` + `project_id`, guarded creation |
| 13 | NIT — a bare colour name may be ffmpeg-only and invalid in CSS | `CSS.supports` check with a black fallback (F4) |
| 14 | Design rulings: A1 conditional, A2/B1/C1 accepted | all conditions met above; strict validation and the colour-only WYSIWYG split confirmed as intended |
| 15 | Scope: per-project serialization, receipt-backed current/stale, and the QA check are required; policing all writers, cleanup, watchers, history and geometry are not | matches §3 as written |
| 16 | Writers/readers: outside replacement must become stale via file identity | F3's identity half |
| 17 | Cache and tests: content-based proxy keys fine; placement, validation, duration, listing and mtime-ns regressions all needed | steps 1–6 |

### Round 4 — APPROVE WITH CHANGES (5 items). All applied in rev 5

Round 4 confirmed every rev-4 anchor resolves and that changes 6, 8, 12 and 13 were fully
addressed. Five remaining, all real:

| # | finding | applied |
|---|---|---|
| 1 | MAJOR — a single `doc` parameter conflates "hash this for the receipt" with "write this to disk", so the editor route could either get no receipt or destroy newer autosaved edits | Split into `receipt_doc` + `persist_doc`, with the three-route table in F1. Editor passes `receipt_doc` only |
| 2 | MAJOR — checking supersede "immediately before the replace" is still racy unless the job-state lock spans both; and an in-turn `superseded` result was never marked consumed, so it would be re-reported | F1 step 2 holds `RenderJobStore._lock` across the check *and* the replace, with the lock order stated once; F1b marks it consumed in the waiter, as its own `done`/`failed` branches already do (`agent_runner.py:1453`, `:1458`). Race test in step 3 |
| 3 | MAJOR — leaving the old receipt does not guarantee stale: `copy2` preserves mtime and sizes can collide. Also `os.replace` from a temp root can raise `EXDEV` | An unreceipted publish **unlinks** the receipt (F1 step 5); staging uses `shutil.move`. Equal-size/equal-mtime and cross-device tests in step 1 |
| 4 | MINOR — a markdown QA director cannot import a Python helper just because the plan says it runs the same checks | F3 names the exact `final_render_status` Bash one-liner as a gate step, and step 4 tests that a stale pair fails the gate |
| 5 | NON-BLOCKING — size + `mtime_ns` is not proof of bytes; `cp -p` preserves both | Wording corrected in F3 with the reason it is the right trade (4s listing poll) and `video_sha256` named as the upgrade. The reviewer explicitly did not block on this |

### Round 5 — **APPROVE**

All five round-4 items confirmed addressed; all cited anchors re-verified as resolving. Two
non-blocking wording notes, both applied here: §2's opening said the receipt binds "the exact
bytes" and §3 said an outside writer "cannot forge" the identity half, each stronger than the
`cp -p` caveat F3 states — both softened to *file identity*. Also noted: CI has no second
filesystem, so the cross-device staging test mocks `os.replace` to raise `EXDEV` (recorded in
step 1).

**Converged. Rev 5 is the agreed plan.**

---

## 7. What shipped, and where it differs from the plan

Built 2026-08-03 in the order of `architecture.md` §8. Three deliberate deviations, all
smaller than what the plan specified:

| plan said | shipped | why |
|---|---|---|
| `_store_asset` routes `kind == "final_render"` to the publisher | `place_asset` itself routes that kind (`lib/project.py`), and `_store_asset` now runs `place_asset` through `asyncio.to_thread` for EVERY kind | the chokepoint belongs in the one writer, not in its one caller, so a future caller can't reintroduce the bug. As a bonus the tool no longer sha256s a 500 MB video on the event loop |
| F4's `ffColorToCss(c) -> {css, alpha}`, where a hex `AA` byte and an `@alpha` suffix MULTIPLY `box.opacity`, validated with `CSS.supports` | `ffBoxColorToCss` + `ffBoxBackground(box)`, where **`box.opacity` alone is the alpha**, validated by exact ffmpeg shape | the plan's alpha model was simply wrong, and measuring ffmpeg 8 (not reading its docs) is what showed it — see §8 finding 5. `CSS.supports` is also absent in jsdom, and a CSS-valid `#CCC` is exactly what ffmpeg rejects, so shape validation is the tighter contract |
| `explainer/compose-director.md` OMITS `output_path` | it passes `"renders/final.mp4"` | same publisher route (row 2 of the route table), but three downstream references in that pipeline (`compose-director.md`, `publish-director.md`, `executive-producer.md`) named `renders/output.mp4` and were updated with it — omitting would have left the tool call with no defined output for a direct caller |

Also added, from the §3 concession: `_run_op` warns when a media op writes into the top
level of `renders/` (`_deliverable_write_warning`), and `_run_media_op` forwards it.

**Verification run (final tree).** `ruff check` clean · `pytest tests` **1423 passed, 16
skipped, 1 xfailed, 0 failed** · `npm test` **317 passed / 14 files** · `npm run build` clean ·
`scripts/dev smoke` ok · `scripts/dev stop` clean.

The end-to-end from §4 step 6 was run with the REAL ffmpeg renderer (agent turns excluded):
13.13s published + committed + receipt current → the timeline trimmed to 11s reads STALE →
the editor re-render publishes 11.13s to the SAME `renders/final.mp4`, leaves the live doc
untouched, reads current, and `renders/` holds exactly `{final.mp4, .final_receipt.json}` with
one current tile in the listing. The exported text box measures 29,418 px of `rgb(183,108,82)`
— `#CC785C` at opacity 0.9 — and the preview emits `rgba(204, 120, 92, 0.9)` for the same doc.

`test_dev_cli.py::test_fast_test_dry_run_shows_only_deterministic_local_checks` was failing
before this change (it asserts the fast tier selects `tests/tools/test_ffmpeg_hdr_preserve.py`,
which only happens while `tools/video/video_compose.py` is dirty — confirmed failing on a clean
worktree at HEAD). It now passes incidentally, because this change touches `video_compose.py`.
The assertion still tracks which files are dirty rather than the code; that is a repo
infrastructure wart, not this ticket's.

---

## 8. QA review — Codex gpt-5.6-sol (high), nine rounds, APPROVED

Reviewer ran its own suites plus a live editor-driven render cycle; it changed no production
file (tests only), per the brief. Every finding was re-verified against the code — and where
the claim was about ffmpeg, against the ffmpeg binary — before being accepted. Its regression
file `tests/contracts/test_opn30_qa_regressions.py` stays in the tree, including one
`xfail(strict=True)` marking the accepted TOCTOU below.

Fourteen findings. Two of the fourteen were reviewer claims about ffmpeg semantics that turned
out to be right where I was wrong, and two were my own reports over-claiming what a test
actually asserted — both caught by the reviewer, not by me.

| # | severity | finding | resolution |
|---|---|---|---|
| 1 | BLOCKER | the QA-gate one-liner hardcoded `'projects'` and `.venv/bin/python`, so a packaged app or any `OPENNOLAN_HOME` would report "no final.mp4" for a current render and block delivery | fixed: `app_paths.projects_dir()` + bare `python` per AGENT_GUIDE.md:278. Mine was the only `.venv/bin/python` in the whole skills tree |
| 2 | MAJOR | a job that PUBLISHED could stay non-terminal: `_set` dropped its `done` when a newer job became active between the publish and the record | fixed and generalized — `_set` now marks `superseded` instead of dropping, and a publish that won the commit guard records with `force=True` |
| 3 | MAJOR | superseded media ops sat at `running` forever (pre-existing, but newly inconsistent) | fixed by the same one `_set` change |
| 4 | MAJOR | `output_path="renders/proxies/<scene>.<key>.mp4"` passed containment and would poison the per-scene proxy cache | fixed: DIRECT children of `renders/` only |
| 5 | MAJOR | preview ≠ export for `box.color` alpha | **confirmed, fixed on the preview side, renderer-side change refused.** Measured ffmpeg 8: `#CC785C80@0.9` renders IDENTICALLY to `#CC785C@0.9` (the `@` suffix overrides a hex AA byte, it does not multiply), and `#CCC` / an embedded `@` fail the render outright. So `box.opacity` alone is the alpha; `ffColorToCss`/`cssAlphaColor` became `ffBoxColorToCss`/`ffBoxBackground`. Teaching the renderer to multiply would invent an export semantic for input ffmpeg rejects and change every existing caption's pixels |
| 6 | MAJOR | a symlinked `renders/` defeated containment — the resolved candidate failed the check while the LEXICAL fallback followed the same link, so the canonical write landed outside the project | fixed: one `renders_dir()` resolver, `RendersDirEscapes` refusal. Chosen over "return a contained path" because `get_file` (`server/app.py:463`) resolves before its own containment check, so such a deliverable would be listed and then 400 on playback |
| 7 | MINOR | receipt-last did not guarantee stale: a same-size replacement with a restored mtime plus a failed receipt write read as current | fixed, and the class is gone — the old receipt is UNLINKED inside the commit guard, before `os.replace` |
| 8 | MINOR | a refused self-move (`src == final.mp4`, `move=True`) deleted the deliverable | fixed: `move` is forced off when `src` resolves to the destination |
| 9 | MINOR | resolved paths leaked into the public deliverable path — `relative_to` raised for a relative `projects_dir` and reported the physical name for an in-project `renders` alias | fixed: `FINAL_RENDER_REL` constant; `relative_to` removed from the reported path |
| 10 | MINOR | absent `box` previewed a black pill the export never draws | fixed: `background` only when `o.box` exists |
| 11 | MINOR | colour validation was charset-only, so `#CCC` and `notacolor` still died deep in the filtergraph | fixed: exact hex shapes + names validated against `ffmpeg -colors` (cached; empty set → names unvalidated, so no environment regression) |
| 12 | MINOR | `red@` (empty alpha) emitted `red@@0.9` | fixed: branch on the partition SEPARATOR, not the alpha |
| 13 | MINOR | the new alpha regex REJECTED ffmpeg-valid `white@00.5`, `white@1.`, `white@0x0` — a working-reel regression | fixed: `_valid_ff_alpha` parses (finite float in 0..1, or `0x` hex in 0..255) instead of spelling |
| 14 | MINOR | `white@0X80` passed but ffmpeg rejects it | fixed: literal lowercase `0x` prefix. Re-measured the boundary — the PREFIX must be lowercase, the DIGITS need not be |

**Refuted, with the reviewer accepting both:**

- **A symlinked PROJECT dir is not an escape.** `get_file` already defines "inside the
  project" as the RESOLVED project dir, so that layout is first-class for every read in the
  app; requiring the project to be a direct child of the projects root would be a new,
  inconsistent policy that breaks a project parked on an external drive. What the reviewer's
  run actually hit there was finding 9, a late `relative_to` crash. Inverted into two positive
  contract tests.
- **The TOCTOU `renders/` swap** (replace the validated dir with a symlink during staging) is
  real and is left open as `pytest.mark.xfail(strict=True)`: it needs an actor that already
  has write access to the project, and such an actor can overwrite `renders/final.mp4`
  directly with no race — the receipt is what detects both. Closing it needs `openat`/
  `O_NOFOLLOW` handles through `copy2`, `os.replace` AND `atomic_write_json`; its own ticket.
- **The `plan-then-architecture` skill in the diff** is not scope creep on this ticket (the
  user requested it in an earlier turn), though the reviewer is right that it belongs in its
  own commit.

**Not verified by the reviewer, in its words:** "I could not perform the browser UI leg
because Chromium launch is blocked by the managed macOS sandbox at MachPortRendezvousServer
with Permission denied 1100; I therefore did not independently inspect the live preview,
assets panel, or exported-frame comparison, and I ran no agent chat turn."

### 8.1 The browser leg, closed by the author

Playwright/Chromium does run here, so the leg the reviewer could not reach was driven against
the REAL dev app (`run-dev`, `OPENNOLAN_PROJECTS_DIR` pointed at the published e2e project) —
not jsdom, not the API alone. Every assertion passed:

| checked in the real browser | result |
|---|---|
| tiles claiming to be the deliverable | **1** (was 3 in the repro) |
| its label while the receipt matches | `Final render`, not styled stale |
| edit `edit_decisions.json`, no re-render, wait for the 4s poll | the SAME tile flips to `Final render (stale — re-render)`, muted |
| hover title | "the timeline changed since this render — re-render" |
| download link | `⤓ final.mp4` (one file, canonical name) |
| studio preview box: visible, named, non-zero geometry inside the stage | `314x74`, `"OpenNolan"`, `rgba(204, 120, 92, 0.9)` |
| the PAINTED preview pixel vs the REAL exported frame (both ffmpeg-sampled) | `rgb(184,108,83)`, found in **26,024 px** of the exported frame |
| the download link | `⤓ final.mp4`, `download="final.mp4"` |
| the cache-bust token in that URL vs the file's mtime | exact match, sub-second, JS-safe |
| console / page errors | none |

**20/20 assertions pass.** The driver went through two reviewer audits: the first found that
v1 *reported* the download link and the "Earlier render" label without asserting them (a real
over-claim — the litter branch had never run in a browser at all), the second asked for the
cache token to be parsed rather than eyeballed.

**Tightening that last assertion found a defect in already-approved code.** Comparing the URL
token to `st_mtime_ns` failed: `v=1785749838707894300` for a file at `...894387`. A 19-digit
nanosecond value is past float64's exact-integer range, so JSON → JS silently dropped the low
digits. Cache-busting still worked (~100 ns resolution), but the API was emitting an opaque
token its only consumer cannot represent — the exact trap the comparison walked into. The
renders bucket now serves **microseconds** (`st_mtime_ns // 1000`): 16 digits, exact in JS,
still far finer than two renders can be apart. Pinned in
`tests/contracts/test_server_read_api.py` (`== st_mtime_ns // 1000`, `!= int(st_mtime)`,
`< 2**53`). `kinds` and `agent_renders` stay on whole seconds — pre-existing buckets outside
this ticket, and the consumer treats every value as opaque; the reviewer accepted that
asymmetry as the surgical choice.

That closes the WYSIWYG claim at both ends with measurements rather than inference, and with
no computed constant on either side: the painted preview pixel is asserted to be a colour the
exported frame demonstrably contains.

**The reviewer's verdict on the browser leg, verbatim:** "Codex could not launch Chromium in
its managed macOS sandbox, but independently audited the author-run browser driver, fixture,
selectors, screenshots, cache token, and preview/export pixel comparison; that audited 20/20
evidence supports the final APPROVE verdict."

### 8.2 Merged with main after approval

`main` moved ahead while this was in review, and its PR #7 ("browse project assets as
folders") rewrote the Assets panel — touching `server/app.py`, `web/src/App.jsx` and
`web/src/styles.css`, the same three files this change touches. Merged and resolved by hand:
both `_browse_*` helpers and the `/browse` endpoint kept alongside this branch's `current` /
`reason` / microsecond token, and all tests from both sides retained. The deliverable tiles
still sit at the top of the Assets panel with main's `FolderNav` and folder grid below.

Everything was re-run on the MERGED tree, not just on the conflict resolution: pytest **1426
passed, 0 failed**, npm test **321**, build clean, smoke ok, real-ffmpeg end-to-end clean, and
the browser check **20/20**.

**A second-order gap main introduced, flagged not fixed:** `HIDDEN_BROWSE_DIRS` hides
`renders/proxies` but not `renders/`, so the new folder browser lists `final.mp4` beside any
earlier render as plain files, with no current/stale marker — the labelled tiles are only in
the panel above it. That surface did not exist when this plan was approved, and the browser's
job is finding media rather than certifying the deliverable, so widening the diff to cover it
now would be scope creep. Worth its own small follow-up if the folder browser becomes the
primary way people pick up a finished reel.

**Still not done by either party, deliberately:** a real agent chat turn. The agent route
(`_run_render` → validate → `start_with_inputs` → publisher) is covered by contract tests and
by the end-to-end driving the same store API, so the only untested link is the LLM producing
the document. The reviewer's recommendation, which I follow: "do not run one; deterministic
route, publication, receipt, FFmpeg, and UI coverage are sufficient, while a roughly 5.50
dollar stochastic provider turn adds little OPN-30 signal." Run one only if the agent's
document-generation prompt changes, or for provider-level acceptance testing.
