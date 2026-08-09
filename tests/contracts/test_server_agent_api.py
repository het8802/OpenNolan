"""Contract tests for the agent endpoints (chat + confirm).

The live streaming path needs real auth, so these cover the wiring we CAN
assert deterministically: project 404, the auth-gated 503 with setup guidance,
and the confirm endpoint's runner handling.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from lib.project import create_project
from server.agent_runner import AgentRunner
from server.app import create_app

PIPELINE = "animated-explainer"
STUB_CAPS = {"composition_runtimes": {}, "capabilities": [], "setup_offers": [], "runtime_warnings": []}


def _client(tmp_path, agent_runner=None):
    app = create_app(
        projects_dir=tmp_path / "projects",
        capabilities_provider=lambda: STUB_CAPS,
        agent_runner=agent_runner,
    )
    return TestClient(app)


def test_chat_unknown_project_404(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = _client(tmp_path).post("/api/projects/ghost/chat", json={"message": "hi"})
    assert r.status_code == 404


def test_chat_without_auth_returns_503_with_guidance(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Force the no-CLI path so the auth-gated 503 is exercised even on a dev
    # machine that has the `claude` CLI installed (which would otherwise pass).
    monkeypatch.setattr("server.agent_runner.claude_cli_available", lambda: False)
    create_project(tmp_path / "projects", "Sky", PIPELINE)
    r = _client(tmp_path).post("/api/projects/sky/chat", json={"message": "hi"})
    assert r.status_code == 503
    assert "setup-token" in r.json()["detail"]


def test_confirm_without_runner_409(tmp_path):
    r = _client(tmp_path).post("/api/projects/sky/agent/confirm", json={"confirm_id": "x", "approved": True})
    assert r.status_code == 409


def test_confirm_unknown_id_returns_resolved_false(tmp_path):
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    r = _client(tmp_path, agent_runner=runner).post(
        "/api/projects/sky/agent/confirm", json={"confirm_id": "nope", "approved": True}
    )
    assert r.status_code == 200
    assert r.json() == {"resolved": False}


def test_answer_without_runner_409(tmp_path):
    r = _client(tmp_path).post("/api/projects/sky/agent/answer", json={"question_id": "x", "answer": "yes"})
    assert r.status_code == 409


def test_answer_unknown_id_returns_resolved_false(tmp_path):
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    r = _client(tmp_path, agent_runner=runner).post(
        "/api/projects/sky/agent/answer", json={"question_id": "nope", "answer": "a"}
    )
    assert r.status_code == 200
    assert r.json() == {"resolved": False}


def test_stop_without_runner_409(tmp_path):
    r = _client(tmp_path).post("/api/projects/sky/agent/stop")
    assert r.status_code == 409


def test_stop_no_live_session_returns_stopped_false(tmp_path):
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    r = _client(tmp_path, agent_runner=runner).post("/api/projects/sky/agent/stop")
    assert r.status_code == 200
    assert r.json() == {"stopped": False}


def test_stop_interrupts_live_client():
    import asyncio

    class FakeClient:
        def __init__(self):
            self.interrupted = False

        async def interrupt(self):
            self.interrupted = True

    fake = FakeClient()
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    runner._clients["proj"] = fake
    assert asyncio.run(runner.interrupt("proj")) is True
    assert fake.interrupted is True
    # No live client for another project -> False, no crash.
    assert asyncio.run(runner.interrupt("other")) is False


# ── request_api_key: the agent's BYOK-key prompt (OPN-5) ─────────────────────


def test_provide_key_without_runner_409(tmp_path):
    r = _client(tmp_path).post(
        "/api/projects/sky/agent/provide-key",
        json={"key_request_id": "x", "env_var": "GOOGLE_API_KEY", "value": "k"},
    )
    assert r.status_code == 409


def test_provide_key_skip_unknown_id_resolved_false(tmp_path):
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    r = _client(tmp_path, agent_runner=runner).post(
        "/api/projects/sky/agent/provide-key",
        json={"key_request_id": "nope", "env_var": "GOOGLE_API_KEY", "skipped": True},
    )
    assert r.status_code == 200
    assert r.json() == {"resolved": False, "saved": False}


def test_provide_key_empty_value_400(tmp_path):
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    r = _client(tmp_path, agent_runner=runner).post(
        "/api/projects/sky/agent/provide-key",
        json={"key_request_id": "x", "env_var": "GOOGLE_API_KEY", "value": "   "},
    )
    assert r.status_code == 400


def test_provide_key_saves_env_then_resolves(tmp_path, monkeypatch):
    """POST /provide-key persists the key (write + reload), THEN unblocks the waiting tool.
    The write/reload are faked so the suite never touches the user's real .env."""
    import asyncio

    from server import env_config

    calls = {"writes": None, "reloaded": False}
    monkeypatch.setattr(
        env_config, "write_env_vars", lambda updates: (calls.__setitem__("writes", dict(updates)), list(updates))[1]
    )
    monkeypatch.setattr(env_config, "reload_env", lambda: calls.__setitem__("reloaded", True))

    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    runner._key_requests["sky:k1"] = fut

    r = _client(tmp_path, agent_runner=runner).post(
        "/api/projects/sky/agent/provide-key",
        json={"key_request_id": "sky:k1", "env_var": "GOOGLE_API_KEY", "value": "sk-goog-123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resolved"] is True and body["saved"] is True
    assert calls["writes"] == {"GOOGLE_API_KEY": "sk-goog-123"}
    assert calls["reloaded"] is True
    assert fut.done() and fut.result() is True
    loop.close()


def test_provide_key_invalid_env_var_name_400(tmp_path, monkeypatch):
    from server import env_config

    # A bad var name makes write_env_vars raise ValueError -> 400 (and the future stays pending).
    def _raise(_updates):
        raise ValueError("Invalid variable name: 'bad name'")

    monkeypatch.setattr(env_config, "write_env_vars", _raise)
    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    r = _client(tmp_path, agent_runner=runner).post(
        "/api/projects/sky/agent/provide-key",
        json={"key_request_id": "sky:k1", "env_var": "bad name", "value": "v"},
    )
    assert r.status_code == 400


async def _drive_key_request(runner, project_id, env_var, provider, reason):
    """Start _request_api_key, wait until it has emitted + registered its future, and return the
    (task, emitted_event) so a test can resolve it."""
    import asyncio

    events: list = []

    async def emit(evt):
        events.append(evt)

    runner._emit[project_id] = emit
    task = asyncio.create_task(runner._request_api_key(project_id, env_var, provider, reason))
    for _ in range(50):
        if events:
            break
        await asyncio.sleep(0)
    return task, (events[0] if events else None)


def test_request_api_key_emits_event_and_decline_path():
    import asyncio

    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)

    async def scenario():
        task, evt = await _drive_key_request(runner, "proj", "GOOGLE_API_KEY", "Google", "to make the video")
        assert evt is not None
        assert evt["type"] == "api_key_request"
        assert evt["env_var"] == "GOOGLE_API_KEY"
        assert evt["label"]  # enriched from the curated BYOK menu
        # decline
        assert runner.resolve_key_request(evt["key_request_id"], False) is True
        return await task

    result = asyncio.run(scenario())
    text = result["content"][0]["text"]
    assert '"provided": false' in text
    assert result["is_error"] is False


