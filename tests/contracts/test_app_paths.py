"""Contract tests for lib.app_paths — the single source of truth for code vs. data roots.

The invariant that matters for packaging: with NO env vars set, everything resolves under the
repo root (dev is unchanged). With OPENNOLAN_HOME set (the packaged app), writable paths move
to that home while code_root stays independent.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from lib import app_paths

_REPO_ROOT = Path(app_paths.__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "OPENNOLAN_HOME", "OPENNOLAN_CODE_ROOT", "OPENNOLAN_PROJECTS_DIR",
        "OPENNOLAN_ENV_FILE", "OPENNOLAN_RUNTIME_DIR", "OPENNOLAN_CACHE_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def test_dev_defaults_are_repo_relative():
    assert app_paths.home() == _REPO_ROOT
    assert app_paths.code_root() == _REPO_ROOT
    assert app_paths.projects_dir() == _REPO_ROOT / "projects"
    assert app_paths.env_path() == _REPO_ROOT / ".env"
    assert app_paths.runtime_dir() == _REPO_ROOT / "runtime"
    assert app_paths.cache_dir() == _REPO_ROOT / "cache"


def test_home_relocates_writable_paths_but_not_code(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENNOLAN_HOME", str(tmp_path))
    assert app_paths.home() == tmp_path
    assert app_paths.projects_dir() == tmp_path / "projects"
    assert app_paths.env_path() == tmp_path / ".env"
    assert app_paths.runtime_dir() == tmp_path / "runtime"
    assert app_paths.cache_dir() == tmp_path / "cache"
    # code_root is independent of home — it points at the read-only bundle in prod.
    assert app_paths.code_root() == _REPO_ROOT


def test_code_root_relocates_independently(monkeypatch, tmp_path):
    bundle = tmp_path / "Resources" / "app"
    monkeypatch.setenv("OPENNOLAN_CODE_ROOT", str(bundle))
    assert app_paths.code_root() == bundle
    assert app_paths.home() == _REPO_ROOT  # unchanged


def test_explicit_overrides_win_over_home(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENNOLAN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENNOLAN_PROJECTS_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("OPENNOLAN_ENV_FILE", str(tmp_path / "secrets.env"))
    assert app_paths.projects_dir() == tmp_path / "elsewhere"
    assert app_paths.env_path() == tmp_path / "secrets.env"


def test_env_config_env_path_tracks_home(monkeypatch, tmp_path):
    # env_config binds ENV_PATH at import; re-import under a set HOME to prove it follows app_paths.
    monkeypatch.setenv("OPENNOLAN_HOME", str(tmp_path))
    from server import env_config
    importlib.reload(env_config)
    try:
        assert env_config.ENV_PATH == tmp_path / ".env"
    finally:
        monkeypatch.delenv("OPENNOLAN_HOME", raising=False)
        importlib.reload(env_config)  # restore module-level ENV_PATH for other tests


def test_agent_runner_defaults_projects_under_repo_root(tmp_path):
    from server.agent_runner import AgentRunner
    runner = AgentRunner(repo_root=tmp_path, client_factory=lambda pid: None)
    assert runner.projects_dir == tmp_path / "projects"


def test_agent_runner_honors_injected_projects_dir(tmp_path):
    from server.agent_runner import AgentRunner
    injected = tmp_path / "app-support" / "projects"
    runner = AgentRunner(repo_root=tmp_path, projects_dir=injected, client_factory=lambda pid: None)
    assert runner.projects_dir == injected
