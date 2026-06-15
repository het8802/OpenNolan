"""Contract tests for the instagram-reels-studio pipeline (Edits-parity Phase 2).

Verifies the manifest validates + loads, every referenced stage skill exists on disk, the
Edits-parity tools are discoverable, and social-reel resolves to a real Remotion composition.
The runtime-presentation contract (render_runtime + HyperFrames mentions) is covered
automatically by tests/contracts/test_runtime_presentation_contract.py, which parametrizes
over all manifests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
MANIFEST = REPO / "pipeline_defs" / "instagram-reels-studio.yaml"
SKILLS_DIR = REPO / "skills"

STAGES = ["idea", "script", "scene_plan", "assets", "edit", "compose", "publish"]
NEW_TOOLS = ["beat_cutter", "motion_ops", "keyframe_animate", "template_apply"]


def test_manifest_loads_and_validates():
    from lib.pipeline_loader import load_pipeline

    manifest = load_pipeline("instagram-reels-studio")  # raises if schema-invalid
    assert manifest["name"] == "instagram-reels-studio"
    stage_names = [s["name"] for s in manifest["stages"]]
    assert stage_names == STAGES


def test_all_stage_director_skills_exist():
    """Every stage's referenced director skill exists on disk and is non-empty.

    Skill filenames follow the manifest's `skill:` ref (the scene_plan stage uses
    scene-director, the assets stage uses asset-director — the standard convention),
    so derive the path from the ref rather than guessing {stage}-director.md.
    """
    from lib.pipeline_loader import load_pipeline

    manifest = load_pipeline("instagram-reels-studio")
    for stage in manifest["stages"]:
        ref = stage.get("skill")
        assert ref, f"stage {stage['name']} has no skill reference"
        skill = SKILLS_DIR / f"{ref}.md"
        assert skill.exists(), f"missing stage skill: {skill}"
        assert skill.read_text().strip(), f"empty stage skill: {skill}"


def test_required_skills_reference_real_files():
    from lib.pipeline_loader import load_pipeline

    manifest = load_pipeline("instagram-reels-studio")
    for ref in manifest.get("required_skills", []):
        # meta skills + stage skills both resolve under skills/
        assert (SKILLS_DIR / f"{ref}.md").exists(), f"required_skill not found: {ref}"


def test_edit_stage_exposes_the_parity_tools():
    from lib.pipeline_loader import load_pipeline

    manifest = load_pipeline("instagram-reels-studio")
    edit = next(s for s in manifest["stages"] if s["name"] == "edit")
    available = set(edit.get("tools_available", []))
    for tool in NEW_TOOLS:
        assert tool in available, f"edit stage missing {tool}"


def test_new_tools_are_discoverable():
    from tools.tool_registry import registry

    registry.discover()
    names = {t.get("name") for tools in registry.capability_catalog().values() for t in tools}
    for tool in ["object_cutout", "restyle_video", *NEW_TOOLS]:
        assert tool in names, f"{tool} not discoverable in the registry"


def test_social_reel_renderer_family_resolves():
    from tools.video.video_compose import VideoCompose

    # social-reel must map to a real composition (no ValueError raised)
    assert VideoCompose._get_composition_id("social-reel") == "SocialReel"


def test_social_reel_composition_registered_in_remotion_root():
    root = (REPO / "remotion-composer" / "src" / "Root.tsx").read_text()
    assert 'id="SocialReel"' in root, "SocialReel composition not registered in Root.tsx"
    assert (REPO / "remotion-composer" / "src" / "SocialReel.tsx").exists()
