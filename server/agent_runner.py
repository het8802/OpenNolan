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
DEFAULT_ANSWER_TIMEOUT_S = 900               # users may take a while to answer a question

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
    # Question tools are always allowed — asking the user is never destructive.
    # Covers the built-in AskUserQuestion and our in-process ask_user MCP tool.
    if tool_name == "AskUserQuestion" or tool_name.startswith("mcp__mc__"):
        return ToolDecision(ACTION_ALLOW, "clarifying-question tool")
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


AGENT_SYSTEM_PROMPT = """You are the OpenMontage production agent, running HEADLESS. \
The user steers via a Mission Control UI between turns.

Obey the repo contract exactly:
- Read CLAUDE.md then AGENT_GUIDE.md before acting. Follow Rule Zero.
- Read the per-stage director skill before each stage. Read Layer 3 skills before any generation tool.
- Checkpoints live under projects/<project_id>/ (NOT pipelines/).

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

To ask the user a clarifying question, call the `ask_user` tool with your question and a list \
of options (the user picks one in the UI and it comes back as the tool result). Use it whenever \
your skills tell you to ask the user something.

Announce cost before any paid generation. Never exceed the budget.
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
    resume: Optional[str] = None,
    mcp_servers: Optional[dict[str, Any]] = None,
    disallowed_tools: Optional[list[str]] = None,
):
    """Construct ClaudeAgentOptions for an OpenMontage agent session.

    ``resume`` is a prior session_id. When set, the SDK restores that
    conversation's full history into the new client, so a session that died
    (transport crash, budget ceiling) comes back with its context intact —
    the agent remembers what it was doing, on a fresh budget.

    ``mcp_servers`` registers in-process tools (the ask_user question tool).
    ``disallowed_tools`` steers the agent away from the built-in AskUserQuestion
    (whose headless I/O we don't control) toward our ask_user tool.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        cwd=str(repo_root),
        system_prompt=AGENT_SYSTEM_PROMPT,
        model=model,
        max_budget_usd=max_budget_usd,
        permission_mode="default",      # so can_use_tool is consulted
        setting_sources=["project"],    # load CLAUDE.md -> the contract applies
        can_use_tool=make_can_use_tool(confirm_handler),
        resume=resume,
        mcp_servers=mcp_servers or {},
        disallowed_tools=disallowed_tools or [],
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
                items.append({
                    "kind": "tool_use",
                    "name": block.name,
                    "id": block.id,
                    "input": trunc,
                    "detail": detail.get("label", ""),
                })
            elif isinstance(block, ToolResultBlock):
                # surface errors; truncate large success output
                content = block.content
                if isinstance(content, str) and len(content) > 2000:
                    content = content[:2000] + "\n… (truncated)"
                items.append({
                    "kind": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "is_error": block.is_error,
                    "content": content,
                })
            elif isinstance(block, ThinkingBlock):
                items.append({"kind": "thinking"})
        return {"type": "assistant", "items": items}
    if isinstance(message, UserMessage):
        # Tool results coming back from the environment — emit as activity
        items = []
        for block in (message.content if isinstance(message.content, list) else []):
            if isinstance(block, ToolResultBlock):
                content = block.content
                if isinstance(content, str) and len(content) > 2000:
                    content = content[:2000] + "\n… (truncated)"
                items.append({
                    "kind": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "is_error": block.is_error,
                    "content": content,
                })
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
    answer_timeout_s: int = DEFAULT_ANSWER_TIMEOUT_S
    client_factory: Optional[Callable[[str], Any]] = None

    _clients: dict[str, Any] = field(default_factory=dict, init=False)
    _emit: dict[str, EmitFn] = field(default_factory=dict, init=False)
    _pending: dict[str, asyncio.Future] = field(default_factory=dict, init=False)
    _confirm_seq: int = field(default=0, init=False)
    _answers: dict[str, asyncio.Future] = field(default_factory=dict, init=False)  # question_id -> answer future
    _question_seq: int = field(default=0, init=False)
    _session_ids: dict[str, str] = field(default_factory=dict, init=False)   # last session_id per project
    _resume_next: dict[str, bool] = field(default_factory=dict, init=False)  # rebuild-with-resume after error
    _fresh_client: dict[str, bool] = field(default_factory=dict, init=False) # client just (re)created this turn

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)
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
                    "options": {"type": "array", "items": {"type": "string"},
                                "description": "Answer choices to offer."},
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

        mc_server = create_sdk_mcp_server("mc", "1.0.0", [ask_user])

        # If a prior session for this project died, resume it so the agent
        # comes back with its full conversation context (on a fresh budget).
        resume = None
        if self._resume_next.pop(project_id, False):
            resume = self._session_ids.get(project_id)

        options = build_agent_options(
            self.repo_root,
            model=self.model,
            max_budget_usd=self.max_budget_usd,
            confirm_handler=confirm,
            resume=resume,
            mcp_servers={"mc": mc_server},
            # Steer the agent to ask_user (we control its UI round-trip) instead
            # of the built-in AskUserQuestion (no controllable headless I/O).
            disallowed_tools=["AskUserQuestion"],
        )
        return ClaudeSDKClient(options=options)

    async def _get_client(self, project_id: str) -> Any:
        client = self._clients.get(project_id)
        if client is None:
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
            from lib.project import get_project_pipeline_type
            pt = get_project_pipeline_type(self.repo_root / "projects", project_id)
        except Exception:
            pt = None
        pipeline_clause = f" using the '{pt}' pipeline" if pt else ""
        stage_cmd = f"python scripts/update_stage.py {project_id} <stage> <status>" + (f" {pt}" if pt else "")
        return (
            f"[PROJECT CONTEXT: You are working on the existing project '{project_id}'{pipeline_clause}. "
            f"Use EXACTLY this project_id for everything — do NOT create a new project directory. "
            f"Write artifacts to projects/{project_id}/artifacts/, assets to projects/{project_id}/assets/, "
            f"and the final render to projects/{project_id}/renders/. As you work each stage, update its "
            f"status so the UI stepper reflects progress: run `{stage_cmd}` "
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

            projects_dir = self.repo_root / "projects"
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
                f"Existing artifacts in projects/{project_id}/artifacts/: {', '.join(artifacts) or 'none'}. "
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
        return "\n".join(parts)

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
        await _maybe_await(emit({
            "type": "question",
            "question_id": question_id,
            "header": header,
            "question": question,
            "options": list(options),
        }))
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

    async def run_turn(
        self, project_id: str, message: str, on_event: Optional[EmitFn] = None
    ) -> TurnResult:
        """Send a message to the project's session and stream the response."""
        client = await self._get_client(project_id)
        if on_event is not None:
            self._emit[project_id] = on_event

        # On the first turn of a freshly-(re)created client, ground the agent in
        # on-disk progress so it RESUMES the project instead of starting over.
        # (When the SDK session was resumed, this is a harmless reminder; when the
        # client is cold — e.g. after a backend restart — it's what preserves the work.)
        prompt = message
        if self._fresh_client.pop(project_id, False):
            prompt = f"{self._first_turn_preamble(project_id)}\n\n{message}"

        texts: list[str] = []
        result = TurnResult(text="", is_error=False, num_turns=0, total_cost_usd=None)
        _error_occurred = False
        try:
            await client.query(prompt)
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
                    # Remember the session id so we can RESUME (not restart-cold)
                    # if this session later dies.
                    if evt.get("session_id"):
                        self._session_ids[project_id] = evt["session_id"]
                    if result.is_error:
                        _error_occurred = True
        except Exception:
            _error_occurred = True
            raise
        finally:
            self._emit.pop(project_id, None)
            if _error_occurred:
                # The session is broken (budget ceiling, transport crash, ...).
                # Drop the dead client, but flag the next turn to RESUME the same
                # session_id so the agent keeps its context. On-disk artifacts and
                # checkpoints are untouched either way.
                self._resume_next[project_id] = True
                dead = self._clients.pop(project_id, None)
                if dead is not None:
                    try:
                        await dead.disconnect()
                    except Exception:
                        pass
        result.text = "".join(texts)
        return result

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
            self._resume_next[project_id] = True   # rebuild WITH resume
        else:
            self._session_ids.pop(project_id, None)
            self._resume_next.pop(project_id, None)  # brand-new thread -> fresh session

    async def aclose(self) -> None:
        for client in self._clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass
        self._clients.clear()