def test_request_api_key_provided_returns_retry_and_double_resolve_noop():
    import asyncio

    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)

    async def scenario():
        task, evt = await _drive_key_request(runner, "p", "REPLICATE_API_TOKEN", "Replicate", "")
        kid = evt["key_request_id"]
        assert runner.resolve_key_request(kid, True) is True
        # second resolve of the same id is a no-op (future already done)
        assert runner.resolve_key_request(kid, True) is False
        return await task

    result = asyncio.run(scenario())
    text = result["content"][0]["text"]
    assert '"provided": true' in text
    assert "RETRY" in text


def test_request_api_key_without_stream_skips():
    import asyncio

    runner = AgentRunner(repo_root=".", client_factory=lambda pid: None)
    result = asyncio.run(runner._request_api_key("proj", "FAL_KEY", "", ""))
    text = result["content"][0]["text"]
    assert '"provided": false' in text


# ── @-mention sidecar: SHAPE -> 422, STATE -> degrade (OPN-27) ───────────────
#
# The composer sends the assets a user picked as structured `mentions[]`; the server turns
# each project-relative path into a verified ABSOLUTE one (the agent's cwd is the code root,
# and the "use absolute paths" note only rides the first-turn preamble).
#
# The split under test:
#   SHAPE  — decidable from the string alone. The menu can never produce it, so it means a
#            client bug or tampering: 422, and the runner is NEVER called.
#   STATE  — needs a filesystem look. A race we cause ourselves (the agent rewrites
#            hf/renders/* mid-turn), so it degrades to "NOT FOUND" and the turn proceeds.


