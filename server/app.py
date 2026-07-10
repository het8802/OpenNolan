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

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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
from server import analytics as analytics_mod
from server import artifacts as artifacts_mod
from server import settings as settings_mod
from server import auth as auth_mod
from server import debug_log as debug_log_mod
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
    model: Optional[str] = None   # UI-selected agent model (validated against AGENT_MODELS)


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


class ProvideKeyRequest(BaseModel):
    # Answer to an agent `request_api_key` prompt. `skipped=True` declines (no write); otherwise
    # `value` is saved to the BYOK .env under `env_var` before the agent's tool is unblocked.
    key_request_id: str
    env_var: str
    value: Optional[str] = None
    skipped: bool = False


class ProvideCapabilityRequest(BaseModel):
    # Answer to an agent `request_capability` prompt. The UI streams the install itself; this only
    # unblocks the waiting tool. installed=true → agent retries; false → declined/failed, agent skips.
    cap_request_id: str
    installed: bool = False


class EnvUpdateRequest(BaseModel):
    # BYOK: {VARIABLE_NAME: value} edits to persist to the local .env (empty value = leave blank).
    vars: dict[str, str]


class AnalyticsUpdateRequest(BaseModel):
    # True = opt OUT of product analytics.
    disabled: bool


class FeedbackRequest(BaseModel):
    kind: str  # "bug" | "feature" | "other"
    message: str
    email: Optional[str] = None       # optional, so we can reply
    diagnostics: Optional[str] = None  # optional client-attached logs/context
    debug_session: Optional[str] = None  # attach a recorded UI session's analysis (editor debug report)


class ClientErrorReport(BaseModel):
    # A JS/renderer error posted by web/src/main.jsx (window.onerror / ErrorBoundary) or by the
    # Electron shell. Forwarded to PostHog Error Tracking; body is redacted server-side.
    source: str                        # e.g. "renderer", "unhandledrejection", "react-boundary"
    message: str
    stack: Optional[str] = None
    context: Optional[dict[str, Any]] = None


class DebugLogBody(BaseModel):
    # A batch from the editor's UI session recorder (web/src/debug/recorder.js).
    session: str
    events: list[dict[str, Any]] = []


class OAuthFinishRequest(BaseModel):
    # The `code#state` string the user copies from the Claude sign-in page.
    code: str


class ApiKeyRequest(BaseModel):
    # An Anthropic API key (fallback to "Sign in with Claude"). Verified live before it is saved.
    api_key: str

from lib import app_paths

# Read-only code root the agent runs against (repo checkout in dev; app-bundle Resources in prod).
REPO_ROOT = app_paths.code_root()


