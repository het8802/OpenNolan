"""Dev-observability sink for the editor's UI session recorder.

The Studio recorder (`web/src/debug/recorder.js`) batch-POSTs timestamped events
(console output, uncaught errors, user interactions, and domain events such as
scrub/seek) to `POST /api/debug/log`. We append them, one JSON object per line
(NDJSON), to a per-session file the coding agent can read back:

    <home>/.agents/tools/logs/ui-sessions/<session>.ndjson

`home()` is the writable data root — the repo checkout in dev (so these land next
to the existing `.agents/tools/logs/backend.log`, gitignored dev tooling), or
~/Library/Application Support/OpenNolan in the packaged app. Set
`OPENNOLAN_DEBUG_LOG_DIR` to redirect the directory (used by tests to stay
hermetic).

`append_events`/`list_sessions` own IO. `analyze_session` is the "query, don't read"
layer: a session can be thousands of lines (tens of thousands of tokens), so agents
must NEVER read the raw NDJSON into context — they call the analyzer (or
`scripts/debug_session.py`), which returns a compact report (histogram + anomalies +
verbatim errors) that stays small regardless of session length.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from lib import app_paths

# Session ids are minted client-side from a wall-clock stamp + random suffix; keep
# the charset tight so a session name can never escape the logs dir or inject path
# separators. (The file name is `<session>.ndjson`.)
_SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def logs_dir() -> Path:
    override = os.environ.get("OPENNOLAN_DEBUG_LOG_DIR")
    base = Path(override) if override else (app_paths.home() / ".agents" / "tools" / "logs" / "ui-sessions")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _session_path(session: str) -> Path:
    if not session or not _SESSION_RE.match(session):
        raise ValueError("invalid session id")
    return logs_dir() / f"{session}.ndjson"


def append_events(session: str, events: list[Any]) -> int:
    """Append event objects as NDJSON. Returns the number of lines written."""
    path = _session_path(session)
    written = 0
    with path.open("a", encoding="utf-8") as fh:
        for ev in events:
            if not isinstance(ev, dict):
                continue
            fh.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
            written += 1
    return written


def list_sessions() -> list[dict[str, Any]]:
    """Newest-first summary of recorded sessions (for tooling / a future picker)."""
    out: list[dict[str, Any]] = []
    for p in logs_dir().glob("*.ndjson"):
        st = p.stat()
        out.append(
            {
                "session": p.stem,
                "bytes": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            }
        )
    out.sort(key=lambda s: s["mtime"], reverse=True)
    return out


def latest_session() -> Optional[str]:
    sessions = list_sessions()
    return sessions[0]["session"] if sessions else None


def _iter_events(session: str) -> Iterator[dict[str, Any]]:
    path = _session_path(session)
    if not path.exists():
        raise FileNotFoundError(session)
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


# Event types that are always worth surfacing verbatim (rare + high signal).
_ERROR_TYPES = {"error", "unhandledrejection", "preview.video.error", "preview.video.stalled"}


def analyze_session(session: str, *, sample: int = 15) -> dict[str, Any]:
    """Compact, token-cheap report for a session — the thing agents read INSTEAD of the raw log.

    Returns: metadata + an event-type histogram + all error/warning events verbatim (capped) +
    a domain analysis of the source-video seek lifecycle (the scrub→canvas path). The output
    size is bounded by `sample`/caps, so it stays small even for a 100k-line session.
    """
    path = _session_path(session)
    if not path.exists():
        raise FileNotFoundError(session)

    hist: Counter[str] = Counter()
    errors: list[dict[str, Any]] = []
    first_wall: Optional[str] = None
    last_wall: Optional[str] = None
    started_meta: Any = None

    req = fired = seek_started = seek_finished = 0
    stuck_sample: list[dict[str, Any]] = []
    pending: Optional[dict[str, Any]] = None  # a 'seeking' awaiting its 'seeked'

    total = 0
    for e in _iter_events(session):
        total += 1
        etype = e.get("type", "?")
        hist[etype] += 1
        wall = e.get("wall")
        if wall:
            if first_wall is None:
                first_wall = wall
            last_wall = wall
        if etype in ("session.start", "session.resume") and started_meta is None:
            started_meta = e.get("data")

        if etype in _ERROR_TYPES or (etype == "console" and e.get("level") in ("error", "warn")):
            if len(errors) < 40:
                errors.append(e)

        # Source-video seek lifecycle: a 'seeking' with no 'seeked' before the NEXT 'seeking'
        # was superseded (the browser coalesced/dropped it) — that's the stuck-canvas signature.
        if etype == "preview.seekReq":
            req += 1
            if (e.get("data") or {}).get("fired"):
                fired += 1
        elif etype == "preview.video.seeking":
            if pending is not None and len(stuck_sample) < sample:
                stuck_sample.append({"seq": pending.get("seq"), "t": pending.get("t"), **(pending.get("data") or {})})
            pending = e
            seek_started += 1
        elif etype == "preview.video.seeked":
            seek_finished += 1
            pending = None

    report: dict[str, Any] = {
        "session": session,
        "file": str(path),
        "bytes": path.stat().st_size,
        "events": total,
        "first_wall": first_wall,
        "last_wall": last_wall,
        "started": started_meta,
        "histogram": dict(hist.most_common()),
        "errors": errors,
    }

    if req or seek_started:
        rate = (seek_finished / seek_started) if seek_started else None
        report["seeks"] = {
            "requests": req,
            "fired": fired,
            "started": seek_started,
            "finished": seek_finished,
            "superseded_before_finishing": seek_started - seek_finished,
            "completion_rate": round(rate, 3) if rate is not None else None,
            "stuck_sample": stuck_sample,
        }
        if rate is not None and rate < 0.6:
            report.setdefault("notes", []).append(
                f"{100 * (1 - rate):.0f}% of source-video seeks never completed — canvas freeze on scrub. "
                "Prime suspect: the paused-scrub seek in StudioPreview sets video.currentTime without "
                "guarding on v.seeking, so a seek issued mid-seek is coalesced/dropped."
            )
    return report
