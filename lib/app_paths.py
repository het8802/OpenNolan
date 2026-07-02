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
    OPENNOLAN_CACHE_DIR     -> cache_dir()            (default: <home>/cache)

Resolution reads os.environ on each call (cheap, and lets a subprocess that sets these
vars be picked up), so this is deliberately NOT cached at import time.
"""

from __future__ import annotations

import os
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


def runtime_dir() -> Path:
    """Where the managed Python venv + downloaded ffmpeg + capability packs live (bootstrapper)."""
    override = os.environ.get("OPENNOLAN_RUNTIME_DIR")
    return Path(override) if override else home() / "runtime"


def cache_dir() -> Path:
    """Root for app-managed caches. ML frameworks (HF_HOME/TRANSFORMERS_CACHE/piper/rembg) get
    routed under here by the bootstrapper so nothing scatters into the user's ~ or the bundle."""
    override = os.environ.get("OPENNOLAN_CACHE_DIR")
    return Path(override) if override else home() / "cache"
