"""Packaged-app catalogue restrictions: ONE pipeline, TWO styles.

A dev checkout exposes every pipeline and style. The packaged Mac app — detected
via the OPENNOLAN_CODE_ROOT env var the Electron shell sets — restricts pipelines
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


def test_is_packaged_reads_code_root(monkeypatch):
    monkeypatch.delenv("OPENNOLAN_CODE_ROOT", raising=False)
    assert app_paths.is_packaged() is False
    monkeypatch.setenv("OPENNOLAN_CODE_ROOT", "/some/bundle/backend")
    assert app_paths.is_packaged() is True


# --- pipelines -------------------------------------------------------------


def test_pipelines_unrestricted_in_dev(monkeypatch):
    monkeypatch.delenv("OPENNOLAN_CODE_ROOT", raising=False)
    names = list_pipelines()
    assert "instagram-fast-reel" in names
    assert "talking-head" in names  # dev sees the whole catalogue
    assert len(names) > 1


def test_pipelines_restricted_when_packaged(monkeypatch):
    monkeypatch.setenv("OPENNOLAN_CODE_ROOT", str(PROJECT_ROOT))
    assert list_pipelines() == list(PACKAGED_PIPELINES) == ["instagram-fast-reel"]


def test_pipelines_explicit_packaged_override():
    assert list_pipelines(packaged=True) == ["instagram-fast-reel"]
    assert "talking-head" in list_pipelines(packaged=False)


# --- styles ----------------------------------------------------------------


def test_styles_unrestricted_in_dev(monkeypatch):
    monkeypatch.delenv("OPENNOLAN_CODE_ROOT", raising=False)
    names = set(list_playbooks())
    assert set(PACKAGED_PLAYBOOKS).issubset(names)
    assert len(names) > 2


def test_styles_restricted_when_packaged(monkeypatch):
    monkeypatch.setenv("OPENNOLAN_CODE_ROOT", str(PROJECT_ROOT))
    assert sorted(list_playbooks()) == sorted(PACKAGED_PLAYBOOKS)
    # both enumerators must agree, or the agent could discover a style via the other
    assert set(playbook_generator.list_playbooks()) == set(PACKAGED_PLAYBOOKS)


def test_styles_explicit_packaged_override():
    assert sorted(list_playbooks(packaged=True)) == sorted(PACKAGED_PLAYBOOKS)
    assert len(list_playbooks(packaged=False)) > 2
    assert sorted(playbook_generator.list_playbooks(packaged=True)) == sorted(PACKAGED_PLAYBOOKS)


def test_keeper_styles_are_the_two_pipeline_playbooks():
    # Guard the intent: the two keepers ARE the recommended playbooks in the
    # single packaged pipeline's manifest.
    from lib.pipeline_loader import load_pipeline

    manifest = load_pipeline("instagram-fast-reel")
    recommended = set(manifest["compatible_playbooks"]["recommended"])
    assert set(PACKAGED_PLAYBOOKS) == recommended
