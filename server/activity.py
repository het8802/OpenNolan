"""Agent activity log for Mission Control.

The agent's tool calls (Read/Write/Edit/Bash/Grep/Skill/...) stream over SSE
during a turn and are then gone. To answer "what files has the agent touched,
which skills/pipeline_defs did it read, which tools did it run" *after* the
turn — and across a backend restart — we persist every tool_use as an
append-only JSONL log per project:

    projects/<project_id>/.mc/activity.jsonl

One line per tool call: {ts, tool, op, category, target}. Append-only and
single-writer (the runner drives one live session per project), so there are
no write races; a reader sees whole lines. The /activity endpoint reads this
back and computes a small "how this was made" summary (skills, pipeline_defs,
tools/providers, op counts) for the Activity tab.

This module owns no orchestration. The runner calls ``record_tool_use`` from
its event loop; the API calls ``read_activity``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# How an op maps from a tool name. Anything not listed (e.g. an MCP/provider
# tool) is "tool".
_OP_BY_TOOL: dict[str, str] = {
    "Read": "read",
    "LS": "read",
    "NotebookRead": "read",
    "Glob": "search",
    "Grep": "search",
    "WebSearch": "search",
    "WebFetch": "fetch",
    "Write": "write",
    "NotebookEdit": "write",
    "Edit": "edit",
    "MultiEdit": "edit",
    "Bash": "exec",
    "Skill": "skill",
    "TodoWrite": "todo",
}

# Tool names we never log — internal plumbing, not "activity" the user cares about.
_SKIP_TOOLS = ("mcp__mc__",)  # the in-process ask_user question tool

# Bash invocations of OpenMontage Python tools/scripts: `python -m tools.elevenlabs_tts`,
# `python scripts/update_stage.py`, `tools/foo.py`. Used to surface "tools/providers used".
_BASH_TOOL_RES = [
    re.compile(r"python[0-9.]*\s+-m\s+tools\.([a-zA-Z0-9_]+)"),
    re.compile(r"\btools/([a-zA-Z0-9_]+)\.py"),
    re.compile(r"python[0-9.]*\s+-m\s+scripts\.([a-zA-Z0-9_]+)"),
    re.compile(r"\bscripts/([a-zA-Z0-9_]+)\.py"),
]

_DEFAULT_MAX_EVENTS = 5000  # bound the read so a long project doesn't blow up a poll


def activity_path(projects_dir: Path | str, project_id: str) -> Path:
    return Path(projects_dir) / project_id / ".mc" / "activity.jsonl"


def _op_for(tool: str) -> str:
    return _OP_BY_TOOL.get(tool, "tool")


def _category_for(tool: str, target: str, project_id: str) -> str:
    """Bucket a tool call for the Activity tab's grouped Files list:
    skill | pipeline_def | project | tool | web | framework | other."""
    low = (target or "").lower()
    if tool in ("WebSearch", "WebFetch"):
        return "web"
    if tool == "Skill" or "skills/" in low:
        return "skill"
    if "pipeline_defs/" in low:
        return "pipeline_def"
    if tool.startswith("mcp__"):
        return "tool"
    if tool == "Bash":
        return "tool" if _bash_tool_slug(target) else "exec"
    if f"projects/{project_id}/" in low or low.startswith(f"projects/{project_id}/"):
        return "project"
    if any(seg in low for seg in ("claude.md", "agent_guide.md", "lib/", "schemas/", "tools/")):
        return "framework"
    return "framework" if target else "other"


def _bash_tool_slug(target: str) -> Optional[str]:
    """Extract the OpenMontage tool/script name a Bash command runs, if any."""
    for rx in _BASH_TOOL_RES:
        m = rx.search(target or "")
        if m:
            return m.group(1)
    return None


def make_event(tool: str, target: str, project_id: str, *, ts: Optional[str] = None) -> dict[str, Any]:
    """Build (but don't persist) an activity event for a tool call."""
    return {
        "ts": ts or datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "op": _op_for(tool),
        "category": _category_for(tool, target, project_id),
        "target": target or "",
    }


def record_tool_use(
    projects_dir: Path | str,
    project_id: str,
    tool: str,
    target: str,
    *,
    ts: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Append one tool_use to the project's activity log. Returns the event, or
    None if it was skipped (internal tool) or the write failed.

    Defensive by contract: this runs in the agent's hot event loop, so it must
    never raise — a logging failure must not break the turn.
    """
    if not tool or any(tool.startswith(p) for p in _SKIP_TOOLS):
        return None
    event = make_event(tool, target, project_id, ts=ts)
    try:
        path = activity_path(projects_dir, project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
        return event
    except Exception:
        return None


def _read_lines(path: Path, max_events: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue  # skip a torn/partial line, don't fail the read
                if isinstance(obj, dict):
                    events.append(obj)
    except Exception:
        return events
    if max_events and len(events) > max_events:
        events = events[-max_events:]
    return events


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive the 'how this was made' summary from the tool log: which skills
    ran, which pipeline_defs were consulted, which tools/providers ran, and
    op tallies. Order-preserving + de-duplicated."""
    skills: list[str] = []
    pipeline_defs: list[str] = []
    tools: list[str] = []
    counts: dict[str, int] = {}
    files: set[str] = set()

    def _add(seq: list[str], val: Optional[str]) -> None:
        if val and val not in seq:
            seq.append(val)

    for e in events:
        tool = e.get("tool", "")
        op = e.get("op", "")
        cat = e.get("category", "")
        target = e.get("target", "") or ""
        counts[op] = counts.get(op, 0) + 1

        if cat == "skill":
            _add(skills, _skill_label(tool, target))
        elif cat == "pipeline_def":
            _add(pipeline_defs, Path(target).stem or target)
        if tool == "Bash":
            _add(tools, _bash_tool_slug(target))
        elif tool.startswith("mcp__"):
            _add(tools, tool.replace("mcp__", "").replace("__", ":"))

        if op in ("read", "write", "edit") and target and "/" in target:
            files.add(target)

    return {
        "skills": skills,
        "pipeline_defs": pipeline_defs,
        "tools": tools,
        "counts": counts,
        "files_touched": len(files),
        "event_count": len(events),
    }


def _skill_label(tool: str, target: str) -> Optional[str]:
    """A readable skill name from a Skill call (target is the skill name) or a
    Read of a skills/.../SKILL.md path (use the containing dir name)."""
    if tool == "Skill":
        return target or None
    low = target.lower()
    if "skills/" in low:
        parts = [p for p in target.split("/") if p]
        # the segment after the last 'skills' dir is the skill's folder name
        try:
            idx = max(i for i, p in enumerate(parts) if p.lower() == "skills")
            if idx + 1 < len(parts):
                nxt = parts[idx + 1]
                return nxt if not nxt.lower().endswith(".md") else Path(target).parent.name
        except ValueError:
            pass
        return Path(target).parent.name or Path(target).stem
    return None


def read_activity(
    projects_dir: Path | str,
    project_id: str,
    *,
    limit: Optional[int] = None,
    since: Optional[str] = None,
    max_events: int = _DEFAULT_MAX_EVENTS,
) -> dict[str, Any]:
    """Read a project's activity log (chronological) plus a synthesized summary.

    ``since`` keeps only events with ts > since (incremental polling).
    ``limit`` keeps the most recent N (applied after ``since``). The summary is
    always computed over the returned events.
    """
    events = _read_lines(activity_path(projects_dir, project_id), max_events)
    if since:
        events = [e for e in events if (e.get("ts") or "") > since]
    if limit and len(events) > limit:
        events = events[-limit:]
    return {"events": events, "summary": summarize(events)}
