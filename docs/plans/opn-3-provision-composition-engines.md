# OPN-3 — Provision the composition engines (Node + Remotion + HyperFrames) deterministically

**Status:** Eng-reviewed (this doc). Ready to implement.
**Linear:** OPN-3 (parent: OPN-2 "App setup when the user first opens the app")
**Target branch:** `feat/desktop-app-mvp` (NOT `main`/current worktree — all provisioning code lives there)
**Extends:** `docs/plans/publish-mac-app.md` Lane E (bootstrapper/doctor/packs)

---

## Problem

The first-run provisioner (`lib/provision.py`) makes the **Python engine** (bundled interpreter + venv + core deps) and **ffmpeg** available automatically. But two of the three composition engines OPN-3 names — **Remotion** and **HyperFrames** — plus their shared prerequisite **Node.js**, are not provisioned at all. On a downloaded `.app` they come back unavailable; the only path to "available" is the AI agent noticing and installing mid-task, which is exactly what OPN-3 says to eliminate:

> "Download all the dependencies automatically. algorithmically. don't depend on the agent... remotion and ffmpeg-hyperframes... should always be available."

Detection already exists and is correct (`video_compose._remotion_available()`, `hyperframes_compose._runtime_check()`); OPN-3 makes the installer **act** on it.

## Decisions locked (eng review)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | How to get Node + JS engines onto the Mac | **Bundle signed Node binary; install packages at runtime** | Consistent with the Python principle already locked (bundle interpreter, populate packages into writable App Support). |
| 2 | Install timing + scope | **Eager, both engines** — Node + both `node_modules` + both browsers install in the first-run setup window before the editor opens | Most literal "always available"; satisfies AGENT_GUIDE "Present Both Composition Runtimes" HARD RULE out of the box. |
| 3 | ffmpeg source | **Pin ffmpeg + ffprobe to version + sha256 in this work** | All three engines become deterministic + verified; closes the parent plan's "pin these before release" note. |

