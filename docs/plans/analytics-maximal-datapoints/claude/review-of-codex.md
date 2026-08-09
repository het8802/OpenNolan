# Review of `codex/plan.md` — adversarial cross-review

STATUS: REVIEW
Reviewer: claude · 2026-08-05
Subject: `docs/plans/analytics-maximal-datapoints/codex/plan.md` (845 lines, 154 catalog rows)
My phase-1 doc: `docs/plans/analytics-maximal-datapoints/claude/plan.md` (1315 lines)

No compliance review — the human deferred it. Neither doc wrote one; no scope drift to report.

---

## 1. Verdict

Not safe to build from as-is, but it is closer than mine on the parts that matter most.
Anchor honesty is high: I verified **186 distinct `file:line` pairs** and only **7 are
materially wrong** — nothing was invented. The defects are in *viability*, not fabrication:
**25 rows name a hook that cannot produce the properties they declare** at the volume they
claim, because the cited line runs per-frame, per-keystroke, per-log-line, or inside a
1.5s/4s poll.

Single worst defect: **the shared envelope's `session_id` (line 105) does not and cannot
exist on backend-emitted events.** `analytics.capture` attaches only `device_id` + `env` +
`internal` (`server/analytics.py:174`, `:68-77`). Four §4 metrics — time-in-app-vs-rendering,
export success rate, crash-free sessions, agent-cost-per-export — join frontend and backend
events across two processes. As specified they cannot be computed. My doc has the same hole
and asserts it less loudly, which is worse, not better.

Single best thing it has that mine lacks: **row 39 `browser_proxy_finished`
(`server/editor.py:140`)** — the ProRes→VP9/WebM transcode Chromium forces on every
HyperFrames alpha overlay. Real, expensive, user-visible, and I did not observe it at all.
Runner-up: **row 114 `render_artifact_current`** — the receipt-staleness concept
(`lib/project.py:445`). I measured whether a file exists; they measured whether it can be
trusted.

---

## 2. CONFIRMED ERRORS

### Sampling method and coverage

I extracted **every distinct `(file, line)` pair appearing anywhere in the doc** — §2's map,
all 154 catalog rows' hook column, §7's 52 code-evidence cells, and §10/§11 — into a single
list and printed the real source line for each. That is 186 pairs and it covers **100% of §2
(28 anchors) and 100% of the catalog rows across 3a–3l**, not a 25-row sample. Two files were
also read in full to settle disputed semantics (`lib/project.py:430-625`,
`server/render_jobs.py`), plus targeted reads of the call sites that determine whether a hook
is hot (`StudioPreview.jsx:226-275`, `desktop/main.js:378-410`, `server/state.py:120-127`,
`web/src/App.jsx:45-62,1288-1300`, `web/src/api.js:1-20`).

| Result | Count | Share |
|---|---:|---:|
| Anchors verified | 186 | 100% |
| Exact — cited line *is* the statement/handler/branch to instrument | 124 | 66.7% |
| Imprecise but findable — points at the explanatory comment, section header, or enclosing `def`/component/route decorator | 55 | 29.6% |
| **Materially wrong** — line does something else | **7** | **3.8%** |
| **Hook-viability defects** — anchor may be right, but a `capture()` there cannot produce the declared row | **25 rows** | — |

The 29.6% "imprecise" group is a house-style difference, not dishonesty (e.g. `Studio.jsx:297`
is the comment above the adopt effect; the effect is at `:305`, the emit-worthy line at `:320`).
I am not filing 55 rows for it. It matters only because the human will open the file and land
three lines early — so the merged doc should adopt one rule: **cite the line you would put the
call on.**

### 2.1 Errors that change the design (HIGH)

