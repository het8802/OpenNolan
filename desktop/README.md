# OpenNolan Desktop (Electron shell — M1)

A thin Electron wrapper around the local Mission Control backend. It launches the
FastAPI server as a child process and opens the UI in a native window — everything
runs on your machine (BYOK, local render).

## Run it

Install once (downloads Electron):

```bash
cd desktop
npm install
```

Then, from `desktop/`:

```bash
npm start
```

`npm start` auto-builds the web UI first (a `prestart` hook) and serves it
same-origin. If you launch Electron directly without a build, the app shows a
clear "UI not built" dialog instead of a blank window.

This spawns the backend on a free port, waits for `GET /api/health`, and opens the
window at `http://127.0.0.1:<port>`. Because the backend serves the UI same-origin,
the frontend's relative `/api` calls work with no CORS and no `file://` issues.

### Dev mode (hot reload)

```bash
# terminal 1 — Vite dev server on :5173
npm --prefix ../web run dev
# terminal 2 — Electron pointed at Vite (spawns the backend on :8000)
npm run dev
```

## API keys (BYOK)

Put keys in the repo-root `.env` (flat `KEY=value`, **no inline comments** — they leak):

- `ANTHROPIC_API_KEY` — powers the agent (the chat panel 503s without it; the rest of the app still works).
- Provider keys as needed: `REPLICATE_API_TOKEN`, `ELEVENLABS_API_KEY`, `FAL_KEY`, `OPENAI_API_KEY`, …

## How it works

- `main.js` spawns `python -m uvicorn server.app:app` using the repo's `.venv`, with
  `cwd` = repo root (the package imports require it), then health-polls before showing the window.
- `server/app.py` mounts `web/dist` as static files (added for this shell); the mount is
  last so `/api/*` routes win, and it's a no-op when `web/dist` is absent (dev).

## Not yet (later milestones)

- **WS4 / M4** — bundle Python + Node (Remotion/HyperFrames) + ffmpeg into a standalone `.app`.
  (Decision: bundle everything. Heaviest piece is Remotion's ~1.3 GB node_modules + chrome-headless-shell.)
  Packaging must also move `OPENNOLAN_PROJECTS_DIR` to a user-writable path (e.g.
  `~/Library/Application Support/OpenNolan`); today it defaults to `<repo>/projects`,
  which is read-only inside a signed `.app`.
- **WS1** — first-run key-entry screen that writes `.env`.
- **WS3 / M3** — the rebuilt editing-software UI (the current `web/src/editor/` is not reused).

**Known MVP limitation:** a *force-quit* of Electron (SIGKILL / crash) can orphan the
backend child (it holds its port until you log out). A normal quit reaps it cleanly
(SIGTERM→SIGKILL). Prod relaunch is unaffected (fresh free port each time); dev relaunch
reuses a healthy `:8000`.
