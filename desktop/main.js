'use strict';

// OpenNolan desktop shell (M1).
//
// Thin Electron wrapper: spawn the existing FastAPI backend as a child process,
// wait until it's healthy, then open a window that loads the UI *over http*
// from that same backend. Same-origin means the frontend's relative `/api`
// calls work with no CORS and none of the file:// asset/fetch breakage.
//
// Prod (`npm start`):  backend on a free port serves web/dist -> window loads http://127.0.0.1:<port>
// Dev  (`npm run dev`): window loads Vite on http://localhost:5173 (Vite proxies /api -> :8000);
//                       reuses an already-running backend on :8000, else spawns one.

const { app, BrowserWindow, dialog, shell, session, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const http = require('node:http');
const https = require('node:https');
const net = require('node:net');
const fs = require('node:fs');
const os = require('node:os');

const REPO_ROOT = path.resolve(__dirname, '..');
const DEV = process.env.ELECTRON_DEV === '1';

// Read-only backend/code tree. Dev: the git checkout (repo root). Packaged: the extraResources
// 'backend' dir the electron-builder config emits (Contents/Resources/backend). Branch on
// app.isPackaged — NOT DEV — because process.resourcesPath is only meaningful in a packaged app.
function codeRoot() {
  return app.isPackaged ? path.join(process.resourcesPath, 'backend') : REPO_ROOT;
}
// The built UI ships INSIDE the backend tree (Resources/backend/web/dist) because the FastAPI
// backend serves it from code_root()/web/dist (server/app.py:717). Keep these in lockstep.
function webDistIndex() {
  return path.join(codeRoot(), 'web', 'dist', 'index.html');
}

let backend = null;        // uvicorn child process (null if we don't own one)
let backendPort = null;
let mainWindow = null;
let shuttingDown = false;
let backendDead = false;   // set when our child exits, so health-wait can bail fast
let fatalShown = false;    // show at most one fatal dialog
const stderrTail = [];     // ring buffer of recent backend stderr lines (for diagnostics)

// ── Crash reporting (Electron layer) ──────────────────────────────────────────
// The backend's PostHog reporter can't see main-process crashes, renderer "aw snap"s, or the case
// that matters most for a packaged app: the backend NEVER BECOMING HEALTHY (bad venv/ffmpeg — see
// docs/plans/publish-mac-app.md). Those must report even though /api is unreachable, so we POST
// straight to PostHog. Public write-only key (same as server/analytics.py); opt-out is honored by
// reading the SAME settings.json the Python side writes; the home dir is scrubbed from message+stack.
const POSTHOG_KEY = process.env.POSTHOG_KEY || 'phc_s9P9JiTbBgmzqYGwug8ciiLnWsCSJF62Vz5UGRJsPGBE';
const POSTHOG_HOST = process.env.POSTHOG_HOST || 'https://us.i.posthog.com';
let errorsSent = 0;

function scrubText(s) {
  let t = String(s == null ? '' : s);
  try { const home = os.homedir(); if (home) t = t.split(home).join('~'); } catch (_) { /* best effort */ }
  // Same prefixes as server/analytics.py _PATH_RE, so both reporters redact absolute paths alike
  // (home dir is already collapsed to ~ above; this catches other users' + /var //private //tmp paths).
  return t.replace(/(\/Users\/|\/home\/|\/var\/|\/private\/|\/tmp\/)[^\s]*/g, '[path]');
}

function reportDesktopError(source, err) {
  try {
    if (!app.isPackaged) return;   // dev crashes surface in the terminal — keep the inbox = real users
    if (errorsSent >= 20) return;  // never let a crash loop flood ingestion
    // Same settings.json app_paths.home() reads (packaged: OPENNOLAN_HOME === userData). Missing file
    // => opted in (the default). ponytail: in dev these paths can differ, but dev is gated out above.
    let settings = {};
    try {
      const home = process.env.OPENNOLAN_HOME || app.getPath('userData');
      settings = JSON.parse(fs.readFileSync(path.join(home, 'settings.json'), 'utf8')) || {};
    } catch (_) { /* no/corrupt settings → default opted-in */ }
    if (settings.analytics_disabled) return;
    errorsSent++;
    const message = scrubText((err && err.message) || err).slice(0, 500);
    const stack = scrubText((err && err.stack) || '').slice(0, 8000);
    // Same internal-machine marker as server/analytics.py so the developer's own crashes filter out.
    let internal = false;
    try {
      const flag = (process.env.OPENNOLAN_INTERNAL || '').trim().toLowerCase();
      internal = (!!flag && !['0', 'false', 'no'].includes(flag))
        || fs.existsSync(path.join(os.homedir(), '.opennolan-internal'));
    } catch (_) { /* default: not internal */ }
    const body = JSON.stringify({
      api_key: POSTHOG_KEY,
      event: 'desktop_error',
      distinct_id: settings.device_id || 'desktop-unknown',
      properties: {
        source, message, stack, app_version: app.getVersion(),
        os: process.platform, arch: process.arch, packaged: true,
        env: 'packaged', internal,
      },
    });
    const u = new URL('/capture/', POSTHOG_HOST);
    const req = https.request(
      { method: 'POST', hostname: u.hostname, path: u.pathname,
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
        timeout: 4000 },
      (res) => { res.on('data', () => {}); res.on('end', () => {}); },
    );
    req.on('error', () => {});
    req.on('timeout', () => { try { req.destroy(); } catch (_) { /* gone */ } });
    req.write(body);
    req.end();
  } catch (_) { /* reporting must NEVER throw into a crash path */ }
}

function fatal(title, message) {
  if (fatalShown) return;
  fatalShown = true;
  // Report before the dialog — this fires for "backend won't start / exited", the crash most likely
  // to lose a new user. Title = the grouping message; message (incl. backend stderr tail) = detail.
  reportDesktopError('fatal', { message: title, stack: message });
  shuttingDown = true;
  stopProvision();
  stopBackend();
  // If the setup window is still up (e.g. the backend died after a first-run install), flip it
  // into its error state — a green bar creeping behind a fatal dialog reads as a lie.
  setupSend('setup:error', message);
  dialog.showErrorBox(title, message);
  app.quit();
}

// The BASE interpreter used to PROVISION the managed venv (Lane E). Packaged: the bundled, signed
// python-build-standalone (Resources/python/bin/python3). Dev: repo .venv, then PATH.
function bundledPython() {
  if (app.isPackaged) return path.join(process.resourcesPath, 'python', 'bin', 'python3');
  const venv = path.join(REPO_ROOT, '.venv', 'bin', 'python');
  if (fs.existsSync(venv)) return venv;
  return 'python3';
}

// The managed venv's interpreter (built at first run into OPENNOLAN_HOME/runtime/venv) — this is the
// one with fastapi/uvicorn/etc. installed.
function venvPython() {
  const home = process.env.OPENNOLAN_HOME || app.getPath('userData');
  return path.join(home, 'runtime', 'venv', 'bin', 'python');
}

// The bundled uv binary (fast installer). Dev: fall through to a PATH uv.
function uvBin() {
  if (app.isPackaged) return path.join(process.resourcesPath, 'uv', 'uv');
  return process.env.OPENNOLAN_UV || 'uv';
}

// The bundled Node runtime dir (OPN-3) — the composition engines (Remotion + HyperFrames) run on it.
// Packaged: Resources/node (from fetch-node.mjs). Dev: unset -> provision.py falls back to PATH node.
function nodeDir() {
  return app.isPackaged ? path.join(process.resourcesPath, 'node') : null;
}
// The bundled node binary, or null in dev (PATH node is used instead).
function nodeBin() {
  const dir = nodeDir();
  return dir ? path.join(dir, 'bin', 'node') : null;
}

// Which Python runs the BACKEND. Explicit override wins. Packaged: the provisioned venv python if it
// exists (ensureProvisioned() guarantees this before startBackend), else the bundled base as a
// bootstrap fallback. Dev: repo .venv, then PATH — unchanged.
function pythonBin() {
  if (process.env.OPENNOLAN_PYTHON) return process.env.OPENNOLAN_PYTHON;
  if (app.isPackaged) {
    const vp = venvPython();
    return fs.existsSync(vp) ? vp : bundledPython();
  }
  const venv = path.join(REPO_ROOT, '.venv', 'bin', 'python');
  if (fs.existsSync(venv)) return venv;
  return 'python3';
}

// A downloaded-and-staged update ({version}), or null. Held at module scope so the renderer can
// re-hydrate the banner after a ⌘R reload via the 'update:get-state' handler (the push event only
// fires once, when the download finishes).
let pendingUpdate = null;

// Auto-update (packaged builds only). electron-updater checks the GitHub Releases feed (build.publish),
// auto-downloads a newer SIGNED build, and — with autoInstallOnAppQuit (default true) — installs it on
// the next quit. On top of that silent path we surface an in-app "update ready" banner (lower-left) so
// the user can restart-and-install NOW: on 'update-downloaded' we push to the renderer, and the banner's
// button invokes 'update:install' → quitAndInstall(). Lazy-required so a dev machine without the dep (or
// without a packaged build) never touches it; the REAL updater never runs in dev — only the
// OPENNOLAN_FAKE_UPDATE test hook below does.
function initAutoUpdate() {
  // Dev/manual test hook: OPENNOLAN_FAKE_UPDATE=<version> (or =1) skips the real updater and instead
  // wires the SAME IPC channels + pushes a fake "downloaded" event, so the in-app banner can be seen
  // and clicked in `npm run dev` without a signed build or a published release. install() only logs.
  const fakeVersion = process.env.OPENNOLAN_FAKE_UPDATE;
  if (fakeVersion) {
    pendingUpdate = { version: fakeVersion === '1' ? '0.0.0-test' : fakeVersion };
    ipcMain.handle('update:get-state', () => pendingUpdate);
    ipcMain.handle('update:install', () => { console.log('[updater] (fake) would restart & install ' + pendingUpdate.version); return true; });
    // Delay the push so the renderer has mounted its listener (getState() covers the earlier window).
    setTimeout(() => {
      try { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('update:downloaded', pendingUpdate); }
      catch (_) { /* window gone */ }
    }, 2000);
    console.log('[updater] fake update armed: ' + pendingUpdate.version);
    return;
  }
  if (!app.isPackaged) return;
  try {
    const { autoUpdater } = require('electron-updater');
    autoUpdater.autoDownload = true;
    autoUpdater.on('error', (e) => console.error('[updater] ' + (e && e.message)));
    autoUpdater.on('update-available', (i) => console.log('[updater] update available: ' + (i && i.version)));
    autoUpdater.on('update-downloaded', (i) => {
      pendingUpdate = { version: (i && i.version) || null };
      console.log('[updater] downloaded, ready to install: ' + pendingUpdate.version);
      try {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('update:downloaded', pendingUpdate);
      } catch (_) { /* window went away between the check and the send */ }
    });
    // Renderer asks on mount (and after a reload) whether an update is already staged.
    ipcMain.handle('update:get-state', () => pendingUpdate);
    // "Restart & update" clicked — install the staged build now. quitAndInstall() triggers before-quit,
    // which stops the backend; set shuttingDown first so its exit handler doesn't misfire the fatal
    // "backend stopped" dialog. No-op (returns false) if nothing is staged.
    ipcMain.handle('update:install', () => {
      if (!pendingUpdate) return false;
      shuttingDown = true;
      setImmediate(() => { try { autoUpdater.quitAndInstall(); } catch (e) { console.error('[updater] install failed: ' + (e && e.message)); } });
      return true;
    });
    // MUST .catch(): checkForUpdatesAndNotify() returns a promise that REJECTS on any feed failure
    // (e.g. the build.publish owner/repo placeholders 404 until they're set). The 'error' event
    // listener does NOT consume this promise, so without the catch every packaged launch throws an
    // unhandledRejection. Self-update also only works on a SIGNED build (Squirrel.Mac verifies the
    // signature); on an unsigned interim build the download silently won't install — expected.
    autoUpdater.checkForUpdatesAndNotify().catch((e) => console.error('[updater] check failed: ' + (e && e.message)));
  } catch (e) {
    console.error('[updater] disabled: ' + (e && e.message));
  }
}

// ── first-run provisioning (Lane E) ───────────────────────────────────────────
// The bundled interpreter has no packages, so on first run we build a managed venv (uv) + install
// core deps + ffmpeg into ~/Library/Application Support/OpenNolan/runtime, driven by the bundled
// python running scripts/provision.py. Only packaged builds provision; dev uses the repo .venv.

function provisionEnv() {
  const home = process.env.OPENNOLAN_HOME || app.getPath('userData');
  const env = {
    ...process.env,
    OPENNOLAN_HOME: home,
    OPENNOLAN_CODE_ROOT: codeRoot(),
    OPENNOLAN_PYTHON: bundledPython(), // the base interpreter the venv is built from
    OPENNOLAN_UV: uvBin(),
  };
  // Composition tier (OPN-3): point provision.py at the bundled node + put its bin on PATH so the
  // sibling npm/npx resolve. Dev leaves these unset (provision.py falls back to a PATH node).
  const nb = nodeBin();
  if (nb) {
    env.OPENNOLAN_NODE = nb;
    env.PATH = path.join(nodeDir(), 'bin') + path.delimiter + (process.env.PATH || '');
  }
  return env;
}

// Run scripts/provision.py with the bundled interpreter, parsing its NDJSON stdout. `onFrame` gets
// each {type:'log'|'step'|'doctor'|'done'|'error', ...}. Resolves with the last frame; rejects on failure.
let provisionChild = null; // in-flight provision.py process (killed if the user cancels setup)
function runProvision(args, onFrame) {
  return new Promise((resolve, reject) => {
    const script = path.join(codeRoot(), 'scripts', 'provision.py');
    // detached:true makes the child a process-GROUP leader, so stopProvision can signal the whole
    // group — a bare kill of python would orphan a live uv/npm grandchild that keeps writing into
    // runtime/*.building after the app quit (and races the next launch's re-provision).
    const child = spawn(bundledPython(), [script, ...args],
      { cwd: codeRoot(), env: provisionEnv(), stdio: ['ignore', 'pipe', 'pipe'], detached: true });
    provisionChild = child;
    let buf = '';
    let last = null;
    child.stdout.on('data', (d) => {
      buf += d.toString();
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const raw = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!raw) continue;
        let frame;
        try { frame = JSON.parse(raw); } catch (_) { frame = { type: 'log', line: raw }; }
        last = frame;
        if (onFrame) onFrame(frame);
      }
    });
    child.stderr.on('data', (d) => { if (onFrame) onFrame({ type: 'log', line: String(d).trimEnd() }); });
    child.on('error', (err) => { if (provisionChild === child) provisionChild = null; reject(err); });
    child.on('exit', (code) => {
      if (provisionChild === child) provisionChild = null;
      return code === 0
        ? resolve(last)
        : reject(new Error((last && last.error) || ('provisioning exited ' + code)));
    });
  });
}

