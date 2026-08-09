# First-run provisioning: offline core + a failure the user can act on

**Status: PLAN**

Companion to the decision log at
<https://claude.ai/code/artifact/5178f7d1-1f64-4cb2-b753-5d38630bce41>, which
carries the rejected alternatives and cost breakdowns for every choice below.
That document was reviewed by Kimi K3 (via `opencode`), which returned 8
findings; 7 were accepted and folded in here, 1 was disputed.

---

## The problem in one paragraph

A beta tester opened OpenNolan 0.2.0 on a clean Mac and setup died with
`command failed (2)`. That message contains no cause, because the cause is
captured and then thrown away one line later. We still do not know why his
install failed. That is two separate defects: first launch depends on four
network round-trips that can each fail, and when one does, the app cannot tell
anyone what happened.

---

## What ships today vs what is missing

We bundle the package *managers*. We bundle none of the packages.

```
  INSIDE OpenNolan.app (1.5 GB)        NOT INSIDE — fetched on first launch
  ─────────────────────────────        ────────────────────────────────────
  Resources/uv/uv          50 MB       pip                    -> pypi.org
  Resources/python/       64 MB        64 core packages       -> pypi.org
  Resources/node/       122 MB        ffmpeg + ffprobe      -> martin-riedl
  Resources/backend/     source        Remotion node_modules  -> npmjs.org
```

The bundled Python is a bare interpreter. `uv venv --seed`
(`lib/provision.py:392`) resolves *pip itself* from PyPI — proven by the version
numbers: `--seed` installs pip 26.2.1 while the interpreter already carries
`ensurepip/_bundled/pip-25.0.1-py3-none-any.whl`. Different versions means
different sources.

---

## The change, in one picture

```
  BEFORE                              AFTER
  ──────                              ─────
  pip              -> pypi.org        pip              -> bundled (ensurepip)
  64 core packages -> pypi.org        64 core packages -> bundled (wheels/)
  ffmpeg           -> one host        ffmpeg           -> host, sha-checked
  node_modules     -> npmjs.org       node_modules     -> npmjs.org (lazy)

  hosts needed to reach the editor:
        2                        ->         0
```

113 MB of wheels (measured: 64 wheels) on a 1.5 GB bundle. The composition tier
and the capability packs stay online on purpose — we are making the *core*
offline, not the whole app.

---

## Build time: one new step

```
  scripts/vendor-wheels.mjs                                    NEW
    |
    |-- reads   requirements-ui.txt + requirements.txt
    |-- runs    uv pip download --no-cache
    |             --python-version 3.12          <- MUST pin. see below
    |             -d desktop/resources/wheels
    |-- asserts every requirement resolved   <- build FAILS loudly here
    '-- writes  wheels/MANIFEST.json  (name, version, sha256)

  desktop/package.json  build.extraResources
    + { "from": "resources/wheels", "to": "wheels" }            NEW
```

**Vendor with uv, not pip.** The install at `lib/provision.py:368` uses
`uv pip install`. Running a *different* resolver at build time can pick a
different version for the same `>=` range, so the set we ship might not be the
set uv wants at install time. One resolver end to end. (Kimi finding 8.)

### Why the build step needs version flags at all

A wheel filename encodes what it is compatible with. Of the 64 wheels measured:

```
  43  ...-py3-none-any.whl              pure Python, runs anywhere
  13  ...-cp312-cp312-macosx_11_0_arm64.whl    needs Python 3.12 + macOS 11+
   4  ...-cp312-cp312-macosx_10_13_universal2.whl
   3  ...-macosx_11_0_arm64.whl
   1  numpy-2.5.1-cp312-cp312-macosx_14_0_arm64.whl   <- needs macOS 14+
```

`cp312` is the Python ABI. **`--python-version 3.12` is mandatory** because
`uv pip download` otherwise resolves for whatever interpreter runs the build. If
the build Mac's default python is 3.13, we vendor `cp313` wheels, and none of
them will install into the bundled 3.12 venv — the build would look fine and
first launch would fail on every machine.

**`--python-platform` is deliberately NOT pinned**, and the earlier draft of this
document was wrong to pin it to `macosx_11_0_arm64`. A wheel tagged
`macosx_14_0` requires macOS 14 or newer, so declaring an 11.0 target *excludes*
it — the numpy above would have been rejected and the resolver would have
silently fallen back to an older numpy, or failed. The build machine is already
arm64 macOS, so its own platform is the correct target.

