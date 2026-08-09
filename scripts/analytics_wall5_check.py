#!/usr/bin/env python3
"""S2's named assertion: a DUPLICATE-START fatal session must still read 0% crash-free.

This is the one check a renderer unit test structurally cannot make. Every case in
`web/src/analytics/track.test.js` mocks `fetch`, so all of them pass while the published query
still divides one distinct fatal session by two `session_started` ROWS and reads 50%.

The session announcement is AT-LEAST-ONCE by construction — the backend can accept it and the
response can be lost on reload — so duplicate starts are a normal state, not an edge case. If
Wall 5 counted rows, every duplicate would *hide* a failure:

    1 fatal session / 2 start rows  ->  50% crash-free, for a session that definitely crashed

Writes through the REAL path (`analytics.capture()`), so the taxonomy gate, the scrubber, the
envelope and the per-session budget all apply exactly as they do in the app. Writing straight
to PostHog would prove the query works on data the app cannot actually produce.

Isolated: a temp `OPENNOLAN_HOME` and a PINNED `OPENNOLAN_INSTALL_ID`, so it never touches the
developer's own install id and the readback can find exactly these rows.

    python3 scripts/analytics_wall5_check.py

Exit 0 pass / 1 fail / 2 no readback key. Requires the DEV project token in `.env` and the
`phx_` readback key in the Keychain; refuses to run against production.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_KEY_PREFIX = "phc_s9P9"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from lib.env_loader import load_env

    load_env()  # the REPO's .env, BEFORE home() moves to the temp dir below
    key = (os.environ.get("POSTHOG_KEY") or "").strip()
    if not key:
        print("no POSTHOG_KEY — this check needs the DEV project token in .env", file=sys.stderr)
        return 2
    if key.startswith(PRODUCTION_KEY_PREFIX):
        print("refusing: POSTHOG_KEY is the PRODUCTION token. This check writes events.", file=sys.stderr)
        return 2

    install_id = "wall5-" + uuid.uuid4().hex[:12]
    session_id = "sess-" + uuid.uuid4().hex[:12]
    os.environ.update(
        {
            "OPENNOLAN_HOME": tempfile.mkdtemp(prefix="wall5-"),
            "OPENNOLAN_INSTALL_ID": install_id,
            "OPENNOLAN_INTERNAL": "1",
        }
    )
    os.environ.pop("OPENNOLAN_ANALYTICS_NO_DEFAULT_KEY", None)

    from server import analytics

    analytics._under_pytest = lambda: False  # this IS the point of the fixture
    analytics.reset()
    if not analytics.is_enabled():
        print("analytics did not enable", file=sys.stderr)
        return 1

    # ONE session, announced TWICE, carrying ONE fatal error.
    sent = [
        analytics.capture("session_started", {"entry": "editor", "session_id": session_id}),
        analytics.capture("session_started", {"entry": "editor", "session_id": session_id}),
        analytics.capture(
            "error_reported",
            {
                "layer": "react",
                "fatal": True,
                "handled": False,
                # Built the way analytics._fingerprint builds it. It used to join with "@", which
                # _BOUNDED_TOKEN rejects, so the crash inbox lost its grouping key on every event.
                "fingerprint": "ValueError:wall5_check.py:1",
                "session_id": session_id,
            },
        ),
    ]
    analytics.shutdown()
    if not all(sent):
        print(f"the fixture was not accepted locally: {sent}", file=sys.stderr)
        return 1
    print(f"wrote fixture install_id={install_id} session_id={session_id}")

    q = [sys.executable, str(ROOT / "scripts" / "analytics_query.py")]
    # Ingestion is not instant, and the store is eventually consistent — a bare query races it
    # and produces a confident "never arrived" about a row that lands later. Measured lag for a
    # FRESH install_id in this project is 5-8 minutes, which is why the window is generous.
    wait = subprocess.run(
        q + ["await", "--install-id", install_id, "--event", "error_reported", "--timeout", "600"], cwd=ROOT
    )
    if wait.returncode == 2:
        return 2
    if wait.returncode != 0:
        print("the fixture never arrived — cannot assert anything about it", file=sys.stderr)
        return 1

    out = subprocess.run(q + ["wall5", "--install-id", install_id], cwd=ROOT, capture_output=True, text=True)
    print(out.stdout, out.stderr, sep="")
    import json

    try:
        result = json.loads(out.stdout)
    except ValueError:
        print("could not parse the wall5 result", file=sys.stderr)
        return 1

    failures = []
    if result["duplicate_start_rows"] < 1:
        failures.append("the fixture's duplicate start is not present — the check proves nothing")
    if result["fatal_sessions"] != 1:
        failures.append(f"expected 1 fatal session, got {result['fatal_sessions']}")
    if result["crash_free"] != 0.0:
        failures.append(
            f"crash_free is {result['crash_free']}, expected 0.0. A duplicate start is HIDING a "
            f"failure — the denominator is counting start ROWS instead of distinct sessions."
        )
    if failures:
        for line in failures:
            print(f"FAIL: {line}", file=sys.stderr)
        return 1
    print("\nPASS: a duplicate start does not inflate the denominator. Row-counting would have read 50% here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