// Kill an in-flight provision run (user closed the setup window = cancel). Signals the process
// GROUP (python + uv/npm/node grandchildren — see the detached spawn above) with a SIGKILL
// escalation, mirroring stopBackend. Provisioning is atomic on the Python side (venv.building /
// <engine>.building + os.replace), so a kill never leaves a half-install the next launch would
// trust — it just re-runs.
function stopProvision() {
  const child = provisionChild;
  provisionChild = null;
  if (!child) return;
  const killGroup = (sig) => {
    try { process.kill(-child.pid, sig); } // negative pid = the whole process group
    catch (_) { try { child.kill(sig); } catch (_) { /* already gone */ } }
  };
  killGroup('SIGTERM');
  setTimeout(() => killGroup('SIGKILL'), 3000);
}

let setupWin = null;
function createSetupWindow() {
  return new Promise((resolve) => {
    setupWin = new BrowserWindow({
      width: 640, height: 480, title: 'Setting up OpenNolan', backgroundColor: '#FBF7F0', resizable: false,
      webPreferences: {
        preload: path.join(__dirname, 'setup-preload.js'),
        contextIsolation: true, nodeIntegration: false, sandbox: true,
      },
    });
    setupWin.on('closed', () => { setupWin = null; });
    setupWin.webContents.once('did-finish-load', () => resolve());
    setupWin.loadFile(path.join(__dirname, 'setup.html'));
  });
}

