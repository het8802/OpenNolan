# OPN-30 architecture — one publisher for the deliverable

**Status: BUILT** (2026-08-03) · companion to [`plan.md`](plan.md) (rev 5, Codex-approved)
What shipped, and the three places it differs from the plan: `plan.md` §7.
Read this for the shape. Read `plan.md` for the reasoning, the rejected options, and
the test list.

**The change in one sentence:** three independent writers can each land a file in
`projects/<id>/renders/` and none of them records which timeline produced it — so they
all funnel into one `publish_final_render()` that forces the name, holds a per-project
lock, and writes a receipt binding the video to the document.

---

## 1. Today — three writers, no link to the timeline

```
┌─ agent turn ─────────────┐  ┌─ editor Render ──────┐  ┌─ store_asset route ┐
│ render(edit_decisions =  │  │ POST /render         │  │ agent made a file  │
│   {...inline...})        │  │ (no body)            │  │ elsewhere:         │
│ output_path OMITTED      │  │                      │  │ kind=final_render  │
└────────────┬─────────────┘  └──────────┬───────────┘  └─────────┬──────────┘
             ▼                           ▼                        ▼
  start_with_inputs()            start()                  place_asset()
    render_jobs.py:53              render_jobs.py:40        lib/project.py:292
             │                           │                        │
  _run_with_inputs()              _run()                          │
    render_jobs.py:262             render_jobs.py:242              │
             │                           │                        │
   ★ DIVERGENCE 1 — the output name
     render_jobs.py:274  _normalize_output_path(..., fallback)
       output_path omitted → renders/agent_render_<job>.mp4
     render_jobs.py:43   out_name (editor, always)
                         → renders/editor_preview_<job>.mp4
     lib/project.py:330  name taken, different bytes
                         → renders/final.<sha8>.mp4   never clobbers
             │                           │                        │
             └───────────┬───────────────┘                        │
                         ▼                                        │
        ╔════════════════════════════════════╗                    │
        ║ _execute_render()  render_jobs.py: ║                    │
        ║ 316   → VideoCompose render_proxies║                    │
        ╚════════════════╤═══════════════════╝                    │
                         ▼                                        ▼
              renders/<whatever>.mp4                    renders/<whatever>.mp4
                         │                                        │
   ★ DIVERGENCE 2 — the document that produced it
     _run_with_inputs renders inputs["edit_decisions"] and NEVER writes it.
     The editor reads artifacts/edit_decisions.json.
     Nothing compares the two.  render_jobs.py:262-285
                         │
                         ▼
   web/src/App.jsx:1318  every top-level file in renders/ is listed under
                         ONE "Final render" heading, no current marker
                         ▼
              ── user picks final.mp4. It is the 15s cut.
                 The timeline says 13s. ──
```

**What the repro actually produced** — `.local/projects/opn30-sync-check/renders/`:

| file | duration | how it got there |
|---|---|---|
| `agent_render_4b4193bafc61.mp4` | 13.13s | `render_jobs.py:274` fallback |
| `final.e5c66172.mp4` | 13.13s | `lib/project.py:330` hash suffix |
| `final.mp4` | **15.13s** | written once, by the FIRST turn. Never replaced |

---

## 2. After — one funnel, one name, one receipt

```
┌─ agent render ───────────┐  ┌─ editor Render ──────┐  ┌─ store_asset ──────┐
│ receipt_doc = caller doc │  │ receipt_doc =        │  │ receipt_doc = None │
│ persist_doc = caller doc │  │   disk snapshot      │  │ persist_doc = None │
│                          │  │ persist_doc = None   │  │                    │
└────────────┬─────────────┘  └──────────┬───────────┘  └─────────┬──────────┘
             │                           │                        │
   ★ validate BEFORE the job starts (agent route only)
     agent_runner.py:1414  _run_render → validate_artifact(...)
       invalid → success=False, NO job, disk untouched
             │                           │                        │
             └───────────┬───────────────┘                        │
                         ▼                                        │
        ╔═════════════════════════════════════════════╗            │
        ║ _execute_render()      render_jobs.py:316   ║            │
        ║                                             ║            │
        ║  with project_lock(...):        ← WHOLE run ║            │
        ║    :A  superseded? → mark + bail, no render ║            │
        ║    :B  VideoCompose → .final.<job>.part.mp4 ║            │
        ║    :C  publish_final_render(...)            ║            │
        ╚════════════════════════╤════════════════════╝            │
                                 │        await asyncio.to_thread ─┘
                                 ▼        (never block the loop)
═════════════ ALL THREE ROUTES CONVERGE HERE — one publisher ═════════════
        ┌──────────────────────────────────────────────────────────┐
        │  publish_final_render()               lib/project.py NEW │
        │  takes project_lock (RLock — re-entrant for route C)     │
        │                                                          │
        │  1  stage   src → renders/.final.<uuid>.part.mp4         │
        │             shutil.move if move else copy2               │
        │             (os.replace raises EXDEV from /tmp)          │
        │  2  recheck superseded, holding RenderJobStore._lock     │
        │             ── steps 2 and 3 are ONE critical section    │
        │  3  os.replace(part → renders/final.mp4)      ATOMIC     │
        │  4  persist_doc → artifacts/edit_decisions.json          │
        │  5  receipt  → renders/.final_receipt.json  ← COMMIT     │
        │       receipt_doc None → UNLINK the receipt              │
        └───────────────────────────┬──────────────────────────────┘
                                    ▼
                     renders/final.mp4  +  .final_receipt.json
                                    ▼
        final_render_status()                  lib/project.py NEW
          ├─ server/app.py:411   listing → current: true|false
          └─ qa-director.md      python3 -c "...final_render_status..."
                                    ▼
        web/src/App.jsx:1318   Final render │ STALE │ Earlier render
```

