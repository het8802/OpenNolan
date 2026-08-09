"""Local user settings — small JSON prefs that are NOT secrets (secrets live in the BYOK .env).

Stored at `<app_paths.home()>/settings.json` so prefs sit next to the user's data (App Support
in the packaged app; repo root in dev), never inside the read-only bundle. The analytics opt-out
lives here; the install id does NOT (see device_id — it moved to ~/.opennolan/install_id so one
machine is one install, whatever worktree is running).

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
from typing import Any, Optional

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


# The install id lives OUTSIDE every worktree and outside OPENNOLAN_HOME (see device_id).
INSTALL_ID_PATH = Path(".opennolan") / "install_id"


def _device_id_from_settings() -> str:
    """The pre-move location: settings.json at home(). Only reached when ~ is unwritable."""
    current = read_all()
    did = current.get("device_id")
    if not did:
        did = f"dev-{uuid.uuid4().hex}"
        set_value("device_id", did)
    return did


def device_id() -> str:
    """A stable, anonymous per-install id for analytics (NO PII) — PostHog's `distinct_id`.

    Stored at `~/.opennolan/install_id`, deliberately outside both the repo and
    OPENNOLAN_HOME. It used to live in settings.json at home(), and home() is the repo root
    in dev — so every worktree and clone minted its own id and one developer machine read as
    N separate installs. `OPENNOLAN_INSTALL_ID` overrides the file, which is what lets an
    end-to-end test pin a known id. settings.json stays the fallback when ~ is unwritable.

    Nothing has shipped, so the old settings.json id is deliberately NOT migrated — after
    beta, changing the id source would split every user's history."""
    override = (os.environ.get("OPENNOLAN_INSTALL_ID") or "").strip()
    if override:
        return override
    path = Path.home() / INSTALL_ID_PATH
    try:
        did = path.read_text().strip()
        if did:
            return did
    except OSError:
        pass
    return _publish_install_id(path, f"dev-{uuid.uuid4().hex}")


class InstallIdUnavailable(Exception):
    """The id could not be published or read. Analytics must DISABLE rather than invent one:
    a second id for one launch breaks every readback join this instrumentation depends on."""


def _publish_install_id(path: Path, candidate: str) -> str:
    """Publish `candidate` atomically, or adopt whoever won. Raises InstallIdUnavailable.

    WRITE-THEN-PUBLISH, with link() and never rename(). `open(path, "x")` creates the inode
    BEFORE the bytes are written, so a process that loses that window reads an empty file —
    and Electron spawns the backend, so both booting together is the normal case, not an edge
    case. `rename()` cannot fix it either: rename REPLACES its destination, so two complete
    temps would both "win" and the later write would silently overwrite the persisted id.
    `link()` is the no-replace primitive — it fails EEXIST, and that is what produces a winner.

    No retry, ever. rev 2 of the plan proposed "retry until non-empty"; if the winner is killed
    between inode creation and write, the file is permanently empty and every later boot spins
    forever inside a synchronous boot path. That trades an occasional duplicate id for a
    permanent startup failure.
    """
    tmp: Optional[Path] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Same directory: link() cannot cross filesystems.
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".install_id.", suffix=".tmp")
        tmp = Path(tmp_name)
        with os.fdopen(fd, "w") as fh:
            fh.write(candidate + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            # Someone published first. Their file is whole — unless a PREVIOUS buggy build
            # left a zero-byte one behind, which is why empty is a disabled state, not an id.
            won = path.read_text().strip()
            if not won:
                raise InstallIdUnavailable(f"{path} exists but is empty")
            return won
        _fsync_dir(path.parent)
        return candidate
    except InstallIdUnavailable:
        raise
    except OSError as exc:
        # Includes an unwritable ~ (the packaged app's sandbox, a read-only home). settings.json
        # at home() is the documented fallback and predates this path.
        try:
            return _device_id_from_settings()
        except OSError:
            raise InstallIdUnavailable(str(exc)) from exc
    finally:
        # A directory fsync that throws AFTER a successful link must still clean up, or a
        # linear implementation leaks one private temp file per boot.
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def _fsync_dir(directory: Path) -> None:
    """Persist the new directory entry. A crash between link() and this leaves the id
    unpublished on some filesystems — recoverable, because the next boot simply re-publishes."""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
