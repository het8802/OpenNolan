import { defineConfig } from 'vite'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const worktreeEnv = path.join(repoRoot, '.env.worktree')
if (fs.existsSync(worktreeEnv)) {
  for (const rawLine of fs.readFileSync(worktreeEnv, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#') || !line.includes('=')) continue
    const separator = line.indexOf('=')
    const key = line.slice(0, separator).trim()
    const value = line.slice(separator + 1).trim().replace(/^['"]|['"]$/g, '')
    if (!(key in process.env)) process.env[key] = value
  }
}

function requiredPort(name) {
  const raw = process.env[name]
  const value = Number(raw)
  if (!raw || !Number.isInteger(value) || value < 1 || value > 65535) {
    throw new Error(`${name} must be a valid port; run scripts/dev setup for this worktree`)
  }
  return value
}

const backendPort = requiredPort('OPENNOLAN_BACKEND_PORT')
const frontendPort = requiredPort('OPENNOLAN_FRONTEND_PORT')

// In dev, the React app calls /api/* and Vite forwards to the FastAPI server.
// Start the backend with:  uvicorn server.app:app --reload   (port 8000)
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: frontendPort,
    strictPort: true,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
    },
  },
  // Vitest: jsdom so component (.jsx) tests can render; pure unit tests don't use the DOM
  // but run fine here too. setup wires @testing-library/jest-dom matchers + per-test cleanup.
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
  },
})
