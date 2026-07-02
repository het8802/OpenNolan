"""Analyze a recorded editor UI session WITHOUT loading the raw log into context.

The editor's session recorder (web/src/debug/recorder.js) writes NDJSON to
.agents/tools/logs/ui-sessions/<session>.ndjson — often thousands of lines
(tens of thousands of tokens) for only a couple of minutes. This is the
"query, don't read" tool: it prints a compact report (event histogram, the
source-video seek-completion analysis that pinpoints scrub freezes, and any
errors verbatim) that stays small regardless of session length.

Run:
    python scripts/debug_session.py                 # newest session
    python scripts/debug_session.py latest          # same
    python scripts/debug_session.py <session-id>    # a specific one
    python scripts/debug_session.py --list          # list sessions
    python scripts/debug_session.py latest --json    # raw report JSON

To zoom into a specific slice AFTER reading the report, use the line numbers
the report implies, e.g.:
    sed -n '600,640p' .agents/tools/logs/ui-sessions/<session>.ndjson
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import debug_log  # noqa: E402


def _print_report(rep: dict) -> None:
    print(f"session : {rep['session']}")
    print(f"file    : {rep['file']}")
    print(f"events  : {rep['events']}   bytes: {rep['bytes']:,}   (~{rep['bytes'] // 4:,} tokens if read raw)")
    print(f"window  : {rep.get('first_wall')} → {rep.get('last_wall')}")
    if rep.get("started"):
        print(f"started : {json.dumps(rep['started'])[:200]}")

    print("\nevent histogram:")
    for etype, n in rep["histogram"].items():
        print(f"  {etype:26} {n:6d}")

    seeks = rep.get("seeks")
    if seeks:
        print("\nsource-video seek lifecycle (scrub → canvas):")
        print(f"  requests={seeks['requests']}  fired={seeks['fired']}  "
              f"started={seeks['started']}  finished={seeks['finished']}  "
              f"superseded={seeks['superseded_before_finishing']}")
        rate = seeks["completion_rate"]
        if rate is not None:
            print(f"  completion_rate={rate:.1%}  (share of started seeks that actually finished)")
        if seeks["stuck_sample"]:
            print(f"  stuck sample (first {len(seeks['stuck_sample'])} superseded seeks):")
            for s in seeks["stuck_sample"]:
                print(f"    seq={s.get('seq')} t={s.get('t')}ms to={s.get('cur')} readyState={s.get('readyState')}")

    if rep.get("errors"):
        print(f"\nerrors / warnings ({len(rep['errors'])}):")
        for e in rep["errors"]:
            print(f"  seq={e.get('seq')} {e.get('type')} {json.dumps(e.get('data') or e.get('message') or '')[:160]}")

    for note in rep.get("notes", []):
        print(f"\n⚠ {note}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Analyze a recorded UI debug session.")
    ap.add_argument("session", nargs="?", default="latest", help="session id, or 'latest' (default)")
    ap.add_argument("--list", action="store_true", help="list recorded sessions and exit")
    ap.add_argument("--json", action="store_true", help="print the raw report JSON")
    args = ap.parse_args(argv)

    if args.list:
        sessions = debug_log.list_sessions()
        if not sessions:
            print("no sessions recorded")
            return 0
        for s in sessions:
            print(f"{s['mtime']}  {s['bytes']:>9,}B  {s['session']}")
        return 0

    session = debug_log.latest_session() if args.session == "latest" else args.session
    if not session:
        print("no sessions recorded", file=sys.stderr)
        return 1
    try:
        rep = debug_log.analyze_session(session)
    except FileNotFoundError:
        print(f"session {session!r} not found", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        _print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