class RecordingRunner:
    """Captures the prompt the endpoint hands to run_turn."""

    def __init__(self):
        self.prompts: list[str] = []

    async def run_turn(self, project_id, message, on_event=None, session_id=None):
        self.prompts.append(message)

    async def switch_session(self, project_id, session_id):
        pass

    async def set_model(self, project_id, model):
        pass


@pytest.fixture
def mention_project(tmp_path, monkeypatch):
    """An authed client over a project with one file in each mentionable bucket."""
    monkeypatch.setattr("server.app.auth_configured", lambda: True)
    projects = tmp_path / "projects"
    create_project(projects, "Sky", PIPELINE)
    proj = projects / "sky"
    for rel in (
        "assets/video/hook.mp4",
        "assets/images/logo.png",
        "assets/music/bed.mp3",
        "hf/renders/scene2.mp4",
        "renders/final.mp4",
        "hf/renders/thumb.png",
        "renders/proxies/p1.mp4",
        "assets/.tmp/clip.mp4",
    ):
        f = proj / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    runner = RecordingRunner()
    return _client(tmp_path, agent_runner=runner), runner, proj


def _chat(client, mentions, message="do it"):
    # Serialize with ensure_ascii like a real JSON client, and hand httpx pre-encoded bytes.
    # `json=` would UTF-8 encode the Python string directly, which CANNOT represent a lone
    # surrogate and blows up in the test client before the request is even sent — hiding a
    # payload a browser can trivially put on the wire as the ASCII escape `\ud800`.
    body = json.dumps({"message": message, "mentions": mentions}, ensure_ascii=True)
    return client.post(
        "/api/projects/sky/chat",
        content=body.encode("ascii"),
        headers={"Content-Type": "application/json"},
    )


def test_mentions_reach_the_runner_as_absolute_paths_from_every_bucket(mention_project):
    client, runner, proj = mention_project
    r = _chat(
        client,
        [
            {"path": "assets/video/hook.mp4"},
            {"path": "hf/renders/scene2.mp4"},
            {"path": "renders/final.mp4"},
        ],
    )
    assert r.status_code == 200
    prompt = runner.prompts[0]
    assert prompt.startswith("do it")  # the user's prose is untouched
    for rel in ("assets/video/hook.mp4", "hf/renders/scene2.mp4", "renders/final.mp4"):
        assert str((proj / rel).resolve()) in prompt
    assert "NOT FOUND" not in prompt


def test_no_mentions_leaves_the_message_byte_for_byte_unchanged(mention_project):
    client, runner, _ = mention_project
    assert _chat(client, [], message="just talking").status_code == 200
    assert runner.prompts == ["just talking"]
    # ...and a client that omits the field entirely behaves identically.
    client.post("/api/projects/sky/chat", json={"message": "just talking"})
    assert runner.prompts == ["just talking", "just talking"]


