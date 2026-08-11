"""FastAPI read layer for the Mission Control UI (v1: read-only).

Wraps the existing libs; owns no orchestration. Endpoints:

  GET /api/health                  -> liveness + which projects dir is in use
  GET /api/pipelines               -> available pipelines + their stage order
  GET /api/pipelines/{name}        -> full pipeline manifest
  GET /api/projects                -> manifests of real projects (manifest-filtered)
  GET /api/projects/{id}/state     -> per-stage checkpoint status (via StateSource)
  GET /api/capabilities            -> provider/tool menu (discovered once, cached)

Write paths (create project, upload assets, gate approval) and the agent
runner are deliberately NOT here yet — this is the read slice.
"""

from __future__ import annotations

import asyncio
import json
import ntpath
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}
# Browser-only: readable companions to the media (subtitles, notes). Deliberately no .json —
# stage artifacts are JSON and the Pipeline tab already surfaces them per stage.
TEXT_EXTS = {".srt", ".vtt", ".txt", ".md"}


# Project-relative folders the asset browser hides: engine internals (the content-keyed
# proxy cache) and the stage artifacts, which are JSON — never media — and already surfaced
# per stage by the Pipeline tab. (Dot-entries — `.mc/` agent chat history,
# `.final_review_frames/` — are hidden by the leading-dot rule instead.)
HIDDEN_BROWSE_DIRS = {"artifacts", "renders/proxies"}


def _classify(rel_parts: tuple[str, ...], ext: str) -> Optional[str]:
    ext = ext.lower()
    if ext in IMAGE_EXTS:
        return "images"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "music" if "music" in rel_parts else "audio"
    return None


# ── @-mention resolution (OPN-27) ────────────────────────────────────────────────────
# The chat composer sends the assets a user picked as a structured `mentions[]` sidecar; we
# turn each project-relative path into a VERIFIED ABSOLUTE one for the agent. It cannot do
# this itself: its cwd is the read-only code root (server/agent_runner.py) and the
# "use absolute paths" instruction only rides the FIRST-turn preamble.
#
# Lives here, not in server/editor.py, for two reasons: the eligibility rules it mirrors are
# the ones `list_assets` applies just below, and app.py already imports server.editor — the
# other direction would be a cycle. Deliberately NOT `editor.resolve_source_path`, whose
# candidate order is repo-root-FIRST and whose containment also accepts the shared repo
# asset library, so `assets/sfx/x.wav` would silently resolve to the REPO file.

# Root -> the extensions that root's bucket actually lists. PER ROOT, never a union:
# renders/ and hf/renders/ list only video, so a union would let a tampered
# `renders/evil.png` pass SHAPE and get reported as a harmless "not found" instead of a 422.
_MENTION_ROOTS: tuple[tuple[str, bool, frozenset[str]], ...] = (
    # (prefix, recursive, allowed extensions)
    ("assets", True, frozenset(IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS)),
    ("hf/renders", True, frozenset(VIDEO_EXTS)),
    ("renders", False, frozenset(VIDEO_EXTS)),  # DIRECT children only
)


def mention_shape_error(rel: Any) -> Optional[str]:
    """Why `rel` could never have come from the mention menu, or None if it could.

    SHAPE is decidable from the string alone — no filesystem touch. A violation means a
    client bug or a tampered request, so the caller answers 422 and never starts a turn.
    Anything that is merely *absent* is a STATE problem and degrades instead (see
    `resolve_mentions`).
    """
    if not isinstance(rel, str) or not rel.strip():
        return "must be a non-empty string"

    # ⚠ MUST BE EXPRESSIBLE AS A FILESYSTEM PATH AT ALL, and this has to be decided HERE.
    # A string the OS cannot encode raises ValueError (not OSError) from the eventual stat,
    # which `resolve_mentions` deliberately does not catch — so without this check the
    # endpoint answers 500 instead of the contract's 422. Two distinct causes, both
    # unreachable from the menu and therefore both tampering:
    #   · a NUL code point — os.fsencode accepts it, the syscall rejects it;
    #   · a LONE SURROGATE outside the surrogateescape window (U+D800-U+DC7F, U+DD00-U+DFFF)
    #     — os.fsencode itself rejects it. U+DC80-U+DCFF are legal: they round-trip to raw
    #     bytes 0x80-0xFF, which is how Python represents undecodable filenames.
    # Other control characters (\x01, \n, ...) are LEGAL in POSIX filenames and are left to
    # degrade as ordinary STATE misses — rejecting them here would be over-reach.
    if "\0" in rel:
        return "must not contain a NUL character"
    try:
        os.fsencode(rel)
    except (UnicodeEncodeError, ValueError):
        return "must be encodable as a filesystem path"

    if rel.startswith("/") or ntpath.isabs(rel) or PurePosixPath(rel).is_absolute():
        return "must be project-relative, not absolute"

    # ⚠ CHECK THE RAW SEGMENTS, BEFORE PurePosixPath. PurePosixPath NORMALIZES a literal
    # "." segment and a doubled slash away — PurePosixPath("assets/./video/x.mp4").parts is
    # ("assets", "video", "x.mp4") — so testing its .parts silently blesses a path the menu
    # could never produce, and the turn would run. The dot-segment rule has to be enforced
    # on the string the client actually sent.
    raw_segments = rel.split("/")
    if any(seg == ".." for seg in raw_segments):
        return "must not contain a '..' segment"
    if any(seg == "" for seg in raw_segments):
        return "must not contain an empty path segment"
    # Any dot-prefixed segment, at any depth — this also catches a bare ".". The listing
    # endpoint only filters a dot LEAF, so the composer drops these client-side too; this is
    # the server half of that pair.
    if any(seg.startswith(".") for seg in raw_segments):
        return "must not contain a hidden (dot-prefixed) path segment"

    # Only now is normalization a no-op, so .parts is safe to reason about.
    p = PurePosixPath(rel)
    parts = p.parts
    ext = p.suffix.lower()
    for prefix, recursive, allowed in _MENTION_ROOTS:
        pre = PurePosixPath(prefix).parts
        if parts[: len(pre)] != pre:
            continue
        rest = parts[len(pre) :]
        if not rest:
            return f"{prefix}/ needs a file name"
        if not recursive and len(rest) != 1:
            return f"must be a direct child of {prefix}/"
        if ext not in allowed:
            return f"{prefix}/ only lists {', '.join(sorted(allowed))}"
        return None
    roots = ", ".join(f"{r[0]}/" for r in _MENTION_ROOTS)
    return f"must start with one of: {roots}"


