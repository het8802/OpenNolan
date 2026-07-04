#!/usr/bin/env node
// Fetch + verify + extract a pinned Node.js arm64 macOS runtime into desktop/resources/node/, ready for
// electron-builder to bundle (extraResources from:resources/node to:node -> Contents/Resources/node/bin/node).
//
// OPN-3: the composition engines (Remotion + HyperFrames) need Node. We ship a (later-SIGNED) Node the
// same way we ship the Python interpreter — NEVER assume a system Node. The npm packages install at FIRST
// RUN (lib/provision.py provision_composition) into ~/Library/Application Support/OpenNolan/runtime, never
// here (the .app is read-only). main.js sets OPENNOLAN_NODE to this binary and puts bin/ on the child PATH.
//
// Pinned deliberately (not "latest") for reproducible builds. Bump NODE_VERSION + re-verify NODE_SHA256
// (from https://nodejs.org/dist/<ver>/SHASUMS256.txt). Idempotent: a matching .node-version stamp skips.
// Run: `node scripts/fetch-node.mjs`.

import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import {
  mkdirSync, rmSync, existsSync, readFileSync, writeFileSync, createWriteStream,
} from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';

// --- pins (verified live against the v22.17.1 release; bump deliberately) ---
const NODE_VERSION = 'v22.17.1';               // Node 22 LTS — HyperFrames' floor + Remotion 4.x supported
const ARCH = 'darwin-arm64';                    // arm64 ONLY for v1 (locked decision, matches the Python pin)
const ASSET = `node-${NODE_VERSION}-${ARCH}.tar.gz`;
// sha256 of the tarball, pinned OUT-OF-BAND. We assert the SHASUMS256.txt entry equals this, so a tampered
// release that also edits its checksums file can't slip through. Update when bumping NODE_VERSION.
const NODE_SHA256 = 'a983f4f2a7b71512b78d7935b9ccf6b72120a255810070afd635c4146bca7b31';

// Unused sub-trees pruned after extraction — the app only needs node + npm to run `npm ci` at first run.
const PRUNE = [
  'include',            // C headers (no native builds on the user's Mac; wheels/prebuilt only)
  'share/doc', 'share/man', 'share/systemtap',
  'lib/node_modules/npm/docs', 'lib/node_modules/npm/man',
];
const BASE = `https://nodejs.org/dist/${NODE_VERSION}`;

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const OUT_DIR = join(REPO_ROOT, 'desktop', 'resources', 'node'); // -> bin/node, bin/npm, bin/npx, lib/
const STAMP = join(OUT_DIR, '.node-version');
const STAMP_VALUE = `${NODE_VERSION}/${ARCH}`;

async function fetchBuf(url) {
  const res = await fetch(url, { redirect: 'follow' });
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status} ${res.statusText}`);
  return Buffer.from(await res.arrayBuffer());
}

async function main() {
  if (existsSync(STAMP) && readFileSync(STAMP, 'utf8').trim() === STAMP_VALUE
      && existsSync(join(OUT_DIR, 'bin', 'node'))) {
    console.log(`[fetch-node] already provisioned (${STAMP_VALUE}) — skipping`);
    return;
  }

  console.log(`[fetch-node] reading checksum from ${BASE}/SHASUMS256.txt`);
  const sums = (await fetchBuf(`${BASE}/SHASUMS256.txt`)).toString('utf8');
  const line = sums.split('\n').find((l) => l.trim().endsWith(ASSET));
  if (!line) throw new Error(`SHASUMS256.txt has no entry for ${ASSET}`);
  const expected = line.trim().split(/\s+/)[0].toLowerCase();
  if (expected !== NODE_SHA256) {
    throw new Error(`SHASUMS256 sha for ${ASSET} (${expected}) != pinned NODE_SHA256 (${NODE_SHA256}) — refusing`);
  }

  console.log(`[fetch-node] downloading ${ASSET} (~45MB)…`);
  const tarball = await fetchBuf(`${BASE}/${ASSET}`);
  const actual = createHash('sha256').update(tarball).digest('hex').toLowerCase();
  if (actual !== expected) {
    throw new Error(`checksum mismatch for ${ASSET}\n  expected ${expected}\n  actual   ${actual}`);
  }
  console.log(`[fetch-node] sha256 OK (${actual.slice(0, 12)}…)`);

  // Fresh extract, stripping the tarball's leading `node-<ver>-<arch>/` dir so bin/lib sit directly under
  // OUT_DIR (clean single-level layout: OUT_DIR/bin/node, matching the Python bundle's shape).
  rmSync(OUT_DIR, { recursive: true, force: true });
  mkdirSync(OUT_DIR, { recursive: true });
  const tmpTar = join(OUT_DIR, ASSET);
  await pipeline(Readable.from(tarball), createWriteStream(tmpTar));
  execFileSync('tar', ['-xzf', tmpTar, '--strip-components=1', '-C', OUT_DIR], { stdio: 'inherit' });
  rmSync(tmpTar, { force: true }); // never ship the tarball inside the .app

  const nodeBin = join(OUT_DIR, 'bin', 'node');
  if (!existsSync(nodeBin)) throw new Error(`extraction did not produce ${nodeBin}`);
  const ver = execFileSync(nodeBin, ['--version'], { encoding: 'utf8' }).trim();
  console.log(`[fetch-node] extracted node ${ver} -> ${nodeBin}`);

  for (const rel of PRUNE) rmSync(join(OUT_DIR, rel), { recursive: true, force: true });
  console.log(`[fetch-node] pruned ${PRUNE.length} unused sub-trees (headers/docs/man)`);

  writeFileSync(STAMP, STAMP_VALUE + '\n');
  console.log(`[fetch-node] done (${STAMP_VALUE})`);
  console.log('[fetch-node] NOTE: ships linker-signed; electron-builder re-signs node with your Developer');
  console.log('[fetch-node] ID + hardened-runtime entitlements during packaging (loads native .node addons).');
}

main().catch((err) => {
  console.error(`[fetch-node] FAILED: ${err.message}`);
  process.exit(1);
});
