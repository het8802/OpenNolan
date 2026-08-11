# Content Calendar MVP

**Status: BUILT — rev 3**

## 1. What is missing

1. There is no schedule contract or persistence layer. Project creation already
   gives every project an `artifacts/` directory (`lib/project.py:135`), and the
   repository already has atomic JSON writes (`lib/atomic_io.py:27`), but no
   schedule reader or writer uses them.
2. Mission Control has only the project dashboard and selected-project views.
   The top-level branch returns `Dashboard` whenever no project is selected
   (`web/src/App.jsx:124`), and `Dashboard` only renders project tiles
   (`web/src/App.jsx:219`). There is no calendar route or calendar data client.
3. The project bar exposes only Edit (`web/src/App.jsx:586`). The existing asset
   poll already discovers `renders/final.mp4` (`web/src/App.jsx:1337`), but that
   eligibility is not available to a Schedule action.
4. The in-app agent MCP server registers six tools and no scheduling tool
   (`server/agent_runner.py:1513`). There is therefore no agent path that can
   create the same record as the UI.
5. A posting-time baseline exists for a specialized daily carousel workflow
   (`skills/social-media/daily-tech-carousel/SKILL.md:109`), but there is no
   general content-calendar skill or writable per-niche cache. Bundled agent
   skills are loaded from the local app plugin (`server/agent_runner.py:838`),
   while project data is the writable packaged-app location
   (`server/agent_runner.py:754`).
6. The analytics taxonomy has project and export events but no scheduling or
   calendar events (`schemas/analytics/project.json:3`). New UI and failure
   paths would be invisible unless declared and emitted.

## 2. Fix intent

Build one small scheduling service that both REST and the agent tool call, then
render its aggregate result in a hash-routed month calendar and reuse the shared
asset lightbox for playback.

1. Add `server/content_calendar.py` as the sole owner of channel vocabulary,
   schedule validation, per-project JSON reads/writes, calendar aggregation,
   collision-aware agent slot selection, and the writable timing-skill cache.
2. Add `GET /api/content-calendar` and
   `POST /api/projects/{project_id}/schedule` in `server/app.py`. The GET response
   carries `channels` so React never duplicates the platform list.
3. Add `schedule_content` to the existing `mc` MCP server. It accepts channels,
   an optional requested time, niche, and an optional researched local time;
   the server shifts agent-selected slots away from obvious two-hour collisions
   and writes through the same service as REST.
4. Add a bundled `content-calendar-scheduling` agent skill. It reuses the
   existing posting-window hypothesis, makes web research optional, and tells
   the agent how to pass learned niche timing back to the tool. The tool persists
   learned values in a writable runtime `SKILL.md` under
   `<projects_dir>/.content-calendar/` so future calls skip research even in the
   packaged app.
5. Add small React components for the Schedule dialog and month calendar. Use
   `/#/calendar` for a real route without adding a router dependency. Calendar
   blocks open the existing `AssetModal` (`web/src/components/AssetModal.jsx:22`).
6. Add a `content_calendar` analytics family for schedule success/failure,
   calendar opens, and scheduled-video opens.

## 3. Deliberately not building

| Not building | Reason | What would change my mind |
|---|---|---|
| Social account auth or posting | Explicitly out of scope; entries are planning records only. | A separate publishing/connectors feature is approved. |
| A database or global calendar database | Per-project JSON is the repository's persistence model and is enough to aggregate an MVP. | Schedule volume or cross-process write contention becomes measurable. |
| Edit/delete/reschedule/status transitions | The request only requires creating and viewing scheduled entries. | Users need to correct mistakes in normal use. |
| Automatic manual collision resolution | A user-picked datetime is authoritative; silently moving it is surprising. | Product defines a conflict UI or explicit auto-move option. |
| Timezone settings UI | `datetime-local` can convert through the browser's local zone; the agent uses the host local zone. | Teams schedule for accounts in other timezones. |
| Immutable copies of scheduled videos | MVP can reference the canonical final render just like the existing UI. | Scheduled versions must survive later re-renders byte-for-byte. |
| Recurring schedules, drag/drop calendar editing, week/day views | Month view plus modal creation closes the requested slice. | Calendar usage shows month density is unusable. |
| Broad test coverage | The request explicitly asks for MVP coverage. | The feature becomes a publishing or billing boundary. |

## 4. Steps and verification