def _default_projects_dir() -> Path:
    # Writable projects tree — repo/projects in dev, App-Support/projects in the packaged app.
    return app_paths.projects_dir()


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

    @app.exception_handler(Exception)
    async def _report_unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all for unhandled route errors: report to PostHog (no-op when opted out / under
        pytest) so backend crashes are visible, then return a clean 500. HTTPException has its own
        handler and never reaches here. Streaming routes that already started a response handle
        their own errors (see the chat SSE `drive()` below)."""
        analytics_mod.capture_exception(exc, {"path": request.url.path, "method": request.method})
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    pdir = Path(projects_dir) if projects_dir is not None else _default_projects_dir()
    source = state_source or FileStateSource(pdir)
    cap_provider = capabilities_provider or _default_capabilities

    app.state.projects_dir = pdir
    app.state.state_source = source
    app.state.capabilities_cache = None  # lazily populated, then reused
    app.state.agent_runner = agent_runner  # injected (tests) or lazily built
    app.state.render_store = None  # editor render-job runner, lazily built

    # One product event per backend boot. No-op when opted out / posthog absent / under pytest.
    import platform
    analytics_mod.capture("app_opened", {"os": platform.platform(), "app_version": app.version})

    # First launch on this install ≈ an install/"download" (a fresh install has a new settings.json,
    # so this fires once per machine). The website "Download" button is still a waitlist placeholder,
    # so this is the real install signal until a downloadable build ships.
    if not settings_mod.get("app_first_run_done", False):
        analytics_mod.capture("app_first_run", {"os": platform.platform(), "app_version": app.version})
        settings_mod.set_value("app_first_run_done", True)

    def _render_store() -> RenderJobStore:
        if app.state.render_store is None:
            app.state.render_store = RenderJobStore(pdir)
        return app.state.render_store

    def _runner() -> Optional[AgentRunner]:
        if app.state.agent_runner is None and auth_configured():
            # Share ONE RenderJobStore with the editor so the agent's in-process
            # `render` tool runs through it (tracked/superseded), instead of the old
            # background-Bash render that broke turn attribution.
            app.state.agent_runner = AgentRunner(
                repo_root=REPO_ROOT, projects_dir=pdir, render_store=_render_store()
            )
        return app.state.agent_runner

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
        # pipeline_type is optional: omit it and (in dev) the agent chooses one on
        # its first turn. If provided, it must be an AVAILABLE pipeline.
        pt = (req.pipeline_type or "").strip() or None
        available = list_pipelines()  # packaged-filtered to the single pipeline
        # In the packaged app there is exactly one pipeline — pin it so there is
        # no "agent picks" path and the project can never land on another pipeline.
        if pt is None and app_paths.is_packaged() and available:
            pt = available[0]
        if pt is not None and pt not in available:
            raise HTTPException(
                status_code=422,
                detail=f"unknown pipeline_type {pt!r}",
            )
        try:
            created = create_project(pdir, req.name, pt)
            analytics_mod.capture("project_created", {"pipeline_type": pt})
            return created
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
        Paths are relative to the project dir; fetch a file via /file?path=...

        Three buckets, kept distinct on purpose:
          - kinds       — user-managed source assets under assets/ (images/video/audio/music).
          - renders     — the editor's FINAL output(s) under renders/ (final.mp4, proxies, etc.);
                          the dashboard surfaces these as the "Final render" player.
          - agent_renders — the AGENT's intermediate HyperFrames clips under hf/renders/ (the
                          building blocks the editor drops onto the timeline). See AGENT_GUIDE.md
                          "Project Directory Convention" — any HyperFrames-rendering pipeline lands
                          per-scene clips here, so the editor's Renders tab is pipeline-agnostic."""
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
            # NON-recursive on purpose: only top-level renders/ files are deliverables.
            # Subdirs hold render-engine internals — renders/proxies/ (content-keyed
            # per-scene proxy cache) and renders/.final_review_frames/ — which are NOT
            # final renders. rglob swept those in and the dashboard showed every proxy
            # clip as a "Final render". Deliverables live directly in renders/ (final.mp4).
            for f in sorted(renders_dir.glob("*")):
                if f.is_file() and f.suffix.lower() in VIDEO_EXTS and not f.name.startswith("."):
                    stat = f.stat()
                    # mtime is the cache-bust/remount key the UI uses so a freshly
                    # finished (or re-rendered) MP4 reloads without a page refresh.
                    renders.append({"path": str(f.relative_to(proj)), "name": f.name,
                                    "size_bytes": stat.st_size, "mtime": int(stat.st_mtime)})

        # Agent-rendered HyperFrames clips: the intermediate building blocks the agent
        # produces under hf/renders/ (separate from the editor's final output in renders/).
        # mtime doubles as the cache-bust/remount key, same as renders above.
        agent_renders: list[dict[str, Any]] = []
        hf_renders_dir = proj / "hf" / "renders"
        if hf_renders_dir.is_dir():
            for f in sorted(hf_renders_dir.rglob("*")):
                if f.is_file() and f.suffix.lower() in VIDEO_EXTS and not f.name.startswith("."):
                    stat = f.stat()
                    agent_renders.append({"path": str(f.relative_to(proj)), "name": f.name,
                                          "size_bytes": stat.st_size, "mtime": int(stat.st_mtime)})

        return {"project_id": project_id, "kinds": kinds, "renders": renders,
                "agent_renders": agent_renders}

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
        browser can seek the <video> for smooth scrubbing without a render.

        ProRes .mov overlays (HyperFrames alpha renders) are transcoded on first request to
        VP9/WebM with alpha preserved — the only alpha-capable format Chromium can decode.
        The proxy is cached in <project>/.browser_cache/ for instant subsequent loads."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        target = editor_mod.resolve_source_path(pdir, project_id, ref)
        if target is None:
            raise HTTPException(status_code=404, detail="source not found within project")
        cache_dir = Path(pdir) / project_id / ".browser_cache"
        preview = editor_mod.browser_preview_path(target, cache_dir)
        if preview is not None:
            return FileResponse(str(preview), media_type="video/webm")
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
        try:
            rel = str(target.relative_to((pdir / project_id).resolve()))
        except ValueError:
            rel = str(target)  # a shared in-repo asset (sfx/kit) resolved outside the project

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
                # Surface discovery failure explicitly rather than 500-ing — and report it, since a
                # swallowed discovery failure means the whole tool/provider menu is silently empty.
                analytics_mod.capture_exception(exc, {"where": "capability_discovery"})
                app.state.capabilities_cache = {"error": f"capability discovery failed: {exc}"}
        return app.state.capabilities_cache

    @app.get("/api/env")
    def get_env() -> dict[str, Any]:
        """BYOK: the curated variable menu + each var's current value from the local .env."""
        from server import env_config
        return {"path": str(env_config.ENV_PATH), "vars": env_config.list_env_vars()}

    @app.put("/api/env")
    def put_env(body: EnvUpdateRequest) -> dict[str, Any]:
        """BYOK: persist edited variables back to the local .env, then reload them for this session."""
        from server import env_config
        try:
            changed = env_config.write_env_vars(body.vars)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        env_config.reload_env()  # so the next agent turn / tool subprocess sees the new keys
        return {"changed": changed, "path": str(env_config.ENV_PATH), "vars": env_config.list_env_vars()}

    # ── Anthropic account auth ("Sign in with Claude" / API-key fallback) ──────────
    @app.get("/api/auth/status")
    def auth_status() -> dict[str, Any]:
        """Whether the agent can reach the user's Anthropic account, and whether a reconnect is
        needed (expired/revoked token, rejected key). Polled by the UI to drive the sign-in CTA,
        the top-right re-auth button, and the in-chat reconnect box."""
        return auth_mod.status()

    @app.post("/api/auth/oauth/start")
    def auth_oauth_start() -> dict[str, Any]:
        """Begin the PKCE flow; returns the claude.ai authorize URL to open in the browser."""
        return auth_mod.start_oauth()

    @app.post("/api/auth/oauth/finish")
    def auth_oauth_finish(body: OAuthFinishRequest) -> dict[str, Any]:
        """Exchange the pasted `code#state` for an OAuth token and persist it."""
        try:
            result = auth_mod.finish_oauth(body.code, app_state=app.state)
        except auth_mod.AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        analytics_mod.capture("auth_connected", {"method": "oauth"})
        return result

    @app.post("/api/auth/api-key")
    def auth_api_key(body: ApiKeyRequest) -> dict[str, Any]:
        """Verify an Anthropic API key with a live call, then persist it (fallback path)."""
        try:
            result = auth_mod.set_api_key(body.api_key, app_state=app.state)
        except auth_mod.AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        analytics_mod.capture("auth_connected", {"method": "api_key"})
        return result

    @app.post("/api/auth/disconnect")
    def auth_disconnect() -> dict[str, Any]:
        """Forget the stored Anthropic credential."""
        return auth_mod.disconnect(app_state=app.state)

    @app.get("/api/settings/analytics")
    def get_analytics_settings() -> dict[str, Any]:
        """Analytics opt-out state + the anonymous device id (no PII). Drives the settings toggle."""
        return {
            "disabled": bool(settings_mod.get("analytics_disabled", False)),
            "device_id": settings_mod.device_id(),
        }

    @app.put("/api/settings/analytics")
    def put_analytics_settings(body: AnalyticsUpdateRequest) -> dict[str, Any]:
        """Flip the opt-out. Persisted to settings.json; the analytics client is torn down and its
        init memo cleared so the change takes effect immediately (no client exists while opted out)."""
        settings_mod.set_value("analytics_disabled", bool(body.disabled))
        analytics_mod.shutdown()
        analytics_mod.reset()
        return {"disabled": bool(body.disabled)}

    @app.post("/api/feedback")
    def post_feedback(body: FeedbackRequest) -> dict[str, Any]:
        """In-app bug / feature feedback. Always stored locally (never lost); a PostHog event is
        emitted (metadata only); emailed via Resend when configured. See server/feedback.py."""
        from server import feedback as feedback_mod
        try:
            return feedback_mod.submit(
                body.kind, body.message, body.email, body.diagnostics, body.debug_session
            )
        except feedback_mod.FeedbackError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/telemetry/error")
    def post_client_error(body: ClientErrorReport) -> dict[str, Any]:
        """Report a frontend (React) / Electron error to PostHog Error Tracking. Always 200 — a
        no-op when analytics is opted out; reporting must never itself error into the client."""
        analytics_mod.capture_client_error(body.source, body.message, body.stack, body.context)
        return {"received": True}

    @app.get("/api/doctor")
    def get_doctor() -> dict[str, Any]:
        """First-run provisioning status: is the managed venv/core/ffmpeg present, which capability
        packs are installed. Drives the setup UI + the 'install pack' prompts. See lib/provision.py."""
        from lib import provision
        return provision.doctor()

    def _stream_provision(work, done_extra: Optional[dict] = None) -> StreamingResponse:
        """Run a provisioning `work(progress)` in a worker thread, streaming NDJSON log/done/error frames
        so the UI can show progress without the (multi-minute) install holding the event loop. Shared by
        the capability-pack and composition-tier install endpoints."""
        import queue
        import threading

        q: "queue.Queue[Optional[str]]" = queue.Queue()

        def worker() -> None:
            try:
                work(lambda line: q.put(json.dumps({"type": "log", "line": line})))
                q.put(json.dumps({"type": "done", **(done_extra or {})}))
            except Exception as exc:  # surface a clean error frame to the stream
                q.put(json.dumps({"type": "error", "error": str(exc)}))
            finally:
                q.put(None)  # sentinel

        threading.Thread(target=worker, daemon=True).start()

        def stream():
            while True:
                frame = q.get()
                if frame is None:
                    break
                yield frame + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/api/provision/composition")
    def post_provision_composition() -> StreamingResponse:
        """Install the composition tier (Node engines: Remotion + HyperFrames) into the managed runtime,
        streaming progress as NDJSON. Provisioned eagerly at first run; this is the RETRY path (Settings)
        after a failed/skipped install. Registered BEFORE /api/provision/{pack} so this static path wins
        over the {pack} param route (else 'composition' would be read as an unknown pack -> 404)."""
        from lib import provision
        return _stream_provision(provision.provision_composition, {"tier": "composition"})

    @app.post("/api/provision/{pack}")
    def post_provision(pack: str) -> StreamingResponse:
        """Lazily install a capability pack (whisperx/torch, mediapipe, rembg, librosa, piper) into the
        managed venv, streaming pip progress as NDJSON so the UI can show it."""
        from lib import provision

        if pack not in provision.PACKS:
            raise HTTPException(status_code=404, detail=f"unknown pack {pack!r}")
        return _stream_provision(lambda progress: provision.provision_pack(pack, progress), {"pack": pack})

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

        # Apply the UI-selected model when the client picked one (no-op when
        # unchanged / unknown). Keeps context across a mid-chat model switch.
        if body.model:
            await runner.set_model(project_id, body.model)

        queue: asyncio.Queue = asyncio.Queue()
        # A live turn is the truth about the credential: the model answering clears any prior
        # "reconnect" flag; a turn-level auth failure sets it (and is re-tagged `auth_error` so the
        # UI shows the reconnect box instead of a nondescript red line). See server/auth.py.
        cleared = {"done": False}

        def _evt_text(evt: dict[str, Any]) -> str:
            # `result` is where a ResultMessage carries its (error) text; the rest cover error events.
            return " ".join(str(evt.get(k, "")) for k in ("detail", "text", "error", "message", "result"))

        async def emit(evt: dict[str, Any]) -> None:
            etype = evt.get("type")
            if etype == "assistant" and not cleared["done"]:
                cleared["done"] = True
                auth_mod.clear_auth_error()
            elif etype == "result" and evt.get("is_error"):
                text = _evt_text(evt)
                if auth_mod.classify_auth_error(text):
                    auth_mod.mark_auth_error(text)
                    await queue.put({"type": "auth_error", "detail": text[:400]})
            await queue.put(evt)

        async def drive() -> None:
            try:
                await runner.run_turn(project_id, body.message, on_event=emit)
            except Exception as exc:  # surface runner failure as an SSE event
                detail = str(exc)[:500]
                if auth_mod.classify_auth_error(detail):
                    auth_mod.mark_auth_error(detail)
                    await queue.put({"type": "auth_error", "detail": detail})
                else:
                    # A real agent-turn failure (not an auth reconnect) — report it; this is where
                    # most runtime crashes surface, and the SSE stream already started so the global
                    # exception handler above can't see it.
                    analytics_mod.capture_exception(exc, {"where": "agent_turn"})
                    await queue.put({"type": "error", "detail": detail})
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

    @app.post("/api/projects/{project_id}/agent/provide-key")
    def agent_provide_key(project_id: str, body: ProvideKeyRequest) -> dict[str, Any]:
        """Answer an agent `request_api_key` prompt. On save, persist the key to the BYOK
        .env (so it also appears in the BYOK panel) and reload it so this session's next tool
        subprocess picks it up — THEN unblock the agent's tool. On skip, unblock as declined
        without writing. The raw key is never returned to the agent."""
        runner = app.state.agent_runner
        if runner is None:
            raise HTTPException(status_code=409, detail="no active agent runner")
        from server import env_config

        if body.skipped:
            return {"resolved": runner.resolve_key_request(body.key_request_id, False), "saved": False}

        value = (body.value or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="a non-empty key value is required (or set skipped=true)")
        try:
            changed = env_config.write_env_vars({body.env_var: value})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        env_config.reload_env()  # so this session's next tool subprocess inherits the new key
        # Persist happened; NOW unblock the waiting tool so it retries with the key present.
        resolved = runner.resolve_key_request(body.key_request_id, True)
        return {"resolved": resolved, "saved": True, "changed": changed}

    @app.post("/api/projects/{project_id}/agent/provide-capability")
    def agent_provide_capability(project_id: str, body: ProvideCapabilityRequest) -> dict[str, Any]:
        """Answer an agent `request_capability` prompt. The UI installs the pack itself (by streaming
        /api/provision/{pack}) and then calls this to unblock the waiting tool: installed=true means
        the agent retries; installed=false (declined) means it moves on. This endpoint does NOT run
        the install — it only resolves the agent's await after the UI's install stream finished."""
        runner = app.state.agent_runner
        if runner is None:
            raise HTTPException(status_code=409, detail="no active agent runner")
        resolved = runner.resolve_capability_request(body.cap_request_id, bool(body.installed))
        return {"resolved": resolved}

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

    # ── Dev observability: UI session recorder sink ────────────────────────────
    # The editor's recorder batch-POSTs timestamped events (console, errors, clicks,
    # scrub/seek) here; we append them as NDJSON under .agents/tools/logs/ui-sessions/
    # so the coding agent can read a full, ordered trace of a session to diagnose (and
    # reproduce) intermittent UI bugs. Dev-only sink; never touches project data.
    @app.post("/api/debug/log")
    def debug_log(body: DebugLogBody) -> dict[str, Any]:
        try:
            written = debug_log_mod.append_events(body.session, body.events)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "written": written}

    @app.get("/api/debug/sessions")
    def debug_sessions() -> dict[str, Any]:
        return {"sessions": debug_log_mod.list_sessions()}

    # "Query, don't read": a compact report (histogram + seek anomalies + verbatim errors) so a
    # tool/agent never loads the multi-thousand-line raw NDJSON into context. Pass 'latest'.
    @app.get("/api/debug/sessions/{session}/analyze")
    def debug_analyze(session: str) -> dict[str, Any]:
        if session == "latest":
            latest = debug_log_mod.latest_session()
            if not latest:
                raise HTTPException(status_code=404, detail="no sessions recorded")
            session = latest
        try:
            return debug_log_mod.analyze_session(session)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"session {session!r} not found")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/api/debug/sessions/{session}")
    def debug_discard(session: str) -> dict[str, Any]:
        """Discard a recorded session's logs (the user chose not to send the debug report).
        Idempotent — removing an already-gone session is a 200 with removed=false."""
        try:
            removed = debug_log_mod.delete_session(session)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "removed": removed}

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
