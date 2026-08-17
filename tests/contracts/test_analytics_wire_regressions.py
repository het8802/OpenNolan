"""Regressions for defects a LIVE PostHog readback caught that no offline test could.

Every test here corresponds to a property that was measured ON THE WIRE and found wrong. They
are grouped in one file because they share one root cause shape: **something happens to an event
after the taxonomy gate has run**, so the gate cannot see it and neither could any existing test.

  F2  asset_added_to_doc.asset_ids was always []      — the path->id map was never populated
  F3  http_error.by_route could never arrive          — the route key failed _bounded()
  F8  every successful export tripped data_quality_violation — export_completed over-splatted
  F12 the whole permission family carried the wrong session — ContextVar read in the wrong task

See docs/plans/analytics-live-event-test/claude/live-event-test-report.md for the readback.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import pytest

from server import analytics, render_jobs

REPO = Path(__file__).resolve().parents[2]
TAXONOMY = analytics._merge_taxonomy(sorted((REPO / "schemas" / "analytics").glob("*.json")))
EVENTS = TAXONOMY["events"]
# What is legal ON THE WIRE: caller-legal join keys plus the names a reporter stamps on after
# validate_event() has run. The two are separate blocks on purpose — see _envelope.json.
ENVELOPE = set(TAXONOMY["envelope"]) | set(TAXONOMY["reporter_envelope"])


# ── F3 — the route template has to survive the boundedness check ──────────────
# by_route is a NESTED map, and _bounded() requires every nested KEY to match
# _BOUNDED_TOKEN. `{id}` does not (no braces in the class) and the old 80-char slice exceeded
# its 64-char limit, so the property was dropped every single time — 7 http_error events
# delivered, by_route present on none of them.


def _route_placeholders() -> list[str]:
    """The literal route templates web/src/api.js can produce, from its own replacements."""
    src = (REPO / "web" / "src" / "api.js").read_text()
    body = re.search(r"function routeTemplate\(url\)\s*\{(.*?)\n\}", src, re.S)
    assert body, "could not find routeTemplate() — update this test with it"
    return re.findall(r"\.replace\([^,]+,\s*'([^']+)'\)", body.group(1))


def test_f3_every_route_placeholder_is_a_bounded_token():
    placeholders = _route_placeholders()
    assert placeholders, "parsed no replacement targets out of routeTemplate()"
    bad = [p for p in placeholders if not analytics._BOUNDED_TOKEN.match(p.lstrip("/"))]
    assert not bad, (
        f"routeTemplate() builds by_route keys containing {bad}, which _BOUNDED_TOKEN rejects. "
        f"Every such map is dropped whole and by_route silently never arrives."
    )


def test_f3_the_route_slice_fits_the_bounded_token_length():
    src = (REPO / "web" / "src" / "api.js").read_text()
    body = re.search(r"function routeTemplate\(url\)\s*\{(.*?)\n\}", src, re.S).group(1)
    limit = int(re.search(r"\.slice\(0,\s*(\d+)\)", body).group(1))
    assert limit <= 64, (
        f"routeTemplate() slices to {limit} chars but _BOUNDED_TOKEN allows 64, so any longer "
        f"route is dropped along with the whole by_route map."
    )


def test_f3_a_realistic_by_route_map_now_passes_bounded():
    """The end-to-end shape check: the map as the renderer actually builds it."""
    assert analytics._bounded({"/api/projects/:id/assets": 3, "/api/projects/:id/render/:id": 1})
    # and the old shape still fails, so this test cannot pass for the wrong reason
    assert not analytics._bounded({"/api/projects/{id}/assets": 3})


# ── F8 — export_completed must not carry properties it does not declare ──────
# `**self._render_summary(data)` splatted 8 keys onto an event that declares 3 of them. The
# validator dropped the other 5 and raised data_quality_violation on EVERY successful export.


def test_f8_export_completed_declares_every_summary_key_it_is_given():
    declared = set(EVENTS["export_completed"]["properties"])
    given = set(render_jobs.EXPORT_COMPLETED_SUMMARY_KEYS)
    assert given <= declared, (
        f"export_completed is handed {sorted(given - declared)} from _render_summary() but does "
        f"not declare them — the validator drops each one and trips data_quality_violation."
    )


def test_f8_the_summary_keys_export_completed_skips_are_declared_somewhere_else():
    """A dropped key is only acceptable if some OTHER event carries it — otherwise the number is
    computed and then thrown away, which is the defect one level down."""
    summary_keys = {
        "n_scenes",
        "n_cached",
        "n_rendered",
        "n_comp_rerendered",
        "miss_reason",
        "runtime",
        "hdr_policy",
        "hdr_decision",
    }
    skipped = summary_keys - set(render_jobs.EXPORT_COMPLETED_SUMMARY_KEYS)
    everywhere = {p for e in EVENTS.values() for p in (e.get("properties") or {})}
    # `miss_reason`/`n_comp_rerendered` are re-shaped at their own emit sites (reason/n_comps),
    # so accept either the summary name or the emitted one.
    aliases = {"miss_reason": "reason", "n_comp_rerendered": "n_comps"}
    homeless = [k for k in sorted(skipped) if k not in everywhere and aliases.get(k) not in everywhere]
    assert not homeless, f"_render_summary computes {homeless} and no event declares them"


def test_f8_the_real_emitter_sends_no_undeclared_property(tmp_path, monkeypatch):
    """Invokes the ACTUAL emitter, not a hand-built payload.

    The earlier version of this test constructed its payload with the same constant the
    production code filters by, so reverting render_jobs to `**self._render_summary(data)`
    left every assertion green. Codex called that out; this drives
    RenderJobStore._emit_export_completed directly, so the splat itself is under test.
    """
    sent: list[tuple[str, dict]] = []

    class FakeClient:
        def capture(self, **kw):
            sent.append((kw["event"], kw["properties"]))

    analytics.reset()
    monkeypatch.setattr(analytics, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)

    store = render_jobs.RenderJobStore(tmp_path)
    job_id = "job1"
    with store._lock:
        store._jobs[job_id] = {
            "job_id": job_id,
            "project_id": "p1",
            "origin": "editor",
            "publish_intent": True,
            "queued_at": 0.0,
            "session_id": "s1",
            "turn_id": None,
        }
    final = tmp_path / "final.mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"\x00" * 2048)
    # Every key _render_summary() can produce, so an unfiltered splat WOULD leak here.
    data = {
        "n_scenes": 3,
        "n_cached": 2,
        "n_rendered": 1,
        "n_comp_rerendered": 1,
        "cache_miss_reason": "scene_spec",
        "render_runtime": "ffmpeg",
        "hdr_policy": "sdr",
        "hdr_decision": "sdr",
        "final_review_status": "pass",
    }
    monkeypatch.setattr(render_jobs, "_loudness", lambda _p: None)  # no ffmpeg in the test env
    store._emit_export_completed(job_id, "p1", final, {"cuts": [], "audio": {}}, data)

    exports = [p for e, p in sent if e == "export_completed"]
    assert exports, f"export_completed was not emitted; saw {[e for e, _ in sent]}"
    declared = set(EVENTS["export_completed"]["properties"]) | ENVELOPE
    leaked = sorted(k for k in exports[-1] if k not in declared)
    assert not leaked, f"the real emitter put undeclared {leaked} on export_completed"
    assert not [e for e, _ in sent if e == "data_quality_violation"], (
        "a successful export still raises data_quality_violation"
    )
    assert analytics._counters["dropped_props"] == 0
    # and the numbers it IS supposed to carry actually arrived
    assert exports[-1]["n_scenes"] == 3 and exports[-1]["n_cached"] == 2


# ── F12 — the permission family must carry the LIVE turn's session ────────────
# can_use_tool runs in the SDK client's task, whose context was captured when the client was
# BUILT. current_session_id() therefore returned the session that created the client and kept
# returning it for every later turn: 11 events across 3 sessions all stamped with the first.


@pytest.mark.asyncio
async def test_f12_permission_events_carry_the_live_turns_session(monkeypatch):
    from server import agent_runner

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(analytics, "capture", lambda e, p=None: captured.append((e, dict(p or {}))))
    # The stale value the old code would have picked up.
    monkeypatch.setattr(analytics, "current_session_id", lambda: "session-that-built-the-client")

    live = {"turn_id": "turn-now", "session_id": "session-now"}
    # The render route moved to the ALWAYS-RUN PreToolUse hook (an allow rule or sandbox
    # auto-approval can resolve a Bash call before can_use_tool). It reads the same live
    # getter, and F12 must hold there too.
    hook = agent_runner.make_pre_tool_use_hook(sandbox=None, turn_ctx=lambda: live)
    await hook(
        {"tool_name": "Bash", "tool_input": {"command": "python -c 'VideoCompose().render_proxies()'"}},
        "tu_1",
        None,
    )

    named = {e: p for e, p in captured}
    assert "tool_permission_decided" in named, f"no permission event captured: {list(named)}"
    for event in ("tool_permission_decided", "agent_rendered_via_bash"):
        if event not in named:
            continue
        assert named[event].get("session_id") != "session-that-built-the-client", (
            f"{event} still resolves the session that BUILT the client (F12)"
        )
    assert named["tool_permission_decided"]["session_id"] == "session-now"
    assert named["tool_permission_decided"]["turn_id"] == "turn-now"


@pytest.mark.asyncio
async def test_f12_decide_tool_captures_inherit_the_bound_session(monkeypatch):
    """decide_tool()'s own captures have no ctx argument — they are fixed by can_use_tool
    binding the ContextVar before calling it. This is the assertion that keeps that binding."""
    from server import agent_runner

    captured: list[tuple[str, Optional[str]]] = []

    def fake_capture(event, props=None):
        captured.append((event, (props or {}).get("session_id") or analytics.current_session_id()))

    monkeypatch.setattr(analytics, "capture", fake_capture)
    analytics.set_session_id("stale-session")
    live = {"turn_id": "t1", "session_id": "correct-session"}
    can_use_tool = agent_runner.make_can_use_tool(sandbox=None, turn_ctx=lambda: live)
    await can_use_tool("Bash", {"command": "ffmpeg -i a.mp4 -vf scale=2:2 b.mp4"}, None)
    freehand = [s for e, s in captured if e == "agent_ffmpeg_freehand"]
    assert freehand, f"agent_ffmpeg_freehand not captured; saw {[e for e, _ in captured]}"
    assert freehand[0] == "correct-session", (
        f"agent_ffmpeg_freehand resolved {freehand[0]!r}; decide_tool's captures are not "
        f"inheriting the session can_use_tool bound"
    )


# ── F2 — the path -> asset_id map has to actually be populated ────────────────


def test_f2_the_editor_writes_the_asset_id_map_it_reads():
    """`assetIds.current` was declared and read, and written NOWHERE, so asset_added_to_doc
    shipped asset_ids=[] forever. A read with no write is invisible to every offline test."""
    src = (REPO / "web" / "src" / "studio" / "Studio.jsx").read_text()
    reads = len(re.findall(r"assetIds\.current\[", src))
    writes = len(re.findall(r"assetIds\.current\s*=", src))
    assert reads and writes, (
        f"assetIds.current: {reads} read(s), {writes} write(s). A lookup map that is never "
        f"assigned makes asset_added_to_doc.asset_ids permanently empty."
    )


def test_f2_the_assets_listing_carries_the_id_the_editor_needs():
    """The map can only be built if GET /assets returns asset_id per file. It did not."""
    src = (REPO / "server" / "app.py").read_text()
    body = re.search(r"def list_assets\(project_id: str\).*?\n        renders: list", src, re.S)
    assert body, "could not find list_assets — update this test with it"
    # CODE only — a comment that merely names asset_probe.asset_id() must not trip this.
    code = "\n".join(l for l in body.group(0).split("\n") if not l.lstrip().startswith("#"))
    assert "asset_id" in code, (
        "GET /api/projects/{id}/assets does not return asset_id, so the editor has no "
        "path -> asset_id map and row 37's imported ⋈ added_in_editor join stays dead."
    )
    assert "asset_probe.asset_id(" not in code, (
        "list_assets must LOOK UP ids, not mint them: asset_probe.asset_id() writes the "
        "manifest, and this route is polled every 4 seconds."
    )


# ── The hole the FIRST version of the F11 fix opened ─────────────────────────
# Declaring os/arch/app_version in `envelope` also added them to validate_event()'s ALLOWED
# set, which would have handed the renderer — any POST /api/telemetry/events — a free-text
# field on every event: an envelope-only property has no per-event `kind`, so _enum_ok returns
# True, and _bounded accepts any string at depth 0. Hence the reporter_envelope split.


def test_reporter_owned_names_are_not_caller_legal(monkeypatch):
    sent: list[tuple[str, dict]] = []

    class FakeClient:
        def capture(self, **kw):
            sent.append((kw["event"], kw["properties"]))

    analytics.reset()
    monkeypatch.setattr(analytics, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    # canvas_changed declares neither `os` nor `app_version`.
    analytics.capture(
        "canvas_changed",
        {"from": "9:16", "to": "1:1", "os": "totally free text; a campaign name", "app_version": "also free"},
    )
    props = [p for e, p in sent if e == "canvas_changed"][-1]
    # The reporter still stamps the REAL values on afterwards; what must not survive is the
    # caller's. _env_props()/_envelope() run after the gate, so `os` is simply absent here.
    assert props.get("os") != "totally free text; a campaign name"
    assert props.get("app_version") != "also free"
    # The drop COUNTER rides out on this very event (capture() embeds the envelope, then zeroes
    # the counters), so read it from the payload rather than from _counters.
    assert props.get("telemetry_dropped_props", 0) >= 2, (
        "the caller's os/app_version were not dropped — reporter-owned names have become "
        "caller-legal again, which is a free-text field on every event"
    )


def test_the_two_envelope_blocks_do_not_overlap():
    """A name in both is a name whose caller-legality depends on which block you read."""
    keys = lambda block: {k for k in TAXONOMY[block] if not k.startswith("$")}  # noqa: E731
    both = keys("envelope") & keys("reporter_envelope")
    assert not both, f"declared in BOTH envelope and reporter_envelope: {sorted(both)}"


# ── F2 end-to-end: ingest -> list must return a REAL id, and legacy ids survive ──


def _api(tmp_path):
    from fastapi.testclient import TestClient

    from server.app import create_app

    projects = tmp_path / "projects"
    caps = {"composition_runtimes": {}, "capabilities": [], "setup_offers": [], "runtime_warnings": []}
    return TestClient(create_app(projects_dir=projects, capabilities_provider=lambda: caps)), projects


def test_f2_ingest_then_list_returns_a_non_null_asset_id(tmp_path):
    """The round trip, not a string search: the previous version of this test only grepped
    server/app.py for the token `asset_id` and would have passed with every value null."""
    client, projects = _api(tmp_path)
    client.post("/api/projects", json={"name": "F2 Round Trip"})
    up = client.post(
        "/api/projects/f2-round-trip/assets",
        data={"kind": "video"},
        files={"file": ("clip.mp4", b"\x00" * 64, "video/mp4")},
    )
    assert up.status_code == 201, up.text
    minted = up.json()["asset_id"]
    assert minted, "ingest did not mint an asset_id"

    listed = client.get("/api/projects/f2-round-trip/assets").json()
    entry = next(f for f in listed["kinds"]["video"] if f["name"] == "clip.mp4")
    assert entry["asset_id"] == minted, (
        f"ingest minted {minted!r} but the listing reports {entry['asset_id']!r} — the writer and "
        f"the reader disagree on the manifest key, so asset_added_to_doc.asset_ids stays empty."
    )


def test_f2_an_id_minted_under_the_legacy_key_is_adopted_not_replaced(tmp_path):
    """Changing the manifest key must not orphan ids minted before the change: a second id for
    an existing asset breaks exactly the historical join the id exists for."""
    from server import asset_probe

    project_dir = tmp_path / "projects" / "p1"
    project_dir.mkdir(parents=True)
    rel = "assets/video/old.mp4"
    # The pre-fix shape: PROJECTS-DIR-relative, leading with the project id.
    asset_probe._write_manifest(project_dir, {f"p1/{rel}": {"asset_id": "legacy-id-0001"}})

    assert asset_probe.lookup_asset_id(project_dir, rel) == "legacy-id-0001", "legacy id not found"
    assert asset_probe.asset_id(project_dir, rel) == "legacy-id-0001", "a second id was minted"
    # and it is re-filed under the new key, so the fallback is needed only once
    assert asset_probe.read_manifest(project_dir).get(rel, {}).get("asset_id") == "legacy-id-0001"


# ── F1 — `scripts/dev run` must refuse the production fallback ────────────────


def test_f1_dev_run_sets_the_no_default_key_guard_even_when_blank():
    """setdefault() was not enough: both reporters read "" as FALSE, so a blank
    OPENNOLAN_ANALYTICS_NO_DEFAULT_KEY in the shell left the guard off and reopened the
    production leak."""
    src = (REPO / "scripts" / "dev").read_text()
    body = re.search(r"def run_app\(.*?\n    with log_path\.open", src, re.S)
    assert body, "could not find run_app() — update this test with it"
    code = "\n".join(l for l in body.group(0).split("\n") if not l.lstrip().startswith("#"))
    assert "OPENNOLAN_ANALYTICS_NO_DEFAULT_KEY" in code, (
        "scripts/dev run does not set the guard, so a worktree with no .env writes to the PRODUCTION PostHog project."
    )
    assert 'setdefault("OPENNOLAN_ANALYTICS_NO_DEFAULT_KEY"' not in code, (
        'setdefault leaves an existing EMPTY value in place, and both reporters treat "" as '
        "false — blank must be treated as unset."
    )


@pytest.mark.asyncio
async def test_f12_the_bound_session_does_not_outlive_the_callback(monkeypatch):
    """The SDK task is long-lived and REUSED across turns. A bind left standing after the
    callback returns would be inherited by the next one, so a turn with no context would emit
    against the previous turn's session — the same misattribution, one layer along."""
    from server import agent_runner

    monkeypatch.setattr(analytics, "capture", lambda e, p=None: None)
    analytics.set_session_id("outer-request-session")

    live = {"turn_id": "t1", "session_id": "turn-session"}
    can_use_tool = agent_runner.make_can_use_tool(sandbox=None, turn_ctx=lambda: live)
    await can_use_tool("Bash", {"command": "ffmpeg -i a.mp4 -vf scale=2:2 b.mp4"}, None)
    assert analytics.current_session_id() == "outer-request-session", (
        "can_use_tool left its session bound after returning; the next callback inherits it"
    )