def test_duplicate_mentions_are_resolved_once(mention_project):
    client, runner, _ = mention_project
    r = _chat(client, [{"path": "assets/video/hook.mp4"}] * 3)
    assert r.status_code == 200
    # One ENTRY, not one string occurrence — the absolute path ends with the relative one.
    assert runner.prompts[0].count(" - assets/video/hook.mp4\n") == 1


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",  # absolute
        "../../etc/passwd",  # traversal
        "assets/../../etc/passwd",  # traversal mid-path
        "artifacts/edit_decisions.json",  # a root the menu never offers
        ".mc/history.json",  # the agent's own chat history
        "hf/renders/thumb.png",  # PER-ROOT: hf/renders lists video only
        "renders/cover.jpg",  # PER-ROOT: renders lists video only
        "renders/proxies/p1.mp4",  # renders/ is direct children only
        "assets/.tmp/clip.mp4",  # dot-directory descendant
        "assets/video/notes.txt",  # not a media extension anywhere
        "",  # empty
        # PurePosixPath NORMALIZES these away, so checking its .parts silently blessed them.
        # They must be caught on the RAW segments, before any path object is built.
        "assets/./video/hook.mp4",  # literal "." segment
        "assets/video/./hook.mp4",  # ... at any depth
        "./assets/video/hook.mp4",  # ... leading
        "assets//video/hook.mp4",  # empty segment from a doubled slash
        # Strings no filesystem path can represent. These pass every character-level rule
        # above and then raise ValueError (NOT OSError) from the eventual stat, which
        # resolve_mentions does not catch — so before the encodability gate they surfaced as
        # a 500, breaking "every string-decidable violation is a 422".
        "assets/video/" + chr(0) + ".mp4",  # NUL: os.fsencode allows it, the syscall does not
        "assets/video/a" + chr(0xD800) + ".mp4",  # lone surrogate: os.fsencode itself rejects it
        "assets/video/a" + chr(0xDFFF) + ".mp4",  # ... the top of the same window
    ],
)
def test_shape_violation_is_422_and_the_runner_is_never_called(mention_project, bad):
    client, runner, _ = mention_project
    r = _chat(client, [{"path": bad}])
    assert r.status_code == 422, f"{bad!r} should be a SHAPE violation"
    assert "invalid asset mention" in r.json()["detail"]
    assert runner.prompts == []  # the turn never started


def test_a_tampered_path_is_never_echoed_as_an_absolute_path(mention_project):
    client, runner, _ = mention_project
    r = _chat(client, [{"path": "../../etc/passwd"}])
    assert r.status_code == 422
    assert "/etc/passwd" not in str(r.json()["detail"]) or "resolved" not in str(r.json())
    assert runner.prompts == []


def test_a_vanished_file_degrades_and_the_turn_still_runs(mention_project):
    client, runner, proj = mention_project
    (proj / "hf/renders/scene2.mp4").unlink()  # the agent replaced it mid-sentence
    r = _chat(client, [{"path": "hf/renders/scene2.mp4"}, {"path": "assets/video/hook.mp4"}])
    assert r.status_code == 200  # the user's message survives
    prompt = runner.prompts[0]
    assert "hf/renders/scene2.mp4\n   NOT FOUND" in prompt
    assert str((proj / "assets/video/hook.mp4").resolve()) in prompt  # the good one still resolves


def test_a_directory_where_a_file_was_degrades_rather_than_422s(mention_project):
    client, runner, proj = mention_project
    f = proj / "assets/video/hook.mp4"
    f.unlink()
    f.mkdir()
    r = _chat(client, [{"path": "assets/video/hook.mp4"}])
    assert r.status_code == 200
    assert "NOT FOUND" in runner.prompts[0]


def test_a_symlink_escaping_the_project_degrades_and_leaks_no_path(tmp_path, mention_project):
    client, runner, proj = mention_project
    outside = tmp_path / "outside-secret.mp4"
    outside.write_bytes(b"secret")
    link = proj / "assets/video/sneaky.mp4"
    link.symlink_to(outside)
    # It IS menu-reachable: list_assets uses is_file(), which follows symlinks.
    listed = client.get("/api/projects/sky/assets").json()["kinds"]["video"]
    assert any(a["path"] == "assets/video/sneaky.mp4" for a in listed)

    r = _chat(client, [{"path": "assets/video/sneaky.mp4"}])
    assert r.status_code == 200  # the user did nothing wrong
    assert "NOT FOUND" in runner.prompts[0]
    assert str(outside) not in runner.prompts[0]  # but the escape is never handed over


