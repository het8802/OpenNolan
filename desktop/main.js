'use strict';

// OpenNolan desktop shell (M1).
//
// Thin Electron wrapper: spawn the existing FastAPI backend as a child process,
// wait until it's healthy, then open a window that loads the UI *over http*
// from that same backend. Same-origin means the frontend's relative `/api`
// calls work with no CORS and none of the file:// asset/fetch breakage.
//
// Prod (`npm start`):  backend on a free port serves web/dist -> window loads http://127.0.0.1:<port>
// Dev  (`npm run dev`): window loads Vite and its API proxy on this worktree's configured ports;
//                       reuses an already-running backend there, else spawns one.

const { app, BrowserWindow, dialog, shell, session, ipcMain } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const http = require('node:http');
const https = require('node:https');
const net = require('node:net');
const fs = require('node:fs');
const os = require('node:os');
const worktreeConfig = require('./worktree-config');

const REPO_ROOT = path.resolve(__dirname, '..');
const DEV = process.env.ELECTRON_DEV === '1';

// The SAME root lib/app_paths.home() resolves to, in BOTH modes. Getting this wrong is not
// cosmetic: dev Python writes settings.json (and the analytics opt-out) to the REPO ROOT, so
// reading Electron's userData in dev makes the shell ignore an opt-out the user actually set.
// app.getPath('userData') is available before `ready` and is stable.
function appHome() {
  return process.env.OPENNOLAN_HOME || (app.isPackaged ? app.getPath('userData') : REPO_ROOT);
}

// Load the SAME .env the Python side reads (lib/env_loader.py -> app_paths.env_path()).
// Environment flows parent -> child ONLY: main.js SPAWNS the backend, so the backend loading
// .env into its own os.environ can never propagate back up here. Without this, POSTHOG_KEY in
// .env pointed only the backend at the dev project while every desktop_error kept going to
// production — and a Finder-launched packaged app inherits no shell env at all, so the
// hardcoded fallback always won there. Does NOT override an already-set var, matching
// python-dotenv, so an explicitly exported key still wins in both processes.
function loadDotenv() {
  try {
    require('dotenv').config({ path: process.env.OPENNOLAN_ENV_FILE || path.join(appHome(), '.env') });
  } catch (_) { /* no .env, or dotenv absent — fall through to process.env + defaults */ }
}
loadDotenv();

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
const DEFAULT_POSTHOG_KEY = 'phc_s9P9JiTbBgmzqYGwug8ciiLnWsCSJF62Vz5UGRJsPGBE';
const POSTHOG_KEY = process.env.POSTHOG_KEY || DEFAULT_POSTHOG_KEY;
const POSTHOG_HOST = process.env.POSTHOG_HOST || 'https://us.i.posthog.com';
// Mirrors server/analytics.py. A harness that must never reach production sets this; with it
// on, a missing POSTHOG_KEY DISABLES reporting instead of falling back to the hardcoded
// production token. It has to cover THIS reporter too, and arguably more than the Python one:
// main.js is what writes when the backend never starts, which is the whole reason it exists.
const NO_DEFAULT_KEY = !(process.env.POSTHOG_KEY || '').trim()
  && !['', '0', 'false'].includes((process.env.OPENNOLAN_ANALYTICS_NO_DEFAULT_KEY || '').trim());
let errorsSent = 0;

// The session id. Minted HERE, in main, not in the renderer: a ⌘R reload rebuilds the whole
// renderer and would split one session in two, and a launch that fails before the UI exists
// would have no id at all. The renderer receives it through preload and puts it on the
// X-ON-Session header, which is how a backend render job ends up joinable to this session.
const SESSION_ID = require('node:crypto').randomUUID();
let sessionStart = Date.now();

