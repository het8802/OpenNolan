"""Chat thread persistence for Mission Control.

Each project can have multiple chat threads (conversations with the agent).
A thread is stored at projects/<project_id>/.mc/threads/<thread_id>.json and
holds the UI's message list plus the agent session_id, so a thread can be
reopened later and continued with its context intact (the runner resumes the
stored session_id).

The frontend owns the message format and is the sole writer (via save_thread),
which avoids write races with the streaming chat endpoint. The chat endpoint
only READS a thread's session_id to resume the right conversation.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from lib.atomic_io import atomic_write_json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def threads_dir(projects_dir: Path | str, project_id: str) -> Path:
    return Path(projects_dir) / project_id / ".mc" / "threads"


def _thread_path(projects_dir: Path | str, project_id: str, thread_id: str) -> Path:
    return threads_dir(projects_dir, project_id) / f"{thread_id}.json"


def create_thread(
    projects_dir: Path | str,
    project_id: str,
    *,
    title: str = "New chat",
    thread_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> dict[str, Any]:
    thread_id = thread_id or uuid.uuid4().hex[:12]
    ts = created_at or _now_iso()
    record = {
        "thread_id": thread_id,
        "project_id": project_id,
        "title": title or "New chat",
        "created_at": ts,
        "updated_at": ts,
        "session_id": None,
        "messages": [],
    }
    atomic_write_json(_thread_path(projects_dir, project_id, thread_id), record)
    return record


def get_thread(projects_dir: Path | str, project_id: str, thread_id: str) -> Optional[dict[str, Any]]:
    path = _thread_path(projects_dir, project_id, thread_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def list_threads(projects_dir: Path | str, project_id: str) -> list[dict[str, Any]]:
    """Return thread summaries (no message bodies), newest-updated first."""
    tdir = threads_dir(projects_dir, project_id)
    if not tdir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for f in tdir.glob("*.json"):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        out.append({
            "thread_id": rec.get("thread_id", f.stem),
            "title": rec.get("title", "Chat"),
            "created_at": rec.get("created_at", ""),
            "updated_at": rec.get("updated_at", ""),
            "message_count": len(rec.get("messages", [])),
            "session_id": rec.get("session_id"),
        })
    out.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
    return out


def save_thread(
    projects_dir: Path | str,
    project_id: str,
    thread_id: str,
    *,
    messages: list[Any],
    session_id: Optional[str] = None,
    title: Optional[str] = None,
    updated_at: Optional[str] = None,
) -> dict[str, Any]:
    """Persist the full thread blob. Creates the thread if it doesn't exist yet.
    Preserves created_at; updates updated_at."""
    existing = get_thread(projects_dir, project_id, thread_id) or {
        "thread_id": thread_id,
        "project_id": project_id,
        "created_at": _now_iso(),
        "title": "New chat",
    }
    record = {
        "thread_id": thread_id,
        "project_id": project_id,
        "title": title if title is not None else existing.get("title", "New chat"),
        "created_at": existing.get("created_at", _now_iso()),
        "updated_at": updated_at or _now_iso(),
        "session_id": session_id if session_id is not None else existing.get("session_id"),
        "messages": messages,
    }
    atomic_write_json(_thread_path(projects_dir, project_id, thread_id), record)
    return record
