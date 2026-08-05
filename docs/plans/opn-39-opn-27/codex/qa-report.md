# OPN-39 + OPN-27 manual QA report

**Status: QA COMPLETE**  
**Verdict: PASS**  
**Environment:** `python3 scripts/dev run` (backend `:20905`, frontend `:20906`),
headed Chromium driven through Playwright. Desktop accessibility control was unavailable
because this machine has not granted Orca Computer Use Accessibility permission.

## What I actually did

I created a new project named `QA OPN39-27 1785909642586` through the project
dialog, uploaded a generated 1280x720 landscape MP4 through the visible drop-zone file
control, opened the editor, switched its source to that upload, changed canvas presets,
reloaded the browser between each preset, and exported through the visible `Export`
button. I used existing `opn39 legacy` as the old-project case.

For mentions, I added test-only media to that QA project: project assets, one
`hf/renders/agent-cut.mp4`, and the newly rendered `renders/final.mp4`. I typed in the
actual composer, used keys (including deliberate Escape / zero-result / email cases), and
captured browser request payloads. Test media and scripts are in the ignored
`.local/ui-qa/` directory.

## OPN-39 — canvas

| Check | Result | Evidence |
| --- | --- | --- |
| New project starts 9:16 | PASS | Editor picker read `9:16 · 1080×1920`; safe frame measured 374x665 (portrait). |
| Saved document | PASS | The document wrote `metadata.compose_target = { width: 1080, height: 1920, fps: 30 }`; the UI's save PUT returned 200. |
| Presets visually update and persist | PASS | Each was changed in the visible picker, then browser-reloaded and editor-reopened: 1:1 665x665, 4:5 532x665, 16:9 768x432, 9:16 374x665. The picker retained the matching label each time. |
| Landscape-source export obeys canvas | PASS | Export clicked live with 1280x720 `landscape-source.mp4` selected. `ffprobe` on `renders/final.mp4`: H.264, **1080x1920**, duration 1.033333. |
| Legacy project remains landscape | PASS | `opn39 legacy` showed `16:9 · 1920×1080`, safe frame 768x432. Its `edit_decisions.json` had no `compose_target`; SHA-256 was `51fd079f44404e69ac2566ab54530bbdf3531ff682e60fe7378abdab8a9d39de` both before and after opening. |

Screenshots: `.local/ui-qa/new-project-editor-9x16.png`,
`.local/ui-qa/preset-returned-9x16.png`, `.local/ui-qa/render-complete-9x16.png`, and
`.local/ui-qa/legacy-editor-16x9.png`.

## OPN-27 — mention composer

### Intercepted chat sends (no billable agent turn)

Successful `/chat` responses were intercepted in Chromium with a minimal successful SSE
response. This exercised the real composer, menu, request serialization, and normal UI
completion without invoking the runner.

| Check | Result |
| --- | --- |
| Bare `@` menu lists all three buckets | PASS — headings were `PROJECT ASSETS`, `AGENT CLIPS`, and `RENDERS`, showing asset, `hf/renders`, and final-render candidates. |
| Ranking for `@video` | PASS — `product-video.mp4` (basename match) was first; `b-roll.mp4` (folder-path-only match) followed it. |
| Arrow wrap | PASS — active option 0 -> ArrowUp option 2 -> ArrowDown option 0. |
| Escape | PASS — menu closed, draft `@video` remained, and the textarea retained focus. |
| Tab / open-menu Enter | PASS — each inserted `@assets/video/product-video.mp4 `, closed the menu, and did not send. A second closed-menu Enter sent. |
| Closed-menu Enter | PASS — ordinary text sent normally. This is the key regression guard. |
| Email | PASS — `someone@example.com` did not open a menu; Enter sent normally. |
| No-result query | PASS — `@nothing-here` closed the menu and Enter sent normally. |
| Hand-edit after selection | PASS — after replacing selected-token text with prose, the serialized `mentions` array was empty. |
| Typed unknown `@123.png` | PASS — it stayed plain text, produced no sidecar mention, and sent without client error or hang. |
| File vanishes after selection | PASS, intercepted — I selected `@hf/renders/agent-cut.mp4`, renamed that file out of the project before sending, and the request still carried the original structured reference. The file was restored after the check. |
| Failed send restores draft | PASS, intercepted 503 — the exact typed draft reappeared in the textarea. |

The intercepted request record is at `.local/ui-qa/mention-results.json`. Screenshot:
`.local/ui-qa/mention-menu-all-groups.png`.

### Live, non-billable server checks

- PASS: a real POST with malformed sidecar path `renders/not-a-video.png` returned
  **422** with the expected per-root extension diagnostic. It was rejected before the
  runner could start.
- The valid-but-vanished (`NOT FOUND`) server-to-runner path was deliberately not executed
  live: by contract it proceeds into a paid agent turn. Its composer serialization and
  draft-preservation path were tested with browser interception above.

### Safety note

One early, mistaken un-intercepted no-mention curl was accepted by `/chat` and entered
runner bootstrap. I immediately called that project's `/agent/stop` endpoint; I saw no
assistant response or tool result, and no test assertion relies on it. All subsequent
successful chat checks were intercepted.

## Aesthetic verdict

PASS. The mention popover belongs in this product: warm cream surfaces, terracotta active
row and Send button, and the quiet uppercase group labels match the existing dashboard.
The active row has enough contrast to follow keyboard navigation; filename/path hierarchy is
readable; at a 1440px desktop viewport the popover is anchored to the composer and neither
overlaps nor clips the composer. Spacing is compact but consistent. I did not judge emoji
rendering from headless captures, per the QA brief.

## Defects

None found in the exercised OPN-39/OPN-27 contracts.

## Shutdown

`python3 scripts/dev stop` was run after QA. The app is stopped.
