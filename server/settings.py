"""Local user settings — small JSON prefs that are NOT secrets (secrets live in the BYOK .env).

Stored at `<app_paths.home()>/settings.json` so prefs sit next to the user's data (App Support
in the packaged app; repo root in dev), never inside the read-only bundle. The device id and the
analytics opt-out both live here.

Writes are ATOMIC: serialize to a temp file in the same dir, then os.replace() — so a crash or a
concurrent reader never sees a half-written file. (The repo already has a non-atomic write footgun
in lib/checkpoint.py; this module deliberately does not repeat it.)

    read (missing file) -> {}          write -> tmp file -> os.replace -> durable
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from lib import app_paths

_DEFAULTS: dict[str, Any] = {
    # Analytics is opt-OUT: on by default, the user can turn it off. False = analytics allowed.
    "analytics_disabled": False,
}


def _path() -> Path:
    return app_paths.home() / "settings.json"


def read_all() -> dict[str, Any]:
    """Full settings dict = defaults overlaid with whatever is on disk. Never raises on a missing
    or corrupt file (a broken settings.json must not brick the app) — it falls back to defaults."""
    path = _path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError):
            data = {}  # corrupt/unreadable -> defaults; don't crash the backend
    return {**_DEFAULTS, **data}


def get(key: str, default: Any = None) -> Any:
    return read_all().get(key, default)


def set_value(key: str, value: Any) -> dict[str, Any]:
    """Persist one key atomically. Returns the full settings dict after the write."""
    current = read_all()
    current[key] = value
    _write_atomic(current)
    return current


def _write_atomic(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # atomic on the same filesystem
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def device_id() -> str:
    """A stable, anonymous per-install id for analytics (NO PII). Generated once and persisted;
    the same value is reused across launches so events from one Mac group together."""
    current = read_all()
    did = current.get("device_id")
    if not did:
        did = f"dev-{uuid.uuid4().hex}"
        set_value("device_id", did)
    return did
