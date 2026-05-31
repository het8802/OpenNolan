# OpenMontage Mission Control (web)

Local single-user UI for OpenMontage: create projects, pick a pipeline, upload
assets, watch the pipeline run live, and chat with the agent.

## Run it

Two processes, two terminals (from the repo root):

```bash
# 1. Backend (FastAPI) on :8000
pip install -r requirements-ui.txt
uvicorn server.app:app --reload

# 2. Frontend (Vite) on :5173 — proxies /api -> :8000
cd web
npm install
npm run dev
```

Open http://localhost:5173.

## Driving the agent (chat panel)

The agent runner uses the Claude Agent SDK. To use your Claude subscription
(instead of per-token API billing):

```bash
claude setup-token            # prints a 1-year OAuth token
export CLAUDE_CODE_OAUTH_TOKEN="<token>"
unset ANTHROPIC_API_KEY        # it takes precedence and would bill per-token
```

Then restart the backend. Until a token is set, `/chat` returns a 503 with this
guidance and the rest of the UI (projects, pipelines, state, uploads) still works.

## What talks to what

```
Browser (Vite :5173) ──/api/* (proxied)──▶ FastAPI (:8000) ──▶ lib/ + tools/
  chat panel ──POST /chat (SSE)──────────▶ AgentRunner ──▶ Claude Agent SDK
  stepper    ──GET  /state (poll 1.5s)───▶ checkpoints on disk
  assets     ──POST /assets──────────────▶ projects/<id>/assets/<kind>/
```
