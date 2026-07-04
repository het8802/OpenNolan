"""Regression tests for OPN-4: the bundled agent gets lost about where it runs.

Two breaks these tests lock down:
  1. The agent's `python` must resolve to the app's managed venv (which has our
     deps), not whatever Python is on the user's machine.
  2. The agent must be told the ABSOLUTE writable projects dir for artifacts,
     because its working directory is the read-only code bundle.

Covers server/agent_runner.py:
  agent_subprocess_path / agent_add_dirs / build_agent_options / _project_context
"""

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from server.agent_runner import (  # noqa: E402
    AgentRunner,
    agent_add_dirs,
    agent_subprocess_path,
)


# ── Break #2: the agent uses the app's Python, not system Python ───────────────

def test_subprocess_path_prepends_managed_venv(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNOLAN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENNOLAN_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("PATH", "/opt/runtime/bin:/usr/bin:/bin")

    entries = agent_subprocess_path().split(os.pathsep)
    venv_bin = tmp_path / "runtime" / "venv" / "bin"

    assert entries[0] == str(venv_bin), "the managed venv bin must resolve first"
    # inherited PATH (ffmpeg in runtime/bin, node, system) is preserved AFTER the venv
    assert entries[1:] == ["/opt/runtime/bin", "/usr/bin", "/bin"]


def test_subprocess_path_handles_empty_inherited_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNOLAN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENNOLAN_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("PATH", raising=False)

    assert agent_subprocess_path() == str(tmp_path / "runtime" / "venv" / "bin")


# ── add_dirs: expose the writable projects dir, but never a missing path ───────

def test_add_dirs_includes_existing_projects_dir(tmp_path):
    pdir = tmp_path / "projects"
    pdir.mkdir()
    assert agent_add_dirs(pdir) == [str(pdir)]


def test_add_dirs_skips_missing_dir_and_none(tmp_path):
    # a `--add-dir` pointing at a nonexistent path aborts the CLI launch
    assert agent_add_dirs(tmp_path / "does-not-exist") == []
    assert agent_add_dirs(None) == []


# ── Break #1: the agent writes to the absolute writable projects dir ───────────

def test_project_context_uses_absolute_projects_dir(tmp_path):
    code_root = tmp_path / "code"          # read-only bundle (agent cwd)
    data_root = tmp_path / "data" / "projects"   # writable App-Support dir
    (data_root / "vid").mkdir(parents=True)

    runner = AgentRunner(repo_root=code_root, projects_dir=data_root)
    ctx = runner._project_context("vid")

    # the agent is handed the ABSOLUTE artifacts path under the writable data root
    assert f"{data_root / 'vid'}/artifacts/" in ctx
    assert "ABSOLUTE" in ctx
    # and never pointed at a projects dir under the read-only code root
    assert str(code_root / "projects") not in ctx


def test_agent_projects_dir_matches_injected(tmp_path):
    data_root = tmp_path / "projects"
    runner = AgentRunner(repo_root=tmp_path / "code", projects_dir=data_root)
    assert runner.projects_dir == data_root


# ── the SDK options actually carry the env + workspace wiring ──────────────────

def test_build_agent_options_wires_env_and_add_dirs(tmp_path, monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    from server.agent_runner import build_agent_options

    monkeypatch.setenv("OPENNOLAN_HOME", str(tmp_path))
    monkeypatch.delenv("OPENNOLAN_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")
    pdir = tmp_path / "projects"
    pdir.mkdir()

    opts = build_agent_options(tmp_path / "code", projects_dir=pdir)

    assert opts.env["PATH"].split(os.pathsep)[0] == str(tmp_path / "runtime" / "venv" / "bin")
    assert str(pdir) in opts.add_dirs
    assert opts.cwd == str(tmp_path / "code"), "cwd stays the read-only code root"
