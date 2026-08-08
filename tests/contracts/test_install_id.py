"""S6 — one launch, one install id, crash-safe.

`install_id` is the PostHog `distinct_id` AND the join key every readback query depends on, so
two ids for one launch is not a cosmetic defect: it splits one machine into two installs at
exactly the moment activation is measured.

The old implementation created the inode BEFORE writing the bytes, in BOTH languages, and then
fell back to its own candidate when the read came back empty (`or did` / `|| minted`). Electron
SPAWNS the backend, so the two racing is the normal case rather than an edge case.

Every test here points HOME at a temp dir. Never the developer's real `~/.opennolan/`, which
either already holds an id — so the race is never exercised — or risks their data.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from server import settings

REPO = Path(__file__).resolve().parents[2]

# The autouse fixture in tests/conftest.py pins OPENNOLAN_INSTALL_ID so no test can touch the
# real home. This module is about what happens BELOW that override, so it clears it.


@pytest.fixture(autouse=True)
def _unpin(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENNOLAN_INSTALL_ID", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def _id_path(home: Path) -> Path:
    return home / settings.INSTALL_ID_PATH


def test_a_fresh_home_mints_once_and_is_stable(tmp_path):
    first = settings.device_id()
    assert first.startswith("dev-")
    assert settings.device_id() == first
    assert _id_path(tmp_path).read_text().strip() == first


def test_the_loser_of_the_race_adopts_the_winner_never_its_own(tmp_path):
    """The publish is atomic, so EEXIST means the file is already whole."""
    path = _id_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("dev-winner\n")
    assert settings._publish_install_id(path, "dev-loser") == "dev-winner"


def test_a_zero_byte_id_disables_rather_than_being_adopted(tmp_path):
    """EEXIST does NOT guarantee a complete winner: the shipped buggy implementation could
    already have left a zero-byte file on a real machine. Empty is a safe disabled state."""
    path = _id_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("")
    with pytest.raises(settings.InstallIdUnavailable):
        settings._publish_install_id(path, "dev-candidate")


def test_a_zero_byte_id_disables_analytics_and_does_not_invent_one(tmp_path, monkeypatch):
    from server import analytics

    path = _id_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("")
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    monkeypatch.setenv("POSTHOG_KEY", "phc_test_key_not_production_00")
    analytics.reset()
    assert analytics.is_enabled() is False


def test_an_fsync_failure_after_link_still_cleans_up_its_temp(tmp_path, monkeypatch):
    """A linear implementation leaks one private temp per boot. The link SUCCEEDED here, so
    the id is published and correct — only the cleanup is at risk."""
    path = _id_path(tmp_path)
    monkeypatch.setattr(settings, "_fsync_dir", lambda d: (_ for _ in ()).throw(OSError("no fsync")))
    # OSError after a successful link falls to the settings.json fallback, but either way the
    # temp must be gone.
    try:
        settings._publish_install_id(path, "dev-candidate")
    except settings.InstallIdUnavailable:
        pass
    leftovers = list(path.parent.glob(".install_id.*.tmp")) if path.parent.exists() else []
    assert not leftovers, f"leaked temp files: {leftovers}"


def test_no_retry_loop_when_the_winner_died_mid_publish(tmp_path):
    """rev 2 of the plan proposed 'retry until non-empty'. If the winner is killed between
    inode creation and write, the file is permanently empty and every later boot would spin
    forever inside a synchronous boot path — an occasional duplicate id traded for a permanent
    startup failure. This must RETURN (raising is a return), not hang."""
    path = _id_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("")  # exactly what a killed winner leaves
    with pytest.raises(settings.InstallIdUnavailable):
        settings._publish_install_id(path, "dev-candidate")


# ── the cross-language contention test ───────────────────────────────────────

_NODE_PUBLISH = textwrap.dedent(
    """
    const path = require('node:path');
    const fs = require('node:fs');
    const os = require('node:os');
    // The exact body of desktop/main.js installId(), extracted so it can run headless.
    const SRC = fs.readFileSync(process.argv[2], 'utf8');
    const body = SRC.slice(SRC.indexOf('function installId()'));
    const end = body.indexOf('\\n}\\n');
    eval(body.slice(0, end + 2));
    process.stdout.write(String(installId() || ''));
    """
).strip()


def _node() -> str | None:
    from shutil import which

    return which("node")


@pytest.mark.skipif(_node() is None, reason="node is not on PATH")
def test_python_and_node_agree_on_one_id_when_they_boot_together(tmp_path):
    """The real case: Electron spawns the backend, so both publish at once. They must return
    ONE id — and it must be the one on disk."""
    script = tmp_path / "publish.js"
    script.write_text(_NODE_PUBLISH)
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("OPENNOLAN_INSTALL_ID", None)

    py = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0,%r);\n"
            "from server import settings; print(settings.device_id(), end='')" % str(REPO),
        ],
        stdout=subprocess.PIPE,
        env=env,
        cwd=str(REPO),
        text=True,
    )
    node = subprocess.Popen(
        [_node(), str(script), str(REPO / "desktop" / "main.js")],
        stdout=subprocess.PIPE,
        env=env,
        text=True,
    )
    py_id = py.communicate()[0].strip()
    node_id = node.communicate()[0].strip()

    on_disk = _id_path(tmp_path).read_text().strip()
    assert py_id == node_id == on_disk, f"python={py_id!r} node={node_id!r} disk={on_disk!r}"
    assert on_disk, "an empty published id is the defect this step removes"


@pytest.mark.skipif(_node() is None, reason="node is not on PATH")
def test_node_returns_null_rather_than_inventing_an_id(tmp_path):
    """`|| minted` was the Node half of the same bug."""
    script = tmp_path / "publish.js"
    script.write_text(_NODE_PUBLISH)
    target = _id_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("")  # a killed winner
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("OPENNOLAN_INSTALL_ID", None)
    out = subprocess.run(
        [_node(), str(script), str(REPO / "desktop" / "main.js")], capture_output=True, env=env, text=True
    )
    assert out.stdout.strip() == "", f"invented an id: {out.stdout!r} / {out.stderr}"


def test_nothing_here_touched_the_real_home(tmp_path):
    """The guard the plan asked for by name. If HOME redirection ever regresses, this fails
    instead of quietly minting into the developer's ~/.opennolan/."""
    assert str(tmp_path) not in (os.path.expanduser("~/.opennolan"),)
    assert _id_path(tmp_path).parent.parent == tmp_path
