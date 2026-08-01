"""Content-addressed cache for rendered proxy clips (M2 render-once / NLE model).

A "proxy" is one scene rendered to its own clip. Rendering a scene
(Remotion/HyperFrames) is expensive; editing the arrangement (order, transitions,
audio, trims) is cheap (FFmpeg concat). So we render each scene ONCE, cache it
keyed by its render-identity, and reuse it across edits — only a scene whose
content actually changes misses the cache and re-renders.

Mirrors the on-disk cache pattern used by restyle_video/object_cutout: a sha256
key, one JSON record per key under OPENNOLAN_CACHE_DIR (or ~/.cache/opennolan),
an flock for concurrency.

CRITICAL: the identity the caller hashes must fold in the *content* of each
source file (its sha256), not just its asset id/path. A regenerated upstream
asset keeps the same id but changes pixels; keying on the id alone would serve a
stale proxy.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

_CHUNK = 1 << 20  # 1 MiB


def file_content_hash(path: Path | str) -> str:
    """Return sha256 of a file's bytes, or "" if it can't be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


class ProxyCache:
    """Disk cache mapping a scene's render-identity -> a rendered proxy clip."""

    def __init__(self, root: Optional[Path | str] = None) -> None:
        base = root or os.environ.get("OPENNOLAN_CACHE_DIR") or (Path.home() / ".cache" / "opennolan")
        self.dir = Path(base) / "proxies"

    @staticmethod
    def key(identity: dict[str, Any]) -> str:
        """sha256 over a canonical identity dict.

        The caller owns what goes in `identity` (render_runtime, renderer_family,
        canvas, the solo-scene spec, and the content-hash of source files).
        Descriptive fields (reason, labels) must be excluded by the caller to
        avoid false cache misses.
        """
        raw = json.dumps(identity, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _record_path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> Optional[dict[str, Any]]:
        """Return the cache record iff it exists AND its clip is still on disk.

        A record whose proxy_path was deleted is treated as a miss (and ignored)
        so a stale record never points the assembler at a vanished file.
        """
        p = self._record_path(key)
        if not p.is_file():
            return None
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            return None
        clip = rec.get("proxy_path")
        if not clip or not Path(clip).is_file():
            return None
        return rec

    def put(self, key: str, record: dict[str, Any]) -> None:
        """Atomically write a cache record (temp file -> os.replace)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        rec = dict(record)
        rec.setdefault("cached_at", time.time())
        tmp = self._record_path(key).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec, indent=2))
        os.replace(tmp, self._record_path(key))

    @contextlib.contextmanager
    def lock(self, key: str):
        """Best-effort cross-process lock so two identical renders don't race.

        Degrades to a no-op if the lock dir can't be created or fcntl is absent.
        """
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            import fcntl
        except Exception:
            yield
            return
        fh = open(self.dir / f"{key}.lock", "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(Exception):
                fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