| # | Their claim (quote + line) | What the code actually says | Sev | Fix |
|---:|---|---|---|---|
| E1 | "Every uploaded product event also carries … `install_id I`, `session_id I` … agent and render events additionally carry `turn_id I` and `job_id I`" (their line 105-107) | `server/analytics.py:172-174` sends `distinct_id=settings.device_id()` plus `_scrub(props)` plus `_env_props()` = `{env, internal}` only (`:68-77`). There is no session, project, turn or job concept anywhere in the backend analytics path. `device_id()` (`server/settings.py:80`) is per-**install**, not per-session. Backend renders (`render_jobs._run_with_inputs`, `:361`) are started by the *agent*, with no HTTP request from the renderer at all, so no session header can be threaded. | HIGH | The envelope must split: `install_id` is free today; `session_id` is **frontend-only** unless the editor passes it on `POST /render` and `POST /chat` and `RenderJobStore` stores it on the job record. `turn_id`/`job_id` must be minted server-side and echoed to the client. Any §4 metric that spans processes has to name which id carries the join. |
| E2 | Row 118 `export_started` hook `server/render_jobs.py:58`; row 102 `render_queued` same line with `origin E{editor\|agent}` | `:58` is `def start(self, project_id)` — the **editor-only** entry point, and it hardcodes `"origin": "editor"` at `:66`. The agent path is `start_with_inputs` (`:71`) and the media-op path is `start_op` (`:94`). Meanwhile row 119 `export_completed` hooks the receipt, which **both** paths reach (`_run_with_inputs` → `_execute_render` → `publish_final_render` when `publish=True`, `:384`, `:396`). | HIGH | Numerator and denominator come from different populations, so their §4 "Export success rate = export_completed / export_started" **can exceed 100%** whenever the agent exports. `origin='agent'` is unreachable at `:58`. Emit the start event from all three entry points, or from `_set` on the `queued→running` transition (`:309`), which every origin passes through. |
| E3 | Row 87 `edit_origin_rollup` hook `web/src/studio/model.js:501` | `model.js:501` is `summarizeDocChange(prev, next)`. `RULES.md:42` states `studio/model.js` is "**Pure**; tested in `model.test.js`". Worse: it is called from `Studio.jsx:179` — `dbg.event('edit.live', summarizeDocChange(prev, nd))` — which runs **on every frame of a coalesced drag** (`live`, `Studio.jsx:175`; RULES.md:60). | HIGH | A `capture()` there both breaks the purity contract and uploads per-frame. Their own §10 proposes the right fix (`classifyDocChange` beside it, pure, tested) — the catalog cell must point at the *caller* (`Studio.jsx:186` `commit`), not the pure function. |
| E4 | Row 134 `tool_failure` hook `server/agent_runner.py:782` | `:782` is `elif isinstance(block, ToolResultBlock):` inside **`event_of`** (`:752`), whose entire signature is `event_of(message)` — **no `project_id`, no `turn_id`, nothing joinable in scope**. `event_of` is also called from `_drain_unsolicited` (`:1902`), the path that exists specifically to discard turns that must not be attributed to the current message. And `event_of` drops the tool *name* from a result — it emits only `tool_use_id` (`:790`). | HIGH | A capture at `:782` cannot carry `tool_id`, cannot be joined to a turn, and **double-counts drained turns** — reintroducing the off-by-one the code was hardened against. Correlate inside `run_turn` (`:1960-1978`) where `project_id` is in scope, holding a `tool_use_id → tool_id` map for the turn. |
| E5 | Row 3 `provision_stage_finished` hook `desktop/main.js:383`, "5-12/install" | `:383` is `const relay = (phase) => (frame) => {` and its first branch is `if (frame.type === 'log') { setupSend('setup:progress', frame.line) }` (`:384-385`). The relay fires **once per NDJSON log line** streamed from `uv`/`pip`/`npm ci`. A 2.6 GB torch install emits thousands. | HIGH | Volume is wrong by 2–3 orders of magnitude. Hook the `frame.type === 'step'` branch (`:386`) or `runProvision`'s resolution/rejection (`:392-407`). |
| E6 | Row 31 `project_state_transition` hook `server/state.py:91`, properties `from E{…}; to E; duration_ms` | `:91` is `_stage_entry(project_id, stage)`, called in a **list comprehension over every stage** (`server/state.py:125`), inside `FileStateSource` whose own docstring says "Polling impl: derive state by reading checkpoint files **on demand**" (`:86`). The frontend polls `/state` every **1500 ms** (`web/src/App.jsx:59`). And `_stage_entry` derives one stage's status from a checkpoint file with **no memory of the previous value**, so `from` and `duration_ms` are not computable there. | HIGH | ~6 stages × 40 polls/min = 240 executions/minute, producing a property set the function cannot know. Transitions need a persisted last-seen state (or an emit inside `scripts/update_stage.py`), not the read path. |
| E7 | Rows 114 `render_artifact_current` / 125 `export_became_stale` hooks `lib/project.py:445` / `:498` | `:445` is `final_render_status(...)`, whose own docstring says "**the UI polls this every 4s**" and "Reported, not raised: this is called from the assets listing the UI polls." Confirmed: `server/app.py:625` calls it inside `list_assets`, and `web/src/App.jsx:1294` runs `setInterval(tick, 4000)` on `api.listAssets`. `:498` is the `doc_hash` comparison **inside that same polled function**. | HIGH | 15 executions/minute per open project. Their "change only" mitigation needs state the function does not hold. Emit from the *consumer* that already diffs (the frontend, on a `current` flip), or from `_set` after publish. |
| E8 | Row 30 `project_content_ready` property `source E{agent\|human\|mixed}` and row 86 `schema_write_rejected` property `author E{human\|agent\|unknown}`, both hooked `server/app.py:784` | `:784` is `@app.put("/api/projects/{project_id}/edit_decisions")` — the **editor's** save route. `RULES.md:79-81`: "The agent edits the JSON directly (the schema is the shared contract); it does **not** use the editor's JS mutators." The agent writes `artifacts/edit_decisions.json` with the `Write` file tool, or via `publish_final_render(persist_doc=…)` (`server/render_jobs.py:397`). Neither passes through PUT. | HIGH | `source='agent'` and `author='agent'` are **structurally unobservable** at `:784`, and the agent's invalid docs never 422 at all — which is exactly why `_render_locked` has to defend against a missing `renderer_family` at `render_jobs.py:524`. Detect agent authorship by diffing on turn end, or validate on read in `server/editor.py`. |
| E9 | Row 75 `property_field_committed` hook `web/src/studio/propertySchema.js:60` | `:60` is `export const PROPERTY_SCHEMA = {` — a declarative object literal in a pure data module. There is no executable path. | HIGH (category) | Hook the inspector's commit path (`StudioInspector.jsx` field handlers → `Studio.jsx:600-601` `onUpdateCut`/`onUpdateOverlay`). Keep `propertySchema.js` as the *enum source* for `field_id`, which is its real and excellent contribution. |
| E10 | Rows 98 `overlay_preview_quality` / 99 `preview_audio_quality` hooks `StudioPreview.jsx:64` / `:33`; row 100 `preview_frame_cadence` hook `:228` | `:33` `syncAudioEls` and `:64` `syncOverlayVideos` are called at `:263` and `:264`, **inside the rAF `tick` loop** (`raf = requestAnimationFrame(tick)`, `:265`/`:267`). `:228` is that loop's `useEffect`. This is the single hottest path in the editor — RULES.md:128 makes it the playhead clock. | HIGH | The rows want `max_drift_ms`, which genuinely needs per-frame sampling, so accumulate into a ref and emit **once** at pause/unmount. As written, "1/play rollup" is asserted at a hook that executes 60×/s. Measuring smoothness must not cost smoothness. |

### 2.2 Errors that mislead but are recoverable (MEDIUM)

