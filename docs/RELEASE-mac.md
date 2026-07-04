# Releasing OpenNolan for Mac (Lane D)

How to build, sign, notarize, and publish the OpenNolan desktop app as a directly-downloadable
Apple-Silicon `.dmg`. This is the packaging layer from [`docs/plans/publish-mac-app.md`](plans/publish-mac-app.md).

**Locked decisions:** direct download (not the App Store), **arm64 only** for v1, bundle a signed
`python-build-standalone` interpreter (packages install at first run — Lane E), Developer ID +
notarization mandatory.

---

## What Lane D ships

```
OpenNolan.app/Contents/
  MacOS/OpenNolan                      Electron launcher
  Resources/
    app.asar                          main.js, preload.js, node_modules (electron-updater)
    backend/                          OPENNOLAN_CODE_ROOT (read-only)
      server/ lib/ tools/ schemas/ pipeline_defs/ skills/ .agents/skills/ scripts/ assets/ templates/ styles/
      AGENT_GUIDE.md PROJECT_CONTEXT.md config.yaml requirements-*.txt
      web/dist/                       served by the backend at code_root()/web/dist
    python/bin/python3                bundled, SIGNED python-build-standalone (arm64)
```

User data (projects, BYOK `.env`, the managed venv, caches) lives in
`~/Library/Application Support/OpenNolan` (`OPENNOLAN_HOME`), never inside the read-only bundle.
`desktop/main.js` sets `OPENNOLAN_CODE_ROOT`, `OPENNOLAN_HOME`, and `OPENNOLAN_PROJECTS_DIR` for the
uvicorn child, and runs the bundled interpreter, only when `app.isPackaged`. Dev is unchanged.

## Files

| File | Role |
|---|---|
| `desktop/package.json` → `build` | electron-builder config: arm64 dmg+zip, extraResources, entitlements, `mac.notarize:true`, publish feed |
| `desktop/build/entitlements.mac.plist` | hardened-runtime entitlements (disable-library-validation, allow-dyld-env-vars, allow-unsigned-executable-memory, allow-jit) |
| `desktop/build/entitlements.mac.inherit.plist` | **same 4 keys** — electron-builder applies THIS to nested Mach-O incl. the bundled Python, so it must stay a superset of the main plist |
| `scripts/fetch-python.mjs` | downloads + sha256-verifies (pinned) + extracts + prunes the arm64 interpreter to `desktop/resources/python/` |

**Signing + notarization are electron-builder built-ins — no custom hooks.** electron-builder's
`@electron/osx-sign` pass recurses the whole app and re-signs every nested Mach-O (including
`Resources/python/*`) with your Developer ID + hardened runtime + the inherit entitlements; `mac.notarize:true`
then notarizes the `.app` and staples both the app and the `.dmg`. With **no** Developer ID identity in the
keychain, electron-builder skips signing AND notarization and produces an **unsigned** `.app` (fine for local
testing, not for distribution).

## Build

```bash
# unsigned local smoke build (no certs needed) — produces an unpacked .app under desktop/dist/mac-arm64
npm --prefix desktop install
npm --prefix desktop run dist:dir

# full signed + notarized dmg (needs the env vars below)
npm --prefix desktop run dist
```
`dist`/`dist:dir` first run `fetch-python.mjs` (interpreter) and the web build, then electron-builder.

## Signing + notarization env (release machine only)

