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

# Shell builtins / file viewers. A Bash command whose program is one of these is
# NOT a tool run, so `cat scripts/gen_vo.py` doesn't masquerade as running gen_vo.
_BASH_VIEWERS = frozenset({
    "cat", "ls", "grep", "sed", "head", "tail", "less", "more", "echo", "printf",
    "which", "mkdir", "rm", "cp", "mv", "touch", "find", "wc", "cd", "pip", "pip3",
    "export", "sleep", "chmod", "open", "test", "true", "false", "git", "#",
})
# Render/encode/build tools invoked directly or via npx.
_DIRECT_TOOLS = frozenset({"remotion", "ffmpeg", "ffprobe", "node"})

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
    """The OpenMontage tool/provider a Bash command actually RUNS, if any: a
    python tool/script (gen_vo, get_broll, update_stage, tools.elevenlabs_tts)
    or a render/encode tool (remotion, ffmpeg). Returns None for plain shell
    (ls/cat/mkdir) and inline `python -c`, so viewers don't look like tool runs."""
    cmd = (target or "").strip()
    if not cmd:
        return None
    for seg in re.split(r"&&|\|\||;|\|", cmd):
        slug = _segment_tool(seg.strip())
        if slug:
            return slug
    return None


def _segment_tool(seg: str) -> Optional[str]:
    if not seg:
        return None
    toks = seg.split()
    i = 0
    while i < len(toks) and re.match(r"^\w+=", toks[i]):  # skip VAR=val prefixes
        i += 1
    if i >= len(toks):
        return None
    prog = toks[i].split("/")[-1]
    if prog in _BASH_VIEWERS:
        return None
    m = re.search(r"-m\s+(?:tools|scripts)\.([a-zA-Z0-9_]+)", seg)
    if m:
        return m.group(1)
    if prog.startswith("python") or prog.startswith("py"):
        if re.search(r"(^|\s)-c(\s|$)", seg):
            return None  # inline snippet, not a tool
        m = re.search(r"([a-zA-Z0-9_]+)\.py\b", seg)
        return m.group(1) if m else None
    if prog == "npx":
        m = re.search(r"npx\s+(?:--[\w-]+\s+)*([a-zA-Z0-9_-]+)", seg)
        return m.group(1) if m else None
    if prog in _DIRECT_TOOLS:
        return prog
    return None


def make_event(tool: str, target: str, project_id: str, *, ts: Optional[str] = None) -> dict[str, Any]:
    """Build (but don't persist) an activity event for a tool call."""
    return {
        "ts": ts or datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "op": _op_for(tool),
        "category": _category_for(tool, target, project_id),
        "target": target or "",
        "label": _label_for(tool, target),
    }


def _label_for(tool: str, target: str) -> str:
    """A short, clean display name for the Files list / chips (the raw target is
    often a long shell command or absolute path)."""
    t = target or ""
    if tool == "Bash":
        slug = _bash_tool_slug(t)
        if slug:
            return slug
        seg = re.split(r"&&|\|\||;", t, 1)[0].strip()
        toks = seg.split()
        i = 0
        while i < len(toks) and re.match(r"^\w+=", toks[i]):
            i += 1
        prog = toks[i].split("/")[-1] if i < len(toks) else t
        return (prog or t)[:40]
    if tool in ("WebFetch", "WebSearch", "Skill"):
        return t or tool
    if "/" in t:
        return t.split("/")[-1] or t
    return t or tool


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
    """A readable skill name. For a Skill call the target IS the skill name; for
    a Read of a skill file, use the file's own name (e.g. research-director from
    skills/pipelines/<p>/research-director.md), falling back to its dir for a
    bare SKILL.md (e.g. scene-planner from skills/scene-planner/SKILL.md)."""
    if tool == "Skill":
        return target or None
    low = (target or "").lower()
    if "skills/" in low:
        p = Path(target)
        if p.suffix.lower() == ".md":
            stem = p.stem
            return p.parent.name if stem.lower() in ("skill", "readme", "index") else stem
        return p.stem or p.parent.name
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
    raw = _read_lines(activity_path(projects_dir, project_id), max_events)
    # Re-derive op/category/label from (tool, target) on read so categorization
    # improvements apply retroactively to logs written by older code — no file
    # migration needed. ts is preserved from the stored line.
    events = [make_event(e.get("tool", ""), e.get("target", "") or "", project_id, ts=e.get("ts"))
              for e in raw]
    if since:
        events = [e for e in events if (e.get("ts") or "") > since]
    if limit and len(events) > limit:
        events = events[-limit:]
    return {"events": events, "summary": summarize(events)}