| # | Their claim | What the code says | Sev | Fix |
|---:|---|---|---|---|
| E11 | Row 130 `network_operation_failed`, classes `offline\|dns\|timeout\|tls\|reset`, hook `web/src/api.js:3` | `:3` is `async function json(resp)`, reached only via `fetch(...).then(json)` (`api.js:11-14`, `:176-180`). A fetch that **rejects** — offline, DNS, TLS, reset — never produces a `resp`, so `json` never runs. | MED | Five of the seven declared classes are unobservable there. Wrap `fetch` itself, or add a `.catch` in a shared helper. (Row 129 `api_expected_failure` at the same line is **correct and good** — see §6.) |
| E12 | Rows 37 `media_probe_finished` hook `server/app.py:879`, 38 `media_probe_failed` hook `:898` | `:879` is `if shutil.which("ffprobe") is not None:` — the probe's *entry gate*. `:898` is `if proc.returncode == 0:` — the **success** branch. The anchors are inverted relative to the event names, and there is no `else` for the failure case (the only failure handler is the `except (ValueError, KeyError, TypeError)` at `:907`). | MED | Swap them and add the missing failure branch. Row 37's *property set* is the best in either doc — keep it (§6). |
| E13 | Row 105 `render_scene_finished`, properties `outcome`, `duration_ms`, `failure_class`, hook `tools/video/video_compose.py:1492` | `:1492` is `with cache.lock(key):` followed immediately by `rec = cache.get(key)` (`:1493`) — the *start* of scene handling, inside the per-scene loop. Duration and outcome are not knowable there. | MED | Hook the post-render append in the cache-miss branch. (Their `:1589` and `:1602` assemble anchors are **exact** — §6.) |
| E14 | Rows 137 `telemetry_delivery_health`, 148 `meaningful_active_day`, 149 `lifecycle_state_changed`, 153 `cohort_retention_metric` all hooked `server/analytics.py:166` | `:166` is `def capture(event, properties)` — the generic sender. Four derived, query-time metrics are given the send function as their "hook point," and row 137 in particular means emitting telemetry-health *from inside the telemetry emitter*. | MED (category) | Mark these `derived — no emitter` with the source events named. The row count is inflated by treating warehouse metrics as instrumentation (I did the same — see 2.4). |
| E15 | Rows 71 `editor_session_ended` hook `Studio.jsx:35`; 33 `project_session_ended` hook `App.jsx:24` | `:35` is `export default function Studio({ projectId, … })` and `App.jsx:24` is `export default function App() {` — component signatures. A capture in a component body runs on **every re-render**, and cannot fire on unmount. | MED | Both need `useEffect(() => () => emit(), [])` cleanup plus a `pagehide` beacon. `recorder.js:198-200` already has the beacon pattern to copy. |
| E16 | Row 139 `crash_free_session_metric` and row 10 `app_session_ended` (`exit_kind` includes `crash`, `backend_fatal`) hook `desktop/main.js:684` | `:684` is `app.on('window-all-closed', () => { shuttingDown = true; stopProvision(); stopBackend(); app.quit(); })`. A crashed session never reaches `window-all-closed`, so `exit_kind='crash'` is unreachable from this hook; and `app.quit()` runs synchronously in the same handler with **no flush await**, so a `capture()` there is likely dropped. | MED | Their §4 counting rule ("synthetic end at the last event plus a 30-minute inactivity cap after a crash", their line 347) patches the *metric*. The *hook* still needs `before-quit` with an awaited flush, plus `previous_exit` reconstruction on next launch — which row 6 already proposes and is the right mechanism. |
| E17 | Row 65 `asset_mention_query` hook `web/src/chat/ChatPanel.jsx:58`, volume "0-5/turn" | `:58` is `const range = useMemo(() => mentionQuery(input, caret), [input, caret])` — dependencies are the input string and the caret, so it re-runs on **every keystroke and every cursor move**. | MED | Emit on menu open/select/dismiss, not on the query memo. **Same error in my doc** — see 2.4. |
| E18 | Row 40 `asset_browser_opened` hook `web/src/components/FolderBrowser.jsx:25`, "1/open/folder" | `:25` is `export function useFolderBrowse(projectId, refreshKey)`. At `web/src/App.jsx:1300` it is called as `useFolderBrowse(selected, data)` where `data` is the result of the **4-second** asset poll (`:1294`). The hook's effect re-runs every tick. | MED | Emit on `setCwd`, not on the hook body. |
| E19 | §4 "Feature adoption rate … / distinct installs that were **eligible and exposed** to that feature"; also feature discovery, feature retention, undo rate | "Eligible" and "exposed" appear in four formulas. No event in §3 emits an eligibility or exposure set: row 72 `editor_feature_discovered` fires on **first use** (their own text: "Can eligible users find each feature?"), and row 71 carries only `unique_feature_count`. | MED | The denominators are uncomputable from their own catalog. Needs a per-session `features_eligible[]` computed from the same predicates the UI uses to render (e.g. `audioMix` only for `video_overlay`, `propertySchema.js:97`; Arrange only with ≥2 overlays, `StudioTimeline.jsx:267`). My doc emits this (#82) — that is the one place my catalog is ahead on formulas. |
| E20 | §4 "Cost of failed work = sum agent turn cost for projects with no export within 7d / those failed-to-export projects" | Every other formula in their table guards with "old enough for the window." This one does not. | MED | Right-censoring: a project created yesterday is counted as failed-to-export. Add the window-eligibility guard. |
| E21 | Row 124 `export_downloaded` hook `web/src/App.jsx:1347` | `:1347` is `<a className="render-dl" href={api.fileUrl(...)} download={r.name}>` — a native download anchor with **no event handler at all**. Row 123 `:1341` is a bare `<video controls …>` element. | MED | Both need an `onClick`/`onPlay` added; the rows read as if a hook exists. `export_downloaded` is a P0 wall-adjacent row in their §8 funnel, so this one matters. |
| E22 | Rows 144/145/146 `survey_shown` / `survey_answered` / `survey_dismissed` all hooked `web/src/studio/Studio.jsx:262` | `:262` is `const render = useCallback(async () => {` — the Export handler. Three survey events are anchored to the render function. | MED | Placeholder anchors. Say "new surface" rather than cite an unrelated line — the doc's credibility rests on every other anchor being real, and these are the ones a reader will spot. |
| E23 | Row 90 `agent_panel_usage` hook `web/src/studio/Studio.jsx:670` | `:670` is `<button className="st-ghost" onClick={onClose} title="Back to project">←</button>` — the **Back** button. | MED | Wrong element. The agent panel is rendered from `chatForPanel` (`Studio.jsx:258-260`). |
| E24 | Row 35 `asset_import_finished` hook `server/app.py:577` | `:577` is `kind_dir.mkdir(parents=True, exist_ok=True)` — *before* the copy (`:578-579`) and before `target.stat()` (`:585`). | LOW-MED | `duration_ms` and `bytes` are not final there. Move to after the stat. Row 36 `:557` is likewise only the 404 branch, one of six declared failure classes. |
| E25 | Row 44 `unused_import_rate` hook `server/app.py:589` | `:589` is `@app.get("/api/projects/{project_id}/assets")` — the list route, polled every 4s (`App.jsx:1294`). | LOW-MED | Derived metric on a poll route. Mark derived. |

### 2.3 The adjudicated divergence: `lib/project.py:614`

**They are right about the semantics and wrong about the hook. I was right about the layer and
vaguer about the semantics.** Evidence, read in full:

`_publish_final_locked`'s docstring states the ordering explicitly (`lib/project.py:542-559`):

```
1. stage src into renders/.final.<uuid>.part.mp4
2. enter commit_guard  (supersede re-check held across the replace)
3. inside the guard: UNLINK the old receipt, then os.replace(part -> final.mp4)
4. persist_doc, when given -> artifacts/edit_decisions.json
5. the RECEIPT, last, as the commit marker
   "Any interruption between steps 3 and 5 leaves NO receipt, so the result
    reads STALE rather than falsely current."
```

`lib/project.py:614` is `if receipt_doc is not None:` — the head of **step 5** (`:557-559`). So their
claim that it is "the canonical receipt commit" is **correct**, and it is a sharper North Star
definition than mine: the video replace (step 3) makes bytes exist, but only the receipt makes
them *attributable to a timeline*. `final_render_status` (`:443-497`) then treats a receiptless
`final.mp4` as not current. Their §3i preamble ("a click is intent, not activation") and §13 Q2
follow correctly from this. **Adopt their definition.**

Three corrections to it:

1. **A `capture()` at `lib/project.py:614` violates a layering rule stated in the code.**
   `lib/project.py:439` reads: "Built here rather than imported from `server.editor`: **lib must
   not depend on server**." `server/analytics.py` does `from server import settings` (`:28`), so
   importing it into `lib/` inverts the dependency. Their own §10 gets this right — "After
   `publish_final_render` has written the receipt at `lib/project.py:614` **and RenderJobStore
   confirms published=true, emit `export_completed` from the server**" — but rows 91, 119, 151
   and §7 `export.final` all print `lib/project.py:614` in the *hook point* column. §3 and §10
   contradict each other. Correct hook: **`server/render_jobs.py:580`** (immediately after the
   `if not published["published"]` guard at `:576`), where `publish=True` guarantees
   `receipt_doc` was non-`None` (`:355` editor path, `:396` agent path).
2. **My own anchor was worse.** My #136 says `render_jobs.py:566`, which is `if publish:` — the
   branch entry, not the publish-success check. See 2.4.
3. **The receipt gate has a blind spot both of us missed.** `store_asset(kind='final_render')`
   exists in the agent's tool schema (`server/agent_runner.py:1014`) and its docstring says
   "`final_render` REPLACES renders/final.mp4 (**place_asset routes that one kind to
   publish_final_render**)" — with `receipt_doc=None`, because `lib/project.py:559` explicitly
   refuses provenance "it has not earned." So **an agent that ships the deliverable via
   `store_asset` produces a real final.mp4 that never fires `export_completed`.** Their
   definition is correct and this consequence is correct, but it means activation can read 0 for
   an entire agent code path. That is a product decision for the human, not a metric detail, and
   neither doc raised it.

