"""In-process render-job runner for the manual editor (single-user, local).

A render can take seconds to a minute+, too long to hold an HTTP request open. So the
editor POSTs to START a job (returns a job_id) and POLLS for status. Jobs run on a daemon
thread; status lives in an in-memory dict. One ACTIVE job per project — a new render
supersedes the prior one (the superseded job's result is discarded). No external queue:
this is a local app, not a cluster.

    POST /render ──▶ start() ──▶ thread:  read edit_decisions + asset_manifest
                       │                  ──▶ VideoCompose.execute(operation="render_proxies")
                       ▼                       (render-once: per-scene cached clips → assemble)
                       │                                    │
                    job_id                                  ▼
    GET /render/{job_id} ──▶ status()  {queued|running|done|failed, output_path?, error?}

Status transitions: queued ─▶ running ─▶ done | failed.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from server.editor import read_asset_manifest, read_edit_decisions


class RenderJobStore:
    """Thread-safe, in-memory store of editor render jobs for one Mission Control app."""

    def __init__(self, projects_dir: Path | str):
        self._projects_dir = Path(projects_dir)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_by_project: dict[str, str] = {}
        self._lock = threading.Lock()
        self._tool: Any = None  # lazily built VideoCompose (importing it discovers nothing heavy)

    # -- public API -----------------------------------------------------------
    def start(self, project_id: str) -> str:
        """Queue a render for `project_id` and return its job_id. Supersedes any prior job."""
        job_id = uuid.uuid4().hex[:12]
        out_name = f"editor_preview_{job_id}.mp4"
        with self._lock:
            self._jobs[job_id] = {"job_id": job_id, "project_id": project_id, "status": "queued"}
            self._active_by_project[project_id] = job_id  # newest job wins
        threading.Thread(
            target=self._run, args=(job_id, project_id, out_name), daemon=True
        ).start()
        return job_id

    def status(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    # -- internals ------------------------------------------------------------
    def _video_compose(self) -> Any:
        if self._tool is None:
            from tools.video.video_compose import VideoCompose
            self._tool = VideoCompose()
        return self._tool

    def _is_superseded(self, project_id: str, job_id: str) -> bool:
        return self._active_by_project.get(project_id) != job_id

    def _set(self, job_id: str, project_id: str, **fields: Any) -> None:
        with self._lock:
            if self._is_superseded(project_id, job_id):
                return  # a newer render replaced this one; drop the result silently
            self._jobs[job_id].update(**fields)

    def _run(self, job_id: str, project_id: str, out_name: str) -> None:
        self._set(job_id, project_id, status="running")
        try:
            edit_decisions = read_edit_decisions(self._projects_dir, project_id)
            if not edit_decisions:
                self._set(job_id, project_id, status="failed",
                          error="no edit_decisions to render — save the timeline first")
                return
            asset_manifest = read_asset_manifest(self._projects_dir, project_id)

            # video_compose's pre-compose gate REQUIRES renderer_family (optional in the
            # schema). A hand-built/scaffolded timeline may lack it — inject a benign default
            # into the render-only copy (NOT persisted) so a preview isn't blocked by a
            # governance field that doesn't change pixels on the ffmpeg path.
            preview_warnings: list[str] = []
            if not edit_decisions.get("renderer_family"):
                edit_decisions = dict(edit_decisions, renderer_family="social-reel")
                preview_warnings.append(
                    "rendered with a default renderer_family='social-reel'; set it explicitly to lock it."
                )

            renders_dir = self._projects_dir / project_id / "renders"
            renders_dir.mkdir(parents=True, exist_ok=True)
            out_path = renders_dir / out_name

            # Render-once: render each scene to a content-cached proxy clip, then
            # assemble (cheap ffmpeg concat) into out_path. On an unchanged timeline
            # every scene is a cache hit, so a re-edit only re-renders what changed.
            result = self._video_compose().execute({
                "operation": "render_proxies",
                "edit_decisions": edit_decisions,
                "asset_manifest": asset_manifest,
                "output_path": str(out_path),
                "proxies_dir": str(renders_dir / "proxies"),
            })

            if result.success and out_path.exists():
                data = result.data or {}
                warnings = list(preview_warnings) + list(data.get("warnings") or [])
                if "n_scenes" in data:
                    warnings.append(
                        f"{data.get('n_rendered', 0)} scene(s) re-rendered, "
                        f"{data.get('n_cached', 0)} reused from cache"
                    )
                self._set(
                    job_id, project_id,
                    status="done",
                    # path is RELATIVE to the project dir, so the UI can fetch it via /file?path=
                    output_path=str(out_path.relative_to(self._projects_dir / project_id)),
                    final_review_status=data.get("final_review_status"),
                    warnings=warnings or None,
                )
            else:
                self._set(job_id, project_id, status="failed",
                          error=(result.error or "render failed")[:2000])
        except Exception as exc:  # never let a render thread die silently
            self._set(job_id, project_id, status="failed",
                      error=f"{type(exc).__name__}: {exc}"[:2000])
