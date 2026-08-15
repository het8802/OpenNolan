"""Headless agent runner for Mission Control.

Wraps the Claude Agent SDK as the engine that drives OpenNolan pipelines,
the same engine a human Claude Code session uses today. Both behavioral
spikes validated (on Sonnet) that a headless agent obeys the contract: reads
AGENT_GUIDE, produces schema-valid artifacts, records the render-runtime
decision with both runtimes, and persists `awaiting_human` at gates.

This module adds the safety layer the spikes told us we need:

- decide_tool: a code-level permission policy. Safe reads/writes run free; Bash
  commands are inspected for destructive/exfil patterns and routed to a UI
  confirm. (OpenNolan tools run as Python *through Bash*, so a tool-NAME
  allowlist can't work — the risk is in the command string.)
- A hard cost ceiling via the SDK-native `max_budget_usd`.
- A system prompt that re-asserts the contract so a headless run doesn't drift,
  including the corrected checkpoint location (projects/, not pipelines/).

Auth: the SDK reads it from the environment. Use the machine's Claude
subscription via CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`) — and unset
ANTHROPIC_API_KEY, which takes precedence and would bill per-token instead.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from server import analytics
from server import content_calendar as content_calendar_mod
from server.activity import record_tool_use

DEFAULT_MODEL = "claude-opus-4-8"  # most capable model; strongest at long-horizon agentic runs

# Models the user can pick in the agent UI (id -> display label). The UI dropdown
# and the /chat payload validate against this set — an unknown id is ignored and the
# session keeps its current model. Keep in sync with web/src/chat/chatUtils.js.
AGENT_MODELS: dict[str, str] = {
    "claude-opus-4-8": "Opus 4.8",  # default / recommended
    "claude-sonnet-5": "Sonnet 5",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
}

DEFAULT_MAX_BUDGET_USD = 15.0  # SDK-native hard ceiling per session
DEFAULT_CONFIRM_TIMEOUT_S = 300
DEFAULT_ANSWER_TIMEOUT_S = 900  # users may take a while to answer a question

# Always-safe tools (run unattended).
SAFE_TOOLS = frozenset(
    {
        "Read",
        "Glob",
        "Grep",
        "LS",
        "NotebookRead",
        "TodoWrite",
        "WebSearch",
        "WebFetch",
    }
)
# Writes are legitimate (the agent writes artifacts/checkpoints under projects/).
WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit", "MultiEdit"})

ACTION_ALLOW = "allow"
ACTION_CONFIRM = "confirm"
ACTION_DENY = "deny"  # hard-deny with a steering message (no user prompt)

import re

# Destructive / exfiltration markers in a Bash command -> require a UI confirm.
_DESTRUCTIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+-\w*[rf]"), "recursive/force file removal"),
    (re.compile(r"\brm\s+[^|&;]*\*"), "wildcard file removal"),
    (re.compile(r"\b(mkfs|shred)\b"), "disk-destroy command"),
    (re.compile(r"\bdd\b\s+if="), "raw disk write (dd)"),
    (re.compile(r">\s*/dev/(?!null\b|stderr\b|stdout\b)"), "write to device"),
    (re.compile(r"\bsudo\b"), "privilege escalation"),
    (re.compile(r"\bchmod\s+-?R?\s*777\b"), "world-writable chmod"),
    (re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sh|bash|zsh|python\d?)\b"), "pipe-to-shell download"),
    (re.compile(r"\b(curl|wget)\b.*\s(-d|--data|-T|--upload-file|-F|--form)\b"), "network upload/exfil"),
    (re.compile(r"\bgit\s+push\b"), "git push"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "destructive git reset"),
    (re.compile(r"\bgit\s+clean\s+-\w*f"), "destructive git clean"),
    (re.compile(r":\(\)\s*\{[^}]*\}\s*;\s*:"), "fork bomb"),
]


@dataclass
class ToolDecision:
    action: str  # ACTION_ALLOW | ACTION_CONFIRM | ACTION_DENY
    reason: str


def bash_destructive_reason(command: str) -> Optional[str]:
    """Return a label if the command matches a destructive pattern, else None."""
    for rx, label in _DESTRUCTIVE_PATTERNS:
        if rx.search(command):
            return label
    return None


def bash_uses_videocompose_render(command: str) -> bool:
    """True if a Bash command renders via VideoCompose `render_proxies`. That path
    must go through the in-process `render` tool instead — rendering through
    background Bash makes the CLI auto-resume the agent in an unsolicited turn,
    which breaks message attribution (the off-by-one). Marker is specific
    (VideoCompose AND render_proxies), so non-render ffmpeg/remotion calls and
    other video_compose operations (compose/encode/burn_subtitles) are untouched."""
    return bool(re.search(r"render_proxies", command) and re.search(r"VideoCompose|video_compose", command))


# Heavy media tools that re-encode video — long enough that the Claude CLI auto-detaches
# them to a background task and ENDS the turn (the off-by-one). Steer them to the in-process
# `run_media_op` tool (blocks the turn, answer stays live).
_HEAVY_OP_MARKERS = re.compile(
    r"tools\.(?:video|audio)\.(?:silence_cutter|motion_ops|auto_reframe|object_cutout)"
    r"|\b(?:SilenceCutter|MotionOps)\b"
    r"|registry\.get\(\s*['\"](?:silence_cutter|motion_ops)['\"]\s*\)"
)


def bash_runs_heavy_media_op(command: str) -> Optional[str]:
    """Return a label if a Bash command RUNS a heavy media op (silence_cutter, motion_ops, …)
    via `python … .execute(…)`, else None. Such a re-encode outlives the CLI's foreground Bash
    timeout, so the CLI auto-backgrounds it and ends the turn — the exact detach that broke
    test-proj-2. Steer these to the in-process `run_media_op` tool instead.

    Gated on `.execute(` so introspection / quick calls never match: `ffprobe`,
    `scripts/update_stage.py`, `registry.discover()`, and `registry.get('silence_cutter').get_info()`
    all lack `.execute(` → ALLOW. This is a STEER, not the correctness guarantee — the run_turn
    drain catches any long op this pattern misses (e.g. an arbitrary user script)."""
    if ".execute(" not in command:
        return None
    m = _HEAVY_OP_MARKERS.search(command)
    return m.group(0) if m else None


# --------------------------------------------------------------------------
# Filesystem sandbox — keep the agent inside the app's OWN folders.
#
# The agent's cwd (repo/bundle code root) and the SDK ``add_dirs`` are only
# workspace HINTS; they do not stop the agent from reading ~/Documents or
# /etc/passwd. In the packaged Mac app that let the bundled agent "wander" the
# user's disk. So the permission policy enforces a hard boundary: file tools may
# only touch paths under one of the app's own roots (code root, the writable
# data home, project data, caches, and ephemeral system temp). ON BY DEFAULT in
# both the packaged app and a dev checkout (OPN-10); opt out for a session with
# OPENNOLAN_AGENT_SANDBOX=0.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Sandbox:
    """A filesystem boundary. ``base`` is the agent cwd (used to resolve relative
    paths); ``roots`` are the only directory trees the agent may read/write."""

    base: Path
    roots: tuple[Path, ...]


# File tools whose input names a path we must keep in-bounds.
_PATH_TOOLS = frozenset(
    {
        "Read",
        "Write",
        "Edit",
        "MultiEdit",
        "NotebookRead",
        "NotebookEdit",
        "LS",
        "Glob",
        "Grep",
    }
)
# Harmless device paths a shell command may legitimately reference.
_BASH_OK_PATHS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})


# Path-like tokens in a shell command: ~…, $HOME…, /abs…, ./rel…, ../rel…
def _truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_under(path_str: str, base: Path, *, expand_vars: bool = False) -> Path:
    """Resolve a possibly-relative, ~-bearing path against ``base`` (the agent
    cwd), following symlinks and collapsing ``..``. Never raises.

    ``expand_vars`` also expands shell variables ($HOME, $TMPDIR, …) — used only
    when scanning a Bash command, so ``cat $HOME/.ssh/id_rsa`` resolves to the
    real home (out of bounds) instead of a literal ``$HOME`` dir under ``base``.
    """
    s = os.path.expandvars(path_str) if expand_vars else path_str
    p = Path(os.path.expanduser(s))
    if not p.is_absolute():
        p = base / p
    try:
        return p.resolve()
    except Exception:
        return Path(os.path.normpath(str(p)))


def _within(path_str: str, sandbox: Sandbox, *, expand_vars: bool = False) -> bool:
    p = _resolve_under(path_str, sandbox.base, expand_vars=expand_vars)
    for r in sandbox.roots:
        if p == r or r in p.parents:
            return True
    return False


def _tool_paths(tool_name: str, ti: dict[str, Any]) -> list[str]:
    """The filesystem paths a given tool call would touch."""
    out: list[str] = []

    def add(v: Any) -> None:
        if isinstance(v, str) and v.strip():
            out.append(v)

    if tool_name in ("Read", "Write", "Edit", "MultiEdit", "NotebookRead", "NotebookEdit"):
        add(ti.get("file_path"))
        add(ti.get("notebook_path"))
    elif tool_name == "LS":
        add(ti.get("path"))
    elif tool_name in ("Glob", "Grep"):
        add(ti.get("path"))
        pat = ti.get("pattern")
        # An absolute glob/regex root can escape too (e.g. "/Users/**").
        if isinstance(pat, str) and (pat.startswith("/") or pat.startswith("~")):
            add(pat)
    return out


def _bash_tokens(command: str) -> list[str]:
    """Tokenize a shell command RESPECTING quotes, so a quoted path containing spaces stays ONE
    token (e.g. "~/Library/Application Support/…" — the app's own data dir has a space in it).
    Tolerant of unbalanced quotes / odd syntax (an unterminated `python -c "…` is common): we keep
    whatever fully-formed tokens were lexed before the error. Best-effort by design — the file-tool
    boundary in decide_tool is the hard guarantee."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)  # split off ; | & < > ( )
        lex.whitespace_split = True
        out: list[str] = []
        try:
            for tok in lex:
                out.append(tok)
        except ValueError:
            pass  # unterminated quote / bad syntax — return the prefix we managed to lex
        return out
    except Exception:
        return command.split()


def _looks_like_path(s: str) -> bool:
    """A token worth boundary-checking: an absolute path, ~, ./ or ../, or a $HOME-rooted path.
    A bare relative token (e.g. `projects/x`) resolves under the in-bounds cwd, so it's not flagged
    here (mirrors the old regex, which only matched these same prefixes)."""
    return s.startswith(("/", "~", "./", "../", "$HOME", "${HOME}"))


def _path_candidates(token: str):
    """The path-like strings hiding in a token: the token itself, plus the value of an
    assignment/flag (`VAR=/path`, `--out=/path`). The `=` split is gated to a real var/flag head so
    a URL like `https://x/api?a=/y` isn't mis-split into a bogus `/y` path."""
    yield token
    head, sep, tail = token.partition("=")
    if sep and tail and re.fullmatch(r"[-A-Za-z_][\w.-]*", head):
        yield tail


def bash_path_escape_reason(command: str, sandbox: Sandbox) -> Optional[str]:
    """Return the first path-like token in a shell command that resolves OUTSIDE
    the sandbox, else None.

    Best-effort STATIC analysis: it catches the obvious escapes (absolute paths
    to other folders, ~, $HOME) but a shell can hide paths behind variables or
    substitution, so this is defense-in-depth — the file-tool boundary is the
    hard guarantee."""
    for token in _bash_tokens(command):
        for cand in _path_candidates(token):
            cand = cand.rstrip(".,:;")
            if not cand or cand.startswith("//"):  # "//" → URL authority, not a path
                continue
            if not _looks_like_path(cand):
                continue
            if cand in _BASH_OK_PATHS:
                continue
            if not _within(cand, sandbox, expand_vars=True):
                return cand
    return None


def _temp_roots() -> tuple[Path, ...]:
    """Resolved roots that mark a path as ephemeral staging: store_asset MOVES
    files from these into the project (the staging copy is litter once placed)
    and copies from everywhere else. Both sides of the comparison are resolved
    because macOS symlinks /tmp -> /private/tmp and /var/folders ->
    /private/var/folders. Computed per call — cache routing may set
    OPENNOLAN_CACHE_DIR after import."""
    from lib import app_paths

    candidates: list[Path | str] = [
        tempfile.gettempdir(),
        "/tmp",
        "/private/tmp",
        "/var/folders",
        app_paths.cache_dir() / "scratch",
    ]
    roots: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        try:
            r = Path(c).resolve()
        except Exception:
            continue
        if str(r) not in seen:
            seen.add(str(r))
            roots.append(r)
    return tuple(roots)


def build_sandbox(
    repo_root: Path | str,
    projects_dir: Path | str | None,
) -> Optional[Sandbox]:
    """The filesystem boundary for a run, or None to run unsandboxed.

    ON BY DEFAULT everywhere — packaged app AND dev checkout (OPN-10: the agent
    must not read outside the app's own folders in either mode). Opt out with an
    explicitly falsy OPENNOLAN_AGENT_SANDBOX ("0"/"false"/"no"/"off"), which wins
    even in the packaged app. Roots: the app code root, its writable data home
    (projects/.env/runtime/cache), the project data dir, and ephemeral system
    temp (media tools stage scratch files there)."""
    from lib import app_paths

    if app_paths.env_flag("OPENNOLAN_AGENT_SANDBOX") is False:
        return None

    base = Path(repo_root).resolve()
    candidates: list[Path | str] = [
        base,
        app_paths.code_root(),
        app_paths.home(),
        app_paths.projects_dir(),
        app_paths.runtime_dir(),
        app_paths.cache_dir(),
    ]
    if projects_dir is not None:
        candidates.append(Path(projects_dir))
    # ephemeral scratch the media/generation tools legitimately use
    candidates += [tempfile.gettempdir(), "/tmp", "/private/tmp", "/var/folders"]

    roots: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        try:
            r = Path(c).resolve()
        except Exception:
            continue
        if str(r) not in seen:
            seen.add(str(r))
            roots.append(r)
    return Sandbox(base=base, roots=tuple(roots))


# ── agent telemetry classifiers ──────────────────────────────────────────────
# Every one of these collapses something UNBOUNDED (a Bash command, a tool name, a filesystem
# path, a reason sentence) into a closed vocabulary BEFORE it can reach capture(). None of the
# raw values ever ship: a Bash command carries paths and content, and a path carries a username.

_ROUTED_MARKERS = frozenset({"silence_cutter", "motion_ops", "auto_reframe", "object_cutout"})

# Raw ffmpeg is a MISSING TOOL, named. The family says which tool to build; the command says
# which project the user is working on, so only the family goes out.
_FFMPEG_FILTERS = (
    ("overlay", ("overlay=",)),
    ("scale", ("scale=", "scale2ref")),
    ("concat", ("concat",)),
    ("atempo", ("atempo=", "asetrate")),
    ("zscale", ("zscale",)),
    ("drawtext", ("drawtext",)),
    ("crop", ("crop=",)),
    ("xfade", ("xfade",)),
)


def _ffmpeg_filter_family(command: str) -> Optional[str]:
    c = str(command or "").lower()
    if "ffmpeg" not in c:
        return None
    for family, markers in _FFMPEG_FILTERS:
        if any(m in c for m in markers):
            return family
    return "other"


def _known_or_hashed(tool_name: str) -> str:
    """A tool id we classify, or a stable hash of one we do not. An unknown tool name is
    externally authored — an MCP server we did not write — so it is not a safe vocabulary."""
    name = str(tool_name or "")
    if name in SAFE_TOOLS or name in WRITE_TOOLS or name.startswith("mcp__mc__") or name in ("Bash", "AskUserQuestion"):
        return name
    import hashlib

    return "h" + hashlib.sha256(name.encode()).hexdigest()[:12]


def _permission_reason_class(reason: str) -> str:
    """decide_tool's `reason` is a full English sentence written for the agent — it embeds the
    flagged path and the heavy-op name. The class is what the sandbox question is sliced by."""
    r = str(reason or "").lower()
    if "unrecognized" in r:
        return "unrecognized"
    if "outside the app workspace" in r:
        return "path_escape"
    if "render via the in-process" in r:
        return "render_route"
    if "heavy media ops" in r:
        return "heavy_media_route"
    return "destructive"


def _root_family(tool_input: dict[str, Any]) -> str:
    """WHICH root the agent reached for, never the path. `/Users/<name>/…` is the username."""
    import re as _re

    blob = " ".join(str(v) for v in (tool_input or {}).values() if isinstance(v, (str, int, float)))
    for match in _re.finditer(r"(?<![\w])(/[A-Za-z0-9_./-]+)", blob):
        p = match.group(1)
        if p.startswith(("/tmp", "/private/tmp", "/var/folders")):
            return "tmp"
        if p.startswith("/Users/") or p.startswith("/home/"):
            return "home"
        if p.startswith(("/System", "/Library", "/usr", "/bin", "/sbin", "/etc")):
            return "system"
    return "other"


def _bucket_seconds(seconds: float) -> str:
    for edge in (5, 30, 120, 600):
        if seconds < edge:
            return f"{0 if edge == 5 else {30: 5, 120: 30, 600: 120}[edge]}-{edge}"
    return "600+"


def decide_tool(
    tool_name: str,
    tool_input: dict[str, Any] | None,
    sandbox: Optional[Sandbox] = None,
) -> ToolDecision:
    """Allow safe tools and clean Bash; route destructive Bash + unknown tools to confirm.
    Hard-deny (with a steer to the `render` tool) Bash that renders via VideoCompose.

    When ``sandbox`` is set, file tools that target a path OUTSIDE the app's own
    folders are hard-denied (with a steering message), and Bash commands that
    reference an out-of-bounds path are routed to confirm. ``sandbox=None`` (the
    dev default) disables path enforcement entirely."""
    ti = tool_input or {}
    # Sandbox: keep the agent inside the app's own folders. File tools name their
    # target directly, so an out-of-bounds path is an unambiguous, hard deny.
    if sandbox is not None and tool_name in _PATH_TOOLS:
        for pth in _tool_paths(tool_name, ti):
            if not _within(pth, sandbox):
                return ToolDecision(
                    ACTION_DENY,
                    f"Path {pth!r} is outside the app workspace. Only read or write "
                    "within the project directory and the app's own folders.",
                )
    if tool_name in SAFE_TOOLS or tool_name in WRITE_TOOLS:
        return ToolDecision(ACTION_ALLOW, f"{tool_name} is a safe/standard tool")
    # Question/render tools are always allowed — our in-process mc tools are safe.
    # Covers the built-in AskUserQuestion and our ask_user / render MCP tools.
    if tool_name == "AskUserQuestion" or tool_name.startswith("mcp__mc__"):
        return ToolDecision(ACTION_ALLOW, "in-process mc tool (ask_user / render)")
    if tool_name == "Bash":
        command = ti.get("command", "") or ""
        if bash_uses_videocompose_render(command):
            analytics.capture("agent_rendered_via_bash", {})
            return ToolDecision(
                ACTION_DENY,
                "Render via the in-process `render` tool, not Bash/VideoCompose — "
                "background renders break turn attribution. Call the `render` tool "
                "with edit_decisions/asset_manifest/proposal_packet.",
            )
        heavy_op = bash_runs_heavy_media_op(command)
        if heavy_op:
            analytics.capture(
                "agent_routed_around_us",
                {
                    "marker": heavy_op if heavy_op in _ROUTED_MARKERS else "other",
                    "steered_to": "run_media_op",
                },
            )
            return ToolDecision(
                ACTION_DENY,
                f"Run heavy media ops ({heavy_op}) via the in-process `run_media_op` tool, "
                "not `python … .execute(…)` in Bash — a long re-encode gets auto-backgrounded "
                "and ends your turn, which breaks turn attribution. Call `run_media_op` with "
                "{tool, input}; it blocks and returns the result in this same turn.",
            )
        # Bash is free-form; flag commands that reach outside the app workspace.
        if sandbox is not None:
            escape = bash_path_escape_reason(command, sandbox)
            if escape:
                return ToolDecision(
                    ACTION_CONFIRM,
                    f"Bash flagged: reaches outside the app workspace ({escape})",
                )
        family = _ffmpeg_filter_family(command)
        if family:
            analytics.capture("agent_ffmpeg_freehand", {"filter_family": family})
        label = bash_destructive_reason(command)
        if label:
            return ToolDecision(ACTION_CONFIRM, f"Bash flagged: {label}")
        return ToolDecision(ACTION_ALLOW, "Bash has no destructive markers")
    # Unknown / MCP / other tool -> be conservative.
    # NOT "tool_not_found": this is a conservative fall-through for anything outside
    # SAFE_TOOLS/WRITE_TOOLS/AskUserQuestion/mcp__mc__/Bash, so it includes valid-but-
    # unclassified SDK and MCP tools and does NOT prove registry absence.
    analytics.capture("unrecognized_tool_requested", {"attempted": _known_or_hashed(tool_name)})
    return ToolDecision(ACTION_CONFIRM, f"unrecognized tool {tool_name!r}")


# confirm_handler(tool_name, tool_input, reason) -> approved?
ConfirmHandler = Callable[[str, dict[str, Any], str], Awaitable[bool]]


def make_can_use_tool(
    confirm_handler: Optional[ConfirmHandler] = None,
    sandbox: Optional[Sandbox] = None,
    turn_ctx: Optional[Callable[[], dict[str, Optional[str]]]] = None,
):
    """Build the SDK `can_use_tool` callback from the policy.

    Flagged calls go to `confirm_handler`. With no handler (a fully
    unattended run), flagged calls are DENIED — the safe default. ``sandbox``
    (when set) confines file tools/Bash to the app's own folders.

    ``turn_ctx`` returns the LIVE turn's ``{turn_id, session_id}``. It has to be passed in
    rather than read from the request ContextVar, because this callback runs in the SDK
    client's task — whose context was captured when the client was BUILT. So
    `current_session_id()` here returns whichever session first created the client and keeps
    returning it for every later turn: measured live, 11 permission-family events from three
    different sessions all stamped with the first one, two of them carrying a `turn_id` from a
    session their own `session_id` disagreed with.
    """
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    async def can_use_tool(tool_name: str, tool_input: dict[str, Any], context: Any):
        ctx = (turn_ctx() if turn_ctx is not None else None) or {}
        # Bind for the DURATION of this callback, so the captures inside decide_tool()
        # (agent_rendered_via_bash / agent_routed_around_us / agent_ffmpeg_freehand /
        # unrecognized_tool_requested) are fixed by the same line rather than each needing the
        # id threaded through decide_tool's signature.
        #
        # UNCONDITIONALLY set (None when there is no live turn) and reset in `finally`. This
        # task is the SDK client's and is deliberately long-lived, so a bind that is left
        # standing outlives the turn it belonged to: the next callback with an empty ctx would
        # otherwise inherit the previous turn's session and mis-attribute against it — the same
        # class of bug as the one this whole change fixes, just one layer along.
        token = analytics._session_ctx.set(ctx.get("session_id") or None)
        try:
            return await _decide(tool_name, tool_input, ctx)
        finally:
            analytics._session_ctx.reset(token)

    async def _decide(tool_name: str, tool_input: dict[str, Any], ctx: dict[str, Any]):
        decision = decide_tool(tool_name, tool_input, sandbox)
        if decision.action == ACTION_ALLOW:
            return PermissionResultAllow()
        reason_class = _permission_reason_class(decision.reason)
        analytics.capture(
            "tool_permission_decided",
            {
                "turn_id": ctx.get("turn_id"),
                "session_id": ctx.get("session_id"),
                "tool_id": tool_name,
                "action": "deny" if decision.action == ACTION_DENY else "confirm",
                "reason_class": reason_class,
                "root_family": _root_family(tool_input),
            },
        )
        if decision.action == ACTION_DENY:
            return PermissionResultDeny(message=decision.reason)
        if confirm_handler is None:
            return PermissionResultDeny(message=f"Blocked (no confirm handler): {decision.reason}")
        t_wait = time.monotonic()
        approved = await confirm_handler(tool_name, tool_input, decision.reason)
        # If approval runs above ~95%, stop asking and auto-allow the pattern.
        analytics.capture(
            "agent_confirm_resolved",
            {
                "turn_id": ctx.get("turn_id"),
                "session_id": ctx.get("session_id"),
                "tool_id": tool_name,
                "reason_class": reason_class,
                "approved": bool(approved),
                "wait_s": _bucket_seconds(time.monotonic() - t_wait),
                "timed_out": False,
            },
        )
        if approved:
            return PermissionResultAllow()
        return PermissionResultDeny(message=f"Denied by user: {decision.reason}")

    return can_use_tool


AGENT_SYSTEM_PROMPT = """You are the OpenNolan production agent, running HEADLESS. \
The user steers via a Mission Control UI between turns.

Obey the repo contract exactly:
- Read AGENT_GUIDE.md before acting. Follow Rule Zero.
- Read the per-stage director skill before each stage. Read Layer 3 skills before any generation tool.
- LOAD EVERY SKILL WITH THE `Skill` TOOL. Any skill named anywhere in this prompt or in \
AGENT_GUIDE.md — a per-stage director skill, a Layer 3 skill, `opennolan:<name>` — is loaded by \
calling the `Skill` tool with that exact name. "Read the X skill" always means that call. Never \
load a skill's content with Bash (`find`, `cat`, ...) or a direct file Read: the `Skill` tool is \
the only correct way to bring a skill into context.
- Checkpoints and artifacts live under the ABSOLUTE project directory given in your PROJECT CONTEXT \
message — NOT relative to your working directory (which is the read-only app code), and NOT pipelines/.

PIPELINE STATUS (critical — the UI stepper shows this in real time):
  Mark a stage IN PROGRESS at start:
    python scripts/update_stage.py <project_id> <stage> in_progress <pipeline_type>
  Mark COMPLETED with artifacts at end:
    python scripts/update_stage.py <project_id> <stage> completed <pipeline_type>
  Mark AWAITING_HUMAN at approval gates:
    python scripts/update_stage.py <project_id> <stage> awaiting_human <pipeline_type>

At approval gates (human_approval_default: true): STOP and mark awaiting_human. \
The UI will send your next turn when the user approves.

When composing video: present BOTH render runtimes (Remotion AND HyperFrames) and record \
a render_runtime_selection decision listing both in options_considered.

To RENDER (the render-once / render_proxies path), call the `render` tool — it runs in-process, \
BLOCKS until the render finishes, and returns {success, output_path, warnings} so you continue to \
QA in the SAME turn. Pass edit_decisions (with render_runtime + renderer_family locked), \
asset_manifest, and proposal_packet. Do NOT render by running VideoCompose render_proxies via \
`run_in_background` Bash — a background render ends your turn and breaks message attribution.

To run ANY heavy media op (silence removal, speed change, reframe, and other tools.video / \
tools.audio re-encodes), call the `run_media_op` tool with {tool: "<registry name>", input: {...}} \
(e.g. tool="silence_cutter" or "motion_ops"). It runs IN-PROCESS, BLOCKS until it finishes, and \
returns {success, output_path, data, error} so you continue in the SAME turn. Do NOT run these via \
`python -c "...execute(...)"` in Bash — a long re-encode gets auto-backgrounded, which ends your \
turn and breaks message attribution (the answer would arrive a message late). Quick read-only calls \
(ffprobe, scripts/update_stage.py, registry introspection) still run via Bash. After a media op \
produces a file, route it through `store_asset` as usual.

To ask the user a clarifying question, call the `ask_user` tool with your question and a list \
of options (the user picks one in the UI and it comes back as the tool result). Use it whenever \
your skills tell you to ask the user something.

MISSING API KEY: if a generation/media tool fails because a required API key or environment \
variable is NOT set (e.g. it returns "GOOGLE_API_KEY not set", "REPLICATE_API_TOKEN not set", \
"No ElevenLabs API key", "FAL_KEY / FAL_AI_API_KEY not set"), do NOT just tell the user to open \
the BYOK panel. Call the `request_api_key` tool with the EXACT env-var name (plus a short \
provider name and reason) — a secure input appears in the chat. If the user provides the key it \
is saved to their keychain and the tool returns {provided: true}; then RETRY the tool that \
needed it. If it returns {provided: false} the user declined — do NOT retry; use an alternative \
or continue without that capability and tell the user exactly what you skipped.

MISSING LOCAL DEPENDENCY: if a LOCAL (on-device) tool fails because its Python packages are not \
installed (e.g. "faster_whisper not installed", "No module named cv2 / mediapipe / librosa / \
rembg"), do NOT hand-run pip or tell the user to install anything. Call the `request_capability` \
tool with the matching pack — 'transcription' (speech-to-text / captions), 'vision' \
(auto-reframe / face tracking), 'bg-removal', 'beat-sync' (music beats), or 'tts' (local \
text-to-speech). An install card appears in the chat; if the user approves, the pack downloads \
into the managed runtime and the tool returns {installed: true} — then RETRY the tool. If \
{installed: false}, the user declined — do NOT retry; use an alternative or continue without it \
and tell the user what you skipped. (Use request_api_key for missing CLOUD keys; use \
request_capability for missing LOCAL packs — never raw pip.)

To SAVE any file you produce (image/video/audio/music, an intermediate scene clip, or the final \
deliverable), call the `store_asset` tool with its `kind` and `src` — it places the file in the \
correct folder and returns the path to reference in edit_decisions/asset_manifest. NEVER write \
into assets/, hf/renders/, or renders/ by hand and never pass a hand-picked project path to a \
generator; write generated files to a scratch path, then hand them to `store_asset`. This is the \
ONLY correct way to place assets — declaring the wrong folder yourself makes intermediate clips \
show up as the final render in the editor.

To SCHEDULE a completed project, call the `Skill` tool with \
`skill: "opennolan:content-calendar-scheduling"`, then call the \
`schedule_content` tool. It writes the same calendar entry as Mission Control, avoids obvious \
collisions, and can remember a researched per-niche local posting time for future calls. It does \
not publish to a social network.

Announce cost before any paid generation. Never exceed the budget.
"""


# The same locations the Agent SDK searches in subprocess_cli._find_cli, so this
# gate agrees with what the SDK will actually resolve at call time.
_CLI_FALLBACK_LOCATIONS = (
    Path("/usr/local/bin/claude"),
    Path.home() / ".npm-global/bin/claude",
    Path.home() / ".local/bin/claude",
    Path.home() / "node_modules/.bin/claude",
    Path.home() / ".yarn/bin/claude",
    Path.home() / ".claude/local/claude",
)


def claude_cli_available() -> bool:
    """True if the `claude` CLI is resolvable on this machine.

    The Agent SDK drives this CLI as a subprocess, and the CLI authenticates from
    its OWN stored login (e.g. the macOS Keychain) when no env credential is set.
    So a resolvable CLI means the agent can run even without CLAUDE_CODE_OAUTH_TOKEN
    — a user already logged into Claude Code needs no env token. (Verified: an SDK
    query with both auth env vars unset returns a normal response on a logged-in
    machine.) CLI present-but-not-logged-in is rare for a Claude Code user; if it
    happens, the agent call surfaces the real auth error rather than a blank 503.
    """
    if shutil.which("claude"):
        return True
    return any(p.is_file() for p in _CLI_FALLBACK_LOCATIONS)


def auth_configured() -> bool:
    """True if the agent has a way to authenticate.

    Either an explicit env credential (CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY)
    OR a resolvable `claude` CLI that self-authenticates from its stored login.
    """
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"):
        return True
    return claude_cli_available()


def agent_subprocess_path() -> str:
    """The PATH the agent's Bash subprocess should use.

    The agent runs `python …` (preflight, scripts/update_stage.py, tool one-liners).
    Its cwd is the read-only code root, and in the packaged app the interpreter that
    actually has our dependencies is the managed venv under OPENNOLAN_HOME/runtime —
    which is NOT on the inherited PATH (desktop/main.js puts ffmpeg + node there, not
    the venv). Without this, the agent's bare `python` resolves to whatever Python is
    on the user's machine, which has none of OpenNolan's deps — that is OPN-4: the
    bundled agent "gets lost" hunting for Python/tools across the whole device.

    We prepend the venv's bin so `python` is the app's Python. The SDK merges this
    over os.environ (our PATH wins), so the inherited ffmpeg (runtime/bin), node
    (Resources/node/bin), and system entries are preserved after it. Dev-safe: the
    venv bin does not exist in a plain checkout, so resolution simply falls through
    to the active dev venv already on PATH.
    """
    from lib import app_paths

    venv_bin = app_paths.runtime_dir() / "venv" / "bin"
    inherited = os.environ.get("PATH", "")
    return f"{venv_bin}{os.pathsep}{inherited}" if inherited else str(venv_bin)


def agent_add_dirs(projects_dir: Path | str | None) -> list[str]:
    """Extra workspace directories the agent's file tools may operate in.

    The agent's cwd is the (read-only) code root, but project data — artifacts and
    checkpoints — lives under projects_dir (the writable App-Support dir in the
    packaged app, which is OUTSIDE the code root). Expose it as an explicit workspace
    dir so Read/Write/Glob of those files are first-class rather than out-of-workspace.

    Guarded on existence: a `--add-dir` that points at a nonexistent path aborts the
    CLI launch, so we only add it when it is really there (the app creates the
    projects dir before any agent turn; dev/tests may not).
    """
    if projects_dir is None:
        return []
    p = Path(projects_dir)
    return [str(p)] if p.exists() else []


def app_skills_plugin_dir(repo_root: Path | str) -> Path:
    """The plugin root holding the agent's video-production skills (OPN-41).

    A "plugin" here is just a directory with `.claude-plugin/plugin.json` beside a
    `skills/` folder; the SDK is handed the path and discovers the skills inside it.
    This is the ONLY way the packaged app can expose skills: the CLI otherwise looks
    only in `<cwd>/.claude/skills`, and the packaged cwd is `Resources/backend`.

    Kept separate from `.agents/skills` (the coding skills, which Codex reads from the
    repo root) so the two audiences cannot see each other's skills.
    """
    return Path(repo_root) / ".agents" / "app"


def build_agent_options(
    repo_root: Path | str,
    *,
    projects_dir: Path | str | None = None,
    model: str = DEFAULT_MODEL,
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
    confirm_handler: Optional[ConfirmHandler] = None,
    resume: Optional[str] = None,
    mcp_servers: Optional[dict[str, Any]] = None,
    disallowed_tools: Optional[list[str]] = None,
    turn_ctx: Optional[Callable[[], dict[str, Optional[str]]]] = None,
    stderr: Optional[Callable[[str], None]] = None,
):
    """Construct ClaudeAgentOptions for an OpenNolan agent session.

    ``projects_dir`` is the writable location of project artifacts/checkpoints. It is
    added to the agent's workspace (``add_dirs``) so file tools can reach it even
    though it lives outside the read-only ``repo_root`` cwd (packaged app).

    ``resume`` is a prior session_id. When set, the SDK restores that
    conversation's full history into the new client, so a session that died
    (transport crash, budget ceiling) comes back with its context intact —
    the agent remembers what it was doing, on a fresh budget.

    ``mcp_servers`` registers in-process tools (the ask_user question tool).
    ``disallowed_tools`` steers the agent away from the built-in AskUserQuestion
    (whose headless I/O we don't control) toward our ask_user tool.
    """
    from claude_agent_sdk import ClaudeAgentOptions
    from lib import app_paths

    # Loud, non-fatal signal if the managed venv is missing in a packaged run
    # (OPENNOLAN_CODE_ROOT is set only by desktop/main.js in the .app). Better a
    # visible warning in the backend log than a silent fall-back to system Python.
    if os.environ.get("OPENNOLAN_CODE_ROOT"):
        venv_python = app_paths.runtime_dir() / "venv" / "bin" / "python"
        if not venv_python.exists():
            print(
                f"[agent_runner] WARNING: managed venv Python not found at {venv_python}; "
                "the agent's `python` may fall back to system Python.",
                file=sys.stderr,
            )

    # Confine the agent's file tools + Bash to the app's own folders. Default-ON
    # everywhere (OPN-10); None only when OPENNOLAN_AGENT_SANDBOX is explicitly falsy.
    sandbox = build_sandbox(repo_root, projects_dir)
    if sandbox is not None:
        print(
            "[agent_runner] filesystem sandbox ON; agent confined to: " + ", ".join(str(r) for r in sandbox.roots),
            file=sys.stderr,
        )

    return ClaudeAgentOptions(
        cwd=str(repo_root),
        system_prompt=AGENT_SYSTEM_PROMPT,
        model=model,
        max_budget_usd=max_budget_usd,
        permission_mode="default",  # so can_use_tool is consulted
        # Skills come from the bundled `.agents/app` plugin, never from the
        # filesystem settings (OPN-41). `setting_sources=[]` is load-bearing:
        # with "project" the dev-mode agent (cwd == repo root) would also pick up
        # the repo's own .claude/skills — the CODING skills for Claude/Codex —
        # which have nothing to do with making a video. The plugin ships inside
        # the .app, so dev and packaged now discover the SAME set.
        setting_sources=[],
        plugins=[{"type": "local", "path": str(app_skills_plugin_dir(repo_root))}],
        skills="all",
        can_use_tool=make_can_use_tool(confirm_handler, sandbox, turn_ctx),
        resume=resume,
        mcp_servers=mcp_servers or {},
        disallowed_tools=disallowed_tools or [],
        # Make the agent's `python` the app's Python, and let it write to the
        # writable projects dir that sits outside its read-only cwd (OPN-4).
        env={"PATH": agent_subprocess_path()},
        add_dirs=agent_add_dirs(projects_dir),
        # LOAD-BEARING for diagnosability: the SDK pipes the CLI's stderr ONLY when a callback is
        # registered, and reports a dead CLI as "Command failed with exit code 1 / Check stderr
        # output for details" — with nothing else. Without this, the one place that says WHY the
        # agent could not start is thrown away (that is how a bundled-CLI crash reached users as
        # an error naming nothing).
        stderr=stderr,
    )


# --------------------------------------------------------------------------
# Event normalization — SDK messages -> JSON-serializable dicts for SSE.
# --------------------------------------------------------------------------


def _truncate_input(tool_input: dict[str, Any] | None, limit: int = 4000) -> dict[str, Any]:
    """Truncate large string fields. Limit is generous so the UI can expand a
    tool call and see what the agent actually did (full command, file content)."""
    if not isinstance(tool_input, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in tool_input.items():
        if isinstance(v, str) and len(v) > limit:
            out[k] = v[:limit] + "\n… (truncated)"
        else:
            out[k] = v
    return out


def _tool_detail(name: str, inp: dict[str, Any]) -> dict[str, Any]:
    """Extract the most useful display field from a tool's input."""
    if name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        return {"label": inp.get("file_path") or inp.get("path") or ""}
    if name == "Bash":
        cmd = (inp.get("command") or "").strip()
        # first non-empty line, max 120 chars
        first = next((l.strip() for l in cmd.splitlines() if l.strip()), cmd)
        return {"label": first[:120] + ("…" if len(first) > 120 else "")}
    if name == "Glob":
        return {"label": inp.get("pattern") or ""}
    if name == "Grep":
        return {"label": inp.get("pattern") or inp.get("query") or ""}
    if name == "WebSearch":
        return {"label": inp.get("query") or ""}
    if name == "WebFetch":
        url = inp.get("url") or ""
        return {"label": url[:80] + ("…" if len(url) > 80 else "")}
    if name == "Skill":
        return {"label": inp.get("skill") or inp.get("name") or ""}
    return {"label": ""}


def event_of(message: Any) -> dict[str, Any]:
    """Normalize one SDK message into a serializable event dict."""
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    if isinstance(message, AssistantMessage):
        items: list[dict[str, Any]] = []
        for block in message.content or []:
            if isinstance(block, TextBlock):
                items.append({"kind": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                trunc = _truncate_input(block.input)
                detail = _tool_detail(block.name, block.input or {})
                items.append(
                    {
                        "kind": "tool_use",
                        "name": block.name,
                        "id": block.id,
                        "input": trunc,
                        "detail": detail.get("label", ""),
                    }
                )
            elif isinstance(block, ToolResultBlock):
                # surface errors; truncate large success output
                content = block.content
                if isinstance(content, str) and len(content) > 2000:
                    content = content[:2000] + "\n… (truncated)"
                items.append(
                    {
                        "kind": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "is_error": block.is_error,
                        "content": content,
                    }
                )
            elif isinstance(block, ThinkingBlock):
                items.append({"kind": "thinking"})
        return {"type": "assistant", "items": items}
    if isinstance(message, UserMessage):
        # Tool results coming back from the environment — emit as activity
        items = []
        for block in message.content if isinstance(message.content, list) else []:
            if isinstance(block, ToolResultBlock):
                content = block.content
                if isinstance(content, str) and len(content) > 2000:
                    content = content[:2000] + "\n… (truncated)"
                items.append(
                    {
                        "kind": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "is_error": block.is_error,
                        "content": content,
                    }
                )
        if items:
            return {"type": "assistant", "items": items}
        return {"type": "other", "repr": "UserMessage"}
    if isinstance(message, ResultMessage):
        return {
            "type": "result",
            "is_error": message.is_error,
            "num_turns": message.num_turns,
            "total_cost_usd": message.total_cost_usd,
            "result": message.result,
            "stop_reason": message.stop_reason,
            "session_id": message.session_id,
        }
    if isinstance(message, SystemMessage):
        return {"type": "system", "subtype": message.subtype}
    return {"type": "other", "repr": type(message).__name__}


@dataclass
class TurnResult:
    text: str
    is_error: bool
    num_turns: int
    total_cost_usd: Optional[float]


# ── turn telemetry reducers ───────────────────────────────────────────────────
# All local. Per-tool percentiles are computed HERE so tool latency survives without a
# per-call upload — a 6-turn session with 20 tools each would otherwise breach the
# per-session event ceiling on its own.

_MCP_PREFIX = "mcp__mc__"


def _tool_family(tool_id: str) -> str:
    """Coarse, closed vocabulary — the raw tool id is already bounded, the family is what
    makes 'is the agent living in Bash?' answerable in one filter."""
    if tool_id.startswith(_MCP_PREFIX):
        return "opennolan"
    if tool_id in ("Bash", "BashOutput", "KillShell"):
        return "shell"
    if tool_id in ("Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep"):
        return "file"
    if tool_id in ("WebFetch", "WebSearch"):
        return "web"
    if tool_id in ("Task", "Skill", "TodoWrite", "AskUserQuestion"):
        return "orchestration"
    return "other"


def _percentile(sorted_vals: list[int], q: float) -> int:
    """Nearest-rank percentile. Exact for the handful of calls a turn makes, and it needs
    no dependency — statistics.quantiles interpolates and misbehaves under 2 samples."""
    if not sorted_vals:
        return 0
    idx = max(0, min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


class _TurnTools:
    """The pending map that joins `tool_use` to `tool_result` within ONE turn.

    The SDK splits a tool call across two blocks with different fields — `tool_use` has the
    name, `tool_result` has only the id and `is_error` — so nothing downstream could ever say
    which tool failed. Leftovers at turn end are real signal, not bookkeeping: they are calls
    that never produced a result."""

    def __init__(self) -> None:
        self.pending: dict[str, tuple[str, float]] = {}
        self.seen: set[str] = set()
        self.per_tool: dict[str, dict[str, Any]] = {}
        self.calls = 0
        self.errors = 0
        self.orphan_results = 0
        self.duplicate_results = 0

    def started(self, tool_use_id: Optional[str], tool_id: str) -> None:
        if not tool_use_id:
            return
        self.pending[tool_use_id] = (tool_id or "unknown", time.monotonic())

    def finished(self, tool_use_id: Optional[str], is_error: bool) -> Optional[dict[str, Any]]:
        """Resolve one result. Returns the failure detail when it was an error, else None."""
        if not tool_use_id:
            return None
        if tool_use_id in self.seen:
            self.duplicate_results += 1
            return None
        hit = self.pending.pop(tool_use_id, None)
        if hit is None:
            # Cause deliberately UNLABELLED until measured — a plausible story (a drained
            # turn) is not evidence, and a wrong label here would be worse than a count.
            self.orphan_results += 1
            return None
        self.seen.add(tool_use_id)
        tool_id, t_start = hit
        duration_ms = int((time.monotonic() - t_start) * 1000)
        self._record(tool_id, duration_ms, is_error)
        if not is_error:
            return None
        return {"tool_id": tool_id, "family": _tool_family(tool_id), "duration_ms": duration_ms}

    def _record(self, tool_id: str, duration_ms: int, is_error: bool) -> None:
        self.calls += 1
        entry = self.per_tool.setdefault(tool_id, {"calls": 0, "errors": 0, "_ms": []})
        entry["calls"] += 1
        entry["_ms"].append(duration_ms)
        if is_error:
            self.errors += 1
            entry["errors"] += 1

    def close(self) -> list[dict[str, Any]]:
        """Synthesize the leftovers as `no_result` and RETURN them.

        A tool call that never produced a result is a 100%-upload failure like any other, so
        the caller emits one `agent_tool_failed{outcome='no_result'}` per orphan. Counting
        them into the rollup alone would have left the most interesting failure class — the
        tool the agent gave up waiting on — visible only as an unexplained error total."""
        orphans = []
        for tool_id, t_start in list(self.pending.values()):
            duration_ms = int((time.monotonic() - t_start) * 1000)
            self._record(tool_id, duration_ms, is_error=True)
            orphans.append(
                {
                    "tool_id": tool_id,
                    "family": _tool_family(tool_id),
                    "duration_ms": duration_ms,
                }
            )
        self.pending.clear()
        return orphans

    def rollup(self) -> dict[str, Any]:
        tools: dict[str, dict[str, int]] = {}
        for tool_id, e in self.per_tool.items():
            ms = sorted(e["_ms"])
            tools[tool_id] = {
                "calls": e["calls"],
                "errors": e["errors"],
                "p50_ms": _percentile(ms, 0.5),
                "p95_ms": _percentile(ms, 0.95),
                "max_ms": ms[-1] if ms else 0,
            }
        return {
            "tools": tools,
            "unique_tools": len(tools),
            "calls": self.calls,
            "errors": self.errors,
            "bash_calls": sum(e["calls"] for t, e in tools.items() if _tool_family(t) == "shell"),
        }


def _doc_snapshot(projects_dir: Optional[Path], project_id: str) -> dict[str, int]:
    """Counts + a hash of the timeline, taken before and after a turn.

    Authorship is detected by DIFF, not by route: the agent writes edit_decisions.json
    directly (RULES.md), never through the editor's PUT, so `author='agent'` was never
    observable at any HTTP boundary. Cheap and defensive — a missing/corrupt doc is a
    zero snapshot, never an exception on the agent's hot path."""
    snap = {"hash": 0, "cuts": 0, "overlays": 0, "audio": 0, "artifacts": 0}
    try:
        base = Path(projects_dir or ".") / project_id
        raw = (base / "artifacts" / "edit_decisions.json").read_bytes()
        snap["hash"] = hash(raw)
        doc = json.loads(raw)
        snap["cuts"] = len(doc.get("cuts") or [])
        snap["overlays"] = len(doc.get("overlays") or [])
        audio = doc.get("audio") if isinstance(doc.get("audio"), dict) else {}
        music = audio.get("music")
        snap["audio"] = (
            (len(music) if isinstance(music, list) else 1 if music else 0)
            + len((audio.get("narration") or {}).get("segments") or [])
            + len(audio.get("sfx") or [])
        )
    except Exception:
        pass
    try:
        snap["artifacts"] = sum(1 for _ in (Path(projects_dir or ".") / project_id / "artifacts").iterdir())
    except Exception:
        pass
    return snap


def _classify_turn_error(exc: BaseException) -> str:
    """Bounded failure class. NEVER the exception text — it carries prompts and paths."""
    name = type(exc).__name__.lower()
    text = f"{name} {exc}".lower()[:400]
    if any(w in text for w in ("auth", "401", "403", "credential", "oauth")):
        return "auth"
    if any(w in text for w in ("budget", "quota", "429", "credit")):
        return "budget"
    if any(w in text for w in ("timeout", "connection", "socket", "broken pipe", "transport")):
        return "transport"
    if "cancel" in text:
        return "cancelled"
    if "sdk" in text or "claude" in text:
        return "sdk"
    return "unknown"


EmitFn = Callable[[dict[str, Any]], Any]  # sync or async


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value


def _text_result(payload: dict[str, Any]) -> dict[str, Any]:
    """An MCP tool result carrying a JSON payload. Marks is_error when the
    payload reports an error, so the agent treats a failure as a failure."""
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "is_error": "error" in payload}


@dataclass
class AgentRunner:
    """Manages one persistent agent session per project.

    A session is created lazily on the first turn and reused, so conversation
    context (and the awaiting_human -> approve -> resume flow) survives across
    turns. ``client_factory(project_id)`` is injectable for tests.
    """

    repo_root: Path
    model: str = DEFAULT_MODEL
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD
    confirm_timeout_s: int = DEFAULT_CONFIRM_TIMEOUT_S
    answer_timeout_s: int = DEFAULT_ANSWER_TIMEOUT_S
    client_factory: Optional[Callable[[str], Any]] = None
    # Shared RenderJobStore (injected from app). The in-process `render` tool drives
    # it so renders are tracked/superseded instead of run via background Bash (which
    # broke turn attribution). None in tests/no-auth -> the render tool errors cleanly.
    render_store: Optional[Any] = None
    render_timeout_s: int = 1800  # 30 min cap on a single in-turn render await
    render_poll_interval_s: float = 0.5  # how often the render tool polls job status
    # Drain of stray/unsolicited turns before each user turn (the off-by-one safety net).
    drain_idle_timeout_s: float = 0.15  # how long to wait for a buffered msg when no turn is open
    drain_result_timeout_s: float = 5.0  # once a stray turn is mid-stream, how long to wait for its result
    # WHERE the agent's project artifacts/checkpoints live. Injected from app.create_app so the
    # agent and the read layer agree; defaults to <repo_root>/projects when omitted (dev + tests).
    # In the packaged app this is the writable App-Support projects dir, NOT inside the bundle.
    projects_dir: Optional[Path] = None

    _clients: dict[str, Any] = field(default_factory=dict, init=False)
    # Recent stderr from each project's Claude CLI subprocess (see _record_cli_stderr).
    _cli_stderr: dict[str, deque] = field(default_factory=dict, init=False)
    _emit: dict[str, EmitFn] = field(default_factory=dict, init=False)
    _pending: dict[str, asyncio.Future] = field(default_factory=dict, init=False)
    _confirm_seq: int = field(default=0, init=False)
    _answers: dict[str, asyncio.Future] = field(default_factory=dict, init=False)  # question_id -> answer future
    _question_seq: int = field(default=0, init=False)
    _key_requests: dict[str, asyncio.Future] = field(
        default_factory=dict, init=False
    )  # key_request_id -> provided(bool) future
    _key_seq: int = field(default=0, init=False)
    _cap_requests: dict[str, asyncio.Future] = field(
        default_factory=dict, init=False
    )  # cap_request_id -> installed(bool) future
    _cap_seq: int = field(default=0, init=False)
    _session_ids: dict[str, str] = field(default_factory=dict, init=False)  # last session_id per project
    _resume_next: dict[str, bool] = field(default_factory=dict, init=False)  # rebuild-with-resume after error
    _fresh_client: dict[str, bool] = field(default_factory=dict, init=False)  # client just (re)created this turn
    _models: dict[str, str] = field(default_factory=dict, init=False)  # UI-selected model per project
    # The live turn's join keys per project: {turn_id, session_id}. Renders and media ops the
    # agent starts mid-turn read them from here, which is how a background render job ends up
    # attributable to the session (and the turn) that caused it.
    # The live turn's {turn_id, session_id}, per project. Read by the SDK permission callback
    # and the MCP tool handlers, which run in the CLIENT's task and therefore cannot use the
    # request ContextVar (it was captured when the client was built, so it names whichever
    # session first created it — measured live, 11 permission events across 3 sessions all
    # stamped with the first).
    #
    # KNOWN LIMIT — one slot per project, so two CONCURRENT turns on the same project still
    # misattribute: the second overwrites the first's context, and whichever finishes first
    # pops it, leaving the other's callbacks reading {}. Nothing serializes turns per project
    # at the API layer today (the UI's `busy` flag is a client convention, not a server
    # guarantee). Fixing that properly means a per-project turn lock or threading an immutable
    # context through the callback lifecycle — a turn-concurrency change, not an analytics one,
    # so it is named here rather than half-done. Sequential turns, the real usage pattern, are
    # correct; the empty-context case degrades to NO session rather than a stale one.
    _turn_ctx: dict[str, dict[str, Optional[str]]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)
        # Default projects under the code root (behavior-preserving for dev + tests); app injects
        # the App-Support dir in prod so agent writes never target the read-only bundle.
        self.projects_dir = Path(self.projects_dir) if self.projects_dir is not None else self.repo_root / "projects"
        if self.client_factory is None:
            self.client_factory = self._default_client_factory

    def _default_client_factory(self, project_id: str) -> Any:
        from claude_agent_sdk import ClaudeSDKClient, create_sdk_mcp_server, tool

        async def confirm(tool_name: str, tool_input: dict[str, Any], reason: str) -> bool:
            return await self._confirm(project_id, tool_name, tool_input, reason)

        # ask_user: the agent's question tool. Surfaces the question + options to
        # the UI and blocks until the user picks one, then returns that as the
        # tool result. The schema's "options" are the choices shown as buttons.
        @tool(
            "ask_user",
            "Ask the user a clarifying question and get their chosen answer. "
            "Provide the question and a list of option strings; the user's pick is returned.",
            {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question to ask."},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Answer choices to offer.",
                    },
                    "header": {"type": "string", "description": "Short label/topic for the question."},
                },
                "required": ["question", "options"],
            },
        )
        async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
            answer = await self._ask_user(
                project_id,
                args.get("header", ""),
                args.get("question", ""),
                args.get("options") or [],
            )
            return {"content": [{"type": "text", "text": answer}]}

        # render: the agent's render tool. Runs IN-PROCESS through the shared
        # RenderJobStore and BLOCKS until the render finishes, then returns the
        # result so the agent continues to QA in the SAME turn. This replaces the
        # old `run_in_background` Bash render, whose CLI auto-resume turn broke
        # message attribution (the off-by-one). The job runs on a store thread, not
        # a CLI background task, so nothing ever auto-resumes the agent.
        @tool(
            "render",
            "Render the project's video. Runs IN-PROCESS and BLOCKS until it finishes, "
            "then returns {success, output_path, warnings, error} so you continue to QA "
            "in THIS SAME TURN. Do NOT render via background Bash or by calling "
            "VideoCompose directly — that breaks turn attribution. Pass edit_decisions "
            "(with render_runtime + renderer_family locked), asset_manifest, and "
            "proposal_packet; omit edit_decisions to render the saved artifact from disk.",
            {
                "type": "object",
                "properties": {
                    "edit_decisions": {
                        "type": "object",
                        "description": "Timeline to render (render_runtime + renderer_family locked). Omit to render the saved artifact.",
                    },
                    "asset_manifest": {
                        "type": "object",
                        "description": "asset_manifest for asset_id->path resolution.",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Where to write the mp4 (project-relative, under renders/). Optional.",
                    },
                    "proxies_dir": {"type": "string", "description": "Proxy cache dir. Optional."},
                    "hdr_policy": {
                        "type": "string",
                        "enum": ["auto", "preserve", "tonemap", "sdr"],
                        "description": "HDR handling. Optional (default auto).",
                    },
                    "proposal_packet": {
                        "type": "object",
                        "description": "proposal_packet artifact for runtime-swap detection. Optional but recommended.",
                    },
                },
                "required": [],
            },
        )
        async def render(args: dict[str, Any]) -> dict[str, Any]:
            return await self._run_render(project_id, args)

        # store_asset: the agent's file-placement tool. The agent declares a
        # KIND and hands over the file it just produced; the tool moves/copies it
        # into the canonical folder for that kind and returns the path to use in
        # edit_decisions/asset_manifest. The agent never names a destination —
        # this is what stops intermediate clips landing in renders/ and showing
        # up as "Final render". Generators should write to a scratch path, then
        # store_asset places the result.
        @tool(
            "store_asset",
            "Save a file you produced into the project. Declare its KIND — the "
            "tool puts it in the correct folder and returns the path to reference "
            "in edit_decisions/asset_manifest. NEVER write into assets/, hf/renders/, "
            "or renders/ yourself; always route produced files through this tool. "
            "kinds: image | video | audio | music | render (intermediate per-scene "
            "clip) | final_render (the ONE assembled deliverable). Idempotent by "
            "content (re-storing identical bytes reuses the existing file).",
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["image", "video", "audio", "music", "render", "final_render"],
                        "description": "What the file is. Determines the destination folder.",
                    },
                    "src": {
                        "type": "string",
                        "description": "Path to the file you produced (absolute, or relative to "
                        "the repo root). Typically a scratch/temp path a generator wrote.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional final filename. Defaults to the source basename.",
                    },
                },
                "required": ["kind", "src"],
            },
        )
        async def store_asset(args: dict[str, Any]) -> dict[str, Any]:
            return await self._store_asset(project_id, args)

        @tool(
            "schedule_content",
            "Schedule this completed project's final render on the Content Calendar. A project "
            "holds one slot: calling this again for the same project MOVES that slot rather than "
            "adding a second entry. The tool checks other projects' entries and moves an occupied "
            "slot to the next open day. Omit "
            "scheduled_at to let it choose a sensible local time. Optionally pass niche; when "
            "you researched a better posting time, pass learned_local_time as 24-hour HH:MM so "
            "future calls for that niche reuse it without web research. This schedules only; it "
            "does not publish to social platforms.",
            {
                "type": "object",
                "properties": {
                    "scheduled_at": {
                        "type": "string",
                        "description": "Optional future ISO-8601 date-time. Omit to auto-select.",
                    },
                    "channels": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(content_calendar_mod.CHANNELS)},
                        "description": "One or more target channels.",
                    },
                    "niche": {
                        "type": "string",
                        "description": "Optional stable niche label used for the posting-time cache.",
                    },
                    "learned_local_time": {
                        "type": "string",
                        "description": "Optional researched host-local posting time as HH:MM.",
                    },
                },
                "required": ["channels"],
            },
        )
        async def schedule_content(args: dict[str, Any]) -> dict[str, Any]:
            return await self._schedule_content(project_id, args)

        # request_api_key: the agent's key-provisioning tool. When a generation/media
        # tool fails because an API key isn't set (e.g. "GOOGLE_API_KEY not set"), the
        # agent calls this instead of telling the user to open BYOK by hand. A secure
        # input appears IN the chat; if the user enters the key it is saved to their BYOK
        # .env (and shows up in the BYOK panel) and this returns success so the agent
        # RETRIES the tool. The user can also decline — then the agent skips that tool.
        # The raw key is written server-side and NEVER returned to the model.
        @tool(
            "request_api_key",
            "Ask the user for a missing API key. Call this when a tool failed because a "
            "required API key / environment variable is NOT set (e.g. the tool returned "
            "'GOOGLE_API_KEY not set' or 'REPLICATE_API_TOKEN not set'). A secure input "
            "appears in the chat; when the user enters the key it is saved to their BYOK "
            "keychain and this returns {provided: true} so you can RETRY the tool that "
            "needed it. If the user declines it returns {provided: false} — then do NOT "
            "retry; use an alternative or continue without that capability and tell the "
            "user what you skipped. Pass the EXACT environment-variable name.",
            {
                "type": "object",
                "properties": {
                    "env_var": {
                        "type": "string",
                        "description": "The exact env-var name the tool needs, e.g. GOOGLE_API_KEY, "
                        "REPLICATE_API_TOKEN, ELEVENLABS_API_KEY, FAL_KEY.",
                    },
                    "provider": {
                        "type": "string",
                        "description": "Human name of the service the key is for, e.g. 'Google (Gemini / Veo)'. Optional.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short reason you need it, e.g. 'to generate the video with Veo'. Optional.",
                    },
                },
                "required": ["env_var"],
            },
        )
        async def request_api_key(args: dict[str, Any]) -> dict[str, Any]:
            return await self._request_api_key(
                project_id,
                str(args.get("env_var") or "").strip(),
                str(args.get("provider") or "").strip(),
                str(args.get("reason") or "").strip(),
            )

        # request_capability: the agent's LOCAL-dependency provisioner (sibling of request_api_key,
        # but for on-device capability packs, not cloud keys). When a LOCAL tool reports its Python
        # deps are missing (e.g. transcriber → "faster_whisper not installed"), the agent calls this
        # instead of hand-running pip. An install card appears in the chat; the UI downloads the pack
        # (streaming progress) into the managed runtime, then this returns {installed: true} so the
        # agent RETRIES the tool. The five packs map to the lib.provision.PACKS registry.
        @tool(
            "request_capability",
            "Install a missing LOCAL capability pack (on-device Python deps). Call this when a LOCAL "
            "tool failed because its packages aren't installed (e.g. 'faster_whisper not installed', "
            "'No module named cv2/mediapipe/librosa/rembg'). An install card appears in the chat; when "
            "the user approves, the pack downloads into the managed runtime and this returns "
            "{installed: true} — then RETRY the tool. If the user declines it returns {installed: "
            "false} — do NOT retry; use an alternative or continue without it and say what you skipped. "
            "This is for LOCAL packs only; for missing cloud API keys use request_api_key instead. "
            "Packs: 'transcription' (speech-to-text / captions / video understanding), 'vision' "
            "(auto-reframe / face tracking), 'bg-removal' (background removal), 'beat-sync' (music "
            "beat detection), 'tts' (local text-to-speech).",
            {
                "type": "object",
                "properties": {
                    "pack": {
                        "type": "string",
                        "enum": ["transcription", "vision", "bg-removal", "beat-sync", "tts"],
                        "description": "Which capability pack the failing tool needs.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short reason you need it, e.g. 'to transcribe the clip for captions'. Optional.",
                    },
                },
                "required": ["pack"],
            },
        )
        async def request_capability(args: dict[str, Any]) -> dict[str, Any]:
            return await self._request_capability(
                project_id,
                str(args.get("pack") or "").strip(),
                str(args.get("reason") or "").strip(),
            )

        # run_media_op: the agent's blocking runner for HEAVY media ops (silence_cutter,
        # motion_ops, and other tools.video re-encodes). Runs the named registry tool
        # IN-PROCESS on a RenderJobStore thread and BLOCKS until it finishes, then returns
        # the result so the agent continues in the SAME turn. This replaces
        # `python -c "...execute(...)"` in Bash — a long re-encode there gets auto-detached
        # by the CLI, ending the turn and breaking message attribution (the off-by-one).
        # Like `render`, the job runs on a store thread, so a Stop leaves it running and the
        # next turn surfaces its result (see _render_resume_note). Produced files still go
        # through store_asset — this tool returns the path; the agent picks the kind.
        @tool(
            "run_media_op",
            "Run a heavy media operation (silence removal, speed change, reframe, …) IN-PROCESS. "
            "Runs the named tool, BLOCKS until it finishes, and returns {success, output_path, "
            "data, error} so you continue in THIS SAME TURN. Use this for any tools.video / "
            'tools.audio re-encode instead of `python -c "...execute(...)"` in Bash (a '
            "background re-encode ends your turn and breaks attribution). Quick read-only calls "
            "(ffprobe, update_stage.py, registry introspection) still run via Bash. After it "
            "produces a file, route that file through `store_asset` as usual.",
            {
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "description": "The registry tool name to run, e.g. 'silence_cutter', 'motion_ops'.",
                    },
                    "input": {
                        "type": "object",
                        "description": "The tool's own input dict (same schema you'd pass to its execute()).",
                    },
                },
                "required": ["tool", "input"],
            },
        )
        async def run_media_op(args: dict[str, Any]) -> dict[str, Any]:
            return await self._run_media_op(
                project_id,
                str(args.get("tool") or "").strip(),
                args.get("input") or {},
            )

        mc_server = create_sdk_mcp_server(
            "mc",
            "1.0.0",
            [ask_user, render, store_asset, schedule_content, request_api_key, request_capability, run_media_op],
        )

        # If a prior session for this project died, resume it so the agent
        # comes back with its full conversation context (on a fresh budget).
        resume = None
        if self._resume_next.pop(project_id, False):
            resume = self._session_ids.get(project_id)

        options = build_agent_options(
            self.repo_root,
            projects_dir=self.projects_dir,
            model=self._model_for(project_id),
            max_budget_usd=self.max_budget_usd,
            confirm_handler=confirm,
            resume=resume,
            mcp_servers={"mc": mc_server},
            # Steer the agent to ask_user (we control its UI round-trip) instead
            # of the built-in AskUserQuestion (no controllable headless I/O).
            # Disallow ScheduleWakeup too: in this per-message runner a scheduled
            # wakeup does nothing useful (the backend only reads the stream when the
            # user sends a message) and is a pure source of unsolicited/stray turns.
            disallowed_tools=["AskUserQuestion", "ScheduleWakeup"],
            # A LIVE getter, not a value: this client outlives the turn that built it, so the
            # permission callbacks must read whichever turn is running now (see F12).
            turn_ctx=lambda: self._turn_ctx.get(project_id) or {},
            stderr=lambda line: self._record_cli_stderr(project_id, line),
        )
        return ClaudeSDKClient(options=options)

    def _model_for(self, project_id: str) -> str:
        """The model a new client for this project should be built with: the
        UI-selected override if one has been set, else the runner default."""
        return self._models.get(project_id, self.model)

    def _record_cli_stderr(self, project_id: str, line: str) -> None:
        """Keep (and log) the Claude CLI's own stderr, so a dead CLI can say why it died.

        Clipped BEFORE it is stored, not on the way out: the CLI dumps whole minified source lines
        around a crash (tens of KB each), so 60 unclipped lines is megabytes held per project — and
        the same text is what reaches the local backend log and Electron's crash dialog."""
        line = line.rstrip()[:300]
        self._cli_stderr.setdefault(project_id, deque(maxlen=60)).append(line)
        print(f"[claude-cli] {line}", file=sys.stderr)

    def cli_error_detail(self, detail: str, project_id: str, n: int = 20) -> str:
        """`detail` plus the CLI's recent stderr — what the user sees when a turn fails.

        The SDK's transport errors ("Command failed with exit code 1", "Check stderr output for
        details") name nothing on their own, and the CLI's stderr is the only place the real cause
        appears. Long lines are dropped, not truncated: around a crash the CLI prints its own
        minified source, so line length is what separates the noise from the message.

        ponytail: scoped to the CURRENT client (the buffer is dropped when one is created), so the
        worst staleness is a warning from an earlier turn of the same client — diagnostic text,
        where recent CLI stderr is useful context either way.
        """
        lines = [ln for ln in (self._cli_stderr.get(project_id) or ()) if ln.strip() and len(ln) <= 200]
        if not lines:
            return detail
        return detail + "\n\nClaude CLI stderr (recent):\n" + "\n".join(lines[-n:])

    async def _get_client(self, project_id: str) -> Any:
        client = self._clients.get(project_id)
        if client is None:
            # This client's CLI has said nothing yet: drop the dead one's stderr so a failure here
            # cannot be explained with the previous subprocess's output (and so the buffer cannot
            # accumulate a key per project for the life of the backend).
            self._cli_stderr.pop(project_id, None)
            client = self.client_factory(project_id)
            await client.connect()
            self._clients[project_id] = client
            self._fresh_client[project_id] = True
        return client

    def _project_context(self, project_id: str) -> str:
        """The binding instruction: which project the agent works on. Always
        injected on the first turn so the agent uses the UI-selected project_id
        instead of inventing a new project dir from the topic (the cause of the
        'stepper stuck on pending' bug — the agent wrote to a different project)."""
        try:
            from lib.project import get_project_pipeline_type, read_project_manifest

            pt = get_project_pipeline_type(self.projects_dir, project_id)
            _m = read_project_manifest(self.projects_dir, project_id) or {}
            style = (_m.get("style") or "").strip() or None
        except Exception:
            pt = None
            style = None
        if pt:
            pipeline_clause = f" using the '{pt}' pipeline"
            choose_clause = ""
            stage_cmd = f"python scripts/update_stage.py {project_id} <stage> <status> {pt}"
        else:
            pipeline_clause = ""
            choose_clause = (
                " No pipeline_type has been chosen for this project yet — read the user's request and the "
                "available pipelines under pipeline_defs/, pick the best-fit pipeline, and then use that SAME "
                "pipeline_type consistently for every checkpoint and update_stage call."
            )
            stage_cmd = f"python scripts/update_stage.py {project_id} <stage> <status> <pipeline_type>"
        if style:
            style_clause = (
                f" The user chose the '{style}' visual style for this project — load it with "
                f"`load_playbook('{style}')` (styles.playbook_loader) and follow it; do NOT pick a "
                f"different style. Set it as the scene_plan's style_playbook."
            )
        else:
            style_clause = ""
        return (
            f"[PROJECT CONTEXT: You are working on the existing project '{project_id}'{pipeline_clause}.{choose_clause}{style_clause} "
            f"Use EXACTLY this project_id for everything — do NOT create a new project directory. "
            f"Write artifacts to the ABSOLUTE path {self.projects_dir / project_id}/artifacts/ — your working "
            f"directory is the read-only app code, so a relative 'projects/{project_id}/...' path would write to "
            f"the wrong place. For every produced asset "
            f"(image/video/audio/music/render/final_render) call the `store_asset` tool instead of "
            f"choosing a folder — it files each asset in the right place and returns its path. As you "
            f"work each stage, update its status so the UI stepper reflects progress: run `{stage_cmd}` "
            f"(status = in_progress at the start of a stage, completed when done, awaiting_human at approval gates).]"
        )

    def _resume_preamble(self, project_id: str) -> Optional[str]:
        """A short note grounding the agent in on-disk progress, so a fresh or
        resumed session continues instead of redoing work. Returns None for a
        brand-new project with no prior work.

        Durable safety net: even if SDK session resume fails or the backend
        restarted (losing the in-memory session_id), the on-disk checkpoints +
        artifacts let any agent pick up where the last one left off.
        """
        try:
            from lib.checkpoint import get_completed_stages, get_next_stage
            from lib.project import get_project_pipeline_type

            projects_dir = self.projects_dir
            pipeline_type = get_project_pipeline_type(projects_dir, project_id)
            completed = get_completed_stages(projects_dir, project_id, pipeline_type)
            artifacts_dir = projects_dir / project_id / "artifacts"
            artifacts = sorted(p.name for p in artifacts_dir.glob("*.json")) if artifacts_dir.exists() else []
            if not completed and not artifacts:
                return None  # nothing done yet — no grounding needed
            nxt = get_next_stage(projects_dir, project_id, pipeline_type)
            return (
                f"[RESUMING WORK on project '{project_id}' (pipeline: {pipeline_type}). "
                f"Completed stages: {', '.join(completed) or 'none'}. "
                f"Next stage: {nxt or 'all done'}. "
                f"Existing artifacts in {artifacts_dir}/: {', '.join(artifacts) or 'none'}. "
                f"Read the relevant checkpoints and artifacts to recover context, then continue from "
                f"the next stage. Do NOT redo completed stages.]"
            )
        except Exception:
            return None

    def _first_turn_preamble(self, project_id: str) -> str:
        """What gets prepended to the first message of a fresh client: the
        project binding (always) plus a resume/progress note (if prior work)."""
        parts = [self._project_context(project_id)]
        progress = self._resume_preamble(project_id)
        if progress:
            parts.append(progress)
        render_note = self._render_resume_note(project_id)
        if render_note:
            parts.append(render_note)
        return "\n".join(parts)

    async def _confirm(self, project_id, tool_name, tool_input, reason) -> bool:
        emit = self._emit.get(project_id)
        if emit is None:
            return False  # no active stream to ask through -> deny
        self._confirm_seq += 1
        confirm_id = f"{project_id}:{self._confirm_seq}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[confirm_id] = fut
        await _maybe_await(
            emit(
                {
                    "type": "confirm_request",
                    "confirm_id": confirm_id,
                    "tool": tool_name,
                    "reason": reason,
                    "input": _truncate_input(tool_input),
                }
            )
        )
        try:
            return bool(await asyncio.wait_for(fut, timeout=self.confirm_timeout_s))
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(confirm_id, None)

    def resolve_confirm(self, confirm_id: str, approved: bool) -> bool:
        """Resolve a pending tool-confirm (called by the /confirm endpoint).
        Returns True if a pending confirm matched."""
        fut = self._pending.get(confirm_id)
        if fut is None or fut.done():
            return False
        fut.set_result(bool(approved))
        return True

    async def _ask_user(self, project_id, header, question, options) -> str:
        """Surface a question to the UI and block until the user picks an answer.
        Returns the chosen answer string (or a sensible default if no UI/timeout)."""
        emit = self._emit.get(project_id)
        if emit is None:
            return "No user is available right now; proceed with your best judgment."
        self._question_seq += 1
        question_id = f"{project_id}:q{self._question_seq}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._answers[question_id] = fut
        await _maybe_await(
            emit(
                {
                    "type": "question",
                    "question_id": question_id,
                    "header": header,
                    "question": question,
                    "options": list(options),
                }
            )
        )
        try:
            return str(await asyncio.wait_for(fut, timeout=self.answer_timeout_s))
        except asyncio.TimeoutError:
            return "No answer received (timed out); proceed with your best judgment."
        finally:
            self._answers.pop(question_id, None)

    def resolve_answer(self, question_id: str, answer: str) -> bool:
        """Resolve a pending ask_user question (called by the /answer endpoint).
        Returns True if a pending question matched."""
        fut = self._answers.get(question_id)
        if fut is None or fut.done():
            return False
        fut.set_result(str(answer))
        return True

    async def _request_api_key(self, project_id, env_var, provider, reason) -> dict[str, Any]:
        """Surface a secure API-key prompt to the UI and block until the user provides the key
        (saved to the BYOK .env by the /provide-key endpoint) or declines. Returns an MCP text
        result carrying {provided: bool}; the raw key is NEVER passed back to the model."""
        if not env_var:
            return _text_result({"error": "request_api_key needs a non-empty env_var name."})
        # The provider FAMILY, never the value. `env_var` is a NAME the agent chose, so it is
        # collapsed to a closed family here — _scrub would not save us, since it tests the key
        # NAME and 'ANTHROPIC_API_KEY' rides through unredacted.
        analytics.capture(
            "api_key_missing",
            {
                "provider_family": analytics.provider_family(env_var),
                "already_in_byok": bool(os.environ.get(env_var)),
                "turn_id": (self._turn_ctx.get(project_id) or {}).get("turn_id"),
                # Explicit, for the same reason as turn_id: an MCP tool handler runs in the
                # client's task, so current_session_id() would resolve to whichever session
                # BUILT the client and stay wrong for every later turn (F12).
                "session_id": (self._turn_ctx.get(project_id) or {}).get("session_id"),
                "project_id": self._project_key(project_id),
            },
        )
        t_key = time.monotonic()
        emit = self._emit.get(project_id)
        if emit is None:
            return _text_result(
                {"provided": False, "detail": "No user is available to provide a key right now; skip this tool."}
            )
        try:
            from server import env_config

            meta = env_config.describe_var(env_var)
        except Exception:
            meta = {"key": env_var, "label": env_var, "description": ""}
        self._key_seq += 1
        key_request_id = f"{project_id}:k{self._key_seq}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._key_requests[key_request_id] = fut
        await _maybe_await(
            emit(
                {
                    "type": "api_key_request",
                    "key_request_id": key_request_id,
                    "env_var": env_var,
                    "provider": provider or meta.get("label") or env_var,
                    "label": meta.get("label") or env_var,
                    "description": meta.get("description") or "",
                    "reason": reason,
                }
            )
        )
        try:
            provided = bool(await asyncio.wait_for(fut, timeout=self.answer_timeout_s))
        except asyncio.TimeoutError:
            return _text_result(
                {"provided": False, "detail": f"No response (timed out) for {env_var}; proceed without it."}
            )
        finally:
            self._key_requests.pop(key_request_id, None)
        analytics.capture(
            "api_key_request_resolved",
            {
                "provider_family": analytics.provider_family(env_var),
                "provided": bool(provided),
                "wait_s": _bucket_seconds(time.monotonic() - t_key),
                "turn_id": (self._turn_ctx.get(project_id) or {}).get("turn_id"),
                # Explicit, for the same reason as turn_id: an MCP tool handler runs in the
                # client's task, so current_session_id() would resolve to whichever session
                # BUILT the client and stay wrong for every later turn (F12).
                "session_id": (self._turn_ctx.get(project_id) or {}).get("session_id"),
                "project_id": self._project_key(project_id),
            },
        )
        if provided:
            return _text_result(
                {
                    "provided": True,
                    "env_var": env_var,
                    "detail": f"The user saved {env_var}. It is now available — RETRY the tool that needed it.",
                }
            )
        return _text_result(
            {
                "provided": False,
                "env_var": env_var,
                "detail": f"The user declined to provide {env_var}. Do NOT retry that tool; "
                f"use an alternative or continue without it and tell the user what you skipped.",
            }
        )

    def resolve_key_request(self, key_request_id: str, provided: bool) -> bool:
        """Resolve a pending request_api_key prompt (called by the /provide-key endpoint,
        AFTER the key is persisted). Returns True if a pending request matched."""
        fut = self._key_requests.get(key_request_id)
        if fut is None or fut.done():
            return False
        fut.set_result(bool(provided))
        return True

    async def _request_capability(self, project_id, pack, reason) -> dict[str, Any]:
        """Surface a capability-install prompt to the UI and block until the pack is installed (the
        UI streams /api/provision/{pack} and then calls /provide-capability) or the user declines.
        Returns an MCP text result carrying {installed: bool}."""
        try:
            from lib import provision

            packs = provision.PACKS
        except Exception:
            packs = {}
        if pack not in packs:
            return _text_result({"error": f"unknown capability pack {pack!r}; known: {sorted(packs)}"})
        # AFTER the guard: `pack` is now provably a member of our own PACKS registry, so it is
        # a closed vocabulary rather than a string the agent invented.
        installed_before = False
        try:
            installed_before = bool(provision.pack_installed(pack))
        except Exception:
            pass
        analytics.capture(
            "capability_missing",
            {
                "pack": pack,
                "reason_class": "tool_reported_missing",
                "installed_before": installed_before,
                "turn_id": (self._turn_ctx.get(project_id) or {}).get("turn_id"),
                # Explicit, for the same reason as turn_id: an MCP tool handler runs in the
                # client's task, so current_session_id() would resolve to whichever session
                # BUILT the client and stay wrong for every later turn (F12).
                "session_id": (self._turn_ctx.get(project_id) or {}).get("session_id"),
                "project_id": self._project_key(project_id),
            },
        )
        t_cap = time.monotonic()
        # Already installed? Then there's nothing to ask — tell the agent to just retry.
        try:
            if provision.pack_installed(pack):
                return _text_result(
                    {"installed": True, "pack": pack, "detail": f"'{pack}' is already installed — RETRY the tool."}
                )
        except Exception:
            pass
        emit = self._emit.get(project_id)
        if emit is None:
            return _text_result(
                {"installed": False, "detail": "No user is available to approve an install right now; skip this tool."}
            )
        meta = packs.get(pack, {})
        self._cap_seq += 1
        cap_request_id = f"{project_id}:c{self._cap_seq}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._cap_requests[cap_request_id] = fut
        await _maybe_await(
            emit(
                {
                    "type": "capability_request",
                    "cap_request_id": cap_request_id,
                    "pack": pack,
                    "label": meta.get("label") or pack,
                    "size_mb": meta.get("size_mb"),
                    "reason": reason,
                }
            )
        )
        try:
            # Installs are large (up to ~2.6 GB) — allow far longer than the question/key timeout.
            installed = bool(await asyncio.wait_for(fut, timeout=max(self.answer_timeout_s, 3600)))
        except asyncio.TimeoutError:
            return _text_result(
                {"installed": False, "detail": f"No response (timed out) for '{pack}'; proceed without it."}
            )
        finally:
            self._cap_requests.pop(cap_request_id, None)
        analytics.capture(
            "capability_request_resolved",
            {
                "pack": pack,
                "outcome": "installed" if installed else "declined",
                "wait_s": _bucket_seconds(time.monotonic() - t_cap),
                "turn_id": (self._turn_ctx.get(project_id) or {}).get("turn_id"),
                # Explicit, for the same reason as turn_id: an MCP tool handler runs in the
                # client's task, so current_session_id() would resolve to whichever session
                # BUILT the client and stay wrong for every later turn (F12).
                "session_id": (self._turn_ctx.get(project_id) or {}).get("session_id"),
                "project_id": self._project_key(project_id),
            },
        )
        if installed:
            return _text_result(
                {
                    "installed": True,
                    "pack": pack,
                    "detail": f"'{pack}' is now installed — RETRY the tool that needed it.",
                }
            )
        return _text_result(
            {
                "installed": False,
                "pack": pack,
                "detail": f"The user declined to install '{pack}'. Do NOT retry that tool; "
                f"use an alternative or continue without it and tell the user what you skipped.",
            }
        )

    def resolve_capability_request(self, cap_request_id: str, installed: bool) -> bool:
        """Resolve a pending request_capability prompt (called by the /provide-capability endpoint
        AFTER the pack install stream finished). Returns True if a pending request matched."""
        fut = self._cap_requests.get(cap_request_id)
        if fut is None or fut.done():
            return False
        fut.set_result(bool(installed))
        return True

    # -- render tool ---------------------------------------------------------
    def _render_tool_result(self, **fields: Any) -> dict[str, Any]:
        """Build the MCP tool result the agent parses: a human summary line plus a
        JSON blob carrying success/output_path/warnings/error."""
        payload = {k: v for k, v in fields.items() if v is not None}
        ok = bool(payload.get("success"))
        summary = (
            f"Render succeeded: {payload.get('output_path')}"
            if ok
            else f"Render failed: {payload.get('error', 'unknown error')}"
        )
        text = summary + "\n\n" + json.dumps(payload)
        return {"content": [{"type": "text", "text": text}], "is_error": not ok}

    def _build_render_inputs(self, project_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Collect the render inputs the agent supplied; fall back to the saved
        artifact on disk when edit_decisions is omitted (a thin `render` call)."""
        keys = ("edit_decisions", "asset_manifest", "output_path", "proxies_dir", "hdr_policy", "proposal_packet")
        inputs = {k: args[k] for k in keys if args.get(k) is not None}
        # The join keys ride into the job record here. This is the ONLY place an agent-started
        # render can learn which session caused it: the render thread outlives the HTTP request
        # that carried X-ON-Session, so it can never read the context itself.
        ctx = self._turn_ctx.get(project_id) or {}
        inputs["session_id"] = ctx.get("session_id")
        inputs["turn_id"] = ctx.get("turn_id")
        if "edit_decisions" not in inputs:
            try:
                from server.editor import read_asset_manifest, read_edit_decisions

                projects_dir = self.projects_dir
                ed = read_edit_decisions(projects_dir, project_id)
                if ed is not None:
                    inputs["edit_decisions"] = ed
                if "asset_manifest" not in inputs:
                    inputs["asset_manifest"] = read_asset_manifest(projects_dir, project_id)
            except Exception:
                pass
        return inputs

    async def _run_render(self, project_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Start a tracked render job via the shared RenderJobStore and AWAIT it,
        emitting progress SSE. Returns the MCP tool result so the agent continues to
        QA in the SAME turn.

        The job runs on a RenderJobStore thread (NOT a Claude-CLI background task),
        so nothing auto-resumes the agent — the render is just a blocking tool call,
        which is what eliminates the off-by-one. On Stop the awaiting handler is
        cancelled but the job keeps running; the next turn's resume note surfaces it."""
        if self.render_store is None:
            return self._render_tool_result(success=False, error="render store unavailable; cannot render in-process")

        inputs = self._build_render_inputs(project_id, args)
        if not inputs.get("edit_decisions"):
            return self._render_tool_result(
                success=False, error="no edit_decisions supplied and none saved on disk — build/save the timeline first"
            )

        # An INLINE doc is the one the publisher will commit to
        # artifacts/edit_decisions.json on success, so validate it FIRST and fail the call
        # rather than render a timeline that can never be persisted — that gap between
        # "what was rendered" and "what the editor reads" is the desync (OPN-30). Strict:
        # the artifact schema is already the contract AGENT_GUIDE requires the agent to
        # write. A doc read from disk was validated by the write path already.
        if args.get("edit_decisions") is not None:
            try:
                from schemas.artifacts import validate_artifact

                validate_artifact("edit_decisions", inputs["edit_decisions"])
            except Exception as exc:
                # jsonschema's str() dumps the whole schema; the agent needs the failing
                # path and the reason, on one line (the summary line is not JSON-escaped).
                where = getattr(exc, "json_path", None)
                why = getattr(exc, "message", None) or str(exc).split("\n")[0]
                return self._render_tool_result(
                    success=False,
                    error=(
                        "edit_decisions failed schema validation, nothing was rendered and "
                        f"nothing was written: {why}" + (f" (at {where})" if where else "")
                    ),
                )
            inputs["persist_edit_decisions"] = True

        loop = asyncio.get_event_loop()
        job_id = self.render_store.start_with_inputs(project_id, inputs)
        emit = self._emit.get(project_id)
        if emit is not None:
            await _maybe_await(emit({"type": "render_started", "job_id": job_id, "project_id": project_id}))

        deadline = loop.time() + self.render_timeout_s
        last_status: Optional[str] = None
        try:
            while True:
                st = self.render_store.status(job_id)
                if st is None:  # superseded/dropped by a newer render
                    return self._render_tool_result(
                        success=False, job_id=job_id, error=f"render job {job_id} was superseded by a newer render"
                    )
                status = st.get("status")
                if status != last_status and emit is not None:
                    await _maybe_await(emit({"type": "render_progress", "job_id": job_id, "status": status}))
                    last_status = status
                if status == "done":
                    self.render_store.mark_consumed(job_id)  # seen in-turn; don't re-surface next turn
                    return self._render_tool_result(
                        success=True,
                        job_id=job_id,
                        output_path=st.get("output_path"),
                        warnings=st.get("warnings"),
                        final_review_status=st.get("final_review_status"),
                    )
                if status == "failed":
                    self.render_store.mark_consumed(job_id)
                    return self._render_tool_result(
                        success=False, job_id=job_id, error=st.get("error") or "render failed"
                    )
                if status == "superseded":
                    # Terminal, like done/failed — and consumed for the same reason: the
                    # supersede is being reported HERE, so the next turn's resume note
                    # must not report it a second time.
                    self.render_store.mark_consumed(job_id)
                    return self._render_tool_result(
                        success=False,
                        job_id=job_id,
                        error=(
                            f"render job {job_id} was superseded by a newer render "
                            "(yours never published); nothing was written"
                        ),
                    )
                if loop.time() > deadline:
                    # Do NOT cancel — the job keeps running on its thread; the next
                    # turn's resume note surfaces the finished output. Leave UNCONSUMED.
                    return self._render_tool_result(
                        success=False,
                        timed_out=True,
                        job_id=job_id,
                        error=(
                            f"render still running after {self.render_timeout_s}s (job {job_id}); "
                            "it continues in the background and its result will be available on "
                            "your next turn — do not re-render."
                        ),
                    )
                await asyncio.sleep(self.render_poll_interval_s)
        except asyncio.CancelledError:
            # User hit Stop -> the turn is being cancelled. Leave the job running (it's
            # a thread, not a CLI bg task); the next turn's resume note surfaces it.
            raise

    # -- run_media_op tool ---------------------------------------------------
    def _media_op_result(self, **fields: Any) -> dict[str, Any]:
        """Build the MCP result for run_media_op: a human summary line plus a JSON blob
        carrying success/tool/output_path/data/error. Mirrors _render_tool_result."""
        payload = {k: v for k, v in fields.items() if v is not None}
        ok = bool(payload.get("success"))
        label = payload.get("tool") or "media op"
        summary = (
            f"{label} done: {payload.get('output_path') or 'ok'}"
            if ok
            else f"{label} failed: {payload.get('error', 'unknown error')}"
        )
        text = summary + "\n\n" + json.dumps(payload)
        return {"content": [{"type": "text", "text": text}], "is_error": not ok}

    async def _run_media_op(self, project_id: str, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Start a tracked media-op job (any registry tool) on the shared RenderJobStore
        and AWAIT it, emitting progress SSE. Returns the MCP result so the agent continues
        in the SAME turn — the in-process, blocking replacement for running a heavy
        re-encode via background Bash (which broke turn attribution).

        Same Stop/timeout semantics as _run_render: the job runs on a store thread, so a
        Stop or timeout leaves it running and the next turn's resume note surfaces it.
        # ponytail: poll loop duplicated from _run_render; unify into a shared helper only
        # if a third store-job kind ever appears."""
        if self.render_store is None:
            return self._media_op_result(
                success=False, tool=tool_name, error="job store unavailable; cannot run media op in-process"
            )
        if not tool_name:
            return self._media_op_result(success=False, error="tool name is required")
        if not isinstance(tool_input, dict):
            return self._media_op_result(success=False, tool=tool_name, error="input must be an object")

        loop = asyncio.get_event_loop()
        ctx = self._turn_ctx.get(project_id) or {}
        job_id = self.render_store.start_op(
            project_id,
            tool_name,
            tool_input,
            session_id=ctx.get("session_id"),
            turn_id=ctx.get("turn_id"),
        )
        emit = self._emit.get(project_id)
        if emit is not None:
            await _maybe_await(
                emit({"type": "media_op_started", "job_id": job_id, "project_id": project_id, "tool": tool_name})
            )

        deadline = loop.time() + self.render_timeout_s
        last_status: Optional[str] = None
        try:
            while True:
                st = self.render_store.status(job_id)
                if st is None:  # superseded/dropped by a newer job
                    return self._media_op_result(
                        success=False,
                        tool=tool_name,
                        job_id=job_id,
                        error=f"media op {job_id} was superseded by a newer job",
                    )
                status = st.get("status")
                if status != last_status and emit is not None:
                    await _maybe_await(emit({"type": "media_op_progress", "job_id": job_id, "status": status}))
                    last_status = status
                if status == "done":
                    self.render_store.mark_consumed(job_id)  # seen in-turn; don't re-surface next turn
                    return self._media_op_result(
                        success=True,
                        tool=tool_name,
                        job_id=job_id,
                        output_path=st.get("output_path"),
                        data=st.get("result_data"),
                        warnings=st.get("warnings"),
                    )
                if status == "failed":
                    self.render_store.mark_consumed(job_id)
                    return self._media_op_result(
                        success=False, tool=tool_name, job_id=job_id, error=st.get("error") or "media op failed"
                    )
                if loop.time() > deadline:
                    # Do NOT cancel — the job keeps running on its thread; the next turn's
                    # resume note surfaces the finished output. Leave UNCONSUMED.
                    return self._media_op_result(
                        success=False,
                        timed_out=True,
                        tool=tool_name,
                        job_id=job_id,
                        error=(
                            f"media op still running after {self.render_timeout_s}s (job {job_id}); "
                            "it continues in the background and its result will be available on your "
                            "next turn — do not re-run it."
                        ),
                    )
                await asyncio.sleep(self.render_poll_interval_s)
        except asyncio.CancelledError:
            # User hit Stop -> leave the job running on its thread; next turn surfaces it.
            raise

    async def _store_asset(self, project_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Place a produced file into its canonical folder for the declared kind
        and return the path the agent should reference. Thin wrapper over
        lib.project.place_asset (the single writer into the asset tree). Errors
        (bad kind, missing src) come back as a JSON {"error": ...} the agent can
        recover from, not an exception that kills the turn.

        `final_render` REPLACES renders/final.mp4 (place_asset routes that one kind to
        publish_final_render) instead of parking beside it as final.<hash>.mp4. It supplies
        neither document — these bytes may be unrelated to anything on disk, so hashing the
        disk doc would falsely certify them — which UNLINKS the receipt, leaving the result
        honestly "not current" until a real render publishes one."""
        from lib.project import place_asset

        kind = (args.get("kind") or "").strip()
        src_arg = (args.get("src") or "").strip()
        name = (args.get("name") or "").strip() or None
        if not src_arg:
            return _text_result({"error": "src is required (path to the file you produced)"})

        # Resolve src relative to the repo root when not absolute (the agent works
        # from repo-root-relative paths everywhere else).
        src = Path(src_arg)
        if not src.is_absolute():
            src = self.repo_root / src_arg

        # Temp-staged files are RELOCATED into the project (OPN-10: the staging
        # copy is litter once placed — /tmp used to accumulate a twin of every
        # asset); files anywhere else are copied, since they're not ours to consume.
        try:
            resolved = src.resolve()
            move = any(resolved.is_relative_to(r) for r in _temp_roots())
        except OSError:
            move = False

        # to_thread, not a direct call: for `final_render` the publisher blocks on
        # project_lock for as long as a render holds it, and every other kind hashes the
        # file — which for a 500 MB video is not something to do on the event loop. Blocking
        # here would stall the SSE stream and the whole turn with it.
        try:
            res = await asyncio.to_thread(place_asset, self.projects_dir, project_id, kind, src, name, move=move)
        except (ValueError, FileNotFoundError) as exc:
            self._emit_store_asset(project_id, kind, ok=False)
            return _text_result({"error": str(exc)})
        self._emit_store_asset(project_id, kind, ok=True)

        # Hand back a repo-relative path — directly usable in edit_decisions /
        # asset_manifest — plus the project-relative form.
        return _text_result(
            {
                "path": f"projects/{project_id}/{res['path']}",
                "project_relative": res["path"],
                "kind": res["kind"],
                "deduped": res["deduped"],
            }
        )

    async def _schedule_content(self, project_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Write an agent-selected slot through the shared calendar service."""
        try:
            entry = await asyncio.to_thread(
                content_calendar_mod.create_scheduled_entry,
                self.projects_dir,
                project_id,
                (args.get("scheduled_at") or "").strip() or None,
                args.get("channels") or [],
                created_by="agent",
                avoid_collisions=True,
                niche=(args.get("niche") or "").strip() or None,
                learned_local_time=(args.get("learned_local_time") or "").strip() or None,
            )
        except (content_calendar_mod.ScheduleValidationError, content_calendar_mod.FinalRenderMissing) as exc:
            analytics.capture(
                "content_schedule_failed",
                {
                    "created_by": "agent",
                    "failure_class": content_calendar_mod.failure_class(exc),
                    "turn_id": (self._turn_ctx.get(project_id) or {}).get("turn_id"),
                    "session_id": (self._turn_ctx.get(project_id) or {}).get("session_id"),
                    "project_id": self._project_key(project_id),
                },
            )
            return _text_result({"error": str(exc), "failure_class": content_calendar_mod.failure_class(exc)})
        except (OSError, ValueError) as exc:
            analytics.capture(
                "content_schedule_failed",
                {
                    "created_by": "agent",
                    "failure_class": "storage",
                    "turn_id": (self._turn_ctx.get(project_id) or {}).get("turn_id"),
                    "session_id": (self._turn_ctx.get(project_id) or {}).get("session_id"),
                    "project_id": self._project_key(project_id),
                },
            )
            return _text_result({"error": "could not save calendar entry", "failure_class": "storage"})
        analytics.capture(
            "content_schedule_created",
            {
                "created_by": "agent",
                "channel_count": len(entry["channels"]),
                "timing_source": entry["timing_source"],
                "replaced": entry["replaced"],
                "turn_id": (self._turn_ctx.get(project_id) or {}).get("turn_id"),
                "session_id": (self._turn_ctx.get(project_id) or {}).get("session_id"),
                "project_id": self._project_key(project_id),
            },
        )
        return _text_result({"scheduled": True, "entry": entry})

    def _render_resume_note(self, project_id: str) -> Optional[str]:
        """If there's an UNCONSUMED agent job (a render OR a media op) for this project,
        return a note telling the agent to continue from it. Fires on the user's NEXT
        message after a Stop/timeout, so the finished job is surfaced attached to that
        message (correct attribution, no off-by-one). Terminal states are marked consumed
        so the note fires exactly once; running/queued are left so the 'done' note fires
        later. None when there's nothing to surface.

        Disjoint from run_turn's stray-turn drain: a store-thread job is surfaced HERE and
        never produces a CLI turn for the drain to see; a stray Bash turn is surfaced by the
        drain and never starts a store job. So the same work is never double-reported."""
        if self.render_store is None:
            return None
        try:
            # A TERMINAL unconsumed agent job first: a superseded one is no longer the
            # active job (a newer one displaced it), so active_job_for would return the
            # displacer — an editor job, say — and this note would silently return None.
            job = self.render_store.latest_unconsumed_agent_job(project_id) or self.render_store.active_job_for(
                project_id
            )
        except Exception:
            return None
        if not job or job.get("consumed") or job.get("origin") not in ("agent", "agent_op"):
            return None
        status = job.get("status")
        job_id = job.get("job_id")
        is_op = job.get("origin") == "agent_op"
        tag = "MEDIA OP UPDATE" if is_op else "RENDER UPDATE"
        noun = f"media op ({job.get('tool_name')})" if is_op else "render"
        redo = "re-run it" if is_op else "re-render"
        if status == "done":
            self.render_store.mark_consumed(job_id)
            return (
                f"[{tag}: {noun} job {job_id} COMPLETED while you were away. "
                f"Output: {job.get('output_path')}. Warnings: {job.get('warnings') or 'none'}. "
                f"Do NOT {redo} — pick up from QA/verification of this output.]"
            )
        if status == "failed":
            self.render_store.mark_consumed(job_id)
            return (
                f"[{tag}: {noun} job {job_id} FAILED: {job.get('error')}. "
                f"Diagnose the cause and decide whether to {redo}.]"
            )
        if status == "superseded":
            self.render_store.mark_consumed(job_id)
            return (
                f"[{tag}: {noun} job {job_id} was SUPERSEDED by a newer one and never "
                f"published — nothing of yours was written. Check the current state "
                f"before you {redo}.]"
            )
        # queued / running — surface but do NOT consume (so the 'done' note fires later)
        return (
            f"[{tag}: {noun} job {job_id} you started is still {status}. "
            f"Its result will be available shortly; only call the tool again "
            f"if you need a fresh run.]"
        )

    async def _drain_unsolicited(self, project_id: str, client: Any, on_event: Optional[EmitFn]) -> None:
        """Consume any turn(s) the CLI produced BETWEEN user messages — a background
        Bash-task completion or a scheduled wakeup — before we send the next message.
        Without this, the next receive_response() reads that buffered turn first and stops
        at its ResultMessage, mis-attributing the prior turn's output as the answer to the
        new message (the off-by-one). Each fully-drained turn is surfaced as a
        `background_update` event (the UI renders it as a system note), cleanly separated
        from the answer that's about to come.

        Cancellation-safe: the SDK read suspends on a memory-stream receive() (queue-style),
        so a timed-out anext() leaves any unread item buffered — no data lost, no stream
        corruption (verified against claude_agent_sdk 0.2.87). We only cancel the FINAL
        (timed-out) read and then discard this generator; successful reads never cancel.
        # ponytail: the grace-wait bounds the rare mid-stream race (a user message arriving
        # while a stray turn is still streaming); a persistent reader task is the fuller fix
        # if that ever actually bites."""
        it = client.receive_messages()
        texts: list[str] = []
        turn_open = False

        async def _flush() -> None:
            summary = "".join(texts).strip()
            texts.clear()
            if summary and on_event is not None:
                await _maybe_await(
                    on_event(
                        {
                            "type": "background_update",
                            "text": f"A background task finished between turns:\n\n{summary}",
                        }
                    )
                )

        try:
            while True:
                # Short wait while idle (nothing buffered → no stray turn); longer once a
                # stray turn is mid-stream, so we consume it whole rather than splitting it.
                timeout = self.drain_result_timeout_s if turn_open else self.drain_idle_timeout_s
                try:
                    msg = await asyncio.wait_for(it.__anext__(), timeout=timeout)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    break
                evt = event_of(msg)
                etype = evt.get("type")
                if etype == "assistant":
                    turn_open = True
                    for itm in evt.get("items", []):
                        if itm.get("kind") == "text":
                            texts.append(itm["text"])
                elif etype == "result":
                    turn_open = False
                    if evt.get("session_id"):
                        self._session_ids[project_id] = evt["session_id"]
                    await _flush()
                # system/other messages: consumed but not surfaced or counted as a turn.
        finally:
            try:
                await it.aclose()
            except Exception:
                pass
            # A stray turn still mid-stream when we gave up: surface its partial text so
            # nothing is silently dropped (rare; see the ponytail note above).
            await _flush()

    async def run_turn(
        self,
        project_id: str,
        message: str,
        on_event: Optional[EmitFn] = None,
        session_id: Optional[str] = None,
    ) -> TurnResult:
        """Send a message to the project's session and stream the response.

        `session_id` is the browser session the message arrived on (X-ON-Session). It is
        passed explicitly rather than read from the request context because this coroutine
        outlives the request, and everything it starts — renders, media ops — inherits it."""
        client = await self._get_client(project_id)
        if on_event is not None:
            self._emit[project_id] = on_event

        is_fresh = self._fresh_client.pop(project_id, False)

        # Warm client only: drain any stray/unsolicited turn (a background Bash-task
        # completion or a scheduled wakeup that arrived BETWEEN messages) before we send
        # this one, so receive_response() can't read that buffered turn and mis-attribute
        # it as the answer to THIS message (the off-by-one). A fresh client has never had a
        # turn, so nothing is buffered — skip it (and avoid touching a just-connected stream).
        if not is_fresh:
            drained_before = (
                len(self._pending_unsolicited.get(project_id) or ()) if hasattr(self, "_pending_unsolicited") else 0
            )
            await self._drain_unsolicited(project_id, client, on_event)
            analytics.capture(
                "agent_continuity",
                {
                    "event": "unsolicited_drained",
                    "n_drained": drained_before,
                    "mid_thread": True,
                    "project_id": self._project_key(project_id),
                },
            )

        # On the first turn of a freshly-(re)created client, ground the agent in
        # on-disk progress so it RESUMES the project instead of starting over.
        # (When the SDK session was resumed, this is a harmless reminder; when the
        # client is cold — e.g. after a backend restart — it's what preserves the work.)
        prompt = message
        if is_fresh:
            prompt = f"{self._first_turn_preamble(project_id)}\n\n{message}"
        else:
            # Warm client (e.g. resumed after Stop, where interrupt() keeps the client):
            # the fresh-client preamble won't run, so surface any unconsumed finished
            # render/media-op here — attached to THIS user message, so it's correctly attributed.
            render_note = self._render_resume_note(project_id)
            if render_note:
                prompt = f"{render_note}\n\n{message}"

        texts: list[str] = []
        result = TurnResult(text="", is_error=False, num_turns=0, total_cost_usd=None)
        _error_occurred = False
        # Mint the turn id HERE — project_id is in scope and everything downstream
        # (renders, media ops, tool outcomes) hangs off it.
        turn_id = uuid.uuid4().hex[:16]
        self._turn_ctx[project_id] = {"turn_id": turn_id, "session_id": session_id}
        tools = _TurnTools()
        before = _doc_snapshot(self.projects_dir, project_id)
        t0 = time.monotonic()
        stop_reason: Optional[str] = None
        analytics.capture(
            "agent_turn_started",
            {
                "turn_id": turn_id,
                "session_id": session_id,
                "project_id": self._project_key(project_id),
                "model": self._model_for(project_id),
                "thread_kind": "new" if is_fresh else "resumed",
                "is_fresh_client": is_fresh,
                # A LENGTH, never the text. Named input_chars because _scrub destroys any key
                # containing "prompt"/"message"/"text": prompt_len arrives as prompt_len_len=None.
                "input_chars": len(message or ""),
            },
        )
        try:
            await client.query(prompt)
            async for msg in client.receive_response():
                evt = event_of(msg)
                if on_event is not None:
                    await _maybe_await(on_event(evt))
                if evt["type"] == "assistant":
                    for it in evt["items"]:
                        kind = it.get("kind")
                        if kind == "text":
                            texts.append(it["text"])
                        elif kind == "tool_use":
                            tools.started(it.get("id"), it.get("name", ""))
                            # What the agent is doing RIGHT NOW, so an interrupt can say what
                            # the user gave up on. One assignment, no new traversal.
                            ctx = self._turn_ctx.get(project_id)
                            if ctx is not None:
                                ctx["tool_in_flight"] = it.get("name", "")
                            # Persist the tool call so the Activity tab can show
                            # what files/skills/tools the agent touched, after the
                            # turn and across restarts. Defensive: never raises.
                            record_tool_use(
                                self.projects_dir,
                                project_id,
                                it.get("name", ""),
                                it.get("detail", "") or "",
                            )
                        elif kind == "tool_result":
                            # The join. `tool_result` carries only tool_use_id + is_error —
                            # no name, no project, no turn — so the outcome of every tool
                            # call used to be read at event_of() and thrown away. It cannot
                            # be resolved there either: event_of takes one argument and is
                            # also called from _drain_unsolicited, which DISCARDS turns.
                            failed = tools.finished(it.get("tool_use_id"), bool(it.get("is_error")))
                            if failed is not None:
                                analytics.capture(
                                    "agent_tool_failed",
                                    {
                                        "turn_id": turn_id,
                                        "session_id": session_id,
                                        "project_id": self._project_key(project_id),
                                        "tool_invocation_id": it.get("tool_use_id"),
                                        "tool_id": failed["tool_id"],
                                        "family": failed["family"],
                                        "outcome": "returned_error",
                                        "duration_ms": failed["duration_ms"],
                                    },
                                )
                elif evt["type"] == "result":
                    result.is_error = bool(evt.get("is_error"))
                    result.num_turns = int(evt.get("num_turns") or 0)
                    result.total_cost_usd = evt.get("total_cost_usd")
                    stop_reason = evt.get("stop_reason")
                    # Remember the session id so we can RESUME (not restart-cold)
                    # if this session later dies.
                    if evt.get("session_id"):
                        self._session_ids[project_id] = evt["session_id"]
                    if result.is_error:
                        _error_occurred = True
        except Exception as exc:
            _error_occurred = True
            analytics.capture(
                "agent_turn_failed",
                {
                    "turn_id": turn_id,
                    "session_id": session_id,
                    "project_id": self._project_key(project_id),
                    "phase": "stream",
                    "failure_class": _classify_turn_error(exc),
                    "retryable": _classify_turn_error(exc) in ("transport", "auth"),
                },
            )
            raise
        finally:
            # In the `finally`, NOT after it: the except above re-raises, so a line placed
            # after this block is unreachable on a crashed turn — and a crashed turn is
            # exactly the one worth reporting. `finally` runs once on both paths, so no
            # dedupe is needed; the fields the raise left unset carry their defaults.
            self._emit.pop(project_id, None)
            self._turn_ctx.pop(project_id, None)
            self._report_turn(
                project_id,
                turn_id,
                session_id,
                result,
                tools,
                before,
                t0,
                stop_reason,
                errored=_error_occurred,
            )
            if _error_occurred:
                # The session is broken (budget ceiling, transport crash, ...).
                # Drop the dead client, but flag the next turn to RESUME the same
                # session_id so the agent keeps its context. On-disk artifacts and
                # checkpoints are untouched either way.
                analytics.capture(
                    "agent_session_died",
                    {
                        "had_result_error": bool(result.is_error),
                        "will_resume": True,
                        "turn_id": turn_id,
                        "session_id": session_id,
                        "project_id": self._project_key(project_id),
                    },
                )
                self._resume_next[project_id] = True
                dead = self._clients.pop(project_id, None)
                if dead is not None:
                    try:
                        await dead.disconnect()
                    except Exception:
                        pass
        result.text = "".join(texts)
        return result

    def _emit_store_asset(self, project_id: str, kind: str, ok: bool) -> None:
        """Does the agent GENERATE, or only arrange?

        `kind='final_render'` routes to publish_final_render with receipt_doc=None — the
        publisher deliberately refuses "provenance it has not earned" — so that path produces
        a real final.mp4 that NEVER fires export_completed. Flagged here rather than left as a
        hole in the North Star: activation could otherwise read 0 for an entire agent path.
        """
        try:
            final = kind == "final_render"
            analytics.capture(
                "agent_store_asset",
                {
                    "kind": kind if kind in ("final_render", "video", "image", "audio", "music") else "other",
                    "ok": ok,
                    "was_final_render": final,
                    "unreceipted_final_artifact": bool(final and ok),
                    "turn_id": (self._turn_ctx.get(project_id) or {}).get("turn_id"),
                    # Explicit, for the same reason as turn_id: an MCP tool handler runs in the
                    # client's task, so current_session_id() would resolve to whichever session
                    # BUILT the client and stay wrong for every later turn (F12).
                    "session_id": (self._turn_ctx.get(project_id) or {}).get("session_id"),
                    "project_id": self._project_key(project_id),
                },
            )
        except Exception:
            pass

    def _project_key(self, project_id: str) -> Optional[str]:
        """The persisted random analytics id for a project — never the user-derived slug."""
        return analytics.project_key(self.projects_dir, project_id)

    def _report_turn(
        self,
        project_id: str,
        turn_id: str,
        session_id: Optional[str],
        result: TurnResult,
        tools: "_TurnTools",
        before: dict[str, int],
        t0: float,
        stop_reason: Optional[str],
        errored: bool = False,
    ) -> None:
        """The turn's two terminal events. Called from run_turn's `finally`, so it must
        tolerate a turn that raised before any of this was populated."""
        try:
            orphans = tools.close()
            for orphan in orphans:
                analytics.capture(
                    "agent_tool_failed",
                    {
                        "turn_id": turn_id,
                        "session_id": session_id,
                        "project_id": self._project_key(project_id),
                        "outcome": "no_result",
                        **orphan,
                    },
                )
            after = _doc_snapshot(self.projects_dir, project_id)
            analytics.capture(
                "agent_turn_completed",
                {
                    "turn_id": turn_id,
                    "session_id": session_id,
                    "project_id": self._project_key(project_id),
                    # `result.is_error` is only ever set by a ResultMessage, so a turn that RAISED
                    # left it False — and the whole reason this emit lives in the `finally` is to
                    # catch that path. Without the OR, every crashed turn reported as a success.
                    "is_error": bool(result.is_error or errored),
                    "sdk_turns": result.num_turns,
                    "cost_usd": result.total_cost_usd,
                    "wall_s": round(time.monotonic() - t0, 1),
                    "stop_reason": stop_reason,
                    "tool_calls": tools.calls,
                    "tool_errors": tools.errors,
                    "orphan_starts": len(orphans),
                    "orphan_results": tools.orphan_results,
                    "duplicate_results": tools.duplicate_results,
                    # Authorship by DIFF (see _doc_snapshot): the only honest way to say whether
                    # the agent actually changed anything.
                    "doc_changed": after["hash"] != before["hash"],
                    "cuts_delta": after["cuts"] - before["cuts"],
                    "overlays_delta": after["overlays"] - before["overlays"],
                    "audio_delta": after["audio"] - before["audio"],
                    "artifacts_delta": after["artifacts"] - before["artifacts"],
                },
            )
            analytics.capture(
                "agent_tool_rollup",
                {
                    "turn_id": turn_id,
                    "session_id": session_id,
                    "project_id": self._project_key(project_id),
                    **tools.rollup(),
                },
            )
        except Exception:
            pass  # a reporting bug must never change how a turn ends

    async def interrupt(self, project_id: str) -> bool:
        """Stop the agent mid-turn (the UI 'Stop' button).

        Sends the SDK interrupt signal to the live session. The session and its
        conversation context survive — the agent stops what it's doing and the
        current turn ends; the next message continues normally. Returns True if
        an interrupt was delivered, False if there was no live client to stop.
        """
        client = self._clients.get(project_id)
        if client is None:
            return False
        try:
            await client.interrupt()
            ctx = self._turn_ctx.get(project_id) or {}
            analytics.capture(
                "agent_interrupted",
                {
                    "tool_in_flight": _known_or_hashed(ctx.get("tool_in_flight") or "none"),
                    "turn_id": ctx.get("turn_id"),
                    "session_id": ctx.get("session_id"),
                    "project_id": self._project_key(project_id),
                },
            )
            return True
        except Exception:
            return False

    async def switch_session(self, project_id: str, session_id: Optional[str]) -> None:
        """Align the project's live session to a specific thread's session_id.

        Reopening a stored thread should continue THAT conversation. If the live
        session differs, tear it down; the next turn rebuilds — resuming the
        given session_id (existing thread) or starting fresh (new thread, None).
        Single live session per project (fine for single-user local).
        """
        if self._session_ids.get(project_id) == session_id and project_id in self._clients:
            return
        dead = self._clients.pop(project_id, None)
        if dead is not None:
            try:
                await dead.disconnect()
            except Exception:
                pass
        if session_id:
            self._session_ids[project_id] = session_id
            self._resume_next[project_id] = True  # rebuild WITH resume
        else:
            self._session_ids.pop(project_id, None)
            self._resume_next.pop(project_id, None)  # brand-new thread -> fresh session

    async def set_model(self, project_id: str, model: Optional[str]) -> None:
        """Point the project's session at a UI-selected model.

        The model is baked into the SDK client at build time, so a change means
        tearing down the live client; the next turn rebuilds with the new model.
        Conversation context survives — if the project already has a session, the
        rebuild RESUMES it (so switching model mid-chat keeps history). Unknown or
        empty ids are ignored (the session keeps its current model). No-op when the
        model is unchanged, so it's safe to call on every turn.
        """
        if not model or model not in AGENT_MODELS:
            return
        if self._model_for(project_id) == model:
            return
        self._models[project_id] = model
        dead = self._clients.pop(project_id, None)
        if dead is not None:
            try:
                await dead.disconnect()
            except Exception:
                pass
        # Preserve context across the model swap: resume the existing session if any.
        if self._session_ids.get(project_id):
            self._resume_next[project_id] = True

    async def aclose(self) -> None:
        for client in self._clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass
        self._clients.clear()