### 2.4 Errors in my own doc

| # | My claim | What the code says | Sev | Correction |
|---:|---|---|---|---|
| M1 | My §9: "PostHog's first paid band (~$0.00005/event above the free 1M)… Post-beta, full fidelity: (214M − 1M) × 0.00005 ≈ **$10,666/mo**" and "(41.5M − 1M) × 0.00005 ≈ **$2,026/mo**" | PostHog's rate is **banded and decreasing**, not flat. Codex's §9 (their lines 622-631) reproduces the real table and their arithmetic checks out exactly: $445.90 = 13M × $0.0000343 (the 2M–15M band) and $354 = 12M × $0.0000295 (15M–50M). Applying those bands: 41.52M ≈ $50 + $445.90 + $782.34 = **$1,278**; 214.32M ≈ $50 + $445.90 + $1,032.50 + $1,090 + $1,029 = **$3,647**. | HIGH | I overstated by **1.6× and 2.9×**. I labelled them "upper bounds," which was directionally honest and numerically useless. **Their pricing math replaces mine wholesale.** (Caveat I owe symmetrically: I have no network access, so I am accepting their band rates on the strength that their own two subtotals reproduce from them exactly — invented rates rarely do that.) |
| M2 | My #136 hook: "`render_jobs.py:566` when `published["published"]`" | `:566` is `if publish:`; the success check is `:576` and the safe emit point is `:580`. | MED | Anchor is 10 lines early and names the wrong condition. This is my North Star row, and I charged codex for exactly this class of imprecision. |
| M3 | My #78 `mention_search_miss` hook: "`web/src/chat/mentions.js` `rankCandidates`" | `rankCandidates` is called from the `useMemo` at `ChatPanel.jsx:58`, dependencies `[input, caret]` — **per keystroke**. | MED | Identical to E17. Both docs get this wrong; the merged row must hook menu open/select/dismiss. |
| M4 | My #143 `error_reported` properties include `session_id`, unifying `analytics.py:179` (backend), `desktop/main.js:65` (Electron) and `app.py:1016` (renderer); my §4 crash-free rate joins them by session | Same as E1: no backend or Electron event carries a session id. `desktop/main.js:91` sends `distinct_id: settings.device_id`. | HIGH | I filed E1 against them; I have the same hole. It is the one defect present in both docs that blocks a wall number. |
| M5 | My #34 `asset_probe`: "extend `server/app.py:856`", volume "**1/asset**" | `:856` is `@app.get("/api/projects/{project_id}/source_meta")` — a **lazy** probe the editor calls per source ref for trim bounds, not an ingest-time probe. It never runs at upload, and runs repeatedly across editor sessions. | MED | Wrong hook and wrong unit. Codex correctly split ingest (row 34, `api.js:176`) from probe (row 37, `app.py:879`). **Their split replaces mine.** |
| M6 | My #5 `provision_finished` hook: "`lib/provision.py:444`/`:655` completion" | `:444` is `def provision_pack(name, progress)` and `:655` is `def provision_composition(...)` — function *entries*, not completions. | LOW | Same enclosing-def imprecision I counted 55 times against them. |
| M7 | My catalog claims "**162 rows**" of data points | #46 (`asset_name`) and #142 (`export_thumbnail`) are strikethrough *anti-rows* that belong in §12, and ~11 rows (#56, #66, #70, #113, #124, #139, #151, #152, #161, and the metric rows in 3l) are **derived query-time metrics with no emitter**, the same category error I filed as E14. | MED | Honest count: **~149 events + ~13 derived metrics**, not 162 event rows. Both docs inflated their headline number the same way; codex's 154 has the same problem (their rows 23, 44, 104, 115, 137, 139, 147, 148, 149, 153). |

### 2.5 Claims about existing code — verified

Both docs assert things about the same five artefacts. All of the following are **true** as
stated by codex:

