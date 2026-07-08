#!/usr/bin/env python3
"""Simple stage-status updater for the headless agent.

Usage:
    python scripts/update_stage.py <project_id> <stage> <status> [pipeline_type]

Status values: in_progress | completed | awaiting_human | failed

Examples:
    python scripts/update_stage.py my-project research in_progress animated-explainer
    python scripts/update_stage.py my-project research completed animated-explainer
    python scripts/update_stage.py my-project proposal awaiting_human animated-explainer

Artifacts are optional at in_progress. At completed/awaiting_human the stage's canonical artifact
is REQUIRED — this script auto-loads it from `<project>/artifacts/<canonical>.json` (the location the
agent writes it), so you just write the artifact JSON and then flip the stage. If the artifact file
is missing, it errors with the exact path it looked for (no more opaque CheckpointValidationError).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import app_paths
from lib.checkpoint import CANONICAL_STAGE_ARTIFACTS, write_checkpoint

VALID = {"in_progress", "completed", "awaiting_human", "failed"}
NEEDS_ARTIFACT = {"completed", "awaiting_human"}

def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    project_id = sys.argv[1]
    stage = sys.argv[2]
    status = sys.argv[3]
    pipeline_type = sys.argv[4] if len(sys.argv) > 4 else None

    if status not in VALID:
        print(f"ERROR: status must be one of {VALID}, got {status!r}", file=sys.stderr)
        sys.exit(1)

    # Resolve the writable projects dir via app_paths (honors OPENNOLAN_PROJECTS_DIR / OPENNOLAN_HOME),
    # NOT a bare relative "projects" — in the packaged app cwd is the READ-ONLY bundle, so a relative
    # path would write into the bundle and silently fail. Dev is unchanged (defaults to repo/projects).
    projects_dir = app_paths.projects_dir()

    # completed/awaiting_human require the stage's canonical artifact. Auto-load it from the project's
    # artifacts/ dir (where the agent writes it) so the agent can flip the stage after writing the JSON
    # — instead of hitting an opaque validation error with no path to fix it.
    artifacts: dict = {}
    canonical = CANONICAL_STAGE_ARTIFACTS.get(stage)
    if status in NEEDS_ARTIFACT and canonical:
        art_path = projects_dir / project_id / "artifacts" / f"{canonical}.json"
        if not art_path.is_file():
            print(
                f"ERROR: stage {stage!r} → {status!r} needs the canonical artifact {canonical!r}, "
                f"but {art_path} does not exist.\n"
                f"Write the artifact JSON there first, then re-run this command.",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            artifacts[canonical] = json.loads(art_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: could not read/parse {art_path}: {exc}", file=sys.stderr)
            sys.exit(1)

    path = write_checkpoint(
        projects_dir,
        project_id,
        stage,
        status,
        artifacts=artifacts,
        pipeline_type=pipeline_type,
    )
    print(f"✓ {project_id} / {stage} → {status}  [{path}]")

if __name__ == "__main__":
    main()