**Refinement (not re-litigating #2):** eager is about *timing/availability*, not a hard gate. Composition provisioning is **best-effort-eager** — attempt in the setup window, but a failure WARNS and lets the editor open in the ffmpeg-only degraded state (same treatment `provision_core` already gives ffmpeg). It must never brick an offline/flaky-network first-run. Retry from Settings + app-driven re-prompt on first render.

## Architecture

```
FIRST-RUN SETUP WINDOW (packaged, desktop/main.js ensureProvisioned)
  │
  ├─ provision_core()            [existing]  venv + core wheels + ffmpeg(pinned)
  │
  └─ provision_composition()     [NEW, best-effort-eager]
        ├─ Node: bundled signed binary already in Resources/node   (no download)
        ├─ npm ci  remotion-composer  → App Support/runtime/composition/remotion/node_modules
        ├─ npm ci  hyperframes(pinned)→ App Support/runtime/composition/hyperframes/node_modules
        ├─ ensure Remotion browser   (cache → App Support, env-routed)
        ├─ ensure HyperFrames browser(cache → App Support, env-routed)
        └─ manifest: {node_version, composition_installed:true}  (atomic)

BACKEND CHILD (startBackend): PATH prepends
  runtime/bin (ffmpeg) + Resources/node + composition/*/node_modules/.bin
  + browser-cache env vars → so shutil.which/npx resolve the bundled, local copies
```

### Component changes

- **`scripts/fetch-node.mjs`** (NEW) — mirror `fetch-python.mjs`: download a pinned arm64 Node, verify sha256, prune, stage to `desktop/resources/node`. Add to the `fetch-runtime` npm script.
- **`desktop/package.json`** — `extraResources`: add `{ resources/node → node }` and `{ ../remotion-composer → backend/remotion-composer }` (composer ships, minus `node_modules` via a `!node_modules` filter). Sign the Node binary with the SAME hardened-runtime entitlements as Python (`disable-library-validation`, `allow-unsigned-executable-memory`, `allow-jit`) — Node loads native `.node` addons + Remotion's Rust compositor.
- **New pinned HyperFrames manifest** — `composition/hyperframes/{package.json,package-lock.json}` in-repo pinning `hyperframes` to a fixed version, so it installs via `npm ci` (local, deterministic) and renders via the local binary instead of `npx --yes hyperframes` (no live network at render time).
- **`lib/provision.py`** — add:
  - `node_bin()`, `node_ok()` (bin present + major ≥ 22), `_node_id()` (version string for staleness)
  - `remotion_ok()`, `hyperframes_ok()`, `composition_ok()`
  - `provision_composition(progress)` — atomic (temp dir → `os.replace`), idempotent, streams progress
  - pinned `FFMPEG_URLS` + `FFMPEG_SHA256`; `_download_binary` verifies sha, re-downloads on mismatch
  - `doctor()` gains `node_ok`, `remotion_ok`, `hyperframes_ok`, `composition_ok`
  - manifest gains `node_version` + `composition_installed`; `composition_ok()` False on Node-version drift (mirrors `base_python` drift → rebuild)
  - `forced()` hides composition too
- **`scripts/provision.py`** — add `--composition` flag (NDJSON CLI, same shape as `--core`).
- **`desktop/main.js`** — `ensureProvisioned()` runs `--composition` after `--core` (best-effort: on error, `console.error` + continue, don't `throw`); `startBackend` PATH/env wiring for Node + browser caches.
- **`server/app.py`** — `POST /api/provision/composition` (retry from Settings), same streaming pattern as `/api/provision/{pack}`. `doctor()` already returns `provision.doctor()`, so new fields flow for free.
- **`tools/video/hyperframes_compose._runtime_check()`** — prefer the locally-provisioned package (check `node_modules`) and SKIP the live `npm view hyperframes version` when provisioned. (Perf + determinism; see Performance.)

## Test coverage plan

Extend `tests/contracts/test_provision.py` (heavy installs mocked, same as existing pack tests):

- `node_ok`/`composition_ok` False on fresh home; True only when bins + node_modules + matching manifest present
- Node-version drift → `composition_ok()` False (rebuild), paralleling the existing `base_python` drift test
- `OPENNOLAN_FORCE_PROVISION=1` hides composition (extend `test_force_provision_flag_reports_everything_missing`)
- `doctor()` returns `node_ok`/`remotion_ok`/`hyperframes_ok`/`composition_ok`
- ffmpeg sha mismatch → re-download path (mock the downloader; assert verify-then-retry)
- `provision_composition` atomicity: simulated mid-install crash leaves NO half-install (temp orphaned, real dir untouched) — mirror `test_manifest_atomic_roundtrip`
- `POST /api/provision/composition` streams `log…` then `done`; unknown → 404 (mirror pack endpoint tests)
- **[CRITICAL, regression]** best-effort-eager: `provision_composition` failure does NOT mark core unusable and does NOT raise out of the core path — assert the editor-open invariant survives a composition failure

## Failure modes

| Codepath | Failure | Rescued? | User sees |
|---|---|---|---|
| `npm ci` (either engine) | registry down / offline | Y — atomic temp→replace, retryable, `deps_install_failed` event | "Couldn't set up video engines. Retry." editor still opens (ffmpeg works) |
| browser ensure | CDN 403 / timeout | Y — retryable; engine reports unavailable until done | progress + retry; agent offers ffmpeg meanwhile |
| Node binary quarantined | Gatekeeper blocks spawn | Y — signed at build + dequarantine fallback | (should not occur; bundled+signed) |
| ffmpeg sha mismatch | corrupt/changed upstream | Y — re-download once, then error | "Verifying media tools…" retry |
| **offline first-run + eager composition** | all composition installs fail | **Y (best-effort)** | editor opens degraded; Settings shows "Video engines not installed — Retry" |

No critical gaps: every composition failure is retryable, atomic, non-fatal, and visible (never silent).

## NOT in scope

- **mermaid-cli (`mmdc`), manim** — ride the Node/Python prerequisites but aren't core engines; defer (TODO).
- **Windows/x64 Node** — arch is arm64-only per parent plan.
- **Wiring `deps_install_*` PostHog events for the composition tier** — depends on the analytics funnel work (publish-mac-app Lane B TODO); add when that lands.
- **Changing detection logic** in `video_compose` beyond the HyperFrames local-vs-npm-view optimization.

## What already exists (reuse)

- `lib/provision.py` atomic/manifest/doctor/lazy-pack machinery — extended, not rebuilt.
- `scripts/fetch-python.mjs` / `fetch-uv.mjs` — templates for `fetch-node.mjs`.
- `remotion-composer/package-lock.json` — committed, so `npm ci` is deterministic today.
- `make setup`'s `cd remotion-composer && npm install` + `npx hyperframes` — the recipe, lifted into the installer.
- `/api/provision/{pack}` streaming + setup window + `tests/contracts/test_provision.py` — endpoint + test patterns to mirror.
- Detection (`_remotion_available`, `_hyperframes_available`, `_runtime_check`) — unchanged except the HyperFrames local-package optimization.

## Worktree parallelization

| Lane | Workstream | Modules | Depends on |
|---|---|---|---|
| A | Node bundling + pinned fetch + entitlements | `scripts/fetch-node.mjs`, `desktop/package.json`, `build/` | — |
| B | `provision_composition` + doctor + ffmpeg pin + tests | `lib/provision.py`, `scripts/provision.py`, `tests/` | — (pure Python, mockable) |
| C | main.js wiring + `/api/provision/composition` | `desktop/main.js`, `server/app.py` | A (bundle layout), B (function to call) |
| D | HyperFrames pinned manifest + `_runtime_check` opt | `composition/hyperframes/`, `tools/video/hyperframes_compose.py` | — |

Execution: **A + B + D in parallel** (disjoint modules). Then **C** (needs A's layout + B's functions). Conflict flag: none — the four lanes touch disjoint files.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | (parent publish-mac-app.md CLEARED) |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 3 arch decisions resolved, 1 perf finding; 9 unit test paths + 1 critical regression test enumerated; 0 critical failure-mode gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |

- **UNRESOLVED:** 0
- **VERDICT:** ENG CLEARED — ready to implement on `feat/desktop-app-mvp`. Outside voice recommended (touches notarization/packaging).
