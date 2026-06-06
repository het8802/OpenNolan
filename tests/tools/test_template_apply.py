"""Tests for the template system (lib/template_loader + tools/video/template_apply.py).

Edits-parity Wave 6. Pure — no ffmpeg/API. Synthetic asset files; asserts the emitted
edit_decisions validates against its real schema.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from lib.template_loader import list_templates, load_template, validate_template
from schemas.artifacts import validate_artifact
from tools.video.template_apply import TemplateApply


@pytest.fixture
def tool():
    return TemplateApply()


def _assets(tmp_path, slot_ids, ext="mp4"):
    out = {}
    for sid in slot_ids:
        p = tmp_path / f"{sid}.{ext}"
        p.write_bytes(b"x")
        out[sid] = str(p)
    return out


# --- loader ---------------------------------------------------------------

def test_starter_templates_load_and_validate():
    names = list_templates()
    assert "punchy-beat-reel" in names and "photo-kenburns-reel" in names
    for n in names:
        load_template(n)  # raises if invalid


def test_unknown_template_raises():
    with pytest.raises(FileNotFoundError):
        load_template("does-not-exist")


def test_invalid_template_rejected():
    with pytest.raises(jsonschema.ValidationError):
        validate_template({"version": "1.0"})  # missing name + slots


# --- slot coverage --------------------------------------------------------

def test_missing_slot_rejected(tool, tmp_path):
    res = tool.execute({
        "template": "punchy-beat-reel",
        "slot_assets": {"hook": str(tmp_path / "hook.mp4")},
        "output_path": str(tmp_path / "ed.json"),
    })
    assert res.success is False and "missing assets for" in res.error


def test_missing_asset_file_rejected(tool, tmp_path):
    # all slot ids present but the files don't exist
    fake = {sid: str(tmp_path / f"{sid}.mp4") for sid in ["hook", "beat1", "beat2", "beat3", "payoff"]}
    res = tool.execute({"template": "punchy-beat-reel", "slot_assets": fake, "output_path": str(tmp_path / "ed.json")})
    assert res.success is False and "not found" in res.error


# --- apply ----------------------------------------------------------------

def test_apply_emits_valid_edit_decisions(tool, tmp_path):
    assets = _assets(tmp_path, ["hook", "beat1", "beat2", "beat3", "payoff"])
    out = tmp_path / "ed.json"
    res = tool.execute({"template": "punchy-beat-reel", "slot_assets": assets, "output_path": str(out)})
    assert res.success, res.error
    assert res.data["n_cuts"] == 5
    doc = json.loads(out.read_text())
    validate_artifact("edit_decisions", doc)  # raises if invalid
    assert doc["renderer_family"] == "social-reel"
    assert doc["render_runtime"] == "ffmpeg"
    assert doc["cuts"][0]["id"] == "hook"


def test_music_attached_when_enabled(tool, tmp_path):
    assets = _assets(tmp_path, ["hook", "beat1", "beat2", "beat3", "payoff"])
    music = tmp_path / "m.mp3"
    music.write_bytes(b"m")
    out = tmp_path / "ed.json"
    res = tool.execute({"template": "punchy-beat-reel", "slot_assets": assets, "music_path": str(music), "output_path": str(out)})
    assert res.success, res.error
    doc = json.loads(out.read_text())
    assert doc["audio"]["music"]["asset_id"] == str(music)
    assert doc["audio"]["music"]["ducking"] is True


def test_music_warning_when_enabled_but_no_path(tool, tmp_path):
    assets = _assets(tmp_path, ["hook", "beat1", "beat2", "beat3", "payoff"])
    res = tool.execute({"template": "punchy-beat-reel", "slot_assets": assets, "output_path": str(tmp_path / "ed.json")})
    assert res.success
    assert any("music" in w for w in res.data.get("warnings", []))


def test_subtitles_carried_from_template(tool, tmp_path):
    assets = _assets(tmp_path, ["hook", "beat1", "beat2", "beat3", "payoff"])
    out = tmp_path / "ed.json"
    res = tool.execute({
        "template": "punchy-beat-reel", "slot_assets": assets,
        "subtitle_source": str(tmp_path / "subs.srt"), "output_path": str(out),
    })
    assert res.success, res.error
    doc = json.loads(out.read_text())
    assert doc["subtitles"]["style"] == "word-by-word"
    assert doc["subtitles"]["position"] == "bottom-center"


def test_image_slots_get_animation_transform(tool, tmp_path):
    assets = _assets(tmp_path, ["photo1", "photo2", "photo3"], ext="jpg")
    out = tmp_path / "ed.json"
    res = tool.execute({"template": "photo-kenburns-reel", "slot_assets": assets, "output_path": str(out)})
    assert res.success, res.error
    doc = json.loads(out.read_text())
    assert all("transform" in c and "animation" in c["transform"] for c in doc["cuts"])
    assert doc["cuts"][0]["transform"]["animation"] == "ken-burns-slow-zoom"


def test_template_path_override(tool, tmp_path):
    # write a minimal custom template and load it by path
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "version: '1.0'\nname: Custom\nrender_runtime: ffmpeg\nslots:\n"
        "  - slot_id: a\n    kind: video\n    seconds: 2.0\n"
    )
    asset = tmp_path / "a.mp4"
    asset.write_bytes(b"x")
    out = tmp_path / "ed.json"
    res = tool.execute({"template_path": str(custom), "slot_assets": {"a": str(asset)}, "output_path": str(out)})
    assert res.success, res.error
    assert res.data["n_cuts"] == 1


def test_registered_in_registry(tool):
    from tools.tool_registry import registry
    registry.discover()
    names = [t.get("name") for tools in registry.capability_catalog().values() for t in tools]
    assert "template_apply" in names