- `TurnResult` computes `is_error`, `num_turns`, `total_cost_usd` and is assigned at
  `server/agent_runner.py:1980-1982` (their `:1979` anchor is the `elif` head). ✓
- `render_jobs._set` (`:309`) is a single choke point every status transition passes; the three
  entry points are `:58`, `:71`, `:94` and the supersede write is `:168`. ✓
- `recorder.js:113` `push` returns immediately unless `state.on` — "a source adapter, not the
  analytics destination" is exactly right. ✓
- `activity.py` persists tool *uses* only; `record_tool_use(projects_dir, project_id, tool,
  target)` (`:170`) never receives an outcome, and `run_turn` passes only name + detail
  (`agent_runner.py:1973-1978`). Their "outcome BLIND" label is correct. ✓
- `analytics._scrub` (`:145`) rewrites free-text keys to `<key>_len` (`:155-157`) and redacts
  secret-looking keys (`:151-153`); it does **not** reject or count, so their row 138
  (`data_quality_violation` at `:145`) describes their §10 extension, not today's code. Their
  §10 says so. ✓ (worth one clarifying word in the row)
- `video_compose.py:1559-1566` returns `n_scenes` / `n_cached` / `n_rendered` and `:1568`
  `hdr_handling`. ✓
- `video_compose.py:1589` is the assemble invocation (`final_res = self._render({…})`) and
  `:1602` is its `return ToolResult(...)`. Both **exact**. ✓
- `desktop/main.js:671-674` are the four real failure hooks: `uncaughtException`,
  `unhandledRejection`, `render-process-gone`, `child-process-gone`. All four **exact** — the
  best-anchored block in either document. ✓
- Their §4 undo-rate denominator (`editor_action_committed + editor_drag_completed`) does **not**
  double-count: I checked the drag path, and `onTrim` (`Studio.jsx:341-345`), `onOverlayMove`
  (`:537`), `onOverlayTrim` (`:538`) and the audio moves (`:551-556`) all route through `live`,
  with `snapshot()` only at drag begin (`:346`, `:539`, `:548`). Drags never call `commit`, so
  the two families are disjoint. I suspected a defect here and there isn't one. ✓
- Their §9 arithmetic: 100×8×135 = 108,000 ✓; 1,000×12×170 = 2,040,000 ✓; 10,000×15×180 =
  27,000,000 ✓; 1,000,000/135 = 7,407 ✓; 7,407/12 = 617 ✓; 1,000,000/280 = 3,571 ✓. Every
  figure recomputes. ✓

### 2.6 Unverified suspicions

Labelled because I could not close them with evidence.

- **Per-session volume escalates 135 → 170 → 180 across their three pricing scales with no
  stated reason**, while their own §9 sampling table introduces 50%/20%/10% session sampling
  post-beta — which should push the average *down*. The rows are labelled "unsampled," so this
  is not a contradiction, but the escalation is unexplained. Suspicion only.
- **Rows 52/53 say "about 20/turn" each; §9 models 10 tools/turn.** A 2× internal
  inconsistency on the single largest event family. I could not settle which is right: I looked
  for a real `.mc/activity.jsonl` to count tool calls per turn and **there is no `projects/`
  directory in this worktree and none under `~/Library/Application Support/opennolan-desktop`**,
  so neither their 10-20 nor my 60 has any evidence behind it. **This is the most important
  unmeasured parameter in both cost models** and should be measured before either is trusted.
- Row 39's `cache_hit` for `browser_preview_path`: the docstring promises "transcoded once and
  cached at `cache_dir/<stem>.webm`… subsequent calls return the cached path instantly"
  (`server/editor.py:150-152`), which supports the property, but I did not read the body to
  confirm a distinguishable hit/miss return.
- Row 129's `user_visible E` property at `api.js:3`: whether a caller surfaces the throw is
  decided in each `.catch`, not at `json()`. Probably needs a call-site flag. Not verified.

---

## 3. MISSING DATA POINTS

### 3.1 In theirs, not mine — credited

| Their row | Why it beats me |
|---|---|
| **39 `browser_proxy_finished`** (`server/editor.py:140`) | The best row in either document. Chromium cannot decode ProRes, and HyperFrames alpha overlays are ProRes 4444 (`editor.py:143-147`), so the editor silently transcodes to VP9/WebM to preview the agent's own output. That is a per-asset CPU cost on the critical path of the app's core promise (`RULES.md:66`, preview without re-render). I did not observe it at all. |
| **114 `render_artifact_current`** + the `reason` enum | Staleness, not existence. `final_render_status` (`lib/project.py:443-497`) already computes a bounded eight-value reason vocabulary; shipping it as an enum is free and it answers "is the deliverable trustworthy," which my catalog never asks. |
| **45 `source_resolution_failure`** (`server/editor.py:68`) | `render_jobs._resolve_sources` (`:190-201`) exists *because* project-relative refs don't resolve from the server cwd — "Cut source not found" is the app's signature render failure. I had it only as a render `error_class`; they observe it at the resolver with `reference_kind` and `consumer`. |
| **88 `dirty_work_abandoned`** | Data loss. My catalog has no row for "the user left with unsaved commits." Given autosave is suspended around agent turns (`Studio.jsx:248`), this is reachable. |
| **89 `feature_noop`** | Free signal I missed: `RULES.md:53` guarantees a no-op mutator returns the **same ref**, so `commit` can detect "clicked, changed nothing" with zero extra work. That is a direct affordance-confusion metric. |
| **78 `editor_redo`** + "redo-adjusted regret rate" | Separates exploration from regret. My raw undo rate conflates them and would have condemned working features. |
| **6 `app_launch_started`** with `previous_exit E{clean\|crash\|kill\|unknown}` | Reconstructing the prior exit on the *next* launch is the correct way to catch crashes that never got to report — the gap I filed as E16 against their own row 139. |
| **37 `media_probe_finished`** property set | `container`, `vcodec`, `acodec`, `bit_depth`, `hdr`, `rotation`, `has_audio`, `fps` — a superset of mine, and correctly separated from the ingest event. |
| **107/108 assemble_started/finished** | Exact stage anchors (`video_compose.py:1589`, `:1602`). I had only "instrument `_render_locked`" over a 100-line range. |
| **§4 counting rules** (their lines 343-351) | Eligible install / eligible session / dedupe by `event_id` / synthetic session end after a crash. Mine has no equivalent hygiene paragraph and it is the difference between a metric and a number. |
| **The `I` correlation-id type + shared envelope** | Formalizing `turn_id`, `job_id`, `project_key` as a type is the right instinct, even though E1 shows it is not yet backed by code. |

