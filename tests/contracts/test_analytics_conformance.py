"""S4a payload conformance + S4b the expected-variant matrix.

These are TWO tests on purpose, and the split is the whole point.

  S4a asks: does a delivered event carry what it declares, with the right types, and is every
            `E` value inside its vocabulary?
  S4b asks: is every declared VARIANT ever exercised anywhere?

A payload validator has no reason to demand unused variants — a normal run legitimately emits
`launch_kind=cold` and `entry=dashboard` and nothing else. So S4a would pass forever on dead
enum members, which is exactly what happened: `entry` declares {dashboard, editor, setup} and
only `dashboard` was ever emitted; `launch_kind` declares `activate`, never emitted;
`agent_turn_started.entrypoint` was declared and never emitted at all.

Contract test 1b proves a declared event has an EMITTER — that is static. Neither of these is.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from server import analytics

REPO = Path(__file__).resolve().parents[2]
TAXONOMY = analytics._merge_taxonomy(sorted((REPO / "schemas" / "analytics").glob("*.json")))
EVENTS = TAXONOMY["events"]
# NO hand-written special cases. `env`/`internal` used to be allowlisted right here, which hid
# the real defect: a name a reporter attaches AFTER validate_event() is ungoverned, and papering
# over two of them is what let four more (app_version/os/arch/packaged) reach the wire from
# Electron. Both blocks now come from the schema.
#
# The split is load-bearing. CALLER_LEGAL is what validate_event() lets an emit site send;
# ON_THE_WIRE additionally covers what a reporter stamps on afterwards. Asserting wire
# conformance against CALLER_LEGAL alone would fail, and merging the two in the SCHEMA would
# hand the renderer a free-text `os` on every event.
CALLER_LEGAL = set(TAXONOMY["envelope"])
ENVELOPE = CALLER_LEGAL | set(TAXONOMY["reporter_envelope"])

_SOURCE_GLOBS = ("server/**/*.py", "scripts/*.py", "desktop/*.js", "web/src/**/*.js", "web/src/**/*.jsx")


# ── S4a — payload conformance ────────────────────────────────────────────────


@pytest.fixture
def sink(monkeypatch):
    sent: list[tuple[str, dict]] = []

    class FakeClient:
        def capture(self, **kw):
            sent.append((kw["event"], kw["properties"]))

    analytics.reset()
    monkeypatch.setattr(analytics, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    return sent


_TYPE_CHECKS = {
    "N": lambda v: v is None or isinstance(v, (int, float)) and not isinstance(v, bool),
    "F": lambda v: v is None or isinstance(v, bool),
    "I": lambda v: v is None or isinstance(v, str),
    "E": lambda v: v is None or isinstance(v, str),
    "B": lambda v: v is None or isinstance(v, str),
    "A": lambda v: v is None or isinstance(v, (list, tuple)),
    "O": lambda v: v is None or isinstance(v, dict),
}


def test_s4a_every_declared_type_is_one_the_validator_understands():
    """A property typed with something outside property_types is ungoverned: the enum gate
    only fires on E and B, so a typo'd type silently disables the check on that field."""
    known = set(TAXONOMY["property_types"])
    bad = [
        f"{event}.{prop}={kind}"
        for event, entry in EVENTS.items()
        for prop, kind in (entry.get("properties") or {}).items()
        if kind not in known
    ]
    assert not bad, f"unknown property types: {bad}"


@pytest.mark.parametrize("event", sorted(EVENTS))
def test_s4a_a_conforming_payload_survives_validation_intact(event, sink):
    """Build one legal value per declared property and assert the whole payload round-trips.

    This is what catches a property that is declared but can never actually be sent — a
    reserved substring in the name, an enum whose declared values fail the bucket shape, a
    nested map the boundedness check refuses."""
    entry = EVENTS[event]
    payload = {}
    for prop, kind in (entry.get("properties") or {}).items():
        payload[prop] = _legal_value(event, prop, kind)
    analytics.capture(event, payload)
    delivered = [p for e, p in sink if e == event]
    assert delivered, f"{event} did not survive validation with a fully conforming payload"
    props = delivered[-1]
    for prop, kind in (entry.get("properties") or {}).items():
        assert prop in props, (
            f"{event}.{prop} was DROPPED by a payload that conforms to its own declaration. "
            f"Usually a reserved substring in the name, or an enum value the gate refuses."
        )
        assert _TYPE_CHECKS[kind](props[prop]), f"{event}.{prop} arrived as {type(props[prop]).__name__}"


def _legal_value(event: str, prop: str, kind: str):
    if kind == "N":
        return 1
    if kind == "F":
        return True
    if kind == "I":
        return "abc123"
    if kind == "O":
        return {"editor.split": 1}
    vocab = analytics._enum_values(TAXONOMY, event, prop)
    if kind == "A":
        return list(vocab[:1]) if vocab else ["editor.split"]
    if vocab:
        return vocab[0]
    return "0-1" if kind == "B" else "bounded_token"


def test_s4a_no_declared_property_name_can_be_destroyed_by_scrub():
    """The single assertion that fails on prompt_len, prompt_chars, message_len, text_len and
    content_len — every one of which appeared in a phase-1 doc — and on content_fingerprint,
    which was introduced two sections after the rule forbidding it was written."""
    reserved = TAXONOMY["reserved_substrings"]
    offenders = []
    for event, entry in EVENTS.items():
        for prop in entry.get("properties") or {}:
            low = prop.lower()
            if any(s in low for s in reserved["freetext"] + reserved["secret"]):
                offenders.append(f"{event}.{prop}")
    assert not offenders, (
        f"property names _scrub would destroy: {offenders}. The freetext group is rewritten to "
        f"'<key>_len' with a None value; the secret group becomes '[redacted]'."
    )


