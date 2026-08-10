#!/usr/bin/env node
// Vendor every CORE Python wheel into desktop/resources/wheels/, bundled by electron-builder
// (extraResources from:resources/wheels to:wheels -> Contents/Resources/wheels). main.js sets
// OPENNOLAN_WHEELS to that dir and lib/provision.py installs the core venv with
// `--offline --no-cache --find-links <dir>`, so first launch reaches the editor with ZERO network.
// A beta tester's clean Mac died on that download and we could not diagnose it; the fix is to not
// need the download. The composition tier (npm ci) and the capability packs stay online on purpose —
// this makes the CORE offline, not the whole app.
//
// ONE resolver end to end: uv resolves here and uv installs on the user's Mac, so the set we ship is
// the set uv wants (a different resolver can pick a different version for the same `>=` range).
// `uv pip download` DOES NOT EXIST — not in uv 0.12.2, not in any version — so we take uv's PEP 751
// lock instead: `uv pip compile --format pylock.toml` emits the URL + sha256 of every wheel that is
// compatible with the target, and we fetch those files ourselves.
//
// --python-version MUST track fetch-python.mjs's PY_VERSION: a wheel's `cp312` tag is the ABI of the
// interpreter we bundle. Resolve on a 3.13 build machine without it and you vendor cp313 wheels that
// cannot install into the bundled 3.12 venv — a green build that fails on every user's machine.
// --python-platform is deliberately NOT pinned: the build machine is already arm64 macOS, which is the
// only target (arm64 ONLY for v1, like Python + Node), and pinning a platform would drop the wheels the
// resolver offers for newer ones. We vendor EVERY wheel a package resolved to, so a package with both a
// `macosx_11_0` and a `macosx_14_0` build (numpy) ships both and uv picks per machine at install time.
// The set's native arm64 floor is macOS 11.0; the app's declared floor is 12.0
// (desktop/package.json build.mac.minimumSystemVersion), so every supported Mac has an installable
// wheel and no requirement needs bounding backwards.
//
// The wheels dir IS the lock: under `--offline --no-cache --find-links` the resolver can only install
// what we shipped, which pins the transitive deps too. Idempotent: MANIFEST.json carries a stamp over
// (python version + the requirement files' bytes), and a match with every wheel present on disk
// short-circuits with no network at all. `rm -rf desktop/resources/wheels` forces a re-resolve.
// FAILS LOUDLY: a requirement that did not resolve must break the BUILD, never first launch.
// Run: `node scripts/vendor-wheels.mjs`.