// Send to the setup window, tolerating a mid-flight close (user cancel destroys the webContents).
function setupSend(channel, payload) {
  try {
    if (setupWin && !setupWin.isDestroyed()) setupWin.webContents.send(channel, payload);
  } catch (_) { /* window went away between the check and the send */ }
}

// Rough share of first-run wall-clock per phase, used to map each provision run's OWN 0-100 step
// frames onto ONE global setup bar. 'backend' = starting uvicorn after install (cold venv import).
const SETUP_WEIGHTS = { core: 58, composition: 36, backend: 6 };

// Ensure the managed venv + core deps + composition engines exist before the backend starts. Packaged
// only (dev has the repo .venv + `make setup`). Shows a setup window streaming install progress on first
// run; no-op once provisioned.
//
// CORE (venv + ffmpeg) is REQUIRED — a failure is fatal (the backend can't `import fastapi` without it).
// COMPOSITION (Node engines) is BEST-EFFORT (OPN-3): install it eagerly in the same window, but a failure
// only WARNS and lets the editor open on the ffmpeg-only path. Never brick the app because Remotion or a
// headless-browser download failed on a flaky network.
//
// On success the setup window is left OPEN — boot() keeps it up through backend start and closes it
// only after the main window exists (handoffFromSetup). Closing it here would leave a zero-window
// moment, which fires 'window-all-closed' and quits the app right after first-run setup.
async function ensureProvisioned() {
  if (!app.isPackaged) return;
  let doc = null;
  try { const f = await runProvision(['--doctor']); doc = f && f.doctor; } catch (_) { /* provision below */ }
  if (shuttingDown) return; // Cmd+Q during the doctor probe — don't start a pointless install mid-quit
  const coreReady = !!(doc && doc.core_ok);
  const compositionReady = !!(doc && doc.composition_ok);
  if (coreReady && compositionReady) return; // everything present + current

  // Plan the phases we'll actually run and give each a weighted slice [g0,g1] of the global bar.
  const phases = [];
  if (!coreReady) phases.push('core');
  if (!compositionReady) phases.push('composition');
  phases.push('backend');
  const totalW = phases.reduce((s, p) => s + SETUP_WEIGHTS[p], 0);
  const seg = {};
  let acc = 0;
  for (const p of phases) {
    const w = (SETUP_WEIGHTS[p] / totalW) * 100;
    seg[p] = [acc, acc + w];
    acc += w;
  }

  await createSetupWindow();
  const sendStep = (pct, end, label) => setupSend('setup:step', { pct, end, label });
  // Per-phase frame relay: logs pass through; step frames are remapped from the run's own
  // 0-100 scale into the phase's global slice.
  const relay = (phase) => (frame) => {
    if (frame.type === 'log') {
      setupSend('setup:progress', frame.line);
    } else if (frame.type === 'step') {
      const [g0, g1] = seg[phase];
      const map = (v) => g0 + (Math.max(0, Math.min(100, Number(v) || 0)) / 100) * (g1 - g0);
      sendStep(map(frame.pct), map(frame.end == null ? frame.pct : frame.end), frame.label || '');
    }
  };
  try {
    if (!coreReady) {
      await runProvision(['--core'], relay('core')); // fatal on failure (caught below)
    }
    if (!compositionReady) {
      // Best-effort: swallow failures so a broken Remotion/HyperFrames install never blocks the editor.
      try {
        await runProvision(['--composition'], relay('composition'));
      } catch (compErr) {
        if (shuttingDown) throw compErr; // user cancelled — don't misread the kill as an engine failure
        console.error('[provision] composition tier failed (non-fatal): ' + (compErr && compErr.message));
        setupSend('setup:progress', 'Video engines unavailable — you can retry later from Settings.');
        sendStep(seg.composition[1], seg.composition[1], 'Video engines skipped.');
      }
    }
    setupSend('setup:done', undefined);
    sendStep(seg.backend[0], 99, 'Starting OpenNolan…'); // backend start = the last slice of the bar
  } catch (err) {
    setupSend('setup:error', String((err && err.message) || err));
    throw err; // boot()'s catch surfaces the fatal dialog (core-only)
  }
}

