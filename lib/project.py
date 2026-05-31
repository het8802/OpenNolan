"""Project workspace creation and discovery.

A project lives at ``projects/<project_id>/`` and is identified by a
``project.json`` manifest written at creation time. The manifest is the
single source of truth for two things the Mission Control UI needs and that
the filesystem alone can't answer:

1. **Which directories are real projects.** ``projects/`` also accumulates
   scratch and analysis dirs (``_analysis``, ``demos``), stray files
   (``.DS_Store``), and legacy reel folders. Globbing ``projects/*`` would
   surface all of that. A directory is a project iff it contains a manifest.

2. **The pipeline_type before any checkpoint exists.** Checkpoint stage
   ordering (``get_next_stage``) needs ``pipeline_type``, but a freshly
   created, not-yet-run project has no checkpoint to read it from. The
   manifest carries it from creation.

    projects/<project_id>/
    ├── project.json          # the manifest (this module owns it)
    ├── artifacts/
    ├── assets/{images,video,audio,music}/
    └── renders/
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from lib.atomic_io import atomic_write_json

MANIFEST_NAME = "project.json"
ASSET_SUBDIRS = ("images", "video", "audio", "music")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class ProjectExistsError(Exception):
    """Raised when creating a project whose id already exists."""


def slugify(name: str) -> str:
    """Derive a kebab-case project id from a human title."""
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"Cannot derive a project id from name: {name!r}")
    return slug


def project_dir(projects_dir: Path | str, project_id: str) -> Path:
    return Path(projects_dir) / project_id


def manifest_path(projects_dir: Path | str, project_id: str) -> Path:
    return project_dir(projects_dir, project_id) / MANIFEST_NAME


def create_project(
    projects_dir: Path | str,
    name: str,
    pipeline_type: str,
    *,
    created_at: Optional[str] = None,
) -> dict[str, Any]:
    """Scaffold a project workspace and write its manifest.

    Returns the manifest dict. Raises ``ProjectExistsError`` if a manifest
    already exists for the derived id (so the API can return 409 instead of
    silently overwriting — the exact collision both behavioral spikes hit
    when they reused the same title).

    ``created_at`` is injectable for deterministic tests; callers normally
    omit it and get a UTC ISO-8601 timestamp.
    """
    projects_dir = Path(projects_dir)
    project_id = slugify(name)
    pdir = project_dir(projects_dir, project_id)
    mpath = manifest_path(projects_dir, project_id)

    if mpath.exists():
        raise ProjectExistsError(
            f"Project {project_id!r} already exists at {pdir}"
        )

    (pdir / "artifacts").mkdir(parents=True, exist_ok=True)
    for sub in ASSET_SUBDIRS:
        (pdir / "assets" / sub).mkdir(parents=True, exist_ok=True)
    (pdir / "renders").mkdir(parents=True, exist_ok=True)

    manifest = {
        "version": "1.0",
        "project_id": project_id,
        "name": name,
        "pipeline_type": pipeline_type,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(mpath, manifest)
    return manifest


def read_project_manifest(
    projects_dir: Path | str, project_id: str
) -> Optional[dict[str, Any]]:
    """Return a project's manifest, or None if it isn't a real project."""
    mpath = manifest_path(projects_dir, project_id)
    if not mpath.exists():
        return None
    with open(mpath) as f:
        return json.load(f)


def is_project(projects_dir: Path | str, project_id: str) -> bool:
    return manifest_path(projects_dir, project_id).exists()


def list_projects(projects_dir: Path | str) -> list[dict[str, Any]]:
    """Return manifests of all real projects, newest first.

    Only directories containing a ``project.json`` are returned; scratch /
    analysis dirs, stray files, and legacy (pre-manifest) projects are
    excluded by construction.
    """
    projects_dir = Path(projects_dir)
    if not projects_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(projects_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest = read_project_manifest(projects_dir, child.name)
        if manifest is not None:
            out.append(manifest)
    out.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return out


def get_project_pipeline_type(
    projects_dir: Path | str, project_id: str
) -> Optional[str]:
    """Resolve a project's pipeline_type.

    Prefers the manifest (works for an empty project with no checkpoints).
    Falls back to the latest checkpoint's ``pipeline_type`` for legacy
    projects created before manifests existed.
    """
    manifest = read_project_manifest(projects_dir, project_id)
    if manifest and manifest.get("pipeline_type"):
        return manifest["pipeline_type"]

    try:
        from lib.checkpoint import get_latest_checkpoint

        cp = get_latest_checkpoint(Path(projects_dir), project_id)
        if cp:
            pt = cp.get("pipeline_type")
            return pt if pt and pt != "unknown" else None
    except Exception:
        pass
    return None
