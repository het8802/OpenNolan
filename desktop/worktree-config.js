'use strict';

// Keep the Electron shell on the same ports as run-dev and Vite. This module is
// intentionally Electron-free so the contract can be checked with plain Node.
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '..');

function loadWorktreeEnv() {
  const envPath = path.join(repoRoot, '.env.worktree');
  if (!fs.existsSync(envPath)) return;
  for (const rawLine of fs.readFileSync(envPath, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const separator = line.indexOf('=');
    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim().replace(/^['"]|['"]$/g, '');
    if (!(key in process.env)) process.env[key] = value;
  }
}

function requiredPort(name) {
  loadWorktreeEnv();
  const raw = process.env[name];
  const value = Number(raw);
  if (!raw || !Number.isInteger(value) || value < 1 || value > 65535) {
    throw new Error(`${name} must be a valid port; run scripts/dev setup for this worktree`);
  }
  return value;
}

function backendPort() {
  return requiredPort('OPENNOLAN_BACKEND_PORT');
}

function frontendPort() {
  return requiredPort('OPENNOLAN_FRONTEND_PORT');
}

function frontendUrl() {
  return `http://localhost:${frontendPort()}`;
}

module.exports = { backendPort, frontendPort, frontendUrl };