// Close the setup window only AFTER the main window's content has loaded (or a short fallback),
// so there is never a zero-window gap for 'window-all-closed' to turn into a quit.
function handoffFromSetup() {
  if (!setupWin) return;
  sendStep100();
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    if (setupWin) { setupWin.close(); setupWin = null; }
  };
  if (mainWindow) {
    mainWindow.webContents.once('did-finish-load', finish);
    setTimeout(finish, 4000); // fallback: never leave the setup window hanging around
  } else {
    finish();
  }
}
function sendStep100() { setupSend('setup:step', { pct: 100, end: 100, label: 'Ready.' }); }

// Grab an OS-assigned free port (prod). Small TOCTOU window before uvicorn binds —
// acceptable on a single-user Mac; a bind failure surfaces the real stderr below.
function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

// One-shot health probe -> resolves true/false (never rejects).
function probeHealth(port, timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/api/health', timeout: timeoutMs }, (res) => {
      let body = '';
      res.on('data', (c) => (body += c));
      res.on('end', () => {
        try { resolve(res.statusCode === 200 && JSON.parse(body).status === 'ok'); }
        catch (_) { resolve(false); }
      });
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

// Poll until healthy, the deadline passes, or our backend child dies.
function waitForHealth(port, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = async () => {
      if (backendDead) return reject(new Error('backend process exited before becoming healthy'));
      if (await probeHealth(port)) return resolve(port);
      if (Date.now() > deadline) return reject(new Error('backend did not become healthy within ' + Math.round(timeoutMs / 1000) + 's'));
      setTimeout(tick, 300);
    };
    tick();
  });
}