// Read from the ONE taxonomy the backend validates against, never a second copy of the enums.
//
// ALL-OR-NOTHING, mirroring server/analytics._merge_taxonomy: a partial merge would silently
// turn valid events into undeclared ones, one family file at a time, with nothing to notice.
// Any defect yields {} — and {} FAILS CLOSED below, exactly as it does in Python.
function loadTaxonomy() {
  const dir = path.join(codeRoot(), 'schemas', 'analytics');
  const files = fs.readdirSync(dir).filter((f) => f.endsWith('.json')).sort();
  if (!files.includes('_envelope.json')) throw new Error('missing _envelope.json');
  const merged = JSON.parse(fs.readFileSync(path.join(dir, '_envelope.json'), 'utf8'));
  merged.events = merged.events || {};
  for (const key of ['schema_version', 'property_types', 'envelope', 'reporter_envelope', 'reserved_substrings']) {
    if (!(key in merged)) throw new Error(`_envelope.json is missing ${key}`);
  }
  for (const f of files) {
    if (f === '_envelope.json') continue;
    const fam = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
    for (const [name, entry] of Object.entries(fam.events || {})) {
      if (name in merged.events) throw new Error(`event ${name} declared twice (${f})`);
      merged.events[name] = entry;
    }
    for (const section of ['enums', 'open_vocabularies']) {
      for (const [k, v] of Object.entries(fam[section] || {})) {
        if (k.startsWith('$')) continue;
        merged[section] = merged[section] || {};
        merged[section][k] = v;
      }
    }
  }
  if (!Object.keys(merged.events).length) throw new Error('merged taxonomy declares no events');
  return merged;
}

const TAXONOMY = (() => {
  try {
    return loadTaxonomy();
  } catch (err) {
    // The only signal there is: fail-closed means no event can carry a counter out.
    console.error(`[analytics/main] TAXONOMY FAILED TO LOAD — ALL events dropped (fail-closed): ${err && err.message}`);
    return {};
  }
})();
const SCHEMA_VERSION = TAXONOMY.schema_version != null ? TAXONOMY.schema_version : null;

// Main's direct events bypassed the backend's gate entirely until now — which put the hole
// exactly where the free-text risk is highest, since `desktop_error` is the one event carrying
// a classified crash and `launch_failure` classifies a local stderr tail. Same rules as
// server/analytics.validate_event: unknown event drops the event, unknown property drops the
// property, and a type-E value outside its declared vocabulary is DENIED BY DEFAULT.
const BOUNDED_TOKEN = /^[A-Za-z0-9_.:/+-]{1,64}$/;
const BUCKET_LABEL = /^(?:0|\d+(?:\.\d+)?)(?:-\d+(?:\.\d+)?|\+)?$/;

function enumOk(event, prop, kind, value) {
  if ((kind !== 'E' && kind !== 'B') || typeof value !== 'string') return true;
  const enums = TAXONOMY.enums || {};
  const allowed = enums[`${event}.${prop}`] || enums[prop];
  if (Array.isArray(allowed)) return allowed.includes(value);
  if (kind === 'B') return BUCKET_LABEL.test(value);
  if (!(prop in (TAXONOMY.open_vocabularies || {}))) return false;
  return BOUNDED_TOKEN.test(value);
}

/** Returns the surviving properties, or null to drop the whole event. */
function validateEvent(event, props) {
  const events = TAXONOMY.events || {};
  if (!Object.keys(events).length) return null;      // FAIL CLOSED
  const entry = events[event];
  if (!entry) return null;
  const declared = entry.properties || {};
  const allowed = new Set([...Object.keys(declared), ...Object.keys(TAXONOMY.envelope || {})]);
  const clean = {};
  for (const [k, v] of Object.entries(props || {})) {
    if (!allowed.has(k)) continue;
    if (!enumOk(event, k, declared[k], v)) continue;
    if (v !== null && typeof v === 'object') continue;  // main sends no nested values
    clean[k] = v;
  }
  return clean;
}

// One line answering "which PostHog project am I writing to". Mirrors server/analytics.py:
// the fallback to the hardcoded production key is SILENT, so a typo'd var name (or a .env
// this process never loaded) writes to production with no error whatsoever.
// A prefix is only safe to print on a well-formed key; slicing a short/malformed value prints
// the whole thing. Mirrors server/analytics.py _key_hint.
function keyHint(key) {
  return key.length >= 24 ? key.slice(0, 12) + '…' : `<malformed key, ${key.length} chars>`;
}