def test_s4a_an_undeclared_event_never_reaches_the_client(sink):
    analytics.capture("definitely_not_declared", {"x": 1})
    assert not [e for e, _ in sink if e == "definitely_not_declared"]


def test_s4a_every_name_python_attaches_after_the_gate_is_declared(sink):
    """The taxonomy gate runs BEFORE the envelope is attached, so anything capture() adds
    afterwards is ungoverned. This asserts the envelope block actually covers it.

    Caught live on the Electron side (10 of 11 shell events shipped os/arch/packaged), and the
    Python reporter had the same latent hole for env/internal — hidden only because this file
    used to allowlist those two names by hand."""
    analytics.capture("app_opened", {"os": "Darwin", "app_version": "0.1.0"})
    delivered = [p for e, p in sink if e == "app_opened"]
    assert delivered, "app_opened did not survive validation"
    declared = set(EVENTS["app_opened"]["properties"]) | ENVELOPE
    undeclared = sorted(k for k in delivered[-1] if k not in declared)
    assert not undeclared, (
        f"capture() attaches {undeclared} after validate_event(), and no schema declares them. "
        f"Add them to _envelope.json's `envelope` block or stop attaching them."
    )


_JS_ENVELOPE = re.compile(r"properties:\s*\{\s*\.\.\.checked,(.*?)\n\s*\},", re.S)


def test_s4a_every_name_electron_attaches_after_the_gate_is_declared():
    """desktop/main.js runs the same taxonomy gate, then attaches its own envelope. Every key in
    that literal must be declared, or it reaches PostHog ungoverned — measured on the wire for
    session_started, session_ended, backend_ready, process_gone and app_launch_started."""
    src = (REPO / "desktop" / "main.js").read_text()
    block = _JS_ENVELOPE.search(src)
    assert block, "could not find postToPostHog's envelope literal — update this test with it"
    keys = set(re.findall(r"^\s{10}([A-Za-z_][A-Za-z0-9_]*):", block.group(1), re.M))
    assert keys, "parsed no keys out of the Electron envelope literal"
    undeclared = sorted(keys - ENVELOPE)
    assert not undeclared, (
        f"desktop/main.js attaches {undeclared} after validateEvent(); _envelope.json does not "
        f"declare them. Every reporter's post-gate envelope must be declared in ONE place."
    )


# ── S4b — the expected-variant matrix ────────────────────────────────────────
# Separate from S4a because a normal run legitimately exercises one variant per enum. This
# fails when a DECLARED variant is never exercised ANYWHERE in the source, which is the state
# `entry={dashboard,editor,setup}` was in: two of three dead, and nothing said so.


def _source_text() -> str:
    parts = []
    for glob in _SOURCE_GLOBS:
        for path in REPO.glob(glob):
            if "node_modules" in path.parts or ".test." in path.name:
                continue
            parts.append(path.read_text(errors="ignore"))
    return "\n".join(parts)


SOURCE = _source_text()

# Vocabularies whose members come from OUTSIDE our code, so "never mentioned in source" is the
# expected state rather than a defect. Each entry names where the values come from.
_EXTERNAL_VOCABULARIES = {
    "model": "AGENT_MODELS, asserted separately against the code that defines it",
    "pipeline_type": "lib.pipeline_loader.list_pipelines(), asserted separately",
    "style": "playbook_loader.builtin_playbooks(), asserted separately",
    "process": "Chromium's own child-process type vocabulary",
    "reason": "Chromium's own exit-reason vocabulary",
    "stop_reason": "the Claude Agent SDK's ResultMessage",
    "final_review_status": "tools/video/video_compose's own status vocabulary",
    "exit_code_bucket": "arithmetic on an OS exit code",
    "ready_state": "the HTMLMediaElement readyState integers",
    "pack": "lib.provision.PACKS, asserted separately",
}


@pytest.mark.parametrize("key", sorted(k for k in TAXONOMY["enums"] if not k.startswith("$")))
def test_s4b_every_declared_enum_variant_is_reachable_from_source(key):
    """A variant nothing can ever produce is a dashboard slice that reads zero forever.

    Deliberately a SOURCE scan and not a runtime assertion: provoking every variant needs a
    fault injection per variant, and the failure this guards against — a member declared and
    then never wired — is visible statically. A variant that IS in the source but unreachable
    at runtime is a different defect, and S4a's per-event round-trip is what covers the shape.
    """
    if key in _EXTERNAL_VOCABULARIES:
        pytest.skip(f"external vocabulary: {_EXTERNAL_VOCABULARIES[key]}")
    values = TAXONOMY["enums"][key]
    if not isinstance(values, list):
        pytest.skip("not a list")
    unreachable = [v for v in values if not isinstance(v, str) or not re.search(rf"(['\"]){re.escape(v)}\1", SOURCE)]
    assert not unreachable, (
        f"enum {key} declares variants no source line can produce: {unreachable}. "
        f"Either wire the emit site or remove the variant — a dead variant is a dashboard "
        f"slice that reads zero forever and never says why."
    )


def test_s4b_every_declared_event_property_is_written_somewhere():
    """The property-level twin of contract test 1b. `agent_turn_started.entrypoint` was
    declared and never emitted, and nothing noticed for four review rounds."""
    missing = []
    for event, entry in EVENTS.items():
        for prop in entry.get("properties") or {}:
            if prop in ENVELOPE:
                continue
            # A key in an object literal, in either language.
            if re.search(rf"(?<![\w.]){re.escape(prop)}\s*[:=]", SOURCE) or f'"{prop}"' in SOURCE:
                continue
            missing.append(f"{event}.{prop}")
    assert not missing, (
        f"declared properties no source line ever writes: {missing}. A declared-but-unwritten "
        f"property is a column that is NULL forever."
    )