function recordStderr(buf) {
  const text = String(buf);
  process.stderr.write('[backend] ' + text);
  for (const line of text.split('\n')) {
    if (line.trim()) { stderrTail.push(line); if (stderrTail.length > 25) stderrTail.shift(); }
  }
}

function startBackend(port) {
  const bin = pythonBin();
  const CODE_ROOT = codeRoot();
  const args = ['-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', String(port)];
  // Where the backend reads CODE vs writes DATA. Dev (from the checkout): repo-relative, unchanged.
  // Packaged (read-only .app): CODE_ROOT is the bundled backend tree (Resources/backend, on cwd so
  // lib.*/server.*/tools.* import), and user data (projects, BYOK .env, the managed venv, caches)
  // goes to ~/Library/Application Support via OPENNOLAN_HOME. lib/app_paths.py reads these vars;
  // see docs/plans/publish-mac-app.md.
  const runtimeEnv = { ...process.env };
  if (app.isPackaged) {
    const home = process.env.OPENNOLAN_HOME || app.getPath('userData');
    runtimeEnv.OPENNOLAN_HOME = home;
    runtimeEnv.OPENNOLAN_CODE_ROOT = CODE_ROOT;
    runtimeEnv.OPENNOLAN_PROJECTS_DIR = process.env.OPENNOLAN_PROJECTS_DIR || path.join(home, 'projects');
    runtimeEnv.OPENNOLAN_UV = uvBin();       // so the backend can install capability packs on demand
    // Composition tier (OPN-3): the bundled node runs Remotion/HyperFrames; expose it + its bin dir so
    // shutil.which('node'/'npx') and provision.node_bin() resolve, and route the engines' browser cache
    // into the writable runtime. Order the PATH: ffmpeg (runtime/bin) > node > system.
    runtimeEnv.OPENNOLAN_NODE = nodeBin();
    const browsersCache = path.join(home, 'runtime', 'composition', 'browsers');
    runtimeEnv.REMOTION_BROWSER_CACHE = browsersCache;
    runtimeEnv.PUPPETEER_CACHE_DIR = browsersCache;
    runtimeEnv.PLAYWRIGHT_BROWSERS_PATH = browsersCache;
    // Downloaded ffmpeg/ffprobe live in runtime/bin; the bundled node lives in Resources/node/bin — put
    // both on PATH so shutil.which() finds them.
    runtimeEnv.PATH = path.join(home, 'runtime', 'bin') + path.delimiter
      + path.join(nodeDir(), 'bin') + path.delimiter + (process.env.PATH || '');
  } else {
    runtimeEnv.OPENNOLAN_PROJECTS_DIR =
      process.env.OPENNOLAN_PROJECTS_DIR || path.join(REPO_ROOT, 'projects');
  }
  const child = spawn(bin, args, {
    cwd: CODE_ROOT, // dev: repo root (unchanged); packaged: Resources/backend — imports lib.*/server.*/tools.*
    env: runtimeEnv,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.on('data', (d) => process.stdout.write('[backend] ' + d));
  child.stderr.on('data', recordStderr);
  child.on('exit', (code, signal) => {
    backend = null;
    backendDead = true;
    if (!shuttingDown) {
      const tail = stderrTail.slice(-20).join('\n');
      fatal(
        'OpenNolan backend stopped',
        'The local backend exited (code ' + code + ', signal ' + signal + ').\n\n' +
        (tail
          ? 'Last backend output:\n' + tail
          : (app.isPackaged
              ? 'No output captured. Try reopening OpenNolan; if it persists, reinstall the app.'
              : 'No output captured. Check that the repo .venv has the UI deps:\n  pip install -r requirements-ui.txt'))
      );
    }
  });
  return child;
}

function rendererUrl() {
  return DEV ? 'http://localhost:5173' : 'http://127.0.0.1:' + backendPort;
}

// Prod-only CSP (defense-in-depth for a local same-origin app). Skipped in dev
// because Vite HMR needs inline/eval/ws. If the prod UI ever looks unstyled or
// can't load an asset, this policy is the first thing to relax.
//
// SCOPED TO THE http:// APP ORIGIN ONLY. The onHeadersReceived handler is session-wide, so an
// unscoped rewrite also lands on the setup window's file:// load — and this policy has no
// `script-src 'unsafe-inline'`, which silently BLOCKS setup.html's script (that was the "blank
// setup window" bug: the page JS never ran, so no progress ever rendered). file:// setup assets
// carry their own <meta> CSP and load an external setup.js, so we pass them through untouched.
function applyCsp() {
  if (DEV) return;
  session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
    if (!/^https?:/i.test(details.url || '')) {
      cb({}); // file:// (setup window, packaged assets) — leave its own CSP alone
      return;
    }
    cb({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; " +
          "style-src 'self' 'unsafe-inline'; font-src 'self' data:; connect-src 'self'",
        ],
      },
    });
  });
}