---

## 3. Inside the publisher — why the order is the design

```
        step 3            step 4                step 5
   os.replace(video)   write doc           write receipt
        │                  │                     │
   ─────┴──────────────────┴─────────────────────┴─────► time
        │                  │                     │
   crash HERE         crash HERE            crash HERE
        │                  │                     │
        ▼                  ▼                     ▼
   new video          new video             all three
   old doc            new doc               agree
   old receipt        old receipt                │
        │                  │                     ▼
        └────────┬─────────┘                  current
                 ▼
      receipt does not describe
      this file → STALE, visibly
```

The receipt is written **last** because it is the commit marker. Any interruption
before it leaves a receipt that does not match, which reads as **stale** — never as
falsely current.

**`current` is two checks, not one.** A document-only check would call new bytes
current during the step-3→5 window, because the *old* receipt still hashes to the
*old* live doc.

```
current == a receipt exists
      AND receipt.doc_hash       == canonical_doc_hash(live edit_decisions)
      AND receipt.video_size     == stat(final.mp4).st_size
      AND receipt.video_mtime_ns == stat(final.mp4).st_mtime_ns
```

`ponytail:` size+mtime_ns is a practical token, not a byte proof — a `cp -p` defeats
it. Deliberate: `web/src/App.jsx:1287` polls this listing every 4s and hashing a
50 MB video there is the wrong trade. Add `video_sha256` if a real out-of-band writer
ever appears.

---

## 4. The three routes, side by side

| | agent render | editor Render | `store_asset(final_render)` |
|---|---|---|---|
| entry | `agent_runner.py:1414` | `render_jobs.py:40` | `agent_runner.py:1549` |
| doc source | inline, from the model | `artifacts/edit_decisions.json` | none |
| validated first | **yes**, fail the call | already on disk | n/a |
| `receipt_doc` | the caller's doc | the snapshot it rendered | `None` |
| `persist_doc` | the caller's doc | **`None`** | `None` |
| result | video + doc + receipt | video + receipt | video, receipt **unlinked** |
| reads as | current | current until the doc moves on | never current |

**Why the editor route must not write the doc.** `web/src/studio/Studio.jsx:221` gates
autosave on `agentBusyRef` / `reconcilingRef` only. `:260`'s `if (rendering) return`
guards re-entering a render, not saving. So:

```
  t0  user clicks Render        snapshot A read from disk
  t1  render running (~40s)     user drags a clip → autosave writes B
  t2  render succeeds           ← writing A back here DESTROYS B
```

Passing `persist_doc=None` leaves B alone. The receipt records A's hash, A ≠ B, so the
render correctly reads **stale**.

---

## 5. `output_path` selects the route (it is NOT removed)

Four pipeline contracts pass it, and one needs a non-final path:
`animation-talking-head-50-50/edit-director.md:106` renders `overlay_raw.mp4`.

```
   output_path
       │
       ├─ omitted ─────────────────────────► publisher → renders/final.mp4
       ├─ resolves to renders/final.mp4 ───► publisher (same)
       ├─ other path UNDER renders/ ───────► direct write, no receipt,
       │                                     non-current  (overlay_raw.mp4)
       └─ anywhere else in the project ────► REJECTED, falls back to canonical
             ★ tightening: _normalize_output_path (render_jobs.py:378) today
               only checks "inside the project", so assets/video/source.mp4
               would let a render overwrite a source asset
```

---

## 6. Supersede — from silent limbo to a terminal state

