"""Atomic JSON writes.

Writing JSON with a plain ``open(path, "w")`` truncates the file before the
new bytes land, so any concurrent reader — the Mission Control UI polling
``/state``, a file watcher, or a second CLI run — can observe a half-written,
invalid file. Every state file OpenMontage persists (checkpoints, decision
logs, project manifests) goes through ``atomic_write_json`` instead:

    write to a temp file in the same directory  ->  fsync  ->  os.replace

``os.replace`` is atomic on POSIX when source and destination share a
filesystem (guaranteed here because the temp file is created in the target
directory). A reader therefore sees either the old file or the fully written
new one. Never a partial. A failed serialization leaves the existing file
untouched and does not leak a ``.tmp`` file.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path | str, data: Any, *, indent: int = 2) -> None:
    """Atomically serialize ``data`` as JSON to ``path``.

    Creates parent directories as needed. On any failure during
    serialization or the replace, the temp file is removed and the
    pre-existing file at ``path`` (if any) is left intact.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # mkstemp in the target directory keeps os.replace on one filesystem.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Don't leak the temp file if serialization or the replace failed.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
