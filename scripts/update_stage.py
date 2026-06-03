#!/usr/bin/env python3
"""Simple stage-status updater for the headless agent.

Usage:
    python scripts/update_stage.py <project_id> <stage> <status> [pipeline_type]

Status values: in_progress | completed | awaiting_human | failed

Examples:
    python scripts/update_stage.py my-project research in_progress animated-explainer
    python scripts/update_stage.py my-project research completed animated-explainer
    python scripts/update_stage.py my-project proposal awaiting_human animated-explainer

Artifacts are optional at in_progress; required at completed/awaiting_human (add them
with the full `python -m lib.checkpoint write --artifacts-file` form if needed).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.checkpoint import write_checkpoint

VALID = {"in_progress", "completed", "awaiting_human", "failed"}

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

    path = write_checkpoint(
        Path("projects"),
        project_id,
        stage,
        status,
        artifacts={},
        pipeline_type=pipeline_type,
    )
    print(f"✓ {project_id} / {stage} → {status}  [{path}]")

if __name__ == "__main__":
    main()
