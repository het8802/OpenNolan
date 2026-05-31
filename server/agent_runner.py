"""Headless agent runner for Mission Control.

Wraps the Claude Agent SDK as the engine that drives OpenMontage pipelines,
the same engine a human Claude Code session uses today. Both behavioral
spikes validated (on Sonnet) that a headless agent obeys the contract: reads
AGENT_GUIDE, produces schema-valid artifacts, records the render-runtime
decision with both runtimes, and persists `awaiting_human` at gates.

This module adds the safety layer the spikes told us we need:

- decide_tool: a code-level permission policy. Safe reads/writes run free; Bash
  commands are inspected for destructive/exfil patterns and routed to a UI
  confirm. (OpenMontage tools run as Python *through Bash*, so a tool-NAME
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
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

DEFAULT_MODEL = "claude-sonnet-4-6"          # cheaper than Opus, held the contract in the spike
DEFAULT_MAX_BUDGET_USD = 5.0                 # SDK-native hard ceiling per session
DEFAULT_CONFIRM_TIMEOUT_S = 300

# Always-safe tools (run unattended).
SAFE_TOOLS = frozenset({
    "Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite", "WebSearch", "WebFetch",
})
# Writes are legitimate (the agent writes artifacts/checkpoints under projects/).
WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit", "MultiEdit"})

ACTION_ALLOW = "allow"
ACTION_CONFIRM = "confirm"

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
    action: str  # ACTION_ALLOW | ACTION_CONFIRM
    reason: str


def bash_destructive_reason(command: str) -> Optional[str]:
    """Return a label if the command matches a destructive pattern, else None."""
    for rx, label in _DESTRUCTIVE_PATTERNS:
        if rx.search(command):
            return label
    return None


def decide_tool(tool_name: str, tool_input: dict[str, Any] | None) -> ToolDecision:
    """Allow safe tools and clean Bash; route destructive Bash + unknown tools to confirm."""
    if tool_name in SAFE_TOOLS or tool_name in WRITE_TOOLS:
        return ToolDecision(ACTION_ALLOW, f"{tool_name} is a safe/standard tool")
    if tool_name == "Bash":
        command = (tool_input or {}).get("command", "") or ""
        label = bash_destructive_reason(command)
        if label:
            return ToolDecision(ACTION_CONFIRM, f"Bash flagged: {label}")
        return ToolDecision(ACTION_ALLOW, "Bash has no destructive markers")
    # Unknown / MCP / other tool -> be conservative.
    return ToolDecision(ACTION_CONFIRM, f"unrecognized tool {tool_name!r}")


# confirm_handler(tool_name, tool_input, reason) -> approved?
ConfirmHandler = Callable[[str, dict[str, Any], str], Awaitable[bool]]


def make_can_use_tool(confirm_handler: Optional[ConfirmHandler] = None):
    """Build the SDK `can_use_tool` callback from the policy.

    Flagged calls go to `confirm_handler`. With no handler (a fully
    unattended run), flagged calls are DENIED — the safe default.
    """
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

    async def can_use_tool(tool_name: str, tool_input: dict[str, Any], context: Any):
        decision = decide_tool(tool_name, tool_input)
        if decision.action == ACTION_ALLOW:
            return PermissionResultAllow()
        if confirm_handler is None:
            return PermissionResultDeny(message=f"Blocked (no confirm handler): {decision.reason}")
        approved = await confirm_handler(tool_name, tool_input, decision.reason)
        if approved:
            return PermissionResultAllow()
        return PermissionResultDeny(message=f"Denied by user: {decision.reason}")

    return can_use_tool


AGENT_SYSTEM_PROMPT = """You are the OpenMontage production agent, running HEADLESS (no human \
is watching mid-turn; the user steers via a UI between turns).

