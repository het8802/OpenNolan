"""Project workspace creation and discovery.

A project lives at ``projects/<project_id>/`` and is identified by a
``project.json`` manifest written at creation time. The manifest is the
single source of truth for two things the Mission Control UI needs and that
the filesystem alone can't answer:

1. **Which directories are real projects.** ``projects/`` also accumulates
   scratch and analysis dirs (``_analysis``, ``demos``), stray files
   (``.DS_Store``), and legacy reel folders. Globbing ``projects/*`` would
   surface all of that. A directory is a project iff it contains a manifest.

2. **The pipeline_type before any checkpoint exists.** Checkpoint stage
   ordering (``get_next_stage``) needs ``pipeline_type``, but a freshly
   created, not-yet-run project has no checkpoint to read it from. The
   manifest carries it from creation.

    projects/<project_id>/
    ├── project.json          # the manifest (this module owns it)
    ├── artifacts/
    ├── assets/{images,video,audio,music}/
    └── renders/
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ContextManager, Optional

from lib.atomic_io import atomic_write_json

MANIFEST_NAME = "project.json"
ASSET_SUBDIRS = ("images", "video", "audio", "music")

# The canonical home for every kind of asset the agent produces, keyed by the
# kind it declares. This is the single source of truth for "where does X go" —
# the agent declares a kind, never a folder (see `place_asset`). Note that the
# UI reads three distinct buckets (server/app.py `list_assets`): source assets
# under assets/, the agent's building-block clips under hf/renders/ (the
# Assets -> Renders tab), and ONLY the final deliverable under renders/. Keeping
# intermediate `render`s out of renders/ is what stops them masquerading as the
# "Final render" surface.
KIND_DIRS: dict[str, str] = {
    "image": "assets/images",
    "video": "assets/video",
    "audio": "assets/audio",
    "music": "assets/music",
    "render": "hf/renders",  # intermediate per-scene clips (building blocks)
    "final_render": "renders",  # the one assembled deliverable
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class ProjectExistsError(Exception):
    """Raised when creating a project whose id already exists."""


def slugify(name: str) -> str:
    """Derive a kebab-case project id from a human title."""
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"Cannot derive a project id from name: {name!r}")
    return slug


def sanitize_filename(filename: str) -> str:
    """Return a safe basename for an uploaded file, or raise ValueError.

    Strips any directory components (so ``../../etc/passwd`` becomes
    ``passwd`` and ``/abs/x.png`` becomes ``x.png``) and rejects empty,
    dot, separator, or null-byte names. Callers should still join the
    result to the target dir and verify containment as defense in depth.
    """
    name = Path(filename).name
    if (
        not name
        or not name.strip()  # whitespace-only
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise ValueError(f"unsafe filename: {filename!r}")
    return name


def project_dir(projects_dir: Path | str, project_id: str) -> Path:
    return Path(projects_dir) / project_id


def manifest_path(projects_dir: Path | str, project_id: str) -> Path:
    return project_dir(projects_dir, project_id) / MANIFEST_NAME


def create_project(
    projects_dir: Path | str,
    name: str,
    pipeline_type: Optional[str] = None,
    *,
    style: Optional[str] = None,
    created_at: Optional[str] = None,
) -> dict[str, Any]:
    """Scaffold a project workspace and write its manifest.

    Returns the manifest dict. Raises ``ProjectExistsError`` if a manifest
    already exists for the derived id (so the API can return 409 instead of
    silently overwriting — the exact collision both behavioral spikes hit
    when they reused the same title).

    ``style`` pins the visual style playbook (a name resolvable by
    styles.playbook_loader.load_playbook) so the agent uses it instead of
    picking one; None = let the agent decide.

    ``created_at`` is injectable for deterministic tests; callers normally
    omit it and get a UTC ISO-8601 timestamp.
    """
    projects_dir = Path(projects_dir)
    project_id = slugify(name)
    pdir = project_dir(projects_dir, project_id)
    mpath = manifest_path(projects_dir, project_id)

    if mpath.exists():
        raise ProjectExistsError(f"Project {project_id!r} already exists at {pdir}")

    (pdir / "artifacts").mkdir(parents=True, exist_ok=True)
    for sub in ASSET_SUBDIRS:
        (pdir / "assets" / sub).mkdir(parents=True, exist_ok=True)
    (pdir / "renders").mkdir(parents=True, exist_ok=True)

    manifest = {
        "version": "1.0",
        "project_id": project_id,
        "name": name,
        "pipeline_type": pipeline_type,
        "style": style,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(mpath, manifest)
    return manifest


def read_project_manifest(projects_dir: Path | str, project_id: str) -> Optional[dict[str, Any]]:
    """Return a project's manifest, or None if it isn't a real project."""
    mpath = manifest_path(projects_dir, project_id)
    if not mpath.exists():
        return None
    with open(mpath) as f:
        return json.load(f)


def is_project(projects_dir: Path | str, project_id: str) -> bool:
    return manifest_path(projects_dir, project_id).exists()


def _infer_legacy_project(projects_dir: Path, project_id: str) -> Optional[dict[str, Any]]:
    """Synthesize a manifest for a real project dir that has no project.json
    (created before manifests, or by the agent directly). A dir counts as a
    project iff it has a top-level checkpoint, an artifacts/ dir with content,
    or a renders/ dir with content. Scratch dirs (_analysis, demos) and stray
    files are excluded by this test."""
    pdir = projects_dir / project_id
    checkpoints = sorted(pdir.glob("checkpoint_*.json"))
    has_artifacts = (pdir / "artifacts").is_dir() and any((pdir / "artifacts").iterdir())
    has_renders = (pdir / "renders").is_dir() and any((pdir / "renders").iterdir())
    if not checkpoints and not has_artifacts and not has_renders:
        return None

    pipeline_type = None
    if checkpoints:
        try:
            data = json.loads(checkpoints[-1].read_text())
            pt = data.get("pipeline_type")
            pipeline_type = pt if pt and pt != "unknown" else None
        except Exception:
            pass
    try:
        created_at = datetime.fromtimestamp(pdir.stat().st_mtime, timezone.utc).isoformat()
    except Exception:
        created_at = ""
    return {
        "version": "1.0",
        "project_id": project_id,
        "name": project_id,
        "pipeline_type": pipeline_type,
        "created_at": created_at,
        "legacy": True,
    }


def get_project_record(projects_dir: Path | str, project_id: str) -> Optional[dict[str, Any]]:
    """The canonical 'is this a real project, and what is it' resolver.

    Returns the project.json manifest if present, else a synthesized manifest
    for a legacy/agent-created dir (one with checkpoints, artifacts, or renders),
    else None. Use this — not read_project_manifest — to decide whether a
    project exists, so legacy dirs are first-class everywhere (state, chat,
    assets, threads), exactly as they appear in the project list.
    """
    rec = read_project_manifest(projects_dir, project_id)
    if rec is None:
        rec = _infer_legacy_project(Path(projects_dir), project_id)
    return rec


def list_projects(projects_dir: Path | str) -> list[dict[str, Any]]:
    """Return every real project, newest first.

    A project is any dir with a project.json manifest OR (for legacy/agent-created
    dirs) one that has checkpoints, artifacts, or renders. Scratch/analysis dirs
    and stray files are excluded.
    """
    projects_dir = Path(projects_dir)
    if not projects_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for child in sorted(projects_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest = get_project_record(projects_dir, child.name)
        if manifest is not None:
            out.append(manifest)
    out.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return out


def get_project_pipeline_type(projects_dir: Path | str, project_id: str) -> Optional[str]:
    """Resolve a project's pipeline_type.

    Prefers the manifest (works for an empty project with no checkpoints).
    Falls back to the latest checkpoint's ``pipeline_type`` for legacy
    projects created before manifests existed.
    """
    manifest = read_project_manifest(projects_dir, project_id)
    if manifest and manifest.get("pipeline_type"):
        return manifest["pipeline_type"]

    try:
        from lib.checkpoint import get_latest_checkpoint

        cp = get_latest_checkpoint(Path(projects_dir), project_id)
        if cp:
            pt = cp.get("pipeline_type")
            return pt if pt and pt != "unknown" else None
    except Exception:
        pass
    return None


# --- asset placement ------------------------------------------------------
#
# The agent must never hand-pick a destination folder — that's what let
# intermediate clips land in renders/ and show up as "Final render". Instead it
# declares a KIND and hands the file to `place_asset`, the single writer into a
# project's asset tree. Exposed to the agent as the `store_asset` SDK tool
# (server/agent_runner.py).


def asset_dir(projects_dir: Path | str, project_id: str, kind: str) -> Path:
    """The canonical directory for a given asset `kind`. Raises ValueError on
    an unknown kind (so a typo fails loudly instead of writing somewhere odd)."""
    try:
        sub = KIND_DIRS[kind]
    except KeyError:
        raise ValueError(f"unknown asset kind {kind!r}; expected one of {sorted(KIND_DIRS)}")
    return project_dir(projects_dir, project_id) / sub


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def place_asset(
    projects_dir: Path | str,
    project_id: str,
    kind: str,
    src: Path | str,
    name: Optional[str] = None,
    *,
    move: bool = False,
) -> dict[str, Any]:
    """Move/copy a produced file into its canonical location for ``kind`` and
    report where it landed.

    This is the single writer into a project's asset tree: the caller declares a
    ``kind`` and the destination folder is derived (via ``KIND_DIRS``), never
    passed in. ``src`` is the file the caller just produced (a scratch/temp path
    or wherever a generator defaulted). Set ``move=True`` to relocate it rather
    than copy.

    Idempotent by content: re-placing identical bytes under a taken name reuses
    the existing file (``deduped=True``); a name collision with DIFFERENT bytes
    gets a short content-hash suffix so nothing is ever clobbered.

    ``final_render`` is the ONE exception and is delegated to
    `publish_final_render`: never-clobber is right for source assets and wrong for
    the single deliverable, where it left the stale previous cut sitting at
    ``renders/final.mp4`` while a ``final.<hash>.mp4`` held the new one (OPN-30).
    That kind therefore REPLACES, ignores ``name``, and takes the project lock.

    Returns ``{"path", "abs_path", "kind", "deduped"}`` where ``path`` is
    relative to the project dir (e.g. ``assets/images/foo.png``); join it to
    ``projects/<id>/`` for a repo-relative path usable in edit_decisions.
    """
    if kind == "final_render":
        return publish_final_render(projects_dir, project_id, src, move=move)

    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"source file not found: {src}")

    dest_dir = asset_dir(projects_dir, project_id, kind)
    dest_dir.mkdir(parents=True, exist_ok=True)

    src_hash = _sha256(src)
    safe = sanitize_filename(name or src.name)
    target = dest_dir / safe
    deduped = False

    if target.is_file() and _sha256(target) == src_hash:
        deduped = True  # same name, same bytes — reuse, don't recopy
    elif target.exists():
        # Name taken by different content — disambiguate by content hash so we
        # neither clobber the existing file nor duplicate identical bytes.
        stem, suffix = Path(safe).stem, Path(safe).suffix
        target = dest_dir / f"{stem}.{src_hash[:8]}{suffix}"
        deduped = target.is_file() and _sha256(target) == src_hash

    if not deduped and target.resolve() != src.resolve():
        if move:
            shutil.move(str(src), str(target))
        else:
            shutil.copy2(str(src), str(target))
    elif deduped and move and target.resolve() != src.resolve():
        # move=True means the caller is DONE with src (a scratch/temp staging
        # file). On the dedup path its bytes already live in the project, so
        # consume src anyway — otherwise re-stored files strand their temp
        # originals, the exact litter move semantics exist to prevent.
        src.unlink()

    rel = target.relative_to(project_dir(projects_dir, project_id))
    return {"path": str(rel), "abs_path": str(target), "kind": kind, "deduped": deduped}


# --- publishing the deliverable (OPN-30) ----------------------------------
#
# `place_asset` above never clobbers, which is right for source assets and wrong
# for the ONE assembled deliverable: a re-render left `renders/final.mp4` holding
# the previous cut while the editor timeline showed the new one. Everything that
# produces the deliverable now funnels through `publish_final_render`, which
#
#   1. forces the name             — one findable file, not final.<hash>.mp4
#   2. holds a per-project lock    — renders share scratch dirs under renders/
#   3. writes a RECEIPT last       — the commit marker binding the video to the
#                                    document that produced it
#
# `final_render_status` is the one verifier (the assets listing and the QA gate
# both call it), so nothing can disagree about whether the deliverable is current.
# See docs/plans/opn-30-edit-decisions-render-desync/claude/architecture.md.

FINAL_RENDER_NAME = "final.mp4"
FINAL_RECEIPT_NAME = ".final_receipt.json"
# The deliverable's PROJECT-RELATIVE path, as the UI and every tool result spell it.
FINAL_RENDER_REL = f"{KIND_DIRS['final_render']}/{FINAL_RENDER_NAME}"

_project_locks: dict[tuple[str, str], threading.RLock] = {}
_project_locks_guard = threading.Lock()


def project_lock(projects_dir: Path | str, project_id: str) -> threading.RLock:
    """The per-project render/publish lock. Same project -> same lock object.

    Keyed by the RESOLVED projects_dir as well as the id, so two checkouts that
    happen to share a project id don't serialize against each other. Creation is
    guarded so two first-callers can't mint two locks for one project.

    Re-entrant on purpose: a render thread holds this for the whole render and
    then calls `publish_final_render`, which takes it again.
    """
    key = (str(Path(projects_dir).resolve()), project_id)
    with _project_locks_guard:
        lock = _project_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _project_locks[key] = lock
        return lock


def canonical_doc_hash(doc: Any) -> str:
    """The ONE hash of an edit_decisions doc. Three canonicalisations would make
    `current` meaningless, so publisher, listing and QA gate all call this."""
    blob = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class RendersDirEscapes(ValueError):
    """The project's renders/ dir resolves outside the project (a symlink)."""