def test_dot_segment_evasion_422s_even_for_a_real_mentionable_file(mention_project):
    """The exact review repro, sharpened.

    `assets/./video/hook.mp4` names a file that really exists and really is mentionable, so
    nothing downstream would object: `PurePosixPath` normalizes the "." away, resolution
    succeeds and the runner starts. Containment is never breached — but the plan's contract
    is SHAPE -> 422 with the runner NEVER called, and a client that can smuggle one
    normalized segment past the gate is a client whose paths we are no longer validating.
    Pinning it against a REAL file is what makes this test bite: a version that only checked
    a nonexistent path would still pass if the fix regressed to a STATE-level "not found".
    """
    client, runner, proj = mention_project
    assert (proj / "assets/video/hook.mp4").is_file()  # the un-dotted form IS mentionable
    assert _chat(client, [{"path": "assets/video/hook.mp4"}]).status_code == 200
    runner.prompts.clear()

    r = _chat(client, [{"path": "assets/./video/hook.mp4"}])
    assert r.status_code == 422
    assert "hidden (dot-prefixed) path segment" in r.json()["detail"]
    assert runner.prompts == []  # the turn never started


def test_legitimate_paths_still_pass_after_the_raw_segment_check(mention_project):
    """The raw-segment check must not over-reject: dots INSIDE a segment are ordinary."""
    client, runner, proj = mention_project
    (proj / "assets/video/my.clip.v2.mp4").write_bytes(b"x")
    r = _chat(client, [{"path": "assets/video/my.clip.v2.mp4"}])
    assert r.status_code == 200
    assert str((proj / "assets/video/my.clip.v2.mp4").resolve()) in runner.prompts[0]


def test_unencodable_paths_are_422_not_500(mention_project):
    """The whole unencodable CLASS, not just NUL.

    A NUL and a lone surrogate fail in two different places — the syscall and `os.fsencode`
    respectively — but share one shape: they satisfy every character-level SHAPE rule and
    then raise `ValueError`, which `resolve_mentions` deliberately does not catch. Before the
    encodability gate each produced a 500. Neither can come from the menu, so both belong on
    the 422/runner-never-called side rather than degrading to a STATE miss.
    """
    client, runner, _ = mention_project
    for path in ("assets/video/" + chr(0) + ".mp4", "assets/video/a" + chr(0xD800) + ".mp4"):
        r = _chat(client, [{"path": path}])
        assert r.status_code == 422, f"{path!r} must be a SHAPE violation, not a 500"
        assert "invalid asset mention" in r.json()["detail"]
        assert runner.prompts == []


def test_legal_but_unusual_filename_characters_still_degrade(mention_project):
    """The over-rejection guard, and the reason the fix tests encodability rather than a
    hand-rolled "control character" class.

    A newline, a \\x01 and a U+DC80-U+DCFF surrogate-escape byte are all LEGAL in a POSIX
    filename — the last one is how Python represents an undecodable on-disk name. They are
    merely absent, which is a STATE miss: the turn must still run and the agent must be told
    NOT FOUND. A tempting `if any(c.isprintable() is False ...)` rule would 422 all three.
    """
    client, runner, _ = mention_project
    for path in (
        "assets/video/a" + chr(10) + "b.mp4",
        "assets/video/a" + chr(1) + "b.mp4",
        "assets/video/a" + chr(0xDCFF) + "b.mp4",
    ):
        runner.prompts.clear()
        r = _chat(client, [{"path": path}])
        assert r.status_code == 200, f"{path!r} is a legal filename and must not 422"
        assert "NOT FOUND" in runner.prompts[0]
