# OpenNolan — Publish as a Proper Mac App (Distribution Plan)

**Status:** Draft for review (CEO review in progress)
**Date:** 2026-06-30
**Owner:** Het
**Branch:** feat/desktop-app-mvp
**Review mode:** SELECTIVE EXPANSION
**Dependency strategy (user decision):** Provision required software locally on first run (bootstrapper), NOT slim/BYOK-cloud. Python is core/eager; WhisperX + other ML are on-demand capability packs.

---

## 0. Why this plan exists

Today the "desktop app" is a dev harness. `desktop/main.js` spawns the FastAPI backend from `REPO_ROOT/.venv/bin/python` with `cwd: REPO_ROOT`, projects at `REPO_ROOT/projects`, UI from `REPO_ROOT/web/dist`, and assumes `ffmpeg`/`node`/`claude` are on PATH. It runs only inside the git checkout. A downloaded `.app` has none of that.

Publishing means closing four gaps (the user's asks) plus two the user did not list but that block launch:

1. **Package the app for Mac** (user ask #1)
2. **Provision required software on first open** — dependency diagnosis + bootstrapper (user ask #2)
3. **User feedback / bug requests** (user ask #3)
4. **Analytics** — is the app used, and is it useful (user ask #4)
5. **Code signing + notarization** (NOT on user's list — blocks the first double-click)
6. **Auto-update** (NOT on user's list — you cannot patch a broken first release without it)

---

## 1. Dependency diagnosis (authoritative)

Scanned across `server/`, `tools/`, `lib/`. Heavy imports are already **lazy** (imported inside functions), so nothing loads until its tool is called. This is what makes tiered provisioning possible.

### External binaries
| Binary | Uses | Needed by | Provisioning |
|---|---|---|---|
| ffmpeg / ffprobe | 90 | scrub, preview, every export | static universal build, bundled or downloaded |
| node / npm / npx | 6 | HyperFrames/Remotion/mermaid comp renders | Node runtime (agent composition) |
| mmdc (mermaid) | 1 | diagram renders | npm global |
| manim | 2 | math-animation renders | pip |
| piper | 1 | local TTS | binary + voice model |
| whisper / whisperx | 6 | video understanding + captions | pip + torch + model |
| claude | 1 | agent auth fallback (BYOK panel covers it) | optional |
| pactl / arecord / cap | 5 | Linux audio + screen capture | **skip on Mac** |

### Heavy Python packages (lazy)
| Package | Uses | Capability | Weight |
|---|---|---|---|
| torch | 16 | ML backbone | ~2 GB |
| cv2 (opencv) | 18 | frame proc, face crop, reframe | ~90 MB |
| whisperx / faster_whisper | 4 | transcription, captions, understanding | + torch + models |
| mediapipe | 4 | face/pose detect (auto-reframe) | ~200 MB |
| transformers | 4 | HF models | + torch |
| rembg | 3 | background removal | onnxruntime + model |
| librosa | 2 | beat sync | numba/llvmlite/scipy ~250 MB |
| piper / scipy | 2 | local TTS, signal | onnxruntime |

Runtime model downloads: whisperx weights, HF `from_pretrained` alignment models, piper voices (`~/.piper/models`), rembg onnx.

### Tiering (the core of the bootstrapper)
```
CORE (eager, app dead without it)
  Python 3.12 + fastapi/uvicorn/pydantic/claude-agent-sdk/jsonschema/Pillow…   ~40MB
  ffmpeg + ffprobe (static)                                                     ~80MB
AGENT-COMPOSITION (on first comp render)
  Node + npx + mermaid-cli
CAPABILITY PACKS (lazy, on first use, with progress UI)
  transcription → whisperx+torch+model    reframe → mediapipe+cv2
  bg-removal → rembg+onnxruntime          beat-sync → librosa
  local-tts → piper+voice
```

---

## 2. Subsystem designs

### 2.0. Locked architecture decisions (eng review 2026-07-01)

**Python runtime (industry standard for install-more-later apps):** bundle a `python-build-standalone` CPython into the app via electron-builder `extraResources`; manage a venv in `~/Library/Application Support/OpenNolan/runtime/venv`; use **`uv`** (single static binary, bundled) as the installer for eager core deps AND lazy capability packs. Matches ComfyUI Desktop / DiffusionBee (bundle core) + InvokeAI (download heavy deps on demand). Freezing (PyInstaller) is rejected: you cannot pip-install torch into a frozen binary, which kills the lazy-pack model.

**repo_root split (the core of P0):** `repo_root` today conflates (a) read-only agent content (skills/, tools/, lib/, AGENT_GUIDE.md, pipeline_defs/, schemas/) with (b) the writable `projects/` tree. In a packaged app these MUST split:
- Read-only agent tree → ships in the app bundle `Resources/app/` (replaced on update).
- Writable state (`projects/`, venv, models, `.env`) → `~/Library/Application Support/OpenNolan/` (persists across updates).
- **`agent_runner.py:539, 576, 691, 835` hardcode `self.repo_root / "projects"`** — these must resolve to the configured projects_dir instead. The agent subprocess still gets `cwd=<bundle Resources>` for reading skills/tools, but every write target is the App-Support projects dir.

**Keys relocation:** `server/env_config.py` `ENV_PATH` → `~/Library/Application Support/OpenNolan/.env` (chmod 600). Keychain deferred to TODO (low stakes, single-user local app).

**Notarization entitlements (prevents the works-in-dev-fails-after-notarize trap):** hardened runtime + these entitlements are REQUIRED so the notarized app can load pip-installed native libs (torch/onnxruntime/numba `.dylib`s):
```
com.apple.security.cs.disable-library-validation      # load unsigned 3rd-party dylibs
com.apple.security.cs.allow-dyld-environment-variables # venv/PYTHONPATH
com.apple.security.cs.allow-unsigned-executable-memory # torch/numba JIT
com.apple.security.cs.allow-jit
```

### 2A. Packaging (ask #1)
- **Tool:** `electron-builder` (mature, handles dmg + sign + notarize + auto-update in one config). Reject electron-forge (thinner notarization story).
- **De-repo the runtime.** `main.js` must stop assuming `REPO_ROOT`. In a packaged app:
  - Backend code ships inside the app bundle (`Resources/backend/` = the `server/`, `tools/`, `lib/`, `schemas/` trees).
  - `OPENNOLAN_PROJECTS_DIR` defaults to `~/Library/Application Support/OpenNolan/projects` (user-writable; the app bundle is read-only and gets replaced on update).
  - Managed runtime (Python venv, ffmpeg, packs, models) lives in `~/Library/Application Support/OpenNolan/runtime/` — survives app updates, is user-writable.
  - `.env` / BYOK keys move to `Application Support` (+ Keychain for secrets), never the bundle.
- **Output:** universal (arm64 + x64) `.dmg`, versioned, uploaded as a GitHub Release asset; website "Download for Mac" points at the latest release.

### 2B. First-run bootstrapper + dependency doctor (ask #2)
- **Detect → Plan → Install → Verify**, driven off the existing `/api/health` pattern extended to `/api/doctor`.
- On launch, before opening the editor: a **setup window** checks the CORE tier. Missing → install with a live progress log (this is a first-class UX screen, not a spinner).
  - Python: prefer a bundled `python-build-standalone` runtime (deterministic, no reliance on system python3). Create the managed venv, `pip install` core requirements from a pinned lockfile.
  - ffmpeg: download a pinned static universal binary to the runtime dir, checksum-verify, `chmod +x`.
  - Node: bundled or downloaded pinned Node; needed only when a comp render is first requested (can defer to composition tier).
- **Capability packs install lazily.** When the agent (or a UI feature) first needs transcription, the backend returns `409 pack_required: transcription`; the UI shows "Installing video-understanding (~2.5 GB, one time)…" with progress, then retries. Same pattern for reframe/bg-removal/beat-sync/tts.
- **torch-on-Apple-Silicon failure is handled explicitly** (see Error map): install can succeed but import can fail; the doctor verifies `import torch; torch.backends.mps.is_available()` and surfaces a real message + a "retry / use cloud fallback / skip" choice, never a silent 500.
- **Offline / firewall:** every install step names what it's downloading and from where; failure is retryable and does not corrupt the runtime dir (install to temp, atomic move on success).

### 2C. Feedback / bug requests (ask #3)
- In-app **Send Feedback** panel (bug / feature toggle + free text).
- Backend `/api/feedback` → forwards to **Resend** (email to you, reuses existing waitlist infra) AND emits a PostHog `feedback_submitted` event (searchable, tied to the user's session + usage).
- Auto-attaches: app version, macOS version, which capability packs are installed, last N backend log lines (the `stderrTail` ring buffer already exists in `main.js`). Consent line: "includes app version + recent logs."
- Public bug tracking: link to GitHub Issues (it is OpenNolan, open-source).

### 2D. Analytics (ask #4)
Reuse the existing PostHog project (OpenNolan, id 478214). BYOK app → self-host the ingestion key in the app; respect an opt-out.

**North Star: first successful watermark-free export.** That is the aha. Everything else is a funnel toward it or a habit built on top of it.

**Funnel:**
```
website download → app first-open → deps installed OK → project created
  → first edit → first agent turn → FIRST EXPORT (activation)
  → 2nd-week return + 2nd export (retention)
```

**Events (minimum viable taxonomy):**
- `app_opened` (version, os, cold/warm)
- `deps_install_started` / `deps_install_completed` / `deps_install_failed` (tier, pack, duration, error)
- `project_created`
- `agent_turn_started` / `agent_turn_completed` (duration, tools used)
- `edit_action` (type: cut/trim/split/overlay/caption/speed/…)
- `render_started` / `render_completed` / `render_failed` (scenes, duration, was_cheap_edit)
- `export_completed` ← **activation event** (resolution, watermark_free=true, duration)
- `feedback_submitted`

**Metrics that answer "is it useful":**
1. **Activation rate** = installs reaching first export within 7 days. (Is the core loop learnable?)
2. **Time-to-first-export.** (Friction.)
3. **Agent-edit acceptance** = agent turns whose edits are kept vs undone within N minutes. (Is the AI actually good? This is the wedge.)
4. **Exports per active user per week.** (Habit / real usefulness.)
5. **Week-2 retention.** (Did it stick.)
6. **Dep-install success rate** by pack. (Is the bootstrapper the thing killing activation? Directly ties #2 to the funnel.)

---

## 3. Build sequence
- **P0 — de-repo the runtime:** relocate backend spawn, projects dir, keys, ffmpeg lookup to Application Support. Nothing packages until this is done.
- **P1 — bootstrapper + doctor:** CORE tier install (Python + ffmpeg), `/api/doctor`, setup window. Then lazy capability packs.
- **P2 — sign + notarize + dmg + auto-update** (electron-builder).
- **P3 — analytics events + PostHog funnel/dashboard.**
- **P4 — feedback panel + crash/error reporting.**
- **P5 — website Download for Mac → GitHub Release.**

---

## 4. NOT in scope
- Mac App Store distribution (sandboxing would break the local subprocess model). Direct download only.
- Windows/Linux packaging (Mac-first per ICP).
- Bundling all ML eagerly (2.5 GB first-run; rejected for lazy packs).
- BYOK-cloud AI (considered; user chose local provisioning — kept as a per-pack fallback, see cherry-picks).

## 5. What already exists (leverage)
- BYOK env panel (agent auth + future pack keys).
- PostHog project provisioned.
- Resend (waitlist) → reuse for feedback.
- `/api/health` + `stderrTail` ring buffer → basis for doctor + feedback log attach.
- Lazy imports throughout `tools/` → makes tiered provisioning possible without refactor.

## 5b. Cherry-pick decisions (resolved in CEO review 2026-06-30)

| # | Expansion | Decision | Reasoning |
|---|---|---|---|
| 1 | Code signing + notarization | **ACCEPTED (required)** | User chose direct-website-download, not App Store. That is precisely the path Gatekeeper blocks without Developer ID + notarize ("app is damaged"). Non-optional. Needs Apple Developer account ($99/yr). |
| 2 | Auto-update (electron-updater) | **ACCEPTED** | First release will have bootstrapper bugs; without it, every user must manually re-download. electron-builder emits the update feed for free. |
| 3 | Crash/error reporting (PostHog) | **ACCEPTED** | Verified: PostHog covers the Python backend (the real crash surface) first-class via `posthog-python` `enable_exception_autocapture`. Electron main-process native symbolication is not first-class yet (open FR), but Electron here is a thin shell, so the gap is minor. |
| 4 | Per-pack cloud fallback | **ACCEPTED** | Defuses the biggest risk of the local-provisioning choice: torch installs but won't load. If a pack fails, offer BYOK cloud (Deepgram/ElevenLabs Scribe/etc) via the existing BYOK panel. |

### PostHog integration specifics
- **Backend (Python):** `pip install posthog`; init with the project token + `host=https://us.i.posthog.com` + `enable_exception_autocapture=True`. Emits product events (`export_completed`, `deps_install_failed`, …) AND autocaptures unhandled exceptions.
- **Renderer (Electron):** `posthog-js` for UI events + renderer JS error capture.
- **Identity:** stable anonymous device id (UUID in Application Support), NO PII. Respect an opt-out toggle in settings (BYOK/open-source ethos).
- **Known gap:** no first-class Electron *native* crash symbolication ([FR #43993](https://github.com/PostHog/posthog/issues/43993)); acceptable because heavy logic is in the Python child.

---

## 5b-2. Bootstrapper / doctor state machine

```
  app launch
      │
      ▼
  ┌─────────────┐   all core present   ┌──────────────┐
  │  DIAGNOSE   │─────────────────────▶│   READY      │──▶ open editor window
  │ /api/doctor │                      └──────────────┘
  └─────┬───────┘                              ▲
        │ missing core (python/ffmpeg)          │ verify OK (import smoke test)
        ▼                                        │
  ┌─────────────┐  download+install (atomic)   ┌─┴──────────┐
  │  SETUP UI   │─────────────────────────────▶│  VERIFY    │
  │ progress log│                              └─────┬──────┘
  └─────┬───────┘                                    │ verify FAIL
        │ install FAIL (network/disk/checksum)        ▼
        ▼                                     ┌──────────────┐
  ┌──────────────┐  retry (idempotent)        │  DEGRADED    │
  │  ERROR CARD  │◀───────────────────────────│ (core broke) │
  │ retry / logs │                            └──────────────┘
  └──────────────┘

  CAPABILITY PACK (lazy, triggered by a tool call):
    tool needs whisperx ──▶ backend 409 {pack_required: transcription}
       │
       ▼
    UI: "Install video-understanding (~2.5GB, one time)?"  ──┐
       │ yes                                                 │ no / install fails
       ▼                                                     ▼
    uv pip install pack (atomic: temp venv-overlay,     ┌──────────────────┐
       verify `import torch` + mps check)               │ CLOUD FALLBACK    │
       │ verify OK          │ verify FAIL               │ (BYOK key present?)│
       ▼                    └──────────────────────────▶│  yes: use cloud    │
    retry original tool                                 │  no: explain+skip  │
                                                        └──────────────────┘
```
Invariants: install to a temp path → `os.replace` (atomic; same footgun as the `write-checkpoint-not-atomic` learning). Every transition emits a PostHog event. VERIFY is not "did pip exit 0" — it is "does `import <pkg>` succeed", because torch installs but fails to load is the #1 real failure.

## 5c. Error & Rescue map — the bootstrapper (the one genuinely new, high-risk codepath)

| Codepath | What can go wrong | Rescued? | Rescue action | User sees |
|---|---|---|---|---|
| Python runtime install | download fails / network drop | Y | retry w/ backoff; install to temp, atomic move | "Couldn't download Python runtime. Retry." + retry button |
| pip install core reqs | PyPI down / wheel build fail | Y | pinned lockfile + wheels; retry; log full pip output | "Setup step failed" + expandable log + retry |
| ffmpeg static download | checksum mismatch / partial | Y | verify sha256; re-download on mismatch | "Verifying media tools…" then retry on fail |
| Capability pack: torch install | disk full / wheel too big | Y | preflight free-space check; clear error | "Needs ~3 GB free; you have X" |
| Capability pack: **torch import** | installs but fails to load (MPS/arch) | Y | doctor verifies `import torch`; offer **cloud fallback** or skip | "Local video AI unavailable on this Mac. Use cloud (needs a key) or skip." |
| Model download (whisperx/piper/rembg) | HF/CDN 403/timeout | Y | retry; cache to Application Support; offer cloud fallback | "Downloading model…" + retry |
| Backend spawn (packaged) | bundled python missing/quarantined | Y (fatal dialog exists) | `fatal()` with stderr tail (already in main.js) | error dialog w/ last backend output |
| Port clash (:free port) | rare TOCTOU | N→**gap** | catch bind failure, pick new port, retry once | currently surfaces raw stderr — **add a retry** |

Rule applied: every install step is **atomic** (temp dir → move on success) so a failure never leaves a half-broken runtime. Every step **logs full output** and emits a `deps_install_failed` event with the tier/pack/error.

## 5d. Failure Modes Registry

| Codepath | Failure mode | Rescued? | Test? | User sees | Logged? |
|---|---|---|---|---|---|
| First-run core install | offline | Y | needed | retryable error screen | Y (event) |
| torch pack | import fail on Apple Silicon | Y | needed | cloud fallback / skip | Y |
| Notarization (build-time) | cert expired | N/A (CI) | CI check | — | CI log |
| Auto-update | bad update bricks app | partial | needed | staged rollout mitigates | Y |
| ffmpeg missing at runtime | 503 on scrub/export | Y (exists) | exists | "media tools unavailable" | Y |
| Analytics opt-out | events still sent | must-fix | needed | nothing | — |
| Port clash | backend won't bind | **N — gap** | needed | raw stderr | partial |

Two must-fix gaps before launch: **(a)** honor the analytics opt-out at the SDK init (don't init if opted out), **(b)** retry-on-port-clash in `main.js` instead of surfacing raw stderr.

## 5e. Deploy / rollout + rollback
- **Release = tagged GitHub Release** with the signed+notarized universal `.dmg` + the electron-updater `latest-mac.yml` feed.
- **Staged rollout:** publish to a `beta` channel first (you + a few testers), promote to `stable` after the funnel shows first-export working on machines that aren't yours.
- **Rollback:** auto-update is forward-only, so rollback = publish a higher version number that reverts the change. Keep the prior `.dmg` downloadable. This is why auto-update (#2) is load-bearing.
- **Post-deploy verification (first hour):** watch PostHog for `app_opened` → `deps_install_completed` → `export_completed` on a non-Het device id, and zero new `$exception` spikes.

---

## 5f. Test plan (implementation must ship tests alongside code)

Mostly Python + a thin Electron shell; test at the seam that matters (path resolution + doctor state machine), not the OS installer.

**Unit (pytest) — P0 de-repo:**
- `env_config.ENV_PATH` resolves to App-Support when `OPENNOLAN_HOME` set, repo `.env` otherwise (back-comaptible dev). Happy/missing-dir/unwritable paths.
- projects_dir resolution: agent_runner writes go to configured dir, NOT `repo_root/projects`. **Regression test (CRITICAL):** assert none of agent_runner.py:539/576/691/835 derive a write path from repo_root. This proves the split didn't regress.
- doctor: `/api/doctor` returns correct missing-core list for (all present / no python / no ffmpeg / no pack).

**Unit — bootstrapper state machine:**
- DIAGNOSE→READY when core present; →SETUP when missing.
- atomic install: simulated mid-install crash leaves NO half-venv (temp dir orphaned, real dir untouched).
- VERIFY distinguishes "pip ok + import ok" from "pip ok + import fails" → routes to CLOUD FALLBACK.
- capability pack: 409 `pack_required` shape; retry-after-install succeeds.

**Unit — must-fix gaps:**
- analytics: PostHog is NOT initialized when opt-out flag set (assert no client constructed, not just "no events"). 
- port clash: `main.js` bind-fail on chosen port picks a new port and retries once (mock `net` EADDRINUSE).

**Integration (spawn real backend from a temp App-Support dir):**
- cold boot with empty App-Support → doctor reports missing core (don't actually install torch in CI).
- feedback: `/api/feedback` → Resend called + `feedback_submitted` event emitted (both mocked).

**Manual / CI-gated (can't unit-test):**
- notarized `.dmg` opens on a clean Mac that isn't yours (Gatekeeper) — the one test that only a real second machine proves.
- auto-update: v(n) → v(n+1) upgrade on the beta channel.

## 5g. Worktree parallelization

| Lane | Workstream | Modules | Depends on |
|---|---|---|---|
| **A** | P0 de-repo the runtime | server/, lib/, desktop/main.js | — (foundation) |
| **B** | Analytics events + PostHog | server/ (events), web/src/ (posthog-js) | A (needs App-Support paths for device id) |
| **C** | Feedback panel | server/ (/api/feedback), web/src/studio/ | A (light) |
| **D** | Packaging + sign + notarize + auto-update | desktop/, CI (electron-builder) | A (bundle layout) |
| **E** | Bootstrapper + doctor + packs | desktop/, server/ (/api/doctor) | A, D (needs bundle + venv layout) |

Execution: **A first, alone** (everything hangs off the path split). Then **B + C in parallel** (different files, both light on A). **D then E** are largely sequential (E needs D's bundle layout), but D can start against A while B/C run. So: A → {B, C, D} in parallel → E. Conflict flag: B and C both touch `web/src/studio/` chrome and `server/app.py` route registration — coordinate route additions or expect a small merge in `app.py`.

## 6. Dream-state delta
12-month ideal: a stranger downloads a signed dmg, opens it, the setup screen provisions core in ~20s, they make a reel with the agent, export watermark-free, and you can see in PostHog that 40% of installs activated. This plan builds exactly that path. The gap it leaves: capability-pack install reliability across diverse Macs/networks is the long-tail risk, mitigated by cloud fallback (cherry-pick).

---

## 7. Outside-voice (Codex) refinements — folded into the plan

Resolved strategic tensions:
- **v1 phasing = HOLD local-first** (Het's call, overriding both AI reviewers' slim-BYOK-first recommendation; he owns the offline-agent + no-per-minute-cost context).
- **Arch = Apple Silicon (arm64) only for v1.** One signed arm64 `.dmg`. No universal build, no per-arch pack matrix, no Intel wheel-gap tickets, MPS available. Add x64 later only if data demands. (Kills Codex's universal-wheel and Intel-UX concerns outright.)

Accepted refinements (now requirements):
1. **Sign the bundled Python binary (arm64) with the hardened-runtime entitlements** — they attach to the process that loads the native dylibs, NOT to Electron. Notarizing the `.app` does not cover runtime-installed wheels; the signed+entitled Python is what makes `disable-library-validation` let them load.
2. **`uv` installs PACKAGES only; never provisions Python.** Bundle a signed CPython; uv/pip populate the venv. (Runtime-downloaded interpreter = unsigned/quarantined trap.)
3. **`--only-binary=:all:`** — forbid source-build fallback (no Xcode CLT on a user's Mac = dead first-run on llvmlite/tokenizers/opencv).
4. **Route ALL caches to App Support:** `HF_HOME`, `TRANSFORMERS_CACHE`, `XDG_CACHE_HOME`, `~/.piper`, rembg, plus the existing `~/.cache/opennolan` / `~/.opennolan/clips_cache`. Plan previously only routed `projects/`.
5. **Capability gate runs BEFORE agent tool execution**, not just on UI retry — a headless agent turn importing whisperx mid-run must be gated up front.
6. **venv rebuild-on-mismatch:** pin a runtime-manifest version; an auto-update that changes Python minor version or bundle layout triggers a venv rebuild, never trust-in-place.
7. **PII scrubbing is designed:** scrub file paths / prompts / keys from PostHog autocapture AND the stderr feedback tail before send.
8. **Atomic install = versioned runtime dirs + pointer swap** (populated-dir `os.replace` isn't clean on macOS); revise transcription-pack disk estimate up (well past 2.5 GB with weights + alignment models + wheel caches); free-space preflight assumes the larger number.
9. **P0 is bigger than stated** (Codex confirmed): `server/app.py:153` builds `AgentRunner(repo_root=REPO_ROOT)`, and `agent_runner.py:532` embeds `projects/...` into the agent PROMPT/scripts. De-repo changes the agent's prompt contract, not just filesystem paths. → single source of truth for paths (`lib/app_paths.py`).

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEARED | mode: SELECTIVE_EXPANSION; 4 expansions proposed, 4 accepted, 0 deferred; reframed packaging+deps as one problem; local-provisioning held per user + cloud fallback added |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | ISSUES_FOUND | notarization applies to Python process not .app; uv must not provision Python; arm64-only beats universal; 9 refinements folded in; both AI models recommended slim-BYOK-v1 (user held local-first) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 3 arch findings (repo_root split, keys relocation, notarize entitlements), 0 critical gaps; Python strategy locked (python-build-standalone + uv); state machine + test plan + parallelization written |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |

- **CROSS-MODEL:** both reviewers recommended slim-BYOK-v1; user held local-first (owns offline/cost context). Both agreed P0 de-repo is real and under-scoped. arm64-only + entitlements-on-Python resolved cleanly.
- **UNRESOLVED:** 1 (default cloud transcription provider for the fallback — Deepgram vs AssemblyAI vs ElevenLabs Scribe)
- **VERDICT:** CEO + ENG CLEARED, Codex refinements folded in. Decisions locked: local-first v1, arm64-only, python-build-standalone + uv (packages only), sign+entitle the bundled Python. Implementing Lane A (de-repo the runtime) first — it's tension-independent (needed in every scenario). Design review recommended for the first-run setup/doctor UX before S-tier polish.