### 3.2 In NEITHER document — the valuable part

1. **A cross-process correlation id.** The single largest gap (E1 + M4). Both docs assume
   frontend and backend events join; neither specifies the plumbing. Concretely: mint
   `session_id` in the renderer, send it as a header on every `/api` call, have FastAPI stash it
   on the request, and have `RenderJobStore.start*` persist it on the job record so `_set`
   (`:309`) can attach it. The agent path needs `turn_id` minted in `run_turn` (`:1924`) and
   threaded into `start_with_inputs` via `_build_render_inputs` (`:1529`). Without this, six
   metrics across the two docs are uncomputable.

2. **The agent↔editor doc-reconciliation race, measured as a race.** Codex has row 68
   `agent_human_conflict`; I have #108/#109. Neither instruments the actual mechanism:
   `agentBusyRef`/`reconcilingRef` (`Studio.jsx:248`, RULES.md:82) gate autosave, and
   `flushAutosave` (`:242`) runs before handing a turn over. The measurable failure is
   **flush-before-turn returning false or throwing** — the agent then reads a stale doc and
   "adopts" over work the user can see on screen. Neither doc emits the flush outcome. Codex's
   row 46 has `preturn_dirty_flush E{none|success|failed}`, which is closest — that property
   deserves to be its own P0 row, because it is the one silent-corruption path in the shared-doc
   design.

3. **Proxy cache *economics*, not just hit rate.** Both count `n_cached/n_scenes`. Neither
   measures what makes the cache pay: bytes on disk, eviction (there is no GC), and **which edit
   kinds invalidate**. `RULES.md:180-185` states crop is baked into the proxy so a crop edit
   re-renders that scene while reorder/retime/transition stay cheap. The decision-bearing metric
   is *invalidation rate by edit kind* — it tells you which editor feature is secretly
   expensive. Neither doc has it.

4. **HyperFrames/Remotion comp re-render triggers.** `RULES.md:70-73`: only composition clips
   require a re-render, and only the changed comp. Neither doc emits an event when a comp clip
   forces a render, so the boundary between "cheap FFmpeg arrangement" and "expensive runtime
   re-render" — the app's central performance claim — is unmeasured. Add
   `comp_rerender_triggered{runtime, reason, n_comps_affected}`.

5. **Audio stems as a first-class shape.** Codex has per-op audio rows (117-120) and I have
   #102; neither records the *stem structure* of what ships: how many music regions after
   splits, whether narration replaced or layered over base VO, whether the mix used
   `_mix_structured_audio`. `interp.musicRegions` (`:693`) and the music `oneOf object|array`
   schema make this a recent, expensive, unvalidated surface.

6. **`decide_tool`'s branch coverage as dead-code detection.** Codex row 55 emits the
   permission *decision*; I emit denials. Neither treats the branch set at
   `agent_runner.py:385-430` as an enumerable vocabulary where a never-taken branch is
   defensibly deletable. Same for `_normalize_output_path`'s four input shapes (`:632-651`) and
   `render_jobs`' three origins.

7. **Nothing measures whether preview actually equals export.** `RULES.md:62` states
   "Preview == export" as a contract, and `RULES.md:183` records a real violation (source-px
   crop on a canvas-sized proxy → `ffmpeg exit 234`). Codex row 112 has a
   `preview_mismatch` warning code and §7 mentions "preview mismatch report," but neither doc
   proposes an actual comparison. The cheap version: at export, hash the assemble EDL's overlay
   geometry against what the source canvas rendered, and emit
   `preview_export_divergence{field, magnitude_bucket}`. The app's stated north star is
   currently unverifiable, and that is exactly what the brief asked us to find.

8. **Multi-project behavior beyond counts.** Both count projects. Neither observes asset reuse
   *across* projects, style reuse, or "did the user copy a timeline" — the signals that decide
   whether a template/library feature is worth building.

9. **Skill and pipeline_def *failure* attribution.** I emit `agent_skill_used`; codex emits
   `skills_used N`. Neither joins a skill to the outcome of the turn that read it, which is the
   only way to find a skill that reliably makes the agent worse.

---

## 4. BLOAT

### 4.1 Cut from theirs

| Their row | Quote | Why it dies |
|---|---|---|
| 100 `preview_frame_cadence` | "sampled_frames N; long_frame_count N; p95_frame_ms N; max_frame_ms N … 1/10 sessions" | Instrumenting the rAF clock to measure the rAF clock (E10). Row 95 `preview_stall` and row 93 seek percentiles already answer "is the editor smooth" from cheap, non-hot hooks. Delete until a stall metric proves insufficient. |
| 54 `agent_tool_retry` | "prior_failure E; backoff_ms N; outcome E" | The SDK owns retry; nothing in `agent_runner.py` implements backoff, so `backoff_ms` is unknowable. `attempt N` already exists on rows 52/53. Fold in, delete the row and the "Agent tool retry recovery" metric that depends on it. |
| 76 `editor_selection_rollup` `median_selection_ms` | "median_selection_ms N(ms)" | The counters earn their keep; the median dwell-per-selection does not change any decision they name ("Improve inspector/navigation" is not a decision). Keep the counts, drop the timing. |
| 82 `timeline_zoom_changed` (P2) + 83 `preview_track_visibility` (P2) + 81 `panel_layout_changed` (P2) as separate rows | three rows, all "roll up/session" | Three events for three view-state toggles. Collapse into row 71 `editor_session_ended` as `zoom_final`, `tracks_hidden_max`, `panel_layout` — which is where they already put nine other counters. |
| 146 `survey_dismissed` | "survey_id E; delay_ms N; repeated_prompt_count N" | Dismissal is the absence of row 145 within a window. A third survey event at pre-beta scale, hooked at an unrelated line (E22). |
| 115 `render_time_ratio` and 104 `render_queue_wait` | "derived/render" | Both derivable from row 109's `total_ms`/`queue_ms`/`assemble_ms` and row 102's `duration_s`. Two catalog rows for two divisions. |
| 149 `lifecycle_state_changed` | "from E{new\|activated\|retained\|dormant\|resurrected}; to E" | A five-state lifecycle machine at zero users. Rows 148 and 153 already give the retention numbers; this adds a state model to maintain before there is anything to model. Defer whole. |
| 142 `feedback_topic_classified` with `confidence B{low\|medium\|high}` | "local/manual classification" | Manual classification by one developer of a handful of reports does not need a confidence bucket emitted as telemetry. Classify in a spreadsheet. |
| 5 `runtime_doctor_snapshot` "1/session, changed only" | — | Correct and useful, but it duplicates rows 3/4's outcome data for the same install. Keep it *instead* of row 4's per-tier duplication, not alongside. |

