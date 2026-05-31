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

import os
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from lib.pipeline_loader import get_stage_order, list_pipelines, load_pipeline
from lib.project import (
    ASSET_SUBDIRS,
    ProjectExistsError,
    create_project,
    list_projects,
    read_project_manifest,
    sanitize_filename,
)
from server.state import FileStateSource, StateSource


class CreateProjectRequest(BaseModel):
    name: str
    pipeline_type: str

REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_projects_dir() -> Path:
    return Path(os.environ.get("OPENMONTAGE_PROJECTS_DIR", REPO_ROOT / "projects"))


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
) -> FastAPI:
    app = FastAPI(title="OpenMontage Mission Control", version="0.1.0")

    pdir = Path(projects_dir) if projects_dir is not None else _default_projects_dir()
    source = state_source or FileStateSource(pdir)
    cap_provider = capabilities_provider or _default_capabilities

    app.state.projects_dir = pdir
    app.state.state_source = source
    app.state.capabilities_cache = None  # lazily populated, then reused

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
        # Reject unknown pipelines before scaffolding anything.
        if req.pipeline_type not in list_pipelines():
            raise HTTPException(
                status_code=422,
                detail=f"unknown pipeline_type {req.pipeline_type!r}",
            )
        try:
            return create_project(pdir, req.name, req.pipeline_type)
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
        if read_project_manifest(pdir, project_id) is None:
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

    @app.get("/api/projects/{project_id}/state")
    def project_state(project_id: str) -> dict[str, Any]:
        st = source.project_state(project_id)
        if st is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        return st

    @app.get("/api/capabilities")
    def capabilities() -> dict[str, Any]:
        if app.state.capabilities_cache is None:
            try:
                app.state.capabilities_cache = cap_provider()
            except Exception as exc:
                # Surface discovery failure explicitly rather than 500-ing.
                app.state.capabilities_cache = {"error": f"capability discovery failed: {exc}"}
        return app.state.capabilities_cache

    return app


# Default app for `uvicorn server.app:app`.
app = create_app()
