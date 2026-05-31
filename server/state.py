"""Project state aggregation for the Mission Control UI.

State-fetching sits behind the ``StateSource`` interface so the polling impl
used in v1 can be swapped for a push (WebSocket) impl later without touching
the API layer. This is the "polling + WS-ready seam" eng-review decision:
the endpoint depends on ``StateSource``, not on how state is obtained.

A stage's status comes ONLY from its checkpoint file. A stage with no
checkpoint yet is reported as ``pending`` (an absence, not a schema status).
Reads are defensive: a corrupt/invalid checkpoint surfaces as ``error`` for
that one stage rather than failing the whole ``/state`` response.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from lib.checkpoint import get_next_stage, read_checkpoint
from lib.pipeline_loader import get_stage_order, load_pipeline
from lib.project import get_project_pipeline_type, get_project_record

PENDING = "pending"
ERROR = "error"

KNOWN_RUNTIMES = {"remotion", "hyperframes", "ffmpeg"}


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def detect_runtime(projects_dir: Path, project_id: str) -> Optional[str]:
    """Best-effort detection of which composition runtime a project actually
    used. Returns one of KNOWN_RUNTIMES, or None if not yet decided.

    Priority (most authoritative first):
      1. render_report.json -> workspace.runtime  (what actually rendered)
      2. edit_decisions.json -> render_runtime     (locked at proposal/edit)
      3. scene_plan.json     -> render_runtime
      4. on-disk workspace dir presence (hyperframes/ or remotion/)
    """
    proj = Path(projects_dir) / project_id
    art = proj / "artifacts"

    def _norm(val: Any) -> Optional[str]:
        if isinstance(val, str) and val.strip().lower() in KNOWN_RUNTIMES:
            return val.strip().lower()
        return None

    rr = _read_json(art / "render_report.json")
    if rr:
        got = _norm((rr.get("workspace") or {}).get("runtime"))
        if got:
            return got
    for fname in ("edit_decisions.json", "scene_plan.json"):
        data = _read_json(art / fname)
        if data:
            got = _norm(data.get("render_runtime"))
            if got:
                return got
    if (proj / "hyperframes").is_dir():
        return "hyperframes"
    if (proj / "remotion").is_dir():
        return "remotion"
    return None


class StateSource(ABC):
    """How the API obtains a project's per-stage state. Swap impls (poll vs
    push) without touching the endpoints."""

    @abstractmethod
    def project_state(self, project_id: str) -> Optional[dict[str, Any]]:
        """Return the project's state dict, or None if it isn't a project."""


class FileStateSource(StateSource):
    """Polling impl: derive state by reading checkpoint files on demand."""

    def __init__(self, projects_dir: Path | str):
        self.projects_dir = Path(projects_dir)

    def _stage_entry(self, project_id: str, stage: str) -> dict[str, Any]:
        try:
            cp = read_checkpoint(self.projects_dir, project_id, stage)
        except Exception as exc:  # corrupt/invalid checkpoint — don't 500 the page
            return {"stage": stage, "status": ERROR, "detail": str(exc)[:200]}
        if cp is None:
            return {
                "stage": stage,
                "status": PENDING,
                "human_approval_required": False,
                "human_approved": False,
            }
        return {
            "stage": stage,
            "status": cp.get("status"),
            "human_approval_required": cp.get("human_approval_required", False),
            "human_approved": cp.get("human_approved", False),
            "timestamp": cp.get("timestamp"),
        }

    def project_state(self, project_id: str) -> Optional[dict[str, Any]]:
        manifest = get_project_record(self.projects_dir, project_id)
        if manifest is None:
            return None

        pipeline_type = get_project_pipeline_type(self.projects_dir, project_id)

        stage_order: list[str] = []
        if pipeline_type:
            try:
                stage_order = get_stage_order(load_pipeline(pipeline_type))
            except Exception:
                stage_order = []

        stages = [self._stage_entry(project_id, name) for name in stage_order]

        try:
            next_stage = (
                get_next_stage(self.projects_dir, project_id, pipeline_type)
                if pipeline_type
                else None
            )
        except Exception:
            next_stage = None

        return {
            "project_id": project_id,
            "name": manifest.get("name"),
            "pipeline_type": pipeline_type,
            "created_at": manifest.get("created_at"),
            "stages": stages,
            "next_stage": next_stage,
            "runtime": detect_runtime(self.projects_dir, project_id),
        }