function logAnalyticsDestination() {
  console.log(
    `[analytics/main] ${NO_DEFAULT_KEY ? 'DISABLED (no explicit key; production fallback refused) ' : ''}`
    + `key=${keyHint(POSTHOG_KEY)} host=${POSTHOG_HOST} `
    + `default_key=${POSTHOG_KEY === DEFAULT_POSTHOG_KEY} env=${app.isPackaged ? 'packaged' : 'dev'} `
    + `internal=${isInternal()} session=${SESSION_ID.slice(0, 8)}`,
  );
}

function scrubText(s) {
  let t = String(s == null ? '' : s);
  try { const home = os.homedir(); if (home) t = t.split(home).join('~'); } catch (_) { /* best effort */ }
  // Same prefixes as server/analytics.py _PATH_RE, so both reporters redact absolute paths alike
  // (home dir is already collapsed to ~ above; this catches other users' + /var //private //tmp paths).
  return t.replace(/(\/Users\/|\/home\/|\/var\/|\/private\/|\/tmp\/)[^\s]*/g, '[path]');
}

// Same internal-machine marker as server/analytics.py so the developer's own use filters out.
function isInternal() {
  try {
    const flag = (process.env.OPENNOLAN_INTERNAL || '').trim().toLowerCase();
    return (!!flag && !['0', 'false', 'no'].includes(flag))
      || fs.existsSync(path.join(os.homedir(), '.opennolan-internal'));
  } catch (_) { return false; }
}

// The SAME id server/settings.py device_id() uses: ~/.opennolan/install_id, deliberately
// outside every worktree. If the two reporters disagree here, one Mac reads as two installs
// and every per-install rate is wrong.
//
// It MINTS the id when the file is absent, and that is the point: on a genuine first launch
// the shell emits app_launch_started BEFORE the backend has ever run, so a read-only reporter
// would send 'desktop-unknown' and Python would mint a different id moments later — one
// launch, two installs, exactly at the moment activation is measured. Same `dev-<32 hex>`
// shape and same exclusive-create race handling as settings.device_id().
// WRITE-THEN-PUBLISH, mirroring server/settings._publish_install_id exactly. `flag:'wx'`
// creates the inode BEFORE the bytes land, so the loser of that window used to read an empty
// file and fall back to `|| minted` — its OWN id. Electron spawns the backend, so both booting
// together is the normal case. link() and NOT rename(): rename REPLACES, so two complete temps
// would both "win" and the later write would silently overwrite the id.
//
// Returns null when no id can be established. Null DISABLES reporting; it never invents one,
// because a second id for one launch breaks every readback join.
function installId() {
  if ((process.env.OPENNOLAN_INSTALL_ID || '').trim()) return process.env.OPENNOLAN_INSTALL_ID.trim();
  const file = path.join(os.homedir(), '.opennolan', 'install_id');
  try {
    const existing = fs.readFileSync(file, 'utf8').trim();
    if (existing) return existing;
  } catch (_) { /* not minted yet — fall through and publish */ }
  const minted = 'dev-' + require('node:crypto').randomBytes(16).toString('hex');
  // Same directory: link() cannot cross filesystems.
  const tmp = file + '.' + process.pid + '.tmp';
  let fd = null;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fd = fs.openSync(tmp, 'wx');
    fs.writeSync(fd, minted + '\n');
    fs.fsyncSync(fd);
    fs.closeSync(fd); fd = null;
    try {
      fs.linkSync(tmp, file);
    } catch (err) {
      if (err && err.code === 'EEXIST') {
        // Their file is whole — unless a PREVIOUS buggy build left a zero-byte one, which is
        // why empty is a disabled state and never an id.
        return fs.readFileSync(file, 'utf8').trim() || null;
      }
      throw err;
    }
    return minted;
  } catch (_) {
    return null;
  } finally {
    // An fsync or link that throws must still clean up, or every boot leaks one private temp.
    try { if (fd !== null) fs.closeSync(fd); } catch (_) { /* already gone */ }
    try { fs.unlinkSync(tmp); } catch (_) { /* never created, or already unlinked */ }
  }
}

