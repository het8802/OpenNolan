"""Checkpoint writer/reader for pipeline state persistence.

Each stage writes a checkpoint after completion. The orchestrator uses
checkpoints to resume pipelines and to present state at human checkpoints.
"""

from __future__ import annotations

import json
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import jsonschema

from lib.atomic_io import atomic_write_json
from schemas.artifacts import ARTIFACT_NAMES, validate_artifact

# All known stages across all pipelines (used only for artifact name lookup).
ALL_KNOWN_STAGES = frozenset([
    "research", "proposal", "idea", "script", "scene_plan",
    "assets", "edit", "compose", "publish",
])

# Backward-compatible alias — existing code / tests that import STAGES still work.
# New code should use get_pipeline_stages(pipeline_type) instead.
STAGES = ["research", "proposal", "idea", "script", "scene_plan",
          "assets", "edit", "compose", "publish"]

CANONICAL_STAGE_ARTIFACTS = {
    "research": "research_brief",
    "proposal": "proposal_packet",
    "idea": "brief",
    "script": "script",
    "scene_plan": "scene_plan",
    "assets": "asset_manifest",
    "edit": "edit_decisions",
    "compose": "render_report",
    "publish": "publish_log",
}

# Additional artifacts that may be produced alongside canonical ones.
# These are not stage-defining but are required by governance contracts.
SUPPLEMENTARY_ARTIFACTS = {
    "source_media_review",  # Required before first planning stage when user media exists
    "final_review",         # Required by compose stage before presenting to user
    "video_analysis_brief", # Reference-video grounding artifact carried alongside stages
}


def get_pipeline_stages(pipeline_type: str | None) -> list[str]:
    """Return the ordered stage list for a specific pipeline.

    Falls back to STAGES (deterministic canonical order) when pipeline_type
    is not provided or the manifest cannot be loaded.

    Previous versions used a set intersection here, which produced
    nondeterministic ordering. The fallback now uses a stable list.
    """
    if pipeline_type is None:
        # Deterministic canonical fallback — sorted to ensure stable ordering
        import logging
        logging.getLogger(__name__).warning(
            "get_pipeline_stages called without pipeline_type — "
            "using canonical fallback order. Pass pipeline_type for correctness."
        )
        return list(STAGES)

    try:
        from lib.pipeline_loader import load_pipeline, get_stage_order
        manifest = load_pipeline(pipeline_type)
        return get_stage_order(manifest)
    except (FileNotFoundError, Exception):
        # Graceful fallback: return all known stages in canonical order
        return list(STAGES)

CHECKPOINT_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "schemas"
    / "checkpoints"
    / "checkpoint.schema.json"
)


class CheckpointValidationError(ValueError):
    """Raised when a checkpoint or its canonical artifacts are invalid."""


@lru_cache(maxsize=1)
def _load_checkpoint_schema() -> dict[str, Any]:
    with open(CHECKPOINT_SCHEMA_PATH) as f:
        return json.load(f)


def _validate_artifacts_for_stage(
    stage: str,
    status: str,
    artifacts: dict[str, Any],
) -> None:
    required_artifact = CANONICAL_STAGE_ARTIFACTS[stage]
    if status in {"completed", "awaiting_human"} and required_artifact not in artifacts:
        raise CheckpointValidationError(
            f"Stage {stage!r} with status {status!r} must include "
            f"canonical artifact {required_artifact!r}"
        )

    for artifact_name, artifact_data in artifacts.items():
        if artifact_name not in ARTIFACT_NAMES:
            continue
        if not isinstance(artifact_data, dict):
            raise CheckpointValidationError(
                f"Artifact {artifact_name!r} must be a JSON object matching its schema"
            )
        try:
            validate_artifact(artifact_name, artifact_data)
        except Exception as exc:
            raise CheckpointValidationError(
                f"Artifact {artifact_name!r} failed schema validation: {exc}"
            ) from exc


def validate_checkpoint(checkpoint: dict[str, Any]) -> None:
    """Validate checkpoint structure and canonical artifact payloads.

    Uses pipeline_type (if present) to resolve the valid stage list.
    Falls back to ALL_KNOWN_STAGES when pipeline_type is absent.
    """
    stage = checkpoint.get("stage")
    status = checkpoint.get("status")
    artifacts = checkpoint.get("artifacts")
    pipeline_type = checkpoint.get("pipeline_type")

    valid_stages = (
        set(get_pipeline_stages(pipeline_type)) if pipeline_type
        else ALL_KNOWN_STAGES
    )

    if not isinstance(stage, str) or stage not in valid_stages:
        raise CheckpointValidationError(
            f"Invalid stage: {stage!r} for pipeline {pipeline_type!r}. "
            f"Valid stages: {sorted(valid_stages)}"
        )
    if not isinstance(status, str):
        raise CheckpointValidationError(f"Invalid status: {status!r}")
    if not isinstance(artifacts, dict):
        raise CheckpointValidationError("Checkpoint artifacts must be a dictionary")

    _validate_artifacts_for_stage(stage, status, artifacts)

    try:
        jsonschema.validate(instance=checkpoint, schema=_load_checkpoint_schema())
    except jsonschema.ValidationError as exc:
        raise CheckpointValidationError(f"Checkpoint failed schema validation: {exc.message}") from exc


