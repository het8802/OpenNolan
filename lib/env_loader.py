"""Environment variable loader for OpenNolan.

Loads .env file and provides typed access to environment configuration.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def load_env(project_root: Optional[Path] = None) -> None:
    """Load the BYOK .env into the environment.

    With no argument, resolves the .env via `lib.app_paths` (repo `.env` in dev; the
    App-Support `.env` in the packaged app), so tools and the agent subprocess inherit
    keys the user saved through the BYOK panel. An explicit `project_root` still loads
    `<project_root>/.env` for callers that want a specific file.
    """
    if project_root is None:
        from lib import app_paths

        env_path = app_paths.env_path()
    else:
        env_path = Path(project_root) / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get an environment variable with optional default."""
    return os.environ.get(key, default)


def require_env(key: str) -> str:
    """Get a required environment variable. Raises if missing."""
    value = os.environ.get(key)
    if value is None:
        raise EnvironmentError(f"Required environment variable {key!r} is not set")
    return value