### 4.2 Cut from mine — rows in my own doc that should die

| My row | Quote | Why it dies |
|---|---|---|
| #85 `selection_changed` | "100–400/sess → roll up" | I wrote a per-interaction event and then told the reader to roll it up. Codex row 76 is the honest form: a session rollup, priority P2. **Their row replaces mine.** |
| #110 `keyboard_shortcut_used` | "0–100/sess → roll up" | Same self-contradiction. Codex row 79 ("roll up counts/session") replaces it. |
| #121 `frame_endpoint_latency` | "0–200/sess → roll up" | I already flagged it in my own §12 as a row that would not be missed. Confirmed: delete. |
| #12 `app_version_upgrade` | "`server/settings.py` compare-and-set on boot" | Codex row 11 `update_lifecycle` covers it with five phases and the real updater hooks. Mine is a strict subset. |
| #159 `session_ordinal` | "`n`[N], `days_since_install`[B]" | This is three properties on `session_start`, not an event. Delete the row. |
| #46 `asset_name` and #142 `export_thumbnail` | strikethrough "Do not collect" rows | Anti-rows padding a catalog table. They belong in §12 (where I also list them). Delete from §3; my honest row count drops to ~160 (see M7). |
| #6 `provision_step`, #10 `disk_headroom`, #23 `capabilities_panel_opened`, #30 `project_deleted`, #40 `asset_browse`, #120 `render_preview_watched`, #135 `render_concurrency_blocked`, #141 `export_reexported` | all P2 | I named these in my own §12 as "rows that would not be missed." A row I predict will not be read should not have been written. Delete all eight; that is the anti-bloat position applied to myself. |
| My 11 derived-metric rows (#56, #66, #70, #113, #124, #139, #151, #152, #161 + 3l metrics) | e.g. "#124 `render_cache_hit_rate` (metric) — `n_cached / n_scenes`" | Same category error I filed as E14 against them: query-time metrics inside an event catalog, inflating the count. They belong in §4 with the other formulas. |

Net: my 162 becomes **~140 events + 13 formulas moved to §4**. Their 154 becomes ~140 after
§4.1. The two catalogs are the same size once both are honest, which is itself worth knowing.

---

## 5. DISAGREEMENTS OF JUDGMENT

**1. Granularity: 135 events/session vs 27.** They emit `agent_tool_started` *and*
`agent_tool_finished` (rows 52/53), ~40 of their 46 agent events per session. I emit one
`agent_tool_summary` map per turn (#58). **They win, with a correction from their own doc.**
Their §9 sampling table already says the post-beta form is "collapse start into terminal event
… Keeps exact success/latency at half the tool volume" — that terminal-only design is where
they end up anyway, so adopt it from day one. And my rollup was over-thrifty: a per-turn map
makes per-tool *latency percentiles* impossible and cannot be joined to a specific failure,
which is the whole point of the metric. Converge on **one terminal event per tool call**;
session-rollup only the interaction families (selection, shortcuts, panels, zoom, seeks).

**2. Event-count ceiling.** I treated the 1M free tier as a design constraint. They priced 27M
events/month at $850 and moved on. **They win.** At 10,000 MAU, $850/month is not a
constraint; my thrift bought nothing and cost fidelity. My rollups remain the right answer for
the per-frame families, for the reason they state better than I did: "High-cardinality raw data
makes queries untrustworthy long before it becomes expensive" (their line 636).

**3. Where the taxonomy lives.** Both propose one JSON file loaded by every layer. Theirs
additionally requires each entry to declare "owner question, decision, sampling class, and
forbidden sensitive alternatives" (their line 679). **Theirs wins** — it makes my rule ("every
event must name a decision") machine-checkable instead of aspirational.

**4. The one contract test.** Mine asserts name coverage in both directions; assertion 2
(every taxonomy entry has a live call site) is the one that would have caught today's actual
bug — `export_completed` living only in `tests/contracts/test_analytics.py:99`. Theirs asserts a
golden fresh-install→receipt journey and that "`export_completed` cannot exist without a valid
current receipt." **Adopt both.** They are orthogonal: mine catches rot, theirs catches
semantic drift on the North Star.

**5. What the wall numbers are.** Theirs: activation, TTV, export success rate, agent cost per
export, crash-free. Mine: activation, agent useful-turn + cost, render success + cache hit,
crash-free, unmet-capability count. Their export success rate is broken as defined (E2) but is
the right number once fixed. **My unmet-capability count survives** — the human's stated ask
was "know the right features to build," and it is the only wall number that answers it. Render
success + cache hit demote to a screen. Converge on their four plus mine.

**6. Retention.** I argued D1/D7/D30 is a wrong-unit metric and demoted it with a caveat. They
keep all three *with thresholds* and separately name project-based retention "the canonical
retention measure." **They are closer to right than I was**: a founder publishing weekly makes
D7 genuinely meaningful, and I over-corrected. But their D1 threshold ("good >=20%") for a tool
used 1–4×/month is a number that will read red forever and train the human to ignore the
dashboard. Converge: project-based retention canonical (we agree), D7 secondary with a
threshold, D1/D30 diagnostic with **no threshold**.

**7. The deletion test.** Theirs requires 100 eligible external installs or 300 sessions across
8 weeks and 2 releases, plus all six gates. Mine requires 30 installs / 60 days. **At zero
users theirs is unreachable for a year and mine is underpowered** — this is a real tension
neither of us resolved. Their gate 4 (no agent/renderer/accessibility/migration/schema
dependency) and their weekly remove-report table (evidence-shape → default action, their lines
474-483) are both better than my flat clause list, and their "with fewer than 20 external
eligible users, telemetry cannot justify deletion; hide experimental UI" is the honest escape
hatch. **Adopt their structure with my lower N**, and make "hide, don't delete" the default
below 30 installs.

**8. Agent-reported vs code-derived signals.** No disagreement — we independently hooked the
same two: `_request_api_key` (`agent_runner.py:1370`) and `_request_capability` (`:1438`) as
the strongest build signal, and `bash_runs_heavy_media_op` (`:128`) as the route-around
detector. That convergence is probably the most reliable finding across both documents.

**9. Where they did not convince me.** Their §5 `demand_score = 5×blocked_export_users +
3×workaround_users + 2×explicit_requesters + 1×search_miss_users` is false precision at zero
users: with n=3 the weights determine the ranking entirely and nothing in the data justifies
5-vs-3. Keep their *ranking of channels* (which is well argued and matches mine), drop the
weighted score until there is a denominator.

---

## 6. WHAT TO ADOPT

From codex, into the merged catalog, replacing mine:

1. **`export_completed` defined as the receipt commit** (§2.3) — their semantics, my layer:
   emit from `server/render_jobs.py:580`, define success as "a receipt was written at
   `lib/project.py:614`."
2. **Row 39 `browser_proxy_finished`** (`server/editor.py:140`) — verbatim. Best row in either doc.
3. **Row 114 `render_artifact_current`** and the `reason` enum from `final_render_status`, plus
   row 125 `export_became_stale`. Re-hook per E7.
4. **Row 45 `source_resolution_failure`** (`server/editor.py:68`).
5. **Row 37's probe property set**, and their ingest/probe split (rows 34 + 37) replacing my
   conflated #34 (M5).
6. **Rows 107/108** assemble stage anchors (`video_compose.py:1589`, `:1602`) — exact.
7. **Rows 88 `dirty_work_abandoned`, 89 `feature_noop`, 78 `editor_redo`**, and the
   **redo-adjusted regret rate** replacing my raw undo rate.
8. **Row 6's `previous_exit`** reconstruction as the crash-detection mechanism (fixes their own E16).
9. **Row 46's `preturn_dirty_flush`** — promoted to its own P0 row (§3.2 item 2).
10. **Row 129 `api_expected_failure` at `web/src/api.js:3`** — one line, catches exactly the 4xx
    the user actually sees. Keep my backend middleware too; they answer different questions.
11. **§4's counting-rules paragraph** (eligible install/session, `event_id` dedupe, synthetic
    session end) — wholesale. Mine has no equivalent.
12. **Their §9 tiered PostHog arithmetic** — replaces my flat-rate math entirely (M1).
13. **Their §9 sampling table's structure** (data class → beta → post-beta → why), especially
    "collapse start into terminal event" and "deterministic hash of install_id + session_id so a
    whole session is kept or dropped."
14. **The `E/B/N/I` property notation and the shared-envelope section** — better formalized than
    my `[E]/[B]/[N]/[F]`, because `I` names the join problem even though E1 shows it is unsolved.
15. **§12's table format** (do-not-collect / why it looks useful / why not / lower-fidelity
    answer) — replaces my prose §12.