def _checkpoint_path(pipeline_dir: Path, project_id: str, stage: str) -> Path:
    return pipeline_dir / project_id / f"checkpoint_{stage}.json"


def _decision_log_path(pipeline_dir: Path, project_id: str) -> Path:
    return pipeline_dir / project_id / "decision_log.json"


def _merge_decision_log(
    pipeline_dir: Path, project_id: str, new_log: dict[str, Any]
) -> None:
    """Append new decisions to the project-level decision log.

    Each stage may produce decisions. This function merges them into a
    single cumulative file so reviewers and the bench can inspect the
    full audit trail.
    """
    path = _decision_log_path(pipeline_dir, project_id)
    if path.exists():
        with open(path) as f:
            existing = json.load(f)
    else:
        existing = {
            "version": "1.0",
            "project_id": project_id,
            "decisions": [],
        }

    existing_ids = {d["decision_id"] for d in existing.get("decisions", [])}
    for decision in new_log.get("decisions", []):
        if decision.get("decision_id") not in existing_ids:
            existing["decisions"].append(decision)

    # Atomic: a /state poll reading the decision log mid-write must never see
    # a truncated file.
    atomic_write_json(path, existing)


def write_checkpoint(
    pipeline_dir: Path,
    project_id: str,
    stage: str,
    status: str,
    artifacts: dict[str, Any],
    *,
    pipeline_type: Optional[str] = None,
    style_playbook: Optional[str] = None,
    checkpoint_policy: str = "guided",
    human_approval_required: bool = False,
    human_approved: bool = False,
    review: Optional[dict] = None,
    cost_snapshot: Optional[dict] = None,
    error: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Path:
    """Write a checkpoint file for a pipeline stage."""
    valid_stages = (
        set(get_pipeline_stages(pipeline_type)) if pipeline_type
        else ALL_KNOWN_STAGES
    )
    if stage not in valid_stages:
        raise ValueError(
            f"Invalid stage: {stage!r} for pipeline {pipeline_type!r}. "
            f"Valid stages: {sorted(valid_stages)}"
        )

    checkpoint = {
        "version": "1.0",
        "project_id": project_id,
        "pipeline_type": pipeline_type or "unknown",
        "stage": stage,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checkpoint_policy": checkpoint_policy,
        "human_approval_required": human_approval_required,
        "human_approved": human_approved,
        "artifacts": artifacts,
    }
    if style_playbook is not None:
        checkpoint["style_playbook"] = style_playbook
    if review is not None:
        checkpoint["review"] = review
    if cost_snapshot is not None:
        checkpoint["cost_snapshot"] = cost_snapshot
    if error is not None:
        checkpoint["error"] = error
    if metadata is not None:
        checkpoint["metadata"] = metadata

    # Merge decision_log: if this checkpoint carries new decisions,
    # append them to the project-level decision log file, then write the
    # reference back into relevant artifacts so downstream consumers can find it.
    if "decision_log" in artifacts and isinstance(artifacts["decision_log"], dict):
        _merge_decision_log(pipeline_dir, project_id, artifacts["decision_log"])
        log_ref = str(_decision_log_path(pipeline_dir, project_id))

        # Write decision_log_ref into proposal_packet and render_report
        # artifacts if they are present in this checkpoint.
        for artifact_key in ("proposal_packet", "render_report"):
            if artifact_key in artifacts and isinstance(artifacts[artifact_key], dict):
                plan_or_top = artifacts[artifact_key]
                # proposal_packet stores it under production_plan
                if artifact_key == "proposal_packet":
                    plan = plan_or_top.get("production_plan")
                    if isinstance(plan, dict):
                        plan["decision_log_ref"] = log_ref
                else:
                    plan_or_top["decision_log_ref"] = log_ref

    validate_checkpoint(checkpoint)

    # Atomic write: the UI polls /state by reading these files; a plain
    # truncate-then-write would let a poll catch a half-written checkpoint.
    path = _checkpoint_path(pipeline_dir, project_id, stage)
    atomic_write_json(path, checkpoint)

    return path


def read_checkpoint(
    pipeline_dir: Path, project_id: str, stage: str
) -> Optional[dict[str, Any]]:
    """Read a checkpoint file. Returns None if not found."""
    path = _checkpoint_path(pipeline_dir, project_id, stage)
    if not path.exists():
        return None
    with open(path) as f:
        checkpoint = json.load(f)
    validate_checkpoint(checkpoint)
    return checkpoint


def get_latest_checkpoint(
    pipeline_dir: Path, project_id: str
) -> Optional[dict[str, Any]]:
    """Find the most recent checkpoint for a project (by file mtime)."""
    project_dir = pipeline_dir / project_id
    if not project_dir.exists():
        return None

    checkpoints = sorted(
        project_dir.glob("checkpoint_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not checkpoints:
        return None

    with open(checkpoints[0]) as f:
        checkpoint = json.load(f)
    validate_checkpoint(checkpoint)
    return checkpoint


def get_completed_stages(
    pipeline_dir: Path, project_id: str, pipeline_type: str | None = None
) -> list[str]:
    """Return list of stages that have a completed checkpoint.

    When pipeline_type is provided, only checks stages defined in that
    pipeline's manifest — preventing false positives from leftover
    checkpoints of a different pipeline type.
    """
    stages_to_check = get_pipeline_stages(pipeline_type)
    completed = []
    for stage in stages_to_check:
        cp = read_checkpoint(pipeline_dir, project_id, stage)
        if cp and cp.get("status") == "completed":
            completed.append(stage)
    return completed


def get_next_stage(
    pipeline_dir: Path, project_id: str, pipeline_type: str | None = None
) -> Optional[str]:
    """Determine the next stage to run based on completed checkpoints.

    Uses pipeline-specific stage order so that pipelines with different
    stage sequences (e.g. cinematic vs explainer) progress correctly.
    """
    stages = get_pipeline_stages(pipeline_type) if pipeline_type else STAGES
    completed = set(get_completed_stages(pipeline_dir, project_id, pipeline_type))
    for stage in stages:
        if stage not in completed:
            return stage
    return None


# ---------------------------------------------------------------------------
# CLI
#
# A first-class entry point so a headless agent can persist/read checkpoints
# without hand-composing `python -c` snippets (Rule Zero forbids ad-hoc
# scripts) or guessing the `pipeline_dir`. Always pass --projects-dir projects
# (checkpoints live under projects/<id>/, NOT pipelines/<id>/).
#
#   python -m lib.checkpoint write --projects-dir projects --project-id sky \
#       --stage research --status completed --pipeline-type animated-explainer \
#       --artifacts-file /tmp/artifacts.json
#   python -m lib.checkpoint read --projects-dir projects --project-id sky --stage research
#   python -m lib.checkpoint next --projects-dir projects --project-id sky --pipeline-type animated-explainer
#   python -m lib.checkpoint completed --projects-dir projects --project-id sky --pipeline-type animated-explainer
#
# --artifacts-file is a JSON object mapping artifact name -> artifact payload,
# e.g. {"research_brief": {...}}. For completed/awaiting_human stages the
# canonical artifact is required (validation enforces it).
# ---------------------------------------------------------------------------

VALID_STATUSES = ("completed", "failed", "awaiting_human", "in_progress")


def _cli(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m lib.checkpoint",
        description="Read/write OpenMontage pipeline checkpoints.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _common(p: "argparse.ArgumentParser") -> None:
        p.add_argument(
            "--projects-dir",
            required=True,
            help="Root dir holding project workspaces (use: projects).",
        )
        p.add_argument("--project-id", required=True)

    w = sub.add_parser("write", help="Write a stage checkpoint.")
    _common(w)
    w.add_argument("--stage", required=True)
    w.add_argument("--status", required=True, choices=VALID_STATUSES)
    w.add_argument("--pipeline-type")
    w.add_argument(
        "--artifacts-file",
        help="JSON file: {artifact_name: payload}. Omit for status=in_progress.",
    )
    w.add_argument("--style-playbook")
    w.add_argument("--checkpoint-policy", default="guided")
    w.add_argument("--human-approval-required", action="store_true")
    w.add_argument("--human-approved", action="store_true")

    r = sub.add_parser("read", help="Read a stage checkpoint (JSON to stdout).")
    _common(r)
    r.add_argument("--stage", required=True)

    n = sub.add_parser("next", help="Print the next stage to run.")
    _common(n)
    n.add_argument("--pipeline-type")

    c = sub.add_parser("completed", help="Print completed stages (JSON list).")
    _common(c)
    c.add_argument("--pipeline-type")

    args = parser.parse_args(argv)
    projects_dir = Path(args.projects_dir)

    if args.command == "write":
        artifacts: dict[str, Any] = {}
        if args.artifacts_file:
            with open(args.artifacts_file) as f:
                artifacts = json.load(f)
        path = write_checkpoint(
            projects_dir,
            args.project_id,
            args.stage,
            args.status,
            artifacts,
            pipeline_type=args.pipeline_type,
            style_playbook=args.style_playbook,
            checkpoint_policy=args.checkpoint_policy,
            human_approval_required=args.human_approval_required,
            human_approved=args.human_approved,
        )
        print(str(path))
        return 0

    if args.command == "read":
        cp = read_checkpoint(projects_dir, args.project_id, args.stage)
        print(json.dumps(cp, indent=2) if cp is not None else "null")
        return 0 if cp is not None else 1

    if args.command == "next":
        nxt = get_next_stage(projects_dir, args.project_id, args.pipeline_type)
        print(nxt if nxt is not None else "")
        return 0

    if args.command == "completed":
        print(json.dumps(
            get_completed_stages(projects_dir, args.project_id, args.pipeline_type)
        ))
        return 0

    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    raise SystemExit(_cli())