// Opt-out is honored by reading the SAME settings.json the Python side writes.
function analyticsOptedOut() {
  try {
    return !!(JSON.parse(fs.readFileSync(path.join(appHome(), 'settings.json'), 'utf8')) || {}).analytics_disabled;
  } catch (_) { return false; }  // no/corrupt settings → opted in (the default)
}

// POST one event straight to PostHog. Main must own this transport rather than routing through
// /api/telemetry/events, because the events that matter most here happen when the backend is
// not there: before it starts, when it never becomes healthy, and after it has been stopped.
// Resolves when the request settles so `before-quit` can AWAIT the final flush.
function postToPostHog(event, properties) {
  return new Promise((resolve) => {
    try {
      if (NO_DEFAULT_KEY) return resolve(false);
      if (analyticsOptedOut()) return resolve(false);
      const distinctId = installId();
      if (!distinctId) return resolve(false);  // no id => no report. Never invent one.
      // The gate runs on the CALLER's properties only. The envelope below is our own values —
      // process.platform, app.getVersion(), a uuid — with no user-input path, so it is attached
      // after validation exactly as server/analytics.capture() attaches _env_props().
      const checked = validateEvent(event, properties);
      if (checked === null) return resolve(false);
      if (!budgetOk(event)) return resolve(false);
      const body = JSON.stringify({
        api_key: POSTHOG_KEY,
        event,
        distinct_id: distinctId,
        properties: {
          ...checked,
          // The same envelope server/analytics.py attaches. Without it these events cannot be
          // deduplicated or version-gated alongside the ones the backend sends.
          schema_version: SCHEMA_VERSION,
          event_id: require('node:crypto').randomUUID().replace(/-/g, ''),
          install_id: distinctId,
          session_id: SESSION_ID,
          app_version: app.getVersion(),
          os: process.platform,
          arch: process.arch,
          packaged: app.isPackaged,
          env: app.isPackaged ? 'packaged' : 'dev',
          internal: isInternal(),
        },
      });
      const u = new URL('/capture/', POSTHOG_HOST);
      const req = https.request(
        { method: 'POST', hostname: u.hostname, path: u.pathname,
          headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
          timeout: 4000 },
        // Only a 2xx is delivered. Resolving true on any status made a rejected capture
        // indistinguishable from an accepted one — including in the awaited quit flush.
        (res) => {
          res.on('data', () => {});
          res.on('end', () => resolve(res.statusCode >= 200 && res.statusCode < 300));
        },
      );
      req.on('error', () => resolve(false));
      req.on('timeout', () => { try { req.destroy(); } catch (_) { /* gone */ } resolve(false); });
      req.write(body);
      req.end();
    } catch (_) { resolve(false); }  // reporting must NEVER throw into a crash path
  });
}

// ── S7: Electron's half of the per-session upload budget ─────────────────────
// A backend counter cannot observe this reporter AT ALL — main POSTs raw JSON straight to
// PostHog, which is the whole reason this transport exists (it must work when the backend
// never starts). So the shell enforces its own share of the same equation:
//
//     backend_noncritical(55) + electron_noncritical(8) + reserves(25 + 12) = 100
//
// One process = one session, so these are plain counters rather than a map.
const BUDGET_NONCRITICAL = 8;
const BUDGET_CRITICAL = 12;
const spent = { noncritical: 0, critical: 0 };

function budgetOk(event) {
  const bucket = (TAXONOMY.events || {})[event] && TAXONOMY.events[event].critical ? 'critical' : 'noncritical';
  const limit = bucket === 'critical' ? BUDGET_CRITICAL : BUDGET_NONCRITICAL;
  if (spent[bucket] >= limit) return false;
  spent[bucket] += 1;
  return true;
}

// Product events from the shell. NOT gated on app.isPackaged (unlike reportDesktopError): they
// carry env + internal, which is what the dashboards filter on, and gating them would leave
// this direct-to-PostHog transport as unexercised as 12F found it.
function track(event, properties) { return postToPostHog(event, properties || {}); }