function createWindow(url) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    title: 'OpenNolan',
    backgroundColor: '#FBF7F0',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  const appOrigin = new URL(url).origin;

  // Pin the privileged window to its own origin; route real external links out
  // to the system browser instead of loading them in-app.
  mainWindow.webContents.on('will-navigate', (event, navUrl) => {
    try {
      if (new URL(navUrl).origin !== appOrigin) {
        event.preventDefault();
        if (/^https?:/i.test(navUrl)) shell.openExternal(navUrl);
      }
    } catch (_) {
      event.preventDefault();
    }
  });
  mainWindow.webContents.setWindowOpenHandler(({ url: u }) => {
    if (/^https?:/i.test(u)) shell.openExternal(u);
    return { action: 'deny' };
  });

  // In dev, a connection-refused (Vite not running) shows a helpful dialog instead
  // of a raw Chromium error page. -3 is ERR_ABORTED (benign).
  mainWindow.webContents.on('did-fail-load', (_e, code, desc, failedUrl, isMainFrame) => {
    if (!isMainFrame || code === -3) return;
    if (DEV) {
      fatal('Vite dev server not reachable', 'Could not load ' + failedUrl + ' (' + desc + ').\n\nStart the web dev server first:\n  npm --prefix web run dev');
    }
  });

  mainWindow.loadURL(url);
  mainWindow.on('closed', () => { mainWindow = null; });
}

