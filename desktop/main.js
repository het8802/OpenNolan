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

const { app, BrowserWindow, dialog, shell, session } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const fs = require('node:fs');

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

function fatal(title, message) {
  if (fatalShown) return;
  fatalShown = true;
  shuttingDown = true;
  stopBackend();
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

// Auto-update (packaged builds only). electron-updater checks the GitHub Releases feed (build.publish),
// downloads a newer SIGNED build, and installs on quit. Lazy-required so a dev machine without the dep
// (or without a packaged build) never touches it; NEVER runs in dev.
function initAutoUpdate() {
  if (!app.isPackaged) return;
  try {
    const { autoUpdater } = require('electron-updater');
    autoUpdater.autoDownload = true;
    autoUpdater.on('error', (e) => console.error('[updater] ' + (e && e.message)));
    autoUpdater.on('update-available', (i) => console.log('[updater] update available: ' + (i && i.version)));
    autoUpdater.on('update-downloaded', (i) => console.log('[updater] downloaded, will install on quit: ' + (i && i.version)));
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
  return {
    ...process.env,
    OPENNOLAN_HOME: home,
    OPENNOLAN_CODE_ROOT: codeRoot(),
    OPENNOLAN_PYTHON: bundledPython(), // the base interpreter the venv is built from
    OPENNOLAN_UV: uvBin(),
  };
}

// Run scripts/provision.py with the bundled interpreter, parsing its NDJSON stdout. `onFrame` gets
// each {type:'log'|'doctor'|'done'|'error', ...}. Resolves with the last frame; rejects on failure.
function runProvision(args, onFrame) {
  return new Promise((resolve, reject) => {
    const script = path.join(codeRoot(), 'scripts', 'provision.py');
    const child = spawn(bundledPython(), [script, ...args],
      { cwd: codeRoot(), env: provisionEnv(), stdio: ['ignore', 'pipe', 'pipe'] });
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
    child.on('error', reject);
    child.on('exit', (code) => (code === 0
      ? resolve(last)
      : reject(new Error((last && last.error) || ('provisioning exited ' + code)))));
  });
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

// Ensure the managed venv + core deps exist before the backend starts. Packaged only (dev has the
// repo .venv). Shows a setup window streaming pip progress on first run; no-op once provisioned.
async function ensureProvisioned() {
  if (!app.isPackaged) return;
  let doc = null;
  try { const f = await runProvision(['--doctor']); doc = f && f.doctor; } catch (_) { /* provision below */ }
  if (doc && doc.core_ok) return; // venv present + current

  await createSetupWindow();
  try {
    await runProvision(['--core'], (frame) => {
      if (setupWin && frame.type === 'log') setupWin.webContents.send('setup:progress', frame.line);
    });
    if (setupWin) setupWin.webContents.send('setup:done');
    await new Promise((r) => setTimeout(r, 600)); // let the user read "done" before we swap windows
  } catch (err) {
    if (setupWin) setupWin.webContents.send('setup:error', String((err && err.message) || err));
    throw err; // boot()'s catch surfaces the fatal dialog
  } finally {
    if (setupWin) { setupWin.close(); setupWin = null; }
  }
}

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
    // Downloaded ffmpeg/ffprobe live in runtime/bin — put them on PATH so shutil.which() finds them.
    runtimeEnv.PATH = path.join(home, 'runtime', 'bin') + path.delimiter + (process.env.PATH || '');
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
function applyCsp() {
  if (DEV) return;
  session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
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
    } else {
      if (!fs.existsSync(webDistIndex())) {
        return fatal('UI not built', 'The web UI has not been built.\n\nRun:\n  npm --prefix web run build\n\nthen start the app again. (`npm start` does this automatically.)');
      }
      await ensureProvisioned(); // first run (packaged): build the venv + core deps + ffmpeg before the backend
      backendPort = await freePort();
      backend = startBackend(backendPort);
      await waitForHealth(backendPort);
      createWindow(rendererUrl());
      initAutoUpdate();
    }
  } catch (err) {
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

app.whenReady().then(boot);

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0 && backendPort) createWindow(rendererUrl());
});

// Single-purpose tool: quit (and stop the backend) when the window closes, macOS included.
app.on('window-all-closed', () => { shuttingDown = true; stopBackend(); app.quit(); });
app.on('before-quit', () => { shuttingDown = true; stopBackend(); });
process.on('exit', stopBackend);
