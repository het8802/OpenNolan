"""Packaged-app catalogue restrictions: ONE pipeline, TWO styles.

A dev checkout exposes every pipeline and style. The packaged Mac app — detected
via the OPENNOLAN_PACKAGED env var the Electron shell sets — restricts pipelines
to `instagram-fast-reel` and styles to the two the pipeline is built around
(`anthropic-editorial-animated`, `greg-isenberg-product-explainer`). The gate is
purely that env var, so these tests toggle it with monkeypatch.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib import app_paths, playbook_generator
from lib.pipeline_loader import PACKAGED_PIPELINES, list_pipelines
from styles.playbook_loader import PACKAGED_PLAYBOOKS, list_playbooks


# --- packaged detection ----------------------------------------------------


def test_is_packaged_reads_the_packaged_flag(monkeypatch):
    monkeypatch.delenv("OPENNOLAN_PACKAGED", raising=False)
    assert app_paths.is_packaged() is False
    monkeypatch.setenv("OPENNOLAN_PACKAGED", "1")
    assert app_paths.is_packaged() is True
    monkeypatch.setenv("OPENNOLAN_PACKAGED", "0")  # explicit off wins, like every other gate here
    assert app_paths.is_packaged() is False


def test_code_root_alone_is_not_packaged(monkeypatch):
    """The regression this var exists for: desktop/main.js provisionEnv() sets OPENNOLAN_CODE_ROOT
    UNCONDITIONALLY (provision.py needs code_root() to find its requirement files in dev too), so
    reading it as the packaged signal made every dev provision think it was the .app."""
    monkeypatch.delenv("OPENNOLAN_PACKAGED", raising=False)
    monkeypatch.setenv("OPENNOLAN_CODE_ROOT", str(PROJECT_ROOT))
    assert app_paths.is_packaged() is False


# --- pipelines -------------------------------------------------------------


def test_pipelines_unrestricted_in_dev(monkeypatch):
    monkeypatch.delenv("OPENNOLAN_PACKAGED", raising=False)
    names = list_pipelines()
    assert "instagram-fast-reel" in names
    assert "talking-head" in names  # dev sees the whole catalogue
    assert len(names) > 1


def test_pipelines_restricted_when_packaged(monkeypatch):
    monkeypatch.setenv("OPENNOLAN_PACKAGED", "1")
    assert sorted(list_pipelines()) == sorted(PACKAGED_PIPELINES)
    assert "talking-head" not in list_pipelines()  # a curated set, not the dev catalogue


def test_pipelines_explicit_packaged_override():
    assert sorted(list_pipelines(packaged=True)) == sorted(PACKAGED_PIPELINES)
    assert "talking-head" in list_pipelines(packaged=False)


def test_every_packaged_pipeline_manifest_actually_loads():
    """An unloadable name here is a dead pipeline in the shipped app: the agent is told
    to pick it, then fails on the manifest."""
    from lib.pipeline_loader import load_pipeline

    for name in PACKAGED_PIPELINES:
        assert load_pipeline(name)["name"] == name


# --- styles ----------------------------------------------------------------


def test_styles_unrestricted_in_dev(monkeypatch):
    monkeypatch.delenv("OPENNOLAN_PACKAGED", raising=False)
    names = set(list_playbooks())
    assert set(PACKAGED_PLAYBOOKS).issubset(names)
    assert len(names) > 2


def test_styles_restricted_when_packaged(monkeypatch):
    monkeypatch.setenv("OPENNOLAN_PACKAGED", "1")
    assert sorted(list_playbooks()) == sorted(PACKAGED_PLAYBOOKS)
    # both enumerators must agree, or the agent could discover a style via the other
    assert set(playbook_generator.list_playbooks()) == set(PACKAGED_PLAYBOOKS)


def test_styles_explicit_packaged_override():
    assert sorted(list_playbooks(packaged=True)) == sorted(PACKAGED_PLAYBOOKS)
    assert len(list_playbooks(packaged=False)) > 2
    assert sorted(playbook_generator.list_playbooks(packaged=True)) == sorted(PACKAGED_PLAYBOOKS)


def test_keeper_styles_are_exactly_the_packaged_pipelines_playbooks():
    """A packaged pipeline whose recommended playbook is filtered out silently falls back
    to another pipeline's motion grammar — which is the bug this pairing prevents."""
    from lib.pipeline_loader import PACKAGED_PIPELINES, load_pipeline

    recommended = {name for p in PACKAGED_PIPELINES for name in load_pipeline(p)["compatible_playbooks"]["recommended"]}
    assert set(PACKAGED_PLAYBOOKS) == recommended
