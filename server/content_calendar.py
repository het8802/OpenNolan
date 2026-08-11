"""Shared on-disk content scheduling for Mission Control and its agent."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from lib.atomic_io import atomic_write_json
from lib.project import (
    FINAL_RENDER_REL,
    final_render_path,
    get_project_record,
    is_safe_project_id,
    list_projects,
)


CHANNELS = ("tiktok", "instagram", "youtube")
SCHEDULE_VERSION = "1.0"
COLLISION_WINDOW = timedelta(hours=2)

# Reused from the daily-tech-carousel posting baseline: lunch/early afternoon in
# the host's local timezone, expressed as minutes after midnight by weekday.
DEFAULT_LOCAL_MINUTES = (12 * 60 + 15, 13 * 60, 14 * 60, 13 * 60 + 45, 11 * 60 + 45, 15 * 60, 13 * 60 + 30)

_CACHE_RE = re.compile(r"<!-- CACHE_JSON\n(.*?)\nCACHE_JSON -->", re.DOTALL)
_write_lock = threading.RLock()


class ScheduleValidationError(ValueError):
    pass


class FinalRenderMissing(FileNotFoundError):
    pass


def schedule_path(projects_dir: Path | str, project_id: str) -> Path:
    # Defense in depth: `get_project_record` already rejects a non-plain id, but this is the
    # only function that turns an id into a WRITE target, so it refuses one too.
    if not is_safe_project_id(project_id):
        raise ScheduleValidationError(f"invalid project id: {project_id!r}")
    return Path(projects_dir) / project_id / "artifacts" / "content_schedule.json"


def timing_skill_path(projects_dir: Path | str) -> Path:
    return Path(projects_dir) / ".content-calendar" / "content-calendar-scheduling" / "SKILL.md"


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ScheduleValidationError("scheduled_at must be an ISO-8601 datetime")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScheduleValidationError("scheduled_at must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        # `.astimezone()` on a NAIVE value reads the platform's local rules FOR THAT DATE.
        # `datetime.now().astimezone().tzinfo` looked equivalent but is today's offset frozen
        # into a fixed-offset object, so a slot across a DST boundary was stamped an hour off
        # (a naive 2026-11-20 18:45 requested in August became 18:45 PDT, i.e. 17:45 PST).
        parsed = parsed.astimezone()
    parsed = parsed.astimezone(timezone.utc)
    if parsed <= datetime.now(timezone.utc):
        raise ScheduleValidationError("scheduled_at must be in the future")
    return parsed


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _channels(values: Iterable[str]) -> list[str]:
    requested = {str(value).strip().lower() for value in values if str(value).strip()}
    unknown = requested.difference(CHANNELS)
    if unknown:
        raise ScheduleValidationError(f"unknown channel(s): {', '.join(sorted(unknown))}")
    selected = [channel for channel in CHANNELS if channel in requested]
    if not selected:
        raise ScheduleValidationError("choose at least one channel")
    return selected


def read_project_schedule(projects_dir: Path | str, project_id: str) -> dict[str, Any]:
    path = schedule_path(projects_dir, project_id)
    if not path.is_file():
        return {"version": SCHEDULE_VERSION, "entries": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ScheduleValidationError(f"invalid schedule file for {project_id}")
    return data


def list_calendar_entries(projects_dir: Path | str) -> list[dict[str, Any]]:
    projects_root = Path(projects_dir)
    entries: list[dict[str, Any]] = []
    for project in list_projects(projects_root):
        project_id = project["project_id"]
        for saved in read_project_schedule(projects_root, project_id)["entries"]:
            entry = dict(saved)
            entry["project_name"] = project.get("name") or project_id
            render = projects_root / project_id / entry["render_ref"]["path"]
            try:
                stat = render.stat()
                entry["playback"] = {"path": entry["render_ref"]["path"], "mtime": stat.st_mtime_ns // 1000}
            except OSError:
                entry["playback"] = {"path": entry["render_ref"]["path"], "mtime": None}
            entries.append(entry)
    entries.sort(key=lambda entry: entry.get("scheduled_at", ""))
    return entries


def _collides(candidate: datetime, entries: list[dict[str, Any]]) -> bool:
    for entry in entries:
        try:
            existing = datetime.fromisoformat(entry["scheduled_at"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        if abs(existing.astimezone(timezone.utc) - candidate) < COLLISION_WINDOW:
            return True
    return False


def _open_slot(candidate: datetime, entries: list[dict[str, Any]]) -> datetime:
    for day_offset in range(15):
        option = candidate + timedelta(days=day_offset)
        if not _collides(option, entries):
            return option
    raise ScheduleValidationError("could not find an open slot in the next 15 days")


def _niche_key(niche: str | None) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", str(niche or "general").strip().lower()).strip("-")
    return (key or "general")[:64]


def _valid_local_time(value: str) -> str:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(value or "")):
        raise ScheduleValidationError("learned_local_time must use 24-hour HH:MM")
    return value


def read_timing_cache(projects_dir: Path | str) -> dict[str, dict[str, str]]:
    path = timing_skill_path(projects_dir)
    if not path.is_file():
        return {}
    match = _CACHE_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _save_timing(projects_dir: Path | str, niche: str, local_time: str) -> None:
    cache = read_timing_cache(projects_dir)
    cache[_niche_key(niche)] = {
        "local_time": _valid_local_time(local_time),
        "source": "web_research",
        "updated_at": _iso_utc(datetime.now(timezone.utc)),
    }
    body = (
        "---\nname: content-calendar-timing-cache\n"
        "description: Writable per-niche posting-time knowledge used by OpenNolan scheduling.\n---\n\n"
        "# Learned posting times\n\n"
        "This runtime cache is maintained by the `schedule_content` tool. Times use the host's local timezone.\n\n"
        "<!-- CACHE_JSON\n"
        f"{json.dumps(cache, indent=2, sort_keys=True)}\n"
        "CACHE_JSON -->\n"
    )
    _write_text_atomic(timing_skill_path(projects_dir), body)


def _next_local_slot(local_time: str | None = None) -> datetime:
    # Walk WALL-CLOCK days naively ("13:00 next Tuesday"), then attach the offset that is
    # actually in force on that date. Carrying today's offset forward on an aware value put
    # the chosen slot an hour off on the far side of a DST transition.
    now = datetime.now()
    floor = now.astimezone() + timedelta(hours=1)
    for day_offset in range(8):
        day = now + timedelta(days=day_offset)
        minutes = DEFAULT_LOCAL_MINUTES[day.weekday()]
        if local_time:
            hour, minute = (int(part) for part in local_time.split(":"))
        else:
            hour, minute = divmod(minutes, 60)
        candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone()
        if candidate > floor:
            return candidate.astimezone(timezone.utc)
    raise ScheduleValidationError("could not choose a future posting slot")


def create_scheduled_entry(
    projects_dir: Path | str,
    project_id: str,
    scheduled_at: str | None,
    channels: Iterable[str],
    *,
    created_by: str,
    avoid_collisions: bool = False,
    niche: str | None = None,
    learned_local_time: str | None = None,
) -> dict[str, Any]:
    projects_root = Path(projects_dir)
    if get_project_record(projects_root, project_id) is None:
        raise ScheduleValidationError(f"project {project_id!r} not found")
    try:
        render = final_render_path(projects_root, project_id)
    except (OSError, ValueError) as exc:
        raise FinalRenderMissing(str(exc)) from exc
    if not render.is_file():
        raise FinalRenderMissing("schedule is available after renders/final.mp4 exists")
    if created_by not in {"user", "agent"}:
        raise ScheduleValidationError("created_by must be user or agent")

    selected_channels = _channels(channels)
    timing_source = "requested" if scheduled_at else "baseline"
    cached_time = None
    with _write_lock:
        if learned_local_time:
            if not niche:
                raise ScheduleValidationError("niche is required with learned_local_time")
            _save_timing(projects_root, niche, learned_local_time)
            cached_time = _valid_local_time(learned_local_time)
            timing_source = "researched"
        elif niche:
            cached = read_timing_cache(projects_root).get(_niche_key(niche)) or {}
            cached_time = cached.get("local_time")
            if cached_time:
                timing_source = "cache"

        candidate = _parse_time(scheduled_at) if scheduled_at else _next_local_slot(cached_time)
        # A project holds ONE scheduled slot, so re-scheduling REPLACES it (same id) instead of
        # appending a second entry. Both the UI's Schedule dialog and the agent's
        # `schedule_content` tool land here, so neither can leave a duplicate behind.
        schedule = read_project_schedule(projects_root, project_id)
        # Earliest-first, matching the UI's `entryForProject`, so both sides agree on WHICH entry
        # is "the" slot if an older file still carries duplicates from before this rule.
        mine = sorted(
            (saved for saved in schedule["entries"] if saved.get("project_id") == project_id),
            key=lambda saved: str(saved.get("scheduled_at") or ""),
        )
        previous = mine[0] if mine else None
        if avoid_collisions:
            # The slot we are about to overwrite must not push itself out of the way.
            others = [other for other in list_calendar_entries(projects_root) if other.get("project_id") != project_id]
            candidate = _open_slot(candidate, others)

        stat = render.stat()
        entry = {
            "id": (previous or {}).get("id") or uuid.uuid4().hex,
            "project_id": project_id,
            "render_ref": {
                "path": FINAL_RENDER_REL,
                "size_bytes": stat.st_size,
                "mtime_us": stat.st_mtime_ns // 1000,
            },
            "scheduled_at": _iso_utc(candidate),
            "channels": selected_channels,
            "status": "scheduled",
            "created_by": created_by,
            "created_at": (previous or {}).get("created_at") or _iso_utc(datetime.now(timezone.utc)),
            "timing_source": timing_source,
            "replaced": previous is not None,
        }
        schedule["entries"] = [saved for saved in schedule["entries"] if saved.get("project_id") != project_id] + [
            entry
        ]
        atomic_write_json(schedule_path(projects_root, project_id), schedule)
        return entry


def failure_class(exc: Exception) -> str:
    if isinstance(exc, FinalRenderMissing):
        return "no_final_render"
    if isinstance(exc, ScheduleValidationError):
        message = str(exc)
        if "channel" in message:
            return "invalid_channels"
        if "time" in message or "future" in message or "slot" in message:
            return "invalid_time"
        return "invalid_request"
    return "storage"