Obey the repo contract exactly:
- Read CLAUDE.md then AGENT_GUIDE.md before acting. Follow Rule Zero: all production goes through a \
pipeline; read the stage director skill before each stage and the Layer 3 skill before each tool.
- Checkpoints live under projects/<project_id>/ (NOT pipelines/). Persist them with the CLI:
  `python -m lib.checkpoint write --projects-dir projects --project-id <id> --stage <s> --status <st> \
--pipeline-type <p> --artifacts-file <f.json>`.
- At approval gates (human_approval_default: true), STOP and persist status: awaiting_human — do not \
auto-advance. The UI will approve and you resume.
- When composing video, present BOTH render runtimes (Remotion AND HyperFrames) and record a \
render_runtime_selection decision listing both in options_considered.
- Announce cost before any paid generation and never exceed the budget.
"""


def auth_configured() -> bool:
    """True if the SDK has credentials to run (subscription OAuth or API key)."""
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"))


def build_agent_options(
    repo_root: Path | str,
    *,
    model: str = DEFAULT_MODEL,
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
    confirm_handler: Optional[ConfirmHandler] = None,
):
    """Construct ClaudeAgentOptions for an OpenMontage agent session."""
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        cwd=str(repo_root),
        system_prompt=AGENT_SYSTEM_PROMPT,
        model=model,
        max_budget_usd=max_budget_usd,
        permission_mode="default",      # so can_use_tool is consulted
        setting_sources=["project"],    # load CLAUDE.md -> the contract applies
        can_use_tool=make_can_use_tool(confirm_handler),
    )


# --------------------------------------------------------------------------
# Event normalization — SDK messages -> JSON-serializable dicts for SSE.
# --------------------------------------------------------------------------

def _truncate_input(tool_input: dict[str, Any] | None, limit: int = 600) -> dict[str, Any]:
    if not isinstance(tool_input, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in tool_input.items():
        if isinstance(v, str) and len(v) > limit:
            out[k] = v[:limit] + "…"
        else:
            out[k] = v
    return out


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
    )

    if isinstance(message, AssistantMessage):
        items: list[dict[str, Any]] = []
        for block in message.content or []:
            if isinstance(block, TextBlock):
                items.append({"kind": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                items.append({"kind": "tool_use", "name": block.name,
                              "id": block.id, "input": _truncate_input(block.input)})
            elif isinstance(block, ToolResultBlock):
                items.append({"kind": "tool_result", "tool_use_id": block.tool_use_id,
                              "is_error": block.is_error})
            elif isinstance(block, ThinkingBlock):
                items.append({"kind": "thinking"})
        return {"type": "assistant", "items": items}
    if isinstance(message, ResultMessage):
        return {
            "type": "result",
            "is_error": message.is_error,
            "num_turns": message.num_turns,
            "total_cost_usd": message.total_cost_usd,
            "result": message.result,
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


EmitFn = Callable[[dict[str, Any]], Any]  # sync or async


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value


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
    client_factory: Optional[Callable[[str], Any]] = None

    _clients: dict[str, Any] = field(default_factory=dict, init=False)
    _emit: dict[str, EmitFn] = field(default_factory=dict, init=False)
    _pending: dict[str, asyncio.Future] = field(default_factory=dict, init=False)
    _confirm_seq: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)
        if self.client_factory is None:
            self.client_factory = self._default_client_factory

    def _default_client_factory(self, project_id: str) -> Any:
        from claude_agent_sdk import ClaudeSDKClient

        async def confirm(tool_name: str, tool_input: dict[str, Any], reason: str) -> bool:
            return await self._confirm(project_id, tool_name, tool_input, reason)

        options = build_agent_options(
            self.repo_root,
            model=self.model,
            max_budget_usd=self.max_budget_usd,
            confirm_handler=confirm,
        )
        return ClaudeSDKClient(options=options)

    async def _get_client(self, project_id: str) -> Any:
        client = self._clients.get(project_id)
        if client is None:
            client = self.client_factory(project_id)
            await client.connect()
            self._clients[project_id] = client
        return client

    async def _confirm(self, project_id, tool_name, tool_input, reason) -> bool:
        emit = self._emit.get(project_id)
        if emit is None:
            return False  # no active stream to ask through -> deny
        self._confirm_seq += 1
        confirm_id = f"{project_id}:{self._confirm_seq}"
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[confirm_id] = fut
        await _maybe_await(emit({
            "type": "confirm_request",
            "confirm_id": confirm_id,
            "tool": tool_name,
            "reason": reason,
            "input": _truncate_input(tool_input),
        }))
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

    async def run_turn(
        self, project_id: str, message: str, on_event: Optional[EmitFn] = None
    ) -> TurnResult:
        """Send a message to the project's session and stream the response."""
        client = await self._get_client(project_id)
        if on_event is not None:
            self._emit[project_id] = on_event
        texts: list[str] = []
        result = TurnResult(text="", is_error=False, num_turns=0, total_cost_usd=None)
        try:
            await client.query(message)
            async for msg in client.receive_response():
                evt = event_of(msg)
                if on_event is not None:
                    await _maybe_await(on_event(evt))
                if evt["type"] == "assistant":
                    for it in evt["items"]:
                        if it.get("kind") == "text":
                            texts.append(it["text"])
                elif evt["type"] == "result":
                    result.is_error = bool(evt.get("is_error"))
                    result.num_turns = int(evt.get("num_turns") or 0)
                    result.total_cost_usd = evt.get("total_cost_usd")
        finally:
            self._emit.pop(project_id, None)
        result.text = "".join(texts)
        return result

    async def aclose(self) -> None:
        for client in self._clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass
        self._clients.clear()