### The macOS floor this exposes

`LSMinimumSystemVersion` in the shipped `Info.plist` is **12.0**, but numpy
2.5.1's only arm64 wheel needs **macOS 14**. Today that inconsistency is hidden:
`requirements.txt` says `numpy>=1.26`, so a macOS 12 user's install quietly
resolves an older numpy that does have an 11.0 wheel. Every machine gets a
different answer — which is the nondeterminism this whole change removes.

Vendoring one wheel set forces the question into the open:

```
  vendor on this Mac (macOS 26) -> numpy 2.5.1 macosx_14_0_arm64
                                      |
        +-----------------------------+-----------------------------+
        |                                                           |
  macOS 14+ user                                        macOS 12-13 user
  installs fine                                   numpy cannot install
                                          -> strict offline = HARD FAIL
```

This is an open question below, not a decision made here. It is a product call:
either raise the declared minimum to 14.0, or constrain numpy so a
`macosx_11_0_arm64` wheel exists and the 12.0 floor holds.

---

## Run time: three edits inside `ensure_core()`

```
  lib/provision.py

  :389   shutil.rmtree(building, ignore_errors=True)         unchanged
  :392   uv venv --seed --python <bundled> <building>
         ---------------------------------------------      REPLACED
         uv venv --python <bundled> <building>
         <building>/bin/python -m ensurepip                  offline

  :406   _pip_install(building_python, req_args, progress)
     '-- :368  uv pip install --only-binary=:all: *args
               -------------------------------------        REPLACED
               uv pip install --only-binary=:all:
                              --offline --no-cache
                              --find-links <res>/wheels      offline

  :410   python -c "import fastapi, uvicorn, pydantic"       unchanged
  :415   os.replace(building, final)                         unchanged
  :419   provision_ffmpeg(...)                               still network
```

### How `uv pip install` can be offline at all

`uv pip install` is not a client that phones PyPI for permission. It is a
resolver plus an unpacker, and PyPI is only its *default* source of packages:

```
  uv pip install <name>
    |
    |-- 1. RESOLVE: which version, and where is its file?
    |      default source: https://pypi.org/simple     (network)
    |      --find-links <dir>: also treat this directory as a source (local)
    |      --offline:          forbid the network entirely
    |      --no-cache:         forbid ~/.cache/uv too
    |          => with all three, the ONLY source left is <dir>
    |
    '-- 2. INSTALL: a .whl is a ZIP of the already-built package.
           unzip -> site-packages, write .dist-info metadata, make scripts.
           Nothing is compiled. Nothing is fetched. The file is complete.
```

So "does it still need to reach PyPI to download something?" — no. PyPI's only
job is handing over the `.whl`. Once that file is on disk we have already had the
delivery; installing is unzipping it into the venv. There is no separate service
that must approve it, and pip is not involved (`uv pip` is uv's own
implementation, named for familiarity).

This was verified empirically, not assumed. With a **fresh, empty** cache, both
wheels present locally, and the network forbidden:

```
  $ UV_CACHE_DIR=<fresh> uv pip install --python v5/bin/python \
        --offline --find-links wheels-pd python-dateutil
  Resolved 2 packages in 4ms
  Prepared 2 packages in 8ms
  Installed 2 packages in 1ms
   + python-dateutil==2.9.0.post0
   + six==1.17.0
  EXIT=0
```

Note it also pulled `six` — the transitive dependency — from the same local
directory. That is the whole mechanism.

