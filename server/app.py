"""FastAPI read layer for the Mission Control UI (v1: read-only).

Wraps the existing libs; owns no orchestration. Endpoints:

  GET /api/health                  -> liveness + which projects dir is in use
  GET /api/pipelines               -> available pipelines + their stage order
  GET /api/pipelines/{name}        -> full pipeline manifest
  GET /api/projects                -> manifests of real projects (manifest-filtered)
  GET /api/projects/{id}/state     -> per-stage checkpoint status (via StateSource)
  GET /api/capabilities            -> provider/tool menu (discovered once, cached)

Write paths (create project, upload assets, gate approval) and the agent
runner are deliberately NOT here yet — this is the read slice.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}


def _classify(rel_parts: tuple[str, ...], ext: str) -> Optional[str]:
    ext = ext.lower()
    if ext in IMAGE_EXTS:
        return "images"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "music" if "music" in rel_parts else "audio"
    return None

from lib.env_loader import load_env
from lib.pipeline_loader import get_stage_order, list_pipelines, load_pipeline
from lib.project import (
    ASSET_SUBDIRS,
    ProjectExistsError,
    create_project,
    get_project_record,
    list_projects,
    sanitize_filename,
)
from server import activity as activity_mod
from server import artifacts as artifacts_mod
from server import editor as editor_mod
from server import threads as thread_store
from server.agent_runner import AgentRunner, auth_configured
from server.render_jobs import RenderJobStore
from server.state import FileStateSource, StateSource


class CreateProjectRequest(BaseModel):
    name: str
    pipeline_type: Optional[str] = None  # None/empty -> the agent picks one


class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None


class ThreadCreate(BaseModel):
    title: Optional[str] = None


class ThreadSave(BaseModel):
    messages: list[Any] = []
    session_id: Optional[str] = None
    title: Optional[str] = None


class ConfirmRequest(BaseModel):
    confirm_id: str
    approved: bool


class AnswerRequest(BaseModel):
    question_id: str
    answer: str

REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_projects_dir() -> Path:
    return Path(os.environ.get("OPENNOLAN_PROJECTS_DIR", REPO_ROOT / "projects"))


def _default_capabilities() -> dict[str, Any]:
    """Discover tools and return the human-ready provider menu.

    Called at most once per app (the result is cached on app.state), so the
    expensive registry.discover() (it imports every tool module) never runs
    per request. Returns counts + install instructions only — never raw key
    or env values.
    """
    from tools.tool_registry import registry

    registry.discover()
    return registry.provider_menu_summary()


def create_app(
    projects_dir: Path | str | None = None,
    *,
    state_source: Optional[StateSource] = None,
    capabilities_provider: Optional[Callable[[], dict[str, Any]]] = None,
    agent_runner: Optional[AgentRunner] = None,
) -> FastAPI:
    # Load .env so the backend picks up agent auth (CLAUDE_CODE_OAUTH_TOKEN /
    # ANTHROPIC_API_KEY) and provider keys the same way every tool does via
    # lib.env_loader. Without this the token had to be exported in the launching
    # shell, so a plain `uvicorn` restart silently dropped auth → chat 503s.
    # load_dotenv does NOT override vars already set in the environment, so an
    # explicitly-exported token still wins.
    load_env()

    app = FastAPI(title="OpenNolan Mission Control", version="0.1.0")

    pdir = Path(projects_dir) if projects_dir is not None else _default_projects_dir()
    source = state_source or FileStateSource(pdir)
    cap_provider = capabilities_provider or _default_capabilities

    app.state.projects_dir = pdir
    app.state.state_source = source
    app.state.capabilities_cache = None  # lazily populated, then reused
    app.state.agent_runner = agent_runner  # injected (tests) or lazily built
    app.state.render_store = None  # editor render-job runner, lazily built

    def _runner() -> Optional[AgentRunner]:
        if app.state.agent_runner is None and auth_configured():
            app.state.agent_runner = AgentRunner(repo_root=REPO_ROOT)
        return app.state.agent_runner

    def _render_store() -> RenderJobStore:
        if app.state.render_store is None:
            app.state.render_store = RenderJobStore(pdir)
        return app.state.render_store

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "projects_dir": str(pdir)}

    @app.get("/api/pipelines")
    def pipelines() -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        for name in list_pipelines():
            try:
                manifest = load_pipeline(name)
                out.append({
                    "name": name,
                    "description": (manifest.get("description") or "").strip(),
                    "stability": manifest.get("stability"),
                    "stages": get_stage_order(manifest),
                })
            except Exception as exc:  # a broken manifest shouldn't hide the others
                out.append({"name": name, "error": str(exc)[:200]})
        return {"pipelines": out}

    @app.get("/api/pipelines/{name}")
    def pipeline_detail(name: str) -> dict[str, Any]:
        try:
            return load_pipeline(name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"pipeline {name!r} not found")

    @app.get("/api/projects")
    def projects() -> dict[str, Any]:
        return {"projects": list_projects(pdir)}

    @app.post("/api/projects", status_code=201)
    def new_project(req: CreateProjectRequest) -> dict[str, Any]:
        # pipeline_type is optional: omit it and the agent chooses one on its
        # first turn. If provided, it must be a known pipeline.
        pt = (req.pipeline_type or "").strip() or None
        if pt is not None and pt not in list_pipelines():
            raise HTTPException(
                status_code=422,
                detail=f"unknown pipeline_type {pt!r}",
            )
        try:
            return create_project(pdir, req.name, pt)
        except ProjectExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:  # un-sluggable name
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/api/projects/{project_id}/assets", status_code=201)
    def upload_asset(
        project_id: str,
        kind: str = Form(...),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        if kind not in ASSET_SUBDIRS:
            raise HTTPException(
                status_code=422,
                detail=f"invalid asset kind {kind!r}; expected one of {list(ASSET_SUBDIRS)}",
            )

        kind_dir = pdir / project_id / "assets" / kind
        try:
            safe_name = sanitize_filename(file.filename or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        target = (kind_dir / safe_name).resolve()
        base = kind_dir.resolve()
        # Defense in depth: the resolved target must stay inside the kind dir.
        if target != base and base not in target.parents:
            raise HTTPException(status_code=400, detail="path traversal detected")

        kind_dir.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as out:
            shutil.copyfileobj(file.file, out)

        return {
            "project_id": project_id,
            "kind": kind,
            "filename": safe_name,
            "path": str(target.relative_to(pdir.resolve())),
            "size_bytes": target.stat().st_size,
        }

    @app.get("/api/projects/{project_id}/assets")
    def list_assets(project_id: str) -> dict[str, Any]:
        """List a project's asset files (grouped by kind) and rendered outputs.
        Paths are relative to the project dir; fetch a file via /file?path=..."""
        proj = pdir / project_id
        if not proj.exists():
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")

        kinds: dict[str, list[dict[str, Any]]] = {"images": [], "video": [], "audio": [], "music": []}
        assets_dir = proj / "assets"
        if assets_dir.is_dir():
            for f in sorted(assets_dir.rglob("*")):
                if not f.is_file() or f.name.startswith("."):
                    continue
                rel = f.relative_to(proj)
                kind = _classify(rel.parts, f.suffix)
                if kind:
                    stat = f.stat()
                    kinds[kind].append({"path": str(rel), "name": f.name,
                                        "size_bytes": stat.st_size, "mtime": int(stat.st_mtime)})

        renders: list[dict[str, Any]] = []
        renders_dir = proj / "renders"
        if renders_dir.is_dir():
            for f in sorted(renders_dir.rglob("*")):
                if f.is_file() and f.suffix.lower() in VIDEO_EXTS and not f.name.startswith("."):
                    stat = f.stat()
                    # mtime is the cache-bust/remount key the UI uses so a freshly
                    # finished (or re-rendered) MP4 reloads without a page refresh.
                    renders.append({"path": str(f.relative_to(proj)), "name": f.name,
                                    "size_bytes": stat.st_size, "mtime": int(stat.st_mtime)})

        return {"project_id": project_id, "kinds": kinds, "renders": renders}

    @app.get("/api/projects/{project_id}/file")
    def get_file(project_id: str, path: str):
        """Serve a single file from within the project dir (images/video/audio/render).
        Path-traversal protected: the resolved target must stay inside the project."""
        proj = (pdir / project_id).resolve()
        target = (pdir / project_id / path).resolve()
        if proj != target and proj not in target.parents:
            raise HTTPException(status_code=400, detail="path traversal detected")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(str(target))

    @app.get("/api/projects/{project_id}/state")
    def project_state(project_id: str) -> dict[str, Any]:
        st = source.project_state(project_id)
        if st is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        return st

    @app.get("/api/projects/{project_id}/artifacts")
    def list_artifacts(project_id: str) -> dict[str, Any]:
        """Artifact manifest grouped by pipeline stage (+ the cross-cutting
        decision_log summary). Fetch one artifact's content via /artifacts/{key}."""
        manifest = artifacts_mod.list_artifacts(pdir, project_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        return manifest

    @app.get("/api/projects/{project_id}/artifacts/{key}")
    def get_artifact(project_id: str, key: str) -> dict[str, Any]:
        """Parsed content of a single artifact, addressed by its key (e.g.
        scene_plan, decision_log). Key is a safe slug — no path traversal."""
        try:
            art = artifacts_mod.read_artifact(pdir, project_id, key)
        except artifacts_mod.BadArtifactKey as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if art is None:
            raise HTTPException(status_code=404, detail=f"artifact {key!r} not found")
        return art

    # ── Manual editor: edit_decisions read/write + render jobs ────────────
    # The editor is the human-driven exception to agent-first orchestration.
    # Writes go through the same schema gate as the agent; renders reuse the
    # exact video_compose path the compose stage uses.

    @app.get("/api/projects/{project_id}/edit_decisions")
    def get_edit_decisions(project_id: str) -> dict[str, Any]:
        """The editable timeline spec. `content` is null for a project with none yet
        (the UI scaffolds a minimal valid doc rather than erroring)."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        return {"project_id": project_id, "content": editor_mod.read_edit_decisions(pdir, project_id)}

    @app.put("/api/projects/{project_id}/edit_decisions")
    def put_edit_decisions(project_id: str, doc: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Validate against the edit_decisions schema and atomically write.
        Returns 422 with the validation message on schema failure (file untouched)."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        try:
            editor_mod.write_edit_decisions(pdir, project_id, doc)
        except editor_mod.EditDecisionsInvalid as exc:
            raise HTTPException(status_code=422, detail=str(exc)[:1500])
        return {"project_id": project_id, "saved": True}

    @app.post("/api/projects/{project_id}/render", status_code=202)
    def start_render(project_id: str) -> dict[str, Any]:
        """Start a background render of the saved edit_decisions. Returns a job_id to poll.
        Supersedes any in-flight render for this project."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        job_id = _render_store().start(project_id)
        return {"project_id": project_id, "job_id": job_id, "status": "queued"}

    @app.get("/api/projects/{project_id}/render/{job_id}")
    def render_status(project_id: str, job_id: str) -> dict[str, Any]:
        """Poll a render job: {status: queued|running|done|failed, output_path?, error?}."""
        st = _render_store().status(job_id)
        if st is None or st.get("project_id") != project_id:
            raise HTTPException(status_code=404, detail=f"render job {job_id!r} not found")
        return st

    @app.get("/api/projects/{project_id}/frame")
    def get_frame(project_id: str, path: str, t: float = 0.0):
        """Extract a single still at time `t` from a project video (cheap scrub preview).
        Path-traversal protected, exactly like /file."""
        proj = (pdir / project_id).resolve()
        target = (pdir / project_id / path).resolve()
        if proj != target and proj not in target.parents:
            raise HTTPException(status_code=400, detail="path traversal detected")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        if shutil.which("ffmpeg") is None:
            raise HTTPException(status_code=503, detail="ffmpeg not available for frame extraction")
        out = Path(tempfile.mkstemp(suffix=".jpg")[1])
        proc = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(max(0.0, t)), "-i", str(target),
             "-frames:v", "1", "-q:v", "3", str(out)],
            capture_output=True,
        )
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            raise HTTPException(status_code=500, detail="frame extraction failed")
        return FileResponse(str(out), media_type="image/jpeg")

    @app.get("/api/projects/{project_id}/source")
    def get_source(project_id: str, ref: str):
        """Serve a cut's SOURCE clip for live (pre-render) scrub preview.

        `ref` is a cut's `source` value (asset_id or path); resolution mirrors video_compose
        and is confined to the project dir. FileResponse honors Range requests, so the
        browser can seek the <video> for smooth scrubbing without a render."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        target = editor_mod.resolve_source_path(pdir, project_id, ref)
        if target is None:
            raise HTTPException(status_code=404, detail="source not found within project")
        return FileResponse(str(target))

    @app.get("/api/projects/{project_id}/source_meta")
    def source_meta(project_id: str, ref: str) -> dict[str, Any]:
        """Probe a source clip's duration + dimensions (for trim bounds and scrub math).

        `duration` is null if ffprobe is unavailable — trimming still works, just unclamped."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        target = editor_mod.resolve_source_path(pdir, project_id, ref)
        if target is None:
            raise HTTPException(status_code=404, detail="source not found within project")
        rel = str(target.relative_to((pdir / project_id).resolve()))
        meta: dict[str, Any] = {
            "project_id": project_id, "ref": ref, "path": rel,
            "duration": None, "width": None, "height": None,
        }
        if shutil.which("ffprobe") is not None:
            proc = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-show_entries", "format=duration",
                 "-of", "json", str(target)],
                capture_output=True, text=True,
            )
            if proc.returncode == 0:
                try:
                    data = json.loads(proc.stdout)
                    dur = (data.get("format") or {}).get("duration")
                    meta["duration"] = float(dur) if dur is not None else None
                    streams = data.get("streams") or []
                    if streams:
                        meta["width"] = streams[0].get("width")
                        meta["height"] = streams[0].get("height")
                except (ValueError, KeyError, TypeError):
                    pass
        return meta

    @app.get("/api/projects/{project_id}/activity")
    def project_activity(
        project_id: str, limit: Optional[int] = None, since: Optional[str] = None
    ) -> dict[str, Any]:
        """The agent's persisted tool-activity log (files touched, skills run,
        tools used) plus a synthesized 'how this was made' summary."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        return activity_mod.read_activity(pdir, project_id, limit=limit, since=since)

    @app.get("/api/capabilities")
    def capabilities() -> dict[str, Any]:
        if app.state.capabilities_cache is None:
            try:
                app.state.capabilities_cache = cap_provider()
            except Exception as exc:
                # Surface discovery failure explicitly rather than 500-ing.
                app.state.capabilities_cache = {"error": f"capability discovery failed: {exc}"}
        return app.state.capabilities_cache

    @app.post("/api/projects/{project_id}/chat")
    async def chat(project_id: str, body: ChatRequest):
        """Stream an agent turn as Server-Sent Events.

        Requires agent auth (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY);
        returns 503 with setup guidance otherwise. Each agent event is one
        `data: {json}` SSE line; a `confirm_request` event pauses the agent
        until the client POSTs /agent/confirm.
        """
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        if not auth_configured():
            raise HTTPException(
                status_code=503,
                detail=(
                    "agent auth not configured. Easiest fix: install and log into "
                    "Claude Code on this machine (`npm i -g @anthropic-ai/claude-code`, "
                    "then `claude` to log in) — the agent then uses your subscription "
                    "automatically. Or run `claude setup-token` and put "
                    "CLAUDE_CODE_OAUTH_TOKEN in .env (unset ANTHROPIC_API_KEY to avoid "
                    "per-token billing)."
                ),
            )
        runner = _runner()
        if runner is None:
            raise HTTPException(status_code=503, detail="agent runner unavailable")

        # If continuing a stored thread, align the live session to it so the agent
        # resumes that conversation (its context), not whatever ran last.
        if body.thread_id:
            thread = thread_store.get_thread(pdir, project_id, body.thread_id)
            await runner.switch_session(project_id, thread.get("session_id") if thread else None)

        queue: asyncio.Queue = asyncio.Queue()

        async def emit(evt: dict[str, Any]) -> None:
            await queue.put(evt)

        async def drive() -> None:
            try:
                await runner.run_turn(project_id, body.message, on_event=emit)
            except Exception as exc:  # surface runner failure as an SSE event
                await queue.put({"type": "error", "detail": str(exc)[:500]})
            finally:
                await queue.put(None)  # sentinel: stream complete

        async def gen():
            task = asyncio.create_task(drive())
            try:
                while True:
                    evt = await queue.get()
                    if evt is None:
                        break
                    yield f"data: {json.dumps(evt)}\n\n"
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/projects/{project_id}/agent/confirm")
    def agent_confirm(project_id: str, body: ConfirmRequest) -> dict[str, Any]:
        runner = app.state.agent_runner
        if runner is None:
            raise HTTPException(status_code=409, detail="no active agent runner")
        return {"resolved": runner.resolve_confirm(body.confirm_id, body.approved)}

    @app.post("/api/projects/{project_id}/agent/answer")
    def agent_answer(project_id: str, body: AnswerRequest) -> dict[str, Any]:
        runner = app.state.agent_runner
        if runner is None:
            raise HTTPException(status_code=409, detail="no active agent runner")
        return {"resolved": runner.resolve_answer(body.question_id, body.answer)}

    @app.post("/api/projects/{project_id}/agent/stop")
    async def agent_stop(project_id: str) -> dict[str, Any]:
        """Interrupt the agent mid-turn. Context is preserved; the next message
        resumes normally. No-op (stopped=False) if nothing is running."""
        runner = app.state.agent_runner
        if runner is None:
            raise HTTPException(status_code=409, detail="no active agent runner")
        return {"stopped": await runner.interrupt(project_id)}

    # ── Chat threads (history + revival) ──────────────────────────────────
    def _require_project(project_id: str) -> None:
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")

    @app.get("/api/projects/{project_id}/threads")
    def list_threads(project_id: str) -> dict[str, Any]:
        return {"threads": thread_store.list_threads(pdir, project_id)}

    @app.post("/api/projects/{project_id}/threads", status_code=201)
    def create_thread(project_id: str, body: ThreadCreate) -> dict[str, Any]:
        _require_project(project_id)
        return thread_store.create_thread(pdir, project_id, title=body.title or "New chat")

    @app.get("/api/projects/{project_id}/threads/{thread_id}")
    def get_thread(project_id: str, thread_id: str) -> dict[str, Any]:
        rec = thread_store.get_thread(pdir, project_id, thread_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"thread {thread_id!r} not found")
        return rec

    @app.put("/api/projects/{project_id}/threads/{thread_id}")
    def save_thread(project_id: str, thread_id: str, body: ThreadSave) -> dict[str, Any]:
        return thread_store.save_thread(
            pdir, project_id, thread_id,
            messages=body.messages, session_id=body.session_id, title=body.title,
        )

    # Serve the built Mission Control UI (web/dist) for the packaged desktop app.
    # Mounted LAST so every /api/* route above takes precedence; the SPA and its
    # assets are then same-origin with the API (no CORS) when Electron loads
    # http://127.0.0.1:<port>/. No-op in dev: when web/dist is absent, Vite serves
    # the SPA on :5173 and proxies /api here. (html=True serves index.html at "/".)
    #
    # NOTE: html=True does NOT catch-all unknown paths -> index.html. The current UI
    # has no client-side router, so every load hits "/" and this is fine. If WS3 adds
    # a history-API router with deep links, add a fallback returning
    # FileResponse(web_dist / "index.html") for non-/api 404s.
    web_dist = REPO_ROOT / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="ui")

    return app


# Default app for `uvicorn server.app:app`.
app = create_app()