def renders_dir(projects_dir: Path | str, project_id: str) -> Path:
    """The project's renders/ dir, RESOLVED, refusing one that leaves the project.

    Every containment check downstream compares resolved paths, so a symlinked renders/
    would defeat all of them at once: the resolved candidate fails a
    "is it under renders/" test while the LEXICAL fallback follows the same symlink, and the
    canonical write lands wherever the link points — outside the project entirely. There is
    no safe target in that case, so refuse rather than write somewhere unexpected.
    """
    proj = project_dir(projects_dir, project_id).resolve()
    real = (proj / KIND_DIRS["final_render"]).resolve()
    if real.parent != proj:
        raise RendersDirEscapes(
            f"{project_id}: {KIND_DIRS['final_render']}/ resolves to {real}, outside the "
            "project — refusing to write the deliverable through it"
        )
    return real


def final_render_path(projects_dir: Path | str, project_id: str) -> Path:
    return renders_dir(projects_dir, project_id) / FINAL_RENDER_NAME


def final_receipt_path(projects_dir: Path | str, project_id: str) -> Path:
    return renders_dir(projects_dir, project_id) / FINAL_RECEIPT_NAME


def _edit_decisions_path(projects_dir: Path | str, project_id: str) -> Path:
    # Built here rather than imported from server.editor: lib must not depend on server.
    return project_dir(projects_dir, project_id) / "artifacts" / "edit_decisions.json"