// Which shell sources END a session. wall #5 counts distinct sessions with a FATAL signal, so
// this is not a severity label — it is the numerator's membership test.
const FATAL_SOURCES = new Set(['fatal', 'main-uncaught', 'renderer-gone']);

// A crash inbox needs GROUPING and a place to look, not prose. `message` (500 chars) and
// `stack` (8000 chars) were undeclared and unvalidated — a live free-text path to PostHog,
// because main posts direct. This is the pattern `launch_failure` already ships (main.js
// classifies a local stderr tail into `failure_class` and sends only that): a classified
// class, one path-scrubbed frame, and a hash to group on. The raw text stays in the local log
// and the dialog, where the user can still read it.
function classifyException(err) {
  const name = (err && err.name) || '';
  if (BOUNDED_TOKEN.test(name)) return name;
  const m = /^([A-Za-z][A-Za-z0-9_]{0,63}Error)\b/.exec(String((err && err.message) || ''));
  return m ? m[1] : 'Error';
}

// basename:line only. A full frame is `/Users/<name>/…/main.js:842:11`, i.e. the OS username.
function topFrame(err) {
  const line = String((err && err.stack) || '').split('\n').find((l) => /:\d+:\d+\)?\s*$/.test(l));
  if (!line) return null;
  const m = /([^/\\\s()]+):(\d+):\d+\)?\s*$/.exec(line);
  return m ? `${m[1]}:${m[2]}` : null;
}

// Group on the SHAPE, not the text: message bodies embed ids and paths, so hashing them would
// give every occurrence its own group — the opposite of what an inbox is for.
function stackHash(err) {
  const shape = String((err && err.stack) || (err && err.message) || '')
    .split('\n').slice(0, 8)
    .map((l) => l.replace(/(\/Users\/|\/home\/|\/var\/|\/private\/|\/tmp\/)[^\s)]*/g, '[path]').replace(/:\d+:\d+/g, ''))
    .join('|');
  return require('node:crypto').createHash('sha256').update(shape).digest('hex').slice(0, 16);
}

function reportDesktopError(source, err) {
  try {
    if (!app.isPackaged) return Promise.resolve(false);  // dev crashes surface in the terminal — keep the inbox = real users
    if (errorsSent >= 20) return Promise.resolve(false);  // never let a crash loop flood ingestion
    errorsSent++;
    // Local only — this is where the detail lives now, and it is what the fatal dialog shows.
    console.error(`[desktop_error/${source}] ${scrubText((err && err.message) || err)}\n${scrubText((err && err.stack) || '')}`);
    // RETURNED, not fired and forgotten: fatal() races this against a 1500ms cap before the
    // blocking error dialog, and a promise it cannot see is a report it cannot wait for.
    return postToPostHog('desktop_error', {
      source,
      fatal: FATAL_SOURCES.has(source),
      exception_class: classifyException(err),
      top_frame: topFrame(err),
      stack_hash: stackHash(err),
    });
  } catch (_) { /* reporting must NEVER throw into a crash path */ }
  return Promise.resolve(false);
}

// ── previous_exit: the only way to see a crash that killed main outright ──────
// before-quit cannot run after the process dies, so "did the last session end cleanly" has to
// be answered by what the LAST session left on disk. A marker written at boot and cleared at
// quit turns an absent-clean-exit into an observable `crash` on the next launch.
function launchMarkerPath() { return path.join(appHome(), '.last-exit.json'); }
function readLaunchMarker() {
  try { return JSON.parse(fs.readFileSync(launchMarkerPath(), 'utf8')) || {}; } catch (_) { return {}; }
}
// Carries the SESSION the marker belongs to, not just its outcome: without it the next launch
// can report that something died but cannot say WHICH session — and wall #5's numerator is
// counted in distinct session_ids, so an unjoinable death is a death it cannot count.
function writeLaunchMarker(exit) {
  try {
    fs.mkdirSync(path.dirname(launchMarkerPath()), { recursive: true });
    fs.writeFileSync(launchMarkerPath(), JSON.stringify({
      exit, version: app.getVersion(), session_id: SESSION_ID,
    }));
  } catch (_) { /* best effort — an unwritable marker just means previous_exit='unknown' */ }
}
// `open` is written at boot and replaced with `clean` by before-quit. Finding it still `open`
// means the previous process never reached an orderly shutdown — a crash or a kill. The two
// are NOT distinguishable from this side (a SIGKILL leaves exactly what a segfault leaves), so
// this reports the honest superset `crash` rather than guessing; `unknown` stays reserved for
// "there was no previous launch to classify".
function classifyPreviousExit(marker) {
  if (!marker || !marker.exit) return 'unknown';
  return marker.exit === 'clean' ? 'clean' : 'crash';
}

