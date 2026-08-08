"""The three taxonomy contract tests (plan §7).

They exist because of three defects that shipped past code review in this exact area:

  1. TWO-WAY NAME COVERAGE — `export_completed` lived in a test file and nowhere else, so
     the North Star read zero forever and nothing noticed. (a) catches a name emitted but
     undeclared; (b) catches a name declared but never emitted.
  2. GOLDEN RECEIPT JOURNEY — `export_completed` must be impossible without a receipt-backed
     publish, and each job must reach exactly one terminal event.
  3. SCRUB ROUND-TRIP — `_scrub` substring-matches the property KEY, so `prompt_len=412`
     silently becomes `prompt_len_len=None`. Verified by execution, not by reading.

pytest HARD-DISABLES analytics (analytics.py `_under_pytest`), so every assertion here runs
against a FAKE SINK, never the real client and never the network.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from server import analytics

REPO = Path(__file__).resolve().parents[2]
TAXONOMY_DIR = REPO / "schemas" / "analytics"
# The SAME all-or-nothing merge the runtime uses, not a second implementation of it: a test
# that merged more leniently than production would pass on a taxonomy production refuses.
TAXONOMY = analytics._merge_taxonomy(sorted(TAXONOMY_DIR.glob("*.json")))
EVENTS = TAXONOMY["events"]

# Where a product event may be emitted from. lib/ is absent on purpose: "lib must not depend
# on server" (lib/project.py), and analytics imports server.settings.
# scripts/ is a REAL emit surface, not tooling: server/agent_runner.py instructs the agent to
# run `python scripts/update_stage.py …`, so the majority of pipeline stage transitions are
# written from a separate short-lived process there (which reaches PostHog via server/outbox).
_SOURCE_GLOBS = ("server/**/*.py", "scripts/*.py", "desktop/*.js", "web/src/**/*.js", "web/src/**/*.jsx")


@pytest.fixture
def sink(monkeypatch):
    """Collect what capture() would have sent, with the real validate_event/_scrub in front."""
    sent: list[tuple[str, dict]] = []

    class FakeClient:
        def capture(self, **kw):
            sent.append((kw["event"], kw["properties"]))

        def capture_exception(self, exc, **kw):
            sent.append(("$exception", kw.get("properties") or {}))

    monkeypatch.setattr(analytics, "_get_client", lambda: FakeClient())
    return sent


def last_of(sink, name: str) -> dict:
    """The most recent capture of ONE event name.

    Not `sink[-1]`: a capture that drops a property is followed by a `data_quality_violation`
    naming the event that drifted, so "the last event" is no longer "the event I captured"."""
    for event, props in reversed(sink):
        if event == name:
            return props
    raise AssertionError(f"{name} was never captured; got {[e for e, _ in sink]}")


# ── 1. two-way name coverage ─────────────────────────────────────────────────


def _emitted_names() -> set[str]:
    """Every event name passed to a real emitter CALL in app source.

    Python is parsed with `ast`, not scanned: a regex counts a name inside a docstring or a
    commented-out line as a live call site, which is precisely how a dead emitter passes 1b —
    and 1b exists because `export_completed` once lived only in a test file and read zero
    forever. A test whose failure mode is silently passing is worse than no test.

    JS has no AST parser available here, so its comments (line AND block) are stripped before
    matching. That covers the same hole for the shell and the renderer.
    """
    names: set[str] = set()
    for glob in _SOURCE_GLOBS:
        for path in REPO.glob(glob):
            if "node_modules" in path.parts or ".test." in path.name:
                continue
            src = path.read_text(errors="ignore")
            names |= _python_call_names(src) if path.suffix == ".py" else _js_call_names(src)
    return names


# The emitter functions. A literal anywhere else is not a call site.
_EMITTERS = {"capture", "_emit", "track", "postToPostHog"}


def _python_call_names(src: str) -> set[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        attr = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        first = node.args[0]
        if attr in _EMITTERS and isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.add(first.value)
    return names


# A regex cannot separate JS code from JS strings, and the attempts prove it: strip-then-match
# over-deleted after a string containing spaced double slashes and between string-held `/*`
# markers, which made 1a go BLIND — it found no emitters, so it had nothing to complain about.
# 1b fails loudly when that happens; 1a fails silently, and 1a is the direction that keeps an
# undeclared event off the wire. So scan properly instead: one pass that knows what a string,
# a comment and a regex literal are, keeping only the code.
_JS_PUNCT_BEFORE_REGEX = set("(,=:[!&|?{};\n+-*%~^<>")


def js_code_only(src: str) -> str:
    """Blank out comments, strings and regex literals, preserving offsets and newlines.

    Not a full parser — a template literal's `${...}` interpolation is treated as part of the
    string. That direction is deliberate: it can only ever HIDE an emitter, which makes 1b
    fail loudly, never admit a commented-out one, which is what fails silently.
    """
    out = []
    i, n = 0, len(src)
    prev_code = ""  # last significant code char, for regex-vs-division
    while i < n:
        c = src[i]
        two = src[i : i + 2]
        if two == "//":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in src[i:j]))
            i = j
        elif c in "\"'`":
            quote, j = c, i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote or (quote != "`" and src[j] == "\n"):
                    j += 1
                    break
                j += 1
            out.append("".join(ch if ch == "\n" else " " for ch in src[i:j]))
            i = j
            prev_code = quote
        elif c == "/" and (prev_code == "" or prev_code in _JS_PUNCT_BEFORE_REGEX):
            j, in_class = i + 1, False  # a regex literal: / may appear inside [...]
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "/" and not in_class:
                    j += 1
                    break
                elif src[j] == "\n":
                    break
                j += 1
            out.append(" " * (j - i))
            i = j
        else:
            out.append(c)
            if not c.isspace():
                prev_code = c
            i += 1
    return "".join(out)


_JS_CALL = re.compile(r"""(?:\.capture|_emit|\btrack|postToPostHog)\(\s*['"]([a-z][a-z0-9_]+)['"]""")
# desktop/main.js POSTs its payload object straight to PostHog: {event: 'desktop_error', ...}
_JS_PAYLOAD = re.compile(r"""(?<![\w'"])event\s*:\s*['"]([a-z][a-z0-9_]+)['"]""")


def _js_call_names(src: str) -> set[str]:
    # The event NAME is itself a string, so match against the original source at offsets the
    # scanner proved are code. js_code_only preserves length, so the two line up exactly.
    code = js_code_only(src)
    names = set()
    for pattern in (_JS_CALL, _JS_PAYLOAD):
        for m in pattern.finditer(src):
            if code[m.start()] != " ":  # the call itself must be code, not commented out
                names.add(m.group(1))
    return names


def test_1a_every_emitted_name_is_declared():
    undeclared = sorted(_emitted_names() - set(EVENTS))
    assert not undeclared, (
        f"emitted but not in schemas/analytics/*.json: {undeclared}. "
        "An undeclared event is DROPPED by validate_event — declare it or stop emitting it."
    )


def test_1b_every_declared_event_has_a_live_call_site():
    """The assertion that would have caught export_completed reading zero forever."""
    orphans = sorted(set(EVENTS) - _emitted_names())
    assert not orphans, (
        f"declared with no emitter: {orphans}. A declared-but-unemitted event is a dashboard "
        "that will read zero forever and never tell you why."
    )


# ── 2. golden receipt journey ────────────────────────────────────────────────


def _store(tmp_path, monkeypatch):
    from server.render_jobs import RenderJobStore

    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    return RenderJobStore(tmp_path)


def test_2_export_completed_requires_a_receipt_backed_publish(tmp_path, monkeypatch, sink):
    """A render that finishes is NOT an export. Only the receipt commit makes it one."""
    import server.render_jobs as rj

    store = _store(tmp_path, monkeypatch)
    proj = "p1"
    (tmp_path / proj / "renders").mkdir(parents=True)

    class Result:
        success = True
        error = None
        data = {"n_scenes": 4, "n_cached": 3, "n_rendered": 1, "final_review_status": "pass"}

    def fake_execute(_inputs):
        Path(_inputs["output_path"]).write_bytes(b"x" * 2048)
        return Result()

    monkeypatch.setattr(store, "_video_compose", lambda: type("T", (), {"execute": staticmethod(fake_execute)})())

    # (a) publish=False — an intermediate agent render. Never an export.
    store._render_locked(
        "jobA",
        proj,
        {"cuts": []},
        {"assets": []},
        tmp_path / proj / "renders" / "intermediate.mp4",
        tmp_path / proj / "renders" / "proxies",
        publish=False,
    )
    assert "export_completed" not in [e for e, _ in sink]

    # (b) publish=True and the publisher REFUSES (superseded) — still not an export.
    monkeypatch.setattr(rj, "publish_final_render", lambda *a, **k: {"published": False, "path": None})
    store._jobs["jobB"] = {"job_id": "jobB", "project_id": proj, "status": "running", "publish_intent": True}
    store._active_by_project[proj] = "jobB"
    store._render_locked(
        "jobB",
        proj,
        {"cuts": []},
        {"assets": []},
        tmp_path / proj / "renders" / "final.mp4",
        tmp_path / proj / "renders" / "proxies",
        publish=True,
    )
    assert "export_completed" not in [e for e, _ in sink]
    assert "render_superseded" in [e for e, _ in sink]

    # (c) publish=True and the receipt commits — THE North Star, exactly once.
    monkeypatch.setattr(rj, "publish_final_render", lambda *a, **k: {"published": True, "path": "renders/final.mp4"})
    store._jobs["jobC"] = {
        "job_id": "jobC",
        "project_id": proj,
        "status": "running",
        "publish_intent": True,
        "origin": "editor",
        "session_id": "sess-1",
    }
    store._active_by_project[proj] = "jobC"
    store._render_locked(
        "jobC",
        proj,
        {"cuts": [{"id": "c1"}], "audio": {"music": {"asset_id": "m"}}},
        {"assets": []},
        tmp_path / proj / "renders" / "final.mp4",
        tmp_path / proj / "renders" / "proxies",
        receipt_doc={"cuts": [{"id": "c1"}], "audio": {"music": {"asset_id": "m"}}},
        publish=True,
    )
    exports = [p for e, p in sink if e == "export_completed"]
    assert len(exports) == 1, "exactly one export per receipt"
    assert exports[0]["job_id"] == "jobC" and exports[0]["session_id"] == "sess-1"
    assert exports[0]["has_music"] is True and exports[0]["n_cuts"] == "1-3"
    # The cache numbers arrive as NUMBERS, not parsed back out of a warning string.
    assert exports[0]["n_cached"] == 3 and exports[0]["n_rendered"] == 1


def test_2b_one_terminal_event_per_job(tmp_path, monkeypatch, sink):
    """A supersede reaches _mark_superseded_locked from BOTH _set and a direct call. Without
    the changed-flag both paths emit and the render-failure rate doubles."""
    store = _store(tmp_path, monkeypatch)
    store._jobs["j"] = {"job_id": "j", "project_id": "p", "status": "running"}
    store._active_by_project["p"] = "newer-job"

    store._set("j", "p", status="done")  # superseded -> one render_superseded
    store._set("j", "p", status="failed")  # already terminal -> nothing

    assert [e for e, _ in sink] == ["render_superseded"]


# ── 3. scrub round-trip (the test neither phase-1 doc had) ───────────────────


def _all_declared_properties():
    for name, entry in EVENTS.items():
        for prop, kind in (entry.get("properties") or {}).items():
            yield name, prop, kind
    for prop, kind in TAXONOMY["envelope"].items():
        if not prop.startswith("$"):
            yield "<envelope>", prop, kind


_SAMPLE = {"N": 1, "F": True, "E": "x", "B": "1-5", "I": "abc", "A": ["x"], "O": {"x": {"calls": 1}}}


@pytest.mark.parametrize("event,prop,kind", list(_all_declared_properties()))
def test_3_every_declared_property_round_trips_through_scrub(event, prop, kind):
    """_scrub matches the KEY, so a name is destroyed by what it CONTAINS, not by its value.

    This single assertion fails on prompt_len, prompt_chars, message_len, text_len,
    content_len and content_fingerprint — every one of which was proposed during planning.
    """
    value = _SAMPLE[kind]
    assert analytics._scrub({prop: value}) == {prop: value}, (
        f"{event}.{prop} is destroyed by _scrub — it contains a reserved substring. "
        f"Rename it (e.g. *_chars, or asset_fingerprint over content_fingerprint)."
    )


def test_3b_scrub_still_destroys_the_names_that_prove_the_rule():
    # Not a hypothetical: `prompt_len` was in one phase-1 draft and `prompt_chars` in the other.
    assert analytics._scrub({"prompt_len": 412}) == {"prompt_len_len": None}
    assert analytics._scrub({"prompt_chars": 412}) == {"prompt_chars_len": None}
    assert analytics._scrub({"message_len": 88}) == {"message_len_len": None}
    assert analytics._scrub({"content_fingerprint": "a" * 16}) == {"content_fingerprint_len": 16}
    # ...and the names actually in use survive.
    assert analytics._scrub({"input_chars": 412}) == {"input_chars": 412}
    assert analytics._scrub({"feedback_chars": 88}) == {"feedback_chars": 88}


# ── validate_event: dropped AND counted ──────────────────────────────────────


def test_undeclared_property_is_dropped_and_counted(sink, monkeypatch):
    analytics.reset()
    monkeypatch.setattr(
        analytics,
        "_get_client",
        lambda: type(
            "C",
            (),
            {
                "capture": staticmethod(lambda **kw: sink.append((kw["event"], kw["properties"]))),
            },
        )(),
    )
    analytics.capture("project_created", {"style": "user", "prompt_len": 412})
    props = last_of(sink, "project_created")
    assert props["style"] == "user"
    assert "prompt_len" not in props and "prompt_len_len" not in props
    assert props["telemetry_dropped_props"] == 1  # counted, not silently lost


def test_undeclared_event_is_dropped_and_counted(sink, monkeypatch):
    analytics.reset()
    analytics.capture("totally_made_up_event", {"x": 1})
    assert sink == []
    assert analytics._counters["unknown_events"] == 1


# ── 2c. the integrated journey: project -> turn -> tool -> render -> receipt ─────
# Test 2 above proves the receipt gate in isolation. This one proves the JOINS survive across
# the three subsystems that mint the ids, which is the half a store-only test cannot reach.
# Auth and a live model are deliberately absent: a real turn spends the developer's money, so
# the SDK client is faked — everything downstream of `receive_response` is the real code.


class _FakeBlock:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _fake_sdk(monkeypatch, blocks):
    """Make agent_runner.event_of return our scripted stream without importing the real SDK."""
    import server.agent_runner as ar

    monkeypatch.setattr(ar, "event_of", lambda msg: msg)

    class FakeClient:
        async def query(self, _prompt):
            return None

        async def receive_response(self):
            for b in blocks:
                yield b

        async def disconnect(self):
            return None

    return FakeClient()


def test_2c_ids_chain_from_turn_to_tool_to_render(tmp_path, monkeypatch, sink):
    import asyncio

    import server.agent_runner as ar
    import server.render_jobs as rj

    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    analytics.set_session_id("sess-journey")
    proj = "p-journey"
    (tmp_path / proj / "artifacts").mkdir(parents=True)
    (tmp_path / proj / "renders").mkdir(parents=True)

    client = _fake_sdk(
        monkeypatch,
        [
            {"type": "assistant", "items": [{"kind": "tool_use", "id": "tu1", "name": "Bash", "detail": ""}]},
            {"type": "assistant", "items": [{"kind": "tool_result", "tool_use_id": "tu1", "is_error": True}]},
            # tu2 is opened and NEVER resolved -> the no_result orphan path.
            {"type": "assistant", "items": [{"kind": "tool_use", "id": "tu2", "name": "Read", "detail": ""}]},
            {"type": "result", "is_error": False, "num_turns": 1, "total_cost_usd": 0.02, "stop_reason": "end_turn"},
        ],
    )
    runner = ar.AgentRunner(repo_root=tmp_path, projects_dir=tmp_path, client_factory=lambda pid: client)
    monkeypatch.setattr(runner, "_get_client", lambda pid: _async(client))
    runner._fresh_client[proj] = True  # skip the unsolicited-turn drain; not what this asserts
    asyncio.run(runner.run_turn(proj, "hello", session_id="sess-journey"))

    by_name = {e: p for e, p in sink}
    # every turn-family event carries the same turn_id AND the session that caused it
    turn_id = by_name["agent_turn_started"]["turn_id"]
    for name in ("agent_turn_started", "agent_turn_completed", "agent_tool_rollup"):
        assert by_name[name]["turn_id"] == turn_id, name
        assert by_name[name]["session_id"] == "sess-journey", name
    # the project id on the wire is the PERSISTED RANDOM id, never the directory slug
    assert by_name["agent_turn_started"]["project_id"] not in (proj, None)
    assert by_name["agent_turn_started"]["project_id"] == analytics.project_key(tmp_path, proj)

    # both tool failures upload: the returned error AND the call that never came back
    outcomes = sorted(p["outcome"] for e, p in sink if e == "agent_tool_failed")
    assert outcomes == ["no_result", "returned_error"]
    assert by_name["agent_turn_completed"]["orphan_starts"] == 1

    # ...and a render started inside that turn inherits both ids all the way to the job record
    store = rj.RenderJobStore(tmp_path)
    job_id = store.start_with_inputs(
        proj, {"edit_decisions": {"cuts": []}, "session_id": "sess-journey", "turn_id": turn_id}
    )
    queued = [p for e, p in sink if e == "render_queued" and p["job_id"] == job_id]
    assert queued and queued[0]["session_id"] == "sess-journey" and queued[0]["turn_id"] == turn_id


def _async(value):
    import asyncio

    fut: "asyncio.Future" = asyncio.Future()
    fut.set_result(value)
    return fut


def test_a_crashed_turn_reports_as_an_error(tmp_path, monkeypatch, sink):
    """The emit lives in the `finally` precisely to catch this path. `result.is_error` is only
    ever set by a ResultMessage, so a raise leaves it False — reporting every crash a success."""
    import asyncio

    import server.agent_runner as ar

    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    monkeypatch.setattr(ar, "event_of", lambda msg: msg)

    class Boom:
        async def query(self, _p):
            raise RuntimeError("transport went away")

        async def disconnect(self):
            return None

    runner = ar.AgentRunner(repo_root=tmp_path, projects_dir=tmp_path, client_factory=lambda pid: Boom())
    monkeypatch.setattr(runner, "_get_client", lambda pid: _async(Boom()))
    runner._fresh_client["p1"] = True
    with pytest.raises(RuntimeError):
        asyncio.run(runner.run_turn("p1", "hi", session_id="s1"))

    completed = [p for e, p in sink if e == "agent_turn_completed"]
    assert len(completed) == 1, "the finally must emit exactly once, on the crashed path too"
    assert completed[0]["is_error"] is True
    assert [p["failure_class"] for e, p in sink if e == "agent_turn_failed"] == ["transport"]


def test_tool_pending_map_counts_duplicates_and_orphan_results():
    from server.agent_runner import _TurnTools

    t = _TurnTools()
    t.started("a", "Bash")
    assert t.finished("a", False) is None  # success -> counted, not uploaded
    assert t.finished("a", False) is None  # the SAME result again
    assert t.duplicate_results == 1
    assert t.finished("never-seen", True) is None  # a result with no matching use
    assert t.orphan_results == 1  # cause deliberately UNLABELLED until measured
    t.started("b", "Read")
    orphans = t.close()
    assert [o["tool_id"] for o in orphans] == ["Read"]
    roll = t.rollup()
    assert roll["calls"] == 2 and roll["errors"] == 1 and roll["bash_calls"] == 1


# ── 1c. 1b's own failure mode ────────────────────────────────────────────────
# A test that silently passes is worse than no test. These pin the two ways a DEAD emitter
# used to satisfy 1b: a mention inside a docstring, and one inside a block comment.


def test_1c_a_docstring_mention_is_not_a_call_site():
    src = '''
"""Module docs. We used to emit analytics.capture("ghost_event") here."""


def f():
    """analytics.capture("ghost_in_a_def")"""
    # analytics.capture("ghost_in_a_comment")
    analytics.capture("really_emitted", {"a": 1})
'''
    assert _python_call_names(src) == {"really_emitted"}


def test_1c_a_block_comment_mention_is_not_a_call_site():
    src = """
/* JSDoc: previously track('ghost_block') — removed.
 * track('ghost_multiline')
 */
// track('ghost_line')
const x = 1; // track('ghost_trailing')
track('really_emitted', {a: 1});
"""
    assert _js_call_names(src) == {"really_emitted"}


# The previous strip-first regex is kept here as the ADVERSARY. Every case below is one it
# actually loses an emitter on — verified, not assumed. A regression test that also passes
# against the broken implementation proves nothing, and the first three cases I wrote did
# exactly that.
_OLD_STRIPPER = re.compile(r"(?s:/\*.*?\*/)|^[ \t]*//.*$|(?<=[\s;{(,])//[^\n]*", re.M)


@pytest.mark.parametrize(
    "label,src",
    [
        # A regex literal whose BODY holds `//`. The stripper sees a comment and eats the line.
        ("regex literal containing //", "const re = /[ //]/; track('kept_regex');"),
        # A URL in a string: ` //` preceded by whitespace, so the stripper eats to end of line.
        ("URL inside a string", "const u = 'see https://x.io // docs'; track('kept_url');"),
        # `/*` and `*/` held in two different strings: the stripper deletes EVERYTHING between
        # them, emitters included.
        (
            "/* inside a string literal",
            "const a = 'begins /* here';\ntrack('kept_block');\nconst b = 'ends */ here';",
        ),
    ],
)
def test_1c_the_scanner_survives_what_breaks_strippers(label, src):
    """Each shape below silently deleted a real emitter. The consequence is not a noisy
    failure — it is test 1a going BLIND: with no emitters found, 1a has nothing to complain
    about and an undeclared event reaches the wire unchallenged."""
    assert not set(_JS_CALL.findall(_OLD_STRIPPER.sub("", src))), (
        f"{label}: this case no longer reproduces the defect, so it guards nothing"
    )
    found = _js_call_names(src)
    assert len(found) == 1 and next(iter(found)).startswith("kept_"), f"{label}: got {found}"


def test_1c_a_commented_out_emitter_is_still_rejected():
    """The scanner must not fix 1a by simply passing everything through — that would reopen
    the 1b hole it replaced."""
    src = "// track('ghost_line')\n/* track('ghost_block') */\ntrack('real');"
    assert _js_call_names(src) == {"real"}


def test_1c_1a_cannot_go_blind():
    """1b fails loudly when the scanner breaks; 1a fails SILENTLY. So 1a gets a floor: the
    scan must actually find emitters in both languages, in the real source tree. Without this
    a scanner returning the empty set is indistinguishable from a clean codebase."""
    names = _emitted_names()
    assert len(names) >= 15, f"the scan found only {len(names)} emitters — it is broken, not clean"
    # one that only exists in Python, and one that only exists in JS
    assert "export_completed" in names, "python scan found nothing"
    assert "session_started" in names, "javascript scan found nothing"

    main_js = (REPO / "desktop" / "main.js").read_text()
    js = _js_call_names(main_js)
    assert {"app_launch_started", "session_started", "backend_ready", "desktop_error"} <= js
    # ...and the scanner is not simply passing everything through: main.js is heavily commented.
    code = js_code_only(main_js)
    assert len(code) == len(main_js)  # offsets preserved
    assert code.count(" ") > main_js.count(" ") * 1.2  # comments really were blanked


# ── 4. the enum gate ─────────────────────────────────────────────────────────
# A property TYPED as an enum that accepts any string is not a constrained field, it is an
# unlabelled free-text field — the same class of defect as shipping the project slug. The whole
# safety argument for this instrumentation is that free text cannot reach the wire, and _scrub
# cannot help: it blocklists key NAMES, not values.


def test_4_a_declared_enum_rejects_an_undeclared_value(sink, monkeypatch):
    analytics.reset()
    monkeypatch.setattr(
        analytics,
        "_get_client",
        lambda: type("C", (), {"capture": staticmethod(lambda **kw: sink.append((kw["event"], kw["properties"])))})(),
    )
    analytics.capture(
        "render_finished",
        {"status": "done", "origin": "editor", "total_ms": 12},
    )
    analytics.capture(
        "render_finished",
        # `status` is declared E with a closed list. This is not in it.
        {"status": "a sentence the user typed into a project name", "origin": "editor", "total_ms": 12},
    )
    renders = [p for e, p in sink if e == "render_finished"]
    good, bad = renders[-2], renders[-1]
    assert good["status"] == "done"
    assert "status" not in bad, "an out-of-vocabulary enum value must NOT survive"
    assert bad["origin"] == "editor" and bad["total_ms"] == 12  # the rest of the event survives
    assert bad["telemetry_dropped_props"] >= 1  # dropped AND counted, like an unknown property


def test_4_an_undeclared_enum_still_cannot_carry_prose():
    """Not every `E` has a knowable closed set — `style` comes from the user's playbook dir and
    `os` from the platform — so those fall back to a bounded token. Free text still cannot
    pass, which is the property that actually matters."""
    tax = analytics.taxonomy()
    assert analytics._enum_ok(tax, "project_created", "style", "E", "anthropic-editorial-animated")
    assert not analytics._enum_ok(tax, "project_created", "style", "E", "Q4 launch teaser for Acme")
    assert analytics._enum_ok(tax, "app_opened", "os", "E", "macOS-26.2-arm64-arm-64bit")


def test_4_every_declared_enum_belongs_to_a_declared_property():
    """A stale enum key silently constrains nothing — or worse, a renamed property leaves the
    old vocabulary behind and the new one ungated."""
    props_by_event = {e: set(v.get("properties") or {}) for e, v in EVENTS.items()}
    all_props = set().union(*props_by_event.values())
    for key in TAXONOMY["enums"]:
        if key.startswith("$"):
            continue
        if "." in key:
            event, prop = key.split(".", 1)
            assert event in EVENTS, f"enum {key} names an event that does not exist"
            assert prop in props_by_event[event], f"enum {key} names a property {event} does not declare"
        else:
            assert key in all_props, f"shared enum {key!r} matches no declared property"


# ── 5. deny by default ───────────────────────────────────────────────────────
# The permissive branch of the enum gate is a DOOR, and the first thing through it was a
# user-created style name. A shape check cannot close it: `q4-launch-teaser` — somebody's
# unreleased campaign — is character-for-character the same shape as `instagram-fast-reel`.


def test_5_a_user_style_name_never_reaches_the_wire(tmp_path, monkeypatch, sink):
    """THE regression. `list_playbooks(packaged=False)` appends user_styles regardless of the
    flag, so the emit site classified every user style as built-in and sent the name verbatim.
    Two independent layers now stop it: the emit site collapses it, and the validator refuses
    the name even if a future caller forgets."""
    import server.app as app_mod
    from styles import playbook_loader

    monkeypatch.setenv("OPENNOLAN_USER_STYLES_DIR", str(tmp_path / "user_styles"))
    (tmp_path / "user_styles").mkdir(parents=True)
    (tmp_path / "user_styles" / "q4-launch-teaser.yaml").write_text("identity:\n  name: Q4\n")

    # layer 1 — the shipped catalogue is the right question to ask, and it excludes them
    assert "q4-launch-teaser" in playbook_loader.list_playbooks()
    assert "q4-launch-teaser" not in playbook_loader.builtin_playbooks()
    assert app_mod.builtin_playbooks is playbook_loader.builtin_playbooks

    # layer 2 — and the validator refuses the name outright
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    monkeypatch.setattr(
        analytics,
        "_get_client",
        lambda: type("C", (), {"capture": staticmethod(lambda **kw: sink.append((kw["event"], kw["properties"])))})(),
    )
    analytics.capture("project_created", {"style": "q4-launch-teaser", "pipeline_type": "instagram-fast-reel"})
    props = last_of(sink, "project_created")
    assert "style" not in props, "a user-authored style name must NOT survive"
    assert props["pipeline_type"] == "instagram-fast-reel"  # ours, so it does

    analytics.capture("project_created", {"style": "user"})  # the collapsed classification
    assert last_of(sink, "project_created")["style"] == "user"


def test_5_every_enum_property_is_declared_or_justified():
    """Deny by default only holds if the exception list is EXHAUSTIVE and reviewed. A new
    type-E property with neither a vocabulary nor a stated reason is dropped at runtime — this
    test says so at build time instead, and forces the reason to be written down."""
    ungoverned = []
    for event, entry in EVENTS.items():
        for prop, kind in (entry.get("properties") or {}).items():
            if kind != "E":
                continue
            declared = f"{event}.{prop}" in TAXONOMY["enums"] or prop in TAXONOMY["enums"]
            justified = prop in TAXONOMY["open_vocabularies"]
            if not (declared or justified):
                ungoverned.append(f"{event}.{prop}")
    assert not ungoverned, (
        f"type-E properties with no closed vocabulary and no justification: {ungoverned}. "
        "Declare the vocabulary, or add it to open_vocabularies with the reason it cannot be "
        "user-authored text."
    )


def test_5_declared_vocabularies_stay_in_sync_with_the_code_that_defines_them():
    """A stale list silently drops real data — the exact failure mode that makes people
    disable validation. Adding a style, a pipeline or a model must fail HERE, loudly."""
    from lib.pipeline_loader import list_pipelines
    from server.agent_runner import AGENT_MODELS
    from styles import playbook_loader

    assert set(TAXONOMY["enums"]["model"]) == set(AGENT_MODELS)
    assert set(TAXONOMY["enums"]["pipeline_type"]) >= set(list_pipelines(packaged=False))
    # ...plus the "user" member, which is what a user-created style collapses to.
    assert set(TAXONOMY["enums"]["style"]) == playbook_loader.builtin_playbooks() | {"user"}


# ── 6. the split taxonomy merges ALL-OR-NOTHING ──────────────────────────────
# Splitting one file into twelve buys a reviewable diff and introduces a failure the single
# file did not have: a PARTIAL merge, where one unreadable family turns its valid events into
# undeclared ones and they are dropped silently, forever, with nothing to notice. So the merge
# refuses to produce a partial result — and the total failure that replaces it is loud.


def _taxonomy_copy(tmp_path):
    """A writable copy of the real taxonomy dir, wired up as the code root."""
    dest = tmp_path / "schemas" / "analytics"
    dest.mkdir(parents=True)
    for src in TAXONOMY_DIR.glob("*.json"):
        (dest / src.name).write_text(src.read_text())
    return dest


def _use(tmp_path, monkeypatch):
    from lib import app_paths

    monkeypatch.setattr(app_paths, "code_root", lambda: tmp_path)
    analytics.reset()


def test_6_one_corrupt_family_drops_every_event_and_never_a_partial_dict(tmp_path, monkeypatch, capsys):
    """FAIL CLOSED, and fail WHOLE. `render` breaking must not leave `install` working —
    a half-loaded gate is a gate with a hole in the half you did not look at."""
    fam = _taxonomy_copy(tmp_path)
    (fam / "render.json").write_text("{ this is not json")
    _use(tmp_path, monkeypatch)

    assert analytics.taxonomy() == {}
    # every event, not just the corrupted family's
    assert analytics.validate_event("render_queued", {"origin": "editor"}) is None
    assert analytics.validate_event("session_started", {"entry": "dashboard"}) is None
    assert analytics.validate_event("app_first_run", {}) is None

    # ...and it is ANNOUNCED. Under fail-closed no event can ever carry the unknown_events
    # counter out, so without this line the outage is invisible in every log and every readback.
    err = capsys.readouterr().err
    assert "TAXONOMY FAILED TO LOAD" in err
    assert "fail-closed" in err

    analytics.taxonomy()  # cached; the line is one-time, not per-call
    assert "TAXONOMY FAILED TO LOAD" not in capsys.readouterr().err


def test_6_a_missing_envelope_is_also_total(tmp_path, monkeypatch):
    fam = _taxonomy_copy(tmp_path)
    (fam / "_envelope.json").unlink()
    _use(tmp_path, monkeypatch)
    assert analytics.taxonomy() == {}


def test_6_a_duplicate_event_name_fails_the_merge(tmp_path, monkeypatch):
    """Two files declaring one name means the survivor depends on directory order, so the
    taxonomy would mean different things on different machines."""
    fam = _taxonomy_copy(tmp_path)
    (fam / "zz_shadow.json").write_text(json.dumps({"events": {"session_started": {"properties": {}}}}))
    _use(tmp_path, monkeypatch)
    assert analytics.taxonomy() == {}


def test_6_the_intact_split_still_declares_everything(tmp_path, monkeypatch):
    """The mirror of the three above: an UNTOUCHED copy must merge cleanly and completely,
    or the fail-closed tests would pass for the wrong reason."""
    _taxonomy_copy(tmp_path)
    _use(tmp_path, monkeypatch)
    assert set(analytics.taxonomy()["events"]) == set(EVENTS)
    analytics.reset()


# ── 7. the leak class that shipped past three reviewers, twice ───────────────
# `project_id` went out as the SLUG of the name the user typed. Then `style=q4-launch-teaser`
# went out as a "built-in" style name. Both passed review because the gate they had to defeat
# is a SHAPE check, and `q4-launch-teaser` is character-for-character the same shape as
# `instagram-fast-reel`. The gate cannot close this; only the emit site can. So every new
# emit-site collapse gets an explicit test, with the leak string as its input.

LEAK = "q4-launch-teaser"


def test_7_the_shape_gate_admits_a_slug_which_is_exactly_why_emit_sites_collapse():
    """Stated as an assertion so nobody re-derives 'the validator will catch it'. It will not."""
    assert analytics._enum_ok(TAXONOMY, "x", "os", "E", LEAK) is True


def test_7_an_asset_filename_never_becomes_an_extension():
    import server.app as app_mod

    assert app_mod._extension(f"{LEAK}.mp4") == ".mp4"
    assert app_mod._extension(f"{LEAK}.weirdext") == "other"


def test_7_a_schema_error_never_echoes_the_value_it_rejected():
    """jsonschema quotes the OFFENDING VALUE, which in this document is a source path, a
    caption or a project name."""
    import server.app as app_mod

    assert app_mod._rejected_field(f"'{LEAK}' is not of type 'number'") == "unknown"
    assert app_mod._rejected_field("'source' is a required property") == "source"


def test_7_an_externally_authored_tool_name_is_hashed_not_sent():
    from server import agent_runner

    assert agent_runner._known_or_hashed(LEAK).startswith("h")
    assert LEAK not in agent_runner._known_or_hashed(LEAK)
    assert agent_runner._known_or_hashed("Bash") == "Bash"  # ours, so it survives


def test_7_a_bash_command_never_leaves_the_machine():
    from server import agent_runner

    cmd = f"ffmpeg -i /Users/someone/{LEAK}/master.mov -vf scale=1080:1920 out.mp4"
    assert agent_runner._ffmpeg_filter_family(cmd) == "scale"
    assert agent_runner._root_family({"command": cmd}) == "home"


def test_7_a_provider_env_var_name_never_leaves_the_machine():
    """_SECRET_HINT would NOT have saved us here: it tests the KEY name, so a property whose
    VALUE is 'ANTHROPIC_API_KEY' rides through unredacted (verified)."""
    assert analytics.provider_family("ANTHROPIC_API_KEY") == "anthropic"
    assert analytics.provider_family(f"{LEAK.upper()}_TOKEN") == "other"


def test_7_a_reference_that_failed_to_resolve_sends_only_its_shape():
    from server import render_jobs

    assert render_jobs._reference_kind(f"assets/video/{LEAK}.mp4") == "project_path"
    assert render_jobs._reference_kind("a1b2c3d4") == "manifest_id"


def test_7_the_crash_fingerprint_carries_no_message_and_no_directory():
    exc = ValueError(f"could not open /Users/someone/{LEAK}/master.mov")
    try:
        raise exc
    except ValueError as caught:
        fp = analytics._fingerprint(caught)
    assert fp.startswith("ValueError:")
    assert LEAK not in fp and "/Users/" not in fp


# ── 8. values our OWN gate rejects ───────────────────────────────────────────
# Every test above checks that a HOSTILE value cannot get through. These check the opposite
# failure, which is the one that actually shipped: a value we generate ourselves, that we need,
# being silently dropped by our own validator. Both were found LIVE by
# data_quality_violation{class: wrong_type} — the event that exists for exactly this, and the
# reason it earns an upload rather than only a counter.


@pytest.mark.parametrize(
    "exc",
    [ValueError("x"), OSError("no such file: /Users/someone/q4-launch-teaser/a.mov"), RuntimeError("")],
)
def test_8_the_crash_fingerprint_always_survives_our_own_gate(exc):
    """It used to join with '@', which _BOUNDED_TOKEN does not allow, so the crash inbox's
    grouping key was dropped from EVERY error_reported. The inbox looked healthy and grouped
    nothing, which is worse than having no fingerprint at all."""
    try:
        raise exc
    except type(exc) as caught:
        fp = analytics._fingerprint(caught)
    assert analytics._BOUNDED_TOKEN.match(fp), f"our own fingerprint fails our own gate: {fp!r}"
    assert analytics._enum_ok(TAXONOMY, "error_reported", "fingerprint", "E", fp)


def test_8_a_synthetic_frame_does_not_break_the_fingerprint():
    """`<string>`, `<stdin>` and `<frozen importlib._bootstrap>` are real basenames, and angle
    brackets are not in the token set either."""
    try:
        exec(compile("1 / 0", "<string>", "exec"))
    except ZeroDivisionError as exc:
        fp = analytics._fingerprint(exc)
    assert "<" not in fp and ">" not in fp, fp
    assert analytics._BOUNDED_TOKEN.match(fp), fp


def test_8_every_render_family_event_declares_the_envelope_its_emitter_attaches():
    """`RenderJobStore._emit` attaches origin/publish_intent to EVERY render event from the
    copied job record. Eight of them did not declare the pair, so the gate dropped it and each
    lost its editor-vs-agent slice — found live, in delivered data, not in review."""
    import server.render_jobs as rj

    attached = {"job_id", "project_id", "session_id", "turn_id", "origin", "publish_intent"}
    undeclared_ok = set(TAXONOMY["envelope"])
    reached = [
        name
        for name, entry in EVENTS.items()
        if re.search(rf'_emit\(\s*\n?\s*"{re.escape(name)}"', (REPO / "server" / "render_jobs.py").read_text())
    ]
    assert reached, "the scan found no _emit call sites — it has gone blind"
    missing = {name: sorted((attached - undeclared_ok) - set(EVENTS[name].get("properties") or {})) for name in reached}
    bad = {k: v for k, v in missing.items() if v}
    assert not bad, f"emitted by _emit but not declared: {bad}"