def final_render_status(projects_dir: Path | str, project_id: str) -> dict[str, Any]:
    """Is ``renders/final.mp4`` the video of the live timeline? -> {current, reason}.

    TWO checks, not one:

        current == a receipt exists
              AND receipt.doc_hash       == canonical_doc_hash(live edit_decisions)
              AND receipt.video_size     == stat(final.mp4).st_size
              AND receipt.video_mtime_ns == stat(final.mp4).st_mtime_ns

    The document half alone would call new bytes current during the window between
    the video replace and the receipt write (the OLD receipt still hashes to the
    still-old live doc). The identity half also catches an outside writer replacing
    final.mp4 behind the publisher's back.

    ponytail: size + mtime_ns is a practical identity token, not proof of bytes — a
    metadata-preserving `cp -p` of same-size content would still read current. That's
    the right trade while the UI polls this every 4s; add a `video_sha256` to the
    receipt if a real out-of-band writer ever shows up.
    """
    try:
        video = final_render_path(projects_dir, project_id)
        receipt_file = final_receipt_path(projects_dir, project_id)
    except RendersDirEscapes as exc:
        # Reported, not raised: this is called from the assets listing the UI polls.
        return {"current": False, "reason": str(exc)}
    if not video.is_file():
        return {"current": False, "reason": f"no renders/{FINAL_RENDER_NAME} yet"}
    if not receipt_file.is_file():
        return {
            "current": False,
            "reason": f"no render receipt — renders/{FINAL_RENDER_NAME} was not "
            "published by a render, so nothing ties it to a timeline",
        }
    try:
        receipt = json.loads(receipt_file.read_text())
        stat = video.stat()
    except (OSError, ValueError) as exc:
        return {"current": False, "reason": f"unreadable receipt or render: {exc}"}
    if receipt.get("video_size") != stat.st_size or receipt.get("video_mtime_ns") != stat.st_mtime_ns:
        return {
            "current": False,
            "reason": f"renders/{FINAL_RENDER_NAME} was replaced after its receipt "
            "was written — re-render to publish it properly",
        }

    doc_file = _edit_decisions_path(projects_dir, project_id)
    if not doc_file.is_file():
        return {"current": False, "reason": "no artifacts/edit_decisions.json to compare against"}
    try:
        doc = json.loads(doc_file.read_text())
    except (OSError, ValueError) as exc:
        return {"current": False, "reason": f"unreadable edit_decisions.json: {exc}"}
    if receipt.get("doc_hash") != canonical_doc_hash(doc):
        return {"current": False, "reason": "the timeline changed since this render — re-render"}
    return {"current": True, "reason": f"renders/{FINAL_RENDER_NAME} matches the live edit_decisions.json"}


