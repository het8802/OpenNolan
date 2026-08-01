#!/usr/bin/env node
// Fetch + verify + extract a pinned python-build-standalone arm64 macOS interpreter into
// desktop/resources/python/, ready for electron-builder to bundle (extraResources from:resources/python
// to:python -> Contents/Resources/python/bin/python3).
//
// This is Lane D's ONLY Python step: ship a (later-SIGNED) interpreter. The venv + uv/pip install of
// packages happens at FIRST RUN (Lane E) into ~/Library/Application Support/OpenNolan/runtime — never
// here, and never a build-time venv (venvs bake absolute build-machine paths and the .app is read-only).
// Code-signing of these binaries is done by electron-builder's own signing pass during packaging
// (they ship adhoc/linker-signed and get re-signed with your Developer ID + hardened runtime).
//
// Pinned deliberately (not "latest") for reproducible builds. Bump PBS_TAG + PY_VERSION together and
// re-verify the sha. Verification parses the release's single SHA256SUMS file (no per-asset sidecars).
// Idempotent: a matching .pbs-version stamp short-circuits. Run: `node scripts/fetch-python.mjs`.

import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import {
  mkdirSync, rmSync, existsSync, readFileSync, writeFileSync, createWriteStream,
} from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';

// --- pins (verified live against the 2026-06-23 release; bump deliberately) ---
const PBS_TAG = '20260623';
const PY_VERSION = '3.12.13';
const TRIPLE = 'aarch64-apple-darwin';        // arm64 ONLY for v1 (locked decision)
const FLAVOR = 'install_only_stripped';       // tarball ~24MB; extracted ~70MB, ~55MB after the Tk/Tcl prune below
const ASSET = `cpython-${PY_VERSION}+${PBS_TAG}-${TRIPLE}-${FLAVOR}.tar.gz`;
// Expected sha256, pinned OUT-OF-BAND (not just read from the release's own SHA256SUMS). We assert the
// SHA256SUMS entry equals this, so a tampered release that also edits its checksums file can't slip
// through (the "checksum from the same server" weakness). Update this when bumping PBS_TAG/PY_VERSION.
const PBS_SHA256 = '41df7d3ae4757e84b97874f76d634268456aaa271740d33f968d826374998fb7';

// Unused, heavy sub-trees pruned after extraction — the backend imports none of these (verified: 0
// tkinter/idlelib/turtle imports). Drops ~15MB + a pile of Mach-O the signer would otherwise process.
const PRUNE = [
  'lib/tk9.0', 'lib/tcl9.0', 'lib/tcl9', 'lib/itcl4.3.5', 'lib/thread3.0.4',
  'lib/libtcl9.0.dylib', 'lib/libtcl9tk9.0.dylib',
  'lib/python3.12/tkinter', 'lib/python3.12/idlelib', 'lib/python3.12/turtledemo',
];
const BASE = `https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}`;

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const OUT_DIR = join(REPO_ROOT, 'desktop', 'resources', 'python'); // -> bin/python3, lib/, include/, share/
const STAMP = join(OUT_DIR, '.pbs-version');
const STAMP_VALUE = `${PBS_TAG}/${PY_VERSION}/${TRIPLE}/${FLAVOR}`;

async function fetchBuf(url) {
  const res = await fetch(url, { redirect: 'follow' });
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status} ${res.statusText}`);
  return Buffer.from(await res.arrayBuffer());
}

async function main() {
  if (existsSync(STAMP) && readFileSync(STAMP, 'utf8').trim() === STAMP_VALUE
      && existsSync(join(OUT_DIR, 'bin', 'python3'))) {
    console.log(`[fetch-python] already provisioned (${STAMP_VALUE}) — skipping`);
    return;
  }

  console.log(`[fetch-python] reading checksum from ${BASE}/SHA256SUMS`);
  const sums = (await fetchBuf(`${BASE}/SHA256SUMS`)).toString('utf8');
  const line = sums.split('\n').find((l) => l.trim().endsWith(ASSET));
  if (!line) throw new Error(`SHA256SUMS has no entry for ${ASSET}`);
  const expected = line.trim().split(/\s+/)[0].toLowerCase();
  if (expected !== PBS_SHA256) {
    throw new Error(`SHA256SUMS sha for ${ASSET} (${expected}) != pinned PBS_SHA256 (${PBS_SHA256}) — refusing`);
  }

  console.log(`[fetch-python] downloading ${ASSET} (~24MB)…`);
  const tarball = await fetchBuf(`${BASE}/${ASSET}`);
  const actual = createHash('sha256').update(tarball).digest('hex').toLowerCase();
  if (actual !== expected) {
    throw new Error(`checksum mismatch for ${ASSET}\n  expected ${expected}\n  actual   ${actual}`);
  }
  console.log(`[fetch-python] sha256 OK (${actual.slice(0, 12)}…)`);

  // Fresh extract, stripping the tarball's leading `python/` dir so bin/lib sit directly under OUT_DIR
  // (clean single-level layout: OUT_DIR/bin/python3, NOT OUT_DIR/python/bin/python3).
  rmSync(OUT_DIR, { recursive: true, force: true });
  mkdirSync(OUT_DIR, { recursive: true });
  const tmpTar = join(OUT_DIR, ASSET);
  await pipeline(Readable.from(tarball), createWriteStream(tmpTar));
  execFileSync('tar', ['-xzf', tmpTar, '--strip-components=1', '-C', OUT_DIR], { stdio: 'inherit' });
  rmSync(tmpTar, { force: true }); // never ship the tarball inside the .app

  const py = join(OUT_DIR, 'bin', 'python3');
  if (!existsSync(py)) throw new Error(`extraction did not produce ${py}`);
  const ver = execFileSync(py, ['--version'], { encoding: 'utf8' }).trim();
  console.log(`[fetch-python] extracted ${ver} -> ${py}`);

  // Prune unused Tk/Tcl/idlelib/tkinter/turtledemo — dead weight the backend never imports.
  for (const rel of PRUNE) rmSync(join(OUT_DIR, rel), { recursive: true, force: true });
  console.log(`[fetch-python] pruned ${PRUNE.length} unused sub-trees (Tk/Tcl/idlelib/tkinter)`);

  writeFileSync(STAMP, STAMP_VALUE + '\n');
  console.log(`[fetch-python] done (${STAMP_VALUE})`);
  console.log('[fetch-python] NOTE: ships adhoc/linker-signed; electron-builder re-signs it with your');
  console.log('[fetch-python] Developer ID + hardened runtime during packaging (mac.notarize:true).');
}

main().catch((err) => {
  console.error(`[fetch-python] FAILED: ${err.message}`);
  process.exit(1);
});
