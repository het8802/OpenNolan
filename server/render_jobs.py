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

from server.editor import read_asset_manifest, read_edit_decisions, resolve_source_path


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
            self._jobs[job_id] = {"job_id": job_id, "project_id": project_id,
                                  "status": "queued", "origin": "editor"}
            self._active_by_project[project_id] = job_id  # newest job wins
        threading.Thread(
            target=self._run, args=(job_id, project_id, out_name), daemon=True
        ).start()
        return job_id

    def start_with_inputs(self, project_id: str, inputs: dict[str, Any]) -> str:
        """Queue a render from CALLER-supplied inputs (the agent's render tool), not
        from disk. Like start() but the caller hands the full render inputs:
        edit_decisions (required), asset_manifest, output_path, proxies_dir,
        hdr_policy, proposal_packet. Honors supersede via _active_by_project and
        runs _resolve_sources, same as start(). Returns the job_id.

        Tagged origin="agent" + consumed=False so the agent runner can surface a
        finished job on the user's next turn (see AgentRunner._render_resume_note)."""
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {"job_id": job_id, "project_id": project_id,
                                  "status": "queued", "origin": "agent", "consumed": False}
            self._active_by_project[project_id] = job_id  # newest job wins
        threading.Thread(
            target=self._run_with_inputs, args=(job_id, project_id, dict(inputs)), daemon=True
        ).start()
        return job_id

    def status(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def active_job_for(self, project_id: str) -> Optional[dict[str, Any]]:
        """The current (newest) job for this project, or None. Copy of its status dict."""
        with self._lock:
            jid = self._active_by_project.get(project_id)
            if jid and jid in self._jobs:
                return dict(self._jobs[jid])
            return None

    def mark_consumed(self, job_id: str) -> None:
        """Flag a finished job as surfaced to the agent (resume-note injected once)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["consumed"] = True

    # -- internals ------------------------------------------------------------
    def _video_compose(self) -> Any:
        if self._tool is None:
            from tools.video.video_compose import VideoCompose
            self._tool = VideoCompose()
        return self._tool

    def _is_superseded(self, project_id: str, job_id: str) -> bool:
        return self._active_by_project.get(project_id) != job_id

    def _resolve_sources(self, project_id: str, edit_decisions: dict[str, Any]) -> dict[str, Any]:
        """Rewrite every cut.source and asset-backed overlay.asset_id to an ABSOLUTE on-disk
        path (render-only copy; never persisted).

        The Studio (and the agent) store these refs PROJECT-relative (e.g.
        "assets/video/x.mp4") or as asset-manifest ids. video_compose resolves bare relative
        refs from the process cwd (repo root), so a project-relative ref points at a
        nonexistent <repo>/assets/... and the render fails ("Cut source not found"). The scrub
        preview hides this because it goes through editor.resolve_source_path. So we resolve
        the SAME way here and hand the renderer absolute paths it can always find. Refs that
        don't resolve to a file (animated/not-yet-on-disk scenes) are left untouched so the
        proxy path's unresolved-hash branch still applies.
        """
        def absolute(ref: str) -> str:
            if not ref:
                return ref
            p = resolve_source_path(self._projects_dir, project_id, ref)
            return str(p) if p else ref

        changed = False
        cuts = []
        for c in edit_decisions.get("cuts", []) or []:
            src = c.get("source")
            new = absolute(src) if src else src
            if new != src:
                c = dict(c, source=new); changed = True
            cuts.append(c)

        overlays = edit_decisions.get("overlays")
        new_overlays = None
        if overlays:
            new_overlays = []
            for o in overlays:
                aid = o.get("asset_id")
                new = absolute(aid) if aid else aid
                if new != aid:
                    o = dict(o, asset_id=new); changed = True
                new_overlays.append(o)

        # Structured audio stems (music bed / narration segments / sfx) are stored as
        # asset-manifest ids or project-relative refs too. The render mixes them into a
        # master (video_compose._mix_structured_audio) but the assemble pass gets an
        # EMPTY manifest, so resolve them to absolute paths HERE (same resolver the
        # scrub preview uses) — otherwise the editor's render would silently drop
        # music/SFX. audio.path (a pre-mixed master) already resolves from the repo cwd.
        new_audio = None
        audio = edit_decisions.get("audio")
        if isinstance(audio, dict):
            a2 = dict(audio)
            music = a2.get("music")

            def _abs_music(region: dict[str, Any]) -> dict[str, Any]:
                nonlocal changed
                aid = region.get("asset_id")
                if not aid:
                    return region
                r = absolute(aid)
                if r != aid:
                    changed = True
                    return dict(region, asset_id=r)
                return region

            if isinstance(music, dict):  # single bed (legacy / one region)
                a2["music"] = _abs_music(music)
            elif isinstance(music, list):  # multiple regions (after a split)
                a2["music"] = [_abs_music(m) if isinstance(m, dict) else m for m in music]
            narr = a2.get("narration")
            if isinstance(narr, dict) and narr.get("segments"):
                segs = []
                for s in narr["segments"]:
                    aid = (s or {}).get("asset_id")
                    r = absolute(aid) if aid else aid
                    if aid and r != aid:
                        s = dict(s, asset_id=r); changed = True
                    segs.append(s)
                a2["narration"] = dict(narr, segments=segs)
            sfx = a2.get("sfx")
            if isinstance(sfx, list) and sfx:
                new_sfx = []
                for s in sfx:
                    aid = (s or {}).get("asset_id")
                    r = absolute(aid) if aid else aid
                    if aid and r != aid:
                        s = dict(s, asset_id=r); changed = True
                    new_sfx.append(s)
                a2["sfx"] = new_sfx
            new_audio = a2

        # The project background image is also stored as a project-relative ref /
        # asset id (metadata.background.asset_id, type=="image"). The renderer reads
        # it from disk during the assemble pass, so resolve it the same way. Color
        # backgrounds carry no path and transform.position/scale stay intact.
        new_metadata = None
        meta = edit_decisions.get("metadata")
        if isinstance(meta, dict):
            bg = meta.get("background")
            if isinstance(bg, dict) and (bg.get("type") or "").strip().lower() == "image":
                aid = bg.get("asset_id")
                new = absolute(aid) if aid else aid
                if new != aid:
                    new_metadata = dict(meta, background=dict(bg, asset_id=new))
                    changed = True

        if not changed:
            return edit_decisions
        out = dict(edit_decisions, cuts=cuts)
        if new_overlays is not None:
            out["overlays"] = new_overlays
        if new_audio is not None:
            out["audio"] = new_audio
        if new_metadata is not None:
            out["metadata"] = new_metadata
        return out

    def _set(self, job_id: str, project_id: str, **fields: Any) -> None:
        with self._lock:
            if self._is_superseded(project_id, job_id):
                return  # a newer render replaced this one; drop the result silently
            self._jobs[job_id].update(**fields)

    def _run(self, job_id: str, project_id: str, out_name: str) -> None:
        """Editor path: read the saved timeline from disk and render it."""
        self._set(job_id, project_id, status="running")
        try:
            edit_decisions = read_edit_decisions(self._projects_dir, project_id)
            if not edit_decisions:
                self._set(job_id, project_id, status="failed",
                          error="no edit_decisions to render — save the timeline first")
                return
            asset_manifest = read_asset_manifest(self._projects_dir, project_id)
            renders_dir = self._projects_dir / project_id / "renders"
            renders_dir.mkdir(parents=True, exist_ok=True)
            self._execute_render(
                job_id, project_id, edit_decisions, asset_manifest,
                renders_dir / out_name, renders_dir / "proxies",
            )
        except Exception as exc:  # never let a render thread die silently
            self._set(job_id, project_id, status="failed",
                      error=f"{type(exc).__name__}: {exc}"[:2000])

    def _run_with_inputs(self, job_id: str, project_id: str, inputs: dict[str, Any]) -> None:
        """Agent path: render CALLER-supplied inputs (with proposal_packet/hdr_policy)."""
        self._set(job_id, project_id, status="running")
        try:
            edit_decisions = inputs.get("edit_decisions")
            if not edit_decisions:
                self._set(job_id, project_id, status="failed",
                          error="edit_decisions required to render")
                return
            asset_manifest = inputs.get("asset_manifest") or {"assets": []}
            renders_dir = self._projects_dir / project_id / "renders"
            renders_dir.mkdir(parents=True, exist_ok=True)
            out_path = self._normalize_output_path(
                project_id, inputs.get("output_path"), f"agent_render_{job_id}.mp4"
            )
            proxies_dir = inputs.get("proxies_dir") or str(renders_dir / "proxies")
            self._execute_render(
                job_id, project_id, edit_decisions, asset_manifest, out_path, proxies_dir,
                proposal_packet=inputs.get("proposal_packet"),
                hdr_policy=inputs.get("hdr_policy"),
            )
        except Exception as exc:
            self._set(job_id, project_id, status="failed",
                      error=f"{type(exc).__name__}: {exc}"[:2000])

    def _execute_render(
        self, job_id: str, project_id: str,
        edit_decisions: dict[str, Any], asset_manifest: dict[str, Any],
        out_path: Path, proxies_dir: Path | str,
        *, proposal_packet: Any = None, hdr_policy: Optional[str] = None,
    ) -> None:
        """Shared render body for both the editor (disk) and agent (inputs) paths:
        renderer_family fallback, source resolution, render_proxies, and recording
        the result (honoring supersede via _set)."""
        # video_compose's pre-compose gate REQUIRES renderer_family (optional in the
        # schema). A hand-built/scaffolded timeline may lack it — inject a benign default
        # into the render-only copy (NOT persisted) so a render isn't blocked by a
        # governance field that doesn't change pixels on the ffmpeg path.
        preview_warnings: list[str] = []
        if not edit_decisions.get("renderer_family"):
            edit_decisions = dict(edit_decisions, renderer_family="social-reel")
            preview_warnings.append(
                "rendered with a default renderer_family='social-reel'; set it explicitly to lock it."
            )

        # Hand the renderer absolute source/asset paths (project-relative refs don't
        # resolve from the server cwd). Render-only copy — NOT persisted.
        edit_decisions = self._resolve_sources(project_id, edit_decisions)

        # Render-once: render each scene to a content-cached proxy clip, then
        # assemble (cheap ffmpeg concat) into out_path. On an unchanged timeline
        # every scene is a cache hit, so a re-edit only re-renders what changed.
        exec_inputs: dict[str, Any] = {
            "operation": "render_proxies",
            "edit_decisions": edit_decisions,
            "asset_manifest": asset_manifest,
            "output_path": str(out_path),
            "proxies_dir": str(proxies_dir),
        }
        # Forward agent-only inputs ONLY when present (keep VideoCompose defaults otherwise).
        if proposal_packet is not None:
            exec_inputs["proposal_packet"] = proposal_packet
        if hdr_policy:
            exec_inputs["hdr_policy"] = hdr_policy

        result = self._video_compose().execute(exec_inputs)

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

    def _normalize_output_path(self, project_id: str, raw: Optional[str], fallback_name: str) -> Path:
        """Resolve an agent-supplied output_path to an ABSOLUTE path inside the
        project's renders/ dir. Accepts repo-relative ("projects/<id>/renders/x.mp4"),
        project-relative ("renders/x.mp4"), or absolute paths. Path-traversal guard:
        anything resolving outside projects/<id>/ falls back to renders/<fallback_name>."""
        proj = (self._projects_dir / project_id).resolve()
        fallback = proj / "renders" / fallback_name
        if not raw:
            return fallback
        p = Path(raw)
        if p.is_absolute():
            cand = p
        else:
            parts = p.parts
            if parts and parts[0] == "projects":          # repo-root-relative
                cand = self._projects_dir / Path(*parts[1:]) if len(parts) > 1 else proj
            elif parts and parts[0] == project_id:        # projects-dir-relative
                cand = self._projects_dir / p
            else:                                         # project-relative
                cand = proj / p
        try:
            cand = cand.resolve()
        except Exception:
            return fallback
        if cand == proj or proj in cand.parents:
            return cand
        return fallback