```
   BEFORE                                AFTER
   ──────                                ─────
   B supersedes A                        B supersedes A
        │                                     │
   A keeps rendering ──► corrupts        A hits a supersede check
   shared .compose_tmp                     ├─ before the lock → no render
   (video_compose.py:1835, 3154,           └─ before step 3   → no publish
    3675, 4002)                                 │
        │                                       ▼
   _set() drops A's update                status = "superseded", .part gone
   render_jobs.py:236                           │
        │                                       ├─ in-turn waiter returns now
   A stuck at "queued" forever                  │  and marks it consumed
        │                                       │  (agent_runner.py:1453,1458)
   active_job_for returns B (editor)            └─ latest_unconsumed_agent_job
   render_jobs.py:97                               surfaces it once next turn
        │
   _render_resume_note returns None
   agent_runner.py:1592  ← agent never told
        │
   in-turn waiter TIMES OUT
   agent_runner.py:1442
```

**Lock order, stated once:** `project_lock` (outer, held across a whole render) then
`RenderJobStore._lock` (inner, microseconds). Never the reverse.

---

## 7. Surface to touch

New, in `lib/project.py`:

| symbol | job |
|---|---|
| `project_lock(projects_dir, project_id)` | `RLock` registry keyed by **resolved** `projects_dir` + `project_id`; creation guarded by a module lock |
| `canonical_doc_hash(doc)` | `sha256` of `json.dumps(sort_keys=True, separators=(",",":"))`. The ONE hash |
| `final_render_status(projects_dir, project_id)` | `{"current": bool, "reason": str}`. The ONE verifier — listing and QA both call it |
| `publish_final_render(...)` / `_publish_final_locked(...)` | public takes the lock; private assumes it is held (route C re-entrancy) |

Changed:

| file:line | change |
|---|---|
| `server/render_jobs.py:40`, `:43` | editor route drops `out_name`, publishes canonically |
| `server/render_jobs.py:262`, `:274` | agent route routes by `output_path` instead of a fallback name |
| `server/render_jobs.py:316` | `project_lock` across the whole render; `.part.mp4`; publish; supersede rechecks |
| `server/render_jobs.py:378` | constrain to the `renders/` subtree |
| `server/render_jobs.py:236` | add `superseded` as a terminal status past `_set`'s guard |
| `server/render_jobs.py:97` | add `latest_unconsumed_agent_job()`, newest-first, one-shot |
| `server/agent_runner.py:1414` | validate the inline doc before starting; pass both docs |
| `server/agent_runner.py:1442` | treat `superseded` as terminal + `mark_consumed` |
| `server/agent_runner.py:1549` | `final_render` → publisher via `await asyncio.to_thread` |
| `server/agent_runner.py:1592` | resume note uses `latest_unconsumed_agent_job` |
| `lib/project.py:292` | `place_asset` unchanged for every other kind |
| `server/app.py:411`, `:425` | `current` per render; `int(st_mtime)` → `st_mtime_ns` |
| `web/src/App.jsx:1318` | three labels instead of one heading |
| `web/src/studio/StudioPreview.jsx:532` | `ffColorToCss()` for the box background |
| `skills/pipelines/instagram-fast-reel/qa-director.md` | run `final_render_status` before `pass` |
| `skills/pipelines/explainer/compose-director.md:167` | drop `"output_path": "renders/output.mp4"` |

Untouched on purpose: `run_media_op` / `start_op` (`render_jobs.py:287`),
`hyperframes_compose.py:673`, `TEXT_ANCHOR_CSS`, `server/activity.py:49`. See
`plan.md` §3.

## 8. Build order

```
1  lib/project.py         lock + hash + status + publisher  ← no deps
2  render_jobs.py         both routes publish; supersede terminal
3  agent_runner.py        validate-before; to_thread; resume note
4  app.py + App.jsx       current/stale + st_mtime_ns
5  StudioPreview.jsx      ffColorToCss
6  the two skill docs
7  re-run the OPN-30 repro end to end
```

Each step has its checks in `plan.md` §4. `scripts/dev test fast` between steps,
`test full` + `smoke` before review, `scripts/dev stop` at the end.

---

**The whole bug is upstream of the renderer.** `_execute_render` and `VideoCompose`
never behaved incorrectly — they rendered exactly what they were handed, to exactly the
path they were given. Every defect lives in *naming the output* and *forgetting the
input*, which is why the fix is one publisher and one receipt rather than anything
inside the render engine.

**`store_asset` was the only writer that literally could not replace the deliverable.**
`place_asset:330` hash-suffixes by design. The agent route always *could* have
overwritten `final.mp4` — `ffmpeg -y` at `video_compose.py:1074` — it just never chose
that name. Two different bugs wearing the same symptom.

**The receipt, not the filename, is the invariant.** A stable path only makes the
deliverable findable; it cannot prove the bytes match the timeline. That is why the
success condition in `plan.md` is a receipt comparison and not a duration comparison —
durations do not even equal `sum(cuts)` once a transition exists (`video_compose.py:1112`).