async function boot() {
  try {
    applyCsp();
    if (DEV) {
      backendPort = 8000;
      const alreadyUp = await probeHealth(backendPort);
      if (!alreadyUp) {
        // Nothing healthy on :8000 — own a backend. (If a separate run-dev is
        // already serving :8000, we reuse it and never spawn, so no port clash.)
        backend = startBackend(backendPort);
        await waitForHealth(backendPort).catch(() => { /* surfaced via exit handler / did-fail-load */ });
      }
      createWindow(rendererUrl());
      initAutoUpdate(); // no-op unless OPENNOLAN_FAKE_UPDATE is set (dev test hook); real updater is packaged-only
    } else {
      if (!fs.existsSync(webDistIndex())) {
        return fatal('UI not built', 'The web UI has not been built.\n\nRun:\n  npm --prefix web run build\n\nthen start the app again. (`npm start` does this automatically.)');
      }
      await ensureProvisioned(); // first run (packaged): build the venv + core deps + ffmpeg before the backend
      if (shuttingDown) return;  // cancelled during setup — never spawn a backend mid-quit
      backendPort = await freePort();
      backend = startBackend(backendPort);
      // First run boots a COLD venv (every .pyc compiles on import) — give it longer than a warm start.
      await waitForHealth(backendPort, setupWin ? 90000 : 30000);
      createWindow(rendererUrl());
      handoffFromSetup(); // swap setup -> main with no zero-window gap (else the app quits itself)
      initAutoUpdate();
    }
  } catch (err) {
    if (shuttingDown) return; // user closed the setup window mid-install — a cancel, not a failure
    const tail = stderrTail.slice(-20).join('\n');
    fatal('OpenNolan failed to start', String((err && err.stack) || err) + (tail ? '\n\nBackend output:\n' + tail : ''));
  }
}

