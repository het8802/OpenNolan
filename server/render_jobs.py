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

Status transitions: queued ─▶ running ─▶ done | failed | superseded.

Both render paths land the deliverable through ONE publisher
(lib.project.publish_final_render): they render to a `.part.mp4` and publish it as
`renders/final.mp4` with a receipt. The per-project lock is held for the WHOLE render,
because video_compose derives its scratch dirs (.compose_tmp, .pip_tmp,
.remotion_props.json, .final_review_frames) from the output's parent — i.e. renders/,
which is per-project, not per-job — so two concurrent renders in one project trample
each other. See docs/plans/opn-30-edit-decisions-render-desync/claude/architecture.md.
"""

from __future__ import annotations

import contextlib
import threading
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional

from lib.project import (
    FINAL_RENDER_NAME,
    KIND_DIRS,
    project_lock,
    publish_final_render,
    renders_dir,
)
from server.editor import read_asset_manifest, read_edit_decisions, resolve_source_path

TERMINAL_STATUSES = ("done", "failed", "superseded")


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
        """Queue a render for `project_id` and return its job_id. Supersedes any prior job.

        Publishes to the canonical renders/final.mp4 (it used to mint a per-job
        editor_preview_<job>.mp4, which is how one project ended up showing three files
        all labelled "Final render")."""
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {"job_id": job_id, "project_id": project_id,
                                  "status": "queued", "origin": "editor"}
            self._active_by_project[project_id] = job_id  # newest job wins
        threading.Thread(
            target=self._run, args=(job_id, project_id), daemon=True
        ).start()
        return job_id

    def start_with_inputs(self, project_id: str, inputs: dict[str, Any]) -> str:
        """Queue a render from CALLER-supplied inputs (the agent's render tool), not
        from disk. Like start() but the caller hands the full render inputs:
        edit_decisions (required), asset_manifest, output_path, proxies_dir,
        hdr_policy, proposal_packet, plus persist_edit_decisions (see _run_with_inputs).
        Honors supersede via _active_by_project and runs _resolve_sources, same as
        start(). Returns the job_id.

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

    def start_op(self, project_id: str, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Queue a heavy media op (any registry tool run) for `project_id` and return
        its job_id. Same lifecycle as start_with_inputs (daemon thread, supersede,
        consumed tagging) but runs an arbitrary registry tool IN-PROCESS instead of a
        render. This is what lets the agent's `run_media_op` tool BLOCK its turn (answer
        stays live) rather than detaching to a background Bash task — the CLI auto-detach
        of a long re-encode is what broke turn attribution (the off-by-one). Tagged
        origin="agent_op" so the runner can surface a finished op on the user's next turn
        (AgentRunner._render_resume_note)."""
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {"job_id": job_id, "project_id": project_id,
                                  "status": "queued", "origin": "agent_op",
                                  "tool_name": tool_name, "consumed": False}
            self._active_by_project[project_id] = job_id  # newest job wins
        threading.Thread(
            target=self._run_op, args=(job_id, project_id, tool_name, dict(tool_input)), daemon=True
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

    def latest_unconsumed_agent_job(self, project_id: str) -> Optional[dict[str, Any]]:
        """The newest TERMINAL, unconsumed agent job for this project, or None.

        active_job_for() cannot find a SUPERSEDED job — by definition a newer job took
        its place — so without this the agent is never told its render was replaced and
        an in-turn waiter just times out. Newest-first by insertion order (dicts preserve
        it); mark_consumed keeps the note one-shot."""
        with self._lock:
            for job in reversed(list(self._jobs.values())):
                if (job.get("project_id") == project_id
                        and job.get("origin") in ("agent", "agent_op")
                        and not job.get("consumed")
                        and job.get("status") in TERMINAL_STATUSES):
                    return dict(job)
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

    def _mark_superseded_locked(self, job_id: str) -> None:
        """Record the TERMINAL `superseded` status. Caller holds self._lock.

        Written directly rather than through _set, whose supersede guard would drop
        exactly this update — which is what used to leave the job sitting at
        queued/running forever, invisible to both the poller and the resume note."""
        job = self._jobs.get(job_id)
        if job is not None and job.get("status") not in TERMINAL_STATUSES:
            job["status"] = "superseded"

    @contextlib.contextmanager
    def _commit_guard(self, project_id: str, job_id: str) -> Iterator[bool]:
        """Hold the job lock across the publisher's supersede re-check AND its replace.

        Checking "am I still the active job" and then replacing final.mp4 as two steps
        is racy: a newer job can become active in between and this one publishes over it.
        Passed to publish_final_render as its commit_guard so both happen in one critical
        section. Lock order is project_lock (outer, held for the whole render) then this
        (inner, microseconds) — never the reverse."""
        with self._lock:
            yield not self._is_superseded(project_id, job_id)

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

    def _set(self, job_id: str, project_id: str, *, force: bool = False, **fields: Any) -> None:
        """Record fields on a job, honoring supersede.

        A newer job for this project makes this one's RESULT moot — but the job itself must
        still reach a terminal state, so it is marked `superseded` rather than having the
        update dropped. A silent drop left the job at queued/running forever: invisible to
        the poller, to `active_job_for`, and to the agent's resume note, so an in-turn
        waiter just timed out.

        `force` records anyway. A render that already won the publish commit guard IS the
        deliverable on disk, so its `done` must stand even if a newer job became active a
        moment later — otherwise a job that genuinely published gets reported as superseded.
        """
        with self._lock:
            if not force and self._is_superseded(project_id, job_id):
                self._mark_superseded_locked(job_id)
                return
            self._jobs[job_id].update(**fields)

    def _run(self, job_id: str, project_id: str) -> None:
        """Editor path: read the saved timeline from disk, render it, publish it.

        Gets a receipt for the snapshot it rendered but NEVER writes the doc back
        (persist_doc=None): autosave is not suspended during a render — Studio.jsx gates
        saves on agentBusyRef/reconcilingRef only — so the user can edit and autosave doc
        B while doc A renders. Writing A back on success would destroy B. Leaving the live
        doc alone instead makes the finished render read STALE against B, which is the
        correct answer."""
        self._set(job_id, project_id, status="running")
        try:
            edit_decisions = read_edit_decisions(self._projects_dir, project_id)
            if not edit_decisions:
                self._set(job_id, project_id, status="failed",
                          error="no edit_decisions to render — save the timeline first")
                return
            asset_manifest = read_asset_manifest(self._projects_dir, project_id)
            renders = renders_dir(self._projects_dir, project_id)
            renders.mkdir(parents=True, exist_ok=True)
            self._execute_render(
                job_id, project_id, edit_decisions, asset_manifest,
                renders / FINAL_RENDER_NAME, renders / "proxies",
                receipt_doc=edit_decisions, publish=True,
            )
        except Exception as exc:  # never let a render thread die silently
            self._set(job_id, project_id, status="failed",
                      error=f"{type(exc).__name__}: {exc}"[:2000])

    def _run_with_inputs(self, job_id: str, project_id: str, inputs: dict[str, Any]) -> None:
        """Agent path: render CALLER-supplied inputs (with proposal_packet/hdr_policy).

        `output_path` selects the route (it is load-bearing — several pipeline directors
        pass it, one of them for a genuine intermediate):

          omitted / renders/final.mp4  -> publish, with a receipt
          another path under renders/  -> direct write, no receipt, never "current"

        `persist_edit_decisions` (set by AgentRunner._run_render when the doc came inline
        and passed schema validation) commits that doc to artifacts/edit_decisions.json —
        but only in the same critical section as the video, so a failed render can't leave
        the doc describing a video that was never produced."""
        self._set(job_id, project_id, status="running")
        try:
            edit_decisions = inputs.get("edit_decisions")
            if not edit_decisions:
                self._set(job_id, project_id, status="failed",
                          error="edit_decisions required to render")
                return
            asset_manifest = inputs.get("asset_manifest") or {"assets": []}
            renders = renders_dir(self._projects_dir, project_id)
            renders.mkdir(parents=True, exist_ok=True)
            out_path = self._normalize_output_path(
                project_id, inputs.get("output_path"), FINAL_RENDER_NAME
            )
            publish = out_path == self._final_target(project_id)
            proxies_dir = inputs.get("proxies_dir") or str(renders / "proxies")
            self._execute_render(
                job_id, project_id, edit_decisions, asset_manifest, out_path, proxies_dir,
                proposal_packet=inputs.get("proposal_packet"),
                hdr_policy=inputs.get("hdr_policy"),
                # The CALLER's doc, not the _resolve_sources copy _execute_render renders.
                receipt_doc=edit_decisions if publish else None,
                persist_doc=(edit_decisions if publish
                             and inputs.get("persist_edit_decisions") else None),
                publish=publish,
            )
        except Exception as exc:
            self._set(job_id, project_id, status="failed",
                      error=f"{type(exc).__name__}: {exc}"[:2000])

    def _run_op(self, job_id: str, project_id: str, tool_name: str,
                tool_input: dict[str, Any]) -> None:
        """Op path: run a registry tool (silence_cutter, motion_ops, ...) in-process on
        this daemon thread. A Stop/timeout leaves it running and the next turn surfaces
        the result — same survival guarantee as a render job. Records the produced file
        under output_path (tools name it `output` or `output_path`) plus the full
        result_data for the agent to read."""
        self._set(job_id, project_id, status="running")
        try:
            from tools.tool_registry import registry
            registry.ensure_discovered()
            tool = registry.get(tool_name)
            if tool is None:
                self._set(job_id, project_id, status="failed",
                          error=f"unknown tool {tool_name!r}")
                return
            result = tool.execute(dict(tool_input))
            if getattr(result, "success", False):
                data = getattr(result, "data", None) or {}
                out = data.get("output") or data.get("output_path")
                self._set(job_id, project_id, status="done",
                          result_data=data, output_path=out,
                          warnings=self._deliverable_write_warning(project_id, out))
            else:
                err = getattr(result, "error", None) or "tool failed"
                self._set(job_id, project_id, status="failed", error=str(err)[:2000])
        except Exception as exc:
            self._set(job_id, project_id, status="failed",
                      error=f"{type(exc).__name__}: {exc}"[:2000])

    def _deliverable_write_warning(self, project_id: str, out: Any) -> Optional[list[str]]:
        """Warn when a media op wrote straight into the top level of renders/.

        The publisher can't be the only writer there — run_media_op forwards an arbitrary
        input dict to any registry tool, and some tools default their output to
        renders/final.mp4 — so instead of policing dozens of tool schemas, say so out loud.
        An unreceipted final.mp4 already reads as stale; this makes the CAUSE visible."""
        if not out:
            return None
        try:
            landed = Path(out).resolve()
            renders = (self._projects_dir / project_id).resolve() / "renders"
            if landed.parent != renders:
                return None
        except OSError:
            return None
        note = (f"wrote {landed.name} into the project's renders/ folder, which holds the ONE "
                "deliverable. Only a render publishes renders/final.mp4 (with a receipt); "
                "anything else there shows in the editor as an earlier/stale render.")
        return [note]

    def _execute_render(
        self, job_id: str, project_id: str,
        edit_decisions: dict[str, Any], asset_manifest: dict[str, Any],
        out_path: Path, proxies_dir: Path | str,
        *, proposal_packet: Any = None, hdr_policy: Optional[str] = None,
        receipt_doc: Optional[dict[str, Any]] = None,
        persist_doc: Optional[dict[str, Any]] = None,
        publish: bool = False,
    ) -> None:
        """Shared render body for both the editor (disk) and agent (inputs) paths:
        renderer_family fallback, source resolution, render_proxies, and recording
        the result (honoring supersede via _set).

        Serialized per project for its whole duration (project_lock) — see the module
        docstring for why the shared scratch dirs make that mandatory. When `publish`,
        renders to a .part.mp4 and hands it to the one publisher, so a failed render
        leaves the previous deliverable and its receipt byte-for-byte intact."""
        with project_lock(self._projects_dir, project_id):
            # Re-check supersede now that we hold the lock: a queue of stale jobs drains
            # instantly instead of each burning a full render nobody will read.
            with self._lock:
                if self._is_superseded(project_id, job_id):
                    self._mark_superseded_locked(job_id)
                    return
            self._render_locked(
                job_id, project_id, edit_decisions, asset_manifest, out_path, proxies_dir,
                proposal_packet=proposal_packet, hdr_policy=hdr_policy,
                receipt_doc=receipt_doc, persist_doc=persist_doc, publish=publish,
            )

    def _render_locked(
        self, job_id: str, project_id: str,
        edit_decisions: dict[str, Any], asset_manifest: dict[str, Any],
        out_path: Path, proxies_dir: Path | str,
        *, proposal_packet: Any = None, hdr_policy: Optional[str] = None,
        receipt_doc: Optional[dict[str, Any]] = None,
        persist_doc: Optional[dict[str, Any]] = None,
        publish: bool = False,
    ) -> None:
        """The render itself. Caller holds project_lock."""
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
        # (Proxy cache keys are content-based — tools/video/render_cache.py — so a
        # stable output name doesn't cost a cache hit.)
        target = out_path.parent / f".final.{job_id}.part.mp4" if publish else out_path
        exec_inputs: dict[str, Any] = {
            "operation": "render_proxies",
            "edit_decisions": edit_decisions,
            "asset_manifest": asset_manifest,
            "output_path": str(target),
            "proxies_dir": str(proxies_dir),
        }
        # Forward agent-only inputs ONLY when present (keep VideoCompose defaults otherwise).
        if proposal_packet is not None:
            exec_inputs["proposal_packet"] = proposal_packet
        if hdr_policy:
            exec_inputs["hdr_policy"] = hdr_policy

        try:
            result = self._video_compose().execute(exec_inputs)
        except BaseException:
            if publish:
                target.unlink(missing_ok=True)
            raise

        if not (result.success and target.exists()):
            if publish:
                target.unlink(missing_ok=True)   # never leave a .part behind
            self._set(job_id, project_id, status="failed",
                      error=(result.error or "render failed")[:2000])
            return

        if publish:
            published = publish_final_render(
                self._projects_dir, project_id, target,
                receipt_doc=receipt_doc, persist_doc=persist_doc, move=True,
                commit_guard=lambda: self._commit_guard(project_id, job_id),
            )
            if not published["published"]:
                with self._lock:
                    self._mark_superseded_locked(job_id)
                return
            rel_out = published["path"]
        else:
            # out_path is always a direct child of the project's renders/ dir (see
            # _normalize_output_path), so name it lexically — relative_to against an
            # unresolved projects_dir raises for a relative path or a symlinked project.
            rel_out = f"{KIND_DIRS['final_render']}/{out_path.name}"

        data = result.data or {}
        warnings = list(preview_warnings) + list(data.get("warnings") or [])
        if "n_scenes" in data:
            warnings.append(
                f"{data.get('n_rendered', 0)} scene(s) re-rendered, "
                f"{data.get('n_cached', 0)} reused from cache"
            )
        self._set(
            job_id, project_id,
            # A published render already won the commit guard, so its bytes ARE the
            # deliverable: record `done` even if a newer job became active in between,
            # or the job that actually published would report as superseded.
            force=publish,
            status="done",
            # path is RELATIVE to the project dir, so the UI can fetch it via /file?path=
            output_path=rel_out,
            final_review_status=data.get("final_review_status"),
            warnings=warnings or None,
        )

    def _final_target(self, project_id: str) -> Path:
        """The canonical deliverable path, in the same resolved form
        _normalize_output_path returns, so the two can be compared."""
        return renders_dir(self._projects_dir, project_id) / FINAL_RENDER_NAME

    def _normalize_output_path(self, project_id: str, raw: Optional[str], fallback_name: str) -> Path:
        """Resolve an agent-supplied output_path to an ABSOLUTE path inside the
        project's renders/ dir. Accepts repo-relative ("projects/<id>/renders/x.mp4"),
        project-relative ("renders/x.mp4"), or absolute paths. Anything that is not a DIRECT
        child of projects/<id>/renders/ falls back to renders/<fallback_name>.

        Two tightenings, both destructive if left open:
          · it used to be enough to stay inside projects/<id>/, so
            output_path="assets/video/source.mp4" would overwrite a SOURCE asset (or an
            artifact) with the assembled video;
          · a descendant of renders/ is not safe either — renders/proxies/ is the
            content-keyed per-scene cache, so output_path="renders/proxies/<scene>.<key>.mp4"
            would drop a full assembled reel where the renderer later expects that one
            scene's clip, and the cache would trust it (tools/video/render_cache.py).

        Raises RendersDirEscapes when renders/ is a symlink out of the project: the
        fallback would follow that link too, so there is no safe path to return."""
        renders = renders_dir(self._projects_dir, project_id)   # raises if it escapes
        proj = renders.parent
        fallback = renders / fallback_name
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
        if cand.parent == renders:
            return cand
        return fallback
