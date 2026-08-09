"""A durable local outbox for events produced by a SEPARATE PROCESS.

`server/agent_runner.py` instructs the agent to run `python scripts/update_stage.py …`, so the
primary writer of pipeline stage transitions is its own short-lived process. It has no PostHog
client, no session id, and no analytics module it may import — `scripts/` is not `server/`, and
a module-registered observer inside `lib/checkpoint.py` would not exist there either.

Claiming "a CLI write correctly emits nothing" would silently drop the majority of real stage
transitions. So the CLI appends a line here, and the backend drains it on its next flush.

ponytail: one append-only JSONL file with a size cap. No queue, no lock — appends of a single
short line are atomic enough on macOS/Linux for this, and a lost line costs one event.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator, Optional

FILENAME = ".telemetry_outbox.jsonl"
# A runaway writer must not be able to fill the disk or hand the backend an unbounded drain.
MAX_LINES = 500


def _path(home: Optional[Path] = None) -> Path:
    if home is None:
        from lib import app_paths

        home = app_paths.home()
    return Path(home) / FILENAME


def capture(event: str, properties: dict[str, Any], home: Optional[Path] = None) -> None:
    """Append one event. Named `capture` to mirror analytics.capture: same contract,
    different transport, and it keeps contract test 1a/1b able to see the call site.

     Never raises: a CLI that fails because telemetry failed is worse than
    a missing event."""
    try:
        path = _path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_LINES * 512:
            return  # capped; the drain will catch up or the file will be truncated below
        with open(path, "a") as fh:
            fh.write(json.dumps({"event": event, "properties": properties}) + "\n")
    except Exception:
        pass


def drain(home: Optional[Path] = None) -> Iterator[tuple[str, dict[str, Any]]]:
    """Read and REMOVE every queued event.

    Rename-then-read, so a writer appending concurrently lands in a fresh file rather than
    having its line deleted out from under it."""
    try:
        path = _path(home)
        if not path.is_file():
            return
        staged = path.with_suffix(".draining")
        os.replace(path, staged)
    except OSError:
        return
    try:
        for line in staged.read_text().splitlines()[:MAX_LINES]:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            name = record.get("event")
            props = record.get("properties")
            if isinstance(name, str) and isinstance(props, dict):
                yield name, props
    except OSError:
        return
    finally:
        try:
            staged.unlink()
        except OSError:
            pass