// Classify a failure_class from the phase + message, never shipping the raw stderr tail: it
// embeds absolute paths, and the dialog already shows the user the real text locally.
function classifyLaunchFailure(text) {
  const t = String(text || '').toLowerCase();
  if (t.includes('did not become healthy')) return 'health_timeout';
  if (t.includes('exited before becoming healthy') || t.includes('backend stopped')) return 'backend_exited';
  if (t.includes('ui not built') || t.includes('vite dev server')) return 'ui_missing';
  if (t.includes('eaddrinuse') || t.includes('port')) return 'port';
  if (t.includes('enoent') || t.includes('no such file')) return 'missing_binary';
  if (t.includes('provision')) return 'provision';
  return 'unknown';
}

function fatal(title, message) {
  if (fatalShown) return;
  fatalShown = true;
  const failureClass = classifyLaunchFailure(title + ' ' + message);
  const reported = track('launch_failure', {
    phase: backendPort ? 'health' : 'spawn',
    failure_class: failureClass,
    retryable: ['health_timeout', 'port'].includes(failureClass),
  });
  // Report before the dialog — this fires for "backend won't start / exited", the crash most likely
  // to lose a new user. Title = the grouping message; message (incl. backend stderr tail) = detail.
  // Its promise joins the flush below: it is the crash-inbox half of the same failure, and
  // leaving it unawaited would let the modal freeze the loop with that POST still in flight.
  const crashReported = reportDesktopError('fatal', { message: title, stack: message });
  shuttingDown = true;
  stopProvision();
  stopBackend();
  // If the setup window is still up (e.g. the backend died after a first-run install), flip it
  // into its error state — a green bar creeping behind a fatal dialog reads as a lie.
  setupSend('setup:error', message);
  // showErrorBox is SYNCHRONOUS and BLOCKS the main process, so the POST above — which needs the
  // event loop to finish its socket work — never completed: measured live, the process then sat
  // unresponsive to SIGTERM with .last-exit.json still 'open', and NEITHER launch_failure nor the
  // session_ended that before-quit would have sent ever reached PostHog. So let the report leave
  // first, bounded exactly like the before-quit flush: a network stall must never leave a user
  // staring at an app that will not tell them it failed.
  const show = () => { dialog.showErrorBox(title, message); app.quit(); };
  const flushed = Promise.all([reported, crashReported].map((p) => Promise.resolve(p).catch(() => false)));
  Promise.race([flushed, new Promise((r) => setTimeout(r, 1500))]).then(show, show);
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
// Set when the user chose "Restart & update". before-quit must NOT defer that quit for a
// telemetry flush — Squirrel owns the shutdown from that point and delaying it is an
// untestable risk on a path that only runs against a signed build.
let installingUpdate = false;

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
    autoUpdater.on('error', (e) => {
      console.error('[updater] ' + (e && e.message));
      track('update_lifecycle', { phase: 'failed', target_version: null });
    });
    autoUpdater.on('update-available', (i) => {
      console.log('[updater] update available: ' + (i && i.version));
      track('update_lifecycle', { phase: 'available', target_version: (i && i.version) || null });
    });
    autoUpdater.on('update-downloaded', (i) => {
      pendingUpdate = { version: (i && i.version) || null };
      track('update_lifecycle', { phase: 'downloaded', target_version: pendingUpdate.version });
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
      track('update_lifecycle', { phase: 'install_clicked', target_version: pendingUpdate.version });
      shuttingDown = true;
      installingUpdate = true;
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
      // NOT the relay above: its first branch fires per NDJSON LOG LINE (banned hook class 3).
      // These are the awaited per-tier resolutions — 1-2 per install, not thousands.
      track('provision_started', { tier: 'core', reason: 'missing' });
      const t = Date.now();
      await runProvision(['--core'], relay('core')); // fatal on failure (caught below)
      track('provision_finished', { tier: 'core', outcome: 'success', duration_s: bucketSeconds(Date.now() - t) });
    }
    if (!compositionReady) {
      // Best-effort: swallow failures so a broken Remotion/HyperFrames install never blocks the editor.
      track('provision_started', { tier: 'composition', reason: 'missing' });
      const t = Date.now();
      try {
        await runProvision(['--composition'], relay('composition'));
        track('provision_finished', { tier: 'composition', outcome: 'success', duration_s: bucketSeconds(Date.now() - t) });
      } catch (compErr) {
        if (shuttingDown) throw compErr; // user cancelled — don't misread the kill as an engine failure
        console.error('[provision] composition tier failed (non-fatal): ' + (compErr && compErr.message));
        track('provision_finished', {
          tier: 'composition',
          outcome: shuttingDown ? 'cancelled' : 'failed',
          duration_s: bucketSeconds(Date.now() - t),
        });
        // The first-run failure taxonomy. Classified from the message, never the raw stderr:
        // it embeds absolute paths, and the local dialog already shows the user the real text.
        track('provisioning_error', {
          tier: 'composition',
          stage: 'install',
          failure_class: classifyLaunchFailure((compErr && compErr.message) || ''),
        });
        setupSend('setup:progress', 'Video engines unavailable — you can retry later from Settings.');
        sendStep(seg.composition[1], seg.composition[1], 'Video engines skipped.');
      }
    }
    setupSend('setup:done', undefined);
    sendStep(seg.backend[0], 99, 'Starting OpenNolan…'); // backend start = the last slice of the bar
  } catch (err) {
    track('provisioning_error', {
      tier: 'core',
      stage: 'install',
      failure_class: classifyLaunchFailure((err && err.message) || ''),
    });
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
  const t0 = Date.now();
  let probes = 0;
  return new Promise((resolve, reject) => {
    const tick = async () => {
      if (backendDead) return reject(new Error('backend process exited before becoming healthy'));
      probes++;
      if (await probeHealth(port)) {
        // Exactly one per wait: the poll's other two branches both terminate the promise.
        track('backend_ready', { startup_ms: Date.now() - t0, probe_count: probes, dev: DEV });
        return resolve(port);
      }
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
  // The backend this shell spawns belongs to THIS session. Without it `app_opened` — the event
  // anyone reaches for as the funnel entry point — has no session_id at all and cannot be
  // joined to anything. Only set on a backend WE own: a dev backend that was already running
  // was not started by this session, and claiming otherwise would be a fabricated join.
  runtimeEnv.OPENNOLAN_SESSION_ID = SESSION_ID;
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
  return DEV ? worktreeConfig.frontendUrl() : 'http://127.0.0.1:' + backendPort;
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
      // Hand main's session id to the sandboxed preload. argv is the documented channel —
      // a sandboxed preload has no module state and no synchronous IPC to fetch it with.
      additionalArguments: ['--on-session=' + SESSION_ID],
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
    // Session + launch are recorded FIRST, before anything that can fail: a launch that
    // never reaches a window still has to appear in the session denominator, or the
    // crash-free rate silently excludes the users who crash hardest.
    logAnalyticsDestination();
    const marker = readLaunchMarker();
    writeLaunchMarker('open');  // replaced with 'clean' by before-quit; survives as a crash flag
    sessionStart = Date.now();
    track('app_launch_started', {
      launch_kind: (marker.version && marker.version !== app.getVersion()) ? 'post_update' : 'cold',
      previous_exit: classifyPreviousExit(marker),
      // The session that died, so wall #5 can attribute the crash to a real session_id rather
      // than only inferring one from a start with no matching end.
      prior_session_id: marker.session_id || null,
    });
    // `dashboard` is the surface the app actually opens on. Dev/product separation already
    // lives in the envelope's `env`/`internal`, so it must not leak into this closed enum.
    track('session_started', { entry: 'dashboard' });
    applyCsp();
    if (DEV) {
      backendPort = worktreeConfig.backendPort();
      const alreadyUp = await probeHealth(backendPort);
      if (!alreadyUp) {
        // Nothing healthy on this worktree's backend port — own a backend. If a
        // separate run-dev already serves it, reuse that process instead.
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

// Native failures that escape JS entirely. `process_gone` is the PRODUCT event (bounded
// enums, joinable to the session); reportDesktopError stays as the crash-inbox entry.
// exitCode is bucketed, not raw: it is unbounded and the bucket answers the same question.
// Durations ship as ORDERED BUCKETS, never raw ms: a packaged install time plus a city plus an
// exact file size is the fingerprinting combination, and the bucket answers the same question.
// The labels must match server/analytics._BUCKET_LABEL, which is what the validator enforces.
function bucketSeconds(ms) {
  const s = Math.max(0, Math.round((Number(ms) || 0) / 1000));
  if (s < 5) return '0-5';
  if (s < 15) return '5-15';
  if (s < 60) return '15-60';
  if (s < 300) return '60-300';
  return '300+';
}

function exitCodeBucket(code) {
  if (code == null) return 'unknown';
  if (code === 0) return '0';
  if (code < 0) return 'signal';
  return code < 128 ? '1-127' : '128+';
}
app.on('render-process-gone', (_e, _wc, d) => {
  // The renderer IS the app window: losing it ends the session. A utility/GPU child does not.
  track('process_gone', { process: 'renderer', session_fatal: true, reason: (d && d.reason) || 'unknown', exit_code_bucket: exitCodeBucket(d && d.exitCode) });
  reportDesktopError('renderer-gone', { message: 'renderer gone: ' + (d && d.reason), stack: 'exitCode=' + (d && d.exitCode) });
});
// Electron's child type is display-cased and can contain SPACES ('Sandbox helper', 'Pepper
// Plugin'). The taxonomy declares a closed token vocabulary for this field, so normalize here
// rather than let the validator drop a legitimate value on the floor.
function processName(type) {
  const t = String(type || 'unknown').trim().toLowerCase().replace(/\s+/g, '_');
  return /^[a-z0-9_]+$/.test(t) ? t : 'unknown';
}
app.on('child-process-gone', (_e, d) => {
  track('process_gone', { process: processName(d && d.type), session_fatal: false, reason: (d && d.reason) || 'unknown', exit_code_bucket: exitCodeBucket(d && d.exitCode) });
  reportDesktopError('child-gone', { message: (d && d.type || 'child') + ' gone: ' + (d && d.reason), stack: 'exitCode=' + (d && d.exitCode) });
});

app.whenReady().then(boot);

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0 && backendPort) createWindow(rendererUrl());
});

// Single-purpose tool: quit (and stop the backend + any in-flight provisioning) when the window
// closes, macOS included. Closing the SETUP window mid-install lands here too — that's a cancel.
app.on('window-all-closed', () => { shuttingDown = true; stopProvision(); stopBackend(); app.quit(); });

// The clean-exit hook. `window-all-closed` cannot observe a crash and quits with no flush;
// `before-quit` is the one place that runs on every ORDERLY exit. The quit is deferred until
// the event lands — but only for a bounded time: a network stall must never leave the user
// staring at an app that will not close.
let quitFlushed = false;
app.on('before-quit', (e) => {
  shuttingDown = true;
  stopProvision();
  stopBackend();
  writeLaunchMarker('clean');
  if (quitFlushed) return;
  quitFlushed = true;
  const ended = track('session_ended', {
    duration_s: Math.round((Date.now() - sessionStart) / 1000),
    exit_kind: installingUpdate ? 'update' : 'clean',
  });
  if (installingUpdate) return;  // Squirrel owns this shutdown — send and let it go
  e.preventDefault();
  const finish = () => app.quit();
  Promise.race([ended, new Promise((r) => setTimeout(r, 1500))]).then(finish, finish);
});
process.on('exit', () => { stopProvision(); stopBackend(); });
