"""Contract tests for the New Project "Style" picker.

Covers the enabling pieces:
  - styles.playbook_loader merges a writable USER styles dir with the
    built-ins; the render path loads by name through this one function.
  - lib.project.create_project stores the chosen ``style`` on the manifest.
"""

import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.project import create_project, read_project_manifest
from styles.playbook_loader import list_playbooks, load_playbook


@pytest.fixture
def user_styles(tmp_path, monkeypatch):
    """Point the user styles dir at a temp dir (app_paths reads the env live)."""
    d = tmp_path / "user_styles"
    d.mkdir()
    monkeypatch.setenv("OPENNOLAN_USER_STYLES_DIR", str(d))
    return d


def _write_valid_style(dest_dir: Path, slug: str, name: str = "Custom") -> None:
    """Dump a KNOWN-valid built-in under a new slug so we don't hand-maintain a
    schema-valid fixture (the schema is what we're relying on, not testing)."""
    pb = load_playbook("clean-professional")
    pb["identity"]["name"] = name
    (dest_dir / f"{slug}.yaml").write_text(yaml.safe_dump(pb))


# --- playbook loader: user styles merge + fallback -------------------------

def test_user_style_is_listed_and_loadable(user_styles):
    _write_valid_style(user_styles, "my-custom", name="My Custom")
    names = list_playbooks(packaged=False)
    assert "my-custom" in names
    # built-ins still present
    assert "clean-professional" in names
    loaded = load_playbook("my-custom")
    assert loaded["identity"]["name"] == "My Custom"


def test_user_styles_available_even_when_packaged(user_styles):
    # The packaged allowlist trims BUILT-INS only; a user style is always offered.
    _write_valid_style(user_styles, "packaged-ok")
    names = list_playbooks(packaged=True)
    assert "packaged-ok" in names
    assert "clean-professional" not in names  # trimmed by the packaged allowlist


def test_builtin_wins_on_name_collision(user_styles):
    # A user file named like a built-in must not shadow it or double-list it.
    _write_valid_style(user_styles, "clean-professional", name="IMPOSTER")
    names = list_playbooks(packaged=False)
    assert names.count("clean-professional") == 1
    assert load_playbook("clean-professional")["identity"]["name"] != "IMPOSTER"


def test_load_unknown_style_raises(user_styles):
    with pytest.raises(FileNotFoundError):
        load_playbook("does-not-exist-anywhere")


# --- project manifest: style ------------------------------------------------

def test_create_project_stores_style(tmp_path):
    projects = tmp_path / "projects"
    m = create_project(projects, "Launch Reel", "animated-explainer", style="clean-professional")
    assert m["style"] == "clean-professional"
    on_disk = read_project_manifest(projects, "launch-reel")
    assert on_disk["style"] == "clean-professional"


def test_create_project_default_style_is_none(tmp_path):
    projects = tmp_path / "projects"
    m = create_project(projects, "No Style", "animated-explainer")
    assert m["style"] is None