1. Implement the scheduling service and focused tests in
   `tests/contracts/test_content_calendar.py`.
   - Verify schema fields, UTC normalization, atomic persistence, aggregation,
     render eligibility, channel validation, and agent collision shifting.
2. Add REST endpoints and test them in the same contract file.
   - Verify GET returns the single channel vocabulary and POST writes a
     `created_by: user` entry referencing `renders/final.mp4`.
3. Add the MCP tool and bundled skill.
   - Extend `tests/contracts/test_agent_runner.py` to verify the tool method
     writes `created_by: agent`, uses an open slot, and updates/reuses the
     per-niche runtime skill cache.
   - Extend the agent-skill packaging contract to verify the new skill ships.
4. Add the client methods, `ScheduleModal`, `ContentCalendar`, hash route, and
   project-bar button.
   - Add pure month-grid/default-time tests in
     `web/src/contentCalendar/model.test.js` and component interaction tests in
     `web/src/contentCalendar/ContentCalendar.test.jsx`.
   - Verify Schedule is disabled without a final render, immediately precedes
     Edit, saves one-or-more channels, and a calendar block opens `AssetModal`.
5. Declare and emit analytics.
   - Run analytics conformance/taxonomy tests and verify no project name, niche,
     or exact schedule time is sent.
6. Run `scripts/dev test fast`, then `scripts/dev test full`,
   `scripts/dev smoke`, and stop managed processes with `scripts/dev stop`.

Existing tests most likely to expose regressions are
`tests/contracts/test_server_read_api.py`,
`tests/contracts/test_server_write_api.py`,
`tests/contracts/test_agent_runner.py`,
`tests/contracts/test_agent_skills_packaging.py`, and the full Vitest suite.

## 5. Risk register

| Risk | Mitigation | Proof step |
|---|---|---|
| UI and agent create incompatible records | Both call one service; callers only choose `created_by` and collision policy. | Steps 1–3 |
| Two channels require duplicate entries | One entry stores a validated `channels[]` set. | Steps 1–2 |
| Calendar scans non-project cache folders | Aggregate via `list_projects`, whose manifest/legacy filter excludes scratch dirs. | Step 1 |
| A partial write breaks the poller | Persist with `atomic_write_json`. | Step 1 |
| Manual dialog enables before a render exists | Reuse the existing asset poll's `final.mp4` result and keep the button disabled otherwise. | Step 4 |
| Agent chooses a visibly crowded time | Compare all entries and advance candidate slots until none is within two hours. | Steps 1 and 3 |
| Packaged agent cannot update bundled skill | Tool owns a writable runtime skill-cache file under `projects_dir`; bundled skill is instruction-only. | Step 3 |
| Hash route breaks direct loading | Use `/#/calendar`, which loads the existing root document before client routing. | Step 4 + smoke |
| New events are silently dropped | Add the taxonomy before emitting and run conformance tests. | Step 5 |
| Calendar opens stale browser-cached bytes | GET aggregation attaches the render's current mtime as the playback cache token. | Steps 1 and 4 |

## 6. Review rounds

| Round | Finding / decision | Resolution |
|---|---|---|
| Coordinator gate | This is one of two throwaway competing MVP spikes; requiring a nested opposite-provider review would block the build-off. | Coordinator explicitly waived the plan/architecture review gate for this spike and directed implementation from this plan. Human comparison of the two worktrees remains the selection review. |

## 7. Built result

- Shared per-project JSON storage and collision-aware slot selection live in
  `server/content_calendar.py:87` and `server/content_calendar.py:198`.
- The manual REST surface lives in `server/app.py:884`, and the in-app agent
  tool lives in `server/agent_runner.py:1394` with its shared implementation at
  `server/agent_runner.py:2317`.
- The project action is wired in `web/src/App.jsx:628`; the month view and
  native scheduling dialog live in `web/src/contentCalendar/ContentCalendar.jsx:13`
  and `web/src/contentCalendar/ScheduleModal.jsx:6`.
- The bundled agent guidance is
  `.agents/app/skills/content-calendar-scheduling/SKILL.md:1`; learned per-niche
  times are written to a runtime copy beside project data.
- Focused feature, analytics, and UI tests pass. The non-LAN backend suite
  passes 2,168 tests and all 413 Vitest tests pass. The required full command is
  blocked only by 19 pre-existing real-LAN socket tests resetting connections
  on this machine; a second `scripts/dev smoke` run passed after the first hit
  an unrelated uvicorn reload race.
