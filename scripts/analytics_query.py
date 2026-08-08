#!/usr/bin/env python3
"""Read events back out of PostHog, so an agent can verify its own instrumentation.

`posthog-python` is WRITE-ONLY. Until this existed, the only available proof that an event
worked was that a function had been called — which is exactly the class of evidence that let a
real fatal crash count as zero for four review rounds.

Reading needs a different host and a different credential from ingest:

    ingest    us.i.posthog.com   phc_…  project token, public, write-only
    readback  us.posthog.com     phx_…  PERSONAL key, scope query:read, account-level secret

The `phx_` key is a real secret and this is a PUBLIC repository, so it is never a file, never
an argument, and never printed past its prefix. It comes from the macOS Keychain:

    security add-generic-password -s opennolan-posthog-readback -a "$USER" -w '<phx_…>'

Usage:
    scripts/analytics_query.py events --install-id dev-abc --since 2026-08-07T00:00:00Z
    scripts/analytics_query.py sql "SELECT event, count() FROM events GROUP BY event"
    scripts/analytics_query.py wall5 --install-id dev-abc
    scripts/analytics_query.py await --install-id dev-abc --event export_completed --timeout 90

Exit codes: 0 found / 1 not found or query error / 2 no key.

**There is no exit-0 SKIP.** A missing key exits 2 and says so. An exit-0 skip is how a
required verification reports success having verified nothing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

# The DEV project. Production readback is a human's job — an E2E must never touch it.
PROJECT_ID = "544720"
HOST = "https://us.posthog.com"
KEYCHAIN_SERVICE = "opennolan-posthog-readback"


class NoKey(Exception):
    """No readback credential. NOT a skip: the caller decides, loudly."""


def read_key() -> str:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NoKey(f"could not reach the Keychain: {exc}") from exc
    key = out.stdout.strip()
    if out.returncode != 0 or not key:
        raise NoKey(
            f"no {KEYCHAIN_SERVICE!r} entry in the Keychain. Add one with:\n"
            f"  security add-generic-password -s {KEYCHAIN_SERVICE} -a \"$USER\" -w '<phx_…>'"
        )
    if not key.startswith("phx_"):
        raise NoKey("the stored key is not a personal API key (phx_…); a phc_ project token cannot read")
    return key


def key_hint(key: str) -> str:
    """A prefix is only safe to print on a WELL-FORMED key — slicing a short value prints all
    of it, which is how a mis-set secret becomes a log leak."""
    return f"{key[:8]}…" if len(key) >= 20 else f"<malformed key, {len(key)} chars>"


def query(sql: str, key: str, *, attempts: int = 4) -> dict:
    """Run one HogQL query, retrying transport failures.

    The query API returns 503 under load ("upstream connect error … connection termination"),
    observed live. Without a retry, `await` dies on the first blip — and a poll that cannot
    survive one bad response is not a poll. A 4xx is OUR bug and is not retried.
    """
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": sql}}).encode()
    last = ""
    for attempt in range(attempts):
        req = urllib.request.Request(
            f"{HOST}/api/projects/{PROJECT_ID}/query/",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            last = exc.read().decode(errors="replace")[:500]
            if exc.code < 500:
                raise SystemExit(f"query failed ({exc.code}): {last}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = str(exc)
        if attempt < attempts - 1:
            time.sleep(2**attempt)
    raise SystemExit(f"query failed after {attempts} attempts: {last}")


def _lit(value: str) -> str:
    """HogQL string literal. Every value here is an install id or an event name we generated,
    but quoting is not the place to rely on that."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def cmd_events(args, key: str) -> int:
    where = [f"properties.install_id = {_lit(args.install_id)}"] if args.install_id else []
    if args.since:
        where.append(f"timestamp >= {_lit(args.since)}")
    if args.event:
        where.append(f"event = {_lit(args.event)}")
    clause = " AND ".join(where) or "1 = 1"
    rows = (
        query(
            f"SELECT event, timestamp, properties FROM events WHERE {clause} "
            f"ORDER BY timestamp DESC LIMIT {int(args.limit)}",
            key,
        ).get("results")
        or []
    )
    for event, ts, props in rows:
        print(f"{ts}  {event}")
        if args.verbose:
            print("   ", json.dumps(json.loads(props) if isinstance(props, str) else props, sort_keys=True)[:1500])
    print(f"\n{len(rows)} row(s)", file=sys.stderr)
    return 0 if rows else 1


def cmd_sql(args, key: str) -> int:
    result = query(args.sql, key)
    print(json.dumps({"columns": result.get("columns"), "results": result.get("results")}, indent=2, default=str))
    return 0 if result.get("results") else 1