@pytest.mark.asyncio
async def test_f12_an_empty_turn_context_emits_no_session_rather_than_a_stale_one(monkeypatch):
    """No live turn must mean NO session, never the last one that happened to run. A wrong
    join key is worse than a missing one: it silently attributes work to the wrong session."""
    from server import agent_runner

    captured: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        analytics,
        "capture",
        lambda e, p=None: captured.append((e, (p or {}).get("session_id") or analytics.current_session_id())),
    )
    analytics.set_session_id("a-stale-session")

    ctx: dict = {"turn_id": "t1", "session_id": "turn-session"}
    can_use_tool = agent_runner.make_can_use_tool(sandbox=None, turn_ctx=lambda: ctx)
    await can_use_tool("Bash", {"command": "ffmpeg -i a.mp4 -vf scale=2:2 b.mp4"}, None)
    ctx.clear()  # the turn ended; _turn_ctx.pop() leaves the getter returning {}
    captured.clear()
    await can_use_tool("Bash", {"command": "ffmpeg -i a.mp4 -vf scale=4:4 b.mp4"}, None)
    sessions = [s for e, s in captured if e == "agent_ffmpeg_freehand"]
    assert sessions, f"agent_ffmpeg_freehand not captured; saw {[e for e, _ in captured]}"
    assert sessions[0] != "turn-session", "a finished turn's session leaked into the next callback"


@pytest.mark.asyncio
async def test_f12_the_mcp_handlers_stamp_the_session_explicitly():
    """The five MCP tool handlers read turn_id from _turn_ctx; they must read session_id from
    the same place. Relying on capture()'s current_session_id() fallback is precisely the bug —
    they run in the client's task too."""
    src = (REPO / "server" / "agent_runner.py").read_text()
    turn_reads = src.count('"turn_id": (self._turn_ctx.get(project_id) or {}).get("turn_id"),')
    session_reads = src.count('"session_id": (self._turn_ctx.get(project_id) or {}).get("session_id"),')
    assert turn_reads and session_reads == turn_reads, (
        f"{turn_reads} handler(s) read turn_id from _turn_ctx but only {session_reads} read "
        f"session_id there — the rest fall back to the client task's stale ContextVar."
    )
