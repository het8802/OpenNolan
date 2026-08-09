"""The abandonment sweep (catalog row 27).

Every other event in this taxonomy fires when a user DOES something. Abandonment is the
absence of that, so it has no hook — a project that dies dies quietly, and "where do projects
die?" is the one question the rest of the catalog structurally cannot answer.

This is the one genuinely SESSION-LESS event: it is detached work about projects, not about a
person's current session, so it is install-scoped by design.

ponytail: swept once per backend start, at most once per project per day. A desktop app is not
a server — it has no scheduler and no overnight uptime to hang a real cron on, so "on start,
if a day has passed" is the same coverage for none of the machinery.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from server import analytics, settings

_LAST_SWEEP_KEY = "lifecycle_last_sweep"
_SWEEP_INTERVAL_S = 20 * 60 * 60  # a day, minus enough slack that a daily launch still sweeps
_STALL_DAYS = 3
_DAY_EDGES = (7, 14, 30, 90)


def sweep(projects_dir: Path | str, *, force: bool = False) -> int:
    """Emit `project_stalled` for projects with no recent activity. Returns the count."""
    if not force and not _due():
        return 0
    emitted = 0
    try:
        from lib.project import list_projects

        for record in list_projects(projects_dir):
            props = _stalled_props(Path(projects_dir), record)
            if props is None:
                continue
            analytics.capture("project_stalled", props)
            emitted += 1
        settings.set_value(_LAST_SWEEP_KEY, time.time())
    except Exception:
        pass  # a sweep that fails must never keep the backend from starting
    return emitted


def _due() -> bool:
    try:
        last = float(settings.get(_LAST_SWEEP_KEY, 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    return (time.time() - last) >= _SWEEP_INTERVAL_S


def _stalled_props(projects_dir: Path, record: dict[str, Any]) -> Optional[dict[str, Any]]:
    project_id = record.get("project_id")
    if not project_id:
        return None
    pdir = projects_dir / project_id
    idle_days = _idle_days(pdir)
    if idle_days is None or idle_days < _STALL_DAYS:
        return None
    return {
        "last_activity_days": _bucket(idle_days, _DAY_EDGES),
        "furthest_stage": _furthest_stage(pdir),
        "has_assets": (pdir / "assets").is_dir() and any((pdir / "assets").rglob("*")),
        "project_id": analytics.project_key(projects_dir, project_id),
    }


def _idle_days(pdir: Path) -> Optional[float]:
    """Newest mtime anywhere shallow in the project. Deliberately not a full rglob: a project
    with a 4K master and a proxy cache is exactly the one where a deep walk is expensive."""
    try:
        newest = max(
            (p.stat().st_mtime for p in list(pdir.iterdir()) + list((pdir / "artifacts").glob("*"))),
            default=None,
        )
    except OSError:
        return None
    if newest is None:
        return None
    return (time.time() - newest) / 86400.0


def _furthest_stage(pdir: Path) -> str:
    """The last stage with a checkpoint on disk — where the pipeline actually stopped."""
    order = ["ideation", "script", "assets", "composition", "render", "review", "publish"]
    found = "unknown"
    try:
        names = {p.stem for p in (pdir / "checkpoints").glob("*.json")}
    except OSError:
        return found
    for stage in order:
        if stage in names:
            found = stage
    return found


def _bucket(value: float, edges: tuple[float, ...]) -> str:
    prev = "0"
    for edge in edges:
        if value < edge:
            return f"{prev}-{int(edge)}"
        prev = str(int(edge))
    return f"{prev}+"
