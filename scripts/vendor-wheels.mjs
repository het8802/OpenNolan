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
  mkdirSync, mkdtempSync, rmSync, existsSync, readFileSync, writeFileSync, statSync,
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
    const intact = prev.stamp === stamp && prev.wheels?.length
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

  const bytes = wanted.reduce((n, w) => n + w.size, 0);
  writeFileSync(MANIFEST, `${JSON.stringify({
    stamp,
    pythonVersion: PY_VERSION,
    requirements: REQUIREMENTS,
    wheels: wanted.map(({ filename, name, version, sha256, size }) => ({ filename, name, version, sha256, size })),
  }, null, 2)}\n`);
  console.log(`[vendor-wheels] done (${stamp}): ${wanted.length} wheels, ${(bytes / 1e6).toFixed(1)} MB`);
}

main().catch((err) => {
  console.error(`[vendor-wheels] FAILED: ${err.message}`);
  process.exit(1);
});
