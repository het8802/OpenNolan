"""Artifact aggregation for the Mission Control UI.

The pipeline is the spine: every stage *produces* named artifacts (the
pipeline YAML's ``produces:`` list; each ``checkpoint_<stage>.json`` carries
those artifacts inline under ``artifacts``, and the agent also drops standalone
copies in ``projects/<id>/artifacts/<name>.json``). This module turns that on-disk
reality into:

  list_artifacts(...)  -> a manifest grouping artifacts under their producing
                          stage (+ stage status/approval/review), plus extras
                          and the cross-cutting decision_log summary.
  read_artifact(...)   -> the parsed content of one artifact, addressed by a
                          single safe key, resolved from the standalone file or
                          (fallback) the owning stage's checkpoint.

Reads are defensive (raw json, never the validating reader) so a legacy or
schema-drifted artifact still renders rather than 500-ing the panel. Owns no
orchestration.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from lib.checkpoint import CANONICAL_STAGE_ARTIFACTS, STAGES
from lib.pipeline_loader import get_stage_order, load_pipeline
from lib.project import get_project_pipeline_type, get_project_record
from schemas.artifacts import ARTIFACT_NAMES

# Artifact keys are addressed as a single path segment; lock them down so the
# content route can never be used for traversal.
SAFE_KEY_RE = re.compile(r"^[a-z0-9_]+$")

_KNOWN_ARTIFACTS = set(ARTIFACT_NAMES)
# artifact_name -> producing stage (reverse of CANONICAL_STAGE_ARTIFACTS)
_STAGE_BY_ARTIFACT = {v: k for k, v in CANONICAL_STAGE_ARTIFACTS.items()}


def _read_json(path: Path) -> Optional[Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _checkpoint_path(proj: Path, stage: str) -> Path:
    return proj / f"checkpoint_{stage}.json"


def _stage_order(projects_dir: Path, project_id: str, pipeline_type: Optional[str], proj: Path) -> list[str]:
    """The pipeline's stage order if known; otherwise the stages that actually
    have a checkpoint on disk, in canonical order (covers no-pipeline / legacy)."""
    if pipeline_type:
        try:
            return get_stage_order(load_pipeline(pipeline_type))
        except Exception:
            pass
    present = {p.name[len("checkpoint_"):-len(".json")] for p in proj.glob("checkpoint_*.json")}
    ordered = [s for s in STAGES if s in present]
    # any non-canonical stage names still surface, after the known ones
    ordered += sorted(present - set(ordered))
    return ordered


def _artifact_size(proj: Path, key: str, payload: Any) -> int:
    f = proj / "artifacts" / f"{key}.json"
    if f.is_file():
        try:
            return f.stat().st_size
        except OSError:
            pass
    try:
        return len(json.dumps(payload).encode("utf-8"))
    except Exception:
        return 0


def list_artifacts(projects_dir: Path | str, project_id: str) -> Optional[dict[str, Any]]:
    """Build the artifact manifest for a project, grouped by pipeline stage.

    Returns None if ``project_id`` isn't a real project (-> 404 at the API).
    """
    projects_dir = Path(projects_dir)
    if get_project_record(projects_dir, project_id) is None:
        return None

    proj = projects_dir / project_id
    pipeline_type = get_project_pipeline_type(projects_dir, project_id)
    stage_names = _stage_order(projects_dir, project_id, pipeline_type, proj)

    stages: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for stage in stage_names:
        cp = _read_json(_checkpoint_path(proj, stage))
        canonical = CANONICAL_STAGE_ARTIFACTS.get(stage)
        entry: dict[str, Any] = {
            "stage": stage,
            "status": "pending",
            "human_approval_required": False,
            "human_approved": False,
            "timestamp": None,
            "review": None,
            "artifacts": [],
        }
        if isinstance(cp, dict):
            entry["status"] = cp.get("status", "pending")
            entry["human_approval_required"] = bool(cp.get("human_approval_required", False))
            entry["human_approved"] = bool(cp.get("human_approved", False))
            entry["timestamp"] = cp.get("timestamp")
            entry["review"] = cp.get("review")
            entry["style_playbook"] = cp.get("style_playbook")
            artifacts = cp.get("artifacts")
            # Surface the style/playbook even when it lives inside the scene_plan
            # artifact rather than the checkpoint's top-level field.
            if not entry["style_playbook"] and isinstance(artifacts, dict):
                spc = artifacts.get("scene_plan")
                if isinstance(spc, dict) and spc.get("style_playbook"):
                    entry["style_playbook"] = spc["style_playbook"]
            if isinstance(artifacts, dict):
                for key, payload in artifacts.items():
                    if key == "decision_log":
                        continue  # cross-cutting; surfaced separately
                    entry["artifacts"].append({
                        "key": key,
                        "canonical": key == canonical,
                        "known": key in _KNOWN_ARTIFACTS,
                        "size_bytes": _artifact_size(proj, key, payload),
                    })
                    seen_keys.add(key)
        stages.append(entry)

    # Standalone artifact files not embedded in any checkpoint we listed:
    # attribute to a producing stage if we know it, else "extra".
    extras: list[dict[str, Any]] = []
    artifacts_dir = proj / "artifacts"
    if artifacts_dir.is_dir():
        for f in sorted(artifacts_dir.glob("*.json")):
            key = f.stem
            if key == "decision_log" or key in seen_keys:
                continue
            stage = _STAGE_BY_ARTIFACT.get(key)
            try:
                size = f.stat().st_size
            except OSError:
                size = 0
            item = {"key": key, "canonical": False, "known": key in _KNOWN_ARTIFACTS, "size_bytes": size}
            if stage:
                target = next((s for s in stages if s["stage"] == stage), None)
                if target is not None:
                    target["artifacts"].append(item)
                    seen_keys.add(key)
                    continue
            extras.append(item)
            seen_keys.add(key)

    return {
        "project_id": project_id,
        "pipeline_type": pipeline_type,
        "stages": stages,
        "extra_artifacts": extras,
        "decision_log": _decision_log_summary(proj),
    }


def _decision_log_summary(proj: Path) -> dict[str, Any]:
    data = _read_json(proj / "decision_log.json") or _read_json(proj / "artifacts" / "decision_log.json")
    if not isinstance(data, dict):
        return {"present": False, "key": "decision_log", "decision_count": 0}
    decisions = data.get("decisions")
    return {
        "present": True,
        "key": "decision_log",
        "decision_count": len(decisions) if isinstance(decisions, list) else 0,
    }


class BadArtifactKey(ValueError):
    """Raised for an unsafe / malformed artifact key (-> 400)."""


def read_artifact(projects_dir: Path | str, project_id: str, key: str) -> Optional[dict[str, Any]]:
    """Resolve one artifact's parsed content by key.

    Resolution order: the project-root decision_log (special), then the
    standalone ``artifacts/<key>.json`` file, then a scan of stage checkpoints
    for an embedded ``artifacts[key]``. Returns None if not found (-> 404).
    Raises ``BadArtifactKey`` for an unsafe key (-> 400).
    """
    if not key or not SAFE_KEY_RE.match(key):
        raise BadArtifactKey(f"unsafe artifact key {key!r}")

    projects_dir = Path(projects_dir)
    if get_project_record(projects_dir, project_id) is None:
        return None
    proj = projects_dir / project_id

    # decision_log: the project-root copy is the cumulative source of truth.
    if key == "decision_log":
        for cand in (proj / "decision_log.json", proj / "artifacts" / "decision_log.json"):
            data = _read_json(cand)
            if data is not None:
                return {"key": key, "stage": None, "source": "file", "content": data}
        return None

    # standalone artifact file
    standalone = proj / "artifacts" / f"{key}.json"
    if standalone.is_file():
        data = _read_json(standalone)
        if data is not None:
            stage = _STAGE_BY_ARTIFACT.get(key)
            return {"key": key, "stage": stage, "source": "file", "content": data}

    # fallback: pull from the owning stage's checkpoint
    pipeline_type = get_project_pipeline_type(projects_dir, project_id)
    for stage in _stage_order(projects_dir, project_id, pipeline_type, proj):
        cp = _read_json(_checkpoint_path(proj, stage))
        if isinstance(cp, dict):
            arts = cp.get("artifacts")
            if isinstance(arts, dict) and key in arts:
                return {"key": key, "stage": stage, "source": "checkpoint", "content": arts[key]}
    return None
