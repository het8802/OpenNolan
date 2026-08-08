"""Single source of truth for WHERE OpenNolan reads code vs. WHERE it writes user data.

Why this exists (P0 of the "publish as a Mac app" plan, docs/plans/publish-mac-app.md):
the desktop shell today boots the backend from inside the git checkout and writes
projects/keys next to the code. A downloaded, notarized `.app` bundle is READ-ONLY, so
user data must live outside it. This module is the one place that decides both roots, so
no other file has to hardcode "repo root" again.

Two independent roots — do not conflate them (the whole point of the refactor):

    code_root()  READ-ONLY: skills/, tools/, lib/, pipeline_defs/, scripts/, schemas/,
                 AGENT_GUIDE.md. Repo checkout in dev; app-bundle Resources/ in prod.
    home()       WRITABLE:   .env (BYOK keys), projects/, the managed venv, caches, models.
                 Repo checkout in dev; ~/Library/Application Support/OpenNolan in prod.

Dev is behavior-preserving: with none of the env vars set, every path falls back to the
repo root, exactly as before. The packaged app sets OPENNOLAN_HOME (and, once the backend
ships in the bundle, OPENNOLAN_CODE_ROOT) before spawning uvicorn.

    OPENNOLAN_HOME          -> home() root            (default: repo root)
    OPENNOLAN_CODE_ROOT     -> code_root()            (default: repo root)
    OPENNOLAN_PROJECTS_DIR  -> projects_dir()         (default: <home>/projects)
    OPENNOLAN_ENV_FILE      -> env_path()             (default: <home>/.env)
    OPENNOLAN_RUNTIME_DIR   -> runtime_dir()          (default: <home>/runtime)  [venv lives here]
    OPENNOLAN_CACHE_DIR     -> cache_dir()            (default: <home>/appcache)
    OPENNOLAN_ROUTE_CACHES  -> route_caches() gate    (default: on iff packaged; "0" forces off)

Resolution reads os.environ on each call (cheap, and lets a subprocess that sets these
vars be picked up), so this is deliberately NOT cached at import time. route_caches()
is the one function here with side effects, and it runs only when called explicitly —
never at import.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

# The git checkout root — the historical default for everything. lib/ is one level down.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def code_root() -> Path:
    """Read-only code/skills/tools/guides root. Repo checkout in dev; bundle Resources in prod."""
    return Path(os.environ.get("OPENNOLAN_CODE_ROOT", _REPO_ROOT))


def home() -> Path:
    """Writable data root. Repo checkout in dev; ~/Library/Application Support/OpenNolan in prod."""
    return Path(os.environ.get("OPENNOLAN_HOME", _REPO_ROOT))


def projects_dir() -> Path:
    """Where per-project artifacts/assets/renders/checkpoints are written."""
    override = os.environ.get("OPENNOLAN_PROJECTS_DIR")
    return Path(override) if override else home() / "projects"


def env_path() -> Path:
    """The BYOK `.env`. Repo `.env` in dev; <home>/.env in prod (chmod 600 by the writer)."""
    override = os.environ.get("OPENNOLAN_ENV_FILE")
    return Path(override) if override else home() / ".env"


def user_styles_dir() -> Path:
    """Where USER-CREATED style playbooks live (dropped in manually by the user).

    Writable in both dev and the packaged app (it sits under home(), which the agent's
    filesystem sandbox whitelists) and kept OUT of the read-only built-in ``styles/`` dir so
    user styles never collide with, or get shadowed by, the shipped playbooks. The playbook
    loader merges this dir with the built-ins so they show in the New Project style picker
    (see styles/playbook_loader.py)."""
    override = os.environ.get("OPENNOLAN_USER_STYLES_DIR")
    return Path(override) if override else home() / "user_styles"


def runtime_dir() -> Path:
    """Where the managed Python venv + downloaded ffmpeg + capability packs live (bootstrapper)."""
    override = os.environ.get("OPENNOLAN_RUNTIME_DIR")
    return Path(override) if override else home() / "runtime"


def cache_dir() -> Path:
    """Root for app-managed caches. ML/tool caches (HF_HOME, TORCH_HOME, U2NET_HOME, npm, pip,
    XDG, scratch) get routed under here by route_caches(), called at backend and provisioning
    startup, so nothing scatters into the user's ~ or the bundle.

    NAMED `appcache`, NOT `cache`, and that is load-bearing: in the packaged app home() is
    Electron's userData, which already holds Chromium's `Cache/` — and macOS APFS is
    case-INSENSITIVE by default, so `home()/cache` and `Cache/` are literally one directory
    (same inode, verified). Routing HuggingFace/torch/u2net/npm/pip/TMPDIR in there would put
    multi-GB model downloads inside a folder Chromium evicts under quota and
    `session.clearCache()` deletes outright."""
    override = os.environ.get("OPENNOLAN_CACHE_DIR")
    return Path(override) if override else home() / "appcache"


def is_packaged() -> bool:
    """True when running inside the packaged Mac app, False in a dev checkout.

    The Electron shell (desktop/main.js) sets OPENNOLAN_CODE_ROOT before it spawns
    the backend, and ONLY in a packaged build (app.isPackaged). A dev checkout leaves
    it unset. This is the single signal used to gate packaged-only behavior:
    the restricted pipeline/style catalogue and cache routing (route_caches).
    Read live from the environment (not cached) to match the rest of this module."""
    return bool(os.environ.get("OPENNOLAN_CODE_ROOT"))


def env_flag(name: str) -> bool | None:
    """Tri-state read of a boolean env var.

    None when unset or blank; True for "1"/"true"/"yes"/"on" (any case); False for
    anything else explicitly set. The tri-state matters: gates like the agent sandbox
    and cache routing distinguish "user said no" from "user said nothing"."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def route_caches() -> Path | None:
    """Point every cache/scratch write of this process TREE at cache_dir().

    Env inheritance does the real work: set once at a process-tree root, and every
    descendant — the agent CLI, Bash, tool subprocesses, and third-party libraries
    (HuggingFace, torch, rembg, npm, pip) — lands its files inside the app's own
    folders. Called explicitly from the TWO tree roots (server/app.py create_app()
    and scripts/provision.py, which Electron spawns directly); NEVER at import time.

        var                  -> destination (under cache_dir())
        ---------------------------------------------------------
        OPENNOLAN_CACHE_DIR  -> opennolan/     (app's own tool caches)
        HF_HOME              -> huggingface/   (transformers v5 dropped TRANSFORMERS_CACHE)
        TORCH_HOME           -> torch/
        U2NET_HOME           -> u2net/         (rembg)
        NPM_CONFIG_CACHE     -> npm/           (npm ignores XDG on macOS)
        PIP_CACHE_DIR        -> pip/           (pip uses ~/Library/Caches via platformdirs)
        XDG_CACHE_HOME       -> xdg/           (uv + anything XDG-aware)
        TMPDIR               -> scratch/       (see below)

    Gate: an explicit OPENNOLAN_ROUTE_CACHES wins both ways ("0" forces off even in
    the packaged app — support escape hatch); otherwise on iff is_packaged().

    Pre-set vars are respected (setdefault), with ONE exception: TMPDIR is always
    overridden when the gate is on, because launchd presets TMPDIR=/var/folders/…
    in every macOS GUI process, so setdefault would never fire. tempfile.tempdir is
    reset so an already-imported tempfile re-reads the env.

    mkdir failures propagate (fail loud): if the cache volume is unwritable the app
    cannot store projects either, and visible breakage beats silently-uncontained
    fallbacks. Returns the captured cache base when routing ran, None when gated off.
    """
    flag = env_flag("OPENNOLAN_ROUTE_CACHES")
    on = flag if flag is not None else is_packaged()
    if not on:
        return None

    # Idempotence sentinel. cache_dir() re-reads OPENNOLAN_CACHE_DIR live, and this
    # function SETS that var — so a second call (create_app() runs many times in a
    # test session; a routed parent may spawn a routed child) would recompute base
    # as .../opennolan and nest TMPDIR one level deeper on every call. The sentinel
    # records the first run's base and short-circuits every run after it — and it
    # inherits into child processes, keeping the whole tree on one base.
    routed = os.environ.get("OPENNOLAN_CACHES_ROUTED")
    if routed:
        return Path(routed)

    # Capture ONCE, before the OPENNOLAN_CACHE_DIR setdefault below.
    base = cache_dir()

    targets = {
        "OPENNOLAN_CACHE_DIR": base / "opennolan",
        "HF_HOME": base / "huggingface",
        "TORCH_HOME": base / "torch",
        "U2NET_HOME": base / "u2net",
        "NPM_CONFIG_CACHE": base / "npm",
        "PIP_CACHE_DIR": base / "pip",
        "XDG_CACHE_HOME": base / "xdg",
    }
    for var, path in targets.items():
        if not os.environ.get(var):  # blank counts as unset, matching env_flag
            path.mkdir(parents=True, exist_ok=True)
            os.environ[var] = str(path)

    scratch = base / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(scratch)
    tempfile.tempdir = None  # forget the launchd tempdir tempfile may have cached
    os.environ["OPENNOLAN_CACHES_ROUTED"] = str(base)
    return base


def sweep_scratch(base: Path, max_age_days: int = 7) -> None:
    """Best-effort housekeeping for <base>/scratch at backend startup.

    Deletes FILES whose own mtime is older than ``max_age_days``, then prunes
    empty directories bottom-up. Never removes a directory by its own mtime —
    dir mtime only reflects direct children, so a stale-looking parent can hold
    fresh nested files a live job still references. Errors are swallowed: a
    locked or vanished file must not break startup (routing itself stays loud).
    """
    scratch = base / "scratch"
    if not scratch.is_dir():
        return
    cutoff = time.time() - max_age_days * 86400
    for dirpath, _dirnames, filenames in os.walk(scratch, topdown=False):
        d = Path(dirpath)
        for fn in filenames:
            p = d / fn
            try:
                if p.lstat().st_mtime < cutoff:  # lstat: judge the entry itself, symlink or file
                    p.unlink()
            except OSError:
                pass
        if d != scratch:
            try:
                d.rmdir()  # succeeds only when empty
            except OSError:
                pass