# The DOCUMENTED Wall 5, executed verbatim rather than re-derived here.
#
# This is the assertion a renderer unit test structurally cannot make. Every case in
# web/src/analytics/track.test.js mocks `fetch`, so all of them can pass while the published
# query still divides one distinct fatal session by two `session_started` ROWS and reads 50%
# crash-free instead of 0%. Only the readback can check the metric itself.
WALL5 = """
WITH per_session AS (
    SELECT
        properties.session_id                          AS session_id,
        countIf(event = 'session_started')             AS start_rows,
        countIf(event = 'session_started') > 0         AS has_start,
        countIf((event = '$exception'     AND properties.fatal = true)
             OR (event = 'error_reported' AND properties.fatal = true)
             OR (event = 'desktop_error'  AND properties.fatal = true)
             OR (event = 'process_gone'   AND properties.session_fatal = true)) > 0 AS has_fatal
    FROM events
    WHERE properties.session_id IS NOT NULL{scope}
    GROUP BY session_id
)
SELECT
    countIf(has_start)                     AS start_sessions,
    sum(start_rows)                        AS start_rows,
    countIf(has_start AND has_fatal)       AS fatal_sessions,
    countIf(NOT has_start AND has_fatal)   AS fatal_sessions_with_no_start
FROM per_session
"""


def cmd_wall5(args, key: str) -> int:
    scope = f" AND properties.install_id = {_lit(args.install_id)}" if args.install_id else ""
    rows = query(WALL5.format(scope=scope), key).get("results") or []
    if not rows:
        print("no rows", file=sys.stderr)
        return 1
    starts, start_rows, fatal, orphans = rows[0]
    rate = None if not starts else 1 - (fatal / starts)
    print(
        json.dumps(
            {
                "start_sessions": starts,
                "start_rows": start_rows,
                "duplicate_start_rows": start_rows - starts,
                "fatal_sessions": fatal,
                "fatal_sessions_with_no_start": orphans,
                "crash_free": rate,
            },
            indent=2,
        )
    )
    # The two numbers this whole step exists for:
    #   · duplicate_start_rows > 0 with crash_free correct proves the DISTINCT denominator is
    #     doing its job — counting rows would have read 50% where this reads 0%.
    #   · fatal_sessions_with_no_start must be 0. It was 1, and that one was the project's only
    #     real fatal crash.
    if orphans:
        print(f"\nFAIL: {orphans} fatal session(s) are on no register — Wall 5 cannot see them.", file=sys.stderr)
        return 1
    return 0


def cmd_await(args, key: str) -> int:
    """Poll to a timeout. Ingestion is not instant, and a bare query races it — which produces
    a confident 'the event never arrived' about an event that arrives four seconds later."""
    # countIf, NOT a second WHERE predicate. Measured live: filtering on install_id AND event
    # together returned 0 for MINUTES against rows that filtering on install_id alone could
    # already count — the two predicates take different query paths and they do not become
    # consistent at the same time. This shape sees the data; the other one lies.
    clause = f"properties.install_id = {_lit(args.install_id)}"
    if args.since:
        clause += f" AND timestamp >= {_lit(args.since)}"
    deadline = time.monotonic() + args.timeout
    seen = 0
    while True:
        rows = query(f"SELECT countIf(event = {_lit(args.event)}) FROM events WHERE {clause}", key).get("results") or [
            [0]
        ]
        if rows[0][0]:
            # Confirm TWICE. A single count() returned 0 live for rows a GROUP BY on the same
            # predicate could already see — the store is eventually consistent, so one reading
            # is a guess. Two agreeing readings is the cheapest thing that is not.
            seen += 1
            if seen >= 2:
                print(f"{args.event}: {rows[0][0]} row(s)")
                return 0
        else:
            seen = 0
        if time.monotonic() >= deadline:
            # Name what never arrived. "Timed out" is not a finding; this is.
            print(
                f"TIMEOUT after {args.timeout}s: {args.event} never arrived for install_id={args.install_id}",
                file=sys.stderr,
            )
            return 1
        time.sleep(min(5, max(1, deadline - time.monotonic())))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("events", help="list recent events")
    e.add_argument("--install-id")
    e.add_argument("--event")
    e.add_argument("--since")
    e.add_argument("--limit", type=int, default=50)
    e.add_argument("-v", "--verbose", action="store_true")
    e.set_defaults(fn=cmd_events)

    s = sub.add_parser("sql", help="run a HogQL query")
    s.add_argument("sql")
    s.set_defaults(fn=cmd_sql)

    w = sub.add_parser("wall5", help="run the DOCUMENTED crash-free query verbatim")
    w.add_argument("--install-id")
    w.set_defaults(fn=cmd_wall5)

    a = sub.add_parser("await", help="poll until an event arrives, or fail naming it")
    a.add_argument("--install-id", required=True)
    a.add_argument("--event", required=True)
    a.add_argument("--since")
    a.add_argument("--timeout", type=float, default=90)
    a.set_defaults(fn=cmd_await)

    args = p.parse_args(argv)
    try:
        key = read_key()
    except NoKey as exc:
        # Exit 2, never 0. An exit-0 skip is how a required verification reports success
        # having verified nothing.
        print(f"NO READBACK KEY — cannot verify anything.\n{exc}", file=sys.stderr)
        return 2
    print(f"[readback] {HOST} project={PROJECT_ID} key={key_hint(key)}", file=sys.stderr)
    return args.fn(args, key)


if __name__ == "__main__":
    sys.exit(main())
