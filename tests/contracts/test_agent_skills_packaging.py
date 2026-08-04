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


def test_every_coding_skill_is_symlinked_for_claude_code():
    """Codex reads .agents/skills directly; Claude Code only reads .claude/skills.

    So each coding skill needs a symlink, and adding one without the other silently
    hides it from one of the two agents. Merging main's `plan-then-architecture`
    did exactly that, which is why this test exists.
    """
    real = {d.name for d in (PROJECT_ROOT / ".agents" / "skills").iterdir() if d.is_dir()}
    links_dir = PROJECT_ROOT / ".claude" / "skills"
    links = {d.name for d in links_dir.iterdir()}

    assert real == links, f"missing symlinks: {sorted(real - links)}; stale: {sorted(links - real)}"
    for name in links:
        link = links_dir / name
        assert link.is_symlink(), f".claude/skills/{name} must be a symlink, not a copy"
        assert link.resolve() == (PROJECT_ROOT / ".agents" / "skills" / name).resolve()


def test_coding_skills_are_not_in_the_app_plugin():
    """The whole point of OPN-41: the video agent must not see coding skills."""
    coding = {d.name for d in (PROJECT_ROOT / ".agents" / "skills").iterdir() if d.is_dir()}
    app = {d.name for d in (PROJECT_ROOT / ".agents" / "app" / "skills").iterdir() if d.is_dir()}

    assert not (coding & app), f"coding skills leaked into the app plugin: {sorted(coding & app)}"


def test_desktop_bundles_plugin_where_agent_runner_looks():
    desktop_package = json.loads((PROJECT_ROOT / "desktop" / "package.json").read_text())
    resources = desktop_package["build"]["extraResources"]
    mapping = next(item for item in resources if item.get("from") == "../.agents/app")

    resources_root = Path("/OpenNolan.app/Contents/Resources")
    packaged_backend = resources_root / "backend"
    packaged_plugin = resources_root / mapping["to"]

    assert mapping["to"] == "backend/.agents/app"
    assert app_skills_plugin_dir(packaged_backend) == packaged_plugin