def resolve_mentions(project_dir: Path, mentions: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """(resolved, shape_errors) for a request's mention sidecar.

    `shape_errors` non-empty ⇒ the caller MUST 422 without calling the runner.

    Otherwise every mention is returned in first-appearance order, de-duplicated, each with
    an `abs` path or None. None means a STATE failure — the file vanished, is not a regular
    file, or resolves (through a symlink) outside the project. Those are races we cause
    ourselves: the agent rewrites `hf/renders/*` during its own turns, so a valid pick can
    go stale mid-sentence. Refusing the turn there would cost the user their message, so it
    degrades to "not found" and the turn proceeds.
    """
    errors: list[str] = []
    ordered: list[str] = []
    seen: set[str] = set()
    for m in mentions or []:
        rel = m.path if isinstance(m, MentionRef) else (m or {}).get("path")
        err = mention_shape_error(rel)
        if err:
            errors.append(f"{rel!r}: {err}")
            continue
        if rel not in seen:
            seen.add(rel)
            ordered.append(rel)
    if errors:
        return [], errors

    root = project_dir.resolve()
    resolved: list[dict[str, Any]] = []
    for rel in ordered:
        abs_path: Optional[str] = None
        try:
            # resolve() FOLLOWS symlinks, and list_assets' is_file() check does too — so a
            # symlink inside assets/ pointing out of the project is menu-reachable. Re-check
            # containment on the REAL path and never emit one that escaped.
            real = (root / rel).resolve()
            if real.is_file() and (real == root or root in real.parents):
                abs_path = str(real)
        except OSError:
            abs_path = None
        resolved.append({"path": rel, "abs": abs_path})
    return resolved, []


def message_with_mentions(message: str, resolved: list[dict[str, Any]]) -> str:
    """The prompt the runner receives: the user's prose, then a resolution block.

    Returns the SAME string object when there is nothing to add — every turn without a
    mention (which is every turn today) must be byte-for-byte what it is now.
    """
    if not resolved:
        return message
    lines = ["[MENTIONED PROJECT ASSETS — resolved by the server, do not re-derive:"]
    for r in resolved:
        lines.append(
            f" - {r['path']}\n   {r['abs']}"
            if r["abs"]
            else f" - {r['path']}\n   NOT FOUND in this project — ask the user which file they meant"
        )
    return f"{message}\n\n" + "\n".join(lines) + "]"


def _browse_hidden(rel: Path) -> bool:
    return rel.name.startswith(".") or rel.as_posix() in HIDDEN_BROWSE_DIRS


def _browse_kind(rel: Path, ext: str) -> Optional[str]:
    """What the asset browser calls a file: the four asset kinds, plus `text` for the
    readable companions the kinds map has no bucket for (subtitles, notes)."""
    return _classify(rel.parts, ext) or ("text" if ext.lower() in TEXT_EXTS else None)


def _browse_count(d: Path, proj: Path) -> int:
    """How many entries the browser would show one level inside `d` (no recursion), so a
    folder row can say "12" or "empty" without a second request."""
    try:
        return sum(
            1
            for c in d.iterdir()
            if not _browse_hidden(c.relative_to(proj)) and (c.is_dir() or _browse_kind(c.relative_to(proj), c.suffix))
        )
    except OSError:
        return 0


from lib.env_loader import load_env
from lib.pipeline_loader import get_stage_order, list_pipelines, load_pipeline
from lib.project import (
    ASSET_SUBDIRS,
    FINAL_RENDER_NAME,
    ProjectExistsError,
    create_project,
    final_render_status,
    get_project_record,
    list_projects,
    sanitize_filename,
)
from styles.playbook_loader import builtin_playbooks, list_playbooks, load_playbook
from server import activity as activity_mod
from server import analytics as analytics_mod
from server import artifacts as artifacts_mod
from server import content_calendar as content_calendar_mod
from server import settings as settings_mod
from server import auth as auth_mod
from server import debug_log as debug_log_mod
from server import editor as editor_mod
from server import lan_receive
from server import lifecycle as lifecycle_mod
from server import outbox as outbox_mod
from server import render_jobs as render_jobs_mod
from server import threads as thread_store
from server.agent_runner import AgentRunner, auth_configured
from server.render_jobs import RenderJobStore
from server.state import FileStateSource, StateSource


class CreateProjectRequest(BaseModel):
    name: str
    pipeline_type: Optional[str] = None  # None/empty -> the agent picks one
    style: Optional[str] = None  # None/empty -> the agent picks a style


class ScheduleProjectRequest(BaseModel):
    scheduled_at: str
    channels: list[str]


class MentionRef(BaseModel):
    """One asset the user picked from the composer's `@` menu.

    `path` is project-relative and authoritative — the prose is never re-parsed, so a file
    name may contain anything (spaces, brackets) without breaking the reference. `token` is
    only what the user sees in the draft; the server ignores it.
    """

    path: str
    token: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None
    model: Optional[str] = None  # UI-selected agent model (validated against AGENT_MODELS)
    # Optional with a default: an older client that sends no sidecar is unaffected.
    mentions: list[MentionRef] = []


class ThreadCreate(BaseModel):
    title: Optional[str] = None


class ThreadSave(BaseModel):
    messages: list[Any] = []
    session_id: Optional[str] = None
    title: Optional[str] = None


class ConfirmRequest(BaseModel):
    confirm_id: str
    approved: bool


class AnswerRequest(BaseModel):
    question_id: str
    answer: str


class ProvideKeyRequest(BaseModel):
    # Answer to an agent `request_api_key` prompt. `skipped=True` declines (no write); otherwise
    # `value` is saved to the BYOK .env under `env_var` before the agent's tool is unblocked.
    key_request_id: str
    env_var: str
    value: Optional[str] = None
    skipped: bool = False


class ProvideCapabilityRequest(BaseModel):
    # Answer to an agent `request_capability` prompt. The UI streams the install itself; this only
    # unblocks the waiting tool. installed=true → agent retries; false → declined/failed, agent skips.
    cap_request_id: str
    installed: bool = False


class EnvUpdateRequest(BaseModel):
    # BYOK: {VARIABLE_NAME: value} edits to persist to the local .env (empty value = leave blank).
    vars: dict[str, str]


class AnalyticsUpdateRequest(BaseModel):
    # True = opt OUT of product analytics.
    disabled: bool


class FeedbackRequest(BaseModel):
    kind: str  # "bug" | "feature" | "other"
    message: str
    email: Optional[str] = None  # optional, so we can reply
    diagnostics: Optional[str] = None  # optional client-attached logs/context
    debug_session: Optional[str] = None  # attach a recorded UI session's analysis (editor debug report)


class ClientErrorReport(BaseModel):
    # A JS/renderer error posted by web/src/main.jsx (window.onerror / ErrorBoundary) or by the
    # Electron shell. Forwarded to PostHog Error Tracking; body is redacted server-side.
    source: str  # e.g. "renderer", "unhandledrejection", "react-boundary"
    message: str
    stack: Optional[str] = None
    context: Optional[dict[str, Any]] = None


# A runaway renderer must not be able to fan one POST into unbounded ingestion.
_TELEMETRY_BATCH_MAX = 100


class TelemetryBatch(BaseModel):
    # Product events from the renderer (web/src/analytics/track.js). Each entry is
    # {event, properties}; the taxonomy gate in analytics.validate_event decides what survives.
    events: list[dict[str, Any]] = []


class DebugLogBody(BaseModel):
    # A batch from the editor's UI session recorder (web/src/debug/recorder.js).
    session: str
    events: list[dict[str, Any]] = []


class OAuthFinishRequest(BaseModel):
    # The `code#state` string the user copies from the Claude sign-in page.
    code: str


class ApiKeyRequest(BaseModel):
    # An Anthropic API key (fallback to "Sign in with Claude"). Verified live before it is saved.
    api_key: str


from lib import app_paths

# Read-only code root the agent runs against (repo checkout in dev; app-bundle Resources in prod).
REPO_ROOT = app_paths.code_root()


def _default_projects_dir() -> Path:
    # Writable projects tree — repo/projects in dev, App-Support/projects in the packaged app.
    return app_paths.projects_dir()


def _default_capabilities() -> dict[str, Any]:
    """Discover tools and return the human-ready provider menu.

    Called at most once per app (the result is cached on app.state), so the
    expensive registry.discover() (it imports every tool module) never runs
    per request. Returns counts + install instructions only — never raw key
    or env values.
    """
    from tools.tool_registry import registry

    registry.discover()
    return registry.provider_menu_summary()


# ── schema-alarm classifiers ─────────────────────────────────────────────────
# A jsonschema message quotes the OFFENDING VALUE, which in this document is a source path, a
# caption or a project name. Only the SHAPE of the failure travels.

_DOC_OBJECTS = (("cuts", "cut"), ("overlays", "overlay"), ("audio", "audio"))
_KNOWN_FIELDS = frozenset(
    "source in_seconds out_seconds speed transform position scale crop keyframes transition "
    "track opacity start_seconds end_seconds volume gain_db canvas width height renderer_family "
    "render_runtime asset_id text duration".split()
)


def _rejected_object_kind(detail: str) -> str:
    d = detail.lower()
    for marker, kind in _DOC_OBJECTS:
        if marker in d:
            return kind
    return "document"


def _rejected_field(detail: str) -> str:
    """The first DECLARED field name mentioned. An undeclared token is not echoed back: it
    would be whatever the user typed."""
    import re as _re

    for token in _re.findall(r"[a-z_]{3,40}", detail.lower()):
        if token in _KNOWN_FIELDS:
            return token
    return "unknown"


# ── asset ingest telemetry ───────────────────────────────────────────────────
# The filename NEVER ships. RULES.md: a user can drop any media on us, so a name is customer
# and campaign text — `extension` is a closed enum and `asset_id` is an opaque persisted uuid4.

_ASSET_BYTES = (1e6, 1e7, 1e8, 1e9)
_KNOWN_EXTENSIONS = frozenset(
    ".mp4 .mov .m4v .webm .mkv .avi .mpg .mpeg .png .jpg .jpeg .gif .webp .heic .tif .tiff "
    ".mp3 .wav .m4a .aac .flac .ogg .opus .aiff .srt .vtt .txt .md".split()
)


def _extension(name: str) -> str:
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    # A closed enum, so an unknown extension collapses rather than becoming a free-text field.
    return ext if ext in _KNOWN_EXTENSIONS else "other"


def _asset_failed(pdir: Path, project_id: str, kind: str, failure_class: str, source: str = "picker") -> None:
    try:
        analytics_mod.capture(
            "asset_import_failed",
            {
                "kind": kind if kind in ASSET_SUBDIRS else "other",
                "source": source,
                "failure_class": failure_class,
                "project_id": analytics_mod.project_key(pdir, project_id),
            },
        )
    except Exception:
        pass


# Buckets for the receive-window rollup. Coarse on purpose: "did anyone leave it open long
# enough to walk to their phone" is the question, not a duration measurement.
_RECEIVE_SECONDS = (15, 60, 300, 900)


def _capture_receive_closed(pdir: Path, project_id: str, rollup: dict[str, Any]) -> None:
    """One `phone_receive_finished` per window. Called from lan_receive.stop(), which is the
    single teardown path — Done, the watchdog, a replace and a status() reap all reach it."""
    try:
        analytics_mod.capture(
            "phone_receive_finished",
            {
                "outcome": rollup["outcome"],
                "files": rollup["files"],
                "bytes": render_jobs_mod._bucket(rollup["bytes"], _ASSET_BYTES),
                "seconds_open": render_jobs_mod._bucket(rollup["seconds_open"], _RECEIVE_SECONDS),
                "by_kind": rollup["by_kind"],
                "project_id": analytics_mod.project_key(pdir, project_id),
            },
        )
    except Exception:
        pass


# ── browser-origin guard for the receive routes ──────────────────────────────────────
# Loopback is not an authorization boundary. A page the user merely VISITS can fire a
# cross-origin POST at 127.0.0.1 — the browser hides the response but sends the request —
# and a page that DNS-rebinds its own name to 127.0.0.1 can read responses too. For most
# routes here that is a pre-existing read; for /receive it would open a listening socket on
# the user's wifi with no user action (reproduced against this app before this guard), and
# the GET hands back the upload token. Hence: Host must be loopback (an attacker cannot
# forge it — it is their own hostname) and any Origin must be loopback too.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def _loopback_host(value: Optional[str]) -> bool:
    if not value:
        return False
    # urlsplit needs a scheme to populate .hostname, and Host arrives bare ("127.0.0.1:8000").
    host = urlsplit(value if "//" in value else f"//{value}").hostname
    return host in _LOOPBACK_HOSTS


def _only_this_app(request: Request) -> None:
    """Refuse anything that is not this desktop app talking to its own backend."""
    if not _loopback_host(request.headers.get("host")):
        raise HTTPException(status_code=403, detail="unexpected Host")
    origin = request.headers.get("origin")
    # Absent on same-origin GETs and on non-browser callers; present on every browser
    # POST/DELETE, including our own — so a foreign value means another site is driving.
    if origin is not None and not _loopback_host(origin):
        raise HTTPException(status_code=403, detail="cross-origin request refused")


def _capture_asset_ingest(
    pdir: Path, project_id: str, kind: str, target: Path, rel: str, name: str, source: str = "picker"
) -> Optional[str]:
    """One `asset_import_finished` + one `media_probe_finished` (or `_failed`) per asset.

    The probe runs HERE, at ingest, and not on the lazy /source_meta route: codec / HDR / fps
    are the render-outcome predictors, and /source_meta is not an ingest path at all.

    `source` is the closed enum in schemas/analytics/asset.json — "picker" for drop-or-choose
    on the Mac, "phone" for a file sent over the LAN by server/lan_receive.py. It is the one
    property that answers whether the phone path is worth keeping."""
    try:
        from server import asset_probe

        project_dir = pdir / project_id
        # PROJECT-relative, matching the `path` GET /assets reports and asset_probe's own
        # `rel_path` contract. `rel` here is PROJECTS-DIR-relative (it leads with the project id,
        # because the POST response's `path` is documented that way), and keying the manifest with
        # it meant the only reader could never find an entry — the id was minted, persisted under
        # a key nothing looks up, and asset_ids stayed empty. One writer, one reader: fixed here,
        # at the writer, so the key means the same thing as `path` everywhere else.
        aid = asset_probe.asset_id(project_dir, str(target.relative_to(project_dir.resolve())))
        analytics_mod.capture(
            "asset_import_finished",
            {
                "asset_id": aid,
                "asset_fingerprint": asset_probe.fingerprint(project_dir, target),
                "kind": kind,
                "source": source,
                "extension": _extension(name),
                "bytes": render_jobs_mod._bucket(target.stat().st_size, _ASSET_BYTES),
                "outcome": "success",
                "project_id": analytics_mod.project_key(pdir, project_id),
            },
        )
        if kind in ("video", "audio", "music"):
            fields, failure = asset_probe.probe(target)
            if failure:
                analytics_mod.capture(
                    "media_probe_failed",
                    {
                        "extension": _extension(name),
                        "failure_class": failure,
                    },
                )
            else:
                analytics_mod.capture("media_probe_finished", {"asset_id": aid, **(fields or {})})
        return aid
    except Exception:
        return None  # ingest must never fail because telemetry did


# ── auth telemetry helpers ───────────────────────────────────────────────────

# CHANGE-ONLY. /api/auth/status is POLLED by the UI, so an event per call is a poll upload.
# This is also why #14 is EMITTED rather than derived from the transitions: an install may
# already hold a valid credential when analytics ships, or may never visit auth at all, and
# would emit none of them.
_last_auth_state: Optional[str] = None
_AUTH_ATTEMPT: dict[str, Any] = {}
_AUTH_SECS = (5, 30, 120, 600)


def _capture_auth_state(snapshot: dict[str, Any]) -> None:
    global _last_auth_state
    try:
        state = "connected" if snapshot.get("authenticated") else "unconnected"
        if snapshot.get("needs_reauth"):
            state = "needs_reauth"
        method = snapshot.get("method") or "none"
        shape = f"{state}:{method}"
        if shape == _last_auth_state:
            return
        _last_auth_state = shape
        analytics_mod.capture(
            "auth_state_observed",
            {
                "state": state,
                "method": method,
                "expired": bool(snapshot.get("expired")),
            },
        )
    except Exception:
        pass


def _classify_connect_failure(detail: str) -> str:
    """A BOUNDED class from the error text, never the text: it can embed a pasted code, a URL
    or a provider message. The enum is what the funnel is sliced by."""
    d = (detail or "").lower()
    if "state" in d or "expired" in d or "stale" in d:
        return "expired_link"
    if "401" in d or "403" in d or "invalid" in d or "rejected" in d:
        return "exchange_rejected"
    if "timeout" in d or "connection" in d or "network" in d or "dns" in d:
        return "network"
    if "permission" in d or "keychain" in d or "write" in d:
        return "storage"
    return "invalid"


def _capture_connect_finished(method: str, outcome: str) -> None:
    try:
        t0 = _AUTH_ATTEMPT.pop("oauth_t0", None) if method == "oauth" else None
        analytics_mod.capture(
            "auth_connect_finished",
            {
                "method": method,
                "outcome": outcome,
                "duration_s": render_jobs_mod._bucket(time.monotonic() - t0, _AUTH_SECS) if t0 else None,
                "attempts": _AUTH_ATTEMPT.pop("oauth", 1) if method == "oauth" else 1,
            },
        )
    except Exception:
        pass


# ── provisioning telemetry helpers ───────────────────────────────────────────
# The app layer is the only one allowed to touch analytics: `lib/` "must not depend on server",
# which is why free_gb / proxy_cache_mb are computed HERE rather than added to provision.doctor().

_PACK_SIZE_EDGES = (50, 250, 1000, 2500)
_PROVISION_SECS = (5, 15, 60, 300)
# CHANGE-ONLY. /api/doctor is called by the setup window and by Settings, and the setup window
# calls it repeatedly while a tier installs — an event per call would be a poll upload.
_last_doctor_shape: Optional[str] = None


def _free_gb() -> Optional[float]:
    try:
        from lib import app_paths

        return shutil.disk_usage(app_paths.home()).free / 1e9
    except Exception:
        return None


def _proxy_cache_mb() -> Optional[float]:
    """Total bytes under the render proxy cache. Best-effort and bounded: a walk of a
    multi-GB tree is not worth blocking a status route for, so a failure returns None."""
    try:
        from lib import app_paths

        root = app_paths.home() / "cache" / "render"
        if not root.is_dir():
            return 0.0
        total = 0
        for p in root.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
        return total / 1e6
    except Exception:
        return None


def _capture_provisioning_snapshot(doc: dict[str, Any]) -> None:
    global _last_doctor_shape
    try:
        packs = sorted(name for name, ok in (doc.get("packs") or {}).items() if ok)
        flags = {
            k: bool(doc.get(k))
            for k in ("venv_ok", "core_ok", "ffmpeg_ok", "node_ok", "remotion_ok", "hyperframes_ok", "composition_ok")
        }
        shape = json.dumps({**flags, "packs": packs}, sort_keys=True)
        if shape == _last_doctor_shape:
            return
        _last_doctor_shape = shape
        analytics_mod.capture(
            "provisioning_snapshot",
            {
                **flags,
                "packs": packs,
                "free_gb": render_jobs_mod._bucket(_free_gb(), (5, 20, 100, 500)),
                "proxy_cache_mb": render_jobs_mod._bucket(_proxy_cache_mb(), (100, 500, 2000, 10000)),
            },
        )
    except Exception:
        pass  # a status route must never fail because telemetry did


def _capture_pack_outcome(done_extra: Optional[dict], outcome: str, started: float) -> None:
    """One event per pack install. `tier` installs are covered by Electron's provision_finished."""
    try:
        pack = (done_extra or {}).get("pack")
        if not pack:
            return
        from lib import provision

        analytics_mod.capture(
            "pack_install_outcome",
            {
                "pack": pack,
                "outcome": outcome,
                "duration_s": render_jobs_mod._bucket(time.monotonic() - started, _PROVISION_SECS),
                "size_mb": render_jobs_mod._bucket((provision.PACKS.get(pack) or {}).get("size_mb"), _PACK_SIZE_EDGES),
            },
        )
    except Exception:
        pass


def create_app(
    projects_dir: Path | str | None = None,
    *,
    state_source: Optional[StateSource] = None,
    capabilities_provider: Optional[Callable[[], dict[str, Any]]] = None,
    agent_runner: Optional[AgentRunner] = None,
) -> FastAPI:
    # Load .env so the backend picks up agent auth (CLAUDE_CODE_OAUTH_TOKEN /
    # ANTHROPIC_API_KEY) and provider keys the same way every tool does via
    # lib.env_loader. Without this the token had to be exported in the launching
    # shell, so a plain `uvicorn` restart silently dropped auth → chat 503s.
    # load_dotenv does NOT override vars already set in the environment, so an
    # explicitly-exported token still wins.
    load_env()

    # Contain cache/model/scratch writes to the app's own folders (OPN-10). Env
    # inheritance carries this to the agent CLI, Bash, and every tool subprocess,
    # so it must run before anything can spawn. Routing failures stay LOUD (an
    # unwritable cache volume means the app can't store projects either); the
    # scratch sweep is best-effort housekeeping and must never block startup.
    routed = app_paths.route_caches()
    if routed is not None:
        try:
            app_paths.sweep_scratch(routed)
        except Exception as exc:  # pragma: no cover — defensive; sweep swallows OSError itself
            print(f"[app] scratch sweep failed (non-fatal): {exc}", file=sys.stderr)

    app = FastAPI(title="OpenNolan Mission Control", version="0.1.0")

    @app.middleware("http")
    async def _bind_session(request: Request, call_next):
        """Bind the caller's session id for the duration of the request.

        The id is minted ONCE in Electron main (so a ⌘R reload does not split a session) and
        rides in on `X-ON-Session`. Binding it to a ContextVar here means every capture() in
        any route picks it up with no per-route plumbing; anyio's threadpool copies the
        context, so sync `def` routes see it too. Render/agent THREADS outlive the request and
        do not inherit it — they carry the id explicitly on the job/turn record instead."""
        analytics_mod.set_session_id(request.headers.get("X-ON-Session"))
        return await call_next(request)

    @app.exception_handler(Exception)
    async def _report_unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all for unhandled route errors: report to PostHog (no-op when opted out / under
        pytest) so backend crashes are visible, then return a clean 500. HTTPException has its own
        handler and never reaches here. Streaming routes that already started a response handle
        their own errors (see the chat SSE `drive()` below)."""
        # handled + non-fatal: the request 500s but the app keeps running, so this must NOT
        # land in wall #5's crash numerator.
        analytics_mod.capture_exception(
            exc, {"path": request.url.path, "method": request.method, "fatal": False, "handled": True}
        )
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    pdir = Path(projects_dir) if projects_dir is not None else _default_projects_dir()
    source = state_source or FileStateSource(pdir)
    cap_provider = capabilities_provider or _default_capabilities

    app.state.projects_dir = pdir
    app.state.state_source = source
    app.state.capabilities_cache = None  # lazily populated, then reused
    app.state.agent_runner = agent_runner  # injected (tests) or lazily built
    app.state.render_store = None  # editor render-job runner, lazily built

    # One product event per backend boot. No-op when opted out / posthog absent / under pytest.
    import platform

    analytics_mod.capture("app_opened", {"os": platform.platform(), "app_version": app.version})

    # First launch on this install ≈ an install/"download" (a fresh install has a new settings.json,
    # so this fires once per machine). The website "Download" button is still a waitlist placeholder,
    # so this is the real install signal until a downloadable build ships.
    if not settings_mod.get("app_first_run_done", False):
        analytics_mod.capture("app_first_run", {"os": platform.platform(), "app_version": app.version})
        settings_mod.set_value("app_first_run_done", True)

    # The abandonment sweep. Every other event fires when a user DOES something; abandonment is
    # the absence of that, so it has no hook and needs a detached pass. At most once per day,
    # and it is the ONE genuinely session-less event in the catalog.
    lifecycle_mod.sweep(pdir)

    def _render_store() -> RenderJobStore:
        if app.state.render_store is None:
            app.state.render_store = RenderJobStore(pdir)
        return app.state.render_store

    def _runner() -> Optional[AgentRunner]:
        if app.state.agent_runner is None and auth_configured():
            # Share ONE RenderJobStore with the editor so the agent's in-process
            # `render` tool runs through it (tracked/superseded), instead of the old
            # background-Bash render that broke turn attribution.
            app.state.agent_runner = AgentRunner(repo_root=REPO_ROOT, projects_dir=pdir, render_store=_render_store())
        return app.state.agent_runner

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "projects_dir": str(pdir)}

    @app.get("/api/pipelines")
    def pipelines() -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        for name in list_pipelines():
            try:
                manifest = load_pipeline(name)
                out.append(
                    {
                        "name": name,
                        "description": (manifest.get("description") or "").strip(),
                        "stability": manifest.get("stability"),
                        "stages": get_stage_order(manifest),
                    }
                )
            except Exception as exc:  # a broken manifest shouldn't hide the others
                out.append({"name": name, "error": str(exc)[:200]})
        return {"pipelines": out}

    @app.get("/api/pipelines/{name}")
    def pipeline_detail(name: str) -> dict[str, Any]:
        try:
            return load_pipeline(name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"pipeline {name!r} not found")

    @app.get("/api/styles")
    def styles() -> dict[str, Any]:
        """Available visual style playbooks (built-in + user-created), with the
        `identity` block the New Project picker shows. A broken user YAML is skipped
        rather than hiding the whole list."""
        builtins = set(list_playbooks(packaged=False))  # names shipped in styles/
        out: list[dict[str, Any]] = []
        for name in list_playbooks():
            entry: dict[str, Any] = {"name": name, "user": name not in builtins}
            try:
                ident = load_playbook(name).get("identity") or {}
                entry.update(
                    {
                        "category": ident.get("category"),
                        "mood": ident.get("mood"),
                        "pace": ident.get("pace"),
                        "best_for": ident.get("best_for"),
                        "label": ident.get("name") or name,
                    }
                )
            except Exception as exc:  # don't let one malformed style hide the rest
                entry["error"] = str(exc)[:200]
            out.append(entry)
        return {"styles": out}

    @app.get("/api/projects")
    def projects() -> dict[str, Any]:
        return {"projects": list_projects(pdir)}

    @app.get("/api/content-calendar")
    def content_calendar() -> dict[str, Any]:
        entries = content_calendar_mod.list_calendar_entries(pdir)
        return {"channels": list(content_calendar_mod.CHANNELS), "entries": entries}

    @app.post("/api/projects/{project_id}/schedule", status_code=201)
    def schedule_project(project_id: str, req: ScheduleProjectRequest) -> dict[str, Any]:
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        try:
            entry = content_calendar_mod.create_scheduled_entry(
                pdir,
                project_id,
                req.scheduled_at,
                req.channels,
                created_by="user",
            )
        except content_calendar_mod.FinalRenderMissing as exc:
            analytics_mod.capture(
                "content_schedule_failed",
                {
                    "created_by": "user",
                    "failure_class": "no_final_render",
                    "project_id": analytics_mod.project_key(pdir, project_id),
                },
            )
            raise HTTPException(status_code=409, detail=str(exc))
        except content_calendar_mod.ScheduleValidationError as exc:
            analytics_mod.capture(
                "content_schedule_failed",
                {
                    "created_by": "user",
                    "failure_class": content_calendar_mod.failure_class(exc),
                    "project_id": analytics_mod.project_key(pdir, project_id),
                },
            )
            raise HTTPException(status_code=422, detail=str(exc))
        except (OSError, ValueError) as exc:
            analytics_mod.capture(
                "content_schedule_failed",
                {
                    "created_by": "user",
                    "failure_class": "storage",
                    "project_id": analytics_mod.project_key(pdir, project_id),
                },
            )
            raise HTTPException(status_code=500, detail="could not save calendar entry") from exc
        analytics_mod.capture(
            "content_schedule_created",
            {
                "created_by": "user",
                "channel_count": len(entry["channels"]),
                "timing_source": entry["timing_source"],
                "replaced": entry["replaced"],
                "project_id": analytics_mod.project_key(pdir, project_id),
            },
        )
        return {"entry": entry}

    @app.post("/api/projects", status_code=201)
    def new_project(req: CreateProjectRequest) -> dict[str, Any]:
        # pipeline_type is optional: omit it and (in dev) the agent chooses one on
        # its first turn. If provided, it must be an AVAILABLE pipeline.
        pt = (req.pipeline_type or "").strip() or None
        available = list_pipelines()  # packaged-filtered to the single pipeline
        # In the packaged app there is exactly one pipeline — pin it so there is
        # no "agent picks" path and the project can never land on another pipeline.
        if pt is None and app_paths.is_packaged() and available:
            pt = available[0]
        if pt is not None and pt not in available:
            analytics_mod.capture("project_create_failed", {"failure_class": "unknown_pipeline"})
            raise HTTPException(
                status_code=422,
                detail=f"unknown pipeline_type {pt!r}",
            )
        # style is optional too; if given it must resolve to a known playbook (built-in or user).
        st = (req.style or "").strip() or None
        if st is not None and st not in set(list_playbooks()):
            analytics_mod.capture("project_create_failed", {"failure_class": "unknown_style"})
            raise HTTPException(status_code=422, detail=f"unknown style {st!r}")
        # builtin_playbooks(), NOT list_playbooks(packaged=False): the latter appends the
        # user's own styles regardless of that flag, so it reported every user style as
        # "built-in" and the name went out verbatim. That is the project-slug leak again.
        builtin_styles = builtin_playbooks()
        try:
            created = create_project(pdir, req.name, pt, style=st)
            # `style` may be a USER-created playbook whose name the user typed, so send the
            # built-in name only and collapse everything else to "user". `pipeline_type` is
            # validated against list_pipelines() above, so it is already a closed set.
            analytics_mod.capture(
                "project_created",
                {"pipeline_type": pt, "style": (st if st in builtin_styles else "user") if st else None},
            )
            return created
        except ProjectExistsError as exc:
            analytics_mod.capture("project_create_failed", {"failure_class": "duplicate"})
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:  # un-sluggable name
            analytics_mod.capture("project_create_failed", {"failure_class": "invalid_name"})
            raise HTTPException(status_code=422, detail=str(exc))
        except OSError as exc:
            analytics_mod.capture("project_create_failed", {"failure_class": "storage"})
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/projects/{project_id}/assets", status_code=201)
    def upload_asset(
        project_id: str,
        kind: str = Form(...),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        if kind not in ASSET_SUBDIRS:
            _asset_failed(pdir, project_id, kind, "invalid_kind")
            raise HTTPException(
                status_code=422,
                detail=f"invalid asset kind {kind!r}; expected one of {list(ASSET_SUBDIRS)}",
            )

        kind_dir = pdir / project_id / "assets" / kind
        try:
            safe_name = sanitize_filename(file.filename or "")
        except ValueError as exc:
            _asset_failed(pdir, project_id, kind, "invalid_name")
            raise HTTPException(status_code=400, detail=str(exc))

        target = (kind_dir / safe_name).resolve()
        base = kind_dir.resolve()
        # Defense in depth: the resolved target must stay inside the kind dir.
        if target != base and base not in target.parents:
            _asset_failed(pdir, project_id, kind, "traversal")
            raise HTTPException(status_code=400, detail="path traversal detected")

        kind_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(target, "wb") as out:
                shutil.copyfileobj(file.file, out)
        except OSError as exc:
            _asset_failed(pdir, project_id, kind, "disk_full" if "space" in str(exc).lower() else "copy")
            raise HTTPException(status_code=500, detail="could not store the asset")

        rel = str(target.relative_to(pdir.resolve()))
        # AFTER the copy, before the return: `target.stat()` is what gives the final byte count,
        # and the probe needs the file to exist. Not at the mkdir above, which runs before it.
        aid = _capture_asset_ingest(pdir, project_id, kind, target, rel, safe_name)

        return {
            "project_id": project_id,
            "kind": kind,
            "filename": safe_name,
            "path": rel,
            "asset_id": aid,
            "size_bytes": target.stat().st_size,
        }

    # ── receive from a phone (server/lan_receive.py) ──────────────────────────────────
    # macOS lets no app receive an AirDrop, so the phone-to-project path is a QR code the
    # phone opens. These three routes stay on 127.0.0.1 like everything else here; the only
    # thing that ever binds 0.0.0.0 is the token-gated, self-expiring server they start.

    @app.post("/api/projects/{project_id}/receive", status_code=201)
    def start_receive(project_id: str, request: Request) -> dict[str, Any]:
        _only_this_app(request)
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        try:
            return lan_receive.start(
                pdir,
                project_id,
                on_saved=lambda kind, target, rel, name: _capture_asset_ingest(
                    pdir, project_id, kind, target, rel, name, source="phone"
                ),
                on_failed=lambda kind, failure_class: _asset_failed(
                    pdir, project_id, kind, failure_class, source="phone"
                ),
                on_closed=lambda rollup: _capture_receive_closed(pdir, project_id, rollup),
            )
        except lan_receive.LanUnavailable as exc:
            # A window that never opened is still an outcome, and the one most likely to mean
            # the feature is unusable for someone — so it rides the same rollup rather than
            # being invisible because there was no session to close.
            _capture_receive_closed(
                pdir,
                project_id,
                {"outcome": "unavailable", "files": 0, "bytes": 0, "seconds_open": 0, "by_kind": {}},
            )
            raise HTTPException(status_code=503, detail=str(exc))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"could not open the receive port: {exc}")

    @app.get("/api/projects/{project_id}/receive")
    def receive_status(project_id: str, request: Request) -> dict[str, Any]:
        # Guarded like the writes: this response CARRIES THE TOKEN, so it is the one route a
        # rebinding page would most like to read.
        _only_this_app(request)
        # One window at a time app-wide, so a window belonging to ANOTHER project reads as
        # closed here — otherwise this project's panel would show someone else's uploads.
        st = lan_receive.status()
        if st is None or st["project_id"] != project_id:
            return {"active": False, "received": []}
        return st

    @app.delete("/api/projects/{project_id}/receive")
    def stop_receive(project_id: str, request: Request, session_id: Optional[str] = None) -> dict[str, Any]:
        _only_this_app(request)
        # `session_id` closes only the window the caller opened. Without it a stale close from
        # a re-mounted dialog would shut down the window that replaced it.
        return {"stopped": lan_receive.stop(session_id)}

    @app.get("/api/projects/{project_id}/assets")
    def list_assets(project_id: str) -> dict[str, Any]:
        """List a project's asset files (grouped by kind) and rendered outputs.
        Paths are relative to the project dir; fetch a file via /file?path=...

        Three buckets, kept distinct on purpose:
          - kinds       — user-managed source assets under assets/ (images/video/audio/music).
          - renders     — the editor's FINAL output(s) under renders/ (final.mp4, proxies, etc.);
                          the dashboard surfaces these as the "Final render" player.
          - agent_renders — the AGENT's intermediate HyperFrames clips under hf/renders/ (the
                          building blocks the editor drops onto the timeline). See AGENT_GUIDE.md
                          "Project Directory Convention" — any HyperFrames-rendering pipeline lands
                          per-scene clips here, so the editor's Renders tab is pipeline-agnostic."""
        proj = pdir / project_id
        if not proj.exists():
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")

        kinds: dict[str, list[dict[str, Any]]] = {"images": [], "video": [], "audio": [], "music": []}
        assets_dir = proj / "assets"
        if assets_dir.is_dir():
            # The persisted ingest ids, read ONCE. A LOOKUP, never asset_probe.asset_id(), which
            # mints-and-writes: this route is polled every 4s, so minting here would write the
            # manifest on every tick and hand ids to files that were never ingested. A manually
            # dropped file has no id and reports null — honest, and the editor just omits it.
            # Without this the editor had no path->asset_id map at all, so asset_added_to_doc
            # shipped asset_ids=[] forever and row 37's imported ⋈ added_in_editor join was dead.
            from server import asset_probe

            manifest = asset_probe.read_manifest(proj)
            for f in sorted(assets_dir.rglob("*")):
                if not f.is_file() or f.name.startswith("."):
                    continue
                rel = f.relative_to(proj)
                kind = _classify(rel.parts, f.suffix)
                if kind:
                    stat = f.stat()
                    kinds[kind].append(
                        {
                            "path": str(rel),
                            "name": f.name,
                            "size_bytes": stat.st_size,
                            "mtime": int(stat.st_mtime),
                            # Resolves the legacy PROJECTS-DIR-relative key too, so ids minted
                            # before the key was corrected still join.
                            "asset_id": asset_probe.lookup_asset_id(proj, str(rel), manifest),
                        }
                    )

        renders: list[dict[str, Any]] = []
        renders_dir = proj / "renders"
        if renders_dir.is_dir():
            # Is renders/final.mp4 the video of the LIVE timeline? Same callable the QA
            # gate runs, so the UI and the gate can never disagree about "current".
            final_status = final_render_status(pdir, project_id)
            # NON-recursive on purpose: only top-level renders/ files are deliverables.
            # Subdirs hold render-engine internals — renders/proxies/ (content-keyed
            # per-scene proxy cache) and renders/.final_review_frames/ — which are NOT
            # final renders. rglob swept those in and the dashboard showed every proxy
            # clip as a "Final render". Deliverables live directly in renders/ (final.mp4).
            for f in sorted(renders_dir.glob("*")):
                if f.is_file() and f.suffix.lower() in VIDEO_EXTS and not f.name.startswith("."):
                    stat = f.stat()
                    is_final = f.name == FINAL_RENDER_NAME
                    # mtime is the cache-bust/remount key the UI uses so a freshly
                    # finished (or re-rendered) MP4 reloads without a page refresh.
                    # SUB-SECOND: now that re-renders reuse ONE filename, two cached
                    # assemblies inside the same second would share a whole-second token
                    # and the browser would keep serving the old bytes.
                    # MICROSECONDS, not nanoseconds: an ns timestamp is 19 digits, past
                    # float64's exact-integer range, so JSON -> JS silently drops its low
                    # digits — an opaque token nobody can compare exactly is a trap. us is
                    # 16 digits (exact in JS) and still far finer than any two renders.
                    entry: dict[str, Any] = {
                        "path": str(f.relative_to(proj)),
                        "name": f.name,
                        "size_bytes": stat.st_size,
                        "mtime": stat.st_mtime_ns // 1000,
                        "current": is_final and final_status["current"],
                    }
                    if is_final:
                        entry["reason"] = final_status["reason"]
                    renders.append(entry)
            # The one current deliverable first; everything else is an earlier render.
            renders.sort(key=lambda r: (r["name"] != FINAL_RENDER_NAME, r["name"]))

        # Agent-rendered HyperFrames clips: the intermediate building blocks the agent
        # produces under hf/renders/ (separate from the editor's final output in renders/).
        # mtime doubles as the cache-bust/remount key, same as renders above.
        agent_renders: list[dict[str, Any]] = []
        hf_renders_dir = proj / "hf" / "renders"
        if hf_renders_dir.is_dir():
            for f in sorted(hf_renders_dir.rglob("*")):
                if f.is_file() and f.suffix.lower() in VIDEO_EXTS and not f.name.startswith("."):
                    stat = f.stat()
                    agent_renders.append(
                        {
                            "path": str(f.relative_to(proj)),
                            "name": f.name,
                            "size_bytes": stat.st_size,
                            "mtime": int(stat.st_mtime),
                        }
                    )

        return {"project_id": project_id, "kinds": kinds, "renders": renders, "agent_renders": agent_renders}

    @app.get("/api/projects/{project_id}/browse")
    def browse_project(project_id: str, path: str = "") -> dict[str, Any]:
        """List ONE folder inside a project — powers the editor's asset browser, which
        walks the real project tree instead of four flat kind tabs.

        Only what a user needs in order to FIND their media is listed: sub-folders, plus
        image / video / audio files and their readable companions (subtitles, notes).
        Everything else is noise for this purpose and is omitted — dot-entries (`.mc/`, the
        agent's chat history), JSON artifacts, HyperFrames HTML/CSS, HIDDEN_BROWSE_DIRS."""
        proj = (pdir / project_id).resolve()
        if not proj.is_dir():
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        target = (proj / path).resolve()
        # Same path-traversal guard as /file: the resolved dir must stay inside the project.
        if target != proj and proj not in target.parents:
            raise HTTPException(status_code=400, detail="path traversal detected")
        if not target.is_dir():
            raise HTTPException(status_code=404, detail=f"folder {path!r} not found")

        entries: list[dict[str, Any]] = []
        # Folders first, then files; each group alphabetical (case-insensitive).
        for f in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            rel = f.relative_to(proj)
            if _browse_hidden(rel):
                continue
            if f.is_dir():
                entries.append(
                    {"name": f.name, "path": rel.as_posix(), "is_dir": True, "count": _browse_count(f, proj)}
                )
                continue
            kind = _browse_kind(rel, f.suffix)
            if not kind:
                continue
            stat = f.stat()
            # `kind` picks the viewer in the asset dialog and what its add button does on the
            # timeline (image→overlay, video→cut, music→bed, audio→SFX; text has no add);
            # `mtime` is the cache-bust key for thumbnails.
            entries.append(
                {
                    "name": f.name,
                    "path": rel.as_posix(),
                    "is_dir": False,
                    "kind": kind,
                    "size_bytes": stat.st_size,
                    "mtime": int(stat.st_mtime),
                }
            )

        return {
            "project_id": project_id,
            "path": "" if target == proj else target.relative_to(proj).as_posix(),
            "entries": entries,
        }

    @app.get("/api/projects/{project_id}/file")
    def get_file(project_id: str, path: str):
        """Serve a single file from within the project dir (images/video/audio/render).
        Path-traversal protected: the resolved target must stay inside the project."""
        proj = (pdir / project_id).resolve()
        target = (pdir / project_id / path).resolve()
        if proj != target and proj not in target.parents:
            raise HTTPException(status_code=400, detail="path traversal detected")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(str(target))

    @app.get("/api/projects/{project_id}/state")
    def project_state(project_id: str) -> dict[str, Any]:
        st = source.project_state(project_id)
        if st is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        return st

    @app.get("/api/projects/{project_id}/artifacts")
    def list_artifacts(project_id: str) -> dict[str, Any]:
        """Artifact manifest grouped by pipeline stage (+ the cross-cutting
        decision_log summary). Fetch one artifact's content via /artifacts/{key}."""
        manifest = artifacts_mod.list_artifacts(pdir, project_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        return manifest

    @app.get("/api/projects/{project_id}/artifacts/{key}")
    def get_artifact(project_id: str, key: str) -> dict[str, Any]:
        """Parsed content of a single artifact, addressed by its key (e.g.
        scene_plan, decision_log). Key is a safe slug — no path traversal."""
        try:
            art = artifacts_mod.read_artifact(pdir, project_id, key)
        except artifacts_mod.BadArtifactKey as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if art is None:
            raise HTTPException(status_code=404, detail=f"artifact {key!r} not found")
        return art

    # ── Manual editor: edit_decisions read/write + render jobs ────────────
    # The editor is the human-driven exception to agent-first orchestration.
    # Writes go through the same schema gate as the agent; renders reuse the
    # exact video_compose path the compose stage uses.

    @app.get("/api/projects/{project_id}/edit_decisions")
    def get_edit_decisions(project_id: str) -> dict[str, Any]:
        """The editable timeline spec. `content` is null for a project with none yet
        (the UI scaffolds a minimal valid doc rather than erroring)."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        return {
            "project_id": project_id,
            "content": editor_mod.read_edit_decisions(pdir, project_id),
            # The random persisted analytics id. The renderer only ever knows the slug (which
            # is the user's typed name), so without this its session summary — the one event
            # carrying every feature_id — could not be joined to a project at all.
            "analytics_id": analytics_mod.project_key(pdir, project_id),
        }

    @app.put("/api/projects/{project_id}/edit_decisions")
    def put_edit_decisions(project_id: str, doc: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Validate against the edit_decisions schema and atomically write.
        Returns 422 with the validation message on schema failure (file untouched)."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        try:
            editor_mod.write_edit_decisions(pdir, project_id, doc)
        except editor_mod.EditDecisionsInvalid as exc:
            analytics_mod.capture(
                "schema_write_rejected",
                {
                    "object_kind": _rejected_object_kind(str(exc)),
                    "failure_field": _rejected_field(str(exc)),
                    "origin": "editor",
                    "project_id": analytics_mod.project_key(pdir, project_id),
                },
            )
            raise HTTPException(status_code=422, detail=str(exc)[:1500])
        return {"project_id": project_id, "saved": True}

    @app.post("/api/projects/{project_id}/render", status_code=202)
    def start_render(project_id: str) -> dict[str, Any]:
        """Start a background render of the saved edit_decisions. Returns a job_id to poll.
        Supersedes any in-flight render for this project."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        # The render thread outlives this request, so it cannot read the session ContextVar
        # later — hand the id over now and let it ride on the job record.
        job_id = _render_store().start(project_id, session_id=analytics_mod.current_session_id())
        return {"project_id": project_id, "job_id": job_id, "status": "queued"}

    @app.get("/api/projects/{project_id}/render/{job_id}")
    def render_status(project_id: str, job_id: str) -> dict[str, Any]:
        """Poll a render job: {status: queued|running|done|failed, output_path?, error?}."""
        st = _render_store().status(job_id)
        if st is None or st.get("project_id") != project_id:
            raise HTTPException(status_code=404, detail=f"render job {job_id!r} not found")
        return st

    @app.get("/api/projects/{project_id}/frame")
    def get_frame(project_id: str, path: str, t: float = 0.0):
        """Extract a single still at time `t` from a project video (cheap scrub preview).
        Path-traversal protected, exactly like /file."""
        proj = (pdir / project_id).resolve()
        target = (pdir / project_id / path).resolve()
        if proj != target and proj not in target.parents:
            raise HTTPException(status_code=400, detail="path traversal detected")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        if shutil.which("ffmpeg") is None:
            raise HTTPException(status_code=503, detail="ffmpeg not available for frame extraction")
        out = Path(tempfile.mkstemp(suffix=".jpg")[1])
        proc = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(max(0.0, t)), "-i", str(target), "-frames:v", "1", "-q:v", "3", str(out)],
            capture_output=True,
        )
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            raise HTTPException(status_code=500, detail="frame extraction failed")
        return FileResponse(str(out), media_type="image/jpeg")

    @app.get("/api/projects/{project_id}/source")
    def get_source(project_id: str, ref: str):
        """Serve a cut's SOURCE clip for live (pre-render) scrub preview.

        `ref` is a cut's `source` value (asset_id or path); resolution mirrors video_compose
        and is confined to the project dir. FileResponse honors Range requests, so the
        browser can seek the <video> for smooth scrubbing without a render.

        ProRes .mov overlays (HyperFrames alpha renders) are transcoded on first request to
        VP9/WebM with alpha preserved — the only alpha-capable format Chromium can decode.
        The proxy is cached in <project>/.browser_cache/ for instant subsequent loads."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        target = editor_mod.resolve_source_path(pdir, project_id, ref)
        if target is None:
            analytics_mod.capture(
                "source_resolution_failed",
                {
                    "reference_kind": render_jobs_mod._reference_kind(ref),
                    "consumer": "preview",
                    "outcome": "missing",
                    "project_id": analytics_mod.project_key(pdir, project_id),
                },
            )
            raise HTTPException(status_code=404, detail="source not found within project")
        cache_dir = Path(pdir) / project_id / ".browser_cache"
        preview = editor_mod.browser_preview_path(target, cache_dir)
        if preview is not None:
            return FileResponse(str(preview), media_type="video/webm")
        return FileResponse(str(target))

    @app.get("/api/projects/{project_id}/source_meta")
    def source_meta(project_id: str, ref: str) -> dict[str, Any]:
        """Probe a source clip's duration + dimensions (for trim bounds and scrub math).

        `duration` is null if ffprobe is unavailable — trimming still works, just unclamped."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        target = editor_mod.resolve_source_path(pdir, project_id, ref)
        if target is None:
            raise HTTPException(status_code=404, detail="source not found within project")
        try:
            rel = str(target.relative_to((pdir / project_id).resolve()))
        except ValueError:
            rel = str(target)  # a shared in-repo asset (sfx/kit) resolved outside the project

        meta: dict[str, Any] = {
            "project_id": project_id,
            "ref": ref,
            "path": rel,
            "duration": None,
            "width": None,
            "height": None,
        }
        if shutil.which("ffprobe") is not None:
            proc = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    str(target),
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                try:
                    data = json.loads(proc.stdout)
                    dur = (data.get("format") or {}).get("duration")
                    meta["duration"] = float(dur) if dur is not None else None
                    streams = data.get("streams") or []
                    if streams:
                        meta["width"] = streams[0].get("width")
                        meta["height"] = streams[0].get("height")
                except (ValueError, KeyError, TypeError):
                    pass
        return meta

    @app.get("/api/projects/{project_id}/activity")
    def project_activity(project_id: str, limit: Optional[int] = None, since: Optional[str] = None) -> dict[str, Any]:
        """The agent's persisted tool-activity log (files touched, skills run,
        tools used) plus a synthesized 'how this was made' summary."""
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        return activity_mod.read_activity(pdir, project_id, limit=limit, since=since)

    @app.get("/api/capabilities")
    def capabilities() -> dict[str, Any]:
        if app.state.capabilities_cache is None:
            try:
                app.state.capabilities_cache = cap_provider()
            except Exception as exc:
                # Surface discovery failure explicitly rather than 500-ing — and report it, since a
                # swallowed discovery failure means the whole tool/provider menu is silently empty.
                analytics_mod.capture_exception(exc, {"where": "capability_discovery"})
                app.state.capabilities_cache = {"error": f"capability discovery failed: {exc}"}
        return app.state.capabilities_cache

    @app.get("/api/env")
    def get_env() -> dict[str, Any]:
        """BYOK: the curated variable menu + each var's current value from the local .env."""
        from server import env_config

        return {"path": str(env_config.ENV_PATH), "vars": env_config.list_env_vars()}

    @app.put("/api/env")
    def put_env(body: EnvUpdateRequest) -> dict[str, Any]:
        """BYOK: persist edited variables back to the local .env, then reload them for this session."""
        from server import env_config

        try:
            changed = env_config.write_env_vars(body.vars)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        env_config.reload_env()  # so the next agent turn / tool subprocess sees the new keys
        for family in sorted({analytics_mod.provider_family(name) for name in body.vars}):
            analytics_mod.capture(
                "byok_var_saved",
                {
                    "provider_family": family,
                    "changed_count": sum(1 for n in body.vars if analytics_mod.provider_family(n) == family),
                    "outcome": "success",
                },
            )
        return {"changed": changed, "path": str(env_config.ENV_PATH), "vars": env_config.list_env_vars()}

    # ── Anthropic account auth ("Sign in with Claude" / API-key fallback) ──────────
    @app.get("/api/auth/status")
    def auth_status() -> dict[str, Any]:
        """Whether the agent can reach the user's Anthropic account, and whether a reconnect is
        needed (expired/revoked token, rejected key). Polled by the UI to drive the sign-in CTA,
        the top-right re-auth button, and the in-chat reconnect box."""
        snap = auth_mod.status()
        _capture_auth_state(snap)
        return snap

    @app.post("/api/auth/oauth/start")
    def auth_oauth_start() -> dict[str, Any]:
        """Begin the PKCE flow; returns the claude.ai authorize URL to open in the browser."""
        analytics_mod.capture("oauth_started", {"entrypoint": "settings"})
        _AUTH_ATTEMPT["oauth"] = _AUTH_ATTEMPT.get("oauth", 0) + 1
        _AUTH_ATTEMPT["oauth_t0"] = time.monotonic()
        return auth_mod.start_oauth()

    @app.post("/api/auth/oauth/finish")
    def auth_oauth_finish(body: OAuthFinishRequest) -> dict[str, Any]:
        """Exchange the pasted `code#state` for an OAuth token and persist it."""
        try:
            result = auth_mod.finish_oauth(body.code, app_state=app.state)
        except auth_mod.AuthError as exc:
            _capture_connect_finished("oauth", _classify_connect_failure(str(exc)))
            raise HTTPException(status_code=400, detail=str(exc))
        _capture_connect_finished("oauth", "success")
        return result

    @app.post("/api/auth/api-key")
    def auth_api_key(body: ApiKeyRequest) -> dict[str, Any]:
        """Verify an Anthropic API key with a live call, then persist it (fallback path)."""
        try:
            result = auth_mod.set_api_key(body.api_key, app_state=app.state)
        except auth_mod.AuthError as exc:
            _capture_connect_finished("api_key", _classify_connect_failure(str(exc)))
            raise HTTPException(status_code=400, detail=str(exc))
        _capture_connect_finished("api_key", "success")
        return result

    @app.post("/api/auth/disconnect")
    def auth_disconnect() -> dict[str, Any]:
        """Forget the stored Anthropic credential."""
        prior = (auth_mod.status() or {}).get("method") or "unknown"
        analytics_mod.capture("auth_disconnected", {"prior_method": prior})
        return auth_mod.disconnect(app_state=app.state)

    @app.get("/api/settings/analytics")
    def get_analytics_settings() -> dict[str, Any]:
        """Analytics opt-out state + the anonymous device id (no PII). Drives the settings toggle."""
        return {
            "disabled": bool(settings_mod.get("analytics_disabled", False)),
            "device_id": settings_mod.device_id(),
        }

    @app.put("/api/settings/analytics")
    def put_analytics_settings(body: AnalyticsUpdateRequest) -> dict[str, Any]:
        """Flip the opt-out. Persisted to settings.json; the analytics client is torn down and its
        init memo cleared so the change takes effect immediately (no client exists while opted out)."""
        settings_mod.set_value("analytics_disabled", bool(body.disabled))
        analytics_mod.shutdown()
        analytics_mod.reset()
        return {"disabled": bool(body.disabled)}

    @app.post("/api/feedback")
    def post_feedback(body: FeedbackRequest) -> dict[str, Any]:
        """In-app bug / feature feedback. Always stored locally (never lost); a PostHog event is
        emitted (metadata only); emailed via Resend when configured. See server/feedback.py."""
        from server import feedback as feedback_mod

        try:
            return feedback_mod.submit(body.kind, body.message, body.email, body.diagnostics, body.debug_session)
        except feedback_mod.FeedbackError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/telemetry/error")
    def post_client_error(body: ClientErrorReport) -> dict[str, Any]:
        """Report a frontend (React) / Electron error to PostHog Error Tracking. Always 200 — a
        no-op when analytics is opted out; reporting must never itself error into the client."""
        analytics_mod.capture_client_error(body.source, body.message, body.stack, body.context)
        return {"received": True}

    @app.post("/api/telemetry/events")
    def post_client_events(body: TelemetryBatch) -> dict[str, Any]:
        """Renderer product events, batched. Mirrors /api/telemetry/error above.

        The renderer has no PostHog client of its own on purpose: routing through here means
        ONE validator, ONE scrubber and ONE envelope for all four sources. Always 200 — a
        client that cannot report telemetry must not see an error because of it."""
        accepted = 0
        for evt in body.events[:_TELEMETRY_BATCH_MAX]:
            name = (evt.get("event") or "").strip()
            props = evt.get("properties")
            if name and analytics_mod.capture(name, props if isinstance(props, dict) else None):
                accepted += 1
        # The renderer's 5s flush is also the backend's natural flush point, so it is where
        # events queued by SEPARATE PROCESSES (scripts/update_stage.py) are drained.
        for name, props in outbox_mod.drain():
            analytics_mod.capture(name, props)
        # `accepted` is what the renderer's session announcement clears its pending marker on,
        # so it must mean "capture() handed this to the client", NOT "we looped over it".
        # `received` is kept for compatibility with anything reading the old shape.
        return {"received": min(len(body.events), _TELEMETRY_BATCH_MAX), "accepted": accepted}

    @app.get("/api/doctor")
    def get_doctor() -> dict[str, Any]:
        """First-run provisioning status: is the managed venv/core/ffmpeg present, which capability
        packs are installed. Drives the setup UI + the 'install pack' prompts. See lib/provision.py."""
        from lib import provision

        doc = provision.doctor()
        _capture_provisioning_snapshot(doc)
        return doc

    def _stream_provision(work, done_extra: Optional[dict] = None) -> StreamingResponse:
        """Run a provisioning `work(progress)` in a worker thread, streaming NDJSON log/done/error frames
        so the UI can show progress without the (multi-minute) install holding the event loop. Shared by
        the capability-pack and composition-tier install endpoints."""
        import queue
        import threading

        q: "queue.Queue[Optional[str]]" = queue.Queue()

        def worker() -> None:
            started = time.monotonic()
            try:
                work(lambda line: q.put(json.dumps({"type": "log", "line": line})))
                q.put(json.dumps({"type": "done", **(done_extra or {})}))
                _capture_pack_outcome(done_extra, "success", started)
            except Exception as exc:  # surface a clean error frame to the stream
                q.put(json.dumps({"type": "error", "error": str(exc)}))
                # The SERVER streaming boundary, not lib/provision.py: that is both a function
                # DEFINITION and inside lib/, which "must not depend on server".
                _capture_pack_outcome(done_extra, "failed", started)
            finally:
                q.put(None)  # sentinel

        threading.Thread(target=worker, daemon=True).start()

        def stream():
            while True:
                frame = q.get()
                if frame is None:
                    break
                yield frame + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/api/provision/composition")
    def post_provision_composition() -> StreamingResponse:
        """Install the composition tier (Node engines: Remotion + HyperFrames) into the managed runtime,
        streaming progress as NDJSON. Provisioned eagerly at first run; this is the RETRY path (Settings)
        after a failed/skipped install. Registered BEFORE /api/provision/{pack} so this static path wins
        over the {pack} param route (else 'composition' would be read as an unknown pack -> 404)."""
        from lib import provision

        return _stream_provision(provision.provision_composition, {"tier": "composition"})

    @app.post("/api/provision/{pack}")
    def post_provision(pack: str) -> StreamingResponse:
        """Lazily install a capability pack (whisperx/torch, mediapipe, rembg, librosa, piper) into the
        managed venv, streaming pip progress as NDJSON so the UI can show it."""
        from lib import provision

        if pack not in provision.PACKS:
            raise HTTPException(status_code=404, detail=f"unknown pack {pack!r}")
        return _stream_provision(lambda progress: provision.provision_pack(pack, progress), {"pack": pack})

    @app.post("/api/projects/{project_id}/chat")
    async def chat(project_id: str, body: ChatRequest):
        """Stream an agent turn as Server-Sent Events.

        Requires agent auth (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY);
        returns 503 with setup guidance otherwise. Each agent event is one
        `data: {json}` SSE line; a `confirm_request` event pauses the agent
        until the client POSTs /agent/confirm.
        """
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
        # SHAPE-check the @-mention sidecar BEFORE anything else: the menu can only ever
        # produce a valid path, so a violation is a client bug or a tampered request and
        # should fail loudly and identically whatever the auth state. STATE failures (the
        # file vanished) are handled below and deliberately do NOT block the turn.
        resolved, shape_errors = resolve_mentions(pdir / project_id, body.mentions)
        if shape_errors:
            raise HTTPException(
                status_code=422,
                detail="invalid asset mention — " + "; ".join(shape_errors),
            )
        if not auth_configured():
            raise HTTPException(
                status_code=503,
                detail=(
                    "agent auth not configured. Easiest fix: install and log into "
                    "Claude Code on this machine (`npm i -g @anthropic-ai/claude-code`, "
                    "then `claude` to log in) — the agent then uses your subscription "
                    "automatically. Or run `claude setup-token` and put "
                    "CLAUDE_CODE_OAUTH_TOKEN in .env (unset ANTHROPIC_API_KEY to avoid "
                    "per-token billing)."
                ),
            )
        runner = _runner()
        if runner is None:
            raise HTTPException(status_code=503, detail="agent runner unavailable")

        # If continuing a stored thread, align the live session to it so the agent
        # resumes that conversation (its context), not whatever ran last.
        if body.thread_id:
            thread = thread_store.get_thread(pdir, project_id, body.thread_id)
            await runner.switch_session(project_id, thread.get("session_id") if thread else None)

        # Apply the UI-selected model when the client picked one (no-op when
        # unchanged / unknown). Keeps context across a mid-chat model switch.
        if body.model:
            await runner.set_model(project_id, body.model)

        queue: asyncio.Queue = asyncio.Queue()
        # A live turn is the truth about the credential: the model answering clears any prior
        # "reconnect" flag; a turn-level auth failure sets it (and is re-tagged `auth_error` so the
        # UI shows the reconnect box instead of a nondescript red line). See server/auth.py.
        cleared = {"done": False}

        def _evt_text(evt: dict[str, Any]) -> str:
            # `result` is where a ResultMessage carries its (error) text; the rest cover error events.
            return " ".join(str(evt.get(k, "")) for k in ("detail", "text", "error", "message", "result"))

        async def emit(evt: dict[str, Any]) -> None:
            etype = evt.get("type")
            if etype == "assistant" and not cleared["done"]:
                cleared["done"] = True
                auth_mod.clear_auth_error()
            elif etype == "result" and evt.get("is_error"):
                text = _evt_text(evt)
                if auth_mod.classify_auth_error(text):
                    auth_mod.mark_auth_error(text)
                    await queue.put({"type": "auth_error", "detail": text[:400]})
            await queue.put(evt)

        # Read the session id NOW, in the request's own context. `drive()` is a task that
        # outlives this handler, so it cannot rely on the ContextVar still being bound.
        session_id = analytics_mod.current_session_id()

        async def drive() -> None:
            try:
                # Same object when there are no mentions, so every existing turn is
                # byte-for-byte unchanged.
                await runner.run_turn(
                    project_id,
                    message_with_mentions(body.message, resolved),
                    on_event=emit,
                    session_id=session_id,
                )
            except Exception as exc:  # surface runner failure as an SSE event
                detail = str(exc)[:500]
                if auth_mod.classify_auth_error(detail):
                    auth_mod.mark_auth_error(detail)
                    await queue.put({"type": "auth_error", "detail": detail})
                else:
                    # A real agent-turn failure (not an auth reconnect) — report it; this is where
                    # most runtime crashes surface, and the SSE stream already started so the global
                    # exception handler above can't see it.
                    analytics_mod.capture_exception(exc, {"where": "agent_turn"})
                    # Append the CLI's own stderr. Classification above stays on the bare message
                    # (a stderr tail mentioning auth must not turn an unrelated crash into a
                    # reconnect prompt); only the text the user reads gets the extra detail.
                    await queue.put({"type": "error", "detail": runner.cli_error_detail(detail, project_id)[:2000]})
            finally:
                await queue.put(None)  # sentinel: stream complete

        async def gen():
            task = asyncio.create_task(drive())
            try:
                while True:
                    evt = await queue.get()
                    if evt is None:
                        break
                    yield f"data: {json.dumps(evt)}\n\n"
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/projects/{project_id}/agent/confirm")
    def agent_confirm(project_id: str, body: ConfirmRequest) -> dict[str, Any]:
        runner = app.state.agent_runner
        if runner is None:
            raise HTTPException(status_code=409, detail="no active agent runner")
        return {"resolved": runner.resolve_confirm(body.confirm_id, body.approved)}

    @app.post("/api/projects/{project_id}/agent/answer")
    def agent_answer(project_id: str, body: AnswerRequest) -> dict[str, Any]:
        runner = app.state.agent_runner
        if runner is None:
            raise HTTPException(status_code=409, detail="no active agent runner")
        return {"resolved": runner.resolve_answer(body.question_id, body.answer)}

    @app.post("/api/projects/{project_id}/agent/provide-key")
    def agent_provide_key(project_id: str, body: ProvideKeyRequest) -> dict[str, Any]:
        """Answer an agent `request_api_key` prompt. On save, persist the key to the BYOK
        .env (so it also appears in the BYOK panel) and reload it so this session's next tool
        subprocess picks it up — THEN unblock the agent's tool. On skip, unblock as declined
        without writing. The raw key is never returned to the agent."""
        runner = app.state.agent_runner
        if runner is None:
            raise HTTPException(status_code=409, detail="no active agent runner")
        from server import env_config

        if body.skipped:
            return {"resolved": runner.resolve_key_request(body.key_request_id, False), "saved": False}

        value = (body.value or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="a non-empty key value is required (or set skipped=true)")
        try:
            changed = env_config.write_env_vars({body.env_var: value})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        env_config.reload_env()  # so this session's next tool subprocess inherits the new key
        # Persist happened; NOW unblock the waiting tool so it retries with the key present.
        resolved = runner.resolve_key_request(body.key_request_id, True)
        return {"resolved": resolved, "saved": True, "changed": changed}

    @app.post("/api/projects/{project_id}/agent/provide-capability")
    def agent_provide_capability(project_id: str, body: ProvideCapabilityRequest) -> dict[str, Any]:
        """Answer an agent `request_capability` prompt. The UI installs the pack itself (by streaming
        /api/provision/{pack}) and then calls this to unblock the waiting tool: installed=true means
        the agent retries; installed=false (declined) means it moves on. This endpoint does NOT run
        the install — it only resolves the agent's await after the UI's install stream finished."""
        runner = app.state.agent_runner
        if runner is None:
            raise HTTPException(status_code=409, detail="no active agent runner")
        resolved = runner.resolve_capability_request(body.cap_request_id, bool(body.installed))
        return {"resolved": resolved}

    @app.post("/api/projects/{project_id}/agent/stop")
    async def agent_stop(project_id: str) -> dict[str, Any]:
        """Interrupt the agent mid-turn. Context is preserved; the next message
        resumes normally. No-op (stopped=False) if nothing is running."""
        runner = app.state.agent_runner
        if runner is None:
            raise HTTPException(status_code=409, detail="no active agent runner")
        return {"stopped": await runner.interrupt(project_id)}

    # ── Chat threads (history + revival) ──────────────────────────────────
    def _require_project(project_id: str) -> None:
        if get_project_record(pdir, project_id) is None:
            raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")

    @app.get("/api/projects/{project_id}/threads")
    def list_threads(project_id: str) -> dict[str, Any]:
        return {"threads": thread_store.list_threads(pdir, project_id)}

    @app.post("/api/projects/{project_id}/threads", status_code=201)
    def create_thread(project_id: str, body: ThreadCreate) -> dict[str, Any]:
        _require_project(project_id)
        return thread_store.create_thread(pdir, project_id, title=body.title or "New chat")

    @app.get("/api/projects/{project_id}/threads/{thread_id}")
    def get_thread(project_id: str, thread_id: str) -> dict[str, Any]:
        rec = thread_store.get_thread(pdir, project_id, thread_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"thread {thread_id!r} not found")
        return rec

    @app.put("/api/projects/{project_id}/threads/{thread_id}")
    def save_thread(project_id: str, thread_id: str, body: ThreadSave) -> dict[str, Any]:
        return thread_store.save_thread(
            pdir,
            project_id,
            thread_id,
            messages=body.messages,
            session_id=body.session_id,
            title=body.title,
        )

    # ── Dev observability: UI session recorder sink ────────────────────────────
    # The editor's recorder batch-POSTs timestamped events (console, errors, clicks,
    # scrub/seek) here; we append them as NDJSON under .agents/tools/logs/ui-sessions/
    # so the coding agent can read a full, ordered trace of a session to diagnose (and
    # reproduce) intermittent UI bugs. Dev-only sink; never touches project data.
    @app.post("/api/debug/log")
    def debug_log(body: DebugLogBody) -> dict[str, Any]:
        try:
            written = debug_log_mod.append_events(body.session, body.events)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "written": written}

    @app.get("/api/debug/sessions")
    def debug_sessions() -> dict[str, Any]:
        return {"sessions": debug_log_mod.list_sessions()}

    # "Query, don't read": a compact report (histogram + seek anomalies + verbatim errors) so a
    # tool/agent never loads the multi-thousand-line raw NDJSON into context. Pass 'latest'.
    @app.get("/api/debug/sessions/{session}/analyze")
    def debug_analyze(session: str) -> dict[str, Any]:
        if session == "latest":
            latest = debug_log_mod.latest_session()
            if not latest:
                raise HTTPException(status_code=404, detail="no sessions recorded")
            session = latest
        try:
            return debug_log_mod.analyze_session(session)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"session {session!r} not found")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete("/api/debug/sessions/{session}")
    def debug_discard(session: str) -> dict[str, Any]:
        """Discard a recorded session's logs (the user chose not to send the debug report).
        Idempotent — removing an already-gone session is a 200 with removed=false."""
        try:
            removed = debug_log_mod.delete_session(session)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "removed": removed}

    # Serve the built Mission Control UI (web/dist) for the packaged desktop app.
    # Mounted LAST so every /api/* route above takes precedence; the SPA and its
    # assets are then same-origin with the API (no CORS) when Electron loads
    # http://127.0.0.1:<port>/. No-op in dev: when web/dist is absent, Vite serves
    # the SPA on :5173 and proxies /api here. (html=True serves index.html at "/".)
    #
    # NOTE: html=True does NOT catch-all unknown paths -> index.html. The current UI
    # has no client-side router, so every load hits "/" and this is fine. If WS3 adds
    # a history-API router with deep links, add a fallback returning
    # FileResponse(web_dist / "index.html") for non-/api 404s.
    web_dist = REPO_ROOT / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="ui")

    return app


# Default app for `uvicorn server.app:app`.
app = create_app()
