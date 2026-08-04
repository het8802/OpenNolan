"""Packaging contracts for the in-app agent skill plugin (OPN-41)."""

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from server.agent_runner import app_skills_plugin_dir  # noqa: E402


def test_app_skills_form_a_local_plugin():
    plugin_root = PROJECT_ROOT / ".agents" / "app"
    manifest = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text())

    assert manifest["name"] == "opennolan"
    assert (plugin_root / "skills" / "ffmpeg" / "SKILL.md").is_file()


def test_desktop_bundles_plugin_where_agent_runner_looks():
    desktop_package = json.loads((PROJECT_ROOT / "desktop" / "package.json").read_text())
    resources = desktop_package["build"]["extraResources"]
    mapping = next(item for item in resources if item.get("from") == "../.agents/app")

    resources_root = Path("/OpenNolan.app/Contents/Resources")
    packaged_backend = resources_root / "backend"
    packaged_plugin = resources_root / mapping["to"]

    assert mapping["to"] == "backend/.agents/app"
    assert app_skills_plugin_dir(packaged_backend) == packaged_plugin