import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import {
  chmodSync, closeSync, mkdirSync, mkdtempSync, openSync, readdirSync, readSync, rmSync,
  existsSync, readFileSync, writeFileSync, statSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// --- pins ---
const PY_VERSION = '3.12';                 // ABI of the bundled interpreter (fetch-python.mjs PY_VERSION)
const REQUIREMENTS = ['requirements-ui.txt', 'requirements.txt']; // = lib/provision.py CORE_REQUIREMENTS
const CONCURRENCY = 8;

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const OUT_DIR = join(REPO_ROOT, 'desktop', 'resources', 'wheels');
const MANIFEST = join(OUT_DIR, 'MANIFEST.json');

// The bundled uv (fetch-uv.mjs runs before this in the `fetch-runtime` chain), else a PATH uv. An old
// PATH uv without `--format pylock.toml` fails loudly here rather than vendoring the wrong thing.
function uvBin() {
  const bundled = join(REPO_ROOT, 'desktop', 'resources', 'uv', 'uv');
  return existsSync(bundled) ? bundled : 'uv';
}

async function fetchBuf(url) {
  const res = await fetch(url, { redirect: 'follow' });
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status} ${res.statusText}`);
  return Buffer.from(await res.arrayBuffer());
}

// PEP 503 normalization, so `Pillow` in requirements.txt matches `pillow` in the lock.
const normalize = (name) => name.toLowerCase().replace(/[-_.]+/g, '-');

// Requirement names DECLARED in a requirements file (no versions, no extras, no comments) — used only
// to assert nothing was silently dropped from the resolution.
function declaredNames(text) {
  const names = [];
  for (const raw of text.split('\n')) {
    const line = raw.split('#')[0].trim();
    if (!line || line.startsWith('-')) continue;
    names.push(normalize(line.split(/[[<>=!~;\s]/)[0]));
  }
  return names;
}

// uv's PEP 751 lock -> [{ name, version, wheels: [{ url, filename, sha256 }] }]. Each wheel entry is
// `{ url = "…", …, hashes = { sha256 = "…" } }`; the `[^}]*?` cannot cross the closing brace of an
// entry, so a url is only ever paired with the sha256 inside its OWN braces.
function parsePylock(toml) {
  const packages = [];
  for (const block of toml.split(/^\[\[packages\]\]$/m).slice(1)) {
    const name = /^name = "([^"]+)"/m.exec(block);
    const version = /^version = "([^"]+)"/m.exec(block);
    if (!name || !version) throw new Error(`pylock package block has no name/version:\n${block.slice(0, 200)}`);
    if (/^sdist = /m.test(block)) {
      throw new Error(`${name[1]} resolved to a source distribution — a user has no compiler`);
    }
    const wheels = [];
    const re = /url = "([^"]+\.whl)"[^}]*?sha256 = "([0-9a-f]{64})"/g;
    for (let m = re.exec(block); m; m = re.exec(block)) {
      wheels.push({ url: m[1], filename: m[1].slice(m[1].lastIndexOf('/') + 1), sha256: m[2] });
    }
    if (!wheels.length) throw new Error(`${name[1]}==${version[1]} resolved to no wheel`);
    packages.push({ name: name[1], version: version[1], wheels });
  }
  if (!packages.length) throw new Error('pylock.toml listed no packages');
  return packages;
}

// Apple's notary service recurses INTO the .whl archives we ship in Resources/wheels and rejects
// every Mach-O inside them that is not Developer-ID signed ("The binary is not signed with a valid
// Developer ID certificate" / "does not include a secure timestamp"). A wheel straight from PyPI
// carries adhoc, linker-signed extensions, so a freshly vendored set fails notarization: 29 wheels,
// 266 errors, no release. electron-builder cannot help — its signing pass walks the file tree and a
// .whl is just a zip to it.
//
// So we sign them here: extract, sign every Mach-O with the SAME hardened runtime + inherit
// entitlements electron-builder applies to nested binaries (the JIT/unsigned-memory entitlements the
// bundled Bun CLI needs to run at all), rezip. This step used to live only in someone's shell
// history — v1.0.0 shipped signed wheels that no script in this repo produced, which meant the
// release could not be reproduced and a re-vendor silently destroyed it.
//
// RECORD hashes inside each wheel go stale (they describe the unsigned bytes). That is what v1.0.0
// shipped and installed from, and neither pip nor uv verifies RECORD on install.
function signingIdentity() {
  if (process.env.CSC_NAME) return process.env.CSC_NAME;
  const out = execFileSync('security', ['find-identity', '-v', '-p', 'codesigning'], { encoding: 'utf8' });
  const line = out.split('\n').find((l) => l.includes('Developer ID Application'));
  return line ? line.split('"')[1] : null;
}

// Mach-O magics: 64-bit LE/BE and the fat/universal wrappers. Cheaper and more exact than shelling
// out to `file` once per member, and there are thousands of members across the wheel set.
function isMachO(path) {
  const fd = openSync(path, 'r');
  try {
    const b = Buffer.alloc(4);
    if (readSync(fd, b, 0, 4, 0) < 4) return false;
    const m = b.readUInt32BE(0);
    return m === 0xcffaedfe || m === 0xfeedfacf || m === 0xcafebabe || m === 0xbebafeca;
  } finally {
    closeSync(fd);
  }
}

function walk(dir) {
  const out = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) out.push(...walk(p));
    else if (e.isFile() && !e.isSymbolicLink()) out.push(p);
  }
  return out;
}

export function signWheelBinaries(files) {
  const identity = signingIdentity();
  if (!identity) {
    // Dev machine with no Developer ID: the build it produces is unsigned anyway, so there is
    // nothing to notarize and nothing to protect. Loud, because on a RELEASE machine this would
    // mean shipping wheels Apple will reject.
    console.log('[vendor-wheels] no Developer ID identity — skipping wheel signing (unsigned build)');
    return new Map();
  }
  const entitlements = join(REPO_ROOT, 'desktop', 'build', 'entitlements.mac.inherit.plist');
  if (!existsSync(entitlements)) throw new Error(`inherit entitlements not found at ${entitlements}`);
  console.log(`[vendor-wheels] signing nested binaries as "${identity}"`);
  const sizes = new Map();
  let signedWheels = 0;
  let signedBins = 0;
  for (const whl of files.filter((f) => f.endsWith('.whl'))) {
    const src = join(OUT_DIR, whl);
    const dir = mkdtempSync(join(tmpdir(), 'opennolan-whl-'));
    try {
      execFileSync('unzip', ['-qq', '-o', src, '-d', dir], { stdio: ['ignore', 'ignore', 'pipe'] });
      const bins = walk(dir).filter(isMachO);
      if (!bins.length) continue; // pure-python wheel: leave the PyPI bytes untouched
      for (const bin of bins) {
        execFileSync('codesign', [
          '--force', '--sign', identity, '--timestamp', '--options', 'runtime',
          '--entitlements', entitlements, bin,
        ], { stdio: ['ignore', 'ignore', 'pipe'] });
      }
      rmSync(src);
      // -X drops the extended attributes that would otherwise land in the archive; the wheel's own
      // layout is preserved because we zip the extraction root as-is.
      execFileSync('zip', ['-q', '-r', '-X', src, '.'], { cwd: dir, stdio: ['ignore', 'ignore', 'pipe'] });
      sizes.set(whl, statSync(src).size);
      signedWheels += 1;
      signedBins += bins.length;
    } catch (err) {
      throw new Error(`could not sign the binaries inside ${whl}: ${String(err.stderr || err.message).slice(0, 400)}`);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  }
  console.log(`[vendor-wheels] signed ${signedBins} binaries across ${signedWheels} wheels`);
  return sizes;
}

// The claude-agent-sdk wheel BUNDLES the Claude Code CLI binary (claude_agent_sdk/_bundled/claude)
// and the SDK prefers it over every system install, so that one file decides whether the agent can
// run at all. 0.2.134's shipped and died on launch with `ReferenceError: SharedArrayBuffer is not
// defined` — the app reached users with a dead agent. Extract it and run it. `--version` is NOT a
// test: it answers before the CLI loads its module graph, which is where that crash happens.
// FAILS THE BUILD, by design: this must never be a first-launch discovery again.
// FAILS CLOSED on every outcome that is not "the CLI ran": a missing wheel, a missing member, a
// corrupt archive, no `unzip`, a hang, a crash. There is deliberately no skip path — the packaged
// app has NO guaranteed system `claude` to fall back to, so a wheel that carries no runnable CLI is
// just as unshippable as one that carries a broken one.
export function smokeTestBundledCli(files) {
  const whl = files.find((f) => /^claude_agent_sdk-.*\.whl$/.test(f));
  if (!whl) throw new Error('no claude-agent-sdk wheel vendored — the agent cannot run without it');
  // The vendored wheel is the arm64 build (the only target), so only an arm64 host can execute it.
  // Refuse rather than skip: a silent skip on an Intel builder is how an unvetted bundle ships.
  if (process.arch !== 'arm64') {
    throw new Error(`cannot vet the arm64 CLI in ${whl} on a ${process.arch} host — build on Apple silicon`);
  }
  const member = 'claude_agent_sdk/_bundled/claude';
  const dir = mkdtempSync(join(tmpdir(), 'opennolan-cli-smoke-'));
  try {
    // Extract to DISK, not through a Node buffer: the binary is ~280MB, and a maxBuffer overflow
    // would arrive as a generic error indistinguishable from a real failure.
    execFileSync('unzip', ['-o', '-q', join(OUT_DIR, whl), member, '-d', dir], { stdio: ['ignore', 'ignore', 'pipe'] });
    const cli = join(dir, member);
    chmodSync(cli, 0o755); // the wheel should carry the exec bit; do not depend on it
    // timeout: a CLI that hangs on startup must fail the build, not stall it forever.
    execFileSync(cli, ['--help'], { stdio: ['ignore', 'ignore', 'pipe'], timeout: 60_000 });
    console.log(`[vendor-wheels] bundled CLI in ${whl} runs`);
  } catch (err) {
    // Last 20 SHORT lines: a Bun crash prints ~10 stack frames after the message that matters,
    // and the lines it interleaves are minified source. Length is the noise filter, not truncation.
    const tail = String(err.stderr || err.message).split('\n').filter((l) => l.length <= 200).slice(-20).join('\n');
    throw new Error(`the CLI bundled in ${whl} could not be extracted and run — do not ship it. `
      + `Pin a different claude-agent-sdk in requirements-ui.txt.\n${tail}`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

// Bounded-concurrency map over a shared iterator (each worker pulls the next item).
async function pool(items, n, fn) {
  const queue = items[Symbol.iterator]();
  await Promise.all(Array.from({ length: n }, async () => {
    for (const item of queue) await fn(item);
  }));
}

async function main() {
  const reqs = REQUIREMENTS.map((f) => ({ file: f, text: readFileSync(join(REPO_ROOT, f), 'utf8') }));
  const stamp = createHash('sha256')
    .update(PY_VERSION)
    .update(reqs.map((r) => `${r.file}\n${r.text}`).join('\0'))
    .digest('hex')
    .slice(0, 16);

  if (existsSync(MANIFEST)) {
    const prev = JSON.parse(readFileSync(MANIFEST, 'utf8'));
    // `cliVerified` is part of intactness: a dir vendored before the CLI smoke test existed carries
    // the same stamp, and skipping on it would let an un-vetted bundle ship. Absent = re-vendor.
    const intact = prev.stamp === stamp && prev.cliVerified && prev.wheelsSigned && prev.wheels?.length
      && prev.wheels.every((w) => existsSync(join(OUT_DIR, w.filename))
        && statSync(join(OUT_DIR, w.filename)).size === w.size);
    if (intact) {
      console.log(`[vendor-wheels] already vendored (${stamp}, ${prev.wheels.length} wheels) — skipping`);
      return;
    }
  }

  const uv = uvBin();
  const lockDir = mkdtempSync(join(tmpdir(), 'opennolan-pylock-'));
  const lock = join(lockDir, 'pylock.toml');
  console.log(`[vendor-wheels] resolving ${REQUIREMENTS.join(' + ')} for CPython ${PY_VERSION} (${uv})`);
  let packages;
  try {
    execFileSync(uv, [
      'pip', 'compile', '--no-cache', '--python-version', PY_VERSION, '--only-binary=:all:',
      '--format', 'pylock.toml', ...REQUIREMENTS.map((f) => join(REPO_ROOT, f)), '-o', lock,
    ], { cwd: REPO_ROOT, stdio: ['ignore', 'ignore', 'inherit'] });
    packages = parsePylock(readFileSync(lock, 'utf8'));
  } finally {
    rmSync(lockDir, { recursive: true, force: true });
  }

  // Every declared requirement must be in the resolution — a dropped one has to break the BUILD.
  const resolved = new Set(packages.map((p) => normalize(p.name)));
  for (const { file, text } of reqs) {
    for (const name of declaredNames(text)) {
      if (!resolved.has(name)) throw new Error(`${file} requires ${name}, which did not resolve`);
    }
  }

  const wanted = packages.flatMap((p) => p.wheels.map((w) => ({ ...w, name: p.name, version: p.version })));
  console.log(`[vendor-wheels] ${packages.length} packages -> ${wanted.length} wheels; downloading…`);

  rmSync(OUT_DIR, { recursive: true, force: true }); // fresh, so a requirements change leaves no stale wheel
  mkdirSync(OUT_DIR, { recursive: true });
  let done = 0;
  await pool(wanted, CONCURRENCY, async (w) => {
    const buf = await fetchBuf(w.url);
    const actual = createHash('sha256').update(buf).digest('hex').toLowerCase();
    if (actual !== w.sha256) {
      throw new Error(`sha256 mismatch for ${w.filename}\n  expected ${w.sha256}\n  actual   ${actual}`);
    }
    writeFileSync(join(OUT_DIR, w.filename), buf);
    w.size = buf.length;
    done += 1;
    if (done % 20 === 0) console.log(`[vendor-wheels]   ${done}/${wanted.length}`);
  });

  // Sign BEFORE the smoke test, so the CLI we vet is the exact artifact we ship — hardened runtime
  // is itself a way to break a JIT binary, and the test would not see it in the other order.
  // Before the stamp either way: a broken or unsignable bundle must not be recorded as vendored.
  const signedSizes = signWheelBinaries(wanted.map((w) => w.filename));
  for (const w of wanted) {
    if (signedSizes.has(w.filename)) w.size = signedSizes.get(w.filename);
  }
  smokeTestBundledCli(wanted.map((w) => w.filename));

  const bytes = wanted.reduce((n, w) => n + w.size, 0);
  writeFileSync(MANIFEST, `${JSON.stringify({
    stamp,
    cliVerified: true,  // smokeTestBundledCli passed for this exact set; see the `intact` check above
    wheelsSigned: true, // nested Mach-O carry a Developer ID signature (signWheelBinaries)
    pythonVersion: PY_VERSION,
    requirements: REQUIREMENTS,
    // `sha256` is the PyPI file's, verified at download. A wheel we signed no longer hashes to it —
    // `signedInPlace` marks those so the manifest does not claim otherwise.
    wheels: wanted.map(({ filename, name, version, sha256, size }) => (
      signedSizes.has(filename)
        ? { filename, name, version, sha256, size, signedInPlace: true }
        : { filename, name, version, sha256, size })),
  }, null, 2)}\n`);
  console.log(`[vendor-wheels] done (${stamp}): ${wanted.length} wheels, ${(bytes / 1e6).toFixed(1)} MB`);
}

// Only when RUN as a script, so smokeTestBundledCli can be imported and exercised on its own.
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((err) => {
    console.error(`[vendor-wheels] FAILED: ${err.message}`);
    process.exit(1);
  });
}
