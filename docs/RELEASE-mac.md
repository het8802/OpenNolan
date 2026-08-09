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
      server/ lib/ tools/ schemas/ pipeline_defs/ skills/ .agents/app/skills/ scripts/ assets/ templates/ styles/
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
`Resources/python/*`) with your Developer ID + hardened runtime + the inherit entitlements; `mac.notarize`
then notarizes the `.app` and staples it. With **no** Developer ID identity in the keychain,
electron-builder skips signing AND notarization and produces an **unsigned** `.app` (fine for local
testing, not for distribution). The `.dmg` is notarized separately — see `notarize_dmg` in
`scripts/build-dmg.sh`.

### Two traps in `build.mac` (both have already cost a build)

1. **`"notarize": false` is the committed default**, so no stray local build uploads to Apple.
   `npm run dist` re-enables it with `--config.mac.notarize=true`. It must stay a real JSON
   **boolean**: app-builder-lib gates on `notarizeOptions === false` (`MacTargetHelper.js:258`),
   and a CLI-passed string `"false"` is truthy and would **not** skip.
2. **No comment keys.** `scheme.json` sets `additionalProperties: false` on `MacConfiguration`, so
   any `_comment`-style key fails validation with the near-useless `configuration.mac should be one
   of these: null`. Explanations go in this doc, not in `package.json`. To find the real offender:

   ```bash
   node -e "const s=require('app-builder-lib/scheme.json'),a=new (require('ajv'))({allErrors:true,strict:false}),v=a.compile(s);
   v(require('./package.json').build)||console.log(v.errors.filter(e=>e.keyword==='additionalProperties'))" # run in desktop/
   ```

## Build

**`scripts/build-dmg.sh` is the entry point** — the raw `npm` scripts below produce the `.app` + `.zip`
but no `.dmg` (`mac.target` is `zip` only; see the DMG-builder comment in that script for why).

```bash
# fast local smoke build — unpacked .app only, never contacts Apple
scripts/build-dmg.sh --dir

# signed + notarized .dmg, not published (Apple round-trip: ~1 GB up, twice)
scripts/build-dmg.sh

# THE RELEASE: signed + notarized .app AND .dmg, Gatekeeper-verified, all three
# artifacts uploaded (.dmg for humans, .zip + latest-mac.yml for auto-update)
gh auth switch --user het8802 && export GH_TOKEN=$(gh auth token)
scripts/build-dmg.sh --publish
```
`--publish` fails fast in preflight if the notary credentials, the signing identity, or `GH_TOKEN`
are missing, rather than after a 20-minute build. Bump `desktop/package.json` `version` first — the
release it targets is `v{version}`.

Underneath, `dist`/`dist:dir` first run `fetch-python.mjs` (interpreter) and the web build, then
electron-builder.

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
- **Publish as a NORMAL release — never tick "Set as a pre-release".** `allowPrerelease` is deliberately
  unset in `main.js:initAutoUpdate()`, so electron-updater ignores prereleases: tagging one silently
  turns auto-update off for every existing user, no matter how correct `latest-mac.yml` is.
- Auto-update artifacts must be **signed** (electron-updater verifies the signature on macOS), so
  auto-update only works on notarized builds. `main.js:initAutoUpdate()` runs only when `app.isPackaged`.
- Website "Download for Mac" links at the GitHub Release `.dmg`.

## Verify a real build

`scripts/build-dmg.sh` now runs the last two itself and **dies** if either fails, so a .dmg that
reaches a user is Gatekeeper-clean by construction. The first two are still useful by hand:

```bash
codesign -dvvv --entitlements - "OpenNolan.app/Contents/Resources/python/bin/python3"   # TeamIdentifier set, flags=0x10000(runtime)
codesign --verify --deep --strict "OpenNolan.app"
spctl -a -vv --type install OpenNolan.dmg      # automated: "accepted / Notarized Developer ID"
xcrun stapler validate OpenNolan.dmg           # automated
```

To feel what a downloader feels (quarantine is what makes Gatekeeper strict):

```bash
xattr -w com.apple.quarantine "0081;00000000;Safari;" OpenNolan.dmg && open OpenNolan.dmg
```

## Known gaps

**Closed since Lane D:** the agent work-root (OPN-4 — `agent_add_dirs()` passes the writable
projects dir to the SDK, `build_sandbox` admits it); the bundled Node runtime + composition
engines (OPN-3, `desktop/resources/node`); `build/icon.icns`; the `build.publish` placeholders;
and .dmg notarization + stapling + upload (now automated in `scripts/build-dmg.sh`).

Still open:

- **ffmpeg is unpinned — the sharpest one.** `lib/provision.py:475` points at a
  `/redirect/latest/` URL with `FFMPEG_SHA256 = ""`, so the download is **unverified** and each
  user's first run fetches whatever `latest` is that day. Two people installing the *same* .dmg a
  month apart get different binaries. Fill the hashes with
  `python scripts/provision.py --print-ffmpeg-sha` against a versioned URL, then pin both.
  Also a single-host runtime dependency: if it's down, provisioning degrades to a 503 on
  scrub/export rather than failing loudly.
- **uv is unpinned:** `scripts/fetch-uv.mjs:32` resolves `releases/latest` at build time, so two
  builds of the same commit can bundle different uv versions. (Python and Node are pinned + sha'd
  out-of-band — match that.)
- **CI:** `.github/workflows/release-mac.yml` does not work. It only runs `npm run dist`, never
  `scripts/build-dmg.sh`, so it produces no .dmg at all — and then fails on `ls desktop/dist/*.dmg`.
  It also has no equivalent of the script's signing gate, so with absent/expired secrets
  electron-builder skips signing, exits 0, and it would publish an **unsigned** release as green.
  Release from a Mac with `scripts/build-dmg.sh --publish` until this calls the script and hard-fails
  on missing secrets.
- **Bundle size:** users download ~1 GB. Trim non-runtime `assets/` + `tools/` from `extraResources`.
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