**`--no-cache` is load-bearing, not belt-and-braces.** `uv --offline` is
documented as "Disable network access" — it says nothing about the on-disk
cache. Without `--no-cache`, a wheel we forgot to vendor still installs on any
machine with a warm `~/.cache/uv` (i.e. the developer's), and the omission only
surfaces on a stranger's Mac. That is the exact bug class this whole change
exists to kill. (Kimi finding 1.)

---

## Making the failure legible

Today the cause is captured and discarded in the same function:

```
  lib/provision.py  _run()

  :349   progress("$ " + cmd)            -> streamed to the setup window
  :351   stderr=subprocess.STDOUT        -> uv's reason merged into stdout
  :355   for line in proc.stdout: ...    -> streamed, then forgotten
  :358   code = proc.wait()              -> 2
  :360   raise RuntimeError(f"command failed ({code}): {cmd}")
         ^^^ output NOT attached -- this is the whole bug
```

Change `:355` to also collect into a list, and `:360` to append the last ~15
lines (hard-capped at ~2000 characters). They then flow unchanged:

```
  lib/provision.py:360  RuntimeError(cmd + tail)
        |
        v
  scripts/provision.py:80   emit({"type":"error","error":str(exc)})
        |
        v
  main.js:285  last = frame     ->  main.js:295  reject(new Error(last.error))
        |
        v
  the dialog, the email body, and the analytics event
```

**Also fix `main.js:78`.** It reads `.slice(0, 500)` — a *leading* slice — so it
already truncates the tail of every error before analytics sees it. Left alone,
we would capture the cause at `:360` and discard it again three hops later.
Change to `.slice(-500)`. This bug is **pre-existing**; it is not introduced by
this design, but this design makes it matter. (Kimi finding 5.)

---

## The failure dialog

Uses `dialog.showMessageBox` from the main process. Not an HTML dialog in the
setup window: `setup-preload.js` exposes only one-way listeners
(`onProgress` / `onStep` / `onDone` / `onError`), so buttons would need a new
renderer-to-main channel — a new trust boundary opened to render three buttons
on a screen seen once.

```
  main.js  ensureProvisioned()                                 :356
    |
    |  + attempts = readAttempts(); ++; write
    |      userData/provision-attempts.json
    |
    :383  relay(phase), frame.type === 'log'
    |       setupSend('setup:progress', frame.line)            unchanged
    |     + provisionLog.push(line)        (ring, 200 lines)
    |     + appendFileSync(logPath, line)  <- so a RELAUNCH still has a log
    |
    :394  runProvision(['--core'], relay('core'))
    :409  catch (err)
    :410    setupSend('setup:error', message)                  unchanged
    :411    throw err
              |
              v
  main.js  boot() catch                                        :652
    :654    fatal('OpenNolan failed to start', ...)
            ---------------------------------------           REPLACED
            provisionFailureDialog({ err, attempts, logPath })
              |
              |-- attempts === 1
              |     "Something went wrong while downloading the tools
              |      OpenNolan needs. We recommend trying again once."
              |
              |-- attempts  >  1
              |     "Setup failed again. Since you have already retried,
              |      please reach out to the developer directly by email."
              |
              '-- buttons: [ Try Again | Email the developer | Quit ]
                                |             |
                                |             '-> shell.openExternal(mailto:)
                                '-> re-run ensureProvisioned() in place
```

Why the ring buffer alone is not enough: it lives in main-process memory, so
after a force-quit and relaunch it is empty — which is precisely when
`attempts > 1` fires and the user is most likely to click Email. Appending each
line to the log file as it arrives makes the file the source of truth.
(Kimi finding 6.)

### The email

```
  to       feedback@opennolan.com
  subject  OpenNolan setup failed (0.2.0, attempt N)
  body     app version / macOS version / arch
           the failing command
           last ~40 log lines           <- capped ~1500 chars encoded
           full log: ~/Library/Application Support/opennolan-desktop/
                     logs/setup-failure-<timestamp>.log
```

`mailto:` has no attachment parameter and clients truncate long bodies without
saying so — cutting exactly the last lines, which are the useful ones. So the
body carries a bounded tail and *names the path* to the complete log.

The address is a literal string compiled into every shipped copy. It cannot be
an env var like `FEEDBACK_TO` in `server/feedback.py`, and the existing Resend
relay cannot be the primary path here because it runs *in the backend* — which
is the thing that failed to start.

---

## Auto-update: CORRECTION — already done, no work needed

**An earlier revision of this document was wrong.** It claimed `web/src` had no
component listening on the update bridge, and put "port `UpdateBanner.jsx`
forward" in scope. That is false against the working tree.

`web/src/UpdateBanner.jsx` and its test already exist on `main`. They were ported
forward in `ae57d47` (the design-token work), adapted to the current tokens, and
mounted in `web/src/App.jsx`. The component calls `getState()` on mount *and*
subscribes to `onDownloaded`. Its 3 tests pass. Nothing to build.

How the error happened, so it is not repeated: the check grepped `web/src` for
the raw IPC channel strings (`update:downloaded`, `update:get-state`). Those
appear **only** in `desktop/preload.js` and `desktop/main.js`, where the bridge
is *defined*. The renderer consumes the exposed API — `window.openNolan.update.*`
— so it could never have matched, and the empty result was read as "nothing
listens". The same too-narrow grep made the shipped-bundle check a false negative.

The lesson is the one in this repo's own rules: grep for the thing the caller
actually uses, and treat an empty result as "my search was wrong" until proven
otherwise.

**Release-time decision (no code).** `main.js:206` sets `autoDownload` but leaves
`allowPrerelease` at its default of false, so the updater **ignores any release
marked as a pre-release** — and `v0.1.0-beta` was published as one. Decision
taken: always publish **normal** releases and leave `allowPrerelease` unset. This
is the one item in this design enforced by discipline rather than by the build,
so it is written into `docs/RELEASE-mac.md` as a release step.

---

## What we are deliberately NOT building

- **A fully offline app.** The composition tier (`npm ci`,
  `lib/provision.py:623`) and the capability packs stay network-bound. Only the
  core path — everything needed to reach the editor — goes offline.
- **Bundled ffmpeg.** It would remove the third network dependency for about
  +80 MB, but the static arm64 builds are GPL and shipping them inside a signed
  app carries source-offer obligations. That is a licensing decision, not an
  architecture one. We pin the URL and fill the sha256 now.
- **A separate lockfile.** The vendored wheel directory *is* the lock: under
  `--offline --no-cache --find-links`, the resolver can only install what we
  shipped, which pins all 64 packages including the 50 transitive ones. Changing
  `>=` to `==` in `requirements*.txt` would pin only the 14 declared.
- **Fixing `.github/workflows/release-mac.yml`.** Descoped by the user. It
  cannot currently produce a dmg and has no signing gate, but nothing uses it —
  releases ship from the developer's Mac via `scripts/build-dmg.sh --publish`.
(The auto-update UI *was* listed here. It is now in scope — see the section
above.)

---

## What would change our mind

| Decision | Reverses if |
| --- | --- |
| Vendor the wheels | The set exceeds ~400 MB (torch enters core), or Intel support is needed — two platform tags roughly double it |
| `ensurepip` for pip | A capability pack needs a pip newer than the bundled CPython ships |
| Strict `--offline` | A core dependency ever ships arm64 as sdist only |
| Wheels dir as the lock | The wheels get committed to git — 113 MB of binaries in history would justify a real lockfile plus a fetch step |
| Native dialog | The setup window needs two or more interactive controls (a proxy field, a mirror picker) |
| Pin ffmpeg, don't bundle | The host goes down or rate-limits |

---

## Open questions

1. ~~**What is the real minimum macOS?**~~ **DECIDED: raise to 14.0.** numpy's
   only current arm64 wheel needs macOS 14, so `minimumSystemVersion` becomes
   `14.0` and we vendor current wheels rather than pinning numpy backwards. Every
   Apple Silicon Mac can run 14, and the app is pre-launch, so the excluded
   population is people who have not updated since 2023.
2. **ffmpeg GPL.** Bundling completes the offline story; the licensing
   obligations are unresolved and are the user's call. Still open.
3. ~~**Publish as a normal release or set `allowPrerelease`?**~~ **DECIDED:
   normal releases only**, `allowPrerelease` stays unset, enforced by a step in
   `docs/RELEASE-mac.md`.
4. ~~**Wheels in git, or fetched at build time?**~~ **RESOLVED by precedent:
   fetched.** `desktop/.gitignore` line 4 already ignores `resources/`, which is
   how the bundled python, uv and node are handled. Wheels land in the same place
   and follow the same rule — downloaded by the build step, never committed. No
   new decision was needed.
5. **The tester's actual root cause is still unknown.** PyPI-unreachable was the
   leading hypothesis until evidence appeared against it: an unreachable index
   prints six lines beginning `Using CPython`, and his log showed none of them.
   The `:360` fix exists so this is never unanswerable again.

---

## Verification notes

- 64 wheels / 113 MB, `uv --offline` semantics, `uv venv` producing no pip, and
  `ensurepip` installing pip 25.0.1 offline were all **measured** this session by
  running the shipped binaries, not inferred.
- uv returns **exit code 2 for every failure mode** — usage error, unreachable
  index, bad `--python` path, unwritable target. The exit code carries no
  information; do not branch on it.
- Every `file:line` above was read from the **shipped v0.2.0 build** (the
  extracted `app.asar` and `Resources/backend/`), because macOS is currently
  denying reads under `~/Documents` — `git` cannot read the working tree. Same
  source, same commit, but re-check the line numbers against the working tree
  before editing.
