"""Editor write-layer for Mission Control (manual, human-driven editing).

OpenNolan is agent-first: orchestration lives in skills, not Python. The MANUAL editor
is a deliberate, documented exception — the *human* is the orchestrator. But the artifact
contract is preserved end to end: every write goes through `validate_artifact`, and a
schema failure raises `EditDecisionsInvalid` WITHOUT touching the file on disk.

    read  ── projects/<id>/artifacts/edit_decisions.json ──▶ UI (mutates in memory)
                                                               │
    write ◀── validate_artifact("edit_decisions", doc) ◀──────┘   (raises on invalid)
              │ (only if valid)
              ▼
          atomic_write_json(...)   (temp → fsync → os.replace; never a partial file)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from lib.atomic_io import atomic_write_json
from schemas.artifacts import validate_artifact


class EditDecisionsInvalid(ValueError):
    """A proposed edit_decisions doc failed schema validation. Carries the jsonschema message.

    Raised BEFORE any write, so the on-disk artifact is never corrupted.
    """


def _project_dir(projects_dir: Path | str, project_id: str) -> Path:
    return Path(projects_dir) / project_id


def edit_decisions_path(projects_dir: Path | str, project_id: str) -> Path:
    return _project_dir(projects_dir, project_id) / "artifacts" / "edit_decisions.json"


def read_edit_decisions(projects_dir: Path | str, project_id: str) -> Optional[dict[str, Any]]:
    """Return the project's edit_decisions content, or None if it has none yet.

    None is a normal state for a fresh manual project — the UI scaffolds a minimal
    valid doc rather than treating absence as an error.
    """
    p = edit_decisions_path(projects_dir, project_id)
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def write_edit_decisions(projects_dir: Path | str, project_id: str, doc: dict[str, Any]) -> None:
    """Validate `doc` against the edit_decisions schema, then atomically write it.

    Raises `EditDecisionsInvalid` (file left untouched) if validation fails. This is the
    single gate that keeps a human-edited timeline honest with the rest of the pipeline.
    """
    try:
        validate_artifact("edit_decisions", doc)
    except Exception as exc:  # jsonschema.ValidationError (+ any loader error)
        raise EditDecisionsInvalid(str(exc)) from exc
    atomic_write_json(edit_decisions_path(projects_dir, project_id), doc)


def resolve_source_path(
    projects_dir: Path | str, project_id: str, ref: str
) -> Optional[Path]:
    """Resolve a cut's `source` (or an overlay `asset_id`) to a real file ON DISK.

    Mirrors `video_compose`'s resolution so the editor's scrub preview reads the SAME
    bytes the renderer will: an `asset_manifest` id wins, otherwise `ref` is treated as a
    path. Cut sources are stored repo-root-relative (e.g. `projects/<id>/assets/video/x.mp4`),
    so we try that first, then project-relative, then projects-dir-relative.

    Returns None if nothing resolves to a file, OR if the resolved file escapes the allowed
    roots (path-traversal / absolute-path containment guard). Allowed roots are the project
    directory AND the repo's shared, checked-in asset libraries under `<repo>/assets/` (e.g.
    `assets/sfx/`, the greg kit). The renderer reads those from repo-root cwd, so the preview
    must serve them too or SFX/kit audio is silent in the editor but present in the export.
    Arbitrary out-of-project user files are still refused.
    """
    if not ref:
        return None
    proj = (Path(projects_dir) / project_id).resolve()
    # Curated, in-repo asset libraries the renderer uses (parent of projects_dir is the repo).
    shared_root = (Path(projects_dir).parent / "assets").resolve()
    manifest = read_asset_manifest(projects_dir, project_id)
    lookup = {
        a.get("id"): a
        for a in manifest.get("assets", [])
        if isinstance(a, dict) and a.get("id")
    }
    raw = ref
    entry = lookup.get(ref)
    if entry and entry.get("path"):
        raw = entry["path"]

    p = Path(raw)
    candidates: list[Path]
    if p.is_absolute():
        candidates = [p]
    else:
        # video_compose resolves relative cut sources from the process cwd (repo root,
        # the parent of the projects dir). Project-relative is the manual-editor fallback.
        candidates = [
            Path(projects_dir).parent / raw,  # repo-root-relative: "projects/<id>/assets/.."
            proj / raw,                        # project-relative:  "assets/video/x.mp4"
            Path(projects_dir) / raw,          # projects-dir-relative
        ]
    for c in candidates:
        try:
            rc = c.resolve()
        except OSError:
            continue
        if rc.is_file() and (rc == proj or proj in rc.parents or shared_root in rc.parents):
            return rc
    return None


def read_asset_manifest(projects_dir: Path | str, project_id: str) -> dict[str, Any]:
    """Load asset_manifest.json for render asset_id→path resolution.

    Returns `{"assets": []}` (not `{}`) when absent so `video_compose._render` doesn't
    bail on a missing manifest — cuts/overlays that reference literal file paths still
    resolve (an empty lookup leaves the path unchanged).
    """
    p = _project_dir(projects_dir, project_id) / "artifacts" / "asset_manifest.json"
    if not p.is_file():
        return {"assets": []}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) and data.get("assets") is not None else {"assets": []}
    except Exception:
        return {"assets": []}


def browser_preview_path(source_path: Path, cache_dir: Path) -> Optional[Path]:
    """Return a browser-decodable WebM/VP9 proxy for ProRes .mov files.

    Chromium (and Electron) cannot decode ProRes. HyperFrames overlay renders use
    ProRes 4444 with alpha (yuva444p12le), so the preview <video> elements load
    silently. This function detects that case and returns a VP9/WebM proxy that
    Chrome can play — with the alpha channel preserved so the overlay composites
    correctly over the main clip.

    The proxy is transcoded once and cached at `cache_dir/<stem>.webm`. Subsequent
    calls return the cached path instantly. Returns None if the source is already
    browser-compatible, if ffprobe/ffmpeg are unavailable, or if transcoding fails
    (caller falls back to serving the original file).
    """
    if source_path.suffix.lower() != ".mov":
        return None
    if shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None:
        return None

    # Detect ProRes codec (prores / prores_aw / prores_ap, etc.)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name",
         "-of", "default=noprint_wrappers=1:nokey=1",
         str(source_path)],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        return None
    codec = probe.stdout.strip().lower()
    if not codec.startswith("prores"):
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / (source_path.stem + ".webm")
    if out.is_file() and out.stat().st_size > 0:
        return out  # cache hit

    # Transcode: VP9 with alpha (yuva420p), fast preset for live preview use.
    # -deadline realtime -cpu-used 8 trades quality for speed (~2-4x realtime on M1).
    tmp = out.with_suffix(".tmp.webm")
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(source_path),
         "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
         "-b:v", "0", "-crf", "30",
         "-deadline", "realtime", "-cpu-used", "8",
         "-an", str(tmp)],
        capture_output=True,
    )
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return None

    tmp.rename(out)
    return out
