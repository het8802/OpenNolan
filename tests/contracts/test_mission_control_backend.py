"""Contract tests for the Mission Control enabling backend.

Covers the three pieces added/changed to make a UI (and a headless agent
runner) safe to build on top of the pipeline state:

1. ``lib.atomic_io.atomic_write_json`` — the half-write race fix. A concurrent
   reader must see either the old file or the fully written new one, never a
   truncated partial; a failed write must leave the existing file intact and
   leak no temp files.
2. ``lib.checkpoint`` — checkpoints write to ``projects/<id>/`` (the pinned
   location, NOT ``pipelines/<id>/``), round-trip, and expose a CLI so a
   headless agent need not hand-compose ``python -c`` snippets.
3. ``lib.project`` — the ``project.json`` manifest: scaffolding, collision
   handling (the bug both behavioral spikes hit), junk filtering in
   ``list_projects``, and pipeline_type recovery for an empty project.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import lib.checkpoint as checkpoint
from lib.atomic_io import atomic_write_json
from lib.checkpoint import (
    get_completed_stages,
    get_next_stage,
    read_checkpoint,
    write_checkpoint,
)
from lib.project import (
    ProjectExistsError,
    create_project,
    get_project_pipeline_type,
    list_projects,
    read_project_manifest,
    sanitize_filename,
    slugify,
)

PIPELINE = "animated-explainer"


# --------------------------------------------------------------------------
# atomic_write_json — the half-write regression guard
# --------------------------------------------------------------------------

def test_atomic_write_creates_valid_json_and_no_tmp(tmp_path):
    p = tmp_path / "nested" / "x.json"
    atomic_write_json(p, {"a": 1, "b": [1, 2, 3]})
    assert json.loads(p.read_text()) == {"a": 1, "b": [1, 2, 3]}
    # No leftover temp files in the target directory.
    assert list(p.parent.glob("*.tmp")) == []


def test_atomic_write_overwrites_existing(tmp_path):
    p = tmp_path / "x.json"
    atomic_write_json(p, {"v": 1})
    atomic_write_json(p, {"v": 2})
    assert json.loads(p.read_text()) == {"v": 2}


def test_failed_write_leaves_original_intact_and_no_tmp_leak(tmp_path):
    """The regression: a write that dies mid-serialization must not corrupt
    the existing file (the reader keeps seeing the last-good version) and
    must not leave a dangling temp file."""
    p = tmp_path / "x.json"
    atomic_write_json(p, {"good": True})

    # A set isn't JSON-serializable -> json.dump raises after the temp file
    # is opened but before os.replace runs.
    with pytest.raises(TypeError):
        atomic_write_json(p, {"bad": {1, 2, 3}})

    assert json.loads(p.read_text()) == {"good": True}  # untouched
    assert list(p.parent.glob("*.tmp")) == []  # no leak


# --------------------------------------------------------------------------
# checkpoint write location + round-trip + CLI
# --------------------------------------------------------------------------

def test_checkpoint_writes_under_projects_dir(tmp_path):
    projects = tmp_path / "projects"
    path = write_checkpoint(
        projects, "sky", "research", "in_progress", {}, pipeline_type=PIPELINE
    )
    # Pinned location: projects/<id>/checkpoint_<stage>.json (NOT pipelines/).
    assert path == projects / "sky" / "checkpoint_research.json"
    assert path.exists()
    assert list(path.parent.glob("*.tmp")) == []  # atomic write, no leak

    cp = read_checkpoint(projects, "sky", "research")
    assert cp["stage"] == "research"
    assert cp["status"] == "in_progress"
    assert cp["pipeline_type"] == PIPELINE


def test_completed_and_next_stage(tmp_path):
    projects = tmp_path / "projects"
    write_checkpoint(projects, "sky", "research", "in_progress", {}, pipeline_type=PIPELINE)
    # in_progress is not completed
    assert get_completed_stages(projects, "sky", PIPELINE) == []
    # first stage of animated-explainer is research
    assert get_next_stage(projects, "sky", PIPELINE) == "research"


def test_checkpoint_cli_write_read_next(tmp_path, capsys):
    projects = tmp_path / "projects"
    rc = checkpoint._cli([
        "write", "--projects-dir", str(projects), "--project-id", "sky",
        "--stage", "research", "--status", "in_progress", "--pipeline-type", PIPELINE,
    ])
    assert rc == 0
    assert (projects / "sky" / "checkpoint_research.json").exists()

    rc = checkpoint._cli([
        "read", "--projects-dir", str(projects), "--project-id", "sky", "--stage", "research",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"status": "in_progress"' in out

    rc = checkpoint._cli([
        "next", "--projects-dir", str(projects), "--project-id", "sky", "--pipeline-type", PIPELINE,
    ])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "research"

    # Reading a stage with no checkpoint returns exit code 1.
    rc = checkpoint._cli([
        "read", "--projects-dir", str(projects), "--project-id", "sky", "--stage", "script",
    ])
    assert rc == 1


def test_checkpoint_cli_write_with_artifacts_file(tmp_path):
    projects = tmp_path / "projects"
    art = tmp_path / "artifacts.json"
    # in_progress doesn't require a canonical artifact, so an empty mapping is fine.
    art.write_text(json.dumps({}))
    rc = checkpoint._cli([
        "write", "--projects-dir", str(projects), "--project-id", "sky",
        "--stage", "research", "--status", "in_progress",
        "--pipeline-type", PIPELINE, "--artifacts-file", str(art),
    ])
    assert rc == 0
    assert read_checkpoint(projects, "sky", "research")["status"] == "in_progress"


# --------------------------------------------------------------------------
# project manifest
# --------------------------------------------------------------------------

def test_create_project_scaffolds_dirs_and_manifest(tmp_path):
    projects = tmp_path / "projects"
    m = create_project(
        projects, "Why The Sky Is Blue", PIPELINE, created_at="2026-05-30T00:00:00+00:00"
    )
    assert m["project_id"] == "why-the-sky-is-blue"
    assert m["pipeline_type"] == PIPELINE

    pdir = projects / "why-the-sky-is-blue"
    assert (pdir / "project.json").exists()
    assert (pdir / "artifacts").is_dir()
    assert (pdir / "renders").is_dir()
    for sub in ("images", "video", "audio", "music"):
        assert (pdir / "assets" / sub).is_dir()


def test_create_project_collision_raises(tmp_path):
    projects = tmp_path / "projects"
    create_project(projects, "Why The Sky Is Blue", PIPELINE)
    # Same title -> same slug -> must NOT silently overwrite (the spike bug).
    with pytest.raises(ProjectExistsError):
        create_project(projects, "why the sky is blue!!!", PIPELINE)


def test_list_projects_filters_junk(tmp_path):
    projects = tmp_path / "projects"
    create_project(projects, "Real One", PIPELINE)
    # Junk that must be excluded:
    (projects / "_analysis").mkdir()                 # scratch dir, no manifest
    (projects / "legacy-no-manifest").mkdir()        # pre-manifest project
    (projects / ".DS_Store").write_text("noise")     # stray file

    ids = [p["project_id"] for p in list_projects(projects)]
    assert ids == ["real-one"]


def test_pipeline_type_recovered_from_manifest_without_checkpoint(tmp_path):
    projects = tmp_path / "projects"
    create_project(projects, "Empty Project", PIPELINE)
    # No checkpoint written yet — manifest is the only source of pipeline_type.
    assert get_project_pipeline_type(projects, "empty-project") == PIPELINE


def test_read_manifest_none_for_non_project(tmp_path):
    projects = tmp_path / "projects"
    (projects / "not-a-project").mkdir(parents=True)
    assert read_project_manifest(projects, "not-a-project") is None


@pytest.mark.parametrize("name,expected", [
    ("Hello, World!", "hello-world"),
    ("  Spaced   Out  ", "spaced-out"),
    ("already-kebab", "already-kebab"),
    ("UPPER_snake.Case", "upper-snake-case"),
])
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_slugify_rejects_empty(tmp_path):
    with pytest.raises(ValueError):
        slugify("!!!")


@pytest.mark.parametrize("raw,expected", [
    ("photo.png", "photo.png"),
    ("../../etc/passwd", "passwd"),
    ("/abs/path/clip.mp4", "clip.mp4"),
    ("sub/dir/voice.mp3", "voice.mp3"),
])
def test_sanitize_filename_strips_traversal(raw, expected):
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("bad", [
    "", ".", "..", "   ", "/", "foo\x00.png",
    # On POSIX, backslashes aren't path separators, so a Windows-style path
    # arrives as one name containing "\" — safer to reject than to mis-parse.
    "..\\..\\windows\\evil.exe",
])
def test_sanitize_filename_rejects_unsafe(bad):
    with pytest.raises(ValueError):
        sanitize_filename(bad)
