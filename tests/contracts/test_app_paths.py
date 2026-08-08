"""Contract tests for lib.app_paths — the single source of truth for code vs. data roots.

The invariant that matters for packaging: with NO env vars set, everything resolves under the
repo root (dev is unchanged). With OPENNOLAN_HOME set (the packaged app), writable paths move
to that home while code_root stays independent.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from lib import app_paths

_REPO_ROOT = Path(app_paths.__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "OPENNOLAN_HOME",
        "OPENNOLAN_CODE_ROOT",
        "OPENNOLAN_PROJECTS_DIR",
        "OPENNOLAN_ENV_FILE",
        "OPENNOLAN_RUNTIME_DIR",
        "OPENNOLAN_CACHE_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def test_dev_defaults_are_repo_relative():
    assert app_paths.home() == _REPO_ROOT
    assert app_paths.code_root() == _REPO_ROOT
    assert app_paths.projects_dir() == _REPO_ROOT / "projects"
    assert app_paths.env_path() == _REPO_ROOT / ".env"
    assert app_paths.runtime_dir() == _REPO_ROOT / "runtime"
    # `appcache`, not `cache`: home() is Electron's userData in the packaged app and macOS
    # APFS is case-insensitive, so `cache` IS Chromium's quota-evicted `Cache/`.
    assert app_paths.cache_dir() == _REPO_ROOT / "appcache"


def test_home_relocates_writable_paths_but_not_code(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENNOLAN_HOME", str(tmp_path))
    assert app_paths.home() == tmp_path
    assert app_paths.projects_dir() == tmp_path / "projects"
    assert app_paths.env_path() == tmp_path / ".env"
    assert app_paths.runtime_dir() == tmp_path / "runtime"
    assert app_paths.cache_dir() == tmp_path / "appcache"
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


# ---------------------------------------------------------------------------
# route_caches / env_flag / sweep_scratch (OPN-10 cache containment)
# ---------------------------------------------------------------------------

# Every var route_caches may touch. The fixture pops them all so tests are
# hermetic even in dev shells that preset TMPDIR / XDG_CACHE_HOME.
_ROUTE_VARS = (
    "OPENNOLAN_ROUTE_CACHES",
    "OPENNOLAN_CACHES_ROUTED",
    "OPENNOLAN_CACHE_DIR",
    "HF_HOME",
    "TORCH_HOME",
    "U2NET_HOME",
    "NPM_CONFIG_CACHE",
    "PIP_CACHE_DIR",
    "XDG_CACHE_HOME",
    "TMPDIR",
)


@pytest.fixture
def route_env(tmp_path):
    """Snapshot/restore os.environ + tempfile.tempdir around route_caches tests.

    route_caches() mutates os.environ DIRECTLY — monkeypatch only reverts its own
    set/del calls, not mutations made by code under test. A TMPDIR left pointing
    at a deleted tmp_path would corrupt every later tempfile user in the session.
    Yields the expected cache base (tmp_path/appcache).
    """
    saved_env = dict(os.environ)
    saved_tempdir = tempfile.tempdir
    for var in _ROUTE_VARS:
        os.environ.pop(var, None)
    os.environ["OPENNOLAN_HOME"] = str(tmp_path)
    yield tmp_path / "appcache"
    os.environ.clear()
    os.environ.update(saved_env)
    tempfile.tempdir = saved_tempdir


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("1", True),
        ("true", True),
        ("YES", True),
        ("On", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("garbage", False),
    ],
)
def test_env_flag_tristate(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("OPN_TEST_FLAG", raising=False)
    else:
        monkeypatch.setenv("OPN_TEST_FLAG", raw)
    assert app_paths.env_flag("OPN_TEST_FLAG") is expected


def test_route_caches_noop_in_dev(route_env):
    before = dict(os.environ)
    assert app_paths.route_caches() is None
    assert dict(os.environ) == before  # env byte-identical, TMPDIR untouched
    assert not route_env.exists()  # no dirs created


def test_route_caches_on_when_packaged(route_env, tmp_path):
    os.environ["OPENNOLAN_CODE_ROOT"] = str(tmp_path / "bundle")
    assert app_paths.route_caches() == route_env


def test_route_caches_explicit_opt_in_dev(route_env):
    os.environ["OPENNOLAN_ROUTE_CACHES"] = "1"
    assert app_paths.route_caches() == route_env


def test_route_caches_explicit_off_wins_when_packaged(route_env, tmp_path):
    os.environ["OPENNOLAN_CODE_ROOT"] = str(tmp_path / "bundle")
    os.environ["OPENNOLAN_ROUTE_CACHES"] = "0"
    before = dict(os.environ)
    assert app_paths.route_caches() is None
    assert dict(os.environ) == before


def test_route_caches_sets_vars_and_creates_dirs(route_env):
    os.environ["OPENNOLAN_ROUTE_CACHES"] = "1"
    base = app_paths.route_caches()
    expected = {
        "OPENNOLAN_CACHE_DIR": base / "opennolan",
        "HF_HOME": base / "huggingface",
        "TORCH_HOME": base / "torch",
        "U2NET_HOME": base / "u2net",
        "NPM_CONFIG_CACHE": base / "npm",
        "PIP_CACHE_DIR": base / "pip",
        "XDG_CACHE_HOME": base / "xdg",
        "TMPDIR": base / "scratch",
    }
    for var, path in expected.items():
        assert os.environ[var] == str(path), var
        assert path.is_dir(), var


def test_route_caches_base_captured_once_no_nesting(route_env):
    os.environ["OPENNOLAN_ROUTE_CACHES"] = "1"
    base = app_paths.route_caches()
    # The regression: HF_HOME must sit beside opennolan/, never under it.
    assert os.environ["HF_HOME"] == str(base / "huggingface")
    assert "opennolan" not in Path(os.environ["HF_HOME"]).parts


def test_route_caches_setdefault_vs_tmpdir_override(route_env, tmp_path):
    custom_hf = tmp_path / "my-hf"
    os.environ["HF_HOME"] = str(custom_hf)
    os.environ["TMPDIR"] = "/var/folders/launchd-preset"
    os.environ["OPENNOLAN_ROUTE_CACHES"] = "1"
    base = app_paths.route_caches()
    assert os.environ["HF_HOME"] == str(custom_hf)  # setdefault respected
    assert os.environ["TMPDIR"] == str(base / "scratch")  # TMPDIR overridden anyway
    assert not (base / "huggingface").exists()  # we don't mkdir the user's dirs


def test_route_caches_retargets_tempfile(route_env):
    os.environ["OPENNOLAN_ROUTE_CACHES"] = "1"
    base = app_paths.route_caches()
    assert tempfile.gettempdir() == str(base / "scratch")


def test_route_caches_idempotent(route_env):
    os.environ["OPENNOLAN_ROUTE_CACHES"] = "1"
    app_paths.route_caches()
    first = dict(os.environ)
    app_paths.route_caches()
    assert dict(os.environ) == first  # no re-nesting, nothing drifts


def test_route_caches_mkdir_failure_raises(route_env, tmp_path):
    (tmp_path / "appcache").write_text("not a directory")
    os.environ["OPENNOLAN_ROUTE_CACHES"] = "1"
    with pytest.raises(OSError):
        app_paths.route_caches()  # fail loud by design


def test_route_caches_env_inherited_by_subprocess(route_env):
    os.environ["OPENNOLAN_ROUTE_CACHES"] = "1"
    base = app_paths.route_caches()
    out = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ['HF_HOME'])"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == str(base / "huggingface")


def _make_scratch(base: Path) -> Path:
    scratch = base / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


def _backdate(path: Path, days: float) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_sweep_scratch_removes_stale_keeps_fresh(tmp_path):
    scratch = _make_scratch(tmp_path)
    stale, fresh = scratch / "stale.mp4", scratch / "fresh.mp4"
    stale.write_text("x")
    fresh.write_text("y")
    _backdate(stale, 8)
    app_paths.sweep_scratch(tmp_path)
    assert not stale.exists()
    assert fresh.exists()


def test_sweep_scratch_prunes_empty_dirs(tmp_path):
    scratch = _make_scratch(tmp_path)
    nested = scratch / "a" / "b"
    nested.mkdir(parents=True)
    old_file = nested / "old.bin"
    old_file.write_text("x")
    _backdate(old_file, 8)
    app_paths.sweep_scratch(tmp_path)
    assert not (scratch / "a").exists()  # emptied chain pruned bottom-up
    assert scratch.is_dir()  # the scratch root itself survives


def test_sweep_scratch_never_removes_dir_with_fresh_file(tmp_path):
    scratch = _make_scratch(tmp_path)
    d = scratch / "job"
    d.mkdir()
    fresh = d / "in_use.wav"
    fresh.write_text("x")
    _backdate(d, 30)  # stale-looking DIR, fresh child
    app_paths.sweep_scratch(tmp_path)
    assert fresh.exists()
    assert d.is_dir()


def test_sweep_scratch_missing_dir_noop(tmp_path):
    app_paths.sweep_scratch(tmp_path)  # no scratch/ at all — must not raise
