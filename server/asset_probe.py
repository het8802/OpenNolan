"""Asset identity + the ingest media probe (catalog rows 29-32).

Two things live here because they are produced at the same moment and only make sense together:

  * `asset_id` — an opaque random uuid4 PERSISTED in the project's `asset_manifest.json`.
    Never the filename: `RULES.md` says a user can drop any media on us, so a name is customer
    and campaign text. The id is what rows 34 and 37 join on.
  * `asset_fingerprint` — `HMAC(install_secret, sha256(bytes))[:16]`. Install-stable so
    "did they reuse this asset across projects" is answerable, and NOT cross-install
    correlatable, so two users with the same stock clip never link to each other.
    Named `asset_fingerprint` and not `content_fingerprint`: the latter contains the reserved
    substring `content` and `_scrub` rewrites it to `content_fingerprint_len: 16`.

The probe itself exists because ingest currently records only width/height/duration, while
**codec, container, pixel format, transfer and fps are the fields that predict render success**
— and none of them were ever captured. That is the difference between "renders fail sometimes"
and "HEVC 10-bit HLG fails, H.264 does not".
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Optional

MANIFEST = "asset_manifest.json"
_SECRET_FILE = ".asset_secret"

# ffprobe fields we keep. Every one is a fixed vocabulary produced by the demuxer, not by the
# user — a container name cannot carry a campaign name the way a filename can.
_PROBE_TIMEOUT_S = 20


def _install_secret(project_dir: Path) -> bytes:
    """A per-install HMAC key so a fingerprint is stable HERE and meaningless anywhere else.

    Lives beside the project rather than in the analytics module because it must survive a
    backend restart and must never be uploaded."""
    path = project_dir.parent / _SECRET_FILE
    try:
        existing = path.read_bytes().strip()
        if existing:
            return existing
    except OSError:
        pass
    secret = uuid.uuid4().hex.encode()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "xb") as fh:
            fh.write(secret)
        return secret
    except FileExistsError:
        try:
            return path.read_bytes().strip() or secret
        except OSError:
            return secret
    except OSError:
        return secret


def fingerprint(project_dir: Path, target: Path) -> Optional[str]:
    """Content-derived, install-scoped. Reads the file in chunks — an imported asset can be a
    4K master and a full read into memory is not worth a telemetry property."""
    try:
        digest = hashlib.sha256()
        with open(target, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return hmac.new(_install_secret(project_dir), digest.digest(), hashlib.sha256).hexdigest()[:16]
    except OSError:
        return None


def _manifest_path(project_dir: Path) -> Path:
    return project_dir / MANIFEST


def read_manifest(project_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads(_manifest_path(project_dir).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _legacy_key(project_dir: Path, rel_path: str) -> str:
    """The pre-fix manifest key: PROJECTS-DIR-relative, so it led with the project id.

    The manifest lives inside the project, so keying it by the project's own name was
    redundant — and the only reader looks up the project-relative path, which is why every id
    minted under the old key was invisible. Kept as a fallback so those ids SURVIVE: minting a
    second id for an asset that already has one breaks exactly the historical join the id
    exists for."""
    return f"{project_dir.name}/{rel_path}"


def lookup_asset_id(project_dir: Path, rel_path: str, manifest: Optional[dict[str, Any]] = None) -> Optional[str]:
    """The persisted id for an asset, or None. READ-ONLY — never mints, never writes.

    For callers on a hot path (GET /assets is polled every 4s) that must not mint an id for a
    file nobody ingested. Pass `manifest` to read it once for a whole listing."""
    m = read_manifest(project_dir) if manifest is None else manifest
    for key in (rel_path, _legacy_key(project_dir, rel_path)):
        entry = m.get(key)
        if isinstance(entry, dict) and entry.get("asset_id"):
            return str(entry["asset_id"])
    return None


def asset_id(project_dir: Path, rel_path: str) -> str:
    """The persisted id for one asset, minted on first sight.

    Best-effort persistence: an unwritable project dir yields a fresh id per call, which makes
    the join weaker but never wrong — a missing join key is honest, an invented stable one is not.
    """
    manifest = read_manifest(project_dir)
    entry = manifest.get(rel_path)
    if isinstance(entry, dict) and entry.get("asset_id"):
        return str(entry["asset_id"])
    # An id minted under the legacy key is ADOPTED and re-filed, never replaced: re-ingesting
    # the same path must not hand the asset a second identity.
    legacy = manifest.pop(_legacy_key(project_dir, rel_path), None)
    if isinstance(legacy, dict) and legacy.get("asset_id"):
        manifest[rel_path] = {**legacy}
        try:
            _write_manifest(project_dir, manifest)
        except OSError:
            pass
        return str(legacy["asset_id"])
    new_id = uuid.uuid4().hex[:16]
    manifest[rel_path] = {**(entry if isinstance(entry, dict) else {}), "asset_id": new_id}
    try:
        _write_manifest(project_dir, manifest)
    except OSError:
        pass
    return new_id


def _write_manifest(project_dir: Path, manifest: dict[str, Any]) -> None:
    path = _manifest_path(project_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    os.replace(tmp, path)


def probe(target: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """`(fields, failure_class)`. Exactly one is non-None.

    The failure branch did not exist before: the old probe tested only for SUCCESS, so a
    non-zero exit produced an unclamped trim with no signal anywhere. "Accepted but
    uninspectable" is a real class of media and it deserves a name."""
    if shutil.which("ffprobe") is None:
        return None, "ffprobe_missing"
    try:
        proc = subprocess.run(
            # -protocol_whitelist file: a crafted playlist-ish "video" can name external URLs
            # and make ffprobe fetch them, which is an SSRF from the user's machine with their
            # network position. Media that arrives over the LAN (server/lan_receive.py) makes
            # that reachable by someone who is not the user, but the picker path wants the same
            # guard — probing a LOCAL file never legitimately needs another protocol.
            # This is not a sandbox: decoder bugs and CPU bombs still run in-process.
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "permission"
    if proc.returncode != 0:
        return None, "nonzero_exit"
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None, "parse"
    return _fields(data), None


def _fields(data: dict[str, Any]) -> dict[str, Any]:
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    fmt = data.get("format") or {}
    transfer = video.get("color_transfer") or "unknown"
    return {
        "container": _token((fmt.get("format_name") or "").split(",")[0]),
        "video_codec": _token(video.get("codec_name")),
        "audio_codec": _token(audio.get("codec_name")),
        "pix_fmt": _token(video.get("pix_fmt")),
        "color_transfer": _token(transfer),
        # The question RULES.md actually asks: is HDR a real USER problem, or only ours?
        "hdr": _hdr(transfer),
        "width": _int(video.get("width")),
        "height": _int(video.get("height")),
        "fps": _fps(video.get("avg_frame_rate")),
        "n_audio_streams": sum(1 for s in streams if s.get("codec_type") == "audio"),
        "has_alpha": bool(video.get("pix_fmt") and "a" in str(video.get("pix_fmt")).split("p")[-1]),
        "duration_s": _bucket(_float(fmt.get("duration")), (5, 15, 60, 300)),
        "bitrate_mbps": _bucket(_float(fmt.get("bit_rate"), 1e6), (2, 8, 25, 100)),
    }


def _hdr(transfer: str) -> str:
    t = (transfer or "").lower()
    if "arib-std-b67" in t or "hlg" in t:
        return "hlg"
    if "smpte2084" in t or "pq" in t:
        return "pq"
    if t in ("", "unknown"):
        return "unknown"
    return "sdr"


def _token(value: Any) -> Optional[str]:
    """ffprobe vocabularies only. A value that is not a bounded token would be dropped by the
    validator anyway, so collapse it here rather than let it look like data that got lost."""
    s = str(value or "").strip()
    return s if s and len(s) <= 64 and all(c.isalnum() or c in "_.-+" for c in s) else None


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any, divisor: float = 1.0) -> Optional[float]:
    try:
        return float(value) / divisor
    except (TypeError, ValueError):
        return None


def _fps(rate: Any) -> Optional[str]:
    try:
        num, den = str(rate).split("/")
        value = float(num) / float(den)
    except (ValueError, ZeroDivisionError, AttributeError):
        return None
    return _bucket(value, (25, 31, 50, 61))


def _bucket(value: Optional[float], edges: tuple[float, ...]) -> Optional[str]:
    """Same label shape as server/render_jobs._bucket, which is what the validator enforces."""
    if value is None:
        return None
    prev = "0"
    for edge in edges:
        if value < edge:
            return f"{prev}-{int(edge)}"
        prev = str(int(edge))
    return f"{prev}+"