def publish_final_render(
    projects_dir: Path | str,
    project_id: str,
    src: Path | str,
    *,
    receipt_doc: Optional[dict[str, Any]] = None,
    persist_doc: Optional[dict[str, Any]] = None,
    move: bool = False,
    commit_guard: Optional[Callable[[], ContextManager[bool]]] = None,
) -> dict[str, Any]:
    """Publish ``src`` as the project's one deliverable, ``renders/final.mp4``.

    Takes ``project_lock`` (re-entrant, so the render thread that already holds it
    calls straight through) and delegates to `_publish_final_locked`.
    """
    with project_lock(projects_dir, project_id):
        return _publish_final_locked(
            projects_dir,
            project_id,
            src,
            receipt_doc=receipt_doc,
            persist_doc=persist_doc,
            move=move,
            commit_guard=commit_guard,
        )


def _publish_final_locked(
    projects_dir: Path | str,
    project_id: str,
    src: Path | str,
    *,
    receipt_doc: Optional[dict[str, Any]] = None,
    persist_doc: Optional[dict[str, Any]] = None,
    move: bool = False,
    commit_guard: Optional[Callable[[], ContextManager[bool]]] = None,
) -> dict[str, Any]:
    """`publish_final_render` for a caller that ALREADY holds ``project_lock``.

    Order is the design:

      1. stage ``src`` into ``renders/.final.<uuid>.part.mp4`` — ``shutil.move`` when
         ``move`` (a temp root can be another filesystem, where ``os.replace`` raises
         EXDEV), else ``copy2``, preserving `place_asset`'s "only temp sources are
         consumed" contract.
      2. enter ``commit_guard`` — a render caller re-checks supersede here, holding its
         own job lock across the check AND the replace so nothing can slip between them.
      3. inside that guard: UNLINK the old receipt, then ``os.replace`` part ->
         ``final.mp4`` (atomic, same directory). The unlink comes first so no failure can
         leave the previous receipt describing the new bytes — `copy2` preserves the
         source's mtime, so a same-size replacement could otherwise satisfy both halves of
         `final_render_status` and read as current. It is inside the guard because a
         REFUSED publish must leave the old video AND its receipt untouched.
      4. ``persist_doc``, when given -> ``artifacts/edit_decisions.json``.
      5. the RECEIPT, last, as the commit marker: ``receipt_doc`` given -> write
         ``{doc_hash, video_size, video_mtime_ns}``; ``receipt_doc`` None -> nothing, so an
         unreceipted publish (`store_asset`) cannot inherit provenance it has not earned.

    Any interruption between steps 3 and 5 leaves NO receipt, so the result reads STALE
    rather than falsely current. The honest limit: once step 3 lands, the previous good
    final.mp4 is gone — what survives is the ability to tell.

    Returns ``{"path", "abs_path", "kind", "deduped", "published"}`` (+ ``"reason"``
    when ``published`` is False, i.e. the commit guard refused).
    """
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"source file not found: {src}")

    renders = renders_dir(projects_dir, project_id)
    renders.mkdir(parents=True, exist_ok=True)
    final = renders / FINAL_RENDER_NAME
    part = renders / f".final.{uuid.uuid4().hex[:8]}.part.mp4"
    receipt_file = renders / FINAL_RECEIPT_NAME
    # The public path is the CONSTANT, never derived from the resolved one: relative_to
    # would raise for a relative projects_dir (and for a symlinked project dir), and an
    # in-project renders symlink would report its physical name instead of the
    # renders/final.mp4 every caller and the UI are promised.
    out = {
        "path": FINAL_RENDER_REL,
        "abs_path": str(final),
        "kind": "final_render",
        "deduped": False,
        "published": True,
    }

    # Publishing the deliverable ONTO ITSELF must never move it out of the way: a
    # commit_guard refusal would then leave the project with no deliverable at all,
    # because the staging file is cleaned up on the way out.
    if move and src.resolve() == final.resolve():
        move = False

    try:
        if move:
            shutil.move(str(src), str(part))
        else:
            shutil.copy2(str(src), str(part))
        guard = commit_guard() if commit_guard is not None else contextlib.nullcontext(True)
        with guard as may_commit:
            if not may_commit:
                return {**out, "published": False, "reason": "superseded by a newer render before publishing"}
            receipt_file.unlink(missing_ok=True)  # no receipt may outlive the video it describes
            os.replace(part, final)
    finally:
        # Covers the guard refusal, a staging failure, and a crash mid-publish. After a
        # successful os.replace there is nothing left to remove.
        part.unlink(missing_ok=True)

    if persist_doc is not None:
        atomic_write_json(_edit_decisions_path(projects_dir, project_id), persist_doc)

    if receipt_doc is not None:
        stat = final.stat()
        atomic_write_json(
            receipt_file,
            {
                "doc_hash": canonical_doc_hash(receipt_doc),
                "video_size": stat.st_size,
                "video_mtime_ns": stat.st_mtime_ns,
            },
        )
    return out