function stopBackend() {
  if (backend && !backend.killed) {
    const child = backend;
    try { child.kill('SIGTERM'); } catch (_) { /* already gone */ }
    // Escalate if uvicorn ignores/hangs on SIGTERM.
    setTimeout(() => { try { if (child && !child.killed) child.kill('SIGKILL'); } catch (_) {} }, 3000);
  }
  backend = null;
}

// Report crashes the backend reporter can't see. Report-only (no forced exit) so behavior matches
// today's no-handler default; the process keeps running where Electron would have.
process.on('uncaughtException', (e) => { reportDesktopError('main-uncaught', e); console.error('[main] uncaught: ' + (e && e.stack || e)); });
process.on('unhandledRejection', (reason) => { reportDesktopError('main-rejection', reason); console.error('[main] unhandledRejection: ' + (reason && reason.stack || reason)); });
app.on('render-process-gone', (_e, _wc, d) => reportDesktopError('renderer-gone', { message: 'renderer gone: ' + (d && d.reason), stack: 'exitCode=' + (d && d.exitCode) }));
app.on('child-process-gone', (_e, d) => reportDesktopError('child-gone', { message: (d && d.type || 'child') + ' gone: ' + (d && d.reason), stack: 'exitCode=' + (d && d.exitCode) }));

app.whenReady().then(boot);

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0 && backendPort) createWindow(rendererUrl());
});

// Single-purpose tool: quit (and stop the backend + any in-flight provisioning) when the window
// closes, macOS included. Closing the SETUP window mid-install lands here too — that's a cancel.
app.on('window-all-closed', () => { shuttingDown = true; stopProvision(); stopBackend(); app.quit(); });
app.on('before-quit', () => { shuttingDown = true; stopProvision(); stopBackend(); });
process.on('exit', () => { stopProvision(); stopBackend(); });
