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

## 6. Dream-state delta
12-month ideal: a stranger downloads a signed dmg, opens it, the setup screen provisions core in ~20s, they make a reel with the agent, export watermark-free, and you can see in PostHog that 40% of installs activated. This plan builds exactly that path. The gap it leaves: capability-pack install reliability across diverse Macs/networks is the long-tail risk, mitigated by cloud fallback (cherry-pick).

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | ISSUES_OPEN | mode: SELECTIVE_EXPANSION; 4 expansions proposed, 4 accepted, 0 deferred; 2 must-fix gaps (analytics opt-out, port-clash retry); reframed packaging+deps as one problem; local-provisioning strategy held per user, cloud fallback added as hedge |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 0 | — | — |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |

- **UNRESOLVED:** 1 (default BYOK provider for the cloud-fallback transcription API — Deepgram vs AssemblyAI vs ElevenLabs Scribe)
- **VERDICT:** CEO CLEARED (with 2 must-fix gaps noted) — eng review required before implementation. The de-repo-the-runtime step (P0) is the gate: nothing packages until the backend boots from Application Support instead of the git checkout.
