"""Wiring tests for OPN-10 cache routing at the two process-tree roots.

route_caches() must run at BOTH entrypoints Electron spawns — the backend
(server.app.create_app) and scripts/provision.py — and NEVER at import time
(several test files monkeypatch OPENNOLAN_CACHE_DIR and rely on that).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib import app_paths  # noqa: E402

_ROUTE_VARS = (
    "OPENNOLAN_ROUTE_CACHES", "OPENNOLAN_CACHES_ROUTED", "OPENNOLAN_CACHE_DIR",
    "HF_HOME", "TORCH_HOME", "U2NET_HOME", "NPM_CONFIG_CACHE", "PIP_CACHE_DIR",
    "XDG_CACHE_HOME", "TMPDIR",
)


@pytest.fixture
def route_env(tmp_path):
    """Snapshot/restore os.environ + tempfile.tempdir (create_app mutates both).

    Also the safety rail: OPENNOLAN_HOME is pointed at tmp_path so create_app's
    load_env() resolves a scratch .env — NEVER the repo .env with real BYOK keys
    (an auth-gate test once spawned a real billable agent turn that way).
    """
    saved_env = dict(os.environ)
    saved_tempdir = tempfile.tempdir
    for var in _ROUTE_VARS:
        os.environ.pop(var, None)
    os.environ["OPENNOLAN_HOME"] = str(tmp_path)
    yield tmp_path / "cache"
    os.environ.clear()
    os.environ.update(saved_env)
    tempfile.tempdir = saved_tempdir


def test_provision_main_routes_caches(tmp_path):
    """scripts/provision.py --doctor under the routing gate creates the routed
    dirs — stdlib-only, end to end, in a child process. Doubles as the
    never-at-import-time canary: the routing here comes from main()'s explicit
    call, and every other test in the suite would break if import did it."""
    env = {k: v for k, v in os.environ.items() if k not in _ROUTE_VARS}
    env["OPENNOLAN_ROUTE_CACHES"] = "1"
    env["OPENNOLAN_HOME"] = str(tmp_path)
    out = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "provision.py"), "--doctor"],
        env=env, capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    assert out.returncode == 0, out.stderr
    assert (tmp_path / "cache" / "huggingface").is_dir()
    assert (tmp_path / "cache" / "npm").is_dir()
    assert (tmp_path / "cache" / "scratch").is_dir()


def test_provision_main_respects_gate_off(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in _ROUTE_VARS}
    env["OPENNOLAN_HOME"] = str(tmp_path)  # dev: no gate vars set
    out = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "provision.py"), "--doctor"],
        env=env, capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    assert out.returncode == 0, out.stderr
    assert not (tmp_path / "cache").exists()


def test_create_app_calls_route_caches(route_env):
    # importorskip on server.app itself: skips on ANY missing transitive dep
    # (fastapi, jsonschema, ...) so the file still collects under bare pythons.
    create_app = pytest.importorskip("server.app").create_app

    os.environ["OPENNOLAN_ROUTE_CACHES"] = "1"
    create_app(projects_dir=route_env.parent / "projects")
    assert os.environ.get("HF_HOME") == str(route_env / "huggingface")
    assert os.environ.get("TMPDIR") == str(route_env / "scratch")


def test_create_app_dev_default_is_noop(route_env):
    create_app = pytest.importorskip("server.app").create_app

    create_app(projects_dir=route_env.parent / "projects")
    assert "HF_HOME" not in os.environ
    assert not route_env.exists()
