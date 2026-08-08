"""Test-wide safety net: never let a test write into the developer's real home dir.

`settings.device_id()` persists the install id at `~/.opennolan/install_id` (deliberately
outside every worktree — see server/settings.py). Any test that reaches it would otherwise
mint or read the developer's REAL id. `OPENNOLAN_INSTALL_ID` is the documented override, so
pinning it here both isolates the tests and exercises the override path.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _pinned_install_id(monkeypatch):
    monkeypatch.setenv("OPENNOLAN_INSTALL_ID", "test-install-id")
