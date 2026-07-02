#!/usr/bin/env node
// Fetch the `uv` arm64 macOS binary into desktop/resources/uv/, bundled by electron-builder
// (extraResources from:resources/uv to:uv -> Contents/Resources/uv/uv). main.js sets OPENNOLAN_UV
// to that path so scripts/provision.py + the backend use uv to build the venv and install packages
// (fast, and the right tool for the heavy torch/onnxruntime capability packs).
//
// Resolves the LATEST uv release via the GitHub API and verifies against uv's per-asset .sha256
// sidecar, then stamps the resolved version for idempotency + reproducible logging. Run:
//   node scripts/fetch-uv.mjs

import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync, existsSync, readFileSync, writeFileSync, createWriteStream } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';

const ASSET = 'uv-aarch64-apple-darwin.tar.gz'; // arm64 only (locked)
const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const OUT_DIR = join(REPO_ROOT, 'desktop', 'resources', 'uv'); // -> uv (the binary)
const STAMP = join(OUT_DIR, '.uv-version');

async function fetchBuf(url, headers = {}) {
  const res = await fetch(url, { redirect: 'follow', headers: { 'User-Agent': 'opennolan-build', ...headers } });
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status} ${res.statusText}`);
  return Buffer.from(await res.arrayBuffer());
}

async function main() {
  // Resolve the latest release tag (pin the RESOLVED tag in the stamp for reproducible logs).
  const rel = JSON.parse((await fetchBuf('https://api.github.com/repos/astral-sh/uv/releases/latest')).toString());
  const tag = rel.tag_name;
  if (existsSync(STAMP) && readFileSync(STAMP, 'utf8').trim() === tag && existsSync(join(OUT_DIR, 'uv'))) {
    console.log(`[fetch-uv] already provisioned (${tag}) — skipping`);
    return;
  }
  const base = `https://github.com/astral-sh/uv/releases/download/${tag}`;

  console.log(`[fetch-uv] ${tag}: reading ${ASSET}.sha256`);
  const shaLine = (await fetchBuf(`${base}/${ASSET}.sha256`)).toString('utf8');
  const expected = shaLine.trim().split(/\s+/)[0].toLowerCase();

  console.log(`[fetch-uv] downloading ${ASSET}…`);
  const tarball = await fetchBuf(`${base}/${ASSET}`);
  const actual = createHash('sha256').update(tarball).digest('hex').toLowerCase();
  if (actual !== expected) throw new Error(`sha256 mismatch for ${ASSET}\n  expected ${expected}\n  actual   ${actual}`);
  console.log(`[fetch-uv] sha256 OK (${actual.slice(0, 12)}…)`);

  rmSync(OUT_DIR, { recursive: true, force: true });
  mkdirSync(OUT_DIR, { recursive: true });
  const tmpTar = join(OUT_DIR, ASSET);
  await pipeline(Readable.from(tarball), createWriteStream(tmpTar));
  // uv tarball extracts to uv-aarch64-apple-darwin/{uv,uvx}; strip that dir so we get OUT_DIR/uv
  execFileSync('tar', ['-xzf', tmpTar, '--strip-components=1', '-C', OUT_DIR], { stdio: 'inherit' });
  rmSync(tmpTar, { force: true });

  const uv = join(OUT_DIR, 'uv');
  if (!existsSync(uv)) throw new Error(`extraction did not produce ${uv}`);
  const ver = execFileSync(uv, ['--version'], { encoding: 'utf8' }).trim();
  console.log(`[fetch-uv] extracted ${ver} -> ${uv}`);
  writeFileSync(STAMP, tag + '\n');
  console.log(`[fetch-uv] done (${tag})`);
}

main().catch((err) => {
  console.error(`[fetch-uv] FAILED: ${err.message}`);
  process.exit(1);
});