Nothing is committed. With **none** of these set, electron-builder skips signing + notarization and the
build succeeds **unsigned** — good for local testing, useless for distribution. If a Developer ID identity
is present but notary creds are missing, the build fails loudly (correct — don't ship un-notarized).

```bash
# Developer ID signing identity (one of):
export CSC_NAME="Developer ID Application: <Your Name> (<TEAMID>)"   # from your login keychain
#   or: export CSC_LINK=/abs/path/cert.p12 ; export CSC_KEY_PASSWORD=...

# Notarization (one of):
#   (A) App Store Connect API key — recommended, survives password rotation, CI-friendly
export APPLE_API_KEY=/abs/path/AuthKey_XXXXXXXXXX.p8
export APPLE_API_KEY_ID=XXXXXXXXXX
export APPLE_API_ISSUER=<issuer-uuid>
#   (B) Apple ID app-specific password
export APPLE_ID="you@example.com"
export APPLE_APP_SPECIFIC_PASSWORD="xxxx-xxxx-xxxx-xxxx"   # generated at appleid.apple.com, NOT your account pw
export APPLE_TEAM_ID=<TEAMID>
```

Requires an **Apple Developer account** ($99/yr) and a **Developer ID Application** certificate.
`codesign --timestamp` needs network access to Apple's timestamp server at build time.

## Publish / auto-update

- `build.publish` points at GitHub Releases — **replace `OWNER_PLACEHOLDER`/`REPO_PLACEHOLDER`** with the
  real repo before releasing. Publishing (`electron-builder --publish always` or CI) uploads the `.dmg`,
  the `.zip`, and `latest-mac.yml` (the update feed electron-updater reads).
- The `zip` target is required even though users install the `.dmg` — Squirrel.Mac/electron-updater
  update from the zip. Do not drop it.
- Auto-update artifacts must be **signed** (electron-updater verifies the signature on macOS), so
  auto-update only works on notarized builds. `main.js:initAutoUpdate()` runs only when `app.isPackaged`.
- Website "Download for Mac" links at the GitHub Release `.dmg`.

## Verify a real build

```bash
codesign -dvvv --entitlements - "OpenNolan.app/Contents/Resources/python/bin/python3"   # TeamIdentifier set, flags=0x10000(runtime)
codesign --verify --deep --strict "OpenNolan.app"
spctl -a -vv --type install OpenNolan.dmg                                                # "accepted / Notarized Developer ID"
xcrun stapler validate OpenNolan.dmg
```

## Known gaps (not done in Lane D)

- **Agent work-root:** the FastAPI editor path works packaged (`projects_dir` is injected). But the
  headless **agent** subprocess runs with `cwd = code_root` (read-only bundle) and its prompt still tells
  it to write to bare-relative `projects/...`. `scripts/update_stage.py` was fixed to resolve
  `app_paths.projects_dir()`, but the agent-prompt/other-relative-write paths still need a writable
  work-root (symlink farm, or absolute paths in the prompt). This is the one remaining blocker before the
  agent works in a packaged app. Tracked for the next lane.
- **Node / HyperFrames / Remotion renders:** the Node runtime + `mmdc` are NOT bundled yet, so agent
  compositions that need them won't render in the packaged app until the composition tier is added (Lane E).
- **Icon:** no `build/icon.icns` yet — the app uses the default Electron icon. Add a 1024px `.icns`
  (a genuine ship-blocker for a public download; electron-builder auto-picks up `build/icon.icns`).
- **Publish placeholders:** `build.publish` ships `OWNER_PLACEHOLDER/REPO_PLACEHOLDER`, baked into
  `app-update.yml` at pack time. Replace with the real GitHub owner/repo before ANY distributed build,
  or every launch's update check 404s (harmless now — logged + caught, not a crash).
- **CI:** `.github/workflows/release-mac.yml` is a skeleton — it needs the repo secrets + icon before it
  runs green. Until then, release with `npm --prefix desktop run dist -- --publish always` on your Mac.
- **First-run provisioning (Lane E — DONE):** on first packaged launch, `main.js` shows a setup window
  and runs `scripts/provision.py --core` with the bundled interpreter, which uses the bundled `uv` to
  build a venv at `OPENNOLAN_HOME/runtime/venv`, install the core deps (`requirements-ui.txt` +
  `requirements.txt`, wheels only), and provision ffmpeg; then `pythonBin()` prefers that venv. Heavy ML
  installs lazily as capability packs (`transcription`/`vision`/`bg-removal`/`beat-sync`/`tts`) via
  `POST /api/provision/{pack}`; `GET /api/doctor` reports status. `OPENNOLAN_FORCE_PROVISION=1` makes the
  doctor report everything missing so you can watch the full flow on a machine that already has the tools.
  Remaining: the ffmpeg source URL (`ffmpeg.martin-riedl.de` arm64) should be pinned + sha-verified before
  release, and the lazy-pack **409 gate** (auto-prompt when an agent tool needs an absent pack) still needs
  wiring into the tool path.
- **Bundle size:** the pruned interpreter is ~56 MB; the full unpacked `.app` was ~950 MB in testing
  (dominated by `assets/` + `tools/`). Trim non-runtime assets from `extraResources` before release.
- **Latent:** `tools/base_tool.py` / `tools/tool_registry.py` load `.env` from a repo-relative path
  instead of `lib.app_paths.env_path()`. Harmless today (create_app loads the right `.env` into the
  environment first), but route them through `app_paths` when convenient.
