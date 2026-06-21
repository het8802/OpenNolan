"""Contract tests for BYOK env config (server/env_config.py) + the /api/env read endpoint.

The read/write/list logic is unit-tested against a tmp .env (path-injected) so nothing touches the
real repo .env. The GET endpoint is smoke-tested via TestClient (read-only). The PUT endpoint is a
thin wrapper over write_env_vars (covered by the unit tests) and is NOT exercised against the real
.env here so the suite never mutates the user's keys.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server import env_config

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402


def test_read_env_file_parses_keys_and_quotes(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text('# a comment\nOPENAI_API_KEY=sk-123\nQUOTED="has space"\n\nEMPTY=\n')
    parsed = env_config.read_env_file(env)
    assert parsed["OPENAI_API_KEY"] == "sk-123"
    assert parsed["QUOTED"] == "has space"      # quotes stripped
    assert parsed["EMPTY"] == ""
    assert "# a comment" not in parsed


def test_read_env_missing_file_is_empty(tmp_path: Path):
    assert env_config.read_env_file(tmp_path / "nope.env") == {}


def test_read_strips_inline_comments_from_unquoted_values(tmp_path: Path):
    env = tmp_path / ".env"
    # single-space comment (dotenv leaks it), two-space comment, and a quoted value with a literal '#'
    env.write_text(
        "GOOGLE_API_KEY=sk-goog # Google Imagen, Cloud TTS\n"
        "FAL_KEY=sk-fal  # two-space comment\n"
        "PASSWORDISH=\"a#b#c\"  # do not strip the quoted hash\n"
    )
    parsed = env_config.read_env_file(env)
    assert parsed["GOOGLE_API_KEY"] == "sk-goog"   # single-space comment stripped
    assert parsed["FAL_KEY"] == "sk-fal"            # two-space comment stripped too
    assert parsed["PASSWORDISH"] == "a#b#c"         # quoted literal '#' preserved
    # and the panel reflects the cleaned values
    rows = {r["key"]: r["value"] for r in env_config.list_env_vars(env)}
    assert rows["GOOGLE_API_KEY"] == "sk-goog"


def test_list_env_vars_shows_menu_plus_extras(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-xyz\nMY_CUSTOM_TOKEN=abc\n")
    rows = env_config.list_env_vars(env)
    by_key = {r["key"]: r for r in rows}
    # a known key carries its current value + curated metadata
    assert by_key["OPENAI_API_KEY"]["value"] == "sk-xyz"
    assert by_key["OPENAI_API_KEY"]["secret"] is True
    # a known-but-unset key is still listed (blank) so the user can populate it
    assert by_key["ANTHROPIC_API_KEY"]["value"] == ""
    # an extra key in the file (not in the menu) is appended under "Other" + flagged secret by name
    assert by_key["MY_CUSTOM_TOKEN"]["group"] == "Other"
    assert by_key["MY_CUSTOM_TOKEN"]["secret"] is True
    assert by_key["MY_CUSTOM_TOKEN"]["value"] == "abc"


def test_write_updates_existing_preserves_others_and_comments(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("# keep me\nOPENAI_API_KEY=old\nFAL_KEY=keepme\n")
    changed = env_config.write_env_vars({"OPENAI_API_KEY": "new"}, env)
    assert changed == ["OPENAI_API_KEY"]
    parsed = env_config.read_env_file(env)
    assert parsed["OPENAI_API_KEY"] == "new"
    assert parsed["FAL_KEY"] == "keepme"        # untouched key preserved
    assert "# keep me" in env.read_text()        # comment preserved


def test_write_skips_noops_and_blank_new_keys(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=same\n")
    changed = env_config.write_env_vars(
        {"OPENAI_API_KEY": "same", "ANTHROPIC_API_KEY": ""}, env)
    assert changed == []                          # unchanged + empty-new are both skipped
    assert "ANTHROPIC_API_KEY" not in env.read_text()  # no blank line added for an untouched key


def test_write_clears_an_existing_value(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FAL_KEY=secret\n")
    changed = env_config.write_env_vars({"FAL_KEY": ""}, env)
    assert changed == ["FAL_KEY"]
    assert env_config.read_env_file(env).get("FAL_KEY", None) == ""


def test_write_creates_file_when_missing(tmp_path: Path):
    env = tmp_path / ".env"
    changed = env_config.write_env_vars({"OPENAI_API_KEY": "sk-1"}, env)
    assert changed == ["OPENAI_API_KEY"]
    assert env.exists()
    assert env_config.read_env_file(env)["OPENAI_API_KEY"] == "sk-1"


def test_write_keeps_simple_values_unquoted_but_quotes_special(tmp_path: Path):
    env = tmp_path / ".env"
    env.touch()
    env_config.write_env_vars({"OPENAI_API_KEY": "sk-abc123", "WEIRD": "has space #x"}, env)
    text = env.read_text()
    assert "OPENAI_API_KEY=sk-abc123" in text          # clean, unquoted (matches file style)
    assert "WEIRD='has space #x'" in text              # quoted so the # can't leak into a comment
    # both still round-trip to their exact values
    parsed = env_config.read_env_file(env)
    assert parsed["OPENAI_API_KEY"] == "sk-abc123"
    assert parsed["WEIRD"] == "has space #x"


def test_write_rejects_bad_names_and_newline_injection(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("OK=1\n")
    with pytest.raises(ValueError):
        env_config.write_env_vars({"bad key": "x"}, env)         # space in name
    with pytest.raises(ValueError):
        env_config.write_env_vars({"X": "a\nINJECTED=evil"}, env)  # newline injection
    # the file is untouched by a rejected write
    assert env.read_text() == "OK=1\n"


def test_get_env_endpoint_returns_menu(tmp_path: Path):
    app = create_app(projects_dir=tmp_path / "projects")
    client = TestClient(app)
    resp = client.get("/api/env")
    assert resp.status_code == 200
    data = resp.json()
    assert "path" in data and isinstance(data["vars"], list)
    keys = {v["key"] for v in data["vars"]}
    assert "ANTHROPIC_API_KEY" in keys            # curated menu is present
    for v in data["vars"]:                         # shape contract
        assert {"key", "label", "group", "secret", "value"} <= set(v)
