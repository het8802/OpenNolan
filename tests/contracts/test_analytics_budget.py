"""S7 — the per-session upload budget, enforced rather than hoped for.

The agreed ceiling is ≤40 uploads for an expected productive session, hard cap 100. Before
this, the only 100 in the code was per POST BODY and per renderer queue — across 5-second
flushes a session could upload arbitrarily many, and the reviewer's counterexample exceeded 40
using P0 rows alone.

A single shared limiter is not implementable: three sources emit independently and Electron's
direct reporter is invisible to a backend counter. Hence per-source budgets under one equation,
asserted below so the two halves cannot drift apart in two languages.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from server import analytics

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def sink(monkeypatch):
    sent: list[tuple[str, dict]] = []

    class FakeClient:
        def capture(self, **kw):
            sent.append((kw["event"], kw["properties"]))

    monkeypatch.setattr(analytics, "_get_client", lambda: FakeClient())
    analytics.reset()
    monkeypatch.setattr(analytics, "_get_client", lambda: FakeClient())
    return sent


def test_the_equation_holds():
    """'Small' and 'separately stated' are adjectives; rev 3 of the plan used them in place of
    numbers, which relocated the unboundedness instead of removing it."""
    total = (
        analytics.BUDGET_NONCRITICAL
        + analytics.ELECTRON_BUDGET_NONCRITICAL
        + analytics.BUDGET_CRITICAL
        + analytics.ELECTRON_BUDGET_CRITICAL
    )
    assert total <= analytics.SESSION_HARD_CAP, f"per-source budgets sum to {total}"


def test_electrons_numbers_match_the_ones_python_publishes():
    """desktop/main.js enforces the shell's half. Two numbers in two languages drift; this is
    what stops the sum from silently exceeding the hard cap after one side is tuned."""
    src = (REPO / "desktop" / "main.js").read_text()
    nc = int(re.search(r"^const BUDGET_NONCRITICAL = (\d+);", src, re.M).group(1))
    cr = int(re.search(r"^const BUDGET_CRITICAL = (\d+);", src, re.M).group(1))
    assert nc == analytics.ELECTRON_BUDGET_NONCRITICAL
    assert cr == analytics.ELECTRON_BUDGET_CRITICAL


def test_a_flood_is_capped_and_the_drop_is_counted_not_silent(sink, monkeypatch):
    """A silent truncation is worse than no cap: every rate computed from it is wrong in an
    unknowable direction."""
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    for _ in range(analytics.BUDGET_NONCRITICAL + 25):
        analytics.capture("project_created", {"session_id": "s1", "pipeline_type": "hybrid"})
    assert len(sink) == analytics.BUDGET_NONCRITICAL
    assert analytics._counters["budget_dropped"] == 25


def test_a_critical_flood_is_bounded_by_the_reserve_not_unbounded(sink, monkeypatch):
    """'Criticals bypass' is what made the hard cap unbounded in the agreed plan. They draw on
    a RESERVE, so a crash loop costs at most 25 here."""
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    for _ in range(analytics.BUDGET_CRITICAL + 40):
        analytics.capture("export_failed", {"session_id": "s1", "stage": "publish"})
    assert len(sink) == analytics.BUDGET_CRITICAL


def test_an_exhausted_ordinary_budget_still_admits_criticals(sink, monkeypatch):
    """The reason criticals get their own bucket at all: activation and every failure class are
    low-N rates where one dropped observation is a real loss."""
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    for _ in range(analytics.BUDGET_NONCRITICAL + 10):
        analytics.capture("project_created", {"session_id": "s1", "pipeline_type": "hybrid"})
    analytics.capture("export_completed", {"session_id": "s1", "origin": "editor"})
    assert sink[-1][0] == "export_completed"


def test_budgets_are_per_session_not_global(sink, monkeypatch):
    """A long-lived backend serves many sessions; one busy session must not silence the next."""
    monkeypatch.setattr(analytics, "_under_pytest", lambda: False)
    for _ in range(analytics.BUDGET_NONCRITICAL + 5):
        analytics.capture("project_created", {"session_id": "s1", "pipeline_type": "hybrid"})
    before = len(sink)
    analytics.capture("project_created", {"session_id": "s2", "pipeline_type": "hybrid"})
    assert len(sink) == before + 1


# ── the reviewer's counterexample, recomputed for all three tiers ─────────────
# D10's original was built from P0 rows only and already exceeded 40. This is the same journey
# with P1 and P2 coverage added, which is the scope this build actually ships.

COUNTEREXAMPLE = Path(__file__).parent / "fixtures" / "session_counterexample.json"


def test_the_counterexample_journey_stays_under_the_hard_cap():
    """The number that matters is the one nobody can afford to exceed. ≤40 is the EXPECTED
    productive session (the catalog's own all-tier model is ~30); this journey is the heavy
    tail, and the assertion is that even it cannot reach the cap."""
    journey = json.loads(COUNTEREXAMPLE.read_text())
    per_source = journey["per_source"]
    total = sum(per_source.values())
    assert total <= analytics.SESSION_HARD_CAP, f"{total} uploads exceeds the hard cap"
    assert per_source["electron"] <= (analytics.ELECTRON_BUDGET_NONCRITICAL + analytics.ELECTRON_BUDGET_CRITICAL)
    assert per_source["backend"] + per_source["renderer"] <= (analytics.BUDGET_NONCRITICAL + analytics.BUDGET_CRITICAL)


def test_the_expected_productive_session_stays_under_forty():
    journey = json.loads(COUNTEREXAMPLE.read_text())
    assert journey["expected_productive_session"] <= 40
