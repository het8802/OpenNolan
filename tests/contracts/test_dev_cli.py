"""Behavior contracts for the repository development command."""

from __future__ import annotations

import json
import os
import re
import runpy
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DEV = REPO_ROOT / "scripts" / "dev"


@pytest.fixture
def external_tmp_path() -> Path:
    candidates = [os.environ.get("RUNNER_TEMP"), "/tmp", str(REPO_ROOT.parent)]
    for value in candidates:
        if not value:
            continue
        candidate = Path(value).resolve()
        try:
            candidate.relative_to(REPO_ROOT)
        except ValueError:
            if candidate.is_dir():
                break
    else:
        raise RuntimeError("no temporary directory exists outside the repository")
    with tempfile.TemporaryDirectory(prefix="opennolan-review-test-", dir=candidate) as directory:
        yield Path(directory)


def test_doctor_json_describes_the_current_worktree() -> None:
    result = subprocess.run(
        [str(DEV), "doctor", "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["command"] == "doctor"
    assert Path(report["worktree"]["root"]) == REPO_ROOT
    assert report["worktree"]["backend_port"] != report["worktree"]["frontend_port"]
    assert report["tools"]["python"]["available"] is True
    assert report["tools"]["node"]["available"] is True
    assert report["tools"]["ffmpeg"]["available"] is True


def test_setup_dry_run_reports_the_environment_without_writing_it() -> None:
    env_file = REPO_ROOT / ".env.worktree"
    before = env_file.read_bytes() if env_file.exists() else None

    result = subprocess.run(
        [str(DEV), "setup", "--dry-run", "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["command"] == "setup"
    assert report["dry_run"] is True
    assert report["environment"]["OPENNOLAN_HOME"] == str(REPO_ROOT / ".local")
    assert report["environment"]["OPENNOLAN_BACKEND_PORT"].isdigit()
    assert report["environment"]["OPENNOLAN_FRONTEND_PORT"].isdigit()
    assert report["doctor"]["command"] == "doctor"
    after = env_file.read_bytes() if env_file.exists() else None
    assert after == before


def test_generated_worktree_state_is_ignored_by_git() -> None:
    result = subprocess.run(
        ["git", "check-ignore", ".env.worktree", ".local/run/app.json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [".env.worktree", ".local/run/app.json"]


def test_run_dev_uses_the_worktree_ports() -> None:
    environment = {
        **os.environ,
        "OPENNOLAN_BACKEND_PORT": "32101",
        "OPENNOLAN_FRONTEND_PORT": "32102",
    }
    result = subprocess.run(
        [str(REPO_ROOT / "run-dev"), "--help"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "127.0.0.1:32101" in result.stdout
    assert "localhost:32102" in result.stdout


def test_vite_and_electron_use_the_same_worktree_ports() -> None:
    environment = {
        **os.environ,
        "OPENNOLAN_BACKEND_PORT": "32201",
        "OPENNOLAN_FRONTEND_PORT": "32202",
    }
    vite = subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            (
                "import('./web/vite.config.js').then(({default: c}) => "
                "console.log(JSON.stringify({port:c.server.port,"
                "target:c.server.proxy['/api'].target})))"
            ),
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    electron = subprocess.run(
        [
            "node",
            "--eval",
            (
                "const c=require('./desktop/worktree-config');"
                "console.log(JSON.stringify({port:c.backendPort(),url:c.frontendUrl()}))"
            ),
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert vite.returncode == 0, vite.stderr
    assert electron.returncode == 0, electron.stderr
    assert json.loads(vite.stdout) == {
        "port": 32202,
        "target": "http://127.0.0.1:32201",
    }
    assert json.loads(electron.stdout) == {
        "port": 32201,
        "url": "http://localhost:32202",
    }


def test_worktree_ports_reject_invalid_values() -> None:
    environment = {
        **os.environ,
        "OPENNOLAN_BACKEND_PORT": "not-a-port",
        "OPENNOLAN_FRONTEND_PORT": "70000",
    }
    result = subprocess.run(
        ["node", "--eval", "require('./desktop/worktree-config').backendPort()"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "valid port" in result.stderr


def test_run_dry_run_is_bounded_and_does_not_start_the_app() -> None:
    pid_file = REPO_ROOT / ".local" / "run" / "app.json"
    before = pid_file.read_bytes() if pid_file.exists() else None
    result = subprocess.run(
        [str(DEV), "run", "--ttl", "90s", "--dry-run", "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["command"] == "run"
    assert report["dry_run"] is True
    assert report["ttl_seconds"] == 90
    assert report["argv"] == [str(REPO_ROOT / "run-dev")]
    after = pid_file.read_bytes() if pid_file.exists() else None
    assert after == before


def test_stop_is_idempotent_when_nothing_is_running() -> None:
    result = subprocess.run(
        [str(DEV), "stop", "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["command"] == "stop"
    assert report["ok"] is True
    assert report["stopped"] == []


def test_fast_test_dry_run_shows_only_deterministic_local_checks() -> None:
    result = subprocess.run(
        [str(DEV), "test", "fast", "--dry-run", "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["command"] == "test"
    assert report["tier"] == "fast"
    assert report["dry_run"] is True
    flattened = " ".join(" ".join(action["argv"]) for action in report["actions"])
    assert "ruff check" in flattened
    ruff_check = next(
        action["argv"] for action in report["actions"] if "ruff" in action["argv"] and "check" in action["argv"]
    )
    assert "scripts/dev" in ruff_check
    assert "pytest" in flattened
    # Which tests get selected depends on what this worktree has changed, so assert the SHAPE of
    # the selection, not a specific file: every pytest target must be a real path under tests/.
    # (This line used to name tests/tools/test_ffmpeg_hdr_preserve.py — true only for the commit
    # that added it, which happened to touch tools/video/. Any change outside video tooling then
    # failed the gate.)
    pytest_argv = next(action["argv"] for action in report["actions"] if "pytest" in action["argv"])
    targets = [arg for arg in pytest_argv if arg.startswith("tests/")]
    assert targets, f"fast tier selected no tests: {pytest_argv}"
    assert all((REPO_ROOT / target).exists() for target in targets), targets
    assert "ANTHROPIC" not in flattened
    assert "REPLICATE" not in flattened


def test_provider_test_requires_provider_and_spend_cap() -> None:
    result = subprocess.run(
        [str(DEV), "test", "provider", "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert report["tier"] == "provider"
    assert "--provider" in report["error"]
    assert "--max-spend" in report["error"]


def test_smoke_dry_run_uses_the_checked_in_playwright_suite() -> None:
    result = subprocess.run(
        [str(DEV), "smoke", "--dry-run", "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["command"] == "smoke"
    assert report["dry_run"] is True
    assert report["actions"] == [{"argv": ["npm", "--prefix", "desktop", "run", "test:smoke"], "ok": None}]
    doctor = json.loads(
        subprocess.run(
            [str(DEV), "doctor", "--json"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    )
    assert report["ports"] != {
        "backend": doctor["worktree"]["backend_port"],
        "frontend": doctor["worktree"]["frontend_port"],
    }


def test_playwright_waits_for_the_frontend_and_has_no_shared_default_ports() -> None:
    config = (REPO_ROOT / "desktop" / "playwright.config.js").read_text(encoding="utf-8")
    worktree_config = (REPO_ROOT / "desktop" / "worktree-config.js").read_text(encoding="utf-8")

    assert "url: `http://127.0.0.1:${frontendPort}`" in config
    assert "reuseExistingServer: false" in config
    assert "|| 8000" not in config + worktree_config
    assert "|| 5173" not in config + worktree_config


def test_git_hooks_use_the_shared_gates_without_blocking_on_review() -> None:
    pre_commit = (REPO_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    post_commit = REPO_ROOT / ".githooks" / "post-commit"
    pre_push = (REPO_ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")

    assert 'scripts/dev" test fast' in pre_commit
    assert post_commit.is_file() and os.access(post_commit, os.X_OK)
    assert 'scripts/dev" test full' in pre_push
    assert 'scripts/dev" test full </dev/null' in pre_push
    assert "review-current" in pre_push
    assert "exit 1" not in pre_push.split("review-current", 1)[1]


def test_stop_rejects_an_untrusted_process_group(monkeypatch, tmp_path: Path) -> None:
    namespace = runpy.run_path(str(DEV))
    stop_pid_file = namespace["_stop_pid_file"]
    killed: list[tuple[int, int]] = []
    monkeypatch.setitem(
        stop_pid_file.__globals__,
        "_read_pid_file",
        lambda _path: {"pid": 12345, "pgid": 0, "root": str(REPO_ROOT)},
    )
    monkeypatch.setitem(stop_pid_file.__globals__, "_owned_process", lambda _record: True)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    result = stop_pid_file(tmp_path / "app.json")

    assert result is None
    assert killed == []


def test_redactor_covers_bare_public_credential_formats() -> None:
    redact = runpy.run_path(str(DEV))["_redact"]
    samples = [
        "".join(("sk-ant-", "api03-EXAMPLESECRET123456789")),
        "".join(("ghp_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456")),
        "".join(("github_pat_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456")),
        "".join(("xoxb-", "1234567890-ABCDEFGHIJKLMNOPQRSTUVWXYZ")),
        "".join(("AKIA", "IOSFODNN7EXAMPLE")),
        "".join(("eyJhbGciOiJIUzI1NiJ9.", "eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123")),
    ]

    assert all(sample not in redact(sample) for sample in samples)
    assert all("[redacted]" in redact(sample) for sample in samples)
    assert redact("no token or secret is configured") == "no token or secret is configured"
    assert "shortvalue" not in redact("api_key=shortvalue")
    assert "shortvalue" not in redact("Authorization: Bearer shortvalue")


def test_truncation_is_bounded_and_reports_the_exact_omitted_size() -> None:
    truncate = runpy.run_path(str(DEV))["_truncate_with_marker"]
    text = "0123456789" * 40

    assert len(truncate(text, 10)) <= 10

    result = truncate(text, 100)
    marker = re.search(r"\.\.\.\[truncated (\d+) characters\]\.\.\.", result)
    assert len(result) == 100
    assert marker is not None
    retained_text = len(result) - len(marker.group(0)) - 2  # marker's surrounding newlines
    assert int(marker.group(1)) == len(text) - retained_text


def test_test_environment_removes_all_credential_shaped_variables(monkeypatch, tmp_path: Path) -> None:
    test_environment = runpy.run_path(str(DEV))["_test_environment"]
    credential_names = (
        "OPENAI_API_KEY",
        "FAL_KEY",
        "GH_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "SLACK_BOT_TOKEN",
        "OPENNOLAN_REVIEW_STATUS_TOKEN",
        "SOME_PASSWORD",
    )
    for name in credential_names:
        monkeypatch.setenv(name, "must-not-reach-tests")

    environment = test_environment(tmp_path / "scratch")

    assert all(name not in environment for name in credential_names)


def test_pr_ci_runs_full_and_smoke_on_the_merge_checkout() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "merge_group:" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "scripts/dev test full" in workflow
    assert "scripts/dev smoke" in workflow
    assert "merge-ready:" in workflow
    assert "persist-credentials: false" in workflow


def test_review_request_routes_to_the_opposite_provider_and_exact_sha() -> None:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    result = subprocess.run(
        [
            str(DEV),
            "review",
            "request",
            "--sha",
            sha,
            "--author-provider",
            "claude",
            "--dry-run",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["sha"] == sha
    assert report["author_provider"] == "claude"
    assert report["reviewer_provider"] == "codex"
    assert report["status_context"] == "review-current"
    assert report["dry_run"] is True
    assert "scripts/dev test full" in report["required_checks"]
    assert "scripts/dev smoke" in report["required_checks"]
    assert "scripts/dev test full --merge-base main" in report["required_checks"]
    assert any("immediately before publishing" in check for check in report["required_checks"])


def test_review_request_refuses_to_dispatch_before_orca_setup_succeeds(monkeypatch, external_tmp_path: Path) -> None:
    namespace = runpy.run_path(str(DEV))
    request_review = namespace["request_review"]
    sha = "a" * 40
    commands: list[list[str]] = []
    receipts = iter(
        [
            ({"result": {"run": {"id": "run-1"}}}, None),
            ({"result": {"task": {"id": "task-1"}}}, None),
            (
                {
                    "result": {
                        "worktree": {"id": "worktree-1", "path": str(REPO_ROOT)},
                        "agentTerminalHandle": "terminal-1",
                        "setup": {"status": "running"},
                    }
                },
                None,
            ),
        ]
    )
    monkeypatch.setitem(request_review.__globals__, "_resolve_sha", lambda _value: sha)
    monkeypatch.setitem(request_review.__globals__, "_git_clean", lambda: True)
    monkeypatch.setitem(request_review.__globals__, "_orca_prefix", lambda: ["orca"])
    monkeypatch.setitem(request_review.__globals__, "_branch_protection", lambda: {"ok": True})
    monkeypatch.setenv("OPENNOLAN_REVIEW_COORDINATOR_DIR", str(external_tmp_path / "coordinator"))
    monkeypatch.setenv("OPENNOLAN_REVIEW_STATUS_TOKEN", "test-coordinator-token")
    monkeypatch.setenv("OPENNOLAN_REVIEW_STATUS_APP_ID", "12345")
    monkeypatch.setattr("shutil.which", lambda _name: "/test/orca")
    monkeypatch.setitem(request_review.__globals__, "_command_json", lambda _command: next(receipts))

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=f"{sha}\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    report = request_review(sha, "claude", dry_run=False)

    assert report["ok"] is False
    assert "wait-for-setup" in report["error"]
    assert not any("/statuses/" in " ".join(command) for command in commands)


def test_review_request_clears_pending_status_when_dispatch_fails(monkeypatch, external_tmp_path: Path) -> None:
    namespace = runpy.run_path(str(DEV))
    request_review = namespace["request_review"]
    sha = "a" * 40
    status_commands: list[list[str]] = []
    status_environments: list[dict[str, str]] = []
    receipts = iter(
        [
            ({"result": {"run": {"id": "run-1"}}}, None),
            ({"result": {"task": {"id": "task-1"}}}, None),
            (
                {
                    "result": {
                        "worktree": {"id": "worktree-1", "path": str(REPO_ROOT)},
                        "agentTerminalHandle": "terminal-1",
                        "setup": {"status": "succeeded"},
                    }
                },
                None,
            ),
            ({"result": {"status": "idle"}}, None),
            (None, "dispatch failed"),
        ]
    )
    monkeypatch.setitem(request_review.__globals__, "_resolve_sha", lambda _value: sha)
    monkeypatch.setitem(request_review.__globals__, "_git_clean", lambda: True)
    monkeypatch.setitem(request_review.__globals__, "_orca_prefix", lambda: ["orca"])
    monkeypatch.setitem(request_review.__globals__, "_branch_protection", lambda: {"ok": True})
    monkeypatch.setenv("OPENNOLAN_REVIEW_COORDINATOR_DIR", str(external_tmp_path / "coordinator"))
    monkeypatch.setenv("OPENNOLAN_REVIEW_STATUS_TOKEN", "test-coordinator-token")
    monkeypatch.setenv("OPENNOLAN_REVIEW_STATUS_APP_ID", "12345")
    monkeypatch.setitem(
        request_review.__globals__,
        "_status_command",
        lambda _sha, state, _description, **_kwargs: ["status", state],
    )
    monkeypatch.setitem(request_review.__globals__, "_command_json", lambda _command: next(receipts))
    monkeypatch.setattr("shutil.which", lambda _name: "/test/orca")

    def fake_run(command, **_kwargs):
        if command[:4] == ["git", "-C", str(REPO_ROOT), "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout=f"{sha}\n", stderr="")
        if command[:4] == ["git", "-C", str(REPO_ROOT), "status"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        status_commands.append(command)
        status_environments.append(_kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    report = request_review(sha, "claude", dry_run=False)

    assert report["ok"] is False
    assert status_commands == [["status", "pending"], ["status", "error"]]
    assert all(environment["GH_TOKEN"] == "test-coordinator-token" for environment in status_environments)
    assert "dispatch failed" in report["error"]


def test_review_request_requires_protected_coordinator_storage(monkeypatch) -> None:
    namespace = runpy.run_path(str(DEV))
    request_review = namespace["request_review"]
    sha = "a" * 40
    monkeypatch.delenv("OPENNOLAN_REVIEW_COORDINATOR_DIR", raising=False)
    monkeypatch.setitem(request_review.__globals__, "_resolve_sha", lambda _value: sha)

    report = request_review(sha, "claude", dry_run=False)

    assert report["ok"] is False
    assert "coordinator" in report["error"].lower()
    assert "outside" in report["error"].lower()


def test_review_publish_rejects_self_review_even_in_dry_run() -> None:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    result = subprocess.run(
        [
            str(DEV),
            "review",
            "publish",
            "--sha",
            sha,
            "--author-provider",
            "codex",
            "--reviewer-provider",
            "codex",
            "--verdict",
            "approved",
            "--summary",
            "looks good",
            "--task-id",
            "task-review-1",
            "--dispatch-id",
            "dispatch-review-1",
            "--request-nonce",
            "nonce-review-1",
            "--dry-run",
            "--json",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert "cannot review itself" in report["error"]


def test_review_publish_dry_run_reports_through_orca_not_github(tmp_path: Path) -> None:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    review_drafts = tmp_path / "review-drafts"
    review_drafts.mkdir()
    review_report = review_drafts / "review.md"
    review_report.write_text("BEGIN: highest severity finding\n" + ("detail\n" * 1000), encoding="utf-8")
    environment = {**os.environ, "OPENNOLAN_REVIEW_DRAFT_DIR": str(review_drafts)}

    result = subprocess.run(
        [
            str(DEV),
            "review",
            "publish",
            "--sha",
            sha,
            "--author-provider",
            "claude",
            "--reviewer-provider",
            "codex",
            "--verdict",
            "approved",
            "--summary",
            "no blocking findings",
            "--report",
            str(review_report),
            "--task-id",
            "task-review-1",
            "--dispatch-id",
            "dispatch-review-1",
            "--request-nonce",
            "nonce-review-1",
            "--dry-run",
            "--json",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    command = " ".join(report["orca_command"])
    assert "orchestration send" in command
    assert "worker_done" in command
    assert "task-review-1" in command
    assert "dispatch-review-1" in command
    assert "gh api" not in command
    assert "comment_command" not in report
    assert "status_command" not in report
    assert report["report"].startswith("BEGIN: highest severity finding")


def test_review_publish_rejects_secrets_and_reports_outside_the_draft_directory(tmp_path: Path) -> None:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    review_drafts = tmp_path / "review-drafts"
    review_drafts.mkdir()
    safe_report = review_drafts / "review.md"
    safe_report.write_text("Correctness: pass\n", encoding="utf-8")
    outside_report = tmp_path / "outside.md"
    outside_report.write_text("Correctness: pass\n", encoding="utf-8")
    environment = {**os.environ, "OPENNOLAN_REVIEW_DRAFT_DIR": str(review_drafts)}

    common = [
        str(DEV),
        "review",
        "publish",
        "--sha",
        sha,
        "--author-provider",
        "claude",
        "--reviewer-provider",
        "codex",
        "--verdict",
        "approved",
        "--task-id",
        "task-review-1",
        "--dispatch-id",
        "dispatch-review-1",
        "--request-nonce",
        "nonce-review-1",
        "--dry-run",
        "--json",
    ]
    secret_summary = "failure included " + "".join(("sk-ant-", "api03-EXAMPLESECRET123456789"))
    secret = subprocess.run(
        [*common, "--summary", secret_summary, "--report", str(safe_report)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    outside = subprocess.run(
        [*common, "--summary", "safe summary", "--report", str(outside_report)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert secret.returncode != 0
    assert "possible secret" in json.loads(secret.stdout)["error"]
    assert outside.returncode != 0
    assert "review draft directory" in json.loads(outside.stdout)["error"]


def test_review_finalize_requires_a_coordinator_credential(tmp_path: Path) -> None:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    result_file = tmp_path / "attested-review.json"
    result_file.write_text(
        json.dumps(
            {
                "sha": sha,
                "author_provider": "claude",
                "reviewer_provider": "codex",
                "verdict": "approved",
                "summary": "no blocking findings",
                "report": "Correctness: pass",
                "task_id": "task-review-1",
                "dispatch_id": "dispatch-review-1",
                "request_nonce": "nonce-review-1",
            }
        ),
        encoding="utf-8",
    )
    environment = {**os.environ}
    environment.pop("OPENNOLAN_REVIEW_STATUS_TOKEN", None)

    result = subprocess.run(
        [
            str(DEV),
            "review",
            "finalize",
            "--result",
            str(result_file),
            "--json",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert "coordinator credential" in report["error"]


def test_review_finalize_rejects_agent_writable_coordinator_storage(monkeypatch, tmp_path: Path) -> None:
    namespace = runpy.run_path(str(DEV))
    finalize_review = namespace["finalize_review"]
    coordinator_dir = REPO_ROOT / ".local" / "reviews" / "coordinator"
    monkeypatch.setenv("OPENNOLAN_REVIEW_COORDINATOR_DIR", str(coordinator_dir))
    monkeypatch.setenv("OPENNOLAN_REVIEW_STATUS_TOKEN", "test-coordinator-token")
    monkeypatch.setenv("OPENNOLAN_REVIEW_STATUS_APP_ID", "12345")

    report = finalize_review(tmp_path / "missing.json", dry_run=False)

    assert report["ok"] is False
    assert "outside" in report["error"]


def test_review_finalize_rejects_a_dispatch_without_a_coordinator_request(external_tmp_path: Path) -> None:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    coordinator_dir = external_tmp_path / "coordinator"
    inbox = coordinator_dir / "inbox"
    inbox.mkdir(parents=True)
    result_file = inbox / f"{sha}.json"
    result_file.write_text(
        json.dumps(
            {
                "sha": sha,
                "author_provider": "claude",
                "reviewer_provider": "codex",
                "verdict": "approved",
                "summary": "no blocking findings",
                "report": "Correctness: pass",
                "task_id": "task-review-1",
                "dispatch_id": "dispatch-review-1",
                "request_nonce": "nonce-review-1",
            }
        ),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "OPENNOLAN_REVIEW_STATUS_TOKEN": "test-coordinator-token",
        "OPENNOLAN_REVIEW_STATUS_APP_ID": "12345",
        "OPENNOLAN_REVIEW_COORDINATOR_DIR": str(coordinator_dir),
    }

    result = subprocess.run(
        [str(DEV), "review", "finalize", "--result", str(result_file), "--dry-run", "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    report = json.loads(result.stdout)
    assert "coordinator request" in report["error"]


def test_review_finalize_requires_a_completed_orca_dispatch(monkeypatch, external_tmp_path: Path) -> None:
    namespace = runpy.run_path(str(DEV))
    finalize_review = namespace["finalize_review"]
    sha = "a" * 40
    coordinator_dir = external_tmp_path / "coordinator"
    inbox = coordinator_dir / "inbox"
    requests = coordinator_dir / "requests"
    inbox.mkdir(parents=True)
    requests.mkdir()
    result_file = inbox / f"{sha}.json"
    record = {
        "sha": sha,
        "author_provider": "claude",
        "reviewer_provider": "codex",
        "verdict": "approved",
        "summary": "no blocking findings",
        "report": "Correctness: pass",
        "task_id": "task-review-1",
        "dispatch_id": "dispatch-review-1",
        "request_nonce": "nonce-review-1",
    }
    result_file.write_text(json.dumps(record), encoding="utf-8")
    (requests / f"{sha}.json").write_text(
        json.dumps({**record, "terminal_handle": "terminal-review-1", "worktree_path": str(REPO_ROOT)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENNOLAN_REVIEW_STATUS_TOKEN", "test-coordinator-token")
    monkeypatch.setenv("OPENNOLAN_REVIEW_STATUS_APP_ID", "12345")
    monkeypatch.setenv("OPENNOLAN_REVIEW_COORDINATOR_DIR", str(coordinator_dir))
    monkeypatch.setitem(finalize_review.__globals__, "_resolve_sha", lambda _value: sha)
    monkeypatch.setitem(finalize_review.__globals__, "_orca_prefix", lambda: ["orca"])
    monkeypatch.setitem(
        finalize_review.__globals__,
        "_command_json",
        lambda _command, **_kwargs: (
            {
                "result": {
                    "dispatch": {
                        "id": "dispatch-review-1",
                        "status": "dispatched",
                        "terminalHandle": "terminal-review-1",
                    }
                }
            },
            None,
        ),
    )

    report = finalize_review(result_file, dry_run=False)

    assert report["ok"] is False
    assert "completed" in report["error"]


def test_branch_protection_binds_review_status_to_the_coordinator_app(monkeypatch) -> None:
    namespace = runpy.run_path(str(DEV))
    branch_protection = namespace["_branch_protection"]
    monkeypatch.setenv("OPENNOLAN_REVIEW_STATUS_APP_ID", "12345")
    monkeypatch.setitem(branch_protection.__globals__, "_github_repository", lambda: "owner/repo")

    protection = {
        "required_pull_request_reviews": {"dismiss_stale_reviews": True},
        "required_conversation_resolution": {"enabled": True},
        "required_status_checks": {
            "contexts": ["merge-ready", "review-current"],
            "checks": [
                {"context": "merge-ready", "app_id": None},
                {"context": "review-current", "app_id": 99999},
            ],
        },
    }
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(protection), stderr=""),
    )

    wrong_app = branch_protection()
    protection["required_status_checks"]["checks"][1]["app_id"] = 12345
    correct_app = branch_protection()

    assert wrong_app["ok"] is False
    assert wrong_app["checks"]["review_current_bound_to_coordinator_app"] is False
    assert correct_app["ok"] is True
    assert correct_app["checks"]["review_current_bound_to_coordinator_app"] is True


def test_review_commands_derive_the_github_repository_from_the_environment() -> None:
    environment = {**os.environ, "GITHUB_REPOSITORY": "example-owner/example-fork"}
    result = subprocess.run(
        [str(DEV), "review", "verify", "--sha", "HEAD", "--dry-run", "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    command = " ".join(json.loads(result.stdout)["argv"])
    assert "repos/example-owner/example-fork/" in command
    assert "het8802/OpenNolan" not in (REPO_ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")


def test_merge_mode_and_reaper_have_non_mutating_dry_runs() -> None:
    merge = subprocess.run(
        [str(DEV), "test", "full", "--merge-base", "main", "--dry-run", "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    reap = subprocess.run(
        [str(DEV), "reap", "--dry-run", "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert merge.returncode == 0, merge.stderr
    merge_report = json.loads(merge.stdout)
    assert merge_report["merge_base"] == "main"
    assert merge_report["dry_run"] is True
    assert reap.returncode == 0, reap.stderr
    reap_report = json.loads(reap.stdout)
    assert reap_report["command"] == "reap"
    assert reap_report["dry_run"] is True
    assert reap_report["current_worktree"] == str(REPO_ROOT)


def test_resolve_sha_finds_main_when_it_only_exists_on_a_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CI checkout is detached with no local branches, so `main` lives only under a remote.

    The remote is not always named `origin` — this repository's is `het8802`.

    Git exports GIT_DIR (and friends) to the hooks it runs, and the pre-push hook runs the FULL
    tier. Inherited, those point every `git` call below — and `_resolve_sha` itself — at the real
    repository instead of the fixture, so `git add` fails with "must be run in a work tree" and
    the local-`main` guard would see the real main. Scrub them: the fixture is the only repo here.
    """
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR"):
        monkeypatch.delenv(name, raising=False)

    resolve_sha = runpy.run_path(str(DEV))["_resolve_sha"]

    def git(*argv: str) -> str:
        result = subprocess.run(["git", *argv], cwd=tmp_path, text=True, capture_output=True, check=False)
        # check=True would hide git's stderr behind a bare CalledProcessError.
        assert result.returncode == 0, f"git {' '.join(argv)} failed: {result.stderr.strip()}"
        return result.stdout.strip()

    git("init", "--initial-branch", "scratch")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    (tmp_path / "file.txt").write_text("contents\n", encoding="utf-8")
    git("add", "file.txt")
    git("commit", "--message", "initial")
    head = git("rev-parse", "HEAD")

    # `main` exists only as a remote-tracking ref, under a non-`origin` remote name.
    git("remote", "add", "upstream", "https://example.invalid/repo.git")
    git("update-ref", "refs/remotes/upstream/main", head)
    assert (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "main"], cwd=tmp_path, capture_output=True, check=False
        ).returncode
        != 0
    ), "fixture must not have a local main branch"

    assert resolve_sha("main", cwd=tmp_path) == head
    assert resolve_sha("HEAD", cwd=tmp_path) == head
    assert resolve_sha("no-such-ref", cwd=tmp_path) is None


def test_test_environment_does_not_leave_git_config_half_scrubbed(tmp_path: Path) -> None:
    """The credential scrub eats GIT_CONFIG_KEY_n; leaving COUNT behind breaks every git call.

    Git reads env config as GIT_CONFIG_COUNT plus matching KEY_n/VALUE_n triples and hard-fails
    with "missing config key" if the count outruns the keys.
    """
    namespace = runpy.run_path(str(DEV))
    environment = dict(os.environ) | {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "user.name",
        "GIT_CONFIG_VALUE_0": "Ambient",
    }
    with pytest.MonkeyPatch.context() as patch:
        for name, value in environment.items():
            patch.setenv(name, value)
        scrubbed = namespace["_test_environment"](tmp_path / "scratch")

    assert "GIT_CONFIG_COUNT" not in scrubbed
    assert not [key for key in scrubbed if key.startswith("GIT_CONFIG_")]
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=REPO_ROOT,
        env=scrubbed,
        text=True,
        capture_output=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