16. **§10's `classifyDocChange(prev, next, actionHint)`** as a *new pure function beside*
    `summarizeDocChange`, tested in `model.test.js` — the correct RULES.md-compliant fix, and
    better than my "add an `op` label at 40 call sites" alone (their `actionHint` is how the
    label gets in without logic in JSX).
17. **§11's P0 success condition**: "one synthetic and one manual packaged journey whose counts
    reconcile exactly … Inject one failure per layer and verify its bounded class." Sharper and
    more testable than my per-item success conditions.
18. **§13 Q7** (app watermark is a server-known false; refuse to claim third-party/generated
    source media is watermark-free) — a distinction I missed entirely, and it is the honest
    answer to my own Q1.

From mine that should survive over theirs: the unmet-capability wall number; `features_eligible[]`
on the session summary (E19); the `swallowed_error` counter across the five silently-swallowing
reporters (`analytics.py:176`, `activity.py:194`, `recorder.js:132`, `desktop/main.js:108`,
`feedback.py:149`); the HTTPException middleware for the 4xx the UI never surfaces; the
"branch-taken as enum → never-taken branch is deletable" technique; and the explicit "these
34 rows are the ones you will actually read" honesty split.

---

## 7. Convergence proposal — my opening position for phase 3

```
WALL (5, non-internal only)
  1 Activation      : installs with receipted export_completed <= first_run + 7d
  2 Time-to-value   : P50/P90 first_run -> first export_completed
  3 Agent value+cost: useful-turn rate (denominator = DELIVERED turns) and
                      median USD per successful export
  4 Crash-free      : sessions with no fatal, all 4 layers, joined by session_id
  5 Unmet capability: distinct capability_missing / api_key_missing / tool_not_found
                      / route-around per week   <- the "what to build" number

GRANULARITY   one TERMINAL event per tool call, render transition, commit, and
              drag; session rollups ONLY for selection / shortcut / panel / zoom /
              seek / frame families. Never per-frame, per-keystroke, per-log-line,
              or inside the 1.5s and 4s polls.
CEILING       ~60 events/session. Beta 100 MAU x 8 sess = 48k/mo (free).
              10k MAU x 15 sess = 9M/mo ~= $290 at real banded rates. Not a
              constraint; query trustworthiness is.
BLOCKER 0     cross-process correlation ids. session_id minted in the renderer and
              sent as a header; turn_id in run_turn:1924; job_id persisted on the
              RenderJobStore record so _set:309 can attach both. Six metrics in the
              two docs are uncomputable until this lands. Do it FIRST.

P0 (3 days, in order)
  1 event_schema.json (their richer form: type + question + decision + sampling
    class) + POST /api/telemetry/events + productAnalytics.js batcher
  2 correlation ids (BLOCKER 0)
  3 export_completed at render_jobs.py:580, defined by the receipt at project.py:614
    + full render lifecycle at _set:309, all three origins
  4 agent turn terminal at run_turn's finally (:1992) + per-tool terminal correlated
    inside run_turn (NOT event_of:782)
  5 session start/end with previous_exit reconstruction; before-quit with awaited flush
  6 contract test = my two-way name coverage + their golden receipt journey
CATALOG       ~140 events + ~15 derived formulas, stated separately. Both of our
              headline counts were inflated by mixing them.
```

Three things I will concede immediately in phase 3 so we do not spend time on them: their
receipt definition of export, their pricing math over mine, and their terminal-only tool-event
granularity over my per-turn map. The two I will hold: the unmet-capability wall number, and
that no metric ships until its join key exists in code.
